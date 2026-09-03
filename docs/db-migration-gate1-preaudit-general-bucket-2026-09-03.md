# Gate 1 pre-audit: general-bucket modules (21 of the ~27-module migration scope)

2026-09-03. Assigned by autotrade-1d as research input to the implementation-plan stage
(reading + documenting only, no code changes — pipeline-legal per CLAUDE.md's
research → design/spec → implementation plan sequencing). Covers the 21 "general bucket"
modules; autotrade-73 covers the 5 individually-named modules (candidate_ledger, paper_broker,
risk_manager, series_watcher, settlement_edge) separately. Same section format as that
document, per the assignment, so the implementation plan can consume both.

Gathered via four parallel mechanical read-only subagents (one per module group below), then
personally spot-checked and corrected — see "Correction to the source material" below for one
load-bearing error caught in that verification pass.

## Correction to the source material, verified directly before compiling

**All 21 modules' `_connect()` lack `@contextlib.contextmanager` — all 21 leak, no exceptions.**
Verified directly (not delegated): `grep -B2 "^def _connect" <file> | grep -E
"contextmanager|^def _connect"` across every module in scope returned a plain
`def _connect() -> sqlite3.Connection:` in every case, never `@contextlib.contextmanager`. This
matters because one of the four gathering passes (group A) reported `services/backup/backup.py`
as "both patterns close correctly... I could not identify a leaking connection pattern," based
on seeing `with _connect() as conn:` at its call sites. That conclusion is wrong: `with X as
conn:` only proves closure if `X` is a real context manager; here `_connect()` returns a plain
`sqlite3.Connection`, whose *native* `__enter__`/`__exit__` only commits or rolls back the
transaction, not close()s the connection — the exact bug shape already fixed twice this session
in `market_history.py`/`title_cache.py`/`market_catalog.py`/`signal_log.py`/`fault_log.py`, and
already confirmed present in `risk_manager.py`/`trade_category.py`/`paper_broker.py` by the
research doc (PR #504) and its adversarial review. `backup.py`'s primary `_connect()` genuinely
leaks like every other module here — the design spec (d5b1430)'s "backup.py: primary
`_connect()` leaks" framing was correct; group A's contradicting read of it was not, and is
superseded by this direct check.

**Separately, `backup.py`'s *other* connection pattern (the `src_conn`/`dest_conn` pair used for
the actual file-backup copy, not schema access) does genuinely close correctly** — both
subagent passes agree on this, and it's a manual `try/finally: conn.close()` pattern, not a
`with` statement, so it isn't subject to the same mistake. Two different connection patterns in
one file; only the primary `_connect()` needs migrating.

**A count that needed a wider scope, not a correction**: the assignment's framing cited "22
bare test callers" for `tools/coordination_engine.py`'s `_connect()`. An initial recount against
only `tests/test_coordination_engine.py` found 15 and this document briefly (and wrongly)
called the "22" figure an error. It wasn't — 22 is the right number, just spread across four
test files that all import and call `coordination_engine._connect()` directly, not one:
`tests/test_coordination_engine.py` (15), `tests/test_quality_coordination_branch_domain.py`
(4: L80, L106, L126, L149), `tests/test_quality_coordination_cli.py` (2: L72, L88),
`tests/test_quality_coordination_cleanup_actions.py` (1: L95) — 15+4+2+1 = 22, all bare, zero
using `with`. Verified via `grep -rnE '_connect\b' tests/ | grep coordination`. The
single-file 15 figure is also accurate, just for a narrower scope than the original claim was
actually about.

## Module-by-module findings

Format: `_connect()` contents (PRAGMAs / DDL / indexes / column-adds — everything a migrated
callback must preserve), table names, call-site shape (module + tests), DB_PATH test isolation
method, and event-loop exposure. All `_connect()` bodies below are confirmed to lack
`@contextlib.contextmanager` per the correction above — not repeated per-module for brevity.

### services/accounts_store.py
- **Body** (`accounts_store.py:27-46`): `PRAGMA journal_mode=WAL`; `CREATE TABLE IF NOT EXISTS
  connected_accounts (provider TEXT PRIMARY KEY, credentials_encrypted BLOB NOT NULL,
  updated_at REAL NOT NULL)`. No indexes, no column-adds.
