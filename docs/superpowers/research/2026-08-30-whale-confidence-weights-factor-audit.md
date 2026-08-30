# `whale_confidence_weights` — Factor-Set Audit (Stage 1: investigate/brainstorm)

**Task:** Stage 1 of the 9-stage delegated pipeline on "is the 9-factor
`whale_confidence_weights` set actually right." Produces findings and rough
directions only — no design, no code changes.

**Scope.** The confidence-scoring formula itself: soundness of each of the 9
current factors, and what is missing. **Out of scope** (owned by the parallel
`docs/kalshi-category-data-audit` pipeline): per-category / per-series
granularity mechanisms, and category-specific Kalshi data shapes.

**Method.** (a) Full read of `services/confidence_scoring.py` and every caller
that computes a factor; (b) an independently re-run live report
(`GET /api/confidence-calibration/report`); (c) read-only probes against the
live `data/signal_log.db` (95,355 resolved, non-excluded, factor-carrying
signals) and `data/series_watcher.db` (33.1M captured raw trades) — opened
`file:…?mode=ro` throughout, never written; (d) `docs/kalshi/` as ground truth
for every Kalshi field.

**Independent verification of the restated inputs.** The live report re-run here
returned `resolved_count: 95351` (vs. 95,101 in the brief — the table is still
growing) and reproduced every gap to within 0.1 pt: depth −28.6, unusualness
−18.1, proximity −20.3, context +24.9, agreement +12.0, cluster +14.1, trend
+13.3, analyst/block `null`. The brief's numbers are sound; **the
interpretation of them is what this audit overturns.**

---

## 0. The two findings that reframe everything below

### 0.1 The outcome label is the market's own price, restated

`correct` is set as `result == side` — `services/signal_log.py:330`
(`resolve_from_market_results`) and `:294–299` (`mark_resolved`). It is
**directional accuracy**, not profit, not edge.

Binned by traded-side unit cost over the 62,584 resolved signals that carry a
price (rows predating the `price` column are null — `signal_log.py:131`):

| unit cost | n | observed win rate | price implies |
|---|---|---|---|
| 0.0–0.1 | 8,237 | 4.6% | ~5% |
| 0.1–0.2 | 4,712 | 14.3% | ~15% |
| 0.2–0.3 | 4,168 | 27.2% | ~25% |
| 0.3–0.4 | 4,964 | 35.0% | ~35% |
| 0.4–0.5 | 6,660 | 44.7% | ~45% |
| 0.5–0.6 | 7,668 | 55.2% | ~55% |
| 0.6–0.7 | 5,312 | 65.2% | ~65% |
| 0.7–0.8 | 4,878 | 72.0% | ~75% |
| 0.8–0.9 | 5,940 | 83.2% | ~85% |
| 0.9–1.0 | 10,045 | 95.0% | ~95% |

The market is **near-perfectly calibrated** across all ten deciles. So
`correct` is almost entirely explained by the price the whale paid, and any
factor correlated with price will "discriminate" — while predicting nothing
the price did not already say. Because EV per contract is exactly
`p − unit_cost − fee` (`services/kalshi_fees.py:368–387`), a factor that steers
the score toward expensive favourites is steering toward **zero or negative
EV** — the 0.60–0.95 negative-EV band already recorded as an open gap
(`ROADMAP.md:276`).

**This makes `_MIN_DISCRIMINATION_GAP` (`confidence_calibration.py:59`) an
objective that rewards the wrong thing.** It is the root cause behind several
findings below, not one more finding among them.

### 0.2 The composite score has essentially no skill over the price

Brier scores over the same 62,584 priced rows:

| predictor | Brier | note |
|---|---|---|
| the market's own unit cost | **0.1580** | the benchmark |
| `composite_confidence` score | 0.2454 | the 9-factor formula |
| constant 0.596 base rate | 0.2553 | knows nothing |

The formula is barely better than a constant and **far worse than simply
reading the price**; `corr(unit_cost, score) = 0.264`. Verdict: the score is
*calibrated but low-resolution* — the 50–90% bands are accurate to within
0.4–2.1 pts (§5) — it just carries almost no information. Nothing in the
current factor set currently justifies overriding the market's own estimate.

