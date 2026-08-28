"""The workflow guard (.claude/hooks/guard_workflow.py) is what turns the audit's
prose rules into harness decisions. Each rule is exercised as a pure function with
injected state, live-session map, git output, and file existence."""
import importlib.util
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


# ---------------------------------------------------------------- R1 / R8
def test_r1_full_suite_pytest_is_denied_but_scoped_runs_pass(tmp_path):
    g = _load()
    st = tmp_path / "st"; st.mkdir()
    assert g.pre_bash("ddev exec -s fastapi python3 -m pytest -q", str(tmp_path), st, {}, set())["decision"] == "deny"
    assert g.pre_bash("python3 -m pytest", str(tmp_path), st, {}, set()) is not None
    assert g.pre_bash("python3 -m pytest tests/test_risk_manager.py -q", str(tmp_path), st, {}, set()) is None
    assert g.pre_bash("pytest -k kill_switch", str(tmp_path), st, {}, set()) is None
    assert g.pre_bash("pytest --lf", str(tmp_path), st, {}, set()) is None


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
    assert g.pre_bash(cmd, str(tmp_path), st, {}, set()) is None  # second attempt passes


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
    # the occupant itself is not blocked
    assert g.pre_bash("git checkout main", str(primary), st, sessions, self_pids={111}) is None
    # -C targeting the occupied checkout from elsewhere is caught too
    out = g.pre_bash(f"git -C {primary} stash", str(tmp_path / "elsewhere"), st, sessions, self_pids={222})
    assert out["decision"] == "deny"
    # a different checkout is fine
    assert g.pre_bash("git checkout -b x", str(tmp_path / "elsewhere"), st, sessions, self_pids={222}) is None


# ---------------------------------------------------------------- R7
def test_r7_ddev_exec_from_a_worktree_gets_the_docker_form(tmp_path):
    g = _load()
    st = tmp_path / "st"; st.mkdir()
    primary = _repo(tmp_path / "autotrade")
    wt = primary / ".claude" / "worktrees" / "feat-x"
    (wt / ".git").parent.mkdir(parents=True)
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
    out = g.pre_edit("Edit", "services/whale_stream/whale_stream_handlers.py", "", str(repo), st, lambda a: "", lambda r: True)
    assert out["decision"] == "deny" and "docs/kalshi/CHEATSHEET.md" in out["reason"]
    g.post("Read", {"file_path": str(repo / "docs/kalshi/get-market.md")}, str(repo), st)
    assert g.pre_edit("Edit", "services/whale_stream/whale_stream_handlers.py", "", str(repo), st, lambda a: "", lambda r: True) is None


def test_r4_hot_file_edit_denied_once_until_gitnexus_ran(tmp_path):
    g = _load()
    st = tmp_path / "st"; st.mkdir()
    repo = _repo(tmp_path)
    out = g.pre_edit("Edit", "services/risk_manager.py", "", str(repo), st, lambda a: "", lambda r: True)
    assert out["decision"] == "deny" and "gitnexus" in out["reason"]
    assert g.pre_edit("Edit", "services/risk_manager.py", "", str(repo), st, lambda a: "", lambda r: True) is None
    st2 = tmp_path / "st2"; st2.mkdir()
    g.post("mcp__gitnexus__impact", {}, str(repo), st2)
    assert g.pre_edit("Edit", "services/strategy_engine.py", "", str(repo), st2, lambda a: "", lambda r: True) is None


def test_hot_file_edit_nudges_dimensional_analysis_once(tmp_path):
    g = _load()
    st = tmp_path / "st"; st.mkdir()
    repo = _repo(tmp_path)
    first = g.post("Edit", {"file_path": str(repo / "services/paper_broker.py")}, str(repo), st)
    assert first and "dimensional-analysis" in first["context"]
    assert g.post("Edit", {"file_path": str(repo / "services/paper_broker.py")}, str(repo), st) is None


# ---------------------------------------------------------------- R5
def test_r5_new_plan_denied_while_branch_plan_has_unchecked_tasks(tmp_path):
    g = _load()
    st = tmp_path / "st"; st.mkdir()
    repo = _repo(tmp_path)
    plans = repo / "docs" / "superpowers" / "plans"; plans.mkdir(parents=True)
    (plans / "2026-08-20-old.md").write_text("# Old\n- [x] T1\n- [ ] T2\n")
    git = lambda args: "docs/superpowers/plans/2026-08-20-old.md\nservices/x.py\n"
    exists = lambda rel: (repo / rel).exists()
    out = g.pre_edit("Write", "docs/superpowers/plans/2026-08-28-new.md", "# New\n- [ ] T1\n", str(repo), st, git, exists)
    assert out["decision"] == "deny" and "2026-08-20-old.md" in out["reason"]
    # naming the old plan in the new one hands off explicitly
    assert g.pre_edit("Write", "docs/superpowers/plans/2026-08-28-new.md",
                      "# New\nSupersedes: 2026-08-20-old.md\n- [ ] T1\n", str(repo), st, git, exists) is None
    # a finished plan on the branch does not block
    (plans / "2026-08-20-old.md").write_text("# Old\n- [x] T1\n- [x] T2\n")
    assert g.pre_edit("Write", "docs/superpowers/plans/2026-08-28-new.md", "# New\n", str(repo), st, git, exists) is None


def test_plan_over_budget_is_reported_loudly(tmp_path):
    g = _load()
    st = tmp_path / "st"; st.mkdir()
    repo = _repo(tmp_path)
    plans = repo / "docs" / "superpowers" / "plans"; plans.mkdir(parents=True)
    big = plans / "2026-08-28-big.md"
    big.write_text("\n".join(f"- [ ] line {i}" for i in range(301)) + "\n")
    out = g.post("Write", {"file_path": str(big)}, str(repo), st)
    assert out["exit"] == 2 and "301 lines" in out["stderr"]
