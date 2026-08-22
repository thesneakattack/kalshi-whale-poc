"""
Market discovery and catalog scanning - decides which markets/events
trading_loop sees each tick (pinned watchlist + volume-ranked discovery +
background catalog scan), fetches their live prices/exchange/milestone
status, and tracks per-event live-data for milestone-based outcome
resolution. Extracted 2026-08-22 as part of main.py's modularization pass
(Phase 7, the largest single extraction - see
docs/next-session-pickup-2026-08-22.md and the plan doc it points to).

This is the current tick-duration bottleneck (see the module boundaries
table in the modularization plan) - a pure extraction here, no behavior
change, but it's what makes that problem addressable inside one bounded
file going forward instead of main.py at large.
"""
import asyncio
import time
from datetime import datetime

from services import (
    event_lifecycle, game_state, market_catalog, market_history, series_cache,
    series_evaluator, signal_log,
)
from services.app_state import bump_generation, state
from services.kalshi_client import KalshiClient
from services.market_lookup import _sport_for_event


_MILESTONE_REPOLL_SEC = 60  # Repoll-cached (2026-08-15 tick_duration fix) -
# this used to call get_milestones_for_event() for every unique event on the
# watchlist, every tick, forever, unconditionally - confirmed live as one of
# two per-event REST loops (see _fetch_event_live_data just below) with zero
# caching, together accounting for the bulk of a ~27s tick_duration plateau
# that survived the earlier same-day rate-limit incident's own "no stone
# unturned" audit (that audit fixed discovery/catalog-scan/signal-resolution/
# account-snapshot, but these two live in a later, separate part of the tick
# it didn't touch). Most events (crypto, politics, ...) never have a
# milestone at all, so this was 13+ wasted calls a tick for nothing. Once a
# winner is found for an event, it's cached permanently - a real-world
# outcome doesn't change, so there's never a reason to poll it again.


async def propagate_milestone_winners(client: KalshiClient, markets: list[dict]) -> dict:
    """Best-effort: fetch first milestone per event, inspect its live-data
    for a declared `details.winner`, map that winner to a related market
    ticker when possible and set market_results for the related tickers
    (yes/no). Also records outcomes via market_history.record_outcome()
    so check_exits can close positions this tick. Returns the market_results
    mapping (ticker -> result) built from the provided markets plus any
    propagated winners. This is kept separate so it can be unit-tested.

    Repoll-cached in state["milestone_cache"] - see _MILESTONE_REPOLL_SEC
    above. A cache hit (event not due for repoll, or already resolved) costs
    zero API calls but still reapplies any already-known winner into this
    tick's market_results below, so callers see identical per-tick
    completeness to the pre-caching behavior - only the network cost was cut.
    """
    market_results = {m["ticker"]: m.get("result") for m in markets if m.get("ticker")}
    try:
        event_tickers = list(dict.fromkeys(m.get("event_ticker") for m in markets if m.get("event_ticker")))
        cache = state["milestone_cache"]
        now = time.time()
        to_poll = [
            et for et in event_tickers
            if et not in cache or (
                not cache[et]["winner_found"] and (now - cache[et]["checked_at"]) >= _MILESTONE_REPOLL_SEC
            )
        ]
        if to_poll:
            milestone_tasks = await asyncio.gather(
                *(client.get_milestones_for_event(et) for et in to_poll), return_exceptions=True
            )
            # First pass: default every polled event to "no winner found
            # this tick" (recorded before any further fetch so a transient
            # failure below still throttles the retry to the next repoll
            # window), then collect the events that actually have a real
            # milestone id/type to check live-data for.
            milestone_by_event = {}
            for et, ms_result in zip(to_poll, milestone_tasks):
                cache[et] = {"checked_at": now, "winner_found": False, "related": None, "mapped_winner_ticker": None}
                if isinstance(ms_result, list) and ms_result:
                    ms = ms_result[0]
                    if ms.get("id") and ms.get("type"):
                        milestone_by_event[et] = ms

            # Batched (2026-08-16 API-doc audit finding B3.2, docs/kalshi/
            # get-live-data.md) - was N individual get_live_data() calls,
            # one per event with a milestone, each inside this same loop.
            # One get_live_datas call now covers every event polled this
            # tick regardless of how many need it.
            live_datas = {}
            if milestone_by_event:
                live_datas = await client.get_live_datas([ms["id"] for ms in milestone_by_event.values()])

            # Pass 1: figure out which events actually have a declared
            # winner + related tickers to map (pure, no I/O), collecting
            # every related ticker across every such event into one flat,
            # deduped list.
            events_with_winner = []  # (et, ms, winner, related)
            all_related: list[str] = []
            seen_related = set()
            for et, ms in milestone_by_event.items():
                ld = live_datas.get(ms["id"])
                if not ld:
                    continue
                details = ld.get("details") or {}
                winner = details.get("winner")
                related = ms.get("related_event_tickers") or details.get("related_event_tickers") or []
                if not winner or not related:
                    continue
                events_with_winner.append((et, ms, winner, related))
                for t in related:
                    if t not in seen_related:
                        seen_related.add(t)
                        all_related.append(t)

            # Batched (2026-08-16, direct efficiency note: "a lot of
            # efficiency could be gained by using batch calls to the API vs
            # individual calls for specific markets") - was one gather of
            # individual get_market() calls PER event with a winner, inside
            # this same loop. One get_markets_by_tickers call now covers
            # every related ticker across every such event this tick,
            # regardless of how many events have a winner to map.
            related_market_by_ticker = await client.get_markets_by_tickers(all_related) if all_related else {}

            for et, ms, winner, related in events_with_winner:
                related_markets = [related_market_by_ticker[t] for t in related if t in related_market_by_ticker]
                mapped_winner_ticker = None
                for rm in related_markets:
                    if not isinstance(rm, dict):
                        continue
                    cs = rm.get("custom_strike") or {}
                    try:
                        if isinstance(cs, dict) and any(str(winner).lower() in str(v).lower() for v in cs.values()):
                            mapped_winner_ticker = rm.get("ticker")
                            break
                    except Exception:
                        pass
                    yst = (rm.get("yes_sub_title") or "")
                    nst = (rm.get("no_sub_title") or "")
                    if isinstance(winner, str) and winner:
                        wlow = winner.lower()
                        if yst and wlow in yst.lower():
                            mapped_winner_ticker = rm.get("ticker")
                            break
                        if nst and wlow in nst.lower():
                            mapped_winner_ticker = rm.get("ticker")
                            break
                        title = (rm.get("title") or "")
                        if title and wlow in title.lower():
                            mapped_winner_ticker = rm.get("ticker")
                            break
                if mapped_winner_ticker:
                    cache[et] = {
                        "checked_at": now, "winner_found": True,
                        "related": related, "mapped_winner_ticker": mapped_winner_ticker,
                    }
                    now_ts = time.time()
                    for rt in related:
                        res = "yes" if rt == mapped_winner_ticker else "no"
                        market_results[rt] = res
                        market_history.record_outcome(rt, res, resolved_at=now_ts)
                    bump_generation()
        # Reapply already-known winners from cache (no new API calls) so a
        # tick that skipped re-polling a resolved event still sees a
        # complete market_results, matching pre-caching per-tick behavior.
        for et in event_tickers:
            entry = cache.get(et)
            if entry and entry["winner_found"] and et not in to_poll:
                for rt in entry["related"]:
                    market_results[rt] = "yes" if rt == entry["mapped_winner_ticker"] else "no"
    except Exception:
        pass
    return market_results


