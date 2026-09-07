# Event-Loop-Blocking Fix 2 (Diagnostics Widening) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert every DB-touching function `diagnostics.run_offline()` and `GET /api/diagnostics/series/{series}` reach, in `services/diagnostics/diagnostics.py` and `services/series_watcher.py`, from raw synchronous `sqlite3` to `aiosqlite`, so the event loop is never blocked by their disk I/O — then delete `services/diagnostics/_diagnostics_pool.py`, whose whole reason to exist (isolating blocking work on a thread pool) disappears once the work is genuinely non-blocking.

**Architecture:** One shared module, `services/diagnostics/_aio_db.py`, holds a small cache of persistent `aiosqlite.Connection`s (one per `(event_loop, db_path)` pair — loop-scoped, not just path-scoped, because one caller (`services/research/research.py`) runs this code from a throwaway `asyncio.run()` loop on a worker thread, never the main app loop, and then calls `close_for_current_loop()` to clean up after itself. **Corrected 2026-09-01 (PR adversarial review finding I3):** the reason is connection *lifetime/ownership*, not loop affinity — keyed by path alone, research.py's cleanup would close connections the main app's long-lived loop is still using, and entries belonging to an already-dead throwaway loop could never be distinguished from the main loop's in order to be cleaned up at all. An `aiosqlite.Connection` in the pinned 0.22.1 is **not** bound to the loop that created it; this plan originally claimed it was.) Every DB-touching function in the two target files becomes `async def` and calls `await _aio_db.connection_for(SOME_MODULE.DB_PATH)` instead of `sqlite3.connect(...)`. Two call sites reach into an out-of-scope module's own DB helper (`trade_category.categories_for_tickers`, `signal_log.resolved_signals_with_factors`) — those stay synchronous and get wrapped in `asyncio.to_thread(...)` at the call site rather than converted themselves, so this plan's file footprint stays exactly the two files the design spec named plus the three call sites that reach `run_offline`/`funnel`/`reconcile`.

**Tech Stack:** Python 3.13, FastAPI, `aiosqlite` (new dependency, pinned `0.22.1` — the current latest stable, confirmed via `pip index versions aiosqlite` inside the `fastapi` container, 2026-09-01; not previously a dependency of this repo, confirmed via `requirements.txt` and a failed `import aiosqlite` inside the container).

**Spec:** `docs/archive/lane-1-kalshi-ingestion/specs/2026-09-01-event-loop-blocking-elimination-design.md` (Fix 2 section, "Diagnostics widening"). This plan also corrects/extends that spec in four places the spec itself didn't name — each verified against current source, not assumed:
1. `services/diagnostics/routes.py` (a *second* route file, `GET /api/diagnostics` and `GET /api/diagnostics/series/{series}`) calls `diagnostics.run_offline()`/`series_watcher.funnel()`/`series_watcher.reconcile()` directly on the event loop — the spec's file list never mentions this file. Both routes are already `async def`, so this is an `await` addition, not a new file to convert.
2. `series_watcher.book_context_at_entry()` and `series_watcher.capture_stats()` (both DB-touching, both reached only via the `GET /api/diagnostics/series/{series}` route above, not via `run_offline()`) are in the same file, same DB, same mechanical shape as the five functions the spec does name — left out of the spec's list, included here for consistency; leaving them out would leave that one route still blocking the event loop right next to the ones this plan fixes.
3. `services/research/research.py`'s `build_report()` also calls `diagnostics.run_offline()` — but synchronously, from inside a plain `def` that already runs via `asyncio.to_thread(run_and_store, cfg)` (its own worker thread, no running event loop of its own). This is the reason the connection cache must be loop-scoped, not just path-scoped — see Task 1.
4. `config_epochs()` (`diagnostics.py:389`) — the spec's own text calls this "not called by `run_offline()` at all, stays out of scope," which reads as "stays synchronous." That's wrong on inspection: `config_epochs()` does its own raw `sqlite3.connect(config_performance.DB_PATH)` call, and `performance_by_epoch()` (which the spec DOES name as in-scope) calls it directly — once `performance_by_epoch` is async, `config_epochs` must be too, or the `await config_epochs(...)` call inside it has nothing to await. Converted in Task 3.

## Global Constraints

- No change to any arithmetic, threshold, or numeric formula anywhere in this plan — every task changes *how* a query runs (sync → async), never *what* it computes or returns. The `dimensional-analysis` HARD RULE (CLAUDE.md) does not add new obligations here for that reason, and no task below touches a money/probability/unit value.
- No change to `_FLUSH_BATCH` thresholds, `flush()`, `record_trade`/`record_book`, `prune()`, or any other *write*-path function in `series_watcher.py` — this plan is read-only functions only, exactly as the spec scopes it.
- No full-application aiosqlite migration. `trade_category.py` and `signal_log.py` are explicitly NOT converted by this plan (see Architecture) — calls into them from newly-async code go through `asyncio.to_thread`, not a signature change, so their other existing callers are untouched: `trade_category.categories_for_tickers()` has callers at `services/history/regime_analytics.py:130` and `services/advisory/advisory_engine.py:788`; `signal_log.resolved_signals_with_factors()` has callers at `services/whale_calibration/routes.py:112` AND `:152` (two call sites, not one), `main.py:490`, and `services/backtest/routes.py:21` (full list confirmed via repo-wide grep at adversarial-review time, 2026-09-01 — not assumed from a partial scan).
- `signal_log.resolved_signals_with_factors()`'s own unscoped-fetch cost (already flagged in `docs/open-decisions.md`'s Task 9 entry) and `candidate_log.population_gate_summary()`'s unbounded 20.9M-row scan (issue #410) are NOT fixed by this plan — wrapping a slow synchronous call in `asyncio.to_thread` stops it blocking the event loop, it does not make the call itself faster. Both stay open, tracked separately (issue #410; the "time-windowed default" fix already agreed for it is a different plan).
- Every existing `except sqlite3.Error:` block stays exactly as written — `aiosqlite` re-exports the same `sqlite3` exception classes (confirmed via Context7 against the library's own docs, not recalled from memory), so no exception-handling code needs to change type.
- Test convention: this repo has no `pytest-asyncio`. Every test for an `async def` function wraps the call in `asyncio.run(...)`, matching `tests/test_tick_executor.py`'s existing pattern.

## File Structure

- **Create:** `services/diagnostics/_aio_db.py` — the shared, loop-scoped `aiosqlite.Connection` cache both target files import.
- **Modify:** `services/diagnostics/diagnostics.py` — 7 `Check` functions + 2 private helpers (`_close_ts_for_tickers`, `_fetch_path_changes`) become `async def`; `run_offline()` becomes `async def` and awaits each check in sequence (same order as today — no new concurrency introduced).
- **Modify:** `services/series_watcher.py` — `_signals_for_series`, `_trades_for_series`, `funnel`, `reconcile`, `check_series_funnel`, `capture_stats`, `book_context_at_entry` become `async def`. Nothing else in this file changes.
- **Modify:** `services/quality/routes.py` — drop the `_diagnostics_pool` import and wrapper; `await diagnostics.run_offline(cfg)` directly.
- **Modify:** `services/diagnostics/routes.py` — add `await` at the 3 call sites (`run_offline`, `funnel`, `reconcile`) plus the 2 newly-async `capture_stats`/`book_context_at_entry` calls in the same route.
- **Modify:** `services/research/research.py` — `build_report()`'s one call to `diagnostics.run_offline()` goes through a small `asyncio.run(...)`-wrapped helper that also closes out its own loop-scoped connections (see Task 1's rationale).
- **Delete:** `services/diagnostics/_diagnostics_pool.py`.
- **Modify:** `requirements.txt` — add `aiosqlite==0.22.1`.
- **Modify/Create tests:** `tests/test_diagnostics.py`, `tests/test_series_watcher.py`, `tests/test_quality_routes.py`, `tests/test_diagnostics_routes.py` (new test functions — confirmed via adversarial review, 2026-09-01, that neither converted route has any existing coverage there today), `tests/test_research.py` (confirmed to exist; its `_patch_every_analyzer()` helper needs an explicit fix — see Task 6 Step 7), `tests/test_aio_db.py` (new).

---

### Task 1: `aiosqlite` dependency + shared loop-scoped connection cache

**Files:**
- Modify: `requirements.txt`
- Create: `services/diagnostics/_aio_db.py`
- Test: `tests/test_aio_db.py`

**Interfaces:**
- Produces: `async def connection_for(db_path: Path, schema_init: Callable[[aiosqlite.Connection], Awaitable[None]] | None = None) -> aiosqlite.Connection` — every later task's DB access goes through this. `schema_init`, when given, runs exactly once — only on the connection's first-ever open for that `(loop, db_path)` key, never on a cache hit — mirroring `services/whalewatchers/_scoring_pool.py`'s existing `cached_read_connection(db_path, schema_init)` precedent for the identical need (a diagnostics.py caller passes nothing, since its 4 DB files' schemas are already guaranteed to exist by their own write-path modules; series_watcher.py's 3 converted read functions pass one, since `series_watcher._connect()` currently self-heals a missing schema on every call and Task 5 must not silently drop that guarantee).
- Produces: `async def close_for_current_loop() -> None` — closes and evicts only the cache entries opened under the currently-running event loop.
- Produces: `async def reset() -> None` — test-only, closes and evicts every cached connection regardless of loop.

