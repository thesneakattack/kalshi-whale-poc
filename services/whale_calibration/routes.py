"""
Whale-calibration routes - moved out of services/analytics/routes.py
(2026-08-22 modularization pass, Phase 2/9), per the already-queued
ROADMAP.md item to split advisory and whale calibration into their own
modules rather than lumping them under one generic "analytics" umbrella.
confidence_calibration.py/calibration_history.py have zero cross-imports
with any other analytics sibling (checked directly before this split), the
cleanest of the modules pulled out this pass.
"""
import asyncio
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import signal_log
from services.config import config_performance
from services.app_state import bump_generation
from services.config.config_store import config_store
from services.quality import evidence_provenance
from services.whale_calibration import calibration_history, confidence_calibration

router = APIRouter()

_REPORT_CACHE_TTL_SEC = 30  # 2026-09-03, Task 6b of docs/superpowers/
# plans/2026-09-03-tier1-backend-hygiene.md: resolved_signals_with_
# factors() cannot be query-bounded (it's a total-sample gate, not a
# recency-scoped read - verified in this task's own research). 30s matches
# Task 2's own History-tab de-poll interval for this exact route -
# coordinated, not independently chosen.
_report_cache: dict = {"cached_at": None, "value": None}

# Same typed-confirmation-phrase gate as real trading - auto-applying a
# config change with no human in the loop is a genuinely consequential
# action. See services/analytics/routes.py's ADVISORY_AUTO_APPLY_
# CONFIRMATION_PHRASE for the sibling advisory-side constant/route.
CALIBRATION_AUTO_APPLY_CONFIRMATION_PHRASE = "ENABLE CALIBRATION AUTO APPLY"


class EnableAutoApplyBody(BaseModel):
    confirmation_phrase: str


async def _build_report_async(cc_cfg: dict, current_weights) -> dict:
    """Builds the calibration report without occupying a tick_executor
    worker. Issue #410, implementing docs/superpowers/specs/2026-09-04-
    issue-410-pool-vs-aiosqlite-design.md.

    ONE helper for what were two byte-identical `def _build_report()`
    closures - the polled GET /api/confidence-calibration/report and the
    human-initiated POST /api/confidence-calibration/apply. The design names
    "_build_report()" in the singular; there were in fact two, both on
    tick_executor, and converting only the polled one would have left a
    second ~7s blocking occupant on a 2-worker pool shared with
    candidate_ledger.claim()/record_decision() on the live per-signal
    decision path. A deliberate, stated extension of that design's scope,
    not a silent one.

    The work splits in two, and BOTH halves must stay off the event loop:

      SQL fetch ....................... 0.884s  -> aiosqlite (yields natively)
      json.loads + dict build ......... 2.691s  -> asyncio.to_thread
      _bucket_win_rates x 9 factors ... 4.383s  -> asyncio.to_thread

    (measured at implementation time against the live 295,807-row table;
    the design's Sec 6 asks for exactly this re-confirmation. Its Sec 1
    put the compute pass at ~3.4s - the real figure is 4.383s, stated here
    rather than quietly rounded to the design's number.)

    The middle line is the one the design's Sec 1 table hides: it folds
    json.loads into the word "fetch", so implementing that table literally
    would move 0.9s off-thread and drop 2.7s of GIL-holding work ONTO the
    event loop - a regression, and precisely the trap
    services/diagnostics/_aio_db.py's docstring already records for
    run_offline(). signal_log.resolved_signals_with_factors_async() is what
    keeps both halves off the loop; see its docstring for why the default
    executor is safe here (20 workers, no *sustained* trading hot-path
    contention - verified, not assumed)."""
    rows = await signal_log.resolved_signals_with_factors_async()
    return await asyncio.to_thread(
        confidence_calibration.generate_calibration_report,
        rows, cc_cfg["min_resolved_signals"], current_weights,
    )


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
        "evidence_provenance": evidence_provenance.current_completeness_state(),
    }


@router.get("/api/confidence-calibration/report")
async def get_confidence_calibration_report():
    # Always safe to call regardless of confidence_calibration.enabled - the
    # data-threshold gate lives inside generate_calibration_report() itself
    # (services/whale_calibration/confidence_calibration.py), not here,
    # matching advisory's own route-level pattern.
    cc_cfg = config_store.get()["confidence_calibration"]
    if not cc_cfg["enabled"]:
        return {
            "report": None, "gated_reason": "confidence calibration is disabled", "resolved_count": None,
            "evidence_provenance": evidence_provenance.current_completeness_state(),
        }

    # Was offloaded via tick_executor (2026-08-26 fix, ROADMAP.md's event-
    # loop-stall entry) - proven live via a py-spy stack trace to run the
    # fetch (signal_log.resolved_signals_with_factors, one JSON-parse per
    # row) and the per-factor bucket analysis (confidence_calibration.
    # _bucket_win_rates, one sort+filter pass per factor) directly on the
    # event loop, on every dashboard poll of this route.
    #
    # Now split between aiosqlite and the default executor instead, and off
    # tick_executor entirely - issue #410, see _build_report_async below.
    current_weights = config_store.get().get("whale_confidence_weights")

    now = time.time()
    if _report_cache["cached_at"] is not None and (now - _report_cache["cached_at"]) < _REPORT_CACHE_TTL_SEC:
        result = dict(_report_cache["value"])
    else:
        result = await _build_report_async(cc_cfg, current_weights)
        # Stamped at COMPLETION, not the `now` captured on request receipt
        # above - same issue #410 cache-alignment bug as services/analytics/
        # routes.py's _population_gates_cache (docs/superpowers/research/
        # 2026-09-04-issue-410-tick-executor-measurement.md Sec 3.4).
        # _build_report_async()'s real cost is ~8.0s, re-measured 2026-09-04
        # against the live 295,807-row table (0.884s SQL + 2.691s json.loads
        # + 4.383s bucket/factor pass), so it burned a smaller but still
        # material share of its own 30s TTL before the entry was written.
        # This route is polled in the same batch as candidate-log/summary by
        # refreshHistoryInsightsIfActive(); before issue #410 both missing
        # together could occupy both tick_executor workers at once, which is
        # what made the alignment urgent. Neither route touches that pool any
        # more, so the alignment now just halves the miss rate rather than
        # protecting the trade path.
        _report_cache["cached_at"] = time.time()
        _report_cache["value"] = result
    result["evidence_provenance"] = evidence_provenance.current_completeness_state()
    return result


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

    # Same _build_report_async as the polled report route above (issue
    # #410). This route is human-initiated rather than polled, so it is the
    # less frequent of the two - but a click still cost ~7s of one
    # tick_executor worker, on a 2-worker pool shared with
    # candidate_ledger.claim()/record_decision(). The design named
    # "_build_report()" singular; there were two identical copies, and
    # converting only one would have left this occupancy in place.
    result = await _build_report_async(cc_cfg, current_weights)
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
