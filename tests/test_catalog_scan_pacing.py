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


# --- pinned watchlist series get a guaranteed scan (2026-09-01 fix) -------
#
# Confirmed live: next_series_to_scan's expired-tier-always-first ranking
# can starve a pinned series indefinitely (16 days measured) whenever the
# expired tier stays persistently non-empty - a batch of size
# _CATALOG_SCAN_BATCH_SIZE fills entirely from the expired tier, so a
# pinned series sitting in the non-expired tier never wins a slot no
# matter how stale its own last scan is.

class _TrackingClient(_FakePacedClient):
    def __init__(self):
        super().__init__()
        self.series_seen: list[str] = []

    async def get_markets(self, limit, status, series_ticker):
        self.series_seen.append(series_ticker)
        return await super().get_markets(limit, status, series_ticker)


def test_scan_catalog_batch_guarantees_a_stale_pinned_series_even_when_the_expired_tier_fills_the_batch(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(market_catalog, "DB_PATH", tmp_path / "market_catalog_pin.db")
    now = time.time()
    # _CATALOG_SCAN_BATCH_SIZE filler series, all in the expired tier - real
    # markets whose only known close_time is already in the past - so the
    # normal ranking fills the whole batch from them, leaving zero slots for
    # anything else.
    filler_tickers = [f"FILL-{i}" for i in range(catalog_scan._CATALOG_SCAN_BATCH_SIZE)]
    for t in filler_tickers:
        market_catalog.upsert_markets(t, "Sports", [{
            "ticker": f"{t}-OLD", "event_ticker": f"{t}-E", "status": "closed",
            "close_time": "2020-01-01T00:00:00Z", "occurrence_datetime": None,
        }], updated_at=now)
    # KXGOLDH: real Commodities pin, never scanned, no known markets at all
    # (so it's not "expired" either - the exact live shape of the incident:
    # a series with zero rows never registers in the expired set).
    state["series_cache"] = {
        "fetched_at": now,
        "series": [{"ticker": t, "category": "Sports"} for t in filler_tickers]
        + [{"ticker": "KXGOLDH", "category": "Commodities"}],
    }
    client = _TrackingClient()
    cfg = {"kalshi": {"categories": None, "markets_watchlist": ["KXGOLDH"]}}

    asyncio.run(catalog_scan._scan_catalog_batch(client, cfg))

    assert "KXGOLDH" in client.series_seen, (
        "a stale pinned series must be scanned even when the expired tier "
        "fills the whole normal batch"
    )
    # The normal batch is untouched in size/selection - this is additive,
    # not a reordering of the general ranking.
    assert all(t in client.series_seen for t in filler_tickers)


def test_scan_catalog_batch_does_not_rescan_an_already_fresh_pinned_series(tmp_path, monkeypatch):
    monkeypatch.setattr(market_catalog, "DB_PATH", tmp_path / "market_catalog_pin2.db")
    now = time.time()
    # Same expired-tier-fills-the-batch setup as the test above, so
    # KXGOLDH's absence from series_seen can only be explained by the
    # freshness gate, not by the normal ranking happening to include it.
    filler_tickers = [f"FILL-{i}" for i in range(catalog_scan._CATALOG_SCAN_BATCH_SIZE)]
    for t in filler_tickers:
        market_catalog.upsert_markets(t, "Sports", [{
            "ticker": f"{t}-OLD", "event_ticker": f"{t}-E", "status": "closed",
            "close_time": "2020-01-01T00:00:00Z", "occurrence_datetime": None,
        }], updated_at=now)
    market_catalog.mark_scanned(["KXGOLDH"], scanned_at=now - 30)  # well inside the staleness window
    state["series_cache"] = {
        "fetched_at": now,
        "series": [{"ticker": t, "category": "Sports"} for t in filler_tickers]
        + [{"ticker": "KXGOLDH", "category": "Commodities"}],
    }
    client = _TrackingClient()
    cfg = {"kalshi": {"categories": None, "markets_watchlist": ["KXGOLDH"]}}

    asyncio.run(catalog_scan._scan_catalog_batch(client, cfg))

    assert "KXGOLDH" not in client.series_seen


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


# --- min_updated_ts delta refresh merges into the existing cache instead of
# replacing it (kalshi-category-data-completeness Task 11). This is the
# "load-bearing gotcha" the task brief called out: once get_series_list can
# return a partial (delta) response, naively assigning that partial list to
# cache["series"] would silently shrink every unchanged series out of
# _get_top_series/_scan_catalog_batch/search_markets.

def test_get_series_cache_merges_a_delta_response_instead_of_replacing(tmp_path, monkeypatch):
    # The gotcha above: a delta response must not shrink the cache down to
    # only the series that changed.
    monkeypatch.setattr(series_cache, "DB_PATH", tmp_path / "series_cache.db")
    state["series_cache"] = {
        "fetched_at": 0.0,
        # last_full_sync_at recent (final whole-branch review re-review
        # finding): without this, a missing key defaults to due for the
        # periodic full resync, which takes the unfiltered get_series_list()
        # branch instead of the delta path this test's own name and fake
        # client (min_updated_ts-keyed signature) specifically exist to
        # exercise - it was still passing either way (the merge/pop logic
        # is shared by both branches), but silently stopped testing what it
        # claims to.
        "last_full_sync_at": time.time(),
        "series": [{"ticker": "OLD-UNCHANGED", "category": "Sports", "volume_fp": "100"}],
    }

    class _FakeDeltaClient:
        async def get_series_list(self, min_updated_ts=None):
            return [{"ticker": "NEW-CHANGED", "category": "Crypto", "volume_fp": "200"}]

        async def get_series_fee_changes(self, show_historical=True):
            return []

    monkeypatch.setattr(catalog_scan, "_SERIES_CACHE_TTL_SEC", 0)  # force refresh

    result = asyncio.run(catalog_scan._get_series_cache(_FakeDeltaClient()))

    tickers = {s["ticker"] for s in result}
    assert tickers == {"OLD-UNCHANGED", "NEW-CHANGED"}  # merged, not replaced


def test_get_series_cache_removes_a_series_whose_volume_drops_to_zero_in_the_delta(tmp_path, monkeypatch):
    # Judgment call (not in the original brief): before this task, a full
    # refresh naturally self-corrected a series whose volume dropped to
    # zero, since the whole list was rebuilt from scratch every cycle. A
    # delta response that includes a now-zero-volume series (it changed, so
    # Kalshi returns it) is the same real signal, not something to silently
    # drop on the pre-merge volume filter and leave a stale, higher-volume
    # entry behind forever.
    monkeypatch.setattr(series_cache, "DB_PATH", tmp_path / "series_cache.db")
    state["series_cache"] = {
        "fetched_at": 0.0,
        # last_full_sync_at recent - same reasoning as the merge test above
        # (final whole-branch review re-review finding): keeps this test on
        # the delta path its own name specifically claims to exercise.
        "last_full_sync_at": time.time(),
        "series": [
            {"ticker": "WENT-QUIET", "category": "Sports", "volume_fp": "100",
             "last_updated_ts": "2026-08-01T00:00:00Z"},
            {"ticker": "STILL-UNCHANGED", "category": "Sports", "volume_fp": "50",
             "last_updated_ts": "2026-08-01T00:00:00Z"},
        ],
    }

    class _FakeZeroVolumeDeltaClient:
        async def get_series_list(self, min_updated_ts=None, include_product_metadata=False):
            return [{"ticker": "WENT-QUIET", "category": "Sports", "volume_fp": "0",
                      "last_updated_ts": "2026-08-31T00:00:00Z"}]

        async def get_series_fee_changes(self, show_historical=True):
            return []

    monkeypatch.setattr(catalog_scan, "_SERIES_CACHE_TTL_SEC", 0)  # force refresh

    result = asyncio.run(catalog_scan._get_series_cache(_FakeZeroVolumeDeltaClient()))

    tickers = {s["ticker"] for s in result}
    assert tickers == {"STILL-UNCHANGED"}  # WENT-QUIET removed, not left stale at volume_fp=100


def test_get_series_cache_derives_min_updated_ts_from_the_cached_last_updated_ts(tmp_path, monkeypatch):
    # The ISO-8601-vs-epoch-seconds gotcha (docs/kalshi/get-series-list.md:
    # 100-108 vs :228-231): the request param is Unix seconds (int), the
    # cached last_updated_ts is an ISO-8601 string - _series_watermark must
    # convert, and _SERIES_WATERMARK_OVERLAP_SEC subtracts a few seconds of
    # deliberate overlap (min_updated_ts is an exclusive "after" filter).
    monkeypatch.setattr(series_cache, "DB_PATH", tmp_path / "series_cache.db")
    state["series_cache"] = {
        "fetched_at": 0.0,
        # last_full_sync_at recent (final whole-branch review fix-round):
        # otherwise this fresh dict's missing key defaults to 0.0 in
        # _get_series_cache, which is always "due" for the periodic full
        # resync and would take the unfiltered get_series_list() branch
        # this test isn't exercising - seeding it recent keeps this test on
        # the delta/min_updated_ts path it specifically tests.
        "last_full_sync_at": time.time(),
        "series": [{"ticker": "K1", "category": "Sports", "volume_fp": "100",
                     "last_updated_ts": "2026-08-01T00:00:00+00:00"}],  # epoch 1785542400
    }
    captured = {}

    class _FakeWatermarkClient:
        async def get_series_list(self, min_updated_ts=None, include_product_metadata=False):
            captured["min_updated_ts"] = min_updated_ts
            captured["include_product_metadata"] = include_product_metadata
            return []

        async def get_series_fee_changes(self, show_historical=True):
            return []

    monkeypatch.setattr(catalog_scan, "_SERIES_CACHE_TTL_SEC", 0)

    asyncio.run(catalog_scan._get_series_cache(_FakeWatermarkClient()))

    expected_epoch = int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp())
    assert captured["min_updated_ts"] == expected_epoch - catalog_scan._SERIES_WATERMARK_OVERLAP_SEC
    assert isinstance(captured["min_updated_ts"], int)  # never the raw float .timestamp() - Kalshi 400s on that
    assert captured["include_product_metadata"] is True


