# Consolidation — Fix 2 (Diagnostics Widening) Implementation Plan

Reconciles the self-review and adversarial review of
`docs/archive/lane-6-observability-quality-safety/plans/2026-09-01-event-loop-blocking-fix2-diagnostics-widening.md`
into one GO/no-go, per CLAUDE.md's "nothing advances on one pass" HARD RULE. This is
the plan-writing pipeline stage; execution (Task 1 onward) does not start until this
document says GO.

## Self-review (same author/context, run before the adversarial pass)

Checked against the writing-plans skill's own checklist:
- **Spec coverage:** every DB-touching function `run_offline()`/`GET /api/diagnostics/series/{series}`
  reach is assigned to a task. Three gaps versus the background design spec were found
  and folded in during writing, not after: a second route file
  (`services/diagnostics/routes.py`), two series_watcher functions the spec's list
  omitted (`capture_stats`, `book_context_at_entry`), and `services/research/research.py`'s
  own caller (which forced the loop-scoped connection-cache design in Task 1).
- **Placeholder scan:** no "TBD"/"add appropriate error handling"/"similar to Task N"
  patterns. Every code block shows the real diff; "... unchanged ..." markers are used
  only for genuinely-unmodified pre-existing business logic (unchanged Python operating
  on already-fetched rows), never for new logic the plan is asking an implementer to
  invent.
- **Type/interface consistency:** `_aio_db.connection_for()`'s signature, `Check`'s
  shape, and every converted function's return type are used identically across every
  task that consumes them.

Self-review did not catch (see adversarial review below): a schema-initialization gap
in the new connection cache, a systemic test-monkeypatch breakage in
`tests/test_research.py`, a cross-loop lock-safety gap in the new module's own
concurrency design, and a missing `try/finally`. All four are the kind of finding that
requires either running the target test suite against the actual current test files or
independently re-deriving the concurrency argument from the aiosqlite library's own
implementation — exactly why the adversarial review is a separate, independent pass and
not a rubber stamp on the self-review.

## Adversarial review

Fresh, memory-less `Agent` call (background, isolated worktree), full report at
`docs/archive/lane-1-kalshi-ingestion/specs/2026-09-01-event-loop-blocking-fix2-diagnostics-widening-plan-review.md`.

**Verdict returned: NO-GO.** Confirmed the large majority of the plan's claims
(function signatures, DB_PATH values, the complete caller list via independent grep,
the aiosqlite API shape and exception re-export guarantee via Context7 + a raw source
fetch, the `aiosqlite==0.22.1` version pin) hold exactly as stated. Found 7 concrete,
sourced problems requiring fixes before execution — 2 critical/significant (a schema-init
gap that would silently regress two real tests' "no data yet" vs. "store unreadable"
distinction; a systemic break across 11 of `tests/test_research.py`'s ~17 tests), 2
moderate (a cross-loop-unsafe shared lock; a missing `try/finally`), 3 minor (an
undocumented 4th spec correction, missing route test coverage, an imprecise deletion
rationale).

## Adjudication

No disagreement to adjudicate between the two passes — the adversarial review's findings
are all new (not contradicting anything the self-review asserted), each backed by a
specific file:line citation and, for the two critical findings, a concrete reproduction
trace (which existing test fails, which line, why). All 7 are accepted as real and are
fixed in this revision, not disputed or deferred.

## Fix-list recheck (item by item, against the revised plan)

1. **Schema-init gap (Finding A)** — FIXED. `_aio_db.connection_for()` gained an
   optional `schema_init` parameter (mirrors the existing
   `services/whalewatchers/_scoring_pool.py` precedent). Task 5 Step 3 adds
   `_ensure_schema_aio()`, copied verbatim (DDL text compared line-for-line against the
   real `services/series_watcher.py:151-207` `_connect()` body) from the function this
   plan's conversion replaces. Wired into all three converted call sites
   (`funnel`/`capture_stats`/`book_context_at_entry`, Steps 4/7/8). Task 5 Step 9 now
   explicitly calls out the two at-risk tests by name and states plainly that loosening
   their assertions instead of fixing the root cause is not an acceptable resolution.
2. **`tests/test_research.py` systemic breakage (Finding B)** — FIXED. New Task 6 Step 7
   converts `_patch_every_analyzer()`'s `run_offline` monkeypatch from a plain lambda to
   an `async def`.
3. **Cross-loop-shared lock (Finding C)** — FIXED. `_aio_db.py`'s single `asyncio.Lock`
   replaced with a `dict[int, asyncio.Lock]` keyed by loop id, matching `_connections`'
   own scoping exactly. `close_for_current_loop()`/`reset()` updated to evict the
   current/all loop's lock entries too (an accompanying leak this fix would otherwise
   introduce on its own). New test (`test_locks_are_scoped_per_loop_not_shared_across_loops`)
   added to Task 1.
4. **Missing `try/finally` (Finding D)** — FIXED. Task 6 Step 6's
   `_diagnostics_and_cleanup()` now wraps the `run_offline()` await in `try` /
   `finally: await _aio_db.close_for_current_loop()`.
5. **Undocumented 4th spec correction (`config_epochs`, Finding E)** — FIXED. Added as
   item 4 in the plan's intro "corrects/extends that spec" list.
6. **No route test coverage (Finding G)** — FIXED. New Task 6 Step 8 adds two tests to
   `tests/test_diagnostics_routes.py` covering both converted routes.
7. **Imprecise `_diagnostics_pool.py` deletion rationale (Finding H)** — FIXED. Task 6's
   deletion step (now Step 9) restates the reasoning to lead with "no thread-pool
   involvement left to isolate" and explicitly names `paper_broker.db`'s own
   trading-criticality rather than implying every `run_offline()` target is low-stakes.

Also corrected while revising (not separately numbered in the review's required-fixes
list, but flagged in its Item 4 as worth fixing): the Global Constraints section's
caller list for `signal_log.resolved_signals_with_factors()` was incomplete (named only
`services/whale_calibration/routes.py` as a single call site); replaced with the
complete, independently-grepped list (`whale_calibration/routes.py` at two call sites,
`main.py:490`, `services/backtest/routes.py:21`).

Each of the 7 fixes was applied at the exact plan location the review's "Fixes needed"
section cited, and re-read after editing to confirm the fix matches what was asked
(not just that *some* edit was made at that location) — this is the fix-list recheck
CLAUDE.md's HARD RULE requires before trusting a revision, not a second full
self-review-plus-adversarial-review pass (the revision's scope — a new cache-module
parameter, a per-loop lock/try-finally, two new test files' worth of assertions — is a
direct, narrow response to findings the adversarial review already saw and described in
detail; it introduces no claim, mechanism, or file the review didn't already examine).

## Verdict: **GO**

Execution (Task 1 of `docs/archive/lane-6-observability-quality-safety/plans/2026-09-01-event-loop-blocking-fix2-diagnostics-widening.md`)
may begin.
