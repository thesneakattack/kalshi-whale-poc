"""Authenticated Kalshi *read* gateway — Phase A Task A8.

Balance / positions / fills / order-history reads for the real connected
account, and nothing else: per the design spec's capability boundaries, an
authenticated read object must be structurally unable to place or cancel
orders (that capability lives only in services/kalshi/orders.py, behind
its own safety gates). services/kalshi/account_client.py is the
compatibility facade composing both.

The gateway borrows an already-signed kalshi_python_async client built by
services/kalshi/transport.build_account_client — it does not construct or
close one. The account read and order write gateways deliberately share
one signed client (one aiohttp session, one auth context), so its
lifecycle is owned by the composer (today the facade; its close()), not by
either gateway. That's the one structural difference from
KalshiPublicGateway, which owns its own unauthenticated client.

Every method returns plain dicts via .model_dump(mode="json"), same as the
public gateway — the SDK's Pydantic field names already match the wire
JSON, and main.py/the dashboard consume dict shapes.
"""
from __future__ import annotations

from typing import Any

from services.kalshi.provenance import ContractDocs
from services.kalshi.transport import call_with_backoff

CONTRACT_DOCS: dict[str, ContractDocs] = {
    "get_balance": ("docs/kalshi/get-balance.md",),
    # I8 (2026-08-25): read-only account limit/cost reads so the app can
    # measure its real budget instead of assuming it (docs/kalshi/
    # rate_limits.md: "query ... from the documented account endpoints").
    "get_api_limits": ("docs/kalshi/get-account-api-limits.md", "docs/kalshi/rate_limits.md"),
    "get_endpoint_costs": ("docs/kalshi/list-non-default-endpoint-costs.md", "docs/kalshi/rate_limits.md"),
    "get_positions": (
        "docs/kalshi/get-positions.md",
        "docs/kalshi/fixed_point_migration.md",
    ),
    "get_fills": (
        "docs/kalshi/get-fills.md",
        "docs/kalshi/order_direction.md",
    ),
    "get_orders": ("docs/kalshi/get-orders.md",),
}


class KalshiAccountGateway:
    """Read-only view of the real account. Active as soon as credentials
    load; nothing here can modify the account."""

    def __init__(self, client):
        # A signed kpa.KalshiClient (or None while unconfigured). Borrowed,
        # never closed here - see module docstring.
        self._client = client

    async def get_api_limits(self) -> dict:
        """GET /account/limits - the authenticated user's usage tier and
        read/write token buckets (refill_rate tokens/s, bucket_capacity).
        Read-only; costs one read request."""
        resp = await call_with_backoff(self._client.get_account_api_limits)
        return resp.model_dump(mode="json")

    async def get_endpoint_costs(self) -> dict:
        """GET /account/endpoint_costs - default_cost plus every endpoint
        priced differently ({method, path, cost}). Read-only."""
        resp = await call_with_backoff(self._client.get_account_endpoint_costs)
        return resp.model_dump(mode="json")

    async def get_balance(self) -> dict:
        resp = await call_with_backoff(self._client.get_balance)
        return resp.model_dump(mode="json")

    async def get_positions(self) -> dict:
        resp = await call_with_backoff(self._client.get_positions)
        return resp.model_dump(mode="json")

    async def get_fills(self, limit: int = 25) -> dict:
        resp = await call_with_backoff(self._client.get_fills, limit=limit)
        return resp.model_dump(mode="json")

    async def get_orders(self, limit: int = 25, cursor: str | None = None, status: str | None = None) -> dict:
        # Only pass cursor/status through when actually set - explicitly
        # passing an unset optional as None vs omitting the kwarg entirely
        # changes results at the wire level for this SDK on other calls
        # (confirmed directly on get_markets - see services/kalshi/public.py),
        # same omit-when-unset pattern applied here defensively rather than
        # re-verifying it call by call.
        kwargs: dict[str, Any] = {"limit": limit}
        if cursor is not None:
            kwargs["cursor"] = cursor
        if status is not None:
            kwargs["status"] = status
        resp = await call_with_backoff(self._client.get_orders, **kwargs)
        return resp.model_dump(mode="json")