- [x] **Step 1: Add the dependency**

Append to `requirements.txt` (after the `websockets` line, before the `anthropic` comment block, matching this file's existing per-dependency rationale-comment convention):

```
# aiosqlite - services/diagnostics/diagnostics.py + services/series_watcher.py's
# read-only functions run through this now (event-loop-blocking-elimination
# Fix 2, 2026-09-01) instead of raw sqlite3 + a hand-rolled ThreadPoolExecutor,
# so the asyncio event loop is never blocked by their disk I/O. 0.22.1 is the
# current latest stable (confirmed via `pip index versions aiosqlite` inside
# the fastapi container, not assumed) - not previously a dependency of this
# repo (confirmed: absent from this file and from a direct `import aiosqlite`
# probe inside the running container, which raised ModuleNotFoundError).
aiosqlite==0.22.1
```

- [x] **Step 2: Write the failing test for the connection cache**

Create `tests/test_aio_db.py`:

```python
"""services/diagnostics/_aio_db.py - the shared, loop-scoped aiosqlite
connection cache services/diagnostics/diagnostics.py and
services/series_watcher.py's read-only functions share.
"""
import asyncio

from services.diagnostics import _aio_db


def test_connection_for_returns_a_usable_connection(tmp_path):
    db_path = tmp_path / "t.db"

    async def _run():
        conn = await _aio_db.connection_for(db_path)
        await conn.execute("CREATE TABLE t (id INTEGER)")
        await conn.commit()
        rows = await conn.execute_fetchall("SELECT COUNT(*) FROM t")
        return rows[0][0]

    assert asyncio.run(_run()) == 0


def test_connection_for_is_cached_within_the_same_loop(tmp_path):
    db_path = tmp_path / "t.db"

    async def _run():
        first = await _aio_db.connection_for(db_path)
        second = await _aio_db.connection_for(db_path)
        return first is second

    assert asyncio.run(_run()) is True


def test_connection_for_does_not_reuse_a_connection_across_different_loops(tmp_path):
    db_path = tmp_path / "t.db"

    async def _get_id():
        conn = await _aio_db.connection_for(db_path)
        return id(conn)

    first_id = asyncio.run(_get_id())
    second_id = asyncio.run(_get_id())
    # Two separate asyncio.run() calls are two separate event loops - a
    # cache keyed only by db_path would wrongly hand the second loop a
    # connection object created (and, by the time this runs, potentially
    # already torn down) under the first, dead loop.
    assert first_id != second_id
    asyncio.run(_aio_db.reset())


def test_close_for_current_loop_only_closes_this_loops_entries(tmp_path):
    db_a = tmp_path / "a.db"
    db_b = tmp_path / "b.db"

    async def _open_two():
        await _aio_db.connection_for(db_a)
        await _aio_db.connection_for(db_b)

    async def _open_one_and_close_loop():
        conn = await _aio_db.connection_for(db_a)
        await _aio_db.close_for_current_loop()
        return conn

    asyncio.run(_open_two())
    closed_conn = asyncio.run(_open_one_and_close_loop())
    # The connection this loop opened and then closed must actually be
    # closed (a second use raises) - proves close_for_current_loop really
    # closes, not just evicts from the dict.
    import pytest
    with pytest.raises(Exception):
        asyncio.run(closed_conn.execute("SELECT 1"))
    asyncio.run(_aio_db.reset())


def test_schema_init_runs_once_on_first_open_only(tmp_path):
    db_path = tmp_path / "t.db"
    calls = []

    async def _schema_init(conn):
        calls.append(1)
        await conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER)")

    async def _run():
        await _aio_db.connection_for(db_path, schema_init=_schema_init)
        await _aio_db.connection_for(db_path, schema_init=_schema_init)  # cache hit

    asyncio.run(_run())
    assert len(calls) == 1
    asyncio.run(_aio_db.reset())


def test_connection_for_without_schema_init_does_not_require_a_preexisting_table(tmp_path):
    # diagnostics.py's own callers pass no schema_init at all (their DB
    # files' schemas are guaranteed by other, write-path modules) - confirm
    # connection_for() itself never requires one.
    db_path = tmp_path / "t.db"

    async def _run():
        conn = await _aio_db.connection_for(db_path)
        return conn is not None

    assert asyncio.run(_run()) is True
    asyncio.run(_aio_db.reset())


def test_locks_are_scoped_per_loop_not_shared_across_loops(tmp_path):
    # Finding C (adversarial review, 2026-09-01): a single cross-loop-shared
    # asyncio.Lock would itself be a loop-safety hazard, undermining the
    # exact guarantee this module exists to provide. Assert the internal
    # lock registry grows one entry per loop actually used, never fewer.
    db_a = tmp_path / "a.db"

    async def _touch():
        await _aio_db.connection_for(db_a)

    asyncio.run(_touch())
    asyncio.run(_touch())
    assert len(_aio_db._locks) == 2  # two asyncio.run() calls, two loops, two lock entries
    asyncio.run(_aio_db.reset())
```

- [x] **Step 3: Run the test to verify it fails**

Run: `cd /app/.claude/worktrees/aiosqlite-diagnostics-whale-scoring && python -m pytest tests/test_aio_db.py -v` (via `docker exec ddev-kalshi-whale-poc-fastapi sh -c "..."`, since `ddev exec` refuses to run from a linked worktree per this repo's own `CLAUDE.md`)
Expected: FAIL with `ModuleNotFoundError: No module named 'services.diagnostics._aio_db'` (and `aiosqlite` itself not yet installed in the container — install it first, see note below).

Before running: the container image doesn't have `aiosqlite` yet either (Task 1 Step 1 only edited `requirements.txt`, the image hasn't rebuilt). For local iteration without a full `ddev restart`/rebuild, `docker exec ddev-kalshi-whale-poc-fastapi sh -c "pip install aiosqlite==0.22.1"` installs it into the running container for this session; a real `ddev restart` (which rebuilds from `requirements.txt`) is still required before this is a durable part of the image, and CI's own build does this from `requirements.txt` directly regardless.

- [x] **Step 4: Implement `_aio_db.py`**

Create `services/diagnostics/_aio_db.py`:

```python
"""Shared, loop-scoped aiosqlite connection cache for
services/diagnostics/diagnostics.py and services/series_watcher.py's
read-only functions (event-loop-blocking-elimination Fix 2,
docs/archive/lane-1-kalshi-ingestion/specs/2026-09-01-event-loop-blocking-elimination-design.md).
One persistent aiosqlite.Connection per (event loop, db_path) pair, opened
on first use and reused.

Read-only, low-frequency callers (dashboard polls at ~5s; services/research/
research.py's on-demand report), not the per-trade whale-scoring hot path
(services/whalewatchers/_scoring_pool.py uses 2 connections/file for that
reason) - a single connection per file is enough headroom here. If
concurrent-caller queueing is ever measured as a real problem, the same
2-per-file escape hatch is available there, not guessed preemptively here
(CLAUDE.md's data-plane HARD RULE).

Keyed by (id(event loop), db_path), not db_path alone: services/research/
research.py's build_report() calls into this module from a plain sync
function that itself runs via asyncio.to_thread(run_and_store, cfg) - a
worker thread with no running event loop of its own - and reaches
diagnostics.run_offline() through its own throwaway asyncio.run() call.
Keying by loop identity means research.py's throwaway loop always gets its
own fresh connections, never the main loop's, and - the actual point - that
its close_for_current_loop() closes exactly those and not the main app's.

[Corrected 2026-09-01, PR adversarial review finding I3. This paragraph
originally read: "An aiosqlite.Connection is bound to the event loop that
created it ... aiosqlite's internal read/write queue is loop-bound." That is
false for the pinned 0.22.1, verified by reading the installed
aiosqlite/core.py: Connection.__init__ warns the `loop` parameter is "no
longer used", Connection._execute creates its future on whatever loop is
CALLING via asyncio.get_event_loop().create_future(), and the worker thread
delivers results with future.get_loop().call_soon_threadsafe(...). The
transport is a plain SimpleQueue on a plain Thread. Loop-scoping the cache
remains correct, but for connection lifetime/ownership reasons - see the
Architecture note above and the shipped docstring in
services/diagnostics/_aio_db.py.]

The lock guarding first-open-per-key is ALSO scoped per loop (a dict of
locks, not one shared asyncio.Lock) for the identical reason: asyncio's own
synchronization primitives are themselves not safe to use across two
different event loops (a waiter Future created under one loop, released by
another, calls that other loop's internals non-threadsafely) - a single
shared Lock would silently reintroduce the exact cross-loop hazard this
whole module exists to eliminate for Connection objects specifically
(adversarial review finding C, 2026-09-01)."""
import asyncio
from pathlib import Path
from typing import Awaitable, Callable

import aiosqlite

_connections: dict[tuple[int, Path], aiosqlite.Connection] = {}
_locks: dict[int, asyncio.Lock] = {}


def _key(db_path: Path) -> tuple[int, Path]:
    return (id(asyncio.get_running_loop()), db_path)


def _lock_for_current_loop() -> asyncio.Lock:
    loop_id = id(asyncio.get_running_loop())
    lock = _locks.get(loop_id)
    if lock is None:
        lock = _locks[loop_id] = asyncio.Lock()
    return lock


async def connection_for(
    db_path: Path,
    schema_init: Callable[[aiosqlite.Connection], Awaitable[None]] | None = None,
) -> aiosqlite.Connection:
    """schema_init, when given, runs exactly once - only on this key's
    first-ever open, never on a cache hit - same contract as
    services/whalewatchers/_scoring_pool.py's cached_read_connection(). Pass
    nothing when the target DB file's schema is already guaranteed to exist
    by its own write-path module (every diagnostics.py caller); pass one
    when the caller previously relied on a plain sqlite3.connect()-adjacent
    helper that also ran CREATE TABLE IF NOT EXISTS on every call
    (series_watcher.py's three converted read functions, replacing
    _connect()'s self-healing schema creation - dropping this silently
    would collapse the "no data yet" vs. "store unreadable" distinction
    diagnostics.py's checks are designed around; adversarial review finding
    A, 2026-09-01)."""
    key = _key(db_path)
    conn = _connections.get(key)
    if conn is not None:
        return conn
    async with _lock_for_current_loop():
        conn = _connections.get(key)
        if conn is None:
            conn = await aiosqlite.connect(db_path)
            conn.row_factory = aiosqlite.Row
            if schema_init is not None:
                await schema_init(conn)
            _connections[key] = conn
        return conn


async def close_for_current_loop() -> None:
    """Closes and evicts only the connections (and this loop's lock entry)
    opened under the currently running loop. services/research/research.py
    calls this once its diagnostics.run_offline() call returns, inside the
    same throwaway asyncio.run() invocation - without it, every research
    report generated over the process's lifetime would leak one connection
    per DB file touched (a new throwaway loop, and therefore a new cache
    key, every single call)."""
    loop_id = id(asyncio.get_running_loop())
    stale = [key for key in _connections if key[0] == loop_id]
    for key in stale:
        await _connections.pop(key).close()
    _locks.pop(loop_id, None)


async def reset() -> None:
    """Test-only: close every cached connection regardless of loop, and
    clear the lock registry. Each test monkeypatches DB_PATH to a fresh
    tmp_path, so a connection cached from a prior test would otherwise
    point at an already-deleted file."""
    for conn in list(_connections.values()):
        await conn.close()
    _connections.clear()
    _locks.clear()
```

- [x] **Step 5: Run the test to verify it passes**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/aiosqlite-diagnostics-whale-scoring && python -m pytest tests/test_aio_db.py -v"`
Expected: PASS, 7 tests.

- [x] **Step 6: Commit**

```bash
git add requirements.txt services/diagnostics/_aio_db.py tests/test_aio_db.py
git commit -m "feat: add aiosqlite dependency + shared loop-scoped connection cache"
```

---

### Task 2: Convert `diagnostics.py`'s shared helpers + `check_threshold_integrity` + `check_price_band_adherence`

**Files:**
- Modify: `services/diagnostics/diagnostics.py:74-305` (`_close_ts_for_tickers`, `_fetch_path_changes`, `check_threshold_integrity`, `check_price_band_adherence`)
- Test: `tests/test_diagnostics.py`

**Interfaces:**
- Consumes: `_aio_db.connection_for(db_path)` from Task 1.
- Produces: `async def _close_ts_for_tickers(tickers) -> dict`, `async def _fetch_path_changes(paths, since_ts) -> list`, `async def check_threshold_integrity(...) -> Check`, `async def check_price_band_adherence(...) -> Check` — Task 6 awaits these from `run_offline()`.

- [x] **Step 1: Update the existing tests to `asyncio.run(...)`-wrap these two checks**

In `tests/test_diagnostics.py`, every call of the shape `diagnostics.check_threshold_integrity(...)` or `diagnostics.check_price_band_adherence(...)` (5 tests total: `test_threshold_integrity_flags_signals_below_the_configured_floor`, `test_threshold_integrity_respects_per_series_overrides`, `test_threshold_integrity_unknown_when_no_data`, `test_threshold_integrity_is_epoch_aware_not_judged_against_todays_config`, `test_threshold_integrity_still_flags_a_real_violation_from_before_a_later_raise`, `test_price_band_flags_entries_above_max_unit_cost`, `test_price_band_uses_side_aware_unit_cost_not_raw_price`, `test_price_band_is_epoch_aware_not_judged_against_todays_band`) — wrap the call:

```python
# before
c = diagnostics.check_threshold_integrity(_cfg(), since_ts=now - 3600, now=now)
# after
c = asyncio.run(diagnostics.check_threshold_integrity(_cfg(), since_ts=now - 3600, now=now))
```

Add `import asyncio` to this test file's imports if not already present. Also add an autouse fixture so the shared cache from Task 1 doesn't leak a stale connection from one test's tmp_path into the next:

```python
@pytest.fixture(autouse=True)
def _reset_aio_db_cache():
    yield
    import asyncio
    from services.diagnostics import _aio_db
    asyncio.run(_aio_db.reset())
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/aiosqlite-diagnostics-whale-scoring && python -m pytest tests/test_diagnostics.py -k 'threshold_integrity or price_band' -v"`
Expected: FAIL — `asyncio.run()` wrapping a plain (non-coroutine) return value raises `TypeError: An asyncio.Future, a coroutine or an awaitable is required`, since the functions aren't `async def` yet.

- [x] **Step 3: Convert the two helpers and two checks**

In `services/diagnostics/diagnostics.py`, add the import (near the top, alongside the other `services` imports):

```python
from services.diagnostics import _aio_db
```

Replace `_close_ts_for_tickers` (lines 74-97):

```python
async def _close_ts_for_tickers(tickers: list[str]) -> dict[str, float]:
    """ticker -> close_ts, from market_catalog (the one store that persists a
    close time per market beyond the rotating watchlist). Deliberately NOT
    reconstructed from the ticker string: the YYMMMDDHHMM convention is a
    per-series habit, not an API guarantee, and close_time is mutable
    upstream anyway (docs/kalshi/market_lifecycle.md's close_date_updated).
    Missing tickers are simply absent from the result - callers bucket
    those as "unknown" rather than guessing."""
    from services.market_catalog import market_catalog

    unique = list({t for t in tickers if t})
    if not unique:
        return {}
    try:
        conn = await _aio_db.connection_for(market_catalog.DB_PATH)
        placeholders = ",".join("?" for _ in unique)
        rows = await conn.execute_fetchall(
            f"SELECT ticker, close_ts FROM markets WHERE ticker IN ({placeholders}) "
            "AND close_ts IS NOT NULL",
            unique,
        )
    except sqlite3.Error:
        return {}
    return {t: ts for t, ts in rows}
```

Replace `_fetch_path_changes` (lines 100-116):

```python
async def _fetch_path_changes(paths: list[str], since_ts: float) -> list[dict]:
    """Every config_performance.applied_changes row for these exact
    config_path values, recorded after since_ts - the raw material
    _historical_value rewinds. One query per check (not one per row)."""
    try:
        conn = await _aio_db.connection_for(config_performance.DB_PATH)
        placeholders = ",".join("?" for _ in paths)
        rows = await conn.execute_fetchall(
            f"SELECT applied_at, config_path, old_value FROM applied_changes "
            f"WHERE applied_at > ? AND config_path IN ({placeholders})",
            (since_ts, *paths),
        )
    except sqlite3.Error:
        return []
    return [{"applied_at": r["applied_at"], "config_path": r["config_path"],
              "old_value": json.loads(r["old_value"])} for r in rows]
```

(`_historical_value` is unchanged — pure Python, no DB access, still a plain `def`, called from async code exactly as before.)

Convert `check_threshold_integrity` — add `async` to the `def` line, add `await` before `_fetch_path_changes(...)`, and replace the DB block:

```python
async def check_threshold_integrity(cfg: dict, since_ts: float | None = None, now: float | None = None) -> Check:
    # ... (docstring unchanged) ...
    now = now if now is not None else time.time()
    since_ts = since_ts if since_ts is not None else now - 24 * 3600
    changes = await _fetch_path_changes([_MIN_CONTRACTS_PATH, _MIN_CONTRACTS_BY_SERIES_PATH], since_ts)

    try:
        conn = await _aio_db.connection_for(signal_log.DB_PATH)
        rows = await conn.execute_fetchall(
            "SELECT series, ticker, size, seen_at FROM signals "
            "WHERE seen_at > ? AND factors_json IS NOT NULL",
            (since_ts,),
        )
    except sqlite3.Error as exc:
        return Check("threshold_integrity", _UNKNOWN, f"signal_log unreadable: {exc}")
    # ... rest of the function body (the pure-Python aggregation loop and
    # Check construction) is completely unchanged ...
```

Convert `check_price_band_adherence` the same way, plus wrap the one out-of-scope call:

```python
async def check_price_band_adherence(cfg: dict, since_ts: float | None = None, now: float | None = None) -> Check:
    # ... (docstring unchanged) ...
    import asyncio
    from services import trade_category
    from services.config import config_overrides

    now = now if now is not None else time.time()
    since_ts = since_ts if since_ts is not None else now - 24 * 3600
    changes = await _fetch_path_changes(
        [_MIN_UNIT_COST_PATH, _MAX_UNIT_COST_PATH, _OVERRIDES_BY_CATEGORY_PATH, _OVERRIDES_BY_SERIES_PATH],
        since_ts,
    )

    try:
        conn = await _aio_db.connection_for(pb_module.DB_PATH)
        rows = await conn.execute_fetchall(
            "SELECT ticker, side, price, size, reason, timestamp FROM trades "
            "WHERE timestamp > ? AND reason LIKE 'whale print%'",
            (since_ts,),
        )
    except sqlite3.Error as exc:
        return Check("price_band_adherence", _UNKNOWN, f"paper_broker unreadable: {exc}")
    if not rows:
        return Check("price_band_adherence", _UNKNOWN, "no whale-follow entries in this window")

    # trade_category.py is out of this plan's scope (see Global Constraints) -
    # categories_for_tickers() stays a plain synchronous DB call, run on a
    # worker thread via asyncio.to_thread so it doesn't block the event loop
    # from inside this now-async function, without converting trade_category.py
    # itself (which has other, unconverted callers).
    cats = await asyncio.to_thread(trade_category.categories_for_tickers, [r["ticker"] for r in rows])
    above, below, inside, offenders = 0, 0, 0, []
    for r in rows:
        # ... rest of the loop body and Check construction unchanged ...
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/aiosqlite-diagnostics-whale-scoring && python -m pytest tests/test_diagnostics.py -k 'threshold_integrity or price_band' -v"`
Expected: PASS, 8 tests.

- [x] **Step 5: Commit**

```bash
git add services/diagnostics/diagnostics.py tests/test_diagnostics.py
git commit -m "feat: convert check_threshold_integrity/check_price_band_adherence to aiosqlite"
```

---

### Task 3: Convert `check_runway_at_entry` + `config_epochs`/`performance_by_epoch`

**Files:**
- Modify: `services/diagnostics/diagnostics.py:310-487`
- Test: `tests/test_diagnostics.py`

**Interfaces:**
- Consumes: `_aio_db.connection_for`, `_close_ts_for_tickers` (now async, from Task 2).
- Produces: `async def check_runway_at_entry(...) -> Check`, `async def config_epochs(...) -> list[dict]`, `async def performance_by_epoch(...) -> Check`.

- [x] **Step 1: Update existing tests to `asyncio.run(...)`-wrap these three**

In `tests/test_diagnostics.py`: `test_runway_buckets_entries_by_time_to_close`, `test_runway_unknown_when_no_close_time_is_recorded`, `test_performance_by_epoch_splits_trades_at_config_change_boundaries`, `test_performance_by_epoch_unknown_without_enough_trades` — same `asyncio.run(...)` wrap as Task 2 Step 1.

- [x] **Step 2: Run the tests to verify they fail**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/aiosqlite-diagnostics-whale-scoring && python -m pytest tests/test_diagnostics.py -k 'runway or performance_by_epoch' -v"`
Expected: FAIL, same `TypeError` shape as Task 2.

- [x] **Step 3: Convert `check_runway_at_entry`**

```python
async def check_runway_at_entry(cfg: dict, since_ts: float | None = None, now: float | None = None) -> Check:
    # ... (docstring unchanged) ...
    now = now if now is not None else time.time()
    since_ts = since_ts if since_ts is not None else now - 24 * 3600
    strat = cfg.get("strategy") or {}
    floor = strat.get("min_seconds_to_close")

    try:
        conn = await _aio_db.connection_for(pb_module.DB_PATH)
        opens = await conn.execute_fetchall(
            "SELECT ticker, side, price, size, timestamp FROM trades "
            "WHERE timestamp > ? AND reason LIKE 'whale print%' ORDER BY timestamp",
            (since_ts,),
        )
        closes = await conn.execute_fetchall(
            "SELECT ticker, reason, timestamp FROM trades "
            "WHERE timestamp > ? AND reason LIKE 'closed:%' AND excluded = 0 ORDER BY timestamp",
            (since_ts,),
        )
    except sqlite3.Error as exc:
        return Check("runway_at_entry", _UNKNOWN, f"paper_broker unreadable: {exc}")
    if not opens:
        return Check("runway_at_entry", _UNKNOWN, "no whale-follow entries in this window")

    close_by_ticker = await _close_ts_for_tickers([r["ticker"] for r in opens])
    # ... rest of the function (bucketing loop, Check construction) unchanged ...
```

- [x] **Step 4: Convert `config_epochs` and `performance_by_epoch`**

```python
async def config_epochs(since_ts: float | None = None, now: float | None = None) -> list[dict]:
    # ... (docstring unchanged) ...
    now = now if now is not None else time.time()
    since_ts = since_ts if since_ts is not None else now - 7 * 24 * 3600
    try:
        conn = await _aio_db.connection_for(config_performance.DB_PATH)
        rows = await conn.execute_fetchall(
            "SELECT applied_at, config_path, old_value, new_value, source "
            "FROM applied_changes WHERE applied_at > ? ORDER BY applied_at",
            (since_ts,),
        )
    except sqlite3.Error:
        return []
    # ... rest (by_ts collapsing, epoch construction) unchanged ...


async def performance_by_epoch(since_ts: float | None = None, now: float | None = None,
                                min_trades: int = 3) -> Check:
    import re
    now = now if now is not None else time.time()
    since_ts = since_ts if since_ts is not None else now - 7 * 24 * 3600
    epochs = await config_epochs(since_ts, now)
    if not epochs:
        return Check("performance_by_epoch", _UNKNOWN,
                     "no config changes recorded in this window — no epochs to compare")
    try:
        conn = await _aio_db.connection_for(pb_module.DB_PATH)
        closes = await conn.execute_fetchall(
            "SELECT ticker, reason, timestamp FROM trades "
            "WHERE timestamp > ? AND reason LIKE 'closed:%' AND excluded = 0",
            (since_ts,),
        )
    except sqlite3.Error as exc:
        return Check("performance_by_epoch", _UNKNOWN, f"paper_broker unreadable: {exc}")
    # ... rest (regex parse, per-epoch aggregation, Check construction) unchanged ...
```

Note: `config_epochs` is also called directly elsewhere in this file only by `performance_by_epoch` (grepped, confirmed) — no other caller inside `diagnostics.py` needs updating.

- [x] **Step 5: Run the tests to verify they pass**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/aiosqlite-diagnostics-whale-scoring && python -m pytest tests/test_diagnostics.py -k 'runway or performance_by_epoch' -v"`
Expected: PASS, 4 tests.

- [x] **Step 6: Commit**

```bash
git add services/diagnostics/diagnostics.py tests/test_diagnostics.py
git commit -m "feat: convert check_runway_at_entry/config_epochs/performance_by_epoch to aiosqlite"
```

---

### Task 4: Convert `selectivity_curve` + `check_confidence_input_coverage`

**Files:**
- Modify: `services/diagnostics/diagnostics.py:616-736`
- Test: `tests/test_diagnostics.py`

**Interfaces:**
- Produces: `async def selectivity_curve(...) -> Check`, `async def check_confidence_input_coverage(...) -> Check`.

- [x] **Step 1: Update existing tests**

In `tests/test_diagnostics.py`: `test_confidence_input_coverage_ok_once_calibration_is_ungated`, `test_confidence_input_coverage_unknown_below_the_resolved_floor`, plus any `selectivity_curve` test present — `asyncio.run(...)` wrap, same as before. `test_confidence_input_coverage_ok_once_calibration_is_ungated` uses `monkeypatch` (per its signature `(dbs, monkeypatch)`) — check what it monkeypatches (likely `confidence_calibration.compute_input_coverage` or `signal_log.resolved_signals_with_factors`) and confirm the patched target's call shape still matches after this task's change (it should — the wrap in Step 3 below calls `signal_log.resolved_signals_with_factors` exactly as before, just via `asyncio.to_thread`).

- [x] **Step 2: Run the tests to verify they fail**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/aiosqlite-diagnostics-whale-scoring && python -m pytest tests/test_diagnostics.py -k 'selectivity or confidence_input_coverage' -v"`
Expected: FAIL, same shape as prior tasks.

- [x] **Step 3: Convert both functions**

```python
async def selectivity_curve(min_notional: float = 2500.0, since_ts: float | None = None,
                             now: float | None = None) -> Check:
    # ... (docstring unchanged) ...
    now = now if now is not None else time.time()
    since_ts = since_ts if since_ts is not None else now - 30 * 24 * 3600
    try:
        conn = await _aio_db.connection_for(signal_log.DB_PATH)
        rows = await conn.execute_fetchall(
            "SELECT confidence, correct, raw_notional_usd, price, side FROM signals "
            "WHERE seen_at > ? AND resolved = 1 AND excluded = 0 "
            "AND raw_notional_usd IS NOT NULL AND raw_notional_usd >= ?",
            (since_ts, min_notional),
        )
    except sqlite3.Error as exc:
        return Check("selectivity_curve", _UNKNOWN, f"signal_log unreadable: {exc}")
    # ... rest (curve-building loop, Check construction) unchanged ...


async def check_confidence_input_coverage(cfg: dict, since_ts: float | None = None, now: float | None = None) -> Check:
    """... (docstring unchanged, except its stale "tick_executor pool" phrase
    - this route has run on services.diagnostics._diagnostics_pool since PR
    #409's write-path-capacity-fix, not tick_executor, and after this plan
    it runs via aiosqlite with no dedicated pool at all; correct that one
    sentence while touching this docstring) ..."""
    import asyncio
    from services.whale_calibration import confidence_calibration

    cc_cfg = cfg.get("confidence_calibration") or {}
    min_resolved_signals = cc_cfg.get("min_resolved_signals", 50)
    # signal_log.py is out of this plan's scope (see Global Constraints) -
    # resolved_signals_with_factors() stays a plain synchronous DB call, run
    # on a worker thread via asyncio.to_thread. Its own unscoped-fetch cost
    # (docs/open-decisions.md's Task 9 entry) is unchanged by this - only
    # where it runs changes, not what it costs.
    rows = await asyncio.to_thread(signal_log.resolved_signals_with_factors, since_ts=since_ts)
    resolved_count = len(rows)
    if resolved_count < min_resolved_signals:
        return Check(
            "confidence_input_coverage", _UNKNOWN,
            f"{resolved_count}/{min_resolved_signals} resolved real signals with a factor "
            "breakdown - calibration activates once that's reached",
        )
    coverage = confidence_calibration.compute_input_coverage(rows, resolved_count)
    return Check(
        "confidence_input_coverage", _OK,
        f"depth {coverage['depth_factor']['absent_pct']}%, trend {coverage['trend_factor']['absent_pct']}%, "
        f"agreement {coverage['agreement_factor']['absent_pct']}%, spread {coverage['raw_spread']['absent_pct']}% "
        f"absent (n={resolved_count})",
        detail={"input_coverage": coverage},
    )
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/aiosqlite-diagnostics-whale-scoring && python -m pytest tests/test_diagnostics.py -k 'selectivity or confidence_input_coverage' -v"`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add services/diagnostics/diagnostics.py tests/test_diagnostics.py
git commit -m "feat: convert selectivity_curve/check_confidence_input_coverage to aiosqlite"
```

---

### Task 5: Convert `series_watcher.py`'s read-only functions

**Files:**
- Modify: `services/series_watcher.py:475-983` (`_signals_for_series`, `_trades_for_series`, `funnel`, `reconcile`, `check_series_funnel`) and `:430-471`/`:847-911` (`capture_stats`, `book_context_at_entry`)
- Test: the test file covering `series_watcher.py`'s read-side functions (confirm exact filename at execution time — likely `tests/test_series_watcher.py`)

**Interfaces:**
- Consumes: `_aio_db.connection_for` (Task 1). Note this file does NOT import `services.diagnostics.diagnostics` (avoiding the existing circular-import concern this file's own module docstring already documents for the reverse direction) — import `_aio_db` directly: `from services.diagnostics import _aio_db`.
- Produces: `async def _signals_for_series(...)`, `async def _trades_for_series(...)`, `async def funnel(...) -> dict`, `async def reconcile(...) -> dict`, `async def check_series_funnel(...) -> Check`, `async def capture_stats(...) -> dict`, `async def book_context_at_entry(...) -> dict`. `check_series_funnel` is what `diagnostics.run_offline()` awaits (Task 6).

- [x] **Step 1: Update existing tests**

`tests/test_series_watcher.py` (confirmed as the exact filename covering this module's read-side functions, via adversarial review, 2026-09-01) — every call to `funnel(...)`, `reconcile(...)`, `check_series_funnel(...)`, `capture_stats(...)`, `book_context_at_entry(...)`, `_signals_for_series(...)`, `_trades_for_series(...)` gets the same `asyncio.run(...)` wrap as prior tasks. Grep first to get the complete list before editing: `grep -n "sw\.\(funnel\|reconcile\|check_series_funnel\|capture_stats\|book_context_at_entry\)(" tests/test_series_watcher.py` (adjust the module alias in the pattern to match however this file actually imports `series_watcher` — check its own import line first).

- [x] **Step 2: Run the tests to verify they fail**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/aiosqlite-diagnostics-whale-scoring && python -m pytest tests/test_series_watcher.py -v"`
Expected: FAIL, same `TypeError` shape.

- [x] **Step 3: Add the schema-init helper, then convert `_signals_for_series` and `_trades_for_series`**

`_connect()` (this file, lines 151-207) currently self-heals a missing/fresh `series_watcher.db` on EVERY call: `DB_PATH.parent.mkdir(exist_ok=True)`, `PRAGMA journal_mode=WAL`, `CREATE TABLE IF NOT EXISTS` for both `raw_trades` and `book_snapshots` plus their indexes. `funnel()`, `capture_stats()`, and `book_context_at_entry()` (Steps 4/7/8 below) are the three functions in this file that move off `_connect()` onto `_aio_db.connection_for()` — without replicating this schema-ensuring behavior, a genuinely fresh/empty `series_watcher.db` would make these three functions raise `OperationalError: no such table`, caught by their own `except sqlite3.Error`, and silently collapse the distinction between "no data yet" (today's honest answer) and "store unreadable" (a different honest answer, but the wrong one) - the exact "degrades honestly" design principle `services/diagnostics/diagnostics.py`'s own module docstring states as this whole subsystem's reason to exist. Add, near `_connect()`:

```python
async def _ensure_schema_aio(conn) -> None:
    """Same DDL as _connect() above, run once per (loop, db_path) key via
    _aio_db.connection_for()'s schema_init hook - _connect() itself stays
    untouched (still used by every write-path function this plan doesn't
    convert). Duplicated rather than shared with _connect() because one is
    sync (sqlite3.Connection) and one is async (aiosqlite.Connection) -
    keep the two DDL blocks in sync by hand if this table's schema ever
    changes; both are exercised by tests/test_series_watcher.py."""
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS raw_trades (
            trade_id TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            series TEXT NOT NULL,
            observed_at REAL NOT NULL,
            exchange_ts REAL,
            taker_outcome_side TEXT,
            taker_book_side TEXT,
            taker_side_legacy TEXT,
            resolved_side TEXT,
            count_fp REAL,
            yes_price_dollars REAL,
            no_price_dollars REAL,
            notional_usd REAL,
            is_block_trade INTEGER,
            excluded INTEGER NOT NULL DEFAULT 0,
            raw_json TEXT NOT NULL
        )
        """
    )
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_trades_series ON raw_trades (series, observed_at)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_trades_ticker ON raw_trades (ticker, observed_at)")
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS book_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            series TEXT NOT NULL,
            observed_at REAL NOT NULL,
            exchange_ts REAL,
            price_dollars REAL,
            yes_bid_dollars REAL,
            yes_ask_dollars REAL,
            yes_bid_size_fp REAL,
            yes_ask_size_fp REAL,
            volume_fp REAL,
            open_interest_fp REAL,
            dollar_volume REAL,
            dollar_open_interest REAL,
            last_trade_size_fp REAL,
            raw_json TEXT NOT NULL
        )
        """
    )
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_book_ticker ON book_snapshots (ticker, observed_at)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_book_series ON book_snapshots (series, observed_at)")
```

`DB_PATH.parent.mkdir(exist_ok=True)` is NOT replicated here — `aiosqlite.connect()` (like `sqlite3.connect()`) doesn't need the parent directory pre-created for `data/`, which already exists for every real deployment (this app's other `_connect()`-style functions across `services/` all mkdir defensively for a from-scratch checkout; `_aio_db.connection_for()` deliberately doesn't take this on generically since diagnostics.py's 4 callers never need it - their DB files are guaranteed to exist first via other write-path modules that already ran their own mkdir). If `tests/test_series_watcher.py`'s `dbs`-equivalent fixture ever points `DB_PATH` at a `tmp_path` subdirectory that doesn't exist yet, add the `mkdir` call to `_ensure_schema_aio` too - check this against the actual fixture at execution time rather than assuming.

Then convert `_signals_for_series`/`_trades_for_series` (these two don't touch `raw_trades`/`book_snapshots` at all - `signal_log.db`/`paper_broker.db`, both schema-guaranteed by their own write paths exactly like diagnostics.py's targets - so no `schema_init` needed here):

```python
async def _signals_for_series(series: str, since_ts: float, before_ts: float) -> list[dict]:
    """Non-excluded signals only — an experiment window is real data about
    the exchange but is not evidence about the strategy (see
    signal_log.mark_excluded_range)."""
    try:
        conn = await _aio_db.connection_for(signal_log.DB_PATH)
        rows = await conn.execute_fetchall(
            "SELECT ticker, side, size, confidence, seen_at, price, resolved, correct, "
            "raw_notional_usd FROM signals "
            "WHERE series = ? AND seen_at > ? AND seen_at <= ? AND excluded = 0",
            (series, since_ts, before_ts),
        )
    except sqlite3.Error:
        return []
    return [dict(r) for r in rows]


async def _trades_for_series(series: str, since_ts: float, before_ts: float) -> list[dict]:
    """... (docstring unchanged) ..."""
    try:
        conn = await _aio_db.connection_for(pb_module.DB_PATH)
        rows = await conn.execute_fetchall(
            "SELECT id, ticker, side, size, price, reason, timestamp, config_fingerprint, "
            "fee, signal_seen_at FROM trades "
            "WHERE ticker LIKE ? AND timestamp > ? AND timestamp <= ? AND excluded = 0 ORDER BY timestamp",
            (f"{series}-%", since_ts - 7 * 86400, before_ts),
        )
    except sqlite3.Error:
        return []
    return [dict(r) for r in rows if signal_log.series_of(r["ticker"]) == series]
```

- [x] **Step 4: Convert `funnel`**

Add `async` to the `def` line; the function's own `raw_trades` queries (currently via this module's `_connect()`) move to the shared cache; the two calls it makes to `_signals_for_series`/`_trades_for_series` (now async) get `await`:

