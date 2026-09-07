# Kalshi Category Data Completeness — Design Review (Stage 4)

**Task.** Independent review of Stage 3's design
(`docs/archive/lane-1-kalshi-ingestion/specs/2026-08-30-kalshi-category-data-completeness-design.md`, commit
`fe67b93`) against the investigation it builds on
(`docs/archive/lane-1-kalshi-ingestion/research/2026-08-30-kalshi-category-data-shape-audit.md`, commit
`2eaa78a`) and against the actual current code, fresh, from source — not a re-read of Stage
3's own citations. Every claim below traces to a code location or doc line I opened directly
in this worktree, or is explicitly marked unverifiable-from-here.

**Overall verdict: needs one correction before Stage 5, not a rewrite.** Six of eight review
items confirm cleanly, several down to exact line numbers. Item 3 (the backfill exclusion) is
factually backwards about which cache is bounded and which is unbounded, and forecloses a real
option — restoring correct series attribution to the 14.87% of signal history that the
MVE/sharded-ticker bug mis-tagged — on a premise the code contradicts. One more real gap
(§9): the investigation's own D1 addendum asked for the two fee-changes REST endpoints to be
folded into D1's schema/population design; the design doc never mentions them. Both are fixable
without touching the rest of the document.

---

## 1. D1's "zero new API calls" claim

**CONFIRMED.** `services/kalshi/public.py:134`'s `get_series_list()` fetches raw JSON via
`_get_json("/series", ..., params={"include_volume": True})` and returns `data.get("series",
[])` unprocessed — deliberately raw, not SDK-typed, per its own docstring (a live
`fee_type: "quadratic_with_combo_maker_fees"` value broke the SDK's Pydantic enum in
2026-08-21). Every field D1's schema (`tags`, `settlement_sources`, `contract_url`,
`contract_terms_url`, `fee_type`, `fee_multiplier`, `additional_prohibitions`, `volume_fp`,
`last_updated_ts`, `exchange_index`) is a documented, required-or-nullable property of
`Series` in `docs/kalshi/get-series.md:100-212` — checked every field name against the schema
directly, all present, no invented field. `services/market_watch/catalog_scan.py:191-210`
(`_get_series_cache`) confirms the call chain: `client.get_series_list()` → filter
`volume_fp > 0` → `series_cache.save(cache["fetched_at"], cache["series"])` at line 209, once
an hour, unconditionally of `kalshi.categories`. `services/series_cache.py`'s current `save()`
(lines 64-72) only writes the blob table today — the design's proposed second write-through
(new `series_metadata`/`series_tags` upserts in the same transaction) is additive to that exact
function, on data already in hand. No new REST call, no assumed field. This is the strongest
section of the design.

