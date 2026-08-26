# Realtime Kalshi Data-Plane — Root-Cause Report (I13)

**Status:** final for this investigation (tasks I0–I12, branch
`chore/realtime-dp-investigation`). Every number below is from a committed research
document or harness run on this branch; nothing is quoted from historical code comments.
**Companion:** `docs/superpowers/specs/2026-08-25-realtime-data-plane-remediation-design.md`
(the selected architecture) and the per-task research documents named in each section.

## 1. Summary

The "Kalshi latency" symptom is four separate failures with two shared roots.

| Failure (as reported) | What it actually is | Root |
|---|---|---|
| Whale signals arrive too late to be useful | The one WebSocket consumer sits behind a 20,000-message queue that is full for most of a busy hour (oldest message p50 119 s); whale receive→decision averaged **94 s** | R1: the shared event loop is stalled by the trading tick's synchronous SQLite work; R2: every print pays the full per-message path before the cheap rejection |
| Missed whale prints / trades | **10.7%** of a busy hour's messages dropped at the app queue, and each of 7 reconnects discarded ~19,000 queued messages; whale-sized capture **107/309 (35%)** across the hour, **0/85** during a drop episode | R1 + R2, plus the reconnect path that throws the queue away |
| Delayed account / watchlist information | Fill, position, lifecycle and ticker messages share the same FIFO and wait the same ~2 minutes; REST position fetches wait p95 1.1 s behind the tick's own catalog batch (4.9 s in a catalog storm) | R3: no class isolation on either plane; the tick spawns background REST before its critical gather |
| REST latency spikes / 429 pressure | Caller-experienced latency is mostly the **local** 8-token limiter (I5: 533 of 590 ms for unattributed callers), not Kalshi; upstream 429s were 16 in an hour; 23% of all REST calls are a wasted immediate re-read on `settled` | R4: demand shape (bursty, duplicated, unproductive) against a local bucket sized at 40% of the verified account budget |

Two genuine correctness defects were proven along the way, independent of load: a
transient enrichment failure permanently loses a whale candidate (H4), and nothing
downstream is idempotent on `trade_id` — any retry or reconciliation path would create
duplicate positions (I12 R8).

## 2. Confirmed causal chains

### C1 — Event-loop stalls, not handler cost, exhaust WebSocket capacity (H1, H10)

Evidence chain (deterministic reproduction → correlated telemetry → source-code proof):

1. **Source:** one asyncio loop hosts the FastAPI handlers, the 6-s trading tick, and
   both WS reader/consumer pairs (I0 §8.6). The tick's `capture_flush_and_titles`
   (synchronous `executemany` into the 16.9 M-row `raw_trades` table, 0.65–1.6 s on
   essentially every tick), `resolve_and_record` (1–5 s) and `series_stats` N+1
   (`main.py:736`) run on that loop; `_connect()` costs ≈12.6 ms per call.
2. **Telemetry (I2, I7):** the provider stage's per-window maximum was 3.5–4.6 s in
   *every* sample of the busy hour and 11–13.6 s after the restart, while its `resolve`
   and `sync` sub-stages stayed in the tens-to-hundreds of milliseconds — the time is in
   the loop re-entry remainder, on the same samples as the tick phases of the same size.
   Nominal handler cost (trade p50 5.25 ms → ~190 msg/s) exceeded the 129 trades/s
   arrival; capacity was lost to the stalls.
3. **Reproduction (I6/I10/I12):** the `busy_hour` replay (129 trades/s + 7 ticker/s +
   0.75 lifecycle/s at measured costs, a 4.5 s loop stall every 10 s, one discarding
   reconnect) reproduces the hour: today's topology strands 68/179 candidates at the
   horizon, candidate p95 109 s. Removing only the loop stalls (`loop_hygiene`) drops it to
   p95 194 ms with zero drops — and every candidate topology is bounded by the stalls
   alike (I10 §4.3): **the stall is the floor for any in-process design**.

Mechanism: with `websockets`' own receive buffer (`max_queue=1024`) full and the app
queue full, the client stops reading TCP; Kalshi sees a slow consumer — that, not
subscription scope, is the error-25 origin (I9 §2), though error 25 itself was never
observed (0 in the hour).

