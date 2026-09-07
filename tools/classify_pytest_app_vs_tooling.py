"""Classifies every tests/test_*.py file as "app" (imports something from
services.* or main - the two roots CLAUDE.md's own file map names as
application code) or "tooling" (does not) - the one split boundary the CI
pipeline audit's pytest-profile doc already measured as safe: "An app-code
change cannot affect the 79.8s of (c); a tools/hooks/scripts change cannot
affect the 365.6s of (a)" (docs/archive/lane-9-tooling-ci-process-governance/research/2026-09-02-ci-pipeline-audit-pytest-profile.md,
category (c) membership list, 2026-09-02).

Deliberately import-based, not name-based or a hand-maintained list: a test
file's real dependency surface is what determines whether an app-code
change could affect it, and imports are the one thing that's both checked
by the interpreter (can't silently drift the way a comment-maintained list
can) and directly machine-derivable. This is the same "read the real
imports, don't guess from directory names" method that caught
kalshi-contract-fixtures.yml's actual wide dependency surface during this
same audit (2026-09-03) - see .woodpecker/tests-dependency-audit.yml's
own comment for that story.

Run as: python -m tools.classify_pytest_app_vs_tooling
Prints two space-separated file lists (relative to tests/), one per line,
prefixed "APP:" / "TOOLING:", for .woodpecker/*.yml to consume directly.
Re-run this whenever the test suite changes meaningfully - it is not a
committed static list, it is a script precisely so it never goes stale.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"


def _imports_app_code(path: Path) -> bool:
    """True if this test file's own AST contains an `import services...`,
    `from services...`, `import main`, or `from main import ...` at any
    depth (including inside functions - some tests import lazily to dodge
    import-order issues, e.g. tests/test_trading_gate.py's own
    importlib.reload calls)."""
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "main" or alias.name.split(".")[0] == "services":
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.module and (node.module == "main" or node.module.split(".")[0] == "services"):
                return True
    return False


def classify() -> tuple[list[str], list[str]]:
    app: list[str] = []
    tooling: list[str] = []
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        (app if _imports_app_code(path) else tooling).append(path.name)
    return app, tooling


def main() -> int:
    # --list app|tooling: bare space-separated file names on stdout, nothing
    # else - for a CI step to capture directly into a pytest argument list.
    if len(sys.argv) == 3 and sys.argv[1] == "--list":
        app, tooling = classify()
        chosen = app if sys.argv[2] == "app" else tooling if sys.argv[2] == "tooling" else None
        if chosen is None:
            print("usage: --list app|tooling", file=sys.stderr)
            return 2
        print(" ".join(chosen))
        return 0

    app, tooling = classify()
    print(f"APP: {len(app)} files")
    print(f"TOOLING: {len(tooling)} files")
    print()
    print("--- APP (tests/<name> imports services.* or main) ---")
    for n in app:
        print(n)
    print()
    print("--- TOOLING (does not) ---")
    for n in tooling:
        print(n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
