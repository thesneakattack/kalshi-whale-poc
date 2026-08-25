"""
Verifies KalshiAccountClient's credential loading and the request shape
create_order/cancel_order build, after migrating to the official
kalshi_python_async SDK.

Credential-loading tests use a throwaway RSA key generated on the fly
(cryptography's own key generator) - never the real Kalshi key - so this
never touches real credentials or the network. Request-shape tests replace
the SDK client instance directly with a fake that records calls, so nothing
here can place or cancel a real order even by accident.
"""
import asyncio

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from services import kalshi_account_client as kac_module
from services.risk_manager import RiskManager


def _generate_test_key_pem() -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def model_dump(self, mode="json"):
        return self._payload


class _FakeSDKClient:
    def __init__(self):
        self.calls = []

    async def create_order_v2(self, **kwargs):
        self.calls.append(("create_order_v2", kwargs))
        return _FakeResp({"ok": True})

    async def cancel_order_v2(self, order_id, **kwargs):
        self.calls.append(("cancel_order_v2", {"order_id": order_id, **kwargs}))
        return _FakeResp({"ok": True})


def _client_with_fake_sdk(trading_enabled=True, timeout=5, risk=None):
    c = kac_module.KalshiAccountClient(
        base_url="https://example.test/trade-api/v2", request_timeout_sec=timeout,
        trading_enabled=trading_enabled, risk=risk,
    )
    fake = _FakeSDKClient()
    c._client = fake  # simulate an already-connected account, no real key needed
    return c, fake


def _risk(tmp_path, monkeypatch, starting_bankroll=1000.0, max_daily_loss_pct=0.1, kill_switch_enabled=True):
    from services import risk_manager as rm
    monkeypatch.setattr(rm, "DB_PATH", tmp_path / "risk_state.db")
    return RiskManager(starting_bankroll, max_daily_loss_pct, kill_switch_enabled)


# ---- credential loading (real code path, throwaway key, no network) -------

def test_no_credentials_is_not_an_error(monkeypatch):
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH", raising=False)
    c = kac_module.KalshiAccountClient(base_url="https://example.test/trade-api/v2", request_timeout_sec=5, trading_enabled=False)
    assert c.enabled is False
    assert c.status == {"connected": False, "error": None}


def test_valid_key_file_loads_and_enables(tmp_path, monkeypatch):
    key_path = tmp_path / "test_key.pem"
    key_path.write_bytes(_generate_test_key_pem())
    monkeypatch.setenv("KALSHI_API_KEY_ID", "test-key-id")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", str(key_path))

    c = kac_module.KalshiAccountClient(base_url="https://example.test/trade-api/v2", request_timeout_sec=5, trading_enabled=False)

    assert c.enabled is True
    assert c.status == {"connected": True, "error": None}


def test_malformed_key_file_fails_with_a_real_error(tmp_path, monkeypatch):
    key_path = tmp_path / "bad_key.pem"
    key_path.write_text("this is not a PEM key")
    monkeypatch.setenv("KALSHI_API_KEY_ID", "test-key-id")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", str(key_path))

    c = kac_module.KalshiAccountClient(base_url="https://example.test/trade-api/v2", request_timeout_sec=5, trading_enabled=False)

    assert c.enabled is False
    assert c.status["connected"] is False
    assert c.status["error"]  # some real error message, not silently swallowed


# ---- request shape (fake SDK client, no network) ---------------------------

def test_create_order_uses_v2_shape(monkeypatch):
    c, fake = _client_with_fake_sdk(trading_enabled=True, timeout=5)
    result = asyncio.run(c.create_order(ticker="TICK-A", side="bid", count="10.00", price="0.5600"))
    assert result == {"ok": True}
    assert len(fake.calls) == 1
    name, kwargs = fake.calls[0]
    assert name == "create_order_v2"
    assert kwargs["ticker"] == "TICK-A"
    assert kwargs["side"] == "bid"
    assert kwargs["count"] == "10.00"
    assert kwargs["price"] == "0.5600"
    assert kwargs["time_in_force"] == "immediate_or_cancel"
    assert kwargs["self_trade_prevention_type"] == "taker_at_cross"
    assert "client_order_id" in kwargs
    assert kwargs["_request_timeout"] == 5
    # Kalshi's legacy order shape - must not leak back in
    assert "action" not in kwargs
    assert "yes_price" not in kwargs
    assert "no_price" not in kwargs
    assert "type" not in kwargs


