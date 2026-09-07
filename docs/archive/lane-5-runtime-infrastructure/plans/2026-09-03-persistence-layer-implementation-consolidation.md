# Consolidation — Persistence Layer Implementation Plan (2026-09-03)

Reconciling `docs/archive/lane-5-runtime-infrastructure/plans/2026-09-03-persistence-layer-implementation.md (moved there 2026-09-06, planning-lanes migration)` (commit
`137d57d`), its embedded Plan self-review, and the independent adversarial review
(`docs/archive/lane-5-runtime-infrastructure/plans/2026-09-03-persistence-layer-implementation-review.md (moved there 2026-09-06, planning-lanes migration)`, commit
`0080397`, a fresh Agent call with no memory of the authoring session) per CLAUDE.md's
"nothing advances on one pass" HARD RULE.

## Verdict: **GO**

The adversarial review's independent verdict was GO-AFTER-FIXES: the plan correctly
translates all seven of the design's top-level decisions into disjoint, appropriately-scoped
tasks; the three code migrations (Tasks 3, 5, 6) were independently re-verified line-for-line
against current source and faithfully preserve every existing DDL/index/column-add call; the
trading-critical blast-radius claims (`decision_bridge.py`/`candidate_ledger.py`/
`tick_executor.py` untouched, `candidate_log.py` off the order-placement path) all hold. All 3
must-fix and all 5 should-fix items are now applied. None of the fixes change which fix
direction Task 4 takes, which module migrates first, or any other design decision — they
correct test code that would not have exercised its intended path, two test files' nonexistent
import aliases, and several citation-precision/completeness gaps. This clears the plan for
execution; per the HARD RULE's own text, a fix-list recheck is sufficient here and a full
second self-review/adversarial-review cycle is not required, since no fix changed the
artifact's scope or introduced a claim neither original pass had seen.

## Disagreements between self-review and adversarial review

None. The plan's own self-review did not claim to have run or verified its own test code
against real `sqlite_errorcode` behavior or the target test files' actual import conventions —
these are exactly the kind of concrete, mechanical defects a same-context self-review is
least likely to catch (the cheapest layer, per CLAUDE.md's own description of what
self-review is for), which is precisely why the adversarial review exists as a separate,
independent pass. No claim in the self-review is contradicted by the adversarial review's
findings; the two are complementary, not in tension.

## Merged fix list and disposition

| # | Source | Finding | Disposition |
|---|---|---|---|
| 1 | Adversarial, must-fix 1 (F6) | Task 2's falsifier test raises a manually-constructed `sqlite3.OperationalError`, which has no `sqlite_errorcode` attribute; `_is_lock_error()` gates exclusively on that attribute, so the test never reaches the branch it's named for, before or after the fix | **Fixed** — rewritten to use the file's own established `_hold_write_lock()`/`_release()` real-lock pattern (matching four neighboring tests), which produces a genuine C-extension-raised error with `sqlite_errorcode` correctly set |
| 2 | Adversarial, must-fix 2 (F7a) | Task 2's three new tests reference a bare `cw` alias that doesn't exist anywhere in `tests/test_capture_writer.py` — `NameError` at collection/run time | **Fixed** — all three tests rewritten to use the file's actual convention (a local `from services import capture_writer` inside each test function), not a new module-level alias |
| 3 | Adversarial, must-fix 3 (F7b) | Task 6's two new tests reference a bare `obs` alias that doesn't exist in `tests/test_observability.py` (which imports the module under its full name, `observability`) — same `NameError` failure mode | **Fixed** — both tests corrected to use `observability.` directly, matching the file's existing module-level import |
| 4 | Adversarial, should-fix 1 (F8/F12) | Task 4's "runs once per ~30-second tick" framing omits `services/settlement_resolver.py`'s independent, up-to-5-second, `tick_executor`-thread-concurrent caller of the same function; Task 2/10's `daemon`/`flush_now` taxonomy can't distinguish the two | **Fixed** — Task 4's rationale (both prose and the inline code comment) corrected to name both callers and their cadences; an explicit "known limitation" note added about the taxonomy's inability to distinguish them, framed as a limit on Task 10's diagnostic power rather than a defect in the fix |
| 5 | Adversarial, should-fix 2 (F9) | Task 4 says "the other four functions" after naming five (`gate_summary`, `population_gate_summary`, `clear_all`, `count_range`, `clear_range`) | **Fixed** — corrected to "five" |
| 6 | Adversarial, should-fix 3 (F10) | Task 2's "28 call sites total" for `flush_now(` doesn't match a direct recount (34); the substantive "zero pass a second positional argument" claim is independently confirmed true regardless | **Fixed** — corrected to 34 with the per-file breakdown, noting the safety claim is unaffected |
| 7 | Adversarial, should-fix 4 (F11) | Task 7's dedicated-PR exclusion list (`risk_manager.py`/`paper_broker.py`/`candidate_ledger.py`) may be short two modules (`series_evaluator.py`, `trade_category.py`) that sit closer to the live trading path than the rest of the 22 | **Fixed** — both added to Task 7's caution list (a lighter "don't bundle silently" caution, not the full dedicated-PR requirement of the top three) and to the tracking-issue body instructions |
| 8 | Adversarial, should-fix 5 (F12) | Global Constraints section cites a non-matching glob (`services/whale_stream/*_handlers.py`) for `candidate_log.py`'s rejection-recording callers, and omits `settlement_resolver.py` as a `resolve_from_market_results()` caller | **Fixed** — corrected to the actual four files plus the settlement-resolver caller, matching Task 4's own correction |

Every item in the adversarial review's must-fix and should-fix lists was applied; none were
deferred.

## Verification of the fix pass against the fix list

Checked item-by-item post-edit (not accepted on completion claim alone, per the HARD RULE's
"a revision that silently drops a requested fix is itself a defect" clause):

- `grep` for bare `cw.`/`obs.` references → zero hits anywhere in the plan document.
- `grep` for "28 call sites"/"The other four functions"/the stale glob citation → zero hits
  outside one explicit, clearly-labeled historical note describing what the earlier version
  said (in the Global Constraints fix itself, for provenance).
- Code-fence count unchanged (36, even/balanced) — every fix touched only prose and the
  specific code blocks the findings named, with fence pairs preserved throughout.
- Task heading count unchanged (10) — no task added, removed, or restructured.
- Task 2's rewritten falsifier test independently re-checked against the established
  `_hold_write_lock`/`_release` pattern used by its four neighboring tests in the same file —
  confirmed structurally equivalent (create the file/table first via `flush_now()`, hold the
  lock, call the code under test, release, assert).

## What GO means here

This plan has a design stage preceding it (already cleared GO in a separate consolidation),
so this consolidation clears the **implementation plan** stage, the pipeline's third stage.
Per CLAUDE.md's "nothing advances on one pass" HARD RULE, the next required review cycle is
the PR-stage one: after this plan (with its fixes) is pushed and a PR opened, one more full
self-review/adversarial-review/consolidation cycle runs against the PR as submitted, before
merge. Writing the actual code for the plan's 10 tasks is implementation-time work covered by
TDD/systematic-debugging/verification-before-completion, not by this cycle — but no code has
been written yet; this consolidation clears the plan document only.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
