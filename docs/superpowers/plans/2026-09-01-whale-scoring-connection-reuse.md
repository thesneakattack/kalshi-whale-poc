# Write-Path Capacity Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `docs/next-action.md`'s top item — the write path can't sustain full
legitimate trade volume — via two independently-revertible fixes under one
architectural principle: nothing non-critical shares `services.tick_executor`'s
2-worker pool (or Python's shared default executor) with trading-critical work.
(1) Eliminate the per-trade fresh-SQLite-connection overhead in the whale-scoring
pipeline, on its own dedicated pool. (2) Isolate `run_offline()`'s diagnostics work
(currently sharing `tick_executor` with trading-critical writes) onto its own
dedicated pool too. Both are needed together — see the spec's section 1b for why
isolating diagnostics alone doesn't fully explain the broader event-loop-stall
symptom without the whale-scoring fix too.

**Architecture:** Fix 1 (Tasks 1-6): a dedicated 4-worker pool
(`services/whalewatchers/_scoring_pool.py`) replaces `asyncio.to_thread` for
`kalshi_trade_tape.py`'s per-trade scoring work (both the WS-message path and the
candidate-retry path); each worker thread caches one open, WAL-mode SQLite
connection per database file (`signal_log.db`, `market_history.db`,
`market_analyst.db`) instead of opening and tearing one down on every call. Fix 2
(Task 8): a dedicated 2-worker pool (`services/diagnostics/_diagnostics_pool.py`)
replaces `tick_executor.run()` for `quality/routes.py`'s `run_offline()` call —
isolation only, no connection caching or query changes.

**Tech Stack:** Python 3, `sqlite3` (stdlib), `concurrent.futures.ThreadPoolExecutor`
(stdlib) — no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-01-whale-scoring-connection-reuse-design.md`
(revision 3 — both fixes, both independently self-reviewed and adversarially
reviewed through a full NO-GO→revised cycle each).

## Global Constraints

- No change to what any of the four scoring functions compute, or to
  `_process_trades_sync`'s gates/ordering — this is a connection-lifecycle and
  execution-context change only.
- The new pool is genuinely separate from both Python's default `asyncio.to_thread`
  executor and `services.tick_executor`'s own pool — never merge them.
- Cached connections use Python's 5.0s default busy timeout (no explicit `timeout=`),
  matching every target module's existing `_connect()` — never `tick_executor`'s 50ms.
- Every existing caller of `signal_log._connect()`/`market_history._connect()`/
  `market_analyst_agent._connect()` outside the four scoring functions is untouched.

---

## Task 1: `services/whalewatchers/_scoring_pool.py` — dedicated pool + connection cache

**Files:**
- Create: `services/whalewatchers/_scoring_pool.py`
- Test: `tests/test_whalewatchers_scoring_pool.py` (new)

**Interfaces:**
- Produces: `async def run(fn: Callable[[], T]) -> T`, `def cached_read_connection(db_path: Path, schema_init: Callable[[sqlite3.Connection], None]) -> sqlite3.Connection`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_whalewatchers_scoring_pool.py
import sqlite3
import threading

import pytest

from services.whalewatchers import _scoring_pool


@pytest.mark.asyncio
async def test_run_executes_on_a_worker_thread_not_the_event_loop():
    result_thread_name = {}

    def _work():
        result_thread_name["name"] = threading.current_thread().name

    await _scoring_pool.run(_work)
    assert result_thread_name["name"].startswith("whale-scoring")


def test_cached_read_connection_reuses_same_object_within_one_thread(tmp_path):
    db_path = tmp_path / "test.db"
    init_calls = []

    def schema_init(conn):
        init_calls.append(1)
        conn.execute("CREATE TABLE IF NOT EXISTS t (x INTEGER)")

    conn1 = _scoring_pool.cached_read_connection(db_path, schema_init)
    conn2 = _scoring_pool.cached_read_connection(db_path, schema_init)
    assert conn1 is conn2
    assert len(init_calls) == 1  # schema_init only ran on the first call


def test_cached_read_connection_is_thread_local(tmp_path):
    db_path = tmp_path / "test.db"
    conns = {}

    def grab(key):
        conns[key] = _scoring_pool.cached_read_connection(db_path, lambda c: None)

    t1 = threading.Thread(target=grab, args=("a",))
    t2 = threading.Thread(target=grab, args=("b",))
    t1.start(); t1.join()
    t2.start(); t2.join()
    assert conns["a"] is not conns["b"]


def test_cached_read_connection_uses_default_busy_timeout(tmp_path):
    db_path = tmp_path / "test.db"
    conn = _scoring_pool.cached_read_connection(db_path, lambda c: None)
    # sqlite3's C-level default is 5.0s when no timeout= is passed to connect()
    row = conn.execute("PRAGMA busy_timeout").fetchone()
    assert row[0] == 5000  # milliseconds
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python -m pytest tests/test_whalewatchers_scoring_pool.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.whalewatchers._scoring_pool'`

