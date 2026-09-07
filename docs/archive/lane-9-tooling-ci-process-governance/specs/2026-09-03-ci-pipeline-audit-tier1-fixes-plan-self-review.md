# Self-review — CI pipeline audit Tier 1 fixes implementation plan (2026-09-03)

Per `superpowers:writing-plans`' own self-review checklist and CLAUDE.md's
"nothing advances on one pass" HARD RULE (self-review layer, before
independent adversarial review), run against
`docs/superpowers/plans/2026-09-03-ci-pipeline-audit-tier1-fixes.md`.

## Spec coverage

The spec (`docs/superpowers/research/2026-09-02-ci-pipeline-audit.md`'s
Tier 1 list) has exactly 4 items. Each has a task:

1. Fix testmon selection (`--testmon-forceselect`) → Task 1.
2. `PRAGMA synchronous=OFF` for test sqlite connections → Task 2.
3. Mark four whole-repo-scan tests `slow` → Task 3.
4. `docs/woodpecker-ci.md` drift pass (3 specific claims) → Task 4.

No spec item without a task; no task without a spec citation.

## Placeholder scan

Searched the plan for "TBD"/"TODO"/"implement later"/"add appropriate
error handling"/"similar to Task N"/vague hand-waving. None found — every
step carries either a runnable command or a literal code diff. Two real
issues were found and fixed during this pass (not placeholders, but
imprecision):

- Task 1 steps 1/3/4 included an unmotivated `-p no:cacheprovider` flag
  with no stated reason — removed (pytest's cache-provider plugin is
  unrelated to testmon's own `.testmondata` mechanism; including it added
  noise without purpose).
- Task 3 step 2 claimed `import pytest` should go "in its existing
  alphabetical position among the stdlib imports" — inaccurate, since
  `pytest` is third-party, not stdlib, and the file doesn't separate the
  two into blocks. Corrected to a plain instruction (insert after
  `import json`) with the accurate reasoning stated.

## Type/interface consistency

Not applicable in the usual sense — all four tasks are independent,
non-overlapping file edits with no shared function signatures or data
types passed between them (each task's own "Interfaces" section states
this explicitly). No cross-task naming drift is possible because there is
no cross-task interface.

## Risk check specific to this plan (beyond the generic checklist)

- Every line-number citation in the plan is flagged as "from the audit,
  may have drifted — verify with `grep`/`sed` first" rather than assumed
  fixed, since the underlying files could have changed on `main` between
  the audit (2026-09-02) and plan execution. Confirmed current as of
  writing this plan (same day) via direct `grep`/`sed` reads for all four
  tasks' target locations.
- Task 2's safety property (the pragma can never reach live `data/` paths)
  is stated as verified "by construction" and given its own explicit
  re-check step (Step 6) rather than only asserted in prose.
- Task 1's verification steps distinguish "first run on a branch with no
  cache" (expected to still run everything, correctly) from "a real
  re-run with an unrelated file touched" (expected to show real
  deselection) — this distinction matters because a naive before/after
  comparison using only the first-run case would show no visible
  difference and could be misread as "the fix didn't work."

## Verdict

No gaps found in spec coverage; two imprecision issues found and fixed
inline (per the writing-plans skill's own instruction: fix and move on,
no need to re-review). Ready for independent adversarial review.
