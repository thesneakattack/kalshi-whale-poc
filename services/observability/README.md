# Observability module — reference

Owns: `observability.py` (bounded, low-frequency persistence of runtime
metrics the app already computes in memory, into `data/observability.db`)
+ `routes.py` (`/api/observability/current|history|summary`, the
informativeness half). New 2026-08-24, Quality Control Plane Task 9
(`docs/superpowers/plans/2026-08-24-quality-control-plane.md`) — the first
of several QCP services turning this project's habit of ad hoc live
investigation (tick-phase timing, trade-stream perf counters, ...) into a
queryable history instead of only "whatever's in `state` right now."

## Why this doesn't import `services.app_state`

Every other periodic-background-task module (`backup.py`, `alerting.py`,
`event_schedule.py`) imports `state` directly from `services.app_state`.
`observability.py` deliberately does not — `capture_from_runtime(cfg,
state, trade_stream, index_stream)` and `maybe_capture(...)` take those as
plain arguments instead. Two reasons:

1. `services.app_state` constructs `PaperBroker`/`RiskManager`/
   `ShadowTrader` and loads `series_cache`/`event_schedule`/`title_cache`
   eagerly at import time (see `tests/support/runtime_isolation.py`'s
   `_EAGER_SINGLETON_MODULES` comment for the exact list and the
   2026-08-23 test-contamination incident that made that eagerness a
   documented hazard). A module that only needs to *read* three or four
   already-computed numbers has no reason to pull in that whole
   construction graph.
2. `capture_from_runtime` becomes trivially unit-testable with a synthetic
   `state` dict and a `types.SimpleNamespace` stand-in for `trade_stream`/
   `index_stream` — no monkeypatching of `services.app_state` singletons
   needed, unlike `tests/test_backup.py`'s `pb_module.DB_PATH =` dance.

`routes.py` still imports `state`/`trade_stream`/`index_stream` from
`services.app_state` directly, same as every other route module — the
decoupling is specific to `observability.py` itself, not the package as a
whole.

## What actually gets captured (Task 9 scope only)

`capture_from_runtime` is a **pure mapping**, no I/O — reused as-is by both
`maybe_capture` (the periodic path) and `GET /api/observability/current`
(an on-demand live read with no DB round-trip):

| Metric name | Source | Real today? |
|---|---|---|
| `tick.duration_sec` | `state["last_tick_duration_sec"]` | yes |
| `tick.rate_limit_hits` | `state["last_tick_rate_limit_hits"]` | yes |
| `tick.phase.<name>_sec` | `state["tick_phase_timings"]`, one per key | yes |
| `trade_stream.messages_per_sec` / `avg_handler_ms` | `state["trade_stream_perf"]` | yes, but `None` (and so omitted) until `whale_stream_handlers._record_trade_perf`'s first 1s window rolls over after the exchange-wide trade stream starts flowing — see `services/whale_stream/whale_stream_handlers.py` |
| `trade_stream.dropped_messages` / `messages_received` | `trade_stream.dropped_messages` / `.messages_received` (the live `KalshiTradeWebSocketClient` instance) | yes, cumulative counters, real even at 0 when the stream is disabled |
| `index_stream.dropped_messages` / `messages_received` | same, for `index_stream` | yes |
| `kalshi_rest.<endpoint>.calls` / `.errors` / `.rate_limited` / `.avg_latency_ms` | `state["last_tick_http_metrics"]`, one group per endpoint-family key | yes (QCP Task 15, 2026-08-24) — see below |

### `kalshi_rest.*` — per-endpoint Kalshi REST telemetry (Task 15)

`services/http_client.py`'s `call_with_backoff` is the one real chokepoint
every Kalshi REST call in this app passes through (`services/kalshi_client.py`
and `services/kalshi_account_client.py` both route every request through it),
so it's instrumented directly rather than wrapped from outside — each retry
attempt inside its own loop is counted as one real HTTP attempt, not
collapsed into one "call" per logical operation (would undercount real
request volume the same way this module's own rate-limit-hits counter
already warns against).

`endpoint` (the per-metric key, e.g. `get_markets`, `create_order_v2`)
defaults to `coro_func.__name__` — free and correct for the ~20 call sites
that pass a bound SDK method directly. The 4 calls routed through
`kalshi_client.py`'s `_get_json` (which all share one local `do_get` closure
name) pass an explicit `endpoint=` override instead — see that method's own
call sites (`get_series_list`, `get_event_live_data`,
`get_tags_for_series_categories`, `get_filters_for_sports`).