- [ ] **Step 3: Write the implementation**

```python
# services/whalewatchers/_scoring_pool.py
"""Dedicated worker pool + thread-local connection cache for
kalshi_trade_tape.py's per-trade scoring work (both the WS-message path,
_process_stream_trade -> fetch_signals, and the candidate-retry path,
score_recovered_trade). See docs/superpowers/specs/2026-09-01-whale-scoring-
connection-reuse-design.md for the full design.

Deliberately its own pool, not services.tick_executor's shared one (also
used by candidate_ledger.claim()/record_decision(), which gate every whale
signal) and not Python's default asyncio.to_thread executor (shared
process-wide with unrelated work, unbounded up to 20 threads on this
container). 4 workers: this call path normally needs ~1 concurrently (the
WS consumer drains one queue item at a time) - headroom for legitimate
brief overlap plus the candidate-retry path, not a load-bearing capacity
guess. If issue #145/#150 (a timed-out handler's OS thread isn't actually
freed - separate, already-tracked, not fixed here) keeps happening, all 4
workers eventually get stuck and further scoring work queues (a visible
backlog/latency symptom) rather than spawning unbounded new OS threads and
connections silently - bounded and observable is this design's actual
goal, not eliminating #145/#150 itself.

Connections cached here use Python's 5.0s default busy timeout (no
explicit timeout= to sqlite3.connect()), matching signal_log.py/
market_history.py/market_analyst_agent/_db.py's own _connect() - not
services.tick_executor.connection_for()'s 50ms, tuned for a different
(write-capable) context."""
import asyncio
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="whale-scoring")
_local = threading.local()


async def run(fn: Callable[[], T]) -> T:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, fn)


def cached_read_connection(db_path: Path, schema_init: Callable[[sqlite3.Connection], None]) -> sqlite3.Connection:
    """schema_init runs once, only on this thread's first connect to this
    db_path - never on a cache hit."""
    cache = getattr(_local, "connections", None)
    if cache is None:
        cache = _local.connections = {}
    conn = cache.get(db_path)
    if conn is None:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        schema_init(conn)
        cache[db_path] = conn
    return conn
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python -m pytest tests/test_whalewatchers_scoring_pool.py -v`
Expected: PASS (all four tests)

- [ ] **Step 5: Commit**

```bash
git add services/whalewatchers/_scoring_pool.py tests/test_whalewatchers_scoring_pool.py
git commit -m "feat: add dedicated worker pool + connection cache for whale-scoring reads"
```

---

## Task 2: `services/signal_log.py` — scoring-read helper via the cache

**Files:**
- Modify: `services/signal_log.py:35-150` (extract schema DDL from `_connect()` into a reusable `_init_schema()`; add a new scoring-read helper)
- Modify: `services/signal_log.py:242-296` (`recent_sides_for_ticker`, `cluster_factor` — use the new helper)
- Test: `tests/test_signal_log.py` (extend existing file — confirmed present)

