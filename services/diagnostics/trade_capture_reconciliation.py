"""REST-vs-WebSocket trade capture reconciliation by trade_id (realtime
data-plane investigation task I4, hypothesis H5 in docs/superpowers/
research/2026-08-25-realtime-data-plane-known-findings.md).

Every other diagnostic reads the app's own stores, so all of them are blind
to a trade that never arrived - dropped at the ingest queue, lost to a
Kalshi-side subscription overflow, or missed across a reconnect. This one
asks the exchange what it printed over a bounded window and checks each
trade_id against what the WebSocket path actually evaluated.

Doc-backed contract (docs/kalshi/get-trades.md, read before writing this):
GET /markets/trades is exchange-wide when `ticker` is omitted ("all trades
for all markets"), pages via a `cursor` that is empty when no more pages
exist, accepts `limit` 1-1000 (default 100), and filters on `min_ts` /
`max_ts` - both "Unix timestamp", integer seconds. The window is enforced
locally on each Trade's own `created_time` as well, because the docs do not
state whether min_ts/max_ts are inclusive.

Read-only and bounded: at most `max_pages` requests of the documented
maximum page size, no subscription change, no writes, never scheduled -
this is a manual/interactive diagnostic (plan I4: "Do not run
reconciliation every tick").
"""
from collections.abc import Callable, Mapping
from datetime import datetime

from services.kalshi.contracts import trade as trade_contract

PAGE_LIMIT = 1000     # docs/kalshi/get-trades.md MarketLimitQuery: "Maximum value is 1000"
MAX_LISTED_IDS = 50   # bounded evidence lists; counts stay exact


def _created_ts(trade: dict) -> float | None:
    """Exchange-side timestamp of a REST Trade, seconds. Prefers the
    boundary's own resolver (services/kalshi/contracts/trade.py), then the
    documented ISO `created_time` field directly."""
    ts = trade_contract.trade_exchange_ts(trade)
    if ts is not None:
        return float(ts)
    raw = trade.get("created_time")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _count_fp(trade: dict) -> float:
    try:
        return float(trade.get("count_fp") or 0.0)
    except (TypeError, ValueError):
        return 0.0


async def reconcile_window(
    client, *, window_start: float, window_end: float,
    seen_exchange_ts_by_id: Mapping[str, float],
    min_contracts_for: Callable[[str], float],
    max_pages: int = 10,
    ingest_evidence: dict | None = None,
    seen_horizon_ts: float | None = None,
) -> dict:
    """Compare Kalshi's REST record of [window_start, window_end] (exchange
    time, Unix seconds) with the WebSocket path's own seen-record.

    seen_exchange_ts_by_id: trade_id -> exchange timestamp for every trade the
    WS path evaluated (KalshiTradeTapeProvider.seen_exchange_ts_by_id()).
    Membership is what decides "captured"; the timestamps only bound the
    WS-side count for the same window.

    seen_horizon_ts: oldest exchange timestamp still retained on the WS side
    (the dedupe ring evicts). A window starting before it cannot distinguish
    a real miss from an evicted id, so that case is flagged, not hidden."""
    caveats: list[str] = []
    result: dict = {
        "window": {"start": window_start, "end": window_end, "seconds": round(window_end - window_start, 3)},
        "rest": {"count": 0, "pages": 0, "truncated": False, "whale_sized_count": 0},
        "ws": {"count_in_window": 0, "not_in_rest": 0},
        "intersection": 0,
        "missing": {"count": 0, "ids": [], "whale_sized": {"count": 0, "ids": [], "tickers": []}},
        "capture_completeness": None,
        "whale_capture_completeness": None,
        "caveats": caveats,
        "error": None,
        "ingest_evidence": ingest_evidence,
    }

    trades: list[dict] = []
    pages = 0
    truncated = False
    cursor: str | None = None
    try:
        for _ in range(max(1, max_pages)):
            resp = await client.get_trades(
                ticker=None, limit=PAGE_LIMIT, min_ts=int(window_start), max_ts=int(window_end), cursor=cursor,
            )
            pages += 1
            trades.extend(resp.get("trades") or [])
            cursor = resp.get("cursor") or None
            if not cursor:
                break
        else:
            truncated = cursor is not None
    except Exception as exc:  # degrade honestly - a diagnostic must not raise
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["rest"]["pages"] = pages
        caveats.append("REST fetch failed; nothing below is a measurement")
        return result

    in_window: list[tuple[dict, float]] = []
    unparseable = 0
    for trade in trades:
        ts = _created_ts(trade)
        if ts is None:
            unparseable += 1
            continue
        if window_start <= ts <= window_end and trade.get("trade_id"):
            in_window.append((trade, ts))
    if unparseable:
        caveats.append(f"{unparseable} REST trade(s) had no parseable created_time and were excluded")

    rest_ids = {t["trade_id"] for t, _ in in_window}
    seen_ids = set(seen_exchange_ts_by_id)
    ws_in_window = {tid for tid, ts in seen_exchange_ts_by_id.items() if window_start <= ts <= window_end}
    intersection = rest_ids & seen_ids
    missing_ordered = [t["trade_id"] for t, _ in in_window if t["trade_id"] not in seen_ids]
    missing = set(missing_ordered)

    whale_ids: list[str] = []
    whale_missing: list[str] = []
    whale_tickers: set[str] = set()
    for trade, _ in in_window:
        ticker = trade.get("ticker") or ""
        if _count_fp(trade) >= float(min_contracts_for(ticker)):
            whale_ids.append(trade["trade_id"])
            if trade["trade_id"] in missing:
                whale_missing.append(trade["trade_id"])
                whale_tickers.add(ticker)

    result["rest"] = {
        "count": len(rest_ids), "pages": pages, "truncated": truncated, "whale_sized_count": len(whale_ids),
    }
    result["ws"] = {"count_in_window": len(ws_in_window), "not_in_rest": len(ws_in_window - rest_ids)}
    result["intersection"] = len(intersection)
    result["missing"] = {
        "count": len(missing),
        "ids": missing_ordered[:MAX_LISTED_IDS],
        "whale_sized": {
            "count": len(whale_missing), "ids": whale_missing[:MAX_LISTED_IDS], "tickers": sorted(whale_tickers),
        },
    }
    result["capture_completeness"] = (len(intersection) / len(rest_ids)) if rest_ids else None
    result["whale_capture_completeness"] = (
        (len(whale_ids) - len(whale_missing)) / len(whale_ids) if whale_ids else None
    )

    if not rest_ids:
        caveats.append("no REST trades in window - completeness unknown, not 100%")
    if truncated:
        caveats.append(f"REST paging truncated at {pages} page(s) - REST count and misses are lower bounds")
    if seen_horizon_ts is not None and seen_horizon_ts > window_start:
        caveats.append(
            f"WS seen-record horizon ({seen_horizon_ts:.0f}) is newer than window start ({window_start:.0f}) - "
            "misses before the horizon may be dedupe-ring eviction, not capture loss"
        )
    return result
