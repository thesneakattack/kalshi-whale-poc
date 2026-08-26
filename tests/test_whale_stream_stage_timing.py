"""_process_stream_trade's stage timers (realtime data-plane task I2).

Imports services.whale_stream.whale_stream_handlers, which imports
services.app_state - safe here because tests/conftest.py installs
tests/support/runtime_isolation.py before any test module loads, so every
eager singleton is already redirected away from the live data/*.db files."""
import asyncio

import pytest

from services import whale_pipeline_perf as wpp
from services.kalshi import websocket as ws_module
from services.whale_stream import whale_stream_handlers as wsh


class _StubProvider:
    name = "kalshi_trade_tape"

    def __init__(self, signals=None):
        self.calls = 0
        self._signals = signals or []

    async def fetch_signals(self, since_ts=None, market_context=None):
        self.calls += 1
        await asyncio.sleep(0.005)
        return list(self._signals)


@pytest.fixture
def _stream_mode(monkeypatch):
    fresh = wpp.WhalePipelinePerf()
    monkeypatch.setattr(wpp, "perf", fresh)
    provider = _StubProvider()
    monkeypatch.setattr(wsh, "whale_provider", provider)
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


def test_receive_to_handler_end_is_measured_from_the_gateway_enqueue_timestamp(_stream_mode):
    perf, _provider = _stream_mode
    token = ws_module.MESSAGE_ENQUEUED_AT.set(ws_module.time.monotonic() - 2.0)
    try:
        asyncio.run(wsh._process_stream_trade(_trade()))
    finally:
        ws_module.MESSAGE_ENQUEUED_AT.reset(token)

    r = perf.snapshot()["stages"]["receive_to_handler_end"]["window"]
    assert r["count"] == 1
    assert 2000.0 <= r["max_ms"] < 2500.0  # 2 s of simulated queue wait + the handler itself


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
