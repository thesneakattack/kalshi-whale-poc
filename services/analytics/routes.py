"""
Analytics routes - the residual "what happened, and did a gate filter it
correctly" surface left after advisory (services/advisory/) and whale
calibration (services/whale_calibration/) were split out 2026-08-22 (see
ROADMAP.md's queued split item): market-strategy calibration (the separate
Market-Native strategy's own tuning), candidate-log/cross-strategy/regime
segmentation, backtesting, and the market-analyst LLM agent. Extracted
2026-08-22 as part of main.py's modularization pass, following the
routers/diagnostics_routes.py convention: an APIRouter, shared state from
services.app_state only, main.py does app.include_router(...) at the same
paths as before. /api/suggestions/* stays here too - shared infrastructure
both advisory and market-analyst read from, not owned by either.

The real market-analyst orchestration logic lives in
market_analyst_orchestrator.py, imported here - these route handlers are
thin wrappers, same shape as every other route in this file.
"""
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import (
    backtest, candidate_log,
    config_performance, cross_strategy, market_analyst_agent, market_strategy_calibration,
    regime_analytics, series_evaluator, signal_log, suggestion_decisions, trade_analytics,
)
from services.analytics.market_analyst_orchestrator import (
    _run_full_spectrum_analysis, _run_market_analyst_for_ticker, _run_series_analysis,
    _series_evaluator_overview_with_crosscheck,
)
from services.app_state import broker, bump_generation, market_broker
from services.config.config_paths import _config_value_at_path
from services.config_store import config_store
from services.kalshi_client import KalshiClient

router = APIRouter()



class DeclineSuggestionBody(BaseModel):
    id: str
    config_path: str
    rationale: str | None = None


@router.post("/api/suggestions/decline")
async def decline_suggestion(body: DeclineSuggestionBody):
    # History tab redesign (2026-08-14/15 direct request) - "choose to hold
    # back" needs to actually stick, not just hide a card until the next
    # poll re-fetches the exact same suggestion. No staleness check needed
    # here unlike the apply route above - declining an id that's already
    # gone from the live recommendation set (or was never real) is still a
    # perfectly valid "no thanks," it just has nothing left to suppress.
    suggestion_decisions.decline(body.id, body.config_path, body.rationale)
    bump_generation()
    return {"declined": True, "id": body.id}


class UndeclineSuggestionBody(BaseModel):
    id: str


@router.post("/api/suggestions/undecline")
async def undecline_suggestion(body: UndeclineSuggestionBody):
    # "You can revisit this anytime" - the Advanced-section "previously
    # declined" list's per-row undo action.
    existed = suggestion_decisions.undecline(body.id)
    bump_generation()
    return {"undeclined": existed, "id": body.id}


@router.get("/api/suggestions/declined")
async def get_declined_suggestions(limit: int = 50):
    return {"declined": suggestion_decisions.list_declined(limit)}



@router.get("/api/market-strategy-calibration/status")
async def get_market_strategy_calibration_status():
    # "Web of expertise" audit (2026-08-11) gap #5 - same status-route shape
    # as the whale-side /api/confidence-calibration/status above.
    msc_cfg = config_store.get()["market_strategy_calibration"]
    resolved_count = len(trade_analytics.build_trade_history([t.to_dict() for t in market_broker.trade_log]))
    return {
        "enabled": msc_cfg["enabled"],
        "min_resolved_trades": msc_cfg["min_resolved_trades"],
        "resolved_count": resolved_count,
        "ready": resolved_count >= msc_cfg["min_resolved_trades"],
    }


@router.get("/api/market-strategy-calibration/report")
async def get_market_strategy_calibration_report():
    msc_cfg = config_store.get()["market_strategy_calibration"]
    if not msc_cfg["enabled"]:
        return {"report": None, "gated_reason": "market-native calibration is disabled", "resolved_count": None}
    rows = trade_analytics.build_trade_history([t.to_dict() for t in market_broker.trade_log])
    return market_strategy_calibration.generate_calibration_report(rows, msc_cfg["min_resolved_trades"])


@router.get("/api/candidate-log/summary")
async def get_candidate_log_summary():
    # services/candidate_log.py - Gap 1 of docs/config-tuning-data-gaps-
    # 2026-08-10.md. Always safe to call, no enable flag: this data
    # collects passively from every gate check regardless of any config
    # toggle, same as signal_log itself.
    return {"gates": candidate_log.gate_summary()}


