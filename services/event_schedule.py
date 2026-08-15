"""
Resolves the REAL real-world start time of an event, for cases where
Kalshi's own open_time/close_time/occurrence_datetime don't reflect it -
direct request (2026-08-15): "a lot of markets for events have large 30 day
market windows... the trade window is meant to be the actual event schedule
(start-finish), but kalshi does a bad job of providing that info... we
discussed doing a web search and parse to get schedule data for these
events since the market open times, close times, and live-status rarely are
useful in determining an actual 'live' event."

This does NOT change how the END of an event's window is judged -
occurrence_datetime already does a reasonable job of that (confirmed live:
KXPGATOUR-FESJC26's occurrence_datetime, Aug 16, matches the real FedEx St.
Jude Championship's actual final day) - only the START, which nothing else
in this app currently derives at all for multi-day events. Direct
instruction: "leave the way series are filtered by the market dates alone
for now" - this module is only ever consulted for the is_live/trade-window
gate (main.py's _handle_signal), never for discovery/series filtering.

Four sources, tried in order, cheapest/most-authoritative first - every one
of these confirmed against real, live Kalshi data before writing this, not
assumed:

1. Event.strike_date - "the specific date this event is based on," a real
   field already captured into state["event_titles"] by main.py's
   _fetch_event_titles at zero extra network cost (part of its own
   get_event() call, previously just discarded). Confirmed live: null for
   sports events like KXPGATOUR-FESJC26/KXNFLGAME-26AUG15CARBUF (those use
   source 2 below instead), but populated and precise for single-date
   announcement events milestones don't cover at all - KXFED-26SEP's
   strike_date is 2026-09-16T18:00:00Z, matching its own sub_title ("On
   Sep 16, 2026") and the real FOMC announcement time. For this source
   there's no separate "end" - a point-in-time announcement's own moment
   is both bounds (see resolve_one).

2. Kalshi's milestone API (services/kalshi_client.py's
   get_milestones_for_event) - a real, structured start_date field.
   Confirmed live: KXPGATOUR-FESJC26's tournament milestone reports
   start_date 2026-08-13T11:00:00Z, matching the real tournament's actual
   Wed-the-13th start exactly; KXNFLGAME-26AUG15CARBUF's game milestone
   reports a kickoff time more precise than occurrence_datetime. Covers
   milestone categories "Sports, Elections, Esports, Crypto" per the SDK's
   own get_milestones docstring - confirmed live for Sports/Crypto/
   Elections; "Politics" is not a real category value (returns 0 results).
   end_date is almost always null even here (confirmed across every sample
   pulled except crypto's hourly/15-min one_off_milestones, which aren't
   the shape this module exists for), so it's never relied on. Note the
   role reversal from source 1: KXOSCARDIR-27's strike_date (2027-12-31,
   "In 2027") is a settlement deadline/reference date, the same role
   occurrence_datetime already plays - NOT a "when does real-world
   activity start" signal the way KXFED's is. resolve_one only trusts
   strike_date as a start/end pair for events milestones don't cover;
   where both exist, milestone wins (see the priority order below).

3. Text already sitting in fields the app fetches per-market anyway
   (rules_primary/rules_secondary, event sub_title) - no extra network
   call at all. Confirmed live: KXNFLGAME-26AUG15CARBUF-BUF's rules_primary
   literally reads "...the Carolina vs Buffalo professional football game
   originally scheduled for Aug 15, 2026." Doesn't cover every shape -
   confirmed KXPGATOUR-FESJC26-RMAC's rules_primary ("If Robert MacIntyre
   wins the FedEx St. Jude Championship...") has no date at all - which is
   exactly why source 4 exists.

4. A plain, keyless web search + regex date-range parse (DuckDuckGo HTML,
   no API key, no AI/LLM call - direct instruction: "the web search tool
   doesnt need to use an AI agent it can be simple searching filter and
   parsing that isnt AI based"). Last resort, reached only when none of
   the above found anything - in practice mostly multi-day tournaments
   whose own winner-market never states a date anywhere in Kalshi's data.

Resolved schedules persist in data/event_schedule.db (see CLAUDE.md's
persistence idiom) so the cost of all four lookups is paid once per event,
not once per tick. A "nothing found" result is cached too (source="none"),
with a much shorter re-check TTL, so events with genuinely no resolvable
schedule (most political/economic futures, mention markets) don't re-pay
the web-search cost every tick forever.
"""
from __future__ import annotations

import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from dateutil import parser as _dateutil_parser

from services.http_client import get_client
from services.kalshi_client import KalshiClient

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "event_schedule.db"

