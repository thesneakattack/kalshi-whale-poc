# Exits module — reference

Owns: `exit_engine.py` (per-position exit decisions: settlement close,
take-profit, stop-loss, time-to-close forced exit, sentiment-reversal, the
`auto_exit` composite scorer) + `position_netting.py` (group-level
hedge/concentration management across confirmed mutually-exclusive events,
runs after `exit_engine`'s per-position pass) + `routes.py`
(`GET /api/position-netting/groups`, the only exit-related HTTP surface).
Extracted 2026-08-22 (modularization Phase 1/9) out of
`services/strategy_engine.py`, which stays entry-only —
`FollowTheWhaleStrategy.check_exits`/`validate_pending_fill` remain public
methods there (unchanged call sites in `main.py`/tests), but `check_exits`'s
body is now a one-line delegation into `exit_engine.check_exits`.
`services/paper_broker.py`'s `close_position` is the actual execution
primitive every function here calls into once a reason has been decided —
it stays flat, it owns no exit *decision* logic, only fills/bankroll/
persistence (see `services/position/README.md`).

## Relevant Kalshi API docs

- `docs/kalshi/market_lifecycle.md` — the market-status state machine this
  whole module is built against. **Read this before touching
  `close_if_settled` or `market_results`'s source.**

## Audit finding, fixed 2026-08-23: `close_if_settled` fired at `determined`,
not `finalized`

Checked directly against `docs/kalshi/market_lifecycle.md` while writing
this cheat sheet (lines 21–23, 51–52, 68–72), not assumed:

- `closed` → `determined`: "Result is known. Settlement timer is running."
  `market.result` is set to `yes`/`no` **at this transition** — this is
  also exactly where `services/market_watch/catalog_scan.py`'s
  `propagate_milestone_winners` reads it (`m.get("result")`) to build the
  `market_results` dict this module's `check_exits`/`close_if_settled`
  consume.
- `determined` → `disputed` (possible) → `amended`: "Result has been
  challenged. May be re-determined... Settlement timer restarts." The
  result can still flip during the settlement-timer window.
- `determined`/`amended` → `finalized`: "positions paid out" — this is the
  actual, no-longer-reversible settlement (`settled` WS event / `finalized`
  REST status).

**Previous behavior**: `close_if_settled` closed the paper position and
locked in P&L the instant `result` was set (`determined`), not once the
market reached `finalized`. This meant a disputed-and-reversed result
during the settlement-timer window would leave this app's paper P&L wrong
with no correction path — the position was already gone from
`broker.positions` and converted into a closed trade by the time any
dispute could occur.

**Fix**: took the "wait for `finalized`, immune to reversal" option this
cheat sheet named as one of the two real choices (over "accept the risk
explicitly"), since it was resolvable for free — an open position's ticker
already stays fetched every tick via `main.py`'s `extra_tickers` regardless
of watchlist rotation (`services/market_watch/market_fetch.py`), so waiting
just means it keeps flowing through `determined`/`disputed`/`amended` and
closes once `finalized` shows up on a real, fresh REST read, rather than
needing a separate correction mechanism. `propagate_milestone_winners` now
only includes a market's own `result` in `market_results` once
`status == "finalized"` — `check_exits`/`close_if_settled` never see a
still-reversible result at all. The same gate was applied to `main.py`'s
own `record_outcome`/`resolve_window` REST-tick loop and to
`services/whale_stream/whale_stream_handlers.py`'s `_process_stream_lifecycle`
(that file's `determined`/`settled` handling had grown the identical bug
independently the same day, for `market_history`/`settlement_edge`/
`market_analyst_agent`/`candidate_log`'s outcome resolution — see that
module's own docstring for the fix there, which needed a fresh single-ticker
REST read at `settled` since that event carries no `result` field of its
own). Milestone-winner results (the rest of `propagate_milestone_winners`)
are a separate, deliberately-earlier signal and untouched by this gate.

## Config keys this module reads (all resolved per-position via
`config_overrides.resolve`, category/series-aware — see `check_exits`'s own
docstring for why per-position, not once per tick)

`take_profit_pct`, `stop_loss_pct`, `exit_on_sentiment_reversal`,
`exit_sentiment_min_signals`, `exit_sentiment_lean_pct`, `auto_exit_enabled`,
`auto_exit_threshold`, `auto_exit_pnl_weight`, `auto_exit_sentiment_weight`,
`auto_exit_staleness_weight`, `auto_exit_analyst_weight`,
`auto_exit_gain_reference_pct`, `auto_exit_loss_reference_pct`,
`auto_exit_stale_after_sec`, `auto_exit_normal_volatility`,
`auto_exit_volatility_lookback_sec`, `auto_exit_series_track_record_weight`,
`exit_min_seconds_to_close`, `min_resolved_for_whale_filter` (shared with
the entry-side filter in `strategy_engine.evaluate`, not duplicated).
`position_netting.py` reads its own `position_netting.*` sub-block
(`enabled`, `min_dwell_sec`, `min_edge_improvement_usd`, `normal_volatility`,
`volatility_lookback_sec`) separately.

**Still-open ROADMAP P0 gap this module owns**: `exit_min_seconds_to_close`
is the only time-to-close-aware exit rule, and it's opt-in/off by default —
`take_profit_pct`/`stop_loss_pct`/`auto_exit` are all purely price-driven,
so a position can still ride unmanaged to a market's close if that one field
is unset. See ROADMAP.md's "Path to production" #1 item for the full
incident this traces to.

## Handoff — who calls this module, who it calls

- **Upstream:** `main.py`'s `trading_loop` (`exit_management` phase) calls
  `strategy.check_exits(...)` (delegates here) and
  `position_netting.review(...)` directly, once per tick, after the
  signal/fill loop — same call sites as before this split, only the
  implementation moved.
- **`close_if_settled`** is also imported directly by
  `services/market_strategy.py`'s `MarketNativeStrategy.check_exits` — the
  one function this module shares with the (possibly-to-be-removed, see
  ROADMAP.md) Market-Native strategy. Keep it here, not duplicated, if that
  strategy is ever removed — check for this import before deleting
  anything.
- **Downstream:** every close funnels into `broker.close_position` /
  `broker.correct_erroneous_close` (`services/paper_broker.py`) — this
  module decides *whether and why*, the broker decides *how* (fees,
  bankroll, persistence, trade-log write).
- `_exit_confidence` calls `market_analyst_agent.analyst_lean()` (a cheap
  indexed read, never a fresh LLM call) and `signal_log.series_stats()` —
  both real cross-module reads, not incidental.

## Benchmark finding, 2026-08-27: `check_exits` scales linearly with open
position count, and the distinct-ticker case dominates the live crash report

P3.5 Task 17c (`docs/superpowers/plans/2026-08-25-realtime-data-plane-
remediation.md`), following `root-cause-debugging`, quantifies a live-reported
symptom ("having a large amount of open positions causes things to lag or
crash," 2026-08-27) before Task 20 fixes it. `tests/test_check_exits_scale_
benchmark.py` proves the mechanism directly: `market_history.recent_price`
(the stop-loss/take-profit price-corroboration read, `check_exits`'s own loop
body) is called exactly once per open position, unconditionally — ahead of
and outside all three opt-in exit rules (`take_profit_pct`/`stop_loss_pct`/
`exit_on_sentiment_reversal`/`auto_exit_enabled`), and regardless of
`cost_basis`. N open positions means N reads every tick, not O(1).

Wall-clock cost at increasing N (synthetic `Position` objects, `recent_price`
mocked to sleep 0.0001s per call — a representative single-row SQLite read,
not real I/O; measured via `ddev exec -s fastapi python -m pytest tests/
test_check_exits_scale_benchmark.py -v -s`):

| n (open positions) | wall-clock cost |
|---|---|
| 10  | 2.2ms |
| 50  | 10.6ms |
| 200 | 39.2ms |
| 500 | 103.0ms |

Near-perfectly linear (~0.2ms/position throughout — 0.22, 0.212, 0.196,
0.206ms/position respectively), confirming O(N) with no hidden superlinear
term at this scale.

**Distinct-ticker case, not same-ticker, dominates — the design of this
benchmark makes that conclusion direct, not inferred.** Every synthetic
position in this benchmark carries its own distinct ticker
(`KXBTC15M-T0`..`KXBTC15M-TN`), the same shape "a large amount of open
positions" describes in practice (many different markets held at once, not
many partial-hedge positions piled onto one market). Task 20's planned
`tick_cache` fix memoizes `recent_price`/`volatility`/`analyst_lean`/
`series_stats` reads keyed by `(function_name, ticker)` — it dedupes repeat
reads *for positions sharing one ticker within a tick*. Because no ticker
repeats anywhere in this benchmark, that cache would have a 100% miss rate
against this exact workload: **Task 20 alone provides zero speedup for the
distinct-ticker case**, which per this benchmark's own construction is also
the case that actually produces the linear O(N) cost. Task 20's own "Known
scope gap" note already flagged this as a real possibility rather than
asserting the fix was sufficient — this benchmark confirms it is the
dominant shape, not just a possibility. The live crash report needs the
bulk-fetch follow-up that same note names (a `signal_log.series_stats_bulk`-
shaped single query across all open tickers, or a `tick_executor` offload of
the whole `check_exits` call) in addition to Task 20's per-ticker
memoization, not instead of it — same-ticker positions (partial hedges) still
benefit from Task 20 once it ships.