- **Tables**: `connected_accounts`.
- **Call sites** (module): `save` L57, `load` L69, `delete` L82, `status` L88 — all
  `with _connect() as conn:`.
- **Tests**: **no test file exists** for this module.
- **Event loop**: **YES, confirmed direct exposure with no tick_executor wrapping** —
  `main.py:1828-1850`, four synchronous calls (`enabled()`, `status()`, `save()`, `delete()`)
  made directly from async route handlers (`list_accounts`, `connect_account`,
  `disconnect_account`). This is a real, pre-existing event-loop-blocking gap independent of
  the migration — worth its own Gate 1 acceptance criterion (route through `tick_executor.run()`
  or migrate-and-wrap together), not just a "preserve as-is" migration.

### services/alerting/alerting.py
- **Body** (`alerting.py:78-96`): `PRAGMA journal_mode=WAL`; `CREATE TABLE IF NOT EXISTS alerts
  (...)`; `CREATE INDEX IF NOT EXISTS idx_alerts_category ON alerts (category, resolved_at)`.
- **Tables**: `alerts`.
- **Call sites** (module): 6 sites (L102, 122, 142, 163, 173, 211), all `with _connect() as
  conn:`.
- **Tests**: `tests/test_alerting.py:26`, `monkeypatch.setattr(alerting, "DB_PATH", ...)`.
- **Event loop**: not fully determined — `check_and_alert` is `async`, awaited from
  `main.py:831`'s trading_loop, and calls `_connect()` synchronously inside that async function
  without an explicit `tick_executor.run()` wrap. Needs a direct read of `check_and_alert`'s
  call chain before the implementation plan can state this as settled either way.

### services/backup/backup.py (partial migration — primary `_connect()` only)
- **Body** (`backup.py:96-122`): `PRAGMA journal_mode=WAL`; `CREATE TABLE IF NOT EXISTS
  backup_runs (...)`; `CREATE INDEX IF NOT EXISTS idx_backup_runs_started_at`;
  `_add_column_if_missing(conn, "backup_runs", "tier", "TEXT")`.
- **Tables**: `backup_runs`.
- **Call sites** (module, primary `_connect()`): L227, L252, both `with _connect() as conn:`
  (leaks — see correction above). **Separate, already-correct pattern**: `_backup_one_file`
  (L152-160) opens `src_conn`/`dest_conn` directly via `sqlite3.connect()` with manual
  `try/finally: close()` — not part of this migration's scope, already closes correctly.
- **Tests**: `tests/test_backup.py:66`, `monkeypatch.setattr(backup, "DB_PATH", ...)`.
- **Event loop**: NO — dispatched via `task_supervisor.supervise()` wrapping
  `asyncio.to_thread` at `backup.py:386-389` and `426-432`.

### services/candidate_log.py
- **Body** (`candidate_log.py:76-113`): `PRAGMA journal_mode=WAL`; executes
  `capture_writer.REJECTED_CANDIDATES_DDL_SQL` and `capture_writer.REJECTION_EVENTS_DDL_SQL`
  (schema owned by `capture_writer.py`, not this file — migration must preserve that
  cross-module DDL-ownership pattern, not just inline two arbitrary CREATE TABLEs); two
  `CREATE INDEX` statements (`idx_rejection_events_gate`, `idx_rejection_events_unresolved`
  with a partial-index `WHERE resolved = 0` clause); two `_add_column_if_missing` calls
  (`unit_cost` on both tables).
- **Tables**: `rejected_candidates`, `rejection_events` (both DDL-sourced from
  `capture_writer.py`).
- **Call sites** (module): 6 sites, all `with _connect() as conn:`.
- **Tests**: `tests/test_candidate_log.py:10`, monkeypatches `DB_PATH` plus
  `capture_writer`'s `_STORE_PATHS`/buffer state (L18-21) — this module's test isolation is
  entangled with `capture_writer.py`'s own state, not self-contained.
- **Event loop**: NO — `resolve_from_market_results` (the main write path) runs via
  `tick_executor.run()` from `settlement_resolver.py:270-271`.

