---
name: quality-plan-task
description: Use when implementing, resuming, or reviewing a Quality Control Plane task, or when the user says to continue that work. Re-ground against current HEAD, execute one genuinely incomplete task using TDD, permanently integrate deterministic recurring checks into CI/CD when appropriate, verify, commit, and stop before the next task.
---

# Quality Control Plane Task Execution

The canonical initiative documents are:

- `docs/superpowers/specs/2026-08-24-quality-control-plane-design.md`
- `docs/superpowers/plans/2026-08-24-quality-control-plane.md`

These files are required project context for Quality Control Plane work.
If either is missing, stop and report the missing file rather than inventing
or reconstructing the plan from memory/chat context.

1. Read `CLAUDE.md`, the design spec above, the implementation plan above,
   and the exact current numbered task.
2. Re-ground the task against current HEAD:
   - `git status --short`
   - `git rev-parse HEAD`
   - relevant recent commits
   - every existing file the task proposes touching
   - relevant `CHEATSHEET.md`
   - current `.github/workflows/`
3. Determine what already exists. The plan describes intent; current code is
   implementation truth. Never create a duplicate service because stale text
   says to.
4. State the smallest remaining implementation delta.
5. Identify whether the task touches persistence, Kalshi contracts,
   observability/performance, frontend behavior, routes, background
   scheduling, or a deterministic recurring failure class. Load the
   corresponding project skill(s), including `ci-cd-guardrails` when a
   repeatable clean-checkout check can own the failure permanently.
6. Use TDD where behavior can meaningfully be tested:
   - write/update failing targeted test,
   - run and confirm expected failure,
   - implement smallest complete fix,
   - run targeted tests,
   - run relevant broader tests/checks.
7. Explicitly verify wiring:
   - router mounted,
   - scheduler actually called,
   - state initialized,
   - persistence registered for test isolation,
   - frontend module imported and bundle rebuilt,
   - config value actually consumed.
8. Apply the standing investigation-to-guard rule to every new real finding.
9. For every deterministic recurring guard created/extended:
   - prove the checker with an isolated failure fixture,
   - wire it into GitHub Actions in the same logical task unless documented
     otherwise,
   - use a separately named CI job/check when that improves failure meaning,
   - verify the workflow actually invokes the checker.
   A task is not complete merely because Claude can run the checker manually.
10. Runtime-only conditions must instead be wired into application
    diagnostics/observability; network-dependent upstream checks normally go
    to scheduled/manual CI.
11. Inspect `git diff`, run `git diff --check`, verify no live
    DBs/secrets/temp files are staged, then commit one independently testable
    unit.
12. Report task, files, tests, CI/runtime ownership, verification, new
    findings and guard disposition, commit SHA, and first incomplete next
    task.
13. Stop. Do not silently begin the next numbered task.
