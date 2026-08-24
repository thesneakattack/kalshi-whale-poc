"""
_fetch_markets - the per-tick orchestrator that assembles the actual
watchlist (pinned + discovery + extra_tickers, deduped, series-grouped,
live-price overlaid). Split out of market_watch.py (2026-08-22
modularization Phase 9/9). Depends on discovery_cache.py (pinned/cached
market fetch, the background discovery cache) and live_status.py
(live_markets_only's live-status filter + its window constants) - the one
file in this split with real cross-sibling imports, since it's the
top-level orchestrator the other pieces feed into.
"""
import time

from services import series_evaluator, signal_log
from services.app_state import state
from services.kalshi_client import KalshiClient
from services.market_catalog import market_catalog
from services.market_watch.discovery_cache import _cached_market_fetch, _maybe_refresh_discovery_cache
from services.market_watch.live_status import _fetch_live_status, _LIVE_STATUS_LOOKAHEAD_SEC, _LIVE_STATUS_LOOKBACK_SEC

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
    # expected_expiration_time added 2026-08-24 (direct report: "trading
    # windows... too much guesswork on the open/close bounds... will close
    # at the conclusion of that event vs. the scheduled market close
    # time"). docs/kalshi/market_lifecycle.md documents this precisely:
    # "the time the event is likely to resolve... close_time may be set
    # well into the future to allow for rescheduling" - exactly the gap
    # reported live (KXVOTEPRIMARY-FLPRIMARY06R26ABAK-9's close_time was
    # 359.5 days out while its real primary was 5.5 days in the past).
    # Zero extra API cost - already present on every market object this
    # app already fetches, just previously discarded at this slim step.
    # See services/market_lookup.py's effective_close_time().
    "expected_expiration_time",
)


def _slim_market(m: dict) -> dict:
    return {k: m.get(k) for k in _MARKET_FIELDS}


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
        # Candidates come from market_catalog (see catalog_scan.
        # _scan_catalog_batch), not a fresh top-40-series fetch - confirmed
        # directly against real Kalshi data that volume-ranking the
        # candidate pool misses almost everything actually live right now
        # (a series can be high-volume overall with nothing airing this
        # exact hour, and vice versa). The catalog is scanned incrementally
        # in the background and may be sparse/empty right after this
        # feature is first turned on - that's an honest, self-correcting
        # transient state (see market_catalog.py), not backfilled with
        # anything fabricated.
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
        # values all across the app. its not updating either."
        #
        # Hydrated via _cached_market_fetch, same TTL-cached path the
        # pinned-watchlist and extra_tickers branches already use (2026-08-23,
        # second pass - direct report that real REST rate limiting was
        # happening, "especially position sections", after a first pass the
        # same day had only fixed the *concurrency* half of this). That
        # first pass fanned out client.get_markets(limit=100,
        # series_ticker=s) per distinct selected series via an uncapped
        # asyncio.gather, replaced with one safe batched get_markets_by_
        # tickers call - genuinely safer, but still UNCACHED, still firing
        # on every single tick regardless of whether the selected tickers
        # had changed since the last one. That's the same "re-fetch every
        # tick unconditionally" bug _cached_market_fetch's own docstring
        # already names for the other two branches, just not yet applied
        # here - this call's structural fields (title, close_time, status)
        # don't need per-6-second freshness any more than a pinned
        # ticker's do, and price still comes from the WS ticker-channel
        # overlay below regardless of source.
        selected_tickers = sorted(m["ticker"] for m in markets if m.get("ticker"))
        try:
            hydrated = await _cached_market_fetch(client, selected_tickers)
            hydrated_by_ticker = {m["ticker"]: m for m in hydrated if m.get("ticker")}
        except Exception:
            # Degrade to the original catalog rows (schedule/title info,
            # just no live price) rather than losing the whole tick's
            # watchlist to one failed fetch - same "degrade honestly,
            # never silently drop" pattern as the rest of this app.
            hydrated_by_ticker = {}
        markets = [hydrated_by_ticker.get(m["ticker"], m) for m in markets]
    else:
        # Discovery caching (2026-08-15, direct incident: "you made the
        # market watch list and whale watching grind to a halt and markets
        # aren't even appearing anymore") - a tick NEVER awaits the REST
        # discovery pipeline itself anymore, only reads whatever's already
        # in state["discovery_cache"] (possibly empty on a cold start,
        # possibly stale by up to discovery_cache._DISCOVERY_REFRESH_SEC -
        # never blocking). The actual fetch runs as an independent
        # background task (see discovery_cache._maybe_refresh_discovery_cache/
        # _refresh_discovery_cache) - price freshness still comes from the
        # WS ticker-stream overlay right before this function returns,
        # completely decoupled from how often the underlying series/market
        # *selection* gets re-run.
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
    # (main._process_stream_ticker), independent of how often this
    # function's own REST discovery re-runs. A shallow copy, not an
    # in-place mutation - the entries in discovery_cache["markets"] must
    # stay untouched by a single tick's price overlay, or the cache would
    # silently accumulate per-tick state instead of remaining a clean "what
    # was selected" snapshot. Falls back to whatever price the market
    # object already carried (its own REST-fetched value) when no WS data
    # has arrived for that ticker yet - never guessed, same "missing isn't
    # zero" idiom as the rest of this app.
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
