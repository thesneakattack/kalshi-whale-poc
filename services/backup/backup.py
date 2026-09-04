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

Two independent tiers as of 2026-08-30 (see _DEFAULT_LARGE_FILES): a
"regular" tier for small, account-critical files on the original 6h/14-
count cadence, and a "large" tier for the files that grow forever by
design, on a longer, separately-configured cadence into their own
LARGE_BACKUP_DIR - see _maybe_run_large_backup's own docstring for why.
"""
import asyncio
import contextlib
import shutil
import sqlite3
import time
from pathlib import Path

from services import db, fault_log, task_supervisor
from services.app_state import state

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
BACKUP_DIR = DATA_DIR / "backups"
# A *sibling* of BACKUP_DIR, not BACKUP_DIR / "large" - nesting would make
# _prune_old_snapshots' directory-name sort for the regular tier treat this
# subdirectory as just another snapshot (and, since "large" sorts after
# every UTC-timestamp name lexicographically, always the "newest" one),
# corrupting both tiers' pruning and GET /api/backup/status's counts.
LARGE_BACKUP_DIR = DATA_DIR / "backups_large"
DB_PATH = DATA_DIR / "backup_log.db"

_DEFAULT_INTERVAL_SEC = 21600  # 6h - frequent enough that a lost disk never
# costs more than a few hours of real trade/signal history.
_DEFAULT_RETENTION_COUNT = 14  # ~3.5 days at the default 6h cadence. Safe to
# keep unchanged now that the large tier below carries series_watcher.db/
# candidate_log.db/market_history.db separately - this tier's own files are
# all single-digit-MB, so 14 of them costs nothing like the pre-tiering
# snapshot did (2026-08-30: 292GB across 13 snapshots once series_watcher.db
# alone reached 24GB, because every snapshot carried a full copy of it).

_DEFAULT_LARGE_FILE_INTERVAL_SEC = 86400  # 24h - deliberately longer than
# the regular tier: these files are already durable, continuously-
# accumulating append logs (CLAUDE.md's "accumulated history is a
# first-class asset" rule), so losing a few hours of them to a backup gap
# costs nothing like losing live trading state would.
_DEFAULT_LARGE_FILE_RETENTION_COUNT = 4  # ~4 days at that cadence.
_DEFAULT_LARGE_FILES = frozenset({"series_watcher.db", "candidate_log.db", "market_history.db"})
# The three data/*.db files with no row-level retention by design (measured
# 2026-08-30: 24.4GB, 2.8GB, and - once Task 1 of this plan ships - a
# capped market_history.db respectively). A full 6h/14-count snapshot of
# these dominated data/backups/ before this split existed.


def _init_backup_runs(conn: sqlite3.Connection) -> None:
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


db.register_schema("backup_runs", _init_backup_runs)


@contextlib.contextmanager
def _connect():
    """Every existing `with _connect() as conn:` call site keeps working
    unchanged - now backed by services/db.py's closing connect(). The
    CREATE INDEX statement isn't expressible in backup_runs' registered
    init_fn (it isn't a CREATE TABLE), so this wrapper still runs it
    itself on the yielded connection, same as add_column_if_missing below."""
    with db.connect(DB_PATH, tables=("backup_runs",)) as conn:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_backup_runs_started_at ON backup_runs (started_at)")
        # tier (2026-08-30): which of the two independent backup cadences a
        # run belongs to - "regular" (small, account-critical files) or
        # "large" (the permanently-growing history files). NULL on rows
        # written before this column existed; recent()/latest() treat NULL
        # as "regular" since that's the cadence those historical runs
        # actually ran on.
        db.add_column_if_missing(conn, "backup_runs", "tier", "TEXT")
        yield conn


def _data_db_files(*, exclude: frozenset[str] = frozenset(), only: frozenset[str] | None = None) -> list[Path]:
    """Every data/*.db file at call time, backup_log.db included (its own
    run history is worth keeping too) - deliberately a fresh glob every
    call, not a cached list, so a newly-added persistence module's file is
    picked up automatically with no registration step. Only matches files
    directly in data/ (glob's * doesn't cross the data/backups/ or
    data/backups_large/ boundary), so this can never recurse into its own
    prior output.

    exclude/only (2026-08-30) select this call's tier: the regular tier
    passes exclude=large_files to skip the permanently-growing files; the
    large tier passes only=large_files to back up nothing else. Passing
    both is never done by this module's own callers - only is checked
    first and, if given, exclude is ignored entirely."""
    if not DATA_DIR.exists():
        return []
    files = sorted(DATA_DIR.glob("*.db"))
    if only is not None:
        return [f for f in files if f.name in only]
    return [f for f in files if f.name not in exclude]


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


def _prune_old_snapshots(retention_count: int, snapshot_root: Path | None = None) -> list[str]:
    """Deletes whole snapshot directories beyond retention_count, oldest
    first (snapshot names are UTC timestamps formatted to sort
    lexicographically in chronological order, so a plain name sort is
    enough - no need to parse them back into datetimes). retention_count
    <= 0 disables pruning entirely (an explicit opt-out, not a footgun -
    matches this app's other "0/None means off" config conventions).

    snapshot_root (2026-08-30) lets the large tier prune its own
    LARGE_BACKUP_DIR independently of the regular tier's BACKUP_DIR -
    defaults to BACKUP_DIR so every pre-tiering call site keeps working
    unchanged."""
    root = snapshot_root if snapshot_root is not None else BACKUP_DIR
    if retention_count <= 0 or not root.exists():
        return []
    snapshots = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name)
    to_prune = snapshots[:-retention_count] if len(snapshots) > retention_count else []
    pruned_names = []
    for p in to_prune:
        shutil.rmtree(p, ignore_errors=True)
        pruned_names.append(p.name)
    return pruned_names


def run_backup_cycle(
    retention_count: int = _DEFAULT_RETENTION_COUNT, now: float | None = None, *,
    tier: str = "regular", exclude: frozenset[str] = frozenset(), only: frozenset[str] | None = None,
    snapshot_root: Path | None = None,
) -> dict:
    """The full cycle for one tier: snapshot the tier's own file selection
    (exclude/only, see _data_db_files), prune that tier's own old snapshots
    (snapshot_root, see _prune_old_snapshots), record the run tagged with
    tier. Synchronous and blocking by design - sqlite3's backup() is a
    blocking call with no async variant, so the async wiring below
    (_run_backup_background) is what keeps this off the event loop, not
    this function itself. That also makes this directly callable from a
    plain script/cron with no asyncio involved at all.

    One bad file never aborts the whole run (fault_log records it and the
    cycle continues) - the same "degrade honestly, keep going" idiom the
    rest of this app already applies to REST fetches; a paper broker DB
    that's momentarily locked shouldn't cost the signal log its backup
    too."""
    root = snapshot_root if snapshot_root is not None else BACKUP_DIR
    started_at = now if now is not None else time.time()
    snapshot_name = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(started_at))
    snapshot_dir = root / snapshot_name
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    files_ok, files_failed, total_bytes = 0, 0, 0
    errors: list[str] = []
    for src in _data_db_files(exclude=exclude, only=only):
        try:
            total_bytes += _backup_one_file(src, snapshot_dir)
            files_ok += 1
        except Exception as exc:
            files_failed += 1
            errors.append(f"{src.name}: {type(exc).__name__}: {exc}")
            fault_log.record("backup", "backup_one_file", exc, context=src.name)

    pruned = _prune_old_snapshots(retention_count, snapshot_root=root)
    finished_at = time.time()

    with _connect() as conn:
        conn.execute(
            "INSERT INTO backup_runs "
            "(started_at, finished_at, snapshot_name, files_ok, files_failed, total_bytes, pruned_count, error, tier) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (started_at, finished_at, snapshot_name, files_ok, files_failed, total_bytes, len(pruned),
             "; ".join(errors) if errors else None, tier),
        )

    return {
        "snapshot": snapshot_name, "tier": tier, "files_ok": files_ok, "files_failed": files_failed,
        "total_bytes": total_bytes, "pruned": pruned, "duration_sec": round(finished_at - started_at, 3),
        "errors": errors,
    }


def recent(limit: int = 20, tier: str = "regular") -> list[dict]:
    cols = ["id", "started_at", "finished_at", "snapshot_name", "files_ok", "files_failed",
            "total_bytes", "pruned_count", "error", "tier"]
    # Rows written before the tier column existed have tier IS NULL - treat
    # those as "regular" (the only cadence that existed then), so a fresh
    # deploy of this change doesn't misread real historical recency as
    # "overdue." Only the regular-tier query needs this; "large" never had
    # pre-migration rows.
    where = "WHERE tier = ? OR tier IS NULL" if tier == "regular" else "WHERE tier = ?"
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM backup_runs {where} ORDER BY started_at DESC LIMIT ?",
            (tier, limit),
        ).fetchall()
    return [dict(zip(cols, r)) for r in rows]


def latest(tier: str = "regular") -> dict | None:
    rows = recent(limit=1, tier=tier)
    return rows[0] if rows else None


async def _run_backup_background(
    retention_count: int, *, tier: str = "regular", exclude: frozenset[str] = frozenset(),
    only: frozenset[str] | None = None, snapshot_root: Path | None = None, state_key: str = "backup",
) -> None:
    """Background-task wrapper, same split as catalog_scan._scan_catalog_
    batch_background/discovery_cache._refresh_discovery_cache_background -
    owns releasing the "running" flag regardless of outcome via finally.
    asyncio.to_thread is what actually keeps run_backup_cycle's blocking
    sqlite3.backup() calls off the event loop; without it, backing up
    several MB-scale files would stall the trading loop's own tick timing
    for the duration.

    state_key (2026-08-30) picks which of state["backup"]/state["backup_large"]
    this run's "running" flag belongs to, so the two tiers' overlap guards
    never share state."""
    backup_state = state[state_key]
    try:
        await asyncio.to_thread(
            run_backup_cycle, retention_count, tier=tier, exclude=exclude, only=only, snapshot_root=snapshot_root,
        )
    finally:
        backup_state["running"] = False


class BackupAlreadyRunningError(Exception):
    """Raised by run_backup_now when the requested tier already has a backup
    in flight - either the periodic scheduler (_maybe_run_backup/
    _maybe_run_large_backup) or another manual trigger."""

    def __init__(self, tier: str):
        super().__init__(f"a {tier}-tier backup is already running")
        self.tier = tier


async def run_backup_now(
    state_key: str, retention_count: int, *, tier: str, exclude: frozenset[str] = frozenset(),
    only: frozenset[str] | None = None, snapshot_root: Path | None = None,
) -> dict:
    """Manual-trigger counterpart to the periodic _maybe_run_backup/
    _maybe_run_large_backup path - same state[state_key]["running"] overlap
    guard those already use, so POST /api/backup/run can't race the
    scheduler (or another manual call) into backing up the same tier twice
    concurrently.

    Real bug found live 2026-08-31: the manual route used to call
    run_backup_cycle directly with no guard at all. A manual large-tier
    trigger landed during a uvicorn --reload cold-start window (state["
    backup_large"] reset to running=False/last_started_at=0.0 - see
    _maybe_run_large_backup's own docstring for why reload does this); before
    that ~100s, ~27GB copy finished and recorded itself, the periodic tick's
    own cold-start reseed read the still-stale pre-restart history, decided
    the tier was overdue, and fired a second, fully independent ~27GB
    snapshot 26 seconds later - neither side aware of the other, because the
    manual path touched no shared state for the periodic guard to see.

    The check-and-set below has no `await` between them, so - like the
    periodic guards' own `if due and not backup_state["running"]:` - it's
    atomic against every other coroutine on this event loop, including the
    periodic guards themselves."""
    backup_state = state[state_key]
    if backup_state["running"]:
        raise BackupAlreadyRunningError(tier)
    backup_state["running"] = True
    backup_state["last_started_at"] = time.time()
    try:
        return await asyncio.to_thread(
            run_backup_cycle, retention_count, tier=tier, exclude=exclude, only=only, snapshot_root=snapshot_root,
        )
    finally:
        backup_state["running"] = False


def _maybe_run_backup(cfg: dict) -> None:
    """Kicks off _run_backup_background as an independent background task
    if a backup is due and none is already running - never awaited by the
    calling tick, same fire-and-forget shape as _maybe_scan_catalog_batch/
    _maybe_refresh_discovery_cache. Synchronous save for one lazy, one-time-
    per-process-lifetime DB read below - this doesn't otherwise do I/O of
    its own.

    This is the "regular" tier only (small, account-critical files) -
    excludes cfg's backup.large_files, which _maybe_run_large_backup below
    backs up on its own, longer cadence. See this module's own docstring
    and _DEFAULT_LARGE_FILES for why: those files (series_watcher.db,
    candidate_log.db, market_history.db) grow forever by design, and every
    snapshot on this tier's 6h/14-count cadence used to carry a full copy
    of them (292GB measured 2026-08-30 before this split).

    Real bug found and fixed live 2026-08-23, same "module quality" pass as
    the rest of this session: state["backup"]["last_started_at"] is pure
    in-memory state (services/app_state.py's default, 0.0), so it resets to
    "never" on every process restart - not just a real reboot, but every
    single `uvicorn --reload` reload this dev environment does routinely on
    any .py edit (including test files, per CLAUDE.md's dev-workflow notes).
    Each reset made the "due" check below fire an immediate full backup
    (every data/*.db file, ~9.4GB, ~40-50s in a background thread) regardless
    of how recently one had actually completed - confirmed live: 37 runs in
    4.4h against a configured 6h interval_sec, 28 of 36 gaps under 200s. Each
    one's real disk I/O measurably contended with the live trading loop's own
    SQLite reads/writes on the same disk - tick_phase_timings.market_fetch
    was observed at 6-8s (vs. a 6s configured poll_interval_sec) during this
    window. Fixed by seeding from the already-persisted backup_runs history
    (this function's own record of every run, via backup_log.db) on cold
    start instead of trusting in-memory state alone - a restart no longer
    means immediately overdue - just unknown, so go check what actually
    happened before deciding."""
    backup_cfg = cfg.get("backup") or {}
    if not backup_cfg.get("enabled", True):
        return
    backup_state = state["backup"]
    interval = backup_cfg.get("interval_sec", _DEFAULT_INTERVAL_SEC)
    now_ts = time.time()
    if backup_state["last_started_at"] == 0.0:
        last = latest(tier="regular")
        backup_state["last_started_at"] = last["started_at"] if last else 0.0
    due = now_ts - backup_state["last_started_at"] > interval
    if due and not backup_state["running"]:
        backup_state["running"] = True
        backup_state["last_started_at"] = now_ts
        retention_count = backup_cfg.get("retention_count", _DEFAULT_RETENTION_COUNT)
        large_files = frozenset(backup_cfg.get("large_files", _DEFAULT_LARGE_FILES))
        backup_state["task"] = task_supervisor.supervise(
            lambda: _run_backup_background(retention_count, tier="regular", exclude=large_files),
            component="backup", operation="run",
        )


def _maybe_run_large_backup(cfg: dict) -> None:
    """Second, independent backup tier for the files CLAUDE.md's
    "accumulated history is a first-class asset" rule keeps growing forever
    (series_watcher.db's raw_trades, candidate_log.db's rejection_events,
    market_history.db's snapshots) - see _DEFAULT_LARGE_FILES and this
    module's own docstring for why these can't share the regular tier's
    6h/14-count cadence without every snapshot ballooning in lockstep with
    the source file (292GB measured 2026-08-30 across 13 regular snapshots
    once series_watcher.db alone reached 24GB). A longer interval and
    shorter retention here trades disaster-recovery freshness for these
    specific files against disk - an explicit, configured choice
    (backup.large_file_interval_sec/large_file_retention_count), not a
    silent one; the regular tier's own cadence for the small,
    account-critical files (_maybe_run_backup) is untouched.

    Same due()/overlap-guard/cold-start-reseed shape as _maybe_run_backup,
    against its own state["backup_large"] and its own tier="large" history
    (latest(tier="large")) so a restart doesn't misread the regular tier's
    recency as this tier's."""
    backup_cfg = cfg.get("backup") or {}
    if not backup_cfg.get("enabled", True):
        return
    backup_state = state["backup_large"]
    interval = backup_cfg.get("large_file_interval_sec", _DEFAULT_LARGE_FILE_INTERVAL_SEC)
    now_ts = time.time()
    if backup_state["last_started_at"] == 0.0:
        last = latest(tier="large")
        backup_state["last_started_at"] = last["started_at"] if last else 0.0
    due = now_ts - backup_state["last_started_at"] > interval
    if due and not backup_state["running"]:
        backup_state["running"] = True
        backup_state["last_started_at"] = now_ts
        retention_count = backup_cfg.get("large_file_retention_count", _DEFAULT_LARGE_FILE_RETENTION_COUNT)
        large_files = frozenset(backup_cfg.get("large_files", _DEFAULT_LARGE_FILES))
        backup_state["task"] = task_supervisor.supervise(
            lambda: _run_backup_background(
                retention_count, tier="large", only=large_files,
                snapshot_root=LARGE_BACKUP_DIR, state_key="backup_large",
            ),
            component="backup", operation="run_large",
        )


if __name__ == "__main__":
    import json

    from services.config.config_store import config_store

    _cfg = (config_store.get().get("backup") or {})
    _large_files = frozenset(_cfg.get("large_files", _DEFAULT_LARGE_FILES))
    regular_result = run_backup_cycle(
        _cfg.get("retention_count", _DEFAULT_RETENTION_COUNT), tier="regular", exclude=_large_files,
    )
    large_result = run_backup_cycle(
        _cfg.get("large_file_retention_count", _DEFAULT_LARGE_FILE_RETENTION_COUNT),
        tier="large", only=_large_files, snapshot_root=LARGE_BACKUP_DIR,
    )
    print(json.dumps({"regular": regular_result, "large": large_result}, indent=2))
