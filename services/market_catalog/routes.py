"""
Market catalog + per-market drill-down/search routes - moved out of
main.py (2026-08-22 modularization pass, Phase 6/9). These routes (market
orderbook/candlesticks/trades/detail, catalog scan-status, and the
series-based search endpoint) were never assigned to any of the 6 concerns
the prior 7-phase main.py modularization split out (whale stream/market
watch/position/position-management/history/analytics) - they depend on
market_catalog/title_cache/market_watch's series cache, the natural home.
"""
import time

from fastapi import APIRouter, HTTPException

from services import title_cache
from services.app_state import bump_generation, state
from services.config_store import config_store
from services.kalshi_client import KalshiClient
from services.market_catalog import market_catalog
from services.market_watch.market_watch import (
    _fetch_live_status, _get_series_cache, _LIVE_STATUS_LOOKAHEAD_SEC, _LIVE_STATUS_LOOKBACK_SEC, _slim_market,
)

router = APIRouter()


@router.get("/api/markets/{ticker}/orderbook")
async def get_market_orderbook(ticker: str):
    # Per-market drill-down (ROADMAP.md Phase 0.5) - on-demand, not part of
    # the poll loop, so it gets its own short-lived client rather than
    # waiting for the next tick. Matches trading_loop()'s own construct/use/
    # close pattern (see its `finally: await client.close()`), just fired
    # from a request instead of a timer.
    cfg = config_store.get()
    client = KalshiClient(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])
    try:
        return await client.get_orderbook(ticker)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        await client.close()


@router.get("/api/markets/{ticker}/candlesticks")
async def get_candlesticks(ticker: str, event_ticker: str):
    # Price history for the per-market drill-down (ROADMAP.md Phase 0.5).
    # get_market_candlesticks requires series_ticker, which market objects
    # don't carry directly (only event_ticker) - verified via introspection,
    # not guessed from the ticker string, since a wrong value here is a hard
    # API error rather than a silently-wrong display. event_ticker comes
    # from the caller (the frontend already has it on state.markets) so
    # this can go straight to the one get_event() lookup it needs rather
    # than an extra get_market() call first to discover it.
    #
    # Fixed window: last 7 days, hourly candles - dense enough for a
    # meaningful chart, short enough to stay a single fast request. Not
    # user-configurable yet.
    cfg = config_store.get()
    client = KalshiClient(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])
    try:
        event = await client.get_event(event_ticker)
        series_ticker = (event.get("event") or {}).get("series_ticker")
        if not series_ticker:
            raise HTTPException(status_code=502, detail="Could not resolve series_ticker for this event")
        end_ts = int(time.time())
        start_ts = end_ts - 7 * 24 * 3600
        return await client.get_candlesticks(series_ticker, ticker, start_ts, end_ts, period_interval=60)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        await client.close()


@router.get("/api/markets/{ticker}/trades")
async def get_market_trades(ticker: str):
    # Recent trades for one market, in the drill-down (ROADMAP.md Phase
    # 0.5) - distinct from the full-exchange trade tape (a separate,
    # not-yet-built Terminal/Whale-Watch-level feed across every watched
    # market). No series_ticker complication here, unlike candlesticks -
    # get_trades takes a plain ticker filter.
    cfg = config_store.get()
    client = KalshiClient(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])
    try:
        return await client.get_trades(ticker=ticker, limit=15)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        await client.close()


def _dollars(v) -> float | None:
    return float(v) if v not in (None, "") else None


def _slim_detail_market(m: dict) -> dict:
    return {
        "ticker": m.get("ticker"),
        "title": m.get("title"),
        "subtitle": m.get("subtitle"),
        "yes_sub_title": m.get("yes_sub_title"),
        "no_sub_title": m.get("no_sub_title"),
        "status": m.get("status"),
        "yes_bid": _dollars(m.get("yes_bid_dollars")),
        "yes_ask": _dollars(m.get("yes_ask_dollars")),
        "no_bid": _dollars(m.get("no_bid_dollars")),
        "no_ask": _dollars(m.get("no_ask_dollars")),
        "last_price": _dollars(m.get("last_price_dollars")),
        "previous_price": _dollars(m.get("previous_price_dollars")),
        "volume": _dollars(m.get("volume_fp")),
        "volume_24h": _dollars(m.get("volume_24h_fp")),
        "open_interest": _dollars(m.get("open_interest_fp")),
        "liquidity": _dollars(m.get("liquidity_dollars")),
        "close_time": m.get("close_time"),
        "open_time": m.get("open_time"),
    }


@router.get("/api/markets/{ticker}/detail")
async def get_market_detail(ticker: str):
    # Everything one whole-market "landing page" view needs in one call
    # (ROADMAP.md, Open Positions -> full market detail): the market's own
    # full object plus, when it belongs to a multi-outcome event, every
    # sibling market in that event (get_event's own `markets` list already
    # includes them with live prices - no per-sibling get_market() round
    # trip needed) so the modal can show the same kind of outcome table
    # Kalshi's own market page shows, not just this one ticker in isolation.
    cfg = config_store.get()
    client = KalshiClient(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])
    try:
        try:
            market = await client.get_market(ticker)
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))

        event_ticker = market.get("event_ticker")
        event_info, siblings = None, []
        if event_ticker:
            try:
                ev = await client.get_event(event_ticker)
                event_info = ev.get("event")
                siblings = [m for m in (ev.get("markets") or []) if m.get("ticker") != ticker]
            except Exception:
                pass  # event context is a bonus, not core to the ticker's own detail
    finally:
        await client.close()

    detail = _slim_detail_market(market)
    detail["rules_primary"] = market.get("rules_primary")
    detail["rules_secondary"] = market.get("rules_secondary")
    detail["event_ticker"] = event_ticker
    detail["event"] = {
        "title": event_info.get("title"),
        "sub_title": event_info.get("sub_title"),
        "category": event_info.get("category"),
    } if event_info else None
    detail["siblings"] = sorted(
        (_slim_detail_market(m) for m in siblings),
        key=lambda m: m["volume_24h"] or 0, reverse=True,
    )
    return detail


