---
name: kanban-board-sync
description: This skill should be used when the user asks to "sync the kanban board", "sync issues", "update the board", "refresh the kanban board", or wants worktrees/ROADMAP.md/active-tracks-board.md/plan docs reflected onto the real GitHub Issues board. Runs tools/kanban_sync end to end, including the judgment-assisted plan-doc classification step a plain CLI invocation can't do on its own.
---

# Kanban board sync

Reflects repo state onto GitHub Issues, one way — repo state is authoritative,
nothing here edits a repo file (`docs/superpowers/specs/2026-08-26-kanban-board-sync-design.md`).
`tools/kanban_sync` does every deterministic part; this skill supplies the one
judgment call it can't: which numbered plan docs are still open. The installed
`github-issues-kanban` plugin owns claiming/working/reporting on an issue (same
`status:*` / `depends-on:#N` labels; `sync_pass_one` leaves a live
`claimed-by:*` claim alone) — don't duplicate it here. For pushing one
issue's Status onto the Project board immediately after a claim/report-
result/block, use the sibling `kanban-live-status` skill instead of running
this whole batch — this skill stays the full-reconciliation, one-writer-at-
a-time pass.

**One writer at a time.** `ListAgents` first; if a peer session is live, ask
which session syncs. Two runs classify the same plan differently and turn the
board into a record of who ran last (2026-08-28).

## Steps

1. `gh auth status` must list the `project` scope; if not, stop and tell the
   user to run `gh auth refresh -s project` (only they can).

2. Mechanical sources, dry run first, then for real:
   ```bash
   python -m tools.kanban_sync sync --sources worktree,roadmap,track --dry-run
   ```
   Sane counts are a few dozen items, not hundreds.

3. `python -m tools.kanban_sync plan-candidates` — plan docs not already
   referenced by `active-tracks-board.md`.

4. Classify each candidate (`done` / `in-progress` / `not-started`) into a JSON
   file, e.g. `/tmp/kanban-plan-classifications.json`
   (`{"<file>": {"status": "...", "note": "..."}}`), including the `done` ones
   (they get skipped, never created-then-closed). This status is always about
   the plan's CODE, never the plan DOCUMENT — every candidate here already has
   a written, merged plan doc by definition (that's what made it a candidate),
   so `not-started` means "no code shipped against it yet," not "no plan
   exists." Evidence, in order:
   - `gh issue list --search 'autotrade-sync: plan:<filename>' --state all --json number,state,title --limit 1`;
     if CLOSED, read its last comment (`gh issue view <N> --comments`) — a
     "done, merged in PR #N" comment is `done`, full stop.
   - Otherwise `git log --oneline -- docs/superpowers/plans/<file>` plus
     `CLAUDE.md`/`ROADMAP.md`. Checkbox state alone is unreliable here (spec §5).

5. `python -m tools.kanban_sync sync --sources plan --plan-classifications <json> --dry-run`,
   review, re-run without `--dry-run`.

6. Report counts created/updated/closed/flagged. Every `flagged mismatch` needs
   a human to close the issue for real or drop the stale dependency (spec §9).

7. For each `not-started`/`in-progress` plan using `### Task N:` headings:
   `python -m tools.kanban_sync decompose-plan --plan <file> --dry-run`, review
   `milestone` / `tasks_found` / `sub_issues_created`, re-run for real
   (idempotent; other heading conventions yield zero sub-issues by design; a
   closed parent is refused). A parent the tool later auto-closes gets
   reclassified `done` next time, or step 5 keeps posting a mismatch comment
   against it.
