"""
The real (not paper) connected Kalshi account's snapshot fetch + field
trimming - shared by the whale-stream fill/position handlers
(_process_stream_fill/_process_stream_position), the REST poll
(trading_loop's market_fetch phase), and the position routes
(GET /api/account/orders). Extracted
2026-08-21 as part of main.py's modularization pass - previously this
lived directly in main.py and both the stream handlers and the routes
reached into it there.
"""
import asyncio
import time

from services.app_state import account

_POSITION_FIELDS = (
    "ticker", "position_fp", "market_exposure_dollars", "realized_pnl_dollars",
    # fees_paid_dollars/total_traded_dollars/last_updated_ts added after a
    # direct data-usage review found them: real fields (confirmed against a
    # real connected account), fetched-for-free on every get_positions()
    # call, previously trimmed here and never reaching the frontend at all —
    # a real position's fees directly eat into its P&L, so showing exposure
    # without what it cost to get there was an incomplete picture on a
    # panel that's specifically about real money.
    "fees_paid_dollars", "total_traded_dollars", "last_updated_ts",
)
# Kalshi's own EventPosition has no price field either (same as
# MarketPosition - confirmed against the SDK's models), so this doesn't need
# a price join the way market_positions does below - it's purely the
# real parent-event grouping/exposure rollup, previously fetched every tick
# and then dropped entirely before /api/state (direct report: real
# positions on child markets of the same event rendered as unrelated flat
# rows with no grouping at all).
_EVENT_POSITION_FIELDS = (
    "event_ticker", "total_cost_dollars", "total_cost_shares_fp",
    "event_exposure_dollars", "realized_pnl_dollars", "fees_paid_dollars",
)
_FILL_FIELDS = (
    "ticker", "market_ticker", "side", "action", "count_fp", "yes_price_dollars", "no_price_dollars",
    # created_time/fee_cost/is_taker/fill_id/order_id added for the same
    # reason as _POSITION_FIELDS above — most notably created_time: the real
    # Trade Log had no timestamp at all before this, so real fills couldn't
    # be read in time order or checked for recency.
    "created_time", "fee_cost", "is_taker", "fill_id", "order_id",
)
# Same trim idea as _slim_market: keep only what renderRealPositions/
# renderRealFills actually read (field names confirmed against a real
# connected account, not guessed — see the comment above renderRealPositions
# for why that mattered). event_positions/cursor/... are real fields, just
# not currently rendered anywhere. Keeps fills well under the ~13.4KB a full
# 25-fill page would otherwise cost, the single largest piece of /api/state's
# payload, while still keeping every field the UI actually shows.

# Real order history - GetOrders' full real fields confirmed against a live
# connected account (docstring in kalshi_account_client.py has the full
# example). Unlike positions/fills, order history isn't part of the main
# poll loop at all (see GET /api/account/orders) - it's on-demand,
# same "paginated, fetched only when that panel is actually open" pattern
# as GET /api/signals/history and GET /api/trading-history, not something
# every 15s tick needs to pull.
_ORDER_FIELDS = (
    "order_id", "ticker", "side", "action", "type", "status",
    "yes_price_dollars", "no_price_dollars", "fill_count_fp", "remaining_count_fp", "initial_count_fp",
    "taker_fees_dollars", "maker_fees_dollars", "created_time", "last_update_time", "client_order_id",
)


def _slim_order(o: dict) -> dict:
    return {k: o.get(k) for k in _ORDER_FIELDS}


def _slim_position(p: dict) -> dict:
    return {k: p.get(k) for k in _POSITION_FIELDS}


def _slim_event_position(p: dict) -> dict:
    return {k: p.get(k) for k in _EVENT_POSITION_FIELDS}


def _join_real_position_prices(account_snapshot: dict, latest_prices: dict) -> None:
    """Kalshi's real MarketPosition has no price field at all (confirmed
    against the SDK's models) - this app has never joined a real position
    against its market's current price, for any real position, anywhere
    (direct report). Attached backend-side, from the same latest_prices
    every other price display already reads, rather than re-derived
    client-side at the render call site - CLAUDE.md's own documented bug
    pattern for displayed financial figures. Real position tickers are
    already force-fetched into `markets` every tick
    (_real_account_position_tickers, phase 60/61), so this should always
    resolve; None (not a fabricated default) if a ticker genuinely isn't
    there yet. Mutates each position dict in place."""
    real_positions = ((account_snapshot.get("positions") or {}).get("market_positions")) or []
    for p in real_positions:
        p["current_yes_price_dollars"] = latest_prices.get(p.get("ticker"))


