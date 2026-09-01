"""
The index-stream callback layer - CF Benchmarks/Pyth index ticks, feeding
settlement-edge observation capture. Extracted 2026-08-22 as part of
main.py's modularization pass. Split from whale_stream_handlers.py
deliberately - the index stream already gets a *second websocket
connection* for physical isolation from the trade firehose (see
services/kalshi_trade_ws.py's own comment on why), so giving it a second
*module* mirrors that same boundary. Wired once in main.py's lifespan()
into index_stream.run(...).
"""
import asyncio
import time

from services import index_feed, settlement_edge, settlement_edge_entry, tick_executor
from services.app_state import broker, risk, state
from services.config.config_store import config_store
from services.kalshi.public import KalshiPublicGateway
from services.whale_stream import decision_bridge

# ticker -> settlement spec (services/index_feed.settlement_spec). A market's
# rules/strike/close are immutable once listed, so this is cached rather than
# re-fetched: the 15-minute series rotates its ticker every quarter hour, so
# this is a handful of fetches an hour, not one per index tick.
_settlement_spec_cache: dict[str, dict] = {}


async def _noop_stream_trade(_trade: dict) -> None:
    """index_stream subscribes to no trade/ticker channels at all (its
    index_ids are the only thing it asks for), so these can never fire -
    they exist because run()'s signature takes them positionally."""
    return


async def _noop_stream_ticker(_ticker_msg: dict) -> None:
    return


async def _handle_index_stream_status(status: dict) -> None:
    state["index_stream_status"] = {**status, "updated_at": time.time()}


async def _process_stream_index(msg_type: str, msg: dict) -> None:
    """CF Benchmarks / Pyth index ticks (services/index_feed/).

    For the crypto series this is the settlement quantity itself, not a
    proxy for it - KXBTC15M settles on "the simple average of the sixty
    seconds of CF Benchmarks' BRTI before <close>", and
    cfbenchmarks_value's last_60s_windowed_average_15min IS that average,
    accumulating one observation per second. ~1 message/sec/index, buffered
    the same way trade capture is. The buffer-full flush trigger is
    scheduled via tick_executor.run() + asyncio.create_task (never awaited
    directly here), so this never blocks the event loop - confirmed as a
    real, previously-live bug this exact docstring's old wording claimed
    was already true (event-loop-blocking elimination Fix 1, 2026-09-01).
    Deliberately does NOT trigger check_exits or any trading action yet -
    capture and projection first, acting on it is a separate, deliberate
    step."""
    if msg_type == "cfbenchmarks_value":
        _, should_flush = index_feed.record_cfbenchmarks(msg)
        if should_flush:
            asyncio.create_task(tick_executor.run(index_feed.flush))
        await _record_settlement_observations(msg.get("index_id"))
    elif msg_type == "pyth_value":
        _, should_flush = index_feed.record_pyth(msg)
        if should_flush:
            asyncio.create_task(tick_executor.run(index_feed.flush))


async def _spec_for(ticker: str) -> dict:
    spec = _settlement_spec_cache.get(ticker)
    if spec is None:
        cfg = config_store.get()
        client = KalshiPublicGateway(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])
        try:
            spec = index_feed.settlement_spec(await client.get_market(ticker))
        except Exception:
            # Don't cache a transport failure as "unsupported" - that would
            # permanently blind this market on one bad request.
            return {"supported": False, "reason": "market fetch failed"}
        finally:
            # Real live leak (2026-08-23): this client was never closed on
            # any path - a fresh KalshiPublicGateway (and its SDK-managed aiohttp
            # session, see services/kalshi_client.py's own close() docstring)
            # leaked on every cache miss. This module's own docstring above
            # already knew the cadence ("the 15-minute series rotates its
            # ticker every quarter hour, so this is a handful of fetches an
            # hour") - that's exactly the ~15-minute "Unclosed connector"
            # pattern confirmed live in ddev logs across 5+ hours of
            # restart-free steady-state operation, one per newly-rotated-in
            # ticker across every crypto index series (BTC/ETH/SOL/DOGE/
            # HYPE each running their own independent 15-minute rotation).
            await client.close()
        _settlement_spec_cache[ticker] = spec
        if len(_settlement_spec_cache) > 500:
            _settlement_spec_cache.clear()
    return spec


async def _resolve_settlement_windows(client: KalshiPublicGateway) -> None:
    """Fill in outcomes for observed settlement windows, driven by
    settlement_edge's own pending list rather than the discovery watchlist.

    Confirmed necessary, not assumed: a KXBTC15M market was found
    `finalized` with `result='yes'` six minutes after close while its 59
    observations sat unresolved, because the 15-minute series rotates its
    ticker every quarter hour and the market had already left the watchlist
    by the time `result` populated. The loop's existing outcome pass is
    kept as well - it costs nothing and catches the markets that are still
    watched - but it cannot be the only path.

    One batched call (get_markets_by_tickers, 50/request) against a list
    that is normally empty and at most a handful long."""
    tickers = settlement_edge.unresolved_tickers()
    if not tickers:
        return
    try:
        markets = await client.get_markets_by_tickers(tickers)
    except Exception:
        return  # transient - the same rows are still pending next tick
    for ticker, market in markets.items():
        result = (market.get("result") or "").strip().lower()
        if result in ("yes", "no"):
            settlement_edge.resolve_window(ticker, result == "yes")


async def _record_settlement_observations(index_id: str | None) -> None:
    """While a settlement window is open, record the projection and the
    market's own price side by side for every watched market settling on
    this index (services/settlement_edge.py), and - if
    settlement_edge_entry.enabled - act on it (services/
    settlement_edge_entry.py).

    This is the measurement that decides whether the index feed is an edge
    or merely interesting: both forecasts of the same binary event, captured
    at the same instant, scored against the realised outcome later.
    settlement_edge.py itself never trades - see its own docstring; acting
    on a confirmed edge is a deliberate, separate, opt-in step, off by
    default."""
    if not index_id:
        return
    entry = index_feed.latest(index_id)
    if not entry or not entry.get("q15_window_size"):
        return  # not in a settlement window; nothing to compare
    cfg = config_store.get()
    for market in (state.get("markets") or []):
        ticker = market.get("ticker")
        if not ticker:
            continue
        spec = await _spec_for(ticker)
        if not spec.get("supported") or spec.get("index_id") != index_id:
            continue
        # Same index is NOT enough: the q15 window opens before every
        # quarter-hour, so an average accumulating toward 06:00 would
        # otherwise be recorded against a market settling at 17:00. See
        # index_feed.window_matches_close - this was a real bug, found by
        # inspecting the captured rows rather than trusting the wiring.
        if not index_feed.window_matches_close(entry, settlement_edge.close_ts(spec)):
            continue
        projection = index_feed.settlement_projection(index_id, spec["strike"])
        market_price = state["latest_prices"].get(ticker)
        settlement_edge.record_observation(ticker, spec, projection, market_price)
        decision = settlement_edge_entry.evaluate_entry(
            ticker, spec, projection, market_price, cfg, broker, risk,
        )
        if decision is not None:
            await decision_bridge.handle_settlement_edge_entry(decision, time.time())