`avg_latency_ms` is averaged only over *successful* attempts — a 429 or a
hard error still took real wall time, but folding those in would conflate
"how long does a real round trip take" with "how long did we wait to get
rate-limited." Omitted (not `0`/`null`) per-endpoint when there were zero
successes that tick, same "unknown over fabricated" rule as everything else
here — see `tests/test_http_client.py`'s per-endpoint telemetry tests for
the exact chosen semantics (documented in the test names themselves, per
that task's own instruction).

`http_client.http_metrics_snapshot(reset=True)` is called exactly once per
tick, in `main.py`'s `trading_loop`, immediately next to the existing
`get_and_reset_rate_limit_hits()` call — same reset-and-stash pattern,
stored as `state["last_tick_http_metrics"]` before `capture_from_runtime`
ever runs. `capture_from_runtime` itself only ever reads that already-
computed value (`reset=False` semantics, i.e. no reset at all) — it must
never call `http_metrics_snapshot(reset=True)` directly, since that would
let an incidental `GET /api/observability/current` request silently zero
out the counters the periodic 60s sampler was about to read.

Live-verified 2026-08-24 (not just unit-tested): `GET
/api/observability/current` showed real `kalshi_rest.get_markets`/
`get_events`/`get_exchange_status`/`get_milestones` entries with nonzero
`calls` and real `avg_latency_ms` values within seconds of restart, and
`GET /api/observability/history?metric=kalshi_rest.get_markets.calls`
showed 5 consecutive persisted samples (10, 10, 10, 11, 15) tracking real,
growing per-tick call volume.

A source that's `None`/missing is **omitted from the returned dict, never
recorded as a fabricated 0** — CLAUDE.md's / the design spec's "unknown is
better than fabricated" rule applies here too. An empty-dict capture (e.g.
very first tick, before any phase has run) is a legitimate, harmless
no-op — `record_samples_bulk` short-circuits on an empty dict.

**Deliberately not yet captured**, per the design spec's own task split —
these are Task 10/11 territory, not this module:
- anomaly findings (tick duration vs. poll interval, stale stores, backup
  age, disconnected streams) — `GET /api/observability/summary` here is a
  plain per-metric count/min/max/avg over a window, not a verdict.
- background task running/restart/fault state, pipeline last-write ages,
  DB file sizes, backup age — these belong to `services/storage_health/`
  and the unified `/api/quality/summary` (Tasks 10–11), which are expected
  to read *through* this module's `history()`/`summary()` rather than
  duplicate its persistence.

### `<stream>.ingest.*` — WebSocket queue health (realtime data-plane I1, 2026-08-25)

Source: `services/kalshi/websocket.py`'s `KalshiStreamGateway.ingest_metrics()`
(pure read), flattened by `_flatten_ingest_metrics` for both `trade_stream`
and `index_stream`. Exists because the I0 baseline
(`docs/superpowers/research/2026-08-25-realtime-data-plane-baseline.md` §4)
found the four failure points the investigation must tell apart — Kalshi
server-side subscription overflow (error 25), the `websockets` receive
buffer, the app queue overflowing (`QueueFull`), and downstream backlog —
collapsed into one lifetime `dropped_messages` int plus an ephemeral status
string.

Names (all `float`; zero-count classes are omitted, never fabricated):

- `…ingest.received.<class>` / `…ingest.processed.<class>` /
  `…ingest.dropped.<class>` — lifetime counters by bounded message class
  (`trade`, `ticker`, `fill`, `position`, `lifecycle`, `index`, `control`,
  `other` — `_CLASS_BY_MESSAGE_TYPE` in websocket.py; unknown `type`s land
  in `other` so the label set cannot grow with vendor changes).
- `…ingest.dropped_window`, `…ingest.malformed_messages`,
  `…ingest.handler_exceptions` — the last is the count of handler
  exceptions the consumer used to swallow with a bare `except: pass`; each
  class is also fault-logged (`kalshi_websocket` / `handle_message:<class>`)
  once per window, never once per message.
- `…ingest.handler_timeouts` (issue #145/#150, 2026-08-28) — count of
  `_process_item`'s `asyncio.wait_for(..., timeout=_HANDLER_TIMEOUT_SEC)`
  actually timing out. Deliberately separate from `handler_exceptions`
  (per-class breakdown, `handler_timeouts_by_class`, is live-only via
  `ingest_metrics()`/`/api/health/pipeline`, same as
  `handler_exceptions_by_class` — only the aggregate is persisted here).
  This is the signal to watch for issue #150's known, accepted
  thread-pool-leak tradeoff (cancelling a timed-out
  `asyncio.to_thread(...)` call doesn't stop the underlying OS thread) —
  see `services/kalshi/CHEATSHEET.md`'s "Consumer-stall bound + liveness
  backstop" entry for the full incident and design.
- `…ingest.queue_depth`, `…ingest.queue_high_water`,
  `…ingest.oldest_message_age_sec` — the head-of-queue age is the direct
  "received promptly but processed stale" measurement.
- `…ingest.queue_wait.window_count|window_max_sec|window_avg_sec|window_p95_upper_bound_sec`
  and `…ingest.queue_wait.bucket.<le_1ms|le_10ms|le_100ms|le_1s|le_10s|gt_10s>`
  — queue wait = monotonic dequeue time − monotonic enqueue time, per
  message. The p95 figure is the smallest finite bucket bound at/above the
  p95 rank (`services/latency_agg.py`), `None`/omitted when the p95 sits in
  `gt_10s` — the overflow count is persisted so that case is visible.
- `…ingest.handler.<class>.window_count|window_avg_ms|window_max_ms` —
  handler time by class. Complements (does not replace) the
  application-level `trade_stream.avg_handler_ms`, which times only the
  trade callback.
- `…ingest.server_errors`, `…ingest.error_25_total`, `…ingest.error_25_window`,
  `…ingest.reconnects`.

**`gate_would_reject` / `gate_exceptions` (realtime data-plane remediation
P0 Task 3, 2026-08-26).** Two lifetime counters on `ingest_metrics()`
itself (`services/whale_gate.py`'s reader-side whale-size gate, run in
`_ingest_raw` for every `trade`-class message). Shadow mode today: nothing
is dropped, `gate_would_reject` counts what a live gate WOULD have
rejected and `gate_exceptions` counts the gate's own failures (fall-open -
never silently hides a whale). **Not yet flattened into `capture_from_runtime`
or persisted to `data/observability.db`** - deliberately out of this
task's scope; read them live via `ingest_metrics()` (e.g. through
whatever route already surfaces it, such as `/api/health/pipeline`) until
Task 17 (which flips the gate from shadow to actually filtering the
market queue) wires them into the persisted history alongside the rest of
`<stream>.ingest.*`.

