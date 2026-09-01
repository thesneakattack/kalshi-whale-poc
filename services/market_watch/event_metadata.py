"""
Event title/metadata caching and the crypto/commodity/weather live-data
feed (get_event_live_data - documented, confirmed NOT to serve Sports; see
_fetch_live_status in live_status.py for the milestone-keyed sports path).
Split out of market_watch.py (2026-08-22 modularization Phase 9/9).
"""
import asyncio
import time

from services import game_state, tick_executor
from services.app_state import state
from services.kalshi.public import KalshiPublicGateway
from services import http_client
from services.market_lookup import _sport_for_event


@http_client.classify("background_catalog")
async def _fetch_event_titles(client: KalshiPublicGateway, markets: list[dict]) -> dict:
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
# fix) - same fix, same root cause as catalog_scan._MILESTONE_REPOLL_SEC
# above: this called get_event_live_data() for every unique event on the
# watchlist, every tick, forever, unconditionally. Real live game-state data
# can change fast during an actual live event, so this stays much shorter
# than live_status._LIVE_STATUS_REPOLL_SEC's 5 minutes, but per-tick
# (~every 15s) was never the right cadence either.

_EVENT_LIVE_DATA_EXCLUDED_CATEGORIES = {"Sports"}  # 2026-08-16 API-doc audit
# finding B2 (docs/kalshi/get-event-live-data.md, docs/next-steps-2026-08-15-
# pt3.md): this endpoint is event-ticker-keyed and documented/confirmed to
# serve "crypto price charts, commodity price timeseries, weather
# observations" - live-verified against 3 real crypto tickers (KXBTC15M-*),
# which returned real BTC candlestick data. Sports is the one category
# confirmed NOT served here: every real sports ticker on the watchlist
# 404s from this endpoint 100% of the time - not a bug, structurally the
# wrong data source (the real source for sports live state is the
# milestone-keyed get_live_data(s), which live_status._fetch_live_status and
# catalog_scan.propagate_milestone_winners already call - see
# state["live_game_state"]). Corrects an earlier, less careful same-day
# comment on this constant that guessed crypto didn't work here either - it
# does; only Sports is excluded, and only because it's actually confirmed
# wasteful, not guessed at. Any other category without live confirmation
# either way is deliberately left in rather than excluded on a guess, same
# "don't fabricate" idiom _fetch_live_status's own schedule-fallback
# already follows.


@http_client.classify("background_live_status")
async def _fetch_event_live_data(client: KalshiPublicGateway, markets: list[dict]) -> dict:
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
                    # KalshiStreamGateway.normalize_trade - a field
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
            # live_status._fetch_live_status only fires for events Kalshi
            # tracks a milestone for, and was measured empty while THIS
            # held six live entries. For crypto events the payload carries
            # OHLC candlesticks and an underlying price timeseries; for
            # games it carries score/period/clock. Both are fetched every
            # tick already and both were living only in memory. Rate-limited
            # and deduplicated inside game_state.record.
            if live_data and (live_data.get("details") or {}):
                _, should_flush = game_state.record(
                    et, live_data["details"],
                    sport=_sport_for_event(state["event_titles"].get(et) or {}),
                    event_type=live_data.get("type"),
                )
                if should_flush:
                    asyncio.create_task(tick_executor.run(game_state.flush))
    return {et: cache[et]["data"] for et in event_tickers if cache.get(et, {}).get("data") is not None}
