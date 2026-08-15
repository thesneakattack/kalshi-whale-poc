"""
Persists state["series_cache"] (Kalshi's ~9,400-series catalog, sorted by
lifetime volume - see main.py's _get_series_cache) across restarts.

Direct request (2026-08-15): "i want event/series/market data like this to
be persistent so i dont have to make heavy api calls over and over again,
only refresh occasionally." _get_series_cache already has its own
occasional-refresh policy (_SERIES_CACHE_TTL_SEC, one hour) - the gap was
that the cache backing it was pure in-memory, seeded empty at import time,
so every restart (including a routine uvicorn --reload during normal
development - CLAUDE.md) forced an immediate full re-fetch regardless of
how fresh the pre-restart data still was. This makes that TTL survive a
restart the same way every other cache in this app already does: on
startup, load() hydrates state["series_cache"] from disk; _get_series_cache
still runs the exact same TTL check it always did, now just measuring
against a fetched_at that isn't reset to zero by a reload.

One-row table, not one-row-per-series - the whole list is always replaced
together (never incrementally updated; that's market_catalog.py's job for
individual markets) and always read back as a whole list, so there's no
real query this would need row-level SQL for. Same one-file-per-concern
idiom as every other services/*.py persistence module (CLAUDE.md), just
with a JSON blob column instead of one column per field since the schema
here is "whatever Kalshi's series objects contain," not a fixed set this
app picks fields out of.
"""
import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "series_cache.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS series_cache (
            id INTEGER PRIMARY KEY CHECK (id = 0),
            fetched_at REAL NOT NULL,
            series_json TEXT NOT NULL
        )
        """
    )
    return conn


def load() -> dict:
    """{"fetched_at": float, "series": list[dict]} - fetched_at=0.0 and
    series=[] (matching state["series_cache"]'s pre-existing initial shape
    exactly) when nothing's been persisted yet, so _get_series_cache's own
    "not cache['series']" check does a real fetch on a genuinely first
    run, same as before this module existed."""
    with _connect() as conn:
        row = conn.execute("SELECT fetched_at, series_json FROM series_cache WHERE id = 0").fetchone()
    if row is None:
        return {"fetched_at": 0.0, "series": []}
    fetched_at, series_json = row
    return {"fetched_at": fetched_at, "series": json.loads(series_json)}


def save(fetched_at: float, series: list[dict]) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO series_cache (id, fetched_at, series_json) VALUES (0, ?, ?)
            ON CONFLICT(id) DO UPDATE SET fetched_at = excluded.fetched_at, series_json = excluded.series_json
            """,
            (fetched_at, json.dumps(series)),
        )
