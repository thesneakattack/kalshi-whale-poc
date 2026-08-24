"""
services/storage_health/storage_health.py (QCP Task 11) - read-only DB
inventory, growth detection, and integrity checks for every data/*.db file.

Uses disposable SQLite fixtures under tmp_path, never the real data/
directory - see tests/support/runtime_isolation.py's sqlite3.connect guard,
which would hard-fail any accidental connection to a real data/*.db path.
storage_health.observability's own DB_PATH is separately redirected by
tests/conftest.py's global install_runtime_isolation() before this file is
even collected, so the growth-finding tests below (which write synthetic
size samples through observability.record_sample) never touch the real
data/observability.db either.
"""
import sqlite3
import time
from pathlib import Path

import pytest

from services.observability import observability
from services.storage_health import storage_health


@pytest.fixture(autouse=True)
def _isolated_observability(tmp_path, monkeypatch):
    monkeypatch.setattr(observability, "DB_PATH", tmp_path / "observability.db")


def _make_db(path: Path, rows: dict[str, int]) -> None:
    """rows: table_name -> row count to insert."""
    conn = sqlite3.connect(path)
    try:
        for table, count in rows.items():
            conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, value TEXT)")
            conn.executemany(
                f"INSERT INTO {table} (value) VALUES (?)", [(f"v{i}",) for i in range(count)]
            )
        conn.commit()
    finally:
        conn.close()


# --- _ro_connect: the actual read-only guarantee (QCP Task 22 final-
# verification - "storage health never repairs/mutates automatically" was
# an asserted property with no test proving it before this) -------------


def test_ro_connect_genuinely_cannot_write_to_the_database(tmp_path):
    """Not just a naming convention - `mode=ro` is a real SQLite URI flag
    enforced by SQLite itself, independent of anything this module's own
    code does right or wrong. Proves the guarantee at the level it
    actually holds: even a deliberate INSERT through this connection
    fails, not just that this module's own functions happen not to call
    one."""
    path = tmp_path / "sample.db"
    _make_db(path, {"widgets": 1})

    conn = storage_health._ro_connect(path)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly database"):
            conn.execute("INSERT INTO widgets (value) VALUES ('should never land')")
            conn.commit()
    finally:
        conn.close()

    # Confirm independently, through a normal read-write connection, that
    # nothing actually landed.
    verify_conn = sqlite3.connect(path)
    try:
        count = verify_conn.execute("SELECT COUNT(*) FROM widgets").fetchone()[0]
    finally:
        verify_conn.close()
    assert count == 1  # still just the one row _make_db inserted


# --- database_health --------------------------------------------------


def test_database_health_reports_size_page_count_and_tables(tmp_path):
    path = tmp_path / "sample.db"
    _make_db(path, {"widgets": 3, "gadgets": 5})

    result = storage_health.database_health(path)

    assert result["exists"] is True
    assert result["name"] == "sample.db"
    assert result["size_bytes"] > 0
    assert result["page_count"] > 0
    assert result["page_size"] > 0
    assert sorted(result["tables"]) == ["gadgets", "widgets"]
    assert result["table_row_counts"] is None  # fast path never counts rows
    assert result["error"] is None


def test_database_health_include_table_counts_reports_row_counts(tmp_path):
    path = tmp_path / "sample.db"
    _make_db(path, {"widgets": 3, "gadgets": 5})

    result = storage_health.database_health(path, include_table_counts=True)

    assert result["table_row_counts"] == {"widgets": 3, "gadgets": 5}


def test_database_health_missing_file_returns_explicit_error(tmp_path):
    result = storage_health.database_health(tmp_path / "nope.db")

    assert result["exists"] is False
    assert result["error"] == "file not found"
    assert result["size_bytes"] is None


def test_database_health_corrupt_file_returns_explicit_error_without_raising(tmp_path):
    path = tmp_path / "corrupt.db"
    path.write_bytes(b"this is not a sqlite file, just garbage bytes")

    result = storage_health.database_health(path)

    assert result["exists"] is True
    assert result["size_bytes"] > 0
    assert result["error"] is not None
    assert result["tables"] is None


# --- inventory_data_dir -------------------------------------------------


def test_inventory_data_dir_lists_every_db_file(tmp_path):
    _make_db(tmp_path / "a.db", {"t": 1})
    _make_db(tmp_path / "b.db", {"t": 1})
    (tmp_path / "not_a_db.txt").write_text("ignore me")

    entries = storage_health.inventory_data_dir(tmp_path)

    assert sorted(e["name"] for e in entries) == ["a.db", "b.db"]


def test_inventory_data_dir_missing_dir_returns_empty_list(tmp_path):
    assert storage_health.inventory_data_dir(tmp_path / "does_not_exist") == []


# --- quick_check ---------------------------------------------------------


def test_quick_check_ok_on_valid_db(tmp_path):
    path = tmp_path / "sample.db"
    _make_db(path, {"widgets": 2})

    result = storage_health.quick_check(path)

    assert result == {"ok": True, "detail": "ok"}


def test_quick_check_missing_file_returns_explicit_error_without_raising(tmp_path):
    result = storage_health.quick_check(tmp_path / "nope.db")

    assert result["ok"] is False
    assert "not found" in result["error"]


def test_quick_check_corrupt_file_returns_explicit_error_without_raising(tmp_path):
    path = tmp_path / "corrupt.db"
    path.write_bytes(b"not a real sqlite database")

    result = storage_health.quick_check(path)

    assert result["ok"] is False
    assert result.get("error") or result.get("detail")


# --- resolve_db_path (path-traversal guard for the integrity-check route) --


