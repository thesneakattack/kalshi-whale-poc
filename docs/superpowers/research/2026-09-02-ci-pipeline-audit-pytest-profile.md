# pytest suite profile — CI pipeline audit (2026-09-02)

(Report authored by the "pytest suite profile and bloat" subagent; saved verbatim by the coordinating session because the subagent's Bash/Write were blocked mid-task by the worktree-isolation guard. HTML entities from the transport unescaped; nothing else changed.)

Repo: `/home/davidf/code/portfolio/showcase-projects/autotrade` @ `f12bb46` (main; only `config/settings.yaml` dirty). All runs inside the `fastapi` ddev container, `-n 4`, one suite at a time, read-only (`tests/conftest.py` + `tests/support/runtime_isolation.py` redirect every `DB_PATH` and wrap `sqlite3.connect`).

Raw artifacts in the scratchpad: `pytest_per_file.csv` (168 rows: file, tests, skipped, total_s, mean_s, max_s, max_test, share_pct), `pytest_durations.txt` (background `--durations=80`, call-phase only), `junit_run.log`, `aggregate_junit.py`, `testmon_src/*.py`, `test_inventory.csv`.

**Process note.** Midway through, this agent's Bash tool became permanently blocked by Claude Code's built-in worktree-isolation guard ("session is isolated in `.claude/worktrees/ci-pipeline-audit`, command cwd resolved to the shared checkout") while `EnterWorktree` simultaneously refused ("cwd is the repository root, not an isolated worktree"). Both full runs and the JUnit aggregation had completed before that; two planned measurements did not run and are marked **NOT RUN**: the standalone `--collect-only` timing and the `--dist loadfile` run (commands given; ~2.5 min total).

**Update (2026-09-02, session resume, all three NOT RUN items now measured):** the original session was lost to repeated rate-limit kills after this block; a resumed session, now with Bash permission and properly registered in this worktree, ran all three outstanding commands directly against the same commit (`f12bb46`). Results:

1. **`--collect-only`, cold**: pytest-reported "3074/3076 tests collected (2 deselected) in 5.36s" (outer wall 9.52s incl. `.pyc` compile + docker exec). **Warm**: pytest-reported **1.09s**. Confirms the ≤8.2s bound in §1.2 — collection itself is not a meaningful cost.
2. **`--dist loadfile`**: `3058 passed, 16 skipped, 4 warnings in 118.79s` — **8.8s (6.9%) faster** than the measured `--dist load` baseline (127.58–128.23s), consistent with and slightly better than §5's simulated -5s estimate. Still not worth the fragility §5 already flagged (few-but-heavy files sorting late as the suite grows); §5's "keep `--dist load`" recommendation stands, revenue is too small either way.
3. **fsync-bound falsification (§3.1), decisive result — hypothesis CONFIRMED, but the recommended fix changes**: `pytest tests/test_fault_log.py tests/test_signal_resolution.py -p no:testmon --basetemp=/dev/shm/pt -q --durations=3` → `18 passed in 0.74s`, call-phase durations **0.26s** (was 12.79s, a **49x** drop) and **0.14s** (was 6.36s, a **45x** drop). This is decisively an fsync-bound cost, not CPU/schema-init, exactly as the falsification test was designed to distinguish (the ≥5x threshold stated in §3.1 was cleared by ~9x). Running the **full** suite the same way (`-n 4 -m "not slow" -p no:testmon --basetemp=/dev/shm/pt`) dropped total wall from 128s to **30.08s** (4.3x) — but broke **12 tests, all in `test_cleanup_worktrees.py`**, every one failing with `gh CLI is not authenticated` even though that file's `gh`/`ddev` shims (§3.5) were present and executable-permission-bit-set under `/dev/shm/pt`. Root cause, confirmed directly: **`/dev/shm` in this container is mounted `noexec`** (`mount | grep shm` → `tmpfs (rw,nosuid,nodev,noexec,relatime,size=65536k)`; a trivial `chmod +x` shim placed there fails with `Permission denied`, exit 126, when exec'd). `test_cleanup_worktrees.py`'s tests exec real shim scripts out of `tmp_path` (§3.5) — the only files in the suite doing that — so this is invisible on the two small falsification files but fires on the first `tmp_path`-exec test the full suite reaches. **Consequence for §3.7's top-ranked recommendation:** raw `--basetemp=/dev/shm` (or any `noexec` tmpfs) is not a safe drop-in for a full-suite run in this container; the underlying fsync-cost mechanism is now proven, not hypothesized, but the safer implementation route is the already-listed alternative — `PRAGMA synchronous=OFF` scoped to test DB connections inside `tests/support/runtime_isolation.py`'s existing `_guarded_connect` wrapper (test-code only, no filesystem/exec interaction, and this container's own docstring at `test_capture_writer.py:265-266` already documents the same 36ms-commit-floor mechanism this would remove) — or, if tmpfs is still wanted for its simplicity, an `exec`-capable tmpfs mount added to the ddev/CI container config (an infra change, more invasive, unverified here) rather than the container's default `/dev/shm`.

