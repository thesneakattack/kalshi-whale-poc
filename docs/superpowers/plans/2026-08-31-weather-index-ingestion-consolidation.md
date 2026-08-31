# Consolidation: weather index ingestion plan stage (2026-08-31)

Reconciles the self-review and independent adversarial review of
`2026-08-31-weather-index-ingestion.md` per CLAUDE.md's "nothing advances
on one pass" HARD RULE. Last stage before execution.

- Self-review: `...-plan-review.md` — verdict GO, flagged 3 items (catalog-
  cycling-delay handling, ddev-running assumption, no final consolidation
  task) as open questions for the adversarial pass to weigh in on.
- Adversarial review: independent Agent call — independently re-derived
  every load-bearing citation from source (`fault_log.py`'s real function
  signature, `main.py`'s actual scheduler-tuple-list lines, `backup.py`'s
  real cold-start guard, `DB_PATH` depth arithmetic) — verdict
  GO-WITH-FIXES, 2 items, plus its own independent judgment on the 3
  self-review items (all acceptable as-is, no fix needed).

## Real finding — a "confirmed" claim that was wrong

The adversarial review caught something the self-review structurally could
not: this plan's Task 5 asserted `fault_log.record(component, context,
exc)` as a "confirmed real call shape," but the actual signature (read
directly from `services/fault_log.py:85`) is `record(component, operation,
exc, context=None, severity="error", now=None)` — the second positional
parameter is named `operation`, not `context`. The proposed call
(`fault_log.record("weather_index", "poll_city", exc)`) happens to work
correctly *positionally* regardless of the label, so this was not a
functional defect, but it was a factually wrong "confirmed" claim — exactly
what the never-guess rule exists to catch, and exactly why same-author
self-review structurally cannot catch this class of error (it can't
independently re-derive its own citation, only restate it more
confidently).

**A second, related defect the adversarial review flagged as a process
smell**: this plan's Task 3 text included the phrase "confirmed accurate by
this plan's adversarial review" — written into the plan *before* the
adversarial review had actually happened, pre-asserting a conclusion the
review process is supposed to reach independently. Adjudicated: this is a
real defect (a plan document should never cite its own not-yet-run review
as evidence), fixed by removing the premature phrase — the underlying
`DB_PATH`-depth claim itself was independently re-verified as accurate by
the adversarial review anyway, so only the premature phrasing needed
fixing, not the substance.

## Merged fix list

1. Task 5: correct the parameter name — `fault_log.record(component,
   operation, exc)`, not `(component, context, exc)`.
2. Task 3: add a test case distinguishing an `incomplete`-status point
   (Kalshi's `detailed=true` response can return a point with no `v` value
   for the trailing minute still inside its receipt deadline — a row
   written with `value=NULL`, later upserted once real data arrives) from a
   true quorum-failure gap (no point returned at all, no row). The nullable
   `value REAL` schema already handles this correctly, but no test
   exercises it — real coverage gap, not just a nice-to-have.
3. Task 3: remove the premature "confirmed accurate by this plan's
   adversarial review" phrase — a plan should not cite its own pending
   review as evidence for a claim.
4. Optional, adopted: one-line pointer in Task 7 that
   `_maybe_poll_weather_index`'s cold-start seed needs its own persisted-
   history source (e.g. `MAX(polled_at)` from `weather_index_ticks`,
   parallel to `backup.py`'s `latest(tier="regular")` pattern), since the
   two tables aren't the same shape and a direct copy-paste of `backup.py`'s
   seed query wouldn't work as-is.

The self-review's 3 flagged items are resolved by the adversarial review's
independent judgment, adopted as-is: none need a fix (catalog-cycling delay
and the ddev-running assumption are acceptable execution-time judgment
calls; no final consolidation task is needed for a plan this size, since
the closing "what this plan deliberately does not do" section already
bounds scope).

## GO / no-go

**GO, with all 4 fixes applied** (see corresponding edits to
`2026-08-31-weather-index-ingestion.md` in this same commit). Plan is ready
to sit as-is awaiting execution go-ahead.