# Kalshi's full market object carries 40+ fields (rules text, combo-leg
# lists, ...); trimming to what's actually used cuts the /api/state payload
# for 8 markets from ~34KB to well under 1KB. event_ticker/close_time/
# strike_type added for Phase 0.5's event/outcome grouping - previously
# dropped here entirely, so the dashboard had no way to know two markets
# were siblings under one event even though Kalshi sends that relationship
# on every market object already. yes_ask_dollars added for the screener
# table's Spread column (ROADMAP.md) - already present on every market
# object _fetch_markets gets back, so exposing it costs nothing extra.
_MARKET_FIELDS = (
    "ticker", "volume_24h_fp", "event_ticker", "close_time", "strike_type",
    "occurrence_datetime", "status", "yes_ask_dollars", "can_close_early",
)


def _slim_market(m: dict) -> dict:
    return {k: m.get(k) for k in _MARKET_FIELDS}


_SERIES_CACHE_TTL_SEC = 3600  # series (a recurring-event template - "Pro Basketball Game") don't
# change often enough to justify get_series_list's ~1s cost (12,500+ entries) every 15s poll tick

_PINNED_MARKET_REFRESH_SEC = 300  # structural fields (title, close_time, status, ...) for a
# manually-pinned ticker change rarely - price freshness comes from the WS ticker stream instead
# (see _fetch_markets' live-price overlay), not from re-fetching the whole market object every tick.

_DISCOVERY_REFRESH_SEC = 300  # 2026-08-15, second incident on the same code path: "you made the
# market watch list and whale watching grind to a halt and markets aren't even appearing anymore."
# The first fix (this constant alone, at 90s, still AWAITED inline every time it was due) was
# necessary but not sufficient - once services/http_client.py's rate limiter was corrected from a
# concurrency cap to the real token-bucket throughput limit Kalshi actually enforces (see that
# module's own comment), the SAME 12-120-series get_candidate_markets fetch this constant gates
# takes proportionally longer under a correctly-conservative rate (tens of seconds to minutes, not
# the old, rate-limit-violating handful of seconds) - and every tick where a refresh was due
# BLOCKED on it, stalling signal handling/trade execution/whale-tape processing behind a fetch for
# data (which series/markets exist right now) that changes on the order of minutes, not seconds.
# Fixed at the root below (_maybe_refresh_discovery_cache runs this as a genuinely independent
# background task, never awaited by the tick loop - a tick always reads whatever's already cached,
# even if stale) rather than by only tuning this number again; 300s (was 90s) on top of that is a
# real reduction in total REST volume for non-trading data unlikely to change tick-to-tick, not a
# response to a timing problem the background-task fix above already solves independently. Between
# refreshes (now always, not just "between" in the blocking sense), the same selected market list
# is reused with live prices overlaid from state["latest_prices"]/state["latest_asks"] (kept fresh
# every tick by the WS ticker-channel stream - see _process_stream_ticker and this function's own
# set_market_tickers call), so price freshness is fully decoupled from how often the underlying
# selection itself gets re-run.


async def _get_series_cache(client: KalshiClient) -> list[dict]:
    """All series with nonzero lifetime volume (~9,400 of Kalshi's ~12,500
    total, as of 2026-08-08), sorted by volume_fp descending, cached in
    state["series_cache"] and refreshed at most once per
    _SERIES_CACHE_TTL_SEC. See get_series_list's docstring for why this
    (series-level volume, then query real series directly) replaced
    browsing individual markets - a flat browse can be 100% dead combo
    markets even across tens of thousands of entries, confirmed directly,
    repeatedly. Shared by both the automatic watchlist (_get_top_series,
    just the top N) and market search (search_markets, which also needs
    the long tail to text-match against)."""
    cache = state["series_cache"]
    if time.time() - cache["fetched_at"] > _SERIES_CACHE_TTL_SEC or not cache["series"]:
        series = await client.get_series_list()
        series = [s for s in series if float(s.get("volume_fp") or 0) > 0]
        series.sort(key=lambda s: float(s.get("volume_fp") or 0), reverse=True)
        cache["series"] = series
        cache["fetched_at"] = time.time()
        series_cache.save(cache["fetched_at"], cache["series"])
    return cache["series"]


async def _get_top_series(client: KalshiClient, categories: list[str] | None = None, top_n_per_category: int = 12) -> list[str]:
    """Per-category discovery (2026-08-15 direct fix, real live report:
    "i see absolutely no signal or trade activity related to any markets
    other than sports or crypto... mentions... politics"). A flat global
    top-N by lifetime volume (the old behavior) systematically starves any
    category whose lifetime volume is small relative to Sports/Crypto's -
    confirmed live: Sports alone held 32 of the old top 40 series by
    lifetime volume, with Mentions/most of Politics/Entertainment/Climate
    holding zero. No amount of re-ranking *within* that narrow top-40 (see
    event_lifecycle.phase_ranked) can fix a category that was never even
    considered in the first place.

    categories: the same kalshi.categories list this app's own category
    filter already scopes to - top_n_per_category series from EACH one,
    guaranteeing every enabled category gets real, bounded coverage
    instead of zero. Total series considered is bounded by
    len(categories) * top_n_per_category (default config: 6 * 12 = 72), a
    moderate, predictable increase from the old flat 40 - deliberately NOT
    "query all ~9,757 volume-positive series every tick," which would mean
    ~9,757 concurrent API calls per 5s tick and risks recreating the exact
    shape of a real, already-documented incident (status.html phase 97 - a
    removed cap on trade-tape processing froze the app for several
    minutes). None/empty categories falls back to the old flat top-N
    behavior (top_n_per_category * 6, matching the default category
    count) rather than returning nothing."""
    series = await _get_series_cache(client)
    if not categories:
        return [s["ticker"] for s in series[:top_n_per_category * 6]]
    by_category: dict[str, list[str]] = {}
    for s in series:
        cat = s.get("category")
        if cat in categories:
            by_category.setdefault(cat, []).append(s["ticker"])
    result: list[str] = []
    for cat in categories:
        result.extend(by_category.get(cat, [])[:top_n_per_category])
    return result


