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


async def call_with_backoff(coro_func, *args, max_retries: int = 4, base_delay: float = 0.5, **kwargs):
    """Kalshi's rate limiter returns 429 with no Retry-After header — their
    own docs (docs.kalshi.com/getting_started/rate_limits) say to apply
    exponential backoff on 429. Originally implemented as a raw-httpx request
    wrapper; rewritten 2026-08-08 to wrap an arbitrary async callable instead
    after migrating the Kalshi clients to the official SDK (kalshi_python_async) —
    the SDK's own retry support only covers 5xx/connection errors, not 429
    specifically, and there's no public way to add 429 to its retry list, so
    this still earns its place. Detects a 429 via the SDK's own exception
    shape (kalshi_python_async.exceptions.ApiException and subclasses all
    expose .status) rather than an HTTP response object. Only a 429-shaped
    exception triggers a retry; anything else propagates immediately,
    including on the final attempt."""
    delay = base_delay
    for attempt in range(max_retries + 1):
        try:
            return await coro_func(*args, **kwargs)
        except Exception as e:
            if getattr(e, "status", None) != 429 or attempt == max_retries:
                raise
            await asyncio.sleep(delay + random.uniform(0, delay * 0.25))  # jitter
            delay *= 2
