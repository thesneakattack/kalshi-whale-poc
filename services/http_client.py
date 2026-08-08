"""
One shared, connection-pooled httpx.AsyncClient for every outbound HTTP call
in the app (Kalshi market data, Kalshi account, whale-watcher feeds, Google
OAuth). Every caller used to open its own `async with httpx.AsyncClient(...)`
per request, paying a full TCP+TLS handshake each time even when hitting the
same host seconds apart. One long-lived client reuses keep-alive connections
across calls instead.
"""
import asyncio
import random

import httpx

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient()
    return _client


async def close_client():
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def request_with_backoff(
    method: str, url: str, max_retries: int = 4, base_delay: float = 0.5, **kwargs
) -> httpx.Response:
    """Kalshi's rate limiter returns 429 with no Retry-After header — their
    own docs (docs.kalshi.com/getting_started/rate_limits) say to apply
    exponential backoff on 429, so this is that, shared by every Kalshi
    client rather than each reimplementing it. Only 429 triggers a retry;
    every other status (including other 4xx/5xx) is returned immediately for
    the caller's own raise_for_status() to handle, same as before."""
    delay = base_delay
    for attempt in range(max_retries + 1):
        resp = await get_client().request(method, url, **kwargs)
        if resp.status_code != 429 or attempt == max_retries:
            return resp
        await asyncio.sleep(delay + random.uniform(0, delay * 0.25))  # jitter
        delay *= 2
    return resp  # unreachable — loop always returns on its last iteration
