# PR Consolidation — Event-Loop-Blocking Fix 2 (Diagnostics Widening)

Branch: `fix/aiosqlite-diagnostics-widening`, merge-base `4493a67` (origin/main).
Reconciles the self-review, the independent adversarial review, and two fix rounds
into one GO/no-go, per CLAUDE.md's "nothing advances on one pass" HARD RULE.

## Sequence

1. **Self-review** (`...pr-self-review.md`): synthesized all 6 tasks' individual
   SDD reviews + the 3-lane merge, found 3 known gaps (a cosmetic dead import, a
   pre-existing compute-redundancy note, the aiosqlite-not-in-primary environment
   gap), no new issues.
2. **Adversarial review** (`...pr-adversarial-review.md`, opus, no memory of the
   implementation): **NO-GO**. 1 Critical (C1: leaked non-daemon `aiosqlite`
   worker threads hang interpreter exit — deterministically reproduced, this
   branch's own `tests/test_diagnostics_routes.py` already hangs today), 6
   Important (I1 false reconnect-parity claim + missing liveness check, I2
   schema-init failure leaks a connection+thread, I3 factually wrong
   loop-binding rationale for aiosqlite 0.22.1, I4/I5 unmeasured performance
   tradeoffs, I6 merge causes a real app outage until image rebuild), 8 Minor.
   Every claim was independently re-derived from primary sources: the installed
   `aiosqlite`/`uvicorn`/CPython source in the container, live measurements
   against the real `data/*.db`, and executed, order-permuted test runs.
3. **Fix round 1** (opus): fixed C1 (via `threading._register_atexit`, not
   `atexit` — empirically found that `atexit.register()` does not work, because
   CPython's `threading._shutdown()` joins non-daemon threads *before* atexit
   hooks run), I1 (liveness probe + compare-and-swap eviction, matching the
   sibling `_scoring_pool.cached_read_connection()` pattern), I2 (close on
   schema-init failure), I3 (corrected the docstring/plan; confirmed the design
   spec never made the false claim), I4/I5 (documentation of the reviewer's own
   measured numbers, zero behavior change), M1/M2/M3/M7. Self-caught and fixed
   a test-quality flaw in its own I2 test before reporting.
4. **Re-review 1** (opus): **NO-GO, narrowly**. C1/I1/I2/I4/I5/M1/M2/M3/M7 all
   independently re-verified from primary sources (re-derived the CPython
   shutdown ordering itself, reproduced the atexit-vs-`_register_atexit`
   difference independently, ran the new tests against the pre-fix code and
   confirmed they fail). 3 items left: I3 had one missed location (a plan-doc
   code block still carrying both falsehoods, positioned where a future session
   could copy it back into source), plus 2 new Minor findings the fix itself
   introduced (a wrong MRO claim in a corrected comment; a bare `pop(key)` newly
   possible to `KeyError` given I1's eviction is now a second deleter of the
   same dict) and 1 unrelated pre-existing staleness (`docs/open-decisions.md`
   referencing the now-deleted `_diagnostics_pool.py` as live).
5. **Fix round 2** (same implementer, narrow scope): closed all 4 remaining
   items.
6. **Re-review 2** (sonnet, narrow scope): **GO**. All 4 independently verified
   (MRO checked directly via `issubclass()` in the container, the `pop`
   guard read in the actual file, the plan-doc correction compared against the
   two existing dated-correction patterns in the same document, the
   open-decisions.md entry cross-checked against `services/quality/routes.py`'s
   own corrected comment for consistency). Zero new breakage. Full targeted
   suite: 99 passed. C1's fix reconfirmed intact (not touched by this round,
   verified anyway).

## What is NOT yet closed, and why that's correct

- **I6** (merge causes a real outage until the container image rebuilds): a
  process/sequencing matter, not a code defect. Handled explicitly in the PR
  body and the actual merge sequence below — not a reason to withhold GO on the
  code itself.
- **M8** (branch unpushed, no CI has run yet): mechanically true until this
  branch is pushed. Real CI status will be confirmed via
  `gh api repos/thesneakattack/kalshi-whale-poc/commits/<sha>/status` before
  merge, per `.claude/rules/branching-and-ci.md` — never assumed from the push
  alone.
- **M4/M5/M6** (an order-dependent absolute-count test, one near-vacuous test,
  an undocumented-but-arguably-more-honest `mkdir` behavior change): the
  adversarial review itself said these are for consolidation to accept or defer
  on the merits, not blockers. **Deferred**, on the merits: none of the three
  is a correctness regression, and fixing them now would be scope creep on top
  of an already-large review cycle. Recorded here so they aren't lost, in case
  a future touch of these files wants to pick them up.
- `docs/next-action.md`'s stale `_diagnostics_pool.py` references: flagged by
  the fix-round implementer as out of its assigned scope (that file is rewritten
  at session end per this repo's own workflow) — will be corrected as part of
  this session's normal end-of-session rewrite, not a PR-blocking edit.

## GO/no-go

**GO.** The code is correct, tested (99 targeted + full suite unaffected),
free of the Critical/Important defects the adversarial review found, and every
fix was independently re-verified rather than trusted on the implementer's own
claim. Proceeding to: state the I6 merge/rebuild ordering explicitly, push,
open the PR, confirm real CI, run the PR-stage review cycle against the PR as
submitted (per CLAUDE.md's stacking requirement — a second, independent pass,
scoped to what changes once real CI exists and the PR is actually opened,
rather than re-litigating code this cycle already exhaustively covered), then
merge.
