"""
Config-variant fingerprinting and the audit trail for advisory-engine config
changes (see docs/advisory-engine-plan.md). Answers "which strategy config
was active when this trade was placed" so services/advisory/advisory_engine.py can
score each config variant on its own resolved trades instead of blending
every config the user has ever run into one number.

SQLite file lives at data/config_performance.db — gitignored, never commit
it. Same one-file-per-concern pattern as services/signal_log.py.

Known limitation, documented rather than silently glossed over: a trade's
fingerprint is always the config active when its position was *opened*
(see services/paper_broker.py), not necessarily the config active when it
was later closed. A position can stay open while the user tweaks
strategy.* mid-hold (e.g. changes stop_loss_pct while a position from an
older variant is still open) — entry-time attribution is the right call
for "did this variant's entry gates perform well," but exit-config drift
mid-hold isn't separately tracked here.
"""
import hashlib
import json
import sqlite3
import time
from pathlib import Path

from services import history_push

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "config_performance.db"

# The only strategy field that isn't a tunable knob - deliberately excluding
# just this one (rather than hand-listing every field that *is* tunable)
# means a newly added strategy.* field automatically joins the fingerprint
# with no code change here.
_NON_TUNABLE_FIELDS = {"name"}


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, coltype: str):
    # data/config_performance.db is a live file the running dev server
    # reads/writes (CLAUDE.md) - CREATE TABLE IF NOT EXISTS alone doesn't
    # add a column to an existing table with existing rows, so a new column
    # needs an explicit, idempotent ALTER TABLE guarded by a check - same
    # pattern services/market_catalog/market_catalog.py/signal_log.py already established.
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    # WAL mode (2026-08-11, real live incident): rollback-journal mode
    # serializes ALL writers and readers against each other for the whole
    # transaction; WAL lets readers proceed concurrently with a writer and
    # is the standard hardening step for exactly the bursty-write scenario
    # that took the app down (trade-tape volume overwhelming a per-call
    # sqlite3.connect()). idempotent - safe to run on every connect.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS config_variants (
            fingerprint   TEXT PRIMARY KEY,
            config_json   TEXT NOT NULL,
            first_seen_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS applied_changes (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            applied_at          REAL NOT NULL,
            config_path         TEXT NOT NULL,
            old_value           TEXT NOT NULL,
            new_value           TEXT NOT NULL,
            rationale           TEXT NOT NULL,
            trade_count         INTEGER NOT NULL,
            fingerprint_before  TEXT NOT NULL,
            fingerprint_after   TEXT NOT NULL,
            auto_applied        INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    # 2026-08-10 (Item 3D) - every real row up to this point came from
    # exactly one place (the Advisory apply route), so backfilling existing
    # rows as 'unified-advisory' via the column default is factually
    # correct, not a placeholder guess.
    _add_column_if_missing(conn, "applied_changes", "source", "TEXT NOT NULL DEFAULT 'unified-advisory'")
    return conn


def strategy_subset(cfg: dict) -> dict:
    """The exact set of fields a fingerprint is computed from - exposed
    separately from fingerprint() so callers (and tests) can inspect what
    actually went into a hash without re-deriving it."""
    return {k: v for k, v in cfg["strategy"].items() if k not in _NON_TUNABLE_FIELDS}


def fingerprint(cfg: dict) -> str:
    subset = strategy_subset(cfg)
    canonical = json.dumps(subset, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def record_variant(fp: str, cfg: dict):
    """Idempotent - first_seen_at is only ever set the first time a given
    fingerprint is observed. Safe to call every trading-loop tick."""
    subset = strategy_subset(cfg)
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO config_variants (fingerprint, config_json, first_seen_at) VALUES (?, ?, ?)",
            (fp, json.dumps(subset, sort_keys=True, default=str), time.time()),
        )


def get_variant(fp: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT fingerprint, config_json, first_seen_at FROM config_variants WHERE fingerprint = ?", (fp,)
        ).fetchone()
    if row is None:
        return None
    return {"fingerprint": row[0], "config": json.loads(row[1]), "first_seen_at": row[2]}


def all_variants() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT fingerprint, config_json, first_seen_at FROM config_variants ORDER BY first_seen_at ASC"
        ).fetchall()
    return [{"fingerprint": r[0], "config": json.loads(r[1]), "first_seen_at": r[2]} for r in rows]