**Interfaces:**
- Consumes: `services.whalewatchers._scoring_pool.cached_read_connection` (Task 1).
- Produces: `signal_log._scoring_read_connection() -> sqlite3.Connection`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_signal_log.py`:

```python
def test_recent_sides_for_ticker_uses_the_scoring_cache(monkeypatch):
    calls = []
    real = signal_log._scoring_pool.cached_read_connection

    def spy(db_path, schema_init):
        calls.append(db_path)
        return real(db_path, schema_init)

    monkeypatch.setattr(signal_log._scoring_pool, "cached_read_connection", spy)
    signal_log.recent_sides_for_ticker("KXTEST-25", since_ts=0)
    assert calls == [signal_log.DB_PATH]


def test_scoring_read_connection_and_plain_connect_see_the_same_committed_data():
    # A row written via the plain, event-loop-side _connect() path must be
    # immediately visible through the cached scoring-read path - same file,
    # same WAL, no staleness introduced by caching.
    signal_log.log_signal("KXTEST-VIS", "yes", 100, 0.9, "test", 12345.0)
    sides = signal_log.recent_sides_for_ticker("KXTEST-VIS", since_ts=0)
    assert "yes" in sides
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python -m pytest tests/test_signal_log.py -k scoring_cache -v`
Expected: FAIL — `AttributeError: module 'services.signal_log' has no attribute '_scoring_pool'`

- [ ] **Step 3: Write the implementation**

In `services/signal_log.py`, extract the schema-creation body currently inside
`_connect()` (everything from the `CREATE TABLE IF NOT EXISTS signals (...)` call at
line 58 through the last `_add_column_if_missing`/`CREATE INDEX` call at line 150)
into a new function, and have both `_connect()` and the new scoring-read helper call
it:

```python
from services.whalewatchers import _scoring_pool

