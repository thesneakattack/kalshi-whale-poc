"""
Authenticated Kalshi client — YOUR real account. Separate from kalshi_client.py
(public, unauthenticated market data) on purpose, per the README: "read market
data" and "touch a real account" should never be able to get accidentally mixed
into the same file.

Auth scheme (Kalshi trade-api v2, confirmed against current Kalshi docs):
  - Every request carries three headers: KALSHI-ACCESS-KEY, KALSHI-ACCESS-TIMESTAMP,
    KALSHI-ACCESS-SIGNATURE.
  - message = f"{timestamp_ms}{method.upper()}{path}"  — path is the route only
    (e.g. "/portfolio/balance"), WITHOUT the "/trade-api/v2" prefix and without
    a query string.
  - signature = base64(RSA-PSS-SHA256.sign(private_key, message)), MGF1(SHA256),
    salt_length = digest length.

Read endpoints (balance/positions/fills/orders) are real and active as soon as
KALSHI_API_KEY_ID + KALSHI_PRIVATE_KEY_PATH are set in .env — nothing else has
to change. They only ever read; there's nothing here they could do to your
account.

Write endpoints (create_order/cancel_order) are fully implemented, not stubs —
but every call checks `trading_enabled` first and refuses unless
`kalshi_account.trading_enabled: true` in config/settings.yaml. That's the
"ready to enable" switch: flipping it doesn't require writing any code, only
deciding you're ready to.

One honesty flag: Kalshi has more than one order-placement API surface (this
POC targets prediction markets, not their perpetuals/margin product), and
that surface has been evolving. The request body below matches the
classic ticker/action/side/count/{yes,no}_price shape used consistently
across the read endpoints' sibling docs — re-verify it against
docs.kalshi.com's current order reference before ever setting
trading_enabled: true.
"""
import base64
import os
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from services.http_client import get_client


class KalshiAccountClient:
    def __init__(self, base_url: str, request_timeout_sec: float, trading_enabled: bool):
        self.base_url = base_url.rstrip("/")
        self.timeout = request_timeout_sec
        self.trading_enabled = trading_enabled

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
        message = f"{timestamp_ms}{method.upper()}{path}".encode("utf-8")
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
        resp = await get_client().request(
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
        action: str,       # "buy" | "sell"
        side: str,          # "yes" | "no"
        count: int,
        order_type: str = "limit",   # "limit" | "market"
        yes_price: int | None = None,   # cents, 1-99
        no_price: int | None = None,    # cents, 1-99
        client_order_id: str | None = None,
    ) -> dict:
        self._require_trading_enabled()
        body = {
            "ticker": ticker,
            "action": action,
            "side": side,
            "count": count,
            "type": order_type,
            "client_order_id": client_order_id or f"kwp-{int(time.time() * 1000)}",
        }
        if yes_price is not None:
            body["yes_price"] = yes_price
        if no_price is not None:
            body["no_price"] = no_price
        return await self._request("POST", "/portfolio/orders", json=body)

    async def cancel_order(self, order_id: str) -> dict:
        self._require_trading_enabled()
        return await self._request("DELETE", f"/portfolio/orders/{order_id}")
