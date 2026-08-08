"""
Verifies the request shape create_order/cancel_order build against Kalshi's
real API - services.http_client.request_with_backoff() is monkeypatched to a
fake that never touches the network, and no real private key is ever loaded,
so nothing here can place or cancel a real order even by accident.
"""
import asyncio
import re

import pytest

from services import kalshi_account_client as kac_module


class _FakeKey:
    def __init__(self):
        self.signed_messages = []

    def sign(self, message, padding_scheme, algorithm):
        self.signed_messages.append(message)
        return b"fake-signature-bytes"


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self):
        self.calls = []

    async def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return _FakeResponse({"ok": True})


def _client(monkeypatch, trading_enabled=True):
    fake = _FakeAsyncClient()
    monkeypatch.setattr(kac_module, "request_with_backoff", fake.request)
    c = kac_module.KalshiAccountClient(
        base_url="https://example.test/trade-api/v2", request_timeout_sec=5, trading_enabled=trading_enabled
    )
    c._private_key = _FakeKey()  # simulate a loaded key without a real file
    c.key_id = "test-key-id"
    return c, fake


def test_create_order_uses_v2_events_orders_path_and_shape(monkeypatch):
    c, fake = _client(monkeypatch, trading_enabled=True)
    result = asyncio.run(c.create_order(ticker="TICK-A", side="bid", count="10.00", price="0.5600"))
    assert result == {"ok": True}
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://example.test/trade-api/v2/portfolio/events/orders"

    body = call["json"]
    assert body["ticker"] == "TICK-A"
    assert body["side"] == "bid"
    assert body["count"] == "10.00"
    assert body["price"] == "0.5600"
    assert body["time_in_force"] == "immediate_or_cancel"
    assert body["self_trade_prevention_type"] == "taker_at_cross"
    assert "client_order_id" in body
    # Kalshi's legacy order shape - must not leak back in
    assert "action" not in body
    assert "yes_price" not in body
    assert "no_price" not in body
    assert "type" not in body


def test_create_order_refuses_when_trading_disabled(monkeypatch):
    c, fake = _client(monkeypatch, trading_enabled=False)
    with pytest.raises(PermissionError):
        asyncio.run(c.create_order(ticker="TICK-A", side="bid", count="1.00", price="0.5000"))
    assert fake.calls == []  # never even attempted the network call


def test_cancel_order_uses_v2_events_orders_path(monkeypatch):
    c, fake = _client(monkeypatch, trading_enabled=True)
    asyncio.run(c.cancel_order("order-123"))
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["method"] == "DELETE"
    assert call["url"] == "https://example.test/trade-api/v2/portfolio/events/orders/order-123"


def test_cancel_order_refuses_when_trading_disabled(monkeypatch):
    c, fake = _client(monkeypatch, trading_enabled=False)
    with pytest.raises(PermissionError):
        asyncio.run(c.cancel_order("order-123"))
    assert fake.calls == []


def test_signed_message_includes_the_trade_api_v2_prefix(monkeypatch):
    # Regression test: every signed request 401'd against Kalshi's real API
    # (both production and demo) until this was fixed - the signed message
    # omitted the "/trade-api/v2" prefix that docs.kalshi.com's own worked
    # example includes (path='/trade-api/v2/portfolio/balance').
    c, fake = _client(monkeypatch, trading_enabled=True)
    asyncio.run(c.cancel_order("order-123"))
    signed = c._private_key.signed_messages[0].decode()
    # message = f"{timestamp_ms}{method}{base_path}{path}" - digits, then
    # method, then the full signed path including "/trade-api/v2".
    assert re.fullmatch(r"\d+DELETE/trade-api/v2/portfolio/events/orders/order-123", signed)


def test_base_path_derived_from_base_url_not_hardcoded(monkeypatch):
    fake = _FakeAsyncClient()
    monkeypatch.setattr(kac_module, "request_with_backoff", fake.request)
    c = kac_module.KalshiAccountClient(
        base_url="https://external-api.demo.kalshi.co/trade-api/v2", request_timeout_sec=5, trading_enabled=True
    )
    c._private_key = _FakeKey()
    c.key_id = "test-key-id"
    asyncio.run(c.cancel_order("order-123"))
    signed = c._private_key.signed_messages[0].decode()
    assert "/trade-api/v2/portfolio/events/orders/order-123" in signed
    assert fake.calls[0]["url"] == "https://external-api.demo.kalshi.co/trade-api/v2/portfolio/events/orders/order-123"
