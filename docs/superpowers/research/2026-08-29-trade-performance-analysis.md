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

## 0. CORRECTIONS (independent verification, 2026-08-30) — READ FIRST

This document was verified claim-by-claim against the live data after it was
written, and every corrected figure below was then recomputed a THIRD time
from the raw 257-trade slice and its derived arithmetic checked in Wolfram —
not taken on the verifier's word (the same mistake in the other direction).
Reproduced exactly: locked_loss 24 / -$2,210.94 and variable 10 / -$483.05;
the ten printed bars $28.70-$114.62 => vol_ratio 0.574-2.2924, provably never
touching either clamp bound (0.25 / 4.0); 11 negative non-crypto series summing
-$4,260.36; ATP -$818.99 total = -$1,339.38 netting + $520.39 non-netting;
union(ATP, netting) = -$2,173.60, and the identity
atpTotal + nettingTotal - atpNetting == unionTotal holds exactly; ceiling slice
n=25, 84.0% win, -$155.85. The naive component sum is +$3,668.83, of which
$1,339.38 is double-counted, giving a true ceiling of **+$2,329.45** - so the
body's "+$3,000-3,500" overstates by $670-$1,170.

**§1, §2, §3, §6's headline split, §10's raw correlations, and §11's
dead-config pass all reproduce exactly and can be trusted.** The following
claims were WRONG, and three of them were load-bearing for config changes
that were already applied. Corrections here override the body text below.

**C1 (HIGH, safety). `risk.max_daily_loss_pct` is 0.8, NOT 0.20.** §12/§13
present 0.20 as applied and rank it the #1 watch item. The audit trail
(`data/config_performance.db`): applied 0.85→0.2 at 02:25:52 with the rest of
the change-set, then **0.2→0.8 at 02:32:21 by a separate manual change** (not
part of this analysis). Live `config/settings.yaml` and `GET /api/config` both
read 0.8. CLAUDE.md already calls 0.85 "not protective"; 0.8 is not materially
different, so the kill switch remains effectively non-protective and the
Tier-1 watch item as written watches a threshold that does not exist. The real
allowance is ~$9,150/day, not ~$2,460. **The other 6 of 7 applied fields
verified correct.**

**C2 (HIGH). "All 34 netting closes were `locked_loss`" is FALSE: 24 are, 10
are `variable`.** The 10 variable trims cleared EV bars of $28.70–$114.62 and
were **net −$483.05**. This falsifies §12's stated justification for the
applied `min_edge_improvement_usd` 50→10 ("a $50 floor plausibly never
cleared" — it cleared 10 times), and inverts its direction: the variable-stage
trims that did fire LOST money, so admitting more of them is not supported by
this history. §13's "falsifiable prediction #5" was already falsified when
written.

**C3 (HIGH). The netting volatility clamp is NOT pinned at its 0.25 floor.**
§13's "new finding" (and its `open-decisions.md` line) is false. The bar is
printed in every variable `exit_reason`: $28.70, $46.60, $58.35, $59.49,
$60.97, $63.99, $68.78, $100.03, $103.02, $114.62 — against `min_edge 50` that
is `vol_ratio` 0.574–2.29, never at the floor, never at the ceiling. The
evidence was in the same rows this document already used.

**C4 (MED-HIGH). "Every other sports series nets positive (+$1,388.96
combined)" is FALSE.** That figure is a residual (total minus the two named
series), not a per-series fact. **Eleven** non-crypto series are negative,
totaling −$4,260.36 (beyond ATP/UFC: KXEPLTOTAL −$420.66, KXEPLBTTS −$406.80,
KXNCAAFTOTAL −$395.85, KXT20MATCH −$340.47, KXSERIEA1H −$326.27, KXEPLGAME
−$301.41, KXNCAAFGAME −$242.85, KXFEDDECISION −$79.11, KXMLBGAME −$16.25).
This framing supported the applied `excluded_series` decision.

**C5 (HIGH). The "+$3,000–3,500 better on replay" figure exceeds its own
arithmetic ceiling.** Its two components double-count: ATP's own netting
closes (−$1,339.38, n=15) sit in BOTH "+$819 ATP exclusion" and "up to +$2,694
netting mitigation"; the union of the two trade sets is −$2,173.60. Even
assuming perfect mitigation the doc calls partial, the ceiling is **+$2,329**.
Worse for the applied change: conditional on the caps fixing netting,
excluding ATP removes ATP's **non-netting** trades, which are **+$520.39**
(n=8) — so `excluded_series: [KXATPMATCH]` has NEGATIVE marginal value on this
book once the caps are in place.

**C6 (LOW, favors the change). The ceiling trim's direction is backwards in
§12.** The slice `max_unit_cost: 0.85` removes (unit_cost > 0.85, n=25, 84.0%
win) is −$155.85 — the trim would have ADDED ~$156, not forgone ~$200.

**C7 (MED). §7's "exactly 11 fields, all under `strategy.*`" understates
advisory's reach.** The 11 hand-written paths are right, but
`advisory_engine.py:733/:841` also emit `strategy_overrides.by_category`/
`by_series`, and `:496`'s variant-comparison branch can emit ANY of the 38
fingerprinted `strategy.*` fields. The core finding survives —
`whale_watcher_kalshi`, `risk`, `position_netting` are genuinely unreachable,
and `whale_watcher.min_contracts` has **14,297,769** logged rejection events
with no map entry — but "every other section is structurally invisible"
overstates it for the other 27 strategy fields.

