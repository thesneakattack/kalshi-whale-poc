"""
Position routes - the paper broker's real-account mirror (order history),
the Market-Native strategy's own state endpoint, manual risk-halt controls
for all three independent risk trackers, position-netting diagnostics, and
the erroneous-close correction admin route. Extracted 2026-08-22 as part
of main.py's modularization pass, following the routers/diagnostics_routes.py
convention: an APIRouter, shared state from services.app_state only,
main.py does app.include_router(...) at the same paths as before.

services/paper_broker.py itself is already a fully clean, self-contained
module (no main.py coupling) - what wasn't extracted yet was the ~150
lines of routing/account-mirroring around it, scattered across 9 separate
line ranges in main.py.
"""
from fastapi import APIRouter, HTTPException

from services import trade_analytics
from services.account_positions import _slim_order
from services.app_state import account, broker, bump_generation, market_broker, market_risk, risk, shadow, state
from services.config_store import config_store
from services.state_view import _enrich_recent_trades, _relevant_tickers, _scoped_market_titles

router = APIRouter()


@router.get("/api/account/orders")
async def get_account_orders(limit: int = 25, cursor: str | None = None, status: str | None = None):
    # Real order history - direct data-usage review finding: get_orders()
    # was fully implemented in kalshi_account_client.py and returns real
    # order objects (confirmed against the live connected account), but
    # nothing ever called it - no route, no state key, no panel. Any order
    # on that account (placed by this app once trading is enabled, or
    # manually on Kalshi's own site) was completely invisible in this
    # dashboard. Kept out of the main /api/state poll loop on purpose -
    # order history isn't bounded the way "current positions" is, so it's
    # an on-demand paginated fetch instead (Kalshi's own cursor, passed
    # through opaquely, not the limit/offset pagination this app's own
    # endpoints use elsewhere - real order history is Kalshi's data, not
    # ours to re-paginate).
    limit = min(max(limit, 1), 100)
    if not account.enabled:
        return {"connected": False, "orders": [], "cursor": None, "error": account.status["error"]}
    try:
        result = await account.get_orders(limit=limit, cursor=cursor, status=status)
        return {
            "connected": True,
            "orders": [_slim_order(o) for o in (result.get("orders") or [])],
            "cursor": result.get("cursor"),
            "error": None,
        }
    except Exception as e:
        return {"connected": True, "orders": [], "cursor": None, "error": str(e)}


@router.get("/api/market-strategy/state")
async def get_market_strategy_state():
    # Backend-only at first (docs/advisory-engine-plan.md §9-adjacent,
    # direct request 2026-08-08) - now the real data source for the
    # dedicated Market-Native tab (2026-08-10, direct request: "the
    # market-native strategy seems to have stalled, and i think itd be
    # good to have its own tab now"). Reuses trade_analytics as-is
    # (strategy-agnostic - it only ever reads Trade dicts) rather than
    # reimplementing summary stats for a second broker.
    market_cfg = config_store.get()["market_strategy"]
    all_rows = trade_analytics.build_trade_history([t.to_dict() for t in market_broker.trade_log])
    scoped_market_titles = _scoped_market_titles(_relevant_tickers())
    return {
        "enabled": market_cfg["enabled"],
        "broker": {**market_broker.state(state["latest_prices"]), "recent_trades": _enrich_recent_trades(market_broker)},
        "summary": trade_analytics.compute_summary(all_rows),
        "risk": {"halted": market_risk.halted, "halt_reason": market_risk.halt_reason},
        "decision_feed": state["market_decision_feed"],
        "market_titles": scoped_market_titles,
        "latest_prices": state["latest_prices"],
    }


@router.post("/api/risk/halt")
async def halt_trading():
    risk.manual_halt("Manually halted from dashboard")
    bump_generation()
    return {"halted": risk.halted, "halt_reason": risk.halt_reason}


@router.post("/api/risk/resume")
async def resume_trading():
    risk.resume()
    bump_generation()
    return {"halted": risk.halted, "halt_reason": risk.halt_reason}


@router.post("/api/market-risk/halt")
async def halt_market_native():
    # Same manual halt as /api/risk/halt above, for market_strategy.py's
    # own independent risk manager - previously had no route at all (real
    # gap found live 2026-08-10: market-native's kill switch had tripped
    # and had no way to be manually managed, only services/risk_manager.py's
    # automatic daily rollover fix - see reset_day - could ever clear it).
    market_risk.manual_halt("Manually halted from dashboard")
    bump_generation()
    return {"halted": market_risk.halted, "halt_reason": market_risk.halt_reason}


@router.post("/api/market-risk/resume")
async def resume_market_native():
    market_risk.resume()
    bump_generation()
    return {"halted": market_risk.halted, "halt_reason": market_risk.halt_reason}


@router.post("/api/shadow-risk/resume")
async def resume_shadow():
    # Same manual un-halt as the two routes above, for shadow_mode.py's own
    # independent risk tracker - previously had no route at all. Confirmed
    # live (2026-08-10) it had been stuck halted (-99.7%) with no recovery
    # path, dormant only because mode was "paper" at the time.
    shadow.resume()
    bump_generation()
    return {"halted": shadow.halted, "halt_reason": shadow.halt_reason}


@router.post("/api/admin/correct-trade")
async def correct_trade(trade_id: str, corrected_price: float | None = None):
    """Remediate one confirmed-fabricated CLOSE trade -
    services/paper_broker.py's correct_erroneous_close, called through the
    live app (not a detached script) so it mutates the SAME in-memory
    `broker` object this server is actually using, not just the on-disk
    file - CLAUDE.md's own data/*.db warning is exactly this failure mode
    in reverse (a script writing to the DB out from under a live process
    leaves the live process's memory stale).

    Added 2026-08-17 for a real, confirmed incident: strategy_engine.
    check_exits closed a real WTA position (Cirstea/Kalinskaya) via
    stop-loss at a fabricated exit_price of 0.0 one tick after
    market_history's own independently-polled REST price had sat pinned at
    0.99 for 13+ minutes - see check_exits' own comment on the
    corroboration fix this same incident produced. Two more tennis
    positions showed the identical shape the same night.

    Deliberately requires the caller to supply `trade_id` explicitly (never
    a bulk/pattern-matched sweep) and, when correcting to a real price
    rather than just reversing the fabricated debit, requires
    `corrected_price` explicitly too - this endpoint applies a correction,
    it does not decide which trades need one."""
    result = broker.correct_erroneous_close(trade_id, corrected_price=corrected_price)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"trade {trade_id!r} not found, not a close, or already corrected",
        )
    return result
