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

> **Revision, 2026-08-30 (post-Stage-2).** An independent review
> (`…-factor-audit-review.md`) reproduced §0–§3 to the decimal and confirmed the
> core diagnosis, but found real defects in this document's *forward-looking*
> half. Every corrected number below was re-derived a third time against the
> live DBs at `signals` n = **95,526** (the table grows continuously; earlier
> counts in this document are the Stage 1 snapshot and are left as written where
> the conclusion is unaffected). The corrections are marked **[REVISED]** and
> are: §1.5 `agreement_factor` is not clean; §1.10 (new) `raw_spread` is a
> defect, not a candidate; §2's validity predicate is the wrong test; §4's N2
> withdrawn and N3 repriced; §4.1 (new) the dual accuracy/edge directive; §5
> re-ranked. Everything else stands as Stage 1 wrote it.

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
EV** — the negative-EV band already recorded as an open gap (`ROADMAP.md:276`,
which still quotes 0.60–0.95; the band actually enforced is `min_unit_cost: 0.5`
/ `max_unit_cost: 0.9`, and §4.1 measures the loss inside it at
**−$0.0150/contract**).

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

**Recommendation [REVISED]: remove from the *accuracy* score, and move it to
the edge score — do not delete it.** Never invert it either way (inverting
means "prefer near-certain contracts", i.e. buy the negative-EV band on
purpose). The identity argument above is unaffected and still retires it as an
*accuracy* factor. But under the dual objective of §4.1 it turns out to be the
**largest credible positive edge term in the set** (+$0.0151/contract in-band),
which is mechanically the same fact read the other way: price near 0.5 is
exactly where the contracts are cheap. So the *price* does belong in the
pipeline as an EV/edge term — which is what
`config_bounds.is_tradeable_unit_cost` and the unit-cost band already do
(`kalshi_trade_tape.py:684`, `config/config_bounds.py:77–87`) — and §4.1 is
where it lands, rather than being dropped or blended in with a weight.

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

### 1.4 `context_factor` — weight 0.2121, reported gap +24.9 ("strongest positive") **[REVISED — evidence upgraded]**

Computation: percentile rank of this market's `volume_24h` among the batch
(`confidence_scoring.py:230–234`).

**It reads the same field `depth_factor` does, and inherits the same artifact
with the opposite sign:**

| population | n | win rate | mean `depth_factor` | mean `context_factor` |
|---|---|---|---|---|
| `raw_volume_24h == 0` | 22,413 | 40.0% | **1.000** | **0.094** |
| `raw_volume_24h > 0` | 72,942 | 65.6% | 0.095 | 0.785 |

`depth_factor`'s high tertile and `context_factor`'s low tertile are largely the
*same rows*. **[REVISED]** Stage 1 argued this from series composition
(`KXMVECROSSCATEGORY` + `KXBTC15M` making up ~74% of one tertile and ~76% of the
other), which is a different quantity — two tertiles can share a series mix
without sharing rows. Measured directly instead, at the row level:

| measure | value |
|---|---|
| `depth` high tertile ∩ `context` low tertile | **27,239 rows** |
| share of `depth` high | **85.5%** |
| share of `context` low | **85.5%** |
| Jaccard | 74.7% |
| `raw_volume_24h == 0` share of each tertile | 70.8% / 70.4% |

The claim is **right and stronger than Stage 1 argued** — 85.5% row-level
identity, not a ~75% series coincidence. Restricted to `volume > 0` the gap
falls from **+24.9 to +11.0**.

**Verdict:** computation is sound (rank is genuinely robust, as its comment
claims), but **its headline discrimination is over half the zero-volume
artifact read backwards** — and it is currently the second-largest weight, with
`suggested_weights` proposing to raise it to **0.34**. Acting on that suggestion
would harden the artifact into the production formula.

**Recommendation: keep, but reweight only after the zero-volume case is fixed.**
Do not adopt the suggested 0.34 as-is. Residual +11.0 is real and price-
confounded (low bucket mean uc 0.433 vs high 0.618).

### 1.5 `agreement_factor` — weight 0.1818, reported gap +12.0 **[REVISED]**

Computed by the caller: share of same-side prints on this exact ticker in the
last 6h (`kalshi_trade_tape.py:696–700`, `_AGREEMENT_LOOKBACK_SEC` at `:88`).

