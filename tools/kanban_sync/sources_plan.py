"""Numbered plan docs not yet represented as a plan candidate: every
`docs/superpowers/plans/*.md` file except `README.md` (the index, never a
candidate), the tracks board itself (`2026-08-26-active-tracks-board.md` -
self-declares "not a plan in its own right"; scheduled for `docs/archive/`
by a later migration step, not moved by this change, so it still needs
excluding here), and a "companion" doc - a review/self-review/adversarial-
review/consolidation artifact for another plan in the same directory,
detected mechanically by filename suffix plus the existence of the parent
file that suffix implies (see `_is_companion`). Candidate listing is
mechanical; classifying a candidate as done/in-progress/not-started is a
judgment call the on-demand kanban-board-sync skill makes by reading git
log/CLAUDE.md/ROADMAP.md, not something this module infers from the plan
doc's own checkboxes - measured unreliable in spec §5.

This status always describes whether the plan's CODE has shipped, never
whether the plan DOCUMENT exists (CLAUDE.md's "nothing advances on one
pass" HARD RULE) - a file only becomes a candidate here because its
document already exists, so "not-started" can only mean its tasks are
unimplemented, not that planning never happened.
"""
from __future__ import annotations

import re
from pathlib import Path

from tools.kanban_sync import labels
from tools.kanban_sync.models import SyncItem

# Companion suffix family: -review, -self-review, -adversarial-review,
# -consolidation, -recheck, each optionally prefixed by one qualifier
# (pr-/catchup-/plan-) and optionally followed by a round/attempt marker
# (-round2, -2). Calibrated against every real filename currently in
# docs/superpowers/plans/, cross-checked against
# docs/superpowers/lanes/step1-plans-classification.md's independently-built
# classification table - not invented from the four suffix names alone
# (that table's own finding G2 is that a fixed 4-suffix list already missed
# a real companion, ...-frontend-modularization-freshness-check.md). This is
# deliberately a pattern over a small closed vocabulary of review-family
# words, not a literal list of exact suffix strings, and is still expected
# to miss a same-directory companion whose filename doesn't carry one of
# these words - that gap is the judgment-assisted kanban-board-sync skill's
# job, not this function's.
_COMPANION_SUFFIX_RE = re.compile(
    r"-(?:pr-|catchup-|plan-)?(?:adversarial-review|self-review|review|consolidation|recheck)"
    r"(?:-round\d+)?(?:-\d+)?$"
)

_EXCLUDED_FILENAMES = frozenset({"README.md", "2026-08-26-active-tracks-board.md"})


def _is_companion(filename: str, all_plans: set[str]) -> bool:
    """True when `filename` is a companion doc: its own suffix matches the
    review/consolidation family AND stripping that suffix off yields
    another real file in `all_plans` (the parent it's a companion of). Both
    conditions are required - a suffix match alone isn't enough: see
    `...-persistence-layer-task8-candidate-ledger-self-review.md` in the
    classification table above, which matches the suffix pattern but has no
    filename-obvious parent, so it correctly stays a candidate here (it was
    a design-assigned exception in the classification table, not something
    a mechanical filename check can determine)."""
    stem = filename.removesuffix(".md")
    match = _COMPANION_SUFFIX_RE.search(stem)
    if not match:
        return False
    parent = stem[: match.start()] + ".md"
    return parent in all_plans


def list_plan_candidates(plans_dir: Path) -> list[str]:
    all_plans = {p.name for p in plans_dir.glob("*.md")}
    return sorted(
        name for name in all_plans
        if name not in _EXCLUDED_FILENAMES and not _is_companion(name, all_plans)
    )


