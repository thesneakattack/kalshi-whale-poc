"""
Remembers a deliberate "no thanks" on a suggestion (History tab, direct
request 2026-08-14/15: redesign the History tab so a complete beginner can
"see and act and observe and most importantly choose to hold back" -
without this, declining a suggestion is purely cosmetic: it hides one card
until the next poll re-fetches the exact same suggestion and shows it
again, which doesn't actually honor "hold back" as a real choice.

Every suggestion this app produces (advisory_engine.rec_id(), reused
identically by the series-analyst/full-spectrum modes) is already keyed by
sha256(config_path|suggested_value|n) - a suggestion's id already encodes
its own evidence. That means declining by id needs no separate staleness/
expiry logic of its own: the moment the underlying evidence genuinely
changes (n grows, or the suggested value shifts), a brand-new id is minted
automatically and the old decline simply doesn't apply to it - the same
"don't nag with the same evidence, but do resurface once something
genuinely changed" property _drop_stale_recommendations already has for
staleness, for free, from the id scheme alone.

Same persistence idiom as every other module in services/ (own SQLite
file, CREATE TABLE IF NOT EXISTS, WAL mode - see candidate_log.py/
calibration_history.py for the precedent this mirrors).
"""
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "suggestion_decisions.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS declined_suggestions (
            id TEXT PRIMARY KEY,
            config_path TEXT NOT NULL,
            rationale TEXT,
            declined_at REAL NOT NULL
        )
        """
    )
    return conn


def decline(suggestion_id: str, config_path: str, rationale: str | None = None, now: float | None = None) -> None:
    """Idempotent - declining an already-declined id just refreshes
    declined_at, same "second click is a no-op, not an error" convention
    as every other write-once-ish table in this app."""
    now = now if now is not None else time.time()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO declined_suggestions (id, config_path, rationale, declined_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET declined_at = excluded.declined_at
            """,
            (suggestion_id, config_path, rationale, now),
        )


def undecline(suggestion_id: str) -> bool:
    """"You can revisit this anytime" (direct request) - forgets a decline
    so the suggestion (or, more likely by the time someone revisits, its
    successor under a new id once evidence moved on) can surface again.
    Returns whether a row actually existed to remove."""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM declined_suggestions WHERE id = ?", (suggestion_id,))
        return cur.rowcount > 0


def declined_ids() -> set[str]:
    """All currently-declined suggestion ids - cheap (this table only ever
    holds as many rows as a human has actually clicked "no thanks" on,
    nothing accumulates from background activity), safe to call on every
    recommendation-generating request."""
    with _connect() as conn:
        rows = conn.execute("SELECT id FROM declined_suggestions").fetchall()
    return {r[0] for r in rows}


def list_declined(limit: int = 50) -> list[dict]:
    """For the Advanced-section "previously declined" list - newest first."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, config_path, rationale, declined_at FROM declined_suggestions "
            "ORDER BY declined_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {"id": r[0], "config_path": r[1], "rationale": r[2], "declined_at": r[3]}
        for r in rows
    ]


def clear_all() -> None:
    """Danger Zone reset hook, same convention as every other module's
    clear_all (market_catalog.clear_all, candidate_log's equivalent, etc.)."""
    with _connect() as conn:
        conn.execute("DELETE FROM declined_suggestions")