def test_create_order_refuses_when_trading_disabled():
    c, fake = _client_with_fake_sdk(trading_enabled=False)
    with pytest.raises(PermissionError):
        asyncio.run(c.create_order(ticker="TICK-A", side="bid", count="1.00", price="0.5000"))
    assert fake.calls == []  # never even attempted the call


def test_cancel_order_uses_v2_shape():
    c, fake = _client_with_fake_sdk(trading_enabled=True)
    asyncio.run(c.cancel_order("order-123"))
    assert len(fake.calls) == 1
    name, kwargs = fake.calls[0]
    assert name == "cancel_order_v2"
    assert kwargs["order_id"] == "order-123"
    assert "_request_timeout" not in kwargs  # cancel_order_v2 rejects it - see kalshi_account_client.py


def test_cancel_order_refuses_when_trading_disabled():
    c, fake = _client_with_fake_sdk(trading_enabled=False)
    with pytest.raises(PermissionError):
        asyncio.run(c.cancel_order("order-123"))
    assert fake.calls == []


# ---- execution-layer risk guard (2026-08-23 gap-check finding) ------------

def test_create_order_refuses_when_risk_halted(tmp_path, monkeypatch):
    risk = _risk(tmp_path, monkeypatch)
    risk.manual_halt("test halt")
    c, fake = _client_with_fake_sdk(trading_enabled=True, risk=risk)
    with pytest.raises(PermissionError):
        asyncio.run(c.create_order(ticker="TICK-A", side="bid", count="1.00", price="0.5000"))
    assert fake.calls == []


def test_create_order_allowed_when_risk_not_halted(tmp_path, monkeypatch):
    risk = _risk(tmp_path, monkeypatch)
    c, fake = _client_with_fake_sdk(trading_enabled=True, risk=risk)
    result = asyncio.run(c.create_order(ticker="TICK-A", side="bid", count="1.00", price="0.5000"))
    assert result == {"ok": True}
    assert len(fake.calls) == 1


def test_create_order_with_no_risk_wired_in_is_unaffected():
    # None (default) is a no-op, same as every other opt-in risk gate.
    c, fake = _client_with_fake_sdk(trading_enabled=True, risk=None)
    result = asyncio.run(c.create_order(ticker="TICK-A", side="bid", count="1.00", price="0.5000"))
    assert result == {"ok": True}


def test_create_order_closing_order_bypasses_the_halt_guard(tmp_path, monkeypatch):
    # A flatten/close must still work while halted - that's the whole
    # point of an emergency flatten.
    risk = _risk(tmp_path, monkeypatch)
    risk.manual_halt("test halt")
    c, fake = _client_with_fake_sdk(trading_enabled=True, risk=risk)
    result = asyncio.run(c.create_order(
        ticker="TICK-A", side="ask", count="1.00", price="0.0100", is_closing_order=True,
    ))
    assert result == {"ok": True}
    assert len(fake.calls) == 1


# ---- flatten_all (2026-08-23 gap-check finding) ----------------------------

def test_flatten_all_closes_a_yes_position_by_selling_yes():
    c, fake = _client_with_fake_sdk(trading_enabled=True)

    async def get_positions(**kwargs):
        return _FakeResp({"market_positions": [{"ticker": "TICK-YES", "position_fp": "10.00"}]})
    fake.get_positions = get_positions

    results = asyncio.run(c.flatten_all())
    assert len(results) == 1
    assert results[0]["ticker"] == "TICK-YES"
    assert results[0]["error"] is None
    name, kwargs = fake.calls[0]
    assert name == "create_order_v2"
    assert kwargs["side"] == "ask"
    assert kwargs["price"] == "0.0100"
    assert kwargs["count"] == "10.00"


