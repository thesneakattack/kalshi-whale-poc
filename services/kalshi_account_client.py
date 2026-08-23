"""
Authenticated Kalshi client — YOUR real account. Separate from kalshi_client.py
(public, unauthenticated market data) on purpose, per the README: "read market
data" and "touch a real account" should never be able to get accidentally mixed
into the same file.

Migrated to Kalshi's official kalshi_python_async SDK 2026-08-08 (see
ROADMAP.md/status.html for the full story). Briefly: a real key made it
possible to test this file for the first time, which surfaced a real bug —
the hand-rolled RSA-PSS signing omitted the "/trade-api/v2" prefix Kalshi
requires in the signed message, so every request had been getting 401
Unauthorized. That got fixed by hand first; investigating whether to adopt
the official SDK afterward initially looked like a dead end (its published
package was stuck on a stale version whose Pydantic models rejected real
positions/fills responses), until it turned out that was a Python-version
resolution artifact — every SDK release past that stale one requires Python
3.13+ (see Dockerfile), and the real latest release matches Kalshi's current
API exactly. The SDK now owns request signing, endpoint paths, and request/
response schemas entirely; this file just wires credentials into it and
keeps the same public method signatures & dict-shaped returns the rest of
the app already expects, so main.py didn't need to change for this.

Read endpoints (balance/positions/fills/orders) are real and active as soon as
KALSHI_API_KEY_ID + KALSHI_PRIVATE_KEY_PATH are set in .env — nothing else has
to change. They only ever read; there's nothing here they could do to your
account.

Write endpoints (create_order/cancel_order) are fully implemented, not stubs —
but every call checks `trading_enabled` first and refuses unless
`kalshi_account.trading_enabled: true` in config/settings.yaml. That's the
"ready to enable" switch: flipping it doesn't require writing any code, only
deciding you're ready to. create_order's field names/types (side "bid"/"ask",
string count/price, required time_in_force/self_trade_prevention_type) were
verified two ways: against docs.kalshi.com's own worked example, and by
reading the SDK's own generated source to confirm create_order_v2() builds
exactly that shape — but re-check before ever flipping trading_enabled: true
for real money regardless; no live order has ever actually been placed
against this code.
"""
import os
import time

import kalshi_python_async as kpa
from cryptography.hazmat.primitives import serialization

from services.http_client import call_with_backoff
from services.risk_manager import RiskManager