---

## 1. Per-factor findings

### 1.1 `depth_factor` — weight 0.0404 (floored), reported gap −28.6

Computation: `size / max(volume_24h, 1.0)`, exponentially saturated
(`services/confidence_scoring.py:194–195`).

| check | evidence | verdict |
|---|---|---|
| Units | `volume_24h_fp` is `FixedPointCount`, "24h market volume **in contracts**" (`docs/kalshi/get-market.md:234–236`); `size` is contracts. | **Sound** — no dimensional bug. |
| Denominator collapse | `max(market_volume, 1.0)` turns a 0-volume market into a **1-contract** denominator. 22,833 of 95,355 rows (23.9%) score `depth_factor` = exactly 1.0; **22,413 of those (98.2%) have `raw_volume_24h == 0`.** | **BUG (real, load-bearing).** |
| Why volume is 0 | `services/market_catalog/market_catalog.py:442–446`: a KXBTC15M market's entire tradeable lifetime is 15 min, so `volume_24h_fp = 0.0` for **every still-open instance** — established by a 2026-08-16 root-cause investigation, not inferred here. Structural for any series whose lifecycle is under 24h. | Confirmed mechanism. |
| Effect on the gap | The saturated set wins 40.1% vs 65.8% for the rest, and is 60% `KXMVECROSSCATEGORY` + 36% `KXBTC15M`. Restricting to `raw_volume_24h > 0` (n=72,942) the gap shrinks from **−28.6 to −17.4**. | Artifact inflates it; does not wholly create it. |

**Verdict:** computation is **defective, not merely inversely predictive.** For
~24% of the population the factor is a constant 1.0 that encodes "this market
reports no 24h volume" — i.e. a *series identity*, not trade size relative to
depth. The reported −28.6 is ~40% artifact. Residual −17.4 is itself
price-confounded (§0.1: low bucket mean unit cost 0.636 vs high 0.437).

**Recommendation: investigate-further, do not invert.** Inverting would reward
prints in high-volume markets bought at expensive prices. Fix the
missing-volume case first (a missing denominator must be *absent*, never a
fabricated 1.0 — the exact failure class `_price_dollars` was written for,
`kalshi_trade_tape.py:114–137`), then re-measure.

### 1.2 `unusualness_factor` — weight 0.0404 (floored), reported gap −18.1

Computation: `1 − |unit_cost − 0.5| × 2` (`confidence_scoring.py:203–204`).

This is a **deterministic, non-monotone function of the traded-side price**,
and the label is approximately the traded-side price (§0.1). Splitting the low
bucket by which tail it came from:

| bucket | n | win rate | mean \|uc−0.5\| | uc>0.5 (favourite) | uc≤0.5 (longshot) |
|---|---|---|---|---|---|
| low (price far from 0.5) | 20,861 | 53.5% | 0.441 | n=11,338, **94.2%** | n=9,523, **5.1%** |
| high (price near 0.5) | 20,861 | 50.7% | 0.072 | n=10,385, 58.4% | n=10,476, 43.0% |

**Verdict:** the −18.1 gap is a **mathematical identity, not a finding.** The
low bucket mixes 94.2%-winners and 5.1%-winners; its 53.5% aggregate is purely
the mix ratio. Because the factor is symmetric about 0.5 while the outcome is
monotone in price, **the tertile test is structurally incapable of evaluating
this factor** — its gap is uninterpretable in either direction. (Within
`volume > 0` the gap moves to −29.5, tracking the mix, not any real effect.)

This also retires the 2026-08-10 finding recorded in
`confidence_scoring.py:151–157`: unusualness's "NEGATIVE discrimination against
~9200 real resolved signals" was the same identity at smaller n.

**Recommendation: remove as a confidence factor** (never invert — inverting
means "prefer near-certain contracts", i.e. buy the negative-EV band on
purpose). The *price* belongs in the pipeline as an EV/edge term, which is what
`config_bounds.is_tradeable_unit_cost` and the unit-cost band already do
(`kalshi_trade_tape.py:684`, `config/config_bounds.py:77–87`) — not as a
0–1 confidence score blended in with a weight.

