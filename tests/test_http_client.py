"""
Verifies call_with_backoff's retry behavior in isolation - no real network
call, no real sleeping (asyncio.sleep is monkeypatched to a no-op so this
runs instantly regardless of how many retries it exercises).

Rewritten 2026-08-08 when the Kalshi clients migrated to the official
kalshi_python_async SDK: the SDK's own retry support doesn't cover 429
specifically (only 5xx/connection errors), so this now wraps arbitrary async
callables (SDK client methods) instead of raw httpx requests, detecting a
429 via the SDK's exception shape (an exception exposing `.status`) rather
than an HTTP response object.
"""
import asyncio
import time

import pytest

from services import http_client


class _FakeApiException(Exception):
    def __init__(self, status):
        self.status = status
        super().__init__(f"status {status}")


@pytest.fixture(autouse=True)
def _reset_rate_limit_counter():
    # Module-level global (services/http_client.py's own single-threaded-
    # asyncio design, no lock needed) - reset around every test so one
    # test's retries can't leak into the next's assertions.
    http_client.get_and_reset_rate_limit_hits()
    yield
    http_client.get_and_reset_rate_limit_hits()


@pytest.fixture(autouse=True)
def _reset_http_metrics():
    # Module-level global (services/http_client.py's own single-threaded-
    # asyncio design, no lock needed), same reasoning as
    # _reset_rate_limit_counter above - reset around every test so one
    # test's endpoint telemetry can't leak into the next's assertions.
    http_client.http_metrics_snapshot(reset=True)
    yield
    http_client.http_metrics_snapshot(reset=True)


@pytest.fixture(autouse=True)
def _reset_kalshi_rate_limiters(monkeypatch):
    # The read/write token buckets (2026-08-15) are ALSO module-level
    # globals, draining real tokens on every call_with_backoff invocation
    # regardless of outcome - without this, tests run in file order would
    # share one slowly-draining bucket across the whole file and eventually
    # exhaust it. That alone wouldn't just make a later test flaky, it
    # would hang it: _no_sleep below mocks asyncio.sleep to a no-op, so an
    # empty bucket's own internal wait-for-refill loop never sees real time
    # pass and spins effectively forever (confirmed live: one test run hit
    # this before this fixture existed). A fresh, full-token limiter per
    # test sidesteps this entirely rather than requiring every test to
    # reason about how many tokens it's allowed to spend.
    monkeypatch.setattr(http_client, "_kalshi_read_limiter", http_client._TokenBucketRateLimiter(http_client._KALSHI_READ_RATE_PER_SEC))
    monkeypatch.setattr(http_client, "_kalshi_write_limiter", http_client._TokenBucketRateLimiter(http_client._KALSHI_WRITE_RATE_PER_SEC))


def _no_sleep(monkeypatch):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(http_client.asyncio, "sleep", fake_sleep)
    return sleeps


def test_returns_immediately_on_success(monkeypatch):
    sleeps = _no_sleep(monkeypatch)
    calls = []

    async def succeeds(x):
        calls.append(x)
        return "ok"

    result = asyncio.run(http_client.call_with_backoff(succeeds, "arg"))

    assert result == "ok"
    assert calls == ["arg"]
    assert sleeps == []  # never had to wait


def test_retries_on_429_then_succeeds(monkeypatch):
    sleeps = _no_sleep(monkeypatch)
    attempts = {"n": 0}

    async def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise _FakeApiException(429)
        return "ok"

    result = asyncio.run(http_client.call_with_backoff(flaky, base_delay=1.0))

    assert result == "ok"
    assert attempts["n"] == 3
    assert len(sleeps) == 2
    assert sleeps[1] > sleeps[0]  # exponential growth, not fixed delay


def test_gives_up_after_max_retries_and_reraises(monkeypatch):
    _no_sleep(monkeypatch)
    attempts = {"n": 0}

    async def always_429():
        attempts["n"] += 1
        raise _FakeApiException(429)

    with pytest.raises(_FakeApiException):
        asyncio.run(http_client.call_with_backoff(always_429, max_retries=3))

    assert attempts["n"] == 4  # 1 initial + 3 retries


def test_non_429_exceptions_are_not_retried(monkeypatch):
    sleeps = _no_sleep(monkeypatch)
    attempts = {"n": 0}

    async def always_500():
        attempts["n"] += 1
        raise _FakeApiException(500)

    with pytest.raises(_FakeApiException):
        asyncio.run(http_client.call_with_backoff(always_500))

    assert attempts["n"] == 1  # no retry at all
    assert sleeps == []


