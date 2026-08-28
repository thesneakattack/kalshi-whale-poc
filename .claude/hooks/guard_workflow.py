#!/usr/bin/env python3
"""Workflow guard. Turns the rules the 2026-08-27 audit found unenforced into
harness decisions instead of prose:

PreToolUse (Bash):
  R1 full-suite pytest with no test path       -> deny   (CI owns the full suite)
  R2 ad hoc sqlite3/python on data/*.db before  -> deny once, then allow
     any /api/quality|health read this session
  R6 checkout/stash/reset/rebase/merge/worktree -> deny   (another live session
     in a checkout another session occupies        owns that working tree)
  R8 git add -A / git add .                     -> deny   (stage specific paths)
  R7 `ddev exec` from a linked worktree         -> deny with the working command
PreToolUse (Edit|Write):
  R3 Kalshi-shaped file without a docs/kalshi   -> deny   (HARD RULE, CLAUDE.md)
     read this session
  R4 hot money/strategy file without a GitNexus -> deny once, then allow
     run this session
  R5 a NEW plan doc while a plan touched on this -> deny   (finish or supersede)
     branch still has unchecked tasks
PostToolUse (Bash|Read|Edit|Write|mcp__gitnexus__*):
  records the markers the gates read; nudges dimensional-analysis once on a
  hot-file edit; reports a plan doc over its 300-line budget (exit 2).

State lives per session under $XDG_RUNTIME_DIR/claude-workflow-guard/<session_id>/
(tmpfs, shared across worktrees, gone at logout) - nothing in the repo.
Every rule is a pure function of (payload, state, live sessions, git) so
tests/test_guard_workflow.py can drive it without a harness.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

KALSHI_PATHS = (
    "services/kalshi/", "services/kalshi_client.py", "services/kalshi_account_client.py",
    "services/kalshi_trade_ws.py", "services/kalshi_fees.py", "services/market_catalog/",
    "services/market_watch/", "services/market_events/", "services/whale_stream/",
)
HOT_PATHS = (
    "services/strategy_engine.py", "services/risk_manager.py", "services/paper_broker.py",
    "services/confidence_scoring.py", "services/shadow_mode.py", "services/advisory/",
    "services/whale_calibration/", "services/exits/", "services/position/",
    "services/kalshi_client.py", "services/kalshi_account_client.py",
)
PLAN_DIR = "docs/superpowers/plans/"
PLAN_LINE_BUDGET = 300
DDEV_PROJECT = "kalshi-whale-poc"

_PYTEST = re.compile(r"\bpytest\b")
_PYTEST_SCOPED = re.compile(r"tests/\S*\.py|tests/test_\w+|(^|\s)-k\s|--lf\b|--last-failed\b")
_SQLITE_ON_DATA = re.compile(r"sqlite3.*data/|data/\S*\.db.*sqlite3|sqlite3\.connect\([^)]*data/")
_DIAG_READ = re.compile(r"api/quality/summary|api/health/|api/observability/")
_RISKY_GIT = re.compile(
    r"\bgit\b(?:\s+-C\s+(\S+))?\s+(checkout|switch|stash|reset\s+--hard|rebase|merge|worktree\s+remove)\b"
)
_GIT_ADD_ALL = re.compile(r"\bgit\s+add\s+(-A\b|--all\b|\.\s*$|\.\s)")
_DDEV_EXEC = re.compile(r"^\s*ddev\s+exec\s+(?:-s\s+\S+\s+)?(.*)$", re.S)
_UNCHECKED = re.compile(r"^\s*- \[ \] ", re.M)


# ---------------------------------------------------------------- state
def state_dir(session_id: str) -> Path:
    base = Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / "claude-workflow-guard"
    d = base / (session_id or "no-session")
    d.mkdir(parents=True, exist_ok=True)
    return d


def has(state: Path, marker: str) -> bool:
    return (state / marker).exists()


def mark(state: Path, marker: str) -> None:
    (state / marker).touch()


# ---------------------------------------------------------- live sessions
def live_sessions(uid: int | None = None) -> dict[int, str]:
    """pid -> cwd for every Claude session with a control socket on this machine."""
    d = Path(f"/run/user/{os.getuid() if uid is None else uid}/cc-socks")
    out: dict[int, str] = {}
    for s in d.glob("*.sock") if d.exists() else []:
        try:
            out[int(s.stem)] = os.readlink(f"/proc/{s.stem}/cwd")
        except (OSError, ValueError):
            continue
    return out


def ancestor_pids() -> set[int]:
    pids: set[int] = set()
    pid = os.getpid()
    for _ in range(30):
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
        except OSError:
            break
        pids.add(pid)
        ppid = int(stat.rsplit(")", 1)[1].split()[1])
        if ppid <= 1:
            break
        pid = ppid
    return pids


def other_sessions(sessions: dict[int, str], self_pids: set[int]) -> dict[int, str]:
    return {p: c for p, c in sessions.items() if p not in self_pids}


# ------------------------------------------------------------- helpers
def repo_root(cwd: str) -> Path:
    p = Path(cwd).resolve()
    for cand in (p, *p.parents):
        if (cand / ".git").exists():
            return cand
    return p


def primary_root(root: Path) -> Path:
    parts = root.parts
    for i in range(len(parts) - 2, 0, -1):
        if parts[i] == ".claude" and parts[i + 1] == "worktrees":
            return Path(*parts[:i])
    return root


def rel_path(file_path: str, cwd: str) -> str:
    p = Path(file_path)
    if not p.is_absolute():
        p = Path(cwd) / p
    try:
        return p.resolve().relative_to(repo_root(cwd)).as_posix()
    except ValueError:
        return p.as_posix()


def _deny(reason: str) -> dict:
    return {"decision": "deny", "reason": reason}


def _git_runner_real(cwd: str):
    def run(args: list[str]) -> str:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
        return r.stdout
    return run


# --------------------------------------------------------------- rules
def pre_bash(command: str, cwd: str, state: Path, sessions: dict[int, str], self_pids: set[int]) -> dict | None:
    cmd = command.strip()

    if _GIT_ADD_ALL.search(cmd):
        return _deny("R8: `git add -A` / `git add .` is never allowed here - stage specific paths "
                     "(data/*.db, .env, session scratch, and other sessions' files live in this tree).")

    m = _RISKY_GIT.search(cmd)
    if m:
        target = Path(m.group(1)).resolve() if m.group(1) else Path(cwd).resolve()
        for pid, other_cwd in other_sessions(sessions, self_pids).items():
            if Path(other_cwd).resolve() == target:
                return _deny(f"R6: `git {m.group(2)}` in {target} - another live Claude session (pid {pid}) "
                             "works in that checkout. Never checkout/stash/reset/rebase/merge under "
                             "another session's working tree; do it from your own worktree "
                             "(.claude/worktrees/<name>, EnterWorktree) or ask that session.")

    if _PYTEST.search(cmd) and not _PYTEST_SCOPED.search(cmd):
        return _deny("R1: full-suite pytest is CI's job (.woodpecker/tests-pytest.yml; ~4 min round trip). "
                     "Locally run only the file(s) you touched: `pytest tests/test_<module>.py`, or push.")

    if _SQLITE_ON_DATA.search(cmd) and not has(state, "diag_checked") and not has(state, "sqlite_warned"):
        mark(state, "sqlite_warned")
        return _deny("R2: ad hoc sqlite3 on data/*.db before reading the app's own diagnostics. Start with "
                     "`curl -s https://kalshi-whale-poc.ddev.site:8443/api/quality/summary` (then "
                     "/api/health/pipeline, /api/health/faults). Re-run this command afterwards if it is still needed "
                     "- this gate only fires once per session.")

    dm = _DDEV_EXEC.match(cmd)
    if dm:
        root = repo_root(cwd)
        primary = primary_root(root)
        if primary != root:
            rel = root.relative_to(primary).as_posix()
            rest = dm.group(1).strip()
            return _deny(f"R7: `ddev exec` refuses to run from a linked worktree. From this worktree use:\n"
                         f"  docker exec ddev-{DDEV_PROJECT}-fastapi sh -c \"cd /app/{rel} && {rest}\"")
    return None


def pre_edit(tool: str, file_path: str, content: str, cwd: str, state: Path, git_run, exists) -> dict | None:
    rel = rel_path(file_path, cwd)

    if rel.startswith(KALSHI_PATHS) and not has(state, "kalshi_docs_read"):
        return _deny(f"R3: {rel} carries Kalshi-sourced data and no docs/kalshi/ page has been read this session "
                     "(HARD RULE, CLAUDE.md). Read docs/kalshi/CHEATSHEET.md (titles are printed at session start) "
                     "and the exact mirrored page for the field/endpoint you are touching, then retry.")

    if rel.startswith(HOT_PATHS) and not has(state, "gitnexus_ran") and not has(state, "hot_edit_warned"):
        mark(state, "hot_edit_warned")
        return _deny(f"R4: {rel} is on the money/strategy hot path. Run a blast-radius check first - "
                     "`npx gitnexus@latest impact <symbol>` or the gitnexus MCP impact tool - then retry. "
                     "This gate fires once per session; the check is what the tool exists for.")

    if tool == "Write" and rel.startswith(PLAN_DIR) and not exists(rel):
        names = git_run(["log", "origin/main..HEAD", "--name-only", "--format="]).split()
        unfinished = []
        for name in sorted(set(names)):
            if name.startswith(PLAN_DIR) and name != rel and exists(name):
                text = Path(repo_root(cwd), name).read_text()
                if _UNCHECKED.search(text) and Path(name).name not in content:
                    unfinished.append(name)
        if unfinished:
            return _deny("R5: a new plan while this branch already carries a plan with unchecked tasks: "
                         + ", ".join(unfinished)
                         + ". Finish it, tick its tasks, or name it in the new plan's header "
                         "(`Supersedes: <file>`) so the hand-off is explicit. Plans that never run to completion "
                         "are the failure this gate exists for.")
    return None


def post(tool: str, tool_input: dict, cwd: str, state: Path) -> dict | None:
    """Returns {"context": str} | {"stderr": str, "exit": 2} | None."""
    if tool == "Bash":
        cmd = tool_input.get("command") or ""
        if _DIAG_READ.search(cmd):
            mark(state, "diag_checked")
        if "docs/kalshi/" in cmd:
            mark(state, "kalshi_docs_read")
        if "gitnexus" in cmd:
            mark(state, "gitnexus_ran")
        return None
    if tool.startswith("mcp__gitnexus__"):
        mark(state, "gitnexus_ran")
        return None
    if tool == "Read":
        if "docs/kalshi/" in (tool_input.get("file_path") or ""):
            mark(state, "kalshi_docs_read")
        return None
    if tool in ("Edit", "Write"):
        rel = rel_path(tool_input.get("file_path") or "", cwd)
        if rel.startswith(PLAN_DIR):
            p = Path(cwd) / rel if not Path(tool_input.get("file_path") or "").is_absolute() else Path(tool_input["file_path"])
            try:
                n = len(p.read_text().splitlines())
            except OSError:
                n = 0
            if n > PLAN_LINE_BUDGET:
                return {"stderr": f"{rel} is {n} lines, over the {PLAN_LINE_BUDGET}-line plan budget (stop rule): "
                                  "cut it or split it before continuing.", "exit": 2}
        if rel.startswith(HOT_PATHS) and not has(state, "dim_nudged"):
            mark(state, "dim_nudged")
            return {"context": f"{rel} is money/probability math: run the dimensional-analysis skill on the change "
                               "before calling it done (two shipped bugs of that class here)."}
    return None


# ---------------------------------------------------------------- main
def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    event = payload.get("hook_event_name") or ""
    tool = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    cwd = payload.get("cwd") or os.getcwd()
    state = state_dir(payload.get("session_id") or "")

    if event == "PreToolUse":
        if tool == "Bash":
            out = pre_bash(tool_input.get("command") or "", cwd, state, live_sessions(), ancestor_pids())
        elif tool in ("Edit", "Write"):
            root = repo_root(cwd)
            out = pre_edit(tool, tool_input.get("file_path") or "", tool_input.get("content") or "",
                           cwd, state, _git_runner_real(str(root)), lambda rel: (root / rel).exists())
        else:
            out = None
        if out:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": out["decision"],
                "permissionDecisionReason": out["reason"],
            }}))
        return 0

    if event == "PostToolUse":
        out = post(tool, tool_input, cwd, state)
        if out and "stderr" in out:
            sys.stderr.write(out["stderr"] + "\n")
            return out["exit"]
        if out and "context" in out:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                                     "additionalContext": out["context"]}}))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
