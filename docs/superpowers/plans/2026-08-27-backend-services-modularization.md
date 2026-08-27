# Backend Services Modularization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Physically relocate 8 flat `services/*.py` files into the existing packages whose `routes.py` already depends on them (`history`, `config`, `position`), and extract `POST /api/reset`/`GET /api/reset/preview`/`GET /api/reset/history` out of `main.py` into a new `services/reset/` package — pure relocation, zero behavior change.

**Architecture:** Four independent, sequential tasks (no dependency between them). Each is a mechanical `git mv` + repo-wide import-path fix + existing-test verification, mirroring this repo's own `frontend-modularization` `T1c` precedent (deliberately not TDD — there is no new behavior to test-first, only existing behavior that must not change).

**Tech Stack:** Python 3.13, FastAPI (`APIRouter`/`app.include_router`), pytest. No new dependency.

**Spec:** `docs/superpowers/specs/2026-08-27-backend-services-modularization-design.md`

## Global Constraints

- No logic changes bundled into any move — every moved function's body, signature, and behavior stays byte-identical.
- No test-file relocation — `tests/` is flat repo-wide; moved modules' existing `tests/test_<module>.py` files stay exactly where they are.
- `services/reset/` additionally needs `app.include_router(...)` wiring in `main.py`, with the old inline route/model/helper removed in the *same* commit (zero dual-path period).
- Full-suite verification happens via Woodpecker on push (this repo's CI-offload policy) — each task's own local verification is targeted: the moved module's existing tests, a `python -c "import main"` sanity check, and `python -m tools.quality_audit`.
- One branch, one task per commit, in this order: history → config → position → reset.

---

### Task 1: `services/history/` — move `trade_analytics.py`, `regime_analytics.py`, `suggestion_decisions.py`

**Files:**
- Move: `services/trade_analytics.py` → `services/history/trade_analytics.py`
- Move: `services/regime_analytics.py` → `services/history/regime_analytics.py`
- Move: `services/suggestion_decisions.py` → `services/history/suggestion_decisions.py`
- Modify (real importers, confirmed via repo-wide grep — update `from services import trade_analytics`/`from services.trade_analytics import ...`-style statements to `from services.history import trade_analytics`/`from services.history.trade_analytics import ...`, same pattern for the other two modules):
  - `services/app_state.py`
  - `services/state_view.py`
  - `services/advisory/advisory_engine.py`
  - `services/advisory/routes.py`
  - `services/history/routes.py` (becomes an intra-package import, e.g. `from services.history import trade_analytics` → `from . import trade_analytics`, or keep the absolute form — match whatever style `services/market_watch/__init__.py`'s own intra-package imports already use)
  - `services/research/research.py`
  - `services/whale_calibration/confidence_calibration.py`
  - `services/series_watcher.py`
  - `main.py`
  - `tests/test_paper_broker.py`
  - `tests/test_performance_regressions.py`
  - `tests/test_trade_analytics.py`
  - `tests/test_advisory_engine.py`
  - `tests/test_regime_analytics.py`
  - `tests/test_suggestion_decisions.py`
- Test: `tests/test_trade_analytics.py`, `tests/test_regime_analytics.py`, `tests/test_suggestion_decisions.py` (existing, unmodified except import lines)

**Interfaces:**
- Consumes: nothing new.
- Produces: `services.history.trade_analytics`, `services.history.regime_analytics`, `services.history.suggestion_decisions` as the new import paths every downstream file in this task's Files list must use. No function signature changes.

- [ ] **Step 1: Confirm the current importer list is still accurate**

Run: `grep -rln "services\.trade_analytics\b\|services\.regime_analytics\b\|services\.suggestion_decisions\b" services/ main.py tests/ tools/ 2>/dev/null`
Expected: the same file list as above (plus possibly `services/history/trade_analytics.py` etc. themselves via self-import, which is fine). If a new importer appears that isn't in the list above, add it to this task's scope before proceeding — do not silently skip it.

- [ ] **Step 2: Move the three files**

```bash
git mv services/trade_analytics.py services/history/trade_analytics.py
git mv services/regime_analytics.py services/history/regime_analytics.py
git mv services/suggestion_decisions.py services/history/suggestion_decisions.py
```

- [ ] **Step 3: Fix every importer**

For each file in the Files list above, change:
- `from services import trade_analytics` → `from services.history import trade_analytics`
- `from services.trade_analytics import X` → `from services.history.trade_analytics import X`
- (same substitution pattern for `regime_analytics` and `suggestion_decisions`)

Within `services/history/routes.py` itself, since it now lives in the same package, either keep the absolute `from services.history import trade_analytics` form or switch to a relative `from . import trade_analytics` — check `services/market_watch/__init__.py`/`services/market_watch/catalog_scan.py` first to see which style this repo's other multi-module packages actually use, and match it rather than introducing a third style.

- [ ] **Step 4: Run the moved modules' own tests**

Run: `ddev exec -s fastapi python -m pytest tests/test_trade_analytics.py tests/test_regime_analytics.py tests/test_suggestion_decisions.py tests/test_advisory_engine.py -v`
Expected: PASS (same pass count as before the move — this proves the import-path fix is complete and correct, not new behavior).

- [ ] **Step 5: Import-wiring sanity check**

Run: `ddev exec -s fastapi python -c "import main"`
Expected: no `ImportError`/`ModuleNotFoundError`. This catches any importer Step 1's grep missed (a broken import surfaces immediately here, since `main.py` transitively imports the whole app).

- [ ] **Step 6: Static-analysis check**

Run: `ddev exec -s fastapi python -m tools.quality_audit`
Expected: exit 0. (Confirmed in the design spec: none of these 3 files have an existing `tools/quality_audit/baseline.json` entry, so no baseline edit is expected — if a new finding does appear, investigate per CLAUDE.md's baseline-ratchet semantics rather than blindly accepting it.)

- [ ] **Step 7: Commit**

```bash
git add services/history/trade_analytics.py services/history/regime_analytics.py \
  services/history/suggestion_decisions.py services/app_state.py services/state_view.py \
  services/advisory/advisory_engine.py services/advisory/routes.py services/history/routes.py \
  services/research/research.py services/whale_calibration/confidence_calibration.py \
  services/series_watcher.py main.py tests/test_paper_broker.py \
  tests/test_performance_regressions.py tests/test_trade_analytics.py \
  tests/test_advisory_engine.py tests/test_regime_analytics.py tests/test_suggestion_decisions.py
git commit -m "refactor: move trade_analytics/regime_analytics/suggestion_decisions into services/history/"
```

---

### Task 2: `services/config/` — move `config_bounds.py`, `config_overrides.py`, `config_performance.py`, `config_store.py`

**Files:**
- Move: `services/config_bounds.py` → `services/config/config_bounds.py`
- Move: `services/config_overrides.py` → `services/config/config_overrides.py`
- Move: `services/config_performance.py` → `services/config/config_performance.py`
- Move: `services/config_store.py` → `services/config/config_store.py`
- Modify (real importers, confirmed via repo-wide grep):
  - `config_bounds`: `services/advisory/advisory_engine.py`, `services/settlement_edge_entry.py`, `services/diagnostics/diagnostics.py`, `services/whalewatchers/kalshi_trade_tape.py`, `services/strategy_engine.py`, `services/config/config_overrides.py` (intra-package after Task 2's own move — see below), `tests/test_strategy_engine.py`, `tests/test_config_bounds.py`
  - `config_overrides`: `services/config/config_bounds.py` (this one is backwards from what the file name implies — confirm the actual import direction with `grep -n "config_overrides\|config_bounds" services/config_bounds.py services/config_overrides.py` before writing the fix, since `config_bounds.py` importing `config_overrides.py` and not the reverse would change which file's import statement needs editing), `services/exits/exit_engine.py`, `services/strategy_engine.py`, `services/diagnostics/diagnostics.py`, `services/advisory/advisory_engine.py`, `tests/test_config_overrides.py`
  - `config_performance`: `services/advisory/routes.py`, `services/config/routes.py`, `services/research/research.py`, `services/diagnostics/diagnostics.py`, `services/whale_stream/whale_stream_handlers.py`, `services/app_state.py`, `services/whale_calibration/routes.py`, `main.py`, `tests/test_diagnostics.py`, `tests/test_config_performance.py`, `tests/test_e2e_terminal_static_and_api.py`, `tests/test_active_terminal_refresh.py`, `tests/test_main_tick_executor_wiring.py`, `tests/test_trading_gate.py`
  - `config_store`: `services/app_state.py`, `services/alerting/alerting.py`, `services/kalshi/websocket.py`, `services/backup/backup.py`, `services/exits/routes.py`, `services/config/routes.py`, `services/research/routes.py`, `services/observability/routes.py`, `services/advisory/routes.py`, `services/backup/routes.py`, `services/quality/routes.py`, `services/whale_calibration/routes.py`, `services/whale_stream/index_stream_handlers.py`, `services/whale_stream/whale_stream_handlers.py`, `services/backtest/routes.py`, `services/analytics/routes.py`, `services/diagnostics/routes.py`, `services/market_catalog/routes.py`, `main.py`, `tools/kalshi_rate_limit_probe.py`, `tests/test_alerting.py`, `tests/test_research.py`, `tests/test_config_store.py`, `tests/test_trading_gate.py`
- Test: `tests/test_config_bounds.py`, `tests/test_config_overrides.py`, `tests/test_config_performance.py`, `tests/test_config_store.py`, `tests/test_strategy_engine.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `services.config.config_bounds`, `services.config.config_overrides`, `services.config.config_performance`, `services.config.config_store` (and the module-level `config_store` singleton instance it exports) as the new import paths. No signature or behavior changes. Note `services/config/` already has its own `config_paths.py` — confirm no name collision before moving (there is none; the four incoming names are distinct).

- [ ] **Step 1: Confirm the import direction between config_bounds and config_overrides**

Run: `grep -n "^from\|^import" services/config_bounds.py services/config_overrides.py`
This determines which of the two files' import statement needs fixing for the other — write Step 3 against the real result, not the assumption in this task's Files list above.

- [ ] **Step 2: Confirm the current importer list is still accurate**

Run: `grep -rln "services\.config_bounds\b\|services\.config_overrides\b\|services\.config_performance\b\|services\.config_store\b" services/ main.py tests/ tools/ 2>/dev/null`
Expected: the same file list as above (adjusted per Step 1's finding). Add any new importer to scope before proceeding.

- [ ] **Step 3: Move the four files**

```bash
git mv services/config_bounds.py services/config/config_bounds.py
git mv services/config_overrides.py services/config/config_overrides.py
git mv services/config_performance.py services/config/config_performance.py
git mv services/config_store.py services/config/config_store.py
```

- [ ] **Step 4: Fix every importer**

Same substitution pattern as Task 1 Step 3: `from services import config_store` → `from services.config import config_store`, `from services.config_store import config_store` → `from services.config.config_store import config_store`, etc., for all four modules across every file in this task's Files list (corrected per Step 1's real import-direction finding). Within `services/config/routes.py` and `services/config/config_paths.py`, prefer the same intra-package import style Task 1 Step 3 established as this repo's convention.

- [ ] **Step 5: Run the moved modules' own tests**

Run: `ddev exec -s fastapi python -m pytest tests/test_config_bounds.py tests/test_config_overrides.py tests/test_config_performance.py tests/test_config_store.py tests/test_strategy_engine.py -v`
Expected: PASS.

- [ ] **Step 6: Import-wiring sanity check**

Run: `ddev exec -s fastapi python -c "import main"`
Expected: no `ImportError`/`ModuleNotFoundError`. `config_store` is the single most widely-imported module in this task (20+ importers) — this check matters more here than in any other task.

- [ ] **Step 7: Static-analysis check**

Run: `ddev exec -s fastapi python -m tools.quality_audit`
Expected: exit 0.

- [ ] **Step 8: Commit**

```bash
git add services/config/ services/advisory/advisory_engine.py services/settlement_edge_entry.py \
  services/diagnostics/ services/whalewatchers/kalshi_trade_tape.py services/strategy_engine.py \
  services/exits/exit_engine.py services/exits/routes.py services/advisory/routes.py \
  services/research/research.py services/research/routes.py services/whale_stream/ \
  services/app_state.py services/whale_calibration/routes.py services/alerting/alerting.py \
  services/kalshi/websocket.py services/backup/ services/observability/routes.py \
  services/quality/routes.py services/backtest/routes.py services/analytics/routes.py \
  services/market_catalog/routes.py main.py tools/kalshi_rate_limit_probe.py \
  tests/test_strategy_engine.py tests/test_config_bounds.py tests/test_config_overrides.py \
  tests/test_diagnostics.py tests/test_config_performance.py tests/test_e2e_terminal_static_and_api.py \
  tests/test_active_terminal_refresh.py tests/test_main_tick_executor_wiring.py \
  tests/test_trading_gate.py tests/test_alerting.py tests/test_research.py tests/test_config_store.py
git commit -m "refactor: move config_bounds/config_overrides/config_performance/config_store into services/config/"
```

---

### Task 3: `services/position/` — move `account_positions.py`

**Files:**
- Move: `services/account_positions.py` → `services/position/account_positions.py`
- Modify (real importers, confirmed via repo-wide grep):
  - `services/state_view.py`
  - `services/whale_stream/whale_stream_handlers.py`
  - `services/position/routes.py` (becomes intra-package)
  - `main.py`
  - `tests/test_kalshi_contracts.py`
  - `tests/test_trading_gate.py`
- Test: none dedicated (confirmed in the design spec: `account_positions.py` is only incidentally exercised via `tests/test_trading_gate.py`, no `tests/test_account_positions.py` exists) — this task's own verification leans more heavily on Steps 3-4 below than the other three tasks do.

**Interfaces:**
- Consumes: nothing new.
- Produces: `services.position.account_positions` (and its `_slim_order` helper, which `services/position/routes.py` already imports by name) as the new import path. No signature or behavior changes.

- [ ] **Step 1: Confirm the current importer list is still accurate**

Run: `grep -rln "services\.account_positions\b\|from services import.*account_positions\b" services/ main.py tests/ 2>/dev/null`
Expected: the same file list as above. Add any new importer to scope before proceeding.

- [ ] **Step 2: Move the file**

```bash
git mv services/account_positions.py services/position/account_positions.py
```

- [ ] **Step 3: Fix every importer**

Same substitution pattern as prior tasks. `services/position/routes.py`'s existing `from services.account_positions import _slim_order` becomes an intra-package import, matching the style established in Task 1 Step 3.

- [ ] **Step 4: Run the trading-gate test suite (this module's real coverage)**

Run: `ddev exec -s fastapi python -m pytest tests/test_trading_gate.py tests/test_kalshi_contracts.py -v`
Expected: PASS (same pass count as before the move).

- [ ] **Step 5: Import-wiring sanity check**

Run: `ddev exec -s fastapi python -c "import main"`
Expected: no `ImportError`/`ModuleNotFoundError`.

- [ ] **Step 6: Static-analysis check**

Run: `ddev exec -s fastapi python -m tools.quality_audit`
Expected: exit 0.

- [ ] **Step 7: Commit**

```bash
git add services/position/account_positions.py services/state_view.py \
  services/whale_stream/whale_stream_handlers.py services/position/routes.py main.py \
  tests/test_kalshi_contracts.py tests/test_trading_gate.py
git commit -m "refactor: move account_positions into services/position/"
```

---

### Task 4: `services/reset/` (new package) — extract the Danger Zone routes from `main.py`

**Files:**
- Move: `services/reset_log.py` → `services/reset/reset_log.py`
- Move: `services/trade_archive.py` → `services/reset/trade_archive.py`
- Create: `services/reset/__init__.py` (empty — matches every other package's convention, e.g. `services/position/__init__.py`)
- Create: `services/reset/routes.py` — the extracted `ResetBody`, `_reset_domain_counts`, `GET /api/reset/preview`, `GET /api/reset/history`, `POST /api/reset`, currently `main.py:1404-1631`
- Modify: `main.py` — remove lines 1404-1631 (the five items above), add `app.include_router(reset_routes.router)` and the corresponding import, in the same commit (no dual-path period)
- Modify (real importers of `reset_log`/`trade_archive`, confirmed via repo-wide grep):
  - `services/app_state.py`
  - `services/diagnostics/routes.py` (imports `trade_archive` only)
  - `tests/test_trade_archive.py`
  - `tests/test_trading_gate.py`
- Test: `tests/test_trade_archive.py`, plus a new `tests/test_reset_routes.py` covering the route-wiring boundary (Step 4 below) — the route bodies themselves are unchanged, so this is a wiring test, not new-behavior TDD.

**Interfaces:**
- Consumes: `services.app_state`'s `broker`, `shadow`, `risk`, `state`, `bump_generation` (same shared singletons every other extracted `routes.py` already imports from there — see `services/position/routes.py`'s own `from services.app_state import account, broker, bump_generation, risk, shadow` for the established pattern); `services.config.config_store` (Task 2); `services.candidate_log`, `services.market_analyst_agent`, `services.market_catalog`, `services.market_history`, `services.series_evaluator`, `services.trade_category`, `services.signal_log` — all existing, unmoved modules whose `clear_all()`/`clear_range()`/`count_range()` the reset route calls into and does **not** own (per the design spec's own scope note: reset orchestrates these domains, it doesn't absorb them).
- Produces: `services.reset.routes.router` (an `APIRouter`, same shape as every other package's `routes.py`), mounted in `main.py` via `app.include_router(reset_routes.router)`. `services.reset.reset_log`, `services.reset.trade_archive` as the new import paths for those two modules.

- [ ] **Step 1: Confirm the current importer list is still accurate**

Run: `grep -rln "services\.reset_log\b\|services\.trade_archive\b" services/ main.py tests/ 2>/dev/null`
Expected: the same file list as above. Add any new importer to scope before proceeding.

- [ ] **Step 2: Move `reset_log.py` and `trade_archive.py`**

```bash
mkdir -p services/reset
touch services/reset/__init__.py
git mv services/reset_log.py services/reset/reset_log.py
git mv services/trade_archive.py services/reset/trade_archive.py
git add services/reset/__init__.py
```

- [ ] **Step 3: Fix `reset_log`/`trade_archive` importers**

Same substitution pattern as prior tasks, for `services/app_state.py`, `services/diagnostics/routes.py`, `tests/test_trade_archive.py`, `tests/test_trading_gate.py`.

- [ ] **Step 4: Create `services/reset/routes.py`**

Read `main.py:1404-1631` in full first (the exact current `ResetBody`, `_reset_domain_counts`, `reset_preview`, `get_reset_history`, `reset_broker` — reproduce them verbatim, including every comment, since this is a pure relocation). The new file's imports:

```python
"""Danger Zone routes: preview, history, and the actual multi-domain paper/
signal_log/market_analyst/market_catalog/market_history/series_evaluator/
candidate_log/calibration_history/trade_category reset - moved out of main.py
2026-08-27 (backend services modularization). See this package's own
reset_log.py (the audit trail every reset writes to) and trade_archive.py
(the pre-reset snapshot taken before a paper reset destroys anything)."""
import time

from fastapi import APIRouter
from pydantic import BaseModel

from services import (
    candidate_log, market_analyst_agent, market_history, series_evaluator, signal_log, trade_category,
)
from services.app_state import bump_generation, broker, risk, shadow, state
from services.config.config_store import config_store
from services.market_catalog import market_catalog
from services.reset import reset_log, trade_archive

router = APIRouter()
```

(Every import above confirmed against `main.py`'s own current top-of-file imports, not guessed - in particular `market_catalog` is `from services.market_catalog import market_catalog` (a submodule of the existing `services/market_catalog/` package), not a flat top-level module like the others; easy to get wrong by pattern-matching the rest of this list.)

Then paste `ResetBody`, `_reset_domain_counts`, `reset_preview` (renamed from its `@app.get` decorator to `@router.get`), `get_reset_history` (`@router.get`), `reset_broker` (`@router.post`) verbatim below the imports, changing only every `@app.` decorator to `@router.`.

- [ ] **Step 5: Remove the extracted code from `main.py`, wire the router**

Delete `main.py:1404-1631` in full. Add near this app's other `app.include_router(...)` calls:

```python
from services.reset import routes as reset_routes
# ... alongside the other app.include_router(...) calls:
app.include_router(reset_routes.router)
```

- [ ] **Step 6: Write a route-wiring test**

```python
# tests/test_reset_routes.py
"""Confirms POST /api/reset, GET /api/reset/preview, and GET /api/reset/history
survived the main.py -> services/reset/routes.py extraction with identical
behavior - a wiring test, not new-behavior coverage (the route bodies
themselves are unchanged; tests/test_trading_gate.py already exercises
reset_broker's real domain-clearing behavior)."""
from fastapi.testclient import TestClient

import main


def test_reset_preview_route_is_wired():
    client = TestClient(main.app)
    resp = client.get("/api/reset/preview")
    assert resp.status_code == 200
    body = resp.json()
    assert "counts" in body and "scope" in body


def test_reset_history_route_is_wired():
    client = TestClient(main.app)
    resp = client.get("/api/reset/history")
    assert resp.status_code == 200
    assert "events" in resp.json()
```

- [ ] **Step 7: Run it**

Run: `ddev exec -s fastapi python -m pytest tests/test_reset_routes.py tests/test_trade_archive.py tests/test_trading_gate.py -v`
Expected: PASS.

- [ ] **Step 8: Import-wiring sanity check**

Run: `ddev exec -s fastapi python -c "import main"`
Expected: no `ImportError`/`ModuleNotFoundError`.

- [ ] **Step 9: Confirm the route actually moved, not duplicated**

Run: `ddev exec -s fastapi python -c "import main; print([r.path for r in main.app.routes if 'reset' in r.path])"`
Expected: `/api/reset`, `/api/reset/preview`, `/api/reset/history` each appear exactly once (not twice - a stale leftover `@app.post("/api/reset")` in `main.py` alongside the new router would register the path twice, which this check catches and Step 5's full-block deletion should have already prevented).

- [ ] **Step 10: Static-analysis check**

Run: `ddev exec -s fastapi python -m tools.quality_audit`
Expected: exit 0.

- [ ] **Step 11: Commit**

```bash
git add services/reset/ services/app_state.py services/diagnostics/routes.py main.py \
  tests/test_trade_archive.py tests/test_trading_gate.py tests/test_reset_routes.py
git commit -m "refactor: extract POST /api/reset + preview/history into services/reset/"
```

---

## Self-review notes (writing-plans skill, completed inline)

- **Spec coverage:** all four groups from the design spec's Architecture section (§1) are covered — history, config, position, reset. The spec's Task 17b-style "cross-post findings" pattern doesn't apply here (that was P3.5's own convention for a *measurement* task; this plan is pure relocation with no findings to record).
- **Scope correction found while grounding this plan, not in the spec:** the design spec named only `POST /api/reset`; grounding this plan against the real `main.py` found two more routes in the same block (`GET /api/reset/preview`, `GET /api/reset/history`) that the spec's author missed. Both are included in Task 4 above — same package, same extraction, no separate task needed, since they share `ResetBody`/`_reset_domain_counts` with the POST route and were always going to move together.
- **Placeholder scan:** Task 4 Step 4's import block intentionally flags one name (`trade_category`'s real import alias) as unverified rather than guessing — Step 1 of the same task requires confirming it before Step 4 is written for real. This is a deliberate "verify against the real file first" instruction, not a placeholder left unresolved.
- **Type/interface consistency:** every task's "Produces" line names the exact new import path; no later task depends on any earlier task's output (all four are independent), so there's no cross-task signature to keep consistent beyond the shared `services.app_state`/`services.config.config_store` imports Task 4 depends on Task 2 having already moved (see Global Constraints' stated task order: history → config → position → reset — Task 4 is deliberately last because it's the only one that imports something Task 2 relocates).