**C8 (MED — and it is CLAUDE.md's own "value must match its label" class).**
§10 says "high `depth_factor` printed correct less often (mean 0.21) than low
(mean 0.41)." Those are `mean(depth_factor | correct)` and
`mean(depth_factor | incorrect)` — group means OF THE FACTOR, not correctness
rates. The actual correctness rates are 0.403 (depth ≥0.9) vs 0.691 (depth ≤0.1).

**C9 (HIGH for framing). §10's depth_factor conclusion does not survive a
series control.** `depth_factor ≈ 1.0` is 62% `KXMVECROSSCATEGORY` (n=12,631,
33.3% correct) plus KXBTC15M — structurally low-volume series where
`depth_ratio` saturates; low-depth rows are tennis/MLB (66–82% correct).
Within-series r: depth **−0.092** (pooled −0.244), unusualness **−0.206**
(pooled −0.172), context +0.068, cluster +0.067, agreement +0.056, trend
+0.169. **Within series, `unusualness_factor` is the strongest factor, not
depth** — so §10's "strongest effect of any factor, never checked before"
headline is confounded by series mix. No weight was changed, so no decision
broke, but the `open-decisions.md` line overstated its evidence.

**C10 (MED). `KXTRUMPMENTION` is not unreachable** (§11): it is in this very
book (+$182.90, settled_win). The same reasoning was applied to KXTRUMPSAY /
KXMAMDANIMENTION and is unverified for them.

**C11 (LOW). §11's longshot scanner reasoning is wrong** (right conclusion):
all three longshot fields ARE flagged unread by the scanner and sit in the
accepted baseline — they are read off `strat_cfg`, an intermediate the v1
scanner cannot follow. The dead-mechanism conclusion itself is confirmed
(2,432 `min_unit_cost` rejections, zero longshot-gated entries).

**C12 (LOW). Snapshot boundary.** This document pins no cutoff, so re-running
its own queries today returns a larger book. Its numbers are the **oldest 257
trades by exit timestamp** (last exit ts 1788058891). Also: 256 of 257 share
one `config_fingerprint` — one row has null entry_price/cost_basis and is
silently excluded from the $91,610.29 deployed total.

**Consequence for the applied change-set:** `min_edge_improvement_usd` 50→10
(C2, C3) and `excluded_series: [KXATPMATCH]` (C4, C5) both rest on
contradicted premises and should be reconsidered; `max_daily_loss_pct` is not
what this document says (C1). `max_unit_cost` 0.85, the two concentration caps
and the KXBTC15M override are unaffected by these corrections.

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
narrow an observed range to say which factor weight is mistuned from the
blended score alone. **Superseded by §10**: the per-factor breakdown
(`signal_log.resolved_signals_with_factors()`, 88,828 rows) answers this
directly — this dataset's boundary was a tool-choice problem, not a real
data ceiling.

**`kalshi.markets_watchlist: [KXBTC15M]`** — deliberately overrides
`kalshi.live_markets_only`'s discovery filter for this one series (direct
2026-08-16 request), because Kalshi has no broadcast/game-clock live-status
signal for a pure price-crossing market and its `volume_24h` stays
structurally 0 while open. Not a bug in the three separate
`live_markets_only` flags (`kalshi`/`whale_signal`/`strategy` — different
pipeline stages, each documented) — this is the actual mechanism behind
§6's crypto finding above: without it, the book's whole profit source would
never be discovered.

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
- New, not yet on `docs/open-decisions.md`: `whale_confidence_weights`
  re-validation (§10) - `depth_factor`'s never-checked negative correlation,
  and `agreement_factor`'s apparent reversal from the 2026-08-10 finding.

## 10. `whale_confidence_weights`, checked at 88,828 resolved signals

Direct follow-up: "you're going to want to do another analysis considering
all the factors and setting you've missed." §6's dismissal of
`whale_confidence_weights` (blended `entry_confidence` too narrow a range
to say anything) used the wrong tool. `signal_log.resolved_signals_with_factors()`
- found early this session, never actually used until this pass - returns
every resolved signal with a real per-factor breakdown, independent of
whether it became a whale-follow trade: 88,828 rows, 345x the §1-§6
dataset.

Pearson correlation of each raw factor value against the signal's own
`correct` outcome:

| Factor | Live weight | r | Note |
|---|---|---|---|
| depth_factor | 0.0404 | **-0.24** | Strongest effect of any factor - never checked before this pass |
| unusualness_factor | 0.0404 | -0.18 | Confirms the 2026-08-10 finding at n≈9,200, now 10x the sample |
| trend_factor | 0.3131 | +0.24 | Heaviest weight, strongest positive signal - well aligned |
| context_factor | 0.2121 | +0.23 | Second-heaviest weight, matching signal strength |
| cluster_factor | 0.1717 | +0.17 | Aligned |
| agreement_factor | 0.1818 | +0.13 | Contradicts the 2026-08-10 finding's sign - see below |
| proximity_factor | 0.0404 | -0.05 | Weak either way; correctly weighted low; only 21,843/88,828 rows populated |
| analyst_factor | 0.0000 | n/a | 0 non-default values in 88,828 rows - weight of 0 already correct |

**`depth_factor` is the largest-magnitude effect in the table, and its
formula's implicit assumption is backwards.**
`services/confidence_scoring.py:193`: `depth_factor = 1.0 - math.exp(-k *
depth_ratio)` - a print LARGE relative to the market's own 24h volume scores
HIGH confidence. In the data, high `depth_factor` printed correct less often
(mean 0.21) than low `depth_factor` (mean 0.41). It already sits at the
config's floor weight (0.0404, cut from `DEFAULT_WEIGHTS`' 0.18 -
`confidence_scoring.py:115`), so today's practical drag is bounded by that
low weight - but nothing in the codebase's own history (checked: the
2026-08-10 finding named in the function's docstring covers only
`unusualness_factor` and `agreement_factor`) shows this was ever
deliberately identified, only that it happens to already sit at the floor
alongside the two factors that were.

