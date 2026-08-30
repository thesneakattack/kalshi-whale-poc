#!/usr/bin/env python3
"""Workflow guard: the standing rules a session must not talk itself out of,
enforced as harness decisions instead of prose.

PreToolUse (Bash):
  R8 `git add -A` / `git add .`                        -> deny (stage specific paths)
  R2 ad hoc sqlite3/python on data/*.db before any     -> deny once, then allow
     /api/quality|health read this session
  R7 `ddev exec` from a linked worktree                -> deny, with the working command

R6 (the peer-session git-merge guard) was retired 2026-08-30: its liveness
check had no time dimension (a session record from a process that died
without a clean handoff counted identically to a genuinely active one), so
it could deny a real merge for a reason that no longer existed - happened
twice in one night, once as a false positive on `merge-tree` (fixed, then
retired anyway) and once as a real block that needed a manual `/proc`
check + `kill -TERM` to clear. No installed replacement covers this
specific job, and this repo's own workflow-tooling standard (CLAUDE.md's
Toolchain section) now defaults a handspun mechanism to off until it has a
demonstrated track record of net value - R6 didn't have one. The session
registry/`--sessions` output it used stays: `scripts/cleanup-worktrees.sh`
and `orient.sh` still read it for their own (correctly staleness-aware)
purposes.
PreToolUse (Edit|Write):
  R3 Kalshi-shaped file, no docs/kalshi read yet       -> deny (HARD RULE, CLAUDE.md)
  R4 money/strategy hot file, no GitNexus run yet      -> deny once, then allow
PostToolUse (Bash|Read|Edit|Write|mcp__gitnexus__*):
  records the markers the gates read; nudges dimensional-analysis once per
  session on a hot-file edit; at most every 10 minutes measures the
  uncommitted diff and nudges /checkpoint once it passes 5 files or 150
  lines (the mechanism CLAUDE.md's "checkpoint often" used to have).

Every event also records this session in a registry the harness feeds:
<state>/session.json = {pid of the Claude process this hook runs under, its
comm, the payload's cwd}. The payload cwd follows EnterWorktree, so the
registry knows where a session really works even when /proc's cwd is stale
or "(deleted)".

`--sessions` prints a "#sessions v1" header, then one line per live Claude
session: `<pid>\t<self|other>\t<cwd>`. orient.sh and
scripts/cleanup-worktrees.sh read that instead of re-implementing discovery
(the header is how a consumer tells a real answer from an older guard that
ignores the flag). Sessions come from three sources, later ones winning on
cwd: control sockets under $XDG_RUNTIME_DIR/cc-socks (only some entrypoints
register one), every process named `claude` owned by this user in /proc, and
the registry above (a record counts while its pid is alive with the same
comm, so a reused pid never resurrects a dead session).

State lives per session under $XDG_RUNTIME_DIR/claude-workflow-guard/<session_id>/
(tmpfs, shared across worktrees). Every rule is a pure function of (payload,
state, live sessions, git) so tests/test_guard_workflow.py drives it without a
harness.
"""
import json
import os
import re
import subprocess
import sys
import time
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
# The dashboard derives money client-side too - the no-side `1 - price` inversion
# shipped here, not in services/. static/js is esbuild output, never hand-edited.
MONEY_UI_PATHS = ("frontend/src/js/",)
DDEV_PROJECT = "kalshi-whale-poc"
GITNEXUS = "npx gitnexus@1.6.10"
SESSIONS_HEADER = "#sessions v1"
NUDGE_EVERY_SEC = 600
NUDGE_FILES = 5
NUDGE_LINES = 150

