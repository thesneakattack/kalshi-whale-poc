# PR-Stage Consolidation — Persistence Layer Implementation Plan (PR #484, 2026-09-03)

Reconciling this PR-stage self-review
(`docs/superpowers/plans/2026-09-03-persistence-layer-implementation-pr-self-review.md`)
and the independent adversarial review run against the PR as submitted (fresh Agent call,
no memory of the authoring/self-review session, dispatched with explicit instruction to
verify every claim from primary sources rather than trust either prior document) per
CLAUDE.md's "nothing advances on one pass" HARD RULE. This is the third and final required
review cycle for PR #484 — design stage (GO) and plan stage (GO-AFTER-FIXES, all 8 fixes
applied) both already cleared.

## Verdict: **GO** (after fixes — all applied and verified below)

The adversarial review's independent verdict was initially **NO (blocking findings)** against
the pre-fix plan text — 3 Critical, 5 Important, 3 Minor. All 3 Critical, all 5 Important, and
2 of 3 Minor findings are now fixed in the plan document (the third Minor, M3, required
verification only — confirmed no change needed). This consolidation clears the PR for merge.

## Disagreements between self-review and adversarial review

None in direction — the adversarial review **confirmed and deepened** the self-review's central
finding rather than contradicting it:

- The self-review found the DDL-centralization drift (`0e90287`) affects Tasks 3 and 5 and
  proposed routing `db.register_ddl()` through `capture_writer.py`'s constants as the fix.
- The adversarial review independently re-verified this from scratch (its own primary-source
  reads, not trusting the self-review's citations) and found the drift is **worse** than
  described in three concrete ways the self-review missed: (1) the hand-typed DDL the plan
  would have installed is not merely duplicate but **divergent** — missing `unit_cost REAL`,
  which the canonical constants bake in; (2) implementing Task 3 literally would silently
  un-pin a real regression test (`test_connect_creates_rejected_candidates_with_unit_cost_from_ddl`)
  while leaving it green, for the wrong reason; (3) `series_watcher.py`'s `_ensure_schema_aio`
  had *already* been migrated to the `raw_trades` constant by the same `0e90287` commit, making
  Task 5's original "keeps its own independent copy, unmigrated" claim doubly stale.
- The adversarial review additionally found an independent, unrelated defect the self-review's
  narrower drift-check did not cover: two of the plan's new tests (Task 3's and Task 6's
  `test_connect_closes_its_connection`) would raise `NameError` at collection time because their
  target test files have no `sqlite3` import in scope — a mechanical TDD-collection bug of the
  same class the plan-stage review's F7a/F7b caught for module aliases, but for stdlib imports
  instead.

No adversarial finding is in tension with anything the self-review claimed; the self-review's
scope (checking file drift on the specific modules Tasks 2-6 touch) was narrower than the
adversarial review's (which also independently re-verified trading-critical blast-radius claims,
call-site counts, and searched for additional drift beyond the self-review's own check). This is
exactly the complementary relationship a same-context self-review and an independent adversarial
review are supposed to have.

## Merged fix list and disposition

| # | Source | Finding | Disposition |
|---|---|---|---|
| 1 | Adversarial, Critical (C1) | Task 3's hand-typed `rejected_candidates`/`rejection_events` DDL would reintroduce duplication `0e90287` already eliminated, and is **divergent** (missing `unit_cost`), silently un-pinning `tests/test_candidate_log.py`'s existing DDL-column regression test | **Fixed** — Task 3's Step 3 now registers `capture_writer.REJECTED_CANDIDATES_DDL_SQL`/`capture_writer.REJECTION_EVENTS_DDL_SQL` directly; the original hand-typed DDL is kept only as clearly-labeled historical context (not live code); `add_column_if_missing` calls retained per `capture_writer.py`'s own stated intent; Files/Interfaces/Step 2 line citations corrected (76-145→76-113; call sites 239,287,387,439,461,481→207,255,355,407,429,449; sed range 60,146p→60,114p) |
| 2 | Adversarial, Critical (C2) | Task 5's hand-typed `raw_trades` DDL would create a two-source-of-truth split within one module, since `_ensure_schema_aio` already uses `capture_writer.RAW_TRADES_DDL_SQL` | **Fixed** — Task 5's Step 3 now registers `capture_writer.RAW_TRADES_DDL_SQL` for `raw_trades`, keeps `book_snapshots` hand-typed (genuinely unaffected by `0e90287`); the "explicitly out of scope" note rewritten to state `_ensure_schema_aio` already shares the `raw_trades` constant, only `book_snapshots` remains independently duplicated; Files/Interfaces/Step 2 line citations corrected (151-207→151-186; call sites 458,484→419,445; sed range 145,207p→145,186p) |
| 3 | Adversarial, Critical (C3) | Task 3's and Task 6's new `test_connect_closes_its_connection` tests call `sqlite3.connect` with no `sqlite3` import in scope in their target files — `NameError` at collection, independent of the DDL drift | **Fixed** — added function-local `import sqlite3` to both tests, matching each file's own existing local-import convention |
| 4 | Adversarial, Important (I1) | Task 1 ships `db._DDL_REGISTRY` without acknowledging `capture_writer._STORE_DDL`, a second registry already covering the same three tables | **Fixed** — added a PR-stage note to Task 1 explaining the two are complementary (C1/C2's fixes make `db._DDL_REGISTRY`'s entries for those three tables *reference* `capture_writer.py`'s constants, not re-declare them), not a third independent copy |
| 5 | Adversarial, Important (I2) | Plan's freshness premise ("Tier 0 still code-not-landed") is now false — Tier 0 landed (PRs #499/#501) | **Fixed** — corrected in three places: the plan's opening freshness note, the Global Constraints "Tier 0 dependency" bullet, and Task 7's own intro (noting Tier 0's five already have their own independent closing-connection fix, not urgent, optional future `db.py` consolidation) |
| 6 | Adversarial, Important (I3) | Task 4 states "six call sites" then lists ten line numbers — internal inconsistency; true count is 10 call sites across 6 functions | **Fixed** — corrected to "10 call sites... across six functions," line numbers refreshed |
| 7 | Adversarial, Important (I4) | Task 4 misattributes `_settlement_resolver_loop()`/`_SCHEDULER_TRIGGER_INTERVAL_SEC` to `services/settlement_resolver.py`; both are actually in `main.py` | **Fixed** — corrected file attribution in Task 4's "why this number" prose and its Step 3 code comment; the underlying concurrency claim (verified true by the adversarial review) is unchanged |
| 8 | Adversarial, Important (I5) | Systematic line-number drift across Tasks 2-5's citations and `sed` ranges (Task 6's were already exactly right) | **Fixed** — every cited line range and `sed` command in Tasks 3, 4, 5 updated to current `main`, each with an explicit "PR-stage correction" note explaining why (avoids silently overwriting the historical record of what the plan originally said) |
| 9 | Adversarial, Minor (M1) | Self-review's own weakness #4 (whether anything outside `candidate_log.py` depends on its local `_add_column_if_missing`) was flagged as unverified, not closed | **Fixed** — added a PR-stage update citing the adversarial review's own `git grep` confirmation: safe to delete |
| 10 | Adversarial, Minor (M2) | Task 6's two new tests redundantly re-set `DB_PATH`, which the target file's autouse fixture already does | **Fixed** — redundant lines removed, replaced with a one-line comment pointing at the existing fixture |
| 11 | Adversarial, Minor (M3) | Task 10's "18 net new tests" claim needs re-counting once C1-C3 land, in case any fix changed a test's shape | **Verified, no change needed** — `grep -c '^def test_'` against the fixed plan document still returns 18; none of the fixes added, removed, or split a test function, only corrected bodies/imports/citations |

