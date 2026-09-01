# Next action

**Resume the 3-plan sequence (kalshi → whale-confidence → frontend) at
whale-confidence-scoring-remediation Task 1's fix round.**

Original instruction (still governing, "do not ask me for input" is still in
force): implement `docs/superpowers/plans/2026-08-30-kalshi-category-data-
completeness-implementation.md`, then `docs/superpowers/plans/2026-08-30-
whale-confidence-scoring-remediation-implementation.md`, then
`docs/superpowers/plans/2026-08-25-frontend-modularization.md`, each through
the full `subagent-driven-development` cycle to merge, using subagents,
without stopping to ask — log anything genuinely requiring a human call in
`docs/superpowers/research/2026-08-31-followups-from-3-plan-implementation.md`
and keep going.

## Plan 1 (kalshi-category-data-completeness): DONE, merged

PR #374 merged to `main` 2026-09-01 (14 tasks + a whole-branch review round +
a PR-level adversarial-review round, both with real Critical/Important
findings found and fixed — see the PR's own comment thread for the full
self-review/adversarial-review/consolidation history). Issues #318 and
#341–354 closed. Worktree `.claude/worktrees/impl-kalshi-category-completeness`
still exists on disk (branch already deleted, both locally-tracked-as-merged
and on the remote) — safe for `scripts/cleanup-worktrees.sh` to reap, not
done yet this session.

## Plan 2 (whale-confidence-scoring-remediation): IN PROGRESS

**Worktree: `.claude/worktrees/impl-whale-confidence-scoring`, branch
`feat/whale-confidence-scoring-remediation` (pushed to origin, no PR yet).
Resume IN THIS EXACT WORKTREE** — its `.superpowers/sdd/2026-08-30-whale-
confidence-scoring-remediation-implementation/progress.md` ledger is
gitignored (workspace scratch, per the `subagent-driven-development` skill's
own convention) and holds real, not-yet-committed-anywhere context: the
citation-drift review findings, the pre-flight scan, and Task 1's two open
review findings. Starting a fresh worktree instead of resuming this one loses
that ledger entirely and will cause exactly the "re-dispatched a completed
task sequence" failure mode the skill's own docs warn about — read the
ledger first, trust it and `git log` over conversational memory.

