# Self-review, round 2: step-1 plans classification

Date: 2026-09-06. Follows the adversarial review
(`step1-plans-classification-adversarial-review.md`, GO-WITH-REQUIRED-FIXES)
and applies its findings.

## What actually happened, stated as plainly as the first self-review was

The first self-review found an arithmetic inconsistency (38 vs 37) and
"resolved" it with a conclusion the adversarial review then proved
false: the count was never wrong, and no lane assignment was inflated.
Separately, and worse: an earlier retraction I sent to two peer sessions
(over Lane 9) had already been applied to this table as prose — but
**only as prose**. The `lane` column on both affected rows was never
touched, and the prose itself quoted design text that does not exist in
the merged document. Neither error was caught by the first self-review,
which checked the fix-list items it knew about but did not re-derive
the two corrected rows against source.

This round does that: every claim in the corrections below was checked
directly (`gh issue view`, `grep`, reading the actual plan files), not
carried forward from either the retraction or the adversarial review's
own text.

## Fixes applied, each independently verified before writing

1. **Header:** "not merged to `main`" was stale — PR #640 merged mid-review.
   Corrected to cite `main` at `8f4976a` directly.
2. **`backend-services-modularization.md`:** lane 9→**4**. Read the Goal
   text and Task 1 myself: "history" is the first-named destination
   package and Task 1's own subject — clause (d)'s first-stated tiebreak
   reproducibly gives Lane 4. Removed the fabricated §5 quote.
3. **`tier1-backend-hygiene.md`:** reverted to its original, correct
   state (lane 5, unchanged) with the bad addendum removed. Cross-
   checked against `49`'s independent finding of the identical shape
   (`#56`) in a different table, also left `RULE-GAP` rather than
   forced — two independent sessions converging on the same treatment
   is stronger evidence than either alone that `RULE-GAP` was the right
   call originally, and my "fix" was a regression.
4. **`event-scoped-me-gate.md`:** status `declined`→`superseded`.
   Verified directly: `#277` is CLOSED (the retired plan's own tracking
   issue — correctly closed) and `#289`-`#293` are all OPEN (real,
   currently-tracked successor work). The row's prior claim "zero of its
   tasks will ever run" was checked and found false.
5. **`economic-strategy-effectiveness-investigation.md`:** status
   `done`→`active`. Verified `#76` (its own umbrella tracker) is OPEN.
6. **`economic-strategy-remediation.md`'s G5 entry:** the claim "all five
   candidate tasks land in Lane 4" was checked against the plan's own
   P2-1 through P2-5 sections and found false — P2-1 touches both
   `candidate_log.py` (L4) and `diagnostics/diagnostics.py` (L6), P2-2
   touches `diagnostics/capture_health.py` (L6), P2-3 touches
   `series_watcher.py` (**L1**, not L4 — verified against §3's own
   ordering-tiebreak precedent for that file). At least three lanes, not
   one homogeneous mistake in the other direction.
7. Lane-counts and status-counts summary tables updated to match all of
   the above; the `done` file-list, `active` file-list, and
   `declined`/`superseded` split all recomputed by hand against the
   corrected rows, not assumed.

## What I did not change, and why

- The `RULE-GAP` flag stays on both corrected-lane rows (items 2 and 3
  above). The adversarial review's own recommendation was "apply clause
  (d)," not "this stops being a gap" — clause (d) produces a
  reproducible answer, but an ordering-tiebreak is not a considered
  subject, and the row text says so.
- G9's flag (economic-investigation and 3 sibling rows) — the
  adversarial review said this "isn't a rule gap," and I largely agree
  the reading is resolved ("subject wins over lane-name genre"), but I
  left the language acknowledging it's a resolved-but-undocumented
  reading rather than restructuring the gap register's numbering under
  time pressure. Renumbering the G-index carries its own error risk for
  a labeling nicety; noted rather than acted on.

## A real, now twice-confirmed design gap, not fixed here on purpose

Both corrected rows plus peer `49`'s independent `#56` finding are the
same shape: a bundled application-code initiative, independent tasks,
several lanes, no named subject. The design has no clean mechanism for
this — clause (d) is available and reproducible but is an artifact of
list order, not a meaningful assignment. This is real feedback for
whoever revises the design next (a "no-subject batch" rule, or an
explicit "split at population time, don't force one lane" escape hatch)
and is out of scope for this table pass to invent unilaterally, having
already caused harm once tonight by inventing a rule mid-table.

## Verdict

Ready for a fix-list recheck (not a full re-review — nothing here adds,
merges, or removes a lane; every change is a row-level correction
against verified source). The pattern across both self-review rounds is
the actual lesson: an unverified "resolution" is worse than an honestly
flagged gap, because it reads as settled when it isn't.
