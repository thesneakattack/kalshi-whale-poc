"""The per-edit PostToolUse hook (.claude/hooks/run_tests.py) must run only the
edited module's own test files, inside the *current* worktree, cover tooling
code (.claude/hooks/, tools/, scripts/) as well as application code, and fail
loudly on timeout instead of silently returning. Loaded by path - it is a hook
script, not a package."""
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


def _names(paths):
    return sorted(p.name for p in paths)


def test_scope_covers_app_code_and_tooling_but_not_docs_or_frontend():
    m = _load()
    for p in ("services/risk_manager.py", "/abs/repo/services/exits/exit_engine.py", "main.py", "/abs/main.py",
              ".claude/hooks/guard_workflow.py", "/abs/repo/.claude/hooks/run_tests.py",
              "tools/kanban_sync/sync.py", "tools/quality_coordination.py",
              "scripts/cleanup-worktrees.sh", "/abs/repo/scripts/woodpecker-status",
              "tests/test_guard_workflow.py"):
        assert m._in_scope(p), p
    for p in ("docs/x.md", "static/index.html", "frontend/src/js/app.js", "config/settings.yaml",
              ".claude/skills/run/SKILL.md", ".claude/settings.json", "tests/support/synthetic_git_repo.py",
              "scripts/README.md"):
        assert not m._in_scope(p), p


def test_tests_for_maps_module_to_its_test_files(tmp_path):
    for n in ("test_risk_manager.py", "test_risk_manager_kill_switch.py", "test_other.py"):
        (tmp_path / n).write_text("")
    assert _names(_load().tests_for("services/risk_manager.py", tmp_path)) == \
        ["test_risk_manager.py", "test_risk_manager_kill_switch.py"]


def test_tests_for_package_module_includes_package_tests(tmp_path):
    for n in ("test_exit_engine.py", "test_exits.py"):
        (tmp_path / n).write_text("")
    assert _names(_load().tests_for("/abs/repo/services/exits/exit_engine.py", tmp_path)) == \
        ["test_exit_engine.py", "test_exits.py"]


def test_tests_for_maps_hooks_tool_packages_scripts_and_test_files(tmp_path):
    for n in ("test_guard_workflow.py", "test_run_tests_hook.py", "test_kanban_sync_sync.py",
              "test_kanban_sync_main.py", "test_cleanup_worktrees.py", "test_other.py"):
        (tmp_path / n).write_text("")
    m = _load()
    assert _names(m.tests_for(".claude/hooks/guard_workflow.py", tmp_path)) == ["test_guard_workflow.py"]
    assert _names(m.tests_for("/abs/.claude/hooks/run_tests.py", tmp_path)) == ["test_run_tests_hook.py"]
    assert _names(m.tests_for("tools/kanban_sync/sync.py", tmp_path)) == \
        ["test_kanban_sync_main.py", "test_kanban_sync_sync.py"]
    assert _names(m.tests_for("scripts/cleanup-worktrees.sh", tmp_path)) == ["test_cleanup_worktrees.py"]
    assert _names(m.tests_for("tests/test_other.py", tmp_path)) == ["test_other.py"]
    assert m.tests_for("tests/test_missing.py", tmp_path) == []


def test_tests_for_applies_stem_overrides_before_the_package_glob(tmp_path):
    # CI pipeline audit Tier 2 #5/#6 (2026-09-03): app_state.py mapped to
    # zero tests (no dedicated test file exists for it); services/kalshi/
    # {websocket,public}.py's bare stems matched the generic "kalshi"
    # package-name glob, pulling in all test_kalshi*.py files regardless
    # of relevance. Both now route through _STEM_OVERRIDES instead.
    for n in ("test_main_scheduler_loops.py", "test_routes_health.py", "test_kalshi_client.py",
              "test_kalshi_trade_ws.py", "test_kalshi_census.py", "test_other.py"):
        (tmp_path / n).write_text("")
    m = _load()
    assert _names(m.tests_for("services/app_state.py", tmp_path)) == \
        ["test_main_scheduler_loops.py", "test_routes_health.py"]
    assert _names(m.tests_for("services/kalshi/public.py", tmp_path)) == ["test_kalshi_client.py"]
    assert _names(m.tests_for("services/kalshi/websocket.py", tmp_path)) == ["test_kalshi_trade_ws.py"]
    # test_kalshi_census.py is deliberately excluded from both overrides
    # (neither file imports from it) - confirms the override narrows scope
    # rather than only adding to it.


def test_container_cwd_is_app_for_primary_and_subpath_for_worktree(tmp_path):
    hook = _load()
    primary = tmp_path / "repo"
    wt = primary / ".claude" / "worktrees" / "x"
    wt.mkdir(parents=True)
    assert hook.container_cwd(primary, primary) == "/app"
    assert hook.container_cwd(wt, primary) == "/app/.claude/worktrees/x"
    assert hook.primary_root(wt) == primary.resolve()
    assert hook.primary_root(primary) == primary.resolve()


def test_no_matching_tests_tells_the_model_not_the_void(tmp_path, capsys, monkeypatch):
    calls = []
    _stdin(monkeypatch, "scripts/new-thing.sh")
    rc = _load().main(
        run=lambda *a, **k: calls.append(a) or subprocess.CompletedProcess(a, 0, "", ""),
        tests_dir=tmp_path,
    )
    assert rc == 0 and calls == []
    out = json.loads(capsys.readouterr().out)
    assert "scripts/new-thing.sh" in out["hookSpecificOutput"]["additionalContext"]


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
    assert '-m "not slow"' in shell


def test_timeout_is_loud_not_silent(tmp_path, capsys, monkeypatch):
    (tmp_path / "test_risk_manager.py").write_text("")
    _stdin(monkeypatch, "services/risk_manager.py")

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(a[0], k.get("timeout"))

    rc = _load().main(run=boom, tests_dir=tmp_path)
    assert rc == 2 and "exceeded" in capsys.readouterr().err
