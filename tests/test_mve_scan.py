"""services/market_watch/mve_scan.py - multivariate (combo) event discovery
(issue #268). Mirrors tests/test_catalog_scan_pacing.py's shape for the
sibling regular-series scan: a fake KalshiPublicGateway stands in for the
real gateway, market_catalog.DB_PATH and title_cache.DB_PATH are isolated
per test (never the live data/*.db files - CLAUDE.md), and app_state's real
`state` dict is reset between tests since mve_scan reads/writes it
directly (state["mve_series_cache"], state["mve_scan"],
state["event_titles"], state["market_titles"]) the same way catalog_scan
reads/writes state["series_cache"]/state["catalog_scan"].
"""
import asyncio
import time

import pytest

from services import title_cache
from services.app_state import state
from services.market_catalog import market_catalog
from services.market_watch import mve_scan


@pytest.fixture(autouse=True)
def _isolated_dbs(tmp_path, monkeypatch):
    monkeypatch.setattr(market_catalog, "DB_PATH", tmp_path / "market_catalog.db")
    monkeypatch.setattr(title_cache, "DB_PATH", tmp_path / "title_cache.db")
    state["mve_series_cache"] = {"fetched_at": 0.0, "series_tickers": []}
    state["mve_scan"] = {"scanning": False, "last_started_at": 0.0, "task": None}
    state["event_titles"] = {}
    state["market_titles"] = {}
    yield


def _iso(unix_ts):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _mve_event(event_ticker, series_ticker, category="Exotics", mutually_exclusive=False, markets=None):
    return {
        "event_ticker": event_ticker, "series_ticker": series_ticker, "category": category,
        "sub_title": "MVE", "title": f"Title for {event_ticker}", "collateral_return_type": "",
        "mutually_exclusive": mutually_exclusive, "available_on_brokers": False,
        "product_metadata": None, "settlement_sources": [], "strike_date": None, "strike_period": "",
        "fee_type_override": None, "fee_multiplier_override": None, "last_updated_ts": "0001-01-01T00:00:00Z",
        "markets": markets or [],
    }


def _mve_market(ticker, event_ticker, close_offset_sec=3600, status="active", volume=0):
    now = time.time()
    return {
        "ticker": ticker, "event_ticker": event_ticker, "status": status,
        "occurrence_datetime": None, "close_time": _iso(now + close_offset_sec),
        "title": f"Title for {ticker}", "yes_sub_title": "Yes", "no_sub_title": "No",
        "volume_24h_fp": str(volume),
    }


class _FakeMveClient:
    """Stands in for KalshiPublicGateway - only the two methods
    mve_scan.py actually calls."""

    def __init__(self, collections, events_by_series):
        self._collections = collections
        self._events_by_series = events_by_series  # series_ticker -> {"events": [...], "cursor": ""}
        self.collections_calls = 0
        self.events_calls = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def get_multivariate_event_collections(self, **kwargs):
        self.collections_calls += 1
        return self._collections

    async def get_multivariate_events(self, series_ticker, with_nested_markets, limit):
        self.events_calls.append(series_ticker)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(0.005)
        self.in_flight -= 1
        return self._events_by_series.get(series_ticker, {"events": [], "cursor": ""})


# --- _get_mve_series_cache ---------------------------------------------------

def test_get_mve_series_cache_extracts_distinct_series_tickers():
    collections = [
        {"collection_ticker": "C1", "series_ticker": "KXMVECROSSCATEGORY"},
        {"collection_ticker": "C2", "series_ticker": "KXMVECROSSCATEGORY-SHARD1"},
        {"collection_ticker": "C3", "series_ticker": "KXMVECROSSCATEGORY-SHARD1"},  # dupe series
    ]
    client = _FakeMveClient(collections, {})
    result = asyncio.run(mve_scan._get_mve_series_cache(client))
    assert result == ["KXMVECROSSCATEGORY", "KXMVECROSSCATEGORY-SHARD1"]
    assert client.collections_calls == 1


def test_get_mve_series_cache_reuses_the_cache_within_the_ttl():
    client = _FakeMveClient([{"collection_ticker": "C1", "series_ticker": "S1"}], {})
    asyncio.run(mve_scan._get_mve_series_cache(client))
    asyncio.run(mve_scan._get_mve_series_cache(client))
    assert client.collections_calls == 1  # second call served from state["mve_series_cache"]


def test_get_mve_series_cache_refetches_once_the_ttl_expires():
    client = _FakeMveClient([{"collection_ticker": "C1", "series_ticker": "S1"}], {})
    asyncio.run(mve_scan._get_mve_series_cache(client))
    state["mve_series_cache"]["fetched_at"] -= mve_scan._MVE_COLLECTIONS_CACHE_TTL_SEC + 1
    asyncio.run(mve_scan._get_mve_series_cache(client))
    assert client.collections_calls == 2


def test_get_mve_series_cache_ignores_a_collection_with_no_series_ticker():
    client = _FakeMveClient([{"collection_ticker": "C1", "series_ticker": None}], {})
    result = asyncio.run(mve_scan._get_mve_series_cache(client))
    assert result == []


