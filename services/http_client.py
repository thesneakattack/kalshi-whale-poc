"""
One shared, connection-pooled httpx.AsyncClient for every outbound HTTP call
in the app (Kalshi market data, Kalshi account, whale-watcher feeds, Google
OAuth). Every caller used to open its own `async with httpx.AsyncClient(...)`
per request, paying a full TCP+TLS handshake each time even when hitting the
same host seconds apart. One long-lived client reuses keep-alive connections
across calls instead.
"""
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
