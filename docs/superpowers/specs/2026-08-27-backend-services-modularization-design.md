# Backend Services Modularization Design

## Status

Brainstormed and approved in chat 2026-08-27. Not yet planned or implemented.

## Goal

`services/` has 24 already-grouped subpackages (each `services/<name>/` with its
own `routes.py`, extracted during modularization phases 115-119 per
`static/status.html`) and 49 flat top-level `.py` files that were never moved into
a package. This spec covers a bounded subset of those 49: the ones that are the
primary logic behind an existing package's `routes.py` but never physically moved
in, plus one route (`POST /api/reset`) never extracted into a package at all.
Direct instruction (2026-08-27): "if you look at the services folder you'll see
that there's a good amount that needs to be modularized/grouped by concern,
mostly frontend services."

## Non-goals

- The other ~35 flat files (`strategy_engine.py`, `risk_manager.py`,
  `paper_broker.py`, `candidate_ledger.py`, `candidate_log.py`,
  `candidate_retry.py`, `execution.py`, `shadow_mode.py`, `settlement_edge.py`,
  `settlement_edge_entry.py`, `confidence_scoring.py`, `whale_simulator.py`,
  `whale_pipeline_perf.py`, `kalshi_fees.py`, `series_watcher.py`,
  `series_evaluator.py`, `signal_log.py`, `market_history.py`,
  `market_lookup.py`, `game_state.py`, `data_quarantine.py`,
  `trade_category.py`, `stats_power.py`,
  `app_state.py`, `http_client.py`, `task_supervisor.py`, `tick_executor.py`,
  `loop_watchdog.py`, `latency_agg.py`, `fault_log.py`, `logging_config.py`,
  `ml_feed.py`, `auth.py`, `accounts_store.py`, `mutual_exclusivity.py`,
  `whale_gate.py`) are core trading-engine/infrastructure logic, not
  dashboard-facing. Out of scope — a separately-motivated cleanup, if ever done.
- `ws_manager.py` — investigated and dropped (decisions record below).
- No logic changes bundled into the moves. Pure relocation; behavior must be
  byte-identical before and after each task.
- No test-file relocation — `tests/` is flat repo-wide (confirmed: no existing
  `services/<name>/` package has a mirrored `tests/<name>/` directory), so moved
  modules' existing `tests/test_<module>.py` files stay exactly where they are.
- No increase in test coverage for `reset_log.py`/`account_positions.py`
  (currently thin — only incidentally exercised via `tests/test_trading_gate.py`).
  Real, but a separate concern from grouping-by-location.

## Decisions record (user-confirmed 2026-08-27)

1. **`ws_manager.py` stays flat, out of this initiative.** Investigated its real
   importers: `main.py` and `services/whale_stream/decision_bridge.py`/
   `whale_stream_handlers.py` — core whale-stream pipeline code, not route
   handlers. It happens to push updates the dashboard consumes, but its own
   domain is realtime infrastructure, not a dashboard tab. Revisit separately if
   realtime infrastructure ever gets its own grouping pass.
2. **`title_cache.py` and `series_cache.py` stay flat, deliberately.** Each has
   5-9 real importers spanning multiple existing packages (`market_catalog`,
   `market_watch`, `history`, `app_state`, `state_view`) — genuinely
   cross-cutting shared caches, not owned by any one tab. Forcing either into one
   package would misleadingly imply domain ownership it doesn't have.
3. **`state_view.py` stays flat, same reasoning.** 3 importers
   (`account_positions.py`, `services/history/routes.py`, `main.py`) spanning
   position + history + the main state route — its own docstring already frames
   it as shared ("Read-side helpers that build `/api/state`'s (and the
   position/history route...)").
4. **Migration mechanics: physical `git mv`, not a re-export shim.** Every
   existing `services/<name>/` package (`market_watch`, `kalshi`, `exits`, ...)
   is a genuine physical move — files live inside the package directory, nothing
   re-exports from the old flat location. A `services/history/__init__.py:
   from services.trade_analytics import *`-style shim was considered and
   rejected: lower effort, but doesn't fix what's actually being pointed at (the
   file still lives in the wrong place) and breaks with every other package's
   established precedent in this repo.

## 1. Architecture — the four groups

### `services/history/`
Move in: `trade_analytics.py`, `regime_analytics.py`, `suggestion_decisions.py`.
`services/history/routes.py` already imports `trade_analytics` directly;
`regime_analytics`/`suggestion_decisions` are the same History-tab's other logic
(confirmed: neither has any importer outside history-tab-adjacent code).

