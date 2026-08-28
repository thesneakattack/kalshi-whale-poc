"""The workflow guard (.claude/hooks/guard_workflow.py) turns standing rules into
harness decisions. Each rule is exercised as a pure function with injected state
and live-session map; session discovery runs against a synthetic /proc tree and a
synthetic registry so no test depends on what is actually running on the machine."""
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


def test_r6_counts_a_session_anywhere_below_the_checkout_root(tmp_path):
    """A Bash-tool cwd persists between calls, so a session that cd'd into
    <checkout>/services still occupies <checkout> - same predicate
    scripts/cleanup-worktrees.sh applies."""
    g = _load()
    st = tmp_path / "st"; st.mkdir()
    primary = _repo(tmp_path / "primary")
    (primary / "services").mkdir()
    sessions = {111: str(primary / "services")}
    assert g.pre_bash("git checkout main", str(primary), st, sessions, self_pids={222})["decision"] == "deny"
    assert g.pre_bash("git checkout main", str(tmp_path), st, sessions, self_pids={222}) is not None  # tmp_path contains primary/services too
    assert g.pre_bash("git checkout main", str(tmp_path / "elsewhere"), st, sessions, self_pids={222}) is None


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


def test_prose_under_kalshi_and_hot_packages_is_not_gated(tmp_path):
    """A README or CHEATSHEET under services/exits/ or services/kalshi/ is not money
    math and carries no Kalshi field semantics in code - no R3, no R4, no nudge."""
    g = _load()
    st = tmp_path / "st"; st.mkdir()
    repo = _repo(tmp_path)
    assert g.pre_edit("Edit", "services/kalshi/CHEATSHEET.md", str(repo), st) is None
    assert g.pre_edit("Edit", "services/exits/README.md", str(repo), st) is None
    assert g.post("Edit", {"file_path": str(repo / "services/exits/README.md")}, str(repo), st) is None
    assert g.pre_edit("Edit", "services/exits/exit_engine.py", str(repo), st)["decision"] == "deny"


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


# ------------------------------------------------------- checkpoint nudge
def test_checkpoint_nudge_measures_at_most_every_ten_minutes_and_fires_past_thresholds(tmp_path):
    g = _load()
    st = tmp_path / "st"; st.mkdir()
    calls = []

    def git_run(args):
        calls.append(args[0])
        if args[0] == "status":
            return "\n".join(f" M f{i}.py" for i in range(6)) + "\n"
        return " 6 files changed, 40 insertions(+), 3 deletions(-)\n"

    first = g.checkpoint_nudge(st, git_run, now=1000.0)
    assert first and "6 changed file(s)" in first and "43 changed line(s)" in first
    assert calls == ["status", "diff"]
    assert g.checkpoint_nudge(st, git_run, now=1000.0 + 599) is None and calls == ["status", "diff"]  # throttled
    assert g.checkpoint_nudge(st, git_run, now=1000.0 + 601) and len(calls) == 4


def test_checkpoint_nudge_is_quiet_below_thresholds(tmp_path):
    g = _load()
    st = tmp_path / "st"; st.mkdir()
    git_run = lambda args: " M a.py\n" if args[0] == "status" else " 1 file changed, 2 insertions(+)\n"
    assert g.checkpoint_nudge(st, git_run, now=5.0) is None


def test_post_appends_the_nudge_to_tool_events_that_change_the_tree(tmp_path):
    g = _load()
    st = tmp_path / "st"; st.mkdir()
    repo = _repo(tmp_path)
    git_run = lambda args: "\n".join(f" M f{i}" for i in range(5)) if args[0] == "status" else ""
    out = g.post("Bash", {"command": "ls"}, str(repo), st, git_run)
    assert out and "checkpoint nudge" in out["context"]
    assert g.post("Read", {"file_path": "x"}, str(repo), st, git_run) is None  # reads never nudge


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


