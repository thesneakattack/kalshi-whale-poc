"""
Advisory routes - moved out of services/analytics/routes.py (2026-08-22
modularization pass, Phase 3/9), per the already-queued ROADMAP.md item to
split advisory and whale calibration into their own modules rather than
lumping them under one generic "analytics" umbrella.
/api/suggestions/decline|undecline|declined stay in services/analytics/
routes.py - shared infrastructure (services/suggestion_decisions.py) used
by both this module's recommendations and market-analyst's own
suggestions, not advisory-owned.

advisory_engine.py has exactly one real cross-import among the modules
that used to sit together under services/analytics/: services/
regime_analytics.py, used by _category_conditional_recommendations/
_series_conditional_recommendations. Kept as a plain `from services import
regime_analytics` here rather than dragged into this package - a
legitimate shared low-level dependency, the same way advisory_engine.py
already depends on services/trade_analytics.py and services/signal_log.py
without those moving either.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import candidate_log
from services.config import config_performance
from services.history import regime_analytics, suggestion_decisions, trade_analytics
from services.advisory import advisory_engine
from services.analytics.market_analyst_orchestrator import _series_evaluator_rows_for_advisory
from services.app_state import broker, bump_generation
from services.config.config_store import config_store
from services.quality import evidence_provenance

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
        "evidence_provenance": evidence_provenance.current_completeness_state(),
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
# consequential action, not a plain checkbox. See
# services/whale_calibration/routes.py's own CALIBRATION_AUTO_APPLY_
# CONFIRMATION_PHRASE for the sibling calibration-side constant/route.
ADVISORY_AUTO_APPLY_CONFIRMATION_PHRASE = "ENABLE ADVISORY AUTO APPLY"


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


@router.get("/api/advisory/recommendations")
async def get_advisory_recommendations():
    # Always safe to call regardless of advisory.enabled - the per-variant
    # data-threshold gate lives inside advisory_engine.generate_recommendations
    # itself (docs/advisory-engine-plan.md §3, layer 2), not here, so there's
    # no route-level check that could accidentally be the only thing standing
    # between an under-sampled variant and a real recommendation.
    adv_cfg = config_store.get()["advisory"]
    if not adv_cfg["enabled"]:
        return {
            "recommendations": [], "gated_reason": "advisory engine is disabled", "resolved_count": None,
            "evidence_provenance": evidence_provenance.current_completeness_state(),
        }

    cfg = config_store.get()
    current_fp = config_performance.fingerprint(cfg)
    all_rows = trade_analytics.build_trade_history([t.to_dict() for t in broker.trade_log])
    variants = {v["fingerprint"]: v for v in config_performance.all_variants()}
    result = advisory_engine.generate_recommendations(
        all_rows, cfg, current_fp, variants, adv_cfg["min_resolved_trades_per_variant"],
        gate_summaries=candidate_log.gate_summary(),
        last_applied_by_path=config_performance.all_last_applied_by_path(),
        series_evaluator_rows=_series_evaluator_rows_for_advisory(cfg),
        category_rows=regime_analytics.by_category(all_rows),
        declined_ids=suggestion_decisions.declined_ids(),
    )
    result["evidence_provenance"] = evidence_provenance.current_completeness_state()
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
    variants = {v["fingerprint"]: v for v in config_performance.all_variants()}
    result = advisory_engine.generate_recommendations(
        all_rows, cfg, current_fp, variants, adv_cfg["min_resolved_trades_per_variant"],
        gate_summaries=candidate_log.gate_summary(),
        last_applied_by_path=config_performance.all_last_applied_by_path(),
        series_evaluator_rows=_series_evaluator_rows_for_advisory(cfg),
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
    # suggestion function in advisory_engine.py).
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
        # regardless of which exact config variant produced it.
        c["effect_windowed"] = advisory_engine.change_effect_windowed(c["config_path"], c["applied_at"], all_rows)
    return {
        "changes": changes,
        "total": config_performance.applied_changes_count(),
    }
