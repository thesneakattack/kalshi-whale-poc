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
    monkeypatch.setattr(backup, "DB_PATH", data_dir / "backup_log.db")
    state["backup"] = {"running": False, "last_started_at": 0.0, "task": None}
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
