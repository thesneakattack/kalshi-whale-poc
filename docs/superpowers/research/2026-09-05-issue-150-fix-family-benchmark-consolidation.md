# Consolidation: issue #150 fix-family benchmark

Reconciles the stage artifact
(`2026-09-05-issue-150-fix-family-benchmark.md`), its self-review, and an
independent adversarial review (fresh Agent-tool call, no memory of this
work, re-derived every load-bearing claim from primary sources: source
reads, `git log`/`git show`, `gh issue view`, a live read-only DB query,
and by actually re-running both benchmark scripts fresh). Lean form per
the 2026-09-05 direct instruction.

## Adversarial review's verdict and findings

**GO-WITH-FIXES.** The reviewer independently re-derived and CONFIRMED all
seven checked areas:

1. Family 1 already shipped - confirmed, with one citation defect (see
   fixes below).
2. `busy_timeout` claims - confirmed; re-ran `family2_busy_timeout_mechanism.py`
   fresh, reproduced the doc's numbers closely (5.0049-5.0050s ceiling vs
   doc's 5.006-5.007s; 0.00026s WAL-reader latency vs doc's 0.00024s; the
   chained-holder scenarios reproduced the same pattern).
3. `handler_exceptions_total: 0` / `handler_timeouts_total: 30` reasoning -
   confirmed against `gh issue view 150 --comments` and against
   `services/kalshi/websocket.py:1460-1510`'s actual except-branch
   structure.
4. `series_evaluator.record_trades_observed_bulk()` never called -
   confirmed via independent source read, independent grep, an
   independent live read-only query (594.37 hours, matching the doc's
   594-hour figure), and confirmed issue #621 exists and matches.
5. Family 1 benchmark numbers - confirmed by re-running
   `family1_pool_isolation.py` fresh (same qualitative shape, similar
   magnitudes); flagged a real citation error (see below) and a
   worth-noting but non-blocking caveat about GIL-monopolization not
   being covered by the pool-isolation claim.
6. No overreach found; "no application code changes" independently
   confirmed via `git status --short` / `git diff --stat origin/main...HEAD`.
7. Two additional citation errors found (see below).

## Disagreements between self-review and adversarial review

None on substance. The self-review's own earlier pass (fixing Section 1's
synchronization bug and Section 4's unhandled-exception bug, and adding
the `series_evaluator` correction) held up under adversarial re-derivation
- the adversarial reviewer explicitly re-ran the "fixed" Section
4 scenario and confirmed no bug remains. The one place the self-review
overstated itself (claiming all three commit hashes were "re-checked... not
typed from memory," when in fact the check missed a wrong commit
attribution) is not a disagreement between the two reviews - it is the
adversarial review correctly catching a real gap the self-review claimed,
but did not actually close. Adjudicated in the adversarial review's favor
(it is independently re-verified, cites the exact `git log`/`git show`
output that resolves it).

## Merged list of required fixes (all four applied)

1. **Fixed**: `_candidate_retry_pool.py` provenance corrected in both the
   main doc and `bench/family1_pool_isolation.py`'s docstring - added by
   commit `c6f3295` (2026-09-04, "feat: dedicated 1-worker pool for
   candidate-retry scoring (#563)"), not `9b55c1b`/2026-09-03.
   `9b55c1b` is real and does wire `score_recovered_trade` onto the pool,
   two minutes later the same day - now cited accurately as that, not as
   the file's origin. Independently re-verified via a fresh
   `git log --diff-filter=A --follow` and `git show --stat` on both
   commits before applying the fix (not accepted on the adversarial
   review's assertion alone).
2. **Fixed**: "1,640 prints/min" quote re-attributed to
   `services/whalewatchers/kalshi_trade_tape.py:64` (confirmed via a
   fresh `grep`), not `services/kalshi/websocket.py`, in both the main doc
   and `bench/family1_pool_isolation.py` (two occurrences there).
3. **Fixed**: self-review's overstated claim about commit-hash checking
   corrected in place, naming the actual gap the adversarial review found.
4. **Fixed**: added a caveat paragraph to the Family 1 section
   cross-referencing the GIL-monopolization possibility already implicit
   in the Family 2 "what remains unexplained" section, so the pool
   isolation claim is not read as broader than what it actually tested
   (worker-slot leakage from a blocked-and-idle thread, not a CPU-bound
   thread that never yields).

None of the four fixes changes the doc's headline conclusions or
recommendation - all are citation/provenance/scope-precision corrections.
Both benchmark scripts were re-run after applying the fixes (comment-only
changes, no logic touched) to produce fresh, current `bench/out/*.json`
output for the commit, since the adversarial review's own re-run had
already overwritten the originally-committed files in place.

## GO / no-go

**GO.** Ready to commit, push, and open a docs-only PR per #601/#603's
precedent. Per this task's own instruction, the PR is opened for review
and not merged by this session.
