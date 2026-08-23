"""
services/whale_stream/index_stream_handlers.py's _spec_for - the per-ticker
KalshiClient it creates on a cache miss must always be closed.

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
    monkeypatch.setattr(ish, "KalshiClient", _FakeClient)
    monkeypatch.setattr(ish.config_store, "get", _fake_cfg)

    spec = asyncio.run(ish._spec_for("TICK-A"))

    assert spec.get("supported") is False
    assert len(_FakeClient.instances) == 1
    assert _FakeClient.instances[0].closed is True
    assert ish._settlement_spec_cache["TICK-A"] == spec


def test_spec_for_still_closes_its_client_when_get_market_raises(monkeypatch):
    ish._settlement_spec_cache.clear()
    _FakeClient.instances = []
    monkeypatch.setattr(ish, "KalshiClient", _BoomClient)
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
    monkeypatch.setattr(ish, "KalshiClient", _FakeClient)

    spec = asyncio.run(ish._spec_for("TICK-C"))

    assert spec == {"supported": True, "ticker": "TICK-C"}
    assert _FakeClient.instances == []  # cache hit - no client ever constructed
