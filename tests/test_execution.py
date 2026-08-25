"""
Emergency flatten orchestration (services/execution.py — Kalshi Integration
Phase A Task A9, moved up from KalshiAccountClient.flatten_all where it
lived since the 2026-08-23 gap-check finding).

Behavior frozen here BEFORE the move, per the A9 plan: closing-side
semantics (YES closes by selling yes, NO closes by buying yes), pinned IOC
prices, zero-position skip, per-ticker error isolation, the risk-halt
bypass (a flatten is risk-reducing and must work mid-halt), and the
trading_enabled gate still applying per order underneath.

Tests run through the REAL KalshiAccountClient facade with a fake SDK
client — so the orchestration exercises the true trading_enabled/risk
gates in services/kalshi/orders.py, not a mock of them. Nothing here can
touch a real account or the network.
"""
import asyncio

import pytest

from services import execution
from services.kalshi import account_client as kac_module
from services.risk_manager import RiskManager


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def model_dump(self, mode="json"):
        return self._payload


class _FakeSDKClient:
    def __init__(self, positions=None):
        self.calls = []
        self._positions = positions or []

    async def get_positions(self, **kwargs):
        return _FakeResp({"market_positions": self._positions})

    async def create_order_v2(self, **kwargs):
        self.calls.append(("create_order_v2", kwargs))
        return _FakeResp({"ok": True})


def _account(positions, trading_enabled=True, risk=None):
    c = kac_module.KalshiAccountClient(
        base_url="https://example.test/trade-api/v2", request_timeout_sec=5,
        trading_enabled=trading_enabled, risk=risk,
    )
    fake = _FakeSDKClient(positions)
    c._client = fake
    return c, fake


def _risk(tmp_path, monkeypatch):
    from services import risk_manager as rm
    monkeypatch.setattr(rm, "DB_PATH", tmp_path / "risk_state.db")
    return RiskManager(1000.0, 0.1, True)


def test_flatten_closes_a_yes_position_by_selling_yes():
    account, fake = _account([{"ticker": "TICK-YES", "position_fp": "10.00"}])
    results = asyncio.run(execution.flatten_all_real_positions(account))
    assert len(results) == 1
    assert results[0]["ticker"] == "TICK-YES"
    assert results[0]["error"] is None
    name, kwargs = fake.calls[0]
    assert name == "create_order_v2"
    assert kwargs["side"] == "ask"
    assert kwargs["price"] == "0.0100"
    assert kwargs["count"] == "10.00"
    assert kwargs["time_in_force"] == "immediate_or_cancel"


def test_flatten_closes_a_no_position_by_buying_yes():
    account, fake = _account([{"ticker": "TICK-NO", "position_fp": "-5.00"}])
    results = asyncio.run(execution.flatten_all_real_positions(account))
    assert len(results) == 1
    name, kwargs = fake.calls[0]
    assert kwargs["side"] == "bid"
    assert kwargs["price"] == "0.9900"
    assert kwargs["count"] == "5.00"


def test_flatten_skips_zero_positions():
    account, fake = _account([{"ticker": "TICK-FLAT", "position_fp": "0.00"}])
    results = asyncio.run(execution.flatten_all_real_positions(account))
    assert results == []
    assert fake.calls == []


def test_flatten_bypasses_the_risk_halt_guard(tmp_path, monkeypatch):
    # The whole point of an emergency flatten: it must still work while
    # the daily-loss kill switch is halted (closing is risk-reducing).
    risk = _risk(tmp_path, monkeypatch)
    risk.manual_halt("test halt")
    account, fake = _account([{"ticker": "TICK-A", "position_fp": "10.00"}], risk=risk)
    results = asyncio.run(execution.flatten_all_real_positions(account))
    assert len(results) == 1
    assert results[0]["error"] is None


def test_flatten_records_a_per_ticker_error_without_aborting_the_rest():
    account, fake = _account([
        {"ticker": "TICK-BAD", "position_fp": "10.00"},
        {"ticker": "TICK-GOOD", "position_fp": "5.00"},
    ])
    real_create = fake.create_order_v2

    async def flaky(**kwargs):
        if kwargs["ticker"] == "TICK-BAD":
            raise RuntimeError("simulated order failure")
        return await real_create(**kwargs)
    fake.create_order_v2 = flaky

    results = asyncio.run(execution.flatten_all_real_positions(account))
    assert len(results) == 2
    bad = next(r for r in results if r["ticker"] == "TICK-BAD")
    good = next(r for r in results if r["ticker"] == "TICK-GOOD")
    assert bad["error"] == "simulated order failure"
    assert good["error"] is None


def test_flatten_still_hits_the_trading_enabled_gate_per_order():
    # Moving orchestration above the adapter must not have created a path
    # around the write gate: with trading disabled, each close attempt is
    # refused by KalshiOrderGateway and recorded per ticker - the flatten
    # itself never bypasses the gate.
    account, fake = _account([{"ticker": "TICK-A", "position_fp": "10.00"}], trading_enabled=False)
    results = asyncio.run(execution.flatten_all_real_positions(account))
    assert len(results) == 1
    assert results[0]["order"] is None
    assert "disabled" in results[0]["error"]
    assert fake.calls == []


def test_the_vendor_facade_no_longer_owns_flatten_policy():
    # A9's acceptance: the adapter exposes primitives; the application
    # execution service owns policy. flatten_all must not quietly return
    # to the vendor facade or either gateway.
    from services.kalshi.account import KalshiAccountGateway
    from services.kalshi.orders import KalshiOrderGateway
    assert not hasattr(kac_module.KalshiAccountClient, "flatten_all")
    assert not hasattr(KalshiAccountGateway, "flatten_all")
    assert not hasattr(KalshiOrderGateway, "flatten_all")
