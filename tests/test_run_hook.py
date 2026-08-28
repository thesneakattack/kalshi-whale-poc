"""The hook launcher (.claude/hooks/run_hook.py) must run the hook that belongs to
the checkout named by the payload's cwd - not the primary checkout's copy - and
skip silently when that checkout has no such hook. resolve_root() is
monkeypatched in the unit tests: the fastapi container has no git, and the
launcher itself only ever runs on the host. The settings.json prelude that
locates run_hook.py in the first place is exercised end to end where git and
bash exist (host, CI)."""
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / ".claude" / "hooks" / "run_hook.py"


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


# ------------------------------------------- the settings.json prelude itself
needs_git = pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None,
                               reason="needs git and bash")


def _preludes() -> list[tuple[str, str]]:
    """(hook name, command) for every settings.json entry - all of them get exercised."""
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text())
    out = []
    for groups in settings["hooks"].values():
        for group in groups:
            for h in group["hooks"]:
                out.append((h["command"].rsplit('"$h" ', 1)[1].split(";")[0], h["command"]))
    return out


def _prelude() -> str:
    return _preludes()[0][1]


def _checkout(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / name
    (repo / ".claude" / "hooks").mkdir(parents=True)
    (repo / "sub").mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    return repo


@needs_git
@pytest.mark.parametrize("hook_name,command", _preludes(), ids=[f"{i}:{n}" for i, (n, _) in enumerate(_preludes())])
def test_every_prelude_runs_the_payload_checkouts_launcher_not_the_project_dirs(tmp_path, hook_name, command):
    """F1 (2026-08-28): every hook was gated on $CLAUDE_PROJECT_DIR/.claude/hooks/run_hook.py,
    which is always the primary checkout - a primary parked on a branch without that file
    silently disabled every hook in every worktree session. Each entry is exercised, and
    the launcher must receive the root the prelude resolved (CLAUDE_HOOK_ROOT)."""
    repo = _checkout(tmp_path, "wt")
    (repo / ".claude" / "hooks" / "run_hook.py").write_text(
        "import os, sys; d = sys.stdin.read(); print('STUB', sys.argv[1], len(d), os.environ.get('CLAUDE_HOOK_ROOT')); sys.exit(3)\n")
    payload = json.dumps({"cwd": str(repo / "sub"), "hook_event_name": "PreToolUse"})
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path / "nonexistent-primary")}
    r = subprocess.run(["bash", "-c", command], input=payload, text=True, capture_output=True, env=env)
    assert r.returncode == 3, r.stderr
    assert r.stdout.split() == ["STUB", hook_name, str(len(payload)), str(repo.resolve())]


def test_launcher_trusts_the_root_the_prelude_already_resolved(tmp_path, monkeypatch):
    repo = tmp_path / "wt"
    (repo / ".claude" / "hooks").mkdir(parents=True)
    (repo / ".claude" / "hooks" / "x.py").write_text("")
    spec = importlib.util.spec_from_file_location("run_hook_env", LAUNCHER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def must_not_run(cwd, fallback):
        raise AssertionError("resolve_root re-ran git although CLAUDE_HOOK_ROOT was set")

    mod.resolve_root = must_not_run
    monkeypatch.setenv("CLAUDE_HOOK_ROOT", str(repo))
    seen = {}
    rc = mod.main(["x.py"], stdin_text=json.dumps({"cwd": "/elsewhere"}),
                  run=lambda cmd, **k: seen.update(cmd=cmd, env=k["env"]) or subprocess.CompletedProcess(cmd, 0))
    assert rc == 0 and seen["cmd"][1] == str(repo / ".claude" / "hooks" / "x.py")
    assert seen["env"]["CLAUDE_PROJECT_DIR"] == str(repo)


@needs_git
def test_prelude_is_silent_when_the_checkout_has_no_launcher(tmp_path):
    repo = _checkout(tmp_path, "wt")
    payload = json.dumps({"cwd": str(repo / "sub")})
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path / "nonexistent-primary")}
    r = subprocess.run(["bash", "-c", _prelude()], input=payload, text=True, capture_output=True, env=env)
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""


@needs_git
def test_prelude_falls_back_to_project_dir_when_cwd_is_not_a_repo(tmp_path):
    primary = _checkout(tmp_path, "primary")
    (primary / ".claude" / "hooks" / "run_hook.py").write_text("print('PRIMARY')\n")
    payload = json.dumps({"cwd": str(tmp_path / "not-a-repo")})
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(primary)}
    r = subprocess.run(["bash", "-c", _prelude()], input=payload, text=True, capture_output=True, env=env)
    assert r.returncode == 0 and r.stdout.strip() == "PRIMARY"
