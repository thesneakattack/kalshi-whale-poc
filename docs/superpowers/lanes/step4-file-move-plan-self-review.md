# Self-review: `step4-file-move-plan.md`

Date: 2026-09-06. Same author/context as the plan itself — this is the
cheap, same-context consistency pass per CLAUDE.md's "nothing advances on
one pass" HARD RULE, before the independent adversarial review.

## What was checked

1. **Every summed total in the document, recomputed from the per-lane
   figures independently of the prose that states them** (a standalone
   verification script, not a re-read of the prose): file counts per lane,
   external-reference counts (total / code / narrative), the lanes-table
   self-citation split, the `type:plan-task` issue counts, and the
   plans/companions/specs-research breakdown per lane. All summed to their
   claimed grand totals.
2. **A real error was found and fixed during this pass**: §1's table
   originally stated Lane 9's "Plans (primary)" count as 12 and the column
   total as 38, copied from `step1-plans-classification.md`'s own summary
   line without adjusting for this document's own stated exclusion of
   `README.md` (§1/§6). `README.md` is `kind: index` in that source table,
   laned 9, and *is* one of that table's 12 — so once this document excludes
   it (correctly, per its own stated scope), Lane 9's true primary-plan count
   is 11, and the column total is 37, not 38. This propagated into the
   "37 + 25 + 184 + 1 UNDECIDED + 1 README.md = 248" reconciliation sentence,
   which also needed the same correction (previously written with 38). Both
   are fixed in the committed version; the verification script's own initial
   run (using the numbers as first drafted) failed exactly one check
   ("plans primary: got=37 want=38") before the fix and passed all checks
   after.
3. **Three individual files' reference counts were manually re-derived** with
   a bare `git grep -c -F` command, independent of the Python script, and
   compared against the script's own recorded JSON: `2026-08-30-whale-
   confidence-scoring-remediation-implementation.md` (2, matches),
   `2026-08-30-data-retention-pruning.md` (1, matches), `2026-08-25-
   frontend-modularization.md` (19, matches — the manual check initially
   looked like "10" by miscounting distinct files rather than summing the
   count column per file, corrected before being trusted).
4. **Every path in the master 248-file list was confirmed to exist on disk,
   exactly once** (already done during the plan's own construction, not
   redone here, but re-confirmed as a step that happened before any number
   built on top of it was trusted).
5. **Scope boundaries re-read against the document's own stated exclusions**:
   `README.md` and the 1 UNDECIDED specs file are excluded from every table,
   every total, and Appendix A — confirmed by grep (`grep -c
   "2026-08-27-backend-services-modularization-design" step4-file-move-
   plan.md` finds it only in the exclusion-explaining prose of §1 and §6,
   never in a per-lane count or Appendix A listing; same check for
   `README.md`).
6. **Internal consistency of the batch-order narrative against the tables
   it cites**: caught and fixed one transcription slip while drafting §4
   (the sorted reference-count list originally placed Lane 6 (42) after
   Lane 9 (45), which is numerically backwards — corrected to `... < Lane 6
   (42) < Lane 9 (45) < ...` before this review, verified again here by
   re-sorting the same 8 numbers independently).

## What this review did not re-derive

This is a same-author, same-context pass — it re-checks the document's own
internal arithmetic and stated scope boundaries, not the underlying claims
against primary sources a second time (that is the adversarial review's job,
run from a separate context with no memory of this session, per CLAUDE.md).
In particular, this review did not re-run the 246 individual `git grep`
invocations, did not re-fetch the GitHub issue data, and did not re-parse the
two source classification tables from scratch — it re-derived the *sums* from
the *already-computed* per-file/per-lane figures, and manually re-checked 3
of 246 files' individual counts as a plausibility spot-check, not all 246.

## Unaddressed scope check

Read back against the task's six numbered asks:

1. Master file→lane resolution with cross-checks — done, including two
   genuine reconciliation findings (the plans table's own `**4**` markdown
   artifact, and the specs/research table's stale 176-vs-185 header count),
   neither papered over.
2. Directory structure proposal, precedent check, plans/specs/research
   split decision with a code-dependency check — done (§2).
3. Live reference-count re-derivation + GitHub issue counts, full (not
   sampled) check of the 76 `type:plan-task` issues — done (§3).
4. Batch order with justification against size/status/dependencies — done
   (§4), including one hard dependency not in the design doc's own
   touchpoint list (§4/§6 item 3).
5. Review-companion atomicity rule + spot check — done (§5), with a fuller
   check (all 95 content-detected companions, not a sample) than the task
   required.
6. Explicit non-decisions — done (§6), 6 items.

No numbered ask was skipped. The one substantive open item this self-review
flags for the adversarial review to specifically re-derive independently
(rather than trust from this document): **the `PLANS_DIR`/`quality_
coordination.py` hard-dependency finding in §4** is the single highest-value
claim in this document to falsify or confirm from primary sources, since it
is new (not inherited from the design doc or either classification table)
and materially affects the recommended batch order for 4 of the 8 lanes.

## Verdict

Self-review found one real arithmetic error (now fixed) and confirmed the
rest. Ready for adversarial review.
