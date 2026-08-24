# TODO: heuristics/advisory audit + exit-tuning findings (2026-08-14)

**Condensed 2026-08-23** — this file had grown to 405 lines, mostly
narrative detail on work that's since shipped or gone stale (see
`ROADMAP.md`'s own condensing history for why that pattern gets cut back
periodically). Full prose for anything summarized below is in `git log -p
-- docs/todo-2026-08-14-heuristics-audit-and-exit-tuning.md`. `ROADMAP.md`
has the one-line pointer per this repo's usual split; this doc is still the
detail, just shorter.

Original scope: a deep bug scan after CI broke, an 8-angle code review of
the "heuristics and suggestions" subsystem (`advisory_engine.py`,
`confidence_calibration.py`, `market_strategy_calibration.py`,
`series_evaluator.py`, `candidate_log.py`, `regime_analytics.py`,
`cross_strategy.py`, `calibration_history.py`, `config_performance.py`,
`backtest.py`, `stats_power.py`), plus a same-session Part 2: an exit-
strategy deep dive, fee-schedule reconciliation, and mutually-exclusive
pair detection.

## Already fixed this session (2026-08-14)

- `check_exits()`'s `exit_on_sentiment_reversal`/`auto_exit_enabled` elif
  chain made auto-exit dead code whenever both flags were on (the real
  production config) — restructured so auto-exit runs whenever
  sentiment-reversal doesn't itself decide to close.
- New volatility-aware pnl thresholds (`market_history.volatility()` scales
  `gain_ref`/`loss_ref`) and a series-track-record exit factor (opt-in,
  weight 0 by default) — both shipped, `market_native` deliberately out of
  scope per direct instruction at the time (moot now — see below).
- `close_window_sec`/`special_market_min_seconds_to_close` were hardcoded
  fallbacks with no real config entry — now real `settings.yaml` fields.
- CI was failing at collection (`requests` missing from
  `requirements-dev.txt`); the underlying `test_static_index_served_from_web_container`
  ddev-network dependency got a `socket.gethostbyname` skip guard instead.
- `advisory_engine.py`'s take-profit recommendation divided a whole-position
  dollar total by a flat 100 instead of normalizing by `cost_basis` per
  trade — fixed to match the stop_loss branch's convention.
- Two whale-follow gates (`close_window`, `special_market_gate`) were
  missing from `_GATE_CONFIG_PATH_AND_DIRECTION`, silently losing their
  rejected-candidate counterfactual data — added.
- `confidence_calibration.py`'s near-constant-factor guard could be
  defeated by float jitter — now rounds to 6 decimals before dedup.

**Deliberately deferred, still open** — a time-til-close exit factor
(urgency should rise as a losing position nears close, but not push out a
winning one early — that's `take_profit_pct`'s job). Needs `close_time`
threaded through `check_exits()`, plus a real design decision on how it
interacts with the volatility multiplier already in place. Sketch:
`auto_exit_time_urgency_window_sec`, tightening `loss_ref` only as
`seconds_to_close` shrinks below it. **This is one of the two items
`ROADMAP.md`'s P4 section still points at.**

## Fee schedule reconciliation (`docs/kalshi/kalshi-fee-schedule.pdf`)

Core formula/rounding were already correct. Two gaps found:

1. **Shipped**: a real per-series fee waiver (multiplier 0 on 10 series,
   e.g. `KXBTCY`, `KXETHY`) was unmodeled — `taker_fee()` took no
   ticker/series at all. Fixed with an optional `ticker` param, wired
   through all 4 production call sites. No retroactive correction needed
   (zero historical trades ever touched any of the 10 series).
2. **Still open**: Kalshi's real per-event API response includes
   `fee_type_override`/`fee_multiplier_override` fields, already fetched
   and cached by `main.py`'s `_fetch_event_titles` but never read by
   anything. Would be a more robust source than the hardcoded 10-series
   list above (always current vs. a dated PDF snapshot) — real plumbing
   change, not a one-line fix, not started.

## Mutually-exclusive pair detection

Shipped: `services/mutual_exclusivity.find_me_pairs()` (Kalshi's own
`mutually_exclusive` flag authoritative, price-sum fallback only when
missing), computed fresh every tick from already-fetched data, both
strategies reject a signal whose confirmed ME complement already has an
open position (new `mutually_exclusive_duplicate` candidate-log gate).

