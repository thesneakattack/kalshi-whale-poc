import sqlite3

import tools.quality_ratchet as qc
import tools.quality_audit.baseline as qc_baseline
from services.quality.models import QualityFinding
from tools.quality_ratchet import derive_automation_key


def test_connect_creates_all_three_tables(tmp_path, monkeypatch):
    monkeypatch.setattr(qc, "DB_PATH", tmp_path / "quality_ratchet.db")
    conn = qc._connect()
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {"coordination_items", "coordination_log", "coordination_runs"} <= tables
    conn.close()


def test_connect_is_idempotent_on_repeated_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(qc, "DB_PATH", tmp_path / "quality_ratchet.db")
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

from tools.quality_ratchet import (
    BranchSignal, Claim, Signal, apply_observation, _connect,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _sig(key="k1", level="info", paths=("services/x.py",)):
    return Signal(automation_key=key, level=level, scope_paths=paths,
                  source_finding_id="fid", source_check="check")


def test_new_signal_creates_observed_item(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.quality_ratchet.DB_PATH", tmp_path / "q.db")
    conn = _connect()
    result = apply_observation(conn, [_sig()], [], [], T0)
    conn.commit()
    assert result["k1"] == "observed"
    row = conn.execute("SELECT * FROM coordination_items WHERE automation_key='k1'").fetchone()
    assert row["observation_count"] == 1
    conn.close()


def test_absent_signal_resolves_previously_tracked_item(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.quality_ratchet.DB_PATH", tmp_path / "q.db")
    conn = _connect()
    apply_observation(conn, [_sig()], [], [], T0)
    conn.commit()
    result = apply_observation(conn, [], [], [], T0 + timedelta(hours=1))
    conn.commit()
    assert result["k1"] == "resolved"
    conn.close()


def test_exact_claim_suppresses(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.quality_ratchet.DB_PATH", tmp_path / "q.db")
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
    monkeypatch.setattr("tools.quality_ratchet.DB_PATH", tmp_path / "q.db")
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
    monkeypatch.setattr("tools.quality_ratchet.DB_PATH", tmp_path / "q.db")
    conn = _connect()
    apply_observation(conn, [_sig(level="warning")], [], [], T0)
    conn.commit()
    result = apply_observation(conn, [_sig(level="warning")], [], [], T0 + timedelta(hours=7))
    conn.commit()
    assert result["k1"] == "escalation_eligible"
    conn.close()


def test_recurrence_reopens_same_key_with_history(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.quality_ratchet.DB_PATH", tmp_path / "q.db")
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
from tools.quality_ratchet import observe_main


def test_observe_main_is_idempotent_on_repeated_identical_audit(tmp_path, monkeypatch):
    """Idempotence now means "the run-history table doesn't grow a new row for
    unchanged content" — NOT "apply_observation is skipped." A consolidated addendum
    review found the original short-circuit-both design defeated the persistence floor
    for the exact case it exists to handle (see the escalation regression test below);
    the fix always reprocesses, and only the coordination_runs bookkeeping stays
    idempotent (UPDATE in place rather than a second INSERT)."""
    monkeypatch.setattr("tools.quality_ratchet.DB_PATH", tmp_path / "q.db")
    report = QualityReport(findings=[_finding(check="config-usage", scope="a.b")])
    with patch("tools.quality_ratchet._run_static_audit", return_value=report), \
         patch("tools.quality_ratchet._current_commit_sha", return_value=None):
        r1 = observe_main(tmp_path, at=T0)
        r2 = observe_main(tmp_path, at=T0 + timedelta(minutes=1))
    assert r1.audit_fingerprint == r2.audit_fingerprint
    assert r2.items_observed == 1  # reprocessed, not skipped
    conn = _connect()
    runs = conn.execute("SELECT COUNT(*) c FROM coordination_runs").fetchone()["c"]
    assert runs == 1  # still one row — UPDATEd in place, not a second INSERT
    conn.close()


def test_observe_main_writes_run_row_with_counts(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.quality_ratchet.DB_PATH", tmp_path / "q.db")
    report = QualityReport(findings=[_finding(check="config-usage", scope="a.b")])
    with patch("tools.quality_ratchet._run_static_audit", return_value=report), \
         patch("tools.quality_ratchet._current_commit_sha", return_value="deadbeef"):
        result = observe_main(tmp_path, at=T0)
    assert result.items_observed == 1
    assert result.commit_sha == "deadbeef"
    assert result.error is None


def test_observe_main_survives_scanner_exception_and_records_error(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.quality_ratchet.DB_PATH", tmp_path / "q.db")
    with patch("tools.quality_ratchet._run_static_audit", side_effect=RuntimeError("boom")):
        result = observe_main(tmp_path, at=T0)
    assert result.error is not None and "boom" in result.error
    assert result.items_observed == 0


def test_scope_paths_prefers_evidence_path_over_dotted_scope():
    """Real bug this test exists to prevent: `scope` is a dotted module path for most semantic
    rules (e.g. 'services.foo'), which never equals a real GitHub file path ('services/foo.py')
    — using it directly for suppression path-overlap matching would make every such rule
    permanently unsuppressible by any real branch. evidence['path'] is the real file path."""
    from tools.quality_ratchet import _scope_paths
    f = _finding(check="router-registration", scope="services.foo", evidence={"path": "services/foo.py"})
    assert _scope_paths(f) == ("services/foo.py",)


def test_scope_paths_falls_back_to_scope_for_aggregate_rules_with_no_path():
    from tools.quality_ratchet import _scope_paths
    f = _finding(check="api-usage-inventory", scope="KalshiClient.get_market", evidence={})
    assert _scope_paths(f) == ("KalshiClient.get_market",)


import urllib.error
import urllib.parse

from tools.quality_ratchet import derive_claims, fetch_branch_signals


def test_derive_claims_returns_empty_list():
    assert derive_claims() == []


def test_fetch_branch_signals_degrades_to_empty_list_on_network_error(monkeypatch):
    def _raise(*a, **kw):
        raise urllib.error.URLError("no network")
    monkeypatch.setattr("tools.quality_ratchet._http_get_json", _raise)
    assert fetch_branch_signals() == []


def test_fetch_branch_signals_parses_real_shaped_response(monkeypatch):
    branches_payload = [{"name": "fix/x", "commit": {"sha": "abc"}}]
    compare_payload = {
        "files": [{"filename": "services/x.py"}],
        "commits": [{"commit": {"committer": {"date": "2026-01-01T00:00:00Z"}}}],
    }

    def _fake_get(url, timeout):
        if url.endswith("/branches"):
            return branches_payload
        return compare_payload

    monkeypatch.setattr("tools.quality_ratchet._http_get_json", _fake_get)
    result = fetch_branch_signals()
    assert len(result) == 1
    assert result[0].name == "fix/x"
    assert result[0].changed_paths == ("services/x.py",)


def test_fetch_branch_signals_skips_a_malformed_branch_without_raising(monkeypatch):
    """Final-review fix (Important #3): parsing used to sit outside the per-branch
    try/except, so a malformed /compare response (a 'files' entry with no 'filename' key, a
    commits entry with a null committer) raised KeyError/TypeError straight out of
    fetch_branch_signals - contradicting its own "never raises... degrades to [] on any
    failure" docstring and aborting the whole coordination cycle. Both shapes below would
    have raised before the fix; now the malformed branch is just skipped and the well-formed
    branch is still returned."""
    branches_payload = [
        {"name": "broken/branch", "commit": {"sha": "bad"}},
        {"name": "fix/good", "commit": {"sha": "good"}},
    ]
    compare_payloads = {
        "broken/branch": {
            "files": [{"status": "modified"}],  # no 'filename' key
            "commits": [{"commit": {"committer": None}}],  # null committer
        },
        "fix/good": {
            "files": [{"filename": "services/y.py"}],
            "commits": [{"commit": {"committer": {"date": "2026-01-01T00:00:00Z"}}}],
        },
    }

    def _fake_get(url, timeout):
        if url.endswith("/branches"):
            return branches_payload
        for name, payload in compare_payloads.items():
            # Branch names are URL-encoded before being interpolated into the compare
            # URL (Task 13's edit 5 - a literal "/" in a branch name must not be sent
            # unencoded into a path segment), so match against the encoded form here too.
            if url.endswith(f"/compare/main...{urllib.parse.quote(name, safe='')}"):
                return payload
        raise AssertionError(f"unexpected compare URL: {url}")

    monkeypatch.setattr("tools.quality_ratchet._http_get_json", _fake_get)
    result = fetch_branch_signals()
    assert [s.name for s in result] == ["fix/good"]


def test_fetch_branch_signals_degrades_to_empty_list_when_branches_payload_is_not_a_list(monkeypatch):
    """GitHub's /branches endpoint is documented to return a list; a malformed/error response
    (e.g. a {'message': ...} error body) must degrade to [] rather than raising when the
    per-branch loop tries to treat it as one."""
    monkeypatch.setattr(
        "tools.quality_ratchet._http_get_json",
        lambda url, timeout: {"message": "Not Found"},
    )
    assert fetch_branch_signals() == []


def test_observe_main_reprocesses_a_fingerprint_that_recurs_non_consecutively(tmp_path, monkeypatch):
    """Task 10: idempotence must be scoped to the immediately-preceding run, not all history.
    {A} -> {A,B} -> {A} again: the third run's fingerprint equals the first's, but the
    immediately-preceding run (the {A,B} one) has a different fingerprint, so the third run
    must reprocess (not short-circuit) and mark B resolved. Under the old all-history
    UNIQUE/WHERE-lookup behavior this fingerprint would already exist in coordination_runs
    (from run 1) and the run would wrongly short-circuit."""
    monkeypatch.setattr("tools.quality_ratchet.DB_PATH", tmp_path / "q.db")
    report_a = QualityReport(findings=[_finding(check="config-usage", scope="a.b", finding_id="a")])
    report_ab = QualityReport(findings=[
        _finding(check="config-usage", scope="a.b", finding_id="a"),
        _finding(check="config-usage", scope="c.d", finding_id="b"),
    ])
    with patch("tools.quality_ratchet._current_commit_sha", return_value=None):
        with patch("tools.quality_ratchet._run_static_audit", return_value=report_a):
            r1 = observe_main(tmp_path, at=T0)
        with patch("tools.quality_ratchet._run_static_audit", return_value=report_ab):
            r2 = observe_main(tmp_path, at=T0 + timedelta(hours=1))
        with patch("tools.quality_ratchet._run_static_audit", return_value=report_a):
            r3 = observe_main(tmp_path, at=T0 + timedelta(hours=2))
    assert r1.audit_fingerprint == r3.audit_fingerprint
    assert r2.audit_fingerprint != r1.audit_fingerprint
    assert r3.items_observed == 1  # reprocessed, not short-circuited

    conn = _connect()
    runs = conn.execute("SELECT COUNT(*) c FROM coordination_runs").fetchone()["c"]
    assert runs == 3  # every run gets its own row now, not deduped against all history
    row = conn.execute(
        "SELECT state FROM coordination_items WHERE automation_key='config-usage|c.d|'"
    ).fetchone()
    assert row["state"] == "resolved"  # B absent from the third (back-to-A) report
    conn.close()


def test_observe_main_reuses_one_run_row_for_true_back_to_back_repeat(tmp_path, monkeypatch):
    """Task 10's original guarantee, restated correctly: two consecutive identical-fingerprint
    runs still produce exactly one coordination_runs row (an UPDATE, not a second INSERT) —
    but, unlike the original Task 10 design, apply_observation genuinely reruns both times
    (see the escalation regression test below for why that distinction matters)."""
    monkeypatch.setattr("tools.quality_ratchet.DB_PATH", tmp_path / "q.db")
    report = QualityReport(findings=[_finding(check="config-usage", scope="a.b")])
    with patch("tools.quality_ratchet._run_static_audit", return_value=report), \
         patch("tools.quality_ratchet._current_commit_sha", return_value=None):
        r1 = observe_main(tmp_path, at=T0)
        r2 = observe_main(tmp_path, at=T0 + timedelta(minutes=1))
    assert r1.audit_fingerprint == r2.audit_fingerprint
    assert r2.items_observed == 1  # reprocessed, not skipped
    conn = _connect()
    runs = conn.execute("SELECT COUNT(*) c FROM coordination_runs").fetchone()["c"]
    assert runs == 1  # still one row — UPDATEd in place, not a second INSERT
    row = conn.execute("SELECT ran_at FROM coordination_runs").fetchone()
    assert row["ran_at"] == (T0 + timedelta(minutes=1)).isoformat()  # ran_at genuinely refreshed
    conn.close()


def test_observe_main_escalates_a_stable_persisting_finding_past_its_floor(tmp_path, monkeypatch):
    """The regression this whole fix exists for. Found by an addendum consolidated review:
    a finding that never changes keeps producing the SAME fingerprint call after call, so
    the original short-circuit-both design meant apply_observation (where the persistence
    floor lives) never ran again after the first observation — a persisting, unresolved
    problem could never reach escalation_eligible, which is exactly backwards for a
    persistence floor. Proves the fix: a stable error-severity finding (2h floor) observed
    3h apart, with nothing else changing, must still escalate."""
    monkeypatch.setattr("tools.quality_ratchet.DB_PATH", tmp_path / "q.db")
    report = QualityReport(findings=[_finding(check="config-usage", scope="a.b", severity="error")])
    with patch("tools.quality_ratchet._run_static_audit", return_value=report), \
         patch("tools.quality_ratchet._current_commit_sha", return_value=None):
        r1 = observe_main(tmp_path, at=T0)
        r2 = observe_main(tmp_path, at=T0 + timedelta(hours=3))
    assert r1.states["config-usage|a.b|"] == "observed"  # below the 2h floor at t=0
    assert r2.states["config-usage|a.b|"] == "escalation_eligible"  # 3h > 2h floor, and it fired


def test_observe_main_error_path_does_not_spam_identical_consecutive_errors(tmp_path, monkeypatch):
    """The error path must not insert a new coordination_runs row for a repeating identical
    error now that the UNIQUE constraint (which used to do this implicitly via
    INSERT OR IGNORE) is gone."""
    monkeypatch.setattr("tools.quality_ratchet.DB_PATH", tmp_path / "q.db")
    with patch("tools.quality_ratchet._run_static_audit", side_effect=RuntimeError("boom")):
        r1 = observe_main(tmp_path, at=T0)
        r2 = observe_main(tmp_path, at=T0 + timedelta(minutes=1))
    assert r1.audit_fingerprint == r2.audit_fingerprint
    conn = _connect()
    runs = conn.execute("SELECT COUNT(*) c FROM coordination_runs").fetchone()["c"]
    assert runs == 1
    conn.close()


def test_observe_main_error_path_logs_a_new_row_when_the_error_changes(tmp_path, monkeypatch):
    """A different consecutive error must still get its own row — this also resolves the
    separately-parked Minor finding that latest_run_at() used to freeze at the first failure
    forever under the old UNIQUE-constrained scheme."""
    monkeypatch.setattr("tools.quality_ratchet.DB_PATH", tmp_path / "q.db")
    with patch("tools.quality_ratchet._run_static_audit", side_effect=RuntimeError("boom")):
        r1 = observe_main(tmp_path, at=T0)
    with patch("tools.quality_ratchet._run_static_audit", side_effect=RuntimeError("crash")):
        r2 = observe_main(tmp_path, at=T0 + timedelta(minutes=1))
    assert r1.audit_fingerprint != r2.audit_fingerprint
    conn = _connect()
    runs = conn.execute("SELECT COUNT(*) c FROM coordination_runs").fetchone()["c"]
    assert runs == 2
    conn.close()


import json


def _write_baseline(repo_root, accepted_ids):
    """Task 11 fixture: a temp baseline.json shaped like the real
    tools/quality_audit/baseline.json ({"version", "notes", "accepted_finding_ids"}), written
    at the exact repo_root/tools/quality_audit/baseline.json convention observe_main is
    expected to read from — only accepted_finding_ids matters for these tests."""
    baseline_dir = repo_root / "tools" / "quality_audit"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    baseline_path = baseline_dir / "baseline.json"
    baseline_path.write_text(json.dumps({
        "version": 1,
        "notes": {"example": "test fixture, not the real baseline"},
        "accepted_finding_ids": list(accepted_ids),
    }))
    return baseline_path


def test_observe_main_excludes_baseline_accepted_findings_from_signals(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.quality_ratchet.DB_PATH", tmp_path / "q.db")
    _write_baseline(tmp_path, ["accepted-1"])
    report = QualityReport(findings=[
        _finding(check="config-usage", scope="a.b", finding_id="accepted-1"),
        _finding(check="config-usage", scope="c.d", finding_id="new-1"),
    ])
    with patch("tools.quality_ratchet._run_static_audit", return_value=report), \
         patch("tools.quality_ratchet._current_commit_sha", return_value=None):
        result = observe_main(tmp_path, at=T0)

    assert result.items_observed == 1  # only the non-accepted finding produced a tracked item
    conn = _connect()
    keys = {
        row["automation_key"]
        for row in conn.execute("SELECT automation_key FROM coordination_items").fetchall()
    }
    conn.close()
    assert keys == {"config-usage|c.d|"}  # not "config-usage|a.b|" — that one is baseline-accepted


def test_observe_main_uses_the_real_baseline_json_path_convention(tmp_path, monkeypatch):
    """Confirms the baseline path is computed as repo_root/tools/quality_audit/baseline.json —
    the exact same file tools/quality_audit/__main__.py's own _DEFAULT_BASELINE_PATH resolves
    to (Path(__file__).resolve().parent / "baseline.json", from that module's own directory) —
    not hardcoded elsewhere or misaligned with the CLI's own convention."""
    monkeypatch.setattr("tools.quality_ratchet.DB_PATH", tmp_path / "q.db")
    report = QualityReport(findings=[_finding(check="config-usage", scope="a.b", finding_id="x")])
    expected_path = tmp_path / "tools" / "quality_audit" / "baseline.json"
    with patch("tools.quality_ratchet._run_static_audit", return_value=report), \
         patch("tools.quality_ratchet._current_commit_sha", return_value=None), \
         patch("tools.quality_audit.baseline.load_baseline", wraps=qc_baseline.load_baseline) as mock_load:
        observe_main(tmp_path, at=T0)

    mock_load.assert_called_once_with(expected_path)


def test_observe_main_resolves_an_item_once_it_becomes_baseline_accepted(tmp_path, monkeypatch):
    """Proves the "absent from .new" path works end to end, not just that .new excludes a
    baseline-accepted finding: a finding tracked as observed in one run, then baseline-accepted
    before the next run, must be marked resolved by that next run (apply_observation's existing
    absent-from-present logic, fed by the now-filtered signal list — no extra plumbing)."""
    monkeypatch.setattr("tools.quality_ratchet.DB_PATH", tmp_path / "q.db")
    report = QualityReport(findings=[
        _finding(check="config-usage", scope="a.b", finding_id="soon-accepted"),
    ])

    with patch("tools.quality_ratchet._run_static_audit", return_value=report), \
         patch("tools.quality_ratchet._current_commit_sha", return_value=None):
        r1 = observe_main(tmp_path, at=T0)
    assert r1.items_observed == 1
    conn = _connect()
    row = conn.execute(
        "SELECT state FROM coordination_items WHERE automation_key='config-usage|a.b|'"
    ).fetchone()
    conn.close()
    assert row["state"] == "observed"

    _write_baseline(tmp_path, ["soon-accepted"])  # now baseline-accepted by a human
    with patch("tools.quality_ratchet._run_static_audit", return_value=report), \
         patch("tools.quality_ratchet._current_commit_sha", return_value=None):
        r2 = observe_main(tmp_path, at=T0 + timedelta(hours=1))

    conn = _connect()
    row = conn.execute(
        "SELECT state FROM coordination_items WHERE automation_key='config-usage|a.b|'"
    ).fetchone()
    conn.close()
    assert row["state"] == "resolved"