def _slim_fill(f: dict) -> dict:
    return {k: f.get(k) for k in _FILL_FIELDS}


def _real_account_position_tickers(account: dict) -> set[str]:
    """Tickers of the *real* connected Kalshi account's currently open
    positions only - deliberately excludes fills (see trading_loop's own
    extra_tickers comment for why folding fills into anything that drives
    the live watchlist fetch is wrong: fills are historical trade records
    that can span days/weeks, unlike a position, which naturally drops out
    the tick it closes). Shared by trading_loop (feeds _fetch_markets'
    extra_tickers) and services.state_view._relevant_tickers (feeds
    /api/state's title scoping) so both stay defined identically rather
    than drifting."""
    return {
        p.get("ticker") for p in ((account.get("positions") or {}).get("market_positions") or []) if p.get("ticker")
    }


_ACCOUNT_SNAPSHOT_REFRESH_SEC = 20  # 2026-08-15 "no stone unturned" API audit - this used to fetch
# balance/positions/fills via 3 uncached REST calls on literally every tick, unconditionally, the
# one real REST-call site this whole pass hadn't touched yet. A real Kalshi WS channel exists for
# this (market_positions/fill, confirmed in docs/kalshi/websocket-connection.md's channel list) but
# fill events specifically cannot be observed or verified against real data right now - real trading
# is off (kalshi_account.trading_enabled: false, same P0 safety gate as always), so no order can ever
# fill, so there is no live message to confirm this app's parsing of that channel's real shape
# against. Shipping unverified parsing for real-account financial data is exactly the class of risk
# CLAUDE.md's safety posture warns against - a wrong field name would silently misreport real
# positions, not just crash loudly. A real interval cache is the safe, immediately-effective
# version of the same fix instead: same three REST calls, same data, just not re-fetched more often
# than something could plausibly have changed. See docs/next-steps-2026-08-15-pt2.md for the
# WS-channel design, deferred pending either a real fill to verify parsing against or explicit
# sign-off to ship best-effort parsing with a REST reconciliation safety net.
_account_snapshot_cache: dict = {"fetched_at": 0.0, "snapshot": None}


async def _fetch_account_snapshot(cfg: dict) -> dict:
    account.trading_enabled = cfg["kalshi_account"]["trading_enabled"]
    if not account.enabled:
        return {
            "connected": False, "balance": None, "positions": None, "fills": None,
            "error": account.status["error"], "trading_enabled": account.trading_enabled,
        }
    now_ts = time.time()
    cached = _account_snapshot_cache["snapshot"]
    if cached is not None and (now_ts - _account_snapshot_cache["fetched_at"]) < _ACCOUNT_SNAPSHOT_REFRESH_SEC:
        return {**cached, "trading_enabled": account.trading_enabled}
    try:
        # balance, positions, and fills are independent reads — fetch all three
        # at once instead of one after another.
        balance, positions, fills = await asyncio.gather(
            account.get_balance(), account.get_positions(), account.get_fills(limit=50)
        )
        positions = {
            "market_positions": [_slim_position(p) for p in (positions.get("market_positions") or [])],
            "event_positions": [_slim_event_position(p) for p in (positions.get("event_positions") or [])],
        }
        fills = {"fills": [_slim_fill(f) for f in (fills.get("fills") or [])]}
        snapshot = {
            "connected": True, "balance": balance, "positions": positions, "fills": fills,
            "error": None, "trading_enabled": account.trading_enabled,
        }
        _account_snapshot_cache["snapshot"] = snapshot
        _account_snapshot_cache["fetched_at"] = now_ts
        return snapshot
    except Exception as e:
        return {
            "connected": True, "balance": None, "positions": None, "fills": None,
            "error": str(e), "trading_enabled": account.trading_enabled,
        }
