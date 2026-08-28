---
name: plan-task
description: Use when implementing, resuming, or reviewing any numbered task from a plan under docs/superpowers/plans/ — pass the plan path and its domain (quality, frontend, kalshi, realtime, economic, workflow). One task per invocation; HEAD is truth, no separate ledger.
---

# Plan task

One shared skeleton for every numbered plan. Domain-specific routing lives in
`domains/<domain>.md` and is the only thing that differs between initiatives.

1. Re-ground: `git branch --show-current`, `git status --short`,
   `git log origin/main..HEAD --oneline`; read the plan; the first task whose
   deliverable is absent from HEAD is the task. Current code/tests/git are
   truth — never re-implement what exists, never keep a separate ledger.
   `ListAgents`: if another session is on this branch or names files this
   task touches, stop and coordinate before editing.
2. Load `domains/<domain>.md` for this plan and follow it.
3. State the smallest remaining delta before editing.
4. `superpowers:test-driven-development` where behavior is testable;
   `superpowers:systematic-debugging` for anything unexpected.
5. Verify targeted only: the task's named test files and checks, plus wiring
   (router mounted, scheduler called, config consumed, CI invokes the
   checker). CI owns the full suite; do not run it locally.
6. Investigation-to-guard: classify any real new failure class (runtime
   diagnostic / CI guard / shared logic / already covered / one-off) and
   record the disposition in the commit message.
7. Safety: never touch live `data/*.db`, real-trading gates, the kill switch,
   or CORS; `docs/kalshi/` before any Kalshi-shaped assumption.
8. Before commit: `git diff --check`, `git status --short`, stage specific
   paths only. Tick the task's checkboxes in the plan in the same commit.
9. Commit one task; push;
   `gh api repos/thesneakattack/kalshi-whale-poc/commits/$(git rev-parse HEAD)/status`;
   on red, fix narrowly and push again — never weaken a guard.
10. Report: task, HEAD before/after, delta, verification, guard disposition,
    next incomplete task. Stop — do not start the next task. Open the PR
    only after the plan's final task (`superpowers:finishing-a-development-branch`).

Stop conditions: a canonical plan/spec file is missing; a guard would have to
be weakened; a change would touch real-trading gates, the kill switch, or
live `data/*.db`; a measured budget in the spec is exceeded with no
documented mitigation (report the number, don't raise the budget).
