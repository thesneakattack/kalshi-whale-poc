---
name: final-verification
description: Use before claiming a large multi-module initiative, quality-control workstream, or reliability refactor is complete. Proves the new guardrails themselves through safe fault injection, then runs the full success-path verification and performs a fresh-eyes review.
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
2. Prove runtime properties where applicable:
   - observability survives restart and retention,
   - storage health never repairs/mutates automatically,
   - quality summary makes no unexpected live Kalshi call,
   - research/report generation never applies config or trades.
3. Run full current verification matrix:
   pytest, frontend lint/build/bundle sync, static audit, contract tests,
   browser E2E, project manifest, `git diff --check`, `git status`.
4. Review every quality-audit baseline entry; no unexplained debt.
5. Inspect for accidental data/secret/trading-state changes.
6. Perform a fresh-eyes code review focused on:
   live-data isolation, hot-path blocking, unexpected network calls,
   unwired code, false-positive CI, unbounded storage/cardinality,
   restart behavior, SQLite locking, environment-dependent tests,
   undocumented Kalshi assumptions, accidental config mutation, missing
   retention/backup/docs, and silently skipped CI.
7. Fix confirmed issues in focused commits and rerun affected verification.
8. Only then claim completion with exact evidence and final HEAD.