### C2 — Every print pays the full path before the 0.2% that matter are found (H3)

373,113 prints in the busy hour → 772 candidates (0.21%) → 329 signals. Each print
crossed a thread hop (`asyncio.to_thread`) into `_process_trades_sync`, a config
fingerprint, provider setup, and — for the 27% on watched markets — one SQLite write pair
per sub-threshold print (100,410 rejection writes, ~26/s). The pure contract-count gate
that decides candidacy costs microseconds; the harness's reader-side prefilter removes
99.7% of the consumer's work (`prefiltered` 76,852 of 81,804 in the busy-hour replay) and,
with hygiene, gives candidate p95 **20 ms** on one loop (I12 §3.3). Falsified variant: a
prefilter whose rejection row is still written inline (12.6 ms per print) saturates the
reader (968 s of work in a 600 s run) — the gate is only a design *with* batched writes.

### C3 — One FIFO on each plane lets ordinary traffic delay critical traffic (H2, H7)

- **WS:** fill/position/lifecycle/ticker messages waited the same ~2 minutes as trades
  (I7). In the honest single-loop model the same workload with class isolation serves
  the critical classes at p95 39–48 ms (I12 §3.3).
- **REST:** with the tick's real launch order modelled (catalog batch spawned before the
  critical gather, `main.py:346` vs `:351`; one `asyncio.gather` of `get_markets` per batch,
  `catalog_scan.py:307`), the position fetch waits p95 **1.13 s** quiet / **4.9 s** under
  a 40-call catalog storm behind today's FIFO bucket, whale enrichment 1.2 s / 4.9 s
  (I11 corrected in I12 §3.3). Live: background classes held 72.7% of calls and nearly
  all limiter time and errors (I7/I8), whale enrichment was fast upstream (47 ms avg) and
  slowed only locally (117 ms avg, 3.8 s max).

### C4 — Reconnects recover latency by losing data

`run()` cancels the consumer and creates a fresh queue on every reconnect; the two
busy-hour reconnects each erased ~19,000 queued messages and were followed by the only
low-latency minutes of the hour (I7). The handshake-timeout reconnect (six attempts) sat
inside a 111.5 s tick; the keepalive-1011 reconnect is the client's own 20 s ping timeout
expiring — either upstream stopped answering or this process could not service the pong.
Keeping the queue helps only once capacity exists (I10 §4.4) and only with a guard against
stale control frames (I12 W8).

### C5 — A transient enrichment failure is a permanent loss (H4, proven)

`_mark_seen(trade_id)` runs before the market lookup; a whale-sized off-list print whose
first `get_markets_by_tickers` raises is recorded as `market_unresolved` and every later
presentation of that `trade_id` is short-circuited by the 250k dedupe ring
(`tests/test_whale_candidate_lifecycle.py`, strict xfail pinning the desired behaviour).
Two siblings: the REST-path `_MAX_ONDEMAND_MARKET_FETCH = 100` cap marks over-cap trades
seen, and a successful-but-incomplete batch negative-caches the ticker for 300 s.

### C6 — Nothing downstream is idempotent on `trade_id` (I12 R8)

`WhaleSignal.id` (= `trade_id`) is never read; `signal_log.signals` has no trade-id
column; the decision bridge logs the signal *before* `strategy.evaluate`; the strategy
guards per ticker only. Today this is latent (the ring is the only dedupe, ~32 min at
129 trades/s, empty after every `--reload`); it becomes a live defect the moment any
retry, reconciliation or restart path re-presents a trade.

## 3. Falsified or bounded hypotheses

