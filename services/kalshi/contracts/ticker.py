"""Ticker WS channel semantics — Phase A Task A10.

The ticker channel carries market_ticker (docs/kalshi/market-ticker.md);
app consumers key everything on the normalized `ticker` name. This
normalizer owns that alias and the raw-payload pass-through — every
documented field (yes_bid/ask_dollars, *_size_fp, open_interest_fp,
volume_fp, ts_ms, ...) survives intact, same "stop shaving off fields"
rule as the trade normalizer. Price-selection policy (which field becomes
the app's latest_price) is an application concern and stays in the stream
handlers until consumer migration (A13/A14).
"""
from __future__ import annotations

from dataclasses import dataclass

from services.kalshi.provenance import ContractDocs
from services.kalshi.contracts.trade import _dollars

CONTRACT_DOCS: dict[str, ContractDocs] = {
    "normalize_ticker": ("docs/kalshi/market-ticker.md",),
    "ticker_update_from_ws": (
        "docs/kalshi/market-ticker.md",
        "docs/kalshi/fixed_point_migration.md",
    ),
}


def normalize_ticker(msg: dict) -> dict:
    return {
        **msg,
        "ticker": msg.get("market_ticker") or msg.get("ticker"),
    }


@dataclass(frozen=True, slots=True)
class TickerUpdate:
    """Canonical ticker-channel contract (A12). Only the fields app logic
    actually reads become attributes (spec rule: no unused surface);
    everything else - sizes, open interest, volume, dollar aggregates -
    stays on raw_payload for capture/research."""

    ticker: str | None
    yes_bid: float | None      # dollars (yes_bid_dollars)
    yes_ask: float | None      # dollars (yes_ask_dollars)
    price: float | None        # dollars, last traded (price_dollars)
    ts_ms: int | None
    raw_payload: dict


def ticker_update_from_ws(msg: dict) -> TickerUpdate:
    ts_ms = msg.get("ts_ms")
    return TickerUpdate(
        ticker=msg.get("market_ticker") or msg.get("ticker"),
        yes_bid=_dollars(msg.get("yes_bid_dollars")),
        yes_ask=_dollars(msg.get("yes_ask_dollars")),
        price=_dollars(msg.get("price_dollars")),
        ts_ms=ts_ms if isinstance(ts_ms, int) else None,
        raw_payload=msg,
    )
