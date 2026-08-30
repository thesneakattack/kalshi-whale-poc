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

import json
import time
from datetime import datetime
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
    # Issue #266: Kalshi's own approximate answer to "how stale is the data
    # I'm reading", independent of this app's own ingest-pipeline staleness
    # (services/kalshi/websocket.py's oldest_message_age_sec) - the two
    # measure different things and are meant to be compared, never merged.
    "get_user_data_timestamp": ("docs/kalshi/get-user-data-timestamp.md",),
    # Issue #261: api_key_region_expiration_ts gates real order placement in
    # Sports/Elections/Entertainment once past. See get_api_keys' own
    # docstring for why this doesn't just .model_dump() the SDK response
    # like every other read here.
    "get_api_keys": ("docs/kalshi/get-api-keys.md",),
    # Pure derivations over the two reads above, not new wire operations -
    # still Kalshi-specific semantic interpretation (CLAUDE.md's permanent
    # semantic rule: that lives in services/kalshi/, not in a diagnostics
    # route), so they carry the same doc mapping as the read they interpret.
    "user_data_age_sec": ("docs/kalshi/get-user-data-timestamp.md",),
    "classify_api_key_attestation": ("docs/kalshi/get-api-keys.md",),
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

    async def get_user_data_timestamp(self) -> dict:
        """GET /exchange/user_data_timestamp (docs/kalshi/get-user-data-
        timestamp.md) - "an approximate indication of when the data
        reflected in this endpoint is likely as of" for GetBalance/
        GetOrder(s)/GetFills/GetPositions. Documented response is one
        required field, as_of_time (RFC3339 date-time). Read-only; costs
        one read request. This is the exchange's OWN reporting lag, not
        this app's ingest-pipeline staleness (services/kalshi/websocket.py's
        oldest_message_age_sec) - see user_data_age_sec below for turning
        this into a number directly comparable to that one; issue #266."""
        resp = await call_with_backoff(self._client.get_user_data_timestamp)
        return resp.model_dump(mode="json")

    async def get_api_keys(self) -> dict:
        """GET /api_keys (docs/kalshi/get-api-keys.md) - every API key on
        the account, plus (2026-08-27 Kalshi changelog)
        api_key_region_expiration_ts: "Once this date has passed, API keys
        are not valid for trading Sports, Elections, and Entertainment
        markets... Absent when the account has never attested" (issue
        #261).

        Deliberately does NOT just call self._client.get_api_keys and
        .model_dump() it like every other read here: the installed
        kalshi_python_async SDK (3.27.0, confirmed against the live
        container 2026-08-30) predates api_key_region_expiration_ts
        entirely - GetApiKeysResponse has no such Pydantic field, and both
        its from_dict() and model_validate() silently drop the key even
        when the raw HTTP body carries it (confirmed directly:
        GetApiKeysResponse.model_validate({"api_keys": [],
        "api_key_region_expiration_ts": 123}).model_dump() comes back with
        only api_keys - see docs/kalshi/CHEATSHEET.md's entry). Trusting
        the parsed model here would silently drop exactly the field this
        method exists to surface - the "no lossy normalization on the way
        in" fidelity rule applied to a vendored-SDK/doc version gap, not
        just a call-site choice. get_api_keys_with_http_info's
        ApiResponse.raw_data carries the real response bytes regardless of
        what the model declares, so that field is recovered from there
        instead. endpoint= keeps REST telemetry labeled by the real
        operation rather than the _with_http_info variant's own name."""
        resp = await call_with_backoff(self._client.get_api_keys_with_http_info, endpoint="get_api_keys")
        payload = resp.data.model_dump(mode="json")
        raw = json.loads(resp.raw_data)
        if "api_key_region_expiration_ts" in raw:
            payload["api_key_region_expiration_ts"] = raw["api_key_region_expiration_ts"]
        return payload


def user_data_age_sec(payload: dict, *, now: float | None = None) -> float | None:
    """now - as_of_time, in seconds - directly comparable to this app's own
    ingest-pipeline staleness (services/kalshi/websocket.py's
    oldest_message_age_sec via trade_stream.ingest_metrics()). The two
    measure different things (issue #266's "do not confuse with": this is
    the exchange's own reporting lag, not this app's pipeline) and are
    meant to sit side by side, never merged into one number. None on a
    missing/unparseable as_of_time - never a fabricated age, same ethos as
    every other diagnostic in this app that can't answer."""
    now = time.time() if now is None else now
    raw = payload.get("as_of_time")
    if not raw:
        return None
    try:
        return now - datetime.fromisoformat(raw).timestamp()
    except (ValueError, TypeError):
        return None


def classify_api_key_attestation(payload: dict, *, now: float | None = None) -> dict:
    """Derive the three-way state docs/kalshi/get-api-keys.md documents for
    api_key_region_expiration_ts: absent entirely (the account has never
    attested), a future unix-seconds timestamp (attested and active), or a
    past one (attested but lapsed - "not valid for trading Sports,
    Elections, and Entertainment markets"). Issue #261 is explicit these
    three must stay distinguishable, never collapsed into one boolean -
    "never attested" and "attestation lapsed" call for different action
    (attest for the first time vs re-attest) even though both currently
    block those categories. Pure function over get_api_keys()'s own return
    shape, kept in services/kalshi/ rather than a diagnostics route per
    CLAUDE.md's permanent semantic rule (vendor-specific field meaning
    lives in the integration boundary)."""
    now = time.time() if now is None else now
    ts = payload.get("api_key_region_expiration_ts")
    if ts is None:
        return {"status": "never_attested", "region_expiration_ts": None, "seconds_until_expiration": None}
    seconds_until = ts - now
    return {
        "status": "active" if seconds_until > 0 else "lapsed",
        "region_expiration_ts": ts,
        "seconds_until_expiration": seconds_until,
    }