Stage 1 called this "the one clean factor… artifact-robust… currently
under-weighted." **That does not survive.** Its tertile *edges* are distinct
(`low_edge` 0.5, `high_edge` 0.840), which is why Stage 1's §2 test passed it —
but the low/mid cut lands **inside** the 25.5% tied mass at exactly 0.5, so the
same arrival-order contamination §2 diagnoses for `proximity_factor` and
`trend_factor` applies here too. Within that tied mass the arrival-order thirds
run **67.7% → 33.5% → 33.4% — a 34.3-pt pure arrival-order spread, larger than
`trend_factor`'s.**

Measured tie-safely, by value rather than by index tertile:

| bucket | n | share | win rate |
|---|---|---|---|
| < 0.5 (disagree) | 19,667 | 20.6% | 59.3% |
| **= 0.5 (default, no recent prints)** | **24,357** | **25.5%** | **44.8%** |
| > 0.5 (agree) | 51,502 | 53.9% | 66.7% |

Honest agree-vs-disagree gap: **+7.4** (**+8.9** controlled for
`raw_volume_24h > 0`) — not +12.0/+11.7. **Both are below
`_MIN_DISCRIMINATION_GAP` = 10** (`confidence_calibration.py:59`), so under an
honest measurement this factor would not qualify as discriminating at all — by
the same threshold this document applies everywhere else.

Note also that the 0.5 default wins **44.8%, below both real buckets**. It is
not neutral in outcome terms: "no recent prints on this ticker" is itself
predictive of a loss. That is the **identical coverage defect** §1.7 diagnoses
for `trend_factor` — a default standing in for absent data, on a quarter of the
population — and Stage 1 did not apply it here.

**Verdict: computation is sound; its measurement is contaminated and its
default masks a coverage hole.** **Recommendation: keep at current weight;
withdraw the up-weight. Re-measure tie-safely after D1, and encode the 0.5
default as absence rather than as a value** — it is the third factor (with
`depth_factor` and `trend_factor`) whose headline number is partly a reading of
missing data. The 2026-08-10 finding that floored it
(`confidence_scoring.py:151–157`) has reversed in *sign*, but not to a magnitude
that clears the bar.

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

**Newly established here, and upgraded from sample to full census
[REVISED].** Stage 1 sampled the last 2,000,000 rows. The whole table has since
been scanned twice — `SELECT is_block_trade, COUNT(*) FROM raw_trades GROUP BY
is_block_trade` over `data/series_watcher.db` returns a **single group,
`(0, 33,221,747)`**, in ~32 s. **Every captured raw trade, without exception.**
Block trades are matched *off book* and simply do not appear on the public trade
flow this app consumes.

**Verdict: a dead factor, not an untested one — by census, not by inference.**
It cannot ever discriminate on this data source, and falsification condition #3
in §7 is closed. **Recommendation: keep at weight 0 and record why**, so a
future session does not re-plan it as "untested, needs data". If block-trade
data is wanted, `GET /trades` accepts `is_block_trade` as a filter
(`docs/kalshi/CHEATSHEET.md:487`) — a different ingestion path, not a scoring
change.

### 1.10 `raw_spread` — logged, never read, and **not a factor candidate [REVISED]**

Stage 1 ranked this as N2, its cheapest new-factor candidate, on a tertile gap
of −15.7. **That was wrong, and wrong in the exact way this document condemns
elsewhere.** It is re-filed here as a defect rather than a lead.

**It is a fabricated missing value.** `kalshi_trade_tape.py:746`:

```python
yes_ask = float(market.get("yes_ask_dollars") or price)
...
"spread": round(max(yes_ask - price, 0.0), 4),
```

