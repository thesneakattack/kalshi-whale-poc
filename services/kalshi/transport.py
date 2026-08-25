"""Vendor SDK-client construction and transport access for the Kalshi
integration boundary (Phase A Task A5).

Owns the kalshi_python_async construction glue both legacy wrappers used to
build inline — an unauthenticated client for public market data, and a
signed client (API key id + validated RSA private key) for the real
account — so later boundary modules (public.py at A6, account.py/orders.py
at A8) and the compatibility facades share exactly one implementation.

Deliberately does NOT own the runtime transport mechanics themselves:
services/http_client.py remains the single shared connection pool, token-
bucket rate limiters, 429-backoff loop, and REST telemetry (per the design
spec: "Do not duplicate connection pools or rate limiters"). This module
re-exports call_with_backoff so boundary modules import transport concerns
from the boundary, but there is one retry/limiter/telemetry stack in the
process, not two.
"""
from __future__ import annotations

import kalshi_python_async as kpa
from cryptography.hazmat.primitives import serialization

from services import http_client
from services.kalshi.provenance import ContractDocs

# One shared backoff/limiter/telemetry implementation - a re-exported name,
# not a wrapper, so `transport.call_with_backoff is
# http_client.call_with_backoff` holds and no second retry stack can drift
# into existence behind the boundary.
call_with_backoff = http_client.call_with_backoff

CONTRACT_DOCS: dict[str, ContractDocs] = {
    "build_public_client": (
        "docs/kalshi/api_environments.md",
    ),
    "build_account_client": (
        "docs/kalshi/api_environments.md",
        "docs/kalshi/api_keys.md",
        "docs/kalshi/quick_start_authenticated_requests.md",
    ),
}


def build_public_client(base_url: str) -> kpa.KalshiClient:
    """An unauthenticated SDK client for public market-data endpoints.
    Public reads require no credentials (docs/kalshi/
    quick_start_market_data.md); base URL per docs/kalshi/
    api_environments.md. The caller owns the returned client's lifecycle -
    it wraps an aiohttp session that must be close()d after use."""
    return kpa.KalshiClient(kpa.Configuration(host=base_url.rstrip("/")))


def build_account_client(base_url: str, key_id: str, private_key_pem: bytes) -> kpa.KalshiClient:
    """A signed SDK client for the real account (API Key ID + RSA private
    key, docs/kalshi/api_keys.md / quick_start_authenticated_requests.md).

    Validates the PEM is actually a loadable private key before handing it
    to the SDK - a real parse error here (ValueError from cryptography),
    not a confusing failure deep inside the SDK's own auth setup; same
    behavior the account wrapper always had inline. The caller owns the
    returned client's lifecycle."""
    serialization.load_pem_private_key(private_key_pem, password=None)
    config = kpa.Configuration(host=base_url.rstrip("/"))
    config.api_key_id = key_id
    config.private_key_pem = private_key_pem
    return kpa.KalshiClient(config)