**A broken doc pointer, and a possible reversal.**
`confidence_scoring.py`'s docstring says the 2026-08-10 finding is explained
"in config/settings.yaml's whale_confidence_weights comment" - that section
(`config/settings.yaml:131-140`) carries no comment at all; verified by
direct read, not inferred from the docstring being wrong elsewhere. More
substantively: that finding says `agreement_factor` showed negative
discrimination against ~9,200 real resolved signals. This pass's 88,828 rows
show `r = +0.13` - the opposite sign. No newer documented recalibration was
found in git history, ROADMAP.md, or `services/whale_calibration/` to
explain the reversal. Left unresolved deliberately: the two candidate
explanations (the underlying relationship changed with 10x the data since
2026-08-10, or something in how `agreement_factor` is computed/consumed
changed since then) point to different next actions, and this dataset alone
can't distinguish them.

**Also checked, inconclusive:** position size (`cost_basis`, driven by
`strategy.max_position_pct` and `kelly_fraction_of_cap`) against outcome
across the 257 real trades. Quartile ROI: 5.6% / 2.7% / -2.6% / 14.8% - not
monotonic, most likely confounded with the unit-cost and crypto/sports
effects already isolated in §2 and §6 rather than an independent size
effect. Reported as checked-and-null, not silently omitted.

**Not a recommendation for new weight values.** Re-weighting a
signal-detection formula is a materially bigger change than a threshold
value - what this section supports is re-running
`services/whale_calibration/confidence_calibration.py`'s discrimination
check against the current 88,828-row population (10x what it last saw) and
writing the settings.yaml comment that's supposed to exist, not swapping in
numbers derived from a single ad hoc correlation pass.


## 11. Every field, individually (145/145) — the complete pass

Direct follow-up: "you skipped analysis of 140 fields. run a complete
analysis again." §6 categorized 23 *sections*; this is every *field*,
checked one at a time, generated from a single flattened inventory
(`yaml.safe_load` over `config/settings.yaml`, 145 leaves) so nothing is
silently dropped between report and doc.

**Mechanical dead-config pass first.** Ran `tools/quality_audit`'s own
`config_usage.scan_config_usage()` directly (not the full multi-scanner CLI,
which chokes on this worktree's own nested test files — a real, separate,
tangential finding: the architecture-audit scanner doesn't exclude
`.claude/worktrees/`, so any live worktree pollutes a from-root run).
120 of 145 fields have no direct `cfg.get(...)` read the v1 scanner can
trace; diffed against `tools/quality_audit/baseline.json`'s
`accepted_finding_ids` — **all 120 are already reviewed and dated** (the
documented intermediate-variable limitation, e.g. `strat_cfg = cfg.get(...)
or {}`). Zero fields are genuinely unreviewed dead config.

**Category counts:** 25 data-checked, 27 source-verified, 7 inert (read,
but structurally unreachable given a sibling gate), 31 checked with a real,
named boundary, 55 structural/infra (each with a field-specific reason, not
a bucket).

**The sharpest new finding: the longshot mechanism is dead.**
`strategy.longshot_price_threshold` (0.05): `is_longshot = price <= 0.05 or
price >= 0.95` in `services/strategy_engine.py` — checked against raw yes
price. But `unit_cost = price if side=="yes" else 1-price`, so every price
in that zone maps to `unit_cost <= 0.05` on whichever side is cheap, and
`min_unit_cost: 0.5` rejects it unconditionally (`strategy_engine.py`'s gate
has no longshot exception). `longshot_entry_threshold_bonus` (0.05) is
therefore never applied, and `longshot_close_window_sec` (300) never
matters. Not caught by the scanner — all three fields *are* read; the
scanner can't see cross-field reachability, only presence of a read.

**Also resolved from an earlier hedge:** `event_lifecycle.*`
(`tournament_min_siblings`/`tournament_pretail_days`/
`pre_tail_volume_weight`/`post_tail_volume_weight`) was flagged in §6 as
"plausibly relevant to `position_netting`'s grouping, not verified." Checked
directly: it feeds only `services/market_events/event_lifecycle.py` and
`services/market_watch/discovery_cache.py` — a discovery/catalog concern.
`position_netting.find_groups()` uses Kalshi's own `event.mutually_exclusive`
flag directly, an entirely separate mechanism. The hypothesized connection
does not exist.