### services/config/config_performance.py
- **Body** (`config_performance.py:46-86`): `PRAGMA journal_mode=WAL`; two `CREATE TABLE`
  statements (`config_variants`, `applied_changes`); one `_add_column_if_missing`
  (`source` on `applied_changes`).
- **Tables**: `config_variants`, `applied_changes`.
- **Call sites** (module): 8 sites, all `with _connect() as conn:`.
- **Tests**: per-test `monkeypatch.setattr(cp, "DB_PATH", ...)`, no shared fixture (14+
  individual call sites in the test file).
- **Event loop**: NO — called from `research.build_report`, itself dispatched via
  `asyncio.to_thread(run_and_store, cfg)` at `research.py:258`.

### services/data_quarantine.py
- **Body** (`data_quarantine.py:54-73`): `PRAGMA journal_mode=WAL`; `CREATE TABLE IF NOT
  EXISTS quarantine_ranges (...)`; `CREATE INDEX IF NOT EXISTS idx_q_open`.
- **Tables**: `quarantine_ranges`.
- **Call sites** (module): 6 sites, all `with _connect() as conn:`.
- **Tests**: no dedicated test file — isolated centrally via
  `tests/support/runtime_isolation.py:53,181-187`'s `_redirect_persistence_modules()`, not a
  per-module fixture. The migration's per-module test pattern should account for this shared
  isolation mechanism rather than assume every module has its own fixture.
- **Event loop**: **YES** — `is_active()` is called from `series_watcher.py:384`
  (`_quarantine_active()`), called from `record_trade()` L310, called directly from `async def
  _process_stream_trade()` in `whale_stream/whale_stream_handlers.py:170` — on the trade
  ingestion hot path.

### services/game_state.py
- **Body** (`game_state.py:81-124`): `PRAGMA journal_mode=WAL`; `CREATE TABLE IF NOT EXISTS
  game_states (...)`; a PRAGMA-table_info-guarded `ALTER TABLE ... ADD COLUMN event_type TEXT`
  (L119-121, same idempotent-check shape as `_add_column_if_missing` but inlined rather than
  calling the helper); two `CREATE INDEX` statements.
- **Tables**: `game_states`.
- **Call sites** (module): `flush` L314, `prune` L389, `timeline` L402, `stats` L414 — all
  `with _connect() as conn:` (all leak, per the correction above). **Separate bare connection**:
  `prune_crypto_backlog()` L365 opens `sqlite3.connect(DB_PATH)` directly (not via
  `_connect()`), sets `conn.isolation_level = None` for autocommit (required for `VACUUM`), and
  closes manually at L375 — correct, but a distinct pattern the migration needs a specific
  answer for (the unified `db.py`'s `connect()` doesn't currently support a caller-chosen
  `isolation_level`).
- **Tests**: autouse fixture `_isolated` (`test_game_state.py:15-23`); tests read the DB
  directly via `sqlite3.connect(gs.DB_PATH)` rather than through `_connect()`.
- **Event loop**: partial — `record()` (buffer-only, no `_connect()` call) is invoked directly
  from async code in `market_watch/live_status.py:297` and `event_metadata.py:215`, but that's
  fine since it doesn't touch the DB. `flush`/`prune` run via tick_executor
  (off event loop, through `main.py`'s `_maybe_prune_capture_stores` /
  `_flush_secondary_capture_stores`); `timeline`/`stats` are reachable from diagnostic routes
  and may run on the event loop — not fully resolved without reading those route handlers
  directly.

### services/history/suggestion_decisions.py
- **Body** (`suggestion_decisions.py:31-45`): `PRAGMA journal_mode=WAL`; `CREATE TABLE IF NOT
  EXISTS declined_suggestions (...)`. No indexes, no column-adds — simplest module in this
  bucket.
- **Tables**: `declined_suggestions`.
- **Call sites** (module): 5 sites, all `with _connect() as conn:`.
- **Tests**: autouse fixture `_redirect_db` (`test_suggestion_decisions.py:6-8`).
- **Event loop**: **YES** — `decline()`, `undecline()`, `list_declined()` are called directly
  from async route handlers in `services/analytics/routes.py` (L66, L79, L85) with no
  tick_executor wrapping visible in what was checked.