```python
async def funnel(series: str | None = None, hours: float = 24.0, cfg: dict | None = None,
                  now: float | None = None) -> dict:
    # ... (docstring unchanged) ...
    series = series or DEFAULT_SERIES
    now = now if now is not None else time.time()
    since_ts = now - hours * 3600

    min_contracts = float(
        ((cfg or {}).get("whale_watcher_kalshi") or {}).get("min_contracts_by_series", {}).get(series)
        or ((cfg or {}).get("whale_watcher_kalshi") or {}).get("min_contracts")
        or 0
    )

    try:
        conn = await _aio_db.connection_for(DB_PATH, schema_init=_ensure_schema_aio)
        cursor = await conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN count_fp >= ? THEN 1 ELSE 0 END), MIN(observed_at) "
            "FROM raw_trades WHERE series = ? AND observed_at > ? AND excluded = 0",
            (min_contracts, series, since_ts),
        )
        observed, whale_sized, capture_start = await cursor.fetchone()
        unreadable_cursor = await conn.execute(
            "SELECT COUNT(*) FROM raw_trades WHERE series = ? AND observed_at > ? "
            "AND resolved_side IS NULL",
            (series, since_ts),
        )
        unreadable_row = await unreadable_cursor.fetchone()
        unreadable_side = unreadable_row[0]
    except sqlite3.Error as exc:
        observed, whale_sized, unreadable_side, capture_start = None, None, None, None
        capture_error = str(exc)
    else:
        capture_error = None

    # ... capture_hours/capture_pct/capture_note block unchanged ...

    signals = await _signals_for_series(series, since_ts, now)
    resolved = [s for s in signals if s["resolved"]]
    correct = [s for s in resolved if s["correct"]]

    raw = await _trades_for_series(series, since_ts, now)
    # ... rest of the function (entries/history/wins/stages construction, return dict) unchanged ...
```

