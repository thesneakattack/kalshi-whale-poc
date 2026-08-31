"""
services/diagnostics/routes.py's GET /api/index/settlement/{ticker} - the
KalshiPublicGateway it creates must always be closed.

Found 2026-08-23 auditing every KalshiPublicGateway() call site in the codebase
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
    monkeypatch.setattr(diagnostics_routes, "KalshiPublicGateway", _FakeClient)
    monkeypatch.setattr(diagnostics_routes.config_store, "get", _fake_cfg)

    result = asyncio.run(diagnostics_routes.get_index_settlement("TICK-A"))

    assert result.get("supported") is False
    assert len(_FakeClient.instances) == 1
    assert _FakeClient.instances[0].closed is True


def test_get_index_settlement_closes_its_client_even_when_the_fetch_fails(monkeypatch):
    _FakeClient.instances = []
    monkeypatch.setattr(diagnostics_routes, "KalshiPublicGateway", _BoomClient)
    monkeypatch.setattr(diagnostics_routes.config_store, "get", _fake_cfg)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(diagnostics_routes.get_index_settlement("TICK-B"))

    assert exc_info.value.status_code == 502
    assert len(_FakeClient.instances) == 1
    assert _FakeClient.instances[0].closed is True


# ---- GET /api/diagnostics/account (issues #266/#261) -----------------------
#
# Deliberately its own route rather than folded into /api/health/pipeline or
# /api/quality/summary - see get_account_diagnostics' own docstring. These
# tests never touch a real account: account.enabled is stubbed, so nothing
# here can reach the real Kalshi API even if credentials happen to be
# configured in the environment these tests run in.


class _FakeDisabledAccount:
    enabled = False

    async def get_user_data_timestamp(self):
        raise AssertionError("must not be called when the account isn't configured")

    async def get_api_keys(self):
        raise AssertionError("must not be called when the account isn't configured")


class _FakeEnabledAccount:
    enabled = True

    def __init__(self, ts_payload=None, ts_error=None, keys_payload=None, keys_error=None):
        self._ts_payload, self._ts_error = ts_payload, ts_error
        self._keys_payload, self._keys_error = keys_payload, keys_error
        self.calls = []

    async def get_user_data_timestamp(self):
        self.calls.append("get_user_data_timestamp")
        if self._ts_error:
            raise self._ts_error
        return self._ts_payload

    async def get_api_keys(self):
        self.calls.append("get_api_keys")
        if self._keys_error:
            raise self._keys_error
        return self._keys_payload


class _FakeTradeStream:
    def __init__(self, oldest_message_age_sec):
        self._age = oldest_message_age_sec

    def ingest_metrics(self):
        return {"queue": {"oldest_message_age_sec": self._age}}


def test_get_account_diagnostics_makes_no_calls_when_account_is_not_configured(monkeypatch):
    monkeypatch.setattr(diagnostics_routes, "account", _FakeDisabledAccount())
    monkeypatch.setattr(diagnostics_routes, "trade_stream", _FakeTradeStream(4.2))

    result = asyncio.run(diagnostics_routes.get_account_diagnostics())

    assert result["configured"] is False
    assert result["user_data_timestamp"] is None
    assert result["api_key_attestation"] is None
    assert result["pipeline_oldest_message_age_sec"] == 4.2


def test_get_account_diagnostics_surfaces_both_reads_side_by_side_when_configured(monkeypatch):
    fake_account = _FakeEnabledAccount(
        ts_payload={"as_of_time": "2026-08-30T12:00:00Z"},
        keys_payload={
            "api_keys": [{"api_key_id": "k1", "name": "n", "scopes": ["read"]}],
            "api_key_region_expiration_ts": 9999999999,
        },
    )
    monkeypatch.setattr(diagnostics_routes, "account", fake_account)
    monkeypatch.setattr(diagnostics_routes, "trade_stream", _FakeTradeStream(1.5))

    result = asyncio.run(diagnostics_routes.get_account_diagnostics())

    assert result["configured"] is True
    assert result["user_data_timestamp"]["as_of_time"] == "2026-08-30T12:00:00Z"
    assert isinstance(result["user_data_timestamp"]["as_of_age_sec"], float)
    assert result["api_key_attestation"]["status"] == "active"
    assert result["api_key_attestation"]["api_key_count"] == 1
    # #266's actual point: the exchange's own lag and this app's own
    # pipeline lag ride in the same response, never merged into one number.
    assert result["pipeline_oldest_message_age_sec"] == 1.5
    assert set(fake_account.calls) == {"get_user_data_timestamp", "get_api_keys"}


def test_get_account_diagnostics_degrades_to_an_explicit_error_when_a_read_fails(monkeypatch):
    from services import fault_log

    recorded = []
    monkeypatch.setattr(fault_log, "record", lambda *a, **kw: recorded.append((a, kw)))
    fake_account = _FakeEnabledAccount(
        ts_error=RuntimeError("kalshi 500"),
        keys_payload={"api_keys": []},
    )
    monkeypatch.setattr(diagnostics_routes, "account", fake_account)
    monkeypatch.setattr(diagnostics_routes, "trade_stream", _FakeTradeStream(None))

    result = asyncio.run(diagnostics_routes.get_account_diagnostics())

    assert result["configured"] is True
    assert result["user_data_timestamp"] == {"error": "kalshi 500"}
    assert result["api_key_attestation"]["status"] == "never_attested"
    assert len(recorded) == 1
    assert recorded[0][0][:2] == ("kalshi_account", "get_user_data_timestamp")


# ---- GET /api/diagnostics/candlestick-volatility ---------------------------


def test_get_candlestick_volatility_diagnostics_returns_report_and_bar_count(monkeypatch, tmp_path):
    """Seed real candlestick data, call the route without mocking the
    candlestick_volatility functions, and assert on real computed values."""
    import time
    from pathlib import Path
    from services import candlestick_volatility

    # Isolate to a temporary DB
    temp_db = tmp_path / "test_candles.db"
    monkeypatch.setattr(candlestick_volatility, "DB_PATH", temp_db)

    # Seed real candlestick data for one ticker
    now = time.time()
    bars = [
        {
            "end_period_ts": now - 300,  # 5 min ago
            "yes_bid": {
                "open_dollars": 0.50,
                "high_dollars": 0.52,
                "low_dollars": 0.49,
                "close_dollars": 0.51,
            },
        },
        {
            "end_period_ts": now - 180,  # 3 min ago
            "yes_bid": {
                "open_dollars": 0.51,
                "high_dollars": 0.53,
                "low_dollars": 0.50,
                "close_dollars": 0.52,
            },
        },
        {
            "end_period_ts": now - 60,  # 1 min ago
            "yes_bid": {
                "open_dollars": 0.52,
                "high_dollars": 0.54,
                "low_dollars": 0.51,
                "close_dollars": 0.53,
            },
        },
    ]
    stored = candlestick_volatility.record_candles(
        ticker="TEST-VOLATILITY-A",
        series_ticker="TEST-VOLATILITY",
        period_interval_min=60,
        bars=bars,
        fetched_at=now,
    )
    assert stored == 3, "Failed to seed test candle data"

    # Set state["markets"] to include the ticker we seeded
    test_markets = [
        {"ticker": "TEST-VOLATILITY-A"},
        {"other_field": "no_ticker"},  # Should be skipped
    ]
    mock_state = {"markets": test_markets}
    monkeypatch.setattr(diagnostics_routes, "state", mock_state)

    # Call the route handler WITHOUT mocking the functions - let them run real
    result = asyncio.run(diagnostics_routes.get_candlestick_volatility_diagnostics())

    # Assert structure
    assert isinstance(result, dict)
    assert "report" in result
    assert "bar_count" in result
    assert isinstance(result["report"], list)
    assert isinstance(result["bar_count"], int)

    # Assert the report contains our seeded ticker
    assert len(result["report"]) == 1
    report_entry = result["report"][0]
    assert report_entry["ticker"] == "TEST-VOLATILITY-A"
    assert report_entry["candlestick_bar_count"] == 3

    # Assert candlestick_volatility computed a real value (not None)
    # The value depends on the variance of closes: 0.51, 0.52, 0.53
    # Deltas: 0.01, 0.01 -> stdev should be 0
    assert report_entry["candlestick_volatility"] is not None
    assert report_entry["candlestick_volatility"] >= 0

    # Assert bar_count returns the total (should be 3 since we only seeded one ticker)
    assert result["bar_count"] == 3

    # Test with non-default lookback_sec to verify parameter is passed through
    result_short = asyncio.run(diagnostics_routes.get_candlestick_volatility_diagnostics(lookback_sec=10))
    # With a 10-second lookback, we should get 0 bars (all our bars are older than 10 sec)
    # So candlestick_volatility should be None due to < 3 bars
    assert result_short["report"][0]["candlestick_volatility"] is None