SOURCE_STRIKE_DATE = "strike_date"
SOURCE_MILESTONE = "milestone"
SOURCE_TEXT = "text"
SOURCE_SEARCH = "search"
SOURCE_NONE = "none"

# A found schedule is treated as effectively permanent (schedules don't
# change once set, barring a rare postponement this module doesn't try to
# track) - but still re-checked occasionally so a real Kalshi update
# eventually flows through. A "nothing found" tombstone gets a much
# shorter TTL specifically so the web-search leg (the only one with a real
# per-attempt cost) isn't retried every tick for events that keep coming up
# empty.
_FOUND_RETRY_SEC = 24 * 3600
_NONE_RETRY_SEC = 12 * 3600

_MONTH_WORD = (
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December"
    r"|Jan\.?|Feb\.?|Mar\.?|Apr\.?|Jun\.?|Jul\.?|Aug\.?|Sept?\.?|Oct\.?|Nov\.?|Dec\.?)"
)
# "August 13-16, 2026" / "Aug 13–16, 2026" / "Aug 13 to 16, 2026"
_RANGE_PATTERN = re.compile(
    rf"({_MONTH_WORD})\s+(\d{{1,2}})\s*(?:-|–|—|to)\s*(\d{{1,2}}),?\s+(\d{{4}})", re.IGNORECASE
)
# "August 15, 2026" / "originally scheduled for Aug 15, 2026"
_SINGLE_PATTERN = re.compile(rf"({_MONTH_WORD})\s+(\d{{1,2}}),?\s+(\d{{4}})", re.IGNORECASE)
# "(Aug 15)" - no year, e.g. event sub_title - only tried with a ref_year supplied
_SINGLE_NO_YEAR_PATTERN = re.compile(rf"({_MONTH_WORD})\s+(\d{{1,2}})\b", re.IGNORECASE)

_PARSE_DEFAULT = datetime(2000, 1, 1, tzinfo=timezone.utc)


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, coltype: str):
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS event_schedule (
            event_ticker TEXT PRIMARY KEY,
            start_ts REAL,
            end_ts REAL,
            source TEXT NOT NULL,
            resolved_at REAL NOT NULL
        )
        """
    )
    return conn


def load_all() -> dict[str, dict]:
    """Hydrates state["event_schedules"] at import time, same pattern as
    services/title_cache.py's load_event_titles()."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT event_ticker, start_ts, end_ts, source, resolved_at FROM event_schedule"
        ).fetchall()
    return {
        event_ticker: {"start_ts": start_ts, "end_ts": end_ts, "source": source, "resolved_at": resolved_at}
        for event_ticker, start_ts, end_ts, source, resolved_at in rows
    }


