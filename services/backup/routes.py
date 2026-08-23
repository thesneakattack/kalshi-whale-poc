"""
Backup status/history/manual-trigger routes - the informativeness half of
services/backup/backup.py (CLAUDE.md's "surface enough of its own behavior
for a human to trust and reason about it, not just run it blind" applied
to a mechanism whose whole job is to sit quietly in the background until
the day it's needed).
"""
import asyncio

from fastapi import APIRouter

from services.app_state import state
from services.backup import backup
from services.config_store import config_store

router = APIRouter()


@router.get("/api/backup/status")
async def get_backup_status():
    backup_state = state["backup"]
    last = backup.latest()
    snapshot_count = len(list(backup.BACKUP_DIR.iterdir())) if backup.BACKUP_DIR.exists() else 0
    return {
        "enabled": (config_store.get().get("backup") or {}).get("enabled", True),
        "running": backup_state["running"],
        "last_started_at": backup_state["last_started_at"] or None,
        "last_run": last,
        "snapshot_count": snapshot_count,
    }


@router.get("/api/backup/history")
async def get_backup_history(limit: int = 20):
    return {"runs": backup.recent(limit=limit)}


@router.post("/api/backup/run")
async def trigger_backup_run():
    """Manual, synchronous trigger - an operator gets the real result back
    immediately (files backed up, bytes, any errors), not just a fire-and-
    forget acknowledgement, since the whole point of running this by hand
    is usually "I want to know this succeeded before I do something risky."
    Runs off the event loop the same way the periodic path does
    (asyncio.to_thread) so it doesn't stall the trading loop or other
    in-flight requests for however long the backup takes."""
    backup_cfg = config_store.get().get("backup") or {}
    retention_count = backup_cfg.get("retention_count", backup._DEFAULT_RETENTION_COUNT)
    return await asyncio.to_thread(backup.run_backup_cycle, retention_count)