Note `DB_PATH` here is `series_watcher.py`'s own module-level `DB_PATH` (the file this function already lives in) — this replaces the module's existing `_connect()`-based access for this one function only; `_connect()` itself is untouched (still used by the write-path functions this plan doesn't touch).

- [x] **Step 5: Convert `reconcile`**

Add `async` to the `def` line, `await` its two calls to `_signals_for_series`/`_trades_for_series`:

```python
async def reconcile(series: str | None = None, hours: float = 24.0, cfg: dict | None = None,
                     now: float | None = None) -> dict:
    # ... (docstring unchanged) ...
    series = series or DEFAULT_SERIES
    now = now if now is not None else time.time()
    since_ts = now - hours * 3600

    signals = await _signals_for_series(series, since_ts, now)
    raw = await _trades_for_series(series, since_ts, now)
    # ... rest of the function (the entire selection_delta/exit_delta/edge_pts/
    # slippage/close_types computation and the final return dict) is pure
    # Python operating on `signals`/`raw` already in hand - completely
    # unchanged ...
```

- [x] **Step 6: Convert `check_series_funnel`**

```python
async def check_series_funnel(cfg: dict, series: str | None = None, hours: float = 24.0,
                               now: float | None = None) -> Check:
    """... (docstring unchanged) ..."""
    series = series or (watched_series(cfg) or [DEFAULT_SERIES])[0]
    r = await reconcile(series, hours=hours, cfg=cfg, now=now)

    # ... acc/win/gap/parts/edge/status/headline logic entirely unchanged ...

    return Check(
        f"series_funnel:{series}", status, headline,
        detail=r,
        evidence=(await funnel(series, hours=hours, cfg=cfg, now=now))["stages"],
    )
```

Note (do not act on this in this task — logged here so it isn't lost): `check_series_funnel` computes `reconcile()` and then, separately, `funnel()` again for the `evidence` field — both call `_signals_for_series`/`_trades_for_series` with the same arguments, so this function does its core DB work twice per call. That's a real, measured cost (`funnel()` alone was ~half of `check_series_funnel`'s measured per-series cost during this plan's own investigation) but it's a compute-redundancy problem, not an event-loop-blocking one — orthogonal to this plan's scope and not fixed here. Record it in `docs/open-decisions.md` as its own line when this plan's PR is opened (Task 7).

- [x] **Step 7: Convert `capture_stats`**

```python
async def capture_stats(series: str | None = None) -> dict:
    """... (docstring unchanged) ..."""
    series = series or DEFAULT_SERIES
    try:
        conn = await _aio_db.connection_for(DB_PATH, schema_init=_ensure_schema_aio)
        trades_cursor = await conn.execute(
            "SELECT COUNT(*), MIN(observed_at), MAX(observed_at) FROM raw_trades WHERE series = ?",
            (series,),
        )
        trades, first_t, last_t = await trades_cursor.fetchone()
        books_cursor = await conn.execute(
            "SELECT COUNT(*), MIN(observed_at), MAX(observed_at) FROM book_snapshots WHERE series = ?",
            (series,),
        )
        books, first_b, last_b = await books_cursor.fetchone()
    except sqlite3.Error as exc:
        return {"series": series, "error": str(exc)}
    # ... rest of the function (the returned dict) unchanged ...
```

- [x] **Step 8: Convert `book_context_at_entry`**

```python
async def book_context_at_entry(series: str | None = None, hours: float = 24.0,
                                 now: float | None = None) -> dict:
    """... (docstring unchanged) ..."""
    series = series or DEFAULT_SERIES
    now = now if now is not None else time.time()
    since_ts = now - hours * 3600

    raw = await _trades_for_series(series, since_ts, now)
    entries = [t for t in raw if not t["reason"].startswith("closed:") and t["timestamp"] > since_ts]
    if not entries:
        return {"series": series, "status": "unknown", "reason": "no entries in this window"}

    matched, spreads, depth_ratios = 0, [], []
    try:
        conn = await _aio_db.connection_for(DB_PATH, schema_init=_ensure_schema_aio)
        for t in entries:
            cursor = await conn.execute(
                "SELECT yes_bid_dollars, yes_ask_dollars, yes_bid_size_fp, yes_ask_size_fp, "
                "open_interest_fp FROM book_snapshots "
                "WHERE ticker = ? AND observed_at BETWEEN ? AND ? "
                "ORDER BY ABS(observed_at - ?) LIMIT 1",
                (t["ticker"], t["timestamp"] - _BOOK_MATCH_WINDOW_SEC,
                 t["timestamp"] + _BOOK_MATCH_WINDOW_SEC, t["timestamp"]),
            )
            snap = await cursor.fetchone()
            if snap is None:
                continue
            matched += 1
            bid, ask = snap["yes_bid_dollars"], snap["yes_ask_dollars"]
            if bid is not None and ask is not None:
                spreads.append(ask - bid)
            resting = snap["yes_ask_size_fp"] if t["side"] == "yes" else snap["yes_bid_size_fp"]
            if resting and t["size"]:
                depth_ratios.append(resting / t["size"])
    except sqlite3.Error as exc:
        return {"series": series, "status": "unknown", "reason": f"watcher store unreadable: {exc}"}
    # ... rest of the function (matched==0 branch, final return dict) unchanged ...
```

- [x] **Step 9: Run the tests to verify they pass**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/aiosqlite-diagnostics-whale-scoring && python -m pytest tests/test_series_watcher.py -v"`
Expected: PASS, including (do not skip checking these two specifically) `test_funnel_flags_that_capture_was_off_rather_than_claiming_no_whales` and `test_book_context_says_unknown_rather_than_inventing_a_spread` — both call `funnel()`/`book_context_at_entry()` against a `series_watcher.db` with no prior write, relying entirely on schema self-healing. Step 3's `_ensure_schema_aio` hook exists specifically so these two pass UNCHANGED, with no assertion edits, proving no behavior regression was introduced (adversarial review finding A, 2026-09-01) — if either fails, do not "fix" it by loosening the assertion; that would be silently accepting the regression this step exists to prevent.

- [x] **Step 10: Commit**

```bash
git add services/series_watcher.py tests/
git commit -m "feat: convert series_watcher.py's read-only functions to aiosqlite"
```

---

### Task 6: Wire `run_offline()` as async, update all 3 call sites, delete `_diagnostics_pool.py`

**Files:**
- Modify: `services/diagnostics/diagnostics.py:739-778` (`run_offline`)
- Modify: `services/quality/routes.py`
- Modify: `services/diagnostics/routes.py`
- Modify: `services/research/research.py`
- Delete: `services/diagnostics/_diagnostics_pool.py`
- Test: `tests/test_diagnostics.py`, `tests/test_quality_routes.py`, `tests/test_diagnostics_routes.py` (new tests, Step 8), `tests/test_research.py` (fix `_patch_every_analyzer()`, Step 7)

**Interfaces:**
- Consumes: every `async def` produced by Tasks 2-5.
- Produces: `async def run_offline(...) -> dict` — the final public entry point every caller reaches.

- [x] **Step 1: Update `run_offline`'s own tests**

`test_run_offline_reports_worst_status_across_checks`, `test_read_paths_close_their_sqlite_connections`, `test_run_offline_never_writes_to_any_db` in `tests/test_diagnostics.py` — `asyncio.run(...)` wrap. `test_read_paths_close_their_sqlite_connections` specifically asserts something about connection lifecycle under the OLD per-call-`closing()` pattern — read this test's actual body once you reach it and adapt its assertion to the new persistent-cache reality (a connection is now expected to stay OPEN across calls within one loop, not closed after each — the test's intent, "no connection leak," is still valid but its mechanism for checking that changes: assert the cache doesn't grow unboundedly across repeated calls with the same DB_PATH, e.g. call `run_offline` twice and assert `len(_aio_db._connections)` doesn't increase on the second call).

- [x] **Step 2: Run the tests to verify they fail**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/aiosqlite-diagnostics-whale-scoring && python -m pytest tests/test_diagnostics.py -k run_offline -v"`
Expected: FAIL.

- [x] **Step 3: Convert `run_offline`**

```python
async def run_offline(cfg: dict, since_ts: float | None = None, now: float | None = None) -> dict:
    """Every check that reads only local stores - no network, safe to call
    on any tick. check_coverage is deliberately excluded (it makes real API
    calls); callers that want it await it separately and merge the result."""
    from services import series_watcher

    now_ts = now if now is not None else time.time()
    hours = (now_ts - since_ts) / 3600 if since_ts is not None else 24.0
    checks = [
        await check_threshold_integrity(cfg, since_ts, now),
        await check_price_band_adherence(cfg, since_ts, now),
        await check_runway_at_entry(cfg, since_ts, now),
        check_config_bounds(cfg),  # unchanged - no DB access, stays sync
        await performance_by_epoch(since_ts, now),
        await selectivity_curve(since_ts=since_ts, now=now),
        await check_confidence_input_coverage(cfg, since_ts, now),
    ]
    for series in series_watcher.watched_series(cfg):
        checks.append(await series_watcher.check_series_funnel(cfg, series, hours=hours, now=now_ts))
    worst = _OK
    for c in checks:
        if c.status == _FAIL:
            worst = _FAIL
            break
        if c.status == _WARN and worst == _OK:
            worst = _WARN
    return {
        "generated_at": now if now is not None else time.time(),
        "overall": worst,
        "checks": [c.to_dict() for c in checks],
    }
```

Sequential `await` (not `asyncio.gather`), deliberately: matches today's exact ordering/semantics with the smallest possible behavior change, and avoids introducing new concurrency (and the questions that come with it — e.g. whether two checks touching the same DB file should race) in a plan whose whole point is eliminating a bug, not adding a new axis of behavior. If `run_offline()`'s wall-clock cost is later measured as a problem in its own right (a separate, compute-cost question from the event-loop-blocking one this plan fixes), parallelizing independent checks via `asyncio.gather` is the next lever — not applied speculatively here.

- [x] **Step 4: Update `services/quality/routes.py`**

Remove the `_diagnostics_pool` import and the wrapper call:

```python
# before (imports)
from services.diagnostics import _diagnostics_pool, diagnostics
# after
from services.diagnostics import diagnostics
```

```python
# before
"diagnostics": await _diagnostics_pool.run(lambda: diagnostics.run_offline(cfg)),
# after
"diagnostics": await diagnostics.run_offline(cfg),
```

Also update the surrounding comment block (currently explains why this route uses `_diagnostics_pool` instead of `tick_executor`) — replace it with a short note that `run_offline()`'s own call graph is aiosqlite-native now, so no dedicated pool is needed (point at this plan's design spec).

