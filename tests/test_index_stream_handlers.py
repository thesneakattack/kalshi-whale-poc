"""
services/whale_stream/index_stream_handlers.py's _spec_for - the per-ticker
KalshiPublicGateway it creates on a cache miss must always be closed.

Real live incident (2026-08-23): ddev logs showed "Unclosed connector"/
"Unclosed client session" warnings firing on a clean ~15-minute cadence
across 5+ hours of continuous, restart-free operation - not a dev-reload
artifact, a genuine steady-state leak. Root-caused to this exact function:
_record_settlement_observations (this same module) runs once per index tick
(~1/sec) and calls _spec_for for every market in state["markets"]; almost
all of those are cache hits (no client created at all), but a brand-new
15-minute-series ticker rotating in - which this file's own docstring
already knew happens "every quarter hour", independently for each of
BTC/ETH/SOL/DOGE/HYPE - is a cache miss, and the old code never called
client.close() on any path (success, exception, or otherwise). This file
existed with zero test coverage before this incident, which is exactly how
a resource leak with this specific a cadence went unnoticed.
"""
import asyncio

import services.whale_stream.index_stream_handlers as ish


async def _async_return(value):
    """Turns a plain value into an awaitable, for monkeypatching an async
    function (e.g. _spec_for) with a plain lambda."""
    return value


class _FakeClient:
    instances: list["_FakeClient"] = []

    def __init__(self, base_url, timeout):
        self.base_url = base_url
        self.timeout = timeout
        self.closed = False
        _FakeClient.instances.append(self)

    async def get_market(self, ticker):
        return {"ticker": ticker}  # no floor_strike - settlement_spec marks it unsupported, fine here

    async def close(self):
        self.closed = True


class _BoomClient(_FakeClient):
    async def get_market(self, ticker):
        raise RuntimeError("network blip")


def _fake_cfg():
    return {"kalshi": {"base_url": "https://example.invalid", "request_timeout_sec": 10}}


def test_spec_for_closes_its_client_on_a_cache_miss(monkeypatch):
    ish._settlement_spec_cache.clear()
    _FakeClient.instances = []
    monkeypatch.setattr(ish, "KalshiPublicGateway", _FakeClient)
    monkeypatch.setattr(ish.config_store, "get", _fake_cfg)

    spec = asyncio.run(ish._spec_for("TICK-A"))

    assert spec.get("supported") is False
    assert len(_FakeClient.instances) == 1
    assert _FakeClient.instances[0].closed is True
    assert ish._settlement_spec_cache["TICK-A"] == spec


def test_spec_for_still_closes_its_client_when_get_market_raises(monkeypatch):
    ish._settlement_spec_cache.clear()
    _FakeClient.instances = []
    monkeypatch.setattr(ish, "KalshiPublicGateway", _BoomClient)
    monkeypatch.setattr(ish.config_store, "get", _fake_cfg)

    spec = asyncio.run(ish._spec_for("TICK-B"))

    assert spec == {"supported": False, "reason": "market fetch failed"}
    assert len(_FakeClient.instances) == 1
    assert _FakeClient.instances[0].closed is True
    # A transport failure must not get cached as "permanently unsupported" -
    # the next call for this ticker should retry, not stay blind forever.
    assert "TICK-B" not in ish._settlement_spec_cache


def test_spec_for_skips_creating_a_client_entirely_on_a_cache_hit(monkeypatch):
    ish._settlement_spec_cache.clear()
    ish._settlement_spec_cache["TICK-C"] = {"supported": True, "ticker": "TICK-C"}
    _FakeClient.instances = []
    monkeypatch.setattr(ish, "KalshiPublicGateway", _FakeClient)

    spec = asyncio.run(ish._spec_for("TICK-C"))

    assert spec == {"supported": True, "ticker": "TICK-C"}
    assert _FakeClient.instances == []  # cache hit - no client ever constructed


