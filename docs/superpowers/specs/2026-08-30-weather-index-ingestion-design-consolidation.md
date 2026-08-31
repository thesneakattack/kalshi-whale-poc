# Consolidation: weather index ingestion design review (2026-08-31)

Reconciles the self-review and an independent adversarial-review Agent call
(fresh, no memory of this conversation) of
`2026-08-30-weather-index-ingestion-design.md`, per CLAUDE.md's "nothing
advances on one pass" HARD RULE — the first pass of this design through the
now-standard cycle (the doc predates the 2026-08-31 HARD RULE, PR #304).

- Self-review: `...-design-review.md` — verdict GO, one correction (the
  city-ranking caveat is real and now resolvable: fresh data shows a
  different ranking than the design doc's original 4-day-stale numbers).
- Adversarial review: independent Agent call — independently re-derived
  every load-bearing claim (Kalshi API text, `settlement_algebra.py`,
  `capture_writer.py`, `index_feed/ingestion.py`, and re-ran the live
  `market_catalog.db` query itself rather than trusting the self-review's
  numbers) — verdict GO, confirming the self-review's correction exactly
  (identical numbers, independently reproduced) and adding one refinement.

## Agreement

Full agreement: every Kalshi-API, settlement-statistic, and schema/write-
path claim in the design doc checked out against primary sources under
both passes, no contradictions found anywhere. The city-ranking correction
is doubly confirmed — same numbers, reproduced independently.

## One addition (adjudicated: adopt)

The adversarial review added a nuance the self-review didn't have:
`docs/open-decisions.md`'s own 2026-08-30 entry notes `catalog_scan.py`
batches only 10 series/scan, so the widened-category catalog hasn't
finished its first full cycle yet — the current KXHIGH% ranking, while far
fresher than the original 4-day-stale snapshot, is still a mid-cycle read,
not a settled steady-state one. Adopted as-is: carried into the
implementation plan as an explicit task (re-run the ranking query once the
catalog has cycled through the widened categories, not just once).

## Merged fix list (for the implementation-plan stage to carry)

1. Use the refreshed city ranking (LAX, MIA, NY, CHI — note MIA/NY swapped
   vs. the original design doc) as the working default, not the original
   4-day-stale numbers.
2. Explicitly consider whether THOU/TDAL/AUS belong in the starting set,
   given how close their volume now sits to CHI's.
3. Add a task to re-run the ranking query once `catalog_scan` has fully
   cycled through the widened categories (not a one-time check) before
   treating the city list as final.
4. Carry forward the design doc's own already-flagged open items as actual
   implementation-plan tasks, not silently dropped: (a) measure real
   per-city REST cost and rate-limit headroom before fixing a polling
   interval; (b) verify the `city` path-parameter spelling against Kalshi's
   real city-ID list before coding (not inferred from `KXHIGH*` ticker
   suffixes).

No disagreement to adjudicate beyond the one addition above, which both
reviews' evidence supports without conflict.

## GO / no-go

**GO.** The design (as corrected) is solid enough to serve as input to the
`writing-plans` implementation-plan stage. Proceeding to write that plan.
