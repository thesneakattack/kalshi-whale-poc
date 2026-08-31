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
remediation.md`), following `superpowers:systematic-debugging`, quantifies a live-reported
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

## Task 20 shipped, 2026-08-27: `tick_cache` is correct but currently inert
in the live app — the same-ticker case it targets cannot occur today

Task 20 (`check_exits(..., tick_cache=None)`) memoizes `recent_price`/
`volatility`/`analyst_lean`/`series_stats` reads keyed by `(function_name,
ticker)`, wired at `main.py`'s single per-tick call site. Verified against
source while implementing it, not assumed: `PaperBroker.positions` is
`dict[str, Position]` — **exactly one `Position` per ticker key**
(`services/paper_broker.py`'s own type annotation, and `open_position`
unconditionally does `self.positions[ticker] = Position(...)`, overwriting
rather than accumulating). `check_exits`' per-position loop
(`for ticker, pos in list(broker.positions.items())`) therefore visits each
ticker **at most once per call**, and `main.py`'s tick loop calls
`check_exits` exactly once per tick with a fresh `tick_cache = {}` each
time — so no cache key can ever be requested twice within that call. The
"two partial-hedge positions on the same market" scenario the Known-scope-
gap note above uses as its same-ticker example cannot happen under this
data model: two positions "hedging the same market" are necessarily two
*different* tickers (different strikes/sides of a structured event), which
is exactly the distinct-ticker case already documented as unfixed by this
task, not the same-ticker case it actually targets.

Net effect: the memoization mechanism itself is implemented correctly and
is exercised by real tests (`tests/test_check_exits_scale_benchmark.py`'s
`test_check_exits_shares_tick_cache_across_calls_on_the_same_ticker` proves
it dedupes when two `check_exits()` calls for the same ticker share one
`tick_cache` dict) — but at the one place it's actually wired today, it
produces zero cache hits and zero measured speedup, because that call site
never gets a repeat ticker to dedupe. It would start doing real work only
if either (a) `PaperBroker` ever supports more than one concurrent position
per ticker, or (b) a `tick_cache` were shared across more than one
`check_exits()` call within a short window (e.g. across `main.py`'s tick
loop and the two `services/whale_stream/whale_stream_handlers.py` call
sites, which Task 20 deliberately left unwired — see its own scope in
`docs/superpowers/plans/2026-08-25-realtime-data-plane-remediation.md`).
Not a defect in the shipped code — `tick_cache=None` stays the byte-
identical default everywhere it isn't passed — but a real gap between "the
interface Task 20 specified" and "what actually reduces read count in the
live app," worth knowing before treating Task 20 as delivering any part of
the live crash report's fix. The distinct-ticker bulk-fetch/`tick_executor`
follow-up above remains the only path with a real chance of doing that.

## Staleness-triggered price corroboration (P8 Task 35, 2026-08-27 — resolves R4)

`check_exits` gained `latest_prices_updated_at: dict | None = None` (threaded
through `FollowTheWhaleStrategy.check_exits` and all three call sites) and a
second trigger on the existing corroboration read. The 2026-08-17 deviation
gate catches a **wrong** price (a garbage tick, override when |WS − REST| >
0.30). It never caught a **stale** one: an in-memory price whose WS ticker
channel had gone quiet passed through indefinitely (P8 Task 34 measured 4 of
10 open positions with no ticker message ever received). Now, when the price
about to be acted on is older than `strategy.price_staleness_corroborate_sec`
(120.0 provisional, `config/settings.yaml`) — or has no write stamp at all,
unknown age is not trusted, the same rule `market_fetch.overlay_live_prices`
uses — the independent `market_history.recent_price` read is trusted even
inside the deviation band. WS-primary, REST verifies at decision time —
Family A's own pattern, applied to the one place it was still missing.

Design choice, from the dedicated research pass: **fail-open stays the
rule.** This codebase has zero precedent for blocking a trading decision on
data staleness (every freshness check — `recent_price`, `analyst_lean`,
`discovery_cache` — fails open by explicit design; the kill switch, the only
fail-closed mechanism, is gated on realized loss, not data age), and a WS
outage during a fast move is exactly when a stop-loss is most needed —
refusing to act would convert a data-plane problem into a larger realized
loss. So a stale price that REST also cannot corroborate is still acted on,
but the condition is recorded in `fault_log` (`exit_engine` /
`stale_price_uncorroborated`, once per ticker per observability window,
rolled by `maybe_capture`) — never silent, per the HARD RULE. Only the
`current_price`/`pnl_pct` path is affected; sentiment-reversal and
time-to-close exits never read price and are untouched. Threshold re-tuning
belongs to P8 Task 40's benchmark on real captured cadence data.

## Family-C-lite: position_netting.review gets a second, WS-triggered caller (P8 Task 38, 2026-08-28)

`position_netting.review` used to run only once per tick from `trading_loop`'s
body, after `strategy.check_exits`. It now also runs from
`services/whale_stream/whale_stream_handlers.py::_process_stream_ticker`,
right after that handler's own `check_exits` call, on the ticker WS path -
same "iterate everything" shape `check_exits` already used there. Gated on
`state["running"]` only, deliberately *not* on `state.get("signal_feed")`
(that's `check_exits`'s own gate, since it searches `signal_feed` for the
position to check) - `review` never reads `signal_feed` and `trading_loop`'s
tick never gated it on that either, so gating it there would have been a
real behavior narrowing, not a no-op.

Concurrency safety for this second caller was proven before the wiring
landed, not assumed: `review` is a plain synchronous `def` that mutates
`broker.positions` via `broker.close_position(...)` as the last step before
returning each decision - no `await` anywhere inside it, so the asyncio
scheduler can never interrupt one call mid-execution. That means a second
caller racing to act on the same position (this WS site vs. `trading_loop`'s
own tick, until Task 39 slows it) always reads the *post-mutation* state:
a position `review` already closed is simply gone from `broker.positions`,
so a racing second call finds nothing left to close. Proven directly with
`asyncio.gather` forcing real interleaving in
`tests/test_position_management_concurrency.py` rather than inferred from
the shape of the code. `PaperBroker.check_pending_fills` got the identical
treatment, same call site, same reasoning, same test file.

## Netting decision inputs are columns, not only prose (issue #213, 2026-08-30)

`position_netting.review` closes a leg with a reason sentence that embeds the
numbers behind the decision - `estimated $X expected-value improvement over
holding (bar $Y)`. Recovering the bar for the #206 blast-radius analysis meant
regex-parsing `bar \$([0-9.]+)` out of `trades.reason`; it happened to work,
and it is not an interface. The same three values now ride onto the CLOSE row
as additive `trades` columns (`services/paper_broker.py`,
`_add_column_if_missing`): `netting_improvement_usd`, `netting_bar_usd`,
`netting_vol_ratio`.

- `_materiality_bar` returns `(bar, vol_ratio)`; the ratio is exactly `1.0` on
  both unscaled paths (no `normal_volatility`, or no usable reading - the
  `v > 0` filter from #206), so the column states what scaling was applied
  rather than reading `None` for "applied nothing". The bar's value is
  unchanged.
- `describe_groups` carries `vol_ratio` on every variable-group recommendation
  next to the existing `expected_value_improvement_usd` /
  `materiality_bar_usd`. Those two are `round(x, 2)` - the same rounding the
  sentence's `:.2f` applies - so the columns equal the prose's numbers
  outright, not approximately;
  `tests/test_position_netting.py::test_review_persists_netting_decision_inputs_that_agree_with_the_reason_prose`
  asserts that on the persisted row itself.
- NULL means "no bar was computed": every entry, every non-netting close, and
  a `locked_loss` close_all (the loss is fixed regardless of timing, so no
  expected-value comparison happens). Never `0.0`, which would read as a real
  bar of zero dollars. Pre-existing rows keep NULL - additive only.
- The sentence is unchanged. Prose is for the reader; columns are for the
  analysis (`docs/data-layer-analysis-layer-contract.md`).

## A fourth column: the locked_loss exit-fee cost (2026-08-30, entry-gate-me-pairing-and-netting-remediation Part 2)

A `locked_loss` group's recommendation carries its own structured number,
`exit_fee_cost_usd` (`describe_groups`) - the real Kalshi taker fee paid to
close now instead of riding to Kalshi's fee-free settlement, which this
module's own top docstring argues is the only real cost a locked, fixed loss
can still incur. It did not exist when the three columns above shipped
(`locked_loss` has no bar/improvement/vol_ratio - no expected-value
comparison happens when every outcome already loses), so it rides onto the
CLOSE row as its own additive column, `trades.netting_exit_fee_usd`, via the
same `_add_column_if_missing`/`close_position`/`INSERT` idiom.

- Populated on exactly the opposite branch from the three columns above:
  NULL on a `variable`/`locked_profit` netting close (and on every non-netting
  row), a real number only on a `locked_loss` `close_all`. The two column
  families are never both non-NULL on the same row.
- `services/reset/trade_archive.py`'s `archived_trades` table, its
  `archive_epoch` SELECT, and its INSERT column list are extended in the same
  change - the #242 fix note in that file's `_connect` documents why an
  explicit-column-list archive silently drops any `trades` column added
  without a matching update here, and this column would otherwise reopen that
  exact gap on its first `POST /api/reset`, an archive being append-only with
  no way to backfill a dropped column after the fact.
- **It is the GROUP TOTAL, repeated on every member row — never `SUM()` it**
  (2026-08-30 final-review finding). A `close_all` writes one `trades` row
  per member ticker and each carries the same whole-group figure, matching
  the three sibling columns' convention; those three are non-additive by
  nature so repeating them is harmless, but a USD amount invites a sum that
  overcounts by exactly the group size. Deliberate: the per-leg number is
  already in the same row's `fee` column (same `taker_fee` call, same
  price), so a per-leg version of this column would carry no information.
  Total netting fee drag is
  `SELECT SUM(fee) FROM trades WHERE netting_exit_fee_usd IS NOT NULL` —
  the new column is the "this row was a locked_loss netting leg" flag,
  `fee` is the cost. Per-group total: read any one member row.
- Tests: `tests/test_position_netting.py::test_review_persists_exit_fee_cost_on_a_locked_loss_close`
  and `::test_review_leaves_netting_exit_fee_usd_null_for_a_variable_close`.
- The tradeoff this makes measurable - closing now for bankroll/position
  headroom versus this fee cost - is itself still open:
  `docs/open-decisions.md`.