@router.get("/api/cross-strategy/comparison")
async def get_cross_strategy_comparison():
    # services/cross_strategy.py - Gap 7 of docs/config-tuning-data-gaps-
    # 2026-08-10.md, and the user's own direct question this session.
    # Always safe to call, no enable flag - a pure read over trades both
    # strategies have already placed.
    whale_rows = trade_analytics.build_trade_history([t.to_dict() for t in broker.trade_log])
    market_rows = trade_analytics.build_trade_history([t.to_dict() for t in market_broker.trade_log])
    return {
        "aggregate": cross_strategy.aggregate_comparison(whale_rows, market_rows),
        "ticker_overlap": cross_strategy.ticker_overlap(whale_rows, market_rows),
    }


@router.get("/api/regime/by-hour")
async def get_regime_by_hour(strategy: str = "whale_follow"):
    # services/regime_analytics.py - Gap 9 of docs/config-tuning-data-gaps-
    # 2026-08-10.md. Always safe to call, no enable flag.
    trade_log = market_broker.trade_log if strategy == "market_native" else broker.trade_log
    rows = trade_analytics.build_trade_history([t.to_dict() for t in trade_log])
    return {"strategy": strategy, "buckets": regime_analytics.by_hour_of_day(rows)}


@router.get("/api/regime/by-day-of-week")
async def get_regime_by_day_of_week(strategy: str = "whale_follow"):
    trade_log = market_broker.trade_log if strategy == "market_native" else broker.trade_log
    rows = trade_analytics.build_trade_history([t.to_dict() for t in trade_log])
    return {"strategy": strategy, "buckets": regime_analytics.by_day_of_week(rows)}


@router.get("/api/regime/by-category")
async def get_regime_by_category(strategy: str = "whale_follow"):
    # services/trade_category.py - the deferred category half of Gap 9,
    # docs/config-tuning-data-gaps-2026-08-10.md. Always safe to call -
    # naturally empty until enough trades placed after this shipped have a
    # recorded category, same "auto-enables once there's real data"
    # pattern every other gate in this app already uses.
    trade_log = market_broker.trade_log if strategy == "market_native" else broker.trade_log
    rows = trade_analytics.build_trade_history([t.to_dict() for t in trade_log])
    return {"strategy": strategy, "buckets": regime_analytics.by_category(rows)}


@router.get("/api/regime/by-series")
async def get_regime_by_series(strategy: str = "whale_follow"):
    # services/regime_analytics.py's by_series() - 2026-08-16 direct
    # request, the finest of the three segmentation tiers. Always safe to
    # call, no enable flag, no trade_category.py dependency (series is a
    # pure function of the ticker).
    trade_log = market_broker.trade_log if strategy == "market_native" else broker.trade_log
    rows = trade_analytics.build_trade_history([t.to_dict() for t in trade_log])
    return {"strategy": strategy, "buckets": regime_analytics.by_series(rows)}


@router.get("/api/regime/by-subcategory")
async def get_regime_by_subcategory(strategy: str = "whale_follow"):
    # services/regime_analytics.py's by_subcategory() - 2026-08-16 direct
    # follow-up, the middle tier between by_series and by_category. Same
    # "auto-enables once there's real data" pattern as by_category - empty
    # until trades placed after this shipped have a recorded subcategory
    # (sports events only; see services/trade_category.py).
    trade_log = market_broker.trade_log if strategy == "market_native" else broker.trade_log
    rows = trade_analytics.build_trade_history([t.to_dict() for t in trade_log])
    return {"strategy": strategy, "buckets": regime_analytics.by_subcategory(rows)}


@router.get("/api/backtest/entry-threshold")
async def get_backtest_entry_threshold():
    # services/backtest.py - Gap 2 of docs/config-tuning-data-gaps-2026-08-
    # 10.md, stateless replay against every already-logged resolved signal.
    # Always safe to call - pure read, no enable flag.
    rows = signal_log.resolved_signals_with_factors()
    current_threshold = config_store.get()["strategy"]["entry_threshold"]
    return {"current_threshold": current_threshold, "sweep": backtest.entry_threshold_sweep(rows)}


@router.get("/api/backtest/min-whale-winrate")
async def get_backtest_min_whale_winrate():
    strat_cfg = config_store.get()["strategy"]
    series_stats = signal_log.all_series_stats(days=30)
    signal_rows = signal_log.resolved_signals_with_series(days=30)
    sweep = backtest.min_whale_winrate_pct_sweep(
        series_stats, signal_rows, min_resolved_for_filter=strat_cfg.get("min_resolved_for_whale_filter", 10),
    )
    return {"current_floor": strat_cfg.get("min_whale_winrate_pct", 40), "sweep": sweep}