### services/index_feed/ingestion.py
- **Body** (`ingestion.py:62-93`): `PRAGMA journal_mode=WAL`; `CREATE TABLE IF NOT EXISTS
  index_ticks (...)`; two `CREATE INDEX` statements, one with a partial-index `WHERE
  q15_window_size IS NOT NULL` clause.
- **Tables**: `index_ticks`.
- **Call sites** (module): `_last_tick_before_sync` L234, `flush` L333, `prune` L353,
  `tick_stats` L378 — all `with _connect() as conn:`.
- **Tests**: autouse fixture `_isolated` (`test_index_feed.py:26-32`); tests also connect
  directly via `sqlite3.connect(_ingestion.DB_PATH)` for post-flush assertions (L124, L312) —
  this is the same module this session's earlier work (issue #485, PR #498) fixed a real
  event-loop/background-thread flush race in; that fix (awaiting the scheduled flush task
  explicitly) is a test-only synchronization fix and doesn't change `_connect()`'s own shape.
- **Event loop**: mostly off (`_last_tick_before_sync`, `flush`, `prune` via
  `tick_executor.run()`), except `tick_stats()` which may be reachable from async
  observability/diagnostic routes — not fully resolved.

### services/market_analyst_agent/_db.py
- **Body** (`_db.py:76-87`): `PRAGMA journal_mode=WAL`; calls `_init_schema(conn)` (L18-73,
  creates 3 tables) rather than inlining DDL directly in `_connect()` — a structurally different
  shape from every other module in this bucket, worth flagging since the migration's
  `register_schema` callback model maps directly onto this existing `_init_schema` function.
- **Tables**: `analyses`, `series_analyses`, `full_spectrum_analyses`.
- **Call sites** (module): only 1 direct `_connect()` call (`clear_all()`, L102). Everything
  else routes through `_scoring_read_connection()` (L90-98), which uses
  `_scoring_pool.cached_read_connection(DB_PATH, _init_schema)` — a **thread-locally cached**
  connection, not a fresh per-call `_connect()`. This module is NOT a simple per-call-open-close
  candidate the way the others are; its primary read path already uses a different persistence
  strategy (see `services/whalewatchers/_scoring_pool.py`, already noted in the design spec's
  "considered and declined: pooling" section as a real, working precedent — this module is
  where that precedent actually lives).
- **Tests**: per-test helper `_agent(tmp_path, monkeypatch)` (`test_market_analyst_agent_db.py:9-11`),
  not autouse.
- **Event loop**: NO for the one direct `_connect()` call; the cached-pool path is accessed from
  tick_executor-routed callers per the subagent's read, not confirmed independently here.

### services/market_events/event_schedule.py
- **Body** (`event_schedule.py:133-148`): `PRAGMA journal_mode=WAL`; `CREATE TABLE IF NOT
  EXISTS event_schedule (...)`.
- **Tables**: `event_schedule`.
- **Call sites** (module): `load_all` L154, `save` L166 — both `with _connect() as conn:`.
- **Tests**: autouse-style monkeypatch (`test_event_schedule.py:32-33`).
- **Event loop**: NO — `_maybe_resolve_event_schedules` runs in `main.py`'s synchronous
  trading-loop tick, not called from async code.

### services/observability/observability.py
- **Body** (`observability.py:41-59`): `PRAGMA journal_mode=WAL`; `CREATE TABLE IF NOT EXISTS
  metric_samples (...)`; `CREATE INDEX IF NOT EXISTS idx_metric_samples_metric_time`.
- **Tables**: `metric_samples`.
- **Call sites** (module): 6 sites, all `with _connect() as conn:`.
- **Tests**: autouse fixture (`test_observability.py:38-40`).
- **Event loop**: NO — `maybe_capture` runs in the synchronous trading-loop tick.

### services/research/research.py
- **Body** (`research.py:38-64`): `PRAGMA journal_mode=WAL`; `CREATE TABLE IF NOT EXISTS
  research_reports (...)`; `CREATE INDEX IF NOT EXISTS idx_research_reports_generated_at`.
- **Tables**: `research_reports`.
- **Call sites** (module): `recent`, `latest`, `summary`, `run_and_store` — 4 sites, all
  `with _connect() as conn:`.
