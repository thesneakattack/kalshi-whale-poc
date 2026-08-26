# Realtime Data-Plane — WebSocket/Data-Plane Candidate Comparison (I10)

**Task:** I10 of `docs/superpowers/plans/2026-08-25-realtime-data-plane-investigation.md`.
**Tool:** `python -m tools.realtime_pipeline_replay --compare --seed 1 --presets … --variants …`
(deterministic; every candidate sees literally the same message stream per preset — the
per-preset workload fingerprint is printed with the matrix).
**Status:** comparative evidence. No selection is made here; I12 attacks the leading
design(s), I13 selects.

## 1. Correctness invariants (defined before benchmarking, checked on every run)

| Invariant | How the harness checks it | Result across all 336 runs |
|---|---|---|
| No duplicate whale decisions | every processed message's `seq` is unique (`duplicate_processed`) | 0 |
| Per-ticker ordering for trades | a trade served before an earlier-arrived trade on the same key counts (`ordering_violations`) | 0 (single-consumer-per-group designs preserve it structurally) |
| Fills / positions / lifecycle never shed by policy | class-group designs route them to a queue that trades cannot fill; drops are reported by kind | 0 critical drops in every run |
| Bounded memory | every queue capped at 20,000 (the production bound); `queue_high_water` reported | respected; the busy hour hits the cap on the FIFO designs |
| Coalescing touches only state-like messages | `received == processed + remaining` holds for trade/fill/lifecycle in coalescing runs | holds |

## 2. Candidates (from the I9 families; none pre-selected)

| Candidate | What it models | I9 family |
|---|---|---|
| `single_queue` | production: one bounded FIFO, one consumer | baseline |
| `critical_first` | two FIFOs under one cap, one consumer, critical kinds served first | B3 (priority within one consumer) |
| `class_groups` | per-class bounded queues (`trade` / `ticker` / `critical`), one consumer each — what separate consumers on one loop (or separate connections) give | B3 (isolation) |
| `class_groups_coalesce` | as above, `ticker` queue keeps only the latest per market | B3 + coalescing |
| `prefilter` | production FIFO, but non-candidate trades are rejected at the reader for ~2 µs each (the contract-count gate before the thread hop) | B2 |
| `staged` | prefilter + class groups + coalesced tickers | B2 + B3 combined |

Workload variants applied to *every* candidate: `as_measured` (stalls and reconnect
behaviour as observed), `loop_hygiene` (loop-scope stalls removed — i.e. the tick's
synchronous SQLite taken off the loop; B1 fixed by hygiene), `keep_queue` (a reconnect
keeps the queued backlog instead of discarding it; B4), and both together.

## 3. Workloads

Measured presets from I0–I7 (see the I6 note for sources): `measured_p95` (322 trades/s),
`measured_burst` (148/s with a 20 s ×2.6 burst), `mixed_trade_ticker` (148/s + 50 tickers/s),
`mixed_critical` (322/s + fills/positions/lifecycle), `loop_stall` (4 s and 8 s loop stalls),
`reconnect` (3 s consumer stall then a reconnect with a 1 s gap), and `busy_hour` — I7's
regime compressed to 10 minutes: 129 trades/s, 7 tickers/s, 0.75 lifecycle/s with the
per-class handler costs measured in that window (5.25 / 25 / 23 ms), a 4.5 s loop stall
every 10 s, one backlog-discarding reconnect. All presets carry the measured candidate mix
(0.25% of trades on a ~30 ms decision path).

## 4. Results (seed 1; candidate = whale-decision latency arrival→service start; crit = fill/position/lifecycle/ticker latency)

### 4.1 `busy_hour` — the regime that produced I7's 10.7% drops and 94 s decisions