def test_exceptions_without_a_status_attribute_are_not_retried(monkeypatch):
    sleeps = _no_sleep(monkeypatch)
    attempts = {"n": 0}

    async def raises_plain_error():
        attempts["n"] += 1
        raise ValueError("not an API exception")

    with pytest.raises(ValueError):
        asyncio.run(http_client.call_with_backoff(raises_plain_error))

    assert attempts["n"] == 1
    assert sleeps == []


# ---- rate-limit-hit visibility (2026-08-15, hardening-and-accuracy-
# roadmap-2026-08-11.md Part 3 Item 2, real incident evidence: a genuine 502
# was hit during the phase-97 cold-start incident with no way to see it
# coming) -------------------------------------------------------------------

def test_rate_limit_hits_counted_on_429_retry(monkeypatch):
    _no_sleep(monkeypatch)
    attempts = {"n": 0}

    async def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise _FakeApiException(429)
        return "ok"

    asyncio.run(http_client.call_with_backoff(flaky, base_delay=1.0))
    assert http_client.get_and_reset_rate_limit_hits() == 2  # 2 retries, not the final success


def test_get_and_reset_rate_limit_hits_resets_to_zero(monkeypatch):
    _no_sleep(monkeypatch)

    async def always_429():
        raise _FakeApiException(429)

    with pytest.raises(_FakeApiException):
        asyncio.run(http_client.call_with_backoff(always_429, max_retries=2))
    assert http_client.get_and_reset_rate_limit_hits() == 2
    assert http_client.get_and_reset_rate_limit_hits() == 0  # already reset by the read above


def test_non_429_failures_dont_count_as_rate_limit_hits(monkeypatch):
    _no_sleep(monkeypatch)

    async def always_500():
        raise _FakeApiException(500)

    with pytest.raises(_FakeApiException):
        asyncio.run(http_client.call_with_backoff(always_500))
    assert http_client.get_and_reset_rate_limit_hits() == 0


# ---- token-bucket rate limiter (2026-08-15 direct incident: a concurrency
# Semaphore was tried first and confirmed live NOT to stop real 429 storms -
# Kalshi's rate limit is throughput-based (tokens/sec), not concurrency-
# based, per docs.kalshi.com/getting_started/rate_limits) --------------------

def test_token_bucket_allows_a_burst_up_to_its_full_size_with_no_wait():
    limiter = http_client._TokenBucketRateLimiter(rate_per_sec=5.0)

    async def drain_five():
        for _ in range(5):
            await limiter.acquire()  # starts full - none of these should need to wait

    asyncio.run(asyncio.wait_for(drain_five(), timeout=1.0))


def test_token_bucket_blocks_once_exhausted_until_real_time_passes():
    limiter = http_client._TokenBucketRateLimiter(rate_per_sec=20.0)  # 1 token every 50ms

    async def drain_then_one_more():
        for _ in range(20):
            await limiter.acquire()  # empties the bucket
        start = time.monotonic()
        await limiter.acquire()  # must wait ~1/20s for a fresh token
        return time.monotonic() - start

    waited = asyncio.run(asyncio.wait_for(drain_then_one_more(), timeout=1.0))
    assert waited > 0.02  # genuinely waited, not an instant no-op


def test_call_with_backoff_default_is_read_bucket(monkeypatch):
    _no_sleep(monkeypatch)
    read_calls = []

    async def spy_acquire():
        read_calls.append(1)

    monkeypatch.setattr(http_client._kalshi_read_limiter, "acquire", spy_acquire)

    async def succeeds():
        return "ok"

    asyncio.run(http_client.call_with_backoff(succeeds))
    assert read_calls == [1]


def test_call_with_backoff_is_write_routes_to_write_bucket(monkeypatch):
    _no_sleep(monkeypatch)
    write_calls = []

    async def spy_acquire():
        write_calls.append(1)

    monkeypatch.setattr(http_client._kalshi_write_limiter, "acquire", spy_acquire)

    async def succeeds():
        return "ok"

    asyncio.run(http_client.call_with_backoff(succeeds, is_write=True))
    assert write_calls == [1]


def test_read_and_write_buckets_are_independent():
    read_limiter = http_client._TokenBucketRateLimiter(rate_per_sec=2.0)
    write_limiter = http_client._TokenBucketRateLimiter(rate_per_sec=2.0)

    async def drain_write_only():
        await write_limiter.acquire()
        await write_limiter.acquire()  # write bucket now empty
        start = time.monotonic()
        await read_limiter.acquire()  # read bucket untouched - should be instant
        return time.monotonic() - start

    elapsed = asyncio.run(asyncio.wait_for(drain_write_only(), timeout=1.0))
    assert elapsed < 0.05  # draining write didn't block read