_SQLITE_ON_DATA = re.compile(r"sqlite3.*data/|data/\S*\.db.*sqlite3|sqlite3\.connect\([^)]*data/")
_DIAG_READ = re.compile(r"api/quality/summary|api/health/|api/observability/")
_GIT_ADD_ALL = re.compile(r"\bgit\s+add\s+(-A\b|--all\b|\.\s*$|\.\s)")
_DDEV_EXEC = re.compile(r"^\s*ddev\s+exec\s+(?:-s\s+\S+\s+)?(.*)$", re.S)
_SHORTSTAT_NUM = re.compile(r"(\d+) (?:insertion|deletion)")


# ---------------------------------------------------------------- state
def registry_root() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / "claude-workflow-guard"


def state_dir(session_id: str) -> Path:
    d = registry_root() / (session_id or "no-session")
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


def process_comm(pid: int, root: Path | None = None) -> str | None:
    try:
        return (Path(root or proc_root()) / str(pid) / "comm").read_text().strip()
    except OSError:
        return None


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


def sessions_from_socks(directory: Path) -> dict[int, str]:
    out: dict[int, str] = {}
    d = Path(directory)
    for s in (d.glob("*.sock") if d.is_dir() else []):
        try:
            pid = int(s.stem)
        except ValueError:
            continue
        cwd = process_cwd(pid)
        if cwd:
            out[pid] = cwd
    return out


def sessions_from_registry(base: Path | None = None, root: Path | None = None) -> dict[int, str]:
    """Sessions this guard has seen, still alive: pid present with the recorded comm.

    One process can own several records - a session id rotates while the pid does
    not - so records are applied oldest first and the newest `at` wins. Ordering by
    the filesystem's glob order instead would hand back whichever cwd happened to be
    listed last (observed 2026-08-28 with two stale records for one pid)."""
    out: dict[int, str] = {}
    base = Path(base or registry_root())
    records = []
    for f in (base.glob("*/session.json") if base.is_dir() else []):
        try:
            rec = json.loads(f.read_text())
            records.append((float(rec.get("at") or 0.0), int(rec["pid"]), rec))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    for _, pid, rec in sorted(records, key=lambda r: r[0]):
        if process_comm(pid, root) != rec.get("comm"):
            continue
        cwd = rec.get("cwd")
        if cwd:
            out[pid] = cwd
    return out


def live_sessions() -> dict[int, str]:
    return {**sessions_from_socks(sock_dir()), **sessions_from_proc(), **sessions_from_registry()}


def ancestor_chain(root: Path | None = None) -> list[int]:
    """This process and its ancestors, nearest first, stopping below pid 1."""
    root = Path(root or proc_root())
    chain: list[int] = []
    pid = os.getpid()
    for _ in range(30):
        try:
            stat = (root / str(pid) / "stat").read_text()
        except OSError:
            break
        chain.append(pid)
        ppid = int(stat.rsplit(")", 1)[1].split()[1])
        if ppid <= 1:
            break
        pid = ppid
    return chain


def ancestor_pids() -> set[int]:
    return set(ancestor_chain())


def claude_pid(chain: list[int] | None = None, root: Path | None = None) -> int | None:
    """The Claude process this hook runs under: the nearest ancestor named `claude`,
    else the nearest `node` (a node-hosted CLI), else None."""
    fallback = None
    for pid in (chain if chain is not None else ancestor_chain(root)):
        comm = process_comm(pid, root)
        if comm == "claude":
            return pid
        if comm == "node" and fallback is None:
            fallback = pid
    return fallback


def record_session(state: Path, cwd: str, pid: int | None, root: Path | None = None) -> None:
    if pid is None or not cwd:
        return
    comm = process_comm(pid, root)
    if comm is None:
        return
    try:
        (state / "session.json").write_text(json.dumps({"pid": pid, "comm": comm, "cwd": cwd, "at": time.time()}))
    except OSError:
        pass


# ------------------------------------------------------------- helpers
def repo_root(cwd: str) -> Path:
    p = Path(cwd).resolve()
    for cand in (p, *p.parents):
        if (cand / ".git").exists():
            return cand
    return p


