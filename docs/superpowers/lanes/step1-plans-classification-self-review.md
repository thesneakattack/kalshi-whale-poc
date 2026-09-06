# Self-review: step-1 plans classification

Date: 2026-09-06. Stage 1 of the review cycle the design's §6 step 1
mandates for every step-1 table. Written by the coordinator, who
dispatched the table but did not author its rows.

## What I actually did, stated plainly

I dispatched a subagent, received a 63-row table plus a 9-entry rule-gap
register, **committed it and propagated its invented status vocabulary
to two other in-flight table-builders without verifying a single row
myself.** That is the honest description. Everything below has to be read
against it.

The propagation was, I still think, the right call under time pressure —
`49` and `ea` were mid-build, and one shared vocabulary reviewed after
the fact beats three divergent vocabularies that can never be merged.
But it converts a single-artifact risk into a three-artifact risk, and
that is a cost I chose, not one I avoided. The adversarial review has
been explicitly pointed at the vocabulary first for exactly this reason.

## Self-identified defect, found while writing the review prompt rather than before committing

**The table's own arithmetic is internally inconsistent.** It reports
lane counts over "38 lane-bearing rows" while simultaneously claiming
the real companion/plan split is "25/37". Those cannot both hold:
25 + 37 = 62, plus `README.md` = 63 total, which implies 37 plans; but
38 lane-bearing rows implies 38. One of the two figures is wrong and I
did not catch it before committing — I caught it only when composing the
adversarial reviewer's instructions, which is later than it should have
been found.

This matters beyond bookkeeping: the 24→25 companion correction is one
of the table's headline findings against the merged design, and its
credibility depends on the count being right.

**Resolved during this self-review, with the concrete consequence
stated.** Counted directly: `ls docs/superpowers/plans/*.md` = 63 files;
24 carry one of the four companion filename suffixes; 1 is `README.md`;
38 remain. So *both* figures are internally correct, for two different
bases — 38 is the count under the **suffix** rule, 37 is the count after
`frontend-modularization-freshness-check.md` is reclassified as a
companion on **content**. The table applies the reclassification to its
headline claim but computed its lane counts before it, and never
recomputed.

**The consequence is a real row error, not just a mismatched total:** if
that file is a companion, it must carry `kind: companion-of:2026-08-25-
frontend-modularization.md` and `lane: inherits`, not a lane of its own.
So one of the 38 lane assignments is wrong, and every lane count that
included it is off by one. The fix is mechanical, but it has to be
applied to the row *and* the totals together, and the adversarial pass
should confirm which lane was inflated rather than take my arithmetic
for it.

## Rulings I made on the table's gaps, and my confidence in each

- **Repo-structure work → Lane 9** (gaps G3/G4). Moderate-to-high
  confidence. Lane 9's §3 definition already covers `tools/`, `.claude/`,
  `tests/`, `scripts/` and "this lane system's own upkeep," so work
  *about* the codebase's organization fits its existing shape. I checked
  this specifically against the design's NO-GO trigger — it absorbs into
  an existing lane and therefore is a clause clarification, not a
  lane-list change. **If the adversarial pass disagrees and this
  genuinely needs a tenth lane, the whole design returns to a full
  cycle**, so this is the single highest-stakes call in this review.
- **`superseded` as a sixth status** (gap G6). Moderate confidence. The
  distinction from `declined` is real in principle — nobody decided
  against `event-scoped-me-gate`, shipped code simply overtook it — but I
  have not tested whether the boundary holds across other rows, and a
  status that applies to exactly one case is weak evidence for a new
  bucket.
- **Subject beats doc genre** (gap G9). High confidence. §3 populates
  lanes by package, not by document genre, so Lane 4's name containing
  "research" cannot claim every research-shaped doc. This one follows
  directly from the design's own construction.
- **Status definitions** (gap G1). Low-to-moderate confidence, and the
  reason the adversarial pass leads with them. They read as coherent,
  but I have not tested whether they are mutually exclusive or
  collectively exhaustive, and `active` in particular ("partly shipped
  **and** carries an open tracker") may be circular in practice, since
  the tracker's existence is often what someone was trying to determine.

## What I did not do

- Did not verify any row against source.
- Did not re-derive the lane counts.
- Did not check that every `plans/` file appears exactly once — I took
  the agent's claim that it verified this.
- Did not resolve the 38-vs-37 inconsistency before committing.

## Verdict

Not ready to be relied on. The table is a credible draft with a genuinely
valuable gap register, and its two corrections to the merged design (the
companion miscount, the undefined statuses) look real. But an artifact
whose vocabulary has already been propagated to two other sessions, with
a known unresolved arithmetic inconsistency, and zero independent row
verification, is not something to build labelling on. The adversarial
pass is the gate, and I would rather it come back NO-GO than have this
quietly become the basis of migration step 2.
