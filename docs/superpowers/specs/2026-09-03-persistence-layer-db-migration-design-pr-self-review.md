# PR-Stage Self-Review — Persistence Layer db.py Migration Design Spec (PR #505)

Per CLAUDE.md's "nothing advances on one pass" HARD RULE: this design already cleared a full
artifact-stage cycle across two rounds (self-review + independent adversarial review → NO-GO,
14 findings fixed → a required scoped re-review of those fixes → 5 more findings fixed →
consolidation, GO). This is the third, PR-stage cycle, run against the PR as submitted.

## What's different about this pass, and why it's scoped lighter

Every load-bearing claim in this document (the API-shape comparison, the `store_stats.py`
scope correction, the fault-log figures, the module count, the `_aio_db.py` precedent) was
already independently re-derived from primary sources twice: once by the original adversarial
review, once more by the scoped re-review that specifically distrusted the first fix pass's own
claims. Re-running that same exhaustive primary-source verification a third time on unchanged
content would not be rigor, it would be motion — the PR-stage cycle's actual job here is
different: confirm nothing changed between what was reviewed and what's actually in the PR, and
catch anything a diff-scale review might see that two artifact-scale reviews focused on content
correctness could have missed structurally.

## What I checked

- `git diff` between the PR's base (`origin/main` at PR open time) and its head — confirms the
  PR contains exactly the three files the design/spec stage produced (the design doc, its
  self-review, its consolidation) plus the round-2 fix commit, no unrelated changes, no `data/*.db`
  file, no code file.
- Re-read the full, final document end to end (not per-section, the way the fix passes did) to
  check it reads as one coherent artifact rather than a patchwork of edits — the two rounds of
  fixes touched a large fraction of the document (322 + 84 lines across two commits against an
  original ~340-line draft), so a structural check for orphaned cross-references or
  contradictions between sections that weren't both touched by the same fix is worthwhile.
- Confirmed the PR body's own "Test plan" checklist accurately reflects what's actually done
  (self-review ✅, adversarial review ✅, scoped re-review ✅, consolidation ✅) versus what's
  still open (PR-stage cycle — this one, in progress; coordinator/user sign-off; implementation
  plan stage) — per CLAUDE.md's own "read the PR body... grep for checklist items" rule, applied
  to the PR I'm the one opening this time, not just the ones I merge.
- Confirmed the consolidation document's own "What GO means here" section correctly states this
  PR-stage cycle is still required and not yet run at the time it was written (it wasn't — this
  self-review is that cycle starting), and correctly restates the open sign-off requirement
  rather than treating the artifact-stage GO as sufficient to skip it.

## What I did not re-verify, and why that's a reasoned choice, not a gap

- The `data/fault_log.db` query results (I2, I3's corrected figures) — queried live twice
  already (once by the original adversarial review, once by the scoped re-review, both getting
  matching results modulo the database's own live growth between queries, which both reviews
  correctly attributed to the data being live rather than to either being wrong). A third live
  query now would either match again (no new information) or differ due to further live growth
  (expected, not a defect, already anticipated by M4/I3's own framing of these as
  point-in-time figures).
- The prototype's source at `17b2e8f` — read directly by both artifact-stage reviews, including
  actually executing its test suite twice (`8 passed` both times). Stable, immutable commit; no
  reason a third read would find something two direct reads and two live test-executions did
  not.
- `services/diagnostics/_aio_db.py`'s `schema_init` citation, `services/signal_log.py`'s
  corrected table/index/column counts, `services/diagnostics/store_stats.py`'s already-fixed
  status — each independently confirmed by direct source read in round 2's scoped re-review,
  which is the review specifically tasked with distrusting round 1's own claims about these
  exact facts.

## Self-assessment

This design document is unusually well-exercised for its stage — two independent Agent-call
reviews, one of which was itself skeptical of the first fix pass and caught real residual and
newly-introduced errors, is more scrutiny than most artifact-stage documents in this repo's
history get before even reaching PR stage. The PR-stage adversarial review dispatched next is
still a genuinely separate, memory-less pass per the HARD RULE's own requirement — but it is
reasonable, and consistent with "decide, don't over-investigate," to scope it at PR-diff-level
plus a final holistic coherence check rather than asking it to re-derive facts two prior passes
already nailed down from primary sources.
