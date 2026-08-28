#!/usr/bin/env python3
"""PostToolUse hook (Edit|Write): run only the test files named after the edited
module, inside the checkout that was edited. CI (.woodpecker/tests-pytest.yml)
is the only full-suite owner. `.claude/settings.json` must give this hook a
timeout greater than BUDGET_SEC, or the harness kills it silently.

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


def _in_scope(file_path: str) -> bool:
    return file_path.endswith(".py") and (
        "/services/" in file_path or file_path.startswith("services/") or file_path.endswith("main.py")
    )


def tests_for(file_path: str, tests_dir: Path) -> list[Path]:
    p = Path(file_path)
    stems = {p.stem} if p.stem != "main" else {"main", "routes"}
    if "services" in p.parts:
        i = p.parts.index("services")
        if len(p.parts) > i + 2:
            stems.add(p.parts[i + 1])  # package name, e.g. "exits"
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


def main(argv=None, run=subprocess.run, tests_dir=TESTS_DIR, cwd=None, primary=None) -> int:
    try:
        file_path = (json.load(sys.stdin).get("tool_input") or {}).get("file_path") or ""
    except Exception:
        return 0
    if not _in_scope(file_path):
        return 0
    targets = tests_for(file_path, tests_dir)
    if not targets:
        print(f"run_tests hook: no tests/test_<module>*.py for {file_path}; CI owns it")
        return 0
    if primary is None:
        primary = primary_root()
    if cwd is None:
        cwd = container_cwd(ROOT, primary)
    files = " ".join(f"tests/{t.name}" for t in targets)
    shell = f"cd {cwd} && python3 -m pytest -q -p no:testmon {files}"
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
        print("run_tests hook: ddev not found; skipped")
        return 0
    if r.returncode != 0:
        sys.stderr.write(f"pytest failed after editing {file_path}:\n{r.stdout}\n{r.stderr}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
