"""
Backup and retention for every data/*.db file - direct instruction
(2026-08-23), after ROADMAP.md's own "Path to production" section named
this as a real gap: "single-file SQLite with no backup/retention policy -
fine for a local paper POC, not once a lost file means lost real financial
state." CLAUDE.md's own "accumulated history is a first-class asset" rule
already treats data/*.db as irreplaceable; this is the mechanism that
actually protects it against a lost/corrupted disk, a bad edit, or a
future refactor gone wrong, rather than just saying so in a doc.

Uses sqlite3.Connection.backup() (the standard library's own online-backup
API), not a raw file copy - this project's persistence idiom (CLAUDE.md)
runs most of these files in WAL mode specifically because it's read/write-
concurrent-safe; a plain `cp` while the live app is mid-write could copy a
torn, inconsistent snapshot (the exact "torn read" failure mode
services/config_store.py's own 2026-08-23 fix - see that module's
docstring - just hit for a different file). backup() takes a clean,
consistent, hot snapshot of a live database with no coordination needed
from the writer's side, regardless of journal mode.

One timestamped snapshot directory per run under data/backups/ (gitignored
- see .gitignore's dedicated data/backups/ entry, since it lives one level
below the flat data/*.db pattern the rest of this project's gitignore
already covers), holding a copy of every data/*.db file as it stood at
that moment. Retention prunes whole snapshot directories, oldest first -
simpler and safer than pruning individual files out of a snapshot, and
matches how a restore would actually be used (one snapshot = one point in
time to roll back to, not a pick-and-mix of file ages).

Runnable two ways, deliberately: wired into main.py's trading loop as a
config-gated (`backup.enabled`, default true) periodic background task
(_maybe_run_backup, same _maybe_*-background-task idiom as catalog_scan.py/
discovery_cache.py), so it "just works" under ddev with zero extra setup -
and as a standalone CLI (`python -m services.backup.backup`, no server
needed) for a real deployment's own cron/systemd timer, since ROADMAP.md's
"Path to production" section is explicit that this app has no real host or
process supervisor yet and a backup schedule shouldn't be hostage to the
app process's own uptime once one exists.
"""
import asyncio
import shutil
import sqlite3
import time
from pathlib import Path

from services import fault_log, task_supervisor
from services.app_state import state

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
BACKUP_DIR = DATA_DIR / "backups"
DB_PATH = DATA_DIR / "backup_log.db"

_DEFAULT_INTERVAL_SEC = 21600  # 6h - frequent enough that a lost disk never
# costs more than a few hours of real trade/signal history.
_DEFAULT_RETENTION_COUNT = 14  # ~3.5 days at the default 6h cadence.
# Deliberately more conservative than reset_log.py/fault_log.py's own "keep
# enough to reconstruct what happened, not everything forever" philosophy
# would suggest on its own - most data/*.db files are single-digit MB, but
# series_watcher.py's raw_trades is deliberately never pruned (CLAUDE.md's
# "accumulated history is a first-class asset" rule) and was measured at
# 6.9GB on 2026-08-23 (the same investigation that found and fixed a
# separate 5.7GB game_state.db bug - see that module's CHEATSHEET/
# static/status.html phase 126), so every snapshot's real size grows over
# time regardless of what this number is. Revisit as data/'s total
# footprint grows.


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS backup_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at REAL NOT NULL,
            finished_at REAL NOT NULL,
            snapshot_name TEXT NOT NULL,
            files_ok INTEGER NOT NULL,
            files_failed INTEGER NOT NULL,
            total_bytes INTEGER NOT NULL,
            pruned_count INTEGER NOT NULL,
            error TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_backup_runs_started_at ON backup_runs (started_at)")
    return conn


def _data_db_files() -> list[Path]:
    """Every data/*.db file at call time, backup_log.db included (its own
    run history is worth keeping too) - deliberately a fresh glob every
    call, not a cached list, so a newly-added persistence module's file is
    picked up automatically with no registration step. Only matches files
    directly in data/ (glob's * doesn't cross the data/backups/ boundary),
    so this can never recurse into its own prior output."""
    if not DATA_DIR.exists():
        return []
    return sorted(DATA_DIR.glob("*.db"))


def _backup_one_file(src: Path, dest_dir: Path) -> int:
    """A live, consistent snapshot of one SQLite file via the stdlib's own
    online-backup API - see this module's docstring for why not a raw
    copy. Returns the backed-up file's size in bytes."""
    dest = dest_dir / src.name
    src_conn = sqlite3.connect(src)
    try:
        dest_conn = sqlite3.connect(dest)
        try:
            src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        src_conn.close()
    return dest.stat().st_size


