"""
Category-at-entry-time capture - the deferred half of Gap 9 (docs/config-
tuning-data-gaps-2026-08-10.md), built at direct follow-up request
(2026-08-10): "add those things, and have them auto-enable and start
getting put into play with my whole system once there *is* enough data."
services/regime_analytics.py could only ever cover hour-of-day/day-of-week
when it first shipped - real category segmentation needed this new
persistence layer first, since market_catalog.category is watchlist-scoped
and rotates, so a historical trade can't be reliably joined back to the
category its market belonged to after the fact (the gap this module's own
predecessor explicitly disclosed rather than working around with a
best-effort guess).

Recorded once per position OPEN (not close - the category a market
belonged to doesn't change over a hold), looked up from
state["market_titles"]/state["event_titles"] at the exact moment main.py's
trading loop places a trade - both already cached in memory every tick, so
this costs zero new API calls. Own SQLite file, standard idiom.
"""
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "trade_category.db"


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
        CREATE TABLE IF NOT EXISTS trade_category (
            ticker TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            recorded_at REAL NOT NULL
        )
        """
    )
    return conn


def record_category(ticker: str, category: str | None, now: float | None = None) -> None:
    """No-op when category is unknown (None/empty) - never overwrites a
    real, previously-recorded category with an unknown one, and never
    records a row with nothing useful in it."""
    if not ticker or not category:
        return
    now = now if now is not None else time.time()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO trade_category (ticker, category, recorded_at) VALUES (?, ?, ?) "
            "ON CONFLICT(ticker) DO UPDATE SET category = excluded.category, recorded_at = excluded.recorded_at",
            (ticker, category, now),
        )


def categories_for_tickers(tickers: list[str]) -> dict[str, str]:
    """Bulk lookup, not one query per ticker - regime_analytics.by_category()
    needs this for potentially hundreds of trade rows at once."""
    unique = list({t for t in tickers if t})
    if not unique:
        return {}
    with _connect() as conn:
        placeholders = ",".join("?" for _ in unique)
        rows = conn.execute(
            f"SELECT ticker, category FROM trade_category WHERE ticker IN ({placeholders})", unique,
        ).fetchall()
    return dict(rows)


def clear_all() -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM trade_category")
