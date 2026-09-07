# Consolidation: `step4-file-move-plan.md`

Date: 2026-09-06. Reconciles the plan artifact, its same-context self-review,
and the independent adversarial review (fresh Agent call, no memory of this
session, per CLAUDE.md's "nothing advances on one pass" HARD RULE) into one
GO/no-go verdict.

## Inputs

- `docs/superpowers/lanes/step4-file-move-plan.md` — the plan (914 lines:
  methodology, master file→lane resolution, directory structure proposal,
  live reference-count/GitHub-issue re-derivation, batch order, companion
  atomicity rule, explicit out-of-scope items, and a generated Appendix A
  with the full per-lane file listings).
- `docs/superpowers/lanes/step4-file-move-plan-self-review.md` — same-author
  pass: recomputed every summed total independently of the prose stating it,
  found and fixed one real error (Lane 9's primary-plan count and the
  `plans/`-table grand total both carried a stale `README.md`-inclusive
  figure — 12/38 instead of 11/37 — before the fix), manually re-derived 3
  individual files' reference counts against the script's own recorded
  output, and confirmed both excluded files (`README.md`, the 1 UNDECIDED
  specs file) genuinely never appear in a counted table or Appendix A
  listing.
- Adversarial review (this session's own dispatched Agent call, transcript
  above/attached to this task) — independently re-derived: the 248-file
  total count; 3 full lanes' file lists plus a full 9-lane cross-tally
  (reproducing 37 primary plans / 25 companions / 184 specs-research = 246,
  and independently reproducing the corrected Lane 9 = 11 figure through its
  own fresh regex tally, not by reading the plan's stated number); both
  stale-source-table findings (the `**4**` markdown artifact,
  the 176-vs-185 header/table mismatch); the `PLANS_DIR`/`quality_
  coordination.py` hard dependency down to exact line numbers and code
  content; the live GitHub issue counts (87 raw "Plan:" hits → 6 genuine
  `Plan: `-prefixed, 76 open `type:plan-task`, matching down to the specific
  issue numbers); 2 individual files' reference counts via independent
  `git grep`; the batch-order reasoning's internal consistency; and the
  scope-exclusion claims. Verdict: **GO**, zero errors found.

## Disagreement between the two reviews

None. The self-review found and fixed the one real defect (the stale
12/38 vs. 11/37 figures) *before* the adversarial review ran; the
adversarial review's own independently-constructed full-table tally
reproduced the corrected figures (11, 37) from primary sources without
being told what the "right" answer was, which is itself a second,
independent confirmation that the fix was correct, not merely
self-consistent. No finding from either review contradicts the other.

## Merged fix list

**Empty.** The one required fix (Lane 9 primary-plan count and the
plans-table grand total) was identified and applied during self-review,
before the adversarial review pass, and that pass's independent re-derivation
confirms the corrected numbers rather than surfacing a new one. No further
fixes are outstanding from either review.

## Judgment calls surfaced (not defects — carried forward to the report to David, not resolved here)

Both reviews treat the following as legitimate, disclosed judgment calls
rather than errors, and this consolidation agrees:

1. The `PLANS_DIR`/`tools/quality_coordination.py` hard dependency (§4/§6
   item 3 of the plan) — a genuine functional regression for the 5 currently-
   `active` primary plan docs once archived, with two plausible fixes and no
   code-level resolution attempted here (out of scope for a file-move plan).
2. The "Kalshi ingestion first" tension over Lane 1's near-last batch
   position (§4/§6 item 4) — explicitly flagged as a reading that may be too
   narrow, not decided unilaterally.
3. The batch order (§4) blends three axes (reference count, active-status
   fraction weighted toward active *primary plan* docs specifically because
   of finding #1, and the Lane-9-must-be-last hard constraint) via reasoned
   judgment rather than a single mechanical formula — the adversarial review
   confirms this is internally consistent, not that it's the only defensible
   ordering.
4. The scope decision to exclude self-citations within `docs/superpowers/
   lanes/` uniformly across all three planning directories, rather than only
   within each file's own single home directory (§6 item 5).

## Verdict: **GO**

The plan document, its self-review, and the independent adversarial review
all agree. No merged fix list item remains. This document is ready to report
back to David per the task's own instructions — branch commit and push, no
PR opened yet (his call, per the task).