### 1.3 `proximity_factor` — weight 0.0404 (floored), reported gap −20.3

Computation: 0.0 unless within 48h of `close_time`, else ramps to 1.0
(`confidence_scoring.py:209–218`).

| check | evidence | verdict |
|---|---|---|
| Value mass | **73.6% of all rows are exactly 0.0** (70,151 / 95,355). 19.8% are ≥0.99. | Near-binary, not continuous. |
| Tertile validity | Both tertile boundaries land on the same value: `low_edge = high_edge = 0.0`. | **The split is invalid** — see §2. |
| Series proxy | The ≥0.99 group is **95.7% `KXBTC15M`** (18,055 / 18,872) — 15-minute markets are inside a 48h window for their entire life. | It measures series identity. |
| Honest comparison | within-48h 56.9% (n=25,204) vs. not-within 60.6% (n=70,151) = **−3.7 pts**, not −20.3. | Effect ~5× overstated. |

**Verdict:** the reported −20.3 is **not a real measurement.** With 73.6% of
rows tied at 0.0, the "low" tertile is an arbitrary arrival-ordered subset of
the tied mass; within that mass the first third wins 71.5% and the last third
40.1% — so the split is reading **calendar time**, not proximity.

**Recommendation: investigate-further; the computation is defensible but the
encoding is wrong.** A fixed 48h window cannot serve both a 15-minute crypto
market and a 2027 mayoral market. Time-to-close normalised to the market's own
lifetime is the more general metric, and `market_history.seconds_to_close`
already exists (`services/market_history.py:95`).

### 1.4 `context_factor` — weight 0.2121, reported gap +24.9 ("strongest positive")

Computation: percentile rank of this market's `volume_24h` among the batch
(`confidence_scoring.py:230–234`).

**It reads the same field `depth_factor` does, and inherits the same artifact
with the opposite sign:**

| population | n | win rate | mean `depth_factor` | mean `context_factor` |
|---|---|---|---|---|
| `raw_volume_24h == 0` | 22,413 | 40.0% | **1.000** | **0.094** |
| `raw_volume_24h > 0` | 72,942 | 65.6% | 0.095 | 0.785 |

`depth_factor`'s high tertile and `context_factor`'s low tertile are the *same
rows*: `KXMVECROSSCATEGORY` (13,817 / 13,739) + `KXBTC15M` (9,303 / 10,434) —
~74% of one and ~76% of the other. Restricted to `volume > 0` the gap falls
from **+24.9 to +11.0**.

**Verdict:** computation is sound (rank is genuinely robust, as its comment
claims), but **its headline discrimination is over half the zero-volume
artifact read backwards** — and it is currently the second-largest weight, with
`suggested_weights` proposing to raise it to **0.34**. Acting on that suggestion
would harden the artifact into the production formula.

**Recommendation: keep, but reweight only after the zero-volume case is fixed.**
Do not adopt the suggested 0.34 as-is. Residual +11.0 is real and price-
confounded (low bucket mean uc 0.433 vs high 0.618).

### 1.5 `agreement_factor` — weight 0.1818, reported gap +12.0

Computed by the caller: share of same-side prints on this exact ticker in the
last 6h (`kalshi_trade_tape.py:696–700`, `_AGREEMENT_LOOKBACK_SEC` at `:88`).

Tertile boundaries are distinct (`low_edge` 0.5, `high_edge` 0.841), so the
split is valid, though 25.5% of rows sit at the neutral 0.5 default. Holds up
within `volume > 0`: **+11.7** (vs +12.0 overall) — the only factor whose gap is
essentially unchanged by the artifact control.

**Verdict: sound computation, real and artifact-robust discrimination.** The
2026-08-10 finding that floored it for negative discrimination
(`confidence_scoring.py:151–157`) has genuinely reversed at 10× the sample
size. **Recommendation: keep; it is currently under-weighted relative to its
evidence.**

### 1.6 `cluster_factor` — weight 0.1717, reported gap +14.1

