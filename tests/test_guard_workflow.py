"""The workflow guard (.claude/hooks/guard_workflow.py) turns standing rules into
harness decisions. Each rule is exercised as a pure function with injected state
and live-session map; session discovery runs against a synthetic /proc tree so no
test depends on what is actually running on the machine."""
import importlib.util
import io
import json
import os
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "guard_workflow.py"


def _load():
    spec = importlib.util.spec_from_file_location("guard_workflow", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _repo(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir(parents=True)
    return tmp_path


def _proc(tmp_path: Path, entries: dict) -> Path:
    """A synthetic /proc: {pid: (comm, cwd symlink target or None)}."""
    root = tmp_path / "proc"
    root.mkdir()
    for pid, (comm, cwd) in entries.items():
        d = root / str(pid)
        d.mkdir()
        (d / "comm").write_text(comm + "\n")
        if cwd:
            os.symlink(cwd, d / "cwd")
    (root / "self").mkdir()
    (root / "meminfo").write_text("")
    return root


# ---------------------------------------------------------------- R8
def test_r8_git_add_all_is_denied(tmp_path):
    g = _load()
    st = tmp_path / "st"; st.mkdir()
    for cmd in ("git add -A", "git add .", "git add --all && git commit -m x", "git add . "):
        assert g.pre_bash(cmd, str(tmp_path), st, {}, set())["decision"] == "deny", cmd
    assert g.pre_bash("git add tests/test_x.py", str(tmp_path), st, {}, set()) is None


# ---------------------------------------------------------------- R2
def test_r2_sqlite_on_data_denied_once_then_allowed(tmp_path):
    g = _load()
    st = tmp_path / "st"; st.mkdir()
    cmd = "sqlite3 data/signal_log.db 'select count(*) from signals'"
    first = g.pre_bash(cmd, str(tmp_path), st, {}, set())
    assert first["decision"] == "deny" and "api/quality/summary" in first["reason"]
    assert g.pre_bash(cmd, str(tmp_path), st, {}, set()) is None


def test_r2_skipped_when_diagnostics_were_read_this_session(tmp_path):
    g = _load()
    st = tmp_path / "st"; st.mkdir()
    g.post("Bash", {"command": "curl -s https://kalshi-whale-poc.ddev.site:8443/api/quality/summary"}, str(tmp_path), st)
    assert g.pre_bash("sqlite3 data/paper_broker.db .tables", str(tmp_path), st, {}, set()) is None


# ---------------------------------------------------------------- R6
def test_r6_checkout_in_a_checkout_another_live_session_occupies_is_denied(tmp_path):
    g = _load()
    st = tmp_path / "st"; st.mkdir()
    primary = _repo(tmp_path / "primary")
    sessions = {111: str(primary), 222: str(tmp_path / "elsewhere")}
    out = g.pre_bash("git checkout main", str(primary), st, sessions, self_pids={222})
    assert out["decision"] == "deny" and "pid 111" in out["reason"]
    assert g.pre_bash("git checkout main", str(primary), st, sessions, self_pids={111}) is None
    out = g.pre_bash(f"git -C {primary} stash", str(tmp_path / "elsewhere"), st, sessions, self_pids={222})
    assert out["decision"] == "deny"
    assert g.pre_bash("git checkout -b x", str(tmp_path / "elsewhere"), st, sessions, self_pids={222}) is None


# ---------------------------------------------------------------- R7
def test_r7_ddev_exec_from_a_worktree_gets_the_docker_form(tmp_path):
    g = _load()
    st = tmp_path / "st"; st.mkdir()
    primary = _repo(tmp_path / "autotrade")
    wt = primary / ".claude" / "worktrees" / "feat-x"
    wt.mkdir(parents=True)
    (wt / ".git").write_text("gitdir: ../../../.git/worktrees/feat-x\n")
    out = g.pre_bash("ddev exec -s fastapi python3 -m pytest tests/test_a.py", str(wt), st, {}, set())
    assert out["decision"] == "deny"
    assert 'cd /app/.claude/worktrees/feat-x && python3 -m pytest tests/test_a.py' in out["reason"]
    assert g.pre_bash("ddev exec -s fastapi ls", str(primary), st, {}, set()) is None


# ---------------------------------------------------------------- R3 / R4
def test_r3_kalshi_file_edit_requires_a_docs_kalshi_read(tmp_path):
    g = _load()
    st = tmp_path / "st"; st.mkdir()
    repo = _repo(tmp_path)
    out = g.pre_edit("Edit", "services/whale_stream/whale_stream_handlers.py", str(repo), st)
    assert out["decision"] == "deny" and "docs/kalshi/CHEATSHEET.md" in out["reason"]
    g.post("Read", {"file_path": str(repo / "docs/kalshi/get-market.md")}, str(repo), st)
    assert g.pre_edit("Edit", "services/whale_stream/whale_stream_handlers.py", str(repo), st) is None


def test_r4_hot_file_edit_denied_once_until_gitnexus_ran(tmp_path):
    g = _load()
    st = tmp_path / "st"; st.mkdir()
    repo = _repo(tmp_path)
    out = g.pre_edit("Edit", "services/risk_manager.py", str(repo), st)
    assert out["decision"] == "deny" and "gitnexus@1.6.10" in out["reason"]
    assert g.pre_edit("Edit", "services/risk_manager.py", str(repo), st) is None
    st2 = tmp_path / "st2"; st2.mkdir()
    g.post("mcp__gitnexus__impact", {}, str(repo), st2)
    assert g.pre_edit("Edit", "services/strategy_engine.py", str(repo), st2) is None


def test_hot_file_edit_nudges_dimensional_analysis_once(tmp_path):
    g = _load()
    st = tmp_path / "st"; st.mkdir()
    repo = _repo(tmp_path)
    first = g.post("Edit", {"file_path": str(repo / "services/paper_broker.py")}, str(repo), st)
    assert first and "dimensional-analysis" in first["context"]
    assert g.post("Edit", {"file_path": str(repo / "services/paper_broker.py")}, str(repo), st) is None


def test_effort_is_not_gated_full_suite_and_new_plans_pass(tmp_path):
    """The 2026-08-27 effort caps (no local full suite, no new plan while one is
    unfinished, 300-line plan budget) were withdrawn 2026-08-28 by direct instruction."""
    g = _load()
    st = tmp_path / "st"; st.mkdir()
    repo = _repo(tmp_path)
    assert g.pre_bash("ddev exec -s fastapi python3 -m pytest -q", str(repo), st, {}, set()) is None
    assert g.pre_edit("Write", "docs/superpowers/plans/2026-08-28-new.md", str(repo), st) is None
    big = repo / "docs" / "superpowers" / "plans" / "big.md"
    big.parent.mkdir(parents=True)
    big.write_text("\n".join(f"- [ ] line {i}" for i in range(400)) + "\n")
    assert g.post("Write", {"file_path": str(big)}, str(repo), st) is None


# ------------------------------------------------------- session discovery
def test_proc_sessions_are_claude_processes_with_a_real_cwd(tmp_path):
    g = _load()
    root = _proc(tmp_path, {
        111: ("claude", "/w/a"),
        222: ("python3", "/w/b"),            # not a session
        333: ("claude", None),               # no readable cwd
        444: ("claude", "/w/d (deleted)"),   # sitting in a removed worktree
    })
    assert g.sessions_from_proc(root) == {111: "/w/a"}


def test_proc_sessions_skip_other_users_processes(tmp_path):
    g = _load()
    root = _proc(tmp_path, {111: ("claude", "/w/a")})
    assert g.sessions_from_proc(root, uid=os.getuid() + 1) == {}


def test_live_sessions_merges_socks_and_proc(tmp_path, monkeypatch):
    g = _load()
    root = _proc(tmp_path, {111: ("claude", "/w/proc"), 555: ("node", "/w/sock")})
    kd = tmp_path / "socks"; kd.mkdir()
    (kd / "555.sock").write_text("")
    (kd / "notapid.sock").write_text("")
    monkeypatch.setenv("CLAUDE_PROC_ROOT", str(root))
    monkeypatch.setenv("CLAUDE_SOCK_DIR", str(kd))
    assert g.live_sessions() == {111: "/w/proc", 555: "/w/sock"}


def test_sessions_cli_tags_self_and_other(monkeypatch, capsys):
    g = _load()
    me = os.getpid()
    monkeypatch.setattr(g, "live_sessions", lambda: {me: "/w/me", 999: "/w/them"})
    assert g.main(["--sessions"]) == 0
    lines = sorted([f"{me}\tself\t/w/me", "999\tother\t/w/them"], key=lambda ln: int(ln.split("\t")[0]))
    assert capsys.readouterr().out == "\n".join(lines) + "\n"


def test_the_real_proc_probe_never_reports_this_python_process():
    g = _load()
    assert os.getpid() not in g.sessions_from_proc()


def test_main_denies_via_hook_json(monkeypatch, capsys, tmp_path):
    g = _load()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(g, "live_sessions", lambda: {})
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": "s1",
               "tool_input": {"command": "git add -A"}, "cwd": str(tmp_path)}
    monkeypatch.setattr(g.sys, "stdin", io.StringIO(json.dumps(payload)))
    assert g.main([]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
