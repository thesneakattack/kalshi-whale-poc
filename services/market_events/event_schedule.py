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

from services import task_supervisor
from services.http_client import get_client
from services.kalshi_client import KalshiClient

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "event_schedule.db"

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
    # Same parsing idiom as services/market_events/event_lifecycle.py's _parse_ts /
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


# ---- background resolution loop (2026-08-24) --------------------------
#
# Wires the waterfall above into main.py's trading loop. Previously this
# whole module was built but never called from anywhere except load_all()
# at app_state.py import time - resolve_one/needs_resolution had no caller,
# so state["event_schedules"] never grew past whatever was already on disk.
# Second sub-unit of the close-time fix (see market_lookup.effective_close_
# time, first sub-unit): that resolver's tier 2 reads state["event_schedules"]
# but nothing was ever populating fresh entries for events that don't
# already have one.

_LONG_WINDOW_THRESHOLD_SEC = 48 * 3600  # a market closing within 48h already has a
# close_time accurate enough to trust for practical purposes - resolving a schedule
# for it is pure waste. A judgment call (order of magnitude borrowed from
# confidence_scoring.py's _CLOSE_PROXIMITY_WINDOW_SEC), not a measured number -
# fine to tune later via config if the batch pool turns out too wide/narrow.

_RESOLVE_MIN_INTERVAL_SEC = 30  # how often a new background batch may be KICKED OFF,
# same role as catalog_scan.py's _CATALOG_SCAN_MIN_INTERVAL_SEC - just avoids spawning
# overlapping tasks. Wider than that module's 15s since a batch here can include up to
# max_resolutions_per_tick sequential web-search legs (each up to _web_search_schedule's
# own 5s timeout), so one batch's own worst-case wall time is meaningfully longer.
#
# Deliberately NOT seeded from persisted history the way backup.py's
# _maybe_run_backup had to be fixed to do (2026-08-23 cold-start reload bug -
# see that module's docstring): last_started_at resetting to 0.0 on every
# uvicorn --reload only makes THIS gate think a batch is immediately due
# again, it doesn't affect which events get resolved - each event's own
# due-ness is decided independently by needs_resolution() against
# event_schedules' disk-persisted resolved_at/source, not by this tracker.
# A spurious post-reload kick-off is therefore cheap: it re-checks up to
# max_resolutions_per_tick events and finds most (usually all) not actually
# due, unlike backup's unconditional whole-database-every-time re-run.


def _events_needing_resolution(markets: list[dict], event_schedules: dict, now: float) -> list[str]:
    """Long-window events (module docstring: Kalshi's raw close_time can't be
    trusted as the real resolution time once it's this far out) whose cached
    schedule, if any, is due for a resolve/re-check per needs_resolution's own
    TTL. An event qualifies if ANY of its markets has a far-out close_time,
    not just the first one seen, since sibling markets under the same event
    can differ. Deduped, first-seen order (state["markets"]' own order)."""
    long_window_events: dict[str, None] = {}
    for m in markets:
        et = m.get("event_ticker")
        if not et or et in long_window_events:
            continue
        close_time = m.get("close_time")
        ct_ts = close_time if isinstance(close_time, (int, float)) else _parse_ts(close_time)
        if ct_ts is not None and (ct_ts - now) >= _LONG_WINDOW_THRESHOLD_SEC:
            long_window_events[et] = None
    return [et for et in long_window_events if needs_resolution(event_schedules.get(et), now)]


