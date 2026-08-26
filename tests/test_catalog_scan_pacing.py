"""services/market_watch/catalog_scan.py's internal concurrency, paced
rather than an unbounded asyncio.gather (realtime data-plane remediation
plan, P1 Task 9). Root-cause report C3: with the tick's real launch order
(catalog batch spawned before the critical asyncio.gather), a full
_CATALOG_SCAN_BATCH_SIZE-wide burst of concurrent get_markets calls delays
the critical position/account fetch behind it in the same REST bucket.
"""
import asyncio
import time

import pytest

from services.app_state import state
from services.market_catalog import market_catalog
from services.market_watch import catalog_scan


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(market_catalog, "DB_PATH", tmp_path / "market_catalog.db")
    yield


def _prime_series_cache(n):
    state["series_cache"] = {
        "fetched_at": time.time(),
        "series": [{"ticker": f"S{i}", "category": "Sports"} for i in range(n)],
    }


class _FakePacedClient:
    def __init__(self):
        self.in_flight = 0
        self.max_in_flight = 0

    async def get_markets(self, limit, status, series_ticker):
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(0.01)
        self.in_flight -= 1
        return [{"ticker": f"{series_ticker}-M1", "occurrence_datetime": None}]


def test_scan_catalog_batch_paces_concurrent_get_markets_calls():
    _prime_series_cache(catalog_scan._CATALOG_SCAN_BATCH_SIZE)
    client = _FakePacedClient()
    cfg = {"kalshi": {"categories": None}}

    asyncio.run(catalog_scan._scan_catalog_batch(client, cfg))

    assert client.max_in_flight <= catalog_scan.PACE_LIMIT


def test_pace_limit_is_below_the_full_batch_size():
    # If PACE_LIMIT ever regresses to >= the batch size, the pacing
    # semaphore stops doing anything - this pins the intended relationship
    # rather than just a magic number.
    assert catalog_scan.PACE_LIMIT < catalog_scan._CATALOG_SCAN_BATCH_SIZE
