"""Canonical order request/response contracts — Phase A Task A12.

The documented V2 semantics for the two write primitives this app
actually uses (design-spec rule: no unused surface):

- CreateOrderRequest — the exact field set docs/kalshi/create-order-v2.md
  requires: BookSide `bid`/`ask` quoting everything from the YES leg
  ("bid means buy YES, ask means sell YES" — NOT the legacy yes/no or
  action/side vocabularies), FixedPointCount contract strings (2
  decimals) and fixed-point dollar price strings (up to 4 decimals) per
  docs/kalshi/fixed_point_migration.md. Construction rejects a non-v2
  side value outright — the one place the legacy vocabulary could
  silently produce a wrong-direction real order.
- CancelOrderResult — docs/kalshi/cancel-order-v2.md's documented
  response shape: {order_id, client_order_id, reduced_by}, plus the raw
  payload for anything else the wire carries (ts_ms today).

Cold path only (order placement is human-gated) — plain slots
dataclasses, no runtime validation framework.
"""
from __future__ import annotations

from typing import Any

from dataclasses import dataclass

from services.kalshi.contracts.types import BookSide
from services.kalshi.provenance import ContractDocs

CONTRACT_DOCS: dict[str, ContractDocs] = {
    "create_order_kwargs": (
        "docs/kalshi/create-order-v2.md",
        "docs/kalshi/fixed_point_migration.md",
    ),
    "cancel_order_result_from_response": ("docs/kalshi/cancel-order-v2.md",),
}

# BookSide (create-order-v2.md): the only two legal values, YES-leg
# vocabulary - the closed Literal lives in contracts/types.py (C2).
# "yes"/"no"/"buy"/"sell" are other surfaces' vocabularies and must
# never reach this endpoint; __post_init__ still enforces it at runtime
# for untyped callers (typing cannot replace the check).
_BOOK_SIDES = ("bid", "ask")


@dataclass(frozen=True, slots=True)
class CreateOrderRequest:
    ticker: str
    side: BookSide                 # "bid" (buy YES) | "ask" (sell YES)
    count: str                     # FixedPointCount string, e.g. "10.00"
    price: str                     # fixed-point dollars string, e.g. "0.5600"
    time_in_force: str = "immediate_or_cancel"
    self_trade_prevention_type: str = "taker_at_cross"
    client_order_id: str | None = None
    expiration_time: int | None = None
    post_only: bool | None = None
    cancel_order_on_pause: bool | None = None
    reduce_only: bool | None = None

    def __post_init__(self):
        if self.side not in _BOOK_SIDES:
            raise ValueError(
                f"create-order-v2 side must be one of {_BOOK_SIDES} (YES-leg book vocabulary, "
                f"docs/kalshi/create-order-v2.md) - got {self.side!r}"
            )


def create_order_kwargs(request: CreateOrderRequest) -> dict:
    """The exact create_order_v2 kwargs for this request - required fields
    always present, optionals only when set (the SDK treats explicit-None
    and omitted differently at the wire level; see services/kalshi/
    public.py's confirmed get_markets case)."""
    kwargs: dict[str, Any] = dict(
        ticker=request.ticker,
        side=request.side,
        count=request.count,
        price=request.price,
        time_in_force=request.time_in_force,
        self_trade_prevention_type=request.self_trade_prevention_type,
    )
    if request.client_order_id is not None:
        kwargs["client_order_id"] = request.client_order_id
    if request.expiration_time is not None:
        kwargs["expiration_time"] = request.expiration_time
    if request.post_only is not None:
        kwargs["post_only"] = request.post_only
    if request.cancel_order_on_pause is not None:
        kwargs["cancel_order_on_pause"] = request.cancel_order_on_pause
    if request.reduce_only is not None:
        kwargs["reduce_only"] = request.reduce_only
    return kwargs


@dataclass(frozen=True, slots=True)
class CancelOrderResult:
    order_id: str | None
    client_order_id: str | None
    reduced_by: str | None
    raw_payload: dict


def cancel_order_result_from_response(resp: dict) -> CancelOrderResult:
    return CancelOrderResult(
        order_id=resp.get("order_id"),
        client_order_id=resp.get("client_order_id"),
        reduced_by=resp.get("reduced_by"),
        raw_payload=resp,
    )