async def _resolve_event_schedules(
    client: KalshiClient,
    cfg: dict,
    markets: list[dict],
    event_titles: dict,
    event_schedules: dict,
    market_object_cache: dict,
) -> None:
    """Resolves one batch of due, long-window events (see
    _events_needing_resolution) and applies each result to event_schedules
    IN PLACE - so state["event_schedules"] is warm for the very next
    effective_close_time call, no restart needed - plus persists each one via
    save(). Sequential, not gathered: resolve_one's 4th tier hits an external
    host (DuckDuckGo), and staying sequential keeps one batch's worst-case
    wall time predictable (bounded by batch size * one request's own timeout)
    instead of however many concurrent external requests happen to be slow at
    once. Open positions need no special-case priority in the batch: main.py's
    existing extra_tickers plumbing already guarantees an open position's
    market stays in state["markets"] every tick regardless of watchlist
    rotation, so scanning markets here already covers them.

    event_strike_date comes from event_titles (state["event_titles"],
    already fetched, zero extra cost - see module docstring source 1).
    texts are pulled opportunistically from market_object_cache
    (state["market_object_cache"], already-fetched un-slimmed market
    objects other paths already populate) for every market under this
    event plus the event's own sub_title - zero extra network call when
    already cached, source 3's rules_primary/rules_secondary/sub_title."""
    event_schedule_cfg = cfg.get("event_schedule") or {}
    max_per_tick = event_schedule_cfg.get("max_resolutions_per_tick", 5)
    web_search_enabled = event_schedule_cfg.get("web_search_enabled", True)
    now = time.time()
    due = _events_needing_resolution(markets, event_schedules, now)[:max_per_tick]
    if not due:
        return
    due_set = set(due)
    tickers_by_event: dict[str, list[str]] = {}
    for m in markets:
        et = m.get("event_ticker")
        if et in due_set and m.get("ticker"):
            tickers_by_event.setdefault(et, []).append(m["ticker"])
    ref_year = datetime.now(timezone.utc).year
    for event_ticker in due:
        et_info = event_titles.get(event_ticker) or {}
        texts = []
        for ticker in tickers_by_event.get(event_ticker, []):
            obj = market_object_cache.get(ticker)
            if obj:
                texts.append(obj.get("rules_primary"))
                texts.append(obj.get("rules_secondary"))
        texts.append(et_info.get("sub_title"))
        texts = [t for t in texts if t]
        title = et_info.get("title") or event_ticker
        start_ts, end_ts, source = await resolve_one(
            client, event_ticker,
            event_strike_date=et_info.get("strike_date"), texts=texts,
            search_query=f"{title} schedule dates", ref_year=ref_year,
            web_search_enabled=web_search_enabled,
        )
        event_schedules[event_ticker] = save(event_ticker, start_ts, end_ts, source)


async def _resolve_event_schedules_background(cfg: dict) -> None:
    """Background-task wrapper, same split as catalog_scan._scan_catalog_
    batch_background/backup._run_backup_background - owns releasing the
    "running" flag and closing its own client regardless of outcome via
    finally. Local `state` import - app_state.py imports this module at
    top level (for load_all()), so a top-level `from services.app_state
    import state` here would be circular, the same trap strategy_engine.py's
    evaluate() works around for its own market_lookup import (see that
    module's comment)."""
    from services.app_state import state

    schedule_state = state["event_schedule_scan"]
    client = KalshiClient(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])
    try:
        await _resolve_event_schedules(
            client, cfg,
            state.get("markets") or [], state.get("event_titles") or {},
            state["event_schedules"], state.get("market_object_cache") or {},
        )
    finally:
        schedule_state["running"] = False
        await client.close()


def _maybe_resolve_event_schedules(cfg: dict) -> None:
    """Triggers _resolve_event_schedules_background as an independent
    background task on its own steady interval, decoupled from the main
    tick entirely - same pattern as catalog_scan._maybe_scan_catalog_batch/
    backup._maybe_run_backup. Synchronous/non-blocking on purpose, exactly
    like those siblings. Same local-import trap as the background function
    above."""
    from services.app_state import state

    event_schedule_cfg = cfg.get("event_schedule") or {}
    if not event_schedule_cfg.get("enabled", True):
        return
    schedule_state = state["event_schedule_scan"]
    now_ts = time.time()
    due = now_ts - schedule_state["last_started_at"] > _RESOLVE_MIN_INTERVAL_SEC
    if due and not schedule_state["running"]:
        schedule_state["running"] = True
        schedule_state["last_started_at"] = now_ts
        schedule_state["task"] = task_supervisor.supervise(
            lambda: _resolve_event_schedules_background(cfg),
            component="event_schedule", operation="resolve_batch",
        )
