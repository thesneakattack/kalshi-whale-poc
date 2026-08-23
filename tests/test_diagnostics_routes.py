"""
services/diagnostics/routes.py's GET /api/index/settlement/{ticker} - the
KalshiClient it creates must always be closed.

Found 2026-08-23 auditing every KalshiClient() call site in the codebase
for the same missing-close() shape that caused a real live leak in
services/whale_stream/index_stream_handlers.py's _spec_for (see
tests/test_index_stream_handlers.py's own docstring for that incident).
This route had zero callers anywhere in the app (frontend or backend,
confirmed by grep) at the time this was found, so it wasn't the source of
that particular incident's "Unclosed connector" pattern - but the same bug
shape, ready to fire on any real hit.
"""
import asyncio

import pytest
from fastapi import HTTPException

from services.diagnostics import routes as diagnostics_routes


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


def test_get_index_settlement_closes_its_client_on_success(monkeypatch):
    _FakeClient.instances = []
    monkeypatch.setattr(diagnostics_routes, "KalshiClient", _FakeClient)
    monkeypatch.setattr(diagnostics_routes.config_store, "get", _fake_cfg)

    result = asyncio.run(diagnostics_routes.get_index_settlement("TICK-A"))

    assert result.get("supported") is False
    assert len(_FakeClient.instances) == 1
    assert _FakeClient.instances[0].closed is True


def test_get_index_settlement_closes_its_client_even_when_the_fetch_fails(monkeypatch):
    _FakeClient.instances = []
    monkeypatch.setattr(diagnostics_routes, "KalshiClient", _BoomClient)
    monkeypatch.setattr(diagnostics_routes.config_store, "get", _fake_cfg)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(diagnostics_routes.get_index_settlement("TICK-B"))

    assert exc_info.value.status_code == 502
    assert len(_FakeClient.instances) == 1
    assert _FakeClient.instances[0].closed is True