def _init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS signals (
        ...  # exact existing body, moved verbatim from _connect(), lines 58-150
        """
    )
    # ... every existing CREATE INDEX / _add_column_if_missing call, unchanged, moved here


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    _init_schema(conn)
    return conn


def _scoring_read_connection() -> sqlite3.Connection:
    """Cached connection for the four whale-scoring read functions only
    (recent_sides_for_ticker, cluster_factor here; momentum in
    market_history.py, analyst_lean in market_analyst_agent - same pattern
    in all three modules). Every other caller of this module keeps using
    _connect() unchanged."""
    DB_PATH.parent.mkdir(exist_ok=True)
    return _scoring_pool.cached_read_connection(DB_PATH, _init_schema)
```

Then change `recent_sides_for_ticker` and `cluster_factor` (lines 250, 280) from
`with _connect() as conn:` to `conn = _scoring_read_connection()` (no `with` — the
connection is cached and reused, never closed at the end of one call; `conn.execute(...)`
directly, same as today otherwise).

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python -m pytest tests/test_signal_log.py -k scoring_cache -v`
Expected: PASS

- [ ] **Step 5: Run the full signal_log test suite for a regression check**

Run: `ddev exec -s fastapi python -m pytest tests/test_signal_log.py -v`
Expected: PASS — schema creation behavior is identical (same DDL, just factored into
a shared function), every other function unchanged.

- [ ] **Step 6: Commit**

```bash
git add services/signal_log.py tests/test_signal_log.py
git commit -m "feat: signal_log's whale-scoring reads use the cached scoring connection"
```

---

## Task 3: `services/market_history.py` — scoring-read helper for `momentum()`

**Files:**
- Modify: `services/market_history.py:57-90ish` (extract schema DDL from `_connect(db_path)` into `_init_schema()`; add scoring-read helper)
- Modify: `services/market_history.py`'s `momentum()` (confirm exact current line via `grep -n "^def momentum" services/market_history.py` before editing — this plan's earlier citation of line 194 is from the design spec's investigation, re-confirm at implementation time since Task 2's edit doesn't touch this file but other work may have landed on `main` since)
- Test: `tests/test_market_history.py` (extend existing file)

**Interfaces:**
- Consumes: `_scoring_pool.cached_read_connection` (Task 1).
- Produces: `market_history._scoring_read_connection() -> sqlite3.Connection`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_market_history.py`, mirroring Task 2's two tests exactly
(substituting `market_history`/`momentum`/its own DB_PATH and a real recorded
snapshot in place of `signal_log`/`recent_sides_for_ticker`/`log_signal`):

```python
def test_momentum_uses_the_scoring_cache(monkeypatch):
    calls = []
    real = market_history._scoring_pool.cached_read_connection

    def spy(db_path, schema_init):
        calls.append(db_path)
        return real(db_path, schema_init)

    monkeypatch.setattr(market_history._scoring_pool, "cached_read_connection", spy)
    market_history.momentum("KXTEST-25", lookback_sec=300, as_of=12345.0)
    assert len(calls) == 1


def test_scoring_read_connection_and_plain_connect_see_the_same_committed_data():
    market_history.record_snapshot_from_ticker(
        "KXTEST-VIS", 0.55, spread=0.02, volume_24h=1000.0, close_time=None, now=12345.0,
    )
    result = market_history.momentum("KXTEST-VIS", lookback_sec=300, as_of=12346.0)
    assert result is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python -m pytest tests/test_market_history.py -k scoring_cache -v`
Expected: FAIL — `AttributeError: module 'services.market_history' has no attribute '_scoring_pool'`

- [ ] **Step 3: Write the implementation**

Same pattern as Task 2: extract `_connect(db_path)`'s DDL body into
`_init_schema(conn)`, have `_connect()` call it, add:

```python
from services.whalewatchers import _scoring_pool

def _scoring_read_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(exist_ok=True)
    return _scoring_pool.cached_read_connection(db_path, _init_schema)
```

Change `momentum()`'s connection acquisition from `with _connect(db_path) as conn:`
to `conn = _scoring_read_connection(db_path)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python -m pytest tests/test_market_history.py -k scoring_cache -v`
Expected: PASS

- [ ] **Step 5: Run the full market_history test suite for a regression check**

Run: `ddev exec -s fastapi python -m pytest tests/test_market_history.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add services/market_history.py tests/test_market_history.py
git commit -m "feat: market_history's momentum() uses the cached scoring connection"
```

---

## Task 4: `services/market_analyst_agent/_db.py` — scoring-read helper for `analyst_lean()`

**Files:**
- Modify: `services/market_analyst_agent/_db.py:16-40ish` (extract schema DDL into `_init_schema()`; add scoring-read helper)
- Modify: `services/market_analyst_agent/per_market.py`'s `analyst_lean()` (uses the new helper instead of `_connect()`)
- Test: `tests/test_market_analyst_agent.py` or wherever `analyst_lean` is currently tested (`grep -rn "def analyst_lean\|analyst_lean(" tests/*.py` to find the right file before editing)

**Interfaces:**
- Consumes: `_scoring_pool.cached_read_connection` (Task 1).
- Produces: `market_analyst_agent._db._scoring_read_connection() -> sqlite3.Connection`.

- [ ] **Step 1: Write the failing tests**

Add to the test file located above, mirroring Task 2/3's pattern:

```python
def test_analyst_lean_uses_the_scoring_cache(monkeypatch):
    from services.market_analyst_agent import _db
    calls = []
    real = _db._scoring_pool.cached_read_connection

    def spy(db_path, schema_init):
        calls.append(db_path)
        return real(db_path, schema_init)

    monkeypatch.setattr(_db._scoring_pool, "cached_read_connection", spy)
    market_analyst_agent.per_market.analyst_lean("KXTEST-25")
    assert len(calls) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python -m pytest tests/test_market_analyst_agent.py -k scoring_cache -v` (adjust path to the file found above)
Expected: FAIL

- [ ] **Step 3: Write the implementation**

Same pattern as Tasks 2/3, in `services/market_analyst_agent/_db.py`:

```python
from services.whalewatchers import _scoring_pool

def _scoring_read_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    return _scoring_pool.cached_read_connection(DB_PATH, _init_schema)
```

`per_market.py`'s `analyst_lean()` imports and calls `_db._scoring_read_connection()`
instead of `_db._connect()`.

- [ ] **Step 4: Run test to verify it passes**

Run the same command as Step 2.
Expected: PASS

- [ ] **Step 5: Run the full market_analyst_agent test suite for a regression check**

Run: `ddev exec -s fastapi python -m pytest tests/test_market_analyst*.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add services/market_analyst_agent/_db.py services/market_analyst_agent/per_market.py tests/
git commit -m "feat: market_analyst_agent's analyst_lean() uses the cached scoring connection"
```

---

## Task 5: `kalshi_trade_tape.py` — run scoring work on the dedicated pool

**Files:**
- Modify: `services/whalewatchers/kalshi_trade_tape.py`'s `fetch_signals()` (replace `asyncio.to_thread`)
- Modify: `services/whalewatchers/kalshi_trade_tape.py`'s `score_recovered_trade()` (make `async`)
- Test: `tests/test_whalewatchers_kalshi_trade_tape.py` (extend existing file — confirmed present per `tests/test_whalewatchers_kalshi_trade_tape.py` referenced earlier in this session's investigation)

**Interfaces:**
- Consumes: `_scoring_pool.run` (Task 1).
- Produces: `score_recovered_trade` signature changes from `def ... -> list[WhaleSignal]` to `async def ... -> list[WhaleSignal]` — Task 6 is the one caller that must change to match.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_whalewatchers_kalshi_trade_tape.py`:

```python
@pytest.mark.asyncio
async def test_fetch_signals_runs_scoring_on_the_dedicated_pool(monkeypatch):
    pool_calls = []
    real_run = kalshi_trade_tape._scoring_pool.run

    async def spy(fn):
        pool_calls.append(fn)
        return await real_run(fn)

    monkeypatch.setattr(kalshi_trade_tape._scoring_pool, "run", spy)
    provider = KalshiTradeTapeProvider()
    await provider.fetch_signals(market_context={"markets": [], "trade_tape": [], "cfg": {}})
    assert len(pool_calls) == 1


@pytest.mark.asyncio
async def test_score_recovered_trade_is_now_async():
    import inspect
    provider = KalshiTradeTapeProvider()
    assert inspect.iscoroutinefunction(provider.score_recovered_trade)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python -m pytest tests/test_whalewatchers_kalshi_trade_tape.py -k "dedicated_pool or now_async" -v`
Expected: FAIL

- [ ] **Step 3: Write the implementation**

In `fetch_signals()`, replace:
```python
signals, started, finished = await asyncio.to_thread(
    self._process_trades_timed, trade_tape, markets, markets_by_ticker, cfg, now, counts,
)
```
with:
```python
signals, started, finished = await _scoring_pool.run(
    lambda: self._process_trades_timed(trade_tape, markets, markets_by_ticker, cfg, now, counts),
)
```
(add `from services.whalewatchers import _scoring_pool` to this file's imports).

Change `score_recovered_trade`'s signature from `def score_recovered_trade(self, trade, market, cfg, now) -> list[WhaleSignal]:` to `async def score_recovered_trade(self, trade, market, cfg, now) -> list[WhaleSignal]:`, and its body from a direct call to `self._process_trades_sync(...)` to
`return await _scoring_pool.run(lambda: self._process_trades_sync([trade], [market], {ticker: market}, cfg, now))`.

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python -m pytest tests/test_whalewatchers_kalshi_trade_tape.py -k "dedicated_pool or now_async" -v`
Expected: PASS

- [ ] **Step 5: Run the full kalshi_trade_tape test suite for a regression check**

Run: `ddev exec -s fastapi python -m pytest tests/test_whalewatchers_kalshi_trade_tape.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add services/whalewatchers/kalshi_trade_tape.py tests/test_whalewatchers_kalshi_trade_tape.py
git commit -m "feat: kalshi_trade_tape runs scoring work on the dedicated whale-scoring pool"
```

---

## Task 6: `services/candidate_retry.py` — await the now-async `score_recovered_trade`

**Files:**
- Modify: `services/candidate_retry.py:167`
- Test: `tests/test_candidate_retry.py` (extend existing file)

**Interfaces:**
- Consumes: Task 5's `score_recovered_trade` (now `async`).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_candidate_retry.py` (or confirm an existing `run_pending` test
already exercises this call site and extend it rather than duplicating fixture
setup — check `grep -n "score_recovered_trade" tests/test_candidate_retry.py` first):

```python
@pytest.mark.asyncio
async def test_run_pending_awaits_score_recovered_trade(monkeypatch):
    called = []

    async def fake_score(trade, market, cfg, now):
        called.append(True)
        return []

    # wire fake_score onto whatever provider fixture run_pending's own tests
    # already use, matching that file's existing setup pattern
    ...
    await candidate_retry.run_pending(...)
    assert called == [True]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python -m pytest tests/test_candidate_retry.py -k awaits_score_recovered -v`
Expected: FAIL — `TypeError: object list can't be used in 'await' expression` (or
similar, since before this task's Step 3, `run_pending` doesn't await the call at all
and `score_recovered_trade` is still sync until Task 5 lands, which precedes this task)

- [ ] **Step 3: Write the implementation**

In `services/candidate_retry.py:167`, change:
```python
for signal in provider.score_recovered_trade(trade, market, cfg, now):
```
to:
```python
for signal in await provider.score_recovered_trade(trade, market, cfg, now):
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python -m pytest tests/test_candidate_retry.py -k awaits_score_recovered -v`
Expected: PASS

- [ ] **Step 5: Run the full candidate_retry test suite for a regression check**

Run: `ddev exec -s fastapi python -m pytest tests/test_candidate_retry.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add services/candidate_retry.py tests/test_candidate_retry.py
git commit -m "fix: candidate_retry awaits score_recovered_trade now that it's async"
```

---

## Task 7: Required measured validation (spec section 6) — before this is trusted

**Not a code task — this is the empirical safety gate the spec's section 6 requires
before merge, per `services.tick_executor.connection_for()`'s own documented bar
("proven safe by real measurement of lock-contention frequency under load"), since
none of the three target files qualifies for the alternative (single-writer
restructuring).**

- [ ] **Step 1: Capture a pre-change baseline**

On `main` (before this branch merges), with the app running under normal live
paper-trading load for at least 10 minutes: read `GET /api/observability/summary`
and record `whale_pipeline.stage.provider.window_avg_ms`/`window_max_ms`,
`whale_pipeline.stage.handler_total.window_avg_ms`/`window_max_ms`, and
`GET /api/health/pipeline`'s `last_tick_duration_sec` and
`ingest.queue_health.handler_timeouts_total`.

- [ ] **Step 2: Deploy this branch and capture a post-change reading**

Same reads, same conditions, at least 10 minutes of real activity after this
branch's changes are running.

- [ ] **Step 3: Compare and decide**

`provider`/`handler_total` window averages should drop meaningfully (the whole
point of removing repeated connection setup); `last_tick_duration_sec` and
`handler_timeouts_total`'s growth rate should not be worse than the baseline.
`GET /api/health/faults` should show no new `capture_writer`/`candidate_log`
lock-contention faults attributable to this branch specifically (cross-check
timestamps against this branch's deploy time). If a scripted burst test comparable
to the 2026-09-01 incident's measured volume is feasible without touching live
`min_contracts_by_series` thresholds (re-raising those is explicitly out of scope —
spec section 2), run one; if not, note in the commit/PR that this was validated
under normal load only, honestly, rather than silently skipping the burst case.

- [ ] **Step 4: Record the result**

Whatever the outcome, write it into `docs/next-action.md` (this item's current
top entry) and `docs/open-decisions.md` if anything remains open — per this repo's
own standing rule that nothing that matters lives only in chat.

---

## Task 8: `services/diagnostics/_diagnostics_pool.py` — isolate `run_offline()` from `tick_executor`

**Files:**
- Create: `services/diagnostics/_diagnostics_pool.py`
- Modify: `services/quality/routes.py:81`
- Test: `tests/test_diagnostics_pool.py` (new)

**Interfaces:**
- Produces: `async def run(fn: Callable[[], T]) -> T`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_diagnostics_pool.py
import threading

import pytest

from services.diagnostics import _diagnostics_pool


@pytest.mark.asyncio
async def test_run_executes_on_a_dedicated_diagnostics_thread():
    result_thread_name = {}

    def _work():
        result_thread_name["name"] = threading.current_thread().name

    await _diagnostics_pool.run(_work)
    assert result_thread_name["name"].startswith("diagnostics")


@pytest.mark.asyncio
async def test_two_concurrent_calls_do_not_serialize_on_one_worker():
    import asyncio
    import time

    def _slow():
        time.sleep(0.2)
        return time.monotonic()

    start = time.monotonic()
    await asyncio.gather(_diagnostics_pool.run(_slow), _diagnostics_pool.run(_slow))
    elapsed = time.monotonic() - start
    assert elapsed < 0.35  # both ran concurrently on the pool's 2 workers, not queued behind each other
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python -m pytest tests/test_diagnostics_pool.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.diagnostics._diagnostics_pool'`

- [ ] **Step 3: Write the implementation**

```python
# services/diagnostics/_diagnostics_pool.py
"""Dedicated worker pool for services/diagnostics/diagnostics.py's
run_offline() - see docs/superpowers/specs/2026-09-01-whale-scoring-
connection-reuse-design.md section 1b/4a for why this needs its own
pool, not services.tick_executor's shared one: run_offline()'s
per-series raw_trades aggregate queries (services/series_watcher.py's
funnel()) plus check_confidence_input_coverage()'s unscoped signal_log
fetch (docs/open-decisions.md, 2026-09-01) grew expensive enough to
permanently occupy both of tick_executor's 2 workers - starving the
trading-critical writes that pool exists to protect (confirmed live,
2026-09-01: both workers in futex_do_wait for 5h10m+, capture_writer
lock faults recurring, reproducing again within ~11 minutes of a fresh
process restart).

2 workers: sized for realistic known concurrent callers of
GET /api/quality/summary - the dashboard's own 5s poll, plus this
repo's own .claude/hooks/guard_workflow.py and CLAUDE.md routing
sessions to this exact endpoint as their first investigation step,
plus tools/quality_coordination.py. Not tick_executor's 2 (shared with
trading-critical work, the problem being fixed) or the whale-scoring
pool's 4 (a different, higher-frequency workload). If 2 isn't enough,
that shows up as measurable backlog on this pool specifically, not a
guess to get exactly right on the first try."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypeVar

