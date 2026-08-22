"""
Calibration-history tracking - Gap 6 of docs/config-tuning-data-gaps-2026-
08-10.md. services/whale_calibration/confidence_calibration.py already computes everything
needed (overall win rate, per-factor discrimination gaps, current weights)
but only ever as a single point-in-time snapshot recomputed fresh on every
request - there was no way to ask "is calibration improving as more data
and the retuned weights accumulate, or stuck" without manually comparing
numbers pasted from two different sessions.

Own SQLite file (data/calibration_history.db), standard idiom. Snapshots
are rate-limited (due()/record_snapshot() split so the hot per-tick check
stays a single cheap MAX() query - the actual report computation, a full
table scan via signal_log.resolved_signals_with_factors(), only runs when
a snapshot is actually due) rather than one row per request, matching this
app's existing reanalyze_cooldown_sec-style idiom elsewhere. Costs zero API
tokens (pure local computation), so automatic background capture is fine
here - distinct from the market analyst's LLM-cost-gated manual button.
"""
import json
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "calibration_history.db"


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
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at REAL NOT NULL,
            resolved_count INTEGER NOT NULL,
            overall_win_rate_pct REAL,
            per_factor_json TEXT NOT NULL,
            weights_json TEXT NOT NULL
        )
        """
    )
    return conn


def _last_recorded_at() -> float | None:
    with _connect() as conn:
        row = conn.execute("SELECT MAX(recorded_at) FROM snapshots").fetchone()
    return row[0] if row else None


def due(now: float, interval_sec: float) -> bool:
    """Cheap check for the trading-loop tick to call unconditionally - a
    single MAX() query, not the expensive full-table-scan report
    computation. True on the very first call ever (no rows yet)."""
    last = _last_recorded_at()
    return last is None or (now - last) >= interval_sec


def record_snapshot(report: dict, now: float | None = None) -> None:
    """report: confidence_calibration.generate_calibration_report()'s
    non-None 'report' value. Unconditional insert - callers are expected
    to have already checked due() before paying for the report computation
    itself."""
    now = now if now is not None else time.time()
    per_factor = {
        f["factor"]: {"gap_pts": f["gap_pts"], "discriminates": f["discriminates"]}
        for f in report["per_factor"]
    }
    with _connect() as conn:
        conn.execute(
            "INSERT INTO snapshots (recorded_at, resolved_count, overall_win_rate_pct, per_factor_json, weights_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                now, report["resolved_count"], report["overall_win_rate"],
                json.dumps(per_factor), json.dumps(report["current_weights"]),
            ),
        )


def history(limit: int = 100) -> list[dict]:
    """Newest first - a trend line, not a live report (that's still
    confidence_calibration.generate_calibration_report()'s job)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT recorded_at, resolved_count, overall_win_rate_pct, per_factor_json, weights_json "
            "FROM snapshots ORDER BY recorded_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {
            "recorded_at": recorded_at, "resolved_count": resolved_count,
            "overall_win_rate_pct": overall_win_rate_pct,
            "per_factor": json.loads(per_factor_json), "weights": json.loads(weights_json),
        }
        for recorded_at, resolved_count, overall_win_rate_pct, per_factor_json, weights_json in rows
    ]


def clear_all() -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM snapshots")