One precision note, not a defect: the design says the app "already re-fetch[es] all ~13.6k
series... every hour" — true of the REST call itself, but `_get_series_cache` filters to
`volume_fp > 0` *before* `series_cache.save()` runs (`catalog_scan.py:205`), so what actually
lands in `series_metadata`/`series_tags` is the ~10,351-series volume-positive subset, matching
the investigation's own measured population. The design's later claims (§1.3: "already covers
every one of the real 17 categories") are correct because the volume filter isn't
category-scoped — just worth being precise that D1 populates from the filtered list, not the
raw ~13.6k.

## 2. `signal_log.series_of()` replacement

**CONFIRMED feasible**, with one caveat feeding into item 3. Read `title_cache.py` in full:
`market_titles.event_ticker` (schema `title_cache.py:68-75`) is populated from
`m.get("event_ticker")` at three real call sites (`main.py:972`, `mve_scan.py:224`,
`market_catalog/routes.py:263`) — a market's own field, not derived. `event_titles.series_ticker`
(schema `title_cache.py:79-86`, column added `title_cache.py:109`) is populated from
`event.get("series_ticker")` at `services/market_watch/event_metadata.py:85`, and is one of
`_fetch_event_titles`'s `required_event_fields` (`event_metadata.py:45-50`) — meaning a cached
event missing it gets re-fetched until it's present, i.e. self-healing, not a one-time capture.
The design's proposed `title_cache.series_ticker_for()` (one indexed join, `market_titles.ticker
→ event_ticker → event_titles.series_ticker`) is structurally identical to the existing
`fee_override_for_ticker()` (`title_cache.py:246-268`), which performs the exact same join
shape for a different pair of columns today — real precedent, not a new pattern.

Spot-checked two call sites beyond the named `excluded_series` gate, as asked:
- `strategy_engine.py:336-337`: `series = signal_log.series_of(signal.ticker)` feeds
  `config_overrides.resolve(..., series=series)` immediately, then the same `series` value is
  reused at line 501 for the `excluded_series` check — one computation, two consumers, exactly
  as the design assumes ("not a second one that could drift").
- `strategy_engine.py:694-695` (`validate_pending_fill`, the resting-limit-order fill-time
  re-validation path): identical shape — `series_of()` then `config_overrides.resolve(...,
  series=series)`. Neither call site needs an edit; both keep working under the corrected
  function body since its contract (a series-ticker string) is unchanged.

`signal_log.py:339` (`series_stats`) and `:380` (`series_stats_bulk`) both query `signals WHERE
series = ?` against the persisted column — confirmed this is exactly the mechanism that makes
the design's named accounting seam (§1.4) real: a `series_stats()` call after the fix for an
MVE ticker's *correct* series will not match the ~14.87% of historical rows still filed under
the old ticker-prefix value.

## 3. The historical-backfill exclusion — **WRONG, load-bearing**

The design states (§1.4): "a backfill would be incomplete anyway (`title_cache` is a rolling
watchlist cache, not a permanent archive — a ticker that rotated off the board months ago has
no cached `event_ticker` to join through today)." This is the design's own stated reason for
never backfilling `signals.series`, and it is **not what the code says**, checked directly:

- `services/state_view.py:142-145` (comment directly above `_scoped_market_titles`):
  *"state['market_titles']/state['event_titles'] themselves accumulate unbounded for the app's
  whole lifetime now (see services/title_cache.py) so history/clusters can still resolve an old
  ticker's title on their own separately-scoped requests..."*
- `main.py:956-958` (comment directly above the `new_market_titles` write): *"this growing
  unbounded server-side doesn't reintroduce the payload-size regression..."*
- `main.py:970-978`: every tick, `new_market_titles` is built from **every** ticker in `markets`
  (the tick's fetch result) and both `state["market_titles"].update(...)` and
  `title_cache.save_market_titles(...)` run — accumulate, never evict. `grep`ed the whole tree
  for prune/evict/trim/cap logic on these tables (`market_titles`, `event_titles`) — none
  exists; the one docstring that says "capped like market_titles" (`event_metadata.py:24`) is
  itself imprecise against the code it's describing.

`title_cache.py` was added 2026-08-08, one day after `signal_log.py`'s initial commit
(2026-08-07) — so the unbounded-accumulation window covers essentially the app's entire signal
history, not a recent slice.

Separately, and this is the sharper point: **`market_catalog.markets` — not `title_cache` — is
the one that's actually horizon-bounded**, the opposite of what the design's framing implies.
`services/market_catalog/market_catalog.py:216-225` (`upsert_markets`): *"Not a 'full' catalog
by design — skip anything with no schedule info at all, or scheduled well outside the near-term
horizon."* And critically, `market_catalog.markets.series_ticker` is populated at line 228 as
the literal `series_ticker` argument the caller passed in (`catalog_scan.py:375`:
`market_catalog.upsert_markets(s["ticker"], s.get("category"), result, ...)`) — a ground-truth
value from the series-scoped fetch that produced each market, not a derived/joined one, but
bounded to `_MAX_PAST_HORIZON_SEC`/`_MAX_FUTURE_HORIZON_SEC` around each market's
`occurrence_datetime`.

So: the design chose the genuinely-unbounded cache (`title_cache`) for `series_ticker_for()`
(a reasonable choice on its own terms — the join precedent is real, as confirmed in item 2), but
then justified *not backfilling* by describing that same cache as if it were the
horizon-bounded one — which is actually `market_catalog`, a table the design didn't even
propose using here. I could not run the one query that settles this (`SELECT COUNT(DISTINCT
ticker) FROM signals s LEFT JOIN market_titles mt ON mt.ticker = s.ticker WHERE mt.ticker IS
NULL` — this worktree has no live `data/*.db`, by design; those files are gitignored and
worktree-local per CLAUDE.md), so I can't state actual backfill coverage as a number. But the
premise the design uses to rule the option out is contradicted by the code it should have read,
and per this repo's own HARD RULE ("when a check is cheap, run it instead of reasoning about
it"), that one query is cheap and was not run before the decision was written down as settled.
This is not a cosmetic issue: 14.87% of all logged signals are MVE/sharded tickers
(investigation §0/X11), and getting their series attribution permanently wrong when a fix is
plausibly available conflicts with the data-plane HARD RULE's completeness/accuracy framing.

**Recommendation:** before Stage 5 builds on this, revise §1.4 to (a) drop the "rolling
watchlist cache" characterization, (b) state the real constraint correctly (unbounded since
2026-08-08, ~1-day gap at signal-history's very start, and only for tickers that were ever in
`state["markets"]` at some tick — itself very likely true of every ticker that ever produced a
signal, since whale-flow signals require WS trade-subscription, which this app's own "watchlist
is the coverage bottleneck" finding says is watchlist-scoped), and (c) run the one-query
coverage check and make the backfill decision from that number, not from an assumption.

## 4. D2's per-type extractor dispatch table

**CONFIRMED**, and honestly conservative exactly where it should be. Verified the two core bug
citations directly: `live_status.py:169` is `status = details.get("widget_status")` (exact
line); `catalog_scan.py:117` is `winner = details.get("winner")` (exact line);
`catalog_scan.py:190-191`'s `game_state.record(et, details, ...)` call and
`catalog_scan.py:142-148`'s `custom_strike` substring-match bug (X6) both match the design's
citations verbatim, including the exact line range.

Tennis pass-through (`tennis_tournament_singles`, "the control case, zero behavior change") is
correctly the safest possible entry — the investigation's S3/S4 revision explicitly measured
49,764/49,764 tennis payloads carrying both keys, and the design's table reflects that with no
invented value mapping.

`esports_match` and `political_race` are correctly flagged as assumptions requiring a live-payload
check before their branch ships — not silently designed around. This matches the investigation's
own S4/P3 findings precisely: the census established that these fields (`is_live`,
`race_call_status`/`tabulation_status`) *exist*, but not their terminal/called-vs-not-called
value vocabulary. The design does not invent a status mapping for either; it says explicitly what
needs verifying and defers the branch (§6 rollout item 2 restates this as a shipping
precondition, not just a footnote). This is the right amount of honesty for a design stage.

The "44 types exist today, 30 return live data and 14 return none" figure in §2.2 traces
directly to the investigation's own §4 D2 section (line 323 of the investigation doc, verbatim),
not invented by Stage 3.

## 5. The Pyth/Commodities claim

**CONFIRMED, fully.** Checked every citation in order:
- `config/settings.yaml:169` is exactly `underlying_tickers: []`.
- `services/kalshi/websocket.py:1431` is exactly `if self.underlying_tickers:` gating the
  `pyth_value` subscribe send — confirmed no subscription fires when the list is empty.
- `services/kalshi/websocket.py:733-735`: `if msg_type in ("cfbenchmarks_value", "pyth_value"):
  await on_index(msg_type, data.get("msg") or {})`. Traced `on_index` to
  `main.py:1281` → `services/whale_stream/index_stream_handlers.py:41` →
  `_process_stream_index`, whose line 56-57 is `elif msg_type == "pyth_value":
  index_feed.record_pyth(msg)`. Handler genuinely exists and is wired; it has simply never
  received a message.
- `services/index_feed/ingestion.py:154` is exactly `def record_pyth(msg: dict, ...)`, and its
  body writes to the same `index_ticks` table `record_cfbenchmarks` already uses — no schema
  change needed, confirmed by reading the function body, not just its signature.
- `services/kalshi/websocket.py:546-565` (`request_index_list`) confirms the CF Benchmarks
  discovery precedent the design proposes mirroring: it requires an existing `sid` from
  `self._subscription_sids.get("cfbenchmarks_value")` and returns early if absent — the same
  shape the design's Pyth `request_underlying_list()` proposal would need.

One precision nit, not a correctness issue: `docs/kalshi/pyth-value.md:33-40` documents
"Subscribe without `underlying_tickers` to create an empty subscription, then use
`underlying_list` to discover... and `subscribe_underlyings` to receive prices" — i.e. the
exchange's own protocol supports a genuinely non-circular empty-subscribe-then-discover flow.
The design calls seeding two known tickers first "necessary" because it's "genuinely circular"
— that's true of *this app's current client code* (which only sends the subscribe
`if self.underlying_tickers`, so an empty list never opens a subscription at all), but overstates
it as a protocol-level constraint. Doesn't change the recommendation (seeding
`["Metal.XAU/USD", "Metal.XAG/USD"]` first is still the right, low-risk move, consistent with
the CF Benchmarks precedent of a curated default over "subscribe to everything") — just a
sentence that reads more absolute than the doc supports.

## 6. Persistence idiom conformance

**CONFIRMED — no violation.** D1's `series_metadata`/`series_tags` tables land in
`services/series_cache.py`'s own file (`series_cache.db`), alongside the existing blob table —
one file, one concern (series metadata), matching CLAUDE.md's "one SQLite file per concern, no
shared DB" rule; this is a second table in an *existing* single-concern DB, not a new
cross-cutting one. Both are `CREATE TABLE IF NOT EXISTS`, genuinely additive (brand-new tables,
so no `_add_column_if_missing` is even needed — correctly not used where it isn't required).
`title_cache.series_ticker_for()` needs zero schema change — it's a new read function over
existing columns. D2's structured-target cache (§2.5) is explicitly kept **in-memory**
(`state["structured_targets_cache"]`), with a stated reason (a discovery aid re-resolved fresh
each tick, not accumulated history) rather than becoming a new SQLite file for no real query
need — the design correctly recognizes when *not* to add persistence, which is the harder half
of this rule to get right.

## 7. Scope check

**CONFIRMED for the three named exclusions** — none is hand-waved:
- `event_lifecycle`/`event_fee_update` (X10): verified `_CLASS_BY_MESSAGE_TYPE`
  (`websocket.py:109-123`) has entries for `market_lifecycle_v2`, `cfbenchmarks_value`,
  `pyth_value`, and the control types, but genuinely none for `event_lifecycle` or
  `event_fee_update`; `_handle_message`'s only lifecycle branch is `msg_type ==
  "market_lifecycle_v2"` at line 747, so both really do fall through undropped-nowhere-else and
  get discarded. Correctly deferred as its own hot-path WS task needing its own cost measurement
  (the HARD RULE's own bar), not designed here — appropriately conservative.
- `price_level_structure`/`price_ranges` (X12): design's "GAP-SKIP while paper mode continues"
  matches the investigation's own verdict exactly ("GAP-SKIP while in paper mode; GAP-PURSUE
  before live").
- `incentive_programs` (X13): design correctly restates the investigation's NEEDS-LIVE verdict
  and that the one settling query was never run — a spike, not a design; nothing invented here.

**But one real gap in this section (see §9 below): the design's §5 "deliberately not designed
further" list does not include, or even mention anywhere in the document, the two fee-changes
REST endpoints the investigation explicitly asked to be folded into D1.** That's not a scope
exclusion — it's an omission the self-review should have caught and didn't.

## 8. Stage 3's self-review — real, but narrower than this review's mandate

The "Spec self-review" section is genuine, not perfunctory: it names one concrete defect it
actually caught and fixed (a literal `...` placeholder in §1.3's original tag-junction delete
clause, replaced with the concrete delete-then-reinsert keyed off the batch's own ticker list) —
verified this claim is accurate by reading the current §1.3 SQL directly: no placeholder, a
real `DELETE FROM series_tags WHERE ticker = ?` executed against `tickers_this_batch`. The
internal-consistency pass (checking the "zero new API calls" claims in §1.3/§2.6/§3.2 against
each other, and D2 §2.4's `game_state.record()` reuse claim against D3 §3.3's hedge of it) is a
real cross-check, not boilerplate.

What it structurally could not catch, because it was "run headless" against the document's own
internal logic rather than against source: item 3's backfill-premise error (the design never
re-opened `state_view.py`/`main.py` to check whether `title_cache` is actually bounded — it
asserted a property of the cache without citing a line for it, the one claim in the whole D1
section with no code citation attached), and the fee-changes omission (§9). Both are exactly
the class of error a self-review that doesn't re-touch the filesystem will structurally miss —
which is the reason this review stage exists as a separate pass rather than folding into Stage
3's own sign-off.

## 9. Found by this review, not flagged by Stage 3: the fee-changes endpoints are silently dropped

The investigation's D1 section (§4, "Added on revision") explicitly says: *"`get-series-fee-
changes.md`... and `get-event-fee-changes.md`... are both in `llms.txt`... and neither has a
code path... A modest addition, and it belongs here because D1 already proposes carrying
`fee_type` forward off the series object."* Grepped the entire design document for
`fee_changes`/`fee-changes`/`series-fee`/`event-fee`: the only hit is a single citation of
`get-event-fee-changes.md:7`'s semantics inside §1.7 (the *deferred* X10 WS-handling
discussion, about clearing an override), not a REST ingestion design. Checked §5's "deliberately
not designed further" list for an explicit deferral — absent there too. The design simply never
addresses the two REST endpoints the investigation asked D1 to carry. This should either be
added to D1's schema/population design (a scheduled/historical `series_fee_changes`-shaped
table, or a note that `fee_type`/`fee_multiplier` as captured are current-value-only and the
change history is out of scope) or explicitly moved into §5 with a stated reason — right now
it's neither designed nor acknowledged as excluded, which is the one place this design is
silent rather than either building or explicitly deferring.

---

## Overall recommendation

**Six of eight items confirm cleanly against source, several down to the exact cited line.**
D1's core mechanism (§1) and D2's extractor design (§2) are both solid enough for Stage 5 to
plan directly — I found no invented fields, no wrong line citations among dozens checked, and
the two places the design flags its own assumptions (esports/political_race value vocabularies)
are exactly the places the investigation's own evidence says to flag them.

**Two things need fixing before Stage 5 builds an implementation plan on this document,
specifically:**

1. **§1.4's backfill-exclusion paragraph.** The stated reason ("`title_cache` is a rolling
   watchlist cache") is backwards — the code says `title_cache` accumulates unbounded and
   `market_catalog` is the horizon-bounded one — and the decision was made without running the
   one cheap query (`signals.ticker` LEFT JOIN `title_cache` coverage) that would settle actual
   backfill feasibility for the 14.87% of signal history currently mis-attributed to
   sharded/MVE ticker prefixes. This doesn't necessarily mean the backfill should happen — it
   means the *decision* needs to be re-made from a correct premise and a real number, not
   reasoned from an unverified claim about cache behavior.
2. **The missing fee-changes endpoints (§9).** A small addition to D1 or an explicit line in §5
   — either is fine, but the document needs to say which.

Neither requires touching D2, D3, or D4, and neither invalidates the rollout ordering in §6.
This is a "fix two paragraphs" revision, not a redesign.
