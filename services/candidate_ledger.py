"""Durable trade_id idempotency for the whale-candidate decision path
(realtime data-plane remediation plan, P0 Task 2). Nothing downstream reads
WhaleSignal.id today and the in-memory dedupe ring is empty after every
uvicorn --reload; this table is the single durable source of truth a
retry, a reconciliation sweep, or a restart can consult before evaluate()
runs again on the same trade_id.

Unwired by this task deliberately - Task 10 gates _handle_signal on
claim(), Task 27 gates reconciliation-recovered trades on it too. This
task only ships the table and its API, additive and inert."""
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "candidate_ledger.db"

_duplicate_count = 0


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS candidates ("
        "trade_id TEXT PRIMARY KEY, ticker TEXT, claimed_at REAL NOT NULL, decision TEXT)"
    )
    return conn


def claim(trade_id: str, *, ticker: str | None = None, now: float | None = None) -> bool:
    """True = newly claimed (this call owns the trade_id). False = a prior
    claim already exists (an INSERT OR IGNORE outcome check, not an
    exception) - the caller must treat this as a duplicate and skip
    re-evaluating."""
    global _duplicate_count
    now = time.time() if now is None else now
    with _connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO candidates (trade_id, ticker, claimed_at) VALUES (?, ?, ?)",
            (trade_id, ticker, now),
        )
        claimed = cur.rowcount == 1
    if not claimed:
        _duplicate_count += 1
    return claimed


def record_decision(trade_id: str, decision: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE candidates SET decision = ? WHERE trade_id = ?", (decision, trade_id))


def stats() -> dict:
    with _connect() as conn:
        claimed = conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
    return {"claimed": claimed, "duplicates": _duplicate_count}