def primary_root(root: Path) -> Path:
    """<primary>/.claude/worktrees/<name> -> <primary>. Path-only on purpose: ddev
    mounts the primary at /app, so only a worktree under it is reachable from the
    container, which is the only reason R7 and run_tests.py need this."""
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


def _is_code(rel: str) -> bool:
    return rel.endswith(".py")


def _is_money_ui(rel: str) -> bool:
    return rel.startswith(MONEY_UI_PATHS) and rel.endswith(".js")


def pre_edit(tool: str, file_path: str, cwd: str, state: Path) -> dict | None:
    rel = rel_path(file_path, cwd)
    if not _is_code(rel):  # READMEs and cheatsheets under these packages are prose, not Kalshi-shaped code
        return None

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


def checkpoint_nudge(state: Path, git_run, now: float | None = None) -> str | None:
    """At most every NUDGE_EVERY_SEC: measure the uncommitted diff; nudge past the thresholds."""
    marker = state / "nudge_checked"
    now = time.time() if now is None else now
    try:
        last = marker.stat().st_mtime
    except OSError:
        last = 0.0
    if now - last < NUDGE_EVERY_SEC:
        return None
    marker.touch()
    os.utime(marker, (now, now))
    files = len([ln for ln in git_run(["status", "--porcelain"]).splitlines() if ln.strip()])
    lines = sum(int(n) for n in _SHORTSTAT_NUM.findall(git_run(["diff", "--shortstat", "HEAD"])))
    if files >= NUDGE_FILES or lines >= NUDGE_LINES:
        return (f"checkpoint nudge: {files} changed file(s), {lines} changed line(s) sitting uncommitted - "
                "run /checkpoint (commit verified units, push, confirm CI) before this grows further.")
    return None


def post(tool: str, tool_input: dict, cwd: str, state: Path, git_run=None) -> dict | None:
    """Returns {"context": str} | None."""
    notes: list[str] = []
    if tool == "Bash":
        cmd = tool_input.get("command") or ""
        if _DIAG_READ.search(cmd):
            mark(state, "diag_checked")
        if "docs/kalshi/" in cmd:
            mark(state, "kalshi_docs_read")
        if "gitnexus" in cmd:
            mark(state, "gitnexus_ran")
    elif tool.startswith("mcp__gitnexus__"):
        mark(state, "gitnexus_ran")
    elif tool == "Read":
        if "docs/kalshi/" in (tool_input.get("file_path") or ""):
            mark(state, "kalshi_docs_read")
    elif tool in ("Edit", "Write"):
        rel = rel_path(tool_input.get("file_path") or "", cwd)
        backend = _is_code(rel) and rel.startswith(HOT_PATHS)
        if (backend or _is_money_ui(rel)) and not has(state, "dim_nudged"):
            mark(state, "dim_nudged")
            where = ("money/probability math" if backend else
                     "a displayed financial figure - expose a backend-computed field rather than re-deriving here")
            notes.append(f"{rel} is {where}: run the dimensional-analysis skill on the change "
                         "before calling it done (two shipped bugs of that class here).")
    if tool in ("Bash", "Edit", "Write") and git_run is not None:
        nudge = checkpoint_nudge(state, git_run)
        if nudge:
            notes.append(nudge)
    return {"context": "\n".join(notes)} if notes else None


# ---------------------------------------------------------------- main
def print_sessions(sessions: dict[int, str], self_pids: set[int]) -> None:
    sys.stdout.write(SESSIONS_HEADER + "\n")
    for pid in sorted(sessions):
        tag = "self" if pid in self_pids else "other"
        sys.stdout.write(f"{pid}\t{tag}\t{sessions[pid]}\n")


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
    record_session(state, cwd, claude_pid())

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
        out = post(tool, tool_input, cwd, state, _git_runner_real(str(repo_root(cwd))))
        if out and "context" in out:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                                     "additionalContext": out["context"]}}))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
