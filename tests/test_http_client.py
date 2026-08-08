"""
Verifies request_with_backoff's retry behavior in isolation - no real network
call, no real sleeping (asyncio.sleep is monkeypatched to a no-op so this
runs instantly regardless of how many retries it exercises).
"""
import asyncio

from services import http_client


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class _FakeAsyncClient:
    def __init__(self, statuses):
        self._statuses = list(statuses)
        self.calls = 0

    async def request(self, method, url, **kwargs):
        self.calls += 1
        return _FakeResponse(self._statuses.pop(0))


def _no_sleep(monkeypatch):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(http_client.asyncio, "sleep", fake_sleep)
    return sleeps


def test_returns_immediately_on_non_429(monkeypatch):
    fake = _FakeAsyncClient([200])
    monkeypatch.setattr(http_client, "get_client", lambda: fake)
    sleeps = _no_sleep(monkeypatch)

    resp = asyncio.run(http_client.request_with_backoff("GET", "https://example.test/x"))

    assert resp.status_code == 200
    assert fake.calls == 1
    assert sleeps == []  # never had to wait


def test_retries_on_429_then_succeeds(monkeypatch):
    fake = _FakeAsyncClient([429, 429, 200])
    monkeypatch.setattr(http_client, "get_client", lambda: fake)
    sleeps = _no_sleep(monkeypatch)

    resp = asyncio.run(http_client.request_with_backoff("GET", "https://example.test/x", base_delay=1.0))

    assert resp.status_code == 200
    assert fake.calls == 3
    assert len(sleeps) == 2
    assert sleeps[1] > sleeps[0]  # exponential growth, not fixed delay


def test_gives_up_after_max_retries_and_returns_the_429(monkeypatch):
    fake = _FakeAsyncClient([429, 429, 429, 429])  # 1 initial + 3 retries = max_retries=3
    monkeypatch.setattr(http_client, "get_client", lambda: fake)
    _no_sleep(monkeypatch)

    resp = asyncio.run(http_client.request_with_backoff("GET", "https://example.test/x", max_retries=3))

    assert resp.status_code == 429  # caller's raise_for_status() handles this, backoff doesn't hide it
    assert fake.calls == 4


def test_other_error_statuses_are_not_retried(monkeypatch):
    fake = _FakeAsyncClient([500])
    monkeypatch.setattr(http_client, "get_client", lambda: fake)
    sleeps = _no_sleep(monkeypatch)

    resp = asyncio.run(http_client.request_with_backoff("GET", "https://example.test/x"))

    assert resp.status_code == 500
    assert fake.calls == 1
    assert sleeps == []
