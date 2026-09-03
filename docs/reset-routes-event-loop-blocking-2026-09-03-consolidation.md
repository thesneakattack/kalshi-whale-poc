# Consolidation: `reset-routes-event-loop-blocking-2026-09-03.md`

Reconciles the self-review, an in-flight correction from the PM (autotrade-1d)
applied before the adversarial review ran, and the adversarial review into a
single GO/no-go plus one merged fix list, per CLAUDE.md's "nothing advances
on one pass" HARD RULE.

## Inputs

1. `reset-routes-event-loop-blocking-2026-09-03-self-review.md` — GO, no
   correction needed; spot-checked 4 citations, stated measurement
   methodology (read-only, DELETE costs are estimates) explicitly.
2. **PM correction (applied before adversarial review, not a separate
   review pass — documented here for a complete record):** the doc's
   original recommendation section proposed an awaited
   `tick_executor.run(...)` call as the fix. The PM flagged this as
   reintroducing PR #409's confirmed live starvation incident (both
   `tick_executor` workers pinned 5h10m+) because `tick_executor` is only
   a 2-worker pool that also serves trading-critical writes
   (`capture_writer`, `candidate_log`). Independently verified before
   acting on it: `tick_executor.py:80`'s `max_workers=2`; PR #409's
   incident description; PR #424's bounded-query precedent (~1.0s/~30%
   measured savings) with its elastic-pool alternative tried and reverted
   after measurement showed it regressed the target incident; and
   `_scoring_pool.py`'s dedicated 4-worker pool as the isolation
   precedent. All four checked out. The doc's recommendation section was
   rewritten accordingly (commit `308af8a`) before adversarial review ran,
   so the reviewer assessed the corrected version, not the original.
3. `reset-routes-event-loop-blocking-2026-09-03-adversarial-review.md` —
   GO with 3 required fixes: missing `analytics/routes.py` precedent
   (with its shared-pool caveat), the "34+ second every single time"
   overstated-constant framing, and the "~2h" nginx window (actually
   ~14h, `2026-09-02 16:38:45 → 2026-09-03 06:37:14`).

## Adjudication

No disagreement between self-review and adversarial review to adjudicate —
the self-review predates the PM correction and the adversarial pass, and
found nothing wrong with what it checked (4 citations, methodology
statement, internal consistency of the summary section); none of that was
contradicted later. The adversarial review's 3 findings are all additive
corrections to supporting detail, not conflicts with the self-review's
scope.

## Fix-list recheck (item by item, against the current file)

1. **Missing `analytics/routes.py` precedent** — added as its own
   paragraph in the "Realistic hit rate" section's preceding discussion,
   including the shared-pool caveat (confirmed via
   `grep -n "^from\|^import\|tick_executor" services/analytics/routes.py`
   that it imports the plain shared pool, not an isolated one). Present in
   the file as of commit `9bc6bab`.
2. **"34+ second every single time" overstated constant** — headline
   figure now carries the cache-state re-measurement (17,027 ms cold /
   433 ms / 370 ms warm); the "Realistic hit rate" section's prose rewritten
   from "pays the full 34+ second freeze every single time they do" to "pays
   a freeze on the order of tens of seconds cold (or low-hundreds-of-ms
   warm...) every time." Verified via `grep -n "every single time"` on the
   current file — no remaining unqualified instance.
3. **"~2h" nginx window → ~14h** — corrected in both locations: the
   "Realistic hit rate" section header paragraph and the closing "Summary
   for whoever picks up the fix/plan stage" section, both now stating
   `2026-09-02 16:38:45 → 2026-09-03 06:37:14`. Verified via
   `grep -n "~2-hour\|~2h\b\|2-hour window\|04:24"` on the current file —
   zero matches (the one remaining "~2h" substring is inside the
   correction note itself, explaining what the number used to say, not a
   live claim).

All three confirmed present and correct in the file as pushed (commit
`9bc6bab`, branch `docs/reset-routes-blocking-research-2026-09-03`), not
accepted on completion-claim alone.

## GO / no-go

**GO.** Scope was research-stage only (no fix implemented, no plan
written) — this consolidation closes PR #512 as a `phase:research`
artifact ready for the fix/plan stage to consume. The headline finding
(candidate_log's `rejection_events` table makes `count_range()` and
`clear_range()`/`clear_all()` genuinely expensive, unawaited,
event-loop-blocking calls reachable from all three `/api/reset*` routes)
stands unmodified through both review passes. The recommendation section's
direction — bound/approximate the query first (PR #424's precedent),
dedicated pool only if still needed, never the shared `tick_executor` pool
(PR #409's precedent) — is corroborated by a closer precedent
(`analytics/routes.py`) that the adversarial review surfaced, itself
possibly carrying the same unresolved risk and worth flagging separately
to whoever owns that file.

## Process note for the record

The adversarial review's own findings were originally applied to the main
doc directly (commit `9bc6bab`) without first landing as their own
artifact — a gap against CLAUDE.md's "each stage produces its own
artifact... never an edit folded into the one before it." Corrected by
writing `reset-routes-event-loop-blocking-2026-09-03-adversarial-review.md`
as its own document (this consolidation's input #3) before this
consolidation, so the full chain — self-review, PM correction, adversarial
review, consolidation — each has its own committed artifact.
