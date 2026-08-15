# TODO: heuristics/advisory audit + exit-tuning findings (2026-08-14)

Direct request: deep bug scan after CI broke, make the whale-follow close
window configurable (done, see below), audit auto-exit/whale-watch bid
issues, and explain why whale-follow's ~68% win rate nets almost no P&L.
Plus an 8-angle code review of the "heuristics and suggestions" subsystem
(`advisory_engine.py`, `confidence_calibration.py`,
`market_strategy_calibration.py`, `series_evaluator.py`,
`candidate_log.py`, `regime_analytics.py`, `cross_strategy.py`,
`calibration_history.py`, `config_performance.py`, `backtest.py`,
`stats_power.py`). This doc is the detail; `ROADMAP.md` has the one-line
pointer per this repo's usual split.

## Already fixed this session (see git log / commit for the exact diff)

- `strategy_engine.py`'s close-window was a hardcoded `_MAX_CLOSE_WINDOW_SEC`
  constant with no config knob (direct complaint: "shouldn't be named or
  hardcoded like that"). Now `strategy.close_window_sec` (default 7200),
  with the constant kept only as the fallback for cfg dicts that don't set
  it. Same fix applied to a second, previously undiscovered instance of the
  identical pattern: `special_market_min_seconds_to_close` was also a
  `.get(key, 300)` default with no real config entry - now
  `strategy.special_market_min_seconds_to_close: 300` in `settings.yaml`.
- CI (`.github/workflows/tests.yml`) has been failing since
  `test_e2e_terminal_static_and_api.py` was added (commit `f53a8ad`) -
  every run failed at collection on a missing `requests` dependency, never
  actually reaching the test body. Added `requests` to
  `requirements-dev.txt`, which surfaced the real underlying issue:
  `test_static_index_served_from_web_container` depends on ddev's
  docker-compose network (`http://web/`), which doesn't exist on a bare
  `ubuntu-latest` runner and never will. Added a `socket.gethostbyname`
  guard that skips just that one test outside ddev; the network-free sibling
  test (`test_api_state_available_and_shapes`, plain `TestClient`) is
  unaffected and always ran fine.
- `advisory_engine.py`'s take-profit recommendation (`_exit_pct_recommendation`)
  averaged `left_on_table` - a whole-position **dollar** total - divided by
  a flat 100, mislabeled as "cents/contract", then added directly onto
  `take_profit_pct` (a cost-basis **fraction**) with no normalization by
  position size. One large trade could dominate the average and produce an
  absurd suggested value with a one-click Apply button. Fixed to normalize
  by `cost_basis` per trade first, matching the stop_loss branch's existing
  convention. Test updated (`test_take_profit_recommendation_suggests_higher_value`)
  since it had encoded the buggy math as the expected value.
- `advisory_engine.py`'s `_GATE_CONFIG_PATH_AND_DIRECTION` allowlist was
  missing two whale-follow gates that had been logging real rejections
  since they shipped (`close_window`, `special_market_gate`) - their
  counterfactual data was captured and never surfaced. Added both. A third
  missing gate, `whale_watcher`/`min_notional_usd`, was deliberately **not**
  added - see "Deferred" below, it needs more than a map entry.
- `confidence_calibration.py`'s `_bucket_win_rates` near-constant-factor
  guard deduplicated factor values via exact float equality
  (`{r["factors"][factor_name] for r in sorted_rows}`), which binary
  floating-point jitter around one real value can defeat (two
  conceptually-identical scores landing as different floats), letting noise
  masquerade as real variance and feed a spurious `discriminates: true`
  into auto-apply. Fixed to round to 6 decimals before dedup.

All fixes covered by the existing suite plus one new test
(`test_close_window_is_configurable_not_hardcoded`). 735 tests passing.

## Urgent, not yet acted on: market_native strategy has lost 98.9% of its paper bankroll

`GET /api/market-strategy/state` → bankroll **$110.23** of a $10,000
starting bankroll. Cross-checked independently via
`GET /api/cross-strategy/comparison`: 4,860 closed trades, **23.6% win
rate**, **-$9,805.87** total realized P&L. Never halted, because
`risk.max_daily_loss_pct` (currently 1.0 = 100% for this strategy) only
measures loss *within a calendar day* against that day's starting bankroll
- there's no cumulative-drawdown floor, so a strategy can bleed out slowly
across many days without ever tripping the kill switch. This is a live,
data-confirmed illustration of the exact gap `ROADMAP.md`'s "Path to
production" section already names (real position-sizing/kill-switch
numbers needed before real capital). Two independent decisions needed:
1. Pause/reset `market_native` now, or let it keep running as a live
   negative-control data point?
2. Add a real cumulative-equity floor (e.g. halt if `equity <
   starting_bankroll * (1 - max_total_loss_pct)`), separate from the
   existing daily-reset check, for both strategies.

