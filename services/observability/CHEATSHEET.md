# Observability module — cheat sheet

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

## Persistence

`data/observability.db`, one `metric_samples` table (`observed_at`,
`metric`, `value`, `labels_json`), indexed on `(metric, observed_at)`.
`record_samples_bulk()` is one connection/commit per capture cycle (a
handful of `INSERT`s), not a connection per metric — this project's
persistence idiom's own "don't connect per row" guidance
(`CLAUDE.md`/`backup.py`'s own comment on the same point).

## Restart-safe cold-start seeding — same fix as `backup.py`, applied up front

`services/backup/backup.py`'s CHEATSHEET.md documents a real live bug
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