With no ask data, `yes_ask` falls back to `price`, producing a spread of exactly
**0.0** — the comment above it says so outright ("no ask data = assume no
spread"). This is the same `max(market_volume, 1.0)` failure class as §1.1, in a
second location, and `market_catalog` has no ask column at all
(`market_watch/market_fetch.py:165–172` records this as a previously-shipped
live bug), so catalog-sourced markets always take the fallback.

**Its tertile split is entirely inside the fabricated mass.** 54,838 rows
(**57.4%**) are exactly 0.0; the low tertile is 31,842 rows with
`low_edge = 0.0`, so the **whole low bucket is drawn from the tied zero mass in
rowid order**. Within that mass the arrival-order thirds run **71.8% → 63.6% →
44.9%** — the same time signal §2 identifies, not a spread effect.

| comparison | gap |
|---|---|
| Stage 1's index-tertile gap | −15.7 (re-derived −15.6) |
| honest: spread == 0 (60.1%, n=54,838) vs spread > 0 (58.9%, n=40,688) | **−1.2** |
| controlled for `raw_volume_24h > 0` | **+1.1 — the sign flips** |

The effect is ~13× overstated and does not survive the volume control.

**It is also mislabeled.** `yes_bid_dollars` is not in `_MARKET_FIELDS`
(`market_watch/market_fetch.py:33–48`) and never reaches the provider, so the
quantity is `max(best_ask − last_traded_yes_price, 0)` — a one-sided distance
between two different things, not a bid/ask spread. Under CLAUDE.md's "a
displayed value must match its label", it needs renaming wherever it survives.

**Verdict: the third instance of the fabricated-missing-value bug**
(`volume_24h` §1.1, `yes_ask_dollars` here, `momentum()` coverage §1.7 — and
`agreement_factor`'s 0.5 default §1.5 is a fourth). **Recommendation: fix the
capture (absent, not 0.0), then re-measure before anyone calls it a candidate
again.** Removed from §4.

---

## 2. A defect in the measurement instrument itself **[REVISED — the validity predicate below is corrected]**

`_bucket_win_rates` (`confidence_calibration.py:67–116`) splits by **index-based
tertiles** and guards only against `len(distinct_values) < 3`. Its own docstring
names the hazard ("index-based tertiles on a tied value would just reflect
whatever order the rows happened to arrive in") — **but the guard only catches
the fully-degenerate case.** A factor with thousands of distinct values but a
large tied mode defeats it.

Stage 1 proposed testing whether the low bucket's top value equals the high
bucket's bottom value (`low_edge != high_edge`). **[REVISED] That is the wrong
predicate, and it misses most of the contamination.** Two tertile edges can be
far apart while *each individual cut* still falls in the middle of a tied run —
which is the condition that actually makes a bucket arrival-ordered. The correct
predicate is **does the boundary value appear on both sides of the cut**, tested
at each of the two cuts independently:

| factor | Stage 1's `low_edge != high_edge` | low/mid cut in a tie | mid/high cut in a tie | modal share |
|---|---|---|---|---|
| `depth_factor` | pass | no | no | 23.9% |
| `unusualness_factor` | pass | **yes** | **yes** | 3.5% |
| `proximity_factor` | **FAIL** | yes | yes | 73.5% |
| `context_factor` | pass | **yes** | no | 9.5% |
| `agreement_factor` | pass | **yes** | **yes** | 25.5% |
| `cluster_factor` | pass | **yes** | **yes** | 50.7% |
| `trend_factor` | **FAIL** | yes | yes | 64.2% |

**Six of the seven measurable factors cut inside a tied value at at least one
boundary; Stage 1's proposed guard catches two of them.** Only `depth_factor` is
clean on both cuts. The four it silently passes include `agreement_factor`
(§1.5), whose contamination is 34.3 pts — larger than `trend_factor`'s.

So the reported gaps are, for six of seven factors, partly comparing identical
factor values against each other, ordered by insertion. Because win rate drifted
from ~71% to ~40% over the accumulation period, that ordering is a strong time
signal, so the artifact is large, not incidental.

Two things compound it. First, `resolved_signals_with_factors()` has **no date
scoping at all** by design (`signal_log.py:606–613`), so the report blends a
71%-win-rate era with a 40%-win-rate era with no time dimension anywhere.
Second, that same query has **no `ORDER BY`**: row order is unspecified by SQL
semantics and merely happens to be rowid order today. Stage 1 called the
tie-break "arrival order"; it is more precisely *undefined order that is
insertion order in practice* — which strengthens the case for the guard, since
nothing pins it.

**Disposition: real bug, CI guard — and the guard must be the
boundary-in-tie predicate, not `low_edge != high_edge`.** A tie-aware split
(rank-with-ties or value-based binning) plus an assertion that neither cut
lands inside a tied run is what actually covers this. This is upstream of every
weight decision the app makes, and `auto_apply_enabled` would act on it
(currently `false` — `config/settings.yaml:153`).

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

Every "gap" below is measured against the same price-confounded label (§0.1) and
by the same contaminated instrument (§2) — they are leads, not verdicts.

**[REVISED] The ranking has changed and the IDs are kept stable for reference,
so N-numbers are no longer rank order.** Stage 1 ranked N2 (spread) and N3
(open interest) top, on the reasoning that both were already captured and free
to read. Both premises were wrong: N2 is a fabricated value and is **withdrawn
to §1.10 as a defect**; N3 is stripped before it reaches scoring and needs
plumbing first.

Re-ranked on **(measured evidence it works) × (coverage on the paths that score)
÷ (plumbing cost)** — the criterion Stage 1's ranking was missing, since it
priced two candidates purely on the last term and got that term wrong for both:

| rank | candidate | why here |
|---|---|---|
| 1 | **N3** open interest | Directly replaces the §1.1 defect with a stock rather than a 24h flow; cost is one allowlist entry. Unmeasured, but the mechanism is sound and the fix is a prerequisite either way |
| 2 | **N1** book depth at the touch | The most direct measure of what `depth_factor` claims; real data already captured. Ranked below N3 only on the join cost and its watched-series scope |
| 3 | **N5** model-vs-market edge | The **strongest measured evidence in the table** (Brier 0.0171 vs market 0.1046) and precisely the skill §0.2 says is missing — held at 3 only because it is settlement-window-scoped, so it cannot serve the whole population |
| 4 | **N6** lifetime-normalised time-to-close | Cheap, fixes §1.3's series proxy, no new data. Unmeasured |
| 5 | **N4** realized volatility | Already computed, but carries the same watchlist-coverage limit that broke `trend_factor` (§1.7) — fix that first or inherit the defect |
| 6 | **N7** rejected-candidate context | Genuinely informative, but a population normaliser rather than a per-signal factor; the largest design change for the least certain payoff |

Two lessons generalise from the correction, and Stage 3 should apply both to
every remaining candidate before adopting it: **(a) check whether the field is
fabricated when absent** (§1.10's failure), and **(b) check whether it actually
reaches the scoring call site on every path** (§4/N3's failure). Neither check
was run on N2 or N3, and both would have caught the error immediately.

| # | Candidate | Where the raw data already lives | Evidence | Caveat |
|---|---|---|---|---|
| N1 | **Order-book depth / liquidity at the touch** — what `depth_factor` *claims* to measure | `data/series_watcher.db` `book_snapshots`: 205,330 rows with `yes_bid_dollars`, `yes_ask_dollars`, `yes_bid_size_fp`, `yes_ask_size_fp`, `open_interest_fp`, `dollar_volume` (`services/series_watcher.py:185–205`, writer `record_book` at `:303`) | Directly measures resting size a print consumed — immune to the 24h-volume zero that breaks `depth_factor` (§1.1) | Watched-series-scoped, not exchange-wide; needs a join by (ticker, observed_at) |
| ~~N2~~ | ~~**Bid/ask spread**~~ | **[REVISED] WITHDRAWN — see §1.10.** `raw_spread` is a fabricated 0.0 on 57.4% of rows, its whole low tertile is that tied mass, its honest gap is −1.2 (**+1.1** controlled — the sign flips), and it is not a bid/ask spread at all. It is a defect, not a candidate. | — | — |
| N3 | **Open interest, and size relative to it** | `market.open_interest_fp` — `FixedPointCount`, "contracts bought on this market disconsidering netting" (`docs/kalshi/get-market.md:246`), a **required** field on the market object (`:130`) | Not yet measured; the denominator `depth_factor` should probably have used — OI is a stock, not a 24h flow, so it does **not** go to zero on short-lived markets | **[REVISED] Not already available.** `_slim_market` (`market_watch/market_fetch.py:51–52`) projects onto a 10-field allowlist (`:33–48`) that excludes `open_interest_fp`, and `state["markets"]` is that slimmed list (`main.py:313`, assigned `:795`). It survives only on the off-watchlist `_resolve_unknown_markets` path (`kalshi_trade_tape.py:464`), so it is a **partial-coverage field** today — the exact shape that produced §1.7 and §1.10. Cheap to fix (one allowlist entry, pinned by `tests/test_kalshi_contracts.py::test_slim_market_keeps_every_documented_field_it_declares`) but a real code change with a data-plane consequence, not a free read. |
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

## 4.1 The scoring objective: accuracy and edge as **two** scores **[REVISED]**

**Human directive, recorded here as the standing constraint for Stage 3: score
accuracy and edge *independently, as two scores* — not one blended score, and
not edge replacing accuracy.** §0.1/§0.2 established that the accuracy objective
is nearly exhausted by the market price. That argues for adding an edge
objective; it does *not* argue for deleting the accuracy one. Both are wanted,
separately, and the data below shows they cannot be collapsed into one number
even if someone wanted to.

**Realized edge is computable per signal today.** Verified by running it:
`kalshi_fees.unit_cost(side, price)` gives the per-contract cost,
`kalshi_fees.taker_fee_per_contract(uc, ticker)` the fee, and `correct` supplies
the $1/$0 payoff, so `edge = payoff − unit_cost − fee`. No new field is needed.
Mean realized edge across all priced rows: **−$0.0097/contract**.

### The two scores cannot share a population

| score | rows | share | base rate |
|---|---|---|---|
| accuracy (needs `correct`) | 95,526 | 100% | 59.6% |
| edge (needs `correct` **and** `price`) | 62,759 | **65.7%** | **52.1%** |

32,771 rows (34.3%) predate the `price` column (`signal_log.py:131`) and never
will have one. The two populations differ by **7.5 pts of base rate**, so the
two scores are not measurable on common ground. Any Stage 3 design must state
that asymmetry explicitly rather than reporting the two as if commensurable —
this is the first real design constraint the split imposes.

### The negative-EV band, now measured rather than designed

Inside the **live** tradeable band — `min_unit_cost: 0.5` / `max_unit_cost: 0.9`
(`config/settings.yaml:59–60`; note ROADMAP.md:276's "0.60–0.95" is the older
figure, and `kalshi_fees.breakeven_unit_cost`'s docstring flags it stale as of
2026-08-30) — only **24,727 rows survive (25.9% of all)**. They **win 68.7%**
and carry a mean realized edge of **−$0.0150/contract**.

That is the ROADMAP's flagged negative-EV band, quantified for the first time:
the app is buying a 68.7% win rate at a price that loses one and a half cents a
contract. A high win rate and a negative edge, on the same rows, is the single
cleanest statement of why one score cannot serve both objectives.

### Five of seven factors flip sign between the two objectives

Measured in-band (n = 24,727), index tertiles, high minus low:

| factor | accuracy gap (pts) | edge gap ($/contract) | |
|---|---|---|---|
| `depth_factor` | −4.5 | **+0.0099** | **FLIP** |
| `unusualness_factor` | −27.4 | **+0.0151** | **FLIP** |
| `proximity_factor` | −1.5 | −0.0025 | — *(tied split)* |
| `context_factor` | +3.2 | **−0.0073** | **FLIP** |
| `agreement_factor` | −0.7 | **+0.0031** | **FLIP** |
| `cluster_factor` | +2.0 | **−0.0019** | **FLIP** |
| `trend_factor` | +1.6 | +0.0158 | — *(tied split: 86.8% of in-band rows sit at 0.5, so this row is not a measurement)* |

**This is direct evidence that one blended score provably cannot serve both
objectives.** The factors that predict "you were right" are largely the factors
that predict "you overpaid" — `context_factor` and `cluster_factor`, the two the
current formula rewards most on accuracy, are both *negative* on edge. Two
separate composites are required; no reweighting of a single formula can
reconcile a sign disagreement.

### Which factors belong in which score

Provisional, from the table above; every row inherits the tie caveat of §2 and
must be re-measured after D1:

| factor | accuracy score | edge score |
|---|---|---|
| `context_factor` | **yes** — its residual +11.0 is the strongest surviving accuracy term | no (−0.0073) |
| `cluster_factor` | **yes** — cleanly monotone (§1.6) | no (−0.0019) |
| `trend_factor` | **yes**, once coverage is fixed (§1.7) | unmeasurable until then |
| `unusualness_factor` | **no** — a price identity (§1.2) | **yes — the largest credible positive edge term** |
| `depth_factor` | no, until the zero-volume fix (§1.1) | candidate (+0.0099), re-measure after the fix |
| `agreement_factor` | marginal — honest gap below threshold (§1.5) | candidate (+0.0031), small |
| `proximity_factor` | no (§1.3) | no — negative on both |

**Consequence Stage 1 did not anticipate: "remove `unusualness_factor`" is
withdrawn as stated.** Under the edge objective, in-band, it carries **+$0.0151
— the largest positive edge term in the set** on a split that is not
tie-invalidated (`trend_factor`'s nominally larger +$0.0158 rides on a split
where 86.8% of in-band rows are tied at 0.5, and is not a measurement).
Mechanically this is exactly right: high unusualness = price near 0.5 = cheaper
contracts = better edge. Deleting the factor outright would discard the most
edge-informative term the app has. **It moves to the edge score; its removal
from the *accuracy* score stands** (§1.2's identity argument is unaffected).

**"Do not invert `unusualness_factor`" is reconfirmed, now from a second
angle.** §1.2 established that inverting means preferring prices far from 0.5,
i.e. buying favourites — and the measured cost of that is the −$0.0150 band
above. The accuracy angle said inversion is meaningless (the response curve is
flat); the edge angle says it is actively expensive. Both agree.

**One caveat Stage 3 must carry:** edge is mechanically a function of
`unit_cost`, and several factors are functions of `unit_cost` too, so edge gaps
carry their own identity confound — the mirror of §0.1. The edge objective is
not confound-free, just differently confounded.

---

## 5. Rough directions for Stage 3 (design)

**[REVISED]** Ordered by expected value. All four are directions, not designs.

### D1 — Fix the measurement instrument before touching any weight

Nothing downstream can be trusted while §2 stands. Three parts:

1. **A tie-aware bucketing method** (rank-with-ties, or value-based bins),
   guarded by the **boundary-in-tie predicate**, not by
   `low_edge != high_edge`. **[REVISED]** Stage 1 proposed the latter; §2's
   corrected table shows it catches only 2 of the 6 contaminated factors and
   silently passes `agreement_factor`, whose contamination (34.3 pts) is the
   largest of them. The assertion that actually covers this is *neither cut may
   land inside a tied run*, tested at both boundaries independently. Anything
   weaker leaves the bug in place under a green test.
2. **A time dimension on `resolved_signals_with_factors()`** so a 71%-era and a
   40%-era are not silently averaged — plus an explicit `ORDER BY`, since the
   query currently has none and its row order is unspecified by SQL semantics.
3. **Stop fabricating missing inputs**, bundled here because it is one defect
   class in four places, not four bugs:
   `max(market_volume, 1.0)` (`confidence_scoring.py:194`, §1.1),
   `yes_ask_dollars or price` (`kalshi_trade_tape.py:746`, §1.10),
   `momentum()`'s 0.5 default (§1.7), and `agreement_factor`'s 0.5 default
   (§1.5). A missing denominator, ask, momentum, or print history means
   *unknown* — the honest encoding is absence, the idiom `_price_dollars`
   already established for exactly this failure
   (`kalshi_trade_tape.py:114–137`). **The reusable disposition here is one
   runtime diagnostic: share of rows where a factor's input was fabricated or
   absent**, per factor. Four independent instances is a bug class that earns
   permanent detection.

This is a prerequisite, cheap, and independently valuable: it also protects
`auto_apply_enabled` from acting on an artifact.

### D2 — Build the second score; do not replace the first **[REVISED]**

Stage 1 framed this as "change the objective." **The directive is narrower and
firmer: build a second, independent edge score alongside the accuracy score**
(§4.1). The evidence that this must be two scores rather than one reweighted
formula is the five-of-seven sign flip — a single blended number cannot satisfy
two objectives that disagree on the sign of most of its inputs.

Design constraints, all measured (§4.1):

- **The populations differ.** Accuracy scores 100% of rows at a 59.6% base rate;
  edge scores 65.7% at 52.1%. Report them side by side, never as one figure, and
  never compare their magnitudes directly.
- **Edge is already computable** from `kalshi_fees.unit_cost` +
  `taker_fee_per_contract` + `correct`. No new field, no new capture. Mean
  realized edge is **−$0.0097/contract** overall and **−$0.0150 in-band** — the
  quantity the whole exercise is trying to move.
- **Report Brier skill relative to the market price** as the accuracy-side
  headline, not a low-vs-high win-rate gap. The repo already has Brier machinery
  in `settlement_edge.py`, `index_feed/settlement_algebra.py` and
  `market_analyst_agent/per_market.py` — reuse, do not re-derive.
- **Assign each factor to a score, per §4.1's table**, rather than reweighting
  nine factors against one target. `unusualness_factor` moves to the edge score
  (it is the largest credible positive edge term) instead of being deleted;
  `context_factor` and `cluster_factor` stay on accuracy, where their edge
  contribution is negative.
- **Carry the mirror confound.** Edge is a function of `unit_cost` and so are
  several factors, so the edge objective has its own identity problem. It is not
  the confound-free answer to §0.1, just a different one.

### D3 — Replace the volume-derived pair with a real liquidity measure **[REVISED]**

`depth_factor` and `context_factor` currently read one field (`volume_24h_fp`)
in two directions and split a zero-volume artifact between them (§1.1/§1.4) —
85.5% of one tertile is the other. Both need replacing with something that
measures resting liquidity and does not collapse on sub-24h markets. The
candidate ranking has changed:

1. **N3 (open interest) first, but not for Stage 1's reason.** Stage 1 ranked it
   top as a free read — that premise was false (`_slim_market` strips
   `open_interest_fp`; §4/N3). It still ranks first: it is a *stock*, not a 24h
   flow, so it is the denominator `depth_factor` should have had, and the fix is
   one allowlist entry plus its pinned contract test. But it is a code change
   with a data-plane consequence, and until it lands the field has **partial
   coverage** — present on the off-watchlist path, stripped on the watchlist
   one. Shipping a factor on a partially-covered field is how §1.7 and §1.10
   happened; fix coverage *first*, then measure.
2. **N1 (book depth at the touch) second**, unchanged. It is the most direct
   measure of what `depth_factor` claims, and `book_snapshots` already carries
   bid/ask/size/OI — but it is watched-series-scoped and needs a
   (ticker, observed_at) join, so it is genuinely the more expensive option.
3. **N2 (spread) is withdrawn entirely.** Stage 1 called it "the cheapest of
   all"; it is a fabricated 0.0 on 57.4% of rows with an honest gap of −1.2 that
   flips to +1.1 under the volume control (§1.10). It is not a cheap candidate,
   it is an unfixed defect — and fixing it is D1 work, not D3 work.

### D4 — Re-measure everything after D1, before any weight moves **[REVISED]**

Every gap in this document, including the corrected ones, was produced by the
contaminated instrument §2 describes; six of seven factors cut inside a tie, and
four factors read a fabricated input. **No weight — and in particular no
`suggested_weights` application — should move before D1 lands and every factor
is re-measured on the fixed instrument.** Two specific reasons this is not
theoretical:

- `auto_apply_enabled` would raise `context_factor` to 0.34, hardening the
  zero-volume artifact into production (§1.4).
- `_suggested_weights` clamps every negative gap to the same floor
  (`confidence_calibration.py:141–150`), so the instrument cannot distinguish
  actively anti-predictive from mildly anti-predictive, and can never propose
  removing or inverting anything — only shrinking toward a floor. It is
  structurally incapable of expressing this document's own findings.

---

## 6. Disposition summary (investigation-to-guard)

**[REVISED]** — rows marked ⟳ changed after the Stage 2 review.

| Finding | Class | Disposition |
|---|---|---|
| ⟳ Tertile cut lands inside a tied value (§2) — **6 of 7 factors**, not 2 | Real bug in the measurement instrument | CI guard + fix, using the **boundary-in-tie** predicate; `low_edge != high_edge` is insufficient. Blocks every weight decision |
| ⟳ **Fabricated missing inputs — one bug class, four sites**: `max(volume, 1.0)` (§1.1), `yes_ask_dollars or price` (§1.10), `momentum()` default (§1.7), `agreement_factor`'s 0.5 default (§1.5) | Real bug class, 23.9% / 57.4% / 64.2% / 25.5% of the population respectively | Fix each at its site; **one shared runtime diagnostic — per-factor share of rows whose input was fabricated or absent.** This is the finding with the most reuse in it |
| ⟳ `unusualness_factor` is a price identity (§1.2) | Design defect, not a bug | Remove from the **accuracy** score; **move to the edge score** (§4.1) — it is the largest credible positive edge term. Do not delete, do not invert |
| ⟳ `context_factor`'s +24.9 is >half artifact (§1.4) — **85.5% row-level** overlap with `depth_factor`'s high tertile | Misleading metric | Do **not** apply `suggested_weights` 0.34 until D1 lands |
| ⟳ `agreement_factor` is not clean (§1.5) — 34.3-pt arrival-order spread inside its own 0.5 mass; honest gap +7.4/+8.9, **below the 10-pt threshold** | Misleading metric + coverage defect | Withdraw the up-weight recommendation; re-measure tie-safely after D1 |
| ⟳ `raw_spread` is a fabricated 0.0 on 57.4% of rows and is not a bid/ask spread (§1.10) | Real bug + label mismatch | Withdrawn as a factor candidate; fix the capture and rename. "A displayed value must match its label" |
| ⟳ `open_interest_fp` stripped by `_slim_market`'s allowlist (§4/N3) | Partial-coverage field | Add to `_MARKET_FIELDS` **before** measuring it as a factor |
| `trend_factor` neutral for 64.2% of signals (§1.7) | Data-completeness defect (data-plane rule) | Coverage fix; the largest weight rides on it |
| ⟳ `block_trade_factor` structurally dead (§1.9) — **full census, 33,221,747 rows, zero block trades** | Confirmed fact, no longer an inference | Record it, so it is not re-planned as "untested" |
| ⟳ Accuracy and edge disagree on the sign of 5 of 7 factors (§4.1) | Design constraint from the human directive | **Two independent scores, never one blend.** Report their differing populations (100%/65.7%, 59.6%/52.1%) explicitly |
| ⟳ `_suggested_weights` clamps every negative gap to one floor (§D4) | Instrument limitation | It cannot express "remove" or "invert" — do not read a floored weight as a verdict |
| Calibration band midpoints (§3) | Methodology artifact | Use the band's actual mean score as its prediction |
| `correct` graded at `determined`, not `finalized` | Pre-existing, already documented | `services/whale_calibration/README.md` — carried forward, not re-opened |

## 7. What would falsify the central claims

1. **§0.1** would fail if the win-rate-by-unit-cost table were flat rather than
   tracking price — re-runnable from `signal_log.db` at any time.
2. **§1.1/§1.4** would fail if the zero-volume rows were not concentrated in
   short-lifecycle series, or if the gaps did not move when controlling for
   `raw_volume_24h > 0`. Both were measured, both moved.
3. ~~**§1.9** rests on a 2M-row sample of 33.1M~~ — **[REVISED] CLOSED.** The
   full table has now been scanned twice: 33,177,986 rows at Stage 2 review and
   33,221,747 on re-verification, both returning a single group
   `is_block_trade = 0`. There is no unsampled remainder. "Structurally dead" is
   a census result.
4. **§2** is arithmetic on the shipped code path and is not probabilistic.
5. Every §4 candidate's gap shares the §0.1 price confound and must be
   re-measured under the D2 dual objective before any of them is adopted.
6. **[REVISED] §4.1's dual-score argument** would fail if the accuracy and edge
   gaps agreed in sign across the factor set — one reweighted formula would then
   suffice. Measured: they disagree on 5 of 7. It would also weaken if the
   in-band edge were positive; measured at **−$0.0150/contract** over 24,727
   rows. Both are re-runnable from `signal_log.db` plus `kalshi_fees` at any
   time.
7. **[REVISED] §1.5's downgrade of `agreement_factor`** would fail if its
   tie-safe agree-vs-disagree gap cleared 10 pts. Measured at +7.4 (+8.9
   controlled). A later, larger sample could move it above the bar — that would
   restore Stage 1's recommendation, and is the specific thing to re-check
   rather than assume.
8. **[REVISED] §1.10's withdrawal of `raw_spread`** would fail if the honest
   zero-vs-positive gap were large; measured at −1.2, flipping to +1.1 under the
   volume control. If the capture is fixed so an absent ask is `NULL` rather
   than 0.0, the factor deserves one fresh measurement — on the repaired data
   only, never on the historical rows.