def test_flatten_all_closes_a_no_position_by_buying_yes():
    c, fake = _client_with_fake_sdk(trading_enabled=True)

    async def get_positions(**kwargs):
        return _FakeResp({"market_positions": [{"ticker": "TICK-NO", "position_fp": "-5.00"}]})
    fake.get_positions = get_positions

    results = asyncio.run(c.flatten_all())
    assert len(results) == 1
    name, kwargs = fake.calls[0]
    assert kwargs["side"] == "bid"
    assert kwargs["price"] == "0.9900"
    assert kwargs["count"] == "5.00"


def test_flatten_all_skips_zero_positions():
    c, fake = _client_with_fake_sdk(trading_enabled=True)

    async def get_positions(**kwargs):
        return _FakeResp({"market_positions": [{"ticker": "TICK-FLAT", "position_fp": "0.00"}]})
    fake.get_positions = get_positions

    results = asyncio.run(c.flatten_all())
    assert results == []
    assert fake.calls == []


def test_flatten_all_bypasses_the_risk_halt_guard(tmp_path, monkeypatch):
    risk = _risk(tmp_path, monkeypatch)
    risk.manual_halt("test halt")
    c, fake = _client_with_fake_sdk(trading_enabled=True, risk=risk)

    async def get_positions(**kwargs):
        return _FakeResp({"market_positions": [{"ticker": "TICK-A", "position_fp": "10.00"}]})
    fake.get_positions = get_positions

    results = asyncio.run(c.flatten_all())
    assert len(results) == 1
    assert results[0]["error"] is None


def test_flatten_all_records_a_per_ticker_error_without_aborting_the_rest():
    c, fake = _client_with_fake_sdk(trading_enabled=True)

    async def get_positions(**kwargs):
        return _FakeResp({"market_positions": [
            {"ticker": "TICK-BAD", "position_fp": "10.00"},
            {"ticker": "TICK-GOOD", "position_fp": "5.00"},
        ]})
    fake.get_positions = get_positions

    call_count = {"n": 0}
    real_create_order_v2 = fake.create_order_v2

    async def flaky_create_order_v2(**kwargs):
        call_count["n"] += 1
        if kwargs["ticker"] == "TICK-BAD":
            raise RuntimeError("simulated order failure")
        return await real_create_order_v2(**kwargs)
    fake.create_order_v2 = flaky_create_order_v2

    results = asyncio.run(c.flatten_all())
    assert len(results) == 2
    bad = next(r for r in results if r["ticker"] == "TICK-BAD")
    good = next(r for r in results if r["ticker"] == "TICK-GOOD")
    assert bad["error"] == "simulated order failure"
    assert good["error"] is None


def test_get_balance_positions_fills_delegate_to_sdk():
    c, fake = _client_with_fake_sdk(trading_enabled=False)

    async def get_balance(**kwargs):
        return _FakeResp({"balance": 1})

    async def get_positions(**kwargs):
        return _FakeResp({"market_positions": []})

    async def get_fills(**kwargs):
        fake.calls.append(("get_fills", kwargs))
        return _FakeResp({"fills": []})

    fake.get_balance = get_balance
    fake.get_positions = get_positions
    fake.get_fills = get_fills

    assert asyncio.run(c.get_balance()) == {"balance": 1}
    assert asyncio.run(c.get_positions()) == {"market_positions": []}
    assert asyncio.run(c.get_fills(limit=10)) == {"fills": []}
    assert fake.calls == [("get_fills", {"limit": 10})]


# ---- A5 transport delegation (Kalshi Integration Phase A) ------------------

def test_credential_construction_delegates_to_the_boundary_transport(tmp_path, monkeypatch):
    """A5: the authenticated SDK-client construction (PEM validation +
    Configuration wiring) is owned by services/kalshi/transport.py; this
    wrapper keeps env/file handling and error capture but delegates the
    vendor construction itself."""
    key_path = tmp_path / "test_key.pem"
    pem = _generate_test_key_pem()
    key_path.write_bytes(pem)
    monkeypatch.setenv("KALSHI_API_KEY_ID", "test-key-id")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", str(key_path))

    sentinel = object()
    seen = []

    def fake_build(base_url, key_id, private_key_pem):
        seen.append((base_url, key_id, private_key_pem))
        return sentinel

    monkeypatch.setattr(kac_module.transport, "build_account_client", fake_build)

    c = kac_module.KalshiAccountClient(
        base_url="https://example.test/trade-api/v2", request_timeout_sec=5, trading_enabled=False,
    )

    assert c._client is sentinel
    assert seen == [("https://example.test/trade-api/v2", "test-key-id", pem)]