# ---- per-endpoint-family REST telemetry (2026-08-24, QCP Task 15 -
# docs/superpowers/plans/2026-08-24-quality-control-plane.md). Semantics
# chosen and encoded in these test names: `calls` increments once per real
# HTTP attempt (including every retried 429, matching call_with_backoff's
# own per-attempt retry loop, not once per call_with_backoff invocation);
# avg_latency_ms is averaged only over *successful* attempts, never over
# 429s or hard errors. ----------------------------------------------------


def test_successful_call_increments_calls_successes_and_records_latency(monkeypatch):
    _no_sleep(monkeypatch)

    async def succeeds():
        return "ok"

    asyncio.run(http_client.call_with_backoff(succeeds, endpoint="widgets"))

    snapshot = http_client.http_metrics_snapshot()
    assert snapshot["widgets"]["calls"] == 1
    assert snapshot["widgets"]["successes"] == 1
    assert snapshot["widgets"]["errors"] == 0
    assert snapshot["widgets"]["rate_limited"] == 0
    assert snapshot["widgets"]["avg_latency_ms"] is not None
    assert snapshot["widgets"]["avg_latency_ms"] >= 0


def test_endpoint_defaults_to_the_coro_funcs_own_name(monkeypatch):
    _no_sleep(monkeypatch)

    async def get_markets():
        return "ok"

    asyncio.run(http_client.call_with_backoff(get_markets))

    assert "get_markets" in http_client.http_metrics_snapshot()


def test_explicit_endpoint_overrides_the_coro_funcs_own_name(monkeypatch):
    _no_sleep(monkeypatch)

    async def do_get():  # the real shape of every _get_json-routed call
        return "ok"

    asyncio.run(http_client.call_with_backoff(do_get, endpoint="get_series_list"))

    snapshot = http_client.http_metrics_snapshot()
    assert "get_series_list" in snapshot
    assert "do_get" not in snapshot


def test_429_retry_then_success_counts_each_retry_as_its_own_rate_limited_attempt(monkeypatch):
    _no_sleep(monkeypatch)
    attempts = {"n": 0}

    async def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise _FakeApiException(429)
        return "ok"

    asyncio.run(http_client.call_with_backoff(flaky, base_delay=1.0, endpoint="widgets"))

    snapshot = http_client.http_metrics_snapshot()
    assert snapshot["widgets"]["calls"] == 3  # 2 rate-limited attempts + 1 success, not 1
    assert snapshot["widgets"]["rate_limited"] == 2
    assert snapshot["widgets"]["successes"] == 1


def test_429_exhausting_every_retry_still_counts_the_final_attempt_as_rate_limited_not_an_error(monkeypatch):
    _no_sleep(monkeypatch)

    async def always_429():
        raise _FakeApiException(429)

    with pytest.raises(_FakeApiException):
        asyncio.run(http_client.call_with_backoff(always_429, max_retries=2, endpoint="widgets"))

    snapshot = http_client.http_metrics_snapshot()
    assert snapshot["widgets"]["calls"] == 3  # 1 initial + 2 retries
    assert snapshot["widgets"]["rate_limited"] == 3
    assert snapshot["widgets"]["errors"] == 0
    assert snapshot["widgets"]["successes"] == 0
    assert snapshot["widgets"]["avg_latency_ms"] is None  # nothing successful to average


def test_non_429_exception_counts_as_an_error_not_rate_limited(monkeypatch):
    _no_sleep(monkeypatch)

    async def always_500():
        raise _FakeApiException(500)

    with pytest.raises(_FakeApiException):
        asyncio.run(http_client.call_with_backoff(always_500, endpoint="widgets"))

    snapshot = http_client.http_metrics_snapshot()
    assert snapshot["widgets"]["calls"] == 1
    assert snapshot["widgets"]["errors"] == 1
    assert snapshot["widgets"]["rate_limited"] == 0
    assert snapshot["widgets"]["successes"] == 0


def test_snapshot_reset_true_clears_counters_after_reading_them(monkeypatch):
    _no_sleep(monkeypatch)

    async def succeeds():
        return "ok"

    asyncio.run(http_client.call_with_backoff(succeeds, endpoint="widgets"))

    first = http_client.http_metrics_snapshot(reset=True)
    assert first["widgets"]["calls"] == 1

    second = http_client.http_metrics_snapshot(reset=True)
    assert second == {}


