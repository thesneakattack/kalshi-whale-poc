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

from services.kalshi.provenance import ContractDocs

CONTRACT_DOCS: dict[str, ContractDocs] = {
    "normalize_ticker": ("docs/kalshi/market-ticker.md",),
}


def normalize_ticker(msg: dict) -> dict:
    return {
        **msg,
        "ticker": msg.get("market_ticker") or msg.get("ticker"),
    }
