"""
Authenticated Kalshi client — YOUR real account. Compatibility facade over
the integration boundary's split account gateways (Phase A Task A8):

- services/kalshi/account.py  — KalshiAccountGateway: balance/positions/
  fills/order-history READS. Structurally cannot place or cancel orders.
- services/kalshi/orders.py   — KalshiOrderGateway: create/cancel WRITE
  primitives, carrying the trading_enabled gate and the risk kill-switch
  guard directly in front of the SDK calls.

Separate from kalshi_client.py (public, unauthenticated market data) on
purpose, per the README: "read market data" and "touch a real account"
should never be able to get accidentally mixed into the same file — and
since A8, "read the account" and "write to the account" are separate
objects too, not just separate docstrings.

This facade keeps the public surface main.py/services.app_state already
wire in: env credential loading (KALSHI_API_KEY_ID +
KALSHI_PRIVATE_KEY_PATH), .status/.enabled, close(), the same read/write
method signatures, and flatten_all (high-level execution policy — moves
above the adapter at Task A9). The two gateways deliberately share one
signed SDK client (one aiohttp session, one auth context); the facade owns
its lifecycle.

Runtime mutation contract: main.py's enable/disable routes and
account_positions' per-poll config re-sync assign `account.trading_enabled`
(and tests inject `account._client`) at runtime. Those names are properties
proxying the gateways, so a facade-level assignment reaches the object that
actually gates the SDK call — never a stale construction-time copy.

History (pre-A8, still true): migrated to Kalshi's official
kalshi_python_async SDK 2026-08-08 after the hand-rolled RSA-PSS signing
omitted the "/trade-api/v2" prefix in the signed message (every request
401'd). The SDK owns request signing, endpoint paths, and request/response
schemas; create_order's v2 field names/types (side "bid"/"ask", string
count/price, required time_in_force/self_trade_prevention_type) were
verified against docs.kalshi.com's worked example AND the SDK's generated
source — but re-check before ever flipping trading_enabled: true for real
money; no live order has ever actually been placed against this code.
"""
import os

from services.kalshi import transport
from services.kalshi.account import KalshiAccountGateway
from services.kalshi.orders import KalshiOrderGateway
from services.risk_manager import RiskManager


class KalshiAccountClient:
    def __init__(
        self, base_url: str, request_timeout_sec: float, trading_enabled: bool,
        risk: RiskManager | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = request_timeout_sec

        self.key_id = os.getenv("KALSHI_API_KEY_ID", "").strip()
        key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH", "").strip()

        # Gateways first, unconfigured - the _client property below fans a
        # facade-level assignment out to both, so construction goes through
        # the same one path runtime injection does (and close() provably
        # closes what __init__ opened - the resource-lifecycle scanner
        # tracks the self._client assignment/close pairing).
        self._reads = KalshiAccountGateway(None)
        self._writes = KalshiOrderGateway(
            None, trading_enabled=trading_enabled,
            request_timeout_sec=request_timeout_sec, risk=risk,
        )

        self._load_error = None
        if self.key_id and key_path:
            try:
                with open(key_path, "rb") as f:
                    key_bytes = f.read()
                # Signed-client construction (PEM validation included) is
                # owned by the integration boundary (services/kalshi/
                # transport.py, Phase A Task A5); this facade keeps the
                # env/file handling and error capture.
                self._client = transport.build_account_client(self.base_url, self.key_id, key_bytes)
            except Exception as e:
                self._load_error = str(e)

    # ---- live proxies: facade assignments must reach the gateways ---------

    @property
    def _client(self):
        return self._reads._client

    @_client.setter
    def _client(self, value):
        # Tests (this repo's own and test_trading_gate.py) simulate a
        # connected/disconnected account by assigning account._client — one
        # assignment must reach both gateways or half the facade keeps
        # using a stale client.
        self._reads._client = value
        self._writes._client = value

    @property
    def trading_enabled(self) -> bool:
        return self._writes.trading_enabled

    @trading_enabled.setter
    def trading_enabled(self, value: bool):
        self._writes.trading_enabled = value

    @property
    def risk(self) -> RiskManager | None:
        return self._writes.risk

    @risk.setter
    def risk(self, value: RiskManager | None):
        self._writes.risk = value

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
        return await self._reads.get_balance()

    async def get_positions(self) -> dict:
        return await self._reads.get_positions()

    async def get_fills(self, limit: int = 25) -> dict:
        return await self._reads.get_fills(limit=limit)

    async def get_orders(self, limit: int = 25, cursor: str | None = None, status: str | None = None) -> dict:
        return await self._reads.get_orders(limit=limit, cursor=cursor, status=status)

    # ---- write: fully implemented, gated inside KalshiOrderGateway --------

    async def create_order(self, *args, **kwargs) -> dict:
        # Signature (and the trading_enabled/risk gates) live on
        # services/kalshi/orders.py's KalshiOrderGateway.create_order —
        # the facade adds nothing between callers and the gated primitive.
        return await self._writes.create_order(*args, **kwargs)

    async def cancel_order(self, order_id: str) -> dict:
        return await self._writes.cancel_order(order_id)

    # ---- execution policy (moves above the adapter at Task A9) ------------

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
