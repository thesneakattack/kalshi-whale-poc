"""
GET /api/quality/summary (services/quality/routes.py, QCP Task 10) - the
unified read-only "is the system healthy" endpoint composing
diagnostics.run_offline, observability.runtime_findings, alerting's active
alerts, and fault_log.summary.

Relies on tests/conftest.py's global install_runtime_isolation() (every
registered persistence module's DB_PATH redirected, and a hard sqlite3.connect
guard against the real data/ directory, both installed before this file is
even collected) rather than a second, redundant per-file DB_PATH redirect -
see tests/support/runtime_isolation.py's own docstring for why the older
per-file pattern (tests/test_trading_gate.py and similar) is no longer
necessary for a new test file.
"""
import pytest  # noqa: E402

import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from services.storage_health import storage_health  # noqa: E402
import services.quality_coordination as qc  # noqa: E402

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def _isolated_storage_health(tmp_path, monkeypatch):
    """storage_health.DATA_DIR defaults to the real repo data/ directory -
    without this redirect, GET /api/quality/summary's new storage_health
    composition (QCP Task 11) would open real data/*.db files, which
    tests/support/runtime_isolation.py's sqlite3.connect guard hard-blocks.

    services.quality_coordination's own DB_PATH no longer needs a local
    redirect here - it's now registered in
    tests/support/runtime_isolation.py's PERSISTENCE_MODULE_PATHS, so the
    global install_runtime_isolation() call in conftest.py already covers
    it before this module is even collected."""
    monkeypatch.setattr(storage_health, "DATA_DIR", tmp_path)


def test_quality_summary_returns_the_documented_top_level_shape():
    resp = client.get("/api/quality/summary")
    assert resp.status_code == 200
    body = resp.json()
    for key in ("generated_at", "status", "counts", "findings", "diagnostics", "alerts", "faults", "storage"):
        assert key in body
    assert body["status"] in ("ok", "warning", "error")
    assert set(body["counts"].keys()) == {"info", "warning", "error"}
    assert isinstance(body["findings"], list)
    assert body["storage"]["databases"] == []  # tmp_path is empty - no data/*.db fixtures


def test_quality_summary_makes_no_kalshi_network_calls(monkeypatch):
    """diagnostics.run_offline() itself deliberately excludes check_coverage
    (the one diagnostic that calls the real Kalshi API) - this proves that
    invariant holds for the whole composed route, not just run_offline in
    isolation, by making any KalshiPublicGateway construction a hard failure."""
    from services.kalshi import public as kalshi_public

    def _raise(*args, **kwargs):
        raise AssertionError("GET /api/quality/summary must never construct a KalshiPublicGateway")

    monkeypatch.setattr(kalshi_public.KalshiPublicGateway, "__init__", _raise)

    resp = client.get("/api/quality/summary")

    assert resp.status_code == 200


def test_quality_coordination_route_returns_items_and_log():
    conn = qc._connect()
    conn.execute(
        """INSERT INTO coordination_items
           (automation_key, state, level, first_observed_at, last_observed_at,
            observation_count, reopen_count, scope_paths, source_finding_id, source_check)
           VALUES ('k1', 'escalation_eligible', 'warning', '2026-01-01T00:00:00',
                   '2026-01-01T06:00:00', 3, 0, 'a.py', 'fid', 'check')"""
    )
    conn.commit()
    conn.close()
    resp = client.get("/api/quality/coordination")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"][0]["automation_key"] == "k1"
    assert body["items"][0]["state"] == "escalation_eligible"


def _insert_item(conn, key, last_observed_at):
    conn.execute(
        """INSERT INTO coordination_items
           (automation_key, state, level, first_observed_at, last_observed_at,
            observation_count, reopen_count, scope_paths, source_finding_id, source_check)
           VALUES (?, 'observed', 'warning', '2026-01-01T00:00:00', ?,
                   1, 0, 'a.py', 'fid', 'check')""",
        (key, last_observed_at),
    )


