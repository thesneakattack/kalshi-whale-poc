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
  ts/ts_ms) survives intact.
"""
from __future__ import annotations

from services.kalshi.provenance import ContractDocs

CONTRACT_DOCS: dict[str, ContractDocs] = {
    "normalize_fill": (
        "docs/kalshi/user-fills.md",
        "docs/kalshi/order_direction.md",
    ),
}


def normalize_fill(msg: dict) -> dict:
    return {
        **msg,
        "ticker": msg.get("market_ticker") or msg.get("ticker"),
    }
