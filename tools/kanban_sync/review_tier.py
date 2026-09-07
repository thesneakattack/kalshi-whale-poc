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


# How many import hops out from Lane 3 count as Lane 3's blast radius.
#
# Two, decided 2026-09-07 (David) after measuring every option against the
# recorded 200-PR window. The curve, in new Tier A paths / PRs reclassified /
# code PRs left at Tier B out of 111:
#
#   depth 1     0 /  0 / 16      the original, too narrow: it missed
#                                stats_power.py (money arithmetic) and
#                                diagnostics/ (opens paper_broker.DB_PATH)
#   depth 2     8 /  4 / 12   <- here
#   depth 3    20 / 10 /  6
#   depth 4    27 / 10 /  6
#   closure    27 / 10 /  6
#
# Depth 3 is the closure in everything but name - identical PRs, identical
# window, and the seven paths the closure adds beyond it are mostly files
# inside directories depth 3 already covers as prefixes. It would leave 5% of
# code PRs on the light path, which is roughly where the repo was before the
# tiering existed.
#
# The reason two is the boundary and not an arbitrary cut: at the third hop the
# traversal reaches observability/, research/, storage_health/ and alerting/
# through app_state and fault_log, which nearly everything touches. A defect in
# research/routes.py does not propagate into a trading decision - the graph
# reached it, the risk did not. In a codebase where everything eventually
# touches shared state, transitive reachability stops being a proxy for blast
# radius around hop three.
#
# What would retire this number: a Tier B PR that breaks something a third hop
# would have caught. Recorded in docs/open-decisions.md.
LANE3_SCAN_DEPTH = 2


def _services_sources(repo_root: Path, prefix: str) -> list[Path]:
    base = repo_root / prefix
    if base.is_dir():
        return sorted(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)
    if base.exists() and base.suffix == ".py":
        return [base]
    return []


def _direct_imports_of(repo_root: Path, prefix: str) -> set[str]:
    found: set[str] = set()
    for source in _services_sources(repo_root, prefix):
        try:
            tree = ast.parse(source.read_text(), filename=str(source))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for dotted in _imported_dotted_names(tree):
            resolved = _resolve_import_target(repo_root, dotted)
            if resolved is not None:
                found.add(resolved)
    return found


def lane3_dependencies(repo_root: Path, *, depth: int = LANE3_SCAN_DEPTH) -> set[str]:
    """Repo-relative paths of every module Lane 3 depends on within `depth` hops.

    The 2026-09-03 memory's "widely-used code deserves the deeper review"
    criterion, made mechanical: strategy, risk, and execution are where a defect
    costs money, so what they depend on is Tier A too - computed from the source
    on every run, not from a list someone must remember to update.

    `depth` is a hard bound, not a suggestion; see LANE3_SCAN_DEPTH above for
    why it is 2 and what the alternatives cost. 62 targets at depth 2, 27 at
    depth 1 (2026-09-07).
    """
    seeds = {labels.resolve_lane_package(pkg) for pkg in labels.LANES[3]["packages"]}
    targets: set[str] = set()
    frontier = seeds
    for _ in range(max(0, depth)):
        reached: set[str] = set()
        for prefix in frontier:
            reached |= _direct_imports_of(repo_root, prefix)
        frontier = (reached - targets) - seeds
        targets |= reached
        if not frontier:
            break
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