def resolve_plan_path(plans_dir: Path, archive_root: Path, filename: str) -> Path:
    """Locates a single named plan doc's current file for decompose-plan
    (`_cmd_decompose_plan`), wherever it lives during the 2026-09-06
    planning-lanes migration (docs/superpowers/specs/2026-09-06-planning-
    lanes-design.md, docs/superpowers/lanes/step4-file-move-plan.md): still
    in `plans_dir` (not yet moved - the common case, and correct for every
    call before/outside this migration), or already relocated to
    `archive_root/lane-<N>-<slug>/plans/`.

    Unlike list_plan_candidates() above (whose "no code change needed" this
    migration finding does not apply here - see the docstring split), this
    genuinely needs to search both locations: decompose-plan specifically
    targets not-started/in-progress plans using `### Task N:` headings
    (kanban-board-sync/SKILL.md step 7) - exactly the still-`active`
    population docs/superpowers/lanes/step1-plans-classification.md
    identifies as subject to being archived while still needing this
    lookup to keep working (found by this fix's own PR adversarial review,
    not by the original investigation).

    Checks `plans_dir` first (cheapest, and correct for the overwhelming
    majority of calls), then searches every `lane-*/plans/` subdirectory
    under `archive_root` for an exact filename match - never hardcoding a
    lane number/slug, tolerating `archive_root` (or any specific lane's
    `plans/` subdirectory) not existing yet. Falls back to
    `plans_dir / filename` when the file is found nowhere, so the caller's
    existing "plan doc not found: <path>" error keeps reporting exactly the
    path it always has for a filename that genuinely doesn't exist
    anywhere - unchanged prior behavior for that case."""
    live_path = plans_dir / filename
    if live_path.exists():
        return live_path
    if archive_root.exists():
        matches = sorted(archive_root.glob(f"lane-*/plans/{filename}"))
        if matches:
            return matches[0]
    return live_path


def build_plan_items(classifications: dict[str, dict]) -> list[SyncItem]:
    """`classifications` maps filename -> {"status": "done"|"in-progress"|
    "not-started", "note": str}, produced by the kanban-board-sync skill's
    judgment-assisted classification step.

    A "done" classification still produces a SyncItem (done=True), rather
    than being skipped outright - sync_pass_one's own create/close logic
    already handles both ends of this correctly (no existing issue + done =>
    nothing created; existing open issue + done => closed), so skipping the
    item here would only lose the second case: a plan classified in-progress
    when its tracking issue was first created, then later actually finished,
    would never get that issue auto-closed. Found live 2026-08-27 (issue #96,
    docs/superpowers/plans/2026-08-27-backend-services-modularization.md) and
    fixed here instead of by hand every time it recurs.

    context_body/acceptance_criteria cite the plan by filename only, never a
    directory path (2026-09-06, planning-lanes migration adversarial
    review): sync_pass_one sets an issue's body once at create_issue time and
    never re-renders it for an already-existing issue (only labels/Project
    status get touched on a later sync - confirmed directly against sync.py),
    so baking in a specific docs/superpowers/plans/ or docs/archive/lane-N/
    plans/ path would go permanently stale the next time the plan doc moves,
    with nothing to correct it afterward. A bare filename stays valid
    forever - only directories move, never filenames."""
    items: list[SyncItem] = []
    for filename, info in classifications.items():
        done = info["status"] == "done"
        note = info.get("note", "")
        items.append(SyncItem(
            kind=labels.SYNC_MARKER_KIND_PLAN,
            key=filename,
            title=f"Plan: {filename}",
            status_label=labels.STATUS_DONE if done else labels.STATUS_CLAIMABLE,
            type_label=labels.TYPE_PLAN_TASK,
            context_body=(
                f"## Context\nTracks the plan doc `{filename}` "
                f"as a whole, not per-task (see kanban-board-sync-design.md "
                f"§5 on why plan-doc checkboxes aren't a reliable per-task "
                f"signal in this repo).\n\nCode classification: {info['status']} "
                f"(this plan's document is already written and merged - this "
                f"status is about whether its tasks are implemented)."
                f"\n{note}"
            ),
            acceptance_criteria=(
                f"Plan doc `{filename}` is reclassified "
                f"'done' on a future sync run.",
            ),
            done=done,
            # PHASE_DONE for a done classification, otherwise always
            # PHASE_PLAN, never a lower phase - every item reaching this
            # function already has a real plan doc (that's how it became a
            # candidate at all; see list_plan_candidates). Never
            # PHASE_IMPLEMENTING/PHASE_VERIFICATION - those are
            # worktree-only, see labels.py's own docstring.
            phase_label=labels.PHASE_DONE if done else labels.PHASE_PLAN,
            classification=info["status"],
        ))
    return items
