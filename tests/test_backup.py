"""
services/backup/backup.py - snapshot + retention for every data/*.db file,
plus the _maybe_run_backup "due" check whose cold-start seeding bug this
session found and fixed live (2026-08-23): state["backup"]["last_started_at"]
is pure in-memory state (services/app_state.py's default, 0.0), so it reset
to "never backed up" on every process restart - not just a real reboot, but
every `uvicorn --reload` cycle this dev environment does routinely on any
.py edit. Each reset fired an immediate full backup of every data/*.db file
regardless of how recently one had actually completed - confirmed live: 37
runs in 4.4h against a configured 6h interval_sec, 28 of 36 gaps under 200s,
each one's real disk I/O measurably slowing the live trading loop's own
tick timing (tick_phase_timings.market_fetch observed at 6-8s against a 6s
poll_interval_sec). The fix seeds last_started_at from the already-persisted
backup_runs history on cold start instead of trusting in-memory state alone.

services.backup.backup imports services.app_state (for state["backup"]),
which constructs PaperBroker/RiskManager at *import time*. Real data/*.db
files can never be reached either way: conftest's install_runtime_isolation()
already redirects services.paper_broker.DB_PATH (and every other
_EAGER_SINGLETON_MODULES entry) to an isolated tmp path before this file - or
any test file - is even collected, and that's what services.app_state's
singleton construction actually picks up. This file's own risk_manager
redirect below is real for RiskManager only insofar as RiskManager itself
isn't in that eager list the same way; services.paper_broker.DB_PATH is
deliberately NOT imported+re-redirected here for that reason - see
tests/test_trading_gate.py's own
comment at the equivalent spot for the full mechanism and the real bug
(archive_epoch() silently reading a second, never-written-to tmp file) this
redundant reassignment used to cause once co-located with another test file
under xdist.
"""
import asyncio
import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

from services import paper_broker as pb_module
from services import risk_manager as rm_module

_tmp_dir = Path(tempfile.mkdtemp(prefix="backup_test_"))
rm_module.DB_PATH = _tmp_dir / "risk_state.db"

from services.app_state import state  # noqa: E402
from services.backup import backup  # noqa: E402


