"""Kalshi REST request signing (docs/kalshi/api_keys.md, docs/kalshi/
quick_start_authenticated_requests.md): every authenticated Trade API
request needs three KALSHI-ACCESS-* headers built by RSA-PSS/SHA256-signing
`timestamp + HTTP_METHOD + path_without_query`, base64-encoded.

Every existing authenticated REST call in this app goes through the
kalshi_python_async SDK (services/kalshi/account.py, services/kalshi/
orders.py), which signs internally once a signed client is built
(services/kalshi/transport.build_account_client) - this module exists
because services/index_feed/backfill.py (issue #260) needs to call the CF
Benchmarks REST passthrough (docs/kalshi/rest-passthrough.md), an endpoint
too new (Kalshi's 2026-08-27 changelog) to have a typed SDK method, so
nothing signs it automatically.

services/kalshi/websocket.py's KalshiStreamGateway._auth_headers implements
the exact same algorithm inline, against the one fixed WS handshake path -
that hot-path method is deliberately left untouched here (no refactor, no
shared-file risk on the trading/WebSocket hot path CLAUDE.md says stays
hot), but the math below is verified against the same two doc pages and
produces a signature the corresponding public key verifies for the same
inputs (tests/test_kalshi_signing.py)."""
from __future__ import annotations

import base64
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from services.kalshi.provenance import ContractDocs

CONTRACT_DOCS: dict[str, ContractDocs] = {
    "sign_request": (
        "docs/kalshi/api_keys.md",
        "docs/kalshi/quick_start_authenticated_requests.md",
    ),
}


def sign_request(
    key_id: str,
    private_key: rsa.RSAPrivateKey,
    method: str,
    path: str,
    now_ms: int | None = None,
) -> dict[str, str]:
    """The three KALSHI-ACCESS-* headers for one authenticated REST
    request. `path` is the full path from the API root (e.g.
    "/trade-api/v2/cfbenchmarks/history/values"); a trailing query string
    is stripped defensively before signing - both doc pages warn a query
    string in the signed path produces a signature mismatch, and their own
    sample code does the same `path.split('?')[0]`, so a caller does not
    have to remember to omit it separately."""
    signed_path = path.split("?", 1)[0]
    timestamp = str(now_ms if now_ms is not None else int(time.time() * 1000))
    message = f"{timestamp}{method}{signed_path}".encode("utf-8")
    signature = private_key.sign(
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
    }
