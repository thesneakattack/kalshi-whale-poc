"""
Mechanical facts about the repo's current size/shape - Quality Control
Plane Task 17 (docs/superpowers/plans/2026-08-24-quality-control-plane.md).
static/status.html's "headline status" header hardcoded file/line/route
counts at whatever they were the day it was last hand-edited (found stale
2026-08-24: "5,189 lines / 29 files / 20 API routes" against a real repo
that had grown to 220 Python files and 93 route decorators by then) -
this generates those specific facts fresh instead, committed as
static/project-manifest.json and re-verified in CI so they can't silently
go stale again the same way.

Deliberately narrow scope, matching the design spec's own list: only facts
that go stale *mechanically* (file/line counts, service/route/frontend-
module/workflow inventory, git HEAD). The historical timeline and any
semantic capability claim ("real order placement is gated behind
trading_enabled") stay human-authored prose in status.html - this module
generates numbers, never narrative.

No live pytest run, no live git subprocess beyond an optional
`git rev-parse HEAD` (never required - see build_manifest's own handling of
a missing .git directory). Everything else is pure filesystem/regex
analysis, same "no network, no live execution" posture as
tools/kalshi_docs_drift.py and tools/quality_audit.
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import yaml

_EXCLUDED_DIR_NAMES = {".git", "node_modules", "__pycache__", ".pytest_cache", "data", ".ddev", ".ruff_cache"}

# static/js/dashboard.bundle.js (frontend/'s esbuild output) is the one real
# generated-output file in this repo as of 2026-08-24 - excluded from
# "source" javascript counts per the design spec's explicit instruction.
# Path is relative to repo_root.
_GENERATED_JS_PATHS = {"static/js/dashboard.bundle.js"}

_ROUTE_DECORATOR_RE = re.compile(r"^@(?:app|router)\.(?:get|post|put|delete|patch)\(", re.MULTILINE)
_TEST_FUNC_RE = re.compile(r"^def test_", re.MULTILINE)


def _excluded(path: Path) -> bool:
    return any(part in _EXCLUDED_DIR_NAMES for part in path.parts)


def _iter_files(repo_root: Path, suffix: str):
    for path in sorted(repo_root.rglob(f"*{suffix}")):
        if path.is_file() and not _excluded(path):
            yield path


def _count_lines(path: Path) -> int:
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        return sum(1 for _ in f)


def _files_and_lines(repo_root: Path, suffix: str, extra_excluded_paths: set[str] = frozenset()) -> tuple[int, int]:
    files = 0
    lines = 0
    for path in _iter_files(repo_root, suffix):
        rel = path.relative_to(repo_root).as_posix()
        if rel in extra_excluded_paths:
            continue
        files += 1
        lines += _count_lines(path)
    return files, lines


def _count_services(repo_root: Path) -> int:
    """A "service" is a direct child of services/ that's either a plain
    module (some_thing.py, excluding the package's own __init__.py) or a
    subpackage directory (has its own __init__.py) - matches how CLAUDE.md
    and status.html's own Component Reference already describe this
    codebase's `services/<name>/` modularization: one row per top-level
    entry, whether that entry is a single file or a whole package."""
    services_dir = repo_root / "services"
    if not services_dir.is_dir():
        return 0
    count = 0
    for entry in services_dir.iterdir():
        if entry.name == "__pycache__":
            continue
        if entry.is_dir():
            if (entry / "__init__.py").exists():
                count += 1
        elif entry.suffix == ".py" and entry.name != "__init__.py":
            count += 1
    return count


def _count_api_routes(repo_root: Path) -> int:
    """@app.get/post/put/delete/patch(...) in main.py plus
    @router.get/post/put/delete/patch(...) across every routes.py under
    services/ - the two real route-registration surfaces in this app
    (see CLAUDE.md's quick file map: main.py is the FastAPI app itself,
    services/<name>/routes.py is where every APIRouter lives)."""
    total = 0
    main_py = repo_root / "main.py"
    if main_py.exists():
        total += len(_ROUTE_DECORATOR_RE.findall(main_py.read_text(encoding="utf-8", errors="ignore")))
    services_dir = repo_root / "services"
    if services_dir.is_dir():
        for path in sorted(services_dir.rglob("routes.py")):
            if _excluded(path):
                continue
            total += len(_ROUTE_DECORATOR_RE.findall(path.read_text(encoding="utf-8", errors="ignore")))
    return total


def _count_tests(repo_root: Path) -> dict:
    tests_dir = repo_root / "tests"
    files = 0
    count = 0
    if tests_dir.is_dir():
        for path in sorted(tests_dir.rglob("test_*.py")):
            if _excluded(path):
                continue
            files += 1
            count += len(_TEST_FUNC_RE.findall(path.read_text(encoding="utf-8", errors="ignore")))
    return {"files": files, "count": count}


def _count_workflows(repo_root: Path) -> tuple[int, int]:
    """workflow_files: every .github/workflows/*.yml + .woodpecker/*.yml.
    workflow_jobs: GitHub Actions' own job count (len of the `jobs:`
    mapping) for the former, step count (len of the `steps:` list) for the
    latter - Woodpecker's "job" unit in this repo is one pipeline-file's
    step list, not a `jobs:` mapping (see docs/woodpecker-ci.md's own
    "Pipeline topology": one file per named check, not one file with many
    jobs). A file that doesn't parse as YAML, or doesn't have the expected
    top-level key, contributes 0 jobs rather than raising - "workflow
    files/jobs where parsed reliably," per this task's own plan, not a
    hard requirement that every file yield a job count."""
    files = 0
    jobs = 0
    for workflows_dir, key in ((repo_root / ".github" / "workflows", "jobs"), (repo_root / ".woodpecker", "steps")):
        if not workflows_dir.is_dir():
            continue
        for path in sorted(workflows_dir.glob("*.yml")):
            if _excluded(path):
                continue
            files += 1
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8", errors="ignore"))
            except yaml.YAMLError:
                continue
            if isinstance(data, dict) and isinstance(data.get(key), (dict, list)):
                jobs += len(data[key])
    return files, jobs


def _git_head(repo_root: Path) -> str | None:
    if not (repo_root / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def build_manifest(repo_root: Path, now: float | None = None) -> dict:
    repo_root = Path(repo_root)
    py_files, py_lines = _files_and_lines(repo_root, ".py")
    js_files, js_lines = _files_and_lines(repo_root, ".js", extra_excluded_paths=_GENERATED_JS_PATHS)
    html_files, html_lines = _files_and_lines(repo_root, ".html")
    workflow_files, workflow_jobs = _count_workflows(repo_root)

    return {
        "schema_version": 1,
        "generated_at": now if now is not None else time.time(),
        "generated_from_head": _git_head(repo_root),
        "files": {"python": py_files, "javascript": js_files, "html": html_files},
        "lines": {"python": py_lines, "javascript": js_lines, "html": html_lines},
        "services": _count_services(repo_root),
        "api_routes": _count_api_routes(repo_root),
        "frontend_modules": _files_and_lines(repo_root / "frontend" / "src" / "js", ".js")[0]
        if (repo_root / "frontend" / "src" / "js").is_dir() else 0,
        "workflow_files": workflow_files,
        "workflow_jobs": workflow_jobs,
        "tests": _count_tests(repo_root),
    }


# Fields that legitimately change on every commit regardless of structural
# drift - excluded from --check's equality comparison per this task's own
# plan ("the check should gate structural facts, not require a manifest
# commit on every unrelated code commit merely because SHA changed").
_CHURN_FIELDS = ("generated_from_head", "generated_at")


def _structural(manifest: dict) -> dict:
    return {k: v for k, v in manifest.items() if k not in _CHURN_FIELDS}


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m tools.project_manifest")
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--write", type=Path, default=None, help="build a fresh manifest and write it to PATH")
    parser.add_argument("--check", type=Path, default=None, help="compare PATH's structural facts against a fresh build")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.write is not None:
        manifest = build_manifest(args.repo_root)
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"project-manifest: wrote {args.write}")
        return 0

    if args.check is not None:
        if not args.check.exists():
            print(f"project-manifest: {args.check} does not exist - run --write first")
            return 2
        committed = json.loads(args.check.read_text())
        fresh = build_manifest(args.repo_root)
        committed_structural = _structural(committed)
        fresh_structural = _structural(fresh)
        if committed_structural == fresh_structural:
            print("project-manifest: up to date")
            return 0
        print(f"project-manifest: {args.check} is stale - structural facts have drifted:")
        for key in sorted(set(committed_structural) | set(fresh_structural)):
            old = committed_structural.get(key)
            new = fresh_structural.get(key)
            if old != new:
                print(f"  {key}: committed={old!r} current={new!r}")
        return 1

    print("project-manifest: pass --write PATH or --check PATH")
    return 2


if __name__ == "__main__":
    sys.exit(main())
