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


_TEST_PATH_RE = re.compile(r"^tests/(?:.*/)?test_(?P<rest>.+)\.py$")


def _tier_a_test_stems() -> frozenset[str]:
    """The basename of every Tier A path, as a test-filename stem:
    `services/risk_manager.py` -> `risk_manager`, `services/kalshi/` ->
    `kalshi`, `.mcp.json` -> `mcp`."""
    stems = set()
    for prefix in labels.REVIEW_TIER_A_PATHS:
        name = prefix.rstrip("/").rsplit("/", 1)[-1].lstrip(".")
        stem = name.split(".", 1)[0]
        if stem:
            stems.add(stem)
    return frozenset(stems)


_TIER_A_TEST_STEMS = _tier_a_test_stems()


def _test_file_stem_match(path: str) -> str | None:
    """`tests/test_<stem>*.py` for a Tier A stem (spec D8). Prefix match at an
    underscore boundary, not a substring match: `test_maintenance.py` must not
    match the stem `main`, and `test_index_feed_backfill.py` (PR #498, Tier B)
    must not match the stem `backfill` from services/index_feed/backfill.py."""
    match = _TEST_PATH_RE.match(path)
    if not match:
        return None
    rest = match.group("rest")
    for stem in sorted(_TIER_A_TEST_STEMS, key=len, reverse=True):
        if rest == stem or rest.startswith(stem + "_"):
            return stem
    return None


def _is_prose_always(path: str) -> bool:
    return (
        path.startswith(labels.REVIEW_TIER_A_PROSE_ALWAYS)
        or bool(labels.REVIEW_TIER_A_PROSE_PATTERN.match(path))
    )


def _is_code_or_config(path: str) -> bool:
    base = path.rsplit("/", 1)[-1]
    return path.endswith(labels.REVIEW_TIER_A_CODE_SUFFIXES) or "." not in base


def _tier_a_prefix(path: str) -> str | None:
    for prefix in labels.REVIEW_TIER_A_PATHS:
        if path == prefix or path.startswith(prefix):
            return prefix
    return None


def _changed_lines(diff_text: str) -> list[str]:
    """Added and removed lines only - a matching line sitting in the diff's
    unchanged context is not a change to the data model."""
    return [
        line for line in diff_text.splitlines()
        if line[:1] in ("+", "-") and not line.startswith(("+++", "---"))
    ]


def review_tier(
    files: Sequence[str],
    *,
    diff_text: str = "",
    pr_labels: Sequence[str] = (),
    escalate: bool = False,
) -> tuple[str, list[str]]:
    """Tier A or Tier B, with the reasons that decided it (spec §3.1).

    Tier A owes the full self-review/adversarial-review/consolidation cycle at
    the PR; Tier B owes one persisted `Tier B self-review` comment plus green
    CI. Any rule firing makes it A - escalation is upward-only, and nothing
    here can move a PR from A to B. The mechanical/trivial exemption is decided
    by the merging session before this runs (`review-tier --exempt`), not here.
    """
    reasons: list[str] = []
    if escalate:
        reasons.append("escalated: author or merging session said Tier A")
    for path in files:
        if _is_prose_always(path):
            reasons.append(f"prose: {path}")
            continue
        if not _is_code_or_config(path):
            continue
        prefix = _tier_a_prefix(path)
        if prefix is not None:
            reasons.append(f"path: {path} (under {prefix})")
            continue
        stem = _test_file_stem_match(path)
        if stem is not None:
            reasons.append(f"test of Tier A code: {path} (stem {stem})")
    for line in _changed_lines(diff_text):
        if labels.REVIEW_TIER_A_DIFF_PATTERN.search(line):
            reasons.append(f"data model: {line.strip()[:100]}")
            break
    for name in pr_labels:
        if name in labels.REVIEW_TIER_A_LABELS:
            reasons.append(f"label: {name}")
    return ("A" if reasons else "B"), reasons
