"""Tests for the Quality Control Plane's static audit framework: baseline-
ratchet comparison, the CLI's exit-code gate, and the source-file walking
helpers (docs/superpowers/plans/2026-08-24-quality-control-plane.md, Task
3). No real scanners exist yet - Tasks 4/5 add router/background-wiring,
persistence, resource-lifecycle, config-usage, and API-usage scanners to
tools/quality_audit/__main__.py's _SCANNERS registry - so tests here use
synthetic QualityFindings and temporary fixture trees rather than depending
on real repo content triggering (or not triggering) a finding.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

from services.quality.models import QualityFinding, QualityReport
from tools.quality_audit import __main__ as audit_cli
from tools.quality_audit import source
from tools.quality_audit.baseline import compare_to_baseline, load_baseline

REPO_ROOT = Path(__file__).resolve().parent.parent


def _finding(finding_id: str, severity: str = "error", confidence: str = "high") -> QualityFinding:
    return QualityFinding(
        finding_id=finding_id,
        check="fixture-check",
        severity=severity,
        confidence=confidence,
        source="ci",
        scope=finding_id,
        summary=f"synthetic finding {finding_id}",
    )


# --- Step 1: baseline-ratchet gate behavior ---------------------------------


def test_existing_error_in_baseline_does_not_fail_default_gate():
    finding = _finding("existing-error:a")
    report = QualityReport(findings=[finding])
    comparison = compare_to_baseline(report, baseline_ids={finding.finding_id})

    assert comparison.new == []
    assert comparison.existing == [finding]
    assert audit_cli.compute_exit_code(comparison) == 0


def test_new_high_confidence_error_fails_default_gate():
    finding = _finding("new-error:a", severity="error", confidence="high")
    report = QualityReport(findings=[finding])
    comparison = compare_to_baseline(report, baseline_ids=set())

    assert comparison.new == [finding]
    assert audit_cli.compute_exit_code(comparison) == 1


def test_new_warning_is_reported_but_does_not_fail_default_gate():
    finding = _finding("new-warning:a", severity="warning", confidence="high")
    report = QualityReport(findings=[finding])
    comparison = compare_to_baseline(report, baseline_ids=set())

    assert comparison.new == [finding]
    assert audit_cli.compute_exit_code(comparison) == 0
    assert audit_cli.compute_exit_code(comparison, strict=True) == 1


def test_resolved_baseline_id_is_reported_as_resolved():
    report = QualityReport(findings=[])
    comparison = compare_to_baseline(report, baseline_ids={"stale-finding:a"})

    assert comparison.resolved == ["stale-finding:a"]
    assert audit_cli.compute_exit_code(comparison) == 0


# --- baseline.py: file I/O ---------------------------------------------------


def test_load_baseline_missing_file_returns_empty_set(tmp_path):
    assert load_baseline(tmp_path / "does-not-exist.json") == set()


def test_load_baseline_reads_accepted_finding_ids(tmp_path):
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"version": 1, "accepted_finding_ids": ["a", "b"]}))
    assert load_baseline(path) == {"a", "b"}


# --- source.py: file discovery ----------------------------------------------


def test_iter_python_files_excludes_known_noise_dirs(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "real.py").write_text("x = 1\n")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "should_not_exist.py").write_text("x = 1\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.py").write_text("x = 1\n")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "cached.py").write_text("x = 1\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "hook.py").write_text("x = 1\n")
    (tmp_path / ".ddev").mkdir()
    (tmp_path / ".ddev" / "conf.py").write_text("x = 1\n")

    found = source.iter_python_files(tmp_path)

    assert found == [tmp_path / "services" / "real.py"]


def test_iter_python_files_excludes_tests_by_default(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("x = 1\n")

    assert source.iter_python_files(tmp_path) == []
    assert source.iter_python_files(tmp_path, include_tests=True) == [tmp_path / "tests" / "test_x.py"]


def test_parse_python_and_relative_path(tmp_path):
    module_path = tmp_path / "example.py"
    module_path.write_text("VALUE = 1\n")

    tree = source.parse_python(module_path)
    assert isinstance(tree, ast.Module)
    assert len(tree.body) == 1
    assert source.relative_path(tmp_path, module_path) == "example.py"


# --- __main__.py: CLI plumbing ----------------------------------------------


def test_run_audit_with_no_scanners_returns_empty_report(tmp_path):
    report = audit_cli.run_audit(tmp_path)
    assert report.findings == []


def test_main_writes_json_out_and_exits_zero_with_no_findings(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps({"version": 1, "accepted_finding_ids": []}))
    json_out = tmp_path / "out.json"

    exit_code = audit_cli.main(
        [
            "--repo-root", str(tmp_path),
            "--baseline", str(baseline_path),
            "--json-out", str(json_out),
        ]
    )

    assert exit_code == 0
    payload = json.loads(json_out.read_text())
    assert payload == {"findings": [], "new": [], "existing": [], "resolved": []}


def test_main_exits_nonzero_for_synthetic_new_high_confidence_error(tmp_path, monkeypatch):
    finding = _finding("new-error:cli")
    monkeypatch.setattr(audit_cli, "_SCANNERS", [lambda repo_root: [finding]])
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps({"version": 1, "accepted_finding_ids": []}))

    exit_code = audit_cli.main(["--repo-root", str(tmp_path), "--baseline", str(baseline_path)])

    assert exit_code == 1


def test_empty_scanner_framework_runs_clean_against_real_repo():
    """Step 6: verify CLI plumbing against the real repo tree, not just a
    tmp_path fixture. Only meaningful right now because zero scanners are
    registered; Task 5 Step 8 adds the "no new high-confidence errors"
    version of this once real scanners exist."""
    exit_code = audit_cli.main(
        [
            "--repo-root", str(REPO_ROOT),
            "--baseline", str(REPO_ROOT / "tools" / "quality_audit" / "baseline.json"),
        ]
    )
    assert exit_code == 0
