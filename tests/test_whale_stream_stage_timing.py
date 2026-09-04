"""_process_stream_trade's stage timers (realtime data-plane task I2).

Imports services.whale_stream.whale_stream_handlers, which imports
services.app_state - safe here because tests/conftest.py installs
tests/support/runtime_isolation.py before any test module loads, so every
eager singleton is already redirected away from the live data/*.db files."""
import asyncio
import time

import pytest

from services import app_state
from services import whale_pipeline_perf as wpp
from services.kalshi import websocket as ws_module
from services.whale_stream import whale_stream_handlers as wsh


class _StubProvider:
    name = "kalshi_trade_tape"

    def __init__(self, signals=None):
        self.calls = 0
        self._signals = signals or []
        self.on_fetch = None  # a test's hook for "the provider's work took this long"

    async def fetch_signals(self, since_ts=None, market_context=None):
        self.calls += 1
        if self.on_fetch is not None:
            self.on_fetch()
        await asyncio.sleep(0.005)
        return list(self._signals)


class _HandlerClock:
    """Stands in for whale_stream_handlers' `time` module: monotonic() is a
    value the test moves by hand; everything else (time.time() for the
    trade-tape timestamp) is the real module. Only the handler module sees
    it - the event loop keeps its own real clock, so the stub provider's
    asyncio.sleep still works."""

    def __init__(self, monotonic: float):
        self._monotonic = monotonic

    def monotonic(self) -> float:
        return self._monotonic

    def advance(self, seconds: float) -> None:
        self._monotonic += seconds

    def __getattr__(self, name):
        return getattr(time, name)


@pytest.fixture
def _stream_mode(monkeypatch):
    fresh = wpp.WhalePipelinePerf()
    monkeypatch.setattr(wpp, "perf", fresh)
    provider = _StubProvider()
    # Patched on app_state, the single source of truth every path resolves
    # through since #565 - whale_stream_handlers no longer owns a copy.
    monkeypatch.setattr(app_state, "_whale_provider", provider)
    monkeypatch.setattr(wsh, "_streaming_trade_tape_enabled", lambda: True)
    wsh.state["running"] = True
    wsh.state["trade_tape"] = []
    return fresh, provider


def _trade(trade_id="t1"):
    return {"trade_id": trade_id, "ticker": "TICK-A", "count_fp": "10.00", "yes_price_dollars": "0.50",
            "no_price_dollars": "0.50", "taker_outcome_side": "yes"}


def test_every_stream_trade_records_capture_config_provider_and_total_stages(_stream_mode):
    perf, provider = _stream_mode
    asyncio.run(wsh._process_stream_trade(_trade()))

    stages = perf.snapshot()["stages"]
    for name in ("capture", "config", "provider", "handler_total"):
        assert stages[name]["window"]["count"] == 1, name
    assert stages["provider"]["window"]["max_ms"] >= 4.0  # the stub's 5 ms sleep, minus timer slop
    assert stages["signals"]["window"]["count"] == 0  # no signal -> the decision stage never ran
    assert provider.calls == 1


def test_receive_to_handler_end_is_measured_from_the_gateway_enqueue_timestamp(_stream_mode, monkeypatch):
    """receive_to_handler_end is the handler's END clock minus the gateway's
    ENQUEUE timestamp (kalshi_websocket.MESSAGE_ENQUEUED_AT): it must include
    the queue wait that elapsed before this handler ever started (2 s here)
    AND the handler's own work (the provider's 0.5 s), so it is neither
    handler_total nor the queue wait on its own. Every clock read is
    injected: the enqueue timestamp through the contextvar exactly as
    _process_item sets it, the handler's monotonic() through _HandlerClock,
    advanced only by the stub provider. Issue #233: the previous
    `2000 <= max_ms < 2500` was a real-time bound on the handler's own
    duration and measured the CI box (2598.6 ms under load, 70 ms
    locally), not this mechanism."""
    perf, provider = _stream_mode
    enqueued_at = 1000.0
    clock = _HandlerClock(monotonic=enqueued_at + 2.0)  # the handler starts 2 s after the enqueue
    provider.on_fetch = lambda: clock.advance(0.5)  # and its own work takes 0.5 s
    monkeypatch.setattr(wsh, "time", clock)
    token = ws_module.MESSAGE_ENQUEUED_AT.set(enqueued_at)
    try:
        asyncio.run(wsh._process_stream_trade(_trade()))
    finally:
        ws_module.MESSAGE_ENQUEUED_AT.reset(token)

    stages = perf.snapshot()["stages"]
    r = stages["receive_to_handler_end"]["window"]
    assert r["count"] == 1
    assert r["max_ms"] == pytest.approx(2500.0)  # 2000 ms of queue wait + the 500 ms handler
    assert stages["handler_total"]["window"]["max_ms"] == pytest.approx(500.0)  # the handler alone
    assert stages["provider"]["window"]["max_ms"] == pytest.approx(500.0)  # where those 500 ms went


