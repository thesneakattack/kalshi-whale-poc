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


def _client_with_fake_sdk(trading_enabled=True, timeout=5):
    c = kac_module.KalshiAccountClient(
        base_url="https://example.test/trade-api/v2", request_timeout_sec=timeout, trading_enabled=trading_enabled
    )
    fake = _FakeSDKClient()
    c._client = fake  # simulate an already-connected account, no real key needed
    return c, fake


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
