# Realtime Data-Plane — Deterministic Replay Baseline (I6)

**Task:** I6 of `docs/superpowers/plans/2026-08-25-realtime-data-plane-investigation.md`.
**Tool:** `tools/realtime_pipeline_replay.py` (`python -m tools.realtime_pipeline_replay --all`),
tests in `tests/test_realtime_pipeline_replay.py`.
**Status:** baseline of the *current* topology under measured workloads. No candidate
design is evaluated here; `CriticalFirstTopology` exists in the tool only to prove the
harness can compare topologies (task I10's job).

## What the harness is

A seeded discrete-event simulation of the production ingest path — reader → bounded
application queue (20,000) → one serial consumer — with a virtual clock, so a run is
deterministic, socket-free, database-free, and fast (all nine presets in ~1.2 s).

Arrival and service distributions come from the measurements recorded so far:

| Input | Value | Source |
|---|---|---|
| trade arrival p50 / p95 / max | 148 / 322 / 383 msg/s | I0 baseline §6 |
| trade handler service | log-normal, mean 3.3 ms, p95 10 ms | I0 §6, I2 window |
| ticker / lifecycle handler | log-normal 5 ms (p95 30 ms) / 2 ms (p95 6 ms) | I1 live handler-by-class |
| consumer stalls | 4.0 s and 4.9 s | I2 provider maxima |
| loop stalls | 4.0 s and 8.0 s | I2 tick series (4.0 s, 7.99 s) |
| inline enrichment | 0.25% of trades, +1.1 s | I2 counters (198 / 77,807), resolve max 1.1–4.3 s |

Two stall scopes are modelled because they leave different fingerprints in the I1/I2
metrics: a **consumer** stall (inline REST await, worker result path) lets the reader keep
filling the app queue, so queue wait sees it; a **loop** stall (synchronous work in the
tick) stops the reader too, so arrivals park in the `websockets`/TCP side and land in the
app queue in one dump when the loop frees — and the enqueue-timestamp-based queue wait
barely notices. The harness therefore reports both `wait` (enqueue → service, what I1's
metric measures in production) and `latency` (arrival → service, the truth).

A reconnect models what `run()` actually does: the old consumer task is cancelled and a
fresh queue created, so the queued backlog is lost, and nothing is received during the
backoff gap.

## Baseline results — current topology, seed 1, 120 s per preset

| preset | received | dropped | queue high-water | oldest age (s) | trade wait p95 (ms) | trade latency p95 (ms) | critical wait p95 (ms) | sustained |
|---|---|---|---|---|---|---|---|---|
| measured_normal (148/s) | 18,711 | 0 | 60 | 0.34 | 23 | 23 | 24 | yes |
| measured_p95 (322/s) | 39,450 | 0 | 3,220 | 10.1 | **9,180** | 9,180 | **9,043** | **no** (3,214 still queued at the horizon) |
| measured_burst (148/s, ×2.6 for 20 s) | 23,378 | 0 | 1,827 | 6.2 | 5,362 | 5,362 | 4,797 | yes (drains after the burst) |
| mixed_trade_ticker (+50 ticker/s) | 24,039 | 0 | 113 | 0.51 | 153 | 153 | 153 | yes |
| mixed_critical (322/s + fills/positions/lifecycle) | 40,874 | 0 | 4,782 | 14.2 | 13,406 | 13,406 | **13,465** | **no** (4,779 still queued) |
| slow_handler (two consumer stalls) | 18,711 | 0 | 742 | 4.9 | 2,890 | 2,890 | 2,765 | yes |
| enrichment_stall (0.25% × 1.1 s) | 18,582 | 0 | 894 | 5.8 | 4,643 | 4,643 | 4,750 | yes (670 queued at horizon) |
| reconnect (3 s stall, reconnect, 1 s gap) | 18,555 | 0 | 474 | 3.0 | 23 | 23 | 23 | yes — but 227 lost on reconnect + 220 missed in the gap |
| loop_stall (4 s and 8 s) | 18,711 | 0 | 1,230 | 4.4 | 2,793 | **5,238** | 2,655 | yes |

## What the baseline establishes

1. **Sustainable rate of the current topology** with the measured trade handler
   distribution is bounded by `1000 / mean service` ≈ 300 msg/s: the binary search on a
   fixed 4 ms handler converges at 258 msg/s (theory 250, plus queueing slack), and the
   measured-p95 preset at 322 msg/s is not sustained — the queue grows ~27 msg/s with no
   drops for the whole 120 s. This is H10's arithmetic reproduced: at that rate the 20,000
   slot queue buys ~12 minutes before drops, and every second of it is added latency.
2. **The p95 arrival rate alone produces multi-second whale latency** (trade wait p95
   9.2 s) without any stall — matching the live receive→decision maxima of 5–9 s seen in
   I2 during a quieter period that also had stalls.
3. **Critical messages inherit the trade backlog in full** under the single FIFO
   (critical p95 = trade p95 in every preset); a critical-first topology on the identical
   burst workload brings critical p95 from ~7.1 s to ~2.8 ms in the pluggability test —
   proof the harness discriminates designs, not a selection.
4. **Queue wait under-reports loop stalls by roughly the stall length** (2.8 s vs 5.2 s
   p95 in `loop_stall`). The I1 metric is exact for consumer stalls and blind to loop
   stalls; the exchange-timestamp-based receive→decision figure (I2) is the one that
   sees both. The live "unattributed remainder" stalls in I2 are consistent with the
   loop scope.
5. **A reconnect is a loss event, not just a latency event**: whatever is queued when the
   connection drops is discarded with the cancelled consumer, and nothing arrives during
   the backoff gap. Neither loss is counted by `dropped_messages` today — only I1's
   `reconnects` counter and I4's reconciliation can reveal it.

None of this selects a remedy. It gives I7 (live baseline) a synthetic twin to compare
against, and I10/I11 a fixed workload set to benchmark candidates on.