**Window semantics — who resets what.** Every `window`/`_window` figure
covers exactly one persisted sample's span: `maybe_capture` calls each
gateway's `reset_ingest_window()` immediately *after* `record_samples_bulk`
succeeds. `capture_from_runtime` (shared with the on-demand `/current`
route) never resets anything — the same rule as `kalshi_rest.*`. Lifetime
counters are monotone; difference two persisted samples for a rate.

**Runtime finding.** `observability:ws-server-error-25:<scope>` (warning)
fires when `error_25_window > 0` — Kalshi reported its own outbound buffer
overflowed for this subscription. Deliberately separate from
`ws-dropped-messages`, which is local `QueueFull` only.

**`observability:ws-dropped-messages:<scope>` severity (corrected
2026-08-30, #72).** `error` only when `dropped_window > 0`, i.e. a drop
inside the current sample window; `info` when the window is clean but the
lifetime `dropped_messages` counter is nonzero; absent when both are zero.
Evidence carries both (`dropped_window`, `dropped_messages`) and the summary
labels the lifetime figure as "lifetime, never reset" — which is what it is:
the gateway sets `dropped_messages` once in `__init__` and only increments
it. Until this correction severity keyed off that lifetime counter, so one
drop pinned `GET /api/quality/summary` to `error` for the rest of the
process (observed live 2026-08-24 at 5,985 drops with zero active alerts).
A stream with no `ingest_metrics` has no window evidence and is reported at
`info` with `dropped_window: null`, not judged either way.

**Measured hot-path cost (in-container, 50k synthetic trade messages ×5,
2026-08-25):** old path (parse + dispatch) 5.41 µs/msg → new path (parse +
classify + timestamps + aggregation) 8.04 µs/msg, i.e. **+2.63 µs/msg**
(~0.1% of the 3.2 ms p50 trade handler cost measured in the I0 baseline);
`ingest_metrics()` snapshot 7 µs. JSON parsing moved from the consumer to
the reader (still one parse per message) so a drop can be attributed to a
class and the enqueue timestamp is taken at receive time.

**First live reading after deploy (≈60 s after the `--reload`, cold
caches — not a controlled window):** queue depth 2,841 / high-water 2,888,
oldest message 20.3 s, every wait in the window in `gt_10s`, trade handler
window avg 5.7 ms with a lifetime max of 4,360 ms, ticker handler avg
~30 ms. Recorded here as the instrumentation's acceptance evidence
("received promptly but processed stale" is now a number); the controlled
baseline is task I7's job.

### `whale_pipeline.*` — whale-trade pipeline stage timing (realtime data-plane I2, 2026-08-25)

Source: `services/whale_pipeline_perf.py` (pure module singleton, not
`services.app_state`), recorded by `services/whale_stream/
whale_stream_handlers.py::_process_stream_trade` and
`services/whalewatchers/kalshi_trade_tape.py::fetch_signals`. Exists so the
most expensive stages of the per-message hot path are measured rather than
inferred from code shape (H3 in the known-findings file).

Names (`float`; the whole group is omitted until the pipeline has recorded
anything in this process — poll mode and most tests never do):

- `whale_pipeline.stage.<stage>.window_count|window_avg_ms|window_max_ms`
  for `capture` (trade_tape insert + `series_watcher.record_trade`),
  `config` (`config_store.get()` + `config_performance.fingerprint`),
  `provider` (the whole `fetch_signals` call), `resolve`
  (`_resolve_unknown_markets`, incl. any REST wait), `thread_wait`
  (`asyncio.to_thread` submitted → worker started), `sync`
  (`_process_trades_sync` on the worker, i.e. all SQLite work), `signals`
  (`_handle_signal` + `check_exits`, only when a signal was emitted),
  `handler_total`, `receive_to_handler_end` (gateway enqueue timestamp →
  handler end, every stream trade), `receive_to_decision` (same, only for
  trades that produced a signal).
- `whale_pipeline.counter.<name>` — per-window counts: `trades`,
  `below_threshold`, `offlist_skipped`, `unresolved_market`, `candidates`,
  `offlist_candidates`, `to_thread_entries`, `rejection_writes`,
  `resolve_calls`, `resolve_failures`, `signals_emitted`.
- `whale_pipeline.receive_to_decision.window_p95_upper_bound_sec` and
  `…receive_to_decision.bucket.<le_1ms…gt_10s>` (same fixed buckets as
  `<stream>.ingest.queue_wait`).

**How receive time reaches the handler.** The gateway's `_process_item`
sets `services/kalshi/websocket.py::MESSAGE_ENQUEUED_AT` (a contextvar)
to the message's monotonic enqueue timestamp for the duration of the
callback and resets it after — no private key stamped into the vendor
payload (which would leak into `series_watcher`'s archival `raw_json`).

**Window ownership.** Same rule as `<stream>.ingest.*`: `maybe_capture`
calls `whale_pipeline_perf.perf.reset_window()` only after a sample is
persisted; `capture_from_runtime` never resets. Lifetime aggregates stay
monotone. The worker thread returns its clocks/counts to the event loop
(never records from the thread), so `snapshot()` never races a writer.
`tests/conftest.py` gives every test a fresh singleton — this is
process-global mutable state, the same isolation hazard as the DBs.

**Per-message constant costs measured in-container (2026-08-25):**
`config_store.get()` 9.3 µs/call (an `os.stat` + lock; its own docstring
says "a handful of times per tick, not per message" — the stream handler
calls it twice per trade message), `config_performance.fingerprint(cfg)`
28.9 µs (json.dumps + sha256, once per trade message, needed only if a
signal is emitted), `signal_log.series_of` 0.24 µs,
`series_watcher.watched_series(cfg)` 3.1 µs. Total ≈ 48 µs/msg ≈ 1.5% of
the I0 p50 handler cost: pure waste on the ~99.9% non-whale flow, but not
where the time goes.

**First live window (2026-08-25, 692 s of monotone samples across a 773 s
read-only capture; 77,807 trades ≈ 112 trades/s — a quieter period than
the I0 baseline's 148 msg/s p50, and interrupted by four `--reload`
restarts caused by editing `.py` files during the capture — lesson for I7:
a capture window must be hands-off, since every reload zeroes the lifetime
counters):**

| Stage (per trade, avg) | ms | window-max |
|---|---|---|
| capture | 0.064 | 59 |
| config | 0.072 | 1.1 |
| provider (= resolve + thread_wait + sync + loop re-entry) | **3.25** | **4,926** |
| · resolve | 0.24 | 4,329 |
| · thread_wait | 0.12 | 21 |
| · sync (worker thread, all SQLite) | 1.58 | 277 |
| · unattributed remainder ≈ loop re-entry after the worker finishes | ≈1.3 | — |
| signals (per emitted signal, n=61) | 69.6 | 110 |
| handler_total | 3.44 | 4,926 |
| receive → handler end (every trade) | **649** | **9,624** |
| receive → decision (n=61 candidates with a signal) | **727** | **5,294** |

Counters: `to_thread_entries/trade = 1.000` vs `candidates/trade = 0.0030`
(234 candidates, 198 of them off-watchlist, 198 resolve calls, 0 failures,
19 `unresolved_market`); `offlist_skipped` 85.7% of trades;
`rejection_writes/trade = 0.142` (11,042 SQLite write pairs in 692 s ≈ 16/s
on the worker, one per sub-threshold print on a *watched* market — 12
markets at the time; this scales with watchlist size, not with candidates).

What it says, and what it does not:

- **H3 confirmed.** The per-message floor is ≈3.3 ms of thread hop +
  worker + loop re-entry, paid on 100% of trades, while the cheap
  contract-count rejection that decides 99.7% of them costs microseconds
  and already runs *inside* the hop. `provider` is 94% of handler time.
- **Receive→decision is queue time, not handler time**: 649 ms average
  wait versus 3.4 ms of work, at ~40% nominal utilization. Queue depth was
  usually tiny (p50 1, max 71) with oldest-age spikes to 5 s — so the wait
  is built by *stalls*, not by steady overload.
- **The multi-second stalls sit in the unattributed remainder**: the
  3.8–4.9 s `provider` maxima recur in windows where `resolve`, `thread_wait`
  and `sync` maxima are all small, and the tick series shows 4.0 s and
  7.99 s ticks in the same capture. The remainder is exactly the time a
  finished worker's result waits for the event loop to be free — the
  signature of the loop being blocked by synchronous work elsewhere (the
  trading tick's SQLite phases are the obvious candidate). Correlation
  only; I7 must pin it with tick-phase timestamps aligned to the stall
  windows before it is called a cause.
- `capture` max 59 ms and `sync` max 277 ms show SQLite contention on both
  the loop (series_watcher flush) and the worker.


### `loop_watchdog.*` — event-loop stall detector (realtime data-plane remediation P0 Task 1, 2026-08-26)

Source: `services/loop_watchdog.py`, a standalone periodic-wakeup timer
started via `task_supervisor.supervise` in `main.py`'s `lifespan`, wholly
independent of `whale_pipeline`/`<stream>.ingest`'s own counters. Where
those measure *symptoms* correlated with a stall (queue wait, provider
max), this measures the stall itself: it schedules an `asyncio.sleep`
every `sample_interval_sec` (default 0.1 s) and records how much later than
expected each wakeup actually ran. Anything scheduled on the same loop —
the trading tick's synchronous SQLite phases being the leading suspect per
the whale_pipeline section above — shows up directly as stall time here,
falsifying (or confirming) that correlation without depending on any one
subsystem's own instrumentation.

Names (`float`; omitted entirely until the watchdog has taken at least one
sample in this process — same "no evidence, no rows" contract as
`whale_pipeline.*`):

- `loop_watchdog.samples` — total wakeups observed in the window.
- `loop_watchdog.stall_count` — wakeups that ran more than 50 ms
  (`_STALL_THRESHOLD_SEC`) late.
- `loop_watchdog.stall_max_ms` — the worst lateness observed in the window.

**Window ownership.** Same rule as every other module in this file:
`maybe_capture` calls `loop_watchdog.reset_window()` only after a sample is
durably persisted; `capture_from_runtime`/`snapshot()` never reset.

**Use.** The realtime data-plane remediation plan's P0 gate reads this via
`GET /api/health/pipeline` as the primary evidence that later phases (the
tick executor, the reader gate, the two-consumer split) actually reduce
loop stalls rather than only moving where the same blocking work runs.


### `candidate_retry.*` — H4-recovery retry queue (realtime data-plane remediation P2 Task 12, 2026-08-26)

Source: `services/candidate_retry.py`, the single-owner retry queue a
whale-sized off-watchlist print's `trade_id` enters when its market
lookup fails transiently (`services/whalewatchers/kalshi_trade_tape.py`'s
`_resolve_unknown_markets`, on the `except Exception:` branch that Task
11's H4 fix also stops from marking the trade seen). `run_pending` is not
yet wired into the trading tick (that is Task 13) — until then this
module accumulates only via direct test calls, and every metric below
reads as its default (`pending` 0, everything else 0).

Names (`float`; same "no evidence, no rows" contract as every other
family in this file — omitted entirely when nothing is pending AND
nothing happened this window; `capture_from_runtime`'s own tested
contract is that a fully quiet tick returns `metrics == {}`, not a page
of meaningful-looking zeros):

- `candidate_retry.pending` — a live gauge, `len(candidate_retry._pending)`
  at capture time, not a windowed count.
- `candidate_retry.retried` — retry attempts made in this window (an
  entry whose backoff had elapsed when `run_pending` ran, regardless of
  outcome).
- `candidate_retry.recovered` — of those, how many found a market and
  claimed their `trade_id` in `candidate_ledger` (Task 10's gate) this
  window.
- `candidate_retry.abandoned` — of those, how many exhausted
  `_BACKOFF_SCHEDULE_SEC` (sums to ≈91.5 s, comfortably over I12 R7's
  ≥72 s minimum-survivable-outage finding) and were dropped this window.

**Window ownership.** Same rule as every other module in this file:
`maybe_capture` calls `candidate_retry.reset_window()` only after a
sample is durably persisted; `capture_from_runtime`/`snapshot()` never
reset. `reset_window()` only zeroes the three window counters — `pending`
reads `_pending`'s real current length, never reset (it isn't a window
metric, it's live state).

**Use.** Task 13's own P2 gate criterion — "abandoned > 0 in a window
surfaces as a quality finding" — reads `candidate_retry.abandoned` from
here, once Task 13 wires the finding.


### `writer.*` — capture writer thread depth/flush recency (realtime data-plane remediation P3 Task 14, 2026-08-27)

Source: `services/capture_writer.py`, the daemon thread that batches
capture-store writes off both the asyncio loop and the reader coroutine.
Unwired today — nothing calls `capture_writer.submit()` yet (Task 15
routes `series_watcher.record_trade`'s row through it instead of its own
local buffer); the thread itself is started/stopped and liveness-
supervised from `main.py`'s `lifespan()` regardless, so it runs idle
(empty buffers never open a DB connection — `_flush_store`'s own early
return) until Task 15 wires a real producer.

Names (`float`; gated on `capture_writer.is_alive()`, not on the depth/age
dicts being non-empty — those always return an entry per known store,
populated at module import, so gating on non-emptiness would emit a
misleadingly "live-looking" `0` for a process that never called
`capture_writer.start()` at all):

- `writer.depth.<store>` — `len(capture_writer._buffers[store])` at
  capture time, a live gauge, not a windowed count.
- `writer.last_flush_age_ms.<store>` — milliseconds since that store's
  last flush (successful or not — see `capture_writer.py`'s own
  `_last_flush_at` comment), also live, not windowed.

**Use.** A sustained rise in `writer.depth.<store>` alongside a rising
`writer.last_flush_age_ms.<store>` means the writer thread is falling
behind or stuck (not dead — `writer.alive`-style detection is the
separate `observability:capture-writer-dead:capture_writer` finding
below, not a metric threshold on these two gauges).

**Finding: `capture-writer-alive`.** `_capture_writer_dead_finding()`
fires `critical` when `capture_writer.was_started()` is true but
`capture_writer.is_alive()` is false — the thread was running and died.
Absent (not a finding at all, not a lower severity) when the writer was
never started in this process at all, since that's not an anomaly, just
a process that hasn't reached `capture_writer.start()` yet. `main.py`'s
`_capture_writer_liveness_loop` already restarts a dead thread within
~5s via `capture_writer.ensure_alive()` — this finding makes a dead
stretch visible in `/api/quality/summary` too, not just recoverable,
same "counted, not silent" bar P2's own retry-abandonment finding set.

**Update (2026-08-27) — "Unwired today" above is now stale; a real completeness
gap found while live.** Task 15 has since shipped: `series_watcher.record_trade`'s
row now does route through `capture_writer.submit()` for the `raw_trades` store,
confirmed live (not from source alone) via `GET /api/health/faults` during Phase
P3.5's stress-test session
(`docs/superpowers/research/2026-08-25-realtime-data-plane-known-findings.md`'s
"Phase P3.5 live-scale attempt" entry has the full detail) — `capture_writer`/
`flush`/`OperationalError: database is locked`, 178 occurrences, first_seen
2026-08-27 20:22:15 UTC, still accumulating. Root cause: `capture_writer.py:122`'s
`_flush_store` uses `PRAGMA busy_timeout=50` (50ms) on its `raw_trades` connection
to `data/series_watcher.db`; `series_watcher.py`'s own `book_snapshots` flush
writes to the *same physical file* via an independent connection, and a collision
inside that 50ms window drops the **whole raw_trades batch** (by `_flush_store`'s
own "never raises" design), counted only in `dropped_count()`, not retried and not
currently surfaced as its own `writer.*` metric above (only depth/last-flush-age
are). A real, ongoing raw_trades completeness gap per CLAUDE.md's data-plane HARD
RULE — not fixed here, since the 50ms `busy_timeout` was a deliberate Task 14
tradeoff needing its own dedicated investigation, not a fix folded into a
measurement task.


### `kalshi_rest_class.*` / `kalshi_rest_limiter.*` — REST latency by caller class (realtime data-plane I5, 2026-08-25)

Source: `services/http_client.py`'s `rest_latency_snapshot()` (pure read),
flattened by `_flatten_rest_latency`. Exists because the I0 baseline found
the only REST latency the app recorded (`kalshi_rest.<endpoint>.avg_latency_ms`)
is timed from *after* `limiter.acquire()` returns, success-only — local
limiter wait was not conflated into it, it was **invisible**, so H6/H7
could not be judged at all.

Every `call_with_backoff` call is attributed to one bounded caller class
(`http_client.CALLER_CLASSES`: `critical_whale`, `critical_position`,
`interactive`, `background_discovery`, `background_catalog`,
`background_live_status`, `background_resolution`, `other`), set by the
`caller_class(name)` context manager or the `@classify(name)` decorator on
the calling async function; the contextvar propagates into `gather`/
`create_task` children. Annotated sites: the provider's off-list market
enrichment (`critical_whale`); `_fetch_markets` / `_fetch_account_snapshot`
/ `_fetch_exchange_status` (`critical_position`); `_fetch_live_status` /
`_fetch_event_live_data` (`background_live_status`); `_fetch_event_titles` /
`_fetch_category_metadata` / catalog scan (`background_catalog`); discovery
refresh (`background_discovery`); signal-resolution checker, event-schedule
resolver, lifecycle `settled` re-read (`background_resolution`); the
coverage and trade-capture diagnostics routes (`interactive`). Anything
unannotated is `other` — a non-trivial `other` share is a to-do, not noise.

Names (`float`; the whole group is omitted until something has called
Kalshi in this process; classes never used are omitted):

- `kalshi_rest_class.<class>.calls|attempts|rate_limited|errors` — **window**
  counts (summable across persisted samples); `calls` are logical calls,
  `attempts` include every 429 retry, `errors` are logical calls that ended
  in an exception (non-429 or exhausted retries). Lifetime counts stay in
  the in-memory snapshot (`/api/health/pipeline`'s `rest_latency`) only.
  (Corrected 2026-08-25 by I8: the first I5 cut persisted the *lifetime*
  counters under these names, so summing per-minute samples inflated demand
  ~30x — the probe that consumed them caught it.)
- `kalshi_rest_endpoint.<family>.calls|rate_limited|errors` — exact
  per-window counts by endpoint family (the same labels as
  `kalshi_rest.<family>.*`, which remain per-*tick* snapshots reset by the
  trading loop and therefore sample only ~1 tick in 10 at a 60 s cadence).
  Use these for demand shares and the milestone/live-data duplicate estimate.
- `kalshi_rest_class.<class>.limiter_wait.window_avg_ms|window_max_ms` —
  time inside `limiter.acquire()` per attempt (**local queueing**).
- `…network.window_avg_ms|window_max_ms` — per attempt, including 429/error
  round trips (the per-endpoint number stays success-only).
- `…backoff.window_avg_ms|window_max_ms` — 429 retry sleep summed per
  logical call (only calls that slept are samples).
- `…total.window_avg_ms|window_max_ms` — caller-experienced elapsed per
  logical call = limiter wait + network + backoff over all attempts.
- `kalshi_rest_limiter.read|write.waiters|waiters_high_water` — callers
  currently inside `acquire()` and the most that ever were (lifetime
  high-water; not reset).

**Window ownership.** Same rule as the other I-series groups:
`maybe_capture` calls `http_client.reset_rest_latency_window()` after a
sample is persisted; `capture_from_runtime` never resets. Lifetime
aggregates are monotone. `tests/conftest.py` gives every test a fresh
`_rest_class_stats` dict.

**Reading it.** A slow call is local queueing if `limiter_wait` carries it,
upstream if `network` does, retry sleep if `backoff` does. Contention shows
as `limiter_wait` rising for `critical_*` classes while `background_*`
classes hold most of the `calls` — that is H7's signature; the endpoint
averages alone can never show it.

**First live window (2026-08-25, 796 s hands-off, one monotone segment,
1,393 logical calls ≈ 1.75 calls/s against the 8/s read bucket):**

| class | calls (share) | attempts | 429 | errors | limiter wait avg / max (ms) | network avg / max (ms) | backoff avg (ms) | total avg / max (ms) |
|---|---|---|---|---|---|---|---|---|
| background_catalog | 404 (29.0%) | 404 | 0 | 0 | **224** / 3,699 | 179 / 3,786 | — | 403 / 3,786 |
| other (unannotated) | 285 (20.5%) | 285 | 0 | 2 | **533** / 4,548 | 57 / 3,571 | — | 590 / 4,576 |
| critical_position | 275 (19.7%) | 276 | 1 | 0 | 164 / 3,704 | **643** / 3,780 | 506 | 812 / 3,869 |
| background_live_status | 184 (13.2%) | 184 | 0 | **78** | 352 / 4,022 | 74 / 3,874 | — | 426 / 4,045 |
| critical_whale | 159 (11.4%) | 161 | **2** | 0 | 61 / **1,826** | 46 / 3,601 | 609 | 116 / 3,601 |
| background_resolution | 86 (6.2%) | 87 | 1 | 0 | 88 / 1,898 | 182 / 3,762 | 608 | 280 / 3,762 |

Read-bucket gauges across the 41 samples: `waiters_high_water` 14 → 19;
`tokens` at sample time was ≤ 3 of 8 in 12 of 41 samples and 0.0 once;
`waiters` > 0 in 5 samples. Ticks: p50 1.75 s, max 4.79 s.

What it says:

- **H6 confirmed as a measurement.** Average demand is ~22% of the local
  budget, yet limiter wait is the *majority* of caller-experienced latency
  for four of six classes and reaches 1.8–4.5 s. The budget is depleted by
  *bursts* (a catalog batch's gather, the tick's own gather), not by the
  average rate — exactly the number that was invisible before I5.
- **Whale enrichment is not insulated**: `critical_whale` waited up to
  1.8 s in the local limiter and drew 2 of the window's 4 upstream 429s
  (backoff ~0.6 s each) while background classes held 69% of calls.
  Consistent with H7; causality (critical waits *because of* background
  bursts) is I8/I11's to pin with demand-share timelines and fault
  injection, not this table's.
- **Upstream is the story only for `critical_position`** (network avg
  643 ms, i.e. the account/markets/exchange-status calls themselves are
  slow) — the one class where a "Kalshi is slow" reading would be right.
- **42% of `background_live_status` calls failed** (78 of 184, non-429
  errors) — a real effectiveness finding for I8, previously visible only
  as per-endpoint error counts with no caller attribution.
- **20.5% of calls are unattributed** (`other`): `propagate_milestone_winners`
  in the tick was the missing annotation (added `background_live_status`
  after this window); anything still landing in `other` is a to-do.
- Four upstream 429s in 13 minutes at ≤ 8 calls/s locally: the anonymous
  market-data ceiling is being brushed by bursts even under the
  conservative local cap — a data point for I8's endpoint-cost/limit
  study, not a limiter change.


## Persistence

`data/observability.db`, one `metric_samples` table (`observed_at`,
`metric`, `value`, `labels_json`), indexed on `(metric, observed_at)`.
`record_samples_bulk()` is one connection/commit per capture cycle (a
handful of `INSERT`s), not a connection per metric — this project's
persistence idiom's own "don't connect per row" guidance
(`CLAUDE.md`/`backup.py`'s own comment on the same point).

## Restart-safe cold-start seeding — same fix as `backup.py`, applied up front

`services/backup/backup.py`'s README.md documents a real live bug
(2026-08-23): trusting `state["backup"]["last_started_at"]`'s in-memory
`0.0` default across a process restart meant every `uvicorn --reload` cycle
looked like "never backed up" and fired an unnecessary immediate re-run.
`maybe_capture`'s interval gate is built with that fix already applied,
not rediscovered: on the first check in a process,
`state["observability"]["last_sample_at"] == 0.0` triggers a lookup of the
most recently *persisted* sample (`_latest_sample_time()`) instead of
assuming "overdue" — a restart means "go check what actually happened,"
not "assume the worst and sample immediately." Covered directly by
`tests/test_observability.py`'s `test_maybe_capture_cold_start_*` tests
(recent persisted sample → no refire; old/missing persisted sample →
fires promptly either way).

## Wiring

- **Capture**: one line in `main.py`'s `trading_loop`, right after
  `state["tick_phase_timings"] = phase_timings` is finalized (so a sample
  taken this tick sees this tick's own numbers, not the previous one's) —
  `_maybe_capture_observability(cfg, state, trade_stream, index_stream)`,
  same `_maybe_*`-in-the-tick idiom as `_maybe_run_backup`/
  `_maybe_check_signal_resolutions`. Purely synchronous — a handful of
  `INSERT`s into a small dedicated file is not worth `task_supervisor`
  offload the way `backup.py`'s multi-GB snapshot pass is; revisit only if
  it ever becomes measurable on `tick_phase_timings`.
- **Pruning**: folded into the *existing* hourly sweep in `main.py`'s
  `_maybe_prune_capture_stores` (alongside `series_watcher`/`index_feed`/
  `game_state`) rather than inventing a second pruning schedule —
  `observability.prune(retention_hours=..., now=now)`, reading its own
  `observability.retention_hours` config leaf (default 336h/14 days,
  independent of `series_watcher`'s own 168h default).
- **Router**: `app.include_router(observability_routes.router)` in
  `main.py`, immediately covered by Task 4's router-registration audit
  (`tools/quality_audit/routers.py`).
- **Test isolation**: `services.observability.observability` is registered
  in `tests/support/runtime_isolation.py`'s `PERSISTENCE_MODULE_PATHS` —
  `tests/test_runtime_isolation.py`'s AST cross-check fails the suite if a
  future edit ever lets a `DB_PATH` owner go unregistered again.

## Config

`config/settings.yaml`'s `observability` section: `enabled` (default
`true`), `sample_interval_sec` (default 60), `retention_hours` (default
336 = 14 days) — deliberately the only three knobs (per-metric thresholds
are explicitly out of scope for this task; that's Task 10's anomaly-rule
territory). All three read fresh from `cfg` each tick via
`maybe_capture(cfg, ...)`, same live-reload-with-no-restart convention as
every other config-gated `_maybe_*`.

## Hot-path impact

Small but real, on purpose. `maybe_capture(cfg, state, trade_stream,
index_stream)` runs synchronously inside `main.py`'s `trading_loop`, once
per tick — not offloaded, unlike `backup.py`'s multi-GB snapshot pass —
but it's gated to fire at most once per `sample_interval_sec` (default
60s), and the actual work on a firing tick is a handful of `INSERT`s via
`record_samples_bulk`'s single connection/commit, not a table scan. On a
non-firing tick (the overwhelming majority) it's one dict-default-lookup
and one time comparison. `capture_from_runtime` itself is pure and cheap
regardless of call frequency, which is also why `GET /api/observability/
current` can call it live with no DB round-trip.

## Failure behavior

`record_samples_bulk` propagates a real SQLite error rather than
swallowing it — an observability write failure is not caught anywhere in
`maybe_capture`'s own call chain today, so it would surface the same way
any other uncaught trading_loop tick exception does (`fault_log`, the
"crash" alert category). Never intentionally degrades silently; a missing
metric source is omitted from `capture_from_runtime`'s own returned dict
(see "unknown is better than fabricated" above), which is a different,
deliberate thing from an actual write failure.

## What is deliberately not automated

No automatic remediation - `runtime_findings` only ever reports, never
acts (e.g. it does not restart a disconnected stream or clear rate-limit
pressure itself). No alerting integration of its own; a finding here only
becomes visible via `GET /api/quality/summary`/`/api/observability/*` or
a human/agent explicitly polling those routes - there is no push
notification path from this module.

## Handoff

- **Downstream**: nothing yet — no dashboard panel wired up as of this
  writing (Task 18's "minimal System Health UI" is the natural home).
  `GET /api/observability/current|history|summary` are live and
  independently curl-able in the meantime.
- **Known limitation to revisit, not a bug**: `trade_stream.messages_per_sec`/
  `avg_handler_ms` will be silently absent from every sample until the
  exchange-wide trade stream has been flowing for at least one second in
  this process — expected, not an observability bug; `trade_stream.
  dropped_messages`/`messages_received` (cumulative, always real) are the
  metrics to check first if the stream-perf pair looks perpetually missing.

## Reconnect gap duration + per-position ticker cadence (P8 Task 34, 2026-08-27)

Two new metric families, both added because a benchmark planned for the
realtime data-plane remediation (P8 Task 40, `docs/superpowers/plans/
2026-08-25-realtime-data-plane-remediation.md`) needs *measured* input
distributions rather than assumed ones - and a first research pass had
wrongly claimed reconnect telemetry didn't exist at all (it did:
`<stream>.ingest.reconnects` has been persisted here since I1). What was
genuinely missing was outage *duration* and per-position *cadence*:

- **`<stream>.ingest.last_gap_sec`** - wall-clock seconds between a recorded
  disconnect (`_record_disconnect`) and the next successful connection
  (`_begin_connection`), computed in `services/kalshi/websocket.py`. Emitted
  only in windows where a reconnect actually completed (the gateway's
  `gap_sec_window` is consumed by `reset_ingest_window`), so the persisted
  series is one real sample per reconnect event. Negative gaps from
  wall-clock skew are dropped, never recorded.
- **`position_ticker.{tracked_count,never_seen_count,oldest/newest/median_
  update_age_sec}`** - seconds since each currently-open position's ticker
  last received a WS `ticker` message. Written by `_process_stream_ticker`
  (`services/whale_stream/whale_stream_handlers.py`) as a bare set-membership
  check + dict assignment on the exchange-wide hot path (measured 0.23 µs/msg
  hit, 0.10 µs/msg miss), keyed off the tick loop's own
  `state["open_position_tickers"]` so "open" has exactly one definition
  (paper + real account). Closed positions' leftover entries are pruned in
  `maybe_capture` post-persist, off the hot path. "Open but never seen" is
  counted separately, never fabricated as an age.

Live on first sample after shipping (2026-08-27): `tracked_count 6`,
`never_seen_count 4`, `oldest_update_age_sec 151`, `median 62.6` - i.e. four
of ten open positions had received *no* ticker message at all in the
process's lifetime, and the median tracked position was over a minute
stale. That is exactly the "a quiet ticker looks identical to an unchanged
price" gap H12 named, now measured rather than hypothesized, and the input
Task 35's staleness-corroboration threshold is meant to be tuned from.

## Metric change: `tick.phase.calibration_advisory_sec` retired (P8 Task 36, 2026-08-28)

The calibration-history snapshot / calibration auto-apply / unified advisory
auto-apply blocks moved out of `trading_loop` into their own supervised
scheduler loop (`main._maybe_run_auto_apply`, driven by `main._scheduler_loop`
like the five `_maybe_*` trigger checks, which also left the tick body the
same day). The tick therefore no longer records a `calibration_advisory`
phase, and `tick.phase.calibration_advisory_sec` stops being emitted - a real
metric-meaning change recorded here per I12 §3.12, not a gap. Its
replacement for "did auto-apply run" is `GET /api/health/pipeline`'s new
`schedulers.auto_apply` block (last-applied ages read from
`config_performance`), alongside `schedulers.{signal_resolution,backup,
research,event_schedule,catalog_scan}` (last-started age + busy flag from
each scheduler's own state). Every other `tick.phase.*` metric is unchanged.