# --- _event_title_fields / _market_rows_from_event ---------------------------
# (see tests/test_kalshi_contracts.py for the fixture-driven doc-field-name
# version of these two - these cover edge cases the fixture doesn't.)

def test_event_title_fields_defaults_competition_fields_to_none_when_product_metadata_missing():
    event = _mve_event("EVT-A", "KXMVECROSSCATEGORY-SHARD1")
    result = mve_scan._event_title_fields(event)
    assert result["competition"] is None
    assert result["competition_scope"] is None
    assert result["product_metadata"] == {}


def test_market_rows_from_event_is_empty_when_no_markets_created_yet():
    # Confirmed live 2026-08-30: a real fraction of multivariate events
    # carry an empty markets list even with with_nested_markets=true.
    event = _mve_event("EVT-A", "KXMVECROSSCATEGORY-SHARD1", markets=[])
    assert mve_scan._market_rows_from_event(event) == []


# --- _scan_mve_batch: the real end-to-end wiring -----------------------------

def test_scan_mve_batch_writes_catalog_rows_and_title_cache_entries():
    collections = [{"collection_ticker": "C1", "series_ticker": "KXMVECROSSCATEGORY-SHARD1"}]
    events_by_series = {
        "KXMVECROSSCATEGORY-SHARD1": {
            "events": [_mve_event(
                "MVE-EVT-A", "KXMVECROSSCATEGORY-SHARD1", mutually_exclusive=True,
                markets=[_mve_market("MVE-A", "MVE-EVT-A")],
            )],
            "cursor": "",
        },
    }
    client = _FakeMveClient(collections, events_by_series)
    cfg = {"kalshi": {"categories": ["Sports"]}}  # deliberately NOT Exotics - must not matter

    asyncio.run(mve_scan._scan_mve_batch(client, cfg))

    # market_catalog.db - discoverable via the same read path open_candidates
    # already uses for the regular scan.
    catalog_rows = market_catalog.open_candidates(min_volume=0)
    assert [r["ticker"] for r in catalog_rows] == ["MVE-A"]
    assert catalog_rows[0]["series_ticker"] == "KXMVECROSSCATEGORY-SHARD1"

    # title_cache.db - persisted, not just in-memory.
    market_titles = title_cache.load_market_titles()
    assert market_titles["MVE-A"]["event_ticker"] == "MVE-EVT-A"
    event_titles = title_cache.load_event_titles()
    assert event_titles["MVE-EVT-A"]["mutually_exclusive"] is True

    # state - the actual thing strategy_engine.evaluate() reads, updated
    # in-memory immediately (not only on the next process restart).
    assert state["market_titles"]["MVE-A"]["event_ticker"] == "MVE-EVT-A"
    assert state["event_titles"]["MVE-EVT-A"]["mutually_exclusive"] is True


def test_scan_mve_batch_runs_regardless_of_configured_categories():
    # The whole point of issue #268: MVE discovery must NOT depend on
    # kalshi.categories scope - confirmed here with categories=[] (nothing
    # configured at all).
    collections = [{"collection_ticker": "C1", "series_ticker": "KXMVECROSSCATEGORY-SHARD1"}]
    events_by_series = {
        "KXMVECROSSCATEGORY-SHARD1": {
            "events": [_mve_event("MVE-EVT-A", "KXMVECROSSCATEGORY-SHARD1", markets=[_mve_market("MVE-A", "MVE-EVT-A")])],
            "cursor": "",
        },
    }
    client = _FakeMveClient(collections, events_by_series)
    asyncio.run(mve_scan._scan_mve_batch(client, {"kalshi": {"categories": []}}))
    assert [r["ticker"] for r in market_catalog.open_candidates(min_volume=0)] == ["MVE-A"]


def test_scan_mve_batch_skips_an_event_with_no_markets_yet_without_error():
    collections = [{"collection_ticker": "C1", "series_ticker": "KXMVECROSSCATEGORY-SHARD1"}]
    events_by_series = {
        "KXMVECROSSCATEGORY-SHARD1": {"events": [_mve_event("MVE-EVT-A", "KXMVECROSSCATEGORY-SHARD1", markets=[])], "cursor": ""},
    }
    client = _FakeMveClient(collections, events_by_series)
    asyncio.run(mve_scan._scan_mve_batch(client, {"kalshi": {}}))
    assert market_catalog.open_candidates(min_volume=0) == []
    # Event-level title data is still captured even with no markets yet.
    assert "MVE-EVT-A" in state["event_titles"]


