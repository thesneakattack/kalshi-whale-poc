"""Shared SQLite layer for every market_analyst_agent mode - DB_PATH,
_connect() (creates all three tables: per-market `analyses`, per-series
`series_analyses`, full-spectrum `full_spectrum_analyses`), and clear_all()
(wipes all three). Kept together deliberately: a function's global-name
lookups (DB_PATH here) always resolve against the module it's *defined* in,
never wherever a caller imported the name from, so _connect()/clear_all()
have to live in the same file as DB_PATH itself - see this package's
__init__.py docstring for the full gotcha writeup.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "market_analyst.db"


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
        CREATE TABLE IF NOT EXISTS analyses (
            id TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            series TEXT NOT NULL,
            market_price REAL NOT NULL,
            estimated_probability REAL NOT NULL,
            llm_confidence REAL NOT NULL,
            reasoning TEXT NOT NULL,
            model TEXT NOT NULL,
            analyzed_at REAL NOT NULL,
            resolved INTEGER NOT NULL DEFAULT 0,
            correct INTEGER,
            resolved_at REAL
        )
        """
    )
    # Per-series analysis mode (Item 3B, 2026-08-10) - a separate table
    # rather than shoehorning series rows into `analyses` above: that
    # table's schema is tightly single-market-probability-shaped
    # (estimated_probability/llm_confidence/etc. are all NOT NULL, and
    # SQLite can't relax a NOT NULL constraint via an additive ALTER TABLE
    # anyway), whereas a series analysis has no probability estimate at
    # all - just a summary and zero or more config-change suggestions. A
    # new table is itself an additive schema change (CLAUDE.md), not a
    # drop-and-recreate of the existing one.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS series_analyses (
            id TEXT PRIMARY KEY,
            series TEXT NOT NULL,
            summary TEXT NOT NULL,
            suggestions_json TEXT NOT NULL,
            model TEXT NOT NULL,
            analyzed_at REAL NOT NULL
        )
        """
    )
    # "Feed the Analyst" full-spectrum scan (Item 3C, 2026-08-10) - same
    # shape as series_analyses (a summary + a list of already-validated,
    # unified-shape suggestions), just with no series/ticker subject at
    # all, so it gets its own table rather than a magic sentinel value in
    # series_analyses' NOT NULL `series` column.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS full_spectrum_analyses (
            id TEXT PRIMARY KEY,
            summary TEXT NOT NULL,
            suggestions_json TEXT NOT NULL,
            model TEXT NOT NULL,
            analyzed_at REAL NOT NULL
        )
        """
    )
    return conn


def clear_all():
    with _connect() as conn:
        conn.execute("DELETE FROM analyses")
        conn.execute("DELETE FROM series_analyses")
        conn.execute("DELETE FROM full_spectrum_analyses")
