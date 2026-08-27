"""Projects V2 "Status" single-select field constants and the status:*
label -> Status-option mapping. Labels cannot drive Projects V2 board/table
grouping at all (confirmed against GitHub's own current docs 2026-08-27) -
only a project's own field, like this one, can. This is the mechanism
that actually produces the board's visible columns - see
docs/superpowers/specs/2026-08-27-kanban-sync-project-status-field-design.md.

Project: thesneakattack/kalshi-whale-poc - Personal Todo Board (project #3).
IDs below are this project's real GraphQL node IDs, confirmed live
2026-08-27 both via raw GraphQL and `gh project field-list 3 --owner
thesneakattack --format json`.

Looked up by ID, not display name, in github_client.py's
set_project_status - deliberately, so a human renaming a column's label
text in the GitHub UI (a purely cosmetic action) doesn't silently break
this sync; only deleting/recreating an option changes its ID, and that
already fails loud via gh's own error.
"""
from __future__ import annotations

from tools.kanban_sync import labels

PROJECT_OWNER = "thesneakattack"
PROJECT_NUMBER = 3
PROJECT_ID = "PVT_kwHOAHYiPM4BhmnC"
STATUS_FIELD_ID = "PVTSSF_lAHOAHYiPM4BhmnCzhghwaY"

STATUS_INBOX = "Inbox"
STATUS_NEXT = "Next"
STATUS_DOING = "Doing"
STATUS_WAITING = "Waiting"
STATUS_DONE = "Done"

STATUS_OPTION_IDS: dict[str, str] = {
    STATUS_INBOX: "02295626",
    STATUS_NEXT: "61df5cea",
    STATUS_DOING: "3b0f9f3a",
    STATUS_WAITING: "b7ff4b2e",
    STATUS_DONE: "1bf7f74a",
}

# Mirrors the installed github-issues-kanban skill's own personal-todo
# archetype (assets/template-personal-todo.json's "columns") exactly -
# that template is what generated this exact board (title "Personal Todo
# Board" matches its title_pattern), so this is the board's own stated
# design, not an invented mapping.
#   Next    <- status:claimable   Doing <- status:in-progress
#   Waiting <- status:blocked     Done  <- status:done
# The template predates status:claimed/status:ready-for-review (added by
# the kanban skill's fuller 6-value lifecycle, references/
# issue-as-task-contract.md), so it has no entries for them. Placed by the
# same reasoning the template already uses for their neighbors:
#   - status:claimed -> Doing: a held lock is active engagement ("Doing" =
#     "Active work"), and claimed work is no longer available to be picked
#     up, so it can't stay in "Next" ("ready for an agent or you").
#   - status:ready-for-review -> Waiting: "Waiting"'s own template
#     description is "Blocked or waiting on external" - an open PR IS
#     "waiting on external" (review), the same column status:blocked
#     already uses. Reusing rather than adding a 6th option keeps the
#     board's shape as the skill generated it.
# No entry maps to Inbox: every SyncItem this tool ever produces already
# carries a real status:* value (status:claimable at minimum). Inbox is
# reserved for whatever a human adds to the project by hand, outside this
# tool's model entirely - kanban_sync never sets it.
STATUS_LABEL_TO_PROJECT_STATUS: dict[str, str] = {
    labels.STATUS_CLAIMABLE: STATUS_NEXT,
    labels.STATUS_CLAIMED: STATUS_DOING,
    labels.STATUS_IN_PROGRESS: STATUS_DOING,
    labels.STATUS_READY_FOR_REVIEW: STATUS_WAITING,
    labels.STATUS_BLOCKED: STATUS_WAITING,
    labels.STATUS_DONE: STATUS_DONE,
}
