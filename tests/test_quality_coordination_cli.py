import json
import subprocess
from datetime import datetime, timezone

from tools import coordination_engine as ce
from tools.quality_coordination import main, run_detect_cycle

AT = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)


class _StubRunner:
    """Same generic Runner fake used elsewhere in this plan (e.g. Task 3's _StubRunner) -
    queues canned CompletedProcess results, defaults to an empty JSON-array response
    (harmless for the handful of gh/git calls this task's tests don't care about the
    content of) so a test only needs to queue the responses it actually asserts on."""

    def __init__(self):
        self.calls = []
        self._responses = []

    def queue(self, stdout="", returncode=0, stderr=""):
        self._responses.append(subprocess.CompletedProcess([], returncode, stdout, stderr))

    def __call__(self, args):
        self.calls.append(list(args))
        if not self._responses:
            return subprocess.CompletedProcess(args, 0, "[]", "")
        return self._responses.pop(0)


def _refusing_getter(url, timeout):
    """Injected in place of a real HTTP call - raising here is the test-side proof that
    nothing in this task's code path ever falls through to _default_http_get's real
    urllib.request.urlopen (Global Constraint 8)."""
    raise AssertionError(f"no real network call expected in a test, got {url!r}")


def _empty_baseline(tmp_path) -> None:
    baseline_dir = tmp_path / "tools" / "quality_audit"
    baseline_dir.mkdir(parents=True)
    (baseline_dir / "baseline.json").write_text(json.dumps({"accepted_finding_ids": [], "notes": {}}))


def test_run_detect_cycle_returns_a_report_with_all_domains(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "DB_PATH", tmp_path / "q.db")
    (tmp_path / "ROADMAP.md").write_text("## Path to production\n\n- [ ] An open item.\n")
    _empty_baseline(tmp_path)
    git_runner = _StubRunner()
    git_runner.queue("")  # `git branch --format=...` - no branches

    report = run_detect_cycle(tmp_path, at=AT, git_runner=git_runner, http_getter=_refusing_getter)

    assert set(report.keys()) >= {"branch", "ledger", "process_hygiene", "docs_roadmap_feed", "app_report"}
    # _refusing_getter never actually gets called successfully - fetch_app_report catches
    # its AssertionError like any other per-call failure and degrades to None, proving the
    # injection point works without needing a real network stack to observe it.
    assert report["app_report"] == {"quality_summary": None, "health_pipeline": None, "health_faults": None}


def test_main_default_invocation_does_not_call_clean(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "DB_PATH", tmp_path / "q.db")
    (tmp_path / "ROADMAP.md").write_text("## Path to production\n")
    _empty_baseline(tmp_path)
    git_runner = _StubRunner()
    git_runner.queue("")

    exit_code = main(
        ["--repo-root", str(tmp_path)], git_runner=git_runner, http_getter=_refusing_getter,
    )

    assert exit_code == 0
    action_count = ce._connect().execute("SELECT COUNT(*) FROM cleanup_actions").fetchone()[0]
    assert action_count == 0


def test_main_clean_flag_always_reruns_detection_first(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "DB_PATH", tmp_path / "q.db")
    (tmp_path / "ROADMAP.md").write_text("## Path to production\n")
    _empty_baseline(tmp_path)
    git_runner = _StubRunner()
    git_runner.queue("")

    exit_code = main(
        ["--repo-root", str(tmp_path), "--clean"], git_runner=git_runner, http_getter=_refusing_getter,
    )

    assert exit_code == 0
    run_row = ce._connect().execute("SELECT COUNT(*) FROM coordination_runs").fetchone()[0]
    assert run_row == 1  # exactly one fresh detect pass, not a reuse of a prior cached one


def test_main_clean_flag_scopes_git_calls_to_repo_root(tmp_path, monkeypatch):
    """Regression test (found in review): the injected runner must be the SAME cwd-bound
    instance for both the detect pass and any --clean cleanup calls - proving --repo-root
    means the same thing throughout one invocation, not just at the top-level branch list."""
    monkeypatch.setattr(ce, "DB_PATH", tmp_path / "q.db")
    (tmp_path / "ROADMAP.md").write_text("## Path to production\n")
    _empty_baseline(tmp_path)
    git_runner = _StubRunner()
    git_runner.queue("")

    main(["--repo-root", str(tmp_path), "--clean"], git_runner=git_runner, http_getter=_refusing_getter)

    assert len(git_runner.calls) >= 1
    assert git_runner.calls[0][:2] == ["git", "branch"]
