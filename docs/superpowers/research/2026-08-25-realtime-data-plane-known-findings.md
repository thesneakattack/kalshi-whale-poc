# Realtime Kalshi Data-Plane — Known Findings and Open Hypotheses

**Purpose:** Seed an investigation, not dictate its conclusion.

**Audit snapshot used to form these hypotheses:** `main` at
`94bfbe15c19b21ec7aaac64a901553f5770fff91`.

Every execution session must re-ground against current HEAD. If current code has already
changed, current code/tests/CI win.

## What appears strong already

The Kalshi contract boundary itself is no longer the primary suspect:

- `services/kalshi/` is the documented integration boundary.
- Kalshi wire semantics are backed by exact mirrored official docs and fixtures.
- legacy clients were removed;
- the Kalshi boundary is type-checked and CI-enforced;
- public/account/order/stream capabilities are separated;
- contract discrepancies are recorded rather than guessed.

Do not restart that architecture initiative unless evidence shows a real contract defect.

## Persistent production symptoms

The long-running operational symptoms remain:

- REST latency spikes;
- 429/rate-limit pressure;
- delayed watchlist/account information during those periods;
- exchange-wide WebSocket backlog/drop behavior;
- suspected missed whale prints/signals/trades;
- whale signals sometimes arriving too late to be useful.

The investigation must distinguish each failure class rather than treating them as one
generic "Kalshi latency" problem.

## Known WebSocket topology at the audit snapshot

The stream path was:

```text
Kalshi server
  ↓
Python `websockets` receive queue (`max_queue=1024`)
  ↓
reader coroutine
  ↓
application `asyncio.Queue(maxsize=20000)`
  ↓
one serial consumer
  ↓
_handle_message()
  ↓
trade/ticker/fill/position/lifecycle callbacks
```

When the application queue fills, the incoming message is dropped and
`dropped_messages` increments.

The repo already separated the index stream after live evidence showed index ticks
starving behind exchange-wide trade traffic.

## Kalshi documentation facts that matter

The mirrored official WebSocket quick-start explicitly recommends:

- asynchronous processing;
- message buffering for high-frequency updates;
- considering connection pooling for multiple subscriptions.

It also documents WebSocket error 25:

> Subscription buffer overflow

which means Kalshi's server-side subscription event buffer overflowed during a burst and
the client should reduce subscription scope or improve read throughput.

The app must distinguish:

1. Kalshi-side subscription overflow;
2. Python `websockets` receive buffering;
3. application queue overflow;
4. downstream processing backlog.

Those are different failure points.

## Hypothesis H1 — sustained WS arrival can exceed one-consumer service capacity

The exchange-wide trade subscription is intentional: reducing it to the current watchlist
would defeat the whale-discovery requirement.

The current serial consumer architecture may have a sustainable service rate below
exchange-wide arrival during bursts.

This must be measured. Do not "fix" it by making the queue larger without proving service
capacity.

## Hypothesis H2 — one mixed FIFO creates head-of-line blocking

Trade, ticker, lifecycle, fill and position messages can share the same application queue
and consumer.

That may allow large ordinary-trade bursts to delay:

- whale candidates;
- open-position ticker updates;
- lifecycle transitions;
- private account fills;
- private positions.

The exact latency by message class must be measured.

## Hypothesis H3 — expensive work happens before cheap whale rejection has been exploited fully

`services/whalewatchers/kalshi_trade_tape.py` contains a deliberately cheap
`_prescan_count()` intended to classify exchange-wide prints before DB/API work.

The surrounding per-message path may still perform config reads, thread hops, DB work,
archival work, or provider setup before/around that cheap rejection.

Measure each stage. Do not assume which call dominates.

## Hypothesis H4 — transient off-watchlist enrichment failures can become permanently missed candidates

Whale-sized exchange-wide trades not already represented in the local market context can
require REST market enrichment.

The current dedupe/seen lifecycle should be tested specifically for this sequence:

```text
trade arrives
→ candidate
→ REST context lookup
→ 429/timeout/transient failure
→ trade marked seen?
→ later retry possible?
```

If a transient lookup failure can make a candidate terminally disappear, that is a direct
missed-whale defect rather than merely a latency problem.

## Hypothesis H5 — WebSocket has no sufficient reconciliation safety net

When streaming mode is enabled, the app relies primarily on the WS trade feed.

Kalshi's REST Get Trades API can return trades over a bounded time range and exposes
trade IDs, making it a candidate reconciliation source.

The investigation should quantify actual WS capture completeness against REST before
deciding whether a permanent overlap/reconciliation path is warranted.

## Hypothesis H6 — local limiter wait is being mistaken for Kalshi network latency

The central REST limiter is shared by latency-sensitive and background work.