## The "68% win rate, pennies of profit" investigation - answered, not a bug

Pulled all 605 closed whale-follow trades via `/api/trading-history`
(paginated, `limit=200`). Settled trades split cleanly:
- **Wins** (366): entry price median 0.565, **P&L median +23.6% of cost
  basis** (mean +39.5%, up to +370%) - capped by how much room price had
  left to move.
- **Losses** (155): entry price median 0.490, **P&L consistently -100% to
  -106% of cost basis** (median -103.1%, essentially no variance) - a
  settled loss always costs the full stake plus fees.
- Settled-only net: +$9,864 wins vs. **-$10,631 losses** - barely negative
  on its own. `take_profit` closes (+$2,384 across 28 trades, all wins)
  are what push the total to net +$444.22 on $44,562 deployed (~1% return
  on capital - "pennies," accurately).

Mechanism: a strategy that wins most of the time by a variable, often
modest amount, and loses the rest of the time by (essentially) everything,
nets out close to breakeven even at a 68% win rate - basic asymmetric
payoff math, not a code defect.

**The literal "betting against myself" pattern is real** on 17 of 564
distinct tickers - same ticker, re-entered on the *opposite* side after the
first position auto-exited at a large loss:
```
KXWNBAGAME-26AUG13WSHLV-WSH: YES@0.34 -> auto_exit -$76.39, then NO@0.04 -> settled_win +$2.95  (net -$73.44)
KXNFLGAME-26AUG13ARILV-LV:   YES@0.55 -> auto_exit -$79.12, then NO@0.01 -> settled_win +$0.65  (net -$78.47)
KXMLBGAME-...MIN:            YES@0.51 -> auto_exit -$54.98, then NO@0.02 -> settled_win +$1.01  (net -$53.97)
KXNFLGAME-26AUG13DETCIN-DET: YES@0.3  -> auto_exit -$70.79, then NO@0.04 -> settled_win +$2.65  (net -$68.14)
```
Whale signal flips, the strategy chases it, eats a large auto-exit loss on
the way out, then "confirms" the new direction with a small position that
wins pennies - net loss on the ticker despite a technical second win. One
counter-example exists too (`KXATPMATCH-26AUG12SVAZHE-ZHE`: YES then NO,
both take_profit wins, +$115.59 and +$136.59) - a live match where momentum
genuinely reversed mid-game and the strategy correctly rode both legs, so
this *isn't* inherently broken behavior, just something worth deliberately
tuning (e.g. a per-ticker re-entry cooldown after an auto-exit loss, or
requiring materially higher confidence to re-enter the opposite side of a
market just exited at a loss).

**Data that doesn't exist and would make future investigations like this
faster:**
1. No per-trade snapshot of which config regime was active *at exit*, only
   at entry (`config_fingerprint`). Today's `settings.yaml` changes (stop-
   loss off, auto-exit off, weights rebalanced) mean every currently-open
   position will exit under different rules than it entered under, with no
   record of that mismatch afterward.
2. No breakdown of the auto-exit composite score's 4 weighted sub-factors
   (P&L/sentiment/staleness/analyst) at the moment of exit - only the final
   "closed: auto-exit ..." string. Can't tell which factor drove the 4
   whipsaw exits above without this.
3. No win-rate/P&L breakdown by `config_fingerprint` exposed anywhere -
   `compute_summary()` always aggregates the full history regardless of how
   many distinct config regimes it spans.

## Deferred: whale_watcher/min_notional_usd gate wiring