**Full table (source: `full_field_table.py`, one generator, two outputs —
this table and the artifact's are the same data):**

| Field | Value | Status | Note |
|---|---|---|---|
| `mode` | paper | Structural / infra | Safety mode switch, not a tuning value — protected by CLAUDE.md. |
| `kalshi.base_url` | … | Structural / infra | Fixed API endpoint. |
| `kalshi.markets_watchlist` | [KXBTC15M] | Source-verified | Deliberate 2026-08-16 override of live_markets_only for crypto — the actual mechanism behind the whole book's profit source. |
| `kalshi.markets_watchlist_mode` | merge | Structural / infra | Governs how the pin combines with discovered scope. |
| `kalshi.watchlist_size` | 150 | Structural / infra | Discovery cap, not a P&L lever directly. |
| `kalshi.max_children_per_parent` | 5 | Structural / infra | Catalog-expansion cap. |
| `kalshi.min_volume_24h` | 10000 | Structural / infra | Discovery volume floor. |
| `kalshi.min_volume_24h_by_series.KXBTC15M` | 0 | Source-verified | Part of the same crypto-override mechanism as markets_watchlist — a pure price-crossing market's volume_24h stays structurally 0 while open. |
| `kalshi.categories` | [Sports] | Data-checked | The reason the real book is Sports + explicit-watchlist-crypto only — foundational to the crypto-vs-sports finding. |
| `kalshi.live_markets_only` | true | Source-verified | Discovery-stage gate — a different pipeline stage from strategy.live_markets_only, not a conflict. |
| `kalshi.poll_interval_sec` | 6 | Structural / infra | Data-plane cadence — this session's other workstream. |
| `kalshi.safety_net_interval_sec` | 30 | Structural / infra | Data-plane, P8 Task 39 — already shipped and explained this session. |
| `kalshi.request_timeout_sec` | 10 | Structural / infra | Infra. |
| `kalshi.trade_stream_exchange_wide` | true | Structural / infra | Data-plane — foundational to exchange-wide whale detection. |
| `kalshi.market_lifecycle_stream_enabled` | true | Structural / infra | Data-plane — the settlement-resolver work this session depends on this being true. |
| `kalshi.top_series_per_category` | 30 | Structural / infra | Discovery cap. |
| `realtime_data_plane.reader_gate_enabled` | false | Source-verified | This session's own initiative — fully covered elsewhere. |
| `realtime_data_plane.two_consumer_mode` | true | Source-verified | This session's own initiative — fully covered elsewhere, currently soaking. |
| `whale_signal.signal_frequency_sec` | 12 | Checked, boundary | Simulator-only config — confirmed none of the 257 real trades came from it. |
| `whale_signal.whale_size_range` | [5000, 50000] | Checked, boundary | Simulator-only. |
| `whale_signal.bias` | random | Checked, boundary | Simulator-only. |
| `whale_signal.live_markets_only` | false | Source-verified | Feeds whale_simulator.py only — documented as "a stronger, upstream version of strategy.live_markets_only". |
| `strategy.name` | follow_the_whale | Structural / infra | Identifier. |
| `strategy.entry_threshold` | 0.55 | Data-checked | entry_confidence clusters 202/256 trades in 0.50–0.60, right above this — narrow observed range. |
| `strategy.max_position_pct` | 0.05 | Data-checked | Position-sizing check: no clean pattern by size quartile — checked, inconclusive. |
| `strategy.cooldown_sec` | 60 | Checked, boundary | Re-entry throttle — no clean signal available this pass on whether it binds. |
| `strategy.close_window_sec` | 2764800 | Checked, boundary | Structural window bound, not independently checked against outcome. |
| `strategy.special_market_min_seconds_to_close` | 120 | Checked, boundary | Advisory-reachable gate field; no rejected-candidate data pulled this session. |
| `strategy.max_open_positions_per_series` | 0 (unlimited) | Data-checked | Recommended → 2–3 — the actual root cause of position_netting's losses. |
| `strategy.kelly_fraction_of_cap` | 0.3 | Data-checked | Position-sizing check: no clean pattern — checked, inconclusive. |
| `strategy.min_unit_cost` | 0.5 | Data-checked | Extensively checked (unit-cost banding) — also the reason the longshot mechanism below is dead. |
| `strategy.max_unit_cost` | 0.9 | Data-checked | Recommended → 0.85 — the only net-negative unit-cost slice sits exactly at this ceiling. |
| `strategy.min_whale_winrate_pct` | 40 | Checked, boundary | Advisory-reachable gate; not independently checked against real rejected-candidate data this pass. |
| `strategy.min_resolved_for_whale_filter` | 20 | Checked, boundary | Threshold for when the above gate activates — not independently checked. |
| `strategy.live_markets_only` | false | Source-verified | The real strategy-level entry gate — distinct pipeline stage from kalshi's and whale_signal's same-named flags. |
| `strategy.excluded_series` | [] | Data-checked | Recommended → + KXATPMATCH — already inside advisory_engine's own reach. |
| `strategy.take_profit_pct` | null | Checked, boundary | Disabled; advisory-reachable if enabled — no live trades to check it against. |
| `strategy.stop_loss_pct` | null | Checked, boundary | Disabled; advisory-reachable if enabled. |
| `strategy.exit_on_sentiment_reversal` | false | Inert (sibling gate) | Disabled — the two sentiment fields below are configured but never exercised while this is off. |
| `strategy.longshot_close_window_sec` | 300 | Inert (sibling gate) | Part of the longshot mechanism — see longshot_price_threshold. |
| `strategy.exit_sentiment_min_signals` | 15 | Inert (sibling gate) | Configured, but exit_on_sentiment_reversal is false — currently never exercised. |
| `strategy.exit_sentiment_lean_pct` | 90 | Inert (sibling gate) | Same — inert while the parent flag is off. |
| `strategy.auto_exit_enabled` | true | Data-checked | auto_exit closes: 37 trades, 97.3% win, +$5,475.47 — the book's single strongest performer by close type. |
| `strategy.auto_exit_threshold` | 0.85 | Data-checked | Drives the result above; not independently decomposed. |
| `strategy.auto_exit_pnl_weight` | 0.85 | Checked, boundary | Internal weighting of a strong aggregate result — per-trigger attribution isn't in trade_history rows. |
| `strategy.auto_exit_sentiment_weight` | 1.5 | Checked, boundary | Same. |
| `strategy.auto_exit_staleness_weight` | 0.5 | Checked, boundary | Same. |
| `strategy.auto_exit_analyst_weight` | 0.5 | Checked, boundary | Same. |
| `strategy.auto_exit_gain_reference_pct` | 0.15 | Checked, boundary | Same. |
| `strategy.auto_exit_loss_reference_pct` | 0.85 | Checked, boundary | Same. |
| `strategy.auto_exit_stale_after_sec` | 7200 | Checked, boundary | Same. |
| `strategy.auto_exit_normal_volatility` | 0.002 | Checked, boundary | Same. |
| `strategy.auto_exit_volatility_lookback_sec` | 1800 | Checked, boundary | Same. |
| `strategy.auto_exit_series_track_record_weight` | 0 | Source-verified | The only auto-exit weight at literal zero while every sibling is nonzero — an unused weight slot, same shape as analyst_factor/block_trade_factor elsewhere. |
| `strategy.longshot_price_threshold` | 0.05 | Source-verified | Dead: is_longshot fires on raw price ≤0.05 or ≥0.95, but every price there maps to unit_cost ≤0.05 on whichever side is cheap — min_unit_cost (0.5) rejects it unconditionally, independent of any longshot bonus. |
| `strategy.longshot_entry_threshold_bonus` | 0.05 | Inert (sibling gate) | Never applied — the longshot zone it modifies is unreachable (see longshot_price_threshold). |
| `strategy.use_limit_orders` | false | Inert (sibling gate) | Disabled — limit_order_timeout_sec below is currently inert as a result. |
| `strategy.limit_order_timeout_sec` | 60 | Inert (sibling gate) | Inert while use_limit_orders is false. |
| `strategy.min_seconds_to_close` | 90 | Checked, boundary | Structural timing gate, not independently checked. |
| `strategy.exit_min_seconds_to_close` | null | Checked, boundary | Same. |
| `strategy.price_staleness_corroborate_sec` | 120.0 | Source-verified | This session's own realtime work (P8 Task 35) — already shipped and understood, not a trade-outcome question. |
| `risk.starting_bankroll` | 10000 | Structural / infra | Paper-mode baseline. |
| `risk.max_daily_loss_pct` | 0.85 | Source-verified | Already named in CLAUDE.md itself as not protective — restated, not re-derived. |
| `risk.kill_switch_enabled` | true | Structural / infra | Safety feature — on is correct, not a tuning question. |
| `risk.max_total_exposure_pct` | null (unset) | Data-checked | Recommended → 0.25–0.35 — the second concentration cap sitting off alongside max_open_positions_per_series. |
| `kalshi_account.trading_enabled` | false | Structural / infra | Safety invariant — never a tuning lever. |
| `whale_watcher_kalshi.min_contracts` | 10000 | Data-checked | The global floor — crypto's separate 2,500 floor is what actually explains the crypto/sports split. |
| `whale_watcher_kalshi.min_contracts_by_series.KXBTC15M` | 2500 | Data-checked | Live and reachable — the crypto profit driver. |
| `whale_watcher_kalshi.min_contracts_by_series.KXBTCD` | 2500 | Data-checked | Live and reachable, same series family. |
| `whale_watcher_kalshi.min_contracts_by_series.KXETH15M` | 2500 | Source-verified | Unreachable: ETH is in neither kalshi.categories nor kalshi.markets_watchlist — never discovered, confirmed zero occurrences in 257 real trades. |
| `whale_watcher_kalshi.min_contracts_by_series.KXETHD` | 2500 | Source-verified | Same — unreachable dead config given current discovery scope. |
| `whale_watcher_kalshi.min_contracts_by_series.KXTRUMPSAY` | 500 | Source-verified | Unreachable — not in categories or watchlist. |
| `whale_watcher_kalshi.min_contracts_by_series.KXTRUMPMENTION` | 500 | Source-verified | Unreachable, same reason. |
| `whale_watcher_kalshi.min_contracts_by_series.KXMAMDANIMENTION` | 500 | Source-verified | Unreachable, same reason. |
| `whale_confidence_weights.depth_factor` | 0.0404 | Data-checked | r=−0.24 at n=88,828 — strongest effect of any factor, never checked before, formula's assumption runs backward. |
| `whale_confidence_weights.unusualness_factor` | 0.0404 | Data-checked | r=−0.18 — confirms the 2026-08-10 finding at 10× the sample. |
| `whale_confidence_weights.proximity_factor` | 0.0404 | Data-checked | r=−0.05, weak either way, correctly weighted low; only 21,843/88,828 rows populated. |
| `whale_confidence_weights.context_factor` | 0.2121 | Data-checked | r=+0.23 — second-heaviest weight, matching signal strength. |
| `whale_confidence_weights.agreement_factor` | 0.1818 | Data-checked | r=+0.13 — contradicts the 2026-08-10 finding's sign; flagged, not resolved. |
| `whale_confidence_weights.cluster_factor` | 0.1717 | Data-checked | r=+0.17 — aligned. |
| `whale_confidence_weights.trend_factor` | 0.3131 | Data-checked | r=+0.24 — heaviest weight, strongest positive signal, well aligned. |
| `whale_confidence_weights.analyst_factor` | 0.0 | Data-checked | 0 real values across 88,828 rows — weight of 0 already correct. |
| `whale_confidence_weights.block_trade_factor` | 0.0 | Source-verified | Doesn't appear in the factor breakdown at all — a distinct, boolean-shaped signal (Kalshi's is_block_trade flag) rather than a weighted continuous factor. |
| `advisory.enabled` | true | Source-verified | Meta-control for the advisory_engine coverage gap (§7). |
| `advisory.min_resolved_trades_per_variant` | 10 | Checked, boundary | Governs a system that's made zero real auto-applies to evaluate. |
| `advisory.auto_apply_enabled` | false | Source-verified | Off by design — matches the standing surface-don't-auto-apply rule. Correct, not a gap. |
| `advisory.auto_apply_min_confidence` | higher | Checked, boundary | Inert while auto_apply is off. |
| `advisory.auto_apply_min_n` | 100 | Checked, boundary | Inert while auto_apply is off. |
| `advisory.auto_apply_cooldown_sec` | 86400 | Checked, boundary | Inert while auto_apply is off. |
| `confidence_calibration.enabled` | true | Source-verified | The mechanism that produced whale_confidence_weights' current live values (the 2026-08-10 finding) — directly relevant to the §10 recommendation. |
| `confidence_calibration.min_resolved_signals` | 50 | Source-verified | 88,828 real signals now exist — 1,776× this floor. Re-calibration has had more than enough data for a long time; nothing has re-triggered it. |
| `confidence_calibration.auto_apply_min_resolved_signals` | 50 | Checked, boundary | Inert while auto_apply is off. |
| `confidence_calibration.snapshot_interval_sec` | 21600 | Structural / infra | Cadence. |
| `confidence_calibration.auto_apply_enabled` | false | Source-verified | Off by design — correct, not a gap. |
| `confidence_calibration.auto_apply_cooldown_sec` | 86400 | Checked, boundary | Inert while auto_apply is off. |
| `market_analyst.enabled` | true | Checked, boundary | The LLM full-spectrum/per-market/per-series advisor — distinct from advisory_engine; no suggestion-acceptance data pulled this session. |
| `market_analyst.model` | claude-sonnet-5 | Structural / infra | Model selection. |
| `market_analyst.reanalyze_cooldown_sec` | 1800 | Structural / infra | Throttle. |
| `position_netting.enabled` | true | Data-checked | 34 closed trades, 11 wins, −$2,693.99 — extensively analyzed (§3). |
| `position_netting.min_dwell_sec` | 300 | Checked, boundary | Not independently checked — analysis focused on the materiality bar floor below. |
| `position_netting.min_edge_improvement_usd` | 50 | Data-checked | Recommended → $10–15 — code default is $1; all 34 observed closes were locked_loss, none a proactive variable-stage trim. |
| `position_netting.normal_volatility` | 0.02 | Checked, boundary | Scales the materiality bar above min_edge_usd — no live volatility data joined to trade history this pass. |
| `position_netting.volatility_lookback_sec` | 1800 | Checked, boundary | Same. |
| `index_feed.index_ids` | [BRTI, ETHUSD_RTI] | Structural / infra | A separate index-price feed, structurally unrelated to whale-follow trade outcomes. |
| `index_feed.underlying_tickers` | [] | Structural / infra | Same feed. |
| `settlement_edge_entry.enabled` | false | Structural / infra | Engine off — all 5 fields below correctly inert; no live trades to check any of them against. |
| `settlement_edge_entry.min_observations_known` | 45 | Structural / infra | Inert while disabled. |
| `settlement_edge_entry.min_probability` | 0.95 | Structural / infra | Inert while disabled. |
| `settlement_edge_entry.min_edge` | 0.05 | Structural / infra | Inert while disabled. |
| `settlement_edge_entry.max_position_pct` | 0.02 | Structural / infra | Inert while disabled. |
| `settlement_edge_entry.volatility_lookback_sec` | 3600 | Structural / infra | Inert while disabled. |
| `series_watcher.enabled` | true | Structural / infra | Passive order-book/history collection — a different concern from whale-follow trading, not trade-outcome-tunable. |
| `series_watcher.series` | [8 series] | Structural / infra | Watches some series (e.g. KXETH15M, KXNBAGAME) that never produce a whale-follow trade — legitimate: this is data-collection breadth, not a trading gate. |
| `series_watcher.book_snapshot_interval_sec` | 5 | Structural / infra | Collection cadence. |
| `series_watcher.retention_hours` | 168 | Structural / infra | Retention window. |
| `series_evaluator.enabled` | false | Structural / infra | Engine off — all 7 fields below correctly inert. |
| `series_evaluator.min_observation_sec` | 3600 | Structural / infra | Inert while disabled. |
| `series_evaluator.min_trades_observed` | 1000 | Structural / infra | Inert while disabled. |
| `series_evaluator.max_observation_sec` | 21600 | Structural / infra | Inert while disabled. |
| `series_evaluator.min_qualify_rate` | 0.05 | Structural / infra | Inert while disabled. |
| `series_evaluator.backoff_base_sec` | 3600 | Structural / infra | Inert while disabled. |
| `series_evaluator.backoff_multiplier` | 2 | Structural / infra | Inert while disabled. |
| `series_evaluator.backoff_max_sec` | 86400 | Structural / infra | Inert while disabled. |
| `logging.level` | INFO | Structural / infra | Infra. |
| `backup.enabled` | true | Structural / infra | Infra. |
| `backup.interval_sec` | 21600 | Structural / infra | Infra. |
| `backup.retention_count` | 14 | Structural / infra | Infra. |
| `alerting.enabled` | true | Structural / infra | Infra. |
| `alerting.webhook_url` | null (unset) | Source-verified | No external alert destination is wired — alerts fire and are visible in-app only, nobody is notified externally. |
| `alerting.crash_auto_resolve_after_sec` | 1800 | Structural / infra | Infra. |
| `observability.enabled` | true | Structural / infra | Infra — this session's own primary battlefield, covered extensively elsewhere. |
| `observability.sample_interval_sec` | 60 | Structural / infra | Infra. |
| `observability.retention_hours` | 336 | Structural / infra | Infra. |
| `research.enabled` | false | Structural / infra | Off until manually reviewed (CLAUDE.md's own note) — the 2 fields below correctly inert. |
| `research.min_new_resolved_signals` | 100 | Structural / infra | Inert while disabled. |
| `research.min_new_closed_trades` | 50 | Structural / infra | Inert while disabled. |
| `event_lifecycle.tournament_min_siblings` | 4 | Source-verified | Feeds market_events/event_lifecycle.py and discovery_cache.py only — NOT position_netting or mutual_exclusivity. The connection hypothesized earlier this session doesn't exist. |
| `event_lifecycle.tournament_pretail_days` | 5.0 | Source-verified | Same feed, discovery/catalog concern only. |
| `event_lifecycle.pre_tail_volume_weight` | 0.4 | Source-verified | Same. |
| `event_lifecycle.post_tail_volume_weight` | 0.2 | Source-verified | Same. |
| `event_schedule.enabled` | true | Structural / infra | Infra — event-schedule resolution. |
| `event_schedule.pre_event_hours` | 6.0 | Structural / infra | Infra. |
| `event_schedule.web_search_enabled` | true | Structural / infra | Infra. |
| `event_schedule.max_resolutions_per_tick` | 5 | Structural / infra | Infra. |
| `strategy_overrides.by_category.Sports.stop_loss_pct` | null | Data-checked | Confirmed no-op — matches the global default; the mechanism a Sports-specific recommendation would route through. |

## 12. Proposed full config change-set (surfaced, NOT applied)

Direct request: one coherent, strongly-confident change-set over every
tunable field, with cross-field interactions reasoned explicitly. Three
mechanics verified in source before proposing (not assumed):
`config_overrides.resolve()` layers `by_series` over strategy fields
BEFORE `evaluate()`'s gates run (`strategy_engine.py:260`), so per-series
carve-outs of any strategy field genuinely work at the entry path;
`check_total_exposure` compares against live CASH, not starting bankroll
(`risk_manager.py:148-150`), so a nominal pct binds at an effective
E <= B*p/(1+p); and real peak concurrency was measured from entry/exit
timestamps (overall 13 positions / $4,504.59 deployed; per-series peaks:
NFL 5, MLB 4, ATP 4, BTC15M 3, UFC 3).

```yaml
strategy:
  max_unit_cost: 0.85            # was 0.90
  max_open_positions_per_series: 2   # was 0 (unlimited)
  excluded_series: [KXATPMATCH]  # was []
risk:
  max_total_exposure_pct: 0.45   # was null; effective cap ~31% of equity
  max_daily_loss_pct: 0.20       # was 0.85; HUMAN-DECISION item, see note
position_netting:
  min_edge_improvement_usd: 10   # was 50 (code default is 1)
strategy_overrides:
  by_series:
    KXBTC15M:
      max_open_positions_per_series: 3   # preserves ALL historical crypto behavior
```

Interactions, the load-bearing part:

- The series cap (2) and the crypto override (3) were FITTED, not guessed:
  KXBTC15M's measured historical peak is exactly 3 concurrent (16-min
  median holds across consecutive 15-min windows), so the override
  preserves 100% of the book's profit engine while the global 2 trims the
  sports pileups (NFL 5 / MLB 4 / ATP 4) where netting groups form.