Historical repo comments already document background jobs consuming enough read budget to
stall other calls for many seconds.

The current metrics must be checked for whether they separately measure:

- local limiter wait;
- actual HTTP/network time;
- retry/backoff sleep;
- total end-to-end call time.

If not, add that measurement before changing the limiter.

## Hypothesis H7 — background REST work starves whale-critical work

Potential competitors for the same read budget include:

- catalog scanning;
- discovery confirmation;
- live-status/milestone polling;
- signal-resolution backlog;
- account refresh;
- whale off-list enrichment;
- interactive diagnostics.

Background `asyncio.create_task()` prevents control-flow blocking but does not eliminate
shared token-bucket contention.

A request scheduler may need priority/fairness/reserved-capacity semantics, but that is a
candidate solution, not a predetermined answer.

## Hypothesis H8 — current batching assumptions may be stale

`get_markets_by_tickers` historically chunks at 50 based on an internal rate-cost
assumption.

Current Kalshi docs say endpoint cost and account limits should be queried from the
documented account endpoints where available.

Revalidate the real endpoint cost and request-size behavior before changing batching.

## Hypothesis H9 — milestone/live-data polling may duplicate work

Multiple subsystems have historically queried per-event milestone/live-data state.

The current code should be measured for duplicate calls and cache overlap. A shared
watermark/category cache may reduce requests, but only if the observed duplicate demand is
material.

## Hypothesis H10 — queue size is not the capacity fix

If sustained arrival > sustained service, any finite queue merely delays failure.

Measure:

```text
arrival rate
service rate
queue growth rate
oldest-message age
```

A good solution must increase sustainable throughput, reduce unnecessary work, isolate
critical traffic, or some combination.

## Hypothesis H11 — market-discovery-driven watchlist churn forces repeated
   WebSocket resubscription, cost currently unmeasured

Direct report (2026-08-26): "the way the websocket subscriptions per Market change after
every Market discovery scan is also a major problem. its taxing everything downstream and
upstream." Not yet confirmed as a root cause of any specific symptom above - recorded as a
distinct, previously-untracked mechanism, separate from H1-H10.

Current mechanism (`kalshi.live_markets_only: true`, the active config): every trading tick
(`poll_interval_sec: 6`) rebuilds the full watchlist from scratch via
`services/market_watch/market_fetch.py`'s `live_markets_only` branch -
`market_catalog.candidates_in_window()` (`ORDER BY volume_24h_fp DESC`, time-windowed) ->
`_fetch_live_status()` (already cached/bounded per a 2026-08-15 incident fix) ->
`selection.round_robin_select()` (a **deterministic top-150-series-by-volume cut**, not an
actual rotation despite the name) -> `trade_stream.set_market_tickers(...)` ->
`services/kalshi/websocket.py`'s `_sync_subscriptions()`, which diffs desired vs. subscribed
tickers and sends `update_subscription` (`add_markets`/`delete_markets`) for the delta.

Live-measured (2026-08-26), not assumed:
- The underlying catalog data (`volume_24h_fp` etc.) refreshes via a background scan on its
  own ~15s interval (`catalog_scan._CATALOG_SCAN_MIN_INTERVAL_SEC = 15`), decoupled from the
  6s tick - so `round_robin_select`'s *input* does not change every tick.