# Series scanned per background batch to build services/market_catalog.py's
# near-term catalog. Cut from 40 to 10 (2026-08-15 tick_duration
# investigation) - confirmed live as the real remaining root cause after
# fixing three other uncached/uncapped call sites (propagate_
# milestone_winners, _fetch_event_live_data, _fetch_live_status's own
# per-tick cap) didn't meaningfully move tick_duration: this batch runs as
# an independent background task (_maybe_scan_catalog_batch), so it never
# blocks the tick's own await chain, but it draws from the exact same
# shared Kalshi rate limiter (services/http_client.py) the tick's own
# latency-sensitive reads need - backgrounding via asyncio.create_task
# decouples CONTROL FLOW, not RESOURCE CONTENTION. At 40 series/batch, with
# _CATALOG_SCAN_MIN_INTERVAL_SEC's overlap guard meaning a new batch starts
# again the moment the previous one finishes draining, this ran back-to-
# back continuously, at times consuming close to the entire 3.0 tokens/sec
# read budget by itself - starving _fetch_account_snapshot (measured
# stalling to 19-33s on affected ticks despite its own working 20s cache)
# and everything else sharing the bucket. Direct priority ordering already
# established this session ("maximum efficiency and maximum speed for
# position management and whale watching") argues for catalog-scan - a
# bulk, non-urgent backlog-clearing task, same category as signal-
# resolution's own already-throttled limit=10 batch - yielding budget to
# the tick-critical path, not competing with it head-on. Slower to fully
# re-cycle through every configured-category series as a result, but the
# catalog was already substantially warm before this change (thousands of
# series/markets from an earlier scanning period).
_CATALOG_SCAN_BATCH_SIZE = 10


async def _scan_catalog_batch(client: KalshiClient, cfg: dict):
    """Incrementally builds market_catalog's near-term market catalog, a
    bounded batch (least-recently-scanned series first, see market_catalog.
    next_series_to_scan) per call - see market_catalog.py's own module
    docstring for the full "why": volume-ranking the top 40 series
    systematically misses markets that are live right now but sit in a
    lower-volume series, confirmed directly against real Kalshi data (found
    ~0 of the real live markets the user could see on Kalshi's own site).

    No longer gated behind kalshi.live_markets_only (2026-08-15, direct
    "no stone unturned" API audit) - this catalog is now the primary
    source for the DEFAULT (non-live-only) discovery path too (see
    market_catalog.open_candidates/_refresh_discovery_cache), not just the
    live-only one, so it needs to stay warm regardless of that flag.
    Scoped to cfg["kalshi"]["categories"] rather than every one of
    Kalshi's ~9,400 series - this app only ever trades within its
    configured categories, so scanning outside them would be pure waste,
    exactly what this pass exists to eliminate. No categories configured
    falls back to scanning everything (matches _get_top_series' own
    no-categories fallback)."""
    all_series = await _get_series_cache(client)
    categories = cfg["kalshi"].get("categories")
    if categories:
        all_series = [s for s in all_series if s.get("category") in categories]
    batch = market_catalog.next_series_to_scan(all_series, _CATALOG_SCAN_BATCH_SIZE)
    if not batch:
        return
    results = await asyncio.gather(
        *(client.get_markets(limit=100, status="open", series_ticker=s["ticker"]) for s in batch),
        return_exceptions=True,
    )
    now = time.time()
    # Data-robustness audit finding (2026-08-10): mark_scanned() used to be
    # called for the WHOLE batch unconditionally, regardless of whether each
    # series' fetch actually succeeded - return_exceptions=True above
    # swallows a failure with no logging at all, so a persistently-failing
    # series (rate limit, malformed/renamed ticker, transient API error)
    # would mark itself "freshly scanned" every ~59-minute rotation forever,
    # looking identical to a series that's simply quiet, while never
    # actually writing a row. Only the series that genuinely succeeded this
    # batch get marked scanned; a failing one stays at the front of the
    # least-recently-scanned queue and gets retried next tick instead of
    # silently going stale for good.
    succeeded = []
    for s, result in zip(batch, results):
        if isinstance(result, list):
            market_catalog.upsert_markets(s["ticker"], s.get("category"), result, updated_at=now)
            succeeded.append(s["ticker"])
        else:
            # No logging framework exists anywhere in this app yet (audit
            # finding) - stdout is captured by `ddev logs -s fastapi` per
            # this project's own documented workflow, so a failing series is
            # at least visible there instead of vanishing with zero trace.
            print(f"[market_catalog] scan failed for {s['ticker']!r}, will retry next tick: {result!r}")
    if succeeded:
        market_catalog.mark_scanned(succeeded, scanned_at=now)


_CATALOG_SCAN_MIN_INTERVAL_SEC = 15  # how often a new background batch may be KICKED OFF - the
# shared token-bucket rate limiter (services/http_client.py), not this interval, is what actually
# keeps the real aggregate call rate safe; this just avoids spawning pointless overlapping tasks.


def _maybe_scan_catalog_batch(cfg: dict) -> None:
    """Triggers _scan_catalog_batch as an independent background task on
    its own steady interval, decoupled from the main tick entirely - same
    pattern (and same 2026-08-15 "no stone unturned" API audit) as
    discovery's own _maybe_refresh_discovery_cache. Synchronous/non-
    blocking on purpose, exactly like that sibling function."""
    catalog_state = state["catalog_scan"]
    now_ts = time.time()
    due = now_ts - catalog_state["last_started_at"] > _CATALOG_SCAN_MIN_INTERVAL_SEC
    if due and not catalog_state["scanning"]:
        catalog_state["scanning"] = True
        catalog_state["last_started_at"] = now_ts
        catalog_state["task"] = asyncio.create_task(_scan_catalog_batch_background(cfg))


async def _scan_catalog_batch_background(cfg: dict) -> None:
    """Owns its own KalshiClient (not the calling tick's, which closes at
    the end of that same tick - see _refresh_discovery_cache's identical
    reasoning) and delegates the real work to _scan_catalog_batch
    unchanged, so its existing behavior/tests keep working when called
    directly with an explicit client."""
    catalog_state = state["catalog_scan"]
    client = KalshiClient(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])
    try:
        await _scan_catalog_batch(client, cfg)
    except Exception as exc:
        print(f"[market_catalog] background scan batch failed entirely, will retry next cycle: {exc!r}")
    finally:
        catalog_state["scanning"] = False
        await client.close()


async def _fetch_category_metadata(client: KalshiClient, ttl_sec: int = 3600) -> dict:
    cache = state["category_metadata"]
    now = time.time()
    if cache.get("fetched_at") and (now - cache["fetched_at"]) < ttl_sec:
        return cache
    try:
        tags_resp, sports_resp = await asyncio.gather(
            client.get_tags_for_series_categories(),
            client.get_filters_for_sports(),
        )
        filters_by_sports = (sports_resp or {}).get("filters_by_sports") or {}
        cache.update({
            "fetched_at": now,
            "tags_by_categories": (tags_resp or {}).get("tags_by_categories") or {},
            "filters_by_sports": filters_by_sports,
            "sport_ordering": (sports_resp or {}).get("sport_ordering") or [],
            # competition -> sport reverse lookup (2026-08-16 direct
            # standing instruction: check docs/kalshi/ for already-
            # available fields before deriving/guessing - get-filters-for-
            # sports.md documents filters_by_sports as {sport: {scopes,
            # competitions: {competition: {scopes}}}}, confirmed live:
            # filters_by_sports["Baseball"]["competitions"] includes "Pro
            # Baseball", "Japan NPB", "Korea KBO", "Mexico LMB" - all one
            # sport, several competitions. The whale-confidence subcategory
            # tier (services/trade_category.py) wants SPORT ("Baseball",
            # matching the user's own "baseball, football" examples), not
            # the finer per-competition string a bare event.competition
            # read would give ("Pro Baseball") - built once per hourly
            # refresh here, not per-trade in _subcategory_by_ticker.
            "sport_by_competition": {
                competition: sport
                for sport, details in filters_by_sports.items()
                for competition in (details.get("competitions") or {})
            },
        })
    except Exception:
        pass
    return cache


