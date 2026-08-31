"""services/market_watch/catalog_scan.py's internal concurrency, paced
rather than an unbounded asyncio.gather (realtime data-plane remediation
plan, P1 Task 9). Root-cause report C3: with the tick's real launch order
(catalog batch spawned before the critical asyncio.gather), a full
_CATALOG_SCAN_BATCH_SIZE-wide burst of concurrent get_markets calls delays
the critical position/account fetch behind it in the same REST bucket.
"""
import asyncio
import time
from datetime import datetime, timezone

import pytest

from services import series_cache
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


# --- fee-changes bulk call folded into _get_series_cache (kalshi-category-
# data-completeness Task 2) - docs/kalshi/get-series-fee-changes.md's
# SeriesFeeChange.scheduled_ts is `type: string, format: date-time`
# (ISO-8601), not an epoch number - confirmed against the installed SDK's
# own model (scheduled_ts: datetime), which model_dump(mode="json")
# re-serializes back to an ISO-8601 string. _get_series_cache._utcnow is a
# thin, monkeypatchable wrapper so these tests can fix "now" without
# patching the stdlib clock. _prime_series_cache isn't reused here since
# these tests need _get_series_cache's real fetch path (a forced refresh),
# not a pre-seeded cache.

class _FakeFeeChangesClient:
    def __init__(self, series, fee_changes):
        self._series = series
        self._fee_changes = fee_changes

    async def get_series_list(self):
        return self._series

    async def get_series_fee_changes(self, show_historical=True):
        return self._fee_changes


def test_get_series_cache_applies_the_most_recently_scheduled_fee_change(tmp_path, monkeypatch):
    monkeypatch.setattr(series_cache, "DB_PATH", tmp_path / "series_cache.db")
    state["series_cache"] = {"fetched_at": 0.0, "series": []}  # force a refresh
    series = [{"ticker": "K1", "category": "Sports", "volume_fp": "100",
               "fee_type": "flat", "fee_multiplier": 0.5}]  # raw Series-object base fee
    fee_changes = [
        {"series_ticker": "K1", "fee_type": "quadratic", "fee_multiplier": 1.0,
         "scheduled_ts": "1970-01-01T00:16:40+00:00"},  # epoch 1000
        {"series_ticker": "K1", "fee_type": "flat", "fee_multiplier": 2.0,
         "scheduled_ts": "1970-01-01T00:33:20+00:00"},  # epoch 2000, more recent, still <= now (epoch 3000)
    ]
    client = _FakeFeeChangesClient(series, fee_changes)
    fixed_now = datetime.fromtimestamp(3000, tz=timezone.utc)
    monkeypatch.setattr(catalog_scan, "_utcnow", lambda: fixed_now)  # not time.time() - scheduled_ts is ISO-8601, not epoch

    result = asyncio.run(catalog_scan._get_series_cache(client))

    assert result[0]["fee_type"] == "flat"
    assert result[0]["fee_multiplier"] == 2.0  # scheduled_ts epoch 2000 wins over 1000, not creation order


def test_get_series_cache_keeps_raw_fee_when_ticker_absent_from_fee_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(series_cache, "DB_PATH", tmp_path / "series_cache.db")
    state["series_cache"] = {"fetched_at": 0.0, "series": []}
    series = [{"ticker": "K2", "category": "Sports", "volume_fp": "100",
               "fee_type": "quadratic", "fee_multiplier": 1.0}]
    client = _FakeFeeChangesClient(series, fee_changes=[])  # K2 never appears
    monkeypatch.setattr(catalog_scan, "_utcnow", lambda: datetime.fromtimestamp(3000, tz=timezone.utc))

    result = asyncio.run(catalog_scan._get_series_cache(client))

    assert result[0]["fee_type"] == "quadratic"
    assert result[0]["fee_multiplier"] == 1.0