## 1. Time profile

### 1.1 Runs

| Run | Command (in container, from `/app`) | Result |
|---|---|---|
| A (background, coordinator-launched) | `python3 -m pytest -n 4 -m "not slow" --durations=80` | `3058 passed, 16 skipped, 4 warnings in 128.23s`; host wall 12:56:25→12:58:43 (138 s) |
| B (JUnit) | `python3 -m pytest -n 4 -m "not slow" -p no:testmon -q --junitxml=/app/build/junit-profile.xml` | `3058 passed, 16 skipped, 4 warnings in 127.60s`; `<testsuite tests="3074" skipped="16" time="127.584">`; host epochs 1788372002.556→1788372139.398 = **136.84 s** outer wall |

`git check-ignore -v build/junit-profile.xml` → `.gitignore:42:build/`.

CI reference (decoded Woodpecker logs): push-event runs with `--testmon` (selection deactivated, collection on): #348 `3057 passed, 17 skipped … in 159.66s`, #306 `… 157.15s`; a run whose header has no `testmon:` line (per `pytest_testmon.py:276-315` the header exists only when testmon is "mentioned"): #351 `3057 passed, 17 skipped … in 133.64s`. n=2 vs n=1, same image — consistent with testmon's `CTracer` (`testmon_core.py:520`) costing ~24 s (~18 %) per push for data never used for selection (§4).

### 1.2 Headline numbers (run B)

