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


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, coltype: str):
    # data/title_cache.db is a live file the running dev server reads/
    # writes (CLAUDE.md) - CREATE TABLE IF NOT EXISTS alone doesn't add a
    # column to an existing table with existing rows, so a new column needs
    # an explicit, idempotent ALTER TABLE guarded by a check - same pattern
    # services/market_catalog.py/paper_broker.py/signal_log.py already
    # established.
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


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
    # Real Kalshi field, already fetched on every get_event() call but
    # previously discarded - lets a 2-outcome event ("Toronto vs
    # Philadelphia Winner") be told apart from a genuine multi-outcome one
    # ("Wyndham Championship Winner", 60+ golfers) or an event whose
    # sibling markets are independent props (not one mutually-exclusive
    # question at all - e.g. "Max Scherzer 15+ outs" and "Aaron Nola 18+
    # outs" sharing an event). Added after the table above already had live
    # rows, hence the guarded ALTER TABLE.
    _add_column_if_missing(conn, "event_titles", "mutually_exclusive", "INTEGER")
    # product_metadata.competition/competition_scope - real Kalshi fields,
    # already fetched on every get_event() call but previously discarded.
    # Direct display value: "Wyndham Championship" for a golf pairing card,
    # confirmed live - not used for grouping (that's series_of/round_robin_
    # select's job, and this field is too generic for that on some series -
    # see ROADMAP.md), just shown as real context on the event card.
    _add_column_if_missing(conn, "event_titles", "competition", "TEXT")
    _add_column_if_missing(conn, "event_titles", "competition_scope", "TEXT")
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
        rows = conn.execute(
            "SELECT event_ticker, title, sub_title, category, mutually_exclusive, competition, competition_scope "
            "FROM event_titles"
        ).fetchall()
    return {
        event_ticker: {
            "title": title, "sub_title": sub_title, "category": category,
            "mutually_exclusive": bool(mutually_exclusive) if mutually_exclusive is not None else None,
            "competition": competition, "competition_scope": competition_scope,
        }
        for event_ticker, title, sub_title, category, mutually_exclusive, competition, competition_scope in rows
    }


def save_event_titles(entries: dict[str, dict]) -> None:
    if not entries:
        return
    with _connect() as conn:
        conn.executemany(
            """
            INSERT INTO event_titles
                (event_ticker, title, sub_title, category, mutually_exclusive, competition, competition_scope)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_ticker) DO UPDATE SET
                title = excluded.title,
                sub_title = excluded.sub_title,
                category = excluded.category,
                mutually_exclusive = excluded.mutually_exclusive,
                competition = excluded.competition,
                competition_scope = excluded.competition_scope
            """,
            [
                (
                    event_ticker, v.get("title"), v.get("sub_title"), v.get("category"),
                    v.get("mutually_exclusive"), v.get("competition"), v.get("competition_scope"),
                )
                for event_ticker, v in entries.items()
            ],
        )