- [x] **Step 5: Update `services/diagnostics/routes.py`**

```python
# get_diagnostics (line 53)
# before
return diagnostics.run_offline(config_store.get(), since_ts=now - hours * 3600, now=now)
# after
return await diagnostics.run_offline(config_store.get(), since_ts=now - hours * 3600, now=now)
```

```python
# get_series_watcher (lines 203-207)
# before
return {
    "funnel": series_watcher.funnel(series, hours=hours, cfg=cfg, now=now),
    "reconcile": series_watcher.reconcile(series, hours=hours, cfg=cfg, now=now),
    "book_context": series_watcher.book_context_at_entry(series, hours=hours, now=now),
    "capture": series_watcher.capture_stats(series),
}
# after
return {
    "funnel": await series_watcher.funnel(series, hours=hours, cfg=cfg, now=now),
    "reconcile": await series_watcher.reconcile(series, hours=hours, cfg=cfg, now=now),
    "book_context": await series_watcher.book_context_at_entry(series, hours=hours, now=now),
    "capture": await series_watcher.capture_stats(series),
}
```

- [x] **Step 6: Update `services/research/research.py`**

`build_report()` stays a plain `def` (it's invoked via `asyncio.to_thread(run_and_store, cfg)` from `_run_research_background`, and still calls other genuinely-synchronous, out-of-this-plan's-scope functions like `candidate_log.population_gate_summary()` and `signal_log.resolved_signals_with_factors()` directly — converting just this one call to `await` would require making the whole function async, which would then need those other still-sync calls wrapped too, expanding this plan's scope well past its two named files). Its one call to `diagnostics.run_offline()` goes through a local `asyncio.run(...)`:

```python
# add near the top of build_report(), or as a module-level import if
# research.py doesn't already do local imports for this kind of thing
# (check the file's existing style before deciding placement)
import asyncio

from services.diagnostics import _aio_db

# ... inside build_report(), replacing the line
#     "diagnostics": diagnostics.run_offline(cfg, now=now),
# with a two-step build:

async def _diagnostics_and_cleanup() -> dict:
    # research.py's build_report() runs off the event loop entirely (via
    # asyncio.to_thread(run_and_store, cfg), services/research/research.py's
    # own _run_research_background docstring) - this asyncio.run() call
    # creates its own throwaway loop just for this one await. _aio_db's
    # cache is keyed by loop identity precisely so this never collides with
    # the main app's own long-lived connections (see _aio_db.py's own
    # docstring) - close_for_current_loop() afterward prevents this
    # throwaway loop's connections from leaking (never explicitly closed
    # otherwise, since asyncio.run() tears down the loop but doesn't know to
    # call our own conn.close() first) across every research report ever
    # generated in this process's lifetime. try/finally (adversarial review
    # finding D, 2026-09-01): without it, an exception from run_offline()
    # skips cleanup entirely, pinning this dead loop object, its
    # connections and their NON-daemon aiosqlite worker threads for the
    # rest of the process - one set per failed report.
    #
    # [CORRECTED 2026-09-01, PR adversarial review findings M2 + I3. Do NOT
    # copy the original of this comment back into research.py: it read
    # "CPython can later reuse this dead loop's freed id() for an unrelated
    # new loop - connection_for() would then hand that new loop a
    # connection actually bound to the dead one", and BOTH halves are
    # false. (1) id()-reuse has been structurally impossible since the
    # cache key became the loop OBJECT rather than id(loop), which holds a
    # strong reference. (2) An aiosqlite.Connection is not "bound to" any
    # loop in the pinned 0.22.1 - _execute creates its future on whatever
    # loop is CALLING. The try/finally is still necessary, for the leak
    # reason stated above; only its rationale was wrong. The shipped
    # comment in services/research/research.py is the corrected one.]
    try:
        return await diagnostics.run_offline(cfg, now=now)
    finally:
        await _aio_db.close_for_current_loop()

diagnostics_report = asyncio.run(_diagnostics_and_cleanup())
```

Then use `diagnostics_report` in the function's final return dict in place of the old inline call.

- [x] **Step 7: Fix `tests/test_research.py`'s `_patch_every_analyzer()` helper (systemic breakage — adversarial review finding B, 2026-09-01)**

Read `tests/test_research.py` line 102's `_patch_every_analyzer()` helper first — it monkeypatches `research.diagnostics.run_offline` as a plain synchronous `lambda cfg, now=None: {"overall": "ok"}`. 11 of this file's ~17 tests call this helper (confirmed by grep at review time), including every test that exercises `build_report()`/`run_and_store()`/`_maybe_run_research()` directly or indirectly. Once Step 6 makes `build_report()` `await diagnostics.run_offline(...)` (inside its own `asyncio.run()` wrapper), awaiting this lambda's plain dict return value raises `TypeError: object dict can't be used in 'await' expression` in every one of those 11 tests. Fix the helper itself, once:

```python
# before (inside _patch_every_analyzer, roughly)
monkeypatch.setattr(research.diagnostics, "run_offline", lambda cfg, now=None: {"overall": "ok"})
# after
async def _fake_run_offline(cfg, now=None):
    return {"overall": "ok"}
monkeypatch.setattr(research.diagnostics, "run_offline", _fake_run_offline)
```

(Exact surrounding syntax may differ slightly from this sketch — match `_patch_every_analyzer()`'s real current structure when editing, don't paste this block verbatim without checking it against the file first.)

- [x] **Step 8: Add route-level test coverage for the two converted `services/diagnostics/routes.py` routes (adversarial review finding G, 2026-09-01)**

Neither `GET /api/diagnostics` nor `GET /api/diagnostics/series/{series}` has any existing test coverage anywhere in `tests/` (confirmed at review time — `tests/test_diagnostics_routes.py` exists but covers different routes in the same file, `get_index_settlement`/`get_account_diagnostics`). Add to `tests/test_diagnostics_routes.py`, following that file's existing test-client/fixture conventions (read a couple of its current tests first to match its setup pattern exactly):

```python
def test_get_diagnostics_returns_run_offline_shape(client):  # adjust fixture name to match this file's actual convention
    resp = client.get("/api/diagnostics")
    assert resp.status_code == 200
    body = resp.json()
    assert "overall" in body
    assert "checks" in body


def test_get_series_watcher_returns_all_four_sections(client):
    resp = client.get("/api/diagnostics/series/KXBTC15M")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"funnel", "reconcile", "book_context", "capture"}
