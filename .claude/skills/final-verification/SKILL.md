---
name: final-verification
description: Use before claiming a large multi-module initiative, quality-control workstream, or reliability refactor is complete. Proves the guardrails themselves through safe fault injection, verifies CI/runtime ownership, then runs the full success-path matrix and a fresh-eyes review.
---

# Final Verification

Do not equate "tests pass" with "the guardrail works."

1. In isolated fixtures/temp repos, deliberately prove applicable guards:
   - unmounted router is detected,
   - unwired scheduler is detected,
   - test access to repo `data/*.db` is blocked,
   - missing frontend/backend route contract is detected,
   - docs drift detects changed content,
   - stale project manifest is detected,
   - browser E2E detects severe frontend failure.
2. For every deterministic guard above, verify the permanent CI workflow/job
   actually invokes it. A manual-only checker is incomplete unless there is a
   documented reason.
3. Prove runtime properties where applicable:
   - observability survives restart and retention,
   - storage health never repairs/mutates automatically,
   - quality summary makes no unexpected live Kalshi call,
   - research/report generation never applies config or trades.
4. Run the current verification matrix:
   pytest, frontend lint/build/bundle sync, static audit, contract tests,
   browser E2E, project manifest, `git diff --check`, `git status`.
5. Inspect `.github/workflows/` and classify every planned guard:
   PR/push CI, scheduled/manual CI, runtime diagnostics, both, or intentionally
   not automated.
6. Review every quality-audit baseline entry; no unexplained debt.
7. Inspect for accidental data/secret/trading-state changes.
8. Perform fresh-eyes review focused on:
   live-data isolation, hot-path blocking, unexpected network calls,
   unwired code, false-positive CI, unbounded storage/cardinality,
   restart behavior, SQLite locking, environment-dependent tests,
   undocumented Kalshi assumptions, accidental config mutation, missing
   retention/backup/docs, and silently skipped CI.
9. Fix confirmed issues in focused commits and rerun affected verification.
10. Only then claim completion with exact evidence and final HEAD.