| Hypothesis | Outcome |
|---|---|
| Handler cost is the capacity limit (H1 as originally framed) | Falsified: nominal cost supports ~190 msg/s against 129/s; the loop stalls are the limit (C1). |
| A larger queue would help (H10) | Falsified: the 20,000-slot queue delayed the first drop by ~7 min and then held every decision ~2 min late. |
| Kalshi error 25 / subscription scope is the loss mechanism | Not observed (0 in the hour); local drops and reconnect discards are. |
| Upstream latency dominates REST (H6 inverse) | Falsified for 4 of 6 classes: local limiter wait dominates; only `critical_position` is upstream-dominated. |
| Batching at 50 tickers is a rate-cost necessity (H8) | Falsified: flat 10-token cost per request for every app endpoint; a 200-ticker request is billed like a 50-ticker one and completes un-throttled (I8). |
| Milestone polling is the dominant REST waste (H9) | Bounded: duplicate factor 1.53 — real but moderate; the `settled` re-read (23% of calls) is the larger single waste. |
| The "375 ms position wait behind live-status" (I11 §4.2) | A harness tie-break artifact; the code-faithful finding is worse and has a different cause (C3). |
| Per-class queues give ~0 ms candidate latency (I10 headline) | A parallel-server artifact; honest single-loop figure is ~20 ms p95 (I12 §3.3). |
| The `settled` REST re-read can be removed | Falsified from the mirror: `settled` carries no `result`, REST ends at `finalized`, and grading on `finalized` is a deliberate 2026-08-23 decision — batch and defer, never remove. |

## 4. Capture completeness

I4's REST-vs-WS reconciliation (`GET /api/diagnostics/trade-capture`): with an idle queue
the WS feed is complete (1.00, 12,737/12,737; whale-sized 29/29; 30/30 at 4 s depth). Under
saturation it is not: whale-sized 107/309 over the busy hour, 0/85 while the queue was full
and dropping, 3/32 after the second reconnect. Loss is concentrated exactly where the queue
is saturated or was just discarded; there is no steady leak. The measurement carries the
`backlog_exceeds_lag` caveat (still-queued ≠ lost), which the tool now reports.

## 5. WebSocket capacity limits (measured and modelled)

| Quantity | Value | Source |
|---|---|---|
| Sustained arrival, busy hour | 138 msg/s all classes (129 trades/s) | I7 |
| Burst arrival (1-s windows) | p95 322, max 383 msg/s | I0 |
| Trade handler cost | p50 5.25 ms, window max 13.6 s (stall-inflated) | I2/I7 |
| Ticker / lifecycle handler cost | 25.2 ms / 22.9 ms (each a fresh `_connect()` chain) | I7, I12 W4 |
| Consumer utilisation at p95 handler cost | 0.98–1.00 (capacity 99 msg/s < p50 arrival) | I0 |
| Sustainable trade rate, today's topology, no stalls | see `2026-08-25-realtime-replay-baseline.md` (binary search) | I6 |
| Honest single-loop candidate p95 with prefilter + hygiene | ~20 ms (max 117–172 ms) | I12 §3.3 |

## 6. REST contention breakdown (busy hour, 6,332 calls, 1.67/s = 8% of the verified budget)