class KalshiAccountClient:
    def __init__(
        self, base_url: str, request_timeout_sec: float, trading_enabled: bool,
        risk: RiskManager | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = request_timeout_sec
        self.trading_enabled = trading_enabled
        # Execution-layer risk enforcement (2026-08-23) - same guard
        # PaperBroker.open_position gained, mirrored here so a real-money
        # order can't be placed while the whale-follow risk tracker is
        # halted either. None (default, no risk instance wired in) is a
        # no-op, same as everywhere else this pattern is used.
        self.risk = risk

        self.key_id = os.getenv("KALSHI_API_KEY_ID", "").strip()
        key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH", "").strip()

        self._client: kpa.KalshiClient | None = None
        self._load_error = None
        if self.key_id and key_path:
            try:
                with open(key_path, "rb") as f:
                    key_bytes = f.read()
                # Validate it's actually a loadable RSA key before handing the
                # raw PEM to the SDK - a real parse error here, not a
                # confusing failure deep inside the SDK's own auth setup.
                serialization.load_pem_private_key(key_bytes, password=None)
                config = kpa.Configuration(host=self.base_url)
                config.api_key_id = self.key_id
                config.private_key_pem = key_bytes
                self._client = kpa.KalshiClient(config)
            except Exception as e:
                self._load_error = str(e)

    @property
    def enabled(self) -> bool:
        return self._client is not None

    @property
    def status(self) -> dict:
        if self.enabled:
            return {"connected": True, "error": None}
        if self.key_id or os.getenv("KALSHI_PRIVATE_KEY_PATH", "").strip():
            # credentials were attempted but didn't load — surface why
            return {"connected": False, "error": self._load_error or "incomplete credentials"}
        return {"connected": False, "error": None}  # not configured at all — not an error

    async def close(self):
        if self._client is not None:
            await self._client.close()

    # ---- read-only: safe, active as soon as credentials load --------------

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
        # (confirmed directly on get_markets - see kalshi_client.py), same
        # omit-when-unset pattern applied here defensively rather than
        # re-verifying it call by call.
        kwargs = {"limit": limit}
        if cursor is not None:
            kwargs["cursor"] = cursor
        if status is not None:
            kwargs["status"] = status
        resp = await call_with_backoff(self._client.get_orders, **kwargs)
        return resp.model_dump(mode="json")

    # ---- write: fully implemented, gated behind trading_enabled ----------

    def _require_trading_enabled(self):
        if not self.trading_enabled:
            raise PermissionError(
                "Order placement is disabled. This POC ships with "
                "kalshi_account.trading_enabled: false in config/settings.yaml on purpose — "
                "read README's safety notes (shadow mode before live) before flipping it to true."
            )

    def _require_risk_ok(self):
        # Only ever called for an OPENING order (see create_order's
        # is_closing_order param) - closing/flattening a position is
        # risk-reducing and must stay available even while halted, if not
        # more so (this is exactly what POST /api/trading/flatten-all
        # needs to do during a live halt). cancel_order isn't guarded
        # either, for the same reason.
        if self.risk is not None and self.risk.halted:
            raise PermissionError(
                f"Order placement is halted by the kill switch: {self.risk.halt_reason}"
            )

    async def create_order(
        self,
        ticker: str,
        side: str,                    # "bid" (buy YES) | "ask" (sell YES)
        count: str,                    # FixedPointCount string, e.g. "10.00" — contracts, 0-2 decimals
        price: str,                    # FixedPointDollars string, e.g. "0.5600" — dollars, up to 6 decimals
        time_in_force: str = "immediate_or_cancel",   # "fill_or_kill" | "good_till_canceled" | "immediate_or_cancel"
        self_trade_prevention_type: str = "taker_at_cross",   # "taker_at_cross" | "maker"
        client_order_id: str | None = None,
        expiration_time: int | None = None,     # unix seconds; pairs with time_in_force="good_till_canceled"
        post_only: bool | None = None,
        cancel_order_on_pause: bool | None = None,
        reduce_only: bool | None = None,
        is_closing_order: bool = False,   # True skips the risk-halt guard - see _require_risk_ok's own comment
    ) -> dict:
        self._require_trading_enabled()
        if not is_closing_order:
            self._require_risk_ok()
        kwargs = dict(
            ticker=ticker,
            side=side,
            count=count,
            price=price,
            time_in_force=time_in_force,
            self_trade_prevention_type=self_trade_prevention_type,
            client_order_id=client_order_id or f"kwp-{int(time.time() * 1000)}",
        )
        if expiration_time is not None:
            kwargs["expiration_time"] = expiration_time
        if post_only is not None:
            kwargs["post_only"] = post_only
        if cancel_order_on_pause is not None:
            kwargs["cancel_order_on_pause"] = cancel_order_on_pause
        if reduce_only is not None:
            kwargs["reduce_only"] = reduce_only
        # create_order_v2 takes **kwargs (unlike the read methods above) and
        # does accept _request_timeout - verified 2026-08-08 by reading its
        # generated source, not assumed (the read endpoints' stricter
        # signatures reject it outright).
        # A 429 here means the order was rejected before ever being
        # processed (not "processed but the response was lost"), so retrying
        # is safe - it can't produce a duplicate submission.
        resp = await call_with_backoff(
            self._client.create_order_v2, _request_timeout=self.timeout, is_write=True, **kwargs
        )
        return resp.model_dump(mode="json")

    async def flatten_all(self) -> list[dict]:
        """Closes every currently-open real market position via an
        aggressive IOC order per ticker (2026-08-23 gap-check finding: no
        "get flat immediately" path existed for the real account either -
        the paper-mode half is PaperBroker.close_all_positions). No bulk
        flatten endpoint exists on Kalshi (confirmed against docs/kalshi/ -
        order-groups/trigger-order-group only cancel resting orders, never
        touch open positions), so this is the only real path: one
        create_order call per ticker.

        Side/price mapping verified against docs/kalshi/create-order-v2.md's
        BookSide description ("this endpoint quotes everything from the
        YES side: bid means buy YES, ask means sell YES") and
        docs/kalshi/get-positions.md's position_fp description ("negative
        means NO contracts and positive means YES contracts") - NOT
        guessed from the legacy action/side vocabulary in
        docs/kalshi/order_direction.md, which uses a different vocabulary
        for a different (non-v2) surface and would give the wrong mapping
        here if followed directly. A held YES position (position_fp > 0)
        closes by SELLING yes (side="ask"); a held NO position
        (position_fp < 0) closes by BUYING yes (side="bid"), which nets
        against the held NO contracts per Kalshi's binary-market
        settlement (1 YES + 1 NO always nets to exactly $1). Price is
        pinned to the extreme end of the 1-99 cent range on each side
        (0.01 for an ask, 0.99 for a bid) so the IOC order is guaranteed to
        cross the current book rather than rest - an emergency flatten
        needs the fill, not the best price.

        Disclosed, not silently assumed: this exact call path has never
        been exercised against a real fill (trading_enabled is false by
        default and no strategy code calls create_order today - this is
        the first real caller). The side/price mapping above is grounded
        directly in the docs, same confidence level as the rest of this
        client's already-implemented, doc-verified order schema - but
        "schema is correct" and "has produced one real observed fill" are
        different claims, and only the first one is true here yet. Same
        disclosure discipline services/account_positions.py's own
        REST-vs-WS deferral already applies to unverified real-money
        paths in this codebase."""
        snapshot = await self.get_positions()
        results = []
        for pos in (snapshot.get("market_positions") or []):
            ticker = pos.get("ticker")
            position_fp = float(pos.get("position_fp") or 0)
            if not ticker or position_fp == 0:
                continue
            side = "ask" if position_fp > 0 else "bid"
            price = "0.0100" if side == "ask" else "0.9900"
            count = f"{abs(position_fp):.2f}"
            try:
                order = await self.create_order(
                    ticker=ticker, side=side, count=count, price=price,
                    time_in_force="immediate_or_cancel", is_closing_order=True,
                )
                results.append({"ticker": ticker, "position_fp": position_fp, "order": order, "error": None})
            except Exception as e:
                results.append({"ticker": ticker, "position_fp": position_fp, "order": None, "error": str(e)})
        return results

    async def cancel_order(self, order_id: str) -> dict:
        self._require_trading_enabled()
        # Unlike create_order_v2, cancel_order_v2 has an explicit (not
        # **kwargs) signature and rejects _request_timeout the same way the
        # read endpoints above do - verified 2026-08-08, not assumed.
        resp = await call_with_backoff(self._client.cancel_order_v2, order_id, is_write=True)
        return resp.model_dump(mode="json")