```

These exist specifically so the `await` additions in Step 5 are verified by durable CI coverage, not only by Task 7's manual curl-based smoke test.

- [x] **Step 9: Delete `_diagnostics_pool.py`**

```bash
git rm services/diagnostics/_diagnostics_pool.py
```

Grep to confirm nothing else references it: `grep -rn "_diagnostics_pool" services/ tests/ main.py` — expected: zero hits (this task's Step 4 already removed `quality/routes.py`'s import). If `tests/test_diagnostics_pool.py` (or similar) exists and only tests this deleted module directly, delete it too; if it also covers something still live, keep the still-live parts and remove only the deleted-module coverage.

Deletion is safe not primarily because `run_offline()`'s target DB files (`signal_log.db`, `paper_broker.db`, `market_catalog.db`, `config_performance.db`, `series_watcher.db`) don't overlap with `tick_executor`'s trading-critical writers (`candidate_log.db`, `candidate_ledger.db`) — `_diagnostics_pool.py` was never on `tick_executor`'s pool to begin with, it was already its own dedicated 2-worker pool (its own docstring: "not services.tick_executor's shared one"), so that non-overlap was never the risk this deletion removes. The real reason: after this plan, `run_offline()`'s entire call graph runs natively on the asyncio event loop via `aiosqlite` — no thread pool involvement anywhere, dedicated or shared — so there is nothing left for a dedicated isolation pool to isolate. (Also worth being explicit rather than glossing over: `paper_broker.db` is one of `run_offline()`'s most heavily-read targets and is arguably the single most trading-critical file in the app; its absence from the tick_executor-sharing list above is about which OTHER writers share a thread pool with it, not about `paper_broker.db` itself being low-stakes.)

- [x] **Step 10: Run every affected test file**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/aiosqlite-diagnostics-whale-scoring && python -m pytest tests/test_diagnostics.py tests/test_quality_routes.py tests/test_diagnostics_routes.py tests/test_research.py -v"`
Expected: PASS across all of it.

