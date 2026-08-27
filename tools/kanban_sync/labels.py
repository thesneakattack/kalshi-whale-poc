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
# phase-detection docstring for what it can and can't infer). Deliberately
# label-based, not a Projects V2 custom field: this repo's board already
# supports grouping its view by Labels natively, and every sync source
# already has a working label-reconciliation path - a new Projects V2 field
# would mean new GraphQL surface this tool doesn't have today. No
# PHASE_BRAINSTORMING constant: an idea with neither a research doc nor a
# spec doc has nothing in the repo to detect it from, so this tool can only
# ever label an initiative once it exists as text somewhere.
PHASE_RESEARCH_EVIDENCE = "phase:research-evidence"
PHASE_DESIGN_SPEC = "phase:design-spec"
PHASE_IMPLEMENTATION_PLAN = "phase:implementation-plan"
PHASE_IMPLEMENTED = "phase:implemented"

ALL_PHASE_LABELS = frozenset({
    PHASE_RESEARCH_EVIDENCE, PHASE_DESIGN_SPEC, PHASE_IMPLEMENTATION_PLAN, PHASE_IMPLEMENTED,
})

SYNC_MARKER_KIND_WORKTREE = "worktree"
SYNC_MARKER_KIND_ROADMAP = "roadmap"
SYNC_MARKER_KIND_TRACK = "track"
SYNC_MARKER_KIND_PLAN = "plan"