def test_process_stream_index_schedules_flush_via_tick_executor_when_told(monkeypatch):
    """Event-loop-blocking fix 1 (2026-09-01): record_cfbenchmarks/record_pyth
    used to call flush() inline on the event loop once their buffer hit
    _FLUSH_BATCH - real synchronous disk I/O blocking the whole loop
    (confirmed live: a 13-minute app-wide stall). Now they only report
    should_flush; _process_stream_index must schedule the actual flush via
    tick_executor.run() wrapped in asyncio.create_task, never await it
    directly (that would just reintroduce the same blocking wait inline)."""
    from services import index_feed, tick_executor

    scheduled = []

    def fake_record_cfbenchmarks(msg, now=None):
        return True, True  # accepted, should_flush=True

    monkeypatch.setattr(index_feed, "record_cfbenchmarks", fake_record_cfbenchmarks)

    real_create_task = asyncio.create_task

    def spy_create_task(coro):
        scheduled.append(coro)
        return real_create_task(coro)

    monkeypatch.setattr(asyncio, "create_task", spy_create_task)

    tick_executor_calls = []

    async def fake_tick_executor_run(fn):
        tick_executor_calls.append(fn)
        return fn()

    monkeypatch.setattr(tick_executor, "run", fake_tick_executor_run)

    async def _drive():
        await ish._process_stream_index("cfbenchmarks_value", {"index_id": "KXBTC"})
        # Let the scheduled task actually run before the loop closes.
        if scheduled:
            await scheduled[0]

    asyncio.run(_drive())

    assert len(scheduled) == 1
    assert len(tick_executor_calls) == 1
    assert tick_executor_calls[0] is index_feed.flush


def test_record_settlement_observations_schedules_flush_via_tick_executor_when_told(monkeypatch):
    """Event-loop-blocking fix 2 (2026-09-01): settlement_edge.record_observation
    used to call flush() inline on the event loop once its buffer hit
    _FLUSH_BATCH - real synchronous disk I/O with no await point, blocking
    the whole loop for the write's duration (confirmed live: a 13-minute
    app-wide stall, unrelated in-memory-only endpoints hung too). Now it
    only reports should_flush; _record_settlement_observations must schedule
    the actual flush via tick_executor.run() wrapped in asyncio.create_task,
    never await it directly (that would just reintroduce the same blocking
    wait inline)."""
    from services import index_feed, settlement_edge, tick_executor
    from services.app_state import state

    monkeypatch.setattr(index_feed, "latest", lambda index_id: {"q15_window_size": 60})
    monkeypatch.setattr(index_feed, "window_matches_close", lambda entry, close_ts: True)
    monkeypatch.setattr(index_feed, "settlement_projection",
                        lambda index_id, strike: {"status": "accumulating"})
    monkeypatch.setattr(
        ish, "_spec_for",
        lambda ticker: _async_return({"supported": True, "index_id": "KXBTC", "strike": 50000.0}),
    )

    monkeypatch.setitem(state, "markets", [{"ticker": "TICK-A"}])
    monkeypatch.setitem(state, "latest_prices", {"TICK-A": 0.55})

    scheduled = []
    real_create_task = asyncio.create_task

    def spy_create_task(coro):
        scheduled.append(coro)
        return real_create_task(coro)

    monkeypatch.setattr(asyncio, "create_task", spy_create_task)

    tick_executor_calls = []

    async def fake_tick_executor_run(fn):
        tick_executor_calls.append(fn)
        return fn()

    monkeypatch.setattr(tick_executor, "run", fake_tick_executor_run)
    monkeypatch.setattr(settlement_edge, "record_observation", lambda *a, **k: (True, True))

    async def _drive():
        await ish._record_settlement_observations("KXBTC")
        # Let the scheduled task actually run before the loop closes.
        if scheduled:
            await scheduled[0]

    asyncio.run(_drive())

    assert len(scheduled) == 1
    assert tick_executor_calls[0] is settlement_edge.flush
