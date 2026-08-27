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

from fastapi import APIRouter, Query

from services import fault_log
from services import quality_coordination as _qc
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


def _coordination_rollup() -> dict[str, int]:
    """Rolled-up coordination-item state counts for the /api/quality/summary composite
    read. Wrapped so a sqlite error here degrades to a safe default instead of 500ing the
    entire summary route - the same self-contained try/except-and-degrade idiom
    services/fault_log.py's own summary() already uses for the same "one broken
    sub-section shouldn't break the whole composite read" principle."""
    try:
        conn = _qc._connect()
        try:
            rows = conn.execute("SELECT state, COUNT(*) c FROM coordination_items GROUP BY state").fetchall()
            counts = {r["state"]: r["c"] for r in rows}
            return {
                "escalation_eligible": counts.get("escalation_eligible", 0),
                "suppressed": counts.get("suppressed_pending_work", 0),
                "observed": counts.get("observed", 0),
            }
        finally:
            conn.close()
    except Exception:
        return {"escalation_eligible": 0, "suppressed": 0, "observed": 0}


@router.get("/api/quality/coordination")
async def get_quality_coordination(limit: int = Query(200, ge=1, le=1000)):
    conn = _qc._connect()
    try:
        items = [
            dict(r) for r in conn.execute(
                "SELECT * FROM coordination_items ORDER BY last_observed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        ]
        keys = [item["automation_key"] for item in items]
        logs_by_key: dict[str, list[dict]] = {k: [] for k in keys}
        if keys:
            placeholders = ",".join("?" * len(keys))
            rows = conn.execute(
                f"""SELECT automation_key, at, message FROM (
                        SELECT automation_key, at, message,
                               ROW_NUMBER() OVER (
                                   PARTITION BY automation_key ORDER BY at DESC
                               ) AS rn
                        FROM coordination_log
                        WHERE automation_key IN ({placeholders})
                    ) WHERE rn <= 20
                    ORDER BY automation_key, at""",
                keys,
            ).fetchall()
            for r in rows:
                logs_by_key[r["automation_key"]].append({"at": r["at"], "message": r["message"]})
        for item in items:
            item["log"] = logs_by_key[item["automation_key"]]
        return {"items": items}
    finally:
        conn.close()


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
        "coordination": _coordination_rollup(),
        "research": {
            "running": research_state["running"],
            "last_report_at": last_research["generated_at"] if last_research is not None else None,
        },
    }