| candidate | variant | drops | queue high-water | oldest (s) | cand p50 / p95 / p99 (ms) | crit p95 (ms) | sustained |
|---|---|---|---|---|---|---|---|
| single_queue | as_measured | 0 | 15,460 | 112.7 | 52,623 / 108,594 / 110,310 | 106,300 | no |
| single_queue | keep_queue | **10,097** | 20,000 | 219.4 | 121,049 / 203,420 / 211,800 | 207,781 | no |
| single_queue | **loop_hygiene** | 0 | 87 | 0.71 | 24.5 / **194** / 316 | 226 | **yes** |
| critical_first | as_measured | 0 | 18,549 | 143.9 | 53,982 / 136,378 / 143,875 | 4,128 | no |
| critical_first | loop_hygiene | 0 | 108 | 0.88 | 27.7 / 242 / 439 | 47.8 | yes |
| class_groups | as_measured | 0 | 8,044 | 62.0 | 31,506 / 58,783 / 60,671 | 4,104 | no |
| class_groups | loop_hygiene | 0 | 82 | 0.71 | 3.4 / 42.1 / 69.1 | 32.0 | yes |
| prefilter | as_measured | 0 | 55 | 4.5 | 512 / 4,171 / 4,441 | 4,117 | **yes** |
| prefilter | loop_hygiene | 0 | 4 | 0.49 | 0.0 / 20.1 / 66.8 | 40.3 | yes |
| staged | as_measured | 0 | 54 | 4.5 | 0.0 / 4,016 / 4,434 | 4,095 | **yes** |
| staged | **hygiene_keep_queue** | 0 | 4 | 0.30 | **0.0 / 0.0 / 0.0** | 32.0 | **yes** |

(`class_groups_coalesce` equals `class_groups` here to within a few messages — only 7
tickers/s in this preset; 77 coalesced.)

### 4.2 `measured_p95` (322 trades/s, no stalls) — the sustained-arrival question (H1)

| candidate | drops | queue high-water | oldest (s) | cand p50 / p95 / p99 (ms) | crit p95 (ms) | sustained |
|---|---|---|---|---|---|---|
| single_queue | 0 | 3,623 | 11.2 | 6,033 / 10,317 / 11,158 | 10,496 | no |
| critical_first | 0 | 3,726 | 11.9 | 6,017 / 9,787 / 11,656 | 18.2 | no |
| class_groups | 0 | 2,619 | 8.4 | 4,742 / 7,963 / 8,196 | 0.0 | no |
| prefilter | 0 | 2 | 0.25 | 0.0 / 0.0 / 30.3 | 0.0 | **yes** |
| staged | 0 | 2 | 0.25 | 0.0 / 0.0 / 29.8 | 0.0 | **yes** |

Variants change nothing here (no stalls, no reconnect): this preset isolates pure
service capacity.

### 4.3 `loop_stall` — stalls hit every in-process design alike

| candidate | as_measured cand p95 / crit p95 (ms) | loop_hygiene cand p95 / crit p95 (ms) |
|---|---|---|
| single_queue | 4,321 / 4,884 | 35.3 / 28.4 |
| critical_first | 4,403 / 2,863 | 35.3 / 11.3 |
| class_groups | 3,976 / 2,858 | 23.6 / 0.0 |
| prefilter | 2,477 / 2,863 | 0.0 / 0.0 |
| staged | 2,445 / 2,640 | 0.0 / 0.0 |

### 4.4 `mixed_critical`, `measured_burst`, `mixed_trade_ticker`, `reconnect`

- `mixed_critical` (322/s + account/lifecycle traffic): only `prefilter` (cand p95 1.5 ms,
  crit p95 5.2 ms) and `staged` (0.0 / 0.0) are sustained; `class_groups` halves the
  backlog (2,449 vs 5,234) and zeroes critical latency but its trade consumer still runs at
  a deficit (cand p95 7.5 s).
- `measured_burst`: every FIFO design absorbs the burst without drops but pays 5–6 s of
  candidate p95 during it; `critical_first`/`class_groups` protect critical latency
  (11.8 / 0.0 ms); `prefilter`/`staged` never notice it (queue high-water 3).
- `mixed_trade_ticker` (50 tickers/s): `class_groups` cand p95 7.2 ms vs 105 ms for the FIFO
  — a ticker-heavy watchlist (150 markets) is where per-class consumers earn their keep even
  without a prefilter; coalescing barely triggers at this rate (2 messages).
- `reconnect`: `keep_queue` on the single FIFO raises critical p95 from 26 to 100 ms (the
  kept backlog is served first) while removing the 227-message loss; on `staged` the
  backlog is tiny, so keeping it costs nothing and loses nothing.

## 5. What the comparison establishes (and what it does not)

