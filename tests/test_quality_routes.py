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


def test_quality_summary_gains_coordination_rollup():
    resp = client.get("/api/quality/summary")
    assert resp.status_code == 200
    assert set(resp.json()["coordination"]) == {"escalation_eligible", "suppressed", "observed"}
