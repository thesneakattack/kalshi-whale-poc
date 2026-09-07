# AI-Assisted Engineering Principles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make review depth follow blast radius — a mechanical Tier A/B
boundary, a merge-time artifact check, and a read-only outcome report — and
land the seven principles as rule text in the files that already hold this
repo's rules.

**Architecture:** One new pure module under `tools/kanban_sync`
(`review_tier.py`) plus tier constants in the existing `labels.py`, four
read-only PR readers on the existing `GithubClient`, and one new CLI
subcommand (`review-tier`). Everything else is verbatim prose edits to
`CLAUDE.md`, `.claude/rules/branching-and-ci.md`, and
`.claude/skills/checkpoint/SKILL.md`. No hook, no gate, no runtime coupling
into the app: nothing here imports from `services/` or `main.py`, and nothing
writes to `data/*.db`.

**Why any code at all, rather than only rule text (David, 2026-09-07):** the
failure this addresses is prose that was not followed — 24 of 84 code PRs
merged with zero review artifacts while a HARD RULE already required them, and
four dated recurrences behind the `persist-code-pr-reviews-as-comments` memory.
More text in the file that was already ignored is the one intervention this
repo's own record says does not work. So exactly one command is built: the one
that counts what is there. The measurement report the design also specified is
cut (see "Deviations", item 5).

**Tech Stack:** Python 3 standard library only (`re`, `json`, `pathlib`,
`argparse`, `dataclasses`); the `gh` CLI through `GithubClient._invoke`'s
existing retry wrapper; pytest with the existing
`tests/support/fake_gh_runner.FakeRunner`.

**Spec:** `../specs/2026-09-07-ai-assisted-engineering-principles-design.md`
(revision 2, GO — read it alongside this plan; §3.1 is the boundary, §5 the
verbatim rule text, §9 the decisions).

**Research:** `../research/2026-09-07-ai-assisted-engineering-principles.md`
(the measurements every claim below rests on).

## Global Constraints

- Branch `docs/lane9-ai-assisted-engineering-principles`, in its worktree
  `.claude/worktrees/docs-lane9-ai-assisted-engineering`. Never rebase; the
  branch already merged `origin/main` at `a39d5f9`.
- Line numbers in spec §5 are anchored at `a39d5f9`. Re-verify each anchor
  with `awk 'NR==<n>' <file>` before editing; if a later merge moves them,
  re-anchor rather than trusting the number.
- Stage the exact paths each task names. The repo-wide staging forms are
  denied by `guard_workflow.py`'s `GIT_ADD_ALL_BLOCKED` rule.
- `config/settings.yaml` carries David's uncommitted safety overrides
  (`strategy.auto_exit_enabled: false`, `risk.max_daily_loss_pct: 0`) in the
  **primary** checkout. No task here touches that file's contents; it appears
  only as a string inside a path list.
- This whole PR is Tier A by its own rule (it changes `CLAUDE.md`,
  `.claude/rules/`, `.claude/hooks/`, and `tools/kanban_sync/labels.py`), so
  it owes the full self-review / adversarial-review / consolidation cycle at
  the PR before merge, on top of this plan stage's own cycle.
- Tests run targeted, not full-suite: `python -m pytest tests/<file> -v`. CI
  (Woodpecker) is the full-suite owner.
- Every new symbol is used by a later task in this plan; nothing here is
  speculative API.

## File structure

