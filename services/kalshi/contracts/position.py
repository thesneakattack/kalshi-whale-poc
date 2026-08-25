"""Market-position WS channel semantics — Phase A Task A10.

Semantics owned:

- The singular/plural split that already produced one real shipped bug
  (QCP Task 13, 2026-08-24): the per-message `type` is "market_position"
  (SINGULAR — docs/kalshi/market-positions.md's own schema,
  `const: market_position`), while the *subscription channel* name is
  "market_positions" (plural). The dispatch in the websocket transport
  previously matched the plural channel name against the message type, so
  on_position was never invoked for any real position update. Both
  spellings now live here, imported by the transport, so they cannot be
  confused independently again.
- market_ticker -> ticker alias (the WS message carries market_ticker;
  the REST GetPositions MarketPosition schema uses ticker — a genuine
  REST-vs-WS naming split, documented in docs/kalshi/CHEATSHEET.md).
- Raw-payload pass-through, same rule as every normalizer here.
"""
from __future__ import annotations

from dataclasses import dataclass

from services.kalshi.provenance import ContractDocs
from services.kalshi.contracts.trade import _dollars

CONTRACT_DOCS: dict[str, ContractDocs] = {
    "normalize_position": ("docs/kalshi/market-positions.md",),
    "market_position_from_ws": (
        "docs/kalshi/market-positions.md",
        "docs/kalshi/get-positions.md",
        "docs/kalshi/fixed_point_migration.md",
    ),
}

# Per-message `type` (singular) vs subscription channel (plural) - see
# module docstring; asserted distinct by tests/test_kalshi_contracts.py.
WS_MESSAGE_TYPE = "market_position"
SUBSCRIPTION_CHANNEL = "market_positions"


def normalize_position(msg: dict) -> dict:
    return {
        **msg,
        "ticker": msg.get("market_ticker") or msg.get("ticker"),
    }


@dataclass(frozen=True, slots=True)
class MarketPosition:
    """Canonical market-position contract (A12). Sign semantics per
    docs/kalshi/get-positions.md's position_fp: positive means YES
    contracts, negative means NO contracts - the exact field the real
    emergency flatten's side mapping depends on."""

    ticker: str | None
    position: float | None       # contracts, signed (from position_fp)
    position_cost: float | None  # dollars (position_cost_dollars)
    realized_pnl: float | None   # dollars (realized_pnl_dollars)
    fees_paid: float | None      # dollars (fees_paid_dollars)
    raw_payload: dict


def market_position_from_ws(msg: dict) -> MarketPosition:
    return MarketPosition(
        ticker=msg.get("market_ticker") or msg.get("ticker"),
        position=_dollars(msg.get("position_fp")),
        position_cost=_dollars(msg.get("position_cost_dollars")),
        realized_pnl=_dollars(msg.get("realized_pnl_dollars")),
        fees_paid=_dollars(msg.get("fees_paid_dollars")),
        raw_payload=msg,
    )