def _make_real_sqlite_file(path: Path) -> None:
    """sqlite3.Connection.backup() needs a real SQLite file, not arbitrary
    bytes - a minimal valid db (schema optional) is enough for it to work."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(backup, "DATA_DIR", data_dir)
    monkeypatch.setattr(backup, "BACKUP_DIR", data_dir / "backups")
    monkeypatch.setattr(backup, "LARGE_BACKUP_DIR", data_dir / "backups_large")
    monkeypatch.setattr(backup, "DB_PATH", data_dir / "backup_log.db")
    state["backup"] = {"running": False, "last_started_at": 0.0, "task": None}
    state["backup_large"] = {"running": False, "last_started_at": 0.0, "task": None}
    yield


# --- run_backup_cycle: the actual snapshot mechanism ------------------------

def test_run_backup_cycle_snapshots_every_data_db_file_and_records_the_run():
    _make_real_sqlite_file(backup.DATA_DIR / "paper_broker.db")
    _make_real_sqlite_file(backup.DATA_DIR / "risk_state.db")

    result = backup.run_backup_cycle(retention_count=5, now=1000.0)

    assert result["files_ok"] == 2
    assert result["files_failed"] == 0
    snapshot_dir = backup.BACKUP_DIR / result["snapshot"]
    assert (snapshot_dir / "paper_broker.db").exists()
    assert (snapshot_dir / "risk_state.db").exists()

    last = backup.latest()
    assert last["files_ok"] == 2
    assert last["error"] is None


def test_run_backup_cycle_survives_one_bad_file():
    _make_real_sqlite_file(backup.DATA_DIR / "good.db")
    (backup.DATA_DIR / "corrupt.db").write_bytes(b"not a real sqlite file")

    result = backup.run_backup_cycle(retention_count=5, now=1000.0)

    assert result["files_ok"] == 1
    assert result["files_failed"] == 1
    assert "corrupt.db" in result["errors"][0]


def test_prune_old_snapshots_keeps_only_retention_count():
    _make_real_sqlite_file(backup.DATA_DIR / "paper_broker.db")
    for i in range(4):
        backup.run_backup_cycle(retention_count=2, now=1000.0 + i)

    remaining = sorted(p.name for p in backup.BACKUP_DIR.iterdir())
    assert len(remaining) == 2  # only the 2 most recent survive


# --- two-tier file selection --------------------------------------------

def test_run_backup_cycle_exclude_skips_named_files():
    _make_real_sqlite_file(backup.DATA_DIR / "paper_broker.db")
    _make_real_sqlite_file(backup.DATA_DIR / "series_watcher.db")

    result = backup.run_backup_cycle(
        retention_count=5, now=1000.0, tier="regular", exclude=frozenset({"series_watcher.db"}),
    )

    assert result["files_ok"] == 1
    assert result["tier"] == "regular"
    snapshot_dir = backup.BACKUP_DIR / result["snapshot"]
    assert (snapshot_dir / "paper_broker.db").exists()
    assert not (snapshot_dir / "series_watcher.db").exists()


def test_run_backup_cycle_only_backs_up_named_files():
    _make_real_sqlite_file(backup.DATA_DIR / "paper_broker.db")
    _make_real_sqlite_file(backup.DATA_DIR / "series_watcher.db")

    result = backup.run_backup_cycle(
        retention_count=5, now=1000.0, tier="large",
        only=frozenset({"series_watcher.db"}), snapshot_root=backup.LARGE_BACKUP_DIR,
    )

    assert result["files_ok"] == 1
    assert result["tier"] == "large"
    snapshot_dir = backup.LARGE_BACKUP_DIR / result["snapshot"]
    assert (snapshot_dir / "series_watcher.db").exists()
    assert not (snapshot_dir / "paper_broker.db").exists()


def test_prune_old_snapshots_respects_snapshot_root():
    _make_real_sqlite_file(backup.DATA_DIR / "series_watcher.db")
    for i in range(4):
        backup.run_backup_cycle(
            retention_count=2, now=1000.0 + i, tier="large",
            only=frozenset({"series_watcher.db"}), snapshot_root=backup.LARGE_BACKUP_DIR,
        )

    assert len(list(backup.LARGE_BACKUP_DIR.iterdir())) == 2
    assert not backup.BACKUP_DIR.exists() or len(list(backup.BACKUP_DIR.iterdir())) == 0


def test_recent_and_latest_filter_by_tier():
    _make_real_sqlite_file(backup.DATA_DIR / "paper_broker.db")
    _make_real_sqlite_file(backup.DATA_DIR / "series_watcher.db")
    backup.run_backup_cycle(retention_count=5, now=1000.0, tier="regular",
                             exclude=frozenset({"series_watcher.db"}))
    backup.run_backup_cycle(retention_count=5, now=2000.0, tier="large",
                             only=frozenset({"series_watcher.db"}), snapshot_root=backup.LARGE_BACKUP_DIR)

    regular = backup.latest(tier="regular")
    large = backup.latest(tier="large")

    assert regular["tier"] == "regular"
    assert regular["started_at"] == 1000.0
    assert large["tier"] == "large"
    assert large["started_at"] == 2000.0


def test_recent_regular_tier_includes_pre_migration_rows_with_null_tier():
    # Simulates a backup_runs row written before the `tier` column existed -
    # recent()/latest() must still count it as "regular" so a fresh deploy
    # of this change doesn't misread historical recency as "overdue."
    _make_real_sqlite_file(backup.DATA_DIR / "paper_broker.db")
    with backup._connect() as conn:
        conn.execute(
            "INSERT INTO backup_runs "
            "(started_at, finished_at, snapshot_name, files_ok, files_failed, total_bytes, pruned_count, error, tier) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (500.0, 501.0, "pre-migration-snapshot", 1, 0, 100, 0, None),
        )

    last = backup.latest(tier="regular")

    assert last["snapshot_name"] == "pre-migration-snapshot"


# --- run_backup_now: the manual-trigger overlap guard ----------------------
# Real bug found live 2026-08-31: the manual route used to call
# run_backup_cycle directly with no guard, so a manual trigger could race
# the periodic scheduler (or another manual call) into backing up the same
# tier twice concurrently - two independent full ~27GB large-tier snapshots
# 26 seconds apart, in the wild. run_backup_now's check-and-set has no
# `await` between them, so it's atomic against every other coroutine on the
# event loop, including this module's own periodic guards.

def test_run_backup_now_raises_when_tier_already_running():
    state["backup"]["running"] = True

    with pytest.raises(backup.BackupAlreadyRunningError):
        asyncio.run(backup.run_backup_now("backup", 5, tier="regular"))


def test_run_backup_now_blocks_a_concurrent_call_for_the_same_tier():
    _make_real_sqlite_file(backup.DATA_DIR / "series_watcher.db")
    original_cycle = backup.run_backup_cycle

    def _slow_cycle(*args, **kwargs):
        time.sleep(0.1)  # hold the tier "running" long enough for the race below
        return original_cycle(*args, **kwargs)

    async def _race():
        first = asyncio.create_task(
            backup.run_backup_now("backup_large", 5, tier="large",
                                   only=frozenset({"series_watcher.db"}), snapshot_root=backup.LARGE_BACKUP_DIR)
        )
        await asyncio.sleep(0.02)  # let `first` set running=True before the second call checks it
        assert state["backup_large"]["running"] is True

        with pytest.raises(backup.BackupAlreadyRunningError):
            await backup.run_backup_now("backup_large", 5, tier="large",
                                         only=frozenset({"series_watcher.db"}), snapshot_root=backup.LARGE_BACKUP_DIR)
        await first

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(backup, "run_backup_cycle", _slow_cycle)
        asyncio.run(_race())

    # the fix's whole point: exactly one snapshot, not two
    assert len(backup.recent(limit=10, tier="large")) == 1
    assert len(list(backup.LARGE_BACKUP_DIR.iterdir())) == 1
    assert state["backup_large"]["running"] is False


def test_run_backup_now_clears_running_flag_even_if_the_cycle_raises():
    def _boom(*args, **kwargs):
        raise RuntimeError("disk full")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(backup, "run_backup_cycle", _boom)
        with pytest.raises(RuntimeError):
            asyncio.run(backup.run_backup_now("backup", 5, tier="regular"))

    assert state["backup"]["running"] is False


# --- large tier's own _maybe_run_large_backup ----------------------------

_LARGE_CFG = {"backup": {"enabled": True, "large_file_interval_sec": 21600,
                          "large_files": ["series_watcher.db"]}}


async def _call_maybe_run_large_backup(cfg):
    backup._maybe_run_large_backup(cfg)
    task = state["backup_large"].get("task")
    if task is not None:
        await task


def test_maybe_run_large_backup_fires_on_a_genuinely_fresh_install():
    _make_real_sqlite_file(backup.DATA_DIR / "series_watcher.db")
    assert backup.latest(tier="large") is None
    assert state["backup_large"]["last_started_at"] == 0.0

    asyncio.run(_call_maybe_run_large_backup(_LARGE_CFG))

    assert len(backup.recent(limit=10, tier="large")) == 1
    assert state["backup_large"]["running"] is False


def test_maybe_run_large_backup_only_writes_files_in_large_files_config():
    _make_real_sqlite_file(backup.DATA_DIR / "series_watcher.db")
    _make_real_sqlite_file(backup.DATA_DIR / "paper_broker.db")

    asyncio.run(_call_maybe_run_large_backup(_LARGE_CFG))

    last = backup.latest(tier="large")
    snapshot_dir = backup.LARGE_BACKUP_DIR / last["snapshot_name"]
    assert (snapshot_dir / "series_watcher.db").exists()
    assert not (snapshot_dir / "paper_broker.db").exists()


def test_maybe_run_backup_still_excludes_large_files_by_default():
    _make_real_sqlite_file(backup.DATA_DIR / "paper_broker.db")
    _make_real_sqlite_file(backup.DATA_DIR / "series_watcher.db")
    cfg = {"backup": {"enabled": True, "interval_sec": 21600,
                       "large_files": ["series_watcher.db"]}}

    asyncio.run(_call_maybe_run_backup(cfg))

    last = backup.latest(tier="regular")
    snapshot_dir = backup.BACKUP_DIR / last["snapshot_name"]
    assert (snapshot_dir / "paper_broker.db").exists()
    assert not (snapshot_dir / "series_watcher.db").exists()


# --- _maybe_run_backup: the cold-start seeding regression -------------------

_CFG = {"backup": {"enabled": True, "interval_sec": 21600}}  # matches config/settings.yaml


async def _call_maybe_run_backup(cfg):
    backup._maybe_run_backup(cfg)
    task = state["backup"].get("task")
    if task is not None:
        await task  # the supervised run itself - a fixed sleep is the CI #582 race class (2026-08-28)


def test_cold_start_does_not_refire_when_a_recent_backup_is_already_persisted():
    _make_real_sqlite_file(backup.DATA_DIR / "paper_broker.db")
    recent_run = backup.run_backup_cycle(retention_count=5, now=time.time() - 30)
    assert state["backup"]["last_started_at"] == 0.0  # simulated cold start (fresh process)

    asyncio.run(_call_maybe_run_backup(_CFG))

    assert state["backup"]["running"] is False  # no spurious re-backup
    assert backup.latest()["snapshot_name"] == recent_run["snapshot"]  # still just the one run
    assert state["backup"]["last_started_at"] > 0.0  # seeded from persisted history, not left at 0.0


def test_cold_start_still_fires_when_persisted_history_is_older_than_the_interval():
    _make_real_sqlite_file(backup.DATA_DIR / "paper_broker.db")
    backup.run_backup_cycle(retention_count=5, now=time.time() - 999_999)  # long ago
    assert state["backup"]["last_started_at"] == 0.0

    asyncio.run(_call_maybe_run_backup(_CFG))

    assert len(backup.recent(limit=10)) == 2  # the real backup fired, on top of the seed


def test_cold_start_fires_on_a_genuinely_fresh_install_with_no_backup_history():
    _make_real_sqlite_file(backup.DATA_DIR / "paper_broker.db")
    assert backup.latest() is None
    assert state["backup"]["last_started_at"] == 0.0

    asyncio.run(_call_maybe_run_backup(_CFG))

    assert len(backup.recent(limit=10)) == 1  # first-ever backup still runs promptly


def test_disabled_never_fires_regardless_of_history():
    _make_real_sqlite_file(backup.DATA_DIR / "paper_broker.db")

    asyncio.run(_call_maybe_run_backup({"backup": {"enabled": False, "interval_sec": 1}}))

    assert backup.latest() is None
    assert state["backup"]["running"] is False


# --- _connect: services/db.py migration (Task 12 - primary _connect only) --

def test_connect_closes_its_connection(monkeypatch):
    """_RecordingConnection wraps the real connection instead of mutating
    conn.close directly - that raises AttributeError on this container's
    Python (sqlite3.Connection.close is read-only), the same defect Task
    1's own implementation (PR #518) found and fixed against
    tests/test_signal_log.py's proven pattern (Tier 0's own Task 5),
    applied here for this module's test file."""
    import sqlite3

    closed = []
    real_connect = sqlite3.connect

    class _RecordingConnection:
        def __init__(self, inner):
            self._inner = inner

        def close(self):
            closed.append(True)
            self._inner.close()

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, *exc_info):
            return self._inner.__exit__(*exc_info)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def _tracking_connect(*args, **kwargs):
        return _RecordingConnection(real_connect(*args, **kwargs))

    monkeypatch.setattr(backup.db.sqlite3, "connect", _tracking_connect)
    with backup._connect() as conn:
        conn.execute("SELECT 1")
    assert closed == [True]


def test_connect_still_creates_table_index_and_tier_column():
    with backup._connect() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "backup_runs" in tables
        indexes = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )}
        assert "idx_backup_runs_started_at" in indexes
        cols = {r[1] for r in conn.execute("PRAGMA table_info(backup_runs)")}
        assert "tier" in cols


def test_connect_sets_explicit_busy_timeout_pragma():
    with backup._connect() as conn:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
