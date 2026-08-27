"""
Audit trail for every Danger Zone reset - direct request (2026-08-16),
after a real investigation this session spent well over an hour
reconstructing (from git history, config_performance.db timestamps, and
memory) when and why signal_log.db and paper_broker.db had each lost days
of accumulated history, with no record anywhere of what happened or why.
Every domain's own clear_range()/clear_all() already existed; nothing
previously recorded that a clear had happened at all, let alone what was
in scope or how much data it removed - this module is that missing
record, own SQLite file, same idiom as every other persisted domain in
this app.

Written by main.py's /api/reset handler on every call, one row per
domain actually cleared, with a before/after row count so "how much did
this lose" is answered by a query instead of an hour of forensics next
time.
"""
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "reset_log.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reset_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            executed_at REAL NOT NULL,
            domain TEXT NOT NULL,
            scope TEXT NOT NULL,
            range_start REAL,
            range_end REAL,
            rows_before INTEGER,
            rows_deleted INTEGER,
            note TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reset_events_executed_at ON reset_events (executed_at)")
    return conn


def record(
    domain: str, scope: str, rows_before: int | None, rows_deleted: int | None,
    range_start: float | None = None, range_end: float | None = None,
    note: str | None = None, executed_at: float | None = None,
) -> None:
    """scope is one of "all"/"before"/"after"/"between" - matches the same
    vocabulary the /api/reset request body and the Danger Zone UI use, so
    a row here reads the same way the action that produced it was framed
    to the person who triggered it."""
    executed_at = executed_at if executed_at is not None else time.time()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO reset_events "
            "(executed_at, domain, scope, range_start, range_end, rows_before, rows_deleted, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (executed_at, domain, scope, range_start, range_end, rows_before, rows_deleted, note),
        )


def recent(limit: int = 50) -> list[dict]:
    cols = ["id", "executed_at", "domain", "scope", "range_start", "range_end", "rows_before", "rows_deleted", "note"]
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM reset_events ORDER BY executed_at DESC LIMIT ?", (limit,),
        ).fetchall()
    return [dict(zip(cols, r)) for r in rows]
