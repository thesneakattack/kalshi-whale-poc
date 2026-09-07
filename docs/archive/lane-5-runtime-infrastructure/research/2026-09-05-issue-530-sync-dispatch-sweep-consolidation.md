# Consolidation: 2026-09-05-issue-530-sync-dispatch-sweep.md

Reconciles the research document, its self-review, and the independent adversarial
review into one GO/no-go, per CLAUDE.md's "nothing advances on one pass" HARD RULE.

## Inputs

- `2026-09-05-issue-530-sync-dispatch-sweep.md` — the artifact (revised in place after
  this consolidation's fix-list item below; see "Revision applied" section).
- `2026-09-05-issue-530-sync-dispatch-sweep-self-review.md` — same-author pass, flagged
  one open scope-boundary question (`record_variant()`'s async-caller status) and named
  the document's central judgment call (status-update instead of a fresh 88-handler
  sweep) as a read, not a certainty.
- `2026-09-05-issue-530-sync-dispatch-sweep-adversarial-review.md` — genuinely
  independent pass (fresh Agent call, no memory of this conversation), re-derived
  every load-bearing claim from primary sources: git log/show, `merge-base
  --is-ancestor`, direct source reads, fresh in-container live measurements, an
  independently-written concurrency-test script, and `gh pr view`/`gh issue view`.

## Adjudication — no disagreement between the two reviews to adjudicate

The self-review and the adversarial review did not conflict; the adversarial review
resolved the self-review's one open question (confirmed `record_variant()` sits inside
`async def trading_loop()`, same defect class) and found one additional, independent
defect the self-review had not caught (the commit-attribution error). Nothing here
required choosing between competing verdicts — both reviews' findings are additive.

## Findings, merged into one fix list

1. **(Adversarial, real defect — FAIL as originally written)** The "Enumeration is
   still current" table misattributed commits `6a0584c`, `2eed9a7`, `3ad2431` to
   `services/analytics/routes.py`; they actually touch
   `services/whale_calibration/routes.py`, which had its `apply` route reclassified
   (dispatched) under issue #410's track since the census — silently missed inside the
   document's "all other 16 sources: unchanged" claim.
   **Status: FIXED.** Verified by direct re-read of the revised document (see below).
2. **(Self-review, disclosed gap, resolved by adversarial review)** Whether
   `main.py:908`'s `record_variant()` call sits inside an `async def` function was
   unchecked in the original document. Adversarial review confirmed: yes,
   `async def trading_loop()` (`main.py:878`). **Status: resolved as information** —
   this is not a defect in the document (it already disclosed the gap rather than
   guessing), it's now a settled fact that strengthens the document's existing
   "same defect class, different scope boundary" framing. No further doc change
   required beyond noting it here.
3. **(Adversarial, footnote, no action needed)** `main.py:1842`'s
   `@app.websocket("/api/ws")` is invisible to the `@app\.(get|post|put|delete|patch)`
   counting convention used by both the census and this document. Checked: zero DB
   calls in that handler, so no missed BLOCKING instance. A pre-existing, repo-wide
   convention blind spot (also present in `tools/project_manifest.py`'s own route
   counter), not unique to this document. Not fixed in this document (out of scope —
   this document reconciles the existing census, it doesn't re-author its counting
   methodology) — worth a footnote for whoever next touches the census file directly,
   named here so it isn't lost.
4. **(Both reviews, independently reproduced)** Every other load-bearing claim —
   prior census exists and is merged to `main`; PR #552 is real, merged, and all 7
   calls are dispatched on disk; live timings for `quality/summary` and
   `observability/summary` (default and `hours=720`) reproduce within normal
   variance; the concurrency test (the document's single strongest claim) reproduces
   cleanly with an independently-written script; the `c4`/`next-action.md`
   coordination gap is real and currently live. **Status: no action needed, both
   reviews agree these hold.**

## Revision applied

Fix-list item 1 was applied to `2026-09-05-issue-530-sync-dispatch-sweep.md` directly
(scoped fix, not a rewrite): corrected the commit-to-file attribution, added a
provenance note disclosing the error and crediting the adversarial review, updated the
"~62 remaining" framing to "~61" throughout (three locations:
"Enumeration is still current," "Verdict on the census's remaining ... instances,"
"What this document does not do"), and added item 6 to the remaining-instances list
covering `services/whale_calibration/routes.py`'s still-open `enable`/`disable`/
`status`/`history` routes. **Checked against the fix list item by item** (not accepted
on completion claim alone): re-grepped the revised document for `whale_calibration`,
`2617b17`, `6a0584c`, `2eed9a7`, `3ad2431`, and `e530ff0` — all five locations reflect
the corrected attribution consistently; re-grepped for stray `~62`/`exactly one`
language — none remain. This is a fix-list recheck, not a second full
self-review-plus-adversarial-review cycle, per CLAUDE.md's explicit allowance for that
distinction (the revision only corrects an attribution error; it does not change the
document's scope or introduce a new claim neither review already saw).

## GO / no-go

**GO.** The document's two headline, load-bearing conclusions — `GET
/api/quality/summary` is fixed and verified live (concurrency-tested, not just timed
in isolation), and `services/observability/routes.py`'s `history`/`summary` routes are
still genuinely blocking (also concurrency-tested, reproducing a full event-loop
freeze) — survived independent re-derivation from primary sources without
qualification. The one real defect found (a commit-attribution error affecting a
secondary bookkeeping claim about the remaining-instance count, not either headline
conclusion) has been fixed and the fix independently verified against the review's
exact recommendation. The central judgment call the document makes — reconciling
existing work instead of duplicating an already-adversarially-reviewed 88-handler
census — is reasoned transparently in the self-review and not disputed by the
adversarial pass, which itself re-confirmed the census's existence, merge status, and
one-instance-already-fixed premise from primary sources rather than taking it on
faith.

Cleared to push this branch and open a docs-only PR, labeled `phase:research`, per the
task's instructions. Not cleared for the PR to merge itself without its own separate
pre-merge review cycle (self-review, adversarial review, consolidation against the PR
as submitted) per CLAUDE.md's explicit requirement for that additional gate — and not
cleared for this session to merge it regardless, since prioritizing which of the
~61 remaining instances to fix next is a real decision point for the parent/
coordinating session, not this one.
