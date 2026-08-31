"""
Real live/scheduled/finished status per event, via Kalshi's milestone/
live-data system, plus exchange status. Split out of market_watch.py
(2026-08-22 modularization Phase 9/9).
"""
import asyncio
import time
from datetime import datetime

from services import game_state
from services.app_state import state
from services.kalshi.public import KalshiPublicGateway
from services import http_client
from services.market_lookup import _sport_for_event

_LIVE_STATUS_LOOKBACK_SEC = 8 * 3600  # keep tracking an event up to 8h after its scheduled start
# Widened 1h -> 12h on 2026-08-17. Measured live: 30 Sports events were on
# the watchlist while live_status held ONE entry and live_game_state held
# zero, because a game scheduled for 13:35 is ~7.5h away at 06:00 and fell
# outside a 1-hour lookahead. Nothing was tracked, so no score/period/clock
# was ever captured for any of them.
#
# Widening is bounded, not open-ended: _LIVE_STATUS_REPOLL_SEC (5 min)
# caches each event's status and _LIVE_STATUS_MAX_POLL_PER_TICK (10) caps
# how many are refreshed in any one tick, so a larger candidate pool
# lengthens the rotation rather than multiplying per-tick API calls. Those
# two bounds are what make this safe, and they must stay if this is widened
# further - see their own comments for the 2026-08-15 incident that put
# them there.
_LIVE_STATUS_LOOKAHEAD_SEC = 12 * 3600
# Direct request: once markets/whale data have populated the system, "no
# need to check if a market is live... every tick... they should have
# scheduled open and close times for you to do some light polling to track
# status but otherwise use the schedule and its previous live status to
# operate." Once an event has been checked at all, don't check it again for
# at least this long - a ~20x reduction in the 2-API-call milestone/live-
# data check's frequency versus doing it fresh every 15s tick regardless of
# whether anything could plausibly have changed.
_LIVE_STATUS_REPOLL_SEC = 5 * 60
_LIVE_STATUS_TERMINAL = {"finished", "closed"}  # once genuinely confirmed, never poll this event again
_LIVE_STATUS_MAX_POLL_PER_TICK = 10  # Bounded per-tick batch (2026-08-15
# tick_duration investigation), same shape as main._check_signal_resolutions'
# own limit=10 batching - to_poll had no cap at all, so whenever a large
# fraction of the in-window candidate pool (up to ~2,500 rows / 100+
# distinct events - see market_catalog.candidates_in_window, called from
# market_fetch._fetch_markets' live_markets_only branch) became simultaneously
# due for their 5-minute repoll, this fired dozens-to-hundreds of concurrent
# REST calls in a single tick, saturating the shared, deliberately-conservative
# Kalshi rate limiter (services/http_client.py). Confirmed live: this
# stalled every OTHER read call sharing that same limiter too
# (_fetch_account_snapshot alone measured at 32-33s on the same ticks,
# despite its own independent 20s interval cache doing exactly what it was
# supposed to) - asyncio.gather's concurrency doesn't bypass a shared token
# bucket. Oldest-checked-first (never-checked treated as most urgent, see
# the sort key below) so a large backlog drains gradually across ticks
# instead of spiking once; events excluded from a given tick's batch keep
# whatever cached status they already had (documented fallback behavior
# this function already relies on for "not yet due" - unchanged here).


