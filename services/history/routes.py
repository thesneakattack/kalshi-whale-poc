"""
History routes - "what happened": browsable signal history, persistent
flow clustering, the Trading History tab (win/loss record + cumulative
P&L curve), and market_history's own tracked-ticker/hypothetical-trade
summaries. Extracted 2026-08-22 as part of main.py's modularization pass,
following the routers/diagnostics_routes.py convention: an APIRouter,
shared state from services.app_state only, main.py does
app.include_router(...) at the same paths as before.
"""
from fastapi import APIRouter

from services import market_history, signal_log, trade_analytics
from services.app_state import broker
from services.state_view import _scoped_market_titles

router = APIRouter()


@router.get("/api/signals/history")
async def get_signal_history(limit: int = 50, offset: int = 0, resolved_only: bool = False):
    # Browsable signal history (ROADMAP.md Phase 0.5) - individual signals,
    # not just the aggregate win-rate stat cards. Pure on-demand read
    # against signal_log.db, not part of /api/state's poll cycle - a
    # separate paginated fetch, same pattern as /api/markets/search.
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    signals = signal_log.recent(limit=limit, offset=offset, resolved_only=resolved_only)
    return {
        "signals": signals,
        "total": signal_log.total_count(resolved_only=resolved_only),
        # Resolved/older signals routinely reference tickers that have long
        # since rotated off the live watchlist and won't be in /api/state's
        # scoped market_titles - state["market_titles"] itself is unbounded
        # for the app's lifetime (see services/title_cache.py), so this page
        # of signals can still resolve its own titles independently.
        "market_titles": _scoped_market_titles({s["ticker"] for s in signals if s.get("ticker")}),
    }


@router.get("/api/signals/clusters")
async def get_signal_clusters(hours: int = 24):
    # Persistent flow clustering (ROADMAP.md P2 stretch item) - probable-
    # same-actor accumulation groups, inferred from timing/size similarity
    # on the persisted signal log, not a live/poll-cycle concern.
    hours = min(max(hours, 1), 24 * 30)
    clusters = signal_log.find_clusters(hours=hours)
    return {
        "clusters": clusters,
        "market_titles": _scoped_market_titles({c["ticker"] for c in clusters if c.get("ticker")}),
    }


@router.get("/api/trading-history")
async def get_trading_history(limit: int = 50, offset: int = 0):
    # The History tab (direct request): win/loss record, what closed each
    # position (take-profit/stop-loss/sentiment-reversal/auto-exit/settled),
    # and sample-size-hedged hints about which config knob a pattern might
    # argue for adjusting. All derived from the trade log's existing reason
    # strings (see services/trade_analytics.py) - no new persistence.
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)

    # broker.trade_log is already chronological ascending (append-only at
    # runtime, ORDER BY timestamp ASC on load) - exactly what
    # build_trade_history expects and what a cumulative P&L curve needs.
    all_rows = trade_analytics.build_trade_history([t.to_dict() for t in broker.trade_log])

    cumulative_pnl_curve = []
    running = 0.0
    for r in all_rows:
        if r["realized_pnl"] is not None:
            running += r["realized_pnl"]
        cumulative_pnl_curve.append({"t": r["exit_timestamp"], "cumulative_pnl": round(running, 2)})

    newest_first = list(reversed(all_rows))
    page = newest_first[offset:offset + limit]
    return {
        "trades": page,
        "total": len(all_rows),
        "summary": trade_analytics.compute_summary(all_rows),
        "exit_management_split": trade_analytics.exit_management_split(all_rows),
        "cumulative_pnl_curve": cumulative_pnl_curve,
        "market_titles": _scoped_market_titles({r["ticker"] for r in page}),
    }


@router.get("/api/market-history/summary")
async def get_market_history_summary():
    return {
        "tracked_tickers": market_history.tracked_ticker_count(),
        "total_snapshots": market_history.snapshot_count(),
        "resolved_outcomes": market_history.outcome_count(),
    }


@router.get("/api/market-history/hypothetical-trades")
async def get_market_history_hypothetical_trades():
    # Retrospective, explicitly hypothetical (see market_history.py's
    # docstring) - never a claim about a real position. Computed on demand
    # from logged snapshots/outcomes, not separately persisted.
    return {"trades": market_history.compute_hypothetical_trades()}
