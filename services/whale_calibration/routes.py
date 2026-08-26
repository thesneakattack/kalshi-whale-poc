"""
Whale-calibration routes - moved out of services/analytics/routes.py
(2026-08-22 modularization pass, Phase 2/9), per the already-queued
ROADMAP.md item to split advisory and whale calibration into their own
modules rather than lumping them under one generic "analytics" umbrella.
confidence_calibration.py/calibration_history.py have zero cross-imports
with any other analytics sibling (checked directly before this split), the
cleanest of the modules pulled out this pass.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import config_performance, signal_log, tick_executor
from services.app_state import bump_generation
from services.config_store import config_store
from services.whale_calibration import calibration_history, confidence_calibration

router = APIRouter()

# Same typed-confirmation-phrase gate as real trading - auto-applying a
# config change with no human in the loop is a genuinely consequential
# action. See services/analytics/routes.py's ADVISORY_AUTO_APPLY_
# CONFIRMATION_PHRASE for the sibling advisory-side constant/route.
CALIBRATION_AUTO_APPLY_CONFIRMATION_PHRASE = "ENABLE CALIBRATION AUTO APPLY"


class EnableAutoApplyBody(BaseModel):
    confirmation_phrase: str


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


@router.get("/api/confidence-calibration/status")
async def get_confidence_calibration_status():
    # Same "honest progress even while gated" idiom as /api/advisory/status -
    # never leaks a real report early, but useful to show real progress
    # toward the threshold while waiting.
    # resolved_with_factors_count() (2026-08-26 fix, ROADMAP.md's event-
    # loop-stall entry) - this used to fetch+JSON-parse every resolved
    # signal with a factor breakdown just to call len() on the result,
    # proven live via a py-spy stack trace to cost real time on every
    # dashboard poll of this route. A plain COUNT(*) answers the same
    # question without materializing a single row in Python.
    cc_cfg = config_store.get()["confidence_calibration"]
    resolved_count = signal_log.resolved_with_factors_count()
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
    # (services/whale_calibration/confidence_calibration.py), not here,
    # matching advisory's own route-level pattern.
    cc_cfg = config_store.get()["confidence_calibration"]
    if not cc_cfg["enabled"]:
        return {"report": None, "gated_reason": "confidence calibration is disabled", "resolved_count": None}

    # Offloaded via tick_executor (2026-08-26 fix, ROADMAP.md's event-loop-
    # stall entry) - proven live via a py-spy stack trace to run the fetch
    # (signal_log.resolved_signals_with_factors, one JSON-parse per row)
    # and the per-factor bucket analysis (confidence_calibration._bucket_
    # win_rates, one sort+filter pass per factor) directly on the event
    # loop, on every dashboard poll of this route.
    current_weights = config_store.get().get("whale_confidence_weights")

    def _build_report():
        rows = signal_log.resolved_signals_with_factors()
        return confidence_calibration.generate_calibration_report(
            rows, cc_cfg["min_resolved_signals"], current_weights
        )

    return await tick_executor.run(_build_report)


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
    # tab. Same manual-apply shape as advisory's own apply route: always
    # available regardless of auto_apply_enabled, always a human-initiated
    # click, recomputes the report fresh rather than trusting anything the
    # request claims.
    cc_cfg = config_store.get()["confidence_calibration"]
    if not cc_cfg["enabled"]:
        raise HTTPException(status_code=400, detail="confidence calibration is disabled")

    cfg = config_store.get()
    current_fp = config_performance.fingerprint(cfg)
    current_weights = cfg.get("whale_confidence_weights") or {}

    def _build_report():
        rows = signal_log.resolved_signals_with_factors()
        return confidence_calibration.generate_calibration_report(
            rows, cc_cfg["min_resolved_signals"], current_weights
        )

    result = await tick_executor.run(_build_report)
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
    # services/whale_calibration/calibration_history.py - Gap 6 of
    # docs/config-tuning-data-gaps-2026-08-10.md. Always safe to call - the
    # trend line these snapshots build up, distinct from the live report
    # above.
    limit = min(max(limit, 1), 500)
    return {"snapshots": calibration_history.history(limit=limit)}
