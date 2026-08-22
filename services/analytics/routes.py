"""
Analytics routes - "what should change": advisory/calibration
recommendations, regime segmentation, backtesting, and the market-analyst
LLM agent. Extracted 2026-08-22 as part of main.py's modularization pass,
following the routers/diagnostics_routes.py convention: an APIRouter,
shared state from services.app_state only, main.py does
app.include_router(...) at the same paths as before.

The real market-analyst orchestration logic lives in
market_analyst_orchestrator.py, imported here - these route handlers are
thin wrappers, same shape as every other route in this file.
"""
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import (
    advisory_engine, backtest, calibration_history, candidate_log, confidence_calibration,
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


class ApplyRecommendationBody(BaseModel):
    id: str


@router.get("/api/advisory/status")
async def get_advisory_status():
    # Honest progress reporting even while gated (docs/advisory-engine-plan.md
    # §3, layer 2) - this never leaks a real recommendation early, but it's
    # useful to show "18/30 resolved trades" while waiting, same real-data-
    # or-honest-fallback idiom as the rest of this app.
    adv_cfg = config_store.get()["advisory"]
    cfg = config_store.get()
    current_fp = config_performance.fingerprint(cfg)
    all_rows = trade_analytics.build_trade_history([t.to_dict() for t in broker.trade_log])
    summaries = advisory_engine.variant_summaries(all_rows)
    known_variants = config_performance.all_variants()
    min_resolved = adv_cfg["min_resolved_trades_per_variant"]
    variants_out = []
    for v in known_variants:
        fp = v["fingerprint"]
        resolved = summaries.get(fp, {}).get("total_closed", 0)
        variants_out.append({
            "fingerprint": fp,
            "first_seen_at": v["first_seen_at"],
            "resolved_count": resolved,
            "ready": resolved >= min_resolved,
            "is_current": fp == current_fp,
        })
    return {
        "enabled": adv_cfg["enabled"],
        "min_resolved_trades_per_variant": min_resolved,
        "auto_apply_enabled": adv_cfg["auto_apply_enabled"],
        "current_fingerprint": current_fp,
        "variants": variants_out,
    }


# Real bug found live (2026-08-10): advisory.auto_apply_enabled was already
# protected from generic /api/config edits, with an error message pointing
# at these exact two routes - but they never actually existed, and nothing
# anywhere read auto_apply_min_confidence/auto_apply_cooldown_sec either.
# The feature was reachable from no path at all. Fixed alongside adding the
# equivalent for confidence_calibration (direct request: "i want the option
# to enable auto whale-signal calibration... have them auto-enable and
# start getting put into play with my whole system once there *is* enough
# data") - same typed-confirmation-phrase gate as real trading, since
# auto-applying a config change with no human in the loop is a genuinely
# consequential action, not a plain checkbox.
ADVISORY_AUTO_APPLY_CONFIRMATION_PHRASE = "ENABLE ADVISORY AUTO APPLY"
CALIBRATION_AUTO_APPLY_CONFIRMATION_PHRASE = "ENABLE CALIBRATION AUTO APPLY"


class EnableAutoApplyBody(BaseModel):
    confirmation_phrase: str


@router.post("/api/advisory/auto-apply/enable")
async def enable_advisory_auto_apply(body: EnableAutoApplyBody):
    if body.confirmation_phrase != ADVISORY_AUTO_APPLY_CONFIRMATION_PHRASE:
        raise HTTPException(
            status_code=400,
            detail=f'Confirmation phrase did not match. Type exactly: "{ADVISORY_AUTO_APPLY_CONFIRMATION_PHRASE}"',
        )
    old_value = config_store.get()["advisory"]["auto_apply_enabled"]
    fp = config_performance.fingerprint(config_store.get())
    config_store.update({"advisory": {"auto_apply_enabled": True}})
    config_performance.log_applied_change(
        config_path="advisory.auto_apply_enabled", old_value=old_value, new_value=True,
        rationale="Enabled via the typed advisory-auto-apply confirmation phrase.", trade_count=0,
        fingerprint_before=fp, fingerprint_after=fp, auto_applied=False, source="manual",
    )
    bump_generation()
    return {"auto_apply_enabled": True}


@router.post("/api/advisory/auto-apply/disable")
async def disable_advisory_auto_apply():
    # Disabling never needs the confirmation phrase - same asymmetric
    # safety convention as real trading (enabling something consequential
    # needs friction, turning it back off shouldn't).
    old_value = config_store.get()["advisory"]["auto_apply_enabled"]
    fp = config_performance.fingerprint(config_store.get())
    config_store.update({"advisory": {"auto_apply_enabled": False}})
    if old_value:
        config_performance.log_applied_change(
            config_path="advisory.auto_apply_enabled", old_value=True, new_value=False,
            rationale="Disabled via the dashboard.", trade_count=0,
            fingerprint_before=fp, fingerprint_after=fp, auto_applied=False, source="manual",
        )
    bump_generation()
    return {"auto_apply_enabled": False}


@router.post("/api/confidence-calibration/auto-apply/enable")
async def enable_calibration_auto_apply(body: EnableAutoApplyBody):
    if body.confirmation_phrase != CALIBRATION_AUTO_APPLY_CONFIRMATION_PHRASE:
        raise HTTPException(
            status_code=400,
            detail=f'Confirmation phrase did not match. Type exactly: "{CALIBRATION_AUTO_APPLY_CONFIRMATION_PHRASE}"',
        )
    old_value = config_store.get()["confidence_calibration"]["auto_apply_enabled"]
    fp = config_performance.fingerprint(config_store.get())
    config_store.update({"confidence_calibration": {"auto_apply_enabled": True}})
    config_performance.log_applied_change(
        config_path="confidence_calibration.auto_apply_enabled", old_value=old_value, new_value=True,
        rationale="Enabled via the typed calibration-auto-apply confirmation phrase.", trade_count=0,
        fingerprint_before=fp, fingerprint_after=fp, auto_applied=False, source="manual",
    )
    bump_generation()
    return {"auto_apply_enabled": True}


@router.post("/api/confidence-calibration/auto-apply/disable")
async def disable_calibration_auto_apply():
    old_value = config_store.get()["confidence_calibration"]["auto_apply_enabled"]
    fp = config_performance.fingerprint(config_store.get())
    config_store.update({"confidence_calibration": {"auto_apply_enabled": False}})
    if old_value:
        config_performance.log_applied_change(
            config_path="confidence_calibration.auto_apply_enabled", old_value=True, new_value=False,
            rationale="Disabled via the dashboard.", trade_count=0,
            fingerprint_before=fp, fingerprint_after=fp, auto_applied=False, source="manual",
        )
    bump_generation()
    return {"auto_apply_enabled": False}


@router.get("/api/advisory/recommendations")
async def get_advisory_recommendations():
    # Always safe to call regardless of advisory.enabled - the per-variant
    # data-threshold gate lives inside advisory_engine.generate_recommendations
    # itself (docs/advisory-engine-plan.md §3, layer 2), not here, so there's
    # no route-level check that could accidentally be the only thing standing
    # between an under-sampled variant and a real recommendation.
    adv_cfg = config_store.get()["advisory"]
    if not adv_cfg["enabled"]:
        return {"recommendations": [], "gated_reason": "advisory engine is disabled", "resolved_count": None}

    cfg = config_store.get()
    current_fp = config_performance.fingerprint(cfg)
    all_rows = trade_analytics.build_trade_history([t.to_dict() for t in broker.trade_log])
    market_rows = trade_analytics.build_trade_history([t.to_dict() for t in market_broker.trade_log])
    variants = {v["fingerprint"]: v for v in config_performance.all_variants()}
    result = advisory_engine.generate_recommendations(
        all_rows, cfg, current_fp, variants, adv_cfg["min_resolved_trades_per_variant"], market_rows=market_rows,
        gate_summaries=candidate_log.gate_summary(),
        last_applied_by_path=config_performance.all_last_applied_by_path(),
        series_evaluator_rows=_series_evaluator_overview_with_crosscheck(cfg),
        category_rows=regime_analytics.by_category(all_rows),
        declined_ids=suggestion_decisions.declined_ids(),
    )
    return result


@router.post("/api/advisory/recommendations/apply")
async def apply_advisory_recommendation(body: ApplyRecommendationBody):
    # Manual apply path (docs/advisory-engine-plan.md §4) - always available
    # regardless of auto_apply_enabled, always a human-initiated click.
    # Recommendations are recomputed fresh here rather than trusting
    # whatever the request body claims a value should be - only a
    # recommendation this call just derived itself can ever be applied.
    adv_cfg = config_store.get()["advisory"]
    if not adv_cfg["enabled"]:
        raise HTTPException(status_code=400, detail="advisory engine is disabled")

    cfg = config_store.get()
    current_fp = config_performance.fingerprint(cfg)
    all_rows = trade_analytics.build_trade_history([t.to_dict() for t in broker.trade_log])
    market_rows = trade_analytics.build_trade_history([t.to_dict() for t in market_broker.trade_log])
    variants = {v["fingerprint"]: v for v in config_performance.all_variants()}
    result = advisory_engine.generate_recommendations(
        all_rows, cfg, current_fp, variants, adv_cfg["min_resolved_trades_per_variant"], market_rows=market_rows,
        gate_summaries=candidate_log.gate_summary(),
        last_applied_by_path=config_performance.all_last_applied_by_path(),
        series_evaluator_rows=_series_evaluator_overview_with_crosscheck(cfg),
        category_rows=regime_analytics.by_category(all_rows),
        declined_ids=suggestion_decisions.declined_ids(),
    )
    match = next((r for r in result["recommendations"] if r["id"] == body.id), None)
    if match is None:
        raise HTTPException(
            status_code=404,
            detail="recommendation not found - it may be stale (config or trade history changed since it was fetched)",
        )

    # config_path is always exactly "<top-level section>.<field>" (see every
    # suggestion function in advisory_engine.py) - strategy.* and, since
    # 2026-08-10's unified engine, market_strategy.* too, so this can no
    # longer assume "strategy." is the only prefix a recommendation carries.
    section, _, field = match["config_path"].partition(".")
    config_store.update({section: {field: match["suggested_value"]}})
    new_fp = config_performance.fingerprint(config_store.get())
    config_performance.log_applied_change(
        config_path=match["config_path"], old_value=match["current_value"], new_value=match["suggested_value"],
        rationale=match["rationale"], trade_count=match["n"],
        fingerprint_before=current_fp, fingerprint_after=new_fp, auto_applied=False, source="unified-advisory",
    )
    bump_generation()
    return {"applied": match, "new_config": config_store.get()["strategy"]}


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


@router.get("/api/advisory/applied-changes")
async def get_advisory_applied_changes(limit: int = 50, offset: int = 0):
    # Effect tracking (Item 3D, 2026-08-10): a strategy.* change already
    # gets a real fingerprint transition logged (fingerprint_before/after) -
    # attach the same before/after win-rate + realized-P&L a cross-variant
    # recommendation already computes, via variant_summaries() over the
    # *current* full trade history (not a snapshot from when the change was
    # applied), so this reflects everything resolved since, not just what
    # existed the moment it was logged. Scoped to config_path.startswith
    # ("strategy.") specifically, not just "fingerprint changed" - a manual
    # patch can touch a strategy.* field and a non-fingerprinted field (e.g.
    # risk.*) in the same save, and only the strategy.* row's own change is
    # what actually caused that transition.
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    changes = config_performance.recent_applied_changes(limit=limit, offset=offset)
    all_rows = trade_analytics.build_trade_history([t.to_dict() for t in broker.trade_log])
    market_rows = trade_analytics.build_trade_history([t.to_dict() for t in market_broker.trade_log])
    summaries = advisory_engine.variant_summaries(all_rows)
    for c in changes:
        c["effect"] = (
            advisory_engine.change_effect(c["fingerprint_before"], c["fingerprint_after"], summaries)
            if c["config_path"].startswith("strategy.") else None
        )
        # Gap 3 of docs/config-tuning-data-gaps-2026-08-10.md - a looser,
        # complementary measurement alongside the strict one above: works
        # for any config_path (not just strategy.*, and with no fingerprint-
        # transition requirement), and isn't starved by fingerprint
        # fragmentation since it counts every trade before/after applied_at
        # regardless of which exact config variant produced it. Picks
        # market_broker's own trade history for market_strategy.* changes -
        # that strategy's trades never appear in the whale-follow broker's
        # own log at all.
        rows_for_path = market_rows if c["config_path"].startswith("market_strategy.") else all_rows
        c["effect_windowed"] = advisory_engine.change_effect_windowed(c["config_path"], c["applied_at"], rows_for_path)
    return {
        "changes": changes,
        "total": config_performance.applied_changes_count(),
    }


@router.get("/api/confidence-calibration/status")
async def get_confidence_calibration_status():
    # Same "honest progress even while gated" idiom as /api/advisory/status -
    # never leaks a real report early, but useful to show real progress
    # toward the threshold while waiting.
    cc_cfg = config_store.get()["confidence_calibration"]
    resolved_count = len(signal_log.resolved_signals_with_factors())
    return {
        "enabled": cc_cfg["enabled"],
        "min_resolved_signals": cc_cfg["min_resolved_signals"],
        "resolved_count": resolved_count,
        "ready": resolved_count >= cc_cfg["min_resolved_signals"],
        "auto_apply_enabled": cc_cfg.get("auto_apply_enabled", False),
    }


@router.get("/api/confidence-calibration/report")
async def get_confidence_calibration_report():
    # Always safe to call regardless of confidence_calibration.enabled - the
    # data-threshold gate lives inside generate_calibration_report() itself
    # (services/confidence_calibration.py), not here, matching advisory's
    # own route-level pattern.
    cc_cfg = config_store.get()["confidence_calibration"]
    if not cc_cfg["enabled"]:
        return {"report": None, "gated_reason": "confidence calibration is disabled", "resolved_count": None}

    rows = signal_log.resolved_signals_with_factors()
    current_weights = config_store.get().get("whale_confidence_weights")
    return confidence_calibration.generate_calibration_report(rows, cc_cfg["min_resolved_signals"], current_weights)


@router.post("/api/confidence-calibration/apply")
async def apply_confidence_calibration_suggestion():
    # Manual apply path (2026-08-17 direct report: "it doesn't auto apply.
    # doesn't update its values on the frontend, doesn't actually refine
    # itself over time. just does nothing"). Before this route, the ONLY
    # way suggested_weights could ever reach the live config was the
    # auto-apply toggle - itself gated behind a typed confirmation phrase,
    # a resolved-signal floor, AND only even checked once per
    # snapshot_interval_sec (6h default) with a further
    # auto_apply_cooldown_sec (24h default) between real applies. A human
    # already looking at a good suggestion in the report table had no way
    # to act on it except retyping every factor by hand into the Config
    # tab. Same manual-apply shape as apply_advisory_recommendation above:
    # always available regardless of auto_apply_enabled, always a
    # human-initiated click, recomputes the report fresh rather than
    # trusting anything the request claims.
    cc_cfg = config_store.get()["confidence_calibration"]
    if not cc_cfg["enabled"]:
        raise HTTPException(status_code=400, detail="confidence calibration is disabled")

    cfg = config_store.get()
    current_fp = config_performance.fingerprint(cfg)
    rows = signal_log.resolved_signals_with_factors()
    current_weights = cfg.get("whale_confidence_weights") or {}
    result = confidence_calibration.generate_calibration_report(rows, cc_cfg["min_resolved_signals"], current_weights)
    if result["report"] is None:
        raise HTTPException(status_code=400, detail=result["gated_reason"])

    blended = confidence_calibration.blended_weights_for_auto_apply(
        current_weights, result["report"].get("suggested_weights"),
    )
    if blended is None or blended == current_weights:
        raise HTTPException(status_code=400, detail="no discriminating factor yet - nothing to apply")

    config_store.update({"whale_confidence_weights": blended})
    new_fp = config_performance.fingerprint(config_store.get())
    config_performance.log_applied_change(
        config_path="whale_confidence_weights", old_value=current_weights, new_value=blended,
        rationale=(
            f"Manually applied calibration-suggested weights "
            f"(n={result['report']['resolved_count']} resolved signals)."
        ),
        trade_count=result["report"]["resolved_count"],
        fingerprint_before=current_fp, fingerprint_after=new_fp, auto_applied=False, source="calibration-manual",
    )
    bump_generation()
    return {"applied": True, "new_weights": blended}


@router.get("/api/confidence-calibration/history")
async def get_confidence_calibration_history(limit: int = 100):
    # services/calibration_history.py - Gap 6 of docs/config-tuning-data-
    # gaps-2026-08-10.md. Always safe to call - the trend line these
    # snapshots build up, distinct from the live report above.
    limit = min(max(limit, 1), 500)
    return {"snapshots": calibration_history.history(limit=limit)}


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
