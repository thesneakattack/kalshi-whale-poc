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

import pytest

from services import http_client


class _FakeApiException(Exception):
    def __init__(self, status):
        self.status = status
        super().__init__(f"status {status}")


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
