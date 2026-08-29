# Real-Money-Shaped Trade Performance Analysis (2026-08-29)

Read-only, live-data pass over the paper-trading strategy's actual closed
trades — run at the user's request during the realtime-data-plane's
`two_consumer_mode` soak, explicitly to avoid touching code or the running
process. Source: `GET /api/trading-history` (limit=200, two pages, 257
rows), which reuses `services/history/trade_analytics.py`'s existing
backend-computed fields (`realized_pnl`, `won`, `close_type`, `cost_basis`)
rather than re-deriving P&L from raw price/size arithmetic — the
2026-08-10-era no-side-inversion and 49x-accounting bugs are exactly what
that discipline exists to prevent. Statistical tests used the app's own
`services/stats_power.py` functions (`one_sample_t_score`,
`two_proportion_z_score`) where applicable, cross-checked via the Wolfram
MCP for exact p-values and for a normality check that changed which test
was valid (see §2).

## 1. Overall book (257 closed trades)

| Metric | Value |
|---|---|
| Win rate | 68.9% (177W / 80L) |
| Total realized P&L | +$5,282.93 |
| Avg realized P&L / trade | +$20.56 |
| Win-rate margin of error (95%) | ±5.7 pts |
| Total capital deployed (cost basis) | $91,610.29 |
| Total fees paid | $2,487.80 |

By close type:

| close_type | n | win% | total P&L | avg P&L |
|---|---|---|---|---|
| settled_win | 130 | 100.0% | +$23,648.28 | +$181.91 |
| settled_loss | 56 | 0.0% | -$21,146.83 | -$377.62 |
| auto_exit | 37 | 97.3% | +$5,475.47 | +$147.99 |
| position_netting | 34 | 32.4% | -$2,693.99 | -$79.23 |

Net positive edge overall. `settled_win`/`settled_loss` sum to +$2,501.45
net (letting positions ride to settlement is working); `auto_exit` is
strongly positive; `position_netting` is the one category dragging the
total down — see §3.

## 2. Entry-price-band confirmation (independent of the 2026-08-26 finding)

`docs/superpowers/specs/2026-08-26-economic-strategy-remediation-design.md`
flagged a 0.60-0.95 unit-cost-band negative-EV pattern from **candidate-gate
data** (hypothetical outcomes of evaluated candidates, `candidate_log`'s
`gate_summary()`). This pass checks the same band against a **different
population: actual executed, closed trades** — an independent
corroboration, not a re-run of the same computation.

| | n | win rate | mean P&L/trade | median P&L/trade |
|---|---|---|---|---|
| 0.60–0.95 entry price | 94 | 74.5% | $3.08 | $66.19 |
| outside that band | 162 | 65.4% | $29.15 | $110.56 |

Worst sub-band, **0.80–0.95: 31 trades, 83.9% win rate, but total P&L is
net NEGATIVE (-$197.07, avg -$6.36)** — a real, live instance of the
"high win rate, thin edge" trap `services/candidate_log.py:326`'s own
comment already names.

**Statistical read, both directions checked:**
- Win-rate difference (74.5% vs 65.4%): two-proportion z-test (the app's
  own `two_proportion_z_score`), z=1.514, **p=0.13 — not significant**.
  The band's apparently-higher win rate could plausibly be noise.
- Mean-P&L difference ($3.08 vs $29.15): a one-sample/two-sample t-test
  was the first tool reached for, but Wolfram's own `TTest` flagged a
  normality violation (`TTest::nortst`, p<0.025) on both groups - expected,
  since binary-settlement P&L is bimodal (a cluster of full wins near
  +$150-400, a cluster of full losses near -$300 to -$470), not normal.
  Switched to the correct tool for that shape: Mann-Whitney U
  (`HypothesisTesting\`MannWhitneyTest`), **p=0.024 — significant** at the
  conventional 0.05 level.

Net finding: the band's higher win rate is not statistically distinguishable
from the rest of the book, but its lower realized P&L is - the exact
asymmetry that makes win-rate-only reasoning (the `advisory_engine`
open-decision line) actively misleading for this specific band: a
win-rate-only view would favor the underperforming segment.

## 3. New finding: `position_netting` losses (not the same mechanism)

Checked explicitly whether this is a second face of the same band problem
before recording it separately: it is not. `position_netting` closes span
entry prices 0.12-0.89 with no concentration in the 0.60-0.95 band (11 of
34, roughly proportional to that band's ~37% share of all trades). 34
trades, 11 wins (32.4%), -$2,693.99 total, -$79.23 avg. `exit_reason`
samples show the mechanism: opposing positions on the same event netted at
a `locked_loss` (e.g. two markets within one NFL/ATP/WTA event moving
against each other). Not investigated further here - recorded as its own
`docs/open-decisions.md` line (`services/exits/position_netting.py` is the
next-action pointer) rather than folded into the price-band finding.

## 4. What this does and does not settle

**Settled by this pass:** the 0.60-0.95 band's negative-EV pattern
reproduces in real executed-trade data, not just candidate-gate
hypotheticals - two independent data sources now agree on the same
direction and roughly the same band.

**Still open, deliberately not answered here:**
- Causal mechanism - is the higher entry price itself the driver (less
  room for the position to move before hitting a stop/take-profit,
  worse risk/reward per the fixed payout structure), or a confound with
  something else these 94 trades share (series mix, hold time, size)?
  The existing spec's own gate-level diagnostic (grouping by
  `(strategy, gate_name, unit_cost_band)`) is the right next tool for
  this, not a second ad hoc pass here.
- Whether `position_netting`'s design is broken or is doing its job
  (locking a smaller loss vs a larger one) at a cost that's simply real -
  no code was read for this pass beyond the exit_reason strings.
- Sample size: 257 total trades, 94/31 in the two bands checked - real
  enough to reach significance on the P&L test, but `confidence_label`
  conventions elsewhere in this repo would still call this a `moderate`
  sample, not a settled long-run edge.

## 5. Relates to (act on these, don't re-derive)

- `docs/open-decisions.md`: "Banded cost-aware gate EV diagnostic" line -
  this is independent corroborating evidence toward approving that spec,
  not a duplicate of the finding behind it.
- `docs/open-decisions.md`: "`advisory_engine` suggests on win rate alone"
  line - §2 above is a concrete, current, quantified instance of exactly
  that blind spot.
- `docs/open-decisions.md`: "`position_netting` closes lose money at
  scale" line (added same day) - this document is its full analysis;
  that line is the pointer.
