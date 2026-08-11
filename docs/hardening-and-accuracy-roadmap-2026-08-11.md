# Hardening & accuracy roadmap (2026-08-11)

Direct request, after the phase 97 trade-tape incident: "write a comprehensive
to-do list of things that will harden the application and make all of the
interpretations and predictions going on more accurate." A second, standing
instruction to weigh while sequencing this: "keep in mind that all of the
analyzers, engines, heuristics, should have the potential to inform each
other, like a web of expertise" - Part 2 below is organized specifically
around that principle, not just around individual-engine fixes.

Every claim below was checked against the current code before being written
down (grep/read, not memory) - findings state what's confirmed, not assumed.
This is a to-do list, not an implementation - nothing here has shipped yet.

## Part 1: Event-lifecycle awareness (direct request)

Triggered by a live screenshot: "MEN'S COLLEGE BASKETBALL TOURNAMENT CHAMPION"
- a real, currently-open market, 19 outcomes, $37,427 cumulative volume, but
individual outcomes at vol 5-50. Direct quote: "this specific market is open
but it sure doesnt seem whale-worthy. maybe it's because of time of day and
not when the event is occuring. i want my system to account for things like
this. pre-tail, mid-series, post-tail type analysis." A related, earlier
report from the same session: "series sometimes stay in the watchlist well
after the volume of the market has gone down to say even 10 positions. that
needs to be factored in throughout."

