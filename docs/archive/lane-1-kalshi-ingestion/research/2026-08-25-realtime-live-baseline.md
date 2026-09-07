# Realtime Kalshi Data-Plane — Live Baseline (I7)

**Task:** I7 of `docs/archive/lane-1-kalshi-ingestion/plans/2026-08-25-realtime-data-plane-investigation.md`.
**Date:** 2026-08-25 (US evening session; MLB/tennis/crypto series active).
**Mode:** paper, real trading disabled, no configuration or code changed during the window.
**Companion:** `docs/archive/lane-1-kalshi-ingestion/research/2026-08-25-realtime-replay-baseline.md` (I6) is
the synthetic twin of this window; `docs/archive/lane-1-kalshi-ingestion/research/2026-08-25-realtime-data-plane-baseline.md`
(I0) holds the topology and the hypothesis table this document updates.

## Method

One read-only sampler, hands-off for the whole window (every `.py` write triggers a
`uvicorn --reload` that zeroes the lifetime counters — the I2 window lost four segments to
that; this one was run with no edits at all):

- every 15 s: `GET /api/health/pipeline` (`ingest.queue_health` — I1; `whale_pipeline`
  — I2; `rest_latency` — I5; provider stats; last tick duration; fault summary) and
  `GET /api/state` (`tick_phase_timings`, `trade_stream_perf`, per-tick rate-limit hits);
- every 5 min: `GET /api/diagnostics/trade-capture?minutes=2&lag_sec=60&max_pages=30`
  (I4) — a bounded REST-vs-WS reconciliation of the two minutes ending 60 s earlier.

Lifetime counters are differenced across the window (split at any restart); window
figures are read as sampled. Raw samples stay in the session scratchpad; this document is
the bounded summary. Correlation is reported as correlation.

## Window

63.5 minutes (3,810 s), 148 usable samples, two monotone segments. The segment break was
**not** a Kalshi event: `ddev logs` shows `WatchFiles detected changes in
'.claude/worktrees/aqc-investigation/…'` — a concurrent investigation created a git
worktree inside this checkout, and `uvicorn --reload` (which watches the whole checkout;
`.git/info/exclude` hides the directory from git but not from the reloader) restarted the
app twice while it was busy, discarding the WebSocket backlog. See "Guard" below.

## Results

### WebSocket ingest (I1 metrics)

| Metric | Value |
|---|---|
| received (all channel types) | 523,143 — 138 msg/s (trade 492,852; ticker 27,184; lifecycle 2,880) |
| **dropped at the app queue** | **55,926 = 10.7%**, in 49 of 148 samples, up to 5,542 per 15 s |
| queue depth | p50 **17,441** · p95 19,928 · max 19,999 (capacity 20,000) |
| oldest message age | p50 **119 s** · p95 189 s · max 209 s |
| queue wait (window avg) | p50 118 s · p95 192 s; 118 of 148 windows had their p95 in the `gt_10s` bucket |
| Kalshi error 25 | 0 |
| reconnects | **7** (3 connections in total) |
| handler time by class (window-avg p50 / max) | trade 5.25 ms / 13.6 s · lifecycle 22.9 ms / 17.8 s · ticker 25.2 ms / 0.24 s |
| consumer exceptions, malformed frames | 0, 0 |

The queue was effectively **full for most of the hour**: depth climbed steadily from
6,421 at t+0 to 19,904 at t+438 s, when drops began, and stayed within a few hundred of
capacity except for two resets. Both resets were reconnects, not recoveries — the depth
went to 0 with `dropped_window = 0` because `run()` discards the old queue with the
cancelled consumer (the loss mechanism I6 modelled; ~19,000 queued messages each time).

### Reconnect causes (I1 `last_disconnect.reason`)

- t+2151 s: `TimeoutError: timed out during opening handshake`, six attempts before a
  connection was re-established — during the same interval the tick took **111.5 s**
  (`market_fetch` 55 s, `resolve_and_record` 51.5 s), immediately after the worktree I/O
  storm above. Correlated, not attributed.
- t+3358 s: `ConnectionClosedError: sent 1011 (internal error) keepalive ping timeout;
  no close frame received` — the `websockets` library gave up after its 20 s
  `ping_timeout`, i.e. either upstream stopped answering pings or this process could not
  service the pong for 20 s. Either way the outcome is the same: a fresh queue, the
  backlog gone.

