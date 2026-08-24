# Research module — cheat sheet

Owns: `research.py` (evidence-triggered orchestration of seven existing
read-only analyzers into one persisted snapshot, `data/research_reports.db`)
+ `routes.py` (`/api/research/status|latest|history|run`, the
informativeness half). New 2026-08-24, Quality Control Plane Task 16
(`docs/superpowers/plans/2026-08-24-quality-control-plane.md`).

## What this is NOT

No new algorithm, no new statistic, no new data source. `build_report`
calls seven functions that already existed and already compute exactly
what they compute for their own existing callers — this module's only real
job is deciding *when* to bother re-running that whole set, and *keeping*
the result somewhere queryable instead of it evaporating the moment an HTTP
response is sent. **Never applies anything** — no `config_store.update`,
no `*.log_applied_change`, anywhere in this package. If a future edit adds
one, it has left this module's actual purpose.

## What `build_report` actually composes, and why each one

| Section | Real function | Why this one, not a duplicate |
|---|---|---|
| `diagnostics` | `diagnostics.run_offline(cfg, now=now)` | Already excludes the one check that makes a live Kalshi call (`check_coverage`) — safe to call from a background task, same reason `GET /api/quality/summary` reuses it as-is. |
| `trade_analytics` | `trade_analytics.compute_summary(trade_rows)` | `trade_rows` built once via `build_trade_history`, shared with the advisory section below rather than rebuilt twice. |
| `confidence_calibration` | `confidence_calibration.generate_calibration_report(...)` | Same call shape as `main.py`'s own periodic snapshot trigger (`calibration_history`) — `report` is `None` until `min_resolved_signals` is met, not fabricated. |
| `advisory` | `advisory_engine.generate_recommendations(...)` | **Byte-for-byte the same read-only assembly as `GET /api/advisory/recommendations`** (`services/advisory/routes.py`) — copied from that route, not reinvented, specifically so this can never drift into calling the auto-apply variant by accident. Gated on `advisory.enabled` the same way that route is. |
| `candidate_population_gate` | `candidate_log.population_gate_summary()` | The plan's own wording ("candidate population gate summary") maps directly to this function's name — distinct from `candidate_log.gate_summary()`, which is a narrower view `advisory_engine` consumes internally as one of its own inputs above. |
| `series_evaluator` | `series_evaluator.overview()` | Same data `GET /api/series-evaluator/status` shows — deliberately not filtered by `series_evaluator.enabled` (that route's own documented "always visible regardless of enabled" history view). |
| `settlement_edge` | `settlement_edge.edge_report()` | Own default `min_samples` threshold, unchanged. |
| `config_epoch` | `config_performance.fingerprint(cfg)` + `all_variants()` + `applied_changes_count()` + `recent_applied_changes(limit=20)` | "config epoch information" from the plan — the fingerprint identifies *which* config produced everything else in this report, the variant/change history says how it got there. |

## Trigger policy — `should_run` / `current_counts`

OR semantics, evidence-triggered rather than a fixed timer (direct plan
requirement): due once **either**
`resolved_signals` or `closed_trades` has grown by its configured threshold
since the last checkpoint (`research.min_new_resolved_signals` /
`min_new_closed_trades`, defaults 100 / 50).

`current_counts()` is deliberately cheap — one indexed `signal_log.
total_count(resolved_only=True)` query plus an in-memory scan of
`broker.trade_log` (already loaded, no DB read) — safe to call on every
trading-loop tick, same "cheap gate before the expensive path" shape as
`calibration_history.due()`. It does **not** run `build_trade_history`'s
full pairing/enrichment pass; it only needs a count, not the rows.

**`checkpoints={}` (no prior watermark) on a genuinely fresh install is a
deliberate "run once" choice, not an oversight** — if real history has
already accumulated past a threshold before research was ever enabled,
that's a legitimate first run. The DIFFERENT failure mode this must not
reproduce — a *lost in-memory* checkpoint firing an unwarranted immediate
run on every `uvicorn --reload`, `services/backup/backup.py`'s documented
2026-08-23 incident — is handled one layer up, in `_maybe_run_research`:
`state["research"]["checkpoints"]` starts `None` (not `{}`), and the first
real check in a process seeds it from the most recently *persisted*
report's own recorded watermark via `latest()`, exactly mirroring
`_maybe_run_backup`'s `last_started_at` fix. Only a genuinely fresh
install with **zero** persisted reports ever falls through to `{}`.

## Persistence

`data/research_reports.db`, one `research_reports` table (`id`,
`generated_at`, `resolved_signals_count`, `closed_trades_count`,
`report_json`), indexed on `generated_at`. Every row is additive — no
row is ever updated or deleted by this module (no pruning here yet; add
one if `research_reports.db` growth ever becomes a real concern — full
JSON reports are considerably larger than `observability.db`'s numeric
samples, so this may need it sooner).

## Wiring

- **Trigger**: `_maybe_run_research(cfg)` in `main.py`'s `trading_loop`,
  right next to `_maybe_run_backup(cfg)` — same fire-and-forget
  `task_supervisor.supervise(..., component="research", operation="run")`
  shape, same `asyncio.to_thread(run_and_store, cfg)` inside the background
  wrapper so the several-analyzer sweep can't stall the trading loop.
- **State**: `services/app_state.py`'s `state["research"]` —
  `{"running": bool, "task": asyncio.Task | None, "checkpoints": dict | None}`.
- **Router**: `app.include_router(research_routes.router)` in `main.py`.
- **Quality summary**: `GET /api/quality/summary` includes a `"research"`
  key with only `running`/`last_report_at` — deliberately never the full
  report (`GET /api/research/latest` is the place for that; repeating a
  seven-module composite on every quality-summary poll would undo the
  point of persisting it at all).
- **Test isolation**: `services.research.research` is registered in
  `tests/support/runtime_isolation.py`'s `PERSISTENCE_MODULE_PATHS` —
  `tests/test_runtime_isolation.py`'s AST cross-check fails the suite if a
  future edit ever lets a `DB_PATH` owner go unregistered again.

## Config

`config/settings.yaml`'s `research` section: `enabled` (default `false` —
off until manually reviewed, direct plan requirement), `min_new_resolved_
signals` (100), `min_new_closed_trades` (50).

## Manual trigger while disabled

`POST /api/research/run` works regardless of `research.enabled`, same
convention as `POST /api/backup/run` — an operator explicitly asking for a
report is a different action from the periodic evidence-triggered
scheduler being on. It resets the in-memory checkpoint to its own run's
watermark afterward, so the periodic trigger doesn't immediately re-fire
against history a manual run just covered.

## Why `research.py` imports `services.app_state` lazily, inside functions

Unlike `services/observability/observability.py` (which deliberately takes
`state`/`trade_stream`/`index_stream` as plain arguments to stay import-
side-effect-free), `research.py` follows the *other* established
convention instead — the one `services/backup/backup.py`/`alerting.py`/
`event_schedule.py` already use, importing `services.app_state` directly.
Reasoning: `build_report` already imports six other heavy analytics
modules (`diagnostics`, `advisory_engine`, `confidence_calibration`, ...)
that themselves ultimately sit on the same construction graph
`services.app_state` builds — there is no lightweight-module case to
protect here the way there was for observability's periodic sampler.
`broker` is imported **inside** `current_counts()`/`build_report()` rather
than at module level purely so `tests/test_research.py` can import
`services.research.research` and monkeypatch its analyzer functions
without triggering `services.app_state`'s full singleton construction at
collection time, for any test that never calls those two functions.
