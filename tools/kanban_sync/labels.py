"""Canonical label constants (docs/superpowers/specs/2026-08-26-kanban-
board-sync-design.md §7). status:* values are copied verbatim from the
installed github-issues-kanban skill's assets/label-scheme.json - do not
invent new status values. type:* is this repo's own extension, already
established by docs/superpowers/specs/2026-08-26-autonomous-engineering-
mode-design.md (Task 15's note); type:tracking is this plan's own addition
to that same repo-local family, for issues that track a worktree rather
than represent claimable work.
"""
from __future__ import annotations

STATUS_CLAIMABLE = "status:claimable"
STATUS_CLAIMED = "status:claimed"
STATUS_IN_PROGRESS = "status:in-progress"
STATUS_READY_FOR_REVIEW = "status:ready-for-review"
STATUS_BLOCKED = "status:blocked"
STATUS_DONE = "status:done"

ALL_STATUS_LABELS = frozenset({
    STATUS_CLAIMABLE, STATUS_CLAIMED, STATUS_IN_PROGRESS,
    STATUS_READY_FOR_REVIEW, STATUS_BLOCKED, STATUS_DONE,
})

TYPE_BUG = "type:bug"
TYPE_INVESTIGATION = "type:investigation"
TYPE_PLAN_TASK = "type:plan-task"
TYPE_FEATURE = "type:feature"
TYPE_DESIGN = "type:design"
TYPE_TRACKING = "type:tracking"

# phase:* (2026-08-27, direct request: "kanban columns groupable by these
# plans/worktrees' phases") - a repo-local family, same status as type:*
# above, not part of the installed skill's own label-scheme.json. Optional
# on SyncItem (not every source can determine one - see each source's own
# phase-detection docstring for what it can and can't infer). Renamed
# 2026-08-27 to match superpowers' own lifecycle vocabulary (brainstorming/
# spec/plan/implementing/verification/done) rather than the original
# ad hoc wording - see docs/superpowers/specs/2026-08-27-kanban-sync-
# project-status-field-design.md §4.1. Label-based, NOT because this
# repo's board can group its view by Labels - it can't: GitHub Projects V2
# board/table views can only be grouped by a single-select or iteration
# *field* on the Project itself, confirmed against GitHub's own current
# docs 2026-08-27 (a prior version of this comment claimed the opposite;
# that was wrong). phase:* exists as issue metadata/filtering, not as the
# mechanism that produces the board's visible columns - that's
# project_status.py's job instead. PHASE_IMPLEMENTING/PHASE_VERIFICATION
# are worktree-only, not general: a worktree already maps 1:1 to one
# branch, but a plan/track item does not reliably correlate to a branch by
# name (see the design doc's §4.1 for the empirical branch-name mismatches
# that ruled this out for plan/track items). No PHASE_BRAINSTORMING
# constant: an idea with neither a research doc nor a spec doc has nothing
# in the repo to detect it from, so this tool can only ever label an
# initiative once it exists as text somewhere.
PHASE_RESEARCH = "phase:research"
PHASE_SPEC = "phase:spec"
PHASE_PLAN = "phase:plan"
PHASE_IMPLEMENTING = "phase:implementing"
PHASE_VERIFICATION = "phase:verification"
PHASE_DONE = "phase:done"

ALL_PHASE_LABELS = frozenset({
    PHASE_RESEARCH, PHASE_SPEC, PHASE_PLAN,
    PHASE_IMPLEMENTING, PHASE_VERIFICATION, PHASE_DONE,
})

SYNC_MARKER_KIND_WORKTREE = "worktree"
SYNC_MARKER_KIND_ROADMAP = "roadmap"
SYNC_MARKER_KIND_PLAN = "plan"
