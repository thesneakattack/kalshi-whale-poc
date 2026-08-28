#!/usr/bin/env python3
"""Workflow guard: the standing rules a session must not talk itself out of,
enforced as harness decisions instead of prose.

PreToolUse (Bash):
  R8 `git add -A` / `git add .`                        -> deny (stage specific paths)
  R6 checkout/switch/stash/reset --hard/rebase/merge/  -> deny (another live session
     `worktree remove` in a checkout another live          owns that working tree)
     session occupies
  R2 ad hoc sqlite3/python on data/*.db before any     -> deny once, then allow
     /api/quality|health read this session
  R7 `ddev exec` from a linked worktree                -> deny, with the working command
PreToolUse (Edit|Write):
  R3 Kalshi-shaped file, no docs/kalshi read yet       -> deny (HARD RULE, CLAUDE.md)
  R4 money/strategy hot file, no GitNexus run yet      -> deny once, then allow
PostToolUse (Bash|Read|Edit|Write|mcp__gitnexus__*):
  records the markers the gates read; nudges dimensional-analysis once per
  session on a hot-file edit.

`--sessions` prints every live Claude session on this machine, one per line:
`<pid>\t<self|other>\t<cwd>`. orient.sh and scripts/cleanup-worktrees.sh read
that instead of re-implementing discovery. A session is any process named
`claude` owned by this user (the VS Code extension and the CLI both are), read
straight from /proc, plus whatever registered a control socket under
$XDG_RUNTIME_DIR/cc-socks (only some entrypoints do - the 2026-08-28 CLI peer
had none, which is why cc-socks alone was blind to it). A cwd that reads
"(deleted)" is dropped: that session is not really anywhere.

State lives per session under $XDG_RUNTIME_DIR/claude-workflow-guard/<session_id>/
(tmpfs, shared across worktrees). Every rule is a pure function of (payload,
state, live sessions, git) so tests/test_guard_workflow.py drives it without a
harness.
"""
import json
import os
import re
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
DDEV_PROJECT = "kalshi-whale-poc"
GITNEXUS = "npx gitnexus@1.6.10"

_SQLITE_ON_DATA = re.compile(r"sqlite3.*data/|data/\S*\.db.*sqlite3|sqlite3\.connect\([^)]*data/")
_DIAG_READ = re.compile(r"api/quality/summary|api/health/|api/observability/")
_RISKY_GIT = re.compile(
    r"\bgit\b(?:\s+-C\s+(\S+))?\s+(checkout|switch|stash|reset\s+--hard|rebase|merge|worktree\s+remove)\b"
)
_GIT_ADD_ALL = re.compile(r"\bgit\s+add\s+(-A\b|--all\b|\.\s*$|\.\s)")
_DDEV_EXEC = re.compile(r"^\s*ddev\s+exec\s+(?:-s\s+\S+\s+)?(.*)$", re.S)


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
def proc_root() -> Path:
    return Path(os.environ.get("CLAUDE_PROC_ROOT") or "/proc")


def sock_dir() -> Path:
    return Path(os.environ.get("CLAUDE_SOCK_DIR") or f"/run/user/{os.getuid()}/cc-socks")


def process_cwd(pid: int, root: Path | None = None) -> str | None:
    try:
        cwd = os.readlink(f"{root or proc_root()}/{pid}/cwd")
    except OSError:
        return None
    return None if cwd.endswith(" (deleted)") else cwd


def sessions_from_proc(root: Path | None = None, uid: int | None = None) -> dict[int, str]:
    """pid -> cwd for every process named `claude` that this user owns."""
    root = Path(root or proc_root())
    uid = os.getuid() if uid is None else uid
    out: dict[int, str] = {}
    for entry in (root.iterdir() if root.is_dir() else []):
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != uid or (entry / "comm").read_text().strip() != "claude":
                continue
        except OSError:
            continue
        cwd = process_cwd(int(entry.name), root)
        if cwd:
            out[int(entry.name)] = cwd
    return out


def sessions_from_socks(directory: Path, root: Path | None = None) -> dict[int, str]:
    out: dict[int, str] = {}
    d = Path(directory)
    for s in (d.glob("*.sock") if d.is_dir() else []):
        try:
            pid = int(s.stem)
        except ValueError:
            continue
        cwd = process_cwd(pid, root)
        if cwd:
            out[pid] = cwd
    return out


def live_sessions() -> dict[int, str]:
    return {**sessions_from_socks(sock_dir()), **sessions_from_proc()}


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


def pre_edit(tool: str, file_path: str, cwd: str, state: Path) -> dict | None:
    rel = rel_path(file_path, cwd)

    if rel.startswith(KALSHI_PATHS) and not has(state, "kalshi_docs_read"):
        return _deny(f"R3: {rel} carries Kalshi-sourced data and no docs/kalshi/ page has been read this session "
                     "(HARD RULE, CLAUDE.md). Read docs/kalshi/CHEATSHEET.md (titles are printed at session start) "
                     "and the exact mirrored page for the field/endpoint you are touching, then retry.")

    if rel.startswith(HOT_PATHS) and not has(state, "gitnexus_ran") and not has(state, "hot_edit_warned"):
        mark(state, "hot_edit_warned")
        return _deny(f"R4: {rel} is on the money/strategy hot path. Run a blast-radius check first - "
                     f"`{GITNEXUS} impact <symbol>` or the gitnexus MCP impact tool - then retry. "
                     "This gate fires once per session; the check is what the tool exists for.")
    return None


def post(tool: str, tool_input: dict, cwd: str, state: Path) -> dict | None:
    """Returns {"context": str} | None."""
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
        if rel.startswith(HOT_PATHS) and not has(state, "dim_nudged"):
            mark(state, "dim_nudged")
            return {"context": f"{rel} is money/probability math: run the dimensional-analysis skill on the change "
                               "before calling it done (two shipped bugs of that class here)."}
    return None


# ---------------------------------------------------------------- main
def print_sessions(sessions: dict[int, str], self_pids: set[int], out=None) -> None:
    out = out or sys.stdout
    for pid in sorted(sessions):
        tag = "self" if pid in self_pids else "other"
        out.write(f"{pid}\t{tag}\t{sessions[pid]}\n")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--sessions"]:
        print_sessions(live_sessions(), ancestor_pids())
        return 0

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
            out = pre_edit(tool, tool_input.get("file_path") or "", cwd, state)
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
        if out and "context" in out:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                                     "additionalContext": out["context"]}}))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