async def _cached_market_fetch(client: KalshiClient, tickers: list[str]) -> list[dict]:
    """Shared by _fetch_markets' pinned-watchlist and extra_tickers
    handling (2026-08-15, "websocket stream everything you can... leave
    the api calls for things that are absolutely necessary") - both used
    to re-fetch every one of their tickers via individual REST get_market()
    calls on every single tick, unconditionally. A ticker's structural
    fields (title, event_ticker, occurrence_datetime, close_time, status)
    change rarely; price comes from the WS ticker-channel stream instead
    (_fetch_markets' own live-price overlay right before it returns), not
    from re-fetching the whole market object. state["market_object_cache"]
    (one shared cache, not two - same ticker->market shape and refresh
    semantics either way) only bounds how stale the *structural* fields
    can get, via _PINNED_MARKET_REFRESH_SEC - never price."""
    cache = state["market_object_cache"]
    now_ts = time.time()
    stale_or_missing = [
        t for t in tickers
        if t not in cache or (now_ts - cache[t]["_cached_at"]) > _PINNED_MARKET_REFRESH_SEC
    ]
    if stale_or_missing:
        # Batched (2026-08-16, direct efficiency note: "a lot of efficiency
        # could be gained by using batch calls to the API vs individual
        # calls for specific markets") - was N individual get_market() calls
        # gathered concurrently; one get_markets_by_tickers call now covers
        # every stale/missing ticker regardless of how many need it.
        fetched = await client.get_markets_by_tickers(stale_or_missing)
        for t in stale_or_missing:
            r = fetched.get(t)
            if r is not None:
                r["_cached_at"] = now_ts
                cache[t] = r
    return [
        {k: v for k, v in cache[t].items() if k != "_cached_at"}
        for t in tickers if t in cache
    ]


def _maybe_refresh_discovery_cache(cfg: dict) -> None:
    """Kicks off _refresh_discovery_cache_background as an independent
    background task if the cache is stale and no refresh is already
    running - never awaited by the calling tick (see _fetch_markets' own
    comment, and _DISCOVERY_REFRESH_SEC's, for the incident this fixes).
    Synchronous on purpose: this only ever schedules work, it never does
    any I/O of its own, so there's nothing to await here even though the
    work it schedules is async. Stores the created Task on discovery_cache
    itself so it isn't garbage-collected mid-flight - a live asyncio
    footgun, a Task object with no reference anywhere can be collected
    before it completes even though it's still "running" on the event
    loop.

    Takes no client - _refresh_discovery_cache_background creates its own
    (2026-08-16 fix, real live incident: this used to hand the calling
    tick's own KalshiClient straight into the background task, but that
    same tick's own `finally: await client.close()` closes it at the end of
    that tick, well before an independent background task reliably
    finishes - a real client-lifecycle race, confirmed live via repeated
    "[discovery] background refresh failed" log lines cycling through
    RuntimeError('Session is closed')/ClientConnectionError('Connector is
    closed.')/AssertionError()). See _refresh_discovery_cache_background's
    own docstring for the fix."""
    disc_cache = state["discovery_cache"]
    now_ts = time.time()
    stale = now_ts - disc_cache["fetched_at"] > _DISCOVERY_REFRESH_SEC
    if stale and not disc_cache["refreshing"]:
        disc_cache["refreshing"] = True
        disc_cache["task"] = asyncio.create_task(_refresh_discovery_cache_background(cfg))


# Real, live-confirmed finding (2026-08-16, "close_time mutability"
# investigation - see ROADMAP.md's now-closed "Active investigation" entry):
# Kalshi's own market_lifecycle docs (docs/kalshi/market_lifecycle.md)
# confirm close_time can be revised earlier via a close_date_updated event
# "when a market is closed ahead of its scheduled close time, including
# before determination" - and a catalog row scanned before that revision
# fires keeps showing the OLD close_time/status indefinitely until its next
# scan (confirmed live: a real finalized MLB market's catalog row still
# showed status=active, close_time 2.5 days out, 24 minutes after Kalshi's
# own API had already moved it to status=finalized with the true, earlier
# close_time). candidates_in_window/open_candidates' close_ts>now filter
# can't catch this - it's trusting the same stale column that's wrong. Any
# status past "active" in the real lifecycle (closed/determined/disputed/
# amended/finalized - see market_lifecycle.md's own table) means the
# catalog's belief about this market is no longer trustworthy regardless of
# what close_ts says.
_DISCOVERY_TERMINAL_STATUSES = {"closed", "determined", "disputed", "amended", "finalized"}


