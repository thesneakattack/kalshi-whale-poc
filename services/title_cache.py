"""
Persists market_titles/event_titles across restarts.

Before this, main.py's state["market_titles"]/state["event_titles"] were
purely in-memory dicts, seeded empty at import time. That's fine in theory
("accumulates, never overwrites") but breaks in practice two ways: (1) the
fastapi service runs uvicorn --reload, so every .py save during normal
development is a real process restart that wipes both dicts back to {}, and
(2) the watchlist is a rotating top-N-by-volume selection, not a fixed set,
so a ticker whose title was learned before the most recent restart and has
since rotated off the watchlist never gets refetched. Combined, this showed
up as raw tickers (e.g. "KXITFWMATCH-26AUG08MILYOS-MIL") leaking into
signal history, accumulation clusters, and the positions panel wherever
marketLabel() fell back to the bare ticker.

Same one-file-per-concern persistence idiom as services/signal_log.py,
services/paper_broker.py, services/risk_manager.py — see CLAUDE.md.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "title_cache.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_titles (
            ticker TEXT PRIMARY KEY,
            title TEXT,
            yes_sub_title TEXT,
            no_sub_title TEXT,
            event_ticker TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS event_titles (
            event_ticker TEXT PRIMARY KEY,
            title TEXT,
            sub_title TEXT,
            category TEXT
        )
        """
    )
    return conn


def load_market_titles() -> dict[str, dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT ticker, title, yes_sub_title, no_sub_title, event_ticker FROM market_titles"
        ).fetchall()
    return {
        ticker: {"title": title, "yes_sub_title": yes_sub, "no_sub_title": no_sub, "event_ticker": event_ticker}
        for ticker, title, yes_sub, no_sub, event_ticker in rows
    }


def save_market_titles(entries: dict[str, dict]) -> None:
    if not entries:
        return
    with _connect() as conn:
        conn.executemany(
            """
            INSERT INTO market_titles (ticker, title, yes_sub_title, no_sub_title, event_ticker)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                title = excluded.title,
                yes_sub_title = excluded.yes_sub_title,
                no_sub_title = excluded.no_sub_title,
                event_ticker = excluded.event_ticker
            """,
            [
                (ticker, v.get("title"), v.get("yes_sub_title"), v.get("no_sub_title"), v.get("event_ticker"))
                for ticker, v in entries.items()
            ],
        )


def load_event_titles() -> dict[str, dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT event_ticker, title, sub_title, category FROM event_titles").fetchall()
    return {
        event_ticker: {"title": title, "sub_title": sub_title, "category": category}
        for event_ticker, title, sub_title, category in rows
    }


def save_event_titles(entries: dict[str, dict]) -> None:
    if not entries:
        return
    with _connect() as conn:
        conn.executemany(
            """
            INSERT INTO event_titles (event_ticker, title, sub_title, category)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(event_ticker) DO UPDATE SET
                title = excluded.title,
                sub_title = excluded.sub_title,
                category = excluded.category
            """,
            [
                (event_ticker, v.get("title"), v.get("sub_title"), v.get("category"))
                for event_ticker, v in entries.items()
            ],
        )
