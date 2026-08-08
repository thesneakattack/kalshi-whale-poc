"""
Thin wrapper around Kalshi's public (unauthenticated) market-data endpoints,
backed by Kalshi's official kalshi_python_async SDK (migrated 2026-08-08 —
see ROADMAP.md/status.html for why: a hand-rolled auth bug had gone
undetected until a real key made it testable, and the SDK removes
request-signing/schema drift as a bug class going forward). Read-only:
listing markets, fetching a market's current prices/volume, its order book,
and exchange status. No API key is required for any of this.

If you later add real order execution, that's a *separate*, authenticated
client (RSA-PSS signed requests, still via the SDK) — deliberately not part
of this file so "read market data" and "place real orders" can never be
accidentally mixed; see services/kalshi_account_client.py.

Every method here returns plain dicts (via .model_dump(mode="json")), not
the SDK's typed Pydantic objects — keeps main.py, the dashboard's JSON
responses, and the test suite unchanged; the SDK's real, verified field
names flow through as-is either way, since Pydantic's field names already
match the wire JSON.
"""
import kalshi_python_async as kpa

from services.http_client import call_with_backoff

# self.timeout is intentionally unused below except where noted. The SDK's
# read-endpoint methods (get_markets/get_market/get_market_orderbook/
# get_exchange_status) have explicit, strictly-validated signatures with no
# _request_timeout parameter and Configuration exposes no global timeout
# knob either (verified 2026-08-08 by introspecting the installed package,
# not assumed) - passing one raises a pydantic ValidationError instead of
# being silently accepted. Of the two order-write methods (in
# kalshi_account_client.py), only create_order_v2 (a **kwargs signature)
# accepts it; cancel_order_v2 has the same strict signature as the read
# methods here and rejects it too. Falls back to the SDK/aiohttp's own
# default timeout for everything here.


class KalshiClient:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self.timeout = timeout
        config = kpa.Configuration(host=base_url.rstrip("/"))
        self._client = kpa.KalshiClient(config)

    async def close(self):
        """main.py constructs a fresh KalshiClient every poll tick (so a live
        change to kalshi.base_url takes effect immediately, same as before
        this migration) - the SDK client wraps its own aiohttp session, so
        each one needs closing after use or the sessions leak over a
        long-running process."""
        await self._client.close()

    async def get_markets(self, limit: int = 20, status: str = "open") -> list[dict]:
        resp = await call_with_backoff(self._client.get_markets, limit=limit, status=status)
        return [m.model_dump(mode="json") for m in resp.markets]

    async def get_market(self, ticker: str) -> dict:
        resp = await call_with_backoff(self._client.get_market, ticker)
        return resp.market.model_dump(mode="json")

    async def get_orderbook(self, ticker: str) -> dict:
        resp = await call_with_backoff(self._client.get_market_orderbook, ticker)
        return resp.model_dump(mode="json")

    async def get_top_volume_markets(self, n: int = 8) -> list[dict]:
        markets = await self.get_markets(limit=100, status="open")
        # Kalshi's real field is volume_24h_fp (a float-shaped string), not
        # "volume" — sorting by a field that doesn't exist silently sorted
        # nothing, surfacing whatever the API happened to return first
        # (often obscure combo markets).
        markets.sort(key=lambda m: float(m.get("volume_24h_fp") or 0), reverse=True)
        return markets[:n]

    async def get_exchange_status(self) -> dict:
        """Public, unauthenticated. Real shape includes exchange_active/
        trading_active at top level plus a per-shard exchange_index_statuses
        breakdown - lets the dashboard distinguish "the exchange is closed"
        from "the strategy found nothing," which otherwise look identical
        from the outside."""
        resp = await call_with_backoff(self._client.get_exchange_status)
        return resp.model_dump(mode="json")
