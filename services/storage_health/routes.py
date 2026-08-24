"""
Storage inventory, deep-scan, and integrity-check routes - the operator-
facing surface for services/storage_health/storage_health.py's read-only DB
diagnostics. Quality Control Plane Task 11; see this package's CHEATSHEET.md
and storage_health.py's own module docstring for the three cost tiers these
three routes map to.
"""
import asyncio
import time

from fastapi import APIRouter, HTTPException

from services.app_state import state
from services.storage_health import storage_health
from services import task_supervisor

router = APIRouter()


@router.get("/api/health/storage")
async def get_storage_health():
    """Fast path only - file stat + lightweight PRAGMAs, no COUNT(*) scans.
    Also surfaces the most recent deep scan's result (if any has ever run
    in this process) and whether one is currently in flight."""
    entries = storage_health.inventory_data_dir(storage_health.DATA_DIR)
    sh_state = state["storage_health"]
    return {
        "databases": entries,
        "scanning": sh_state["scanning"],
        "last_scan": sh_state.get("last_scan"),
    }


@router.post("/api/health/storage/scan")
async def trigger_storage_scan():
    """Manual deep scan (adds per-table COUNT(*)) - fire-and-forget through
    task_supervisor, same shape as market_catalog's/backup's own background
    scans. Guards against overlap: a scan already in flight is reported,
    not restarted."""
    sh_state = state["storage_health"]
    if sh_state["scanning"]:
        return {"scheduled": False, "already_running": True}
    sh_state["scanning"] = True
    sh_state["last_started_at"] = time.time()
    sh_state["task"] = task_supervisor.supervise(
        _deep_scan_background, component="storage_health", operation="scan",
    )
    return {"scheduled": True, "already_running": False}


async def _deep_scan_background() -> None:
    """Owns releasing the "scanning" flag regardless of outcome, same split
    as catalog_scan._scan_catalog_batch_background/backup._run_backup_
    background. asyncio.to_thread keeps the per-table COUNT(*) scans (the
    one genuinely non-trivial cost in this module) off the event loop."""
    sh_state = state["storage_health"]
    try:
        data_dir = storage_health.DATA_DIR
        paths = sorted(data_dir.glob("*.db")) if data_dir.exists() else []
        entries = await asyncio.to_thread(
            lambda: [storage_health.database_health(p, include_table_counts=True) for p in paths]
        )
        sh_state["last_scan"] = {"generated_at": time.time(), "databases": entries}
    finally:
        sh_state["scanning"] = False


@router.post("/api/health/storage/integrity-check")
async def trigger_integrity_check(name: str):
    """Explicit, single-database PRAGMA quick_check - never automatic, never
    batched across every db file in one call. asyncio.to_thread keeps a
    slow scan of a large file (series_watcher.db was measured at 6.9GB,
    static/status.html phase 126) off the event loop."""
    path = storage_health.resolve_db_path(storage_health.DATA_DIR, name)
    if path is None:
        raise HTTPException(status_code=400, detail="invalid database name")
    result = await asyncio.to_thread(storage_health.quick_check, path)
    return {"name": name, **result}
