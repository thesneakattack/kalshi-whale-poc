"""
Category metadata, cached pinned-ticker fetches, and the automatic
watchlist discovery pipeline - the background task that selects which
markets/events trading_loop sees each tick when no explicit pin covers
them. Split out of market_watch.py (2026-08-22 modularization Phase 9/9).
"""
import asyncio
import time

from services import series_evaluator, task_supervisor
from services.app_state import state
from services.kalshi_client import KalshiClient
from services.market_catalog import market_catalog
from services.market_events import event_lifecycle

_PINNED_MARKET_REFRESH_SEC = 300  # structural fields (title, close_time, status, ...) for a
# manually-pinned ticker change rarely - price freshness comes from the WS ticker stream instead
# (see market_fetch._fetch_markets' live-price overlay), not from re-fetching the whole market
# object every tick.

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
# every tick by the WS ticker-channel stream - see main._process_stream_ticker and market_fetch.
# _fetch_markets' own set_market_tickers call), so price freshness is fully decoupled from how
# often the underlying selection itself gets re-run.


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
    """Shared by market_fetch._fetch_markets' pinned-watchlist and
    extra_tickers handling (2026-08-15, "websocket stream everything you
    can... leave the api calls for things that are absolutely necessary") -
    both used to re-fetch every one of their tickers via individual REST
    get_market() calls on every single tick, unconditionally. A ticker's
    structural fields (title, event_ticker, occurrence_datetime, close_time,
    status) change rarely; price comes from the WS ticker-channel stream
    instead (_fetch_markets' own live-price overlay right before it
    returns), not from re-fetching the whole market object.
    state["market_object_cache"] (one shared cache, not two - same ticker->
    market shape and refresh semantics either way) only bounds how stale
    the *structural* fields can get, via _PINNED_MARKET_REFRESH_SEC - never
    price."""
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
    running - never awaited by the calling tick (see market_fetch.
    _fetch_markets' own comment, and _DISCOVERY_REFRESH_SEC's, for the
    incident this fixes). Synchronous on purpose: this only ever schedules
    work, it never does any I/O of its own, so there's nothing to await
    here even though the work it schedules is async. Stores the created
    Task on discovery_cache itself so it isn't garbage-collected mid-flight
    - a live asyncio footgun, a Task object with no reference anywhere can
    be collected before it completes even though it's still "running" on
    the event loop.

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
        disc_cache["task"] = task_supervisor.supervise(
            lambda: _refresh_discovery_cache_background(cfg),
            component="discovery_cache", operation="refresh",
        )


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
    (services/market_catalog/market_catalog.py's open_candidates) instead of a fresh
    get_candidate_markets REST fetch per series - 2026-08-15 direct "no
    stone unturned" API audit, the definitive fix for the same incident
    _DISCOVERY_REFRESH_SEC's own comment describes: "no more excessive api
    calls to get non trading data that is unlikely to change." Only one
    small network call of its own now (see the real-time confirmation step
    below, 2026-08-16) - the catalog itself still stays warm via the fully
    separate catalog_scan._scan_catalog_batch/_maybe_scan_catalog_batch
    background task, paced independently by the same shared rate limiter.
    Still run as a background task (async def, still triggered by
    _maybe_refresh_discovery_cache above) rather than inlined
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
    main._check_signal_resolutions/_check_signal_resolutions_background
    already established for this exact same shape of problem. Lets
    exceptions propagate rather than swallowing them - the background
    wrapper is what catches/logs/retries; tests call this directly with a
    fake client and should see real failures, not a silently-eaten one."""
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
    # market_fetch._fetch_markets' own live_markets_only hydration
    # already uses.
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
    main._check_signal_resolutions_background/_check_signal_resolutions -
    that function's own docstring already described this exact pattern as
    if _refresh_discovery_cache followed it too, which is what surfaced
    this gap on review. Exceptions are now caught and recorded by
    task_supervisor.supervise (the caller, see _maybe_refresh_discovery_cache
    above) instead of a bare print - this only needs its own finally to
    release the "refreshing" flag and close the client regardless of
    outcome. The stale cache stays in place and _maybe_refresh_discovery_cache
    will try again next time it's due."""
    disc_cache = state["discovery_cache"]
    client = KalshiClient(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])
    try:
        await _refresh_discovery_cache(cfg, client)
    finally:
        disc_cache["refreshing"] = False
        await client.close()