Every item in the adversarial review's Critical/Important/Minor lists was applied or (for M3)
explicitly verified as not requiring a change; none were silently deferred.

## Verification of the fix pass against the fix list

Checked item-by-item post-edit, not accepted on completion claim alone, per the HARD RULE's "a
revision that silently drops a requested fix is itself a defect" clause:

- `grep` for the stale line-number citations the adversarial review flagged (`236-237`,
  `239, 287, 387, 439`, `458, 484`, `151-207`, `76-145`, `234,238`, `145,207p`, `60,146p`) → the
  only remaining hits are inside this PR's own explicit "PR-stage correction" notes, which cite
  the old numbers deliberately as historical context for why the new numbers are correct — not
  live, uncorrected claims. One additional stray hit found and fixed during this verification
  pass itself (a `candidate_log.py:76-145` citation in Task 3's design-example-correction prose,
  outside the blocks the fix list named directly) — corrected to `76-113`.
- `grep` for "six call sites" against `candidate_log.py`'s flush_now count → zero hits (now "10").
- `grep` for "settlement_resolver.py's separately-supervised" (the misattributed loop) → zero
  hits outside the corrected paragraph, which now correctly names `main.py`.
- **Code-fence balance re-derived, not assumed**: the edit that split Task 3's single DDL+`_connect()`
  code block into three pieces (live registration code, historical-DDL context, and the actual
  `_connect()` implementation) left the third piece unfenced — caught by re-running the fence
  count after every edit batch (35 evens expected, found 39 - odd - after the first pass) rather
  than trusting the edit succeeded; traced to the specific gap (`_connect()`'s definition sitting
  outside any ` ``` ` block) and fixed by adding the missing opening fence. Final count: 40,
  even, confirmed by direct re-grep after the fix.
- Task heading count unchanged (10) — no task added, removed, or restructured.
- Net-new test function count unchanged (18, `grep -c '^def test_'`) — confirms M3 needed no
  change.
- Task 6's citations (already exactly right per the adversarial review) — re-confirmed untouched
  by this fix pass, since no defect was found there beyond the redundant-fixture cosmetic (M2).

## What GO means here

This PR's design stage and plan stage were already GO (design consolidation and plan
consolidation, both merged into this PR's own commit history). This consolidation clears the
**PR stage** — the third and final required review cycle per CLAUDE.md's "nothing advances on
one pass" HARD RULE. Writing the actual code for the plan's 10 tasks remains separate, later
implementation work, covered by TDD/systematic-debugging/verification-before-completion when
picked up — this PR ships documentation only; no code, config, or `data/*.db` file is touched by
merging it.

One live, explicit dependency this consolidation does **not** resolve, named rather than
silently assumed: this plan's `services/db.py` (Task 1) and `capture_writer.py`'s `_STORE_DDL`
now sit side by side as two related-but-distinct registries for an overlapping table set (see
fix #4/I1 above) — a reasonable state for this PR to ship in (neither blocks the other, and the
fix makes `db.register_ddl`'s calls for the three shared tables reference rather than duplicate
`capture_writer.py`'s constants), but a future reader picking up Task 1's implementation should
be aware two initiatives converged on this same problem the same day, uncoordinated, and design
their actual `services/db.py` code with that context in mind rather than rediscovering it from
scratch.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