def log_applied_change(
    config_path: str, old_value, new_value, rationale: str, trade_count: int,
    fingerprint_before: str, fingerprint_after: str, auto_applied: bool = False,
    source: str = "manual",
):
    # source (2026-08-10, Item 3D): which of this app's config-change
    # sources produced this row - "manual" (a plain Config-tab save via
    # POST /api/config, previously never logged at all), "unified-advisory"
    # (services/advisory/advisory_engine.py's apply route), or a future
    # "series-analyst"/"full-spectrum-analyst" once the market analyst
    # agent can suggest changes too (Item 3B/3C). Kept as a plain string,
    # not an enum, so a new source never needs a schema migration - same
    # "machine-queryable, not simplified" idea as every other field here,
    # since this table is meant to be read by the engines themselves
    # eventually (e.g. "was this field changed too recently to have enough
    # post-change data yet"), not just rendered in a UI table.
    with _connect() as conn:
        conn.execute(
            """INSERT INTO applied_changes
               (applied_at, config_path, old_value, new_value, rationale, trade_count,
                fingerprint_before, fingerprint_after, auto_applied, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                time.time(), config_path, json.dumps(old_value), json.dumps(new_value), rationale,
                trade_count, fingerprint_before, fingerprint_after, 1 if auto_applied else 0, source,
            ),
        )
    # History-push hook (design §2/§4.3 - loadChangeHistory). Fired
    # unconditionally for both the manual and auto-apply paths, not just
    # auto-apply as design §2's table suggested exempting - that exemption
    # was scoped to "the client already knows" for the ONE browser tab that
    # made the manual apply call and already re-fetches on its own POST
    # response; a second connected tab/browser watching the same dashboard
    # has no way to know about a manual change otherwise. Hooking this
    # write function directly (design §4.3 option 2) is what makes that
    # coverage free rather than requiring a second call-site enumeration
    # pass for "every manual-apply route too."
    history_push.mark_history_changed()


def recent_applied_changes(limit: int = 50, offset: int = 0) -> list[dict]:
    cols = [
        "id", "applied_at", "config_path", "old_value", "new_value", "rationale",
        "trade_count", "fingerprint_before", "fingerprint_after", "auto_applied", "source",
    ]
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM applied_changes ORDER BY applied_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(zip(cols, r))
        d["old_value"] = json.loads(d["old_value"])
        d["new_value"] = json.loads(d["new_value"])
        d["auto_applied"] = bool(d["auto_applied"])
        out.append(d)
    return out


def diff_patch(old_cfg: dict, patch: dict) -> list[tuple[str, object, object]]:
    """Every leaf field a config_store.update(patch) call would actually
    change, as (config_path, old_value, new_value) - mirrors
    ConfigStore.update()'s own one-level-deep merge exactly (a patch is
    always either a bare top-level scalar or one level of {section:
    {field: value}} nesting, never deeper - see config_store.py), so this
    never needs to guess at a shape update() itself doesn't support.
    Skips any field where old_value == new_value - a no-op save (e.g.
    re-submitting the Config tab's form unchanged) shouldn't manufacture a
    change-history row."""
    changes = []
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(old_cfg.get(key), dict):
            for field, new_value in value.items():
                old_value = old_cfg[key].get(field)
                if old_value != new_value:
                    changes.append((f"{key}.{field}", old_value, new_value))
        else:
            old_value = old_cfg.get(key)
            if old_value != value:
                changes.append((key, old_value, value))
    return changes


def applied_changes_count() -> int:
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM applied_changes").fetchone()[0]


def last_applied_at(source: str) -> float | None:
    """The cooldown check every auto-apply path (2026-08-10) needs - "how
    long since we last auto-applied a change from this source" - reusing
    applied_changes' own timestamps rather than a second, parallel
    last-applied tracker that could drift out of sync with the real audit
    trail."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT MAX(applied_at) FROM applied_changes WHERE source = ?", (source,)
        ).fetchone()
    return row[0] if row and row[0] is not None else None


def all_last_applied_by_path() -> dict[str, float]:
    """One query, not one per config_path - the freshness check every
    per-field advisory suggestion needs. Direct, confirmed-live bug report
    (2026-08-11): "if i click apply it just gives me the same evaluation
    and same potential increase value... suggesting a massive bug." Root
    cause: every per-field suggestion function in advisory_engine.py
    recomputes its verdict from the full trade history on every call, with
    no idea whether its own config_path was JUST changed - if zero new
    trades have entered since the last apply, the exact same stale
    evidence (an old avg_pnl/win-rate gap from trades that closed under
    the *previous* value) justifies suggesting yet another nudge in the
    same direction, chaining indefinitely off evidence that never
    actually validated the prior change. advisory_engine.generate_
    recommendations() uses this to drop any suggestion for a field
    changed more recently than the newest trade entered since."""
    with _connect() as conn:
        rows = conn.execute("SELECT config_path, MAX(applied_at) FROM applied_changes GROUP BY config_path").fetchall()
    return {path: ts for path, ts in rows}
