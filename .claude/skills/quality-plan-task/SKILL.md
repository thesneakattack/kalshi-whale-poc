---
name: quality-plan-task
description: Use when implementing, resuming, or reviewing a numbered task from docs/superpowers/plans/2026-08-24-quality-control-plane.md, or when the user says to continue the Quality Control Plane work. Re-ground against current HEAD, execute one genuinely incomplete task using TDD, verify, commit, and stop before the next task.
---

# Quality Control Plane Task Execution

1. Read `CLAUDE.md`, the Quality Control Plane design spec, and the exact
   numbered plan task.
2. Re-ground the task against current HEAD:
   - `git status --short`
   - `git rev-parse HEAD`
   - relevant recent commits
   - every existing file the task proposes touching
   - relevant `CHEATSHEET.md`
3. Determine what already exists. The plan describes intent; current code is
   implementation truth. Never create a duplicate service because a stale
   task says to.
4. State the smallest remaining implementation delta.
5. Identify whether the task touches persistence, Kalshi contracts,
   observability/performance, frontend behavior, routes, or background
   scheduling. Load the corresponding project skill(s) before editing.
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
9. Inspect `git diff`, run `git diff --check`, verify no live DBs/secrets/temp
   files are staged, then commit one independently testable unit.
10. Report task number/name, files, tests, verification, new findings and
    guard disposition, commit SHA, and first incomplete next task.
11. Stop. Do not silently begin the next numbered task.
