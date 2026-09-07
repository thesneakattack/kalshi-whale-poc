# Consolidation: whale-confidence-scoring-remediation plan catch-up review (2026-08-31)

Per CLAUDE.md's "nothing advances on one pass" HARD RULE — same situation as the kalshi
plan: written 2026-08-30, design already reviewed (2 rounds), plan stage never was.

- Investigative pass: found a real, load-bearing regression risk — Tasks 2 and 15 both
  rewrite `resolved_signals_with_factors()`'s SQL SELECT in a way that silently dropped
  the `series` column a same-day-but-unrelated commit (`5bb29be`) had already added and
  that `services/whale_calibration/README.md` documents as feeding a real report field.
  Task 16's full rewrite of `generate_calibration_report` had no field anywhere for
  `by_series` (same commit, same field family) — also a silent regression. All fixed:
  `series` restored to both SELECTs, `by_series` restored to Task 16's new report shape
  with a regression-guard test added. Plus 6 stale line-number citations (Tasks 8, 11,
  12, 16) corrected.
- Independent adversarial review: re-derived every correction, all CONFIRMED accurate —
  but found a **significantly larger regression than the first pass caught**: Task 16's
  restructuring moves `per_factor`/`current_weights` from the report's top level into
  `report["accuracy"]`, and two REAL, currently-live consumers of the old flat shape
  were never in Task 16's scope — `services/whale_calibration/calibration_history.py`'s
  `record_snapshot` (would raise `KeyError` on the next scheduled auto-apply snapshot)
  and `frontend/src/js/advisory-calibration.js` (the entire whale-confidence dashboard
  panel would break live). Also found the Files-list line-number fix from the first pass
  was incomplete — several of the same tasks' Step-3 prose still carried the old numbers
  a few lines below where the header was already fixed. And one vacuous test (Task 2's
  ordering test asserted only `len(rows) == 3`, which passes with or without the `ORDER
  BY` the task exists to add).

No disagreement to adjudicate — every adversarial finding was additive verification or a
genuinely missed issue, not a contradiction of the first pass. Both consumers added to
Task 16's Files list and its own text corrected from a hedged "if any exists" to a
confirmed, concrete instruction; all remaining stale line numbers fixed; Task 2's test
tightened to actually assert ordering via the now-restored `series` column.

**GO.** This catch-up cycle is the clearest demonstration yet of why the review-cycle
exists: the calibration_history.py/dashboard regression would have shipped silently
under a plan that itself already went through one review pass. Ready to execute starting
with Task 1, once the kalshi plan (first in sequence) merges to main.