Computed by `signal_log.cluster_factor` (`services/signal_log.py:229–257`):
`min(matches/3, 1.0)` — so it takes **only 4 distinct values**.

| value | n | share | win rate |
|---|---|---|---|
| 0.0 | 31,089 | 32.6% | 48.0% |
| 0.333 | 9,574 | 10.0% | 58.9% |
| 0.667 | 6,331 | 6.6% | 60.4% |
| 1.0 | 48,361 | 50.7% | **67.1%** |

**Cleanly monotone across all four levels** — the only factor in the set that
is. Tertile boundaries are distinct. Weakens under the artifact control
(+14.1 → **+4.4** within `volume > 0`), so part of its strength is the same
confound.

**Verdict: sound computation, genuine monotone signal, partly confounded
magnitude.** **Recommendation: keep.** The cap at 3 matches is coarse — half
the population saturates at 1.0, discarding the difference between 3 and 30
prints. Raising or removing the cap is a cheap resolution win for Stage 3.

### 1.7 `trend_factor` — weight 0.3131 (**largest**), reported gap +13.3

Computed by `_trend_factor` (`kalshi_trade_tape.py:177–194`) from
`market_history.momentum()` over a 1800s window (`:44`), defaulting to 0.5 when
momentum returns `None`.

| value | n | share | win rate |
|---|---|---|---|
| < 0.5 (fights trend) | 9,402 | 9.9% | 53.8% |
| **= 0.5 (no data / flat)** | **61,186** | **64.2%** | 51.6% |
| > 0.5 (with trend) | 24,767 | 26.0% | **81.7%** |

Two separate defects:

1. **The tertile test is invalid here.** `low_edge = high_edge = 0.5` — both
   boundaries sit inside the 64.2% tied mass. Within that mass, the
   arrival-order first third wins **64.9%** and the last third **45.0%**. The
   reported +13.3 is substantially a reading of *when the row was written*, not
   of trend. The real effect is far larger and differently shaped: with-trend
   81.7% vs neutral 51.6% ≈ **+30 pts**.
2. **The largest weight in the formula is a constant for two-thirds of every
   signal it scores.** `momentum()` returns `None` without ≥2 snapshots
   covering the window (`market_history.py:194–217`), and `market_history`
   snapshots are watchlist-scoped while the whale provider now sees
   exchange-wide flow. This is a **data-completeness defect** under CLAUDE.md's
   data-plane HARD RULE, surfacing as a scoring problem.

**Verdict: computation is sound; its measurement and its input coverage are
both broken.** **Recommendation: keep the factor, fix its coverage, and
re-measure with a method that does not split ties** — it is plausibly the
strongest real factor in the set and is currently being credited for the wrong
reason.

### 1.8 `analyst_factor` — weight 0.0, no data

`_analyst_factor` (`kalshi_trade_tape.py:197–214`) reads
`market_analyst_agent.analyst_lean()` (`per_market.py:168–194`), which returns
`None` with no fresh analysis. **All 95,355 rows carry exactly 0.5 — 1 distinct
value, 100% of the mass.** The calibration's `< _BUCKET_COUNT` distinct-value
guard (`confidence_calibration.py:104–106`) correctly returns `{}`.

**Verdict: correctly wired, structurally never populated** — it fires only when
a human manually spends an LLM call on that exact market within 24h, which has
never happened at scale. **Recommendation: keep at weight 0; not a factor-set
problem.** Any Stage 3 proposal to use it must first answer how analyses get
produced for markets that print whales.

### 1.9 `block_trade_factor` — weight 0.0, no data