def test_registry_records_the_harness_cwd_and_survives_only_while_the_pid_matches(tmp_path):
    g = _load()
    root = _proc(tmp_path, {111: ("claude", "/w/proc-cwd"), 555: ("node", "/w/cli")})
    base = tmp_path / "reg"
    st = base / "session-A"; st.mkdir(parents=True)
    g.record_session(st, "/w/worktree-from-payload", 111, root)
    st2 = base / "session-B"; st2.mkdir()
    g.record_session(st2, "/w/cli-payload", 555, root)
    st3 = base / "session-C"; st3.mkdir()
    (st3 / "session.json").write_text(json.dumps({"pid": 111, "comm": "python3", "cwd": "/w/reused"}))  # pid reused
    (base / "session-D").mkdir()
    (base / "session-D" / "session.json").write_text("{")
    assert g.sessions_from_registry(base, root) == {111: "/w/worktree-from-payload", 555: "/w/cli-payload"}
    g.record_session(st, "/w/x", None, root)  # no claude ancestor found: nothing written, nothing raised
    assert json.loads((st / "session.json").read_text())["cwd"] == "/w/worktree-from-payload"


def test_claude_pid_is_the_nearest_claude_then_node_ancestor(tmp_path):
    g = _load()
    root = _proc(tmp_path, {10: ("python3", None), 20: ("bash", None), 30: ("claude", None), 40: ("node", None)})
    assert g.claude_pid([10, 20, 30, 40], root) == 30
    assert g.claude_pid([10, 20, 40], root) == 40
    assert g.claude_pid([10, 20], root) is None


def test_live_sessions_merges_socks_proc_and_registry_with_registry_cwd_winning(tmp_path, monkeypatch):
    g = _load()
    root = _proc(tmp_path, {111: ("claude", "/w/proc"), 555: ("node", "/w/sock")})
    kd = tmp_path / "socks"; kd.mkdir()
    (kd / "555.sock").write_text("")
    (kd / "notapid.sock").write_text("")
    base = tmp_path / "reg"; (base / "s").mkdir(parents=True)
    (base / "s" / "session.json").write_text(json.dumps({"pid": 111, "comm": "claude", "cwd": "/w/registry"}))
    monkeypatch.setenv("CLAUDE_PROC_ROOT", str(root))
    monkeypatch.setenv("CLAUDE_SOCK_DIR", str(kd))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(g, "registry_root", lambda: base)
    assert g.live_sessions() == {111: "/w/registry", 555: "/w/sock"}


def test_sessions_cli_prints_the_header_then_self_and_other(monkeypatch, capsys):
    g = _load()
    me = os.getpid()
    monkeypatch.setattr(g, "live_sessions", lambda: {me: "/w/me", 999: "/w/them"})
    assert g.main(["--sessions"]) == 0
    lines = sorted([f"{me}\tself\t/w/me", "999\tother\t/w/them"], key=lambda ln: int(ln.split("\t")[0]))
    assert capsys.readouterr().out == "#sessions v1\n" + "\n".join(lines) + "\n"


def test_the_real_proc_probe_never_reports_this_python_process():
    g = _load()
    assert os.getpid() not in g.sessions_from_proc()


def test_main_denies_via_hook_json_and_records_the_session(monkeypatch, capsys, tmp_path):
    g = _load()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(g, "live_sessions", lambda: {})
    monkeypatch.setattr(g, "claude_pid", lambda: os.getpid())  # stand in for the claude ancestor
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": "s1",
               "tool_input": {"command": "git add -A"}, "cwd": str(tmp_path)}
    monkeypatch.setattr(g.sys, "stdin", io.StringIO(json.dumps(payload)))
    assert g.main([]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    rec = json.loads((tmp_path / "claude-workflow-guard" / "s1" / "session.json").read_text())
    assert rec["pid"] == os.getpid() and rec["cwd"] == str(tmp_path)
