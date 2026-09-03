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


@pytest.fixture(autouse=True)
def _reset_aio_db_cache():
    # Same fixture tests/test_diagnostics.py, tests/test_series_watcher.py and
    # tests/test_main_tick_executor_wiring.py carry. Missing here, this file
    # printed "7 passed" and then hung forever: aiosqlite gives every cached
    # connection a NON-daemon OS thread and CPython's exit joins those
    # (PR adversarial review finding C1, 2026-09-01). _aio_db now also closes
    # them from a threading._register_atexit hook, so this is defence in
    # depth rather than the only thing standing between the suite and a hang -
    # but it still matters on its own: it stops one test's cached connection,
    # opened against a tmp_path DB that is deleted at teardown, from being
    # handed to the next test.
    yield
    from services.diagnostics import _aio_db
    asyncio.run(_aio_db.reset())


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


# ---- GET /api/diagnostics and GET /api/diagnostics/series/{series} --------
#
# Event-loop-blocking-fix2-diagnostics-widening (Step 5) added `await` at
# both of these routes' now-async calls into diagnostics.run_offline() /
# series_watcher's funnel/reconcile/book_context_at_entry/capture_stats -
# neither route had ANY test coverage anywhere in tests/ before this
# (adversarial review finding G, 2026-09-01). Exercised through the real
# route functions, same asyncio.run(...) convention as every other test in
# this file (no TestClient here - that's test_quality_routes.py's
# convention, not this file's), against tests/conftest.py's globally
# runtime-isolated DB_PATHs, so nothing here can reach real data/*.db.

def test_get_diagnostics_returns_run_offline_shape():
    body = asyncio.run(diagnostics_routes.get_diagnostics())

    assert "overall" in body
    assert "checks" in body


def test_get_series_watcher_returns_all_four_sections():
    body = asyncio.run(diagnostics_routes.get_series_watcher("KXBTC15M"))

    assert set(body.keys()) == {"funnel", "reconcile", "book_context", "capture"}


# ---- GET /api/health/faults -------------------------------------------

def test_get_faults_runs_off_the_event_loop(monkeypatch):
    """Same #210 bug class as /api/health/pipeline's store probes
    (tests/test_pipeline_health_cost.py) - fl.summary()/fl.recent() must
    not run synchronously inside this async def route, or a slow fault_log
    query blocks every other request this process is serving."""
    from services import fault_log

    on_loop = []

    def _summary(*args, **kwargs):
        try:
            asyncio.get_running_loop()
            on_loop.append("summary")
        except RuntimeError:
            pass
        return {"distinct_faults": 0, "total_occurrences": 0, "by_component": {},
                "by_severity": {}, "most_frequent": []}

    def _recent(*args, **kwargs):
        try:
            asyncio.get_running_loop()
            on_loop.append("recent")
        except RuntimeError:
            pass
        return []

    monkeypatch.setattr(fault_log, "summary", _summary)
    monkeypatch.setattr(fault_log, "recent", _recent)

    body = asyncio.run(diagnostics_routes.get_faults())

    assert on_loop == []
    assert body["summary"]["distinct_faults"] == 0
    assert body["faults"] == []
