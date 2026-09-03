# Self-review, round 2: `edge-gate-retro-measurement-2026-09-03.md`

Own-context review of the revision made after the doc's adversarial review
(`-adversarial-review.md`, GO WITH REQUIRED FIXES, 12 findings — 2
CRITICAL, 2 HIGH, 8 MEDIUM/LOW). Per CLAUDE.md's "nothing advances on one
pass" rule, a revision that introduces a claim neither prior pass saw gets
the full cycle re-run, not a fix-list recheck alone — this revision adds a
new section (§3.6, the EV/P&L finding) and substantially rewrites §1's
central mechanism claim, both squarely in that category.

## Independent verification before accepting the review's two most consequential claims

Did not apply either of the review's two most decision-critical findings
on trust alone:

- **Retention pruning as the real root cause (Finding 1):** re-checked
  `market_history.db` directly — at re-check time the oldest surviving
  snapshot was 7.00 days old (the review's own check, run earlier, found
  16.45 days — a prune backlog that has since drained closer to the
  configured 168h boundary). This independently corroborates the
  mechanism rather than just trusting the reviewer's number: retention
  pruning is demonstrably active and converging on its configured
  boundary, exactly the behavior that would produce the age-based miss-
  rate cliff the review reports.
- **The would-reject cohort not being worse (Finding 6):** wrote and ran
  an independent P&L-matching script (different from the reviewer's,
  matching each entry to the *first* subsequent close on the same ticker
  and parsing the broker's own realized-P&L text) against a later
  snapshot of the same live data. Got a directionally identical result —
  would-REJECT had the highest win rate (78.8%) of the three buckets, not
  the lowest — confirming the qualitative finding independently rather
  than reporting the reviewer's own numbers as fact. Exact figures differ
  slightly from the review's (different snapshot, different script), and
  the doc's §3.6 states this explicitly rather than implying false
  precision.

## Fix-list check against all 12 findings

1. **CRITICAL — wrong root-cause explanation.** §1 rewritten: states
   retention pruning (`market_history.retention_hours: 168`) as the ~60%-
   of-misses driver, cites the age-based miss-rate cliff at the 7-day
   boundary, and states the corrected within-retention gap (49.4%, not
   71.3%). Notes the 30-day calibration window is effectively ~7 days.
2. **CRITICAL — wrong exemplar markets.** §1 rewritten: `KXMVECROSSCATEGORY`
   (100% zero-coverage) and the sports-match families named as the real
   gap; `KXGOLD15M`/`KXSILVER15M` corrected to "reasonably well covered."
   Also fixed the same wrong exemplars where they'd been repeated in
   §3.5's "config vs data limit" follow-up — softened to not claim a
   specific family breakdown for that population without having checked
   it separately.
3. **HIGH — §2 overreached "neither condition is currently occurring."**
   Rewritten to state only "no *existing* cell is under-sized," adds that
   81.7% of attached signals have no category (a live, current condition,
   not hypothetical), and adds the zero-row-cell case §1 didn't cover
   before. Cross-referenced in the Summary's first bullet too.
4. **HIGH — §4's "transfers to observe-only unchanged" for the admit
   path.** Rewritten: traced `decision["edge_gate"]`'s actual consumers
   (zero) and confirmed neither path captures anything durable today.
   Summary's observe-only bullet updated to say both paths need the same
   kind of work, not just the reject side.
5. **MEDIUM — §3's sample framed as a lifetime shortfall.** Rewritten:
   states the 20-hour span and the broker reset explicitly, and that the
   archive can't extend it either since retention has pruned its own
   inputs.
6. **MEDIUM — no EV/outcome measurement, called "the real headline"
   anyway.** New §3.6 added with the independently-reproduced P&L split,
   heavily caveated (small sample, approximate netting-unaware matching,
   still-open positions, one profitable session). Promoted to the top of
   the Summary as the single most important framing point, not a footnote.
7. **LOW — `p_est` "never captured anywhere" too strong.** §4 corrected:
   `p_est` survives as unparsed text in the reject-path `reason` string
   inside a 50-entry ring; `q_pre`/`delta` genuinely never captured.
8. **LOW — "10 seconds before the print" imprecise.** §2 corrected: up to
   ~610s stale in practice given `p_pre_max_age_sec=600`; the margin
   required restated as ~0.045 at the extremes, ~0.06 in the flagged band
   specifically (fee-dependent, not a flat number).
9. **LOW — rounding (81.8%→81.7%, 18.2%→18.3%).** Fixed in both
   occurrences in §1's measurement block and prose.
10. **LOW — "read-only"/"deleted after use" not literally accurate;
    internal inconsistency about what was called vs. replicated.** Opening
    paragraph rewritten: states the underlying `services.*` functions
    still open read-write connections internally (PRAGMA/CREATE TABLE),
    names exactly which functions were called for real vs. which formula
    was inlined, and why (the flat-fee-type early-out needs a live
    lookup a standalone script doesn't replicate — confirmed no sampled
    ticker is flat-fee, so this doesn't affect the numbers). Also
    cleaned up three leftover schema-only `.db` files the shadowing bug
    (documented in the original self-review) had left in this worktree's
    gitignored `data/` directory — harmless but sloppy, removed.
11. **LOW — undisclosed category-source substitution in §3.** Added: the
    retro sim uses `trade_category.db` while the live gate uses a denser
    in-memory map; no practical divergence found, but named.
12. **LOW — blanket "verified every inherited claim" unevidenced.** Opening
    paragraph softened: states the PM's specific numbers are time-varying
    and gives a live re-check with its own timestamp instead of asserting
    verification without showing it.

All twelve confirmed present and correct in the current file — re-read
each changed section against the review's specific required-fix text
after editing, not accepted on the edit's own completion claim.

## New content introduced in this fix — checked for its own errors

- §3.6 (the EV/P&L section) is new relative to everything before it in
  the doc's history. Checked: its own caveats list is exhaustive relative
  to what the underlying matching script actually does (first-close-only
  matching, no netting awareness, still-open exclusions) — cross-read
  against the script's own logic rather than just restating a generic
  disclaimer list.
- The Summary's opening callout (promoting §3.6 above all the volume
  numbers) was checked against whether it overstates what §3.6 actually
  found — it doesn't claim the gate is bad, only that quality-of-rejected
  was never checked and the one indicative check available doesn't
  support "the gate improves things." Re-read twice to make sure this
  doesn't tip into overclaiming the opposite conclusion, which would be
  the same class of error as the original overreach.

## What this revision does NOT do, stated explicitly

Does not perform a rigorous, netting-aware EV/outcome measurement — §3.6
is explicitly indicative, not a replacement for one. Does not resolve
which specific market families drive §3's own (as opposed to §1's 30-day)
coverage gap. Does not implement observe-only or touch any code/config.

## Verdict

GO. Ready for a fresh, independent adversarial-review pass with no memory
of this fix or the review that prompted it.
