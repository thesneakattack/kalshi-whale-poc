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


def test_quality_summary_surfaces_a_completeness_defect_as_a_warning_finding(monkeypatch):
    monkeypatch.setattr(
        main.quality_routes.evidence_provenance, "current_completeness_state",
        lambda: {
            "degraded": True,
            "defects": [{
                "component": "settlement_resolver", "field": "dropped_after_max_attempts",
                "count": 3, "detail": "3 settlement(s) gave up after the retry budget",
            }],
            "checked_at": 0.0,
        },
    )

    resp = client.get("/api/quality/summary")

    assert resp.status_code == 200
    body = resp.json()
    ids = [f["finding_id"] for f in body["findings"]]
    assert "evidence_provenance:settlement_resolver:dropped_after_max_attempts" in ids
    assert body["status"] in ("warning", "error")


def test_quality_summary_has_no_evidence_provenance_findings_when_clean(monkeypatch):
    monkeypatch.setattr(
        main.quality_routes.evidence_provenance, "current_completeness_state",
        lambda: {"degraded": False, "defects": [], "checked_at": 0.0},
    )

    resp = client.get("/api/quality/summary")

    ids = [f["finding_id"] for f in resp.json()["findings"]]
    assert not any(i.startswith("evidence_provenance:") for i in ids)


def test_quality_summary_dispatches_its_blocking_calls_off_the_event_loop(monkeypatch):
    """Issue #530: this route ran undispatched synchronous DB/file reads
    directly in its async body. Direct read of the current source (not the
    census doc's grep-based list alone, which named
    alerting.active_alerts/fault_log.summary/research.latest/
    observability.runtime_findings but missed three more reached directly in
    this same handler body: storage_health.inventory_data_dir,
    storage_health.storage_findings, and backup.latest, the last one buried
    as an inline argument expression) found seven genuine undispatched
    call sites, not four.

    Excluded from this test on purpose, each independently confirmed by
    direct read: alerting.alert_findings (pure computation over an
    already-fetched list) and evidence_provenance.findings (its whole call
    graph - settlement_resolver.snapshot/index_feed.ingestion.snapshot/
    capture_writer.dropped_count - is in-memory counters, no I/O)
    genuinely need no dispatch; diagnostics.run_offline is already awaited
    and runs natively on aiosqlite (a separate, already-fixed piece, per
    this file's own module docstring and services/diagnostics/diagnostics.py's
    comment), so re-wrapping it here would be redundant, not a fix.

    Same spy idiom as tests/test_loop_watchdog.py's
    test_stall_captures_a_stack_and_records_it_off_the_loop: the spy checks
    whether asyncio.get_running_loop() succeeds *inside* the real call. This
    proves the call actually left the event loop (ran on a worker thread),
    not merely that asyncio.to_thread was invoked somewhere - a mock of
    asyncio.to_thread itself could pass while the real dispatch was wired
    wrong."""
    import asyncio

    from services.alerting import alerting
    from services.backup import backup
    from services import fault_log
    from services.observability import observability
    from services.research import research
    from services.storage_health import storage_health

    on_loop: dict[str, bool] = {}

    def _spy(name, real):
        def wrapper(*args, **kwargs):
            try:
                asyncio.get_running_loop()
                on_loop[name] = True
            except RuntimeError:
                on_loop[name] = False
            return real(*args, **kwargs)
        return wrapper

    monkeypatch.setattr(alerting, "active_alerts", _spy("active_alerts", alerting.active_alerts))
    monkeypatch.setattr(fault_log, "summary", _spy("fault_log_summary", fault_log.summary))
    monkeypatch.setattr(research, "latest", _spy("research_latest", research.latest))
    monkeypatch.setattr(
        observability, "runtime_findings", _spy("runtime_findings", observability.runtime_findings)
    )
    monkeypatch.setattr(
        storage_health, "inventory_data_dir", _spy("inventory_data_dir", storage_health.inventory_data_dir)
    )
    monkeypatch.setattr(
        storage_health, "storage_findings", _spy("storage_findings", storage_health.storage_findings)
    )
    monkeypatch.setattr(backup, "latest", _spy("backup_latest", backup.latest))

    resp = client.get("/api/quality/summary")

    assert resp.status_code == 200
    expected = {
        "active_alerts", "fault_log_summary", "research_latest", "runtime_findings",
        "inventory_data_dir", "storage_findings", "backup_latest",
    }
    assert set(on_loop.keys()) == expected, f"not every target call site was exercised: {on_loop}"
    still_on_loop = {name for name, was_on_loop in on_loop.items() if was_on_loop}
    assert not still_on_loop, f"these calls still ran synchronously on the event loop: {still_on_loop}"
