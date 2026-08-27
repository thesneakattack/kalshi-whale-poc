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

**Division of labor with the installed `github-issues-kanban` plugin skill
(2026-08-27):** that skill already fully specifies claiming, working, and
reporting on an issue (`prompts/claim-issue.md`, `prompts/dispatch-next.md`,
`prompts/report-result.md`) using the same `status:*`/`depends-on:#N` label
vocabulary this tool uses - zero code, just `gh` commands to follow
directly. Use it for anything claim/work/report-shaped. This skill and
`tools/kanban_sync` own only what that plugin explicitly leaves to "the
host": deriving which issues should exist from this repo's own state
(worktrees/ROADMAP.md/tracks/plan docs), creating/closing them to match,
native GitHub Milestones + Sub-Issues, and driving the Projects V2 board's
native Status field (the plugin is label-only; it never touches Project
fields). `sync_pass_one` is claim-aware (`sync.py`'s `_has_active_claim`) -
a live, unexpired `claimed-by:*`/`claim-expires:*` claim from that plugin
is left untouched by this tool's own status-label/Project-Status
reconciliation, never silently reverted back to the source-computed
status. Do not build a parallel status/claim CLI in `tools/kanban_sync` -
that capability already exists and is out of scope for this tool to
duplicate.

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

7. **Decompose canonical-convention `not-started`/`in-progress` plans into
   milestone + sub-issues.** One-time per plan, per
   `docs/superpowers/specs/2026-08-27-kanban-sync-milestones-and-subissues-design.md`
   §4.2 — safe to re-run, it no-ops once already decomposed. For each plan
   classified `not-started` or `in-progress` in step 4 that uses the
   canonical `### Task N: <title>` heading convention (the other two
   conventions found in this repo, `## Task N:` and `## T1a —`, are out of
   scope — zero sub-issues created is expected for those, not an error):
   ```bash
   python -m tools.kanban_sync decompose-plan --plan <file> --dry-run
   ```
   Review the JSON output (`milestone`, `tasks_found`,
   `sub_issues_created`), then re-run without `--dry-run`. A plan whose
   parent issue auto-closes on a later `sync --sources plan` run (once all
   its sub-issues are closed, per spec §4.4) should be reclassified `done`
   in the next classification JSON (step 4) — otherwise step 5's next run
   will keep posting a mismatch comment against an issue this tool itself
   already closed correctly.