- [x] **Step 11: Commit**

```bash
git add services/diagnostics/diagnostics.py services/quality/routes.py services/diagnostics/routes.py services/research/research.py tests/
git rm services/diagnostics/_diagnostics_pool.py
git commit -m "feat: wire run_offline() as async end-to-end, delete _diagnostics_pool.py"
```

---

### Task 7: Full-suite verification, live smoke test, PR

**Files:** none (verification only)

- [x] **Step 1: Run the full targeted test surface**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/aiosqlite-diagnostics-whale-scoring && python -m pytest tests/test_aio_db.py tests/test_diagnostics.py tests/test_series_watcher.py tests/test_quality_routes.py tests/test_diagnostics_routes.py tests/test_research.py -v"`
Expected: PASS, 0 failures. Per CLAUDE.md, the full suite is CI's job (Woodpecker), not this session's — this targeted run is what a local pre-push check should cover.

- [x] **Step 2: `ddev restart` so the container image actually has `aiosqlite` baked in from `requirements.txt`** (not just the `pip install` used for local iteration in Task 1)

Run from the primary checkout (not the worktree — `ddev` commands run from the primary root per CLAUDE.md): `ddev restart`

- [x] **Step 3: Live smoke test against the running app**

Per CLAUDE.md's "Start investigations here" list and the `run` skill:
- `curl -sk -m 10 https://kalshi-whale-poc.ddev.site:8443/api/quality/summary -w "\ntime_total=%{time_total}\n"` — expect a normal (<2s) response, not the 30s hang this plan's own investigation reproduced live before the fix.
- `curl -sk -m 10 https://kalshi-whale-poc.ddev.site:8443/api/diagnostics -w "\ntime_total=%{time_total}\n"`
- `curl -sk -m 10 "https://kalshi-whale-poc.ddev.site:8443/api/diagnostics/series/KXBTC15M" -w "\ntime_total=%{time_total}\n"`
- Fire 5 concurrent requests at `/api/quality/summary` (`for i in 1 2 3 4 5; do curl -sk -m 15 .../api/quality/summary -o /dev/null -w "%{time_total}\n" & done; wait`) and confirm none of them time out and `/api/health/pipeline`'s `last_tick_duration_sec` doesn't spike during the burst — the actual, direct test of "no longer blocking the event loop" this whole plan exists to achieve.
- `GET /api/health/faults` — confirm no new fault class appears post-restart.

