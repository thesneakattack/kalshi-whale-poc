"""Market-position WS channel semantics — Phase A Task A10.

Semantics owned:

- The singular/plural split that already produced one real shipped bug
  (QCP Task 13, 2026-08-24): the per-message `type` is "market_position"
  (SINGULAR — docs/kalshi/market-positions.md's own schema,
  `const: market_position`), while the *subscription channel* name is
  "market_positions" (plural). The dispatch in services/kalshi_trade_ws.py
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

from services.kalshi.provenance import ContractDocs

CONTRACT_DOCS: dict[str, ContractDocs] = {
    "normalize_position": ("docs/kalshi/market-positions.md",),
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
