# Consolidation — CI pipeline audit Tier 1 fixes implementation plan (2026-09-03)

Reconciles the plan artifact
(`docs/archive/lane-9-tooling-ci-process-governance/plans/2026-09-03-ci-pipeline-audit-tier1-fixes.md`), its
same-session self-review
(`docs/archive/lane-9-tooling-ci-process-governance/specs/2026-09-03-ci-pipeline-audit-tier1-fixes-plan-self-review.md`),
and an independent adversarial review (fresh Agent call, no memory of this
session, full findings below) per CLAUDE.md's "nothing advances on one
pass" HARD RULE — this is the implementation-plan stage gate.

## Self-review findings

Spec coverage confirmed (all 4 Tier 1 items from the merged audit map to a
task); placeholder scan clean; two imprecision issues found and fixed
inline (an unmotivated `-p no:cacheprovider` flag removed; an inaccurate
"alphabetical stdlib position" instruction corrected). Full detail in the
self-review document.

## Adversarial review findings

Independent Agent call, given Bash access to the same repo/worktree and
told to assume the plan wrong until re-derived. It **executed** several of
the plan's own verification commands rather than only reading them, which
is what surfaced real defects a text-only review would have missed:

- **Task 1's core mechanism**: confirmed byte-for-byte against
  `testmon==2.2.0`'s installed `configure.py`, then went further and
  actually ran the fix end-to-end — populated a real `.testmondata`, made
  a comment-only edit, and got `testmon: changed files: 0, unchanged
  files: 339` with **0 tests run in 4.69s** (vs ~148s for the full suite).
  This is direct, executed proof the fix works, not just a plausible
  mechanism.
- **Task 1, two verification-step defects found by executing them**: (1)
  the plan's own `-q` flag on the verification commands suppresses
  testmon's report-header line entirely — as originally written, an
  executor would never see the message the "Expected" text promises. (2)
  Step 4's cleanup command chained `git checkout` onto a `docker exec ...
  sh -c '...'` call, which fails inside this specific container for a
  linked worktree (`fatal: not a git repository`) — a real, reproduced
  gotcha, not a hypothetical.
- **Task 2**: confirmed the exact before/after code and `_is_repo_data_path`
  logic; confirmed no repo code binds an early reference to `sqlite3.connect`
  that could bypass the guard; searched the entire suite for a
  crash-then-assert-durability test pattern and found none, so the one
  class of test `synchronous=OFF` could plausibly break doesn't exist here;
  confirmed `test_capture_writer`'s lock tests are governed by locking
  mode, not `synchronous`, exactly as claimed. Found one factual
  inaccuracy: `install_runtime_isolation()` is invoked from
  `tests/support/e2e_server.py` too, not only `tests/conftest.py` — both
  are equally test-only, so the safety property is unaffected, but the
  specific claim was wrong.
- **Task 3**: every citation (line numbers, function names, existing
  marker count, `pytest.ini` text, the `_SCANNERS` wiring) confirmed exactly
  right, no discrepancies.
- **Task 4**: all three "find" strings confirmed to appear verbatim and
  exactly once in `docs/woodpecker-ci.md`, unambiguous for an executor. One
  related-but-out-of-scope observation: `quality-architecture-audit.yml`'s
  own comment carries the identical stale "trusted... all true" claim Task
  4 fixes elsewhere — noted for a possible future follow-up, not blocking.
- **Cross-cutting**: independently ran the full local suite twice and got
  `3058 passed, 16 skipped` both times, confirming the plan's baseline
  claim is currently accurate.

**Verdict returned: GO WITH FIXES.** Three required fixes, all in
verification-step wording/mechanics — none in the actual code changes any
task makes:

1. Task 1 Steps 1/3/4: `-q` → `-v` (the report header is invisible under `-q`).
2. Task 1 Step 4: move the cleanup `git checkout` out of `docker exec` into
   a plain host command.
3. Task 2 Global Constraints: correct the call-site claim to name both
   `tests/conftest.py` and `tests/support/e2e_server.py`.

## Fixes applied

All three applied via direct edits to the plan document (verified after
editing that the corrected text reads coherently and the fixed commands
are internally consistent with the surrounding steps — e.g. Step 4 now
reads `docker exec ... -v ... | head -8` followed by a clearly-separated
host-level `git checkout` command, matching Step 5's existing pattern).

## GO / no-go

**GO.** No finding blocks execution — every fix required was a
verification-step correctness/mechanics issue, not a defect in what any
task actually changes; both reviews independently confirmed all four
tasks' underlying code changes are correct and safe. Proceeding to
execution via `superpowers:subagent-driven-development`, dispatching one
subagent per task (the four tasks are file-disjoint, confirmed by both
reviews), then integrating, verifying the full suite, pushing, and running
the PR-stage review cycle before merge.