**Before Task 1: this plan's own catch-up review (2026-08-31, done, GO
verdict) predates PR #374's merge.** This session already ran a focused
post-merge citation-drift review (see the ledger's Setup section) — 11 stale
line-number citations into `services/signal_log.py`/`main.py` corrected, one
pre-existing citation defect in Task 11 resolved, a stale worktree path in
Global Constraints fixed. Committed as `433c606`. No semantic conflict found;
the plan's own catch-up-review verdict (GO) still stands.

**Task 1 (`_bucket_win_rates` materiality-floored tie predicate): implemented
but NOT YET fix-rounded.** Commit `51ead87`. The task-reviewer (dispatched,
verdict "Needs fixes") found two real problems, independently confirmed
against the diff before dispatch by the controller for the first one:

1. **Critical — documentation lost, not preserved.** The brief's Step 3 code
   showed `_bucket_win_rates`'s docstring as `"""... (existing docstring,
   extended:) Returns (buckets, data_status) - ..."""` — an editing
   placeholder meaning "keep the real docstring, append this." The
   implementer pasted the literal `...` into shipped code, deleting three
   real paragraphs (the 2026-08-10 KeyError-on-9000-already-logged-signals
   production incident behind the missing-factor-key filter; the
   near-constant-factor bug behind the distinct-values guard; the
   tertile-vs-value-comparison design rationale) plus a separate 6-line
   inline comment explaining the 2026-08-14 float-jitter dedup fix. Fix:
   restore all of it, append the new `data_status` explanation to it rather
   than replacing it, remove the literal placeholder text.
2. **Important — a vacuous test.**
   `test_small_incidental_tie_at_a_cut_boundary_is_not_contaminated` inserts
   a tied value of `0.5` into a 1000-row fixture, but that value sorts to
   positions ~495-500 — nowhere near either real cut (333, 667) — so the
   test passes regardless of whether the materiality-floor logic is even
   correct. This is the primary positive-path case for the whole feature
   and currently has zero real coverage. Fix: use a tied value that actually
   sorts to a cut boundary (e.g. near `333/1000`), per the reviewer's own
   suggestion.

**Exact next steps, in order:**
1. Resume the fix round for Task 1 — either resume the original implementer
   subagent (name `ada25b94140d8b034` — likely no longer addressable across
   a session boundary; if so, apply the fix directly or dispatch a fresh
   implementer with the two findings above as its brief) — this is fix round
   1 of the skill's 5-round budget.
2. Independently verify the fix yourself (read the diff, don't just trust a
   report) before dispatching a scoped re-review.
3. Once Task 1 is clean, continue Tasks 2–16 via the same
   implementer → controller-verify → task-reviewer → (fix rounds as needed)
   → re-review cycle used throughout — see `.claude/worktrees/
   impl-whale-confidence-scoring/.superpowers/sdd/2026-08-30-whale-
   confidence-scoring-remediation-implementation/progress.md` for the full
   ledger of what's already verified (Global Constraints, pre-flight scan,
   Task 1's state).
4. **Task 10 is a hard, PROCESS-only (not code/CI-enforced) prerequisite for
   Tasks 11–16** — the plan's own Global Constraints section is explicit
   that nothing will stop an implementer from starting Task 11 early; honor
   the ordering manually.
5. After Task 16: final whole-branch review (most capable model), push, PR,
   the PR-level "nothing advances on one pass" cycle (self-review →
   independent adversarial review → consolidation, each its own PR comment),
   confirm CI, merge, close the plan's tracking issue (#320) and its 16
   sub-issues, run `/kanban-board-sync` (see below — don't skip this again).
   Delete the SDD workspace once merged.

## Plan 3 (frontend-modularization): NOT STARTED

Worktree not yet created. Its own catch-up review (2026-08-25 plan +
2026-08-25 design, both done 2026-08-31, GO verdict — see
`docs/superpowers/plans/2026-08-25-frontend-modularization-catchup-
consolidation.md`) predates PR #374 too — **the same post-merge citation-
drift review that plan 2 needed will be needed here too** before Task/Step 1
dispatch (check whether PR #374's 32 changed files overlap any of this
plan's citations the same way `signal_log.py`/`main.py` did for plan 2 —
`frontend/` files are unlikely to overlap, but the backend pieces this plan
touches might). Create a fresh worktree (`git worktree add .claude/worktrees/
impl-frontend-modularization -b feat/frontend-modularization origin/main`,
`EnterWorktree`) once plan 2 merges, matching the pattern used for plan 2.

## Also this session: kanban board sync gap found and fixed

The user asked mid-session why completed plans weren't reflecting on the
kanban board — the honest answer was `/kanban-board-sync` was never run
during any of this session's implementation/review/merge work. Ran it fully
(mechanical sources + a 30-plan-doc backlog classification going back to
2026-08-24, not just this session's own plans) — 8 new issues created, 1
closed (an explicitly-retired plan), several stale/already-correct states
confirmed. One real correction made along the way: almost closed issue #74
(Autonomous Quality Coordination workflow-health) as done based on an
earlier session's own summary claim, but `ROADMAP.md`'s actual checkbox is
correctly still unchecked (10 of 11 tasks done, 1 genuinely gated on a live
human approval per the plan's own text) — left it open, don't re-attempt
closing it without that approval happening first. **Don't forget this step
again at plan 2 and plan 3's own close-out.**

## New MCP servers noted, not yet usable this session

The user mentioned installing a prediction-market MCP server with
potentially useful skills for this work, and reminded to use the Wolfram
MCP server (`mcp__claude_ai_Wolfram__WolframAlpha`/`WolframLanguageEvaluator`)
for dimensional-analysis arithmetic. Checked `ListMcpResourcesTool` — the
prediction-market server isn't showing up in this session's tool config
(only `gitnexus`/`github` have resources); it likely needs a fresh session
to load. **Check for it early in the new session** (`ToolSearch`/
`ListMcpResourcesTool`) and use it where relevant. Wolfram's tools are
confirmed available and should be used for any non-trivial arithmetic in
this plan's dimensional-analysis passes (per CLAUDE.md's HARD RULE) —
none of Task 1's own arithmetic (`max(30, 0.005 * n)`) needed it, it's
simple enough to hand-verify, but later tasks may not be.

## Also still open, unrelated (carried over from before this session, not re-verified)

- PR #303 mentioned here previously — check `gh pr list` for its current
  state before assuming it's still open; this note predates the current
  session's work and may already be stale.
- `.claude/worktrees/candlestick-volatility` (`feat/candlestick-
  volatility`) — check `ListAgents`/`gh pr list` before touching, may
  still be another session's active work or may be stale by now.
- Re-run `python -m tools.soak_analyzer` to confirm `capture_writer_health`/
  `exit_engine_faults` fault-log FAILs aged out with zero new occurrences;
  if clean, close `docs/open-decisions.md`'s `two_consumer_mode` permanence
  item.
- **Parked, needs a human read:** `docs/superpowers/specs/2026-08-30-
  weather-index-ingestion-design.md` — already has a full design+plan+review
  cycle done (GO, see `docs/open-decisions.md`), waiting on a go-ahead.
- `docs/superpowers/plans/2026-08-31-claudesuperpower-plugin-pilot.md` —
  same: full pipeline done, waiting on a go-ahead (also in
  `docs/open-decisions.md`).
- `services/market_watch/catalog_scan.py`'s `related_event_tickers`-vs-
  `get_markets_by_tickers` mismatch (found during plan 1's PR-level
  adversarial review, `docs/open-decisions.md`) — pre-existing, predates all
  3 plans, blocks Tasks 9/14's structured-target resolution from executing
  against real markets. Tracked, not fixed. A real, separate, non-trivial
  fix someone should prioritize.
