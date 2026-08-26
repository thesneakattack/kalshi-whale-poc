---
name: economic-strategy-effectiveness-investigation
description: Use to investigate where economic edge is created or destroyed between a raw Kalshi whale print and an actually executable, priced trade - gate marginal contribution, adverse-selection root cause, advisory/calibration objective alignment, execution realism, and capture-health-tagged replay gaps. Reconstruct current state from data, measure with the app's own existing reviewed functions before building new ones, and keep remediation candidates separate from investigation findings.
---

# Economic Strategy Effectiveness & Execution Realism Investigation

Canonical files:

- `docs/kalshi-personal-production-execution-program-2026-08-26.md` §6.1 (the governing
  specification — read this, not a summary of it, since it is the source of every
  required-evidence item this investigation answers to)
- `docs/superpowers/specs/2026-08-26-economic-strategy-effectiveness-investigation-design.md`
- `docs/superpowers/plans/2026-08-26-economic-strategy-effectiveness-investigation.md`
  (the numbered E0-E12 task list and status table — the actual progress ledger)
- `docs/superpowers/research/2026-08-26-economic-population-and-replay-gaps.md`
- `docs/superpowers/research/2026-08-26-economic-gate-marginal-contribution.md`
- `docs/superpowers/research/2026-08-26-economic-advisory-calibration-execution-audit.md`
- `docs/superpowers/research/2026-08-26-economic-strategy-effectiveness-adversarial-review.md`
- `docs/superpowers/research/2026-08-26-economic-strategy-effectiveness-status-report.md`
- `docs/superpowers/specs/2026-08-26-economic-strategy-remediation-design.md` /
  `docs/superpowers/plans/2026-08-26-economic-strategy-remediation.md` (Program 2 candidate
  design/plan — remediation, not investigation; explicitly not approved for execution as of
  this skill's authorship — check their status lines before assuming otherwise)

If any of the first five are missing, stop before editing — the investigation's own
progress ledger and constraints must exist before continuing it.

## Role

This skill is the top-level orchestrator for the investigation phase only (E0-E12 in the
plan file). It does not own Program 2 (economic remediation) — once that plan's own status
line changes from "NOT approved for execution," use `superpowers:executing-plans` or
`superpowers:subagent-driven-development` for it directly, per that plan's own note on why
it doesn't need a bespoke orchestrator the way the investigation does.

The goal is not to confirm the original 88.8%/394/58.3%/12 finding or any specific
remediation. The research documents already found that specific sample is unrecoverable
(E2/E10) and that the original finding's direction does not currently reproduce under the
live gate configuration (E5) — a future session's job is to extend E3/E4/E5's
provisional/open items (see the status report §4's insufficient-sample list), not to
re-litigate what E1-E7 already settled with evidence.

## Non-interference constraints (binding — re-read before every task)

- Research-only unless and until Program 2 is explicitly approved: no edits to `main.py`,
  the realtime scheduler, the WS consumer, the Kalshi rate-limiter implementation, live
  strategy config (`config/settings.yaml`), or any live `data/*.db` file.
- `feat/realtime-data-plane-remediation` and its worktree are not to be read from as a
  dependency or touched, until it merges to `main` — confirm its current status
  (`gh pr list --state all`, `git worktree list`) at the start of any resumed session rather
  than assuming it's still unmerged.
- `data/*.db` files: read-only. Copy small files to scratchpad; use a `mode=ro` URI
  connection for large ones (`series_watcher.db`, `candidate_log.db` — both multi-GB) rather
  than copying them.
- Do not call `GET /api/diagnostics/trade-capture` (the one diagnostic that makes real
  Kalshi REST calls) while any realtime-remediation work is actively measuring against the
  same shared REST limiter — check for that active work first if this ever seems necessary.
- Any finding implying a permanent guard gets its disposition *recorded* (which kind, which
  future task owns it), not built, on this investigation's own branch — that's Program 2's
  job, gated behind human approval and (per the design doc's own reasoning) behind Program 1
  landing for several of the candidates.

## Relevant capability selection

Before each task, inspect available repository skills/plugins and use only those that
materially help.

Normally relevant:

- `root-cause-debugging` — for any new anomaly this investigation's own queries surface
- `observability-performance` — if a new query needs to run against a large live table
  (`series_watcher.db` is ~13.5 GB) without becoming its own performance problem
- `kalshi-contract-review` / `docs/kalshi/` — before interpreting any Kalshi
  lifecycle/result/fee field this investigation's queries touch (already done once for
  `determined`-vs-`finalized`, reused not re-derived — see the whale_calibration/exits
  CHEATSHEETs)
- `dimensional-analysis` — on any new EV/unit-cost/P&L derivation added (this investigation's
  existing `EV_per_contract = win_rate − mean_unit_cost` formula should be checked, not just
  new ones, if a future task changes it)
- `verification-before-completion` — before recording any new finding as settled

Use `brainstorming` before proposing new remediation designs beyond D1-D5 already recorded.

Use `dispatching-parallel-agents` only for independent research tracks (e.g. running E4's
banded-EV analysis against a different series while another task investigates E3's
capture-health trough) — synthesize conclusions yourself; never accept a subagent's economic
conclusion without reconciling it against current measurements and this repo's protected-
domain policy.

## Per-task workflow

1. Read the plan file's status table, `CLAUDE.md`, the relevant research documents, the
   relevant CHEATSHEET.md files, exact mirrored Kalshi docs where relevant, and recent git
   history.
2. Re-ground: branch, HEAD, working tree, current config values relevant to the task,
   whether `feat/realtime-data-plane-remediation` has merged yet.
3. Determine whether current HEAD already answers the task (a later merge — e.g. Program 1
   landing — can change what's measurable).
4. State the question being tested and what evidence would confirm/falsify it (per
   `docs/superpowers/specs/2026-08-26-economic-strategy-effectiveness-investigation-design.md`
   §4's evidence classes — label which class each new finding uses).
5. Prefer running the app's own existing, already-reviewed functions
   (`series_watcher.reconcile()`/`funnel()`/`book_context_at_entry()`,
   `candidate_log.population_gate_summary()`) over a new reimplementation of their logic —
   only add new ad hoc analysis where no existing function answers the question (the pattern
   E4's banding and E5's regime-segmentation both followed).
6. Attempt to falsify the hypothesis, not just confirm it — the adversarial-review document
   is the standing example of what this looks like (re-check a claimed mechanism against
   real data, check an "unrecoverable" claim against `data/backups/`, check an "outage"
   hypothesis against `git log`) rather than a rhetorical pass.
7. Record negative/open results explicitly — the status report's insufficient-sample list is
   the running home for these; extend it, don't silently drop what a task couldn't resolve.
8. Do not propose a specific remediation config change — that's Program 2's job once
   approved; this skill's tasks stay on the investigation side of that line.
9. Run targeted verification proportional to what changed (a docs-only branch rarely needs
   more than confirming the query/computation is correct against a known small fixture by
   hand).
10. Before commit: `git diff`, `git diff --check`, `git status --short` — no live DB,
    credentials, `.env`, or large scratch/analysis-script output staged.
11. Commit one logical investigation task, update the plan file's status table in the same
    commit.
12. Report: task/question; evidence class used; result; whether it changes the status
    report's conclusions or insufficient-sample list; commit SHA; next task.
13. Stop.

## No premature conclusion

Do not conclude, without running the actual query against current data:

- "the original adverse-selection finding must still be true, just harder to see";
- "a specific gate is definitely miscalibrated";
- "the capture-health trough was definitely a WS problem" (or definitely wasn't);
- "paper P&L is close enough to real execution to ignore" (E7 already found a concrete
  16% thin-book counterexample; a future session extending E7/E9 should not walk that back
  without new evidence).

A finding that merely sounds consistent with the original 2026-08-17 narrative is still a
guess until it's re-verified against current, capture-health-tagged data — the population
this investigation measures keeps growing and changing shape (E1's own regime break is the
proof that "the same question, asked later, can have a different answer").