async def _refresh_discovery_cache(cfg: dict, client: KalshiClient) -> None:
    """Discovery's selection pipeline, now sourced entirely from
    market_catalog's already-persisted, independently-scanned data
    (services/market_catalog.py's open_candidates) instead of a fresh
    get_candidate_markets REST fetch per series - 2026-08-15 direct "no
    stone unturned" API audit, the definitive fix for the same incident
    _DISCOVERY_REFRESH_SEC's own comment describes: "no more excessive api
    calls to get non trading data that is unlikely to change." Only one
    small network call of its own now (see the real-time confirmation step
    below, 2026-08-16) - the catalog itself still stays warm via the fully
    separate _scan_catalog_batch/_maybe_scan_catalog_batch background task,
    paced independently by the same shared rate limiter. Still run as a
    background task (async def, still triggered by
    _maybe_refresh_discovery_cache below) rather than inlined
    synchronously - defensive: a SQLite query + phase_ranked/
    round_robin_select over a large, still-growing catalog should stay
    fast, but "still fast" isn't a promise worth betting the tick loop's
    responsiveness on now that it doesn't have to.

    Selects the top top_series_per_category DISTINCT SERIES per category
    by walking open_candidates' already volume-sorted rows (mirrors the
    old _get_top_series' per-category cap exactly, just computed from
    current per-market catalog volume instead of a series' lifetime
    total - arguably the more relevant signal for "worth watching right
    now," not a weaker substitute for it), then keeps only markets
    belonging to a selected series before phase-ranking/final selection -
    unchanged from before this fix.

    Pure worker, takes client as a param (2026-08-16 client-lifecycle fix -
    see _refresh_discovery_cache_background's own docstring for why this
    doesn't own/close the client itself) - same split
    _check_signal_resolutions/_check_signal_resolutions_background already
    established for this exact same shape of problem. Lets exceptions
    propagate rather than swallowing them - the background wrapper is what
    catches/logs/retries; tests call this directly with a fake client and
    should see real failures, not a silently-eaten one."""
    disc_cache = state["discovery_cache"]
    min_volume = cfg["kalshi"].get("min_volume_24h", 0)
    categories = cfg["kalshi"].get("categories")
    top_n_per_category = cfg["kalshi"].get("top_series_per_category", 12)
    catalog_rows = market_catalog.open_candidates(
        categories=categories, min_volume=min_volume,
        min_volume_by_series=cfg["kalshi"].get("min_volume_24h_by_series"),
    )
    selected_series_by_category: dict[str, list[str]] = {}
    for row in catalog_rows:
        cat, series = row.get("category"), row.get("series_ticker")
        if not cat or not series:
            continue
        bucket = selected_series_by_category.setdefault(cat, [])
        if series not in bucket and len(bucket) < top_n_per_category:
            bucket.append(series)
    selected_series = {s for bucket in selected_series_by_category.values() for s in bucket}
    if cfg.get("series_evaluator", {}).get("enabled"):
        ineligible = series_evaluator.ineligible_series(time.time())
        selected_series -= set(ineligible)
    candidates = [r for r in catalog_rows if r.get("series_ticker") in selected_series]
    el_cfg = cfg.get("event_lifecycle") or {}
    candidates = event_lifecycle.phase_ranked(
        candidates, state["event_titles"], now=time.time(),
        tournament_min_siblings=el_cfg.get("tournament_min_siblings", 4),
        tournament_pretail_days=el_cfg.get("tournament_pretail_days", 5.0),
        pre_tail_volume_weight=el_cfg.get("pre_tail_volume_weight", 0.4),
        post_tail_volume_weight=el_cfg.get("post_tail_volume_weight", 0.2),
    )
    markets = KalshiClient.round_robin_select(
        candidates, cfg["kalshi"]["watchlist_size"],
        max_children_per_parent=cfg["kalshi"].get("max_children_per_parent"),
    )
    # Real-time confirmation pass (2026-08-16, close_time-mutability fix
    # - see _DISCOVERY_TERMINAL_STATUSES above). Bounded to exactly the
    # final, already-narrowed selection (watchlist_size, not the whole
    # candidate pool), so this stays one cheap batched call per refresh
    # cycle (_DISCOVERY_REFRESH_SEC = 300s), not a return to the
    # per-refresh REST-fetch pattern the 2026-08-15 incident removed.
    # Drops anything Kalshi now reports as past "active" outright - a
    # stale catalog row must never reach the real watchlist just
    # because it hasn't been rescanned yet. A ticker Kalshi didn't
    # return (a genuine fetch miss) keeps its catalog row rather than
    # being dropped - same "degrade honestly, never guess" idiom
    # _fetch_markets' own live_markets_only hydration already uses.
    selected_tickers = [m["ticker"] for m in markets if m.get("ticker")]
    if selected_tickers:
        confirmed = await client.get_markets_by_tickers(selected_tickers)
        live_markets = []
        for m in markets:
            real = confirmed.get(m.get("ticker"))
            if real is None:
                live_markets.append(m)
                continue
            if (real.get("status") or "").strip().lower() in _DISCOVERY_TERMINAL_STATUSES:
                continue  # confirmed no longer tradeable - drop before it ever reaches the watchlist
            live_markets.append(real)  # real current price/status, not the catalog's possibly-stale copy
        markets = live_markets
    disc_cache["markets"] = list(markets)
    disc_cache["fetched_at"] = time.time()


async def _refresh_discovery_cache_background(cfg: dict) -> None:
    """Background-task wrapper around _refresh_discovery_cache - owns its own
    KalshiClient (2026-08-16 client-lifecycle fix, real live incident:
    _maybe_refresh_discovery_cache used to hand this the calling tick's own
    client, but that same tick's own `finally: await client.close()` closes
    it at the end of that tick regardless of whether this independent
    background task has finished with it - confirmed live via repeated
    "[discovery] background refresh failed" log lines cycling through
    RuntimeError('Session is closed')/ClientConnectionError('Connector is
    closed.')/AssertionError()). Same split as
    _check_signal_resolutions_background/_check_signal_resolutions - that
    function's own docstring already described this exact pattern as if
    _refresh_discovery_cache followed it too, which is what surfaced this
    gap on review."""
    disc_cache = state["discovery_cache"]
    client = KalshiClient(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])
    try:
        await _refresh_discovery_cache(cfg, client)
    except Exception as exc:
        # No logging framework exists anywhere in this app yet (same gap
        # _scan_catalog_batch's own per-series failure print already
        # documented) - stdout is captured by `ddev logs -s fastapi`, so a
        # failed background refresh is at least visible there instead of
        # vanishing with zero trace. The stale cache stays in place and
        # _maybe_refresh_discovery_cache will try again next time it's due.
        print(f"[discovery] background refresh failed, will retry next cycle: {exc!r}")
    finally:
        disc_cache["refreshing"] = False
        await client.close()


