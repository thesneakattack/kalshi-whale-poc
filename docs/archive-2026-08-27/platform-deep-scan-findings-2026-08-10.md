# Platform deep-scan findings (2026-08-10)

Direct request: re-read `docs/prediction-markets-research-reference.md` and
`docs/prediction-market-strategy-alignment-plan.md` in full, then read the
actual current implementation of every core decision-making engine
(`strategy_engine.py`, `market_strategy.py`, `whale_simulator.py`,
`whalewatchers/kalshi_trade_tape.py`, `confidence_calibration.py`,
`advisory_engine.py`, `series_evaluator.py`, `risk_manager.py`,
`paper_broker.py`, `kalshi_fees.py`, `market_analyst_agent.py`) and form a
real opinion on where it diverges from prediction-market best practice, and
whether that divergence is deliberate or accidental. Most of the alignment
plan's own Part 2 findings (cluster/trend/analyst factors, fee modeling,
FLB-tiered entry threshold, `min_notional_usd`) are already shipped and
working as designed — confirmed by reading the current code, not assumed —
so they aren't re-litigated here. What follows are seven gaps that survived
that read: real, concrete, and not already tracked in `ROADMAP.md` or either
existing research doc. None of these are "the algorithm is wrong" — they're
places where a real input this app already computes (a confidence score, an
analyst estimate, a calibration bucket) stops one step short of the decision
it could plausibly improve.

---

## Finding 1 — Position sizing is pure fixed-fraction; it never uses the confidence score it just computed

**Grounding.** `services/risk_manager.py::max_trade_size` (lines 79-80):

```python
def max_trade_size(self, bankroll: float, max_position_pct: float) -> float:
    return round(bankroll * max_position_pct, 2)
```

Both `FollowTheWhaleStrategy.evaluate()` (`strategy_engine.py:144`) and
`MarketNativeStrategy._evaluate_one()` (`market_strategy.py:179`) call this
with the same flat `strat_cfg["max_position_pct"]` regardless of how strong
the signal is. A whale print at `confidence=0.61` (barely above
`entry_threshold: 0.6`) and one at `confidence=0.95` get **identically
sized** positions — the entire 0-1 composite confidence score
(`whale_simulator.composite_confidence_breakdown`) that eight weighted
factors went into computing is thrown away the instant the entry decision
is made; it only ever gates whether a trade happens, never how big it is.
This isn't something either research doc flagged — Kelly-style edge-scaled
sizing isn't mentioned in `prediction-markets-research-reference.md` or the
alignment plan, so this is a fresh finding, not a previously-identified gap.
It's also not a deliberate tradeoff recorded anywhere; nothing in
`risk_manager.py`'s or `strategy_engine.py`'s comments discusses why sizing
is flat.

