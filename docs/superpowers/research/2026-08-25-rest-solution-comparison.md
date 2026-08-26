# Realtime Data-Plane — REST Scheduling and Recovery Candidate Comparison (I11)

**Task:** I11 of `docs/superpowers/plans/2026-08-25-realtime-data-plane-investigation.md`.
**Tool:** `python -m tools.rest_scheduler_replay --compare` / `--recovery` (deterministic;
tests in `tests/test_rest_scheduler_replay.py`).
**Status:** comparative evidence. No selection; I12 attacks the leaders, I13 selects.

## 1. What is simulated

**Demand** is shaped like the measured call sites, not drawn from an average: the tick's
`asyncio.gather` fires 3 `critical_position` calls *at the same instant* as 6–10
`background_live_status` calls every 6 s; catalog batches of 10–40 every 15 s; resolution
batches every 30 s; Poisson `critical_whale` enrichment at 0.15–0.2/s; interactive at
0.02/s. Per-class network latencies are the I5/I7 measurements (whale 47 ms; the busy
preset uses the I7 upstream averages of 0.5–1.5 s with 5 s p95 tails).

**Upstream** is Kalshi's bucket as documented and probed (I8): continuous refill, 10 tokens
per request, 200 tokens/s with 600 capacity (20 req/s, 60-request burst), 429 when the
bucket cannot cover a request; the `429_storm` preset shrinks it to 5 req/s / 10-burst to
model an anonymous ceiling far below the account's. 429s are retried through the same
scheduler with the production backoff (0.5/1/2/4 s, four retries). Timeouts are errors, as
in `call_with_backoff`.

**Local policies** (all in front of the same upstream): the production shared FIFO bucket
at 8/s with an 8-token burst (`fifo_8`); the same bucket sized to the verified budget
(`fifo_20_60`, I9 family C2); strict priority by caller class with 5 s aging
(`priority_aging`, C3); deficit round robin with critical quanta of 4 (`drr`, C4); a
critical reserve of 3/s with a 10-token burst that may borrow from the shared bucket
(`reserved`, C5); per-class in-flight caps on the FIFO (`concurrency_caps`, C6); and the
priority/reserve policies on the budget-sized bucket.

Invariants checked on every run: every call completes or is counted as an error; upstream
token usage per second is reported so no policy can hide a budget breach; Jain's fairness
index across background classes and each class's worst wait are reported.

## 2. Scheduling results (seed 1, 600 s per preset)

Columns: whale = `critical_whale` limiter-wait p95 / caller-experienced total p95; pos =
`critical_position` limiter-wait p95; bg = worst background limiter-wait p95 / max;
429 = upstream rejections; qhw = local queue high-water; tok/s = peak upstream tokens
used in any second (budget 200 sustained, 600 burst).

### 2.1 `measured_quiet` (I5 shapes)

| policy | whale p95 (ms) | whale total p95 | pos p95 | bg p95 / max | 429 | qhw | Jain | tok/s |
|---|---|---|---|---|---|---|---|---|
| fifo_8 (today) | **1,180** | 1,227 | 1,875 | 1,500 / 1,612 | 0 | 16 | 0.43 | 150 |
| fifo_20_60 | 0 | 47 | 0 | 0 / 0 | 0 | 1 | 1.00 | 250 |
| priority_aging | 45 | 92 | 375 | 2,000 / 2,125 | 0 | 16 | 0.35 | 150 |
| drr | 241 | 288 | 1,375 | 1,875 / 2,125 | 0 | 16 | 0.48 | 150 |
| reserved | 0 | 47 | 0 | 2,400 / 2,400 | 0 | 13 | 0.43 | 180 |
| concurrency_caps | 663 | 710 | 375 | 1,875 / 2,125 | 0 | 11 | 0.72 | 150 |
| priority_aging_20_60 / reserved_20_60 | 0 | 47 | 0 | 0 / 0 | 0 | 1 | 1.00 | 250 |

The model reproduces I5: with the measured shapes and the 8-token bucket, whale enrichment
waits ~1.2 s at p95 (live: 0.4–1.1 s averages, 1.8–3.8 s maxima) while upstream sees
150 tokens/s at most — the wait is entirely local.

### 2.2 `background_storm` (40-call catalog batches — I5's waiter high-water of 34)

| policy | whale p95 (ms) | pos p95 | bg max | 429 | qhw | tok/s |
|---|---|---|---|---|---|---|
| fifo_8 | **4,930** | 5,625 | 5,362 | 0 | 46 | 150 |
| fifo_20_60 | 0 | 0 | 0 | 0 | 1 | **550** |
| priority_aging | 118 | 375 | 6,000 | 0 | 46 | 150 |
| reserved | 0 | 0 | 8,400 | 0 | 43 | 180 |
| concurrency_caps | 761 | 875 | 6,000 | 0 | 11 | 150 |

A 40-call storm costs critical calls ~5 s on today's bucket; the verified budget absorbs
the whole storm in one second (550 of the 600-token capacity) with no 429.

### 2.3 `429_storm` (if the anonymous ceiling were 5 req/s / 10-burst)

| policy | whale p95 (ms) | whale total p95 | 429 |
|---|---|---|---|
| fifo_8 | 1,180 | 1,464 | 122 |
| **fifo_20_60** | 0 | 47 | **621** |
| priority_aging | 61 | 98 | 124 |
| drr | 262 | **1,954** | 123 |
| reserved | 0 | 47 | 85 |

