"""
Authenticated Kalshi client — YOUR real account. Separate from kalshi_client.py
(public, unauthenticated market data) on purpose, per the README: "read market
data" and "touch a real account" should never be able to get accidentally mixed
into the same file.

Auth scheme (Kalshi trade-api v2). Fixed 2026-08-07 after a real key finally
made it possible to test this for the first time — every signed request had
been getting 401 Unauthorized, on both production and demo hosts. The
message being signed omitted the "/trade-api/v2" prefix; fetching
docs.kalshi.com's own worked example (`path='/trade-api/v2/portfolio/balance'`)
confirmed the signed path must include it, contrary to what this docstring
used to claim. This was never actually load-bearing before now: nothing had
a real key to notice the read endpoints below were completely non-functional
the moment credentials existed.
  - Every request carries three headers: KALSHI-ACCESS-KEY, KALSHI-ACCESS-TIMESTAMP,
    KALSHI-ACCESS-SIGNATURE.
  - message = f"{timestamp_ms}{method.upper()}{base_path}{path}" — base_path is
    derived from base_url (e.g. "/trade-api/v2"), path is the route
    (e.g. "/portfolio/balance"), no query string.
  - signature = base64(RSA-PSS-SHA256.sign(private_key, message)), MGF1(SHA256),
    salt_length = digest length.
  - Verified working end-to-end against a real (read-only, demo-environment)
    Kalshi API key: KALSHI-ACCESS-KEY accepted, balance/positions/fills all
    returned real 200s instead of 401.

Read endpoints (balance/positions/fills/orders) are real and active as soon as
KALSHI_API_KEY_ID + KALSHI_PRIVATE_KEY_PATH are set in .env — nothing else has
to change. They only ever read; there's nothing here they could do to your
account.

Write endpoints (create_order/cancel_order) are fully implemented, not stubs —
but every call checks `trading_enabled` first and refuses unless
`kalshi_account.trading_enabled: true` in config/settings.yaml. That's the
"ready to enable" switch: flipping it doesn't require writing any code, only
deciding you're ready to.

Order schema verified against docs.kalshi.com on 2026-08-07 (previously an
open question — see ROADMAP.md/status.html). The original implementation
targeted a ticker/action/side(yes,no)/count/{yes,no}_price shape at
POST /portfolio/orders — that was Kalshi's *legacy* order surface. Current
docs (create-order-v2/cancel-order-v2) show:
  - Endpoint moved to POST /portfolio/events/orders (create) and
    DELETE /portfolio/events/orders/{order_id} (cancel) — not /portfolio/orders.
  - "action" (buy/sell) + "side" (yes/no) collapsed into a single `side`
    field: "bid" (buy YES) or "ask" (sell YES). There is no "no" value —
    selling YES and buying NO are the same trade from Kalshi's order-book
    perspective.
  - `count` and `price` are both *strings*, not numbers — count is contracts
    with 0-2 decimals (e.g. "10.00"), price is dollars with up to 6 decimals
    (e.g. "0.5600"), not the old integer-cents yes_price/no_price pair.
  - `time_in_force` and `self_trade_prevention_type` are newly *required*
    enums with no equivalent in the old shape — this implementation defaults
    them (immediate_or_cancel / taker_at_cross) but callers can override.
  - No explicit market-vs-limit `type` field exists anymore; a market-style
    fill is expressed via time_in_force="immediate_or_cancel" instead.
  - Kalshi's own docs note migration off the legacy /portfolio/orders
    endpoint "no earlier than May 6, 2026" — today is well past that, so the
    old shape this file used to send could already be rejected outright.
This was verified by fetching docs.kalshi.com's own pages directly, not
inferred from a changelog — but re-check before ever flipping
trading_enabled: true for real money regardless; Kalshi's docs can move
again, and no live order has ever actually been placed against this code.
"""
import base64
import os
import time
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from services.http_client import request_with_backoff