def test_quality_coordination_route_respects_limit(monkeypatch, tmp_path):
    # Own isolated DB file, not the shared session-wide one the earlier
    # single-item test already wrote 'k1' into - the global redirect in
    # conftest.py is per-session, not per-test, so reusing it across tests
    # that pick predictable keys would collide (UNIQUE constraint / stale
    # locked connections), same class of issue tests/support/runtime_
    # isolation.py's own docstring documents for module-scope singletons.
    monkeypatch.setattr(qc, "DB_PATH", tmp_path / "quality_coordination_test.db")
    conn = qc._connect()
    # 3 items with distinct last_observed_at - k3 most recent, k1 oldest.
    _insert_item(conn, "k1", "2026-01-01T01:00:00")
    _insert_item(conn, "k2", "2026-01-01T02:00:00")
    _insert_item(conn, "k3", "2026-01-01T03:00:00")
    conn.commit()
    conn.close()

    resp = client.get("/api/quality/coordination?limit=2")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 2
    assert [item["automation_key"] for item in body["items"]] == ["k3", "k2"]


def test_quality_coordination_route_caps_log_entries_per_item(monkeypatch, tmp_path):
    monkeypatch.setattr(qc, "DB_PATH", tmp_path / "quality_coordination_test.db")
    conn = qc._connect()
    _insert_item(conn, "k1", "2026-01-01T00:00:00")
    for i in range(25):
        conn.execute(
            "INSERT INTO coordination_log (automation_key, at, message) VALUES (?, ?, ?)",
            ("k1", f"2026-01-01T00:{i:02d}:00", f"msg{i}"),
        )
    conn.commit()
    conn.close()

    resp = client.get("/api/quality/coordination")
    assert resp.status_code == 200
    body = resp.json()
    log = body["items"][0]["log"]
    assert len(log) == 20
    # The inner window function selects the 20 most recent by rn (msg5..msg24
    # survive, msg0..msg4 are dropped as the 5 oldest) - but the final display
    # order is ascending (oldest of the surviving 20 first), matching Task 7's
    # original ORDER BY at convention this route inherits.
    assert log[0]["message"] == "msg5"
    assert log[-1]["message"] == "msg24"


def test_quality_coordination_route_makes_one_log_query_not_n_plus_one(monkeypatch, tmp_path):
    """sqlite3.Connection is an immutable C type - neither instance nor
    class-level attribute assignment of `execute` is possible (verified: both
    raise). So rather than patching sqlite3.Connection.execute itself, this
    wraps the connection object returned by _qc._connect() - the same
    object routes.py calls .execute()/.close() on - in a counting proxy.
    Because the wrap happens only after _connect()'s own internal setup
    (PRAGMA + CREATE TABLE IF NOT EXISTS calls) has already run on the real
    connection, the counter reflects only the queries the route body itself
    issues, matching the "one for items, one for all logs" claim exactly."""
    monkeypatch.setattr(qc, "DB_PATH", tmp_path / "quality_coordination_test.db")
    conn = qc._connect()
    _insert_item(conn, "k1", "2026-01-01T01:00:00")
    _insert_item(conn, "k2", "2026-01-01T02:00:00")
    _insert_item(conn, "k3", "2026-01-01T03:00:00")
    conn.commit()
    conn.close()

    call_count = 0
    real_connect = qc._connect

    class _CountingConn:
        def __init__(self, real):
            self._real = real

        def execute(self, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            return self._real.execute(*args, **kwargs)

        def close(self):
            self._real.close()

    def _counting_connect():
        return _CountingConn(real_connect())

    monkeypatch.setattr(qc, "_connect", _counting_connect)

    resp = client.get("/api/quality/coordination")

    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 3
    assert call_count == 2  # one for items, one window-function query for all logs - not 1 + len(items)


def test_quality_summary_gains_coordination_rollup():
    resp = client.get("/api/quality/summary")
    assert resp.status_code == 200
    assert set(resp.json()["coordination"]) == {"escalation_eligible", "suppressed", "observed"}
