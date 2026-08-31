"""GET/POST /api/backup/* (services/backup/routes.py), extended 2026-08-30
to report the large-file tier alongside the regular one - see
services/backup/backup.py's own module docstring for why the two tiers
exist. Relies on tests/conftest.py's global install_runtime_isolation() for
services.backup.backup's DB_PATH/DATA_DIR (it's in both
PERSISTENCE_MODULE_PATHS and DATA_DIR_MODULE_PATHS); BACKUP_DIR/
LARGE_BACKUP_DIR are derived constants that mechanism doesn't re-derive, so
this file's own _isolated fixture overrides them explicitly too - same
shape as tests/test_storage_health_routes.py's own fixture."""
import sqlite3
from pathlib import Path

import pytest

import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from services.app_state import state  # noqa: E402
from services.backup import backup  # noqa: E402

client = TestClient(main.app)


def _make_real_sqlite_file(path: Path) -> None:
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


def test_status_reports_both_tiers():
    resp = client.get("/api/backup/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "large_tier" in body
    assert body["large_tier"]["last_run"] is None
    assert body["large_tier"]["snapshot_count"] == 0


def test_status_large_tier_reflects_a_completed_run():
    _make_real_sqlite_file(backup.DATA_DIR / "series_watcher.db")
    backup.run_backup_cycle(retention_count=4, now=1000.0, tier="large",
                             only=frozenset({"series_watcher.db"}), snapshot_root=backup.LARGE_BACKUP_DIR)

    body = client.get("/api/backup/status").json()

    assert body["large_tier"]["last_run"]["tier"] == "large"
    assert body["large_tier"]["snapshot_count"] == 1


def test_run_route_defaults_to_regular_tier():
    _make_real_sqlite_file(backup.DATA_DIR / "paper_broker.db")
    _make_real_sqlite_file(backup.DATA_DIR / "series_watcher.db")

    resp = client.post("/api/backup/run")

    assert resp.status_code == 200
    body = resp.json()
    assert body["tier"] == "regular"
    snapshot_dir = backup.BACKUP_DIR / body["snapshot"]
    assert (snapshot_dir / "paper_broker.db").exists()
    assert not (snapshot_dir / "series_watcher.db").exists()


def test_run_route_large_tier_backs_up_only_large_files():
    _make_real_sqlite_file(backup.DATA_DIR / "paper_broker.db")
    _make_real_sqlite_file(backup.DATA_DIR / "series_watcher.db")

    resp = client.post("/api/backup/run?tier=large")

    assert resp.status_code == 200
    body = resp.json()
    assert body["tier"] == "large"
    snapshot_dir = backup.LARGE_BACKUP_DIR / body["snapshot"]
    assert (snapshot_dir / "series_watcher.db").exists()
    assert not (snapshot_dir / "paper_broker.db").exists()


def test_run_route_all_tier_runs_both_cycles():
    _make_real_sqlite_file(backup.DATA_DIR / "paper_broker.db")
    _make_real_sqlite_file(backup.DATA_DIR / "series_watcher.db")

    resp = client.post("/api/backup/run?tier=all")

    assert resp.status_code == 200
    body = resp.json()
    assert body["regular"]["tier"] == "regular"
    assert body["large"]["tier"] == "large"
    regular_dir = backup.BACKUP_DIR / body["regular"]["snapshot"]
    large_dir = backup.LARGE_BACKUP_DIR / body["large"]["snapshot"]
    assert (regular_dir / "paper_broker.db").exists()
    assert not (regular_dir / "series_watcher.db").exists()
    assert (large_dir / "series_watcher.db").exists()
    assert not (large_dir / "paper_broker.db").exists()
