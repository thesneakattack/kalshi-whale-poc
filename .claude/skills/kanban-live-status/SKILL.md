---
name: kanban-live-status
description: This skill should be used immediately after claiming, reporting a result on, or blocking a GitHub issue via the installed github-issues-kanban plugin skill (claim-issue, report-result, block), or after directly closing an issue - pushes that one issue's current status onto the GitHub Projects board's visible Status column (Next/Doing/Waiting/Done) right now, instead of waiting for the next batch kanban-board-sync run.
---

# Kanban live status push

The installed `github-issues-kanban` plugin updates an issue's `status:*`
label in real time on claim/report-result/block (see that plugin's own
`prompts/claim-issue.md`/`prompts/report-result.md` and
`references/worker-protocol.md`), but it never touches the GitHub Projects V2 "Status"
field — that field is the only mechanism that drives the board's visible
columns (`tools/kanban_sync/project_status.py`). Without this skill, the
board looks stale until someone runs the full batch `kanban-board-sync`
skill. This skill is the one-issue, real-time counterpart — it does
nothing else (no source scanning, no plan classification, no stale-issue
closing; that is still `kanban-board-sync`'s job, and the batch command
stays useful for full reconciliation).

## When to use

Right after any of these, for the issue just acted on:

- `claim-issue` (issue moves to `status:claimed`)
- `report-result` (issue moves to `status:ready-for-review` or `status:done`)
- `block` (issue moves to `status:blocked`)
- Directly closing an issue (state becomes closed → board moves to Done)

## Steps

1. `gh auth status` must list the `project` scope (same requirement as
   `kanban-board-sync`); if missing, stop and tell the user to run
   `gh auth refresh -s project` (only they can).
2. `python -m tools.kanban_sync push-status --issue <N>` — reads the
   issue's current open/closed state and `status:*` label
   (`tools/kanban_sync/live_status.py`'s `resolve_project_status`), maps
   it onto the board's Status field, and pushes it via the Project's
   GraphQL API (`ensure_on_project` + `set_project_status`, the same
   primitives `kanban-board-sync`'s batch run already uses per item).
   Idempotent — safe to call more than once for the same issue.
3. Report the printed `#<N> -> <Status>` line back to whoever asked.

## Errors

- `error: issue #N not found` — the issue number is wrong, or it has not
  been created on GitHub yet (e.g. a plan task before `decompose-plan`
  has run for that plan).
- `error: issue #N has 0 status:* labels` / `has 2 status:* labels` — a
  protocol violation upstream (the github-issues-kanban plugin's own
  `claim-issue` edge cases say a `status:*` conflict should never reach
  this state). Surface it as-is; do not guess which label is "right."

## Not yet automated

Currently invoked explicitly, one issue at a time, right after the
triggering action. A later iteration may wire this into a Claude Code
hook that fires automatically whenever a `gh issue edit` call changes a
`status:*` label, removing the need to remember to call it by hand —
tracked as a follow-up, not built yet (2026-08-31 decision: explicit
invocation first, automation later).
