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

from services.kalshi.contracts.types import AS_OUTCOME_SIDE, BOOK_SIDE_TO_OUTCOME, OutcomeSide
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
    "trade_exchange_ts": (
        "docs/kalshi/public-trades.md",
        "docs/kalshi/market-ticker.md",
    ),
    "taker_notional_usd": (
        "docs/kalshi/get-trades.md",
        "docs/kalshi/public-trades.md",
        "docs/kalshi/fixed_point_migration.md",
    ),
    "public_trade_from_ws": (
        "docs/kalshi/public-trades.md",
        "docs/kalshi/get-trades.md",
        "docs/kalshi/order_direction.md",
        "docs/kalshi/fixed_point_migration.md",
    ),
}

# Closed vocabularies + narrowing maps live in contracts/types.py (C2) -
# one copy shared with fill.py, so the two channels can never disagree.


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


def resolve_taker_outcome_side(msg: dict) -> OutcomeSide | None:
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
    outcome = AS_OUTCOME_SIDE.get(str(msg.get("taker_outcome_side") or "").lower())
    if outcome is not None:
        return outcome
    book = BOOK_SIDE_TO_OUTCOME.get(str(msg.get("taker_book_side") or "").lower())
    if book is not None:
        return book
    return AS_OUTCOME_SIDE.get(str(msg.get("taker_side") or "").lower())


def _dollars(value: str | float | int | None) -> float | None:
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
    outcome_side: OutcomeSide | None   # never guessed - unknown stays None
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


def taker_notional_usd(msg: dict, side: str) -> float | None:
    """Real dollar size of a trade, side-aware - count times whichever
    price the taker actually paid, NOT always the yes price (the same
    no-side-inversion lesson this app already paid for once: a no-side
    cost is count * no_price == count * (1 - yes_price)).

    `side` is passed in (resolved once by resolve_taker_outcome_side)
    rather than re-read here, so the notional and the signal's own
    direction can never disagree about which side the taker took.

    Returns None when either the count or the side's price is missing - a
    notional derived from an invented zero silently reads as "tiny trade"
    and gets filtered for the wrong reason rather than flagged unusable."""
    count = _dollars(msg.get("count_fp"))
    price = _dollars(msg.get("yes_price_dollars") if side == "yes" else msg.get("no_price_dollars"))
    if count is None or price is None:
        return None
    return count * price


def trade_exchange_ts(msg: dict) -> float | None:
    """Exchange-side timestamp of a trade/ticker message as epoch seconds,
    or None when the message carries no readable one. Precedence: ts_ms
    (millisecond precision, both channels), then ts (seconds), then a
    parse-back of the normalized created_time ISO string (itself derived
    from ts_ms by normalize_trade). Never invents a receive-side time -
    the caller decides its own fallback."""
    ms = msg.get("ts_ms")
    if ms is not None:
        try:
            return float(ms) / 1000.0
        except (TypeError, ValueError):
            pass
    secs = msg.get("ts")
    if secs is not None:
        try:
            return float(secs)
        except (TypeError, ValueError):
            pass
    created_time = msg.get("created_time")
    if not created_time:
        return None
    try:
        return datetime.fromisoformat(str(created_time).replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None
