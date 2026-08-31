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
from fastapi import APIRouter, HTTPException

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
    (backup.run_backup_now's own asyncio.to_thread) so it doesn't stall the
    trading loop or other in-flight requests for however long the backup
    takes.

    Goes through run_backup_now's overlap guard (2026-08-31, real bug found
    live) rather than calling run_backup_cycle directly: a manual trigger
    used to have no idea whether the periodic scheduler (or another manual
    call) already had the same tier running, so a manual call landing during
    a uvicorn --reload cold-start window could race the scheduler's own
    cold-start reseed into two independent, fully redundant ~27GB large-tier
    snapshots 26 seconds apart - see run_backup_now's own docstring for the
    full mechanism. 409 here means exactly what it says: this tier is
    already backing up somewhere else right now, so this call did nothing -
    not a failure of the backup itself.

    tier (2026-08-30, corrected 2026-08-30): the bare/default call covers
    the "regular" tier only - the small, account-critical files - NOT
    "unchanged behavior" against the pre-split single-cadence backup;
    since the two-tier split, the default silently excludes
    series_watcher.db/candidate_log.db/market_history.db.
    "large" covers only those three permanently-growing files
    (backup.py's own large_files config). "all" runs both cycles in
    sequence (regular then large) and is what to call before a risky
    operation if full coverage of every data/*.db file is actually
    wanted - mirrors the shape backup.py's own `__main__` CLI block
    already uses to run both tiers."""
    backup_cfg = config_store.get().get("backup") or {}
    large_files = frozenset(backup_cfg.get("large_files", backup._DEFAULT_LARGE_FILES))
    try:
        if tier == "all":
            regular_retention = backup_cfg.get("retention_count", backup._DEFAULT_RETENTION_COUNT)
            large_retention = backup_cfg.get("large_file_retention_count", backup._DEFAULT_LARGE_FILE_RETENTION_COUNT)
            regular_result = await backup.run_backup_now(
                "backup", regular_retention, tier="regular", exclude=large_files,
            )
            try:
                large_result = await backup.run_backup_now(
                    "backup_large", large_retention, tier="large", only=large_files,
                    snapshot_root=backup.LARGE_BACKUP_DIR,
                )
            except backup.BackupAlreadyRunningError as exc:
                # The regular tier already completed and is safely recorded
                # (backup_runs, on disk) by this point - say so instead of a
                # bare 409 that would otherwise make a real, successful
                # backup look like it never happened.
                raise HTTPException(
                    status_code=409,
                    detail=f"regular tier completed (snapshot {regular_result['snapshot']}); {exc}",
                ) from exc
            return {"regular": regular_result, "large": large_result}
        if tier == "large":
            retention_count = backup_cfg.get("large_file_retention_count", backup._DEFAULT_LARGE_FILE_RETENTION_COUNT)
            return await backup.run_backup_now(
                "backup_large", retention_count, tier="large", only=large_files,
                snapshot_root=backup.LARGE_BACKUP_DIR,
            )
        retention_count = backup_cfg.get("retention_count", backup._DEFAULT_RETENTION_COUNT)
        return await backup.run_backup_now(
            "backup", retention_count, tier="regular", exclude=large_files,
        )
    except backup.BackupAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
