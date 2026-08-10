# Config-tuning data gaps (2026-08-10)

Written immediately after a full manual review of every field in
`config/settings.yaml` against real historical data (see commit `20a334a`
and the two commits before it). That review is the concrete evidence base
for everything below — not a generic wishlist. Of ~60 tunable fields, only
a handful could be changed with real statistical backing; most were left
alone specifically because no mechanism exists yet to evaluate them. This
doc catalogs those gaps and what would need to be built to close each one,
so a future pass (mine or an agent's) has more than "read the same rows
again" to work with.

## The core problem: what today's review could and couldn't see

Every existing self-tuning surface — `trade_analytics.compute_insights()`,
`advisory_engine.generate_recommendations()`, `confidence_calibration.py`,
`series_evaluator.py`, the market analyst agent — answers questions of the
shape *"among trades that were actually placed, did X correlate with
winning?"* That's a real, useful question, and it's why exit-side fields
(`exit_sentiment_lean_pct`, `auto_exit_*` weights, `min_resolved_for_
whale_filter`) could be tuned today with actual evidence.

But a large fraction of `config/settings.yaml` — nearly everything under
`kalshi.*`, `whale_watcher_kalshi.min_notional_usd*`, `strategy.
entry_threshold`, `strategy.min_whale_winrate_pct`, every `market_strategy.
min_*`/`max_*` discovery filter, `strategy.longshot_*` — is a **gate**, not
a **scoring weight**. A gate's job is to decide whether a signal ever
becomes a trade at all. None of today's data answers "what happened to the
candidates this gate rejected" — because nothing rejected is ever logged.
That's the single biggest gap, and it's why `entry_threshold`,
`min_notional_usd`, `min_momentum_delta`, `max_spread`, and most of
`kalshi.*` got zero changes today despite being exactly the kind of value
most people would assume is "tunable from data."

The gaps below are grouped by mechanism, not by config section, since
several unrelated-looking fields are blocked by the same missing piece of
infrastructure.

---

## Gap 1: No visibility into rejected candidates (the counterfactual gap)

**Blocks:** `strategy.entry_threshold`, `strategy.min_whale_winrate_pct`,
`strategy.longshot_price_threshold`/`longshot_entry_threshold_bonus`,
`market_strategy.min_price`/`max_price`/`max_spread`/`min_volume_24h`/
`min_momentum_delta`/`min_seconds_to_close`/`entry_confidence_threshold`,
`whale_watcher_kalshi.min_notional_usd`(`_by_series`), `kalshi.min_volume_24h`.

Confirmed directly today: `advisory_engine._entry_threshold_recommendation()`
and `_longshot_bonus_recommendation()` both return `None` against the full
real trade history — not because the data says "leave it alone," but
because the only data that exists is trades that already cleared
`entry_threshold`. There is no confidence-bucketed sample of signals that
scored *just below* threshold to compare against. Every one of these gate
fields has the identical problem: raising or lowering the number changes
which candidates get a *chance* to become a trade, and the only way to
judge that honestly is to know what happened to the ones on the other side
of the line.

**Mechanism to build:** a lightweight rejected-candidate log. When a
signal/market candidate fails a specific gate, record it — which gate,
what value it had vs. the threshold, and (critically) the ticker, so it
can later be joined against the market's real outcome the same way
`signal_log.resolved_signals_with_factors()` already resolves accepted
signals. This does *not* mean acting on rejected candidates — just
observing what would have happened. Concretely:
- A new table, same idiom as everything else in `services/` (own SQLite
  file, `CREATE TABLE IF NOT EXISTS`, additive only): `ticker, gate_name,
  observed_value, threshold_value, rejected_at`.
- One insert per rejection, at each existing gate check site
  (`strategy_engine.py`'s `FollowTheWhaleStrategy.evaluate()`,
  `market_strategy.py`'s `_evaluate_one()`, the discovery filters in
  `main.py`) — these are all places that already compute the comparison,
  just currently only act on the boolean, not log it.
- A resolution job mirroring `signal_log`'s existing resolve step: for
  each logged rejection, look up whether that market later resolved
  yes/no, and what a hypothetical entry at that price would have done.
- This unlocks real answers to "if `entry_threshold` were 0.55 instead of
  0.6, how would the marginal candidates in that band have performed" —
  the exact question `advisory_engine` already tries to answer for
  applied trades, just extended to candidates that never got the chance.

This is the highest-priority single addition — it's what most of the
other gaps below turn out to be special cases of.

---

## Gap 2: No backtesting/replay engine

**Blocks:** any "what if this threshold had been different" question,
generalized beyond simple gates — e.g. compound questions like "what if
`entry_threshold` were 0.55 **and** `min_whale_winrate_pct` were 35 at the
same time," which Gap 1's log alone can't answer cleanly once two gates
interact.

`signal_log` already stores every signal ever generated (86k+ rows), with
its full factor breakdown and eventual resolution, whether or not it
became a trade. That's most of the raw material for a real backtest
already sitting in the database. What's missing is a harness that replays
it under a hypothetical config:

- **Stateless replay** (buildable today, no new schema): for every
  historical signal, re-run just the entry-gate logic
  (`strategy_engine.py`'s threshold/whitelist checks) with a candidate
  config, and tally what a naive "would this have been accepted" pass
  looks like against the signal's real resolution. This answers most gate
  questions (`entry_threshold`, `min_whale_winrate_pct`, spread/price/
  volume filters) without needing Gap 1's new logging at all, since
  `signal_log` already has the input data for signals that exist —
  the only blind spot stateless replay can't fix is candidates that were
  filtered *before* a signal was even generated (e.g. a market excluded by
  `kalshi.min_volume_24h` during discovery never reaches `signal_log` at
  all — that part still needs Gap 1).
- **Stateful replay** (materially harder, not recommended until stateless
  proves valuable): replaying `cooldown_sec`, `max_open_positions_per_
  series`, `max_position_pct`, and bankroll/risk-halt state requires
  simulating the whole trading loop against historical ticks, not just
  scoring signals independently — a real project, probably its own
  design pass rather than a quick add.

Recommended scope: build the stateless version first (it's a pure
function over `signal_log` rows, testable the same way every other engine
in this app is), explicitly punt on stateful replay until there's a
concrete question it's needed for.

---

## Gap 3: Config-variant fragmentation dilutes the evidence that does exist

**Blocks:** the cross-variant comparison (`advisory_engine.
variant_summaries()` / `_cross_variant_recommendations`) and `config_
performance.change_effect()`'s before/after measurement.

Confirmed today via `/api/advisory/status`: 16 distinct `strategy.*`
config-fingerprint variants exist in `config_performance`, and the large
majority have 0 resolved trades — a handful (104, 19, 11, 1) hold nearly
all the real signal. Every manual tweak (mine included, applying today's
Advisory suggestions) mints a brand-new fingerprint and starts that
variant's trade count back at zero, because `config_performance.
fingerprint()` hashes the *entire* `strategy.*` dict, not the field that
actually changed. This means `change_effect()` — the mechanism built
earlier this session specifically to measure "did the change I just
applied help" — will show `None`/no-data for a long time after almost any
single-field edit, even though the vast majority of *other* fields didn't
change.

**Mechanism to build:** per-field windowed before/after comparison,
distinct from whole-fingerprint variant comparison. The raw material
already exists — `config_performance.applied_changes` already has
`config_path` and `applied_at` per field, from this session's 3D work.
What's missing is a query that uses *that* timestamp as a boundary (`WHERE
opened_at < applied_at` vs `WHERE opened_at >= applied_at`) for trades
regardless of full-fingerprint match, the same relaxation `compute_
insights()`'s per-field suggestions already got in 3A — `change_effect()`
just needs the equivalent treatment for its *own* before/after window
instead of relying on exact fingerprint equality on both sides. This is a
genuinely small addition (one new function alongside the existing
`change_effect()`, reusing `applied_changes` rows that already exist) with
outsized value, since it directly fixes the tool most relevant to "did the
thing I just changed actually help."

---

## Gap 4: No per-series granularity for global thresholds

**Blocks:** `strategy.min_whale_winrate_pct` (global 40%),
`whale_watcher_kalshi.min_notional_usd` (global $500, with an unused
`_by_series` override map sitting empty).

Confirmed today via direct `series_stats()` queries: real per-series win
rates range from 25.7% (`KXMLBGAME`, n=766) to 58.5%+ on the better series
— both well-sampled, not noise. A single global 40% floor is already
correctly excluding `KXMLBGAME` and `KXATPCHALLENGERMATCH` (34.4%, n=2326)
via `min_resolved_for_whale_filter`, so the mechanism *works*, but it's
binary (excluded vs. not) where the underlying data supports something
more graded — e.g. a series at 38% isn't obviously "the same" as one at
15%, but both currently just fail the same gate the same way.

There are actually **two** separate per-series mechanisms in this app
today (`min_whale_winrate_pct` filtering by realized win rate, and
`series_evaluator.py` filtering by trade-qualifying-rate) that have never
been cross-checked against each other — it's not known today whether a
series `series_evaluator` approves is also one with a healthy win rate, or
whether they're measuring genuinely independent things. A view joining
`series_evaluator.overview()` against `signal_log.series_stats()` per
series would answer that directly, and would be a reasonable extension of
the existing series-evaluator log panel (list qualifying rate *and* win
rate per row, not qualifying rate alone) — small addition, real
diagnostic value, no new persistence needed since both data sources
already exist.

A genuine per-series *override* mechanism for `min_notional_usd` already
exists in schema (`min_notional_usd_by_series`) but is empty and has no UI
or advisory suggestion path populating it — worth noting as "the config
surface already anticipated this, the tuning mechanism to actually fill it
in from data doesn't exist yet."

---

## Gap 5: `market_strategy.stop_loss_pct` has zero real data to calibrate against

Set to `0.2` today as a disclosed, not-data-backed default (no
`stop_loss_pct` closes exist anywhere in `market_broker.db` — it's a new
field). This isn't fixable by better analysis of *existing* data; it
requires the field to actually be live for a while and start producing
`stop_loss` close-type rows, the same way `strategy.stop_loss_pct` (the
whale-follow equivalent, currently `null`/unused) would need the same.
Flagging this explicitly rather than folding it into a "mechanism to
build" — the correct next step here is just time + real trades, then a
revisit once `trade_analytics.build_trade_history()` has stop_loss rows
for `market_strategy` to look at (`_rec_id`/advisory machinery is already
generic enough to handle it once data exists — this is a data gap, not a
tooling gap).

---

## Gap 6: The whale-confidence scoring formula's own validity is under-verified

Confirmed today via `confidence_calibration.py`'s bands (now live after
this session's crash fix): the `<50%` confidence band actually
*outperforms* the `70-80%` band in realized win rate. The high bands are
thin-sampled, so this may resolve with more data — but as it stands, it's
a real signal that the composite score's calibration (not just individual
factor discrimination, which the per-factor gap table already covers) is
weak. This directly informed leaving `strategy.kelly_fraction_of_cap` at
`0` today: sizing positions bigger when "confidence" is high only helps if
high confidence is actually associated with winning more, and the data
right now doesn't clearly support that.

**Mechanism to build:** nothing new schema-wise — `confidence_calibration.
py` already computes exactly this. What's missing is *tracking it over
time* rather than as a single point-in-time snapshot: a small table
logging each calibration report's headline numbers (overall win rate,
per-band gaps, discrimination gaps) on a rolling basis (e.g. once a day,
or once per N new resolved signals) would let a future review ask "is
calibration improving as more data and the retuned weights accumulate, or
stuck" instead of re-deriving the same one-shot snapshot every time. This
is the natural gating condition for ever safely turning on `kelly_
fraction_of_cap` above 0 — don't scale position size by a confidence score
until that score has a documented track record of actually predicting
outcomes.

---

## Gap 7: No cross-strategy comparison between whale-follow and market-native

Directly asked about this session and not resolved with a real mechanism
(today's review compared their *aggregate* summaries side by side —
13.6% win rate for market_strategy vs. whale-follow's much better
figures — but that's two numbers looked at together, not a real joint
analysis). The two strategies write to entirely separate storage
(`paper_broker.db` vs `market_broker.db`, no shared key) and nothing joins
them today. The genuinely interesting question — did the two strategies
ever evaluate the *same* ticker and disagree on direction, and who was
right — currently can't be answered at all, since neither strategy logs
"I looked at this ticker and decided not to trade it," only what it
actually traded (this is Gap 1 again, from a different angle: market_
strategy's own non-entries are just as invisible as whale-follow's).

**Mechanism to build:** once Gap 1's rejected-candidate log exists for
both strategies, a join on ticker + approximate timestamp between the two
strategies' candidate/decision streams would surface real disagreement
cases. Until then, the only honest comparison is the aggregate-summary
side-by-side already done today — worth stating plainly rather than
implying a deeper comparison happened.

---

## Gap 8: Only derived factors are logged, not raw inputs

`signal_log`'s `factors_json` stores the 8 already-computed 0-1 factor
scores (`depth_factor`, `unusualness_factor`, etc.) but not the raw
numbers those factors were derived from (actual dollar notional, actual
market spread/volume at signal time). This means a future analysis can
ask "does `depth_factor` discriminate" but not "would a *differently
shaped* transform of the same raw depth data discriminate better" —
re-deriving the raw inputs after the fact requires reconstructing
`market_catalog`/`market_history` state at the exact signal timestamp,
which is unreliable (those tables are watchlist-scoped and rotate, per
`CLAUDE.md`'s already-known "write-only column" gaps in `market_history`).

**Mechanism to build:** log 2-3 raw fields alongside `factors_json` at
signal-creation time in `services/whalewatchers/kalshi_trade_tape.py` —
notional_usd, spread, volume_24h as they existed at that instant. Cheap
(a few extra columns, additive `ALTER TABLE`), and turns "is the current
formula's shape right" from an unanswerable question into a real one,
without needing to touch the formula itself yet.

---

## Gap 9: No time-of-day / category / liquidity-regime segmentation

Already flagged as deep-scan Finding 7 (`docs/platform-deep-scan-
findings-2026-08-10.md`) — repeating here only to connect it explicitly to
config tuning: every win-rate figure used in today's review was a single
global or per-series aggregate. It's entirely possible (untested) that,
e.g., `entry_threshold` should behave differently for a fast-moving sports
market near close than a slow-moving weekly economics market — but
nothing today buckets trades by category, hour-of-day, or time-to-close-
at-entry to check. `market_catalog.category` is already fetched and
cached but stripped before reaching most consumers (another already-known
write-only-ish field per `CLAUDE.md`). Building this out is real new
aggregation work, not a quick add — noting it here as a config-tuning
consumer for Finding 7's eventual implementation, not proposing new scope
beyond what's already tracked.

---

## Gap 10: No documented statistical basis for "is n enough"

Every judgment made today about whether to trust a finding used an
inconsistent set of ad hoc thresholds already scattered through this
codebase: `trade_analytics.confidence_label()`'s n<5/n<15/n>=15,
`advisory.min_resolved_trades_per_variant` (now 10),
`confidence_calibration.min_resolved_signals` (50), `series_evaluator.
min_trades_observed` (1000). These aren't wrong, but none of them are
derived from an actual power calculation (e.g. "how large a win-rate gap
can we reliably detect at this n, given realistic base rates") — they're
reasonable-looking round numbers. This is lower priority than the gaps
above (it doesn't unlock any new *data*, just makes the existing
sample-size gates more rigorous and consistent with each other), but worth
recording since today's review repeatedly had to reason about this
informally (e.g. eyeballing `sqrt(p(1-p)/n)` by hand for the per-series
win-rate comparisons) rather than pointing at a shared, documented
convention.

---

## Suggested build order

1. **Gap 1 (rejected-candidate logging)** — unlocks evidence-based tuning
   for the largest number of currently-frozen fields; every gate field in
   `strategy.*`/`market_strategy.*`/`kalshi.*`/`whale_watcher_kalshi.*`
   benefits.
2. **Gap 3 (per-field before/after windowing in `change_effect()`)** —
   small, reuses data that already exists (`applied_changes`), directly
   fixes the tool most likely to be used right after any future config
   change.
3. **Gap 2, stateless half only (backtest replay over existing
   `signal_log` rows)** — generalizes Gap 1 to compound/hypothetical
   configs once the simple version proves useful; explicitly defer the
   stateful (portfolio-state-aware) half.
4. **Gap 8 (raw signal fields)** — cheap, additive, no urgency but no
   reason to delay once touching `kalshi_trade_tape.py` for other reasons.
5. **Gap 4 (series_evaluator × win-rate cross-check view)** — small,
   diagnostic-only, no new persistence.
6. **Gap 6 (calibration-history tracking)** — the real gate before ever
   raising `kelly_fraction_of_cap` above 0; low urgency until that's an
   active goal.
7. **Gap 7 (cross-strategy join)** and **Gap 9 (regime segmentation)** —
   both depend on earlier gaps (7 on Gap 1; 9 on new aggregation work) and
   are lower priority per the deep-scan doc's own ranking of the regime
   finding.
8. **Gap 10 (formal power-analysis convention)** — housekeeping, do
   whenever touching the sample-size gates for another reason.

**Gap 5** (stop-loss calibration) isn't on this list — it's not blocked by
missing tooling, just by not having run long enough yet with the field
live.

## What's explicitly NOT a gap

Worth stating plainly, since most of this doc is about missing pieces:
exit-side tuning (`exit_sentiment_*`, `auto_exit_*` weights,
`min_resolved_for_whale_filter`) already has a real, working, sufficiently
-evidenced mechanism today — that's exactly why those were the fields
actually changed in today's review, not left as open questions. The gaps
above are specifically about the discovery/entry/gate side of the config
surface, where the "only see what passed" blind spot is structural, not a
matter of needing more time for existing mechanisms to accumulate data.
