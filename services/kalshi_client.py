"""
Thin wrapper around Kalshi's public (unauthenticated) market-data endpoints.
Read-only: listing markets, fetching a market's current prices/volume, and
its order book. No API key is required for any of this.

If you later add real order execution, that's a *separate*, authenticated
client (RSA-PSS signed requests) — deliberately not part of this file so
"read market data" and "place real orders" can never be accidentally mixed.
"""
from services.http_client import get_client


class KalshiClient:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def get_markets(self, limit: int = 20, status: str = "open") -> list[dict]:
        url = f"{self.base_url}/markets"
        params = {"limit": limit, "status": status}
        resp = await get_client().get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json().get("markets", [])

    async def get_market(self, ticker: str) -> dict:
        url = f"{self.base_url}/markets/{ticker}"
        resp = await get_client().get(url, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json().get("market", {})

    async def get_orderbook(self, ticker: str) -> dict:
        url = f"{self.base_url}/markets/{ticker}/orderbook"
        resp = await get_client().get(url, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    async def get_top_volume_markets(self, n: int = 8) -> list[dict]:
        markets = await self.get_markets(limit=100, status="open")
        # Kalshi's real field is volume_24h_fp (a float), not "volume" — sorting
        # by a field that doesn't exist silently sorted nothing, surfacing
        # whatever the API happened to return first (often obscure combo markets).
        markets.sort(key=lambda m: float(m.get("volume_24h_fp") or 0), reverse=True)
        return markets[:n]
