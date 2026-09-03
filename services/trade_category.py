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
import contextlib
import sqlite3
import time
from pathlib import Path

from services import db

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "trade_category.db"


def _init_trade_category(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trade_category (
            ticker TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            recorded_at REAL NOT NULL
        )
        """
    )


db.register_schema("trade_category", _init_trade_category)


@contextlib.contextmanager
def _connect():
    """Every existing `with _connect() as conn:` call site keeps working
    unchanged - now backed by services/db.py's closing connect(). WAL mode
    is set by db.connect() itself, same as every other migrated module.

    subcategory (2026-08-16 direct request: "before falling back to
    category winrate from series winrate, theres a middle step... by
    subcategory (e.g., baseball, football)"). Sourced from Kalshi's own
    per-event `competition` field (e.g. "Pro Baseball"), reverse-mapped
    to its SPORT ("Baseball") via category_metadata's sport_by_competition
    (main.py's _sport_for_event/_fetch_category_metadata) - checked
    docs/kalshi/get-filters-for-sports.md per standing instruction before
    guessing further: filters_by_sports nests competition WITHIN sport
    (filters_by_sports["Baseball"]["competitions"] contains "Pro
    Baseball"/"Japan NPB"/"Korea KBO"/"Mexico LMB" - several competitions,
    one sport), so "Baseball" is the real match for the user's own
    "baseball, football" examples, not the finer per-league string alone.
    NOT category_tags: that field's semantics changed under this same
    branch (kalshi-category-data-completeness Task 4) - it used to be
    the same full facet-filter vocabulary on every event in a category
    (no per-event information), but a task-review round found real
    frontend consumers depending on it and rewired it to real per-SERIES
    tags (main.py's _build_series_tags_cache/state["series_cache"]) -
    still not the finer sport/competition granularity this function
    needs (two events of the same series still share identical tags),
    so the reasoning above still holds, just via a different, now-
    accurate justification than the original "carries nothing" claim.
    Same "needs its own capture at entry time" reasoning as category
    itself (this module's own docstring) - market_catalog/event_titles
    are watchlist-scoped and rotate, so a historical trade can't be
    joined back to it after the fact without persisting it here too.
    Nullable/idempotent-migration: not every event carries a competition
    value (non-sports categories generally don't), and rows recorded
    before this field existed have none to backfill.

    The inlined PRAGMA table_info guard is replaced with the shared
    db.add_column_if_missing - same idempotent-check shape, consolidating
    onto one implementation (architecture audit's Sec 9.2 finding this
    whole migration exists partly to close)."""
    with db.connect(DB_PATH, tables=("trade_category",)) as conn:
        db.add_column_if_missing(conn, "trade_category", "subcategory", "TEXT")
        yield conn


def record_category(ticker: str, category: str | None, now: float | None = None, subcategory: str | None = None) -> None:
    """No-op when category is unknown (None/empty) - never overwrites a
    real, previously-recorded category with an unknown one, and never
    records a row with nothing useful in it. subcategory is optional (most
    categories don't have one) and independently nullable - a category
    update should never blow away an already-known subcategory by passing
    None, so it's only overwritten when a real value is actually given."""
    if not ticker or not category:
        return
    now = now if now is not None else time.time()
    with _connect() as conn:
        if subcategory:
            conn.execute(
                "INSERT INTO trade_category (ticker, category, recorded_at, subcategory) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(ticker) DO UPDATE SET category = excluded.category, recorded_at = excluded.recorded_at, "
                "subcategory = excluded.subcategory",
                (ticker, category, now, subcategory),
            )
        else:
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


def subcategories_for_tickers(tickers: list[str]) -> dict[str, str]:
    """Mirrors categories_for_tickers - regime_analytics.by_subcategory()'s
    bulk lookup. Only tickers with a real recorded subcategory are present
    in the result (same "excluded, not fabricated" convention as category
    itself)."""
    unique = list({t for t in tickers if t})
    if not unique:
        return {}
    with _connect() as conn:
        placeholders = ",".join("?" for _ in unique)
        rows = conn.execute(
            f"SELECT ticker, subcategory FROM trade_category WHERE ticker IN ({placeholders}) "
            "AND subcategory IS NOT NULL",
            unique,
        ).fetchall()
    return dict(rows)


def clear_all() -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM trade_category")


def count_range(before: float | None = None, after: float | None = None) -> int:
    """Danger Zone preview support (2026-08-16) - mirrors signal_log.
    count_range's (after, before] convention."""
    where, params = _range_where(before, after)
    with _connect() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM trade_category {where}", params).fetchone()[0]


def clear_range(before: float | None = None, after: float | None = None) -> int:
    """Deletes only rows whose recorded_at falls in (after, before]."""
    where, params = _range_where(before, after)
    with _connect() as conn:
        cur = conn.execute(f"DELETE FROM trade_category {where}", params)
        return cur.rowcount


def _range_where(before: float | None, after: float | None) -> tuple[str, list]:
    clauses, params = [], []
    if after is not None:
        clauses.append("recorded_at > ?")
        params.append(after)
    if before is not None:
        clauses.append("recorded_at <= ?")
        params.append(before)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params
