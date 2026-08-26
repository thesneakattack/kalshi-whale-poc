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
- **Not yet measured**: the actual downstream/upstream *cost* of a churn burst - how many
  WS frames one `_sync_subscriptions` call sends (recall it replicates the same
  add/delete_markets payload across both the `trade` and `ticker` channel sids in scoped
  mode), whether Kalshi's `send_initial_snapshot: True` ticker resubscribe behavior applies
  per-add or only on first subscribe, whether a burst measurably correlates with ingest
  queue depth/latency or REST demand, and whether a distinct, not-yet-traced app
  unresponsiveness event observed live immediately after an 8->13 burst was actually caused
  by this mechanism or was coincidental/a different bug (two unrelated event-loop-blocking
  bugs were found and fixed the same day in unrelated modules - `candidate_log.
  population_gate_summary` and `whale_calibration` routes - so a third, distinct cause is not
  ruled out).

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
