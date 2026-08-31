# Kalshi Category Data Completeness — Revision Review

**Task.** Independent verification of the revision (commit `19c72e9`) made in response to
Stage 4's review (`docs/superpowers/specs/2026-08-30-kalshi-category-data-completeness-design-
review.md`, commit `bc5bc38`). Every number below was re-derived from the live primary-checkout
databases (`data/signal_log.db`, `data/title_cache.db`, opened `mode=ro`, this session — this
worktree's own copies are the gitignored empty placeholders, confirmed 0 bytes) or from a source
file/doc page opened directly. Nothing below is re-derived from the revision's own prose.

**Overall verdict: partial GO.** The backfill-premise fix (§1.4) is structurally sound and its
two code citations check out verbatim. Its own headline coverage numbers (90/218) are real but
measure a shallower join than the one the design's own proposed function performs — the true
number, via that exact join, is **zero**, which if anything strengthens the "don't backfill"
conclusion rather than weakening it. §1.8 (the new fee-changes section) is a different story: it
has four real problems — a schema-fit gap, a REST call-count claim that's very likely wrong by
roughly three orders of magnitude, an inconsistency with this codebase's own established
per-series pacing convention, and a rollout-section wiring gap that means as written it wouldn't
actually ship. **§1.4 is Stage-5-ready as revised (with one number worth tightening). §1.8 needs
one more correction pass before Stage 5 builds on it** — either fix the call shape and schema fit,
or fold it into §5's explicit-deferral list the way X10 already is.

---

## 1. The core measurement claim (§1.4's backfill coverage)

**Code citations: CONFIRMED verbatim.**
- `services/state_view.py:142-145`: *"state['market_titles']/state['event_titles'] themselves
  accumulate unbounded for the app's whole lifetime now (see services/title_cache.py) so
  history/clusters can still resolve an old ticker's title..."* — exact match to what the
  revision and Stage 4 both quote.
- `main.py:956-958`: *"_build_state_body scopes what's actually sent over /api/state, so this
  growing unbounded server-side doesn't reintroduce the payload-size regression that scoping was
  built to fix."* — exact match, and `main.py:970-978` confirms the mechanism: every tick,
  `new_market_titles` is built from every ticker in that tick's `markets` and both
  `state["market_titles"].update(...)` and `title_cache.save_market_titles(...)` run —
  accumulate, never evict. The design's "title_cache is unbounded, not bounded" correction is
  factually right.

**Coverage numbers: reproduced closely, but the revision measured the wrong join.** Re-running
against the live DBs today (table grows continuously, so small deltas from the revision's
snapshot are expected and were): 12,360 distinct MVE-shaped tickers / 14,609 MVE-shaped signal
rows out of 98,405 total (14.85%) — vs. the revision's 12,357 / 14,606 / 14.87%, a difference of
~3 rows, consistent with live growth in the ~20 minutes since that commit. A direct
`LEFT JOIN market_titles ON ticker` reproduces the revision's 90 covered tickers / 218 covered
rows exactly.

But `market_titles` alone is not what the design's own proposed `series_ticker_for()` reads
(§1.4's code block: `market_titles mt JOIN event_titles et ON et.event_ticker = mt.event_ticker`
— an inner join through *both* tables). Running that exact join, restricted to the same MVE-
shaped signal rows: **0 of 12,360 distinct tickers, 0 of 14,609 rows** resolve a `series_ticker`.
Every one of the 89 distinct `event_ticker` values behind the 90 covered `market_titles` rows is
absent from `event_titles` entirely (checked directly, not inferred). Separately, `event_titles`
does have 2,264 MVE-shaped rows and the two-table join does produce 307 rows for MVE tickers
*overall* — just none of them are among the specific tickers that ever produced a logged signal,
consistent with MVE tickers being short-lived instances that rotate out before a later scan
re-discovers them.

**Verdict on this item:** the revision's "218 of 14,606 (1.5%) ... would gain a corrected
series_ticker from a backfill attempted right now" overstates actual backfill-usable coverage —
the real figure, via the join the design itself specifies, is 0%. This doesn't change the
recommendation (still don't backfill; the case is if anything stronger), so it's not blocking,
but the stated number is not what it claims to be and should be corrected to either the true 0%
figure or reworded to say what it actually measured (partial `market_titles`-only coverage).

## 2. Is the new reasoning sound? ("shipped same day" claim)

**CONFIRMED.** `git log --follow --diff-filter=A -- services/market_watch/mve_scan.py` shows one
commit, `819ac32`, `2026-08-30 14:07:31 -0500`, `"feat: multivariate (combo) event discovery,
independent of kalshi.categories"` — a brand-new file, not a move/rename, shipped today. MVE-
shaped signal history in `signal_log.db` spans `2026-08-17` through today (`seen_at` range
1786945228–1788139304, i.e., ~13 days), almost entirely predating `mve_scan.py`. Read the module
docstring directly: MVE-producing series report `volume_fp: "0.00"` on their own `/series` entry
even while their generated markets carry real trading volume, and `catalog_scan._get_series_cache`
filters on `volume_fp > 0` *before* any per-series scan runs — so MVE markets were structurally
invisible to the watchlist-building path (`main.py:752`'s `_fetch_markets` → the `markets` list
that feeds both `title_cache` writes and `trade_stream.set_market_tickers`) until this sibling
discovery module existed. `mve_scan.py:233-238` confirms it writes to *both* `market_titles` and
`event_titles`, so going forward the mechanism the design describes is real. The design's own
phrasing hedges correctly ("ephemeral, cycle out, **and** mve_scan.py... shipped the same day...
so almost none of the historical MVE tickers were ever cached") — it doesn't over-claim a single
cause, and my finding in §1 above (0% via the full join, even against `event_titles` rows that do
exist for *other* MVE tickers) is consistent with "ephemeral" being the dominant factor going
forward, not just a one-time cold-start gap. This reasoning holds up.

## 3. §1.8 (fee-changes endpoints) — four real problems

Read `docs/kalshi/get-series-fee-changes.md` and `docs/kalshi/get-event-fee-changes.md` directly
(OpenAPI schemas), and `grep -rn fee_changes services main.py tools tests` (no output, confirming
the design's "no code path today" claim).

**(a) Schema-fit gap.** `GET /series/fee_changes` returns `series_fee_change_arr`: a list of
`SeriesFeeChange` objects, each with its own `id`/`fee_type`/`fee_multiplier`/`scheduled_ts` —
i.e., a set of discrete scheduled-and-historical change *events*, potentially several per series,
some in the future. `series_metadata` (§1.2) has `PRIMARY KEY (ticker)` with single scalar
`fee_type`/`fee_multiplier` columns. §1.8 says this response gets "written into
`series_metadata`'s existing `fee_type`/`fee_multiplier` columns... the same way the rest of that
row is populated" — that elides the actual work: picking which change (most-recent-past
`scheduled_ts`?) resolves to "current," and it silently drops every future-scheduled and
historical entry the endpoint returns, which is the entire reason `show_historical=true` was
called for in the first place. Needs either a real selection rule stated, or (as the original
investigation and Stage 4 both suggested) its own change-history table.

**(b) The "one additional call per series" claim is very likely wrong.** `series_ticker` is an
*optional* query parameter on `/series/fee_changes`, and — unlike `/events/fee_changes`, which
documents `limit`/`cursor` pagination — `/series/fee_changes` has no pagination parameters at
all. That's consistent with one unfiltered call returning fee changes for every series in a
single response (fee changes are a rare, low-volume event type), which would need *one* call, not
one per series. §1.8 never checked this before proposing "one additional call per series...
riding `_get_series_cache()`'s existing hourly refresh" — as written that's ~10,351 extra REST
calls/hour (today's volume-positive series count, per §1.3), not one. This is exactly the kind of
REST-rate claim CLAUDE.md's HARD RULE says must be verified, not assumed ("never change... REST
rate... because it 'should help'": the inverse also applies — never *add* REST load on an
unverified assumption).

**(c) Inconsistent with this codebase's own established per-series REST convention.**
`services/market_watch/catalog_scan.py:341-358` (`_scan_catalog_batch`) is the existing precedent
for "one REST call per series, at scale" in this exact module family: it deliberately batches
`_CATALOG_SCAN_BATCH_SIZE = 10` series per tick, paced through `asyncio.Semaphore(PACE_LIMIT=4)`,
explicitly because unbounded per-series fan-out is a known rate-limit risk (the module's own
comments cite a prior rate-limit incident). §1.8 proposes firing all ~10,351 per-series calls
inside one hourly refresh with no batching or pacing design at all — a materially heavier and
differently-shaped load than the pattern this same file family already uses for the same class of
problem, and D1 elsewhere (§1.5, Phase 2) is explicitly careful about not changing REST call shape
without its own before/after measurement. §1.8 doesn't hold itself to that same bar.

**(d) Rollout-section wiring gap.** §6 item 1, "D1 Phase 1," enumerates "§1.2-1.4, §1.6" as what
ships together. §1.8 is not listed — not there, not in item 3 ("D1 Phase 2," §1.5), not anywhere
in §6. Unlike §1.7 (X10), which is explicitly and correctly kept out of §6 with a stated reason
("not designed further in this pass"), §1.8 claims to belong in D1's scope directly ("no reason to
defer") but was never actually wired into any rollout item. As written, the document doesn't
schedule its own new section to ship.

**One thing that is accurate:** §1.8's citation of `get-event-fee-changes.md:7`'s override
semantics is a correct verbatim quote, and grepping the design confirms `/events/fee_changes`
(the *event*-level endpoint) gets no REST ingestion design anywhere in §1.8 despite the section's
title ("the fee-changes endpoints," plural) implying both are addressed — only the series-level
one is designed; the event-level one is left with neither a design nor an explicit §5 deferral,
which is a smaller-scope recurrence of the exact class of omission Stage 4 caught the first time.

## 4. Internal consistency elsewhere in the document

**CONFIRMED clean.** `grep`ed the whole document for "rolling watchlist"/"not a permanent
archive"/"bounded"/"unbounded"/"horizon" — the only hits are inside the corrected §1.4 paragraph
itself; no other section repeats the old backwards framing. D1's schema (§1.2), population (§1.3),
and the other two named consumer migrations (§1.4's `catalog_scan.py`/`market_catalog` items) are
untouched and still hold, matching Stage 4's clean bill on those. The backfill fix is genuinely
isolated to the one paragraph it needed to touch.

---

## Recommendation

1. **§1.4**: tighten the coverage number (state the true 0% via the full `series_ticker_for()`
   join, or explicitly label 90/218 as "have a `market_titles` row" rather than "would gain a
   corrected series_ticker"). Non-blocking — the conclusion doesn't change either way.
2. **§1.8**: needs one more revision pass before Stage 5 plans against it — verify whether
   `/series/fee_changes` actually requires per-series calls (test one unfiltered call against the
   live API, the same "run the cheap check" standard the backfill fix itself just modeled), state
   a selection rule for the array-vs-scalar schema mismatch, and either wire it into §6's rollout
   list or move it into §5 next to X10 with a stated reason, matching how this design already
   treats every other assumption it hasn't verified.

Neither finding touches D2, D3, or D4, or invalidates the rollout ordering for anything except
§1.8's own missing rollout entry. This is not a rewrite — it's the same shape of "fix specific
paragraphs" correction the design has already been through once.
