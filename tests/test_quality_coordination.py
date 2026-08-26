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


from datetime import datetime, timedelta, timezone

from services.quality_coordination import (
    BranchSignal, Claim, Signal, apply_observation, _connect,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _sig(key="k1", level="info", paths=("services/x.py",)):
    return Signal(automation_key=key, level=level, scope_paths=paths,
                  source_finding_id="fid", source_check="check")


def test_new_signal_creates_observed_item(tmp_path, monkeypatch):
    monkeypatch.setattr("services.quality_coordination.DB_PATH", tmp_path / "q.db")
    conn = _connect()
    result = apply_observation(conn, [_sig()], [], [], T0)
    conn.commit()
    assert result["k1"] == "observed"
    row = conn.execute("SELECT * FROM coordination_items WHERE automation_key='k1'").fetchone()
    assert row["observation_count"] == 1
    conn.close()


def test_absent_signal_resolves_previously_tracked_item(tmp_path, monkeypatch):
    monkeypatch.setattr("services.quality_coordination.DB_PATH", tmp_path / "q.db")
    conn = _connect()
    apply_observation(conn, [_sig()], [], [], T0)
    conn.commit()
    result = apply_observation(conn, [], [], [], T0 + timedelta(hours=1))
    conn.commit()
    assert result["k1"] == "resolved"
    conn.close()


def test_exact_claim_suppresses(tmp_path, monkeypatch):
    monkeypatch.setattr("services.quality_coordination.DB_PATH", tmp_path / "q.db")
    conn = _connect()
    apply_observation(conn, [_sig()], [], [], T0)
    conn.commit()
    result = apply_observation(
        conn, [_sig()], [], [Claim(automation_key="k1", source="PR#1")], T0 + timedelta(minutes=5)
    )
    conn.commit()
    assert result["k1"] == "suppressed_pending_work"
    conn.close()


def test_path_overlap_branch_suppresses(tmp_path, monkeypatch):
    monkeypatch.setattr("services.quality_coordination.DB_PATH", tmp_path / "q.db")
    conn = _connect()
    apply_observation(conn, [_sig()], [], [], T0)
    conn.commit()
    branch = BranchSignal(name="fix/x", changed_paths=("services/x.py",),
                           last_commit_at_iso=(T0 + timedelta(minutes=5)).isoformat())
    result = apply_observation(conn, [_sig()], [branch], [], T0 + timedelta(minutes=5))
    conn.commit()
    assert result["k1"] == "suppressed_pending_work"
    conn.close()


def test_floor_met_with_no_signal_is_escalation_eligible(tmp_path, monkeypatch):
    monkeypatch.setattr("services.quality_coordination.DB_PATH", tmp_path / "q.db")
    conn = _connect()
    apply_observation(conn, [_sig(level="warning")], [], [], T0)
    conn.commit()
    result = apply_observation(conn, [_sig(level="warning")], [], [], T0 + timedelta(hours=7))
    conn.commit()
    assert result["k1"] == "escalation_eligible"
    conn.close()


def test_recurrence_reopens_same_key_with_history(tmp_path, monkeypatch):
    monkeypatch.setattr("services.quality_coordination.DB_PATH", tmp_path / "q.db")
    conn = _connect()
    apply_observation(conn, [_sig()], [], [], T0)
    conn.commit()
    apply_observation(conn, [], [], [], T0 + timedelta(hours=1))  # resolved
    conn.commit()
    result = apply_observation(conn, [_sig()], [], [], T0 + timedelta(hours=5))
    conn.commit()
    assert result["k1"] == "observed"
    row = conn.execute("SELECT reopen_count FROM coordination_items WHERE automation_key='k1'").fetchone()
    assert row["reopen_count"] == 1
    log_count = conn.execute(
        "SELECT COUNT(*) c FROM coordination_log WHERE automation_key='k1'"
    ).fetchone()["c"]
    assert log_count >= 3  # observed, resolved, reopened — history retained, not truncated
    conn.close()


from unittest.mock import patch

from services.quality.models import QualityReport
from services.quality_coordination import observe_main


def test_observe_main_is_idempotent_on_repeated_identical_audit(tmp_path, monkeypatch):
    monkeypatch.setattr("services.quality_coordination.DB_PATH", tmp_path / "q.db")
    report = QualityReport(findings=[_finding(check="config-usage", scope="a.b")])
    with patch("services.quality_coordination._run_static_audit", return_value=report), \
         patch("services.quality_coordination._current_commit_sha", return_value=None):
        r1 = observe_main(tmp_path, at=T0)
        r2 = observe_main(tmp_path, at=T0 + timedelta(minutes=1))
    assert r1.audit_fingerprint == r2.audit_fingerprint
    assert r2.items_observed == 0  # second call short-circuits, no re-processing
    conn = _connect()
    runs = conn.execute("SELECT COUNT(*) c FROM coordination_runs").fetchone()["c"]
    assert runs == 1  # only one row, not two
    conn.close()


def test_observe_main_writes_run_row_with_counts(tmp_path, monkeypatch):
    monkeypatch.setattr("services.quality_coordination.DB_PATH", tmp_path / "q.db")
    report = QualityReport(findings=[_finding(check="config-usage", scope="a.b")])
    with patch("services.quality_coordination._run_static_audit", return_value=report), \
         patch("services.quality_coordination._current_commit_sha", return_value="deadbeef"):
        result = observe_main(tmp_path, at=T0)
    assert result.items_observed == 1
    assert result.commit_sha == "deadbeef"
    assert result.error is None


def test_observe_main_survives_scanner_exception_and_records_error(tmp_path, monkeypatch):
    monkeypatch.setattr("services.quality_coordination.DB_PATH", tmp_path / "q.db")
    with patch("services.quality_coordination._run_static_audit", side_effect=RuntimeError("boom")):
        result = observe_main(tmp_path, at=T0)
    assert result.error is not None and "boom" in result.error
    assert result.items_observed == 0


def test_scope_paths_prefers_evidence_path_over_dotted_scope():
    """Real bug this test exists to prevent: `scope` is a dotted module path for most semantic
    rules (e.g. 'services.foo'), which never equals a real GitHub file path ('services/foo.py')
    — using it directly for suppression path-overlap matching would make every such rule
    permanently unsuppressible by any real branch. evidence['path'] is the real file path."""
    from services.quality_coordination import _scope_paths
    f = _finding(check="router-registration", scope="services.foo", evidence={"path": "services/foo.py"})
    assert _scope_paths(f) == ("services/foo.py",)


def test_scope_paths_falls_back_to_scope_for_aggregate_rules_with_no_path():
    from services.quality_coordination import _scope_paths
    f = _finding(check="api-usage-inventory", scope="KalshiClient.get_market", evidence={})
    assert _scope_paths(f) == ("KalshiClient.get_market",)