- The caps address N-way concentration, NOT 2-leg hedge pairs - a series
  cap of 2 still admits an opposing pair on one event. That residual is
  exactly what min_edge_improvement_usd 50->10 covers: all 34 observed
  netting closes were locked_loss (never a proactive variable-stage trim),
  and a $50 floor x volatility scaling (clamp 0.25-4.0 -> bar $12.50-$200)
  plausibly never cleared; at $10 the scaled bar is $2.50-$40.
- ATP exclusion and the caps overlap: part of ATP's -$818.99 is its own
  netting closes, which the caps also mitigate. Both are still proposed -
  exclusion is the certain lever on a 56.5%-win series, the caps are the
  mechanism-level fix; revisit re-admitting ATP only after the caps prove
  out. KXUFCFIGHT (n=6) is watch-only, not excluded.
- max_total_exposure_pct 0.45 nominal = ~31% effective (cash-relative
  formula above). Peak observed deployment ($4,504.59, ~45% of cash at the
  worst moment) WOULD have been trimmed - deliberately: that peak is the
  same concentration class as the 51-position/43.4% incident
  position_netting.py was built in response to. Under the new caps the
  plausible peak (~8-9 concurrent x ~$360 median) sits comfortably inside.
- max_unit_cost 0.85 removes the top half of the 0.80-0.90 band - the
  half where the fee-inclusive breakeven (85.9%->90.6%) is hardest to
  clear - and the exact-0.90 slice that was net negative. Historically
  roughly P&L-neutral (forgoes some positive 0.85-0.90 trades, ~+$200);
  the case is mechanism at scale, not backtest dollars.
