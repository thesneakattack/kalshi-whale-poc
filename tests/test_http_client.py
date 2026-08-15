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
