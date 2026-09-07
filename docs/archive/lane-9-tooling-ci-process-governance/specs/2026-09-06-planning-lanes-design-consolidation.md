# Consolidation: planning-lanes design (round 1)

Date: 2026-09-06. Reconciles the design, its self-review, and the
independent adversarial review.

## Verdict: NO-GO — full revision and fresh review cycle required

Adopting the adversarial review's own framing: the required fixes change
the lane list itself, which the design's own §3 says is exactly the
change that must go through the full cycle. This is not a fix-list
recheck against round 1's two reviews — it is a new artifact (round 2)
that gets its own self-review and its own independent adversarial pass.

## Adjudication where the two reviews disagreed

- Self-review's "no self-identified blocker" verdict: **the adversarial
  review is right to overrule it.** The self-review never checked a
  single path, count, issue state, or PR file list — it verified one
  fact (`labels.py`'s `phase:*` lines) and reasoned from there. That's a
  real self-review failure mode worth naming plainly: catching your own
  *reasoning* gaps (the whale_stream straddle, the P0 justification's
  shakiness) without checking your own *factual* claims is not enough.
- Self-review's P0-for-Lanes-1-and-5 "prerequisite" framing: **both
  reviews agree this reasoning is sound**, but the adversarial review is
  right that it doesn't rescue Lane 5 as originally defined, because
  Lane 5's stated evidence (the PR list) is simply false. The fix is to
  keep the prerequisite framing and rebuild Lane 5's membership around
  it as a package boundary, not a property.
- No other disagreements between the two reviews — the adversarial
  review's findings are additive to, not contradictory with, the
  self-review's two flagged gaps (whale_stream, P0 reasoning).

## Merged, ordered fix list for round 2

All 7 blocking + all 4 required-non-blocking + the 1 optional item from
the adversarial review, adopted in full, no items dropped or softened:

1. Redefine Lane 5 as a package-bounded "Runtime infrastructure" lane
   (explicit file list); introduce a separate cross-cutting *concern*
   mechanism (not a lane) for property-based work like event-loop
   blocking.
2. Fix all ≥9 mis-assigned/omitted units with a stated, re-appliable
   resolution rule for straddlers, not case-by-case judgment calls.
3. Decide and state the realtime-remediation plan's handling (primary
   lane + cross-lane sub-issue linking, not a silent split).
4. Correct the GitHub Projects claim: label = filtering (real, day-one);
   board grouping = a new deliverable or explicitly deferred, not "zero
   new infrastructure."
5. Re-sequence migration: persist real classification tables (checked
   in, with a DECLINED bucket for plans) → label issues → fix
   `kanban_sync`'s Track-retirement touchpoints → only then move files,
   in lane-sized batches, review companions atomic with their parent,
   budgeted reference-fix count.
6. Correct the Track C claim and route "Standing human decisions" to
   `docs/open-decisions.md`.
7. Fix the board's real path everywhere it's cited, including
   `next-action.md`.
8. Rule 2 (§6): label is a day-one precondition, stated as such. Rules 3
   /4: rewritten as pointers to the `checkpoint` skill's existing AQC/
   `kanban_sync` runs. Rule 5: add a machine-readable `LANES` map in
   `labels.py`. Add the "parked by recorded decision" exemption to §5/
   rule 3 so `feat/candlestick-volatility` doesn't re-triage every
   session.
9. Correct every numeric citation flagged in §2.F.
10. State `lane:*` supersedes `area:*`; state how `main.py` changes are
    tagged (by the route/loop they wire); rename the archive target away
    from the PR-#635-specific `archive-2026-08-27/` name.
11. Fix the two dangling internal references (§0b, §3-vs-§2).
12. Regenerate or retire `docs/superpowers/plans/README.md`'s stale
    count as part of the migration.

## What does not change

The adversarial review explicitly endorses the core concept — package-
owned lanes, `lane:N` as the single query key, Lane > Initiative > Task
replacing "Track", written rules over tooling where a rule is actually
runnable, David's destructive-rebuild authorization, and the priority
order (Kalshi ingestion + prerequisite infrastructure first). Round 2
keeps all of this; it does not start over on the concept, only on the
table and the migration mechanics that operationalize it.

## Next step

Author round 2 directly (not delegated — the boundary-fixing judgment
calls need one consistent hand), persist real classification tables as
a parallel, bounded, delegatable task, then run round 2 through its own
self-review + independent adversarial review + consolidation before any
lane structure is created or any file moved.
