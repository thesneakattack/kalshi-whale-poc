"""Public-trade WS channel semantics — Phase A Task A10.

The one implementation of trade-message normalization, moved verbatim from
services/kalshi_trade_ws.py (which now delegates its compatibility
staticmethod here). Semantics owned:

- market_ticker -> ticker alias (WS carries market_ticker; app consumers
  read the normalized ticker key).
- Direction precedence: taker_outcome_side FIRST, deprecated taker_side
  only as fallback (docs/kalshi/get-trades.md marks taker_side deprecated
  — "will not be removed before May 14, 2026", a guarantee that has now
  expired — and names taker_outcome_side/taker_book_side the canonical way
  to determine trade direction). An unreadable direction stays None —
  never guessed (the pre-2026-08-17 code turned every unreadable trade
  into a confident "no": wrong direction AND wrong notional, silently, on
  every signal).
- created_time derivation from ts_ms (exchange epoch millis -> ISO UTC).
- Raw-payload preservation: **msg passes EVERYTHING through, overlay keys
  win (2026-08-17, direct and repeated instruction: "I keep insisting
  that you stop shaving off fields and values from the various shapes you
  get but you persist"). A field Kalshi adds tomorrow arrives intact,
  reaches series_watcher's raw store, and is queryable the day it appears.

The whale provider's own richer direction resolution (book-side bid≡yes/
ask≡no fallback, services/whalewatchers/kalshi_trade_tape.py::_taker_side)
migrates behind this boundary at Task A13, not here.
"""
from __future__ import annotations

from datetime import datetime, timezone

from services.kalshi.provenance import ContractDocs

CONTRACT_DOCS: dict[str, ContractDocs] = {
    "normalize_trade": (
        "docs/kalshi/public-trades.md",
        "docs/kalshi/get-trades.md",
        "docs/kalshi/order_direction.md",
    ),
}


def normalize_trade(msg: dict) -> dict:
    ts_ms = msg.get("ts_ms")
    created_time = None
    if ts_ms is not None:
        try:
            created_time = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        except (TypeError, ValueError, OSError):
            created_time = None
    return {
        **msg,
        "trade_id": msg.get("trade_id"),
        "ticker": msg.get("market_ticker"),
        "yes_price_dollars": msg.get("yes_price_dollars"),
        "no_price_dollars": msg.get("no_price_dollars"),
        "count_fp": msg.get("count_fp"),
        # taker_outcome_side FIRST (2026-08-17 audit) - see module docstring.
        # The day Kalshi drops the deprecated field, direction still resolves
        # from the canonical one instead of silently falling to a default.
        "taker_side": msg.get("taker_outcome_side") or msg.get("taker_side"),
        "taker_outcome_side": msg.get("taker_outcome_side") or msg.get("taker_side"),
        "taker_book_side": msg.get("taker_book_side"),
        "is_block_trade": msg.get("is_block_trade", False),
        "ts": msg.get("ts"),
        "ts_ms": msg.get("ts_ms"),
        "created_time": created_time,
    }
