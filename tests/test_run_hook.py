"""The hook launcher (.claude/hooks/run_hook.py) must run the hook that belongs to
the checkout named by the payload's cwd - not the primary checkout's copy - and
skip silently when that checkout has no such hook. resolve_root() is
monkeypatched: the fastapi container has no git, and the launcher itself only
ever runs on the host."""
import importlib.util
import json
import subprocess
from pathlib import Path

LAUNCHER = Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "run_hook.py"


def _load(root: Path):
    spec = importlib.util.spec_from_file_location("run_hook", LAUNCHER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.resolve_root = lambda cwd, fallback: root
    return mod


def test_runs_the_hook_from_the_payload_cwd_checkout(tmp_path, monkeypatch):
    repo = tmp_path / "wt"
    hook = repo / ".claude" / "hooks" / "x.py"
    hook.parent.mkdir(parents=True)
    hook.write_text("print('hi')\n")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "primary"))
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, cwd=kw["cwd"], env=kw["env"], input=kw["input"])
        return subprocess.CompletedProcess(cmd, 0)

    payload = json.dumps({"cwd": str(repo / "sub"), "hook_event_name": "PreToolUse"})
    assert _load(repo).main(["x.py"], stdin_text=payload, run=fake_run) == 0
    assert seen["cmd"][1] == str(hook)
    assert seen["cwd"] == str(repo)
    assert seen["env"]["CLAUDE_PROJECT_DIR"] == str(repo)
    assert seen["input"] == payload


def test_shell_hooks_run_under_bash(tmp_path, monkeypatch):
    repo = tmp_path / "wt"
    (repo / ".claude" / "hooks").mkdir(parents=True)
    (repo / ".claude" / "hooks" / "orient.sh").write_text("echo hi\n")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "primary"))
    seen = {}
    rc = _load(repo).main(["orient.sh"], stdin_text=json.dumps({"cwd": str(repo)}),
                          run=lambda cmd, **k: seen.update(cmd=cmd) or subprocess.CompletedProcess(cmd, 0))
    assert rc == 0 and seen["cmd"][0] == "bash" and seen["cmd"][1].endswith("orient.sh")


def test_missing_hook_in_that_checkout_is_skipped_silently(tmp_path, monkeypatch):
    repo = tmp_path / "wt"
    repo.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "primary"))
    called = []
    rc = _load(repo).main(["nope.py"], stdin_text=json.dumps({"cwd": str(repo)}),
                          run=lambda *a, **k: called.append(a))
    assert rc == 0 and called == []


def test_exit_code_passes_through(tmp_path, monkeypatch):
    repo = tmp_path / "wt"
    (repo / ".claude" / "hooks").mkdir(parents=True)
    (repo / ".claude" / "hooks" / "y.sh").write_text("exit 2\n")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "primary"))
    rc = _load(repo).main(["y.sh"], stdin_text=json.dumps({"cwd": str(repo)}),
                          run=lambda cmd, **k: subprocess.CompletedProcess(cmd, 2))
    assert rc == 2


def test_real_resolve_root_falls_back_when_git_is_unavailable(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("run_hook_real", LAUNCHER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout="", stderr=""))
    assert mod.resolve_root(str(tmp_path), "/fallback") == Path("/fallback")