`kalshi_trade_tape.py:246` logs rejections under strategy key
`"whale_watcher"`, a third value `_rejected_candidate_recommendations`
doesn't handle - its `accepted_summary = whale_summary if strategy_key ==
"whale_follow" else market_summary` is a binary branch, and
`whale_watcher_kalshi` is a third config section `current_value`'s lookup
(`strat_cfg if section == "strategy" else market_cfg`) doesn't cover
either. This is this app's single highest-leverage whale-bid tuning knob
and its rejected-candidate counterfactual data (real, already accumulating)
currently reaches no suggestion anywhere. Needs:
- A third `whale_cfg` param threaded through `_rejected_candidate_recommendations`
  and its ~5 call sites (`main.py:1465,1669,2020,2753,2779`, `ml_feed.py:50`).
- `current_value` lookup extended to a 3-way section branch.
- A real decision on what `whale_watcher`'s rejections should be compared
  against - whale-follow's accepted-trade win rate (`whale_summary`) is the
  closest fit since whale-follow is this gate's only consumer, but that's a
  judgment call worth confirming, not assuming.

## Other verified findings from the 8-angle review, not yet fixed

Ranked roughly by how much a fix would actually change live behavior:

1. **`_drop_stale_recommendations` checks the wrong evidence pool for
   category/series-specific suggestions** (`advisory_engine.py:704-739`) -
   it checks whether *any* whale-follow trade closed since the last apply,
   not whether a trade in that specific category/series did. A stale
   per-category nudge can be silently re-applied off evidence that never
   actually changed for that category - this is the same class of bug the
   function was built to fix (2026-08-11, "apply button gives the same
   suggestion again"), just not closed for every recommendation source.
2. **`services/strategy_engine.py`'s special-settlement gate reaches into
   `main.state` via a lazy circular `import main`, wrapped in a bare
   `except Exception: pass`** (lines ~175-206), instead of using the
   parameter-passing convention the function already uses for `category`
   two lines above. If `main.state`'s shape ever changes this safety gate
   silently goes dark - no log, no error, just stops filtering
   special-settlement markets.
3. **`series_evaluator.py` has two independent copies of its status state
   machine** - `record_trades_observed_bulk()` (the one real code calls)
   and `record_trade_observed()` (singular, called only by tests). They can
   silently diverge since neither delegates to the other.
4. **`calibration_history.record_snapshot()` silently drops the `buckets`
   field** when persisting `confidence_calibration.py`'s report - only
   `gap_pts`/`discriminates` survive. Not user-visible today (the History
   tab only reads `gap_pts`), but any future "how did this factor's
   high-bucket win rate trend over time" feature will find the data was
   never captured, with no comment flagging this as a scope cut (unlike
   this codebase's usual practice).
5. **`market_strategy_calibration.py`'s `confidence_label` and
   `overall_win_rate` are computed from two different sample sizes** -
   `confidence_label` gates on all resolved rows, `overall_win_rate` only
   on the subset with a parseable `entry_confidence`. Its sibling
   `confidence_calibration.py` uses one consistent denominator for both.
   Can show e.g. a "higher" confidence label (n=50) next to a win rate
   actually backed by n=4.
6. **Regime Segmentation panel shows per-bucket win rates with zero
   minimum-sample-size gate** (`regime_analytics.py`'s three bucket
   functions), unlike every sibling rate-producing function in this
   cluster and unlike its own consumer
   (`_category_conditional_recommendations` requires n>=5 before
   suggesting). A single-trade bucket that happened to win renders as a
   solid-green "100.0%" tile, visually identical to a 50-trade bucket.
7. **Backtest Sweeps panel's Apply button has no sample-size warning** -
   `backtest.py`'s sweep functions only suppress a row at `n==0`; a
   single-signal bucket at an extreme threshold can show "100.0%" with a
   clickable Apply next to it.
8. Efficiency (no behavior change, just wasted work - lower priority):
   `regime_analytics.by_category()` computed twice with identical inputs
   in `main.py`'s full-spectrum context builder;
   `trade_analytics.compute_summary(rows)` computed twice in
   `advisory_engine.generate_recommendations()`; the entire
   advisory/calibration/candidate-log/series-evaluator/regime read path
   fully recomputes from scratch on every 5s dashboard poll while the
   History tab is open, with no cheap "did anything actually change"
   check (the `/api/state` ETag/304 pattern already exists elsewhere in
   this app and could be reused); `series_evaluator.evaluate_pending()`
   opens one SQLite connection per series in its verdict-writing loop
   instead of batching, the same bug class already diagnosed and fixed
   once for its sibling `record_trades_observed_bulk()`.
9. Duplication/reuse (cosmetic, no behavior risk - lowest priority):
   `_CONFIDENCE_BANDS`/`_MIN_BAND_SIZE`/the banding function are
   byte-for-byte duplicated between `confidence_calibration.py` and
   `market_strategy_calibration.py` (disclosed in the latter's docstring,
   but still two copies that can silently diverge); `regime_analytics.py`'s
   `by_hour_of_day`/`by_day_of_week` are copy-paste twins differing only
   in the `time.gmtime()` field read; `advisory_engine.py` hand-rolls
   win-rate math in two spots instead of calling
   `trade_analytics.compute_summary()`, which it already imports and uses
   elsewhere in the same file; `stats_power.py` - the module built
   specifically to replace this cluster's scattered ad hoc sample-size
   thresholds - is imported by exactly one caller (`main.py`, for one
   endpoint) and none of the five ad hoc thresholds it names in its own
   docstring have actually been migrated to it yet.

## Suggested order

1. Decide market_native's fate (pause vs. let it keep failing as data) and
   whether to add a cumulative-drawdown kill switch - highest real-money-
   relevant impact of anything in this doc, even in paper mode.
2. Whale-follow whipsaw tuning (item under "betting against myself" above)
   - a re-entry cooldown or confidence bump after an auto-exit loss on the
   same ticker.
3. Finding 1 (`_drop_stale_recommendations` evidence pool) - re-opens the
   exact bug class a prior session already fixed once.
4. The `whale_watcher` gate-wiring (deferred section above) - highest
   real leverage of the "not yet fixed" review findings, needs a real
   design decision, not just a patch.
5. Everything else in "Other verified findings," roughly in the listed
   order.
