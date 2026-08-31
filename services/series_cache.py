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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS series_metadata (
            ticker TEXT PRIMARY KEY,
            category TEXT,
            frequency TEXT,
            tags_json TEXT,
            settlement_sources_json TEXT,
            contract_url TEXT,
            contract_terms_url TEXT,
            fee_type TEXT,
            fee_multiplier REAL,
            additional_prohibitions_json TEXT,
            exchange_index INTEGER,
            volume_fp TEXT,
            last_updated_ts TEXT,
            fetched_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS series_tags (
            ticker TEXT NOT NULL,
            tag TEXT NOT NULL,
            PRIMARY KEY (ticker, tag)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_series_metadata_category ON series_metadata (category)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_series_tags_tag ON series_tags (tag)")
    return conn


def _series_metadata_row(s: dict, fetched_at: float) -> tuple:
    """Defensive .get() throughout, matching _get_series_cache's own style
    (float(s.get("volume_fp") or 0) in catalog_scan.py) - get-series.md marks
    tags/settlement_sources/additional_prohibitions all nullable, so a bare
    series dict from a live payload must not raise. The *_json columns are
    always json.dumps(x or []), never None: series_tags' own population loop
    below iterates s.get("tags") or [] the same way, and an empty-but-valid
    JSON array is a cleaner contract than a nullable one for a column named
    _json."""
    return (
        s["ticker"],
        s.get("category"),
        s.get("frequency"),
        json.dumps(s.get("tags") or []),
        json.dumps(s.get("settlement_sources") or []),
        s.get("contract_url"),
        s.get("contract_terms_url"),
        s.get("fee_type"),
        s.get("fee_multiplier"),
        json.dumps(s.get("additional_prohibitions") or []),
        s.get("exchange_index"),
        s.get("volume_fp"),
        s.get("last_updated_ts"),
        fetched_at,
    )


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
        conn.executemany(
            """
            INSERT INTO series_metadata (ticker, category, frequency, tags_json,
                settlement_sources_json, contract_url, contract_terms_url, fee_type, fee_multiplier,
                additional_prohibitions_json, exchange_index, volume_fp, last_updated_ts, fetched_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(ticker) DO UPDATE SET category=excluded.category, frequency=excluded.frequency,
                tags_json=excluded.tags_json, settlement_sources_json=excluded.settlement_sources_json,
                contract_url=excluded.contract_url, contract_terms_url=excluded.contract_terms_url,
                fee_type=excluded.fee_type, fee_multiplier=excluded.fee_multiplier,
                additional_prohibitions_json=excluded.additional_prohibitions_json,
                exchange_index=excluded.exchange_index, volume_fp=excluded.volume_fp,
                last_updated_ts=excluded.last_updated_ts, fetched_at=excluded.fetched_at
            """,
            [_series_metadata_row(s, fetched_at) for s in series],
        )
        # Delete-then-reinsert per ticker, not a diff: this batch's tag list
        # for a series fully replaces last batch's, since get-series.md's
        # tags array can shrink upstream and a stale series_tags row would
        # otherwise never be cleaned up. `series` is always the full fetched
        # batch (same as the blob write above), so tickers_this_batch covers
        # every series this refresh touched - not a fetched_at-scoped delete.
        tickers_this_batch = [s["ticker"] for s in series]
        conn.executemany(
            "DELETE FROM series_tags WHERE ticker = ?",
            [(t,) for t in tickers_this_batch],
        )
        conn.executemany(
            "INSERT OR IGNORE INTO series_tags (ticker, tag) VALUES (?, ?)",
            [(s["ticker"], tag) for s in series for tag in (s.get("tags") or [])],
        )