### Whale pipeline (I2 metrics)

| Stage | n | avg | max |
|---|---|---|---|
| capture / config | 373,115 | 0.07 / 0.07 ms | 38 / 18 ms |
| provider (resolve 0.25 + thread_wait 0.11 + sync 2.06 + re-entry) | 373,113 | **7.07 ms** | **13.6 s** |
| handler_total | 373,115 | 7.27 ms | 13.6 s |
| signals (n=329 emitted) | 329 | 66.8 ms | 136 ms |
| **receive → handler end** | 373,115 | **92.2 s** | **423 s** |
| **receive → decision** (n=329) | 329 | **94.1 s** | **212 s** |

Counters: trades 373,113 · below_threshold 99,963 · offlist_skipped 272,374 · candidates
772 (0.21%) · offlist_candidates 545 · resolve_calls 545 (0 failures) · unresolved_market
4 · rejection_writes **100,410 (27% of trades — one SQLite write pair per sub-threshold
print on a watched market, ~26/s)** · signals_emitted 329. Of the 541 receive→decision
samples with a bucket, **424 (78%) exceeded 10 s**.

The provider's per-window maximum was 3.5–4.6 s in *every* sample of the first hour and
11.4–13.6 s in most samples after the restart, while `resolve` and `sync` maxima stayed
in the tens-to-hundreds of milliseconds — the stalls live in the loop re-entry remainder,
as in I2. The tick phases sampled alongside show `capture_flush_and_titles` at 0.65–1.6 s
on essentially every tick (a synchronous flush into a 16.9 M-row `raw_trades` table),
`resolve_and_record` at 1–5 s and `market_fetch` at 1–6 s on many ticks; ticks were
p50 2.36 s, p95 7.4 s, max 111.5 s. Every one of those seconds is time the shared event
loop is not running the consumer.

### REST (I5 metrics)