def test_snapshot_reset_false_leaves_counters_intact_for_a_later_read(monkeypatch):
    _no_sleep(monkeypatch)

    async def succeeds():
        return "ok"

    asyncio.run(http_client.call_with_backoff(succeeds, endpoint="widgets"))

    first = http_client.http_metrics_snapshot(reset=False)
    second = http_client.http_metrics_snapshot(reset=False)
    assert first == second
    assert second["widgets"]["calls"] == 1


def test_two_different_endpoints_are_tracked_independently(monkeypatch):
    _no_sleep(monkeypatch)

    async def succeeds():
        return "ok"

    asyncio.run(http_client.call_with_backoff(succeeds, endpoint="widgets"))
    asyncio.run(http_client.call_with_backoff(succeeds, endpoint="gadgets"))
    asyncio.run(http_client.call_with_backoff(succeeds, endpoint="widgets"))

    snapshot = http_client.http_metrics_snapshot()
    assert snapshot["widgets"]["calls"] == 2
    assert snapshot["gadgets"]["calls"] == 1


# --- REST latency decomposition by caller class (realtime data-plane I5) ---
#
# Deterministic: http_client reads its clock through the module-level
# _monotonic indirection so tests can drive it, and the limiter is replaced
# by a fake whose acquire() advances that clock - no real sleeping, and no
# patching of time.monotonic itself (asyncio's loop clock uses it).

class _FakeClock:
    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class _Err429(Exception):
    status = 429