@http_client.classify("background_live_status")
async def _fetch_live_status(client: KalshiPublicGateway, markets: list[dict]) -> dict:
    """The real live/scheduled/finished status per event, via Kalshi's
    actual milestone/live-data system - confirmed directly against a real
    AFL match at its actual start time (status "scheduled"->"inprogress"->
    "closed", widget_status "none"->"live"->"finished"), not inferred from
    timestamps alone. Reads/writes state["live_status_cache"] directly
    (same module-global pattern catalog_scan._get_series_cache already uses
    for its own cache) rather than taking it as a parameter.

    Schedule-gated in two layers now, not one:
    1. Only markets whose occurrence_datetime falls in a plausible window
       (up to 1h before the scheduled start through 6h after it) are
       considered at all - unchanged in spirit from before, but the bounds
       were actually inverted from what this comment always claimed (a
       real bug found while touching this: the old condition let events
       starting hours in the future in but dropped anything that had been
       running for more than an hour, exactly backwards from "started up
       to 6h ago, or starting within the next hour"). Fixed here.
    2. Within that window, an event is only actually re-polled (the 2 real
       API calls) if it has no cached status yet, or its cached status is
       older than _LIVE_STATUS_REPOLL_SEC - otherwise the cached value is
       reused as-is. A market past its own close_time is treated as
       finished from the schedule alone, no poll needed: trading has
       already stopped there regardless of what the live-data API would
       say. Once a status is confirmed terminal (finished/closed), it's
       never polled again for the rest of this process's life.

    3. When a real poll *is* attempted but Kalshi's milestone/live-data
       system has nothing for this event (confirmed live in practice: most
       real candidates get no milestone at all), falls back to inferring
       from the schedule alone rather than leaving it unknown - started
       per occurrence_datetime and not yet past close_time (already
       screened above) means presumed live. See the schedule-fallback
       block below for the source="schedule" vs "milestone" cache tag."""
    now = time.time()
    cache = state["live_status_cache"]
    event_occ_ts: dict[str, float] = {}
    for m in markets:
        occ, et = m.get("occurrence_datetime"), m.get("event_ticker")
        if not occ or not et or et in event_occ_ts:
            continue
        try:
            occ_ts = datetime.fromisoformat(occ.replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        if -_LIVE_STATUS_LOOKAHEAD_SEC <= (now - occ_ts) <= _LIVE_STATUS_LOOKBACK_SEC:
            event_occ_ts[et] = occ_ts
    if not event_occ_ts:
        return {}

    close_ts_by_ticker = {}
    for m in markets:
        et, close_time = m.get("event_ticker"), m.get("close_time")
        if et in event_occ_ts and close_time and et not in close_ts_by_ticker:
            try:
                close_ts_by_ticker[et] = datetime.fromisoformat(close_time.replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass

    result = {}
    to_poll = []
    for et in event_occ_ts:
        cached = cache.get(et)
        if cached and cached["status"] in _LIVE_STATUS_TERMINAL:
            result[et] = cached["status"]
            continue
        close_ts = close_ts_by_ticker.get(et)
        if close_ts and now > close_ts:
            cache[et] = {"status": "finished", "checked_at": now}
            result[et] = "finished"
            continue
        if cached:
            result[et] = cached["status"]
            if (now - cached["checked_at"]) < _LIVE_STATUS_REPOLL_SEC:
                continue  # recently confirmed, not due for a re-check yet
        to_poll.append(et)

    if not to_poll:
        return result

    to_poll.sort(key=lambda et: (cache.get(et) or {}).get("checked_at", 0.0))
    to_poll = to_poll[:_LIVE_STATUS_MAX_POLL_PER_TICK]

    # milestone_scan.py's broad cache first (entry-gate-me-pairing-and-
    # netting-remediation Part 3) - an event already covered there skips
    # the per-event REST call entirely. A cache miss falls back to the
    # exact pre-existing per-event get_milestones_for_event call, so a
    # cold/not-yet-covered cache reproduces today's behavior byte for byte.
    #
    # SCOPE, precisely (corrected 2026-08-30, final-review finding): this
    # is a REST-call reduction, NOT a coverage broadening. `to_poll` above
    # is still derived entirely from the `markets` argument - the same
    # watchlist-scoped list as before - and already truncated to
    # _LIVE_STATUS_MAX_POLL_PER_TICK before this cache is consulted, so an
    # event milestone_scan knows about but that never appears in `markets`
    # is still never polled and still contributes nothing to game_state.
    # Making this loop ALSO poll milestone-known events outside `markets`
    # would be the real broadening; it is deliberately not done here (it
    # changes what the per-tick poll budget is spent on) and is tracked as
    # a follow-up in docs/open-decisions.md.
    broad_cache = state["milestone_by_event"]
    milestone_by_event: dict[str, str] = {}
    has_milestone: set[str] = set()
    needs_fetch: list[str] = []
    for et in to_poll:
        ms_id = broad_cache.get(et)
        if ms_id:
            milestone_by_event[et] = ms_id
            has_milestone.add(et)
        else:
            needs_fetch.append(et)

    if needs_fetch:
        # Batched (2026-08-16 API-doc audit finding B3.2, docs/kalshi/
        # get-multiple-live-data.md) - was N individual get_live_data()
        # calls via asyncio.gather, one per event with a milestone.
        # Live-verified: 3 individual = 0.99s wall, 1 batched get_live_datas
        # call = 0.02s wall.
        milestone_results = await asyncio.gather(
            *(client.get_milestones_for_event(et) for et in needs_fetch), return_exceptions=True
        )
        for et, ms_result in zip(needs_fetch, milestone_results):
            if isinstance(ms_result, list) and ms_result:
                ms = ms_result[0]
                if ms.get("id") and ms.get("type"):
                    has_milestone.add(et)
                    milestone_by_event[et] = ms["id"]

    confirmed = {}
    if milestone_by_event:
        live_datas = await client.get_live_datas(list(milestone_by_event.values()))
        for et, ms_id in milestone_by_event.items():
            ld = live_datas.get(ms_id)
            if not ld:
                continue
            details = ld.get("details") or {}
            status = details.get("widget_status")
            if status:
                confirmed[et] = status
            # Real score/quarter/clock/down-distance/last_play (2026-08-16
            # audit finding B2, docs/kalshi/get-multiple-live-data.md) - the exact
            # same get_live_datas call above already fetches this full
            # payload; previously only widget_status was ever read out of
            # it. Pure value-add at zero extra API cost: surfaced here so
            # the dashboard can show real in-game state alongside a
            # watchlisted market, not just a live/finished label.
            if details:
                state["live_game_state"][et] = {"details": details, "updated_at": now}
                # Persist it too (2026-08-17). The line above has been
                # parsing this correctly since 08-16, but into an in-memory
                # dict that dies with the process - so score/period/clock
                # history has been arriving and evaporating every restart.
                # Storing it costs zero extra API calls (this payload is
                # already fetched for widget_status) and is what makes
                # questions like "did this whale print land right after a
                # scoring play" answerable later. Deduplicated on real state
                # change, so a finished game polled for hours writes once.
                game_state.record(et, details, sport=_sport_for_event(
                    state["event_titles"].get(et) or {}))

    # Schedule fallback, direct request: "otherwise use the schedule and
    # its previous live status to operate" - but a direct correction right
    # after: "just because a market is open doesn't mean it's live like
    # sports or mentions or award shows - be careful about how you infer
    # when you can't find live status." A market having no registered
    # milestone at all isn't the same situation as one that has a milestone
    # but whose live-data confirmation didn't come back this tick - the
    # first case likely means this market isn't the kind of discrete,
    # clocked, real-world event this system can meaningfully call "live" in
    # the first place (a mention/award-show market can stay open around its
    # occurrence_datetime with no real in-progress state the way a game
    # clock has), and guessing "live" there would be fabricating a status
    # Kalshi never actually confirmed. So the fallback only applies to
    # events Kalshi *has* confirmed are milestone-tracked - real games this
    # system already knows have a genuine live/in-progress state - and
    # simply couldn't get a fresh widget_status for on this particular
    # tick. Anything with no milestone at all is left out of the result
    # entirely (not cached, not "none", not "live" - genuinely unknown)
    # rather than guessed at either way.
    for et in to_poll:
        if et in confirmed:
            status, source = confirmed[et], "milestone"
        elif et in has_milestone:
            status = "none" if now < event_occ_ts[et] else "live"
            source = "schedule"
        else:
            continue  # not a milestone-tracked event type - no basis to infer anything
        cache[et] = {"status": status, "checked_at": now, "source": source}
        result[et] = status
    return result


@http_client.classify("critical_position")
async def _fetch_exchange_status(client: KalshiPublicGateway) -> dict | None:
    # A transient hiccup here shouldn't take down the whole poll tick the way
    # a markets/account failure would (nothing downstream depends on it) —
    # swallow and keep the last known status rather than clearing it.
    try:
        return await client.get_exchange_status()
    except Exception:
        return None
