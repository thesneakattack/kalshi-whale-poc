"""
Read-only, unauthenticated Kalshi API canary - Quality Control Plane
Task 14 (docs/archive/lane-6-observability-quality-safety/plans/2026-08-24-quality-control-plane.md, moved there 2026-09-06, planning-lanes migration).

Confirms two publicly documented endpoints still return the response shape
production code depends on: `GET /exchange/status` and `GET /markets`. Both
declare `security: []` at the OpenAPI spec level in docs/kalshi/
(get-exchange-status.md, get-markets.md, confirmed 2026-08-24 - not
assumed), matching services/kalshi_client.py's own docstring ("No API key
is required for any of this"). This module never calls, and must never be
extended to call, any order/position/balance/fill/portfolio endpoint - see
test_main_never_touches_account_or_order_endpoints in
tests/test_kalshi_public_canary.py, which asserts the exact URL set hit.

Only checks structural invariants Kalshi's own schema marks `required` and
that production code actually reads (services/market_watch/market_fetch.py's
_MARKET_FIELDS) - deliberately not the full required-field lists of either
schema (e.g. Market's rules_primary, price_ranges, ...), since asserting on
fields this app doesn't depend on would only add flakiness surface without
protecting anything real. Never asserts volatile values (specific tickers,
prices, categories, counts) - only presence/type/enum-membership of stable
fields, per the plan's Step 3.

HTTP is isolated behind an injectable `Fetcher` (same shape as
tools/kalshi_docs_drift.py's own Fetcher) so tests never touch the network.
Response headers are never included in the report (`_http_fetch` returns
only status + body text) - nothing to redact by construction, since public
market-data responses carry no secrets to begin with, but this keeps it
true even if the fetch layer changes later.
"""
import argparse
import json
import sys
import time
from pathlib import Path
from typing import Callable

import httpx

BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
_EXCHANGE_STATUS_URL = f"{BASE_URL}/exchange/status"
_MARKETS_URL = f"{BASE_URL}/markets"

_REQUIRED_EXCHANGE_STATUS_FIELDS = ("exchange_active", "trading_active")
_REQUIRED_MARKET_FIELDS = ("ticker", "status")
_VALID_MARKET_STATUSES = {
    "initialized", "inactive", "active", "closed", "determined", "disputed", "amended", "finalized",
}
_BODY_SAMPLE_LEN = 500

Fetcher = Callable[..., tuple[int, str]]


def _http_fetch(url: str, params: dict | None = None, timeout: float = 10.0) -> tuple[int, str]:
    try:
        resp = httpx.get(url, params=params, timeout=timeout)
    except httpx.HTTPError as exc:
        return 0, str(exc)
    return resp.status_code, resp.text


def _fail(endpoint: str, error: str, body: str) -> dict:
    return {"ok": False, "endpoint": endpoint, "error": error, "body_sample": body[:_BODY_SAMPLE_LEN]}


def check_exchange_status(fetch: Fetcher) -> dict:
    status, body = fetch(_EXCHANGE_STATUS_URL, None)
    if status != 200:
        return _fail("exchange_status", f"http {status}", body)
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        return _fail("exchange_status", f"invalid json: {exc}", body)
    if not isinstance(data, dict):
        return _fail("exchange_status", "response is not a JSON object", body)
    missing = [f for f in _REQUIRED_EXCHANGE_STATUS_FIELDS if f not in data]
    if missing:
        return _fail("exchange_status", f"missing required field(s): {missing}", body)
    bad_types = [f for f in _REQUIRED_EXCHANGE_STATUS_FIELDS if not isinstance(data[f], bool)]
    if bad_types:
        return _fail("exchange_status", f"field(s) not boolean: {bad_types}", body)
    return {"ok": True, "endpoint": "exchange_status"}


def check_markets(fetch: Fetcher) -> dict:
    status, body = fetch(_MARKETS_URL, {"limit": 10, "status": "open"})
    if status != 200:
        return _fail("markets", f"http {status}", body)
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        return _fail("markets", f"invalid json: {exc}", body)
    if not isinstance(data, dict):
        return _fail("markets", "response is not a JSON object", body)
    markets = data.get("markets")
    if not isinstance(markets, list):
        return _fail("markets", "'markets' is not a list", body)
    if not markets:
        return _fail("markets", "'markets' list is empty (status=open returned nothing)", body)
    first = markets[0]
    if not isinstance(first, dict):
        return _fail("markets", "market list item is not an object", body)
    missing = [f for f in _REQUIRED_MARKET_FIELDS if f not in first]
    if missing:
        return _fail("markets", f"missing required field(s) on market item: {missing}", body)
    if first["status"] not in _VALID_MARKET_STATUSES:
        return _fail("markets", f"unexpected status value: {first['status']!r}", body)
    return {"ok": True, "endpoint": "markets"}


def run_canary(fetch: Fetcher) -> dict:
    checks = [check_exchange_status(fetch), check_markets(fetch)]
    return {
        "ok": all(c["ok"] for c in checks),
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "checks": checks,
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m tools.kalshi_public_canary")
    parser.add_argument("--json-out", type=Path, default=None, help="write the full report as JSON to this path")
    parser.add_argument("--timeout", type=float, default=10.0, help="per-request HTTP timeout in seconds (default: 10)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    def fetch(url: str, params: dict | None = None) -> tuple[int, str]:
        return _http_fetch(url, params, timeout=args.timeout)

    report = run_canary(fetch)

    for check in report["checks"]:
        word = "OK" if check["ok"] else "FAIL"
        suffix = "" if check["ok"] else f" - {check['error']}"
        print(f"kalshi-public-canary: [{word}] {check['endpoint']}{suffix}")

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2))

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