def test_receive_to_handler_end_is_skipped_when_no_enqueue_timestamp_is_known(_stream_mode):
    perf, _provider = _stream_mode
    asyncio.run(wsh._process_stream_trade(_trade()))
    assert perf.snapshot()["stages"]["receive_to_handler_end"]["window"]["count"] == 0


def test_trades_that_skip_the_provider_still_record_capture_and_total(_stream_mode, monkeypatch):
    perf, provider = _stream_mode
    wsh.state["running"] = False
    asyncio.run(wsh._process_stream_trade(_trade()))
    stages = perf.snapshot()["stages"]
    assert stages["capture"]["window"]["count"] == 1
    assert stages["handler_total"]["window"]["count"] == 1
    assert stages["config"]["window"]["count"] == 0
    assert provider.calls == 0


def test_process_stream_ticker_schedules_flush_via_tick_executor_when_told(monkeypatch):
    """Event-loop-blocking elimination Fix 1 (2026-09-01): a should_flush=True
    from series_watcher.record_book must be scheduled off the event loop
    via tick_executor.run(series_watcher.flush), never called inline on the
    ticker-channel hot path - see series_watcher.record_book's own
    docstring for why this is plausibly the highest-frequency instance of
    the blocking-flush bug class."""
    from services import series_watcher, tick_executor

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
    monkeypatch.setattr(series_watcher, "record_book", lambda *a, **k: (True, True))

    asyncio.run(wsh._process_stream_ticker({
        "ticker": "KXTEST-25", "yes_bid_dollars": "0.55",
    }))

    assert len(scheduled) == 1
    assert tick_executor_calls[0] is series_watcher.flush


def test_process_stream_ticker_schedules_snapshot_write_via_tick_executor(monkeypatch):
    """Task 4 of docs/superpowers/plans/2026-09-03-tier1-backend-hygiene.md
    (§4.5 of the architecture-audit-second-pass research): record_snapshot_
    from_ticker did a synchronous `with _connect(DB_PATH)` SQLite write per
    throttled ticker message, directly on the event loop, with no thread
    hop at all - the exact bug class PR #414 already fixed for 4 sibling
    functions. Unlike those, this function has no accumulation buffer, so
    the fix is at the call site (schedule the whole call via tick_executor,
    not modify record_snapshot_from_ticker's own signature)."""
    from services import market_history, series_watcher, tick_executor

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
    # Isolate this test from series_watcher's own real buffer/flush path -
    # this test's only concern is record_snapshot_from_ticker's scheduling;
    # see test_process_stream_ticker_schedules_flush_via_tick_executor_when_
    # told above for that path's own coverage.
    monkeypatch.setattr(series_watcher, "record_book", lambda *a, **k: (True, False))
    snapshot_calls = []
    monkeypatch.setattr(market_history, "record_snapshot_from_ticker",
                         lambda *a, **k: snapshot_calls.append((a, k)) or True)

    # state["running"]=False keeps this test focused on the snapshot-
    # scheduling call site only, skipping check_exits/check_pending_fills/
    # position_netting.review (unrelated to this fix, and not mocked here).
    monkeypatch.setitem(wsh.state, "running", False)
    monkeypatch.setitem(wsh.state, "markets", [{
        "ticker": "KXTEST-25", "volume_24h_fp": "12.0",
        "close_time": "2026-09-03T00:00:00Z",
    }])

    asyncio.run(wsh._process_stream_ticker({
        "ticker": "KXTEST-25", "yes_bid_dollars": "0.55", "yes_ask_dollars": "0.57",
    }))

    assert len(scheduled) == 1
    assert tick_executor_calls[0] is not None
    assert len(snapshot_calls) == 1
    call_args, call_kwargs = snapshot_calls[0]
    assert call_args[0] == "KXTEST-25"
    assert call_args[1] == pytest.approx(0.55)  # state["latest_prices"][ticker], captured as a plain value
    assert call_kwargs["volume_24h"] == pytest.approx(12.0)
    assert call_kwargs["close_time"] == "2026-09-03T00:00:00Z"
