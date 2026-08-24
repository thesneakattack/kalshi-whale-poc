"""Tests for the Quality Control Plane's static audit framework: baseline-
ratchet comparison, the CLI's exit-code gate, the source-file walking
helpers, and the router-registration/background-wiring scanners
(docs/superpowers/plans/2026-08-24-quality-control-plane.md, Tasks 3-4).
Task 5 adds persistence, resource-lifecycle, config-usage, and API-usage
scanners on top of these. Baseline-gate and CLI-plumbing tests use synthetic
QualityFindings and temporary fixture trees rather than depending on real
repo content triggering (or not triggering) a finding; the router/
background scanner tests use small synthetic fixture repos for the same
reason (real repo content shouldn't need to change to keep a unit test
green).
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

from services.quality.models import QualityFinding, QualityReport
from tools.quality_audit import __main__ as audit_cli
from tools.quality_audit import background, routers, source
from tools.quality_audit.baseline import compare_to_baseline, load_baseline

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


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


# --- routers.py: router-registration scanner --------------------------------


def test_mounted_router_produces_no_finding(tmp_path):
    _write(tmp_path / "services" / "foo" / "routes.py", "from fastapi import APIRouter\n\nrouter = APIRouter()\n")
    _write(
        tmp_path / "main.py",
        "from services.foo import routes as foo_routes\n\napp = None\napp.include_router(foo_routes.router)\n",
    )

    assert routers.scan_router_registration(tmp_path) == []


def test_unmounted_router_produces_high_confidence_error(tmp_path):
    _write(tmp_path / "services" / "foo" / "routes.py", "from fastapi import APIRouter\n\nrouter = APIRouter()\n")
    _write(tmp_path / "main.py", "app = None\n")

    findings = routers.scan_router_registration(tmp_path)

    assert len(findings) == 1
    assert findings[0].finding_id == "router-unmounted:services.foo.routes"
    assert findings[0].severity == "error"
    assert findings[0].confidence == "high"


def test_standalone_router_marker_suppresses_finding(tmp_path):
    _write(
        tmp_path / "services" / "foo" / "routes.py",
        "# quality-audit: standalone-router\nfrom fastapi import APIRouter\n\nrouter = APIRouter()\n",
    )
    _write(tmp_path / "main.py", "app = None\n")

    assert routers.scan_router_registration(tmp_path) == []


def test_router_scan_with_no_main_py_treats_every_router_as_unmounted(tmp_path):
    _write(tmp_path / "services" / "foo" / "routes.py", "from fastapi import APIRouter\n\nrouter = APIRouter()\n")

    findings = routers.scan_router_registration(tmp_path)

    assert len(findings) == 1
    assert findings[0].finding_id == "router-unmounted:services.foo.routes"


# --- background.py: background-wiring scanner -------------------------------

_SCHEDULER_SOURCE = (
    "import task_supervisor\n\n\n"
    "def _maybe_do_work(cfg):\n"
    "    state = {}\n"
    '    state["task"] = task_supervisor.supervise(lambda: None)\n'
)


def test_scheduler_function_called_from_main_produces_no_finding(tmp_path):
    _write(tmp_path / "services" / "foo" / "scheduler.py", _SCHEDULER_SOURCE)
    _write(tmp_path / "main.py", "from services.foo.scheduler import _maybe_do_work\n\n_maybe_do_work({})\n")

    assert background.scan_background_wiring(tmp_path) == []


def test_scheduler_function_with_zero_external_references_fails_high_confidence(tmp_path):
    _write(tmp_path / "services" / "foo" / "scheduler.py", _SCHEDULER_SOURCE)
    _write(tmp_path / "main.py", "x = 1\n")

    findings = background.scan_background_wiring(tmp_path)

    assert len(findings) == 1
    assert findings[0].finding_id == "background-unwired:services.foo.scheduler:_maybe_do_work"
    assert findings[0].severity == "error"
    assert findings[0].confidence == "high"


def test_scheduler_function_called_from_same_file_it_is_defined_in_produces_no_finding(tmp_path):
    """Real-repo shape: main.py's own _maybe_check_signal_resolutions is
    defined and called both inside main.py itself - same-file wiring must
    still count, or the scanner would flag legitimate entrypoint-local
    schedulers."""
    _write(
        tmp_path / "main.py",
        _SCHEDULER_SOURCE + "\n\n_maybe_do_work({})\n",
    )

    assert background.scan_background_wiring(tmp_path) == []


def test_non_scheduler_maybe_function_is_ignored(tmp_path):
    _write(tmp_path / "services" / "foo" / "scheduler.py", "def _maybe_prune(cfg):\n    return None\n")
    _write(tmp_path / "main.py", "x = 1\n")

    assert background.scan_background_wiring(tmp_path) == []


# --- __main__.py: CLI plumbing ----------------------------------------------


def test_run_audit_against_empty_repo_returns_empty_report(tmp_path):
    """An empty fixture dir has no routes.py/main.py/scheduler functions for
    the registered scanners to find anything in - distinct from Task 3's
    now-obsolete "no scanners registered" case."""
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


def test_real_repo_audit_has_no_new_high_confidence_errors():
    """Task 4 Step 6: run the real (now non-empty) scanner set against this
    repo and confirm no new high-confidence router/background-wiring
    findings slipped in unbaselined - both scanners were manually verified
    clean against current HEAD before this task was committed (all 12
    routers are mounted in main.py, all 5 real _maybe_* schedulers have a
    live external caller). Task 5 Step 8 extends this same shape to the
    persistence/resource/config/API scanners it adds."""
    exit_code = audit_cli.main(
        [
            "--repo-root", str(REPO_ROOT),
            "--baseline", str(REPO_ROOT / "tools" / "quality_audit" / "baseline.json"),
        ]
    )
    assert exit_code == 0