- max_daily_loss_pct 0.20: the book has NO losing day to size from (2
  calendar days, worst day +$93.27), so this is derived from the exposure
  cap instead - a 0.20 switch fires before a worst-case wipe of the ~31%
  capped book completes. ROADMAP names kill-switch numbers a human
  decision; 0.20 is a proposed number for that decision, not a claim.

Deliberate NO-CHANGES, each a positive decision (every remaining tunable):

- whale_confidence_weights (all 9): NO re-weighting despite section 10's
  correlations, for a newly articulated reason - calibration's target is
  `correct` (win), and win-rate is NOT the objective: unusualness_factor's
  negative r with correctness coexists with its zone (prices near 0.5 =
  unit costs near 0.5) being the book's MOST profitable band (9.0% ROI at
  61.8% win). Re-weighting toward "predicts correct" would push entries
  toward the high-cost/high-win-rate thin-edge trap - the same win-rate
  blind spot already flagged for advisory_engine, one level down. The
  right fix is re-targeting calibration on EV-per-contract (the open
  banded-EV spec's territory), not new weights from these correlations.
- strategy.entry_threshold 0.55: conf 0.6-0.7 trades averaged $5.56 vs
  $20.92 for 0.5-0.6 - raising it would select WORSE trades by P&L;
  lowering is unmeasured territory. Keep.
- strategy.min_unit_cost 0.5: keep - lowering would revive the longshot
  mechanism and admit an entirely unmeasured price zone; the dead-longshot
  decision (section 11) stays a separate, explicit call.
- take_profit_pct / stop_loss_pct null: keep - auto_exit (97.3% win,
  +$5,475.47) is the active manager and its pnl weight already covers this
  ground; a hard stop-loss interacts badly with the known stale-price
  exit history. The $21k settled_loss tail is real but belongs to the
  banded-EV spec's entry-side fix, not an exit patch.
- auto_exit_* (12 fields): the book's best-performing mechanism;
  per-trigger attribution isn't in trade rows, so any tweak would be
  blind. Keep all.
- whale_watcher_kalshi floors: sports 10000 - no sports data below it
  exists to justify lowering, and size bands above it show no improvement
  (10-20k: -$7.52 avg); crypto 2500 - the profit engine, keep. ETH/mention
  floors are unreachable dead entries (separate cleanup decision). Keep.
- kalshi.categories [Sports] + watchlist [KXBTC15M]: adding ETH/Crypto
  category = new unmeasured exposure; not part of a "strongly confident"
  set. Keep.
- cooldown_sec, close_window_sec, min_seconds_to_close, special/exit
  timing gates, sentiment-exit fields (inert while parent flag off),
  limit-order fields (inert), min_whale_winrate_pct/
  min_resolved_for_whale_filter: no discriminating data pulled this
  session; every one is advisory-reachable or boundary-documented in
  section 11. Keep.
- realtime_data_plane, kalshi data-plane cadences, and all
  STRUCTURAL/infra fields from section 11: not trade-outcome tuning;
  two_consumer_mode stays in its soak. Keep.

Historical-book arithmetic for the set (honest bounds, not a promise):
ATP exclusion +$819; netting mitigation up to +$2,694 (partial - caps
kill the N-way class, the $10 bar addresses pairs); ceiling trim ~neutral
historically, positive by mechanism; exposure/daily caps cost nothing on
the observed book outside the deliberately-trimmed peak. Net: the same
book replayed under this config lands roughly +$3,000-3,500 better on
$5,283 actual - concentrated in loss-avoidance, which is also why it
can't simply be extrapolated (the avoided losses fund no new wins).

## 13. Change-set APPLIED (2026-08-29) + per-field suggestion column

The §12 change-set was applied on direct request via `POST /api/config`
(the same surfaced-then-approved path as `two_consumer_mode`): all 7
fields read-back verified, `strategy_overrides.by_category.Sports`
survived the nested-merge footgun (checked explicitly), config persisted
to `config/settings.yaml` on disk, app running, kill switch not tripped,
`dropped_window` 0 after apply. The pre-apply settings.yaml state is
recoverable from this doc's own tables and the config change-history log
(`config_performance.log_applied_change` recorded each field).

The generator now emits a per-field **Suggestion** column across all 145
fields (applied / keep-with-reason / decision-open), rendered in the
artifact's full table. Every `position_netting` field carries an explicit
verdict, answering "why no change suggestions to netting" directly: its
binding lever (min_edge $50→$10) and both upstream root-cause caps WERE
the netting changes; `enabled` stays true because the module is the
tourniquet, not the wound.

**New finding from that per-field pass:**
`position_netting.normal_volatility` (0.02) baselines the same
`market_history.volatility()` measure as
`strategy.auto_exit_normal_volatility` (0.002) at 10× the value. At 0.02,
typical real vols (~0.002 scale, per auto_exit's own tuning history) give
`vol_ratio ≈ 0.1`, clamped to the 0.25 floor
(`position_netting.py:250`) — the netting bar's volatility scaling has
plausibly been pinned at its clamp floor the entire time, never actually
varying. Deliberately NOT changed with the rest: moving it to 0.002 now
would raise the effective bar (~$2.50 → ~$10) and trim less — the
opposite of the applied direction. The right fix is measuring the real
volatility distribution first, then aligning both baselines to it.

Post-apply watch items, ranked by consequence. Items 1-4 are mandatory;
5-7 are cheap because the counters already exist; 8 is a standing caveat.

**Tier 1 - can silently stop everything**

1. **Kill-switch trip.** `max_daily_loss_pct` went 0.85 -> 0.20, a 4x
   tightening, on a book with NO losing day to calibrate from (worst
   observed day: +$93.27) - the number was derived from the exposure cap,
   not from data. If it trips, all trading halts and the only signal is
   `halted: true` in `/api/state`. At the current $12,296 bankroll that is
   a ~$2,460 daily drawdown; the worst single observed netting pair was
   ~$700 combined, so 3-4 bad cascades in one session reaches it. This is
   the highest-consequence field in the change-set and nothing watched it
   before this line. Check `risk.halted` / `halt_reason` every time.

**Tier 2 - the changes may be silently doing nothing, or too much**

2. **Crypto entry volume specifically.** KXBTC15M's `by_series` override
   of 3 is fitted to that series' exact historical peak. If
   `config_overrides.resolve()` is not reached on the live entry path, or
   the series key does not match what `signal_log.series_of()` derives,
   crypto entries quietly drop and the change-set kills the profit engine
   (+$5,624 of the book's +$5,283). The most expensive way to be wrong.
3. **Combined entry-rate effect.** Four tightenings (max_unit_cost 0.85,
   series cap 2, ATP exclusion, exposure cap) were modeled INDIVIDUALLY,
   never jointly. Their intersection could cut entries far more than the
   sum of the per-field reasoning. Watch entries/day vs the pre-change
   baseline.
4. **Exposure-cap rejections.** `max_total_exposure_pct` was null and is
   now binding for the first time; peak observed deployment (~45% of cash)
   WOULD have been trimmed. If it rejects constantly, 0.45 is too tight.

**Tier 3 - falsifiable predictions**

5. **Netting closes should change SHAPE, not just count.** The $50->$10
   bar predicts `variable`-state trims start appearing; all 34 historical
   closes were `locked_loss`. Still 100% locked_loss => the bar was not
   the binding constraint, and item 6 is the likely reason.
6. **Whether the netting materiality bar actually varies.** Section 6
   found `position_netting.normal_volatility` (0.02) is 10x
   `auto_exit_normal_volatility` (0.002) against the same measure, pinning
   `vol_ratio` at its 0.25 clamp floor. If the observed bar is always
   exactly `min_edge * 0.25`, that is confirmed - and it is a code fix,
   not config.
7. **Fee ratio.** Fees are maximal at unit_cost 0.50
   (`0.07 * p * (1-p)`), which is also the most profitable band, so
   trimming the top of the range concentrates the book into the
   highest-fee zone. Baseline 2.7% of deployed capital ($2,487.80 /
   $91,610.29) - watch it drift.
8. **Does the crypto edge persist?** The whole thesis rests on 146 trades
   in one series family over ~2 days: `moderate` confidence by this
   repo's own `confidence_label` convention, not a settled edge.

**Deferred until the event-scoped ME gate ships** (spec/plan in PR #202,
revision 2): `me_gate_evaluated_total` / `me_gate_blocked_total` /
`positions_with_event_ticker` coverage, and the blocked-flip held-leg P&L
(that spec's section 4.4) - a strict gate always converts a hedge into a
lingering single loss under today's code, and that is the data the
softer-variant decision needs.