| Metric | Value | Derivation |
|---|---|---|
| Test cases | 3074 (3058 passed + 16 skipped) | `testsuite/@tests` |
| Test files | 168 (`ls`, `find`, JUnit classnames agree; brief's 169 not reproducible) | |
| Σ per-test time (setup+call+teardown) | **477.66 s** | Σ `testcase/@time` |
| Ideal 4-worker wall | 119.41 s | 477.66/4 |
| Measured pytest wall | **127.58 s** | `testsuite/@time` (session timer incl. collection + worker start) |
| xdist utilisation | **93.6 %** | 477.66/(4×127.58) |
| Tail + start-up + collection | 8.17 s | 127.58−119.41 |
| Outer wall beyond pytest | 9.26 s | 136.84−127.58 (ddev exec + interpreter + plugin import) |
| Median / p90 / p99 / max | 6 ms / 0.465 s / 1.07 s / 18.70 s | |
| Tests > 1 s | 35 = 106.4 s (22 %) | |
| Tests > 0.5 s | **258 = 246.2 s (51.5 %)** | |
| Tests < 50 ms | 1773 = 9.1 s total | |

**Collection time — NOT RUN.** Bounded: collection on 4 workers + start-up + tail ≤ 8.17 s. Command: `ddev exec -s fastapi sh -c 'cd /app && time python3 -m pytest --collect-only -q -m "not slow" -p no:testmon | tail -3'` (run twice; first pays `.pyc`).

### 1.3 Top 25 files

| # | File | tests | total s | mean s | share |
|---|---|---|---|---|---|
| 1 | test_strategy_engine.py | 135 | 76.28 | 0.565 | 15.97 % |
| 2 | test_trading_gate.py | 194 | 54.97 | 0.283 | 11.51 % |
| 3 | test_kalshi_census.py | 26 | 36.56 | 1.406 | 7.65 % |
| 4 | test_paper_broker.py | 66 | 28.45 | 0.431 | 5.96 % |
| 5 | test_whalewatchers_kalshi_trade_tape.py | 61 | 18.70 | 0.307 | 3.91 % |
| 6 | test_signal_log.py | 51 | 17.88 | 0.351 | 3.74 % |
| 7 | test_fault_log.py | 12 | 14.72 | 1.226 | 3.08 % |
| 8 | test_series_watcher.py | 32 | 11.98 | 0.374 | 2.51 % |
| 9 | test_position_netting.py | 24 | 11.15 | 0.464 | 2.33 % |
| 10 | test_market_catalog.py | 48 | 9.84 | 0.205 | 2.06 % |
| 11 | test_realtime_pipeline_candidates.py | 13 | 8.93 | 0.687 | 1.87 % |
| 12 | test_diagnostics.py | 19 | 8.61 | 0.453 | 1.80 % |
| 13 | test_signal_resolution.py | 6 | 7.98 | 1.330 | 1.67 % |
| 14 | test_capture_writer.py | 22 | 7.09 | 0.322 | 1.49 % |
| 15 | test_title_cache.py | 22 | 6.79 | 0.309 | 1.42 % |
| 16 | test_settlement_edge_entry.py | 13 | 6.78 | 0.522 | 1.42 % |
| 17 | test_realtime_pipeline_replay.py | 21 | 6.30 | 0.300 | 1.32 % |
| 18 | test_observability.py | 68 | 5.76 | 0.085 | 1.21 % |
| 19 | test_backup.py | 18 | 5.70 | 0.316 | 1.19 % |
| 20 | test_trade_archive.py | 9 | 5.62 | 0.625 | 1.18 % |
| 21 | test_candidate_log.py | 22 | 5.41 | 0.246 | 1.13 % |
| 22 | test_kalshi_account_client.py | 26 | 4.72 | 0.182 | 0.99 % |
| 23 | test_quality_audit.py | 71 | 4.34 | 0.061 | 0.91 % |
| 24 | test_cleanup_worktrees.py | 12 | 4.32 | 0.360 | 0.90 % |
| 25 | test_historical_data_backfill.py | 25 | 4.27 | 0.171 | 0.89 % |

Top 25 = 379.8 s = 79.5 %; remaining 143 files = 97.9 s; 69 files cost < 0.1 s each.

### 1.4 Top 30 tests (JUnit s; call-phase from `--durations` where materially different)

18.70 (16.64) test_kalshi_census::test_real_repo_fixtures_all_have_existing_source_docs · 17.73 (17.02) test_kalshi_census::test_real_repo_census_runs_and_legacy_caller_count_stays_zero · 12.79 (11.86) test_fault_log::test_repeats_increment_a_count_instead_of_flooding_the_table · 6.36 (5.92) test_signal_resolution::test_check_signal_resolutions_respects_the_larger_batch_size · 4.14 test_realtime_pipeline_candidates::test_compare_runs_every_candidate_on_the_same_workload_set_and_reports_the_matrix · 4.05 (3.53) test_quality_audit::test_unit_cost_scanner_is_clean_on_this_repo · 3.54 test_historical_data_backfill::test_module_never_reads_deprecated_direction_aliases_directly · 3.05 test_realtime_pipeline_replay::test_cli_all_runs_every_preset · 2.29 test_realtime_pipeline_candidates::test_compare_supports_a_hygiene_variant_that_removes_loop_stalls · 2.23 test_trading_gate::test_enrich_recent_trades_pairs_correctly_even_when_entry_is_outside_the_tail_25 · 1.97 test_trading_gate::test_calibration_apply_end_to_end_updates_config_and_logs_change · 1.71 test_trading_gate::test_calibration_apply_twice_against_unchanged_data_does_not_crash · 1.52 test_kalshi_rate_limit_probe::test_probe_anonymous_ceiling_reports_where_429s_start · 1.48 test_trading_gate::test_calibration_apply_rejected_when_nothing_discriminates · 1.43 test_strategy_engine::test_check_exits_auto_exit_analyst_divergence_neutral_estimate_no_pressure · 1.36 test_series_watcher::test_negative_edge_outranks_the_gap_in_the_headline · 1.36 test_strategy_engine::test_check_exits_auto_exit_pnl_gain_triggers_at_threshold · 1.33 test_execution::test_flatten_records_a_per_ticker_error_without_aborting_the_rest · 1.31 test_strategy_engine::test_check_exits_auto_exit_volatility_dampens_loss_factor · 1.28 test_capture_writer::test_submit_is_non_blocking_and_flush_lands_in_the_db · 1.25 test_capture_writer::test_stop_waits_out_a_flush_already_blocked_on_the_daemon_budget · 1.21 test_market_analyst_agent_full_spectrum::test_analyze_full_spectrum_parses_the_forced_tool_call · 1.19 test_runtime_isolation::test_quality_summary_route_does_not_open_live_repo_data · 1.17 test_strategy_engine::test_falls_back_to_signal_close_time_when_ticker_not_in_markets · 1.14 test_diagnostics::test_performance_by_epoch_splits_trades_at_config_change_boundaries · 1.13 test_strategy_engine::test_check_exits_stop_loss_closes_position · 1.12 test_capture_writer::test_duplicate_trade_id_is_ignored_and_does_not_kill_the_thread · 1.12 test_signal_log::test_series_stats_scoped_to_series_not_global · 1.11 test_strategy_engine::test_min_seconds_to_close_unset_is_a_no_op · 1.07 test_strategy_engine::test_fresh_ws_price_keeps_the_deviation_only_rule.

The four slowest (55.6 s, 11.6 % of the suite) are two whole-repo scans and two loops of hundreds of sqlite commits (§3).

## 2. Classification

Method: every file whose category was not obvious from its name was opened (36 files read for this purpose, plus those read for §3). Files whose unit under test is `tools.*`, `scripts/`, `.claude/hooks`, or `tests/support/*` → (c); the six files in `.woodpecker/kalshi-contract-fixtures.yml` → (d); `test_browser_*`/`test_e2e_*` → (b); everything else (`services/*`, `main.py` routes, other `services/kalshi/*`) → (a).

| Category | files | tests | test share | test time | time share |
|---|---|---|---|---|---|
| (a) core app (`services/`, `main.py` routes) | 108 | 1990 | 64.7 % | 365.59 s | **76.5 %** |
| (b) frontend / browser | 3 | 14 | 0.5 % | 0.20 s | 0.04 % |
| (c) repo tooling never shipped | 51 | 799 | 26.0 % | 79.82 s | **16.7 %** |
| (d) six Kalshi contract/fixture files | 6 | 271 | 8.8 % | 32.04 s | 6.7 % |
| (c) without `test_kalshi_census.py` | 50 | 773 | 25.1 % | 43.26 s | 9.1 % |

An app-code change cannot affect the 79.8 s of (c); a `tools/`/hooks/scripts change cannot affect the 365.6 s of (a). At -n 4 the (c) share is ≈ 20 s of the 127.6 s wall, 9 s of it one file. The (d) files run twice per push/PR — here across 4 workers and again serially with `-v` in `kalshi-contract-fixtures.yml` (deliberate per its header; ≥ 32 s serial there plus install).

(c) membership (s): test_kalshi_census 36.56 · test_realtime_pipeline_candidates 8.93 · test_realtime_pipeline_replay 6.30 · test_quality_audit 4.34 · test_cleanup_worktrees 4.32 · test_historical_data_backfill 4.27 · test_quality_ratchet 3.52 · test_runtime_isolation 1.89 (tests the harness; one test drives a route) · test_coordination_engine 1.78 · test_kalshi_rate_limit_probe 1.63 (also imports `services.http_client`) · test_rest_scheduler_replay 0.77 · test_project_manifest 0.68 · test_quality_ratchet_fault_injection 0.65 · test_quality_coordination_cli 0.57 · test_run_hook 0.55 · test_quality_coordination_branch_domain 0.52 · test_wait_for_health 0.51 · test_quality_coordination_cleanup_actions 0.41 · test_guard_workflow 0.26 · test_kanban_sync_main 0.23 · test_orient_hook 0.18 · test_run_tests_hook 0.18 · test_kalshi_docs_drift 0.12 · test_realtime_replay_review_fixes 0.11 · test_kalshi_docs_sync 0.09 · test_soak_analyzer 0.08 · test_frontend_api_contract 0.06 (a `tools/quality_audit` scanner) · test_kanban_sync_sync 0.06 · test_kanban_sync_github_client 0.05 · 22 more files ≤ 0.02 s each (rest of `test_kanban_sync_*`, `test_quality_coordination_*`, test_kalshi_public_canary, test_hooks_wiring, test_mcp_and_plugin_wiring, test_watchlist_scale_stress_test).

Borderline placements: `test_check_exits_scale_benchmark` (benchmarks `services/exits`) → (a); `test_performance_regressions` (opt-in, 4 skipped) → (a); `test_quality_models`, `test_evidence_provenance` (`services/quality`) → (a); `test_quality_routes`, `test_active_terminal_refresh` → (a). (b) is nearly empty because 12 of 14 tests are env-gated skips. The 16 skips = 8 `test_browser_playwright_e2e` + 4 `test_browser_e2e` + 4 `test_performance_regressions` (`test_performance_regressions.py:4-9`).

## 3. Bloat candidates (verified by reading; cost from JUnit)

### 3.1 Systemic: fresh sqlite connection + WAL pragma + schema-init + commit per store operation — ~50 % of test time

- `services/fault_log.py:58-82,125-144`: `record()` → `_write()` → `with _connect() as conn` per call (WAL pragma, `CREATE TABLE IF NOT EXISTS`, 2× `CREATE INDEX IF NOT EXISTS`, INSERT, commit). `tests/test_fault_log.py:43-52` loops 500× → **12.79 s = 25.6 ms/record**.
- `services/signal_log.py:150-161`: same shape; `_init_schema` re-runs 4 CREATEs + 8 `_add_column_if_missing` `PRAGMA table_info` scans per connect. `tests/test_signal_resolution.py:92-102` logs `_SIGNAL_RESOLUTION_BATCH_SIZE + 20` = 220 signals → **6.36 s = 28.9 ms/insert**.
- `services/paper_broker.py:163-267`, `services/risk_manager.py:49-75`: constructors open a connection running WAL + 4 CREATE TABLE + 13 guarded ALTERs + index + commit. `tests/test_strategy_engine.py:37-75` builds both per test, then `evaluate()`/`check_exits()` open further per-call connections (candidate_log, market_history, market_analyst, signal_log). 135 tests × 0.565 s.
- Repo's own floor: `tests/test_capture_writer.py:265-266` — "every one of those commits holds it longer than 50ms on this disk (a single WAL commit measured at a 36ms floor)".
- Same 0.2–0.6 s/test signature: test_paper_broker 0.43, test_position_netting 0.46, test_settlement_edge_entry 0.52, test_trade_archive 0.62, test_series_watcher 0.37, test_signal_log 0.35, test_title_cache 0.31, test_whalewatchers_kalshi_trade_tape 0.31, test_diagnostics 0.45, test_trading_gate 0.28. With strategy_engine, fault_log, signal_resolution: ~290 s of 477 s.

Hypothesis (unfalsified): commit cost is fsync-bound on the container's overlay FS (`tmp_path` under `/tmp/pytest-of-<uid>/`). **Falsification (NOT RUN):** `ddev exec -s fastapi sh -c 'cd /app && python3 -m pytest tests/test_fault_log.py tests/test_signal_resolution.py -p no:testmon --basetemp=/dev/shm/pt -q --durations=3'` — if the 12.8 s test does not drop ≥ 5×, the cost is CPU (schema re-init), not I/O. (Docker default `/dev/shm` is 64 MB; a full-suite run may need `--shm-size` or a tmpfs on `/tmp/pytest-of-*`.)

Recommendations (test/CI side, no app change): (i) `--basetemp` on tmpfs in CI and locally; (ii) or `PRAGMA synchronous=OFF` for non-`data/` connections inside the existing `_guarded_connect` in `tests/support/runtime_isolation.py:245-250` — test-only; locking semantics (which `test_capture_writer`'s retain-on-lock tests rely on) are unaffected by `synchronous`. App-side connection reuse is out of this audit's scope and per CLAUDE.md's data-plane rule needs its own measurement — but `fault_log.record()` costing ~25 ms synchronously on the WS path (per its docstring) deserves its own ticket.

### 3.2 Whole-repo scans not marked `slow` — 43.6 s (9.1 %)

| test | s | what | overlap |
|---|---|---|---|
| test_kalshi_census::test_real_repo_fixtures_all_have_existing_source_docs | 18.70 | `build_census(REPO_ROOT)` (`:345-349`) | `_meta.source_doc` presence asserted per fixture in `test_kalshi_contracts.py:84-87` (key presence, not file existence — partial) |
| test_kalshi_census::test_real_repo_census_runs_and_legacy_caller_count_stays_zero | 17.73 | same call again, no caching (`:330-342`) | legacy-import ban already a hard error in the architecture-audit's `kalshi_boundary` scanner (`test_quality_audit.py:720-737`) |
| test_quality_audit::test_unit_cost_scanner_is_clean_on_this_repo | 4.05 | `scan_unit_cost_derivations(REPO_ROOT)` (`:878-885`), hand-filters `.claude/` | scanner is in `audit_cli._SCANNERS` (`:873-875`) → already runs in `quality-architecture-audit.yml` |
| test_historical_data_backfill::test_module_never_reads_deprecated_direction_aliases_directly | 3.54 | whole-repo `_scan_known_field_reads` to check one file (`:141-161`) | its own docstring says CI's `kalshi_boundary` check 4 fails the same read |

Census mechanism: `tools/kalshi_census.py:130-220` — each of five scanners calls `source.iter_python_files` + `source.parse_python` independently, so every file is parsed five times per `build_census`, twice per file. `pytest.ini`'s only marker reads "slow: scans the real repo tree end-to-end (redundant with a dedicated CI job); excluded by default" — all four match verbatim. Fix: mark all four `slow` (−43.6 s test time, ≈ −11 s wall), or a module-scoped census fixture asserting both properties (−18 s), or scope the alias-read test to its own file. The census tests are also non-hermetic the way `test_quality_audit.py:650-655` documents (they scan `.claude/worktrees/`).

### 3.3 Tooling simulation harnesses — ~16 s pure CPU in (c)

`test_realtime_pipeline_candidates` 8.93 s (matrix 4.14, hygiene 2.29), `test_realtime_pipeline_replay` 6.30 s (`--all` CLI 3.05), `test_rest_scheduler_replay` 0.77 s — seeded discrete-event sims of `tools/realtime_pipeline_replay.py`/`tools/rest_scheduler_replay.py` ("Nothing here opens a socket or touches a database"). Fix: shorten `duration_sec` in the matrix/hygiene workloads or mark the `--all` test slow; or run `tools/` tests in their own job.

### 3.4 Real waits

32 non-zero `sleep` sites in 14 files (test_task_supervisor 5, test_kalshi_ws_ingest_metrics 5, test_capture_writer 4, test_loop_watchdog 3, test_kalshi_ws_two_consumers 3, test_signal_log 2, test_main_scheduler_loops 2, test_backup 2, one each in test_whale_stream_stage_timing, test_position_management_concurrency, test_mve_scan, test_event_schedule, test_check_exits_scale_benchmark, test_catalog_scan_pacing). Material: `test_capture_writer.py` 7.09 s / 22 — real daemon thread per test, `time.sleep(0.3)` at `:79` and `:455`, 50 ms polling in `_row_count_when_ready` (`:19-43`), stop tests waiting out `_DAEMON_BUSY_TIMEOUT_MS`/`_STOP_JOIN_SEC` (1.25 / 1.28 / 1.12 s). Fix: monkeypatch budgets down where only ordering matters; `threading.Event` instead of sleep-polling. `test_backup.py:207-235` `sleep(0.1)`+`asyncio.sleep(0.02)` (small; file total is mostly real `Connection.backup()`); test_task_supervisor 1.19 s / 6, test_loop_watchdog 0.31 s / 2. Recoverable ≈ 4–5 s.

### 3.5 Real subprocess / server start-ups

- Real `git`: `tests/support/synthetic_git_repo.py` users — `test_cleanup_worktrees.py` 4.32 s / 12 (each `_setup()` ≈ 14 git commands + real `scripts/cleanup-worktrees.sh` under bash with `gh`/`ddev` shims, `:54-80`), `test_quality_coordination_cleanup_actions` 0.41 s, `test_run_tests_hook` 0.18 s; hook tests (`test_orient_hook`, `test_run_hook`, `test_guard_workflow`) ~1.0 s; kanban client tests use a fake `gh` runner (≤ 0.3 s). Fix: module-scoped synthetic repo + `copytree` per test (≈ −3 s).
- Real uvicorn/HTTP servers: **none in the default run.** `tests/support/e2e_server.py` is only used by the two browser files (all skipped); `uvicorn` in `test_backup.py:7` is docstring prose (verified), in `test_observability.py` assumed likewise (68 tests at 85 ms mean; not read). `test_wait_for_health` starts a stdlib `http.server` thread (0.51 s / 2).
- `TestClient(main.app)`: 10 files; each `with TestClient(main.app)` runs `main`'s lifespan (conftest `_fresh_loop_watchdog_window` docstring confirms). Per-test cost not isolated.

### 3.6 `import main`, reloads, fixture scope, parametrization, duplication

- In-test `import main`: test_pipeline_health_cost (4), test_runtime_isolation (1), test_main_scheduler_loops (1) — cached, not a cost. `importlib.reload` 3× in `test_trading_gate.py` (grep; sites not read).
- Module/session-scoped fixtures: exactly 2 in the suite. Safe widening: census result (§3.2), synthetic git repo (§3.5). Per-test DB isolation must stay per-test (the safety invariant); make it cheap (§3.1) instead.
- Parametrizations: small — test_kalshi_fees (4 decorators, 54 tests, 3.63 s), unit-cost snippets (9), `test_kalshi_ws_ingest_metrics` `[two_consumer]` (58 tests, 2.24 s).
- Duplicated coverage: (i) six (d) files run twice per push; (ii) census legacy check vs `kalshi_boundary`; (iii) alias-read test vs `kalshi_boundary` check 4; (iv) unit-cost real-repo scan vs architecture-audit job; (v) `GET /api/state` shape asserted in `test_active_terminal_refresh`, `test_e2e_terminal_static_and_api`, `test_trading_gate` (per `test_kalshi_contracts.py:58-68`) — small.

### 3.7 Recoverable time (test-time s; ÷ ~3.7 for wall)

| lever | test time | confidence |
|---|---|---|
| `synchronous=OFF` for test DBs (not raw tmpfs — see the 2026-09-02 update above) | up to ~200 s of the 246 s in tests > 0.5 s | **verified mechanism** (49x/45x on the two falsification files; full-suite tmpfs run hit 128s→30s before the `noexec`-caused 12 failures forced the fix to move from filesystem to connection-pragma) |
| mark four real-repo scans `slow` | 43.6 s | verified mechanism |
| stop testmon tracing on pushes until selection works (§4) | ~24 s wall/push | n=3 CI samples |
| replay-harness workloads | ~10 s | verified |
| sleeps / git fixture reuse | ~7 s | verified |

## 4. pytest-testmon 2.2.0

Source: `/usr/local/lib/python3.13/site-packages/testmon/` (`importlib.metadata.version` → `2.2.0`).

### 4.1 Exact `-m` deactivation path

`configure.py:65-85`:
```python
def _get_noselect_reasons(options):
    if options["testmon_forceselect"]:
        return []
    if options["testmon_noselect"]:
        return [None]
    if options["keyword"]:
        return ["-k was used"]
    if options["markexpr"]:
        return ["-m was used"]
    if options["lf"]:
        return ["--lf was used"]
    file_or_dir = options.get("file_or_dir") or []
    if any(re.match(r"(.*)\.py::(.*)", opt) for opt in file_or_dir):
        return ["you selected tests manually"]
    return []
```
`_header_collect_select` (`:114-151`) → `TmConf(select=False, collect=True)`, message `"selection automatically deactivated because -m was used, "` (`_formulate_deactivation`, `:88-95`); `pytest_testmon.py:341-342` appends `"environment: default"` — character-for-character the CI line. `register_plugins` (`pytest_testmon.py:227-247`) still registers `TestmonSelect`, `TestmonCollect` and `TestmonXdistSync` because `collect` is True; `TestmonSelect.pytest_collection_modifyitems` (`:538-559`) only reorders when `select` is False (`items[:] = selected + deselected`); `pytest_ignore_collect` (`:532-536`) is gated on `select`. Net: every branch push traces coverage on all 4 workers and writes a `.testmondata` never consulted for deselection.

### 4.2 Re-enabling selection with `-m`

`--testmon-forceselect` (`pytest_testmon.py:75-83`, `dest="testmon_forceselect"`, help: "Run testmon and select only tests affected by changes and satisfying pytest selectors at the same time") is the first branch above, and `_get_notestmon_reasons` (`:24-40`) treats it as "mentioned"; so `python -m pytest --testmon --testmon-forceselect -n 4 -m "not slow"` keeps both. https://testmon.org/ documents it with the same sentence and documents `--testmon-noselect` as "Forced if you use -m, -k, -l, -lf, test_file.py::test_name". Verify on first real run: `TestmonCollect.pytest_collection_modifyitems` (`:377-386`) calls `sync_db_fs_tests(retain=raw_test_names)` which includes the two `-m`-deselected slow tests with a `"0match"` placeholder fingerprint (`testmon_core.py:321-344`) — they will always count as "affected" but `-m` removes them again (harmless). Expect header `changed files: N, unchanged files: M, environment: default` plus a `deselected` count.

Alternatives avoiding `markexpr` (all checked against the option list): `--deselect tests/test_quality_audit.py::test_real_repo_audit_has_no_new_high_confidence_errors --deselect tests/test_quality_audit.py::test_real_repo_tree_has_no_kalshi_boundary_violations` (the `file_or_dir` regex inspects positional args only); a conftest `pytest_collection_modifyitems` skipping `slow` unless `--run-slow`; or moving them to `tests/slow/` outside `testpaths` and dropping `-m` from both tiers. `--testmon-forceselect` is the one-token change; `--deselect` leaves marker semantics untouched. The PR/main tier is unaffected either way.

### 4.3 What `.testmondata` keys on

Content, not mtime: `process_code.py:235-272` `get_source_sha()` uses `git ls-files --stage` blob SHAs (identical across clones; 2.0 notes: "Testmon now obtains file checksums from git, if possible" — https://testmon.org/blog/version-20-is-out/), falling back to a git-blob-style SHA-1 of bytes (`bytes_to_string_and_fsha`, `:87-99`). `testmon_core.py:346-372` `determine_stable()` compares `fsha` (`db.fetch_unknown_files`, `db.py:462-491`) then per-method CRC32 fingerprints (`match_fingerprint`, `process_code.py:280-306`; `db.determine_tests`, `db.py:496-559`). `mtime` is stored but labelled "optimization helper" (`common.py:31-36`) and `check_mtime` is not on that path; https://testmon.org/blog/determining-affected-tests/: "testmon doesn't store the whole code of the block but just a checksum of it". File location `<rootdir>/.testmondata` (`testmon_core.py:46-52,172-174`; `TESTMON_DATAFILE` overrides) — matches the CI script's `cp` from the repo root. **A fresh clone does not invalidate the per-branch cache.**

What does invalidate: `db.fetch_or_create_environment` (`db.py:647-700`) — a differing `system_packages` string (every `importlib.metadata` distribution, patch dropped by `drop_patch_version`, `common.py:76-95`) or `python_version` (`major.minor.micro`, `testmon_core.py:214-215`) → `packages_changed` → full re-run ("The packages installed in your Python environment have been changed. All tests have to be re-executed.", `pytest_testmon.py:336-338`). In this CI: pins are exact, but `python:3.13-slim` floats (a micro bump invalidates every branch once) and `pip install -q uv` is unpinned into the same site-packages, so a `uv` minor release would force a full re-run. Mitigation: pin `uv`, or `testmon_ignore_dependencies = uv` in `pytest.ini` (`parser.addini`, `pytest_testmon.py:119-124`; consumed by `get_system_packages(ignore=...)`). Verify via `SELECT system_packages, python_version FROM environment` on a cached `.testmondata`.

Per-branch reuse is sound (testmon has no branch notion), but a **new branch always starts empty** and tier 2 never runs `--testmon`, so nothing seeds a baseline: every branch's first push is full + traced. Option: main pushes with `--testmon --testmon-noselect` (collect only; merge gate stays full) and copy `/testmon-cache/main/.testmondata` into a branch dir on first sight.

### 4.4 xdist

Supported: `TestmonXdistSync.pytest_configure_node` (`pytest_testmon.py:443-471`) ships `exec_id`/`system_packages_change`/`files_of_interest` to workers; workers open read-only (`TestmonData.for_worker`, `testmon_core.py:248-262`), trace per test and attach `nodes_files_lines` to the teardown report (`:397-406`); only the controller writes fingerprints (`:408-421`) and finishes (`:433-439`); `get_running_as` (`:217-224`) keys on `config.option.dist`. Project history: "With pytest-testmon>=1.4.0 the support has recently been added" (https://testmon.org/blog/v14-with-xdist-support-is-out/). Cost: a CTracer per worker per test — the ~24 s/push in §1.1. `https://testmon.org/docs/` returns 404 (verified); current docs are the root page + blog.

## 5. xdist at `-n 4`

Measured (`--dist load`): 477.66 s → ideal 119.41 s; wall 127.58 s; **93.6 % utilisation**; ≤ 8.2 s for collection + start-up + idle tail combined. Host noise ~1 s (128.23 vs 127.60); CI 133–160 s.

`--dist loadfile` — **NOT RUN.** Command: `ddev exec -s fastapi sh -c 'cd /app && python3 -m pytest -n 4 --dist loadfile -m "not slow" -p no:testmon -q'`. Simulation using xdist 3.8.0's actual rule (fetched from the `v3.8.0` tag): `LoadFileScheduling(LoadScopeScheduling)` groups by file; `schedule()` orders units by **descending test count** when `--loadscope-reorder` is on, and its `default=True` (`plugin.py`: `"--loadscope-reorder", dest="loadscopereorder", action="store_true", default=True`); each finishing worker takes the next unit (`_assign_work_unit`, `workqueue.popitem(last=False)`). Greedy list scheduling of the 168 per-file JUnit totals (Wolfram):

| ordering | makespan s | per-worker loads | critical worker's last unit |
|---|---|---|---|
| count-descending (3.8.0 default) | **122.4** | 118.1 / 118.3 / 122.4 / 118.8 | test_signal_resolution.py |
| alphabetical (reorder off) | 147.2 | 147.2 / 138.2 / 104.7 / 87.6 | test_strategy_engine.py late |
| LPT (theoretical best) | 119.4 | 119.4 ×4 | — |
| measured `--dist load` | 127.6 | — | — |

Best case ≈ −5 s, inside CI's run-to-run spread, and fragile: few-but-heavy files (census 26 tests/36.6 s, fault_log 12/14.7 s, signal_resolution 6/8.0 s) sort late under count ordering and become the tail as the suite grows. Simulation ignores per-file import cost and dispatch latency (±3 s). `loadgroup` = `load` + `xdist_group` pinning; no such marks seen (not grep-verified) → behaves as `load`. Isolation fixtures give no reason to group by file: every DB redirect is per-test (`tmp_path` + autouse monkeypatch in `tests/conftest.py`); the four module-level `mkdtemp` files (`test_trading_gate`, `test_backup`, `test_active_terminal_refresh`, `test_e2e_terminal_static_and_api`) are per-process; documented cross-file leaks were closed with autouse resets, not co-location. **Keep `--dist load`; the lever is per-test cost (§3.1–3.2).**

## Assumptions / unverified

1. ~~`--collect-only` NOT RUN~~ **RESOLVED 2026-09-02**: 1.09s warm, 5.36s cold (see update above).
2. ~~`--dist loadfile` NOT RUN~~ **RESOLVED 2026-09-02**: measured 118.79s, 8.8s faster than `load` — confirms §5's simulation was directionally right (and slightly conservative).
3. ~~fsync-bound hypothesis (§3.1) unverified~~ **RESOLVED 2026-09-02, CONFIRMED**: 45-49x speedup under tmpfs on the two falsification files; full-suite run under tmpfs surfaced a new, previously-unknown fact — this container's `/dev/shm` is `noexec`, which breaks the 12 tests in `test_cleanup_worktrees.py` that exec real shim scripts from `tmp_path`. The mechanism (fsync) is now proven; the safe fix is `PRAGMA synchronous=OFF` on test connections, not a blanket `--basetemp=/dev/shm`. The "36 ms floor" was measured on the bind-mounted `data/` path, not necessarily container `/tmp` — still not re-verified for `/tmp` specifically, though the tmpfs result makes the fsync mechanism itself no longer in doubt.
4. JUnit `time` = setup+call+teardown (pytest junitxml accumulates phases); consistent with call-only durations being 0.5–2 s smaller; not re-derived from pytest source here.
5. ~24 s testmon tracing overhead rests on three CI logs (#348, #306 with `--testmon`; #351 without, inferred from the absent header line; #351's event type not checked in the Woodpecker JSON).
6. `uv` in testmon's `system_packages` inferred from `pip install -q uv` into the same interpreter; confirm on a cached `.testmondata`.
7. `test_observability.py`'s `uvicorn` mention assumed prose (not read); `test_backup.py`'s verified.
8. `importlib.reload` sites in `test_trading_gate.py` not read.
9. No `xdist_group` marks — inferred, not grep-verified.
10. (c)/(a) placement of `test_runtime_isolation`, `test_kalshi_rate_limit_probe`, `test_check_exits_scale_benchmark`, `test_performance_regressions` is a stated judgment call.
11. File count 168 (three sources agree); brief's 169 not reproducible.
12. Bash was blocked from the collect/loadfile step onward by the harness's worktree-isolation guard; the report file could not be written (Write tool refused a report file for a subagent; Bash unavailable) — all numbers up to that point come from runs completed before the block plus Read/WebFetch/Wolfram. **Resolved on session resume 2026-09-02**: a fresh top-level session, correctly registered in this worktree (not a subagent whose cwd fell back to the primary checkout), ran the three outstanding commands directly with no guard conflict — see the update note above §1.
