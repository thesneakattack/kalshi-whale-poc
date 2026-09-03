#!/usr/bin/env python3
"""PostToolUse hook (Edit|Write): run only the test files named after the edited
module, inside the checkout that was edited. Scope is application code AND the
workflow tooling (.claude/hooks/, tools/, scripts/) - the four-fix cascade on
scripts/cleanup-worktrees.sh (2026-08-28) happened because tooling edits had no
automatic test at all. CI (.woodpecker/tests-pytest-app.yml +
tests-pytest-tooling.yml, split 2026-09-03 - see
tools/classify_pytest_app_vs_tooling.py) is the only full-suite
owner. `.claude/settings.json` must give this hook a timeout greater than
BUDGET_SEC, or the harness kills it silently.

ddev mounts the *primary* checkout at /app and refuses `ddev exec` from a
linked worktree's directory, so the command is launched from the primary
root and `cd`s to the worktree's path inside the container.
"""
import json
import subprocess
import sys
from pathlib import Path

BUDGET_SEC = 55
ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = ROOT / "tests"
SCOPES = ("services/", ".claude/hooks/", "tools/", "scripts/")


def _in_scope(file_path: str) -> bool:
    if file_path.endswith("main.py"):
        return True
    name = Path(file_path).name
    if "/tests/" in file_path or file_path.startswith("tests/"):
        return name.startswith("test_") and name.endswith(".py")
    if ("/scripts/" in file_path or file_path.startswith("scripts/")) and "." not in name:
        return True  # extension-less shell scripts (scripts/woodpecker-status)
    if not (name.endswith(".py") or name.endswith(".sh")):
        return False
    return any(f"/{s}" in file_path or file_path.startswith(s) for s in SCOPES)


# Explicit overrides, checked before the generic package-name glob below.
# CI pipeline audit Tier 2 #5/#6 (2026-09-03): two real local-feedback gaps
# found by inspecting real edit frequency against this hook's own mapping -
# app_state.py mapped to zero tests despite 18 commits/7d (it has no
# dedicated test file; it's main.py's own extracted singleton wiring per
# its docstring, so it shares main.py's {main,routes} bucket), and
# services/kalshi/{websocket,public}.py's bare stems matched the "kalshi"
# package-name glob below, pulling in all 18 test_kalshi*.py files (387
# tests, 55.5s serial - over this hook's own 55s budget before any
# ddev-exec/interpreter overhead). Narrowed to each file's real import-graph
# dependents, confirmed via `grep -oE "from services\.kalshi[a-zA-Z_.]*
# import" tests/test_kalshi_*.py`; re-derive that grep if either file's own
# imports change enough to plausibly add/drop a dependent test file, don't
# assume this list stays accurate forever.
_STEM_OVERRIDES: dict[str, set[str]] = {
    "app_state": {"main", "routes"},
    "websocket": {
        "kalshi_contracts", "kalshi_trade_ws", "kalshi_ws_consumer_liveness",
        "kalshi_ws_ingest_metrics", "kalshi_ws_signing_credentials", "kalshi_ws_two_consumers",
    },
    "public": {"kalshi_account_client", "kalshi_client", "kalshi_public_gateway"},
}


def tests_for(file_path: str, tests_dir: Path) -> list[Path]:
    p = Path(file_path)
    if p.name.startswith("test_") and p.name.endswith(".py"):
        t = tests_dir / p.name
        return [t] if t.exists() else []
    stem = p.stem.replace("-", "_")
    if stem in _STEM_OVERRIDES:
        stems = set(_STEM_OVERRIDES[stem])
    else:
        stems = {stem} if stem != "main" else {"main", "routes"}
        for pkg_root in ("services", "tools"):
            if pkg_root in p.parts:
                i = p.parts.index(pkg_root)
                if len(p.parts) > i + 2:
                    stems.add(p.parts[i + 1])  # package name, e.g. "exits", "kanban_sync"
    return sorted({t for s in stems for t in tests_dir.glob(f"test_{s}*.py")})


def primary_root(root: Path = ROOT) -> Path:
    """A linked worktree lives at <primary>/.claude/worktrees/<name>; anything
    else is the primary checkout itself. Path-only - no git needed."""
    parts = root.resolve().parts
    for i in range(len(parts) - 2, 0, -1):
        if parts[i] == ".claude" and parts[i + 1] == "worktrees":
            return Path(*parts[:i])
    return root.resolve()


def container_cwd(root: Path, primary: Path) -> str:
    try:
        rel = root.resolve().relative_to(primary.resolve())
    except ValueError:
        return "/app"
    return "/app" if str(rel) == "." else f"/app/{rel.as_posix()}"


def _context(text: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}))


def main(argv=None, run=subprocess.run, tests_dir=TESTS_DIR, cwd=None, primary=None) -> int:
    try:
        file_path = (json.load(sys.stdin).get("tool_input") or {}).get("file_path") or ""
    except Exception:
        return 0
    if not _in_scope(file_path):
        return 0
    targets = tests_for(file_path, tests_dir)
    if not targets:
        _context(f"run_tests hook: no tests/test_<module>*.py covers {file_path} - write one before calling "
                 "this done (tooling code without a test is how the cleanup-worktrees cascade happened).")
        return 0
    if primary is None:
        primary = primary_root()
    if cwd is None:
        cwd = container_cwd(ROOT, primary)
    files = " ".join(f"tests/{t.name}" for t in targets)
    shell = f'cd {cwd} && python3 -m pytest -q -p no:testmon -m "not slow" {files}'
    cmd = ["ddev", "exec", "-s", "fastapi", "sh", "-c", shell]
    try:
        r = run(cmd, capture_output=True, text=True, timeout=BUDGET_SEC, cwd=str(primary))
    except subprocess.TimeoutExpired:
        sys.stderr.write(
            f"run_tests hook: {len(targets)} file(s) exceeded {BUDGET_SEC}s budget for {file_path}"
            " - narrow the scope or let CI own it\n"
        )
        return 2
    except FileNotFoundError:
        _context(f"run_tests hook: ddev not found, so {files} did NOT run for {file_path} - run them yourself or let CI own it.")
        return 0
    if r.returncode != 0:
        sys.stderr.write(f"pytest failed after editing {file_path}:\n{r.stdout}\n{r.stderr}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