T = TypeVar("T")

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="diagnostics")


async def run(fn: Callable[[], T]) -> T:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, fn)
```

In `services/quality/routes.py:81`, change:
```python
"diagnostics": await tick_executor.run(lambda: diagnostics.run_offline(cfg)),
```
to:
```python
"diagnostics": await _diagnostics_pool.run(lambda: diagnostics.run_offline(cfg)),
```
(add `from services.diagnostics import _diagnostics_pool` to this file's imports;
confirm whether `tick_executor` is still imported/used elsewhere in this file before
removing that import — `grep -n "tick_executor" services/quality/routes.py` first).

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python -m pytest tests/test_diagnostics_pool.py -v`
Expected: PASS (both tests)

- [ ] **Step 5: Run the full quality-routes test suite for a regression check**

Run: `ddev exec -s fastapi python -m pytest tests/test_quality*.py -v`
Expected: PASS — pure call-site swap, `run_offline()`'s own behavior is unchanged
(confirmed read-only by its own existing test, `test_diagnostics.py::test_run_offline_never_writes_to_any_db`).

- [ ] **Step 6: Live validation (spec section 6's diagnostics bullets) — required, not optional**

With the app running: probe `tick_executor`'s own worker availability (submit a
cheap no-op via `tick_executor.run(lambda: None)` and time it) while a
`run_offline()`-triggering `GET /api/quality/summary` call is in flight, before and
after this change. Before: expect visible delay (this is the bug, reproducing it
confirms the mechanism). After: the probe should return promptly regardless of
`quality/summary`'s own state. This is the direct, mechanistic proof of isolation —
not an inference from fewer faults alone.

