# Real-Money-Shaped Trade Performance Analysis (2026-08-29)

Read-only, live-data pass over the paper-trading strategy's actual closed
trades — run at the user's request during the realtime-data-plane's
`two_consumer_mode` soak, explicitly to avoid touching code or the running
process. Source: `GET /api/trading-history` (limit=200, two pages, 257
rows), which reuses `services/history/trade_analytics.py`'s existing
backend-computed fields (`realized_pnl`, `won`, `close_type`, `cost_basis`)
rather than re-deriving P&L from raw price/size arithmetic — the
2026-08-10-era no-side-inversion and 49x-accounting bugs are exactly what
that discipline exists to prevent.

**Correction (same day, before this doc's first version reached anyone):**
the first pass bucketed trades by the raw `entry_price` field — which
`services/paper_broker.py:open_position`'s own comment names explicitly as
"always the YES price... a NO contract's real per-unit cost is (1 - price),
not price itself." 102 of 257 trades (40%) are `no`-side. Bucketing by raw
price rather than side-adjusted `unit_cost` mixed a `no` trade bought at
yes-price 0.10 (real unit cost 0.90, expensive) in with a `yes` trade
bought at 0.10 (actually cheap) — the identical bug shape CLAUDE.md already
names twice. Caught by verifying the field's definition in source before
trusting a second pass's numbers, not by inspection. §2 below is the
corrected version; the original 0.60–0.95-raw-price framing is retracted.

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

Net positive edge overall. `position_netting` is the one category dragging
the total down — see §3.

## 2. Unit-cost banding (side-corrected: `unit_cost = price if side=="yes"
   else 1-price`, matching `services/strategy_engine.py`'s own gate)

All 257 trades share one `config_fingerprint` (`73a77035692e6520`,
confirmed live via `config_performance.get_variant()` — matches current
`config/settings.yaml`: `min_unit_cost: 0.5`, `max_unit_cost: 0.9`), so
there is no config-drift confound in what follows. Observed `unit_cost`
range is exactly 0.5–0.9, confirming the gate is enforced correctly.

| unit_cost band | n | win% | total P&L | avg P&L | ROI on cost |
|---|---|---|---|---|---|
| 0.50–0.60 | 110 | 61.8% | +$3,658.57 | +$33.26 | 9.0% |
| 0.60–0.70 | 60 | 65.0% | +$196.14 | +$3.27 | 0.9% |
| 0.70–0.80 | 38 | 73.7% | +$790.31 | +$20.80 | 5.8% |
| 0.80–0.90 | 40 | 85.0% | +$405.84 | +$10.15 | 3.0% |
| 0.90 (ceiling, exact) | 8 | 87.5% | **-$40.00** | -$5.00 | -1.5% |

The clean, quantified finding is narrower than first reported: capital
deployed above unit_cost 0.60 earns markedly less per dollar (0.9%-5.8%
ROI) than the 0.50-0.60 band (9.0%), and the only band that is net
negative is the small sample sitting exactly at the `max_unit_cost: 0.9`
ceiling. See the config-recommendation writeup delivered directly to the
user for the mechanistic explanation (fee-inclusive breakeven win rate)
and the specific settings suggestion — not duplicated here to avoid a
second copy of numbers that can drift from each other.

n=8 at the ceiling is a real but thin sample on its own; the effect is
corroborated by mechanism (see the fee-breakeven calculation below), not
by that slice's statistical power alone.

## 3. `position_netting` losses: concentrated at LOW unit cost, not high

Re-checked with the corrected `unit_cost` (this reverses part of the first
pass's framing): losses are worst at 0.60-0.70 (-$1,084.62 over 10 trades),
not at the top of the price range.

| unit_cost band | n | win% | total P&L |
|---|---|---|---|
| 0.50-0.60 | 14 | 35.7% | -$857.00 |
| 0.60-0.70 | 10 | 30.0% | -$1,084.62 |
| 0.70-0.80 | 6 | 33.3% | -$374.34 |
| 0.80-0.90 | 4 | 25.0% | -$378.03 |

This confirms (now on corrected data) what the first pass already
concluded: `position_netting` is an independent mechanism from the
unit-cost-band effect, not a second face of it. `exit_reason` samples show
`locked_loss` netting on same-event opposing positions (e.g. two markets
within one NFL/ATP/WTA event moving against each other) - the module's own
design doc (`services/exits/position_netting.py`) frames this as correct,
unavoidable damage control (the loss is already mathematically fixed
before the module ever sees it), which reframes the real question as
upstream: what let multiple positions accumulate on the same
mutually-exclusive event in the first place. See the settings
recommendation delivered directly to the user (`max_open_positions_per_series`,
`position_netting.min_edge_improvement_usd`) for the concrete levers this
points to - not duplicated here.

## 6. Full settings.yaml pass (23 sections, ~140 fields)

Prompted directly: "I meant all the settings." §§1-3 above cover what the
257-trade dataset can speak to; this section is the rest.

**The whale-print-size split reframes into a series/category split, not a
size effect.** `whale_watcher_kalshi.min_contracts_by_series` gives crypto
(KXBTC15M/KXBTCD) a 2,500-contract floor while every sports series sits on
the 10,000 global default (`whale_watcher_kalshi.min_contracts`) — two
different populations, not one dose-response curve. Parsed print size from
`entry_reason` ("whale print N @ price"), cross-checked against ticker
series to confirm the split before trusting it:

| Group | n | Total P&L |
|---|---|---|
| Crypto (BTC/ETH) | 146 | +$5,624.66 |
| Sports & other | 111 | -$341.73 |

Nearly the entire book's edge is crypto. Sports is not uniformly bad —
broken out by series, every sports series except two nets positive
(+$1,388.96 combined); two series alone account for -$1,730.69:

| Series | n | win% | total P&L |
|---|---|---|---|
| KXUFCFIGHT | 6 | 33.3% | -$911.70 |
| KXATPMATCH | 23 | 56.5% | -$818.99 |

KXATPMATCH (n=23) is the more load-bearing finding; KXUFCFIGHT (n=6) is too
thin to act on alone.

**`whale_confidence_weights`** — checked `entry_confidence` against outcome;
202 of 256 trades cluster in 0.50-0.60 (`entry_threshold` is 0.55), too
narrow an observed range to say which factor weight is mistuned. Not a
finding, a boundary: this dataset can't answer that question.

**`risk.max_daily_loss_pct: 0.85`** — already named in `CLAUDE.md` itself as
not protective. Restated here for completeness, not re-derived as new.

**Everything else** (`kalshi`, `realtime_data_plane`, `whale_signal` [the
simulator's own knobs — none of these 257 trades came from it],
`confidence_calibration`/`advisory`/`market_analyst` [meta-tuning-process
controls, see §7], `settlement_edge_entry` [`enabled: false`, no live
trades to check it against], `series_watcher`/`series_evaluator`
[data-collection infra; the evaluator is itself disabled],
`event_lifecycle`/`event_schedule` [feeds `position_netting`'s event
grouping — plausibly relevant to §3, not independently verified this pass],
`index_feed`/`logging`/`backup`/`alerting`/`observability`/`research`
[operational infrastructure], `strategy_overrides` [currently a no-op —
`Sports.stop_loss_pct: null` matches the global default]) reviewed and
categorized as operational or not trading-outcome-tunable from this
dataset, not skipped.

## 7. `advisory_engine`'s coverage gap (verified in source, not asserted)

Direct report: "the advisory is not aware of all the settings in
settings.json." Confirmed by reading every `config_path` string
`services/advisory/advisory_engine.py` can ever emit: exactly 11 fields, all
under `strategy.*` — `entry_threshold`, `min_whale_winrate_pct`,
`close_window_sec`, `special_market_min_seconds_to_close`,
`longshot_entry_threshold_bonus`, `take_profit_pct`, `stop_loss_pct`,
`auto_exit_threshold`, `exit_sentiment_lean_pct`,
`exit_sentiment_min_signals`, `excluded_series`. Against ~140 fields across
23 sections, that's the entire reachable surface — every other section in
§6, including `position_netting`, `risk`, and `whale_watcher_kalshi`, is
structurally invisible to it, independent of the reasoning-quality gap the
existing "win rate alone, never cost/P&L" open-decision line already names.

The most relevant single instance is already named in the code's own
comment (`advisory_engine.py:527-544`, dated 2026-08-14):
`whale_watcher_kalshi`'s `min_contracts` gate "also logs rejections...
deliberately NOT added here yet, since `_rejected_candidate_recommendations`'
current_value lookup reads straight off `strat_cfg` and `whale_watcher_kalshi`
is a different config section entirely - needs its own comparison-baseline +
cfg-section wiring, not just a map entry (see ROADMAP.md)." Real rejection
data has been logged against exactly this gate since it shipped and has
never once surfaced as a suggestion. Checked ROADMAP.md and
`docs/open-decisions.md` for an existing tracking line before treating this
as new — neither has one.

`strategy.excluded_series` is the one exception: it's already inside
`advisory_engine`'s reach, and the §6 KXATPMATCH finding is exactly the kind
of input `_series_evaluator_recommendations` is built to act on. It simply
never ran that specific check against that specific series.

## 8. What this does and does not settle

**Settled by this pass:** unit-cost bands above 0.60 earn a real, visible
haircut in ROI on real executed trades; the current `max_unit_cost: 0.9`
ceiling's own edge slice is the one place the book was actually net
negative. `position_netting`'s losses are not explained by unit cost at
all - they need their own, separate lever.

**Still open, deliberately not answered here:**
- Causal mechanism for the unit-cost effect beyond the ceiling slice - is
  0.60-0.90 broadly a worse risk/reward zone, or a confound with something
  these trades share (series mix, hold time, size)? The existing spec's
  own gate-level diagnostic (grouping by `(strategy, gate_name,
  unit_cost_band)`) is the right next tool, not a second ad hoc pass here.
- Whether lowering `position_netting.min_edge_improvement_usd` would have
  caught any of the 34 locked-loss closes earlier, while still variable -
  this trade history has no visibility into what the "variable" state
  looked like before each group locked; `position_netting.preview()`
  run live (read-only, changes nothing) is the way to get that data.
- Sample size: 257 total trades - real enough to reach significance on the
  §2 finding as a whole, but individual bands (especially the n=8 ceiling
  slice and n=4-14 `position_netting` sub-bands) are `moderate`-confidence
  by this repo's own `confidence_label` convention, not a settled long-run
  edge.

## 9. Relates to (act on these, don't re-derive)

- `docs/open-decisions.md`: "Banded cost-aware gate EV diagnostic" line -
  this is independent corroborating evidence toward approving that spec
  (now correctly unit-cost-based), not a duplicate of the finding behind it.
- `docs/open-decisions.md`: "`advisory_engine` suggests on win rate alone"
  line - the 0.90-ceiling slice (87.5% win rate, net negative) is a
  concrete, current, quantified instance of that blind spot; §7 above is a
  second, structurally distinct blind spot (coverage, not reasoning
  quality) the same line's fix would not by itself close.
- `docs/open-decisions.md`: "`position_netting` closes lose money at
  scale" line (added same day) - this document is its full analysis;
  that line is the pointer.
- New, not yet on `docs/open-decisions.md`: `advisory_engine`'s 11-of-~140
  field coverage gap, and specifically the named-but-unaddressed
  `whale_watcher_kalshi.min_contracts` wiring (§7).
- New, not yet on `docs/open-decisions.md`: KXATPMATCH (and, on thinner
  data, KXUFCFIGHT) as `strategy.excluded_series` candidates (§6).