@router.get("/api/market-catalog/status")
async def get_market_catalog_status():
    # Honest progress reporting (same idiom as /api/advisory/status) for
    # market_catalog.py's incremental background scan - lets the Config tab
    # or a curl check say "X series scanned, Y near-term markets known"
    # instead of the catalog being an opaque, silently-filling-in cache.
    cfg = config_store.get()
    progress = market_catalog.scan_progress()
    progress["enabled"] = bool(cfg["kalshi"].get("live_markets_only"))
    return progress


@router.get("/api/markets/search")
async def search_markets(q: str = "", min_volume: float = 0, category: str = "", limit: int = 50, live_only: bool = False):
    # On-demand market search/browse (ROADMAP.md Phase 0.5) - distinct from
    # the automatic watchlist selection (_fetch_markets), which stays
    # volume-filtered by config default (kalshi.min_volume_24h). Defaults to
    # min_volume=0 - full catalog access, dormant markets included - so a
    # market being excluded from the automatic watchlist never means it's
    # unreachable, only that it's not the default view.
    #
    # Series-based, same as the automatic watchlist and for the same
    # reason: an early version of this endpoint browsed individual markets
    # directly (even paginating 5000+ of them for a text query) and that
    # turned out fundamentally unreliable - confirmed directly, repeatedly,
    # with real numbers - Kalshi's combo/MVE markets are generated in such
    # bulk that a flat browse of even tens of thousands of markets can
    # still contain zero real matches. Text-matching against ~9,400
    # series (title/tags/category), a much smaller and cleanly-labeled
    # set, then querying only the matching series directly, is what
    # actually works.
    cfg = config_store.get()
    client = KalshiClient(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])
    try:
        all_series = await _get_series_cache(client)  # already sorted by volume_fp desc
        q_lower = q.strip().lower()
        category_lower = category.strip().lower()
        candidates = all_series
        if q_lower:
            candidates = [
                s for s in candidates
                if q_lower in (s.get("title") or "").lower()
                or q_lower in " ".join(s.get("tags") or []).lower()
                or q_lower in (s.get("category") or "").lower()
                or q_lower in (s.get("ticker") or "").lower()
            ]
        if category_lower:
            candidates = [s for s in candidates if (s.get("category") or "").lower() == category_lower]

        # Caps how many series to fan out to (a network request each), not
        # how many markets come back - candidates is already volume-sorted.
        candidate_tickers = [s["ticker"] for s in candidates[:30]]
        if live_only:
            # Prefer market_catalog (see _scan_catalog_batch) if it already
            # has near-term data for the matched series - same reasoning as
            # _fetch_markets' discovery path (volume-ranking a fresh 30-
            # series fetch misses almost everything actually live right
            # now). Falls back to a fresh live fetch when the catalog has
            # nothing for these specific series yet (e.g. the background
            # scan hasn't reached them, or kalshi.live_markets_only has
            # never been turned on) - search must still work even before
            # the catalog's built up, just less completely.
            now = time.time()
            catalog_candidates = market_catalog.candidates_in_window(
                now, lookahead_sec=_LIVE_STATUS_LOOKAHEAD_SEC, lookback_sec=_LIVE_STATUS_LOOKBACK_SEC,
                min_volume=min_volume,
            )
            if q_lower or category_lower:
                # A real search/category narrowing is active - scope the
                # catalog to the (untruncated) matched series, not just the
                # top 30 by volume that candidate_tickers caps at below,
                # which would otherwise throw away most of the catalog's
                # own breadth advantage for a search that matched more than
                # 30 series.
                matched_series = {s["ticker"] for s in candidates}
                catalog_candidates = [m for m in catalog_candidates if m.get("series_ticker") in matched_series]
            market_candidates = catalog_candidates or await client.get_candidate_markets(
                min_volume=min_volume, series_tickers=candidate_tickers,
            )
            live_status = await _fetch_live_status(client, market_candidates)
            live_candidates = [
                m for m in market_candidates
                if live_status.get(m.get("event_ticker")) == "live"
            ]
            markets = KalshiClient.round_robin_select(
                live_candidates, limit, max_children_per_parent=cfg["kalshi"].get("max_children_per_parent"),
            )
        else:
            markets = await client.get_top_volume_markets(
                limit, min_volume=min_volume, series_tickers=candidate_tickers,
                max_children_per_parent=cfg["kalshi"].get("max_children_per_parent"),
            )
        results = markets
        # Opportunistically cache titles/events for whatever this search
        # touched, same shape _fetch_markets already populates - so a result
        # added to the watchlist afterward already has a label, no gap.
        searched_titles = {
            m["ticker"]: {
                **title_cache.market_title_fields(m), "event_ticker": m.get("event_ticker"),
                "legs": m.get("mve_selected_legs") or None,
            }
            for m in results if m.get("ticker")
        }
        state["market_titles"].update(searched_titles)
        title_cache.save_market_titles(searched_titles)
        bump_generation()  # market_titles changed - invalidate the cached /api/state body, see _build_state_body
        return {
            "markets": [_slim_market(m) for m in results],
            "market_titles": {m["ticker"]: state["market_titles"][m["ticker"]] for m in results if m.get("ticker")},
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        await client.close()