- **Tests**: autouse fixture (`test_research.py:33-35`).
- **Event loop**: NO — `_maybe_run_research` is called synchronously from the trading loop, and
  the actual blocking write (`run_and_store`) is explicitly dispatched via
  `asyncio.to_thread(run_and_store, cfg)` (`research.py:246-267`).

### services/reset/reset_log.py
- **Body** (`reset_log.py:25-45`): `PRAGMA journal_mode=WAL`; `CREATE TABLE IF NOT EXISTS
  reset_events (...)`; `CREATE INDEX IF NOT EXISTS idx_reset_events_executed_at`.
- **Tables**: `reset_events`.
- **Call sites** (module): `record` L58, `recent` L69 — both `with _connect() as conn:`.
- **Tests**: **no dedicated test file** — only indirectly exercised via
  `tests/test_reset_routes.py`; DB_PATH isolation for this module specifically not confirmed.
- **Event loop**: NO — `record()` is called from `services/reset/routes.py:161`, a synchronous
  FastAPI route handler (FastAPI runs sync routes in a thread pool automatically, so this
  doesn't block the event loop directly, though it's worth the implementation plan stating this
  explicitly rather than assuming).

### services/reset/trade_archive.py
- **Body** (`trade_archive.py:54-136`) — the largest schema in this bucket: `PRAGMA
  journal_mode=WAL`; `CREATE TABLE IF NOT EXISTS epochs (18 columns)`; `CREATE TABLE IF NOT
  EXISTS archived_trades (12 columns, composite PRIMARY KEY (epoch_id, id))`; four
  `_add_column_if_missing` calls (`netting_improvement_usd`, `netting_bar_usd`,
  `netting_vol_ratio`, `netting_exit_fee_usd`, all REAL); two `CREATE INDEX` statements; `CREATE
  TABLE IF NOT EXISTS archived_positions (8 columns, composite PRIMARY KEY (epoch_id, ticker))`.
- **Tables**: `epochs`, `archived_trades`, `archived_positions`.
- **Call sites** (module): `archive_epoch` L229, `epochs` L285, `epoch_trades` L344 — all
  `with _connect() as conn:`.
- **Tests**: autouse fixture monkeypatches both `trade_archive.DB_PATH` AND
  `paper_broker.DB_PATH` together (`test_trade_archive.py:17-22`) — this module's tests are
  cross-coupled with `paper_broker.py`'s isolation, not self-contained.
- **Event loop**: NO — `archive_epoch` is called from `services/reset/routes.py:190`, a
  synchronous route handler.

### services/series_cache.py
- **Body** (`series_cache.py:34-78`): `PRAGMA journal_mode=WAL`; three `CREATE TABLE`
  statements (`series_cache`, `series_metadata`, `series_tags`); two `CREATE INDEX` statements.
- **Tables**: `series_cache`, `series_metadata`, `series_tags`.
- **Call sites** (module): 2 sites, both `with _connect() as conn:`. Tests also call
  `cache._connect()` directly (3 sites, all `with`).
- **Tests**: helper `_sc(tmp_path, monkeypatch)` (`test_series_cache.py:7`).
- **Event loop**: not confirmed either way — no async callers found in the scope checked, but
  not exhaustively ruled out.

### services/series_evaluator.py
- **Body** (`series_evaluator.py:50-73`): `PRAGMA journal_mode=WAL`; `CREATE TABLE IF NOT
  EXISTS series_status (...)`.
- **Tables**: `series_status`.
- **Call sites** (module): 7 sites, all `with _connect() as conn:`. Tests call
  `se._connect()` directly at 12 sites, all `with`.
- **Tests**: helper `_se(tmp_path, monkeypatch)` (`test_series_evaluator.py:7-9`) — also
  monkeypatches `signal_log.DB_PATH` alongside its own, another cross-module test coupling.
- **Event loop**: not confirmed either way.

### services/trade_category.py
- **Body** (`trade_category.py:27-78`): `PRAGMA journal_mode=WAL`; `CREATE TABLE IF NOT EXISTS
  trade_category (...)`; a PRAGMA-table_info-guarded `ALTER TABLE ... ADD COLUMN subcategory
  TEXT` (inlined, same shape as `game_state.py`'s inlined column-add, not calling the shared
  `_add_column_if_missing` helper).
