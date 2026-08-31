"""
Backup status/history/manual-trigger routes - the informativeness half of
services/backup/backup.py (CLAUDE.md's "surface enough of its own behavior
for a human to trust and reason about it, not just run it blind" applied
to a mechanism whose whole job is to sit quietly in the background until
the day it's needed).

Two tiers as of 2026-08-30 (see backup.py's own module docstring): this
file surfaces both, but backup_overdue_finding (services/storage_health/
storage_health.py) and GET /api/quality/summary still check only the
regular tier's recency - that's the tier whose staleness actually matters
for "is my critical backup fresh" alerting. Large-tier staleness is visible
here (GET /api/backup/status's large_tier key) but not yet alerted on -
a deliberate scope boundary, not an oversight.
"""
import asyncio

from fastapi import APIRouter

from services.app_state import state
from services.backup import backup
from services.config.config_store import config_store

router = APIRouter()


@router.get("/api/backup/status")
async def get_backup_status():
    backup_state = state["backup"]
    large_state = state["backup_large"]
    last = backup.latest(tier="regular")
    last_large = backup.latest(tier="large")
    snapshot_count = len(list(backup.BACKUP_DIR.iterdir())) if backup.BACKUP_DIR.exists() else 0
    large_snapshot_count = len(list(backup.LARGE_BACKUP_DIR.iterdir())) if backup.LARGE_BACKUP_DIR.exists() else 0
    return {
        "enabled": (config_store.get().get("backup") or {}).get("enabled", True),
        "running": backup_state["running"],
        "last_started_at": backup_state["last_started_at"] or None,
        "last_run": last,
        "snapshot_count": snapshot_count,
        "large_tier": {
            "running": large_state["running"],
            "last_started_at": large_state["last_started_at"] or None,
            "last_run": last_large,
            "snapshot_count": large_snapshot_count,
        },
    }


@router.get("/api/backup/history")
async def get_backup_history(limit: int = 20, tier: str = "regular"):
    return {"runs": backup.recent(limit=limit, tier=tier)}


@router.post("/api/backup/run")
async def trigger_backup_run(tier: str = "regular"):
    """Manual, synchronous trigger - an operator gets the real result back
    immediately (files backed up, bytes, any errors), not just a fire-and-
    forget acknowledgement, since the whole point of running this by hand
    is usually "I want to know this succeeded before I do something risky."
    Runs off the event loop the same way the periodic path does
    (asyncio.to_thread) so it doesn't stall the trading loop or other
    in-flight requests for however long the backup takes.

    tier (2026-08-30): "regular" (default, unchanged behavior - the small,
    account-critical files) or "large" (the permanently-growing history
    files, backup.py's own large_files config)."""
    backup_cfg = config_store.get().get("backup") or {}
    large_files = frozenset(backup_cfg.get("large_files", backup._DEFAULT_LARGE_FILES))
    if tier == "large":
        retention_count = backup_cfg.get("large_file_retention_count", backup._DEFAULT_LARGE_FILE_RETENTION_COUNT)
        return await asyncio.to_thread(
            backup.run_backup_cycle, retention_count, tier="large", only=large_files,
            snapshot_root=backup.LARGE_BACKUP_DIR,
        )
    retention_count = backup_cfg.get("retention_count", backup._DEFAULT_RETENTION_COUNT)
    return await asyncio.to_thread(
        backup.run_backup_cycle, retention_count, tier="regular", exclude=large_files,
    )