def test_get_series_cache_first_sync_omits_min_updated_ts_entirely(tmp_path, monkeypatch):
    # A genuine first-ever sync (cache["series"] empty) must call
    # get_series_list() with zero args - the pre-Task-11 shape - not even
    # min_updated_ts=None explicitly, so every existing zero-arg fake client
    # in this file (_FakeFeeChangesClient above) keeps working unmodified.
    monkeypatch.setattr(series_cache, "DB_PATH", tmp_path / "series_cache.db")
    state["series_cache"] = {"fetched_at": 0.0, "series": []}
    calls = []

    class _FakeFirstSyncClient:
        async def get_series_list(self):
            calls.append(())
            return [{"ticker": "FIRST", "category": "Sports", "volume_fp": "1"}]

        async def get_series_fee_changes(self, show_historical=True):
            return []

    result = asyncio.run(catalog_scan._get_series_cache(_FakeFirstSyncClient()))

    assert calls == [()]
    assert [s["ticker"] for s in result] == ["FIRST"]


def test_get_series_cache_forces_a_full_resync_once_last_full_sync_at_is_overdue(tmp_path, monkeypatch):
    # Final whole-branch review finding: min_updated_ts filters on Kalshi's
    # own "metadata updated" definition, confirmed live to be decoupled
    # from trading volume (KXNCAAMBGAME: $5.9B lifetime volume_fp, 147-day-
    # stale last_updated_ts). Once a delta ever fires - which
    # series_cache.load() seeding state["series_cache"] at every process
    # start means happens on effectively every restart in production - a
    # series whose metadata stops changing would have its volume_fp frozen
    # forever with no path back to a full fetch, and a series that starts
    # at zero volume could never re-enter the cache at all. This test
    # proves the periodic full-resync trigger actually fires: even with a
    # non-empty, non-stale cache (the delta path's own precondition), an
    # overdue last_full_sync_at forces the unfiltered get_series_list()
    # call - the same shape a genuine first-ever sync uses - not a
    # min_updated_ts-filtered one.
    monkeypatch.setattr(series_cache, "DB_PATH", tmp_path / "series_cache.db")
    state["series_cache"] = {
        "fetched_at": 0.0,
        "last_full_sync_at": time.time() - catalog_scan._SERIES_CACHE_FULL_RESYNC_SEC - 1,
        "series": [{"ticker": "OLD", "category": "Sports", "volume_fp": "100",
                     "last_updated_ts": "2026-08-01T00:00:00+00:00"}],
    }
    calls = []

    class _FakeFullResyncClient:
        async def get_series_list(self, min_updated_ts=None, include_product_metadata=False):
            calls.append((min_updated_ts, include_product_metadata))
            return [{"ticker": "OLD", "category": "Sports", "volume_fp": "999"}]  # a real refreshed volume_fp

        async def get_series_fee_changes(self, show_historical=True):
            return []

    monkeypatch.setattr(catalog_scan, "_SERIES_CACHE_TTL_SEC", 0)

    result = asyncio.run(catalog_scan._get_series_cache(_FakeFullResyncClient()))

    # Unfiltered call - no min_updated_ts, matching the first-sync shape
    # exactly (not a delta-shaped {"min_updated_ts": ..., "include_product_
    # metadata": True} call).
    assert calls == [(None, False)]
    assert [s["ticker"] for s in result] == ["OLD"]
    assert result[0]["volume_fp"] == "999"  # the frozen-forever value this fix exists to prevent
    # last_full_sync_at re-stamped so the next refresh goes back to the
    # cheaper delta path, not another full fetch immediately after.
    assert state["series_cache"]["last_full_sync_at"] > time.time() - 5
