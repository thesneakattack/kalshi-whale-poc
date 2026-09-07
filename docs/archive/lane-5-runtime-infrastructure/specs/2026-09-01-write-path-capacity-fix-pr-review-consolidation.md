# PR #409 Review Consolidation

Reconciles PR #409's own self-review (inline, throughout implementation) with
the independent adversarial review (fresh Agent call, no memory of this
session, `docs/archive/lane-5-runtime-infrastructure/specs/2026-09-01-write-path-capacity-fix-pr-review.md`, moved there 2026-09-06, planning-lanes migration).

## Verdict: code review passes GO. Merge still blocked on Task 7 (unchanged from before this review).

The adversarial review's NO-GO had two components. One was a request for
fixes to real findings - both now addressed and rechecked against the fix
list, not re-litigated from scratch. The other was that Task 7's own
required gate (spec section 6) was still unmet - that was already true
before this review started and this review didn't change it; it isn't a new
finding, it's a restatement of the plan's own pre-existing requirement.

## Fix list, checked item by item

1. **`analytics/routes.py`'s `population_gate_summary()` and
   `whale_calibration/routes.py`'s `_build_report()` still share
   `tick_executor`'s pool, unaddressed and unmentioned by the PR.**
   Re-verified directly (grepped both files, confirmed both still route
   through `await tick_executor.run(...)`). **Not folded into this PR** -
   deliberately: their query cost against real data hasn't been measured,
   and CLAUDE.md's data-plane HARD RULE forbids changing capacity/isolation
   without first identifying the measured bottleneck. Filed as issue #410
   instead, with the reasoning stated in the issue itself. Disclosed in
   `services/quality/routes.py`'s comment (commit `a730425`) rather than
   silently dropped. **Fixed as: disclosed + tracked, not code-fixed** - the
   correct outcome per this repo's own rules, not a partial fix.

2. **`market_history.py`'s circular-import claim doesn't reproduce.**
   Independently re-tested (not trusting either the original claim or the
   reviewer's contradiction): added the module-level import for real this
   time (the first attempt during this recheck was invalid - it deleted the
   local import without adding a module-level one, producing a `NameError`
   that proved nothing), then ran both `python -c "import main"` and
   `python -c "import services.market_history"` inside the container - both
   clean. Confirmed the reviewer was right and the original Task 3 claim was
   wrong (its "confirmed" evidence was actually reproducing signal_log.py's
   real issue, not market_history.py's). Fixed: switched to the module-level
   import, corrected the docstring to state the actual mechanism (which
   import style each module uses to reach the other) rather than a false
   "same package, same problem" generalization. Full `test_market_history.py`
   suite reran clean (27/27) after the fix.

3. **Stale "Offloaded via tick_executor" comment above the line rewritten to
   use `_diagnostics_pool`.** Fixed directly (commit `a730425`); also used
   the same edit to fold in item 1's disclosure rather than leaving that
   comment out of date on two counts.

## Claims the adversarial review confirmed correct (re-verified, not just re-stated)

- Both root-cause diagnoses (whale-scoring per-trade connections; diagnostics
  starving `tick_executor`).
- `signal_log.py`'s circular-import fix is genuinely required (reviewer's own
  live repro, independent of the original claim).
- `base.py`'s async type-consistency fix - reviewer grepped every provider
  and every caller, found nothing missed.
- The 5.0s busy_timeout claim - empirically checked in-container.
- Connection-cache thread-safety against the untouched write paths.
- Test result claims: 331 targeted tests pass exactly as stated; full suite
  regression (2991 passed / 5 failed, all pre-existing, unrelated to this
  diff - confirmed independently by both the reviewer and Task 8's own
  earlier live check).
- The PR body's own honesty about Task 7 being incomplete - reviewer found
  no understatement of what's still required.

## What remains before merge

**Task 7 only** (issue #407): a pre-merge baseline on `main` under at least
10 minutes of real load, then the same reads post-merge, per the plan's own
steps. Complicated further by a mid-session change (market discovery
disabled, watchlist-only) that lowers current trade volume - a fresh baseline
is needed once load patterns are representative again, not a stale one
captured under today's reduced-discovery conditions. This is an empirical
gate requiring real elapsed time, not something a review cycle can close.

## Disposition

Both real findings from the adversarial review are fixed and independently
rechecked (not accepted on the revision's own completion claim). No new
scope introduced beyond what the fix list required. The code-quality side of
the "nothing advances on one pass" cycle is satisfied for this PR. Task 7
remains the sole blocking gate, tracked at issue #407 and in the PR body's
own Test Plan checklist.
