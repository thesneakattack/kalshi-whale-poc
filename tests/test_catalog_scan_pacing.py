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
        # Total calls that actually reached get_markets - distinct from
        # max_in_flight, which stays silent about a task that never got
        # this far at all (see finding #8's own test below: a crashed
        # pace_sem.acquire() raises BEFORE get_markets is ever called, so
        # max_in_flight alone can't reveal that kind of loss).
        self.calls = 0

    async def get_markets(self, limit, status, series_ticker):
        self.calls += 1
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


# --- pacing semaphore survives multiple event loops (code-review finding #8) --
#
# asyncio.Semaphore binds lazily to whatever event loop is running the
# first time acquire() actually has to wait (contended past its initial
# value) - an uncontended acquire() never touches the loop at all. A
# module-level Semaphore singleton reused across separate asyncio.run()
# calls (each its own loop, as pytest naturally produces test-by-test)
# raises "RuntimeError: ... is bound to a different event loop" the second
# time it's genuinely contended in a DIFFERENT loop than the first -
# reproduced directly (not assumed) before this fix.
#
# Crucially, _scan_catalog_batch's own asyncio.gather(..., return_exceptions
# =True) SWALLOWS that RuntimeError as an ordinary per-series failure (the
# same path a real transient API error takes) rather than letting it
# propagate as a crash - so max_in_flight alone can't detect this: a task
# whose pace_sem.acquire() raises never reaches client.get_markets() at
# all, so it never touches in_flight/max_in_flight bookkeeping either. The
# real, observable symptom is worse than a crash: PACE_LIMIT-past series
# silently fail every single such run, printed identically to a real API
# hiccup ("scan failed for ..., will retry next tick"), with no signal
# anywhere pointing at the actual cause. This test checks the real
# signal - every series in the batch must actually reach get_markets().

def test_scan_catalog_batch_survives_a_second_contended_run_in_a_fresh_loop(tmp_path, monkeypatch):
    monkeypatch.setattr(market_catalog, "DB_PATH", tmp_path / "market_catalog_2.db")

    def _run_one_contended_batch():
        _prime_series_cache(catalog_scan._CATALOG_SCAN_BATCH_SIZE)  # > PACE_LIMIT - genuinely contends the semaphore
        client = _FakePacedClient()
        cfg = {"kalshi": {"categories": None}}
        asyncio.run(catalog_scan._scan_catalog_batch(client, cfg))
        return client

    # Each asyncio.run() call is its own fresh event loop, same process -
    # exactly the shape that crashed a module-level Semaphore singleton.
    first = _run_one_contended_batch()
    second = _run_one_contended_batch()

    assert first.max_in_flight <= catalog_scan.PACE_LIMIT
    assert second.max_in_flight <= catalog_scan.PACE_LIMIT
    # Every series in the batch must have actually reached get_markets() on
    # BOTH runs - not just "didn't crash the test process," which
    # return_exceptions=True already guaranteed even with the bug present.
    assert first.calls == catalog_scan._CATALOG_SCAN_BATCH_SIZE
    assert second.calls == catalog_scan._CATALOG_SCAN_BATCH_SIZE
