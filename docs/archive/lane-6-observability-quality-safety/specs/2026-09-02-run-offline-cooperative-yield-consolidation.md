# Consolidation — run_offline() Fast-Follow (Round 1)

Reconciles the self-review (`...-self-review.md`) and the independent
adversarial review (`...-adversarial-review.md`) of commit `85631d2`, per
CLAUDE.md's "nothing advances on one pass" HARD RULE.

## Result: NO-GO on round 1, fixed in round 2

The adversarial review found 2 Critical, 4 Important, 4 Minor issues in
`85631d2` (cooperative yielding + elastic connection pool):

- **C1/C2 (Critical, both fixed in `a9d06b2`)**: the elastic pool's fast
  path and locked section were not properly synchronized, allowing
  `connection_for()` to return `None` under concurrency (C1, deterministically
  reproduced) or a dead, unprobed connection sequentially with zero
  concurrency (C2, also deterministically reproduced) - both regressed
  PR #420's own hard-won self-healing liveness-probe fix. Root-caused and
  fixed by redesigning so `_round_robin` only ever advances in the same
  step that hands back a connection this function has itself just
  confirmed live or created, with every eviction/creation/return for a
  given call happening in one lock-held sequence. Two new regression tests
  (one sequential, one a 25-round concurrent stress test) both confirmed
  to fail against the buggy code and pass against the fix.
- **I1 (Important, fixed in `a9d06b2`)**: the yield-point fix's entire
  premise was falsified - aiosqlite's own awaits already yield ~1,100
  times per `run_offline()` call, so the added `asyncio.sleep(0)` calls
  and their test were vacuous. Removed rather than kept on a disproven
  claim; the correction is recorded in both `diagnostics.py` and
  `_aio_db.py`'s docstrings so a future session doesn't reach for the
  same fix independently.
- **I2/I3/I4 (docstring accuracy, fixed in `a9d06b2`)**: corrected a
  factual error (`_scoring_pool.py` runs 4 workers, not 2 - never
  independently verified before being cited as precedent) and rewrote the
  module docstring's whole narrative to match what was actually measured,
  in the order it actually mattered (query-bounding first, pool depth
  second, yield-points disproven).
- **4 Minor findings**: not individually re-triaged here: the entire
  surface they applied to (the original `_POOL_SIZE`-based design) was
  superseded by the elastic-pool rewrite in `85631d2` itself and the C1/C2
  fix in `a9d06b2` - moot rather than fixed or deferred.

## Scope note: two more commits joined mid-review, never independently reviewed

While the adversarial review of `85631d2` was in progress, this session
implemented and committed two more changes based on direct user
instruction (bounding `check_confidence_input_coverage`'s previously
unscoped query - `6073df4`, `ce9bc66`) - explicitly flagged by the
reviewer's own closing note ("the branch gained 2 more commits during my
review; findings are against 85631d2 only"). Those commits have their own
RED/GREEN test evidence and live production measurements in their own
commit messages, but have NOT yet been through an independent adversarial
pass. Given how intertwined all four commits now are (the query-bounding
fix and the pool-correctness fix both touch the same call graph), the
next step is ONE comprehensive review of the full branch diff
(`23f12e5..HEAD`), not a narrower incremental pass - dispatched next.

## GO/no-go for round 1's specific findings

**GO** - every C1/C2/I1/I2/I3/I4 finding from this round is either fixed
(with independently-reproducible-and-fixed regression tests for the two
Criticals) or moot (Minors superseded by the rewrite). Proceeding to a
full-branch review before push/PR, per the scope note above.
