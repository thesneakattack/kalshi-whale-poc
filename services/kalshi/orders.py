"""Kalshi order-write gateway — Phase A Task A8.

The only module in the integration boundary that can touch a real account:
create_order / cancel_order vendor primitives, moved verbatim from the
legacy KalshiAccountClient. Per the design spec, the order gateway "owns
create/cancel/amend vendor primitives and preserves current write safety
checks" — so both long-standing gates moved here with the primitives and
sit directly in front of the SDK call, where they cannot be bypassed by
reaching past a facade:

- trading_enabled (kalshi_account.trading_enabled, default false, plus the
  typed in-app confirmation phrase behind POST /api/trading/enable): every
  write checks it first and refuses while disabled. main.py's enable/
  disable routes and account_positions' per-poll config re-sync assign it
  at runtime, so the gate reads the live attribute at call time — never a
  construction-time snapshot.
- risk kill switch: an OPENING order is refused while the daily-loss kill
  switch is halted; a CLOSING order (is_closing_order=True) deliberately
  bypasses it — flattening during a halt is risk-reducing (exactly what
  POST /api/trading/flatten-all must do mid-halt). cancel_order is not
  risk-gated for the same reason.

Like KalshiAccountGateway, this gateway borrows the shared signed SDK
client (lifecycle owned by the composer); the two are split so read
capability and write capability are separate objects, not separate
docstrings.
"""
from __future__ import annotations

import time

from services.kalshi.provenance import ContractDocs
from services.kalshi.transport import call_with_backoff

CONTRACT_DOCS: dict[str, ContractDocs] = {
    "create_order": (
        "docs/kalshi/create-order-v2.md",
        "docs/kalshi/fixed_point_migration.md",
    ),
    "cancel_order": ("docs/kalshi/cancel-order-v2.md",),
}


class KalshiOrderGateway:
    def __init__(self, client, *, trading_enabled: bool, request_timeout_sec: float, risk=None):
        # A signed kpa.KalshiClient (or None while unconfigured). Borrowed,
        # never closed here - see module docstring.
        self._client = client
        self.trading_enabled = trading_enabled
        self.timeout = request_timeout_sec
        # Execution-layer risk enforcement (2026-08-23) - same guard
        # PaperBroker.open_position gained, mirrored here so a real-money
        # order can't be placed while the whale-follow risk tracker is
        # halted either. None (default, no risk instance wired in) is a
        # no-op, same as everywhere else this pattern is used.
        self.risk = risk

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
        # more so. cancel_order isn't guarded either, for the same reason.
        if self.risk is not None and self.risk.halted:
            raise PermissionError(
                f"Order placement is halted by the kill switch: {self.risk.halt_reason}"
            )

    async def create_order(
        self,
        ticker: str,
        side: str,                    # "bid" (buy YES) | "ask" (sell YES) — BookSide, docs/kalshi/create-order-v2.md
        count: str,                    # FixedPointCount string, e.g. "10.00" — contracts, 0-2 decimals
        price: str,                    # FixedPointDollars string, e.g. "0.5600" — dollars, up to 4 decimals
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
        # create_order_v2 takes **kwargs (unlike the account read methods)
        # and does accept _request_timeout - verified 2026-08-08 by reading
        # its generated source, not assumed (the read endpoints' stricter
        # signatures reject it outright).
        # A 429 here means the order was rejected before ever being
        # processed (not "processed but the response was lost"), so retrying
        # is safe - it can't produce a duplicate submission.
        resp = await call_with_backoff(
            self._client.create_order_v2, _request_timeout=self.timeout, is_write=True, **kwargs
        )
        return resp.model_dump(mode="json")

    async def cancel_order(self, order_id: str) -> dict:
        self._require_trading_enabled()
        # Unlike create_order_v2, cancel_order_v2 has an explicit (not
        # **kwargs) signature and rejects _request_timeout the same way the
        # read endpoints do - verified 2026-08-08, not assumed.
        resp = await call_with_backoff(self._client.cancel_order_v2, order_id, is_write=True)
        return resp.model_dump(mode="json")
