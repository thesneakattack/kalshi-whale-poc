# Kalshi Category Data Completeness — Revision 2 Review

**Task.** Independent verification of revision 2 (commit `7d945c1`), made in response to the
revision-of-revision review (`docs/archive/lane-1-kalshi-ingestion/specs/2026-08-30-kalshi-category-data-
completeness-design-revision-review.md`, which found two real problems in revision 1,
`19c72e9`: an overstated backfill-coverage number from a partial join, and a guessed
per-series call shape for the fee-changes endpoint). Every number and schema claim below was
independently re-derived — the DB join was re-run from scratch against the primary
checkout's live databases (`data/signal_log.db`, `data/title_cache.db`, `mode=ro`; this
worktree's own copies confirmed 0-byte gitignored placeholders, not used), and both fee-
changes doc pages were read directly, not taken from the revision's own prose.

**Overall verdict: GO, with one small residual gap worth a one-line fix, not a re-open.**
Both substantive corrections (the coverage number, the call shape) are verified correct and
exact. The selection rule is well-formed given the schema. One loose end remains: §1.8
states it "folds into D1 Phase 1 (§6, item 1)" but §6 item 1's own enumeration was never
actually edited to list §1.8 — the commit message's "wired into Sec 6's D1 Phase 1 rollout
line" overstates what the diff did. This is a one-line addition, not a design defect, and
does not block Stage 5.

---

## 1. The corrected coverage number — CONFIRMED, exactly

Reproduced independently, not from the revision's numbers: attached `title_cache.db` to
`signal_log.db` and ran the join fresh.