| class | calls (share) | 429 | errors | limiter wait avg / max (ms) | network avg / max (ms) | total avg / max (ms) |
|---|---|---|---|---|---|---|
| background_live_status | 2,239 (35.4%) | 1 | **332** | 1,117 / 25,531 | 1,217 / 37,519 | 2,335 / 40,501 |
| background_catalog | 1,534 (24.2%) | 12 | 0 | 532 / 17,166 | 1,489 / 25,887 | 2,045 / 26,756 |
| critical_position | 991 (15.7%) | 0 | 0 | 420 / 12,813 | 1,453 / 26,840 | 1,875 / 27,946 |
| background_resolution | 827 (13.1%) | 3 | 0 | 88 / 16,743 | 472 / 25,900 | 565 / 27,935 |
| critical_whale | 545 (8.6%) | 0 | 0 | 117 / 3,781 | 47 / 3,696 | 164 / 3,882 |
| interactive (this sampler's reconciliations) | 132 (2.1%) | 0 | 0 | 31 / 1,101 | 180 / 6,732 | 212 / 6,732 |
| other | 64 (1.0%) | 0 | 0 | 81 / 1,089 | 227 / 3,934 | 308 / 3,934 |

1.67 calls/s overall; read-bucket waiter high-water **34**; 16 upstream 429s. Unlike the
quiet I5 window, upstream time was also large here (network averages of 1.2–1.5 s and
maxima of 26–37 s for the background and position classes) — but the **whale enrichment
class stayed fast on the network (47 ms avg)** and was slowed only by the local limiter
(117 ms avg, 3.8 s max). 15% of `background_live_status` calls failed again (332/2,239).

### REST-vs-WS capture (I4 reconciliations, 2-minute windows, lag 60 s)

| t+ | REST | WS-seen | completeness | whale-sized captured | queue age at run | backlog > lag |
|---|---|---|---|---|---|---|
| 400 s | 15,152 | 6,325 | 0.42 | 9 / 32 | 129 s | yes |
| 800 s | 12,808 | 0 | **0.00** | **0 / 85** | 206 s (queue full, drops) | yes |
| 1,199 s | 16,463 | 16,500 | **1.00** | 30 / 30 | 4 s | no |
| 1,600 s | 24,365 | 22,915 | 0.94 | 35 / 41 | 67 s | yes |
| 2,355 s | 22,087 | 15,171 | 0.69 | 26 / 34 | 99 s | yes |
| 2,856 s | 17,514 | 3,168 | 0.18 | 3 / 32 | 156 s | yes |
| 3,340 s | 19,353 | 2,960 | 0.15 | 3 / 27 | 157 s | yes |
| 3,820 s | 14,861 | 2,498 | 0.17 | 1 / 28 | 157 s | yes |

Totals: 69,500 of 142,603 REST trades found in the seen-record (0.49); whale-sized
**107 / 309 (0.35)**. Read these as a lower bound with one caveat the window itself
exposed: with the queue 100–200 s deep and a 60 s lag, prints that arrived inside the
window were often *still queued* (not yet evaluated) rather than lost, so "missing" mixes
backlog with loss. The reconciliation tool now flags this (`backlog_exceeds_lag`,
added in this task with a test); the one unambiguous run (queue 4 s deep) reconciled at
1.00, and the run at t+800 s (queue full, drops in progress) at 0.00 — **the loss is
real, and it is concentrated exactly where the queue is saturated or was just dumped**.

## Correlation (not causation) summary

1. Drops begin only after the queue has been full for a while (t+438 s), and every
   drop window sits at depth ≥ 19,200. Sustained arrival (~129 trades/s + 7 ticker/s)
   exceeded *effective* service — nominal handler cost (5.3 ms → ~190 msg/s) was not the
   binding constraint; the ~4 s loop stalls in every sample were.
2. Those stalls co-occur with tick phases of the same magnitude on the same samples
   (`capture_flush_and_titles` every tick, `resolve_and_record` and `market_fetch` on
   busy ticks) and never with `resolve`/`sync` maxima.
3. The two reconnects each erased ~19 k queued messages and were followed by the only
   moments of low latency in the hour — the system's recovery mechanism was loss.
4. Whale-sized capture tracked queue state: 30/30 at 4 s depth, 0/85 at a full queue.

## Hypothesis table update

- **H1 confirmed.** Sustained arrival exceeded effective single-consumer service for
  most of the hour: depth p50 17,441 of 20,000, 10.7% dropped, oldest age p50 119 s.
- **H2 confirmed in effect.** With one FIFO, every class waited the same ~2 minutes:
  lifecycle events (settlements) and open-position ticker updates were processed 100–200 s
  stale alongside trades; the harness's critical-first topology shows the same workload
  would not do that — a comparison for I10, not a selection.
- **H5 quantified.** Capture loss under saturation is severe (whale-sized 35% overall,
  0% during a drop/dump episode) and invisible to every existing metric except I4's
  reconciliation and I1's drop/reconnect counters.
- **H10 confirmed.** The 20,000 slot queue delayed the first drop by ~7 minutes and then
  made every whale decision ~2 minutes late; a larger queue would only lengthen both.
- H3, H4, H6 stand as recorded in I2, I3, I5. H7 (background starving critical REST)
  remains partially informed: here critical_whale's limiter wait was small in absolute
  terms while the background classes carried both the volume and the errors.

## Guard (investigation-to-guard rule)

- Permanent runtime diagnostics already own everything measured here (I1/I2/I4/I5).
- New in this task: the reconciliation tool's `backlog_exceeds_lag` caveat (CI-tested).
- Recommended environment change, **not applied here** because it needs a `ddev restart`
  that would disrupt the live app and concurrent work: exclude in-repo worktrees from the
  reloader (`.claude/worktrees/` is git-excluded via `.git/info/exclude` but still watched
  by `uvicorn --reload`). Verified against the installed uvicorn's
  `supervisors.watchfilesreload.FileFilter`: an `--reload-exclude` value that is an
  *existing directory* at startup becomes an `exclude_dirs` entry checked recursively via
  `path.parents`; a value that does not exist is treated as a glob and would not match
  nested files. So the change is `--reload-exclude .claude/worktrees` in
  `.ddev/docker-compose.fastapi.yaml`'s uvicorn command plus a guarantee the directory
  exists at container start (a `mkdir -p` in the command or the post-start hook). Until
  then, any worktree created inside the checkout restarts the app and invalidates a live
  capture.