@router.get("/api/market-analyst/status")
async def get_market_analyst_status():
    # Same "honest progress even while gated/disconnected" idiom as
    # advisory/confidence-calibration's own status routes. api_key_configured
    # is reported separately from enabled - both gate the feature (see
    # _run_market_analyst_for_ticker), and a user turning the checkbox on
    # with no key set should see *why* the Analyze button won't do anything.
    ma_cfg = config_store.get()["market_analyst"]
    return {
        "enabled": ma_cfg["enabled"],
        "api_key_configured": bool(os.environ.get("ANTHROPIC_API_KEY")),
        **market_analyst_agent.stats(days=30),
    }


class MarketAnalystAnalyzeBody(BaseModel):
    ticker: str


@router.post("/api/market-analyst/analyze")
async def post_market_analyst_analyze(body: MarketAnalystAnalyzeBody):
    # On-demand trigger, direct request (2026-08-09) - replaces the earlier
    # automatic per-tick background scan (see _run_market_analyst_for_ticker's
    # own docstring for why: this is the only thing in this app that spends
    # real money per call). A human clicks "Analyze" on one specific market
    # they're actually looking at; nothing runs on a schedule anymore.
    cfg = config_store.get()
    client = KalshiClient(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])
    try:
        return await _run_market_analyst_for_ticker(client, cfg, body.ticker)
    finally:
        await client.close()


@router.get("/api/market-analyst/analyses")
async def get_market_analyst_analyses(limit: int = 25, offset: int = 0, resolved_only: bool = False):
    # Always safe to call regardless of market_analyst.enabled - past
    # analyses stay visible/inspectable even after the feature's turned off,
    # same as every other history panel in this app.
    return {
        "rows": market_analyst_agent.recent(limit=limit, offset=offset, resolved_only=resolved_only),
        "total": market_analyst_agent.total_count(resolved_only=resolved_only),
    }


class MarketAnalystSeriesAnalyzeBody(BaseModel):
    series: str


@router.post("/api/market-analyst/series/analyze")
async def post_market_analyst_series_analyze(body: MarketAnalystSeriesAnalyzeBody):
    # Per-series analysis mode (Item 3B, 2026-08-10, direct request) -
    # button lives on the series-evaluator log panel (Item 1), analyzing
    # one whole series' whale-signal + closed-trade performance rather than
    # a single market's own probability. Same "deliberate human click, not
    # background spend" reasoning as the single-market Analyze button.
    cfg = config_store.get()
    return await _run_series_analysis(cfg, body.series)


class MarketAnalystSeriesApplyBody(BaseModel):
    analysis_id: str
    suggestion_id: str


@router.post("/api/market-analyst/series/apply")
async def post_market_analyst_series_apply(body: MarketAnalystSeriesApplyBody):
    # Applies one suggestion from a *persisted* series analysis - looked up
    # by (analysis_id, suggestion_id) rather than trusting whatever
    # config_path/suggested_value the request body might claim, same
    # never-trust-the-client principle as the rule-based Advisory apply
    # route. Unlike that route, this can't recompute the suggestion fresh
    # (an LLM's raw output isn't deterministically reproducible the way a
    # rule-based one is) - the persisted, server-computed value at analysis
    # time is the trusted source of truth instead.
    analysis = market_analyst_agent.get_series_analysis(body.analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="Series analysis not found.")
    match = next((s for s in analysis["suggestions"] if s["id"] == body.suggestion_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail="Suggestion not found on this analysis.")

    cfg_before = config_store.get()
    # Staleness check (direct report 2026-08-11: "make sure the suggested
    # values arent stale") - this suggestion's current_value was captured
    # when the analysis ran, not recomputed just now (see the docstring
    # above on why this route can't do what the rule-based Advisory apply
    # route does). If the live config has moved since - a manual edit, an
    # auto-apply, or a second analysis touching the same field - applying
    # this suggestion would silently overwrite based on a premise that's no
    # longer true, and the audit trail would log a "before" value that was
    # never actually live at apply time.
    live_value = _config_value_at_path(cfg_before, match["config_path"])
    if live_value != match["current_value"]:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Stale suggestion - {match['config_path']} is now {live_value!r}, not the "
                f"{match['current_value']!r} this suggestion was based on. Re-run the analysis and try again."
            ),
        )
    fp_before = config_performance.fingerprint(cfg_before)
    section, _, field = match["config_path"].partition(".")
    config_store.update({section: {field: match["suggested_value"]}})
    fp_after = config_performance.fingerprint(config_store.get())
    config_performance.log_applied_change(
        config_path=match["config_path"], old_value=match["current_value"], new_value=match["suggested_value"],
        rationale=match["rationale"], trade_count=0,
        fingerprint_before=fp_before, fingerprint_after=fp_after, auto_applied=False, source="series-analyst",
    )
    bump_generation()
    return {"applied": match, "new_config": config_store.get()["strategy"]}


