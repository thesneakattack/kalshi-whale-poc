"""
Rejected-candidate logging - closes Gap 1 of docs/config-tuning-data-gaps-
2026-08-10.md (the "counterfactual gap"). Every entry/discovery gate in this
app (strategy.entry_threshold, strategy.min_whale_winrate_pct,
whale_watcher_kalshi.min_notional_usd) only ever produces a boolean "did
this candidate pass" - nothing previously recorded what happened to a
candidate that failed. advisory_engine's own entry-threshold/longshot
recommendations return None against real trade history specifically
because there's no data on candidates that scored just below the bar,
only ones that cleared it - this module is what that data would come
from.

This does NOT act on rejected candidates (no trade is ever placed from
this module) - it only observes. Dedup key is (ticker, strategy,
gate_name): a candidate that keeps failing the same gate on repeated
evaluation updates its one row in place rather than growing a new row per
tick - the meaningful data point is "what did this gate's most recent
observed value look like, and how did the market eventually resolve," not
a full tick-by-tick history of a value that mostly drifts slowly. Once a
ticker resolves, the strategy already skips it before reaching any gate
(the market_results check runs first in evaluate()),
so a resolved row is never overwritten by a later rejection - no extra
guard needed for that race.

Same persistence idiom as every other module in services/ (own SQLite
file, CREATE TABLE IF NOT EXISTS, resolved via the already-fetched
market_results dict each tick - see market_analyst_agent.
resolve_from_market_results for the precedent this mirrors, zero new API
calls needed).
"""
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "candidate_log.db"


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
        CREATE TABLE IF NOT EXISTS rejected_candidates (
            ticker TEXT NOT NULL,
            strategy TEXT NOT NULL,
            gate_name TEXT NOT NULL,
            observed_value REAL,
            threshold_value REAL,
            side TEXT,
            rejected_at REAL NOT NULL,
            resolved INTEGER NOT NULL DEFAULT 0,
            result TEXT,
            resolved_at REAL,
            PRIMARY KEY (ticker, strategy, gate_name)
        )
        """
    )
    return conn


def record_rejection(
    ticker: str, strategy: str, gate_name: str,
    observed_value: float | None, threshold_value: float | None,
    side: str | None = None, now: float | None = None,
) -> None:
    """side, when known, is the direction a trade would have taken had this
    gate not rejected the candidate (e.g. the whale print's own side) - not
    every gate can supply this, and that's fine: gate_summary() only computes a
    hypothetical win rate for rows where side is present, and reports the
    plain yes/no resolution split otherwise."""
    now = now if now is not None else time.time()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO rejected_candidates
                (ticker, strategy, gate_name, observed_value, threshold_value, side, rejected_at, resolved)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(ticker, strategy, gate_name) DO UPDATE SET
                observed_value = excluded.observed_value,
                threshold_value = excluded.threshold_value,
                side = excluded.side,
                rejected_at = excluded.rejected_at
            WHERE rejected_candidates.resolved = 0
            """,
            (ticker, strategy, gate_name, observed_value, threshold_value, side, now),
        )


def resolve_from_market_results(market_results: dict) -> int:
    """Same shape as market_analyst_agent.resolve_from_market_results -
    market_results is the {ticker: "yes"/"no"/""/None} mapping main.py's
    trading loop already builds from that tick's fetched markets, so this
    costs zero new API calls. Returns how many rows were resolved this
    call."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT rowid, ticker FROM rejected_candidates WHERE resolved = 0",
        ).fetchall()
        resolved_count = 0
        now = time.time()
        for rowid, ticker in rows:
            result = (market_results.get(ticker) or "").strip().lower()
            if result not in ("yes", "no"):
                continue
            conn.execute(
                "UPDATE rejected_candidates SET resolved = 1, result = ?, resolved_at = ? WHERE rowid = ?",
                (result, now, rowid),
            )
            resolved_count += 1
        return resolved_count


def gate_summary() -> list[dict]:
    """One row per (strategy, gate_name) - the direct answer to "what would
    have happened to the candidates this gate rejected." hypothetical_win_rate
    is only populated when at least one resolved row for this gate carries a
    known side (see record_rejection's docstring) - None otherwise, not 0,
    since a missing side means "can't be computed," not "0% win rate.\""""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT strategy, gate_name, side, result, resolved FROM rejected_candidates",
        ).fetchall()
    grouped: dict[tuple, dict] = {}
    for strategy, gate_name, side, result, resolved in rows:
        key = (strategy, gate_name)
        g = grouped.setdefault(key, {
            "strategy": strategy, "gate_name": gate_name,
            "rejected_count": 0, "resolved_count": 0,
            "yes_count": 0, "no_count": 0,
            "_sided_total": 0, "_sided_wins": 0,
        })
        g["rejected_count"] += 1
        if resolved:
            g["resolved_count"] += 1
            if result == "yes":
                g["yes_count"] += 1
            elif result == "no":
                g["no_count"] += 1
            if side in ("yes", "no"):
                g["_sided_total"] += 1
                if result == side:
                    g["_sided_wins"] += 1
    out = []
    for g in grouped.values():
        sided_total = g.pop("_sided_total")
        sided_wins = g.pop("_sided_wins")
        g["hypothetical_win_rate"] = round(100 * sided_wins / sided_total, 1) if sided_total > 0 else None
        g["hypothetical_win_rate_n"] = sided_total
        out.append(g)
    out.sort(key=lambda g: (-g["resolved_count"], g["strategy"], g["gate_name"]))
    return out


def clear_all() -> None:
    """Danger-zone reset support, same convention as market_catalog.
    clear_all()/market_history.clear_all() - drops accumulated rows, not
    the table itself."""
    with _connect() as conn:
        conn.execute("DELETE FROM rejected_candidates")


def count_range(before: float | None = None, after: float | None = None) -> int:
    """Danger Zone preview support (2026-08-16) - mirrors signal_log.
    count_range's (after, before] convention exactly. Note rejected_
    candidates is an upsert-per-(ticker,strategy,gate_name) table (only
    the most recent rejection survives per key, see record_rejection's own
    docstring) - a range here scopes by that latest rejected_at, not a
    full rejection history, same caveat that applies to clear_range."""
    where, params = _range_where(before, after)
    with _connect() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM rejected_candidates {where}", params).fetchone()[0]


def clear_range(before: float | None = None, after: float | None = None) -> int:
    """Deletes only rows whose rejected_at falls in (after, before],
    instead of the whole table - same "purge a noisy stretch without
    losing what's on either side of it" reasoning as signal_log.
    clear_range."""
    where, params = _range_where(before, after)
    with _connect() as conn:
        cur = conn.execute(f"DELETE FROM rejected_candidates {where}", params)
        return cur.rowcount


def _range_where(before: float | None, after: float | None) -> tuple[str, list]:
    clauses, params = [], []
    if after is not None:
        clauses.append("rejected_at > ?")
        params.append(after)
    if before is not None:
        clauses.append("rejected_at <= ?")
        params.append(before)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params