class KalshiAccountClient:
    def __init__(self, base_url: str, request_timeout_sec: float, trading_enabled: bool):
        self.base_url = base_url.rstrip("/")
        self.timeout = request_timeout_sec
        self.trading_enabled = trading_enabled
        # Kalshi signs the full request path *including* the API version
        # prefix (e.g. "/trade-api/v2"), not just the route after it — derive
        # it from base_url so this stays correct on production, demo, or any
        # other host, rather than hardcoding "/trade-api/v2" a second time.
        self._base_path = urlsplit(self.base_url).path

        self.key_id = os.getenv("KALSHI_API_KEY_ID", "").strip()
        key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH", "").strip()

        self._private_key = None
        self._load_error = None
        if self.key_id and key_path:
            try:
                with open(key_path, "rb") as f:
                    self._private_key = serialization.load_pem_private_key(f.read(), password=None)
            except Exception as e:
                self._load_error = str(e)

    @property
    def enabled(self) -> bool:
        return self._private_key is not None

    @property
    def status(self) -> dict:
        if self.enabled:
            return {"connected": True, "error": None}
        if self.key_id or os.getenv("KALSHI_PRIVATE_KEY_PATH", "").strip():
            # credentials were attempted but didn't load — surface why
            return {"connected": False, "error": self._load_error or "incomplete credentials"}
        return {"connected": False, "error": None}  # not configured at all — not an error

    def _sign(self, method: str, path: str) -> tuple[str, str]:
        timestamp_ms = str(int(time.time() * 1000))
        message = f"{timestamp_ms}{method.upper()}{self._base_path}{path}".encode("utf-8")
        signature = self._private_key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return timestamp_ms, base64.b64encode(signature).decode("utf-8")

    def _headers(self, method: str, path: str) -> dict:
        timestamp_ms, signature = self._sign(method, path)
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            "KALSHI-ACCESS-SIGNATURE": signature,
        }

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        if not self.enabled:
            raise RuntimeError("Kalshi account client is not configured (see .env.example)")
        resp = await request_with_backoff(
            method, f"{self.base_url}{path}", headers=self._headers(method, path), timeout=self.timeout, **kwargs
        )
        resp.raise_for_status()
        return resp.json()

    # ---- read-only: safe, active as soon as credentials load --------------

    async def get_balance(self) -> dict:
        return await self._request("GET", "/portfolio/balance")

    async def get_positions(self) -> dict:
        return await self._request("GET", "/portfolio/positions")

    async def get_fills(self, limit: int = 25) -> dict:
        return await self._request("GET", "/portfolio/fills", params={"limit": limit})

    async def get_orders(self) -> dict:
        return await self._request("GET", "/portfolio/orders")

    # ---- write: fully implemented, gated behind trading_enabled ----------

    def _require_trading_enabled(self):
        if not self.trading_enabled:
            raise PermissionError(
                "Order placement is disabled. This POC ships with "
                "kalshi_account.trading_enabled: false in config/settings.yaml on purpose — "
                "read README's safety notes (shadow mode before live) before flipping it to true."
            )

    async def create_order(
        self,
        ticker: str,
        side: str,                    # "bid" (buy YES) | "ask" (sell YES) — see module docstring
        count: str,                    # FixedPointCount string, e.g. "10.00" — contracts, 0-2 decimals
        price: str,                    # FixedPointDollars string, e.g. "0.5600" — dollars, up to 6 decimals
        time_in_force: str = "immediate_or_cancel",   # "fill_or_kill" | "good_till_canceled" | "immediate_or_cancel"
        self_trade_prevention_type: str = "taker_at_cross",   # "taker_at_cross" | "maker"
        client_order_id: str | None = None,
        expiration_time: int | None = None,     # unix seconds; pairs with time_in_force="good_till_canceled"
        post_only: bool | None = None,
        cancel_order_on_pause: bool | None = None,
        reduce_only: bool | None = None,
    ) -> dict:
        self._require_trading_enabled()
        body = {
            "ticker": ticker,
            "side": side,
            "count": count,
            "price": price,
            "time_in_force": time_in_force,
            "self_trade_prevention_type": self_trade_prevention_type,
            "client_order_id": client_order_id or f"kwp-{int(time.time() * 1000)}",
        }
        if expiration_time is not None:
            body["expiration_time"] = expiration_time
        if post_only is not None:
            body["post_only"] = post_only
        if cancel_order_on_pause is not None:
            body["cancel_order_on_pause"] = cancel_order_on_pause
        if reduce_only is not None:
            body["reduce_only"] = reduce_only
        return await self._request("POST", "/portfolio/events/orders", json=body)

    async def cancel_order(self, order_id: str) -> dict:
        self._require_trading_enabled()
        return await self._request("DELETE", f"/portfolio/events/orders/{order_id}")