1. **Loop stalls dominate everything.** With the measured 4.5 s stall every 10 s, no
   topology keeps whale decisions under 4 s — the stall itself is the latency floor —
   and the current FIFO reproduces I7 (oldest 113 s, decisions ~1 min). Removing the loop
   stalls (`loop_hygiene`) makes even the **current** topology sustain the busy hour with
   candidate p95 194 ms. B1 is not a topology problem; it is the tick's synchronous work
   on the loop, and it must be fixed regardless of which topology is chosen.
2. **Rejecting non-candidates before the thread hop is the only in-process change that
   makes the observed p95 arrival rate (322/s) sustainable** on one consumer: `prefilter`
   turns 3.6k of backlog and 10 s of candidate p95 into 2 queued messages and 30 ms p99,
   at ~2 µs of reader time per print. It also makes the busy hour sustainable *even with
   the stalls present* (4.2 s p95 — the stalls, not the queue). B2's measured cost
   (100% hops for 0.25% candidates) was the capacity problem.
3. **Per-class consumers buy critical-event latency, not capacity.** `critical_first`
   and `class_groups` cut critical p95 from seconds to ≤ 50 ms in every preset, but leave
   the trade backlog (and candidate latency) essentially where the FIFO had it. They are
   the B3 answer; they are not a B1/B2 answer. Coalescing tickers matters only at ticker
   rates far above these presets (a 150-market watchlist is the case to re-check).
4. **Keeping the queue across reconnects is only safe once capacity exists.** On the
   overloaded FIFO it converts silent reconnect loss into 10,097 counted drops and a
   219 s backlog — more honest, not better. On `staged` it costs nothing. B4's fix is
   contingent on B1/B2.
5. **Candidates that merely move the backlog** — `critical_first` and `class_groups` under
   `as_measured` — are visible as such: same queue high-water order of magnitude, same
   candidate p95, different critical p95. The matrix rejects them as *complete* answers
   while keeping them as components.
6. **The combination that meets every requirement in every preset is `staged` +
   `loop_hygiene` (+ `keep_queue`)**: zero drops, candidate p50/p95/p99 of 0.0/0.0/0.0 ms in
   the busy hour, critical p95 ≤ 32 ms, no ordering violations, queue high-water 4. That
   is a *finding*, not a selection: it rests on two assumptions I12 must attack — that the
   reader-side gate really is a ~µs pure function of the print (it is today:
   `_prescan_count` needs no DB or market data — but the watched-market rejection *write*
   would have to be batched off the hot path), and that the loop stalls can in fact be
   removed (the correlation in I7 has not been reproduced causally).

## 6. Resource cost and recovery

- CPU: `busy_fraction` on the trade consumer falls from ~0.68–1.0 (FIFO designs) to
  ~0.02 under `prefilter`/`staged` (only candidates and other classes are served); the
  reader's added work is `prefiltered × 2 µs` ≈ 0.15 s over the 10-minute busy hour.
- Memory: bounded by the 20,000-slot caps; `staged` never exceeds a few dozen queued
  messages in any preset.
- Recovery after stalls (`stall_recovery_sec`): FIFO designs need seconds to drain a
  4 s stall's backlog (~600 messages at ~200/s); `prefilter`/`staged` recover within the
  stall's own duration because the post-stall dump is mostly prefiltered.

## 7. Fault injection covered

Bursts (`measured_burst`, ×2.6 for 20 s; the burst tests in `tests/` at ×8–×12), slow
handlers (consumer stalls of 3–4.9 s), loop stalls (4–8 s, and 4.5 s every 10 s), inline
enrichment stalls (0.25% × 1.1 s), reconnects with and without backlog discard, and
sustained overload (`measured_p95`, `busy_hour`). Malformed messages are outside the
queue model (the gateway counts them; I1).

## 8. Open items carried to I11/I12

- The prefilter's *writes*: today a sub-threshold print on a watched market triggers a
  SQLite rejection write (27% of trades in I7). A reader-side gate must not perform that
  write; the design must batch or drop it (I9 track B, family B/C) — cost to be shown.
- Whether the loop stalls are removable: I12 must reproduce "tick SQLite on the loop ⇒
  stall" and the remediation must carry a stall watchdog that shows them gone.
- `seq` on trade envelopes (I9 open item 1) for gap accounting.
- The REST side (critical enrichment behind background bursts, candidate loss on
  transient failure) is I11's matrix.