def test_scan_mve_batch_isolates_a_failing_series_from_the_rest(capsys):
    collections = [
        {"collection_ticker": "C1", "series_ticker": "KXMVECROSSCATEGORY"},
        {"collection_ticker": "C2", "series_ticker": "KXMVECROSSCATEGORY-SHARD1"},
    ]
    events_by_series = {
        "KXMVECROSSCATEGORY-SHARD1": {
            "events": [_mve_event("MVE-EVT-OK", "KXMVECROSSCATEGORY-SHARD1", markets=[_mve_market("MVE-OK", "MVE-EVT-OK")])],
            "cursor": "",
        },
    }

    class _PartiallyFailingClient(_FakeMveClient):
        async def get_multivariate_events(self, series_ticker, with_nested_markets, limit):
            if series_ticker == "KXMVECROSSCATEGORY":
                raise RuntimeError("simulated transient API error")
            return await super().get_multivariate_events(series_ticker, with_nested_markets, limit)

    client = _PartiallyFailingClient(collections, events_by_series)
    asyncio.run(mve_scan._scan_mve_batch(client, {"kalshi": {}}))

    assert [r["ticker"] for r in market_catalog.open_candidates(min_volume=0)] == ["MVE-OK"]
    assert "scan failed for 'KXMVECROSSCATEGORY'" in capsys.readouterr().out


def test_scan_mve_batch_returns_early_when_no_mve_series_are_known():
    client = _FakeMveClient([], {})
    asyncio.run(mve_scan._scan_mve_batch(client, {"kalshi": {}}))
    assert market_catalog.open_candidates(min_volume=0) == []
    assert client.events_calls == []


# --- pacing: same discipline as catalog_scan.PACE_LIMIT ---------------------

def test_scan_mve_batch_paces_concurrent_get_multivariate_events_calls():
    n_series = mve_scan._MVE_SCAN_PACE_LIMIT + 4
    collections = [{"collection_ticker": f"C{i}", "series_ticker": f"S{i}"} for i in range(n_series)]
    client = _FakeMveClient(collections, {})

    asyncio.run(mve_scan._scan_mve_batch(client, {"kalshi": {}}))

    assert client.max_in_flight <= mve_scan._MVE_SCAN_PACE_LIMIT
    assert len(client.events_calls) == n_series  # every series still reached the call, just paced


def test_mve_scan_pace_limit_is_a_small_bounded_constant():
    # Pins the intended relationship, same reasoning as catalog_scan's own
    # test_pace_limit_is_below_the_full_batch_size - if this regresses to
    # something unbounded, pacing stops doing anything.
    assert 0 < mve_scan._MVE_SCAN_PACE_LIMIT <= 10


# --- _maybe_scan_mve_batch: due()/overlap-guard scheduling -------------------

def test_maybe_scan_mve_batch_triggers_when_due_and_not_already_scanning(monkeypatch):
    supervised = []
    monkeypatch.setattr(mve_scan.task_supervisor, "supervise", lambda fn, **kw: supervised.append(kw) or "task-sentinel")

    mve_scan._maybe_scan_mve_batch({"kalshi": {}})

    assert len(supervised) == 1
    assert supervised[0]["component"] == "mve_scan"
    assert state["mve_scan"]["scanning"] is True
    assert state["mve_scan"]["task"] == "task-sentinel"


def test_maybe_scan_mve_batch_does_not_refire_before_the_interval_elapses(monkeypatch):
    supervised = []
    monkeypatch.setattr(mve_scan.task_supervisor, "supervise", lambda fn, **kw: supervised.append(kw) or "task-sentinel")
    state["mve_scan"]["last_started_at"] = time.time()  # just started

    mve_scan._maybe_scan_mve_batch({"kalshi": {}})

    assert supervised == []


def test_maybe_scan_mve_batch_does_not_overlap_a_run_already_in_flight(monkeypatch):
    supervised = []
    monkeypatch.setattr(mve_scan.task_supervisor, "supervise", lambda fn, **kw: supervised.append(kw) or "task-sentinel")
    state["mve_scan"]["scanning"] = True
    state["mve_scan"]["last_started_at"] = 0.0  # otherwise due()

    mve_scan._maybe_scan_mve_batch({"kalshi": {}})

    assert supervised == []


# --- _scan_mve_batch_background: client lifecycle + scanning-flag release --

def test_scan_mve_batch_background_closes_the_client_and_clears_scanning(monkeypatch):
    closed = []

    class _FakeGateway:
        def __init__(self, base_url, timeout):
            pass

        async def get_multivariate_event_collections(self, **kwargs):
            return []

        async def close(self):
            closed.append(True)

    monkeypatch.setattr(mve_scan, "KalshiPublicGateway", _FakeGateway)
    state["mve_scan"]["scanning"] = True

    asyncio.run(mve_scan._scan_mve_batch_background({"kalshi": {"base_url": "https://x", "request_timeout_sec": 1.0}}))

    assert closed == [True]
    assert state["mve_scan"]["scanning"] is False


def test_scan_mve_batch_background_closes_the_client_even_when_the_scan_raises(monkeypatch):
    closed = []

    class _FakeGateway:
        def __init__(self, base_url, timeout):
            pass

        async def get_multivariate_event_collections(self, **kwargs):
            raise RuntimeError("boom")

        async def close(self):
            closed.append(True)

    monkeypatch.setattr(mve_scan, "KalshiPublicGateway", _FakeGateway)

    with pytest.raises(RuntimeError):
        asyncio.run(mve_scan._scan_mve_batch_background({"kalshi": {"base_url": "https://x", "request_timeout_sec": 1.0}}))

    assert closed == [True]
    assert state["mve_scan"]["scanning"] is False
