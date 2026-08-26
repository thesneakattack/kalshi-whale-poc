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
    # WAL mode (2026-08-11, real live incident): rollback-journal mode
    # serializes ALL writers and readers against each other for the whole
    # transaction; WAL lets readers proceed concurrently with a writer and
    # is the standard hardening step for exactly the bursty-write scenario
    # that took the app down (trade-tape volume overwhelming a per-call
    # sqlite3.connect()). idempotent - safe to run on every connect. Added
    # here (code-review fix, finding #4) - every sibling persistence module
    # already has this; this one was missed, and claim()/record_decision()
    # are called on the exchange-wide hot path (a claim per whale-sized
    # print), the same write-volume shape the original incident was.
    conn.execute("PRAGMA journal_mode=WAL")
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


def decision_for(trade_id: str) -> str | None:
    """The decision recorded for trade_id, or None if it was never claimed
    or never reached record_decision (P2 Task 10 - read-side complement to
    record_decision, used by tests and any future reconciliation sweep)."""
    with _connect() as conn:
        row = conn.execute("SELECT decision FROM candidates WHERE trade_id = ?", (trade_id,)).fetchone()
    return row[0] if row else None


def stats() -> dict:
    with _connect() as conn:
        claimed = conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
    return {"claimed": claimed, "duplicates": _duplicate_count}