### Confirmed root cause
`KalshiClient.round_robin_select()` ranks candidate series purely by
**cumulative 24h volume** (`get_candidate_markets`'s own volume-sort). This
is a lagging, cumulative metric with no relationship to *when* the event
actually occurs:
- A season-long futures market (like the tournament-champion example) can
  accumulate real 24h volume from casual activity while genuinely dormant
  months before the event it resolves on - "pre-tail."
- A market whose real occurrence has already passed but hasn't settled yet
  can keep ranking on residual volume from when it *was* active - "post-tail."
- Only `kalshi.live_markets_only` mode does anything occurrence-aware at all
  (`_fetch_live_status`'s `_LIVE_STATUS_LOOKAHEAD_SEC`/`_LIVE_STATUS_
  LOOKBACK_SEC` window), and that's an opt-in discovery filter, not a ranking
  signal - the *default* (non-live-only) discovery path never looks at
  `occurrence_datetime` at all.
- `series_evaluator.py`'s whale-worthiness verdict has the identical blind
  spot: `evaluate_pending()` judges qualifying rate over a fixed
  `min_observation_sec`/`max_observation_sec` window with no idea whether
  that window landed pre-tail (unfairly judging a dormant-until-showtime
  series as "not worthy") or mid-series (a fair judgment).

### Proposed design: a three-phase activity model
For any market/series with a knowable `occurrence_datetime` (most sports/
event markets already have this - `market_catalog` schema already stores
it):
- **pre-tail**: `now < occurrence_datetime - <threshold>` - event hasn't
  started and isn't imminent. Expected low activity; a low current volume
  here is *normal*, not evidence of disinterest.
- **mid-series**: within the live-status window (already exists -
  `_LIVE_STATUS_LOOKAHEAD_SEC`/`_LIVE_STATUS_LOOKBACK_SEC`, 1h before through
  6h after scheduled start) - this is where whale activity is genuinely
  meaningful and watchlist slots are most valuable.
- **post-tail**: event has concluded (past `_LIVE_STATUS_LOOKBACK_SEC`, or
  Kalshi's own `widget_status`/`status` says finished) but the market hasn't
  closed/settled. Lingering interest, declining relevance.
- Markets with **no single occurrence** at all (political/economic futures,
  season-long futures, mention markets) don't fit this shape cleanly - flag
  them as a fourth "no-occurrence" bucket that keeps today's pure-volume
  ranking, not forced into a phase that doesn't apply. Don't guess a fake
  occurrence time for these.

### Where this should plug in (the "throughout" in the direct request)
1. **Discovery ranking** (`round_robin_select`): weight/re-rank candidates by
   phase, not just raw volume - mid-series candidates should outrank
   pre-tail/post-tail ones of similar raw volume, freeing watchlist slots for
   markets where whale signals actually mean something right now.
2. **series_evaluator's verdict window**: a series currently pre-tail
   shouldn't have its observation clock running yet (or should get a
   materially longer window) - being judged "not whale-worthy" for looking
   quiet before its own event has even started is a false negative baked
   into the current design.
3. **Whale confidence scoring** (`composite_confidence_breakdown`): a large
   trade during pre-tail dormancy is a *different* signal than the same size
   trade mid-series (arguably more unusual, arguably more likely to be noise
   - genuinely an open question, not obvious which direction). Worth a new
   factor or a modifier on an existing one (`unusualness_factor` is the
   natural home) once real data exists to calibrate it against.
4. **Watchlist eviction**, the more literal reading of the second report:
   today's full-recompute-every-tick design means a series *should*
   naturally fall off once it stops ranking - but a stale-but-still-decent
   24h cumulative volume figure can keep it artificially ranked well past
   the point its *current* activity justifies. A "current activity" signal
   (e.g. trades in the last N minutes, not 24h cumulative) as a tie-breaker
   or override would directly address "stays in the watchlist well after
   volume has gone down."

### What's needed before implementing
- A concrete threshold decision for pre-tail's cutoff (how far before
  `occurrence_datetime` counts as "not yet relevant") - likely needs to vary
  by category (a multi-day political market's pre-tail window looks nothing
  like a single game's).
- A real, live-data check of how much of the *current* watchlist is
  pre-tail/post-tail today, to size the actual problem before designing
  around it (the one screenshot is one confirmed instance, not yet a
  measured prevalence).

## Part 2: Cross-engine "web of expertise" (direct request)

Direct instruction: "all of the analyzers, engines, heuristics, should have
the potential to inform each other, like a web of expertise." Audited every
pairing below against the current code (not assumed) - each item states
what's confirmed already-wired vs. a confirmed gap.

**Already wired** (for contrast, so the gaps below read as real gaps, not a
guess at what's missing):
- Market Analyst (LLM) → whale confidence scoring, via `analyst_factor`
  (`market_analyst_agent.analyst_lean()`, a cheap DB read).
- `series_evaluator` → discovery (`ineligible_series()` filters candidates)
  and → Market Analyst's per-series context (`_build_series_context`).
- `config_performance`'s change-history → `advisory_engine.change_effect()`
  (measured before/after effect on applied changes).
- Signal log → `confidence_calibration` → `whale_confidence_weights` (the
  whole calibration auto-apply loop, phase 90/95).

**Confirmed gaps** (grepped, not guessed):

1. **`candidate_log` (rejected/counterfactual candidates) never reaches
   `advisory_engine`.** `advisory_engine.py` has zero references to
   `candidate_log` anywhere. `candidate_log.gate_summary()` already computes
   a real `hypothetical_win_rate` for candidates a gate rejected (e.g. "what
   would have happened to entries just below `entry_threshold`") - this is
   *exactly* the counterfactual data `_entry_threshold_recommendation()`
   would want alongside its existing confidence-bucket analysis of trades
   that *did* qualify, and it's sitting unused. Wiring it in would let a
   threshold recommendation reason about both sides of the cutoff instead of
   only the accepted side.

2. **No category-conditional tuning anywhere.** `trade_category.py`/
   `regime_analytics.by_category()` exist and the History tab already shows
   win-rate-by-category - but `advisory_engine.py` has zero references to
   either. Every rule-based suggestion (`entry_threshold`,
   `longshot_entry_threshold_bonus`, exit percentages) is one global knob
   applied identically to Politics and Sports and Crypto, even though the
   category breakdown this app already collects might show they perform
   very differently. A category-scoped variant of the existing within-
   variant recommendations (not a full redesign - reuse the same bucket-
   comparison logic, just grouped by category first) would close this.

3. **`regime_analytics` (hour-of-day/day-of-week/category segmentation) is
   read-only, never fed into live entry gating.** Confirmed:
   `strategy_engine.py` has zero references to `regime_analytics`. The three
   `/api/regime/*` routes are the only callers, all descriptive GET
   endpoints for the History tab. If Tuesday-morning entries genuinely win
   less than Saturday-night ones (a real, already-computed finding once
   there's enough data), nothing in the actual entry decision knows that.
   This is deep-scan finding 7 from the 2026-08-10 platform audit
   (`docs/platform-deep-scan-findings-2026-08-10.md`), still open.

4. **`series_evaluator` verdicts never reach `advisory_engine`.** A series
   `series_evaluator` has judged low-qualifying-rate (rejected, serving
   backoff) is exactly the kind of series `strategy.min_whale_winrate_pct`/
   `strategy.excluded_series` might also want to know about - today these
   are two entirely independent judgments (qualifying rate vs. realized win
   rate) with only a *read-only* cross-check between them
   (`GET /api/series-evaluator/status`'s `below_winrate_floor` flag, phase
   82) - no suggestion engine acts on the disagreement it can already
   surface.

5. **`market_strategy` (the whale-independent strategy) has no calibration
   tooling at all.** Confirmed: zero references to `confidence_calibration`
   in `market_strategy.py`. `market_strategy`'s own confidence factors
   (momentum, price-band, spread) are exactly as parallel a dataset as the
   whale side's factors, and get none of the calibration-band/gap analysis
   `confidence_calibration.py` already does for whale signals. Deep-scan
   finding 6, still open.

6. **`_build_full_spectrum_context()` doesn't include `candidate_log` or
   `regime_analytics` data.** The "Feed the Analyst" full-spectrum LLM scan
   already assembles a lot (full config, trade history, whale stats,
   variant summaries, change history) but is missing two datasets that
   would materially improve what it can reason about - the rejected-
   candidate counterfactual data (item 1) and the category/time
   segmentation (item 2/3). Cheapest fix in this whole list: it's an
   aggregation change, not new data collection - everything it would read
   already exists.

7. **Deep-scan finding 5 (wash-trading/adversarial whale detector) - still
   open, unchanged since 2026-08-10.** Whale confidence's `agreement_factor`
   only checks same-side agreement; an alternating-side pattern (a form of
   wash trading) scores as neutral, not suspicious. Explicitly sample-size-
   gated in the original finding - revisit once there's a real incident or
   enough data to build a detector against, not before.

### Suggested sequencing (cheapest/highest-signal first, matching this
project's own established "actually verify, don't reorder by vibes"
practice)
1. Item 6 (full-spectrum context gap) - pure aggregation, no new logic,
   immediately makes the most expensive/broadest analysis mode smarter for
   free.
2. Item 1 (candidate_log → advisory) - the data already exists and
   `gate_summary()` already computes the exact number needed; "just" a new
   comparison function in `advisory_engine.py` in the same shape as the
   existing ones.
3. Item 4 (series_evaluator ↔ advisory cross-reference) - similarly, the
   disagreement is already surfaced (phase 82); this closes the loop from
   "visible" to "actionable."
4. Items 2/3 (category and regime conditioning) - larger, touches the
   suggestion-generation shape itself (grouping before bucketing), needs
   real design thought about how a category-scoped suggestion should render
   differently from today's global ones.
5. Item 5 (market_strategy calibration) - a real second implementation of
   an existing pattern (`confidence_calibration.py`'s shape, applied to a
   second dataset), not a new concept - mechanical but non-trivial size.
6. Item 7 (wash-trading detector) - deliberately last, per its own original
   sample-size gating.

## Part 3: Resilience follow-ups from the phase 97 incident

Direct fixes already shipped in phase 97 (uncapped/paginated trade fetching,
`asyncio.to_thread` offloading, batched writes, WAL mode). What that incident
exposed but didn't fix, worth a deliberate look:

1. **The same unthrottled-concurrency pattern exists in `_fetch_markets`
   (pinned-watchlist branch), `_fetch_event_titles`, and `_fetch_live_status`
   - all still fire one `asyncio.gather` per watched ticker with no
   concurrency cap.** These are reads (not the blocking-SQLite-write pattern
   that caused phase 97's actual freeze), so lower severity, but the same
   *class* of risk: confirmed live that a 513-market cold start took ~74s,
   and a `/api/markets/{ticker}/trades` call shortly after a restart hit a
   real 502 (Kalshi-side rate-limit exhaustion from the burst). A shared
   `asyncio.Semaphore` around per-ticker Kalshi API fan-out (a small,
   central helper, not four separate ad-hoc limits) would bound worst-case
   concurrency across all of these at once, not just trade-tape.
2. **No visible indicator today for "the last tick took unusually long" or
   "we're currently rate-limited."** `state["error"]`/the tick-health badge
   (phase 66) surfaces an outright exception, but a tick that's merely slow
   (like the 74s cold start) or repeatedly hitting 429s produces no signal
   anywhere - confirmed by grepping for a tick-duration or rate-limit-hit
   metric in `state`; neither exists. Worth a simple `state["last_tick_
   duration_sec"]` and a rolling count of recent 429s, surfaced the same way
   the tick-health badge already is.
3. **`call_with_backoff`'s 4-retry/~7.5s-max schedule was tuned for isolated
   calls, not a burst of hundreds at once.** Confirmed live: a single
   `/api/markets/{ticker}/trades` request exhausted all 4 retries and 502'd
   during the post-restart burst. Once the semaphore in item 1 bounds
   concurrency, this may resolve itself - worth re-checking empirically
   after that ships, not re-tuning blind.
4. **Extend the WAL-mode/off-thread pattern audit to other high-frequency
   write paths**, not just the two this incident happened to hit. Not yet
   audited: `market_history.record_snapshots()` (called every tick for the
   whole watchlist) and `trade_category.record_category()` (called once per
   trade placement) - both plausibly fine at today's volume (trade
   *placements* are rare relative to raw trade *prints*), but worth the same
   verification this incident forced on the two that actually broke, rather
   than assuming.

## Part 4: General accuracy hardening (not yet covered above)

1. **`market_history.snapshots`' `spread`/`volume_24h`/`time_to_close_sec`
   columns are still write-only** (flagged in the 2026-08-10 data-robustness
   audit, never revisited) - real collected data with zero consumers
   anywhere. Directly relevant to "more accurate predictions": spread and
   time-to-close are exactly the kind of liquidity/urgency signals a
   confidence factor could use, and the data's already being paid for.
2. **No margin-of-error framing on whale-confidence-weight auto-apply
   decisions**, unlike `series_evaluator`'s per-series win rate (which
   already gained a real margin-of-error column, phase 86, via
   `stats_power.margin_of_error_pts`). The calibration auto-apply gate
   (phase 95's `auto_apply_min_resolved_signals`) is a sample-size floor,
   not a confidence-interval-aware one - reusing `stats_power.py`'s already-
   built math here would make an already-shipped safeguard measurably
   stronger, not just bigger.
3. **Real per-series notional overrides
   (`whale_watcher_kalshi.min_notional_usd_by_series`) are 100% manual** -
   no suggestion engine has ever proposed one, despite `series_evaluator`
   and `signal_log.series_stats()` both already having the qualifying-rate/
   win-rate data that would make a good per-series threshold suggestion
   possible. A natural extension once item 4 (series_evaluator ↔ advisory)
   in Part 2 ships.