def test_resolve_db_path_accepts_a_real_file_directly_inside_data_dir(tmp_path):
    (tmp_path / "sample.db").write_bytes(b"")

    resolved = storage_health.resolve_db_path(tmp_path, "sample.db")

    assert resolved == tmp_path / "sample.db"


@pytest.mark.parametrize(
    "name", ["../secrets.db", "sub/dir.db", "no_extension", "", "..", "sample.db/../../etc/passwd"]
)
def test_resolve_db_path_rejects_anything_that_is_not_a_bare_filename(tmp_path, name):
    assert storage_health.resolve_db_path(tmp_path, name) is None


# --- capture_size_samples -------------------------------------------------


def test_capture_size_samples_persists_into_observability_history():
    now = time.time()
    storage_health.capture_size_samples({"game_state.db": 12345}, now=now)

    samples = observability.history("db.game_state.db.size_bytes", since_ts=now - 1)

    assert len(samples) == 1
    assert samples[0]["value"] == 12345


def test_capture_size_samples_skips_entries_with_no_size():
    now = time.time()
    storage_health.capture_size_samples({"missing.db": None}, now=now)

    assert observability.history("db.missing.db.size_bytes", since_ts=now - 1) == []


# --- storage growth finding ------------------------------------------------


def test_storage_growth_finding_detects_synthetic_fast_growth_fixture():
    now = time.time()
    observability.record_sample(
        "db.game_state.db.size_bytes", 1_000_000, observed_at=now - 3 * 3600,
    )

    finding = storage_health.storage_growth_finding("game_state.db", 5_000_000, now=now)

    assert finding is not None
    assert finding.finding_id == "storage-growth:data/game_state.db"
    assert finding.severity == "warning"
    assert finding.evidence["oldest_size_bytes"] == 1_000_000
    assert finding.evidence["current_size_bytes"] == 5_000_000


def test_storage_growth_finding_no_history_returns_none():
    finding = storage_health.storage_growth_finding("brand_new.db", 5_000_000)

    assert finding is None


def test_storage_growth_finding_normal_growth_returns_none():
    now = time.time()
    observability.record_sample(
        "db.steady.db.size_bytes", 1_000_000, observed_at=now - 3 * 3600,
    )

    finding = storage_health.storage_growth_finding("steady.db", 1_100_000, now=now)

    assert finding is None


def test_storage_growth_finding_span_too_short_returns_none():
    now = time.time()
    observability.record_sample(
        "db.fresh.db.size_bytes", 1_000_000, observed_at=now - 60,  # 1 minute ago
    )

    finding = storage_health.storage_growth_finding("fresh.db", 5_000_000, now=now)

    assert finding is None


def test_storage_growth_finding_shrinking_db_returns_none():
    now = time.time()
    observability.record_sample(
        "db.shrunk.db.size_bytes", 5_000_000, observed_at=now - 3 * 3600,
    )

    finding = storage_health.storage_growth_finding("shrunk.db", 1_000_000, now=now)

    assert finding is None


# --- backup-overdue finding --------------------------------------------


def test_backup_overdue_finding_flags_a_stale_last_run():
    now = time.time()
    last_run = {"finished_at": now - 100_000}

    finding = storage_health.backup_overdue_finding(last_run, interval_sec=21600, now=now)

    assert finding is not None
    assert finding.finding_id == "backup-overdue"
    assert finding.severity == "warning"


def test_backup_overdue_finding_recent_run_returns_none():
    now = time.time()
    last_run = {"finished_at": now - 60}

    finding = storage_health.backup_overdue_finding(last_run, interval_sec=21600, now=now)

    assert finding is None


def test_backup_overdue_finding_no_run_yet_returns_none():
    finding = storage_health.backup_overdue_finding(None, interval_sec=21600)

    assert finding is None


# --- storage integrity finding (from the fast pragma-open failure) --------


def test_storage_integrity_finding_from_a_pragma_open_failure():
    entry = {"name": "corrupt.db", "exists": True, "error": "file is not a database"}

    finding = storage_health.storage_integrity_finding(entry)

    assert finding is not None
    assert finding.finding_id == "storage-integrity:data/corrupt.db"
    assert finding.severity == "error"


def test_storage_integrity_finding_healthy_entry_returns_none():
    entry = {"name": "fine.db", "exists": True, "error": None}

    assert storage_health.storage_integrity_finding(entry) is None


def test_storage_integrity_finding_missing_file_returns_none():
    entry = {"name": "nope.db", "exists": False, "error": "file not found"}

    assert storage_health.storage_integrity_finding(entry) is None


# --- storage_findings composition ------------------------------------------


def test_storage_findings_composes_growth_integrity_and_backup_overdue():
    now = time.time()
    observability.record_sample(
        "db.growing.db.size_bytes", 1_000_000, observed_at=now - 3 * 3600,
    )
    entries = [
        {"name": "growing.db", "exists": True, "size_bytes": 5_000_000, "error": None},
        {"name": "corrupt.db", "exists": True, "size_bytes": 10, "error": "file is not a database"},
        {"name": "fine.db", "exists": True, "size_bytes": 500, "error": None},
    ]

    findings = storage_health.storage_findings(
        entries, last_backup_run={"finished_at": now - 100_000}, backup_interval_sec=21600, now=now,
    )

    ids = {f.finding_id for f in findings}
    assert ids == {
        "storage-growth:data/growing.db",
        "storage-integrity:data/corrupt.db",
        "backup-overdue",
    }


def test_storage_findings_empty_when_everything_is_healthy():
    entries = [{"name": "fine.db", "exists": True, "size_bytes": 500, "error": None}]

    findings = storage_health.storage_findings(
        entries, last_backup_run={"finished_at": time.time()}, backup_interval_sec=21600,
    )

    assert findings == []