# A review artifact is recognised by the first line of its comment *starting*
# with what it is - optional markdown noise, an optional qualifier, then the
# word. Anchoring is the whole point: an unanchored search counted eight real
# comments that only *talk about* a review ("Response to the independent
# adversarial review's finding", "Fix-list recheck (adversarial review returned
# NO-GO)"), and PR #632 reached its required three through one of them. That is
# the body-narrative failure moving into a comment.
#
# Anchoring alone is not enough, and the 2026-09-07 PR-stage adversarial review
# proved it: narration that *starts* with the word still matched. "Consolidation
# is still pending - do not merge yet." counted as a consolidation, and under
# Tier B (one artifact required) that single comment was a full PASS with no
# review behind it - the initiative's own failure mode, inverted. Two real
# comments were counted this way (PRs #511 and #515, both author responses
# reporting a review's verdict rather than being one).
#
# The rule that fixes it is about grammar, not vocabulary, so it does not become
# a list of the mistakes made so far: an artifact's first line is a *label* -
# the artifact's name, optionally qualified - where narration is a *sentence*,
# the name followed by a finite verb. A markdown heading is a title by
# construction, so the start anchor alone settles it. Unmarked or bold-led text
# must additionally continue with a delimiter or end, never with another word.
#
# Measured against tests/fixtures/review_artifact_first_lines.tsv - 265 real
# first lines, 214 matched. Exactly two dropped against the unrestricted form,
# both of them the named false positives; nothing else moved, and no real
# heading form this repo posts was lost, including the `**bold**` ones that
# broke the stricter `^#+\s*(self-review|...)` form (which matched 170 and
# failed the fully compliant PR #625).
#
# One real artifact is still missed: `## Review outcome (independent adversarial
# review, fresh Agent call)`. Accepted deliberately - it fails *closed* (the PR
# reads FAIL and the author retitles the comment), and a pattern that defines
# the expected heading is worth more than one that guesses every past form. The
# rule text says what the first line must look like.
REVIEW_ARTIFACT_FIRST_LINE = re.compile(
    r"^[\s#*_>`]*"
    r"(?:(?:independent|pr(?:[-\s](?:level|stage))?|dispatching[-\s]session|"
    r"tier\s+b|final|lean|post[-\s]merge|stage\s+\d+(?:\s+of\s+\d+)?|"
    r"review[-\s]cycle)[\s,:—–-]+)*"
    r"(?:self[-\s]?review|adversarial(?:[-\s]review)?|consolidation)"
    r"(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)

# A `#` heading is a title, not a sentence - nothing further to check.
_ARTIFACT_HEADING = re.compile(r"^\s*#")

# Otherwise the name must end the label or hand off to a delimiter: `—`, `-`,
# `:`, `(`, `[`, `/`, `|`, `+` (a combined "Adversarial review + consolidation"),
# `.`, or a closing `**`. A following *word* means it is a sentence.
_ARTIFACT_LABEL_BOUNDARY = re.compile(r"^\s*(?:[*_`]*\s*)?(?:$|[—–\-:(\[/|+#.])")


def is_review_artifact_first_line(line: str) -> bool:
    """Does this comment's first line announce a review artifact (spec D2)?

    True for `## Self-review`, `**Consolidation — GO**`, `Tier B self-review`,
    `## stage 2 of 3 — Adversarial review`. False for a comment that merely
    talks about a review: `Consolidation is still pending`, `Adversarial review
    returned NO-GO on one finding`, `Self-review, adversarial review and
    consolidation are all in the PR body above`.
    """
    match = REVIEW_ARTIFACT_FIRST_LINE.match(line)
    if match is None:
        return False
    if _ARTIFACT_HEADING.match(line):
        return True
    return bool(_ARTIFACT_LABEL_BOUNDARY.match(match.group("rest")))


def count_review_artifacts(comments: Sequence[str]) -> tuple[int, list[str]]:
    """How many persisted review artifacts this PR carries.

    Comments only, by design. A committed `-self-review.md` / `-consolidation.md`
    document is a *stage* artifact for the planning pipeline; the PR-stage cycle
    reviews the PR as submitted, so its artifacts are PR comments. Counting
    review-named files in the diff would let a pipeline PR's earlier stages
    satisfy its PR stage - this initiative's own branch carries seven such files
    and would have passed with zero PR-stage comments, which is exactly the
    "nothing is shared, reused, or 'already covered' across stages" that
    CLAUDE.md forbids.

    Only the first line of a comment is read, and it must *begin* with what it
    is - a comment that narrates a review is not one (spec D2).

    What this cannot do (spec D11): tell an independent Agent's comment from the
    author's. Every comment in the measured window is by the same GitHub login.
    Independence rests on the session's honesty, as the rule already does; this
    catches absence, the failure that actually recurred four times.
    """
    matched: list[str] = []
    for body in comments:
        stripped = (body or "").strip()
        if not stripped:
            continue
        first_line = stripped.splitlines()[0]
        if is_review_artifact_first_line(first_line):
            matched.append(first_line)
    return len(matched), matched