**Still open** (explicit "your call" from the user, not an oversight):
folding a complement ticker's inverted-side whale prints into
`_whale_lean()`'s sentiment read — a whale buying YES on "Team B" is
informationally equivalent to buying NO on "Team A," currently invisible to
`TEAM-A`'s own sentiment factor. Deferred specifically to avoid stacking a
fourth simultaneous behavior change onto the safety-relevant exit path in
one session. **The other of the two items `ROADMAP.md`'s P4 section points
at.**

## Obsolete / superseded — do not act on these as originally written

- **market_native's 98.9%-bankroll-loss "urgent decision"** (pause vs. let
  it run negative-control) is moot: the entire Market-Native strategy was
  removed 2026-08-23, commit `e2dcf33` (`ROADMAP.md` P4). Its underlying
  point — no cumulative-drawdown kill switch, only a daily-reset one — is
  still real and still applies to whale-follow; worth a fresh item in its
  own right if it matters, not resumed from this one.
- **The `whale_watcher`/`min_notional_usd` gate-wiring gap** is moot: that
  whole config field was removed and replaced by `min_contracts` 2026-08-23
  (`ROADMAP.md`, "whale threshold switched from dollars to contract
  count"). If the analogous wiring gap exists for `min_contracts` today, it
  needs re-checking fresh against current code, not resumed from this
  investigation's `min_notional_usd`-shaped state.

## The "68% win rate, pennies of profit" investigation — answered, historical

Settled trades: wins (366) median +23.6% of cost basis; losses (155)
consistently -100% to -106% (essentially no variance, a settled loss costs
the full stake). Net was barely negative on settled trades alone;
`take_profit` closes pushed it to net +$444.22 on $44,562 deployed. Basic
asymmetric-payoff math, not a code defect — this is the data behind
CLAUDE.md's now-retired 70%/70% target (see `git log -S 'HARD COMMANDMENT'
CLAUDE.md` if that reasoning is ever needed again). One real, still-possibly-
relevant pattern found: 17 of 564 tickers showed "betting against myself" —
re-entering the opposite side right after an auto-exit loss, net negative
even when the second leg technically won. A per-ticker re-entry cooldown
after an auto-exit loss was suggested, never built.

## Other verified findings from the 8-angle review, not reconfirmed since 2026-08-14

Status below reflects 2026-08-14 unless a `ROADMAP.md`/`status.html` entry
is cited — these have not been individually re-checked against current
code during this condensing pass, only cross-referenced where an obvious
match existed.

1. `_drop_stale_recommendations` checks whether *any* whale-follow trade
   closed since the last apply, not one in the specific category/series a
   suggestion is about — a stale per-category nudge can silently re-apply.
2. `strategy_engine.py`'s special-settlement gate reaches into `main.state`
   via a lazy circular `import main` wrapped in a bare `except Exception:
   pass` — if `main.state`'s shape changes, this safety gate silently goes
   dark with no log.
3. `series_evaluator.py` has two independent copies of its status state
   machine (`record_trades_observed_bulk()`, real; `record_trade_observed()`,
   test-only) that can silently diverge.
4. `calibration_history.record_snapshot()` drops the `buckets` field —
   only `gap_pts`/`discriminates` survive.
5. `market_strategy_calibration.py` — moot, module removed with
   Market-Native (see Obsolete section above).
6. Regime Segmentation panel shows per-bucket win rates with no
   minimum-sample-size gate, unlike every sibling in this cluster.
7. Backtest Sweeps panel's Apply button has no sample-size warning.
8. Efficiency findings — **3 of these fixed 2026-08-23** (`ROADMAP.md`,
   "per-module data-consumption audit"): `regime_analytics.by_category()`/
   `trade_analytics.compute_summary()` double-computation, and
   `series_evaluator.evaluate_pending()`'s per-series SQLite connections.
   **Still open**: no cheap "did anything change" check on the
   advisory/calibration/candidate-log read path (recomputes from scratch on
   every 5s poll while History is open) — the `/api/state` ETag/304 pattern
   exists elsewhere and could be reused here.
9. Duplication (cosmetic, lowest priority): `_CONFIDENCE_BANDS`/banding
   logic duplicated between `confidence_calibration.py` and the now-removed
   `market_strategy_calibration.py` (partly moot); `regime_analytics.py`'s
   `by_hour_of_day`/`by_day_of_week` are copy-paste twins;
   `advisory_engine.py` hand-rolls win-rate math instead of calling
   `trade_analytics.compute_summary()` in two spots; `stats_power.py` is
   still imported by exactly one caller, none of its own named ad hoc
   thresholds migrated to it.