async def _fetch_markets(client: KalshiClient, cfg: dict, extra_tickers: list[str] | None = None) -> list[dict]:
    # Real live report (2026-08-15): kalshi.markets_watchlist used to be a
    # strict either/or with discovery below - a non-empty pinned list
    # replaced round-robin discovery entirely rather than adding to it, so
    # pinning a handful of tickers (e.g. one political market's own
    # candidates) silently zeroed out every other category's whale-signal
    # coverage for as long as the pin stayed set. Direct instruction:
    # "merge: keep KXPRESNOMD pinned + add real discovery." Merge is the
    # default (kalshi.markets_watchlist_mode: merge) - the pinned list is
    # fetched and merged with whatever discovery below finds, and pinned
    # tickers don't count against watchlist_size's cap, same "always
    # included, exempt from the cap" treatment extra_tickers already gets
    # a few lines down.
    #
    # 2026-08-17 direct request restored the choice this fix removed, as an
    # explicit opt-in rather than the old implicit either/or:
    # markets_watchlist_mode: "exclusive" skips discovery entirely (see
    # below) so the watchlist is ONLY the pinned list - for deliberately
    # narrowing to a hand-picked set rather than the 2026-08-15 bug's
    # accidental version of the same thing.
    #
    # Series-level pins (2026-08-16 direct request: "the market watch list
    # should act as that override, that's what the pinned list is for" -
    # KXBTC15M can never pass live_markets_only's milestone-based live-
    # status check by design, no matter what volume overrides exist). Each
    # watchlist entry is tried against market_catalog.open_markets_for_series
    # first - a literal exact ticker never matches any row's series_ticker
    # column, so it naturally falls through to the existing exact-ticker
    # path below. A series pin resolves to whatever instance(s) are
    # currently open, every refresh - so a rolling 15-minute series stays
    # pinned across rollovers instead of going stale the way a literal
    # ticker pin would.
    watchlist = cfg["kalshi"]["markets_watchlist"]
    series_pinned_markets: list[dict] = []
    literal_pins: list[str] = []
    for entry in watchlist:
        series_markets = market_catalog.open_markets_for_series(entry)
        if series_markets:
            series_pinned_markets.extend(series_markets)
        else:
            literal_pins.append(entry)
    pinned_markets = series_pinned_markets + (
        await _cached_market_fetch(client, literal_pins) if literal_pins else []
    )

    # Exclusive mode (2026-08-17 direct request: "give the option to merge
    # with discovery or make it exclusive to the manual list") - skips
    # BOTH discovery branches below entirely, including the real REST
    # hydration calls the live_markets_only path makes, rather than running
    # discovery and throwing its result away at the merge step. `markets`
    # ends up exactly `pinned_markets` once the merge below runs a no-op
    # union against an empty list.
    exclusive = cfg["kalshi"].get("markets_watchlist_mode") == "exclusive"
    min_volume = cfg["kalshi"].get("min_volume_24h", 0)
    if exclusive:
        markets: list[dict] = []
    elif cfg["kalshi"].get("live_markets_only"):
        # Direct request: discovery itself, not just whether an already-
        # selected market's signal gets acted on, should be able to only
        # ever pick currently-live markets. Round-robin's usual top-n cut
        # happens *after* filtering here, not before - checking live
        # status only on an already-narrowed watchlist would mean "only
        # live" really meant "only live among whichever 50 happened to
        # win on volume," which could easily be zero of them.
        #
        # Candidates come from market_catalog (see _scan_catalog_batch),
        # not a fresh top-40-series fetch - confirmed directly against
        # real Kalshi data that volume-ranking the candidate pool misses
        # almost everything actually live right now (a series can be
        # high-volume overall with nothing airing this exact hour, and
        # vice versa). The catalog is scanned incrementally in the
        # background and may be sparse/empty right after this feature is
        # first turned on - that's an honest, self-correcting transient
        # state (see market_catalog.py), not backfilled with anything
        # fabricated.
        now = time.time()
        candidates = market_catalog.candidates_in_window(
            now, lookahead_sec=_LIVE_STATUS_LOOKAHEAD_SEC, lookback_sec=_LIVE_STATUS_LOOKBACK_SEC,
            min_volume=min_volume,
        )
        candidate_live_status = await _fetch_live_status(client, candidates)
        live_candidates = [
            m for m in candidates
            if candidate_live_status.get(m.get("event_ticker")) == "live"
        ]
        # series_evaluator's BEFORE-check (direct request): the watchlist
        # is fully recomputed from scratch every tick with zero memory,
        # so a series flapping near this filter's own boundary would
        # otherwise be re-added/re-evaluated/re-removed indefinitely.
        # Cheap, one batch query, gated behind series_evaluator.enabled
        # (default off) so this never changes discovery behavior for
        # anyone who hasn't opted in. Only applies to automatic
        # discovery, same carve-out kalshi.min_volume_24h already has -
        # pinned markets (the `if watchlist:` branch above) bypass this
        # entirely, same as every other automatic-discovery-only filter.
        if cfg.get("series_evaluator", {}).get("enabled"):
            ineligible = series_evaluator.ineligible_series(now)
            live_candidates = [
                m for m in live_candidates
                if signal_log.series_of(m.get("ticker")) not in ineligible
            ]
        # Never backfilled with non-live markets to hit watchlist_size -
        # direct choice: the watchlist shrinks (down to zero, if nothing
        # real is live right now) rather than quietly padding it with
        # markets that don't meet the filter someone deliberately turned on.
        markets = KalshiClient.round_robin_select(
            live_candidates, cfg["kalshi"]["watchlist_size"],
            max_children_per_parent=cfg["kalshi"].get("max_children_per_parent"),
        )
        # Real, confirmed-live bug: market_catalog rows only ever carry
        # schedule/title/volume metadata for discovery purposes (see
        # market_catalog.upsert_markets - no yes_bid_dollars/
        # yes_ask_dollars column exists), so every catalog-sourced
        # market silently fell through to state["latest_prices"]'s 0.5
        # fallback below - every card showed 50c/50c YES/NO and never
        # moved, for as long as live_markets_only has been on, direct
        # report: "showing 50c in green and red for all sets of yes/no
        # values all across the app. its not updating either." Final
        # selection is already bounded (watchlist_size parent series,
        # whatever max_children_per_parent allows), so re-fetching by
        # *series* here (one real get_markets(series_ticker=...) call
        # per distinct selected series, full priced market objects) is
        # the same per-series cost the non-live-only branch below
        # already pays - just deferred until after selection instead of
        # spent on the whole broad candidate pool.
        selected_tickers = {m["ticker"] for m in markets if m.get("ticker")}
        selected_series = sorted({signal_log.series_of(t) for t in selected_tickers})
        hydration_results = await asyncio.gather(
            *(client.get_markets(limit=100, status="open", series_ticker=s) for s in selected_series),
            return_exceptions=True,
        )
        hydrated_by_ticker = {}
        for r in hydration_results:
            if isinstance(r, list):
                for hm in r:
                    if hm.get("ticker") in selected_tickers:
                        hydrated_by_ticker[hm["ticker"]] = hm
        # A ticker that settled between the catalog scan and now won't
        # come back from the status="open" batch fetch above (confirmed
        # live: a handful of already-finalized markets were still
        # falling back to the 0.5 placeholder for exactly this reason) -
        # one batched fetch (no status filter, whatever its real current
        # state is - 2026-08-16 batching pass) for just what's still
        # missing, same "always the real current price, never a
        # placeholder" goal, cheap since this is normally a small
        # residual set.
        still_missing = [t for t in selected_tickers if t not in hydrated_by_ticker]
        if still_missing:
            fallback_results = await client.get_markets_by_tickers(still_missing)
            hydrated_by_ticker.update(fallback_results)
        # Still falls back to the original catalog row (schedule/title
        # info, just no live price) rather than dropping a ticker
        # outright if even the per-ticker fetch failed (a real API
        # error) - same "degrade honestly, never silently drop" pattern
        # as the rest of this app.
        markets = [hydrated_by_ticker.get(m["ticker"], m) for m in markets]
    else:
        # Discovery caching (2026-08-15, direct incident: "you made the
        # market watch list and whale watching grind to a halt and markets
        # aren't even appearing anymore") - a tick NEVER awaits the REST
        # discovery pipeline itself anymore, only reads whatever's already
        # in state["discovery_cache"] (possibly empty on a cold start,
        # possibly stale by up to _DISCOVERY_REFRESH_SEC - never blocking).
        # The actual fetch runs as an independent background task (see
        # _maybe_refresh_discovery_cache/_refresh_discovery_cache below) -
        # price freshness still comes from the WS ticker-stream overlay
        # right before this function returns, completely decoupled from
        # how often the underlying series/market *selection* gets re-run.
        _maybe_refresh_discovery_cache(cfg)
        markets = list(state["discovery_cache"]["markets"])

    # Merge in the pinned watchlist fetched at the top of this function -
    # always included, never counted against watchlist_size (same "always
    # included, exempt from the cap" treatment as extra_tickers just below).
    # Pinned first in list order (an explicit, deliberate pin reads as more
    # authoritative than whatever discovery happened to rank), discovery
    # results after, deduped by ticker.
    pinned_tickers = {m["ticker"] for m in pinned_markets if m.get("ticker")}
    markets = pinned_markets + [m for m in markets if m.get("ticker") not in pinned_tickers]

    # A currently-open paper position must keep getting a fresh price/title
    # every tick even if its market has rotated out of the top-volume
    # watchlist selection above - otherwise state["latest_prices"] silently
    # stops updating for it, which freezes mark_to_market and breaks
    # check_exits' take-profit/stop-loss/auto-exit triggers for a position
    # nobody's actively watching anymore even though real money (paper or
    # not) is still on the line.
    have = {m["ticker"] for m in markets if m.get("ticker")}
    missing = [t for t in (extra_tickers or []) if t not in have]
    if missing:
        # Cached, not re-fetched via REST every tick (2026-08-15, "websocket
        # stream everything you can") - same _cached_market_fetch as the
        # pinned watchlist above; price comes from the WS ticker-channel
        # overlay below regardless of when this last hit the real API.
        markets.extend(await _cached_market_fetch(client, missing))

    # Direct report (2026-08-11): "watchlist groupings is broken... likely a
    # result of the active removal of watchlist items. reorganization should
    # occur at the same time the watchlist updates." Confirmed: an open
    # position kept alive above after rotating out of round_robin_select's
    # own selection lands at the *end* of markets regardless of series - if
    # that position's series still has other members earlier in the list
    # (only this one ticker dropped, not the whole series), the frontend
    # (renderMarketCards' seriesRuns) - which assumes same-series markets are
    # always consecutive, since round_robin_select's own output guarantees
    # that - splits one series into two separate on-screen sections instead
    # of merging them. Re-groups by series here, preserving each series'
    # first-occurrence order (not an alphabetical sort, which would destroy
    # round_robin_select's volume-priority ordering) so any appended
    # straggler rejoins its series' existing run. Cheap - one pass, no extra
    # fetches - and also covers the manually-pinned kalshi.markets_watchlist
    # branch above, whose ticker order is whatever the user typed, not
    # necessarily grouped at all.
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for m in markets:
        key = signal_log.series_of(m.get("ticker") or "")
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(m)
    markets = [m for key in order for m in groups[key]]

    # Live-price overlay (2026-08-15, "websocket stream everything you can") -
    # applies regardless of source (pinned, freshly-discovered, or reused
    # from discovery_cache above): state["latest_prices"]/state["latest_asks"]
    # are kept continuously fresh by the WS ticker-channel stream
    # (_process_stream_ticker), independent of how often this function's own
    # REST discovery re-runs. A shallow copy, not an in-place mutation - the
    # entries in discovery_cache["markets"] must stay untouched by a single
    # tick's price overlay, or the cache would silently accumulate per-tick
    # state instead of remaining a clean "what was selected" snapshot.
    # Falls back to whatever price the market object already carried (its
    # own REST-fetched value) when no WS data has arrived for that ticker
    # yet - never guessed, same "missing isn't zero" idiom as the rest of
    # this app.
    latest_prices = state.get("latest_prices") or {}
    latest_asks = state.get("latest_asks") or {}
    overlaid = []
    for m in markets:
        ticker = m.get("ticker")
        if ticker and (ticker in latest_prices or ticker in latest_asks):
            m = dict(m)
            if ticker in latest_prices:
                m["yes_bid_dollars"] = latest_prices[ticker]
            if ticker in latest_asks:
                m["yes_ask_dollars"] = latest_asks[ticker]
        overlaid.append(m)
    return overlaid


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
# tick_duration investigation), same shape as _check_signal_resolutions' own
# limit=10 batching - to_poll had no cap at all, so whenever a large
# fraction of the in-window candidate pool (up to ~2,500 rows / 100+
# distinct events - see market_catalog.candidates_in_window, called from
# _fetch_markets' live_markets_only branch) became simultaneously due for
# their 5-minute repoll, this fired dozens-to-hundreds of concurrent REST
# calls in a single tick, saturating the shared, deliberately-conservative
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