@router.post("/api/market-analyst/full-spectrum/analyze")
async def post_market_analyst_full_spectrum_analyze():
    # "Feed the Analyst" (Item 3C, 2026-08-10, direct request) - one button
    # (History tab), confirm-gated client-side given this prompt is
    # materially bigger/costlier than the single-market/per-series modes
    # and no cost/latency numbers exist anywhere for this agent yet.
    cfg = config_store.get()
    return await _run_full_spectrum_analysis(cfg)


class MarketAnalystFullSpectrumApplyBody(BaseModel):
    analysis_id: str
    suggestion_id: str


@router.post("/api/market-analyst/full-spectrum/apply")
async def post_market_analyst_full_spectrum_apply(body: MarketAnalystFullSpectrumApplyBody):
    # Same persisted-lookup trust model as the per-series apply route above -
    # looked up by (analysis_id, suggestion_id) against what was actually
    # validated and persisted at analysis time (main.py's
    # _full_spectrum_suggestions_from_raw already confirmed the config_path
    # exists and isn't one of the two protected fields), never whatever the
    # request body itself claims.
    analysis = market_analyst_agent.get_full_spectrum_analysis(body.analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="Full-spectrum analysis not found.")
    match = next((s for s in analysis["suggestions"] if s["id"] == body.suggestion_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail="Suggestion not found on this analysis.")

    cfg_before = config_store.get()
    # Staleness check - see the identical guard on the series-apply route
    # above for the full reasoning.
    live_value = _config_value_at_path(cfg_before, match["config_path"])
    if live_value != match["current_value"]:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Stale suggestion - {match['config_path']} is now {live_value!r}, not the "
                f"{match['current_value']!r} this suggestion was based on. Re-run the analysis and try again."
            ),
        )
    fp_before = config_performance.fingerprint(cfg_before)
    section, _, field = match["config_path"].partition(".")
    config_store.update({section: {field: match["suggested_value"]}})
    fp_after = config_performance.fingerprint(config_store.get())
    config_performance.log_applied_change(
        config_path=match["config_path"], old_value=match["current_value"], new_value=match["suggested_value"],
        rationale=match["rationale"], trade_count=0,
        fingerprint_before=fp_before, fingerprint_after=fp_after, auto_applied=False, source="full-spectrum-analyst",
    )
    bump_generation()
    return {"applied": match, "new_config": config_store.get()}


@router.get("/api/series-evaluator/status")
async def get_series_evaluator_status():
    # Every series ever evaluated, independent of what's on the *current*
    # watchlist - direct request: the log/history the user wanted, doubling
    # as the persisted series_status table itself (see services/
    # series_evaluator.py). Always safe to call regardless of enabled -
    # same "history stays visible after a feature's turned off" idiom as
    # market_analyst's own status route above.
    se_cfg = config_store.get().get("series_evaluator") or {}
    series_rows = _series_evaluator_overview_with_crosscheck(config_store.get())
    return {"enabled": bool(se_cfg.get("enabled")), "series": series_rows}


class SeriesEvaluatorResetBody(BaseModel):
    series: str


@router.post("/api/series-evaluator/reset")
async def post_series_evaluator_reset(body: SeriesEvaluatorResetBody):
    # The manual "Re-evaluate" action - a deliberate fresh start (clears
    # strike_count too, see series_evaluator.reset's own docstring), not a
    # continuation of prior escalation. Doesn't force-pin the series back
    # onto the watchlist - normal volume/live-status ranking still decides
    # whether it actually reappears.
    ok = series_evaluator.reset(body.series)
    if not ok:
        raise HTTPException(status_code=404, detail=f"No series_evaluator record for {body.series!r}")
    return {"ok": True, "series": body.series}