- A direct 10-sample measurement of the live watchlist (`GET /api/state`'s `markets` field,
  confirmed via `main.py`'s own comment to be the real live watchlist) showed it holding
  stable at 8 tickers for 3 consecutive ~6s ticks, then jumping to 13 in one step - **churn
  is real but bursty, aligned with the catalog-refresh cadence, not a continuous per-tick
  thrash.**
- No observability existed for this at all before 2026-08-26 - confirmed via a direct query
  against `data/observability.db`. A first, minimal, additive instrumentation pass now
  exists (`KalshiStreamGateway._sync_subscriptions` counts a sync + ticker-add/remove deltas
  only when the diff is non-empty; surfaced as `<stream>.ingest.subscription_churn.*` via the
  existing `ingest_metrics()`/`reset_ingest_window()`/`capture_from_runtime` pipeline - no
  new metrics subsystem). Live-confirmed working: one real sync captured 5 tickers added, 0
  removed, in a single window.
- **CH1 measured (2026-08-27)** — the churn burst's actual downstream/upstream cost, against
  `services/kalshi/websocket.py`'s real code path, the exact mirrored
  `docs/kalshi/websocket-connection.md` contract, and 64h/449,574-row live
  `data/observability.db` history (188 real non-empty churn windows in that span). The two
  extremes never co-occur in the same window, checked directly rather than assumed: the
  largest single-direction event ever observed is 6 tickers added (6 separate windows hit
  this, every one of them 0 removed in that same window); the largest *removal* is 5 (2
  windows, each paired with 5 added, not 6); the largest *combined* magnitude in one window
  is 5 added + 5 removed = 10 total ticker changes (2 windows) - consistent with, not larger
  than, the earlier 5-ticker sample:
  - **Frame count**: not "N tickers x messages" — `_sync_subscriptions` sends one
    `update_subscription` frame per *participating market-channel sid* per non-empty
    direction (`to_add`/`to_remove`), each frame carrying the **entire** diff as a single
    `market_tickers` array, never one frame per ticker (websocket.py:919-932). Which sids
    participate depends on `exchange_wide_trades` (websocket.py:910:
    `market_channels = ("ticker",) if exchange_wide_trades else ("trade", "ticker")`), and
    the live config (`config/settings.yaml`'s `kalshi.trade_stream_exchange_wide: true`)
    sets it `True` - `trade` is exchange-wide and is explicitly excluded from
    add/delete_markets (websocket.py:896-909's own comment: sending it there "would either
    error or, worse, silently narrow the firehose back down to a watchlist"). So under the
    **current live config a churn burst sends at most 2 raw WS frames total** (one
    add_markets + one delete_markets), and often **1 frame**: all 6 of the largest-by-added
    windows (added=6) were add-only, 0 removed in that window, so a max-magnitude add event
    sends exactly 1 frame; only a window with both a nonzero add *and* a nonzero remove (2
    such windows observed, both 5-added/5-removed) sends the full 2. In scoped
    (non-exchange-wide) mode it would be up to 4 frames (2 sids x 2 directions) - still a
    small constant, not N.
  - **`send_initial_snapshot`**: confirmed via the exact mirrored doc, not memory -
    `docs/kalshi/websocket-connection.md` documents it as a distinct parameter on
    `update_subscription` itself (not just `subscribe`), described as "If true, receive an
    initial snapshot for **newly added** market tickers on the ticker channel", default
    `false`. So per Kalshi's contract it does apply per-add, not only on first subscribe -
    but the code's `add_markets` call (websocket.py:919-925) never sets this key, so it
    defaults to `false`: **no ticker snapshot is requested for churned-in tickers today**,
    only for the very first `subscribe` on connect (websocket.py:883-891, which does pass
    `"send_initial_snapshot": True`). Worth flagging as its own small gap (a freshly-added
    ticker gets no seeded snapshot until the next natural tick update), but it also means
    per-add churn cost is *lower* than the original report assumed, not higher.
  - **Queue-depth/wait correlation** (correlational evidence, not a controlled experiment -
    churn timing tracks the ~15s catalog-refresh cadence, not an injected/randomized
    variable, and the underlying `queue_depth` distribution is heavily right-skewed/
    zero-inflated, e.g. churn-window mean 1079 vs. median 0.5, so a handful of outliers can
    move the mean; medians and the correlation coefficient below matter more than the means
    for that reason): measured across all 188 real churn windows. Same-window
    `trade_stream.ingest.queue_depth`: churn-window mean 1079/median 0.5 (n=188) vs.
    all-window mean 3381/median 10 (n=1622) - churn windows run **below** the population
    average on both statistics, not above. `queue_wait.window_avg_sec`: churn-window mean
    7.9s/median 0.09s vs. all-window mean 35.6s/median 0.6s - same direction. Pearson r
    between `tickers_added_window` and same-window `queue_depth` across all 188 events:
    **-0.217** (weak negative). Checked in temporal order for a lag effect too - mean
    `queue_depth` in the sampler window immediately *before* each churn event (1440) ->
    the churn window itself (1079) -> the window immediately *after* (1206) - no rise
    anywhere in that sequence, all three below the 3381 population mean. **No evidence of a
    positive relationship between churn magnitude and queue depth/latency in this data** -
    per `.claude/rules/realtime-data-plane-evidence.md`'s own standard ("queue depth was
    high at the same time as latency" is correlation, not yet causation), this is
    correlational evidence *against* the "churn burst raises queue depth/latency" mechanism,
    not a controlled-experiment proof that it cannot happen under a different (e.g. much
    larger watchlist, non-exchange-wide) configuration.
  - **REST demand**: `_sync_subscriptions` itself makes zero REST calls (its full body is
    `self._send(...)` WS writes only). The one REST path whose *input* depends on watchlist
    membership, `_resolve_unknown_markets` (`services/whalewatchers/kalshi_trade_tape.py:358`,
    own docstring: "so an exchange-wide trade subscription actually produces signals instead
    of silently dropping ~98% of the flow" - i.e. under the live `exchange_wide_trades: true`
    config, ~98% of trade flow is already off-watchlist regardless of churn), runs once per
    trading tick from `fetch_signals` regardless of whether that tick coincides with a churn
    event. Using the same churn-window-vs-all-window split as the queue-depth check above
    (not just an unrelated magnitude comparison): `whale_pipeline.counter.resolve_calls` runs
    higher in churn-active windows than non-churn windows - mean 7.43/median 6 (n=191) vs.
    mean 4.96/median 4 (n=1414), roughly 50% higher. But the *magnitude* correlation is the
    opposite sign - Pearson r between `tickers_added_window` and same-window `resolve_calls`
    across 192 events is **-0.247** (weak negative), meaning windows with more churned
    tickers do not show more resolve calls. Read together, these two results best fit a
    shared-confound explanation (both churn and off-watchlist resolution activity likely
    rise with overall market volume) rather than churn *causing* extra resolve calls
    one-for-one - and in absolute terms it stays low-impact regardless of the mechanism:
    `kalshi_rest_class.critical_whale.rate_limited` totals only 22 across 1486 samples with
    0 `errors`, so whatever the true relationship, it is not producing rate-limit stress.
  - **CH1 verdict: measured negligible on every axis checked**, not confirmed-material and
    not proven-irrelevant either - frame count is small and bounded (<=2 under the live
    config), the one mechanism that could have made per-add cost expensive isn't even
    exercised (no snapshot on churn-add), queue depth/wait show no positive correlation with
    churn magnitude (weak negative), and while churn-active windows do run modestly higher
    on `resolve_calls` than non-churn windows (7.43 vs. 4.96 mean), that effect doesn't scale
    with churn magnitude itself (r=-0.247) and produces no measured rate-limit pressure
    (22 `rate_limited` events / 1486 samples, 0 errors) - so it reads as a shared-confound
    correlation, not a material direct cost. Feeds into CH3's classification of H11.
- **CH2 resolved (2026-08-27) — classification (a): a third, unrelated module, same bug
  shape as PR #35/#36, not churn-caused.** Live proof, not inference: `GET
  /api/observability/history?metric=loop_watchdog.stall_max_ms` surfaced a fresh 12.5s stall
  and an isolated 56.9s stall (neither temporally adjacent to a churn event - the nearest
  churn sync in both cases was 60-100s away, consistent with CH1's own no-correlation
  result), so a 12-minute live `py-spy dump` watch (`cap_add: SYS_PTRACE`, same mechanism as
  PR #35/#36) was run against the real worker process. It caught `series_watcher.funnel()`
  (via `check_series_funnel` -> `services/diagnostics/diagnostics.py`'s `run_offline()`)
  holding the event loop for a continuous ~15s stretch, recurring every ~30-45s throughout
  the watch window - every single sample during those stretches showed the identical frame,
  the signature of one sustained block, not many fast unrelated calls. Isolated and measured
  directly against the live process: `funnel()` alone costs 1.8-2.5s on a cold page cache,
  collapsing to ~0.01s on a repeated identical call once warm - the signature of slow
  bind-mount disk I/O (WSL2/Docker Desktop), not GIL-bound Python object construction (ruling
  out the population_gate_summary-style fix; this needed a plain offload, not a SQL
  rewrite - the query was already using its `(series, observed_at)` index).
  `services/quality/routes.py`'s `get_quality_summary()` (`GET /api/quality/summary`) called
  `diagnostics.run_offline(cfg)` synchronously, unoffloaded, directly on the event loop - and
  the dashboard's `frontend/src/js/polling-and-websocket.js` `refresh()` calls
  `loadSystemHealth()` (which hits this route) on every poll tick
  (`scheduleRefreshTimer`'s default `refreshIntervalMs = 5000`) whenever the Terminal tab is
  open, explaining the ~30-45s recurrence (not every single 5s tick, since a warm-cache call
  is cheap - only the ones that catch the cache cold pile up). **Fixed**: wrapped the
  `run_offline()` call in `services.tick_executor.run(...)`, the same established idiom PR
  #36 used for the whale_calibration routes. Verified live post-fix: while
  `GET /api/quality/summary` was in flight (~4.55s), five concurrent `GET /api/state`
  requests all completed in 0.08-0.11s each - before the fix, per the py-spy evidence above,
  they would have queued behind it. Mechanism (b) (churn-caused) is now ruled out
  definitively, not just deprioritized - the actual cause has no relationship to subscription
  churn at all. Feeds into CH3's classification of H11 as (ii) or (iii), not (i).
- **CH3 resolved (2026-08-27) — classification: (ii) real but currently-negligible cost,
  and separately (iii) unrelated to the observed instability. Not (i).** Reconciling
  CH1 and CH2 rather than re-measuring: CH1 measured churn's own downstream/upstream
  cost as negligible on every axis checked (bounded frame count <=2, no snapshot cost
  on churn-add, no positive queue-depth/latency correlation with churn magnitude, no
  measured rate-limit pressure) - real and bursty, but its cost does not rise to a
  material bottleneck under the live config, which is (ii), not (i). CH2 separately
  ruled out mechanism (b) *definitively* for the specific instability event that
  prompted this investigation (the live "taxing everything downstream and upstream"
  report) - the actual cause (`GET /api/quality/summary` blocking the event loop,
  now fixed) has no relationship to subscription churn at all, which is (iii) for that
  symptom specifically. Neither result supports (i); per this investigation's own stop
  rule, Phase P2.5 (`docs/superpowers/plans/2026-08-25-realtime-data-plane-remediation.md`)
  stops at CH3 - CH4/CH5 (solution-family research/benchmarking) do not run.

  **Re-grounded against current live state (2026-08-27), not just CH1's original
  measurement window**: `GET /api/config` still shows `kalshi.min_volume_24h: 10000`,
  `categories: ["Sports"]`, `max_children_per_parent: 5`,
  `trade_stream_exchange_wide: true` - the same config CH1 measured under - and
  `GET /api/state` shows a 12-ticker live watchlist, inside CH1's own observed 8-13
  range. Nothing has shifted since CH1/CH2 ran; this classification is not stale
  relative to current config.

  **What would change this classification:** a materially larger watchlist (a lower
  volume floor, more categories than just Sports, `max_children_per_parent`
  raised/unset, or non-exchange-wide scoped mode - which alone would raise frame count
  from <=2 to up to 4 per churn burst per CH1's own frame-count analysis) could push
  frame count, `send_initial_snapshot` cost, or queue correlation into a different
  regime than what CH1 measured at today's scale. This is exactly what Phase P3.5
  (below, in the same plan) tests live - **this classification is provisional pending
  its result, not final.** See P3.5's own header note and Task 17b's Step 5 for the
  addendum contract; check for that addendum before treating CH4/CH5 as permanently
  out of scope.

  **CH3 addendum (2026-08-27) - provisional status not resolved by Phase P3.5's live
  attempt.** Task 17b ran `tools/watchlist_scale_stress_test.py` live, 3 separate
  times, specifically to test the "materially larger watchlist" condition named
  above. All 3 attempts crashed before completing even the first (`widen_scope`)
  step's measurement, for a confirmed reason unrelated to the widened scope itself -
  see the dedicated "Phase P3.5 live-scale attempt" entry below for full detail and
  root cause. **No real widened-scope pipeline or subscription-churn data was
  captured in any attempt** - CH3's classification is therefore neither confirmed nor
  reopened by this run; it remains exactly as stated above, genuinely untested at
  wider scale, pending a future successful rerun.

  **H11 verdict, current state:** confirmed real (mechanism traced, observability
  live), confirmed bursty (not continuous, ~15s catalog-refresh cadence), confirmed
  negligible in cost at current scale (CH1), and confirmed uninvolved in the one
  concrete symptom that motivated the original report (CH2). Not a confirmed
  bottleneck today. Commit: `docs: classify H11 (CH3)`.

## Phase P3.5 live-scale attempt (2026-08-27) - 3/3 runs failed before producing
   comparable data

Task 17b (`docs/superpowers/plans/2026-08-25-realtime-data-plane-remediation.md`) ran
`ddev exec -s fastapi python -m tools.watchlist_scale_stress_test --measure-after-sec 180`
three times against the live paper-mode app. **Safety gate confirmed before and
independently re-confirmed after all three attempts**: `GET /api/config` showed
`mode: paper`, `kalshi_account.trading_enabled: False` throughout - never touched.

**Baseline (pre-experiment, `GET /api/health/pipeline` at 2026-08-27 22:31 UTC)**, kept
for reference since no widened-scope comparison could be completed: `markets_watched:
12`, `ingest.queue_health.queue`: depth 0/capacity 20000/high_water 5699,
`queue_wait.window`: avg 0.9788s/max 1.5241s, `server_errors.total: 0`, `connection`:
connects 2/reconnects 1, `subscription_churn`: syncs_total 1/tickers_added_total 5/
tickers_removed_total 0. `loop_watchdog.stall_max_ms` history (1h) mostly 160-2789ms,
with one 91291.673ms outlier at the very tail of the baseline window (22:31:32 UTC) -
not caused by the experiment (nothing had been patched yet); noted for completeness,
not chased (out of this task's scope).

**All 3 attempts failed identically**: `run_stress_steps` posts the `widen_scope`
config patch (`kalshi.min_volume_24h: 1000`, `kalshi.categories: ["Sports",
"Crypto"]`), sleeps 180s, then `GET /api/health/pipeline` (5.0s client timeout) -
which timed out every time, raising `TimeoutError` inside `urlopen`, before any
subsequent step ran. **Zero comparable data exists for any of the 5 config steps or
the search probe** - `markets_watched`, `queue.depth`/`queue_wait`,
`server_errors`/`reconnects`, `loop_watchdog.stall_max_ms`, and the 3
`subscription_churn` counters cannot be compared against baseline at any
widened-scope step from this run.

**Root cause, confirmed via `ddev logs -s fastapi -t` timestamps, not assumed - not
the widened Kalshi scope at all:**
- **Attempt 1** (`POST /api/config` 22:32:07.386 UTC): during the 180s sleep, uvicorn
  `--reload` restarted the shared `fastapi` process **twice** (`WatchFiles detected
  changes in '.claude/worktrees/agent-a3b129ee5c937a06e/tests/test_http_client.py'`
  at 22:34:33, then `.../test_trading_gate.py` at 22:34:55) - an unrelated concurrent
  Claude Code session's own isolated worktree (branch
  `fix/xdist-parallel-test-isolation`), not this experiment's own edits. `finally`'s
  revert `POST /api/config` succeeded at 22:35:21.618 (200 OK).
- **Attempt 2** (`POST /api/config` 22:38:36.007): landed mid-burst of **4** reload
  cycles in 46s (22:37:32-22:38:18.7), from a *different* concurrent worktree session
  (`.claude/worktrees/kanban-milestones-subissues`, branch
  `feat/kanban-sync-milestones-subissues`). Revert POST succeeded at 22:41:52.025
  (200 OK).
- **Attempt 3** (`POST /api/config` 22:42:57.293): `GET /api/health/pipeline` timed
  out again, and this time the `finally` block's own revert `POST` *also* raised
  `TimeoutError` client-side - the tool's own exception-handling network call is not
  immune to the same collision. The mutation still committed server-side despite the
  client never reading the response (confirmed below) - FastAPI finished handling the
  request before the socket closed, the client just didn't get to see it in time.

**Independent re-confirmation after all 3 attempts** (Task 17b Step 4, run separately
from the tool's own `finally` block each time, not assumed to have worked just
because the code has a `finally`): `GET /api/config` showed
`kalshi.min_volume_24h: 10000`, `kalshi.categories: ['Sports']`,
`kalshi.live_markets_only: True`, `kalshi.max_children_per_parent: 5`,
`strategy.live_markets_only: False`, `whale_watcher_kalshi.min_contracts: 10000`, and
the full original `min_contracts_by_series` map (`KXBTC15M: 2500`, `KXBTCD: 2500`,
`KXETH15M: 2500`, `KXETHD: 2500`, `KXTRUMPSAY: 500`, `KXTRUMPMENTION: 500`,
`KXMAMDANIMENTION: 500`) - **correct and complete after every one of the 3 attempts**,
including the one where the revert POST's own response was lost client-side. `mode:
paper` / `trading_enabled: False` unaffected throughout.

**New finding, more significant than the original queue-depth/REST-demand question
this phase set out to answer**: live multi-minute measurements against this app's
shared ddev `fastapi` service are not reliable while any other Claude Code session
has its own in-repo worktree (`.claude/worktrees/<name>/`) open, because that
worktree still lives inside ddev's bind-mounted repo tree, which `uvicorn --reload`'s
`WatchFiles` watches recursively - **any `.py` file saved in *any* worktree, not just
the primary checkout, restarts the one shared `fastapi` process** and drops in-flight
connections. Measured twice with two different concurrent sessions across 3
attempts, not a single coincidence. Same mechanism would apply to the real trading
loop, not just this diagnostic tool - a concurrent worktree save mid-tick could drop
an in-flight WS reconnect or REST call exactly as it dropped this experiment's HTTP
calls; worth a dedicated follow-up (not attempted here - out of this task's docs-only
scope) on whether `uvicorn --reload`'s watch path should exclude
`.claude/worktrees/` (e.g. via `--reload-exclude`).

**`services/capture_writer.py` / `services/series_watcher.py` SQLite lock contention
- independently verified (2026-08-27), not this experiment's own doing.** `GET
/api/health/faults` (checked directly, not taken on trust): `capture_writer`/`flush`/
`OperationalError: database is locked`, count **178**, `first_seen` 2026-08-27
20:22:15 UTC, `last_seen` 22:41:29 UTC. Already at 177 in this session's very first
baseline pipeline read (22:31:39 UTC, before any config was touched) and only 178 by
22:42 - i.e. this fault predates the P3.5 experiment by 2h+ and grew by exactly 1
across all 3 (mostly-crashed) attempts combined, so **this run does not show evidence
that widened scope materially changes the collision rate** - inconclusive rather than
negative, since none of the 3 attempts sustained the widened config for more than the
~180s sleep window before crashing, leaving no clean widened-vs-baseline window to
compare. Root cause (`services/capture_writer.py:122`'s `_flush_store`, `PRAGMA
busy_timeout=50` on its `raw_trades` connection to `data/series_watcher.db`,
colliding with `series_watcher.py`'s independent `book_snapshots` connection to the
same physical file): a real, ongoing raw_trades completeness gap under CLAUDE.md's
data-plane HARD RULE - each collision drops the **whole batch**, by `_flush_store`'s
own "never raises" design, counted only in `dropped_count()`, not retried. **Not
fixed here** - the 50ms `busy_timeout` was a deliberate Task 14 tradeoff ("never
sleeps five seconds on any thread that matters" per its own docstring); changing it
needs its own dedicated investigation, not a fix folded into this measurement task.
Cross-posted to `services/observability/README.md`'s `writer.*` section.

**Cross-post disposition for the plan's other 4 named candidates**
(`services/market_watch/CHEATSHEET.md`, `services/market_catalog/CHEATSHEET.md`,
`services/kalshi/CHEATSHEET.md`, `services/quality/README.md`): **none received a
cross-post.** None of the 5 `widen_scope*` config steps produced any comparable
pipeline data for these domains - all 3 attempts crashed at Step 1 before any
widened-scope measurement was captured, so there is no material result of their own
to report into any of them. `services/observability/README.md` is the one exception
(above) because the capture_writer finding materially updates something that doc
already documents (its "Unwired today" framing, now stale).

## Phase P3.5 live-scale attempt, successful run (2026-08-27, post-reload-fix)

Re-run of the same `tools.watchlist_scale_stress_test` (`--measure-after-sec 120`) after
the uvicorn reload-collision root cause above was actually fixed (`.ddev/docker-
compose.fastapi.yaml` gained `--reload-exclude /app/.claude/worktrees`, PR #123, merged
and loaded via `ddev restart` at 2026-08-27 23:15 UTC; end-to-end confirmed live by
touching a real file inside an active worktree and observing no reload before this run
started). **This is the first of the 4 total attempts to run to completion** - all 5
`widen_scope*` steps plus the search probe, uninterrupted. Safety gate re-confirmed
before and after: `mode: paper`, `kalshi_account.trading_enabled: False` throughout.

**Side-finding first, since it explains an oddity in this run's own baseline: the live
app's config had already drifted from `config/settings.yaml`'s committed defaults**
before this run even started - `categories: ['Sports', 'Crypto']` and
`min_volume_24h: 1000` (should be `['Sports']` / `10000`), left behind by one of the 3
earlier failed attempts whose `finally`-block revert either didn't run cleanly or was
itself caught in a reload-restart. `run_stress_steps` correctly captured and reverted to
*that* (already-widened) state as its own "original," so its own revert logic isn't at
fault - the live app had simply been running in a wider-than-intended configuration for
several hours before this run. Restored to the real committed defaults by hand
immediately after discovering this (`POST /api/config` with `config/settings.yaml`'s
values) - confirmed via `GET /api/config` afterward. Worth remembering for any future
live experiment: don't trust `run_stress_steps`' captured `original_config` as proof of
"the intended default" - it only proves "whatever was live when this run started."

**Headline result: zero data loss at every step.** `dropped_messages: 0` and
`last_tick_rate_limit_hits: 0` in all 5 `GET /api/health/pipeline` snapshots, cumulative
`ingest.messages_received` climbing 34,117 → 116,445 over the ~10-minute run (roughly
1,400-2,700 trade-class messages per 2-minute step window). Queue depth stayed
effectively empty between snapshots (`queue.depth` 0-1) and peak `high_water` across the
whole run was 2,593 of a 20,000 capacity (13%) - no evidence of the widened scope
(`categories: [Sports, Crypto]`, `min_volume_24h: 1000`, `live_markets_only` toggled off
then back on, `max_children_per_parent` uncapped, `whale_watcher_kalshi.min_contracts`
lowered to 500-1000) pushing the consumer anywhere near its capacity ceiling at this
account's current live market population. Per-step 2-minute-window `queue_wait.max_sec`
stayed under ~6.5s at every step (`widen_scope`: 6.476s; the other 4: 0.665-3.961s) -
healthy, not the multi-minute backlog H1/H2 hypothesized.

**`markets_watched` barely moved** (15 → 15 → 13 → 13 → 14) despite the scope-widening
config changes across all 5 steps - at this account's current live market population,
none of `min_volume_24h`, `categories`, `live_markets_only`, `max_children_per_parent`,
or the whale-signal `min_contracts` thresholds materially changed the REST-discovered
watchlist size. This means this run mainly stress-tested the **exchange-wide trade
WebSocket's** ingest/queue/consumer path (which was already exchange-wide regardless of
watchlist size, per `trade_stream_exchange_wide: true` and the CHEATSHEET's own "How do
you subscribe to the WHOLE exchange" entry) rather than the REST catalog/discovery path
P4/P5 also care about - a real result, but a narrower one than the step names imply;
REST-demand-at-scale remains untested by this run.

**Useful incidental confirmation for the P3 reader-gate flip decision:** the shadow-mode
`prefiltered.trade` / `gate_would_reject` counters (added by Task 17's
`_gate_check_and_maybe_filter`, still shadow-only per `reader_gate_enabled: false`) show
the gate would have rejected ~99.6-99.9% of received trade-class messages at every step
(e.g. step 1: 32,510 of 32,559 trade messages; step 5: 111,142 of 112,031) - consistent
with the P3 gate design's whole premise (most exchange-wide trade volume is below the
whale-size threshold), and this holds up under a genuinely widened scope, not just the
narrow single-series baseline it was originally measured against.

**One 89.3-second stall/queue-wait outlier appears identically in every step's `lifetime`
stats** (`loop_watchdog.stall_max_ms` max 89189.525, `queue_wait.lifetime.max_sec`
89.2997, `handler_time_by_class.trade.lifetime.max_ms` 89300.1559) alongside an identical
`connection.last_disconnect` timestamp (`ConnectionClosedError: ... keepalive ping
timeout`) in every step - a single historical WS disconnect/reconnect event sitting in
the metrics' lifetime/1h-lookback window, not something that recurred across steps or
was caused by this run's own load (each step's own 2-minute `window` stats stayed low;
only the carried-forward `lifetime` max reflects it). Not chased further - out of this
measurement task's scope, and it predates this run.

**`capture_writer`/`series_watcher` SQLite lock contention - fresh data point, still not
fixed, but now leaning toward "not scope-correlated."** `GET /api/health/faults` showed
`capture_writer`/`flush`/`database is locked` at count **181** (`last_seen` 2026-08-27
23:46 UTC) versus the **178** recorded in the previous (crashed) attempt's write-up
(`last_seen` 22:41 UTC) - only **+3** across roughly 5 hours that included this run's
first-ever full, clean, sustained ~10-minute widened-scope window (the 3 earlier attempts
never got a clean window at all). That's a materially smaller increment than the
2h-baseline rate implied by the original 177→178 datapoint, weakly suggesting the
collision rate is dominated by the two writers' independent flush/snapshot cadences
(Task 14's 50ms `busy_timeout`) rather than by trade volume or watchlist scope - still
**not proven** (one run, small numbers, no controlled A/B), so this stays a directional
update, not a resolved conclusion. The underlying fix (documented in the prior section)
remains open and out of scope for this measurement task.

**Cross-post disposition, superseding the prior "none received a cross-post" note:**
`services/observability/README.md`'s `writer.*` section already covers the lock-
contention finding (no change needed here). None of `services/market_watch/CHEATSHEET.md`,
`services/market_catalog/CHEATSHEET.md`, `services/kalshi/CHEATSHEET.md`, or
`services/quality/README.md` are getting a cross-post from *this* run either - despite
now having real data, none of it is REST-catalog/discovery-path data (per the
`markets_watched` finding above), which is the layer those four docs cover; this run's
real findings are entirely in the exchange-wide WS ingest/queue path, which doesn't have
its own `CHEATSHEET.md`/`README.md` outside this research doc and
`services/kalshi/websocket.py`'s own module docstring.

## What the investigation must not assume

Do not assume any of the following is automatically correct:

- more consumer workers;
- one queue per message type;
- one WebSocket connection per traffic class;
- a priority queue;
- a coalescing ticker map;
- Redis/NATS/Kafka/ZeroMQ;
- uvloop;
- a process pool;
- more REST rate;
- a larger queue;
- a smaller subscription;
- REST reconciliation;
- a different SDK.

All are candidates only if they fit the measured workload and repo constraints.

## Desired outcome

The investigation should produce a causal explanation and an architecture decision that
answers:

1. Where are messages actually lost?
2. Where are they merely delayed?
3. Which delay is local queueing, CPU, DB, event-loop, REST limiter, or upstream network?
4. How complete is WS capture compared with REST?
5. How many true whale candidates exist relative to total trade flow?
6. Which activities consume the Kalshi read budget?
7. Which architecture gives the best combination of:
   - capture completeness;
   - p95/p99 whale decision latency;
   - account/lifecycle reliability;
   - REST efficiency;
   - ordering correctness;
   - recovery behavior;
   - implementation simplicity;
   - observability;
   - maintainability?
