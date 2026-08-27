---
name: kanban-board-sync
description: This skill should be used when the user asks to "sync the kanban board", "sync issues", "update the board", "refresh the kanban board", or wants worktrees/ROADMAP.md/active-tracks-board.md/plan docs reflected onto the real GitHub Issues board. Runs tools/kanban_sync end to end, including the judgment-assisted plan-doc classification step a plain CLI invocation can't do on its own.
---

# Kanban board sync

Reflects this repo's current state onto real GitHub Issues, per
`docs/superpowers/specs/2026-08-26-kanban-board-sync-design.md`. One-way:
repo state is always authoritative, nothing here ever edits a repo file.
Uses `tools/kanban_sync` for every deterministic part; this skill's own
job is the one judgment call that tool can't make on its own (spec §5) -
classifying which numbered plan docs are actually still open.

## Steps

1. **Confirm `gh` has the `project` scope.** `gh auth status` - if
   `project` isn't listed, stop and tell the user to run
   `gh auth refresh -s project` (interactive OAuth device flow, only they
   can do it). Don't attempt to work around a missing scope.

2. **Run the mechanical sources first, as a dry run.**
   ```bash
   python -m tools.kanban_sync sync --sources worktree,roadmap,track --dry-run
   ```
   Read the report. If the created/updated/closed counts look sane (a few
   dozen items, not hundreds - spec §5's measured numbers are a rough
   sanity bound), re-run without `--dry-run` to actually write.

3. **List plan-doc candidates.**
   ```bash
   python -m tools.kanban_sync plan-candidates
   ```
   Prints filenames under `docs/superpowers/plans/` not already referenced
   by `active-tracks-board.md`.

4. **Classify each candidate.** For each filename this is a judgment call,
   not a checkbox scan (spec §5 measured why a plan doc's own `- [ ]`
   state is unreliable in this repo): read
   `git log --oneline -- docs/superpowers/plans/<file>`, cross-reference
   `CLAUDE.md` and `ROADMAP.md` for whether that initiative is described
   as shipped, and classify as `done`, `in-progress`, or `not-started`.
   Write the result to a JSON file, e.g. `/tmp/kanban-plan-classifications.json`:
   ```json
   {
     "2026-08-25-frontend-modularization.md": {
       "status": "in-progress",
       "note": "frontend-modularization-task skill still has open tasks"
     }
   }
   ```
   Include every candidate from step 3, even ones classified `done` — they
   get skipped, not created-then-closed (spec §5, Global Constraint 6).

5. **Sync the plan source.**
   ```bash
   python -m tools.kanban_sync sync --sources plan \
     --plan-classifications /tmp/kanban-plan-classifications.json --dry-run
   ```
   Review, then re-run without `--dry-run`.

6. **Report a short summary** to the user: counts created/updated/closed/
   flagged, and call out any `flagged mismatches` explicitly — those need a
   human to look at the issue and decide whether to close it for real or
   remove the stale dependency (spec §9).
