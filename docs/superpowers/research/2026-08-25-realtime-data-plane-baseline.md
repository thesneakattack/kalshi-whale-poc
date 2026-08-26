# Realtime Kalshi Data-Plane — I0 Baseline and Causal Investigation Map

**Task:** I0 of `docs/superpowers/plans/2026-08-25-realtime-data-plane-investigation.md`.
**Date:** 2026-08-25.
**Status:** investigation baseline. Nothing in this document upgrades a hypothesis to
"confirmed" — it records the exact current topology, the exact current measurement
surface, live point-in-time readings, and what evidence would confirm or falsify each
hypothesis from the known-findings file.

## 1. Re-grounding record

- **Branch:** work committed on `chore/realtime-dp-investigation`, cut from `main` at
  `a0c1569` (merge of PR #10, which added the investigation plan/spec/skill). `main` was
  synchronized with `origin/main` at branch time; working tree clean apart from an
  untracked, unrelated `BUNDLE_README.md`.
- **Runtime mode (live, verified via `/api/state` + `/api/health/pipeline`, not
  assumed):** `mode: paper`, `running: true`, whale source `kalshi_trade_tape
  (websocket)` — i.e. **streaming mode is active**, the REST trade-tape poll branch is
  dormant. Real trading disabled (`kalshi_account.trading_enabled: false`, standing P0
  gate).
- **Stream config (config/settings.yaml):** `trade_stream_exchange_wide: true`,
  `market_lifecycle_stream_enabled: true`, `poll_interval_sec: 6`,
  `request_timeout_sec: 10`, `watchlist_size: 150` (live watchlist at observation time:
  **12 markets**).
- **Library versions (verified inside the `fastapi` container):** Python 3.13.15,
  `websockets` 17.0.1, `httpx` 0.27.2.
- **Limiter constants (services/http_client.py):** read bucket 8.0 tokens/s, burst 8;
  write bucket 1.5/s, burst 1. The connected account's verified real budget
  (docs/kalshi/CHEATSHEET.md rate-limit entry) is 200 read-tokens/s with a 600-token
  burst pool at 10 tokens/request ⇒ ~20 req/s sustained, ~60-request burst. The local
  read limiter therefore sits at ~40% of the verified sustained ceiling and ~13% of the
  verified burst pool.
- **Ingest constants (services/kalshi/websocket.py):** `websockets` transport
  `max_queue=1024`, ping 20s/20s; application `asyncio.Queue(maxsize=20000)`; one serial
  consumer per connection.
- **Dedupe constants (services/whalewatchers/kalshi_trade_tape.py):**
  `_MAX_SEEN_TRADE_IDS = 250000` (live: **at cap** — the ring is actively cycling),
  `_MARKET_CACHE_TTL_SEC = 300`, `_MAX_ONDEMAND_MARKET_FETCH = 100`,
  `get_markets_by_tickers` chunk size 50.

## 2. Exact current WebSocket message path

Two physically separate `KalshiStreamGateway` connections (`services/app_state.py`):

- **`trade_stream`** — channels: `trade` (exchange-wide, no market list), `ticker`
  (watchlist-scoped), `market_lifecycle_v2` (exchange-wide, opt-in, on), `fill` +
  `market_positions` (account-wide).
- **`index_stream`** — channels: `cfbenchmarks_value` / `pyth_value` only. Physically
  isolated after live evidence (2026-08-17) that ~1/s index ticks starved behind
  exchange-wide trade traffic on a shared connection.

Per-connection path (`services/kalshi/websocket.py::run`):

```text
Kalshi server
  ↓ (TCP/TLS)
`websockets` 17.0.1 receive buffer          max_queue=1024, ping 20/20
  ↓
reader loop                                  per iteration: create update_event.wait()
                                             task + ws.recv() task, asyncio.wait
                                             FIRST_COMPLETED, cancel the loser
                                             (≈2 task creations + 1 cancellation
                                             PER MESSAGE, on the event loop)
  ↓ messages_received += 1; queue.put_nowait
app asyncio.Queue(maxsize=20000)             overflow → dropped_messages += 1
                                             (aggregate int: no type, no timestamp,
                                             no age; message discarded silently)
  ↓
one serial _consume() task                   exceptions swallowed by bare
                                             `except Exception: pass` — no log line,
                                             no fault_log entry, no counter
  ↓ json.loads → dispatch on data["type"]
channel-specific contract normalizer (services/kalshi/contracts/)
  ↓
application callback (services/whale_stream/whale_stream_handlers.py)
```

Everything below the queue runs on the single consumer task, so per-message handler cost
is directly the connection's service rate: at N ms average handler time, sustainable
throughput is 1000/N msg/s for the whole connection (all channels combined).

### 2.1 `trade` message → `_process_stream_trade` (the dominant hot path)

Stage by stage, with execution context and I/O class:

| # | Stage | Context | I/O |
|---|---|---|---|
| 1 | `trade_id` presence check (discard if missing) | event loop | none |
| 2 | `state["trade_tape"]` insert + slice to 100 | event loop | none |
| 3 | `series_watcher.record_trade(trade, config_store.get())` | event loop | buffered append; watched-series-only; flush elsewhere |
| 4 | gate: `state["running"]` and streaming enabled, else return | event loop | none |
| 5 | `config_store.get()` + `config_performance.fingerprint(cfg)` | event loop | fingerprint computed **per message** |
| 6 | `whale_provider.fetch_signals(...)` → `_resolve_unknown_markets` | event loop | dedupe-membership check, `_prescan_count`, market-cache check; **REST `get_markets_by_tickers`** (≤100 tickers, ≤2 chunks) only when a whale-sized off-list print needs a market |
| 7 | `asyncio.to_thread(_process_trades_sync, …)` | **thread hop, per message** (not per candidate — entered even for a 1-trade tape that will be rejected in microseconds) | see below |
| 8 | `_process_trades_sync`: dedupe → `_mark_seen` → per-series count → market lookup → rejection paths write `candidate_log.record_rejection` (SQLite) → qualifying prints read `signal_log.recent_sides_for_ticker`, `signal_log.cluster_factor`, `market_history.momentum`, `market_analyst_agent.analyst_lean` (each SQLite) → composite scoring | worker thread | SQLite reads/writes as listed |
| 9 | per returned signal: `_handle_signal` → `signal_log.log_signal` (SQLite), `strategy.evaluate`, decision feed, `ws_manager.broadcast` task | event loop | SQLite + broadcast |
| 10 | `strategy.check_exits(...)` (only when signals were emitted) | event loop | reads in-memory state |
| 11 | `bump_generation()` | event loop | none |

Live measurement already built in (`trade_stream_perf`, 1-second windows):
`fetch_signals_calls_per_sec == messages_per_sec` — the provider (and its thread hop) is
entered for **every** trade message that passes stage 4.

**`_mark_seen` ordering fact (H4-relevant, code-verified, not yet test-proven):**
`_mark_seen(trade_id)` fires in `_process_trades_sync` for every previously-unseen trade
*before* the market-resolution outcome is known. A failed
`get_markets_by_tickers` batch in `_resolve_unknown_markets` caches nothing and returns
(`resolve_failures += 1`); the trade then reaches `_process_trades_sync` with no market,
is logged as a `market_unresolved` rejection, and is permanently deduped. The WS path
presents each `trade_id` exactly once (a 1-item tape per message), so there is no
natural re-presentation. Separately, a *successful* batch that omits a ticker caches
`None` for 300 s, suppressing retries from subsequent prints on that market within the
TTL. This is the exact mechanism I3 must reproduce or falsify under test.

### 2.2 `ticker` message → `_process_stream_ticker`

Watchlist-scoped (12 markets live), so low rate. Per message: `series_watcher.record_book`
(per-ticker throttled), `latest_prices` update, matched-market ask update,
`market_history.record_snapshot_from_ticker` (SQLite), and — whenever `running` and
`signal_feed` is non-empty — a full `strategy.check_exits(...)` pass on the event loop.

### 2.3 `market_lifecycle_v2` → `_process_stream_lifecycle`

Exchange-wide. Counted per event type in `lifecycle_stream_stats`. `close_date_updated`
→ in-memory + `market_catalog.apply_lifecycle_update` (SQLite). `determined` → catalog
status (SQLite). `settled` → catalog status + **REST `get_market(ticker)`** + up to four
resolver writes (`market_history.record_outcome`, `settlement_edge.resolve_window`,
`market_analyst_agent` / `candidate_log.resolve_from_market_results`). Live cumulative
counts this process: created 2,498 / determined 4,319 / settled 5,972 /
close_date_updated 693 / deactivated 513 / metadata_updated 48.

### 2.4 `fill` / `market_position` → account-state updates

In-memory only (dedupe by `trade_id`; positions keyed on canonical `ticker`). Inert in
practice while `trading_enabled` is false, but they share the same queue and consumer —
their latency class is coupled to trade-burst behavior (H2's subject).

### 2.5 Kalshi-side `error` messages (error 25 included)

`_handle_message`'s `error` branch only forwards a transient status text to
`on_status` → `state["trade_stream_status"]["error"]` (overwritten by the next status
update) and `state["error"]`. **Nothing counts server-side errors; error 25 is
indistinguishable after the fact from any other transient error string.** The four
failure points the findings file requires distinguishing (server subscription overflow /
`websockets` buffering / app-queue overflow / downstream backlog) currently collapse to
two observables: `dropped_messages` (app queue only) and an ephemeral status string.

## 3. Exact current REST demand graph

All REST goes through one shared read token bucket (8/s, burst 8) in
`services/http_client.py::call_with_backoff` (429-only retry, max 4 retries, exp backoff
0.5 s base + jitter). Classification uses the plan's taxonomy:

| Caller | Endpoint(s) | Trigger/cadence | Class |
|---|---|---|---|
| `kalshi_trade_tape._resolve_unknown_markets` | `get_markets_by_tickers` (≤100 tickers → ≤2 calls) | whale-sized off-list print, cache-missed | **whale-critical** |
| `whale_stream` `settled` handler | `get_market` | per settled event (~0.06/s historical) | whale-critical (outcome grading) |
| `_fetch_markets` (+ `extra_tickers` for open positions) | `get_markets` / `get_markets_by_tickers` | every 6 s tick | position/risk-critical |
| `_fetch_account_snapshot` | balance/positions/fills (account client) | 20 s cache, else per tick | position/risk-critical |
| `_fetch_exchange_status` | `get_exchange_status` | every tick | position/risk-critical (halt detection) |
| `_fetch_live_status` | `get_milestones_for_event` ≤10/tick + `get_live_datas` | 5-min repoll per event, ≤10 polls/tick | background_live_status |
| `_fetch_event_live_data` | `get_event_live_data` | 60 s repoll per event | background_live_status |
| `_fetch_event_titles` / `event_metadata` | `get_events` (chunked) | per tick, cache-missed events only | background_catalog |
| `_fetch_category_metadata` | tags/sports filters | 1 h TTL | background_catalog |
| `catalog_scan._maybe_scan_catalog_batch` | `get_series_list` (rotation), `get_markets(series_ticker=…)` ×10/batch, milestones + `get_live_datas` + `get_markets_by_tickers` | batch kickoff every ≥15 s (background task) | background_catalog |
| `_refresh_discovery_cache_background` | `get_markets_by_tickers` (confirmation) | every ≥300 s | background_discovery |
| `_maybe_check_signal_resolutions` | `get_markets_by_tickers` (unresolved-signal batches) | every ≥30 s (background task) | background_resolution |
| `event_schedule._maybe_resolve_event_schedules` | `get_milestones_for_event` | every ≥30 s kickoff, 12/24 h per-event retry | background_resolution |
| dashboard/diagnostics routes (`market_catalog/routes`, `diagnostics/routes`, `event_inspector`, analyst) | market/event/orderbook/candlesticks/trades | on human request | interactive |
| `diagnostics.py` coverage check | `get_trades(ticker=None, limit=1000)` paged | on-demand only | interactive (existing prior art for I4 — samples exchange-wide REST trades and compares whale-sized prints against the *watchlist*, but does **not** reconcile trade_ids against WS capture) |

Structural facts (not judgments): whale-critical enrichment shares the same token bucket
with every background class; `asyncio.create_task` backgrounding removes control-flow
blocking but not token contention (H7's subject). No priority, fairness, or reservation
semantics exist anywhere in the limiter.

## 4. Measurement surface: what exists vs. what is missing

### Exists today (reused, per observability-performance discipline)

- **WS:** `messages_received`, `dropped_messages` (both aggregate ints per connection);
  `trade_stream_perf` 1-s windows (trade-channel msg/s, avg handler ms, fetch_signals
  rate/avg); `trade_stream_status` (connected/error text, latest only);
  `lifecycle_stream_stats` (counts by event type + close-time/catalog/outcome apply
  counts); index-stream counterparts.
- **Provider:** `stats` = prescanned / whale_sized_offlist / markets_resolved /
  resolve_failures; `candidate_log` rejection rows by reason (incl. `market_unresolved`);
  `signal_log` for emitted signals; `/api/health/pipeline`'s `ingest` block (incl.
  dedup-ring occupancy and store growth: `raw_trades` 16.88 M rows live).
- **REST:** per-endpoint-family `http_metrics` (calls / successes / errors /
  rate_limited / avg success latency), reset per tick into `observability.db`
  (`kalshi_rest.*`); per-tick `rate_limit_hits`; tick duration + phase timings.
- **Persisted trends:** `observability.db` 60 s sampling of all of the above; runtime
  quality findings for drops>0, tick>interval, repeated 429s, stream disconnects.

### Missing (mapped to the design spec's required metrics — this is I1/I2/I5 scope)

- received/processed **by message type**; queue depth / high-water; **oldest-message
  age**; queue-wait distribution (p50/p95/p99/max); drops **by type** or by time window
  (only a lifetime int exists — a 9% cumulative drop rate cannot currently be
  attributed to bursts vs. sustained overload, which is exactly the H1-vs-H10
  distinction);
- Kalshi server-side error counts (error 25 specifically) and reconnect count/reason —
  reconnects currently leave no counter at all, only log lines;
- consumer-exception counter (currently silently swallowed);
- handler time **by message class** (only the trade channel is timed);
- whale-pipeline stage latencies (receive→prescan→candidate→context→analytics→decision)
  — only the aggregate `fetch_signals` timer exists;
- REST **limiter-wait** time, **backoff-sleep** time, caller-experienced total elapsed,
  attempts-per-logical-call, and any **caller-class** attribution. Note the precise
  current semantics: `avg_latency_ms` is measured from *after* `limiter.acquire()`
  returns to response receipt, success-only — so limiter wait is not conflated into the
  recorded number; it is **invisible**. No currently-recorded metric can show a caller
  waiting seconds in the local bucket (H6 cannot be judged from existing data).
- p50/p95/p99 anywhere (all latency aggregates are means).

## 5. Live point-in-time readings (2026-08-25, process uptime — not a controlled window)

Read from the running paper-mode instance before any change:

- `trade_stream`: 558,643 received / **50,248 dropped ≈ 9.0% cumulative** since process
  start; connected, no current error. `index_stream`: 8,501 received / 0 dropped.
- One sampled 1-s window at observation time: 396.9 trade msg/s, avg handler 2.268 ms
  (⇒ implied service ceiling ≈ 441 msg/s at that cost — ~90% utilization *in that
  window*), `avg_fetch_signals_ms` 2.152 (≈95% of handler cost inside the provider
  call). A second window minutes later: 212.5 msg/s at 4.594 ms (⇒ ceiling ≈ 218 msg/s
  — ~98% utilization). Both are single windows, not distributions; I1 owns making this
  a real measurement.
- Provider: prescanned 463,314; whale_sized_offlist 530; markets_resolved 510;
  resolve_failures 0; dedup ring at cap 250,000/250,000.
- Trading loop: `last_tick_duration_sec` 0.69–1.41 across observations vs. 6 s
  interval; `rate_limit_hits` 0; phase timings all sub-second.
- App stats: signals_seen 308, trades_placed 18, skipped 301.

## 6. Ten-minute no-change baseline (I0 controlled window)

Method: read-only sampler (GET `/api/observability/current` + `/api/state` every 15 s,
41 samples ≈ 10 min) against the live paper-mode instance. No configuration, code, or
state was changed during the window. Raw samples retained in session scratchpad only
(deliberately not committed; bounded summary below).

**Window:** 651 s wall, 41 samples attempted, 40 usable (sample 2 at t+31 s lost: the
sampler's own `GET /api/observability/current` hit its 10 s read timeout — the API, which
shares the one event loop with the WS reader/consumer, was unresponsive for >10 s).

**Throughput and loss (deltas of the existing lifetime counters):**

| Metric | Value |
|---|---|
| `trade_stream` messages received (all channel types) | 87,209 (avg 133.9 msg/s over the window) |
| `trade_stream` messages dropped at the app queue | **958 (1.10% of the window's arrivals)** |
| `index_stream` received / dropped | 1,303 / 0 (~2/s, isolated connection healthy) |
| trade-channel 1-s windows (sampled every 15 s): msg/s | min 1.1 · p50 148 · p95 322 · max 383 · mean 168 |
| `avg_handler_ms` per 1-s window | min 0.36 · p50 3.19 · p95 10.08 · **max 793.2** |
| `avg_fetch_signals_ms` per window | p50 3.04 · p95 9.93 · max 793.0 (≈95% of handler cost at every quantile) |
| consumer utilization (`msg/s × handler_ms / 1000`) | min 0.01 · **p50 0.65 · p95 0.98 · max 1.00** |
| implied service ceiling (`1000 / handler_ms`) | 314 msg/s at p50 handler cost; **99 msg/s at p95 handler cost** |
| tick duration | p50 0.62 s · max 5.91 s (4 of 40 ticks >3 s against a 6 s interval) |
| `rate_limit_hits` | 0 across the window |

**Time correlation of notable events (correlation, explicitly not causation):**

- **t+129 s:** the *only* interval with drops — all 958 landed in one 16-s interval whose
  average arrival was an unremarkable ~199 msg/s (all types) — coincided with a **5.79 s
  tick**. A 20,000-slot queue cannot fill from a 200 msg/s arrival in 16 s from empty
  (that needs ≥100 s of zero service), so the queue was almost certainly already deep
  entering that interval. Whether the slow tick starved the consumer or merely co-occurred
  with an already-full queue is exactly what I1's queue-depth/oldest-age series must
  answer.
- **t+332 s:** a 4.65-s reporting window in which only 5 trade messages completed, each
  spending ~793 ms inside `fetch_signals` — a ~4 s consumer stall. It produced **no drops**
  (≈600 messages of backlog at that arrival rate, well inside the queue), showing the queue
  does absorb short stalls; the loss event at t+129 s was a different, larger regime.
- **t+31 s:** API unresponsive >10 s (sample lost). No drop counter movement in that
  interval, so the reader/consumer were not necessarily stalled at the same time — but
  the single shared event loop is the common resource in all three events.

**What the window supports and does not support:**

- Handler cost is **not a constant**: it varies ~2,200× between windows (0.36 → 793 ms),
  and even the p50→p95 spread (3.2 → 10.1 ms) moves the implied ceiling from 314 to
  99 msg/s — i.e. at p95 handler cost, capacity is *below the p50 arrival rate*. Backlog is
  built in the cost tails, not by a steady overload; this is why an average-only figure
  cannot adjudicate H1 and why the design spec forbids judging on averages.
- Utilization is understated: `trade_stream_perf` times only trade-channel messages, so
  ticker/lifecycle/fill/position handler time (unmeasured — §4) is missing from the
  numerator while their messages still occupy the same consumer.
- Sampling covers one 1-s window per 15 s; intra-gap bursts are unseen, so the p95/max
  arrival figures are lower bounds on true burstiness.
- Drops in this window (1.1%) were far below the process-lifetime rate (9.0%), so the
  lifetime figure reflects heavier periods than this one — a 10-minute sample is not a
  representative-load claim (I7 owns a longer window including a busy period).
- Zero 429s and sub-second p50 ticks: REST rate pressure was **not** active in this window;
  H6/H7 remain unmeasured rather than falsified.

## 7. Hypotheses: current status and confirm/falsify criteria

Statuses used: **untested** (no discriminating evidence yet), **mechanism-visible**
(code path exists and is fully traced, but behavior not yet demonstrated),
**partially-informed** (live point readings consistent with it, no controlled
measurement). Nothing is "confirmed" in this document.

| H | Claim | Status at I0 | Confirming evidence would be | Falsifying evidence would be |
|---|---|---|---|---|
| H1 | Sustained WS arrival can exceed one-consumer service capacity | **confirmed** (I7, 2026-08-25 busy-hour window, `2026-08-25-realtime-live-baseline.md`): queue depth p50 17,441 of 20,000, oldest message p50 119 s / max 209 s, 10.7% of 523k messages dropped, whale receive→decision avg 94 s; nominal handler cost (5.3 ms) was not the binding constraint — ~4 s event-loop stalls in every sample were, coinciding with synchronous tick phases | I1 metrics showing sustained arrival ≥ measured service rate with monotone queue growth/age and drops during *sustained* load, plus I6 replay reproducing it at measured handler-cost distributions | drops confined to short bursts the 20k queue absorbs; sustained arrival p95 comfortably below service rate |
| H2 | One mixed FIFO creates head-of-line blocking for critical messages | **confirmed in effect** (I7): lifecycle and open-position ticker messages waited the same ~2 minutes as trades (shared queue age p50 119 s); I6's critical-first topology shows the same workload would not — a comparison for I10, not a selection | I1 per-class queue-wait showing ticker/fill/position/lifecycle wait scaling with trade backlog depth | per-class waits statistically indistinguishable under trade bursts |
| H3 | Expensive work happens before cheap whale rejection is fully exploited | partially-informed (`fetch_signals` ≈95% of handler cost; provider + thread hop entered on 100% of trade messages; fingerprint computed per message) | I2 stage timings showing the majority of per-message cost in stages that a cheap-first-classifier design would skip for the ~99.9% non-whale flow (thread hop, config fingerprint, provider setup) | stage timings showing per-message cost already dominated by irreducible work (e.g. `record_trade` capture) that any design must pay |
| H4 | Transient off-watchlist enrichment failure permanently loses candidates | **proven defect** (I3, 2026-08-25, `tests/test_whale_candidate_lifecycle.py`): a whale-sized off-list print whose first `get_markets_by_tickers` raises is `_mark_seen` after the failed lookup with no market, recorded only as a `market_unresolved` rejection row, and every later re-presentation of that `trade_id` is short-circuited by the dedupe ring before any lookup is attempted — `wire_seen=True`, `candidate_pending=False`, `terminal_evaluated=False`. A later *different* print on the same market recovers the market but not the lost trade; a successful batch that omits the ticker reaches the same terminal outcome via the 300 s negative cache. Desired behavior pinned as a strict xfail so the eventual fix flips it visibly | I3 deterministic test: whale-sized unknown-market trade + first-lookup failure ⇒ trade never reaches evaluation on any later presentation | code path shown to re-present/retry the same trade_id to terminal evaluation despite the failure |
| H5 | No sufficient WS reconciliation safety net exists | mechanism-visible as a topology fact, now **measurable** (I4, 2026-08-25, `services/diagnostics/trade_capture_reconciliation.py` + `GET /api/diagnostics/trade-capture`): first 2-minute window with zero drops showed capture completeness 1.00 (12,737/12,737; whale-sized 29/29), i.e. no steady leak; **I7 quantified the loss under saturation**: whale-sized capture 107/309 (0.35) across eight busy-hour windows, 0/85 during a full-queue drop episode, 30/30 when the queue was 4 s deep — loss tracks queue saturation and reconnect dumps, and no metric except I4/I1 sees it | I4 reconciliation quantifying real WS capture gaps (esp. whale-sized) during drop windows | I4 showing ~100% capture (then a permanent reconciliation path may be unjustified — H5's remedy, not the gap, would be falsified) |
| H6 | Local limiter wait is mistaken for upstream latency | **confirmed as a measurement** (I5, 2026-08-25, 796 s window in `services/observability/CHEATSHEET.md`): at 1.75 calls/s against an 8/s budget, local limiter wait is the majority of caller-experienced latency for 4 of 6 caller classes (e.g. 533 of 590 ms for unattributed callers, 224 of 403 ms for catalog) with maxima of 1.8–4.5 s; only `critical_position` is upstream-dominated (network avg 643 ms). The budget is depleted by bursts, not average demand | I5 decomposition showing material limiter-wait share on slow calls | I5 showing slow calls dominated by upstream network time |
| H7 | Background REST starves whale-critical calls | partially-informed (I5): background classes held 69% of calls while `critical_whale` waited up to 1.8 s in the local limiter and drew 2 of the window's 4 upstream 429s; the mechanism (shared bucket, no reservation) is fact, the causal link (critical waits *because of* background bursts) still needs I8's demand timeline / I11's fault injection | I5/I8 caller-class demand shares + critical-call wait under normal/burst background load | critical-call wait unaffected by background demand at measured volumes |
| H8 | Batching assumptions (50/chunk) may be stale | **answered — stale** (I8, 2026-08-25, `2026-08-25-rest-demand-study.md`): the docs bill per item only for batch *order* endpoints; this account's endpoint costs are the flat default 10 for every app endpoint; 50/100/200-ticker `GET /markets` requests each completed in one un-throttled request (41/73/106 ms). Local limiter = 40% of the verified 20 req/s and 13% of the verified 60-request burst | I8 measured cost/latency/429 behavior at 50/100/200 chunk sizes against documented account limits | measurements showing 50 already optimal or larger chunks unsafe |
| H9 | Milestone/live-data polling duplicates work | inconclusive (I8): exact per-endpoint-family window counts only exist from I8's fix onward; the probe's duplicate-factor method is in place — run after a hands-off hour before I11 | I8 duplicate-demand measurement showing material overlapping calls for the same milestone/event | negligible measured overlap |
| H10 | Queue size is not the capacity fix | **confirmed** (I7): the 20,000-slot queue delayed the first drop by ~7 minutes and then held every decision ~2 minutes late; the only low-latency moments in the hour followed reconnects that discarded the backlog | I1/I6: service rate < sustained arrival ⇒ any finite queue only delays loss | sustained service rate ≥ arrival (then queue sizing is a burst-absorption question, not a capacity one) |

## 8. Additional code-level observations recorded for later tasks (not hypotheses upgrades)

1. **Reader-loop task churn:** two task creations + one cancellation per received
   message (~800 task objects/s at 400 msg/s) on the event loop — a measurable-cost
   candidate for I1's instrumentation-overhead work and I10's candidate designs.
2. **Consumer exception swallowing:** `_consume()`'s bare `except Exception: pass`
   means a systematically-failing handler class (e.g. a normalizer regression) is
   silently absorbed at full message rate. Informativeness gap; candidate for a bounded
   counter in I1.
3. **`_record_trade_perf` only times stage-4-passing trade messages;** ticker/
   lifecycle/fill/position handler costs are entirely unmeasured (H2 needs them).
4. **Dedup ring at cap:** 250 k IDs ≈ a few hours of current exchange-wide flow; any
   future re-presentation path (REST reconciliation included) must account for ring
   eviction age, which is now observable via `/api/health/pipeline`'s `dedup_ids_held`.
5. **Existing prior art for I4:** `services/diagnostics/diagnostics.py`'s coverage
   check already pages exchange-wide REST `get_trades` on demand — I4 should extend it with
   trade_id reconciliation rather than duplicate the fetch scaffolding.
6. **One event loop, three tenants:** the FastAPI request handlers, the 6-s trading tick,
   and both WS reader/consumer pairs all share one asyncio loop (plus one default
   `to_thread` pool). §6 observed the API unresponsive for >10 s and ticks up to 5.9 s in
   the same window as consumer stalls. Any synchronous or thread-pool-saturating work in
   the tick (SQLite flushes, `to_thread` calls) is therefore a candidate cause of consumer
   starvation that I2's stage timings and I1's queue-age series must be able to attribute
   — a hypothesis to test, not a finding.

## 9. What I0 did *not* do

No queue, worker, limiter, subscription, or batching values were changed. No
instrumentation was added. No hypothesis was upgraded to confirmed. The 10-minute
baseline is observational only.
