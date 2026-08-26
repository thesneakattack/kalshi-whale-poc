import sqlite3

import services.quality_coordination as qc
from services.quality.models import QualityFinding
from services.quality_coordination import derive_automation_key


def test_connect_creates_all_three_tables(tmp_path, monkeypatch):
    monkeypatch.setattr(qc, "DB_PATH", tmp_path / "quality_coordination.db")
    conn = qc._connect()
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {"coordination_items", "coordination_log", "coordination_runs"} <= tables
    conn.close()


def test_connect_is_idempotent_on_repeated_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(qc, "DB_PATH", tmp_path / "quality_coordination.db")
    qc._connect().close()
    conn = qc._connect()  # CREATE TABLE IF NOT EXISTS must not raise on the second call
    conn.execute("SELECT 1")
    conn.close()


def _finding(**kw):
    base = dict(
        finding_id="x", check="c", severity="info", confidence="high",
        source="ci", scope="module.func", summary="s", evidence={},
    )
    base.update(kw)
    return QualityFinding(**base)


def test_semantic_rule_key_uses_scope_directly():
    f = _finding(check="router-registration", scope="services.foo")
    assert derive_automation_key(f) == "router-registration|services.foo|"


def test_frontend_route_unknown_uses_normalized_raw_evidence():
    f = _finding(check="frontend-api-contract", scope="file.js:12", evidence={"raw": "  fetch('/x')  "})
    assert derive_automation_key(f) == "frontend-api-contract|file.js|fetch('/x')"


def test_kalshi_boundary_sdk_import_uses_imported_module():
    f = _finding(check="kalshi-boundary", scope="services/x.py", evidence={"imported": "kalshi_python"})
    assert derive_automation_key(f) == "kalshi-boundary|services/x.py|kalshi_python"


def test_kalshi_boundary_deprecated_read_uses_field_from_evidence():
    f = _finding(check="kalshi-boundary", scope="services/x.py", evidence={"field": "taker_side"})
    assert derive_automation_key(f) == "kalshi-boundary|services/x.py|taker_side"


def test_resource_unclosed_falls_back_to_finding_id():
    """No class name available in evidence today (I11 §3 fallback #1) — falls back to the
    existing finding_id, which stays as symbol-sensitive as it already is."""
    f = _finding(check="resource-lifecycle", finding_id="resource-lifecycle:mod.func:client", scope="mod.func")
    assert derive_automation_key(f) == "resource-lifecycle|mod.func|resource-lifecycle:mod.func:client"


def test_kalshi_boundary_host_falls_back_to_bounded_snippet():
    """No isolated host token available in evidence today (I11 §3 fallback #2) — falls back to
    the raw snippet, bounded to 80 chars."""
    f = _finding(check="kalshi-boundary", scope="services/x.py", evidence={"snippet": "x" * 200})
    key = derive_automation_key(f)
    assert key == "kalshi-boundary|services/x.py|" + "x" * 80


def test_line_shift_does_not_change_identity():
    """I1 §9's location-free contract: two findings differing only in scope's line number
    produce the SAME automation_key when the rule is semantic (scope already excludes line for
    these rules by construction)."""
    f1 = _finding(check="config-usage", scope="alerting.crash_auto_resolve_after_sec")
    f2 = _finding(check="config-usage", scope="alerting.crash_auto_resolve_after_sec")
    assert derive_automation_key(f1) == derive_automation_key(f2)
