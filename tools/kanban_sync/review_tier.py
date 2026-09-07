"""Review-tier classification (docs/archive/lane-9-tooling-ci-process-governance/
specs/2026-09-07-ai-assisted-engineering-principles-design.md §3.1, §6.2).

Pure by design: every input - the changed-file list, the diff text, the labels,
the comment bodies - is passed in by the caller, so the whole boundary is
testable without a network call. tools/kanban_sync/__main__.py does the
fetching. The data (which paths, which suffixes, which pattern) lives in
labels.py; only the rules live here.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Iterator, Sequence

from tools.kanban_sync import labels


def _resolve_import_target(repo_root: Path, dotted: str) -> str | None:
    """`position.account_positions` -> `services/position/account_positions.py`;
    `exits` -> `services/exits/`. None for an import resolving to neither (a
    name re-exported from a package's __init__, say)."""
    rel = "services/" + dotted.replace(".", "/")
    if (repo_root / f"{rel}.py").exists():
        return f"{rel}.py"
    if (repo_root / rel).is_dir():
        return f"{rel}/"
    return None


def _imported_dotted_names(tree: ast.Module) -> Iterator[str]:
    """The three forms that appear in this repo, all of which must be caught:

        from services import fault_log, market_lookup     # the dominant form
        from services.position import account_positions
        import services.app_state

    Parsed rather than pattern-matched. A regex over `from services\\.` sees
    only the second and third, which is how six real Lane 3 dependencies stayed
    invisible until 2026-09-07 - `services/strategy_engine.py:9` alone imports
    six modules in the first form.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module == "services":
                for alias in node.names:
                    yield alias.name
            elif node.module.startswith("services."):
                yield node.module[len("services."):]
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("services."):
                    yield alias.name[len("services."):]


def lane3_direct_imports(repo_root: Path) -> set[str]:
    """Repo-relative paths of every module a Lane 3 source imports directly.

    The 2026-09-03 memory's "widely-used code deserves the deeper review"
    criterion, made mechanical: strategy, risk, and execution are where a
    defect costs money, so what they depend on is Tier A too - computed from
    the source on every run, not from a list someone must remember to update.
    Returns 27 targets at a39d5f9.
    """
    targets: set[str] = set()
    for pkg in labels.LANES[3]["packages"]:
        base = repo_root / labels.resolve_lane_package(pkg)
        if base.is_dir():
            sources = sorted(base.rglob("*.py"))
        elif base.exists():
            sources = [base]
        else:
            sources = []
        for source in sources:
            tree = ast.parse(source.read_text(), filename=str(source))
            for dotted in _imported_dotted_names(tree):
                resolved = _resolve_import_target(repo_root, dotted)
                if resolved is not None:
                    targets.add(resolved)
    return targets
