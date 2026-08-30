"""User-fill WS channel semantics — Phase A Task A10.

Semantics owned:

- Identity is `trade_id` — the real WS fill message (docs/kalshi/
  user-fills.md) has NO fill_id field at all; fill_id is the REST
  GetFills schema's name for the same value. Keying on fill_id silently
  discarded every real WS fill (QCP Task 13 finding, 2026-08-24) — this
  normalizer passes trade_id through untouched and never invents a
  fill_id.
- market_ticker -> ticker alias (REST-vs-WS naming split, same class as
  the position message's).
- Raw-payload pass-through: every documented field (side/action/
  outcome_side/book_side/purchased_side, count_fp, prices, post_position_fp,
  ts/ts_ms, exchange_index) survives intact through normalize_fill's **msg
  spread. exchange_index (2026-08-30, issue #251, Trade API 3.29.0) is now
  a required field here (docs/kalshi/user-fills.md:207) - this inventory
  omitted it even though the spread already carried it; the actual drop
  was one layer downstream, in services/position/account_positions.py's
  _FILL_FIELDS whitelist (fixed the same commit as this docstring).
"""
from __future__ import annotations

from dataclasses import dataclass

from services.kalshi.contracts.types import AS_OUTCOME_SIDE, BOOK_SIDE_TO_OUTCOME, OutcomeSide
from services.kalshi.provenance import ContractDocs
from services.kalshi.contracts.trade import _dollars

CONTRACT_DOCS: dict[str, ContractDocs] = {
    "normalize_fill": (
        "docs/kalshi/user-fills.md",
        "docs/kalshi/order_direction.md",
    ),
    "user_fill_from_ws": (
        "docs/kalshi/user-fills.md",
        "docs/kalshi/order_direction.md",
        "docs/kalshi/fixed_point_migration.md",
    ),
}

# Closed vocabularies + narrowing maps live in contracts/types.py (C2) -
# identical on Fill responses per order_direction.md, one shared copy.


def normalize_fill(msg: dict) -> dict:
    return {
        **msg,
        "ticker": msg.get("market_ticker") or msg.get("ticker"),
    }


def _fill_outcome_side(msg: dict) -> OutcomeSide | None:
    """Canonical-first direction on a fill (order_direction.md: the same
    outcome_side/book_side pair carries direction on Fill responses; the
    bare `side` field is the legacy vocabulary). Unknown values stay None
    - never guessed into a yes/no."""
    outcome = AS_OUTCOME_SIDE.get(str(msg.get("outcome_side") or "").lower())
    if outcome is not None:
        return outcome
    book = BOOK_SIDE_TO_OUTCOME.get(str(msg.get("book_side") or "").lower())
    if book is not None:
        return book
    return AS_OUTCOME_SIDE.get(str(msg.get("side") or "").lower())


@dataclass(frozen=True, slots=True)
class UserFill:
    """Canonical user-fill contract (A12). Identity is trade_id - the WS
    message has no fill_id (that name is REST-only), and inventing one
    silently discarded every real fill once already."""

    trade_id: str | None
    ticker: str | None
    outcome_side: OutcomeSide | None   # never guessed - unknown stays None
    action: str | None         # "buy" | "sell" (user-fills.md)
    count: float | None        # contracts (from count_fp)
    yes_price: float | None    # dollars/contract
    ts_ms: int | None
    raw_payload: dict


def user_fill_from_ws(msg: dict) -> UserFill:
    ts_ms = msg.get("ts_ms")
    return UserFill(
        trade_id=msg.get("trade_id"),
        ticker=msg.get("market_ticker") or msg.get("ticker"),
        outcome_side=_fill_outcome_side(msg),
        action=msg.get("action"),
        count=_dollars(msg.get("count_fp")),
        yes_price=_dollars(msg.get("yes_price_dollars")),
        ts_ms=ts_ms if isinstance(ts_ms, int) else None,
        raw_payload=msg,
    )