| File | Responsibility | Task |
|---|---|---|
| `.claude/hooks/guard_workflow.py` | drop three path entries naming deleted files | 1 |
| `tools/kanban_sync/labels.py` | tier **data**: `REVIEW_TIER_A_*` constants + `resolve_lane_package()` | 1 |
| `tools/kanban_sync/review_tier.py` (new) | tier **logic**: `lane3_direct_imports()`, `review_tier()`, `count_review_artifacts()` | 2, 3, 4 |
| `tools/kanban_sync/github_client.py` | four read-only PR readers | 5 |
| `tools/kanban_sync/__main__.py` | the `review-tier` subcommand | 6 |
| `tests/test_kanban_sync_labels.py` | constant integrity (exists-on-disk, hook subset) | 1 |
| `tests/test_kanban_sync_review_tier.py` (new) | import scan, classifier corpus, artifact regex | 2, 3, 4 |
| `tests/fixtures/review_artifact_first_lines.tsv` (new) | real PR-comment first lines | 4 |
| `tests/test_kanban_sync_github_client.py` | reader tests incl. >100-file pagination | 5 |
| `tests/test_kanban_sync_main.py` | CLI wiring and exit codes | 6 |
| `CLAUDE.md` | P1–P3, P5–P7 rule text (spec §5.1's fourteen edits) | 7 |
| `.claude/rules/branching-and-ci.md` | merge-step and protection-text edits (§5.2) | 8 |
| `.claude/skills/checkpoint/SKILL.md` | step 9 (§5.3) | 8 |
| `static/project-manifest.json` | regenerated file/line/test counts | 9 |
| `docs/next-action.md`, `docs/open-decisions.md` | rollout bookkeeping, and P4 parked with a date | 9 |

## Deviations from the spec, and why

Each is a refinement this plan makes while implementing spec §6; a reviewer
should check these first, because they are the only places the code will not
read exactly as §6 wrote it.

1. **Two modules, not one.** Spec §6.1 puts `review_tier()` and
   `lane3_direct_imports()` in `labels.py`. This plan keeps the **constants**
   there — the verbatim CLAUDE.md rule text in §5.1 names
   `REVIEW_TIER_A_PATHS` in `tools/kanban_sync/labels.py`, so that string must
   stay true — and puts the **functions** in a new
   `tools/kanban_sync/review_tier.py`. `labels.py` is this package's
   vocabulary module (constants and one resolver); a 120-line classifier doing
   file I/O in it would be a second responsibility. Nothing in the rule text
   names the functions' home.
2. **`labels=` renamed to `pr_labels=`.** Spec §6.1's signature
   `review_tier(files, *, diff_text="", labels=(), escalate=False)` shadows
   the `labels` module the function itself reads constants from. Renamed; the
   parameter's meaning is unchanged.
3. **Rule 1 also fires on an extensionless file under a Tier A path.** §3.1
   rule 1 requires a suffix from a fixed list, which silently excludes
   `scripts/woodpecker-status` and `scripts/woodpecker-trigger` — both
   extensionless shell scripts, both explicitly Tier A paths in the same rule.
   Measured against the 200-PR window before adopting: exactly one
   extensionless file appears anywhere in it (`Dockerfile`, PR #415), and it
   is not under a Tier A prefix, so §3.2's counts are unchanged. The suffix
   list exists to stop a `README.md` inside a Tier A package from firing; an
   extensionless executable is not prose.
4. **The Lane-3 import list in §3.1 is over-stated, and the plan says so.**
   Re-derived at `a39d5f9` by scanning all 16 Lane 3 source files: the direct
   `services.*` imports are `app_state`, `confidence_scoring`, `config`,
   `config.config_bounds`, `config.config_store`, `exits`,
   `kalshi.contracts.lifecycle`, `kalshi.interfaces`, `paper_broker`,
   `position.account_positions`, `risk_manager`, `signal_log`,
   `whale_calibration`. Of the five the spec lists as additions, four are
   **already** Tier A through `LANES` (`signal_log.py` is Lane 2, `kalshi/*`
   is Lane 1, `position/` is Lane 3); only `services/app_state.py` is
   genuinely added by this rule. That is consistent with the spec's own "moves
   0 further PRs" claim — the rule guards against future drift rather than
   adding coverage today — but the list as worded reads as five new paths.
   Task 2's test is the authority either way.
5. **The `outcomes` report (spec §6.3 and principle P4) is not built** —
   David's scope decision, 2026-09-07, on the direct question "why all this
   work and not just an updated CLAUDE.md". What survives is the part the
   evidence demands: the merge-time artifact count, which addresses a failure
   that actually recurred. What is cut is the part nothing has yet asked for —
   a defect-per-tier report, whose own design already carried a retirement
   date because it might never be cited, and which the repo's standing rule
   ("a handspun tool defaults to disabled until its own run history proves
   real value", 2026-08-30) says to earn rather than assume. Consequences:
   `get_pr_meta` and `list_merged_prs` drop out of Task 5, spec §5.3's
   `outcomes` line does not go into the checkpoint skill, and P4 is recorded
   as a dated open decision (Task 9 step 3) rather than a CLAUDE.md line about
   a measure nothing computes. Spec §6.3 is written out in full, so building
   it later is a task, not a redesign.

---

### Task 1: Tier A path constants, and the hook lists they must agree with

**Files:**
- Modify: `.claude/hooks/guard_workflow.py:76-89` (three dead entries out)
- Modify: `tools/kanban_sync/labels.py` (append a new section at the end)
- Modify: `tests/test_kanban_sync_labels.py` (helper at the top, tests at the end)

**Interfaces:**
- Consumes: `labels.LANES` (existing), `guard_workflow.KALSHI_PATHS` /
  `HOT_PATHS` / `MONEY_UI_PATHS` (existing)
- Produces: `labels.resolve_lane_package(pkg: str) -> str`;
  `labels.REVIEW_TIER_A_PATHS: tuple[str, ...]`;
  `labels.REVIEW_TIER_A_CODE_SUFFIXES: tuple[str, ...]`;
  `labels.REVIEW_TIER_A_PROSE_ALWAYS: tuple[str, ...]`;
  `labels.REVIEW_TIER_A_PROSE_PATTERN: re.Pattern`;
  `labels.REVIEW_TIER_A_DIFF_PATTERN: re.Pattern`;
  `labels.REVIEW_TIER_A_LABELS: frozenset[str]`

The hook edit belongs in this task, not a later one: the subset test below
cannot pass while `KALSHI_PATHS` names three files that no longer exist (they
cannot be added to a constant whose every entry must exist on disk). Removing
them and adding the constant is one coherent change.

- [ ] **Step 1: Write the failing tests**

In `tests/test_kanban_sync_labels.py`, replace the existing
`_ROOT_ANCHORED_PREFIXES` tuple and `_resolve_package_path` helper (lines
6–20) with this, so the test and the constant resolve lane entries through the
same code:

```python
import importlib.util

_HOOK = _REPO_ROOT / ".claude" / "hooks" / "guard_workflow.py"


def _resolve_package_path(pkg: str) -> Path:
    """Now a thin wrapper over labels.resolve_lane_package: the tier constant
    is built from LANES with that same resolver, so a divergence between this
    test and the constant is impossible by construction."""
    return _REPO_ROOT / labels.resolve_lane_package(pkg)


def _load_hook():
    spec = importlib.util.spec_from_file_location("guard_workflow", _HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tier_a_entry_exists(entry: str) -> bool:
    """An entry resolves to something real: a file, a directory, or - for the
    two `scripts/` prefix entries - at least one file starting with it."""
    if (_REPO_ROOT / entry).exists():
        return True
    return bool(list(_REPO_ROOT.glob(entry + "*")))
```

Add at the end of the same file:

```python
def test_every_review_tier_a_path_exists_on_disk():
    """The same guarantee LANES has (test_every_lane_package_path_exists_on_disk).
    This is what makes a stale path a blocking CI failure rather than a note:
    guard_workflow.py carried three entries naming files deleted by the
    services/kalshi/ migration for weeks without anything noticing."""
    missing = [e for e in labels.REVIEW_TIER_A_PATHS if not _tier_a_entry_exists(e)]
    assert not missing, f"REVIEW_TIER_A_PATHS entries not found on disk: {missing}"


def test_guard_workflow_path_tuples_are_subsets_of_review_tier_a_paths():
    """One authoritative list. The hook keeps its own tuples on purpose (spec
    D4: a hook launched as a bare script would need a sys.path insert to import
    from tools/, and an import failure would silently disable the Kalshi deny),
    so CI enforces the containment instead."""
    hook = _load_hook()
    covered = set(labels.REVIEW_TIER_A_PATHS)
    uncovered = [
        (name, entry)
        for name in ("KALSHI_PATHS", "HOT_PATHS", "MONEY_UI_PATHS")
        for entry in getattr(hook, name)
        if entry not in covered
    ]
    assert not uncovered, f"hook path entries missing from REVIEW_TIER_A_PATHS: {uncovered}"


def test_review_tier_a_paths_include_the_money_and_decision_inputs_outside_lanes_1_to_3():
    for entry in (
        "services/settlement_edge.py", "services/candidate_log.py",
        "services/history/", "services/app_state.py",
    ):
        assert entry in labels.REVIEW_TIER_A_PATHS


def test_review_tier_a_paths_include_the_tier_definition_itself():
    assert "tools/kanban_sync/labels.py" in labels.REVIEW_TIER_A_PATHS


def test_review_tier_a_prose_always_covers_the_rule_files_and_pipeline_dirs():
    assert "CLAUDE.md" in labels.REVIEW_TIER_A_PROSE_ALWAYS
    assert ".claude/rules/" in labels.REVIEW_TIER_A_PROSE_ALWAYS
    assert ".claude/skills/" in labels.REVIEW_TIER_A_PROSE_ALWAYS
    assert "docs/superpowers/" in labels.REVIEW_TIER_A_PROSE_ALWAYS
    assert labels.REVIEW_TIER_A_PROSE_PATTERN.match(
        "docs/archive/lane-9-tooling-ci-process-governance/specs/x-design.md"
    )
    assert not labels.REVIEW_TIER_A_PROSE_PATTERN.match(
        "docs/archive/lane-9-tooling-ci-process-governance/README.md"
    )


def test_review_tier_a_diff_pattern_matches_the_four_data_model_forms():
    for line in (
        "+    register_schema(_SCHEMA)",
        "+CREATE TABLE IF NOT EXISTS trades (",
        "+ALTER TABLE trades ADD COLUMN fee_cents INTEGER",
        "+PRAGMA user_version = 7",
    ):
        assert labels.REVIEW_TIER_A_DIFF_PATTERN.search(line), line
    assert not labels.REVIEW_TIER_A_DIFF_PATTERN.search("+    # schema notes live in db.py")


def test_review_tier_a_labels_is_exactly_concern_hotpath():
    assert labels.REVIEW_TIER_A_LABELS == frozenset({labels.CONCERN_HOTPATH})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_kanban_sync_labels.py -v`
Expected: FAIL — `AttributeError: module 'tools.kanban_sync.labels' has no attribute 'resolve_lane_package'`, and `REVIEW_TIER_A_PATHS` missing on the six new tests.

- [ ] **Step 3: Add the resolver and the constants to `labels.py`**

Add `import re` under `from __future__ import annotations`, then append after
`ALL_LANE_LABELS`:

```python
_ROOT_ANCHORED_PREFIXES = (
    "services/", ".claude/", ".github/", ".woodpecker/", "scripts/",
    "tests/", "bench/", "ui_samples/", "frontend/", "static/", "config/",
    "tools/", "docs/",
)


def resolve_lane_package(pkg: str) -> str:
    """A LANES entry as a repo-relative path prefix. Entries are written the
    way the lanes design's §3 table writes them: a handful (`services/kalshi/`,
    `config/settings.yaml`) already carry a root-anchored prefix, everything
    else is a bare name implicitly under `services/`. Lives here rather than in
    the test that used to own it, so REVIEW_TIER_A_PATHS below and
    tests/test_kanban_sync_labels.py resolve lane entries identically."""
    if pkg.startswith(_ROOT_ANCHORED_PREFIXES):
        return pkg
    return f"services/{pkg}"


# review tiering (docs/archive/lane-9-tooling-ci-process-governance/specs/
# 2026-09-07-ai-assisted-engineering-principles-design.md §3.1) - the
# mechanical answer to "how much review does this PR owe", decided by the paths
# it touches. Tier A owes CLAUDE.md's full self-review/adversarial-review/
# consolidation cycle; Tier B owes one persisted self-review comment plus green
# CI. Data only: the classifier is tools/kanban_sync/review_tier.py. Every
# entry must exist on disk and every guard_workflow.py path entry must appear
# here - both CI-enforced in tests/test_kanban_sync_labels.py.
_REVIEW_TIER_A_LANES = (1, 2, 3, 7)

_REVIEW_TIER_A_EXTRA_PATHS: tuple[str, ...] = (
    # guard_workflow.py's own lists, beyond what LANES 1/2/3/7 already cover:
    # the whole whale_stream package (LANES[1] names only two of its files),
    # advisory/ (a Lane 4 package on HOT_PATHS), and the money UI.
    "services/whale_stream/",
    "services/advisory/",
    "frontend/src/js/",
    # money and decision inputs outside Lanes 1-3: projected_probability() is
    # called from settlement_edge_entry.py:108, record_rejection() at eight
    # sites in strategy_engine.py, history/ computes fees and realized P&L.
    "services/settlement_edge.py",
    "services/candidate_log.py",
    "services/history/",
    # every module a Lane 3 module imports directly that no other rule covers
    # (review_tier.lane3_direct_imports() is the CI check that keeps this true).
    "services/app_state.py",
    # the data-plane plumbing CLAUDE.md's six properties ride on, plus the
    # paths that gate accounts or discard live data.
    "main.py",
    "services/db.py",
    "services/capture_writer.py",
    "services/task_supervisor.py",
    "services/tick_executor.py",
    "services/auth.py",
    "services/accounts_store.py",
    "services/reset/",
    ".ddev/",
    # process and CI, including this file: a change to the tier definition is
    # always Tier A.
    ".claude/hooks/",
    ".claude/settings.json",
    ".mcp.json",
    ".woodpecker/",
    ".github/workflows/",
    "tools/quality_audit/",
    "scripts/ci-",
    "scripts/woodpecker-",
    "tools/kanban_sync/labels.py",
)

REVIEW_TIER_A_PATHS: tuple[str, ...] = tuple(sorted(set(
    [resolve_lane_package(pkg)
     for lane in _REVIEW_TIER_A_LANES for pkg in LANES[lane]["packages"]]
    + list(_REVIEW_TIER_A_EXTRA_PATHS)
)))

# A path under a Tier A prefix fires rule 1 only if it is code or config - a
# README.md or CHEATSHEET.md inside services/kalshi/ does not. An extensionless
# file (scripts/woodpecker-status) counts as code: the suffix list exists to
# exclude prose, and an extensionless executable is not prose.
REVIEW_TIER_A_CODE_SUFFIXES: tuple[str, ...] = (
    ".py", ".js", ".ts", ".mjs", ".yaml", ".yml", ".json", ".toml", ".sh",
    ".sql", ".html", ".css",
)

# Prose that is always Tier A whatever its suffix: the rule files themselves,
# and any planning-pipeline stage document (which already owes a cycle).
REVIEW_TIER_A_PROSE_ALWAYS: tuple[str, ...] = (
    "CLAUDE.md", ".claude/rules/", ".claude/skills/", "docs/superpowers/",
)
REVIEW_TIER_A_PROSE_PATTERN = re.compile(
    r"^docs/archive/lane-[^/]+/(research|specs|plans)/"
)

# The data model is Tier A wherever it changes - no path list catches a Lane 4
# module altering its own table (spec D12: six Tier B PRs in the 200-PR window
# carried one of these lines).
REVIEW_TIER_A_DIFF_PATTERN = re.compile(
    r"register_schema\(|CREATE TABLE|ALTER TABLE|PRAGMA user_version"
)

REVIEW_TIER_A_LABELS = frozenset({CONCERN_HOTPATH})
```

- [ ] **Step 4: Remove the three dead hook entries**

In `.claude/hooks/guard_workflow.py`, `KALSHI_PATHS` and `HOT_PATHS` become:

```python
KALSHI_PATHS = (
    "services/kalshi/", "services/kalshi_fees.py", "services/market_catalog/",
    "services/market_watch/", "services/market_events/", "services/whale_stream/",
)
HOT_PATHS = (
    "services/strategy_engine.py", "services/risk_manager.py", "services/paper_broker.py",
    "services/confidence_scoring.py", "services/shadow_mode.py", "services/advisory/",
    "services/whale_calibration/", "services/exits/", "services/position/",
)
```

`services/kalshi_client.py`, `services/kalshi_account_client.py`, and
`services/kalshi_trade_ws.py` were deleted by the `services/kalshi/` migration
(Phase A, merged 2026-08-25); `services/kalshi/` already covers the code that
replaced them, so the deny rule's coverage is unchanged. No test references the
removed strings — `grep -rn 'kalshi_client' tests/` returns nothing.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_kanban_sync_labels.py tests/test_guard_workflow.py -v`
Expected: PASS, including the pre-existing
`test_every_lane_package_path_exists_on_disk` through its rewritten helper.

- [ ] **Step 6: Commit**

Stage `tools/kanban_sync/labels.py`, `tests/test_kanban_sync_labels.py`, and
`.claude/hooks/guard_workflow.py`, then commit with:

```
feat: add the Tier A path constants and make the hook lists agree with them

The tier boundary needs one authoritative path list. guard_workflow.py's three
lists become a CI-enforced subset of it, which immediately surfaced three
entries naming files the services/kalshi/ migration deleted - now removed.
Every entry must exist on disk, the same guarantee LANES already has.
```

---

### Task 2: `lane3_direct_imports()` — the "widely-used code" criterion, made mechanical

**Files:**
- Create: `tools/kanban_sync/review_tier.py`
- Create: `tests/test_kanban_sync_review_tier.py`

**Interfaces:**
- Consumes: `labels.LANES`, `labels.resolve_lane_package`,
  `labels.REVIEW_TIER_A_PATHS` (Task 1)
- Produces: `review_tier.lane3_direct_imports(repo_root: Path) -> set[str]` —
  repo-relative paths (`"services/app_state.py"`, `"services/exits/"`), not
  dotted names

- [ ] **Step 1: Write the failing tests**

Create `tests/test_kanban_sync_review_tier.py`:

```python
from pathlib import Path

from tools.kanban_sync import labels, review_tier

_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_lane3_direct_imports_finds_a_from_import(tmp_path):
    (tmp_path / "services").mkdir(parents=True)
    (tmp_path / "services" / "app_state.py").write_text("STATE = {}\n")
    (tmp_path / "services" / "strategy_engine.py").write_text(
        "from services.app_state import STATE\nimport os\n"
    )
    assert review_tier.lane3_direct_imports(tmp_path) == {"services/app_state.py"}


def test_lane3_direct_imports_finds_a_dotted_module_and_a_package(tmp_path):
    (tmp_path / "services" / "position").mkdir(parents=True)
    (tmp_path / "services" / "position" / "account_positions.py").write_text("")
    (tmp_path / "services" / "exits").mkdir(parents=True)
    (tmp_path / "services" / "exits" / "__init__.py").write_text("")
    (tmp_path / "services" / "strategy_engine.py").write_text(
        "import services.position.account_positions\nfrom services.exits import run\n"
    )
    assert review_tier.lane3_direct_imports(tmp_path) == {
        "services/position/account_positions.py", "services/exits/",
    }


def test_lane3_direct_imports_ignores_a_commented_out_import(tmp_path):
    (tmp_path / "services").mkdir(parents=True)
    (tmp_path / "services" / "app_state.py").write_text("")
    (tmp_path / "services" / "strategy_engine.py").write_text(
        "# from services.app_state import STATE\n"
    )
    assert review_tier.lane3_direct_imports(tmp_path) == set()


def test_every_lane3_direct_import_is_covered_by_review_tier_a_paths():
    """Spec §3.1: a module Lane 3 imports directly is Tier A, whatever lane it
    nominally belongs to. Run against the real repo, so a future import of a
    Tier B module from strategy/risk/execution code fails CI here rather than
    shipping as a Tier B PR that changes trading behaviour."""
    uncovered = sorted(
        target for target in review_tier.lane3_direct_imports(_REPO_ROOT)
        if not any(target == p or target.startswith(p) for p in labels.REVIEW_TIER_A_PATHS)
    )
    assert not uncovered, f"Lane 3 imports these, but they are not Tier A: {uncovered}"


def test_lane3_direct_imports_on_the_real_repo_finds_app_state():
    """A liveness check on the scan itself: if the regex or the LANES[3]
    resolution breaks, the coverage test above passes vacuously on an empty
    set. services/strategy_engine.py imports app_state today."""
    targets = review_tier.lane3_direct_imports(_REPO_ROOT)
    assert "services/app_state.py" in targets
    assert len(targets) >= 10
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_kanban_sync_review_tier.py -v`
Expected: FAIL — `ImportError: cannot import name 'review_tier' from 'tools.kanban_sync'`.

- [ ] **Step 3: Create `tools/kanban_sync/review_tier.py`**

```python
"""Review-tier classification (docs/archive/lane-9-tooling-ci-process-governance/
specs/2026-09-07-ai-assisted-engineering-principles-design.md §3.1, §6.2).

Pure by design: every input - the changed-file list, the diff text, the labels,
the comment bodies - is passed in by the caller, so the whole boundary is
testable without a network call. tools/kanban_sync/__main__.py does the
fetching. The data (which paths, which suffixes, which pattern) lives in
labels.py; only the rules live here.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

from tools.kanban_sync import labels

_LANE3_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+services\.([A-Za-z0-9_.]+)|import\s+services\.([A-Za-z0-9_.]+))",
    re.M,
)


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


def lane3_direct_imports(repo_root: Path) -> set[str]:
    """Repo-relative paths of every module a Lane 3 source imports directly.

    The 2026-09-03 memory's "widely-used code deserves the deeper review"
    criterion, made mechanical: strategy, risk, and execution are where a
    defect costs money, so what they depend on is Tier A too - computed from
    the source on every run, not from a list someone must remember to update.
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
            for match in _LANE3_IMPORT_RE.finditer(source.read_text()):
                resolved = _resolve_import_target(repo_root, match.group(1) or match.group(2))
                if resolved is not None:
                    targets.add(resolved)
    return targets
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_kanban_sync_review_tier.py -v`
Expected: PASS (5 tests). If
`test_every_lane3_direct_import_is_covered_by_review_tier_a_paths` fails, do
not loosen the test — add the named path to `_REVIEW_TIER_A_EXTRA_PATHS` in
`labels.py`. That finding is what the test exists to produce.

- [ ] **Step 5: Commit**

Stage `tools/kanban_sync/review_tier.py` and
`tests/test_kanban_sync_review_tier.py`, then commit with:

```
feat: scan Lane 3's direct imports so its dependencies are Tier A too

A module strategy/risk/execution imports directly is Tier A whatever lane it
nominally sits in. Computed from source on every CI run rather than kept as a
list someone has to remember to update.
```

---

### Task 3: `review_tier()` — the classifier

**Files:**
- Modify: `tools/kanban_sync/review_tier.py` (append)
- Modify: `tests/test_kanban_sync_review_tier.py` (append)

**Interfaces:**
- Consumes: every `labels.REVIEW_TIER_A_*` constant (Task 1)
- Produces: `review_tier.review_tier(files: Sequence[str], *, diff_text: str = "",
  pr_labels: Sequence[str] = (), escalate: bool = False) -> tuple[str, list[str]]`
  — returns `("A" | "B", reasons)`, where each reason is a human-readable
  string naming the rule that fired and the path or line that fired it

**Verification note (already run, 2026-09-07):** this exact rule set was
simulated over the recorded 200-PR window before the plan was written. It
reproduces spec §3.2's code-typed split (68 A / 16 B) and unreviewed split
(23 A / 7 B, the same seven numbers) **exactly**, and the test-stem rule moves
zero PRs, as §3.2 claims. The overall count comes out 142/58 rather than
141/59 for one reason that is not a rule difference: the recorded window ends
at #663 (merged during the adversarial review) and therefore starts at #255,
where the spec's window ends at #662 and starts at #254 — and #254 is a
docs-only PR (`docs/next-action.md`, `docs/open-decisions.md`) that is Tier B,
swapped for #663, which is Tier A through the pipeline-directory prose rule.
Step 4's expected values below are the ones this rule set actually produces.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_kanban_sync_review_tier.py`:

```python
# Real changed-file lists, taken from the merged PRs themselves (spec §3.2 and
# §7). These are the fixture corpus: if the boundary ever reclassifies one of
# them, that is a decision someone has to make on purpose.
def test_tier_b_the_seven_unreviewed_low_blast_radius_prs():
    """The seven PRs from the research's unreviewed-30 list that the boundary
    genuinely excuses: a test tightening, a backup-overlap guard, a container
    image line, a gitignore, a test flush-race, a comment fix, a one-off
    backfill tool. Nothing here touches trading, money, or the data plane."""
    corpus = {
        301: ["tests/test_e2e_terminal_static_and_api.py"],
        308: ["services/backup/backup.py", "services/backup/routes.py",
              "tests/test_backup.py", "tests/test_backup_routes.py"],
        415: ["Dockerfile", "docs/open-decisions.md"],
        445: [".gitignore"],
        498: ["tests/test_index_feed_backfill.py"],
        623: ["services/diagnostics/routes.py"],
        273: ["static/project-manifest.json", "tests/test_historical_data_backfill.py",
              "tools/historical_data_backfill.py"],
    }
    for number, files in corpus.items():
        tier, reasons = review_tier.review_tier(files)
        assert tier == "B", f"PR #{number} should be Tier B, got A because {reasons}"


def test_tier_a_via_main_py_and_its_test():
    tier, reasons = review_tier.review_tier(["main.py", "tests/test_main_scheduler_loops.py"])
    assert tier == "A"
    assert any("main.py" in r for r in reasons)


def test_tier_a_via_lane_1_signal_source():
    tier, _ = review_tier.review_tier([
        "services/series_evaluator.py", "services/trade_category.py",
        "tests/test_series_evaluator.py", "tests/test_trade_category.py",
    ])
    assert tier == "A"


def test_tier_a_via_the_money_ui():
    tier, _ = review_tier.review_tier([
        "frontend/src/js/screener-and-header.js", "frontend/src/js/shared-utils.js",
        "static/css/dashboard.css",
    ])
    assert tier == "A"


def test_a_cheatsheet_inside_a_tier_a_package_alone_is_tier_b():
    """Spec §3.1 rule 2's stated consequence: prose under a Tier A directory
    does not fire rule 1. In the 200-PR window this moves no PR - it is here so
    the behaviour is a decision rather than an accident."""
    tier, _ = review_tier.review_tier(["services/kalshi/CHEATSHEET.md"])
    assert tier == "B"


def test_the_rule_files_are_tier_a_whatever_their_suffix():
    for path in (
        "CLAUDE.md", ".claude/rules/branching-and-ci.md",
        ".claude/skills/checkpoint/SKILL.md",
        "docs/superpowers/plans/2026-09-01-x.md",
        "docs/archive/lane-4-analytics-advisory-research/specs/2026-09-01-x-design.md",
    ):
        tier, reasons = review_tier.review_tier([path])
        assert tier == "A", f"{path} should be Tier A"
        assert any("prose" in r for r in reasons)


def test_an_archived_lane_doc_outside_the_pipeline_dirs_is_tier_b():
    """Spec D9: a claim-asserting document outside research/specs/plans is Tier
    B unless escalated - it is misplaced first and under-reviewed second."""
    tier, _ = review_tier.review_tier([
        "docs/archive/lane-9-tooling-ci-process-governance/README.md"
    ])
    assert tier == "B"


def test_a_data_model_line_in_a_lane_4_file_is_tier_a():
    """Spec D12: the data model is Tier A wherever it changes. services/
    analytics/ is Lane 4 and on no path list."""
    diff = (
        "diff --git a/services/analytics/store.py b/services/analytics/store.py\n"
        "--- a/services/analytics/store.py\n"
        "+++ b/services/analytics/store.py\n"
        "@@ -1,3 +1,5 @@\n"
        "+CREATE TABLE IF NOT EXISTS whale_rollup (id INTEGER PRIMARY KEY)\n"
    )
    tier, reasons = review_tier.review_tier(["services/analytics/store.py"], diff_text=diff)
    assert tier == "A"
    assert any("data model" in r for r in reasons)


def test_a_data_model_string_in_an_unchanged_context_line_does_not_fire():
    """Only added or removed lines count: a matching line that merely sits in
    the diff's context is not a change to the data model."""
    diff = (
        "@@ -1,3 +1,4 @@\n"
        " CREATE TABLE IF NOT EXISTS whale_rollup (id INTEGER PRIMARY KEY)\n"
        "+LOG = logging.getLogger(__name__)\n"
    )
    tier, _ = review_tier.review_tier(["services/analytics/store.py"], diff_text=diff)
    assert tier == "B"


def test_the_hotpath_label_alone_is_tier_a():
    tier, reasons = review_tier.review_tier(
        ["services/quality/report.py"], pr_labels=[labels.CONCERN_HOTPATH]
    )
    assert tier == "A"
    assert any(labels.CONCERN_HOTPATH in r for r in reasons)


def test_escalation_makes_any_pr_tier_a_and_says_so():
    tier, reasons = review_tier.review_tier([".gitignore"], escalate=True)
    assert tier == "A"
    assert any("escalat" in r for r in reasons)


def test_no_rule_moves_a_pr_from_a_to_b():
    """Spec §3.1 rule 5: escalation is upward-only. There is no de-escalation
    parameter, and adding a Tier B file to a Tier A PR cannot lower it."""
    tier, _ = review_tier.review_tier(["services/risk_manager.py", ".gitignore"])
    assert tier == "A"


def test_a_test_of_a_tier_a_module_is_tier_a_but_the_tests_dir_is_not():
    """Spec D8. `tests/test_<stem>*.py` for a Tier A stem, prefix-matched at an
    underscore boundary - not any test file mentioning the stem anywhere, which
    would pull in tests/test_index_feed_backfill.py (PR #498, Tier B) through
    services/index_feed/backfill.py."""
    assert review_tier.review_tier(["tests/test_strategy_engine_gate.py"])[0] == "A"
    assert review_tier.review_tier(["tests/test_risk_manager.py"])[0] == "A"
    assert review_tier.review_tier(["tests/test_index_feed_backfill.py"])[0] == "B"
    assert review_tier.review_tier(["tests/test_e2e_terminal_static_and_api.py"])[0] == "B"


def test_an_extensionless_file_under_a_tier_a_path_is_code():
    """scripts/woodpecker-status carries no suffix; the suffix list exists to
    exclude prose, and an extensionless executable is not prose. Measured
    before adopting: the only extensionless file anywhere in the 200-PR window
    is Dockerfile, which is under no Tier A path, so §3.2's counts stand."""
    assert review_tier.review_tier(["scripts/woodpecker-status"])[0] == "A"
    assert review_tier.review_tier(["Dockerfile"])[0] == "B"


def test_reasons_name_the_rule_and_the_path_that_fired_it():
    """The output is pasted into a PR comment as the record of the decision, so
    a reason has to be readable on its own."""
    _, reasons = review_tier.review_tier(["services/risk_manager.py"])
    assert reasons == ["path: services/risk_manager.py (under services/risk_manager.py)"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_kanban_sync_review_tier.py -v`
Expected: FAIL — `AttributeError: module 'tools.kanban_sync.review_tier' has no attribute 'review_tier'` on all fifteen new tests.

- [ ] **Step 3: Implement the classifier**

Append to `tools/kanban_sync/review_tier.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_kanban_sync_review_tier.py -v`
Expected: PASS (20 tests: 5 from Task 2, 15 here).

- [ ] **Step 5: Commit**

Stage `tools/kanban_sync/review_tier.py` and
`tests/test_kanban_sync_review_tier.py`, then commit with:

```
feat: classify a PR as Tier A or Tier B from the paths it touches

Five rules, in the spec's order: Tier A code/config paths, prose that is
always Tier A, a data-model line in the diff, concern:hotpath, and upward-only
escalation. The fixture corpus is real changed-file lists from merged PRs, so
a reclassification has to be somebody's decision rather than a silent drift.
```

---

### Task 4: counting persisted review artifacts

**Files:**
- Modify: `tools/kanban_sync/review_tier.py` (append)
- Create: `tests/fixtures/review_artifact_first_lines.tsv`
- Modify: `tests/test_kanban_sync_review_tier.py` (append)

**Interfaces:**
- Consumes: nothing new
- Produces: `review_tier.REVIEW_ARTIFACT_FIRST_LINE: re.Pattern`;
  `review_tier.count_review_artifacts(comments: Sequence[str], files:
  Sequence[str]) -> tuple[int, list[str]]` — returns the count and the list of
  matched labels (a comment's first line, or a filename)

This is P3's whole mechanism: *a review that is not a persisted artifact did
not happen.* It counts presence, not independence — spec D11: every comment in
the window is by one GitHub login, so a fabricated adversarial pass is not
mechanically detectable. The failure that actually recurred four times is
absence, and that is what this catches.

- [ ] **Step 1: Generate the fixture snapshot**

The regex has to be validated against what this repo's review comments
actually look like, not against what they ought to look like — the stricter
`^#+\s*(self-review|…)` form proposed in the spec's first revision matched 170
of 261 and rejected the fully compliant PR #625.

```bash
gh pr list --repo thesneakattack/kalshi-whale-poc --state merged --limit 200 \
  --json number --jq '.[].number' > /tmp/review-fixture-prs.txt
: > tests/fixtures/review_artifact_first_lines.tsv
while read -r n; do
  gh pr view "$n" --repo thesneakattack/kalshi-whale-poc --json comments \
    --jq ".comments[] | \"${n}\t\" + ((.body | split(\"\n\"))[0] | gsub(\"\t\"; \" \"))" \
    >> tests/fixtures/review_artifact_first_lines.tsv
done < /tmp/review-fixture-prs.txt
wc -l tests/fixtures/review_artifact_first_lines.tsv
```

This is a **snapshot**, committed once and then frozen — the numbers in step 3
are asserted against this file, not against the live repo, so the test does
not drift as new PRs are merged. Measured while writing this plan over the
same 200-PR window: **261 records, 224 matching, 37 not**, and every one of
the 37 is a CI re-trigger, a correction, a checkpoint, a fix-list recheck, or
a scope amendment — no review artifact among them. If the regenerated snapshot
differs (comments landed since), record the new totals in the test and say the
new figures in the commit message; do not adjust the regex to hit a number.

Add a first line to the file, before committing it, marking what it is:

```
# snapshot 2026-09-07: first line of every comment on the 200 most recently
# merged PRs. Frozen test data for REVIEW_ARTIFACT_FIRST_LINE - regenerate
# deliberately, never to make a test pass.
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_kanban_sync_review_tier.py`:

```python
_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "review_artifact_first_lines.tsv"


def _fixture_first_lines() -> list[str]:
    lines = []
    for raw in _FIXTURE.read_text().splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        parts = raw.split("\t", 1)
        lines.append(parts[1] if len(parts) > 1 else parts[0])
    return lines


def test_review_artifact_pattern_matches_this_repos_real_artifact_headings():
    """The forms this repo actually posts, taken verbatim from merged PRs."""
    for line in (
        "## Self-review",
        "## Consolidation",
        "## Independent adversarial review",
        "**Consolidation — GO**",
        "**Self-review (lean, per PR #587)**",
        "**Adversarial review** (independent Agent-tool call, no session memory)",
        "## PR-stage adversarial review (fresh Agent, no session memory)",
        "## Dispatching-session self-review (lean)",
        "Tier B self-review",
    ):
        assert review_tier.REVIEW_ARTIFACT_FIRST_LINE.search(line), line


def test_review_artifact_pattern_rejects_the_comments_that_are_not_artifacts():
    for line in (
        "Re-triggering CI: required pr/* contexts never posted",
        "## Fix-list recheck — all 16 items confirmed, not just claimed",
        "## Checkpoint — fleet-wide pause (David, 3.5h), stopping here",
        "## Production-scale equivalence check (read-only, no writes)",
    ):
        assert not review_tier.REVIEW_ARTIFACT_FIRST_LINE.search(line), line


def test_review_artifact_pattern_against_the_recorded_snapshot():
    """Frozen data (tests/fixtures/review_artifact_first_lines.tsv): 261 real
    comment first lines from the 200 most recently merged PRs as of
    2026-09-07. The stricter heading-anchored form the spec's first revision
    proposed matched 170 of these and rejected the fully compliant PR #625."""
    lines = _fixture_first_lines()
    matched = [ln for ln in lines if review_tier.REVIEW_ARTIFACT_FIRST_LINE.search(ln)]
    assert len(lines) == 261
    assert len(matched) == 224


def test_count_review_artifacts_uses_only_the_first_line_of_a_comment():
    """A PR body or comment that *narrates* a review in its prose counts for
    nothing (spec D2): 12 of the 24 unreviewed code PRs in the research did
    exactly that."""
    comments = [
        "## Self-review\n\nfindings: none",
        "Merging now — the adversarial review found nothing worth blocking on.",
    ]
    count, matched = review_tier.count_review_artifacts(comments, [])
    assert count == 1
    assert matched == ["## Self-review"]


def test_count_review_artifacts_counts_review_named_files_in_the_diff():
    """A planning-pipeline PR carries its artifacts as committed documents
    rather than comments; both forms count, and only files in this PR's own
    diff are counted."""
    files = [
        "docs/archive/lane-9-tooling-ci-process-governance/research/x.md",
        "docs/archive/lane-9-tooling-ci-process-governance/research/x-self-review.md",
        "docs/archive/lane-9-tooling-ci-process-governance/research/x-adversarial-review.md",
        "docs/archive/lane-9-tooling-ci-process-governance/research/x-consolidation.md",
    ]
    count, matched = review_tier.count_review_artifacts([], files)
    assert count == 3
    assert all("x.md" != m for m in matched)


def test_count_review_artifacts_does_not_double_count_one_comment():
    count, _ = review_tier.count_review_artifacts(
        ["## Self-review and adversarial review and consolidation"], []
    )
    assert count == 1


def test_count_review_artifacts_ignores_an_empty_or_whitespace_comment():
    count, _ = review_tier.count_review_artifacts(["", "   \n\n"], [])
    assert count == 0
```

- [ ] **Step 3: Implement the counter**

Append to `tools/kanban_sync/review_tier.py`:

```python
# A review artifact is recognised by the first line of its comment. Validated
# against tests/fixtures/review_artifact_first_lines.tsv - 261 real comment
# first lines, 224 matched, and every one of the 37 misses is a CI re-trigger,
# a correction, a checkpoint, a fix-list recheck, or a scope amendment. The
# stricter `^#+\s*(self-review|...)` form this replaced matched 170 and
# rejected PR #625, which was fully compliant.
REVIEW_ARTIFACT_FIRST_LINE = re.compile(
    r"\b(self[-\s]?review|adversarial|consolidation)\b", re.IGNORECASE
)
_ARTIFACT_FILENAME = re.compile(
    r"(self-review|adversarial-review|consolidation)", re.IGNORECASE
)


def count_review_artifacts(
    comments: Sequence[str], files: Sequence[str]
) -> tuple[int, list[str]]:
    """How many persisted review artifacts this PR carries.

    Two forms count: a PR comment whose *first line* names itself as one, and a
    review-named document added or changed in this PR's own diff (a planning-
    pipeline PR commits its artifacts rather than commenting them). Only the
    first line of a comment is read - a body that merely narrates a review in
    its prose is not an artifact (spec D2), which is what stood in for a review
    on 12 of the 24 unreviewed code PRs the research found.

    What this cannot do (spec D11): tell an independent Agent's comment from
    the author's. Every comment in the measured window is by the same GitHub
    login. Independence rests on the session's honesty, as the rule already
    does; this catches absence, the failure that actually recurred.
    """
    matched: list[str] = []
    for body in comments:
        stripped = (body or "").strip()
        if not stripped:
            continue
        first_line = stripped.splitlines()[0]
        if REVIEW_ARTIFACT_FIRST_LINE.search(first_line):
            matched.append(first_line)
    for path in files:
        if _ARTIFACT_FILENAME.search(path.rsplit("/", 1)[-1]):
            matched.append(path)
    return len(matched), matched
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_kanban_sync_review_tier.py -v`
Expected: PASS (27 tests). If
`test_review_artifact_pattern_against_the_recorded_snapshot` fails on the
counts, the snapshot differs from the one measured here — update both numbers
from the new file and say so in the commit message, rather than changing the
regex.

- [ ] **Step 5: Commit**

Stage `tools/kanban_sync/review_tier.py`,
`tests/fixtures/review_artifact_first_lines.tsv`, and
`tests/test_kanban_sync_review_tier.py`, then commit with:

```
feat: count persisted review artifacts from comment first lines

A review that is not a persisted artifact did not happen. The pattern is
validated against a frozen snapshot of 261 real comment first lines (224
match; the 37 that don't are CI re-triggers, corrections, checkpoints and
rechecks) rather than against what a heading ought to look like - the stricter
form matched 170 and rejected a fully compliant PR.
```

---

### Task 5: read-only PR readers on `GithubClient`

**Files:**
- Modify: `tools/kanban_sync/github_client.py` (append four methods after `find_pr_state`)
- Modify: `tests/test_kanban_sync_github_client.py` (append)

**Interfaces:**
- Consumes: `GithubClient._run` / `_invoke` (existing retry wrapper)
- Produces: `get_pr_files(number) -> list[str]`; `get_pr_diff(number) -> str`;
  `get_pr_labels(number) -> frozenset[str]`; `list_pr_comments(number) ->
  list[str]`

Four readers, not the six the spec's §6.2 lists: `get_pr_meta` and
`list_merged_prs` existed only for the `outcomes` report, which is cut from
this plan (see "Deviations", item 5). Nothing here is speculative API — every
one of the four is called by Task 6.

Two shapes matter here and were both verified live on 2026-09-07:

- `gh pr view --json files` caps at 100 entries; PR #660 changed 124. The
  files reader therefore goes through the REST endpoint with `--paginate`,
  which returned all 124.
- `gh api` takes no `--repo` flag, so it cannot use `_run` (which appends
  one). It goes through `_invoke` directly, exactly as `graphql_rate_limit`
  already does, keeping the transient-retry policy.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_kanban_sync_github_client.py`:

```python
def test_get_pr_files_uses_rest_pagination_not_the_capped_json_field():
    """`gh pr view --json files` caps at 100; PR #660 changed 124 files
    (verified live 2026-09-07). A capped list silently under-reports the tier,
    which is the one failure mode this reader must not have."""
    runner = FakeRunner()
    runner.queue("\n".join(f"services/file_{i}.py" for i in range(124)))
    client = GithubClient(REPO, runner=runner)

    files = client.get_pr_files(660)

    assert len(files) == 124
    call = runner.calls[0]
    assert call[:2] == ["gh", "api"]
    assert "repos/thesneakattack/kalshi-whale-poc/pulls/660/files" in call
    assert "--paginate" in call
    assert "--repo" not in call  # gh api rejects it


def test_get_pr_files_raises_rather_than_returning_an_empty_list_on_failure():
    """An empty file list classifies as Tier B. A fetch failure must never be
    indistinguishable from a PR that changed nothing."""
    runner = FakeRunner()
    runner.queue("", returncode=1, stderr="HTTP 404: Not Found")
    client = GithubClient(REPO, runner=runner)

    with pytest.raises(GithubCliError):
        client.get_pr_files(99999)


def test_get_pr_diff_returns_the_raw_diff():
    runner = FakeRunner()
    runner.queue("diff --git a/x.py b/x.py\n+CREATE TABLE t (id INTEGER)\n")
    client = GithubClient(REPO, runner=runner)

    assert "CREATE TABLE" in client.get_pr_diff(1)
    assert runner.calls[0][:3] == ["gh", "pr", "diff"]


def test_get_pr_labels_includes_the_labels_of_a_closing_issue():
    """Spec §3.1 rule 4: the PR *or an issue it closes* carrying
    concern:hotpath makes it Tier A - a code PR often carries no label of its
    own while the issue it closes carries the concern."""
    runner = FakeRunner()
    runner.queue(json.dumps({
        "labels": [{"name": "lane:3"}],
        "closingIssuesReferences": [{"number": 410}],
    }))
    runner.queue(json.dumps({
        "number": 410, "state": "OPEN",
        "labels": [{"name": "concern:hotpath"}],
    }))
    client = GithubClient(REPO, runner=runner)

    assert client.get_pr_labels(1) == frozenset({"lane:3", "concern:hotpath"})


def test_list_pr_comments_returns_bodies_in_order():
    runner = FakeRunner()
    runner.queue(json.dumps({"comments": [
        {"body": "## Self-review\n..."},
        {"body": "## Independent adversarial review\n..."},
    ]}))
    client = GithubClient(REPO, runner=runner)

    assert client.list_pr_comments(1) == [
        "## Self-review\n...", "## Independent adversarial review\n...",
    ]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_kanban_sync_github_client.py -v -k "pr_files or pr_diff or pr_labels or pr_comments"`
Expected: FAIL — `AttributeError: 'GithubClient' object has no attribute 'get_pr_files'`.

- [ ] **Step 3: Implement the readers**

Insert into `tools/kanban_sync/github_client.py` after `find_pr_state`:

```python
    # --- read-only PR readers for `review-tier` (the AI-assisted engineering
    # principles design, §6.2). None of these writes anything.

    def get_pr_files(self, number: int) -> list[str]:
        """Every changed path in the PR.

        REST plus --paginate, not `gh pr view --json files`: that field caps at
        100 entries and PR #660 changed 124 (verified live 2026-09-07). A
        truncated file list would silently classify a large PR as Tier B, which
        is the one failure this reader must not have. `gh api` takes no --repo
        flag, so this goes through _invoke rather than _run, exactly as
        graphql_rate_limit does - the transient-retry policy still applies.
        """
        result = self._invoke([
            "gh", "api", f"repos/{self._repo}/pulls/{number}/files",
            "--paginate", "--jq", ".[].filename",
        ])
        if result.returncode != 0:
            raise GithubCliError(
                f"gh api pulls/{number}/files failed: {result.stderr or result.stdout}"
            )
        return [line for line in result.stdout.splitlines() if line.strip()]

    def get_pr_diff(self, number: int) -> str:
        """The raw unified diff, for the data-model rule's line pattern."""
        return self._run(["pr", "diff", str(number)])

    def get_pr_labels(self, number: int) -> frozenset[str]:
        """The PR's own labels plus those of every issue it closes: a code PR
        often carries no label while the issue it closes carries the concern."""
        stdout = self._run([
            "pr", "view", str(number), "--json", "labels,closingIssuesReferences",
        ])
        data = json.loads(stdout)
        names = {label["name"] for label in data.get("labels") or []}
        for issue in data.get("closingIssuesReferences") or []:
            state = self.get_issue(issue["number"])
            if state is not None:
                names |= set(state.labels)
        return frozenset(names)

    def list_pr_comments(self, number: int) -> list[str]:
        """Comment bodies in posted order. Issue-style comments only - the
        review artifacts this repo posts are all of that kind."""
        stdout = self._run(["pr", "view", str(number), "--json", "comments"])
        return [comment["body"] for comment in json.loads(stdout)["comments"]]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_kanban_sync_github_client.py -v`
Expected: PASS (the existing tests plus the five new ones).

- [ ] **Step 5: Commit**

Stage `tools/kanban_sync/github_client.py` and
`tests/test_kanban_sync_github_client.py`, then commit with:

```
feat: add four read-only PR readers to the kanban_sync gh client

Files, diff, labels (including a closing issue's), and comments. Files go
through REST with --paginate because the `--json files` field caps at 100 and a
124-file PR would otherwise classify as Tier B on a truncated list.
```

---

### Task 6: the `review-tier` subcommand

**Files:**
- Modify: `tools/kanban_sync/__main__.py` (new `_cmd_review_tier`, new subparser)
- Modify: `tests/test_kanban_sync_main.py` (append)

**Interfaces:**
- Consumes: `review_tier.review_tier`, `review_tier.count_review_artifacts`
  (Tasks 3–4); `GithubClient.get_pr_files` / `get_pr_diff` / `get_pr_labels` /
  `list_pr_comments` (Task 5)
- Produces: `python -m tools.kanban_sync review-tier --pr N [--exempt
  "<reason>"] [--tier A] [--json]`; `cli._cmd_review_tier(args) -> None`

This is the command the rule text in Task 7 names, so its flags and its exit
codes are load-bearing: `0` on `PASS`/`EXEMPT`, `1` on `FAIL`, `2` on a fetch
error. It must never print `PASS` when it could not read the PR — a silent
pass is worse than no check.

It does **not** call `_check_project_scope()`: that gate exists for the
Projects V2 board calls, and nothing here touches the board.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_kanban_sync_main.py` (add `import json` and
`from tools.kanban_sync.github_client import GithubCliError` to its imports):

```python
class _FakeReviewClient:
    def __init__(self, files, diff="", labels=frozenset(), comments=()):
        self._files, self._diff = files, diff
        self._labels, self._comments = labels, list(comments)

    def get_pr_files(self, number): return list(self._files)
    def get_pr_diff(self, number): return self._diff
    def get_pr_labels(self, number): return self._labels
    def list_pr_comments(self, number): return list(self._comments)


def test_review_tier_passes_a_tier_a_pr_with_three_artifacts(monkeypatch, capsys):
    monkeypatch.setattr(cli, "GithubClient", lambda repo: _FakeReviewClient(
        ["services/risk_manager.py"],
        comments=["## Self-review\nx", "## Adversarial review\nx", "## Consolidation — GO\nx"],
    ))
    args = argparse.Namespace(pr=1, exempt=None, tier=None, json=False)

    cli._cmd_review_tier(args)

    out = capsys.readouterr().out
    assert "Tier A" in out and "PASS" in out
    assert "3 of 3" in out


def test_review_tier_fails_a_tier_a_pr_with_one_artifact(monkeypatch, capsys):
    monkeypatch.setattr(cli, "GithubClient", lambda repo: _FakeReviewClient(
        ["services/risk_manager.py"], comments=["## Self-review\nx"],
    ))
    args = argparse.Namespace(pr=1, exempt=None, tier=None, json=False)

    with pytest.raises(SystemExit) as exc:
        cli._cmd_review_tier(args)

    assert exc.value.code == 1
    assert "FAIL" in capsys.readouterr().out


def test_review_tier_passes_a_tier_b_pr_with_one_artifact(monkeypatch, capsys):
    monkeypatch.setattr(cli, "GithubClient", lambda repo: _FakeReviewClient(
        ["services/diagnostics/routes.py"], comments=["Tier B self-review\n..."],
    ))
    args = argparse.Namespace(pr=1, exempt=None, tier=None, json=False)

    cli._cmd_review_tier(args)

    out = capsys.readouterr().out
    assert "Tier B" in out and "PASS" in out


def test_review_tier_fails_a_tier_b_pr_whose_only_review_is_narrated_in_prose(
    monkeypatch, capsys
):
    """The failure this command exists for: text that describes a review
    instead of being one. 12 of the 24 unreviewed code PRs did exactly that."""
    monkeypatch.setattr(cli, "GithubClient", lambda repo: _FakeReviewClient(
        ["services/diagnostics/routes.py"],
        comments=["Merging - I reviewed this carefully and CI is green."],
    ))
    args = argparse.Namespace(pr=1, exempt=None, tier=None, json=False)

    with pytest.raises(SystemExit) as exc:
        cli._cmd_review_tier(args)

    assert exc.value.code == 1


def test_review_tier_exempt_records_the_reason_and_requires_no_artifact(monkeypatch, capsys):
    monkeypatch.setattr(cli, "GithubClient", lambda repo: _FakeReviewClient([]))
    args = argparse.Namespace(pr=1, exempt="CI re-trigger, no diff", tier=None, json=False)

    cli._cmd_review_tier(args)

    out = capsys.readouterr().out
    assert "EXEMPT" in out and "CI re-trigger, no diff" in out


def test_review_tier_rejects_an_empty_exemption_reason(monkeypatch):
    monkeypatch.setattr(cli, "GithubClient", lambda repo: _FakeReviewClient([]))
    args = argparse.Namespace(pr=1, exempt="   ", tier=None, json=False)

    with pytest.raises(SystemExit) as exc:
        cli._cmd_review_tier(args)

    assert exc.value.code == 1


def test_review_tier_escalation_overrides_a_computed_b(monkeypatch, capsys):
    monkeypatch.setattr(cli, "GithubClient", lambda repo: _FakeReviewClient(
        ["services/diagnostics/routes.py"],
        comments=["## Self-review\nx", "## Adversarial\nx", "## Consolidation\nx"],
    ))
    args = argparse.Namespace(pr=1, exempt=None, tier="A", json=False)

    cli._cmd_review_tier(args)

    out = capsys.readouterr().out
    assert "Tier A" in out and "escalat" in out


def test_review_tier_exits_2_on_a_fetch_failure_and_never_prints_pass(monkeypatch, capsys):
    """A silent PASS on an unreadable PR is worse than no check at all."""
    class _Broken:
        def get_pr_files(self, number):
            raise GithubCliError("HTTP 502: Bad Gateway")

    monkeypatch.setattr(cli, "GithubClient", lambda repo: _Broken())
    args = argparse.Namespace(pr=1, exempt=None, tier=None, json=False)

    with pytest.raises(SystemExit) as exc:
        cli._cmd_review_tier(args)

    assert exc.value.code == 2
    assert "PASS" not in capsys.readouterr().out


def test_review_tier_json_output_carries_the_decision_fields(monkeypatch, capsys):
    monkeypatch.setattr(cli, "GithubClient", lambda repo: _FakeReviewClient(
        ["services/risk_manager.py"], comments=["## Self-review\nx"],
    ))
    args = argparse.Namespace(pr=7, exempt=None, tier=None, json=True)

    with pytest.raises(SystemExit):
        cli._cmd_review_tier(args)

    payload = json.loads(capsys.readouterr().out)
    assert payload["pr"] == 7
    assert payload["tier"] == "A"
    assert payload["required"] == 3
    assert payload["artifacts"] == 1
    assert payload["verdict"] == "FAIL"


def test_review_tier_subcommand_requires_a_pr_number():
    with pytest.raises(SystemExit):
        cli.main(["review-tier"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_kanban_sync_main.py -v -k review_tier`
Expected: FAIL — `AttributeError: module 'tools.kanban_sync.__main__' has no attribute '_cmd_review_tier'`.

- [ ] **Step 3: Implement the subcommand**

Replace the existing import line
`from tools.kanban_sync.github_client import GithubClient` in
`tools/kanban_sync/__main__.py` with:

```python
from tools.kanban_sync.github_client import GithubClient, GithubCliError
from tools.kanban_sync.review_tier import count_review_artifacts, review_tier
```

Add the command function after `_cmd_plan_candidates`:

```python
_REVIEW_TIER_REQUIREMENT = {"A": 3, "B": 1}


def _cmd_review_tier(args: argparse.Namespace) -> None:
    """The merge-time check (the AI-assisted engineering principles design §4).

    Decides Tier A/B from the PR's paths, diff, and labels, counts its
    persisted review artifacts, and prints PASS / FAIL / EXEMPT. The exit code
    is the machine-readable answer: 0 pass or exempt, 1 fail, 2 could-not-read.
    Never prints PASS on a fetch failure - a silent pass is worse than no
    check. Touches nothing on the Projects board, so no project scope needed.
    """
    if args.exempt is not None:
        reason = args.exempt.strip()
        if not reason:
            print("error: --exempt needs a non-empty reason", file=sys.stderr)
            sys.exit(1)
        _emit_review_tier(
            args, tier="exempt", reasons=[f"mechanical/trivial: {reason}"],
            artifacts=0, matched=[], required=0, verdict="EXEMPT",
        )
        return

    client = GithubClient(REPO)
    try:
        files = client.get_pr_files(args.pr)
        diff_text = client.get_pr_diff(args.pr)
        pr_labels = sorted(client.get_pr_labels(args.pr))
        comments = client.list_pr_comments(args.pr)
    except GithubCliError as exc:
        print(f"error: could not read PR #{args.pr}: {exc}", file=sys.stderr)
        sys.exit(2)

    tier, reasons = review_tier(
        files, diff_text=diff_text, pr_labels=pr_labels, escalate=args.tier == "A",
    )
    artifacts, matched = count_review_artifacts(comments, files)
    required = _REVIEW_TIER_REQUIREMENT[tier]
    verdict = "PASS" if artifacts >= required else "FAIL"
    _emit_review_tier(
        args, tier=tier, reasons=reasons, artifacts=artifacts,
        matched=matched, required=required, verdict=verdict,
    )
    if verdict == "FAIL":
        sys.exit(1)


def _emit_review_tier(args, *, tier, reasons, artifacts, matched, required, verdict):
    if args.json:
        print(json.dumps({
            "pr": args.pr, "tier": tier, "reasons": reasons,
            "artifacts": artifacts, "matched": matched,
            "required": required, "verdict": verdict,
        }, indent=2))
        return
    label = "EXEMPT" if tier == "exempt" else f"Tier {tier}"
    print(f"PR #{args.pr}: {label} — {verdict}")
    for reason in reasons:
        print(f"  why: {reason}")
    if tier != "exempt":
        print(f"  artifacts: {artifacts} of {required} required")
        for item in matched:
            print(f"    - {item}")
        if verdict == "FAIL":
            print(
                "  do not merge. Supply what is missing: a fresh Agent for an "
                "adversarial pass, the author for a Tier B self-review comment."
            )
```

Register the subparser in `main()`, after the `plan-candidates` parser:

```python
    review_tier_parser = sub.add_parser(
        "review-tier",
        help="decide a PR's review tier and count its persisted review artifacts",
    )
    review_tier_parser.add_argument("--pr", type=int, required=True)
    review_tier_parser.add_argument(
        "--exempt", default=None,
        help="mechanical/trivial change (typo, CI re-trigger, a config value "
             "edited exactly as dictated, a revert): records the reason, "
             "requires no artifact",
    )
    review_tier_parser.add_argument(
        "--tier", choices=["A"], default=None,
        help="escalate to Tier A; there is no de-escalation flag by design",
    )
    review_tier_parser.add_argument("--json", action="store_true")
    review_tier_parser.set_defaults(func=_cmd_review_tier)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_kanban_sync_main.py -v`
Expected: PASS (existing tests plus the ten new ones).

- [ ] **Step 5: Commit**

Stage `tools/kanban_sync/__main__.py` and `tests/test_kanban_sync_main.py`,
then commit with:

```
feat: add the review-tier merge-time check

One command, run before the merge: decides the tier from the PR's paths, diff
and labels, counts the persisted review artifacts, prints PASS/FAIL/EXEMPT and
exits accordingly. A fetch failure exits 2 and never prints PASS.
```

---

### Task 7: the `CLAUDE.md` rule text

**Files:**
- Modify: `CLAUDE.md` (fourteen edits, spec §5.1, anchored at `a39d5f9`)

**Interfaces:**
- Consumes: the `review-tier` command name and flags from Task 6 — the rule
  text names them, so this task must not land before that command exists
- Produces: nothing code-level

This is the deliverable the whole plan exists for. Every edit below is the
exact text; nothing is paraphrased. Re-verify each line number with
`awk 'NR==<n>' CLAUDE.md` before editing — the anchors were confirmed at
`a39d5f9` but a merge could move them.

- [ ] **Step 1: Append principle P6 to the header paragraph (line 3)**

At the end of line 3, after "…not here.", append one sentence:

> A new rule, hook, or tool names the failure it prevents and what would retire it.

- [ ] **Step 2: Tier the rule's preamble (line 38)**

Replace the phrase
"and neither does a PR carrying a claim, design decision, new logic, or a process/rule change to `main`: submission is not the final gate, merge is."
with:

> and neither does a **Tier A** PR (the Scope bullet below defines the tiers): submission is not the final gate, merge is. A Tier B PR owes one persisted self-review and green CI, never nothing.

- [ ] **Step 3: Replace the Scope bullet in full (line 40)**

> - Scope: a mechanical/trivial change (typo fix, CI re-trigger, a config value edited exactly as the user dictated, a revert) is exempt — decided first, reason recorded by `review-tier --exempt`. Everything else is **Tier A** or **Tier B** by what it touches (`REVIEW_TIER_A_PATHS` in `tools/kanban_sync/labels.py`; `python -m tools.kanban_sync review-tier --pr N` decides it; tiered by consequence by David's decision, 2026-09-07 — `git log -S'review-tier'`). Tier A — Lanes 1–3 and 7, the Kalshi/hot-path/money-UI lists, modules Lane 3 imports, `settlement_edge`/`candidate_log`/`history`, `main.py`, `db.py` and any schema change, auth, reset, `config/settings.yaml`, `.ddev/`, CLAUDE.md/rules/hooks/skills/CI, the tier definition and tests of Tier A code, `concern:hotpath`, and every planning-pipeline stage — is in scope regardless of diff size, including this rule's own PR: each stage boundary in the planning pipeline, plus the PR once, before merge — never an individual commit or push inside a branch; implementation-time work (writing the code a gated plan already called for) is covered by TDD, systematic-debugging, and verification-before-completion instead. Tier B — everything else — owes one persisted `Tier B self-review` PR comment (tier, change, evidence, falsifier, left undone) plus green CI; no adversarial pass, no consolidation. Doubt escalates to A, never down. Do not invent an extra gate at commit or push granularity.

- [ ] **Step 4: Scope the artifact requirement to Tier A (line 43)**

Replace the opening phrase
"Every in-scope stage produces its own artifact, then three more before the next stage starts"
with:

> Every planning-pipeline stage and every Tier A PR produces its own artifact, then three more before the next stage (or the merge)

- [ ] **Step 5: Add #613's decided bullet after the Consolidation bullet (after line 46)**

> - A finding first raised in an adversarial review is a new claim, not a verdict: it meets the same evidence standard (primary source, falsifier stated) before consolidation adopts it (decided 2026-09-05, #613; a wrong finding entered a consolidated report at exactly that step on 2026-09-02).

- [ ] **Step 6: Replace the in-scope-PR bullet in full (line 48)**

> - For a Tier A PR: after it's pushed and opened, one more full review cycle of the same shape (self-review, adversarial review, consolidation, each its own artifact) runs against the PR as submitted before `gh pr merge` runs. For a Tier B PR the one `Tier B self-review` comment is that cycle. `.claude/rules/branching-and-ci.md`'s "read the PR body before merging" step is a floor, not a substitute for either.

- [ ] **Step 7: Add the merge-check bullet (after line 48)**

> - Before `gh pr merge` on any PR, the merging session runs `python -m tools.kanban_sync review-tier --pr N` (with `--exempt "<reason>"` for a mechanical change, `--tier A` to escalate) and merges only on `PASS` or `EXEMPT`: Tier A needs three distinct persisted artifacts, Tier B one; a PR body that *narrates* a review counts for nothing (12 of the 24 unreviewed code PRs in the 2026-09-07 research did exactly that; the four dated recurrences behind memory `persist-code-pr-reviews-as-comments` are the same shape).

- [ ] **Step 8: Tier the lean-execution clause (line 49)**

Replace the phrase
"every required artifact — self-review, adversarial review, consolidation — still exists as its own document or PR comment, and the adversarial pass still runs from a context genuinely independent of the one that produced the artifact under review."
with:

> every artifact the tier requires — three for Tier A (self-review, adversarial review, consolidation), one for Tier B — still exists as its own document or PR comment, and a Tier A adversarial pass still runs from a context genuinely independent of the one that produced the artifact under review.

- [ ] **Step 9: Close #613's second decided edit (line 60)**

Replace from "not yet by the hook" to the end of the line with:

> deliberately session-enforced, not by the hook (decided 2026-09-05, #613: a hook cannot recognize arithmetic without firing on nearly every edit, and a nudge that always fires trains sessions to dismiss the money/probability nudge that has real incidents behind it).

- [ ] **Step 10: Fix the stale path in the safety invariants (line 100)**

Replace `services/kalshi_account_client.py` with `services/kalshi/account_client.py`
— the file moved under `services/kalshi/` and the old path does not exist
(the same migration Task 1 cleaned out of the hook).

- [ ] **Step 11: Add principle P7 after the last safety-invariants bullet (after line 103)**

> - Review tiering (the Scope bullet above) never lowers a safety gate; `KALSHI_PATHS` stays a deny in every tier, and nothing about Tier B touches `trading_enabled`, the kill switch, or `data/*.db` handling.

- [ ] **Step 12: Add principles P2 and P5 (after line 107)**

> - A Tier A change to pricing, P&L, fees, sizing, or settlement cites in its PR one read-only query or replay over recorded `data/*.db` history that exercises the changed path, and what it showed; fixtures alone are not evidence (the no-side cost inversion and the 2026-09-04 NO-side exit valuation both passed the suite and were found in recorded data).
> - For Tier A tests and PR evidence, name where the expected value comes from — a `docs/kalshi/` page, a recorded row, an invariant, an independent calculation — never the code's own output.

- [ ] **Step 13: Correct the branch-protection claim (line 117)**

Replace the opening clause "`main` is protected;" with:

> `main` takes no direct work (server-side protection is off — #615, David's open call, cause not recoverable from the API — so the six required contexts are read from `commits/<sha>/status` before every merge);

- [ ] **Step 14: Tier the peer-ping clause (line 120)**

Replace the phrase
"not a substitute for the "nothing advances on one pass" adversarial-review requirement (2026-08-31)"
with:

> not a substitute for the "nothing advances on one pass" requirement — a Tier A PR's fresh-Agent adversarial review, a Tier B PR's self-review comment (2026-08-31, tiered 2026-09-07)

- [ ] **Step 15: Verify no unqualified statement of the old rule survives**

This is spec §10's own NO-GO trigger: a rule edit that contradicts a line the
PR does not also change. Run:

```bash
grep -n 'self-review, adversarial review, consolidation' CLAUDE.md
grep -n 'adversarial-review requirement' CLAUDE.md
grep -n 'in-scope PR\|in-scope stage' CLAUDE.md
grep -n 'kalshi_account_client\|main` is protected' CLAUDE.md
```

Expected: every surviving hit is scoped to Tier A (lines 43, 48, 49 as
rewritten) or is inside the new merge bullet. The last two greps return
nothing. If any hit states the requirement unconditionally, fix it here — do
not leave it for the PR review.

- [ ] **Step 16: Commit**

Stage `CLAUDE.md` and commit with:

```
docs: tier review depth by consequence (David's decision, 2026-09-07)

Tier A keeps the full self-review/adversarial-review/consolidation cycle; Tier
B owes one persisted self-review comment plus green CI, with the boundary
decided mechanically by the paths a PR touches. Also closes #613's two decided
edits and corrects two statements this section made that are no longer true:
the moved kalshi_account_client path, and main being server-side protected
(#615 found it off).
```

---

### Task 8: the rules file and the checkpoint skill

**Files:**
- Modify: `.claude/rules/branching-and-ci.md` (four edits, spec §5.2)
- Modify: `.claude/skills/checkpoint/SKILL.md` (step 9, spec §5.3)

**Interfaces:**
- Consumes: the `review-tier` command (Task 6) and CLAUDE.md's tier
  vocabulary (Task 7)
- Produces: nothing code-level

- [ ] **Step 1: Insert the merge check before "Read the PR body" (line 63)**

Immediately before "Read the PR body before merging":

> Decide the exemption question first, then run `python -m tools.kanban_sync review-tier --pr <n>` (`--exempt "<reason>"` for a mechanical change, `--tier A` to escalate) and paste its output into the final PR comment or the merge commit; merge only on `PASS` or `EXEMPT`. It decides Tier A/B from the changed paths, data-model lines, and `concern:hotpath`, and counts the persisted review artifacts (three for A, one for B). A `FAIL` is not a formality — supply the missing artifact (a fresh Agent for an adversarial pass, the author for a Tier B self-review) or do not merge.

- [ ] **Step 2: Tier the peer-ping parenthetical (lines 76–78)**

Replace
"(that still needs its own fresh, memory-less Agent call regardless of what a peer says)"
with:

> (a Tier A PR still needs its own fresh, memory-less Agent call regardless of what a peer says; a Tier B PR still needs its `Tier B self-review` comment)

- [ ] **Step 3: Tier the single-developer bullet (lines 88–94)**

Replace
"but the AI-executed self-review/adversarial-review/consolidation cycle still runs before `gh pr merge` — that is rigor, not approval ceremony."
with:

> but the AI-executed review its tier requires — the self-review/adversarial-review/consolidation cycle for Tier A, the one self-review comment for Tier B — still runs before `gh pr merge`, and `review-tier` records that it did — that is rigor, not approval ceremony.

- [ ] **Step 4: Replace the GitHub-side enforcement paragraph in full (lines 96–116)**

> **GitHub-side enforcement (configured 2026-08-25; found off 2026-09-05, #615):** `gh api repos/thesneakattack/kalshi-whale-poc/branches/main` reports `protected: false`, `enforcement_level: off`; whether it lapsed via a plan change or the repo going private is not recoverable from the API. Until #615 is decided (GitHub Pro, a public repo, or accepting this as permanent), the six contexts below are enforced by the merging session reading `commits/<sha>/status` and treating any state other than `success` on any of them as not merged: `ci/woodpecker/pr/tests-pytest-app`, `.../tests-pytest-tooling`, `.../tests-dependency-audit`, `.../quality-architecture-audit`, `.../quality-browser-e2e`, `.../kalshi-contract-fixtures`. `quality-frontend-build` is path-filtered to `frontend/**` and posts no status when skipped, so it is read only when it ran. If protection is re-enabled, restore the 2026-09-03 contexts list with `gh api -X PUT .../protection --input <file>` and rewrite this paragraph.

Before making this edit, re-confirm the claim rather than trusting #615's
text — it is the load-bearing fact of the paragraph:

```bash
gh api repos/thesneakattack/kalshi-whale-poc/branches/main --jq '{protected, protection: .protection.enabled}'
```

If it reports `protected: true`, do **not** make this edit; restore the
original paragraph's meaning, note the discrepancy in the PR body, and say so
in the commit message.

- [ ] **Step 5: Add the check to the checkpoint skill's step 9**

In `.claude/skills/checkpoint/SKILL.md`, step 9 currently reads "…when CI is
green and the diff is reviewed, `gh pr merge --merge`, then:". Insert before
`gh pr merge --merge`:

> decide the exemption question, run `python -m tools.kanban_sync review-tier --pr <n>`, merge only on `PASS` or `EXEMPT`;

Step 8 is left alone: the `outcomes` line the spec's §5.3 called for belongs to
the report this plan does not build (see "Deviations", item 5).

- [ ] **Step 6: Verify the rule files agree with CLAUDE.md**

```bash
grep -n 'self-review/adversarial-review/consolidation' .claude/rules/branching-and-ci.md
grep -n 'review-tier' .claude/rules/branching-and-ci.md .claude/skills/checkpoint/SKILL.md
grep -rn 'main` is protected' .claude/rules/
```

Expected: the first hit is the Tier-A-scoped sentence from step 3; `review-tier`
appears in both files; the third returns nothing.

- [ ] **Step 7: Commit**

Stage `.claude/rules/branching-and-ci.md` and
`.claude/skills/checkpoint/SKILL.md`, then commit with:

```
docs: put the review-tier check in the merge step, and correct the protection text

The merge step now decides the exemption question, runs review-tier, and merges
only on PASS or EXEMPT. The branch-protection paragraph described server-side
enforcement that #615 found switched off; it now says what actually holds the
line, which is the session reading the six contexts.
```

---

### Task 9: live dry run, manifest, and the rollout bookkeeping

**Files:**
- Modify: `static/project-manifest.json` (regenerated)
- Modify: `docs/open-decisions.md`
- Modify: `docs/next-action.md`

**Interfaces:**
- Consumes: everything above
- Produces: the evidence that goes in the PR body

- [ ] **Step 1: Dry-run `review-tier` against the last ten merged PRs**

```bash
for n in $(gh pr list --repo thesneakattack/kalshi-whale-poc --state merged --limit 10 --json number --jq '.[].number'); do
  python -m tools.kanban_sync review-tier --pr "$n" --json
done > /tmp/review-tier-dryrun.json
```

Read every result. Compare each tier against spec §3.2's simulation for that
PR number. A disagreement is spec §10's second NO-GO trigger — stop and
reconcile it before merging, do not adjust the fixture to match.

Two things this dry run will show and that the PR body should state plainly:
these are the PRs merged *before* the rule existed, so most will read `FAIL`
on the artifact count. That is the research's finding reproduced by the tool,
not a defect in the tool.

- [ ] **Step 2: Regenerate the project manifest**

Three new files and three new test files change the counts the architecture
audit checks, so `quality-architecture-audit` goes red without this.

```bash
python -m tools.project_manifest --check static/project-manifest.json --repo-root .
python -m tools.project_manifest --write static/project-manifest.json --repo-root .
```

Run it from the host, not `ddev exec`: a container run has produced a null
`generated_from_head` before.

- [ ] **Step 3: Replace the tiering line in `docs/open-decisions.md`**

The branch already carries a "Decided 2026-09-07 — spec in progress (remove
when the spec's PR merges)" section. Replace that whole section with two
lines: one recording that the decision shipped, one parking the measurement
question this plan deliberately did not build.

> ## Parked 2026-09-07 — is a review-outcomes report worth building?
>
> Review tiering shipped (PR #<n>, `review-tier` decides the tier and counts
> artifacts). The design also specified `python -m tools.kanban_sync outcomes`
> — defects per tier and size band over a fixed window — and it was cut before
> implementation: the repo's own rule is that a handspun tool earns its place
> by run history, and nothing has yet needed this number. **Decide by
> 2026-10-05:** has any decision since the merge wanted a defect-per-tier
> figure? If yes, build it from the design's §6.3, which is written out in
> full. If no, close this line and let the tiering stand on the merge check
> alone. Principle P4 as stated — outcomes are judged by defects per tier and
> size band, never by volume, and never by human hours (only David can supply
> that one) — holds either way.

- [ ] **Step 4: Rewrite `docs/next-action.md`**

Name the merged state, the one command sessions must now run before merging,
and the 2026-10-05 decision above. Do not leave it describing this work.

- [ ] **Step 5: Commit and push**

Stage `static/project-manifest.json`, `docs/open-decisions.md`, and
`docs/next-action.md`, then commit with:

```
docs: record the tiering decision as shipped and park the outcomes question

The outcomes report was cut before implementation - the design writes it out in
full if a decision ever wants the number. Manifest regenerated for the three new
modules and three new test files.
```

Push, then confirm CI:

```bash
gh api repos/thesneakattack/kalshi-whale-poc/commits/<sha>/status
```

Read each context's `state` independently. All six required contexts must be
`success`.

- [ ] **Step 6: Open the PR and run its own review cycle**

```bash
gh pr create --title "..." --body "..."
gh pr edit <n> --add-label lane:9 --add-label phase:research --add-label phase:spec --add-label phase:plan
```

The PR body records: David's 2026-09-07 tiering decision, the numbers from
spec §3.2, the dry-run output from step 1, the deviations list at the top of
this plan, and the fact that #613's two edits are folded in (the PR closes
#613). Then, per CLAUDE.md, this Tier A PR owes its own self-review,
adversarial review (a fresh Agent, no session memory), and consolidation as
three distinct persisted comments — and `review-tier --pr <n>` must print
`PASS` against its own rule before the merge. Verify the comments exist with
`gh pr view <n> --json comments` rather than trusting the narrative; that is
the failure this whole PR is about.

---

## Plan self-review

**Spec coverage.** P1 → Tasks 1, 3, 7. P2 and P5 → Task 7 step 12. P3 → Tasks
4, 6, 7 step 7, 8 step 1. P6 → Task 7 step 1. P7 → Task 7 step 11. Spec §3.1's
five rules → Task 3, each with a test. §4's merge check → Task 6. §5.1 →
Task 7 (all fourteen edits). §5.2 → Task 8. §5.3 → Task 8 step 5, minus the
`outcomes` line. §5.4 → Task 1 step 4. §6.1 → Tasks 1–2. §6.2 → Tasks 4–6.
§6.3 → **not built** (deviation 5). §7's verification list → the tests in
Tasks 1–6 plus Task 9 step 1. §8's rollout → Task 9.

**Not covered, deliberately:** spec §6.3 and the P4 tooling. Parked with a
dated decision rather than dropped silently (Task 9 step 3).

**Type consistency.** `review_tier(files, *, diff_text, pr_labels, escalate)`
is called with exactly those keywords in Task 6. `count_review_artifacts(comments,
files)` returns `(int, list[str])` and is unpacked as such. `_REVIEW_TIER_REQUIREMENT`
is keyed `"A"`/`"B"`, and the exempt path never indexes it.
`labels.resolve_lane_package` returns a `str` and is wrapped in a `Path` by
the test helper that uses it as one.

**Placeholder scan.** No TBD or TODO. Every rule edit is verbatim text. The
one intentional blank is `PR #<n>` in Task 9 step 3, which cannot be known
before the PR exists.
