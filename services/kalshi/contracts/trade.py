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

from dataclasses import dataclass
from datetime import datetime, timezone

from services.kalshi.provenance import ContractDocs

CONTRACT_DOCS: dict[str, ContractDocs] = {
    "normalize_trade": (
        "docs/kalshi/public-trades.md",
        "docs/kalshi/get-trades.md",
        "docs/kalshi/order_direction.md",
    ),
    "resolve_taker_outcome_side": (
        "docs/kalshi/get-trades.md",
        "docs/kalshi/order_direction.md",
    ),
    "public_trade_from_ws": (
        "docs/kalshi/public-trades.md",
        "docs/kalshi/get-trades.md",
        "docs/kalshi/order_direction.md",
        "docs/kalshi/fixed_point_migration.md",
    ),
}

# Closed vendor vocabularies (order_direction.md): outcome_side is
# yes|no; book_side is bid|ask with bid == yes, ask == no, always.
_OUTCOME_SIDES = ("yes", "no")
_BOOK_SIDE_TO_OUTCOME = {"bid": "yes", "ask": "no"}


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


def resolve_taker_outcome_side(msg: dict) -> str | None:
    """Which outcome the taker is positioned for, or None when the trade
    doesn't say. Canonical-first precedence per docs/kalshi/get-trades.md
    (taker_side is deprecated - its "will not be removed before May 14,
    2026" guarantee has expired - and taker_outcome_side/taker_book_side
    are named "the canonical way to determine trade direction"), with
    book vocabulary mapping exactly per order_direction.md: bid == yes,
    ask == no, always.

    Returns None rather than defaulting: an UNKNOWN value (a new enum
    member, a malformed field) must never become a confident yes/no -
    the pre-2026-08-17 code turned every unreadable trade into "no",
    wrong direction AND wrong notional, silently, on every signal."""
    outcome = str(msg.get("taker_outcome_side") or "").lower()
    if outcome in _OUTCOME_SIDES:
        return outcome
    book = str(msg.get("taker_book_side") or "").lower()
    if book in _BOOK_SIDE_TO_OUTCOME:
        return _BOOK_SIDE_TO_OUTCOME[book]
    legacy = str(msg.get("taker_side") or "").lower()
    if legacy in _OUTCOME_SIDES:
        return legacy
    return None


def _dollars(value) -> float | None:
    """Cheap float parse of a fixed-point dollars/count string; None stays
    None and garbage stays None rather than raising on a hot-adjacent
    path (spec numeric policy: cheap parsing, raw strings preserved in
    raw_payload)."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class PublicTrade:
    """Canonical public-trade contract (A12) - the likely-final interface
    consumers migrate to at A13. Constructed on demand (never eagerly in
    the exchange-wide dispatch loop); every upstream field, known or
    unknown, stays available on raw_payload."""

    trade_id: str | None
    ticker: str | None
    outcome_side: str | None   # "yes" | "no" | None - never guessed
    count: float | None        # contracts (from count_fp)
    yes_price: float | None    # dollars/contract
    no_price: float | None     # dollars/contract
    occurred_at: str | None    # ISO-8601 UTC (from ts_ms)
    ts_ms: int | None
    raw_payload: dict


def public_trade_from_ws(msg: dict) -> PublicTrade:
    ts_ms = msg.get("ts_ms")
    occurred_at = None
    if ts_ms is not None:
        try:
            occurred_at = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        except (TypeError, ValueError, OSError):
            occurred_at = None
    return PublicTrade(
        trade_id=msg.get("trade_id"),
        ticker=msg.get("market_ticker") or msg.get("ticker"),
        outcome_side=resolve_taker_outcome_side(msg),
        count=_dollars(msg.get("count_fp")),
        yes_price=_dollars(msg.get("yes_price_dollars")),
        no_price=_dollars(msg.get("no_price_dollars")),
        occurred_at=occurred_at,
        ts_ms=ts_ms if isinstance(ts_ms, int) else None,
        raw_payload=msg,
    )
