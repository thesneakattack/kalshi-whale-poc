"""
GET/POST /api/health/storage* (services/storage_health/routes.py, QCP
Task 11). Relies on tests/conftest.py's global install_runtime_isolation()
for every registered persistence module's DB_PATH, same as
tests/test_quality_routes.py - see tests/support/runtime_isolation.py's own
docstring for why a second, per-file redirect isn't needed for a new test
file.
"""
import sqlite3
from pathlib import Path

import pytest

import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from services.app_state import state  # noqa: E402
from services.storage_health import storage_health  # noqa: E402

client = TestClient(main.app)


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.execute("INSERT INTO t (x) VALUES (1)")
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_health, "DATA_DIR", tmp_path)
    state["storage_health"] = {
        "last_sampled_at": 0.0, "scanning": False, "last_started_at": 0.0,
        "task": None, "last_scan": None,
    }
    yield


def test_get_storage_health_reports_every_db_file_fast_path_only(tmp_path):
    _make_db(tmp_path / "a.db")
    _make_db(tmp_path / "b.db")

    resp = client.get("/api/health/storage")

    assert resp.status_code == 200
    body = resp.json()
    assert sorted(d["name"] for d in body["databases"]) == ["a.db", "b.db"]
    assert all(d["table_row_counts"] is None for d in body["databases"])  # fast path, no COUNT(*)
    assert body["scanning"] is False
    assert body["last_scan"] is None


def test_post_storage_scan_schedules_a_background_scan():
    """Doesn't assert on state["storage_health"]["scanning"] afterward - an
    empty tmp_path's deep scan is fast enough to complete (flipping
    "scanning" back to False in its own finally) before this synchronous
    thread's next line runs, a genuine race rather than a bug. Checking
    last_started_at instead is race-free: the route handler sets it
    synchronously, before task_supervisor.supervise() is even called, and
    nothing ever resets it back to 0."""
    resp = client.post("/api/health/storage/scan")

    assert resp.status_code == 200
    assert resp.json() == {"scheduled": True, "already_running": False}
    assert state["storage_health"]["last_started_at"] > 0


def test_post_storage_scan_reports_already_running_without_rescheduling():
    state["storage_health"]["scanning"] = True
    state["storage_health"]["last_started_at"] = 12345.0

    resp = client.post("/api/health/storage/scan")

    assert resp.status_code == 200
    assert resp.json() == {"scheduled": False, "already_running": True}
    assert state["storage_health"]["last_started_at"] == 12345.0  # untouched - no new scan kicked off


def test_post_integrity_check_returns_ok_for_a_valid_db(tmp_path):
    _make_db(tmp_path / "sample.db")

    resp = client.post("/api/health/storage/integrity-check", params={"name": "sample.db"})

    assert resp.status_code == 200
    assert resp.json() == {"name": "sample.db", "ok": True, "detail": "ok"}


def test_post_integrity_check_reports_missing_file_without_500(tmp_path):
    resp = client.post("/api/health/storage/integrity-check", params={"name": "nope.db"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "not found" in body["error"]


@pytest.mark.parametrize("name", ["../secrets.db", "sub/dir.db", "no_extension", ""])
def test_post_integrity_check_rejects_path_traversal_and_invalid_names(name):
    resp = client.post("/api/health/storage/integrity-check", params={"name": name})

    assert resp.status_code in (400, 422)  # 422 if FastAPI itself rejects an empty required str
