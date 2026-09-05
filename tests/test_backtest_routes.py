"""services/backtest/routes.py - offload wiring for GET /api/backtest/entry-threshold.

Issue #585 (adversarial-review finding F3 of PR #581/issue #410): this
route is `async def` but called the SYNC signal_log.resolved_signals_with_
factors() inline, no thread offload - measured live at ~5.4s blocking the
event loop on the same 30s History-tab batch #581's own routes serve.
Routed through resolved_signals_with_factors_async() instead, matching the
pattern services/whale_calibration/routes.py already uses for the sibling
calibration-report route. entry_threshold_sweep() itself is pure Python
over an already-small, already-fetched list (unlike confidence_calibration's
per-factor bucket pass) so it stays a direct call, not asyncio.to_thread -
only the fetch was the event-loop-blocking half here.
"""
import asyncio

import pytest

from services.backtest import routes as backtest_routes


def _cfg():
    return {"strategy": {"entry_threshold": 0.6}}


def test_entry_threshold_route_uses_the_async_fetch_not_the_sync_one(monkeypatch):
    """The regression this guards: calling the sync function directly on the
    event loop inside an `async def` route, no thread offload."""
    def _boom():
        raise AssertionError("resolved_signals_with_factors() (sync) must not be called by this async route")

    async def _stub_fetch(since_ts=None):
        return [{"confidence": 0.9, "correct": True}, {"confidence": 0.3, "correct": False}]

    monkeypatch.setattr(backtest_routes.signal_log, "resolved_signals_with_factors", _boom)
    monkeypatch.setattr(backtest_routes.signal_log, "resolved_signals_with_factors_async", _stub_fetch)
    monkeypatch.setattr(backtest_routes.config_store, "get", _cfg)

    result = asyncio.run(backtest_routes.get_backtest_entry_threshold())

    assert result["current_threshold"] == 0.6


def test_entry_threshold_route_response_shape_is_unchanged(monkeypatch):
    """Same JSON response shape as before the fix: {current_threshold, sweep},
    sweep computed by the real backtest.entry_threshold_sweep() over exactly
    what the (now async) fetch returned."""
    rows = [{"confidence": 0.9, "correct": True}, {"confidence": 0.3, "correct": False}]

    async def _stub_fetch(since_ts=None):
        return rows

    monkeypatch.setattr(backtest_routes.signal_log, "resolved_signals_with_factors_async", _stub_fetch)
    monkeypatch.setattr(backtest_routes.config_store, "get", _cfg)

    result = asyncio.run(backtest_routes.get_backtest_entry_threshold())

    assert set(result.keys()) == {"current_threshold", "sweep"}
    assert result["current_threshold"] == 0.6
    assert result["sweep"] == backtest_routes.backtest.entry_threshold_sweep(rows)