Done, and this exact 5-concurrent burst test is what surfaced the real
live incident this plan's Task 7 was meant to catch: 5 concurrent
`GET /api/quality/summary` calls stalled an unrelated `GET /api/state` for
minutes. That incident became its own fast-follow branch
(`fix/run-offline-cooperative-yield`, PR #424) rather than a fix folded
back into this already-merged PR — see that PR's own review trail
(`docs/superpowers/specs/2026-09-02-run-offline-cooperative-yield-*.md`)
for the full investigation, an elastic connection pool that was tried and
proven (by measurement) to regress the incident further, and the query-
bound fix that actually resolved it.

- [x] **Step 4: `superpowers:requesting-code-review` + this repo's "nothing advances on one pass" cycle**

Per CLAUDE.md's HARD RULE: this PR carries new logic (a new dependency, a new connection-cache module, an architectural change to how 2 files do I/O) — self-review, then a fresh memory-less adversarial-review `Agent` call re-deriving every load-bearing claim from source (not this plan's own tables), then a consolidation doc reconciling both into a GO/no-go, each its own artifact under `docs/superpowers/specs/`. Do not open the PR's merge step until that cycle says GO.

- [x] **Step 5: Push, open PR, label, merge per `.claude/rules/branching-and-ci.md`**

`git push -u origin fix/aiosqlite-diagnostics-widening`, `gh pr create`, confirm real Woodpecker CI status via `gh api repos/thesneakattack/kalshi-whale-poc/commits/<sha>/status` (never assume from the push alone), run the PR-stage review cycle from Step 4 again against the PR as actually pushed, then `gh pr merge --merge`.

Merged as PR #420.

- [x] **Step 6: Record the deferred findings**

Recorded in `docs/open-decisions.md` (the unscoped `check_confidence_input_coverage` query, closed 2026-09-02 by PR #424) and in this plan's own review artifacts under `docs/superpowers/specs/2026-09-01-event-loop-blocking-fix2-*.md`.

Add to `docs/open-decisions.md`: (1) `check_series_funnel`'s double computation of `reconcile()`+`funnel()` (Task 5 Step 6's note) — compute-redundancy, not event-loop-blocking, separate from this plan; (2) confirm issue #410 and the `resolved_signals_with_factors()`/`population_gate_summary()` unbounded-query findings are still accurately described there (this plan didn't touch either, only stopped them from blocking the event loop where they're called from newly-async code).