def test_get_series_cache_excludes_a_fee_change_scheduled_in_the_future(tmp_path, monkeypatch):
    # A change whose scheduled_ts is still ahead of "now" hasn't taken
    # effect yet - the raw (currently-real) fee must be kept, not the
    # not-yet-effective future value.
    monkeypatch.setattr(series_cache, "DB_PATH", tmp_path / "series_cache.db")
    state["series_cache"] = {"fetched_at": 0.0, "series": []}
    series = [{"ticker": "K3", "category": "Sports", "volume_fp": "100",
               "fee_type": "flat", "fee_multiplier": 0.5}]
    fee_changes = [
        {"id": "1", "series_ticker": "K3", "fee_type": "quadratic", "fee_multiplier": 9.0,
         "scheduled_ts": "1970-01-01T01:00:00+00:00"},  # epoch 3600, AFTER now (epoch 3000)
    ]
    client = _FakeFeeChangesClient(series, fee_changes)
    monkeypatch.setattr(catalog_scan, "_utcnow", lambda: datetime.fromtimestamp(3000, tz=timezone.utc))

    result = asyncio.run(catalog_scan._get_series_cache(client))

    assert result[0]["fee_type"] == "flat"
    assert result[0]["fee_multiplier"] == 0.5


def test_get_series_cache_breaks_a_scheduled_ts_tie_by_id(tmp_path, monkeypatch):
    # Two entries scheduled at the exact same instant - spec Sec1.8's own
    # tie-break rule (by id) must pick one deterministically rather than
    # depending on array order.
    monkeypatch.setattr(series_cache, "DB_PATH", tmp_path / "series_cache.db")
    state["series_cache"] = {"fetched_at": 0.0, "series": []}
    series = [{"ticker": "K4", "category": "Sports", "volume_fp": "100",
               "fee_type": "flat", "fee_multiplier": 0.5}]
    same_ts = "1970-01-01T00:16:40+00:00"  # epoch 1000, tied on both entries
    fee_changes = [
        {"id": "a", "series_ticker": "K4", "fee_type": "quadratic", "fee_multiplier": 1.0,
         "scheduled_ts": same_ts},
        {"id": "b", "series_ticker": "K4", "fee_type": "flat", "fee_multiplier": 2.0,
         "scheduled_ts": same_ts},
    ]
    client = _FakeFeeChangesClient(series, fee_changes)
    monkeypatch.setattr(catalog_scan, "_utcnow", lambda: datetime.fromtimestamp(3000, tz=timezone.utc))

    result = asyncio.run(catalog_scan._get_series_cache(client))

    # "b" > "a" lexically - the greater id wins the tie, deterministically.
    assert result[0]["fee_type"] == "flat"
    assert result[0]["fee_multiplier"] == 2.0


def test_get_series_cache_tie_break_survives_a_missing_id(tmp_path, monkeypatch):
    # Regression guard (found during self-review, not in the original
    # brief): a same-scheduled_ts tie where one entry's id is missing
    # entirely used to crash - (None, str) aren't mutually orderable in a
    # tuple comparison - which would have aborted the WHOLE series-cache
    # refresh (every series, not just this one) rather than just this
    # ticker's fee resolution.
    monkeypatch.setattr(series_cache, "DB_PATH", tmp_path / "series_cache.db")
    state["series_cache"] = {"fetched_at": 0.0, "series": []}
    series = [{"ticker": "K5", "category": "Sports", "volume_fp": "100",
               "fee_type": "flat", "fee_multiplier": 0.5}]
    same_ts = "1970-01-01T00:16:40+00:00"
    fee_changes = [
        {"series_ticker": "K5", "fee_type": "quadratic", "fee_multiplier": 1.0,
         "scheduled_ts": same_ts},  # id missing entirely
        {"id": "b", "series_ticker": "K5", "fee_type": "flat", "fee_multiplier": 2.0,
         "scheduled_ts": same_ts},
    ]
    client = _FakeFeeChangesClient(series, fee_changes)
    monkeypatch.setattr(catalog_scan, "_utcnow", lambda: datetime.fromtimestamp(3000, tz=timezone.utc))

    result = asyncio.run(catalog_scan._get_series_cache(client))  # must not raise

    # missing id defaults to "" < "b", so the real-id entry wins the tie.
    assert result[0]["fee_type"] == "flat"
    assert result[0]["fee_multiplier"] == 2.0
