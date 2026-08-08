"""
Config-variant fingerprinting and the audit trail for advisory-engine config
changes (see docs/advisory-engine-plan.md). Answers "which strategy config
was active when this trade was placed" so services/advisory_engine.py can
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

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "config_performance.db"

# The only strategy field that isn't a tunable knob - deliberately excluding
# just this one (rather than hand-listing every field that *is* tunable)
# means a newly added strategy.* field automatically joins the fingerprint
# with no code change here.
_NON_TUNABLE_FIELDS = {"name"}


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
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
):
    with _connect() as conn:
        conn.execute(
            """INSERT INTO applied_changes
               (applied_at, config_path, old_value, new_value, rationale, trade_count,
                fingerprint_before, fingerprint_after, auto_applied)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                time.time(), config_path, json.dumps(old_value), json.dumps(new_value), rationale,
                trade_count, fingerprint_before, fingerprint_after, 1 if auto_applied else 0,
            ),
        )


def recent_applied_changes(limit: int = 50, offset: int = 0) -> list[dict]:
    cols = [
        "id", "applied_at", "config_path", "old_value", "new_value", "rationale",
        "trade_count", "fingerprint_before", "fingerprint_after", "auto_applied",
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


def applied_changes_count() -> int:
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM applied_changes").fetchone()[0]
