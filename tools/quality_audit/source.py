"""Filesystem/AST helpers shared by every Quality Control Plane static
scanner (docs/superpowers/plans/2026-08-24-quality-control-plane.md, Task 3
Step 3). Centralizes "what counts as this repo's own Python source" so each
scanner doesn't reinvent its own exclude list.
"""
from __future__ import annotations

import ast
from pathlib import Path

# Directories no scanner should ever walk into: VCS/tooling internals,
# live SQLite data (never touched, read or otherwise, by static analysis -
# see CLAUDE.md's "data/*.db files are live" section), and dependency/cache
# trees that aren't this repo's own source.
EXCLUDED_DIR_NAMES: frozenset[str] = frozenset({
    ".git",
    ".ddev",
    "data",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
})

# Generated, not hand-authored - .github/workflows/quality.yml's
# frontend-build job already diffs this file against a fresh build. Not
# reachable via iter_python_files' *.py glob today, but listed here so any
# future non-Python file walker built on this module inherits the same
# exclusion without rediscovering it.
EXCLUDED_RELATIVE_FILES: frozenset[str] = frozenset({
    "static/js/dashboard.bundle.js",
})


def _is_excluded(path: Path, repo_root: Path) -> bool:
    rel_parts = path.relative_to(repo_root).parts
    if any(part in EXCLUDED_DIR_NAMES for part in rel_parts):
        return True
    return "/".join(rel_parts) in EXCLUDED_RELATIVE_FILES


def iter_python_files(repo_root: Path, include_tests: bool = False) -> list[Path]:
    """Every non-excluded *.py file under repo_root, sorted for determinism.

    tests/ is excluded by default since scanners audit application source,
    not the test suite itself; pass include_tests=True for a scanner that
    genuinely needs to see test code (e.g. cross-referencing symbol usage
    that legitimately excludes tests/ callers, per the background-wiring
    scanner's "non-test external references" rule in Task 4).
    """
    repo_root = Path(repo_root)
    results = []
    for path in repo_root.rglob("*.py"):
        if _is_excluded(path, repo_root):
            continue
        rel_parts = path.relative_to(repo_root).parts
        if not include_tests and rel_parts and rel_parts[0] == "tests":
            continue
        results.append(path)
    return sorted(results)


def read_text(path: Path) -> str:
    return Path(path).read_text()


def parse_python(path: Path) -> ast.Module:
    return ast.parse(read_text(path), filename=str(path))


def relative_path(repo_root: Path, path: Path) -> str:
    return str(Path(path).relative_to(Path(repo_root)))


def module_dotted_path(repo_root: Path, path: Path) -> str:
    """The dotted import path a file would be imported as, e.g.
    services/foo/routes.py -> "services.foo.routes",
    services/foo/__init__.py -> "services.foo"."""
    rel_parts = list(Path(path).relative_to(Path(repo_root)).with_suffix("").parts)
    if rel_parts and rel_parts[-1] == "__init__":
        rel_parts = rel_parts[:-1]
    return ".".join(rel_parts)