def save(event_ticker: str, start_ts: float | None, end_ts: float | None, source: str, resolved_at: float | None = None) -> dict:
    resolved_at = resolved_at if resolved_at is not None else time.time()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO event_schedule (event_ticker, start_ts, end_ts, source, resolved_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(event_ticker) DO UPDATE SET
                start_ts = excluded.start_ts,
                end_ts = excluded.end_ts,
                source = excluded.source,
                resolved_at = excluded.resolved_at
            """,
            (event_ticker, start_ts, end_ts, source, resolved_at),
        )
    return {"start_ts": start_ts, "end_ts": end_ts, "source": source, "resolved_at": resolved_at}


def needs_resolution(cached: dict | None, now: float) -> bool:
    """Whether resolve_one is worth calling again - no entry at all, or a
    stale one whose retry TTL (found vs not-found, see module docstring)
    has elapsed. A fresh entry, found or not, is left alone regardless of
    which source produced it."""
    if cached is None:
        return True
    ttl = _NONE_RETRY_SEC if cached.get("source") == SOURCE_NONE else _FOUND_RETRY_SEC
    return (now - cached.get("resolved_at", 0)) >= ttl


def _parse_ts(value: str | None) -> float | None:
    # Same parsing idiom as services/event_lifecycle.py's _parse_ts /
    # services/market_history.py's seconds_to_close - no value (or an
    # unparseable one) returns None rather than guessing.
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def parse_date_range_from_text(text: str | None, ref_year: int | None = None) -> tuple[float, float | None] | None:
    """Finds the first date or date-range substring in free text and
    returns (start_ts, end_ts) - end_ts is None for a single date. Tries
    the range pattern first (more specific - a range's second date would
    otherwise also satisfy the single-date pattern on its own). Every
    result is midnight UTC on the parsed calendar date - text sources
    never carry a time-of-day, only milestone start_date does."""
    if not text:
        return None
    m = _RANGE_PATTERN.search(text)
    if m:
        month, day1, day2, year = m.groups()
        try:
            start = _dateutil_parser.parse(f"{month} {day1}, {year}", default=_PARSE_DEFAULT)
            end = _dateutil_parser.parse(f"{month} {day2}, {year}", default=_PARSE_DEFAULT)
            return start.timestamp(), end.timestamp()
        except (ValueError, OverflowError):
            pass
    m = _SINGLE_PATTERN.search(text)
    if m:
        month, day, year = m.groups()
        try:
            dt = _dateutil_parser.parse(f"{month} {day}, {year}", default=_PARSE_DEFAULT)
            return dt.timestamp(), None
        except (ValueError, OverflowError):
            pass
    if ref_year:
        m = _SINGLE_NO_YEAR_PATTERN.search(text)
        if m:
            month, day = m.groups()
            try:
                dt = _dateutil_parser.parse(f"{month} {day}, {ref_year}", default=_PARSE_DEFAULT)
                return dt.timestamp(), None
            except (ValueError, OverflowError):
                pass
    return None


async def _milestone_schedule(client: KalshiClient, event_ticker: str) -> tuple[float, float | None] | None:
    try:
        milestones = await client.get_milestones_for_event(event_ticker)
    except Exception:
        return None
    if not milestones:
        return None
    ms = milestones[0]
    start_ts = _parse_ts(ms.get("start_date"))
    if start_ts is None:
        return None
    return start_ts, _parse_ts(ms.get("end_date"))


_TAG_RE = re.compile(r"<[^>]+>")


async def _web_search_schedule(query: str) -> tuple[float, float | None] | None:
    if not query:
        return None
    try:
        client = get_client()
        resp = await client.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0 (compatible; kalshi-whale-poc research bot)"},
            timeout=5.0,
        )
        if resp.status_code != 200:
            return None
        text = _TAG_RE.sub(" ", resp.text)
        text = re.sub(r"\s+", " ", text)
    except Exception:
        return None
    return parse_date_range_from_text(text[:6000])


async def resolve_one(
    client: KalshiClient,
    event_ticker: str,
    *,
    event_strike_date: str | None = None,
    texts: list[str] | None = None,
    search_query: str | None = None,
    ref_year: int | None = None,
    web_search_enabled: bool = True,
) -> tuple[float | None, float | None, str]:
    """The full waterfall for one event: strike_date -> milestone -> text ->
    web search. Returns (start_ts, end_ts, source) - (None, None, "none")
    when nothing was found anywhere. Never raises - a lookup failure on any
    leg is treated the same as "found nothing there," not a fatal error,
    since this is a best-effort accuracy improvement, not a required input
    (callers already have a working, if less precise, fallback - see
    main.py's is_live computation).

    event_strike_date checked first since it costs nothing (already sitting
    in state["event_titles"], see module docstring source 1) - a point in
    time, not a range, so it's used as both start and end (a Fed decision's
    own announcement moment is the whole "live" window, not a lead-up to
    something later)."""
    strike_ts = _parse_ts(event_strike_date)
    if strike_ts is not None:
        return strike_ts, strike_ts, SOURCE_STRIKE_DATE

    milestone_result = await _milestone_schedule(client, event_ticker)
    if milestone_result:
        return milestone_result[0], milestone_result[1], SOURCE_MILESTONE

    for text in (texts or []):
        text_result = parse_date_range_from_text(text, ref_year=ref_year)
        if text_result:
            return text_result[0], text_result[1], SOURCE_TEXT

    if web_search_enabled and search_query:
        search_result = await _web_search_schedule(search_query)
        if search_result:
            return search_result[0], search_result[1], SOURCE_SEARCH

    return None, None, SOURCE_NONE


def trade_window_is_open(
    now: float, start_ts: float | None, end_ts: float | None, pre_event_hours: float,
) -> bool | None:
    """Pure window check: True/False once a real start_ts is known, None
    when there's nothing to judge (caller should fall back to its existing
    logic - see main.py's is_live computation, which only trusts this
    module's answer when it isn't None). "hours leading up to it to
    whenever it closes" (direct instruction) = [start_ts - pre_event_hours,
    end_ts] inclusive; end_ts is required here (main.py supplies
    occurrence_datetime as the fallback end when this module didn't itself
    resolve one - see module docstring, source 1)."""
    if start_ts is None or end_ts is None:
        return None
    return (start_ts - pre_event_hours * 3600) <= now <= end_ts
