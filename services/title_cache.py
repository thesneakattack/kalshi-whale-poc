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

event_titles' extra columns (strike_date and everything below it) were a
second, later gap of the exact same shape (2026-08-15 direct request: "i
want event/series/market data like this to be persistent so i dont have to
make heavy api calls over and over again, only refresh occasionally").
main.py's _fetch_event_titles has always extracted these fields (they're
part of its own required_event_fields re-fetch check) but this table never
had columns for them, so they lived only in the in-memory state["event_titles"]
dict - meaning every restart silently dropped them, and
required_event_fields' own "re-fetch if a required field is missing" check
then forced a full get_event() re-fetch of every already-cached event on
the very next tick. Confirmed live: this is exactly the field
services/market_events/event_schedule.py depends on (strike_date is a real, precise
schedule signal for single-date announcement events like Fed decisions -
confirmed live against KXFED - that isn't covered by the milestone API at
all), so losing it every restart wasn't just wasted calls, it was silently
degrading that feature's best source back to its fallbacks after every
reload.
"""
import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "title_cache.db"


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, coltype: str):
    # data/title_cache.db is a live file the running dev server reads/
    # writes (CLAUDE.md) - CREATE TABLE IF NOT EXISTS alone doesn't add a
    # column to an existing table with existing rows, so a new column needs
    # an explicit, idempotent ALTER TABLE guarded by a check - same pattern
    # services/market_catalog/market_catalog.py/paper_broker.py/signal_log.py already
    # established.
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


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
    # The rest of main.py._fetch_event_titles' required_event_fields set -
    # see this module's own docstring for the restart-drops-them-silently
    # gap this closes. product_metadata/settlement_sources are real nested
    # structures (a dict and a list of dicts respectively), stored as JSON
    # text same as every other non-scalar field this app persists.
    _add_column_if_missing(conn, "event_titles", "series_ticker", "TEXT")
    _add_column_if_missing(conn, "event_titles", "available_on_brokers", "INTEGER")
    _add_column_if_missing(conn, "event_titles", "collateral_return_type", "TEXT")
    _add_column_if_missing(conn, "event_titles", "strike_date", "TEXT")
    _add_column_if_missing(conn, "event_titles", "strike_period", "TEXT")
    _add_column_if_missing(conn, "event_titles", "fee_type_override", "TEXT")
    _add_column_if_missing(conn, "event_titles", "fee_multiplier_override", "REAL")
    _add_column_if_missing(conn, "event_titles", "last_updated_ts", "TEXT")
    _add_column_if_missing(conn, "event_titles", "product_metadata_json", "TEXT")
    _add_column_if_missing(conn, "event_titles", "settlement_sources_json", "TEXT")
    return conn


def market_title_fields(m: dict) -> dict:
    """The title/yes_sub_title/no_sub_title fallback shape, in one place -
    previously reimplemented independently in three spots (main.py's
    new_market_titles builder, the /api/markets/search route, and
    services/market_catalog/market_catalog.py's upsert_markets), each free to drift from
    the other two. `title` falls back to `yes_sub_title` (a market can have
    a real yes_sub_title with no separate title at all - e.g. one child of
    a multi-outcome event) and then to the raw ticker as a last resort,
    never left blank."""
    return {
        "title": m.get("title") or m.get("yes_sub_title") or m.get("ticker"),
        "yes_sub_title": m.get("yes_sub_title"),
        "no_sub_title": m.get("no_sub_title"),
    }


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
            "SELECT event_ticker, title, sub_title, category, mutually_exclusive, competition, competition_scope, "
            "series_ticker, available_on_brokers, collateral_return_type, strike_date, strike_period, "
            "fee_type_override, fee_multiplier_override, last_updated_ts, product_metadata_json, settlement_sources_json "
            "FROM event_titles"
        ).fetchall()
    result = {}
    for (
        event_ticker, title, sub_title, category, mutually_exclusive, competition, competition_scope,
        series_ticker, available_on_brokers, collateral_return_type, strike_date, strike_period,
        fee_type_override, fee_multiplier_override, last_updated_ts, product_metadata_json, settlement_sources_json,
    ) in rows:
        result[event_ticker] = {
            "title": title, "sub_title": sub_title, "category": category,
            "mutually_exclusive": bool(mutually_exclusive) if mutually_exclusive is not None else None,
            "competition": competition, "competition_scope": competition_scope,
            "series_ticker": series_ticker,
            "available_on_brokers": bool(available_on_brokers) if available_on_brokers is not None else None,
            "collateral_return_type": collateral_return_type,
            "strike_date": strike_date,
            "strike_period": strike_period,
            "fee_type_override": fee_type_override,
            "fee_multiplier_override": fee_multiplier_override,
            "last_updated_ts": last_updated_ts,
            "product_metadata": json.loads(product_metadata_json) if product_metadata_json else {},
            "settlement_sources": json.loads(settlement_sources_json) if settlement_sources_json else [],
        }
    return result


def save_event_titles(entries: dict[str, dict]) -> None:
    if not entries:
        return
    with _connect() as conn:
        conn.executemany(
            """
            INSERT INTO event_titles
                (event_ticker, title, sub_title, category, mutually_exclusive, competition, competition_scope,
                 series_ticker, available_on_brokers, collateral_return_type, strike_date, strike_period,
                 fee_type_override, fee_multiplier_override, last_updated_ts, product_metadata_json, settlement_sources_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_ticker) DO UPDATE SET
                title = excluded.title,
                sub_title = excluded.sub_title,
                category = excluded.category,
                mutually_exclusive = excluded.mutually_exclusive,
                competition = excluded.competition,
                competition_scope = excluded.competition_scope,
                series_ticker = excluded.series_ticker,
                available_on_brokers = excluded.available_on_brokers,
                collateral_return_type = excluded.collateral_return_type,
                strike_date = excluded.strike_date,
                strike_period = excluded.strike_period,
                fee_type_override = excluded.fee_type_override,
                fee_multiplier_override = excluded.fee_multiplier_override,
                last_updated_ts = excluded.last_updated_ts,
                product_metadata_json = excluded.product_metadata_json,
                settlement_sources_json = excluded.settlement_sources_json
            """,
            [
                (
                    event_ticker, v.get("title"), v.get("sub_title"), v.get("category"),
                    v.get("mutually_exclusive"), v.get("competition"), v.get("competition_scope"),
                    v.get("series_ticker"), v.get("available_on_brokers"), v.get("collateral_return_type"),
                    v.get("strike_date"), v.get("strike_period"),
                    v.get("fee_type_override"), v.get("fee_multiplier_override"), v.get("last_updated_ts"),
                    json.dumps(v["product_metadata"]) if v.get("product_metadata") else None,
                    json.dumps(v["settlement_sources"]) if v.get("settlement_sources") else None,
                )
                for event_ticker, v in entries.items()
            ],
        )


def fee_override_for_ticker(ticker: str) -> tuple[str | None, float | None]:
    """(fee_type_override, fee_multiplier_override) for the event `ticker`'s
    market belongs to (market_titles.event_ticker -> the matching
    event_titles row) - a single indexed join, not the full-table
    load_market_titles()/load_event_titles() scans above, since
    services/kalshi_fees.py calls this on every fee calculation (issues
    #264/#258) rather than once at startup.

    (None, None) when the market isn't cached yet, its event isn't cached
    yet, or the event carries no active override in either column -
    docs/kalshi/get-event-fee-changes.md: "If fee_type_override and
    fee_multiplier_override are null, that indicates the override is
    cleared." Callers fall back to the series-level fee table in that
    case, exactly as if this function didn't exist - never to a guessed
    value."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT et.fee_type_override, et.fee_multiplier_override "
            "FROM market_titles mt JOIN event_titles et ON et.event_ticker = mt.event_ticker "
            "WHERE mt.ticker = ?",
            (ticker,),
        ).fetchone()
    return (row[0], row[1]) if row else (None, None)


def series_ticker_for(ticker: str) -> str | None:
    """The real series_ticker `ticker`'s market belongs to
    (market_titles.event_ticker -> the matching event_titles row's
    series_ticker column) - a single indexed join, same shape as
    fee_override_for_ticker() above, one hop further.

    docs/kalshi/terms.md:29: "There are occasional exceptions [to the
    Series -> Event -> Market ticker convention], so do not parse ticker
    strings to infer relationships. Best practice is to use the series,
    event, market, and search endpoints and rely on fields like
    series_ticker, event_ticker...". docs/kalshi/get-market.md documents
    a market's own event_ticker field; docs/kalshi/get-events.md documents
    an event's own series_ticker field - this is exactly that chain,
    already persisted by save_market_titles()/save_event_titles() from
    every get_market()/get_event() response, nothing new fetched here.

    None when the market isn't cached yet, its event isn't cached yet, or
    the event's series_ticker column is empty/null (e.g. the event row was
    saved before required_event_fields' series_ticker fetch existed) -
    services/signal_log.py::series_of() is the only caller and falls back
    to its own ticker-prefix heuristic in every one of those cases, never
    guessing a series here."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT et.series_ticker FROM market_titles mt "
            "JOIN event_titles et ON et.event_ticker = mt.event_ticker "
            "WHERE mt.ticker = ?",
            (ticker,),
        ).fetchone()
    return row[0] if row and row[0] else None
