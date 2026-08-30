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

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def _isolated_storage_health(tmp_path, monkeypatch):
    """storage_health.DATA_DIR defaults to the real repo data/ directory -
    without this redirect, GET /api/quality/summary's new storage_health
    composition (QCP Task 11) would open real data/*.db files, which
    tests/support/runtime_isolation.py's sqlite3.connect guard hard-blocks."""
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




def test_quality_summary_reports_historical_drops_as_info_and_window_drops_as_error(monkeypatch):
    """#72 at the API level (step 1 of CLAUDE.md's investigation ladder): a
    nonzero lifetime drop count with a clean sample window must not pin
    status to error; the live gateway's dropped_window is the verdict input.
    The evidence assertion on dropped_window == 0 (not None) also proves the
    route reads a real ingest_metrics() snapshot off the gateway singleton."""
    from services.quality import routes as quality_routes

    before = client.get("/api/quality/summary").json()
    assert not [f for f in before["findings"] if f["check"] == "ws-dropped-messages"]
    monkeypatch.setattr(quality_routes.trade_stream, "dropped_messages", 5985)
    monkeypatch.setattr(quality_routes.trade_stream, "_dropped_window", 0)

    body = client.get("/api/quality/summary").json()
    matches = [f for f in body["findings"] if f["check"] == "ws-dropped-messages"]
    assert len(matches) == 1
    assert matches[0]["severity"] == "info"
    assert matches[0]["evidence"] == {"dropped_window": 0, "dropped_messages": 5985}
    assert body["counts"]["error"] == before["counts"]["error"]

    monkeypatch.setattr(quality_routes.trade_stream, "_dropped_window", 1)
    body = client.get("/api/quality/summary").json()
    matches = [f for f in body["findings"] if f["check"] == "ws-dropped-messages"]
    assert matches[0]["severity"] == "error"
    assert matches[0]["evidence"] == {"dropped_window": 1, "dropped_messages": 5985}
    assert body["status"] == "error"


def test_quality_summary_status_is_error_while_a_critical_alert_is_active():
    """#71: a kill-switch/crash alert (severity "critical" in
    services/alerting) must be able to raise overall status; until now it
    lived only in the uncombined `alerts` field and could not."""
    import asyncio

    from services.alerting import alerting

    before = client.get("/api/quality/summary").json()
    assert not [f for f in before["findings"] if f["check"] == "active-alert"]

    async def _record():
        return alerting.record_alert("kill_switch", "critical", "Kill switch tripped: test", now=1000.0)

    alert_id = asyncio.run(_record())
    try:
        body = client.get("/api/quality/summary").json()
        assert body["status"] == "error"
        assert body["counts"]["error"] == before["counts"]["error"] + 1
        matches = [f for f in body["findings"] if f["check"] == "active-alert"]
        assert [f["finding_id"] for f in matches] == [f"alerting:active-alert:kill_switch:{alert_id}"]
        assert matches[0]["severity"] == "error"
        assert [a["id"] for a in body["alerts"]["active"]] == [alert_id]  # raw field kept
    finally:
        alerting.resolve_category("kill_switch", now=1010.0)

    after = client.get("/api/quality/summary").json()
    assert not [f for f in after["findings"] if f["check"] == "active-alert"]
    assert after["counts"]["error"] == before["counts"]["error"]
