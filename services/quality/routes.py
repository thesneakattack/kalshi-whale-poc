"""
Unified, local-only "is the system healthy" endpoint - composes existing
read-only surfaces (services/diagnostics, services/observability,
services/alerting, services/fault_log) into one response instead of a
human needing to know which of four separate routes to check. Quality
Control Plane Task 10 (docs/superpowers/plans/2026-08-24-quality-control-
plane.md); see this package's CHEATSHEET.md.

Deliberately composes only sources that are already local/read-only -
diagnostics.run_offline() itself explicitly excludes the one diagnostic
that makes a real Kalshi call (check_coverage). No source here does any
network I/O; tests/test_quality_routes.py proves that by monkeypatching
KalshiClient construction to raise and confirming the route still
succeeds.
"""
import time

from fastapi import APIRouter

from services import fault_log
from services.alerting import alerting
from services.app_state import index_stream, state, trade_stream
from services.config_store import config_store
from services.diagnostics import diagnostics
from services.observability import observability
from services.quality.models import QualityReport

router = APIRouter()


@router.get("/api/quality/summary")
async def get_quality_summary():
    cfg = config_store.get()
    findings = observability.runtime_findings(cfg, state, trade_stream, index_stream)
    report = QualityReport(findings=findings)
    return {
        "generated_at": time.time(),
        "status": report.overall_status(),
        "counts": report.counts(),
        "findings": [f.to_dict() for f in findings],
        "diagnostics": diagnostics.run_offline(cfg),
        "alerts": {"active": alerting.active_alerts()},
        "faults": fault_log.summary(),
    }