def _prune_old_snapshots(retention_count: int) -> list[str]:
    """Deletes whole snapshot directories beyond retention_count, oldest
    first (snapshot names are UTC timestamps formatted to sort
    lexicographically in chronological order, so a plain name sort is
    enough - no need to parse them back into datetimes). retention_count
    <= 0 disables pruning entirely (an explicit opt-out, not a footgun -
    matches this app's other "0/None means off" config conventions)."""
    if retention_count <= 0 or not BACKUP_DIR.exists():
        return []
    snapshots = sorted((p for p in BACKUP_DIR.iterdir() if p.is_dir()), key=lambda p: p.name)
    to_prune = snapshots[:-retention_count] if len(snapshots) > retention_count else []
    pruned_names = []
    for p in to_prune:
        shutil.rmtree(p, ignore_errors=True)
        pruned_names.append(p.name)
    return pruned_names


def run_backup_cycle(retention_count: int = _DEFAULT_RETENTION_COUNT, now: float | None = None) -> dict:
    """The full cycle: snapshot every data/*.db file, prune old snapshots,
    record the run. Synchronous and blocking by design - sqlite3's backup()
    is a blocking call with no async variant, so the async wiring below
    (_run_backup_background) is what keeps this off the event loop, not
    this function itself. That also makes this directly callable from a
    plain script/cron with no asyncio involved at all.

    One bad file never aborts the whole run (fault_log records it and the
    cycle continues) - the same "degrade honestly, keep going" idiom the
    rest of this app already applies to REST fetches; a paper broker DB
    that's momentarily locked shouldn't cost the signal log its backup
    too."""
    started_at = now if now is not None else time.time()
    snapshot_name = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(started_at))
    snapshot_dir = BACKUP_DIR / snapshot_name
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    files_ok, files_failed, total_bytes = 0, 0, 0
    errors: list[str] = []
    for src in _data_db_files():
        try:
            total_bytes += _backup_one_file(src, snapshot_dir)
            files_ok += 1
        except Exception as exc:
            files_failed += 1
            errors.append(f"{src.name}: {type(exc).__name__}: {exc}")
            fault_log.record("backup", "backup_one_file", exc, context=src.name)

    pruned = _prune_old_snapshots(retention_count)
    finished_at = time.time()

    with _connect() as conn:
        conn.execute(
            "INSERT INTO backup_runs "
            "(started_at, finished_at, snapshot_name, files_ok, files_failed, total_bytes, pruned_count, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (started_at, finished_at, snapshot_name, files_ok, files_failed, total_bytes, len(pruned),
             "; ".join(errors) if errors else None),
        )

    return {
        "snapshot": snapshot_name, "files_ok": files_ok, "files_failed": files_failed,
        "total_bytes": total_bytes, "pruned": pruned, "duration_sec": round(finished_at - started_at, 3),
        "errors": errors,
    }


def recent(limit: int = 20) -> list[dict]:
    cols = ["id", "started_at", "finished_at", "snapshot_name", "files_ok", "files_failed",
            "total_bytes", "pruned_count", "error"]
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM backup_runs ORDER BY started_at DESC LIMIT ?", (limit,),
        ).fetchall()
    return [dict(zip(cols, r)) for r in rows]


def latest() -> dict | None:
    rows = recent(limit=1)
    return rows[0] if rows else None


async def _run_backup_background(retention_count: int) -> None:
    """Background-task wrapper, same split as catalog_scan._scan_catalog_
    batch_background/discovery_cache._refresh_discovery_cache_background -
    owns releasing the "running" flag regardless of outcome via finally.
    asyncio.to_thread is what actually keeps run_backup_cycle's blocking
    sqlite3.backup() calls off the event loop; without it, backing up
    several MB-scale files would stall the trading loop's own tick timing
    for the duration."""
    backup_state = state["backup"]
    try:
        await asyncio.to_thread(run_backup_cycle, retention_count)
    finally:
        backup_state["running"] = False


def _maybe_run_backup(cfg: dict) -> None:
    """Kicks off _run_backup_background as an independent background task
    if a backup is due and none is already running - never awaited by the
    calling tick, same fire-and-forget shape as _maybe_scan_catalog_batch/
    _maybe_refresh_discovery_cache. Synchronous on purpose: this only ever
    schedules work, it never does any I/O of its own."""
    backup_cfg = cfg.get("backup") or {}
    if not backup_cfg.get("enabled", True):
        return
    backup_state = state["backup"]
    interval = backup_cfg.get("interval_sec", _DEFAULT_INTERVAL_SEC)
    now_ts = time.time()
    due = now_ts - backup_state["last_started_at"] > interval
    if due and not backup_state["running"]:
        backup_state["running"] = True
        backup_state["last_started_at"] = now_ts
        retention_count = backup_cfg.get("retention_count", _DEFAULT_RETENTION_COUNT)
        backup_state["task"] = task_supervisor.supervise(
            lambda: _run_backup_background(retention_count),
            component="backup", operation="run",
        )


if __name__ == "__main__":
    import json

    from services.config_store import config_store

    _cfg = (config_store.get().get("backup") or {})
    result = run_backup_cycle(_cfg.get("retention_count", _DEFAULT_RETENTION_COUNT))
    print(json.dumps(result, indent=2))
