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


def _resolve_windows(settled: list[tuple[str, bool]]) -> int:
    """Runs settlement_edge.resolve_window() for every ticker this tick
    found settled, on the tick executor's worker pool rather than the
    calling event loop (event-loop-blocking fix, issue #605 - a partial
    fix: #605's other two contributors, candidate_log.gate_summary() and a
    jsonable_encoder recursion stall filed as #634, are tracked and fixed
    separately).

    THE BUG: resolve_window() is a single, indexed SQLite UPDATE against
    window_observations (WHERE ticker = ? AND settled_yes IS NULL, uses
    idx_se_window - confirmed cheap via EXPLAIN QUERY PLAN, not a table
    scan). settlement_edge.flush()'s executemany INSERT writes the SAME
    table and already runs on a tick_executor worker thread (this module's
    own _record_settlement_observations below, and main.py's
    _flush_secondary_capture_stores_async). SQLite is single-writer, so
    before this fix - when this function's own for loop called
    resolve_window() directly, synchronously, once per settled ticker (up
    to unresolved_tickers()'s own limit=40) - a resolve_window() call
    contending with an in-flight flush() could genuinely block waiting on
    the file lock for up to busy_timeout (5000ms, services/db.py connect()'s
    default), directly on the event loop, with resolve_window()'s own
    `except sqlite3.Error: return 0` swallowing that outcome with no
    exception ever raised or logged - matching #605's "zero exceptions"
    symptom exactly. A live loop_watchdog stack capture caught this exact
    call chain blocked (main.py trading_loop -> this module's
    _resolve_settlement_windows -> settlement_edge.resolve_window ->
    services/db.py connect()); more than one contended ticker in the same
    pass stacks sequential waits into the observed ~9-10s stalls.

    WHY tick_executor.run() AND NOT asyncio.to_thread(): this is the
    established, already-load-tested precedent for this exact call, not a
    new choice - services/settlement_resolver.py's _resolve_one_sync
    already routes this same settlement_edge.resolve_window() call (among
    four other resolvers) through tick_executor.run() for the
    settled-lifecycle-event path. bench/bench_tick_executor_contention.py's
    "settlement" workload already models up to 50 sequential
    tick-executor-routed resolver calls per pass (heavier than a bare
    resolve_window() call, since _resolve_one_sync does five writes), and
    its calibrated results (bench/out/results_calibrated.log) show
    single-digit-to-double-digit-ms p95 queueing delay for that workload
    under realistic load (S1/S2/S3), with pool utilization well under 1.0;
    the #579/#580 "shared pool starves decision-critical work" hypothesis
    was investigated directly and falsified (docs/next-action.md - the real
    fix was #601). Sharing tick_executor also keeps this module's
    worker-thread footprint at the existing, already-measured 2 workers
    rather than adding load to asyncio.to_thread's separately-sized default
    executor. Note, verified directly rather than assumed: sharing the pool
    does NOT itself force mutual exclusion between flush() and
    resolve_window() - ThreadPoolExecutor(max_workers=2) starts two
    submitted callables within a fraction of a millisecond of each other
    (confirmed with a local probe), so both can still run as genuinely
    concurrent OS threads when both workers are free. What sharing the pool
    does is bound their total concurrent SQLite writers to at most 2 (down
    from "one on a tick_executor worker plus one unbounded on the event
    loop, today") and move both off the event loop - the actual mechanism
    that fixes #605, not an incidental serialization side-effect.

    Batched as ONE tick_executor.run() call per tick, not one submission
    per ticker: matches this file's own _record_settlement_observations and
    main.py's _flush_trade_capture_async / _flush_secondary_capture_stores_
    async / _resolve_and_record_settlements_async, all of which offload
    their tick's whole block of synchronous work as a single pool
    submission. Per-ticker failure isolation is unaffected by batching:
    resolve_window() already catches sqlite3.Error internally and returns 0
    rather than raising, so one contended/failing ticker already cannot
    take down the others in this same batched call - unchanged from
    before."""
    resolved_rows = 0
    for ticker, settled_yes in settled:
        resolved_rows += settlement_edge.resolve_window(ticker, settled_yes)
    return resolved_rows


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
    that is normally empty and at most a handful long. The per-ticker
    resolution writes themselves are scheduled via tick_executor.run() (see
    _resolve_windows' own docstring, issue #605) rather than run directly
    here - this coroutine's own for loop used to call
    settlement_edge.resolve_window() synchronously, which is what blocked
    the event loop."""
    tickers = settlement_edge.unresolved_tickers()
    if not tickers:
        return
    try:
        markets = await client.get_markets_by_tickers(tickers)
    except Exception:
        return  # transient - the same rows are still pending next tick
    settled = []
    for ticker, market in markets.items():
        result = (market.get("result") or "").strip().lower()
        if result in ("yes", "no"):
            settled.append((ticker, result == "yes"))
    if settled:
        await tick_executor.run(lambda: _resolve_windows(settled))


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
        _, should_flush = settlement_edge.record_observation(ticker, spec, projection, market_price)
        if should_flush:
            asyncio.create_task(tick_executor.run(settlement_edge.flush))
        decision = settlement_edge_entry.evaluate_entry(
            ticker, spec, projection, market_price, cfg, broker, risk,
        )
        if decision is not None:
            await decision_bridge.handle_settlement_edge_entry(decision, time.time())