### `services/config/`
Move in: `config_bounds.py`, `config_overrides.py`, `config_performance.py`,
`config_store.py`. `services/config/routes.py` already imports
`config_performance`/`config_store`; `config_bounds`/`config_overrides` are the
same Config-tab's validation logic. Note: `services/config/` already has its own
`config_paths.py` — confirm no name collision before the move (there is none;
different names).

### `services/position/`
Move in: `account_positions.py`. `services/position/routes.py` already imports
it (`_slim_order`).

### `services/reset/` (new package)
Move in:
- `POST /api/reset` itself, plus its request model `ResetBody` and helper
  `_reset_domain_counts` — currently defined directly in `main.py` (lines
  1404/1451/1516 at investigation time), never extracted into any package.
  Becomes `services/reset/routes.py`, wired into `main.py` via
  `app.include_router(...)`, the same pattern every other extracted package
  already follows.
- `reset_log.py` — the reset route's own dedicated audit-trail module.
- `trade_archive.py` — confirmed via `grep -rl "trade_archive\." services/*.py
  main.py`: used *only* by the reset route (the pre-reset archive-before-destroy
  step) and itself. No other importer anywhere in the codebase.

`POST /api/reset` itself calls into many *other* domains' own `clear_all()`/
`clear_range()` methods (`market_history.clear_all()`, `candidate_log.
clear_range()`, `market_catalog.clear_all()`, `series_evaluator.clear_all()`,
etc.) — those stay exactly where they are. `services/reset/` owns the
orchestrating route and its own two dedicated modules, not every domain it
happens to call into.

### Deliberately unchanged (stay flat)
`ws_manager.py`, `title_cache.py`, `series_cache.py`, `state_view.py` — see
Decisions record above.

## 2. Task shape (per group)

Mirrors this repo's own precedent for a mechanical move
(`docs/superpowers/plans/2026-08-25-frontend-modularization.md`'s `T1c`) —
deliberately not full TDD, since there is no new behavior to test-first, only
existing behavior that must not change:

1. `git mv` the file(s) into the target package directory.
2. Update every importer repo-wide (`grep -rl` the old module path across
   `services/`, `main.py`, `tests/`; fix each import statement).
3. Run that module's existing `tests/test_<module>.py` file(s), plus
   `ddev exec -s fastapi python -c "import main"` as a fast import-wiring
   sanity check.
4. Run `ddev exec -s fastapi python -m tools.quality_audit` — expect exit 0, no
   new findings. (Confirmed via `tools/quality_audit/baseline.json`: none of
   these 8 files currently have any baselined finding, so no baseline edit is
   expected — if one does appear, investigate per CLAUDE.md's baseline-ratchet
   semantics rather than blindly accepting it.)
5. Commit.

`services/reset/`'s task additionally needs: moving `ResetBody`/
`_reset_domain_counts`/the route body out of `main.py`, adding
`app.include_router(reset_routes.router)` in `main.py`, and removing the old
in-`main.py` versions in the same commit (no dual-path period).

## 3. Sequencing

One branch (`refactor/backend-services-modularization` or similar, per
`.claude/rules/branching-and-ci.md`), one task per commit: history → config →
position → reset. Order doesn't matter much between them (no dependency
between the four groups), kept together as one branch since they're one
coherent "modularize the dashboard-facing services" effort per the numbered
multi-task convention this repo already uses elsewhere (Quality Control Plane,
Kalshi Integration Phase A/C). Full-suite verification via Woodpecker on push,
per this repo's CI-offload policy — not a manual full local run after every task.

## 4. Guards and CI ownership

No new deterministic guard needed — `tools/quality_audit` already exists and
already runs in CI; Step 4 above is verifying against an existing guard, not
introducing one. `python -c "import main"` is a cheap, already-standard sanity
check (not a new CI job) that catches an import-path mistake before it reaches
the test suite.

## 5. Risks and rollback

Lowest-risk category of change this repo makes: no logic changes, only import
paths. Primary failure mode is a missed importer (an import statement
referencing the old flat path that Step 2's `grep -rl` pass didn't catch) —
caught by Step 3's `import main` sanity check and the full test suite either
way. Each of the four tasks is its own commit, so a bad one reverts cleanly
without touching the others. No data migration, no schema change, no `data/*.db`
involvement at all — none of these 8 files own persisted state directly (they
read/write through the domain modules they call, which are unaffected).

## 6. Success criteria

- All 8 files physically live in their target package; nothing still imports
  the old flat path anywhere in `services/`, `main.py`, or `tests/`.
- `POST /api/reset` is wired via `app.include_router`, not defined inline in
  `main.py`.
- Full test suite green (Woodpecker) after each of the 4 commits.
- `tools/quality_audit` exit 0 after each commit, no unexplained new findings.
- Behavior is byte-identical — no dashboard-visible change, no API response
  shape change, no config semantics change.