Wiring is correct end to end: `docs/kalshi/public-trades.md:212` ("True if the
trade was matched off book as a block trade") → `services/kalshi/contracts/
trade.py:93` → `kalshi_trade_tape.py:729`. All 63,678 rows that carry the key
are 0.0.

**Newly established here:** a read-only sample of the **last 2,000,000 of
33,119,829 captured raw trades** in `data/series_watcher.db` returns
`is_block_trade = 0` for **every single row**. Block trades are matched *off
book* and simply do not appear on the public trade flow this app consumes.

**Verdict: a dead factor, not an untested one.** It cannot ever discriminate on
this data source. **Recommendation: keep at weight 0 and record why**, so a
future session does not re-plan it as "untested, needs data". If block-trade
data is wanted, `GET /trades` accepts `is_block_trade` as a filter
(`docs/kalshi/CHEATSHEET.md:487`) — a different ingestion path, not a scoring
change.

---

## 2. A defect in the measurement instrument itself

`_bucket_win_rates` (`confidence_calibration.py:67–116`) splits by **index-based
tertiles** and guards only against `len(distinct_values) < 3`. Its own docstring
names the hazard ("index-based tertiles on a tied value would just reflect
whatever order the rows happened to arrive in") — **but the guard only catches
the fully-degenerate case.** A factor with thousands of distinct values but a
large tied mode defeats it.

The correct test is whether the low bucket's top value equals the high bucket's
bottom value:

| factor | low_edge | high_edge | valid? |
|---|---|---|---|
| `proximity_factor` | 0.0 | 0.0 | **INVALID** |
| `trend_factor` | 0.5 | 0.5 | **INVALID** |
| `cluster_factor` | 0.333 | 1.0 | valid |
| `agreement_factor` | 0.5 | 0.841 | valid |
| `depth_factor` | 0.019 | 0.196 | valid |
| `context_factor` | 0.5 | 0.899 | valid |
| `unusualness_factor` | 0.22 | 0.68 | valid (but non-monotone, §1.2) |

Two of the nine reported gaps — including the **largest-weighted factor** — are
comparing identical factor values against each other, ordered by insertion.
Because win rate drifted from ~71% to ~40% over the accumulation period, that
ordering is a strong time signal, so the artifact is large, not incidental.

Compounding it: `resolved_signals_with_factors()` has **no date scoping at
all** by design (`signal_log.py:606–609`), so the report blends a 71%-win-rate
era with a 40%-win-rate era with no time dimension anywhere.

**Disposition: real bug, CI guard.** A tie-aware split (or rank-with-ties /
value-based binning) plus a `low_edge == high_edge` assertion would have caught
both. This is upstream of every weight decision the app makes, and
`auto_apply_enabled` would act on it (currently `false` —
`config/settings.yaml:153`).

---

## 3. The calibration bands are partly a band-width artifact

| band | n | observed | midpoint pred (shipped) | gap | **actual mean score** | honest gap |
|---|---|---|---|---|---|---|
| <50% | 43,934 | 53.8% | 25.0% | +28.8 | **36.6%** | **+17.2** |
| 50–60% | 19,798 | 56.9% | 55.0% | +1.9 | 54.8% | +2.1 |
| 60–70% | 17,097 | 62.2% | 65.0% | −2.8 | 64.0% | −1.8 |
| 70–80% | 7,517 | 72.3% | 75.0% | −2.7 | 74.1% | −1.8 |
| 80–90% | 6,963 | 84.1% | 85.0% | −0.9 | 84.5% | −0.4 |
| 90–100% | 64 | 67.2% | 95.0% | −27.8 | 90.1% | −22.9 |

`_confidence_calibration_bands` uses the band **midpoint** as the prediction
(`confidence_calibration.py:219`). The `<50%` band is 50 points wide and holds
46% of the population at an actual mean of 0.366 — so **40% of the headline
"+28.8 under-confidence" is the band being too wide**, not the model being
wrong.

The `90–100%` band is worse than thin: the **maximum score ever achieved across
95,373 signals is 0.91**, and only 64 rows reach ≥0.90 (60 at exactly 0.90, 4 at
0.91). It is a ceiling-crush bucket, not a confidence band.

**Verdict:** the middle bands (50–90%, 51,375 signals) are **well calibrated**,
within 0.4–2.1 pts. Read together with §0.2, the diagnosis is precise: the score
is *well-calibrated but low-resolution* — honest about its own uncertainty, and
carrying almost no information beyond the base rate.

---

## 4. Candidate factors not in the formula

Ranked by strength of evidence. Every "gap" below is measured against the same
price-confounded label (§0.1) and inherits that caveat — they are leads, not
verdicts.

| # | Candidate | Where the raw data already lives | Evidence | Caveat |
|---|---|---|---|---|
| N1 | **Order-book depth / liquidity at the touch** — what `depth_factor` *claims* to measure | `data/series_watcher.db` `book_snapshots`: 205,330 rows with `yes_bid_dollars`, `yes_ask_dollars`, `yes_bid_size_fp`, `yes_ask_size_fp`, `open_interest_fp`, `dollar_volume` (`services/series_watcher.py:185–205`, writer `record_book` at `:303`) | Directly measures resting size a print consumed — immune to the 24h-volume zero that breaks `depth_factor` (§1.1) | Watched-series-scoped, not exchange-wide; needs a join by (ticker, observed_at) |
| N2 | **Bid/ask spread** | **Already logged per signal** — `signals.raw_spread`, populated on all 95,355 rows, >0 on 40,609 (`signal_log.py:99`, captured `kalshi_trade_tape.py:746–757`) | Tertile gap **−15.7** (tight 71.1% vs wide 55.4%) | Gap 8 raw data captured for exactly this purpose and never used; spread proxies both liquidity and price extremity |
| N3 | **Open interest, and size relative to it** | `market.open_interest_fp` — `FixedPointCount`, "contracts bought on this market disconsidering netting" (`docs/kalshi/get-market.md`), present in the same market dict already passed into `composite_confidence_breakdown` | Not yet measured; the denominator `depth_factor` should probably have used — OI is a stock, not a 24h flow, so it does **not** go to zero on short-lived markets | The general form of N1/§1.1; check before proposing the narrow version |
| N4 | **Realized volatility** | `market_history.volatility()` (`market_history.py:223`) — computed and consumed by `exit_engine`, **never by confidence scoring** | Siloed analytic; regime context the score is blind to | Same watchlist-coverage limit as `momentum()` (§1.7) |
| N5 | **Model-vs-market edge (`projected_probability` − price)** | `services/settlement_edge.py:191` `projected_probability()`, with a measured projection Brier of 0.0171 vs market 0.1046 (`config/settings.yaml:171–174`) | The one place in this app that **already beats the market's own Brier** — the exact skill the confidence score lacks (§0.2) | Settlement-window-scoped (crypto index families), not universal |
| N6 | **Time-to-close normalised to the market's own lifetime** | `market_history.seconds_to_close()` (`market_history.py:95`) + `close_time` already on `WhaleSignal` (`confidence_scoring.py:64`) | The general form of `proximity_factor`, without the 48h series proxy (§1.3) | — |
| N7 | **Rejected-candidate population context** | `data/candidate_log.db` (2.7 GB) `rejection_events` — one row per rejection with per-row `unit_cost` and `side` (`services/candidate_log.py:31–51`) | The denominator the score never sees: how unusual *this* print is against everything rejected on the same market | Population statistics, not a per-signal factor — likelier a normaliser than a factor |

**Explicitly weak candidates, checked and rejected as leads:**

- **Raw notional USD** — tertile gap +26.8, the largest in the table. But
  `notional = size × unit_cost`, so it is mostly a price restatement (§0.1);
  it would steer straight into the negative-EV band. Rejected.
- **Raw contract size** — tertile gap −24.8, but size is inversely coupled to
  price (more contracts per dollar at low prices). This is the structural bias
  the contract-count gate was adopted to fix
  (`config/settings.yaml:108–114`). Rejected.

**Also noted (not a factor question):** `services/whale_calibration/README.md`
already records that `correct` is graded at `determined`, not `finalized`, and
that a `determined` result may be disputed. That caveat sits underneath every
number in this document.

---

## 5. Rough directions for Stage 3 (design)

Ordered by expected value. All three are directions, not designs.

### D1 — Fix the measurement instrument before touching any weight

Nothing downstream can be trusted while §2 stands. A tie-aware bucketing method
(rank-with-ties, or value-based bins with a `low_edge != high_edge` assertion),
a hard failure when a split lands inside one tied value, and a time dimension
on `resolved_signals_with_factors()` so a 71%-era and a 40%-era are not silently
averaged. This is a prerequisite, cheap, and independently valuable: it also
protects `auto_apply_enabled` from acting on an artifact.

Bundled here because it is the same class of defect: `max(market_volume, 1.0)`
(`confidence_scoring.py:194`) must stop fabricating a denominator. A market with
no reported 24h volume has *unknown* depth — the honest encoding is absence,
the idiom `_price_dollars` already established for exactly this failure
(`kalshi_trade_tape.py:114–137`).

### D2 — Change the objective, not just the factors

The deepest finding is §0.1/§0.2: the pipeline optimises **directional
accuracy**, which the market already prices correctly, so the score's ceiling is
"restate the price" and its floor is "buy negative EV confidently." Directions
worth designing against:

- Score factors against **edge** (`p − unit_cost − fee`, the definition already
  in `kalshi_fees.breakeven_unit_cost`) or realized P&L, not `correct`.
- Report **Brier skill relative to the market price** as the headline metric,
  not a low-vs-high win-rate gap. The repo already has Brier machinery in
  `settlement_edge.py`, `index_feed/settlement_algebra.py` and
  `market_analyst_agent/per_market.py` — reuse, do not re-derive.
- Under that objective, a factor that merely tracks price scores **zero**
  automatically, which retires `unusualness_factor` (§1.2) on principle rather
  than by hand-flooring it.

### D3 — Replace the volume-derived pair with a real liquidity measure

`depth_factor` and `context_factor` currently read one field (`volume_24h_fp`)
in two directions and split a zero-volume artifact between them (§1.1/§1.4).
Candidates N1 (book depth at the touch) and N3 (open interest) both measure
what `depth_factor` claims to and neither collapses on sub-24h markets. N3 is
the more general and cheaper option — `open_interest_fp` is already in the same
market dict the function receives, requiring no new join or API call — and per
the brief's own ground rule it should be evaluated **before** the narrower N1.
N2 (spread) is the cheapest of all: already logged on every one of the 95,355
rows, and never read.

---

## 6. Disposition summary (investigation-to-guard)

| Finding | Class | Disposition |
|---|---|---|
| Tertile split lands inside a tied value (§2) — `proximity_factor`, `trend_factor` | Real bug in the measurement instrument | CI guard + fix; blocks every weight decision |
| `max(volume, 1.0)` fabricates a denominator (§1.1) | Real bug, 23.9% of population | Fix in `confidence_scoring.py`; runtime diagnostic for zero-volume share |
| `unusualness_factor` is a price identity (§1.2) | Design defect, not a bug | Remove from the factor set under D2 |
| `context_factor`'s +24.9 is >half artifact (§1.4) | Misleading metric | Do **not** apply `suggested_weights` 0.34 until D1 lands |
| `trend_factor` neutral for 64.2% of signals (§1.7) | Data-completeness defect (data-plane rule) | Coverage fix; the largest weight rides on it |
| `block_trade_factor` structurally dead (§1.9) | Confirmed fact | Record it, so it is not re-planned as "untested" |
| Calibration band midpoints (§3) | Methodology artifact | Use the band's actual mean score as its prediction |
| `correct` graded at `determined`, not `finalized` | Pre-existing, already documented | `services/whale_calibration/README.md` — carried forward, not re-opened |

## 7. What would falsify the central claims

1. **§0.1** would fail if the win-rate-by-unit-cost table were flat rather than
   tracking price — re-runnable from `signal_log.db` at any time.
2. **§1.1/§1.4** would fail if the zero-volume rows were not concentrated in
   short-lifecycle series, or if the gaps did not move when controlling for
   `raw_volume_24h > 0`. Both were measured, both moved.
3. **§1.9** rests on a 2M-row sample of 33.1M, not the full table; a block trade
   in the unsampled 31M would weaken "structurally dead" to "vanishingly rare"
   — it would not change the recommendation.
4. **§2** is arithmetic on the shipped code path and is not probabilistic.
5. Every §4 candidate's gap shares the §0.1 price confound and must be
   re-measured under a D2 objective before any of them is adopted.