async def _fetch_live_status(client: KalshiClient, markets: list[dict]) -> dict:
    """The real live/scheduled/finished status per event, via Kalshi's
    actual milestone/live-data system - confirmed directly against a real
    AFL match at its actual start time (status "scheduled"->"inprogress"->
    "closed", widget_status "none"->"live"->"finished"), not inferred from
    timestamps alone. Reads/writes state["live_status_cache"] directly
    (same module-global pattern _get_series_cache already uses for its own
    cache) rather than taking it as a parameter.

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

    milestone_results = await asyncio.gather(
        *(client.get_milestones_for_event(et) for et in to_poll), return_exceptions=True
    )
    # Batched (2026-08-16 API-doc audit finding B3.2, docs/kalshi/
    # get-live-data.md) - was N individual get_live_data() calls via
    # asyncio.gather, one per event with a milestone. Live-verified: 3
    # individual = 0.99s wall, 1 batched get_live_datas call = 0.02s wall.
    milestone_by_event = {}
    has_milestone = set()
    for et, ms_result in zip(to_poll, milestone_results):
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
            # audit finding B2, docs/kalshi/get-live-data.md) - the exact
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


async def _fetch_exchange_status(client: KalshiClient) -> dict | None:
    # A transient hiccup here shouldn't take down the whole poll tick the way
    # a markets/account failure would (nothing downstream depends on it) —
    # swallow and keep the last known status rather than clearing it.
    try:
        return await client.get_exchange_status()
    except Exception:
        return None


async def _fetch_event_titles(client: KalshiClient, markets: list[dict]) -> dict:
    """Fetches every not-yet-cached event's own title/sub_title/category -
    not just events with sibling markets (an earlier, narrower version of
    this only fetched for multi-outcome groups; broadened because this data
    is also what answers "what sport, who vs who" for a *single* market's
    display, not just grouping). Cached in state["event_titles"]
    (accumulates, capped like market_titles) so an event only needs
    fetching once even as the watchlist rotates - a typical watchlist
    (8-20 markets) means at most that many new lookups on a given tick, and
    usually zero once the cache is warm.

    event.get("subtitle") looked plausible but was wrong - the real field
    is sub_title (confirmed directly against a live event: "SD vs AZ (Aug
    6)" only came back under that key), so this was silently returning None
    for every event until caught.

    Also re-fetches an already-cached event if its cached entry has no
    mutually_exclusive value yet (None) - real Kalshi events always return
    a real True/False for this field, so a cached None uniquely means "this
    entry predates that field being extracted here," not a genuine value.
    Without this, every event cached before mutually_exclusive was added
    would stay permanently None forever (this function only ever fetches
    what's "not yet cached" - confirmed live: every entry already in
    data/title_cache.db showed null for it after the field was added,
    since none of them had ever been "not yet cached" again). This
    self-heals over the next few ticks as each event naturally reappears in
    the watchlist, no one-time backfill script or DB wipe needed."""
    required_event_fields = {
        "mutually_exclusive", "series_ticker", "available_on_brokers",
        "product_metadata", "settlement_sources", "strike_date",
        "strike_period", "fee_type_override", "fee_multiplier_override",
        "last_updated_ts",
    }
    to_fetch = [
        m["event_ticker"] for m in markets
        if m.get("event_ticker") and (
            m["event_ticker"] not in state["event_titles"]
            or state["event_titles"][m["event_ticker"]].get("mutually_exclusive") is None
            or any(
                field not in state["event_titles"][m["event_ticker"]]
                for field in required_event_fields
            )
        )
    ]
    to_fetch = list(dict.fromkeys(to_fetch))  # de-dupe, preserve order
    if not to_fetch:
        return {}
    # Batched (2026-08-16 API-doc audit finding B3.1, docs/kalshi/
    # get-events.md) - was N individual get_event() calls via
    # asyncio.gather, one per not-yet-cached event ticker every tick. Live-
    # verified: 3 individual = 0.36s wall, 1 batched call = 0.02s wall, same
    # events returned, no misses. A ticker Kalshi doesn't return (renamed,
    # removed) just doesn't appear in `by_ticker` below and is silently
    # skipped this tick, same as a failed get_event() used to be.
    try:
        events = await client.get_events(to_fetch)
    except Exception:
        events = []
    by_ticker = {e["event_ticker"]: e for e in events if e.get("event_ticker")}
    fetched = {}
    for et in to_fetch:
        event = by_ticker.get(et)
        if event is not None:
            fetched[et] = {
                "title": event.get("title") or et,
                "sub_title": event.get("sub_title"),
                "category": event.get("category"),
                "series_ticker": event.get("series_ticker"),
                "available_on_brokers": event.get("available_on_brokers"),
                "collateral_return_type": event.get("collateral_return_type"),
                # Kalshi's own real field for "exactly one of this event's
                # sibling markets resolves YES" - already present in every
                # get_event() response above, previously discarded. Lets
                # the dashboard tell a genuine 2-outcome inversion pair
                # ("Toronto vs Philadelphia Winner" - the two sibling
                # markets are the same information mirrored, confirmed
                # live: their yes_bid prices sum to ~1.0) apart from
                # sibling markets that are independent props sharing an
                # event but NOT mutually exclusive (e.g. "Max Scherzer 15+
                # outs" and "Aaron Nola 18+ outs") or a genuine multi-way
                # market (e.g. "Wyndham Championship Winner", 60+ golfers,
                # also mutually_exclusive but with no simple pairwise
                # complement) - see eventGroupCardHTML in static/index.html.
                "mutually_exclusive": event.get("mutually_exclusive"),
                # product_metadata.competition/competition_scope - real
                # fields, same "already fetched here, previously discarded"
                # finding. Direct display value only (e.g. "Wyndham
                # Championship" shown on a golf pairing's event card) - NOT
                # used for grouping, since it's tournament-specific for golf
                # but sport-generic for esports ("Dota 2", shared by
                # unrelated matches, confirmed live) and so can't safely
                # replace series_of/round_robin_select's own grouping logic
                # (see ROADMAP.md's parent/child grouping work). Legitimately
                # absent for most non-competitor markets (politics,
                # economics) - unlike mutually_exclusive, a missing value
                # here is a real "this event has no competition," not a
                # backfill signal, so it isn't part of the re-fetch check
                # above.
                "competition": (event.get("product_metadata") or {}).get("competition"),
                "competition_scope": (event.get("product_metadata") or {}).get("competition_scope"),
                "product_metadata": event.get("product_metadata") or {},
                "settlement_sources": event.get("settlement_sources") or [],
                "strike_date": event.get("strike_date"),
                "strike_period": event.get("strike_period"),
                "fee_type_override": event.get("fee_type_override"),
                "fee_multiplier_override": event.get("fee_multiplier_override"),
                "last_updated_ts": event.get("last_updated_ts"),
            }
    return fetched


_EVENT_LIVE_DATA_REPOLL_SEC = 60  # Repoll-cached (2026-08-15 tick_duration
# fix) - same fix, same root cause as _MILESTONE_REPOLL_SEC above: this
# called get_event_live_data() for every unique event on the watchlist,
# every tick, forever, unconditionally. Real live game-state data can
# change fast during an actual live event, so this stays much shorter than
# _LIVE_STATUS_REPOLL_SEC's 5 minutes, but per-tick (~every 15s) was never
# the right cadence either.

_EVENT_LIVE_DATA_EXCLUDED_CATEGORIES = {"Sports"}  # 2026-08-16 API-doc audit
# finding B2 (docs/kalshi/get-event-live-data.md, docs/next-steps-2026-08-15-
# pt3.md): this endpoint is event-ticker-keyed and documented/confirmed to
# serve "crypto price charts, commodity price timeseries, weather
# observations" - live-verified against 3 real crypto tickers (KXBTC15M-*),
# which returned real BTC candlestick data. Sports is the one category
# confirmed NOT served here: every real sports ticker on the watchlist
# 404s from this endpoint 100% of the time - not a bug, structurally the
# wrong data source (the real source for sports live state is the
# milestone-keyed get_live_data(s), which _fetch_live_status and
# propagate_milestone_winners already call - see state["live_game_state"]).
# Corrects an earlier, less careful same-day comment on this constant that
# guessed crypto didn't work here either - it does; only Sports is excluded,
# and only because it's actually confirmed wasteful, not guessed at. Any
# other category without live confirmation either way is deliberately left
# in rather than excluded on a guess, same "don't fabricate" idiom
# _fetch_live_status's own schedule-fallback already follows.


async def _fetch_event_live_data(client: KalshiClient, markets: list[dict]) -> dict:
    event_tickers = list(dict.fromkeys(
        m["event_ticker"] for m in markets if m.get("event_ticker")
    ))
    if not event_tickers:
        return {}
    cache = state["event_live_data_cache"]
    now = time.time()
    # A brand-new event's category isn't known yet on the very first tick it
    # appears (_fetch_event_titles runs concurrently with this function, not
    # before it - state["event_titles"] only reflects prior ticks' fetches
    # during this call). Category-unknown events are polled anyway rather
    # than guess-excluded; the exclusion self-corrects from the next tick
    # once event_titles has caught up, same self-healing shape
    # _fetch_event_titles's own mutually_exclusive backfill already uses.
    to_poll = [
        et for et in event_tickers
        if (et not in cache or (now - cache[et]["checked_at"]) >= _EVENT_LIVE_DATA_REPOLL_SEC)
        and (state["event_titles"].get(et) or {}).get("category") not in _EVENT_LIVE_DATA_EXCLUDED_CATEGORIES
    ]
    if to_poll:
        results = await asyncio.gather(
            *(client.get_event_live_data(et) for et in to_poll), return_exceptions=True
        )
        for et, result in zip(to_poll, results):
            live_data = None
            if isinstance(result, dict):
                ld = result.get("live_data") or {}
                if ld:
                    # **ld first (2026-08-17): this used to keep five named
                    # keys and drop the rest of the live-data response.
                    # Same instruction, same reason as
                    # KalshiTradeWebSocketClient.normalize_trade - a field
                    # Kalshi adds should arrive intact rather than be
                    # discarded before anything can notice it exists. The
                    # explicit keys still win, so `details` is still
                    # guaranteed to be a dict and `range_options` a list for
                    # every existing consumer.
                    live_data = {
                        **ld,
                        "type": ld.get("type"),
                        "details": ld.get("details") or {},
                        "is_historical": ld.get("is_historical"),
                        "default_range": ld.get("default_range"),
                        "range_options": ld.get("range_options") or [],
                    }
            cache[et] = {"data": live_data, "checked_at": now}
            # Persist it (2026-08-17). This is the live-data path that is
            # actually populated in practice - the milestone-driven one in
            # _fetch_live_status only fires for events Kalshi tracks a
            # milestone for, and was measured empty while THIS held six live
            # entries. For crypto events the payload carries OHLC
            # candlesticks and an underlying price timeseries; for games it
            # carries score/period/clock. Both are fetched every tick
            # already and both were living only in memory. Rate-limited and
            # deduplicated inside game_state.record.
            if live_data and (live_data.get("details") or {}):
                game_state.record(
                    et, live_data["details"],
                    sport=_sport_for_event(state["event_titles"].get(et) or {}),
                    event_type=live_data.get("type"),
                )
    return {et: cache[et]["data"] for et in event_tickers if cache.get(et, {}).get("data") is not None}