def _install_clock(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(http_client, "_monotonic", clock.monotonic)
    monkeypatch.setattr(http_client, "_rest_class_stats", {})  # fresh per test - module-global state
    return clock


def _install_limiter(monkeypatch, clock, wait_sec: float):
    class _FakeLimiter:
        waiters = 0
        waiters_high_water = 0

        async def acquire(self):
            clock.advance(wait_sec)

        def tokens_available(self):
            return 1.0

    limiter = _FakeLimiter()
    monkeypatch.setattr(http_client, "_kalshi_read_limiter", limiter)
    return limiter


def test_limiter_wait_network_time_and_total_are_measured_separately(monkeypatch):
    clock = _install_clock(monkeypatch)
    _install_limiter(monkeypatch, clock, wait_sec=0.100)

    async def coro():
        clock.advance(0.010)
        return "ok"

    assert asyncio.run(http_client.call_with_backoff(coro)) == "ok"

    other = http_client.rest_latency_snapshot()["by_class"]["other"]
    assert (other["calls"], other["attempts"], other["rate_limited"], other["errors"]) == (1, 1, 0, 0)
    assert other["limiter_wait"]["window"]["avg_ms"] == pytest.approx(100.0)
    assert other["network"]["window"]["avg_ms"] == pytest.approx(10.0)
    assert other["backoff"]["window"]["count"] == 0
    assert other["total"]["window"]["avg_ms"] == pytest.approx(110.0)
    # The pre-existing per-endpoint number is (and stays) network-only.
    assert http_client.http_metrics_snapshot()["coro"]["avg_latency_ms"] == pytest.approx(10.0)


def test_429_backoff_sleep_and_attempts_are_accounted_per_logical_call(monkeypatch):
    clock = _install_clock(monkeypatch)
    _install_limiter(monkeypatch, clock, wait_sec=0.0)
    monkeypatch.setattr(http_client.random, "uniform", lambda a, b: 0.0)

    async def fake_sleep(seconds):
        clock.advance(seconds)

    monkeypatch.setattr(http_client.asyncio, "sleep", fake_sleep)
    calls = {"n": 0}

    async def coro():
        calls["n"] += 1
        clock.advance(0.010)
        if calls["n"] < 3:
            raise _Err429()
        return "ok"

    assert asyncio.run(http_client.call_with_backoff(coro, base_delay=0.5)) == "ok"

    c = http_client.rest_latency_snapshot()["by_class"]["other"]
    assert (c["calls"], c["attempts"], c["rate_limited"], c["errors"]) == (1, 3, 2, 0)
    assert c["network"]["window"]["count"] == 3 and c["network"]["window"]["avg_ms"] == pytest.approx(10.0)
    assert c["backoff"]["window"]["count"] == 1 and c["backoff"]["window"]["avg_ms"] == pytest.approx(1500.0)
    assert c["total"]["window"]["count"] == 1 and c["total"]["window"]["avg_ms"] == pytest.approx(1530.0)


def test_caller_class_context_manager_attributes_the_call(monkeypatch):
    clock = _install_clock(monkeypatch)
    _install_limiter(monkeypatch, clock, wait_sec=0.0)

    async def coro():
        return 1

    async def run():
        with http_client.caller_class("critical_whale"):
            await http_client.call_with_backoff(coro)
        await http_client.call_with_backoff(coro)
        return http_client.current_caller_class()

    assert asyncio.run(run()) == "other"
    by = http_client.rest_latency_snapshot()["by_class"]
    assert by["critical_whale"]["calls"] == 1 and by["other"]["calls"] == 1
    assert set(by) == {"critical_whale", "other"}  # classes never used are omitted, not fabricated


def test_unknown_caller_class_is_rejected_so_the_label_set_stays_bounded():
    with pytest.raises(ValueError):
        with http_client.caller_class("critical_whales"):
            pass
    assert set(http_client.CALLER_CLASSES) == {
        "critical_whale", "critical_position", "interactive", "background_discovery",
        "background_catalog", "background_live_status", "background_resolution", "other",
    }


def test_classify_decorator_sets_the_class_for_the_whole_coroutine(monkeypatch):
    clock = _install_clock(monkeypatch)
    _install_limiter(monkeypatch, clock, wait_sec=0.0)
    seen = []

    async def coro():
        seen.append(http_client.current_caller_class())
        return 1

    @http_client.classify("background_catalog")
    async def scan(x):
        await http_client.call_with_backoff(coro)
        return x * 2

    async def run():
        result = await scan(21)
        return result, http_client.current_caller_class()

    assert asyncio.run(run()) == (42, "other")
    assert seen == ["background_catalog"]
    assert http_client.rest_latency_snapshot()["by_class"]["background_catalog"]["calls"] == 1


def test_classify_propagates_into_gathered_child_tasks(monkeypatch):
    clock = _install_clock(monkeypatch)
    _install_limiter(monkeypatch, clock, wait_sec=0.0)

    async def coro():
        return 1

    @http_client.classify("background_live_status")
    async def fan_out():
        await asyncio.gather(http_client.call_with_backoff(coro), http_client.call_with_backoff(coro))

    asyncio.run(fan_out())
    assert http_client.rest_latency_snapshot()["by_class"]["background_live_status"]["calls"] == 2


def test_failed_logical_calls_are_counted_once_as_errors(monkeypatch):
    clock = _install_clock(monkeypatch)
    _install_limiter(monkeypatch, clock, wait_sec=0.0)

    async def fake_sleep(seconds):
        clock.advance(seconds)

    monkeypatch.setattr(http_client.asyncio, "sleep", fake_sleep)

    async def boom():
        raise RuntimeError("upstream 500")

    async def always_429():
        raise _Err429()

    with pytest.raises(RuntimeError):
        asyncio.run(http_client.call_with_backoff(boom))
    with pytest.raises(_Err429):
        asyncio.run(http_client.call_with_backoff(always_429, max_retries=2))

    c = http_client.rest_latency_snapshot()["by_class"]["other"]
    assert c["calls"] == 2 and c["errors"] == 2
    assert c["attempts"] == 1 + 3 and c["rate_limited"] == 3
    assert c["total"]["window"]["count"] == 2  # a failed call still has a measured total


def test_limiter_tracks_concurrent_waiters_and_their_high_water():
    limiter = http_client._TokenBucketRateLimiter(rate_per_sec=200.0, burst=1.0)

    async def run():
        await asyncio.gather(limiter.acquire(), limiter.acquire(), limiter.acquire())
        return limiter.waiters, limiter.waiters_high_water

    waiters_after, high_water = asyncio.run(run())
    assert waiters_after == 0
    assert high_water >= 2  # two callers queued behind the one free token


def test_snapshot_reports_limiter_depth_gauges_for_both_buckets():
    snap = http_client.rest_latency_snapshot()["limiter"]
    assert set(snap) == {"read", "write"}
    assert set(snap["read"]) == {"waiters", "waiters_high_water", "tokens"}


def test_reset_rest_latency_window_keeps_lifetime_and_high_water(monkeypatch):
    clock = _install_clock(monkeypatch)
    _install_limiter(monkeypatch, clock, wait_sec=0.05)

    async def coro():
        return 1

    asyncio.run(http_client.call_with_backoff(coro))
    http_client.reset_rest_latency_window()
    c = http_client.rest_latency_snapshot()["by_class"]["other"]
    assert c["calls"] == 1  # lifetime counters survive
    assert c["limiter_wait"]["window"]["count"] == 0
    assert c["limiter_wait"]["lifetime"]["count"] == 1