- [ ] **Step 7: Update the stale cross-reference**

In `docs/open-decisions.md`, add a note to the 2026-09-01 Task 9 entry
(`resolved_signals_with_factors()`'s unscoped fetch) that this fix resolves its
"eats into tick_executor capacity" framing as a side effect — the underlying
per-call cost is unchanged and the entry's own query-bounding question stays open,
but the specific concern about shared-pool duty cycle no longer applies once
`run_offline()` runs on its own pool.

- [ ] **Step 8: Commit**

```bash
git add services/diagnostics/_diagnostics_pool.py services/quality/routes.py tests/test_diagnostics_pool.py docs/open-decisions.md
git commit -m "fix: isolate run_offline() onto its own pool, stop it starving tick_executor's critical-path work"
```

---

## Plan self-review

**Spec coverage:** Section 1/1a (root cause + the missed `score_recovered_trade`
path) → Tasks 5, 6. Section 3 (why not `connection_for()`) → Task 1's module
docstring carries the reasoning forward so a future reader of the code, not just the
spec, sees it. Section 4 (the full design: dedicated pool, three modules'
scoring-read helpers, `is_new`/`schema_init` mechanism) → Tasks 1-5. Section 5
(resilience — corrected to target `ProgrammingError`, not file-relocation) —
**not yet a task**, flagged below as a gap this self-review found. Section 6 (the
required measured validation) → Task 7. Section 7/8 (rollback, self-review) — no
dedicated task needed, properties of the other tasks.

**Gap found in this self-review, fixed inline rather than left for later:** section
5's retry-on-`ProgrammingError` fallback was in the spec but never made it into a
task above. Adding it now as part of Task 1 rather than a ninth task, since it
belongs in `cached_read_connection` itself:

```python
def cached_read_connection(db_path: Path, schema_init: Callable[[sqlite3.Connection], None]) -> sqlite3.Connection:
    cache = getattr(_local, "connections", None)
    if cache is None:
        cache = _local.connections = {}
    conn = cache.get(db_path)
    if conn is not None:
        try:
            conn.execute("SELECT 1")
            return conn
        except sqlite3.ProgrammingError:
            del cache[db_path]  # evict a connection closed out from under us elsewhere, fall through to reopen
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    schema_init(conn)
    cache[db_path] = conn
    return conn
```

Task 1's Step 1 tests need one more case for this, added here rather than
re-numbering the whole task:

```python
def test_cached_read_connection_recovers_from_a_closed_connection(tmp_path):
    db_path = tmp_path / "test.db"
    conn1 = _scoring_pool.cached_read_connection(db_path, lambda c: None)
    conn1.close()  # simulate the connection being closed out from under the cache
    conn2 = _scoring_pool.cached_read_connection(db_path, lambda c: None)
    assert conn2 is not conn1
    conn2.execute("SELECT 1")  # must be usable, not the stale closed one
```

**Placeholder scan:** every code block is real, runnable code against this repo's
actual current source (schema-extraction tasks reference exact existing line ranges
rather than requiring the DDL to be retyped, since it's moved verbatim, not
rewritten — this is a scoping decision stated explicitly, not a placeholder).

**Type consistency:** `_scoring_pool.run`/`cached_read_connection` (Task 1) are
imported and called identically in Tasks 2-5. `score_recovered_trade`'s
`async def` change (Task 5) matches exactly what Task 6 expects at its one call
site (verified directly against current source during the design phase, not
assumed). Every module's new `_scoring_read_connection()` helper follows the same
name and signature convention across Tasks 2-4.

**Task 8 addendum (spec revision 3):** covers section 1b/4a — the diagnostics pool,
2 workers (not 1, per that section's own adversarial review correcting an unverified
concurrency assumption), isolation-only (no connection caching, deliberately
different in shape from Tasks 1-6 since the underlying mechanism is pool-sharing,
not connection overhead). Step 7's `docs/open-decisions.md` update closes the loop
on a cross-reference the spec's own adversarial review found missing on first draft,
rather than leaving that entry stale once this ships. Task 7 (measured validation)
and Task 8 are independently gate-able — Task 8 doesn't depend on Task 7's outcome,
and either fix can ship without the other, though the spec's section 1b explains why
both are needed for the full symptom picture.
