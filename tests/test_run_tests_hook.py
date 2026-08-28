"""The per-edit PostToolUse hook (.claude/hooks/run_tests.py) must run only the
edited module's own test files, inside the *current* worktree, and fail loudly
on timeout instead of silently returning. Loaded by path - it is a hook script,
not a package."""
import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "run_tests.py"


def _load():
    spec = importlib.util.spec_from_file_location("run_tests_hook", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _stdin(monkeypatch, path):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"tool_input": {"file_path": path}})))


def test_tests_for_maps_module_to_its_test_files(tmp_path):
    for n in ("test_risk_manager.py", "test_risk_manager_kill_switch.py", "test_other.py"):
        (tmp_path / n).write_text("")
    got = sorted(p.name for p in _load().tests_for("services/risk_manager.py", tmp_path))
    assert got == ["test_risk_manager.py", "test_risk_manager_kill_switch.py"]


def test_tests_for_package_module_includes_package_tests(tmp_path):
    for n in ("test_exit_engine.py", "test_exits.py"):
        (tmp_path / n).write_text("")
    got = sorted(p.name for p in _load().tests_for("/abs/repo/services/exits/exit_engine.py", tmp_path))
    assert got == ["test_exit_engine.py", "test_exits.py"]


def test_container_cwd_is_app_for_primary_and_subpath_for_worktree(tmp_path):
    hook = _load()
    primary = tmp_path / "repo"
    wt = primary / ".claude" / "worktrees" / "x"
    wt.mkdir(parents=True)
    assert hook.container_cwd(primary, primary) == "/app"
    assert hook.container_cwd(wt, primary) == "/app/.claude/worktrees/x"
    assert hook.primary_root(wt) == primary.resolve()
    assert hook.primary_root(primary) == primary.resolve()


def test_no_matching_tests_runs_nothing(tmp_path, capsys, monkeypatch):
    calls = []
    _stdin(monkeypatch, "services/nothing_here.py")
    rc = _load().main(
        run=lambda *a, **k: calls.append(a) or subprocess.CompletedProcess(a, 0, "", ""),
        tests_dir=tmp_path,
    )
    assert rc == 0 and calls == [] and "CI owns it" in capsys.readouterr().out


def test_runs_scoped_files_inside_the_worktree(tmp_path, monkeypatch):
    (tmp_path / "test_risk_manager.py").write_text("")
    _stdin(monkeypatch, "services/risk_manager.py")
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    rc = _load().main(run=fake_run, tests_dir=tmp_path, cwd="/app/.claude/worktrees/x")
    assert rc == 0
    shell = seen["cmd"][-1]
    assert "cd /app/.claude/worktrees/x" in shell and "tests/test_risk_manager.py" in shell


def test_timeout_is_loud_not_silent(tmp_path, capsys, monkeypatch):
    (tmp_path / "test_risk_manager.py").write_text("")
    _stdin(monkeypatch, "services/risk_manager.py")

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(a[0], k.get("timeout"))

    rc = _load().main(run=boom, tests_dir=tmp_path)
    assert rc == 2 and "exceeded" in capsys.readouterr().err
