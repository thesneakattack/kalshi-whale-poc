"""
Unified, local-only "is the system healthy" endpoint - composes existing
read-only surfaces (services/diagnostics, services/observability,
services/alerting, services/fault_log, services/storage_health) into one
response instead of a human needing to know which of five separate routes
to check. Quality Control Plane Tasks 10-11 (docs/superpowers/plans/
2026-08-24-quality-control-plane.md); see this package's README.md.

Deliberately composes only sources that are already local/read-only -
diagnostics.run_offline() itself explicitly excludes the one diagnostic
that makes a real Kalshi call (check_coverage), and storage_health's own
inventory is the fast-tier (no COUNT(*), no PRAGMA quick_check) path, never
the explicit deep scan/integrity check. No source here does any network
I/O; tests/test_quality_routes.py proves that by monkeypatching
KalshiClient construction to raise and confirming the route still
succeeds.

Event-loop dispatch (issue #530, 2026-09-04): seven of this route's own
calls were synchronous DB/file reads made directly in the async body with
no dispatch - observability.runtime_findings (via history()),
storage_health.inventory_data_dir, backup.latest, storage_health.
storage_findings (which itself calls observability.history() once per
data/*.db entry via storage_growth_finding - a second level of
indirection, same shape as runtime_findings'), alerting.active_alerts,
research.latest, and fault_log.summary. Each is now wrapped in
asyncio.to_thread, the same mechanism services/storage_health/routes.py's
own integrity-check/deep-scan routes already use successfully for this
exact class of problem (a diagnostic read that must stay off the event
loop but must NOT share services/tick_executor.py's dedicated 2-worker
pool - issue #510's research rejected sharing that pool for a slow
diagnostic call once already, PR #409, because it starved trading-critical
writes for 5+ hours). asyncio.to_thread uses the asyncio default loop
executor, a pool tick_executor never touches, so this carries none of that
risk. Deliberately NOT wrapped, each confirmed by direct read: alerting.
alert_findings (pure computation over the already-fetched active_alerts
list) and evidence_provenance.findings (its whole call graph - three
snapshot()/dropped_count() reads - is in-memory counters, no I/O);
diagnostics.run_offline is already awaited and runs natively on aiosqlite
(a separate, already-fixed piece - see its own comment below), so
wrapping it here would add a redundant thread-hop, not fix anything.

Direct live measurement before this fix (docker exec against the running
app's real data/*.db files, one-shot, read-only): the four calls named in
issue #530's own census doc cost roughly 1-40ms each today (alerting.
active_alerts 1.5ms, fault_log.summary 36.7ms, research.latest 1.9ms,
observability.runtime_findings 0.6ms) - genuinely cheap, not the dominant
cost the census doc's "11.59-33.35s" route-latency figure implied. That
figure is almost entirely diagnostics.run_offline() alone, measured at
~9.1s against the same live data - already awaited/non-blocking, a
separate concern this fix does not touch. This fix removes real
event-loop-blocking time (roughly 40-100ms x 358 calls in the census
doc's 19.6h window, plus whatever storage_findings' N-database indirect
history() calls and backup.latest() add - not separately measured pre-fix),
but does not make GET /api/quality/summary itself fast; it was never
going to, and no fix in this scope claims otherwise.
"""
import asyncio
import time

from fastapi import APIRouter

from services import fault_log
from services.alerting import alerting
from services.app_state import index_stream, state, trade_stream
from services.backup import backup
from services.config.config_store import config_store
from services.diagnostics import diagnostics
from services.observability import observability
from services.quality import evidence_provenance
from services.quality.models import QualityReport
from services.research import research
from services.storage_health import storage_health

router = APIRouter()


@router.get("/api/quality/summary")
async def get_quality_summary():
    cfg = config_store.get()
    findings = await asyncio.to_thread(
        observability.runtime_findings, cfg, state, trade_stream, index_stream
    )
    storage_entries = await asyncio.to_thread(storage_health.inventory_data_dir, storage_health.DATA_DIR)
    backup_cfg = cfg.get("backup") or {}
    last_backup_run = await asyncio.to_thread(backup.latest)
    findings += await asyncio.to_thread(
        storage_health.storage_findings,
        storage_entries,
        last_backup_run=last_backup_run,
        backup_interval_sec=backup_cfg.get("interval_sec", backup._DEFAULT_INTERVAL_SEC),
    )
    # One DB read serves both the raw `alerts` field below and the
    # alert-derived findings that let a critical alert drive `status` (#71).
    active_alerts = await asyncio.to_thread(alerting.active_alerts)
    findings += alerting.alert_findings(active_alerts)
    findings += evidence_provenance.findings()
    report = QualityReport(findings=findings)
    # Deliberately just the timestamp/running flag, never the full report
    # (services/research/research.py's own build_report composes seven other
    # modules' worth of analytics into one JSON blob - repeating that here on
    # every /api/quality/summary poll would be exactly the kind of "recompute
    # something expensive on every read" this route otherwise avoids
    # everywhere else). GET /api/research/latest is the place for the real
    # report.
    research_state = state.setdefault("research", {"running": False, "task": None, "checkpoints": None})
    last_research = await asyncio.to_thread(research.latest)
    return {
        "generated_at": time.time(),
        "status": report.overall_status(),
        "counts": report.counts(),
        "findings": [f.to_dict() for f in findings],
        # No dedicated pool needed here (event-loop-blocking-fix2-diagnostics-
        # widening, docs/superpowers/specs/2026-09-01-event-loop-blocking-
        # elimination-design.md) - run_offline()'s entire call graph
        # (services/diagnostics/diagnostics.py's own checks plus
        # series_watcher.check_series_funnel() -> funnel()'s raw_trades
        # aggregate query) now runs natively on the asyncio event loop via
        # aiosqlite, so there's nothing left for a thread-pool offload to
        # protect against. Previously offloaded first via tick_executor
        # (2026-08-27 fix, subscription-churn investigation CH2 - see
        # docs/superpowers/research/2026-08-25-realtime-data-plane-known-
        # findings.md's H11 entry), then via a dedicated services/diagnostics/
        # _diagnostics_pool.py (write-path capacity fix Task 8, 2026-09-01,
        # deleted by this fix) once run_offline() grew expensive enough to
        # permanently occupy both of tick_executor's 2 workers and starve the
        # trading-critical writes (capture_writer, candidate_log) that pool
        # exists to protect - confirmed live 2026-09-01, see
        # docs/superpowers/specs/2026-09-01-whale-scoring-connection-reuse-
        # design.md section 1b/4a. Two structurally identical siblings still
        # share tick_executor as of this fix and are NOT addressed here
        # (candidate_log.population_gate_summary in services/analytics/
        # routes.py, whale_calibration/routes.py's _build_report) - tracked
        # separately, issue #410, pending their own measurement before any
        # fix (their query cost hasn't been confirmed comparable to
        # run_offline()'s).
        "diagnostics": await diagnostics.run_offline(cfg),
        "alerts": {"active": active_alerts},
        "faults": await asyncio.to_thread(fault_log.summary),
        "storage": {"databases": storage_entries},
        "research": {
            "running": research_state["running"],
            "last_report_at": last_research["generated_at"] if last_research is not None else None,
        },
    }
