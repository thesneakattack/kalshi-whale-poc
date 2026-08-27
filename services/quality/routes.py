"""
Unified, local-only "is the system healthy" endpoint - composes existing
read-only surfaces (services/diagnostics, services/observability,
services/alerting, services/fault_log, services/storage_health) into one
response instead of a human needing to know which of five separate routes
to check. Quality Control Plane Tasks 10-11 (docs/superpowers/plans/
2026-08-24-quality-control-plane.md); see this package's CHEATSHEET.md.

Deliberately composes only sources that are already local/read-only -
diagnostics.run_offline() itself explicitly excludes the one diagnostic
that makes a real Kalshi call (check_coverage), and storage_health's own
inventory is the fast-tier (no COUNT(*), no PRAGMA quick_check) path, never
the explicit deep scan/integrity check. No source here does any network
I/O; tests/test_quality_routes.py proves that by monkeypatching
KalshiClient construction to raise and confirming the route still
succeeds.
"""
import time

from fastapi import APIRouter

from services import fault_log
from services.alerting import alerting
from services.app_state import index_stream, state, trade_stream
from services.backup import backup
from services.config_store import config_store
from services.diagnostics import diagnostics
from services.observability import observability
from services.quality.models import QualityReport
from services.research import research
from services.storage_health import storage_health

router = APIRouter()


@router.get("/api/quality/summary")
async def get_quality_summary():
    cfg = config_store.get()
    findings = observability.runtime_findings(cfg, state, trade_stream, index_stream)
    storage_entries = storage_health.inventory_data_dir(storage_health.DATA_DIR)
    backup_cfg = cfg.get("backup") or {}
    findings += storage_health.storage_findings(
        storage_entries,
        last_backup_run=backup.latest(),
        backup_interval_sec=backup_cfg.get("interval_sec", backup._DEFAULT_INTERVAL_SEC),
    )
    report = QualityReport(findings=findings)
    # Deliberately just the timestamp/running flag, never the full report
    # (services/research/research.py's own build_report composes seven other
    # modules' worth of analytics into one JSON blob - repeating that here on
    # every /api/quality/summary poll would be exactly the kind of "recompute
    # something expensive on every read" this route otherwise avoids
    # everywhere else). GET /api/research/latest is the place for the real
    # report.
    research_state = state.setdefault("research", {"running": False, "task": None, "checkpoints": None})
    last_research = research.latest()
    return {
        "generated_at": time.time(),
        "status": report.overall_status(),
        "counts": report.counts(),
        "findings": [f.to_dict() for f in findings],
        "diagnostics": diagnostics.run_offline(cfg),
        "alerts": {"active": alerting.active_alerts()},
        "faults": fault_log.summary(),
        "storage": {"databases": storage_entries},
        "research": {
            "running": research_state["running"],
            "last_report_at": last_research["generated_at"] if last_research is not None else None,
        },
    }
