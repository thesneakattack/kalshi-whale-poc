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

SYNC_MARKER_KIND_WORKTREE = "worktree"
SYNC_MARKER_KIND_ROADMAP = "roadmap"
SYNC_MARKER_KIND_TRACK = "track"
SYNC_MARKER_KIND_PLAN = "plan"