Sizing the local limiter above the *real* ceiling converts local waits into upstream 429
storms (621 rejections) that the backoff then spreads over every class; the reserve policy
draws the fewest 429s because critical calls stop competing with the storm.

### 2.4 `measured_busy` and `timeouts`

`measured_busy` (I7 upstream latencies) repeats the quiet ordering with smaller local waits
(the slow network spaces the bursts out) — whale p95 424 ms on `fifo_8`, 0 on the reserve
or the budget-sized bucket. `timeouts` (5% of calls time out at the 10 s
`request_timeout_sec`) shows the one cost no policy touches: a critical call that times out
costs 10 s under every policy; the timeout is itself a critical-path latency knob.

## 3. Recovery results (400 candidates)

Scenario `transient_and_wire_loss`: enrichment fails during three windows (10 s, 3 s, 60 s);
wire copies are lost or delayed 90 s during two saturation windows. `restart_mid_retry`: a
15 s outage with a process restart in the middle.

| policy | evaluated once | lost (transient / wire / restart) | abandoned | duplicates | extra REST | sweep REST | decision latency max |
|---|---|---|---|---|---|---|---|
| mark_seen_first (today) | 378 | **17 / 5 / 0** | 0 | 0 | 0 | 0 | 90 s (delayed wire copies) |
| retry_state_machine | 384 | 0 / 5 / 0 | 11 (60 s outage > 12 s retry budget) | 0 | 69 | 0 | 90 s |
| retry_journaled | 384 | 0 / 5 / 0 | 11 | 0 | 69 | 0 | 90 s |
| retry + reconciliation (idempotent) | **389** | **0 / 0 / 0** | 11 | 0 | 80 | **100** (89 sweeps + 11 recovered) | 82 s |
| retry + reconciliation, no idempotency | 389 | 0 / 0 / 0 | 11 | **6** | 80 | 100 | 82 s |

| `restart_mid_retry` | evaluated once | lost restart | abandoned | extra REST | sweep REST |
|---|---|---|---|---|---|
| mark_seen_first | 398 | 0 (2 lost transient) | 0 | 0 | 0 |
| retry_state_machine | 399 | **1** | 0 | 3 | 0 |
| retry_journaled | 399 | 0 | 1 | 8 | 0 |
| retry + reconciliation | 399 | 0 | 1 | 8 | 89 (for nothing — no wire loss) |

## 4. What the comparison establishes

1. **The local burst budget is the cause of critical-call latency, not Kalshi.** Every
   policy that stops critical calls from queueing behind the tick's own gather removes the
   wait at identical upstream usage; the model reproduces the live I5/I7 numbers from the
   measured demand shapes alone. H7 moves from "consistent" to "reproduced causally in the
   model"; the live confirmation is the post-fix I5 metrics.
2. **Two families remove critical wait entirely: a critical reserve (C5) and a bucket
   sized to the verified budget (C2).** Strict priority (C3) cuts it 10–40× but cannot beat
   one refill interval when a burst has already been dispatched — the tick's own 3 position
   calls still wait 375 ms behind the 10 live-status coroutines that were launched a few
   microseconds earlier. DRR is the wrong tool for critical latency (240–370 ms, with
   1.9 s totals under 429s). Concurrency caps bound amplitude only.
3. **C2 is only safe if the ceiling it targets is real.** At the verified budget it absorbs
   even a 40-call storm with zero 429s; at an unverified anonymous ceiling of 5/s it
   generates 621 rejections. The anonymous-vs-authenticated probe (I9 open item 2) is
   therefore a *prerequisite* for C2, not a nicety; C5 needs no such evidence and draws the
   fewest 429s under a storm.
4. **Demand reduction (C1) is the multiplier for every policy**: the storm preset shows
   what the catalog batch alone costs; the H9 duplicate factor of 1.53 and the lifecycle
   `settled` re-read at 23% of all calls (I8 follow-up) are the largest removable demands,
   and neither needs a scheduler.
5. **Recovery: the retry state machine fixes H4 outright** (17 transient losses → 0) at
   ~0.2 extra REST calls per candidate; its only residue is *abandonment* under outages
   longer than the retry budget — counted, not silent. **Journaling matters only when a
   restart lands inside an outage** (1 candidate here) — real under `uvicorn --reload`, rare
   in production. **Reconciliation is the only family that recovers wire loss**, but a
   periodic sweep costs ~9 REST calls per recovered candidate at this loss rate and 89
   calls for nothing when there is no loss — it should be *triggered by the I1 drop and
   reconnect counters*, not scheduled. **Idempotency on `trade_id` is mandatory** the moment
   any second evaluation path exists (6 duplicates from delayed wire copies without it).
6. **Timeouts are a policy-independent cost**: the 10 s request timeout is the largest
   single latency a critical enrichment can suffer; a shorter timeout for the critical
   class with a retry is a candidate the scheduler families do not cover.

## 5. Open items carried to I12/I13

- Verify the anonymous market-data ceiling (or sign the reads) before any C2 sizing.
- The reserve's size: 3/s with a 10-token burst was enough for every measured shape;
  I12 should try to starve it (many simultaneous whale candidates during a catalog storm).
- The retry state machine's interaction with the 250k dedupe ring and with a reconnect
  that discards the queue (I10 B4): pending candidates must not be lost with the queue.
- Whether the tick's gather should be de-burst in code (C1) regardless of policy: the
  375 ms position wait under strict priority is caused by coroutine launch order, which
  no scheduler sees.