| Class | Share | Limiter wait avg / max | Network avg / max | Errors / 429 |
|---|---|---|---|---|
| background_live_status | 35.4% | 1,117 ms / 25.5 s | 1,217 ms / 37.5 s | 332 / 1 |
| background_catalog | 24.2% | 532 / 17.2 s | 1,489 / 25.9 s | 0 / 12 |
| critical_position | 15.7% | 420 / 12.8 s | 1,453 / 26.8 s | 0 / 0 |
| background_resolution (incl. the `settled` re-read, 23% of calls in I8's window) | 13.1% | 88 / 16.7 s | 472 / 25.9 s | 0 / 3 |
| critical_whale | 8.6% | 117 / 3.8 s | 47 / 3.7 s | 0 / 0 |

Account budget (verified, `GET /account/limits`): 200 tokens/s, 600 capacity, flat cost 10
→ 20 req/s sustained, 60 burst; local limiter 8/s, 8 burst. The docs' "two seconds of
budget" (400) disagrees with the live 600 and say nothing about the app's *unauthenticated*
market-data traffic — the anonymous ceiling is unmeasured and is a prerequisite for any
budget change (`docs/kalshi/CHEATSHEET.md`).

## 7. Solution comparison (matrices in I10/I11, corrected in I12)

Families explored per bottleneck (I9), prototyped in the two deterministic harnesses
(`tools/realtime_pipeline_replay.py`, `tools/rest_scheduler_replay.py`), fault-injected
(bursts, loop and consumer stalls, enrichment stalls, reconnect, 429 storms, timeouts,
transient failures, restart):

| Bottleneck | Selected | Rejected (reason) |
|---|---|---|
| C1 stalls | Move the tick's synchronous SQLite phases off the loop (executor with persistent per-thread connections), then per-store batched single-writer for the capture stores; stall watchdog metric | Process isolation for the consumer (I9 §3: pickling every message costs more than the work; loses `check_exits`/broker atomicity); uvloop (does nothing for synchronous stalls) |
| C2 per-print cost | Reader-side pure count gate with an explicit capture contract (append-only reader buffers, writer-thread flush, aggregated rejections) | Prefilter with inline rejection writes (saturates the reader, I12 §1); more consumer workers (the work is per-print, not parallelisable past the stalls; breaks the single-in-flight dedupe invariant) |
| C3 WS isolation | Two consumers on one loop: `critical` (fill, position, lifecycle, control — never shed) and `market` (prefiltered trades FIFO, then coalesced tickers); explicit priority | Four physical groups (I12 §3.3: within 1–3 ms of two once prefiltered — more consumers to supervise for no measured gain); a second WS connection per class (adds reconnect surfaces; the index split already exists and is enough); priority queue in one consumer (`critical_first`: cand p95 241 ms — ordering alone does not create capacity) |
| C3 REST isolation | Critical-first waiter queues inside the limiter with background aging and a global 429 brake; budget unchanged until the anonymous ceiling is probed; tick spawns background tasks after its critical gather | Carved-out reserve (halves the shared rate, unbounded borrowing starves background for 200 s, more 429s under a low ceiling — I12 R2/R3); budget-sized bucket now (unmeasured ceiling; I5 saw upstream 429s at ≤8/s); deficit round-robin (no better than priority at these shapes, more state); concurrency caps (bounds amplitude, never orders) |
| C4 reconnect loss | Keep queue and consumers across reconnects with a connection-generation stamp on queued items | Discard (today); journal-to-disk on reconnect (the loss is queue discard, not process death) |
| C5/C6 loss & duplicates | Durable `trade_id` ledger gating `evaluate`; single-owner retry state machine (classified, budgeted, abandonment counted); reconciliation on reconnect/error-25 only, own caller class, bounded pages | Mark-seen-first (today: loses every transient failure, I11 §3); periodic sampled audit only (measures loss, recovers nothing); persistent candidate journal (duplicates are already covered by the ledger; restart loss is measured by the new metrics and revisited only if material) |
| REST demand | Batched + deferred `settled` resolver (`GET /markets?tickers=`), shared milestone/live-data cache, classified tape poll | Removing the `settled` read (falsified, §3); larger `get_markets` chunks as a first move (safe per I8, but a demand cut — not a fix for contention) |

## 8. Expected targets after remediation (busy-hour conditions, paper mode)

| Metric | Today (I7) | Target | Basis |
|---|---|---|---|
| App-queue drops | 10.7% | 0 | replay: 0 for every hygiene variant |
| Oldest queued message age | p50 119 s | p95 < 1 s | replay `oldest` 0.5 s |
| Whale receive→decision | avg 94 s, 78% > 10 s | p95 < 250 ms (queueing ~20 ms + off-list enrichment ~50–160 ms) | I12 §3.3 + I7 whale network 47 ms avg |
| Critical class (fill/position/lifecycle) latency | ~119 s (shared queue) | p95 < 100 ms | replay crit p95 39–48 ms |
| Loop stall per observability window | 4–13.6 s | max < 250 ms | new watchdog metric (falsifiable, not assumed) |
| Whale-sized capture completeness (no reconnect in window) | 0.35 | ≥ 0.99 | I4 at idle queue: 1.00 |
| Candidates lost to transient enrichment failure | all of them | 0 lost; abandoned counted separately | state machine |
| Duplicate decisions per `trade_id` | unmeasurable | 0 (ledger `INSERT OR IGNORE` conflicts counted) | ledger |
| Critical REST limiter wait | position p95 ~1.1 s (model), whale 117 ms avg (live) | p95 < 50 ms | priority model: whale p95 45 ms |
| `settled` re-read demand | 0.55/s (23% of calls) | ≤ 0.1/s (batched, deferred) | one call per N settlements |
| Upstream 429s | 16/h | < 1/h at unchanged budget | brake |

These are acceptance gates for the remediation plan's paper-mode soak, not predictions to
be trusted without the soak.