# ---- A8 capability split: account reads vs order writes --------------------
# (Kalshi Integration Phase A Task A8 - services/kalshi/account.py +
# services/kalshi/orders.py behind this same compatibility facade.)

def _order_gateway(trading_enabled=True, timeout=5, risk=None):
    from services.kalshi.orders import KalshiOrderGateway
    fake = _FakeSDKClient()
    gw = KalshiOrderGateway(
        fake, trading_enabled=trading_enabled, request_timeout_sec=timeout, risk=risk,
    )
    return gw, fake


def test_account_read_gateway_exposes_no_write_methods():
    """The design spec's capability boundary: an authenticated *read*
    object must not be able to place or cancel orders, structurally -
    not merely by convention."""
    from services.kalshi.account import KalshiAccountGateway
    for write_name in ("create_order", "cancel_order", "amend_order", "flatten_all",
                       "create_order_v2", "cancel_order_v2"):
        assert not hasattr(KalshiAccountGateway, write_name)


def test_public_gateway_exposes_no_write_methods():
    from services.kalshi.public import KalshiPublicGateway
    for write_name in ("create_order", "cancel_order", "amend_order", "flatten_all",
                       "create_order_v2", "cancel_order_v2"):
        assert not hasattr(KalshiPublicGateway, write_name)


def test_order_gateway_refuses_create_when_trading_disabled():
    gw, fake = _order_gateway(trading_enabled=False)
    with pytest.raises(PermissionError):
        asyncio.run(gw.create_order(ticker="TICK-A", side="bid", count="1.00", price="0.5000"))
    assert fake.calls == []


def test_order_gateway_refuses_cancel_when_trading_disabled():
    gw, fake = _order_gateway(trading_enabled=False)
    with pytest.raises(PermissionError):
        asyncio.run(gw.cancel_order("order-123"))
    assert fake.calls == []


def test_order_gateway_risk_halt_blocks_opening_but_not_closing(tmp_path, monkeypatch):
    risk = _risk(tmp_path, monkeypatch)
    risk.manual_halt("test halt")
    gw, fake = _order_gateway(trading_enabled=True, risk=risk)
    with pytest.raises(PermissionError):
        asyncio.run(gw.create_order(ticker="TICK-A", side="bid", count="1.00", price="0.5000"))
    assert fake.calls == []
    result = asyncio.run(gw.create_order(
        ticker="TICK-A", side="ask", count="1.00", price="0.0100", is_closing_order=True,
    ))
    assert result == {"ok": True}
    assert len(fake.calls) == 1


def test_facade_trading_enabled_mutation_reaches_the_write_gate():
    """main.py's enable/disable routes and account_positions' config
    re-sync all assign account.trading_enabled at runtime - the gate that
    actually blocks the SDK call must read that live value, not a
    construction-time snapshot. This is the one behavior the A8 split
    could silently regress."""
    c, fake = _client_with_fake_sdk(trading_enabled=False)
    with pytest.raises(PermissionError):
        asyncio.run(c.create_order(ticker="TICK-A", side="bid", count="1.00", price="0.5000"))

    c.trading_enabled = True  # what POST /api/trading/enable does
    result = asyncio.run(c.create_order(ticker="TICK-A", side="bid", count="1.00", price="0.5000"))
    assert result == {"ok": True}

    c.trading_enabled = False  # what POST /api/trading/disable does
    with pytest.raises(PermissionError):
        asyncio.run(c.create_order(ticker="TICK-B", side="bid", count="1.00", price="0.5000"))
    assert len(fake.calls) == 1


def test_facade_client_injection_reaches_both_gateways():
    """Existing tests (this file + test_trading_gate.py) simulate a
    connected account by assigning account._client - after the split that
    one assignment must reach the read gateway and the write gateway
    alike, or half the facade would silently keep using a stale client."""
    c, fake = _client_with_fake_sdk(trading_enabled=True)
    assert c._reads._client is fake
    assert c._writes._client is fake
    assert c._client is fake