- MVE-shaped signals (`series LIKE 'KXMVE%'`, matching the three known MVE series names —
  `KXMVECROSSCATEGORY`, `KXMVECROSSCATEGORY0`, `KXMVESPORTSMULTIGAMEEXTENDED` — used
  throughout this investigation's prior stages): 12,365 distinct tickers / 14,615 rows today
  (vs. the design's cited 12,357/14,606 — a ~9-row difference consistent with continued live
  growth in the DB between the revision commit and this check, same pattern the prior review
  already observed and explained).
- Distinct MVE tickers with a `market_titles` row: **90** — exact match to the design's
  figure.
- MVE signal rows with a `market_titles` row: **218** — exact match.
- The actual proposed join, `market_titles mt JOIN event_titles et ON et.event_ticker =
  mt.event_ticker`, restricted to those same MVE tickers, with a non-null `series_ticker`:
  **0 distinct tickers, 0 rows.** Confirmed zero, not an off-by-something.
- Went one step further than "non-null `series_ticker`" to rule out a masked join failure:
  even *without* the non-null filter (i.e., does the join produce any row at all, even one
  with a null `series_ticker`), the result is still **0 rows**. Checked directly: of the 89
  distinct `event_ticker` values behind the 90 covered `market_titles` rows, **0** exist as a
  row in `event_titles` at all. The join fails at the `event_ticker` match itself, not at a
  null `series_ticker` on a matched row — a stronger and cleaner zero than "matched but
  null" would have been.

**Verdict: CONFIRMED.** Revision 2's "0.0%, not 1.5%" claim is exact. The don't-backfill
conclusion is, as claimed, stronger under the corrected number, not weaker.

## 2. The fee-changes call-shape correction — CONFIRMED

Read `docs/kalshi/get-series-fee-changes.md` and `docs/kalshi/get-event-fee-changes.md` in
full (OpenAPI schemas), independently of the design's citations.

- **Series-level (`/series/fee_changes`).** `series_ticker` is `required: false` (optional
  query param). The response schema, `GetSeriesFeeChangesResponse`, has exactly one property,
  `series_fee_change_arr` — no `limit`, no `cursor`, no pagination field anywhere in the
  schema. This confirms the design's claim: omitting `series_ticker` returns everything in one
  call: **CONFIRMED**, supports "one bulk call, not per-series."
- **Event-level (`/events/fee_changes`).** The endpoint's parameters explicitly include
  `$ref: '#/components/parameters/LimitQuery'` and `$ref: '#/components/parameters/
  CursorQuery'` (limit 1-1000, default 100; cursor-follow for the next page), and the response
  schema `GetEventFeeChangesResponse` requires both `event_fee_changes` and `cursor`. This
  confirms the design's claim that the event-level endpoint genuinely is paginated, unlike the
  series-level one: **CONFIRMED**, supports splitting it out as a separately-scoped,
  differently-shaped, explicitly deferred piece rather than folding it into D1.

## 3. The selection rule — well-formed given the schema

`SeriesFeeChange`'s required fields are exactly `id`, `series_ticker`, `fee_type`,
`fee_multiplier`, `scheduled_ts` — five fields, nothing else. No `status`, `cancelled`,
`active`, or `created_at` field exists anywhere in the schema. Given that, "the entry with
the greatest `scheduled_ts ≤ now` per `series_ticker`" is a sound and standard way to resolve
an effective-dated event log to a point-in-time value, and there is no cancellation/status
flag the rule fails to account for — there isn't one to account for. The design's own hedge
("ties broken by `id`, since the schema gives no other ordering guarantee") is accurate: `id`
is documented only as "unique identifier," with no stated ordering semantics, so a same-
`scheduled_ts` tie-break by `id` is honestly labeled as arbitrary-but-deterministic rather
than claimed to be meaningful. Not a flaw — a correctly-hedged limitation.

One small, non-blocking gap the design doesn't spell out: what happens for a series with zero
entries at or before `now` (e.g., only future-scheduled changes, or no fee-changes history at
all). Presumably `series_metadata.fee_type`/`fee_multiplier` simply keep whatever the base
`/series` listing (§1.2) already populated, since nothing in the fee-changes response would
have anything to overwrite them with — but the design doesn't say so explicitly. Worth one
sentence at implementation-planning time; not a correctness problem with the rule itself.

## 4. Consistency check

**§1.4:** grepped the whole document for `0.7%`, `1.5%`, `one call per series`, `one
additional call per series`, `~10,351 extra` — the only hits are inside the corrective
sentences themselves (explicitly saying what the earlier draft got wrong), not left over as
live claims elsewhere. Clean.

**§1.8 vs. §6:** this is the one real residual gap. §1.8's new "Rollout" paragraph asserts:
*"the series-level fee-changes call folds into **D1 Phase 1** (§6, item 1) ... it needs no
separate rollout step."* But `git show 7d945c1` touches exactly two hunks — §1.4's paragraph
and §1.7/§1.8's section — and **§6 itself was not edited**. §6 item 1 still reads "**D1 Phase
1** (§1.2-1.4, §1.6)" verbatim, with no mention of §1.8. So the claim is a one-way pointer:
§1.8 asserts it belongs to §6 item 1, but §6 item 1's own section list wasn't updated to say
so. This is exactly the wiring gap the prior review's item (d) flagged, now half-closed
(explicit rollout intent stated) but not fully closed (the actual rollout enumeration wasn't
touched) — and the commit message's "wired into Sec 6's D1 Phase 1 rollout line" overstates
what the diff actually did.

This does not require reopening the design: the fix is a one-line edit to §6 item 1's
parenthetical (`§1.2-1.4, §1.6` → `§1.2-1.4, §1.6, §1.8`), consistent with how the design
already handles every other cross-reference in that list. Not blocking Stage 5 — an
implementation planner reading §1.8 directly would still get the correct rollout placement —
but it is the one loose end this pass found.

---

## Recommendation

**Go for Stage 5**, with one trivial fix carried forward (not requiring another review pass):
add `§1.8` to §6 item 1's section list. Both substantive corrections from the prior review —
the coverage number and the fee-changes call shape — are verified correct, exact, and
consistent with the rest of the document. No new issues of the kind that blocked the first
two passes (wrong joins, guessed call shapes, unaccounted schema fields) were found in this
pass.