- **Tables**: `trade_category`.
- **Call sites** (module): 6 sites, all `with _connect() as conn:`.
- **Tests**: autouse fixture `_redirect_db` (`test_trade_category.py:6-8`).
- **Event loop**: not confirmed either way.

### services/shadow_mode.py
- **Body** (`shadow_mode.py:48-99`): `PRAGMA journal_mode=WAL`; two `CREATE TABLE` statements
  (`shadow_trades`, `shadow_risk`); two `_add_column_if_missing` calls (`day_start_date` on
  `shadow_risk`, `config_fingerprint` on `shadow_trades`). Notably: `shadow_risk`'s
  `check_daily_loss` already has the `day_start_bankroll == 0` guard this session verified
  earlier tonight (in a different, unrelated PR) as the precedent for `risk_manager.py`'s own
  equivalent fix — same file, unrelated to this migration's scope, just worth cross-referencing
  since this doc's earlier verification work already covers `shadow_mode.py`'s schema.
- **Tables**: `shadow_trades`, `shadow_risk`.
- **Call sites** (module): 6 sites, all `with _connect() as conn:`. **One bare test call**:
  `tests/test_shadow_mode.py:255-262` opens `sqlite3.connect(db_path)` directly (not via
  `_connect()`) for a specific pre-existing-row test setup, closed manually.
- **Tests**: helper `_trader(tmp_path, monkeypatch, ...)` (`test_shadow_mode.py:8`).
- **Event loop**: not confirmed either way.

### services/whale_calibration/calibration_history.py
- **Body** (`calibration_history.py:27-49`): `PRAGMA journal_mode=WAL`; `CREATE TABLE IF NOT
  EXISTS snapshots (...)`.
- **Tables**: `snapshots`.
- **Call sites** (module): 4 sites, all `with _connect() as conn:`.
- **Tests**: autouse fixture `_redirect_db` (`test_calibration_history.py:6-8`).
- **Event loop**: not confirmed either way.

### tools/coordination_engine.py (lowest priority — CLI tool, not server-resident)
- **Body** (`coordination_engine.py:29-63`): sets `conn.row_factory = sqlite3.Row` (the only
  module in this bucket that does) in addition to `PRAGMA journal_mode=WAL`; three `CREATE
  TABLE` statements (`signal_state`, `coordination_runs`, `cleanup_actions`).
- **Tables**: `signal_state`, `coordination_runs`, `cleanup_actions`.
- **Call sites** (module): `_connect()` is called only by external callers, not internally —
  functions take `conn` as a parameter.
- **Tests**: **22 bare `_connect()` calls across four test files, zero using `with`** — see
  "A count that needed a wider scope" above for the per-file breakdown (15 in
  `test_coordination_engine.py` alone, 22 total across all four `tests/test_quality_coordination_*.py`
  files that also call `ce._connect()` directly). `tests/test_coordination_engine.py`
  monkeypatches `DB_PATH` per-test.
- **Event loop**: explicitly N/A — the module's own docstring (L11-12) states it's "never
  imported by main.py or any part of the live trading app, per CLAUDE.md's 'Workflow/tooling
  and application code must never overlap' standing rule." Confirms the "lowest priority"
  framing from the assignment: real fd leak, but a short-lived CLI process where the OS
  reclaims fds on exit, not a server-resident accumulation risk.

## What this document does not cover

The 5 individually-named modules (candidate_ledger, paper_broker, risk_manager, series_watcher,
settlement_edge) — assigned separately to autotrade-73. `store_stats.py` (the design spec's
scope-correction addition, not part of either the original 25 or this 21-module assignment).
Independent verification of every "not confirmed either way" event-loop-exposure line above —
those are honestly flagged gaps, not silent assumptions, and should be resolved before the
implementation plan finalizes migration order for those specific modules. This document was
gathered via parallel subagents and personally spot-checked (the `@contextlib.contextmanager`
sweep across all 21 modules, and the coordination_engine bare-call recount) rather than fully
independently re-derived line-by-line for every claim — a genuinely separate adversarial review
pass, per this repo's own process, should still verify this before it's treated as final input
to the implementation plan, the same way PR #504's research doc and the design spec both got
one.
