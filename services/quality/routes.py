"""
Unified, local-only "is the system healthy" endpoint - composes existing
read-only surfaces (services/diagnostics, services/observability,
services/alerting, services/fault_log, services/storage_health) into one
response instead of a human needing to know which of five separate routes
to check. Quality Control Plane Tasks 10-11
(docs/archive/lane-6-observability-quality-safety/plans/2026-08-24-quality-control-plane.md,
moved there 2026-09-06, planning-lanes migration); see this package's README.md.

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
Also deliberately NOT wrapped: config_store.get() (the first line of this
handler) does its own synchronous stat()/YAML-reparse-on-change - a real,
undispatched 8th call, but identical in every other route in this app, so
a systemic fix belongs in config_store.py, not repeated per-handler here.

Direct live measurement (docker exec against the running app's real
data/*.db files, one-shot, read-only; re-measured after PR #552's
adversarial review found the DB files had grown since the first pass):
the seven calls this fix dispatches sum to roughly 100-300ms today
(fault_log.summary and storage_findings are the largest individually, at
~90-110ms each; the rest are single-digit-to-low-double-digit ms) - this
range will keep climbing as fault_log.db/observability.db grow, so treat
it as an order of magnitude, not a fixed number. Still ~30-90x smaller
than diagnostics.run_offline() alone, measured at ~9.1s against the same
live data. run_offline() yields control genuinely and frequently (~1,100
real awaited aiosqlite yields per call, corroborated independently by two
separate reviews) - but per _aio_db.py's own docstring it also has its
own pre-existing, undisclosed, out-of-scope contiguous on-loop CPU chunks
between those yields (>=280ms measured 2026-09-01, likely more now) that
this fix does not touch and were not previously surfaced against the
route's cost breakdown. This fix removes real event-loop-blocking time
from the 7 calls it dispatches, but does not make GET /api/quality/summary
itself fast, and does not close run_offline()'s own separate on-loop-CPU
gap; neither was ever this fix's scope.
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
        # widening, docs/archive/lane-1-kalshi-ingestion/specs/2026-09-01-event-loop-blocking-
        # elimination-design.md) - run_offline()'s entire call graph
        # (services/diagnostics/diagnostics.py's own checks plus
        # series_watcher.check_series_funnel() -> funnel()'s raw_trades
        # aggregate query) now runs natively on the asyncio event loop via
        # aiosqlite, so there's nothing left for a thread-pool offload to
        # protect against. Previously offloaded first via tick_executor
        # (2026-08-27 fix, subscription-churn investigation CH2 - see
        # docs/archive/lane-1-kalshi-ingestion/research/2026-08-25-realtime-data-plane-known-
        # findings.md's H11 entry), then via a dedicated services/diagnostics/
        # _diagnostics_pool.py (write-path capacity fix Task 8, 2026-09-01,
        # deleted by this fix) once run_offline() grew expensive enough to
        # permanently occupy both of tick_executor's 2 workers and starve the
        # trading-critical writes (capture_writer, candidate_log) that pool
        # exists to protect - confirmed live 2026-09-01, see
        # docs/archive/lane-5-runtime-infrastructure/specs/2026-09-01-whale-scoring-connection-reuse-design.md (moved there 2026-09-06, planning-lanes migration) section 1b/4a. Two structurally identical siblings still
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