**Suggested approach.** A fractional-Kelly overlay, not literal full Kelly
(full Kelly needs a real win-probability and payout odds; `signal.confidence`
is a 0-1 heuristic score, not a calibrated probability yet — see Finding 4).
Concretely: treat `max_position_pct` as a **ceiling**, not a fixed size.
Add a `strategy.kelly_fraction_of_cap` config field (default e.g. `1.0` =
today's behavior unchanged) and scale the requested size by something like
`min(1.0, (confidence - entry_threshold) / (1.0 - entry_threshold))` —
a signal right at the threshold gets a small fraction of the cap, a
signal near 1.0 confidence gets the full cap. This is a few lines in
`strategy_engine.evaluate()` and `market_strategy._evaluate_one()` right
where `contracts = int(max_size / unit_cost)` is computed — `max_size`
becomes `max_size * scale_factor`. Keep `max_position_pct` as the hard
per-trade ceiling (already risk-audited) — this only ever shrinks a
position, never grows it past the existing cap.

**Effort/impact:** Moderate effort (touches the one line every position-size
decision in both strategies funnels through, needs new tests mirroring the
existing `max_position_pct` cap tests). High impact — this is the single
biggest place the app's own confidence machinery stops short of influencing
an actual trading decision.

---

## Finding 2 — No concentration/correlation risk across simultaneously-open positions

**Grounding.** `RiskManager.check_daily_loss()` (`risk_manager.py:82-92`) is
the only portfolio-level risk check in the app, and it looks at exactly one
number: today's aggregate equity loss. The only per-position gate is
ticker-level: `strategy_engine.evaluate()` at line 138 (`if signal.ticker in
self.broker.positions: return self._skip(...)`) and the matching check in
`market_strategy._evaluate_one()` (line 142). Nothing anywhere aggregates by
`signal_log.series_of(ticker)` or by `event_ticker` before opening a new
position. A market's own `mutually_exclusive` flag is already fetched and
used elsewhere in this app (`title_cache.py`, per the research reference
doc's §2.2 note) — so the codebase already has the concept of "these
tickers are the same real-world event," it just isn't consulted by either
strategy's entry path. Five simultaneously-open whale-follow positions
could all be different markets in the same tournament, different candidates
in the same race, or literally different outcomes of the same
mutually-exclusive event — each individually within `max_position_pct`,
collectively a much larger bet on one real-world outcome than the risk
config implies. This is a genuine accidental gap, not a documented
tradeoff — `risk_manager.py`'s own module docstring describes its job as
"is this trade within limits, and has the bankroll dropped past the daily
loss cap," with no mention of concentration ever being considered and
deferred.

**Suggested approach.** Add a `risk.max_open_positions_per_series` (and/or
`max_exposure_pct_per_series`, summing `cost_basis()` across
`self.broker.positions` filtered by `signal_log.series_of(ticker) ==
series_of(candidate_ticker)`) check in both strategies' entry paths,
alongside the existing per-ticker check. For the mutually-exclusive case
specifically, a cheaper first pass: if `market.get("event_ticker")` matches
an already-open position's event, apply a stricter combined-exposure cap
(these aren't just correlated, they're often *anti-correlated in a way that
caps total exposure automatically if structured right* — buying "yes" on
multiple mutually-exclusive outcomes of the same event is a real,
identifiable pattern worth a dedicated check rather than folding it into the
series-level one). Keep this a new, additive `risk.*` gate — no change to
`max_trade_size`'s existing math.

**Effort/impact:** Moderate effort (a new query against `self.broker.positions`
plus `signal_log.series_of`/event-ticker lookups already used elsewhere; no
new persistence needed). High impact — this is a real, currently-unguarded
tail risk specifically because prediction markets structurally produce
clusters of correlated tickers (multi-outcome events, tournament brackets,
election contract families) more than typical equities do.

---

## Finding 3 — Exit logic never consults the market analyst's own probability estimate; `analyst_lean()` is entry-only

**Grounding.** `market_analyst_agent.analyst_lean(ticker, max_age_sec)`
already exists, is already wired into **entry-side** confidence for both
strategies — `whale_simulator.composite_confidence_breakdown`'s
`analyst_factor` (populated by `kalshi_trade_tape._analyst_factor`,
`kalshi_trade_tape.py:103-117`) and `market_strategy._entry_confidence`
(`market_strategy.py:85-87`) — but neither strategy's exit path calls it at
all. `FollowTheWhaleStrategy._exit_confidence` (`strategy_engine.py:334-390`)
blends exactly three factors — `pnl`, `sentiment` (whale-lean reversal), and
`staleness` — with no fourth analyst-divergence term, and
`MarketNativeStrategy.check_exits` (`market_strategy.py:197-268`) has no
composite exit-confidence concept at all, just take-profit/stop-loss/
momentum-reversal. Concretely: a position opened "yes" on a whale print
could sit at a healthy unrealized gain while a *freshly re-analyzed*
market-analyst estimate now puts the true probability well below the
current market price and below 0.5 — a strong, reasoned signal to lock in
gains or cut the position early — and nothing in either exit path would
ever notice, because `analyst_lean()` is a single indexed SQLite read this
app already knows how to make cheaply (the whole reason it exists, per
`market_analyst_agent.py`'s module docstring: "inform the various engines...
without consuming AI tokens"). The alignment plan's Part 3.4 explicitly
scoped the analyst to "advisory-only... not wired into
`strategy_engine.evaluate()`'s actual trade decision" for the *entry* side
as a deliberate safety-first rollout choice — but exits are a lower-stakes
surface (closing a position early is more conservative than opening one on
LLM say-so) and the plan doc never actually discusses the exit side at all,
so this reads as an oversight rather than the same deliberate decision
extended to a second surface.

**Suggested approach.** Add an `analyst_divergence` factor to
`_exit_confidence` (`strategy_engine.py`), same "left out of the average
entirely when absent" idiom the other three factors already use: when a
fresh `analyst_lean()` exists and disagrees with the held side by more than
some margin (e.g., lean < 0.5 - margin for a held "yes"), score it toward 1.0
(pressure to exit); when it agrees, score toward 0.0. Mirror the same
addition into a new, equivalent composite for `MarketNativeStrategy`
(which currently has no composite exit score to extend — this would be the
first). Both are additive, opt-in via existing `auto_exit_enabled`-style
gating, so today's behavior is unchanged unless someone has both
`auto_exit_enabled: true` and a fresh analyst estimate on file for that
ticker — the overwhelmingly uncommon case already established in
`_analyst_factor`'s own docstring.

**Effort/impact:** Low-to-moderate effort — the function this extends
already exists and already has the "conditionally add a factor to the
average" pattern built in; this is the cheapest finding in this document to
implement. Medium-high impact — it closes a real asymmetry (the analyst
informs whether to get in, never whether to get out) using a data source
and calling convention that already exist end-to-end.

---

## Finding 4 — `confidence_calibration.py`'s calibration bands are computed but have no consumer anywhere

**Grounding.** `confidence_calibration.py`'s own module docstring is explicit
that this is intentional for v1: *"this ships strictly read-only/report-only
for v1... A human reads the report and edits the weights in code if/when the
finding is worth acting on. Building an apply path is a real follow-up, not
done here."* So the *absence* of an apply path for `_suggested_weights()` is
a documented, deliberate scope boundary, not an oversight. What's a genuine
gap, though, is `_confidence_calibration_bands()` (lines 147-168) — the
newer half of this module that buckets resolved signals by their *overall*
composite score and checks whether a "60-70% confidence" signal actually won
60-70% of the time. This is exactly the Clinton & Huang vs. Kalshi
calibration-vs.-hit-rate distinction the research reference doc's §1.4
surfaces as a live, contested methodological question — and once this
module is enabled (`confidence_calibration.enabled: false` today, gated at
`min_resolved_signals: 50`), its bands would be the single most direct
answer this app could give to "is a 0.75-confidence whale signal actually
worth more than a 0.65 one, in reality, not just by construction of the
formula." Right now that report has no destination: it isn't read by
`strategy_engine.evaluate()`'s threshold check, isn't read by
`advisory_engine.py`'s entry-threshold recommendation (which independently
re-derives its own tertile buckets from `trade_analytics` rows rather than
reusing this module's bands), and isn't surfaced anywhere a sizing decision
could use it.

**Suggested approach.** This is the natural second consumer of Finding 1's
sizing hook, once both exist: instead of (or in addition to) scaling
position size by raw `confidence - entry_threshold`, scale it by the
*calibration-adjusted* estimate — look up which `_CONFIDENCE_BANDS` bucket
the current signal's confidence falls into, and if that band's own
`observed_win_rate_pct` (from real resolved history) is available and
differs meaningfully from the band's naive midpoint, use the observed rate
instead of the raw score for sizing. This keeps `confidence_calibration.py`'s
existing "read-only report, human decides" posture for the *weights*
(unchanged, still v1-scoped) while giving the *bands* — which are already a
concrete, resolved-outcome-grounded number — one real use beyond a report
nobody's UI currently surfaces outside the (currently disabled) module
itself. Worth checking whether this module's report even has a UI panel yet
before building the consumer — if not, that's a smaller, useful prerequisite
step on its own.

**Effort/impact:** Low effort once Finding 1 lands (mostly a lookup against
an already-computed report). Medium impact — mostly gated by real sample
size (the module needs 50 resolved real signals with factor breakdowns
before it activates at all, and this app's real-signal volume has
historically been thin), so this is more "have the wiring ready" than
"expect it to fire soon."

---

## Finding 5 — Whale confidence has no adversarial/wash-trading detector; alternating opposite-side prints score as two independent whales, never as one suspicious pattern

**Grounding.** The research reference doc's §3.5 documents that Kalshi's own
enforcement toolkit for wash trading exists (CEA §4c(a)(1)-(2)(A), Reg.
1.38(a)) but that no case has actually used it yet, and §1.5 notes that a
manipulator and a genuinely informed large trader are *observationally
identical* in raw size — the whole reason this app's `agreement_factor` and
`cluster_factor` exist is to look past raw size toward corroborating
pattern. But both of those factors are **same-side only**:
`signal_log.recent_sides_for_ticker` (`signal_log.py:96-108`) and
`signal_log.cluster_factor` (`signal_log.py:111-139`) both filter `WHERE
ticker = ? AND side = ?` — they ask "do recent prints on this side agree
with this one," never "has this ticker seen unusual back-and-forth activity
across *both* sides recently." A pattern of alternating large yes/no prints
on the same ticker in a tight window — a plausible signature of a single
actor crossing their own orders to manufacture the appearance of "whale
interest," or of two colluding accounts — would currently score each print
independently, with `agreement_factor`/`cluster_factor` from the *opposing*
side's own recent prints contributing nothing (they're scoped to the same
side), and nothing else in `composite_confidence_breakdown`'s eight factors
would ever flag the alternation itself as suspicious. This is an accidental
gap: nothing in `kalshi_trade_tape.py` or `whale_simulator.py` discusses
opposite-side alternation as a case considered and consciously left out.

**Suggested approach.** Add a lightweight same-ticker, both-sides lookback
(reusing the existing `_AGREEMENT_LOOKBACK_SEC`/`_CLUSTER_LOOKBACK_SEC`
windows already defined in `kalshi_trade_tape.py`) that counts qualifying
whale prints on the *opposite* side of the same ticker in the same tight
window `cluster_factor` uses. When that count is unusually high relative to
same-side activity (e.g., roughly balanced yes/no whale-sized prints
alternating rather than one side dominating), treat it as a caution signal —
either a discount applied directly to the composite score, or a new ninth
factor following the existing "caution factor, not a block" precedent
`trend_factor` already established (Hanson's own result that manipulator
presence isn't unambiguously accuracy-destroying argues against a hard
block here too). Cheap to compute: it's the same `signal_log` table already
queried for `agreement_factor`/`cluster_factor`, just without the `side = ?`
filter.

**Effort/impact:** Moderate effort (one new query pattern plus a new factor
threaded through `composite_confidence_breakdown`'s signature, matching how
`cluster_factor`/`trend_factor`/`analyst_factor` were each added
incrementally before). Medium impact — this is a real, currently-invisible
blind spot, but its practical frequency on Kalshi's real trade tape is
unknown (no data exists yet on how often this pattern actually occurs on
this app's watchlist).

---

## Finding 6 — `confidence_calibration.py` is architecturally whale-signal-only; `market_strategy`'s independent confidence factors have no calibration tooling at all

**Grounding.** `confidence_calibration.py`'s `_FACTOR_NAMES`
(lines 30-33: `depth_factor, unusualness_factor, proximity_factor,
context_factor, agreement_factor, cluster_factor, trend_factor,
analyst_factor`) are hardcoded to exactly the eight fields of
`whale_simulator.ConfidenceBreakdown`, and its input
(`signal_log.resolved_signals_with_factors()`) reads `factors_json` off the
`signals` table — a column only ever populated by whale-signal providers
(`kalshi_trade_tape.py`). `market_strategy.py`'s own
`_entry_confidence()` (lines 63-90) computes a structurally different
factor set — `momentum`, `liquidity`, `spread`, optionally `analyst` — that
never gets logged anywhere calibration-shaped; `market_strategy`'s trades
only ever reach `trade_analytics`/`advisory_engine` as plain
entry_confidence-bucketed win rates (`advisory_engine._entry_threshold_recommendation`'s
tertile split), the same discrimination-only lens `confidence_calibration.py`
already went further than for the whale side. This matters because
`market_strategy.py`'s own module docstring frames its entire reason for
existing as producing "a real, whale-independent trade-history dataset...
for the advisory/ML groundwork to eventually learn from" — it's explicitly
meant to be a comparable, parallel dataset to the whale side, but one half
of that pair got a real calibration module and the other didn't. Deliberate
in the narrow sense that nobody claimed otherwise, but there's no comment
anywhere noting this asymmetry was a conscious choice rather than
`confidence_calibration.py` simply having been built before `market_strategy`
had enough of its own resolved history to justify the parallel effort.

**Suggested approach.** Either (a) generalize
`confidence_calibration.py` to accept a factor-name list and a row-source
function as parameters instead of hardcoding both to the whale shape, then
call it twice (once per strategy) — the cleaner long-term fix, since the
bucket/discrimination/calibration-band math itself (`_bucket_win_rates`,
`_confidence_calibration_bands`) has nothing whale-specific about it once
the factor names are parameterized; or (b) a smaller first step, add a
`market_strategy_signals`-equivalent factors column (or reuse
`trade_analytics`' existing per-trade rows, which already carry
`entry_confidence` and could carry a `factors_json`-shaped breakdown of
momentum/liquidity/spread/analyst the same way `WhaleSignal.factors` does)
and a parallel, much smaller calibration report scoped to
`market_strategy`'s own trade history. Either path needs `market_strategy`'s
`_evaluate_one()` to persist its factor breakdown per trade first (it
currently only stringifies it into `reason`'s bracketed suffix for display,
per the comment about `trade_analytics._ENTRY_CONF_RE` at
`market_strategy.py:185-193`) — that's the real prerequisite, not the
calibration math itself.

**Effort/impact:** Moderate-to-high effort (needs new persistence for
market_strategy's factor breakdown before any calibration math can run
against it, plus the generalization work on `confidence_calibration.py`
itself). Medium impact — real value is gated on `market_strategy` having
accumulated enough resolved trades to be worth calibrating in the first
place, same sample-size caveat as Finding 4.

---

## Finding 7 — No time-of-day / liquidity-regime awareness in either strategy's entry gating

**Grounding.** `market_strategy.py`'s liquidity filters
(`min_volume_24h`, `max_spread` — `config/settings.yaml` lines 56-57) are
static thresholds checked against whatever the market's current snapshot
happens to be at the instant a tick runs; nothing distinguishes a market
observed during a globally quiet window (e.g., overnight for a
US-centric political/economic market, where real spreads and depth are
typically worse) from the same market observed during active trading
hours. `Glosten & Milgrom (1985, JFE)` — cited in the research reference
doc's §1.5 — grounds spread itself as adverse-selection compensation that
should plausibly widen when fewer real participants are present to compete
it down; this app's `max_spread` gate correctly filters on the *symptom*
(a wide spread) at the moment of evaluation, but has no *regime* concept
that would, for instance, apply a stricter threshold or lower confidence
weight during historically thin hours, or flag a trade that only barely
cleared `max_spread`/`min_volume_24h` as "borderline liquidity" for the
purposes of position sizing. `whale_simulator.py`'s `context_factor`
(percentile rank of a market's volume among the current batch) is the
closest thing to a regime-aware signal in the codebase, and it's relative
to the current tick's batch only, not to that market's own typical
time-of-day pattern (`market_history.py` already logs enough real snapshot
history over time to compute this, per its use elsewhere for
`momentum()`/`seconds_to_close()`).

**Suggested approach.** Lowest-effort version: an hour-of-day (UTC or
market-category-appropriate) multiplier on `market_strategy`'s
`entry_confidence_threshold` and/or `whale_simulator`'s `context_factor`,
derived empirically from `market_history.py`'s own accumulated snapshots
(e.g., bucket historical volume/spread by hour-of-day per series, the same
tertile-bucket idiom `confidence_calibration.py` already uses elsewhere) —
no new data source needed, this reuses history already being persisted.
Given this needs real accumulated `market_history` data to derive anything
meaningful from, this is more of a "build the bucketing utility now, let it
accumulate signal" item than something with an immediate payoff.

**Effort/impact:** Low-to-moderate effort, low-to-medium impact, and
explicitly gated on having enough `market_history.py` data accumulated to
derive real hour-of-day patterns from — the lowest-priority finding in this
document for that reason, included because it's a real, concrete, currently
completely-unaddressed gap rather than because it's urgent.

---

## Recommended sequencing

Matching this project's own `ROADMAP.md` style — a short, concrete
punch-list, not a narrative. If picked up as a future work session, roughly
this order:

- [ ] **Finding 3 (exit-side analyst signal)** — cheapest item here by a
      wide margin; `analyst_lean()` and the "add a factor to the average
      when present" pattern both already exist verbatim in
      `_exit_confidence`. Good first PR to validate the approach before
      touching sizing.
- [ ] **Finding 2 (concentration/correlation risk)** — highest safety
      payoff per unit of effort; this is the kind of gap that matters most
      exactly when it's never been tested (a real correlated-loss event),
      which argues for closing it before position sizing changes (Finding 1)
      make individual positions larger.
- [ ] **Finding 1 (edge-aware position sizing)** — the biggest single
      change in this document and the one with the most compounding value
      (Finding 4 becomes cheap once this lands), but do it after Finding 2
      so bigger, better-sized positions aren't also uncapped on
      concentration.
- [ ] **Finding 5 (wash-trading/alternating-side detector)** — moderate
      effort, real but currently-unquantified payoff; worth doing once
      Finding 1/2 are in and stable, since it's an independent addition to
      the same scoring function Finding 1 doesn't touch.
- [ ] **Finding 4 (calibration-band feedback into sizing)** — natural
      follow-on to Finding 1, but only worth doing once
      `confidence_calibration.enabled: true` has real bands to feed (needs
      50 resolved real signals — check `signal_log`'s current count before
      prioritizing this over Finding 6).

Findings 6 and 7 are real but explicitly lower-priority in this doc's own
ranking — both are gated on accumulating more resolved history
(`market_strategy` trades, `market_history` snapshots) before the payoff is
realized, so they're better candidates for "start the persistence/plumbing
now, revisit the analysis later" than for an immediate full build-out.
