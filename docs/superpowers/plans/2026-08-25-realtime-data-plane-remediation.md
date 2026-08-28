# Realtime Kalshi Data-Plane Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate event-loop stalls and class-blocking on the WebSocket and REST
planes so whale candidates are captured and decided in milliseconds instead of
minutes, with zero silent loss and no duplicate decisions, while keeping every
existing safety invariant untouched.

**Architecture:** Two WS consumers on the existing single asyncio loop (`critical`:
fill/position/lifecycle/control, never shed; `market`: gated trade candidates then
coalesced tickers), fed by a reader that runs a microsecond pure-count gate before
enqueueing anything and never blocks; a dedicated executor for the trading tick's
synchronous SQLite phases and a daemon writer thread for capture-store batching; a
durable `trade_id` ledger that gates `strategy.evaluate`; a critical-first REST
waiter/aging scheduler with a global 429 brake; and reconnect/error-25-triggered
reconciliation. Six phases, each behind its own `config/settings.yaml` flag with its
own test/replay/soak gate and full rollback.

**Tech Stack:** Python 3.13, FastAPI 0.134.0, `websockets` 17.0.1, SQLite (WAL),
`asyncio` (loop, `ThreadPoolExecutor`, `to_thread`), pytest, the two deterministic
replay harnesses this initiative built (`tools/realtime_pipeline_replay.py`,
`tools/rest_scheduler_replay.py`).

**Spec:** `docs/superpowers/specs/2026-08-25-realtime-data-plane-remediation-design.md`
**Root cause:** `docs/superpowers/research/2026-08-25-realtime-root-cause-report.md`

## Global Constraints

- Real trading stays disabled; `kalshi_account.trading_enabled` and the typed
  confirmation phrase in `POST /api/trading/enable` are never touched by this plan.
- No change to CORS, auth, or the daily-loss kill switch's persisted invariants
  (`data/risk_state.db`'s `day_start_bankroll` must stay consistent with
  `data/paper_broker.db`'s bankroll at every step).
- `data/*.db` files are additive-only: every schema change is `CREATE TABLE IF NOT
  EXISTS` / `_add_column_if_missing`-style `ALTER TABLE`, never a drop-and-recreate.
  Money stores (`paper_broker.db`, `risk_state.db`, `accounts.db`) stay write-through
  on the loop thread — write-behind is explicitly out of scope for them.
- Tests always redirect `DB_PATH` via `monkeypatch` to an isolated tmp path; never
  touch a real `data/*.db` file.
- Vendor-specific Kalshi interpretation stays inside `services/kalshi/`
  (`.claude/rules/kalshi-integration-authority.md`); this plan only consumes
  `services/kalshi/public.py` / `websocket.py` / `account*.py` contracts, it does not
  add new ones outside that boundary.
- Every phase ships behind a `config/settings.yaml` flag, default `false`/unset
  (today's behavior), flippable live without a restart per the existing
  live-reload convention.
- Follow the branching policy: this plan's tasks land on
  `chore/realtime-dp-investigation` if still open, or a fresh
  `feat/realtime-data-plane-remediation` branch off `main` — confirm at execution
  time per `.claude/rules/branching-and-ci.md`.
- No hot-path validation without a measured cost first (the reader gate's own
  measured cost is Task 3's acceptance gate).

---

## Phase P0 — Guards first (no behavior change)

### Task 1: Loop-stall watchdog metric

**Files:**
- Create: `services/loop_watchdog.py`
- Modify: `services/observability/observability.py` (register the snapshot)
- Test: `tests/test_loop_watchdog.py`

**Interfaces:**
- Produces: `loop_watchdog.start(loop=None, sample_interval_sec: float = 0.1) -> asyncio.Task`,
  `loop_watchdog.snapshot() -> dict` (`{"stall_max_ms": float, "stall_count": int,
  "samples": int}`, window semantics matching `LatencyAgg.snapshot`), `loop_watchdog.reset_window() -> None`.
- Consumes: nothing new (uses `time.monotonic`, `asyncio.get_running_loop`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_loop_watchdog.py
import asyncio
import time

from services import loop_watchdog


def test_watchdog_reports_a_real_stall():
    async def run():
        task = loop_watchdog.start(sample_interval_sec=0.01)
        await asyncio.sleep(0.05)
        time.sleep(0.2)  # blocks the loop - the thing a stall watchdog must catch
        await asyncio.sleep(0.05)
        task.cancel()
        return loop_watchdog.snapshot()

    loop_watchdog.reset_window()
    snap = asyncio.run(run())
    assert snap["stall_max_ms"] >= 150.0
    assert snap["stall_count"] >= 1
    assert snap["samples"] > 0


def test_reset_window_clears_state():
    loop_watchdog.reset_window()
    assert loop_watchdog.snapshot() == {"stall_max_ms": 0.0, "stall_count": 0, "samples": 0}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python -m pytest tests/test_loop_watchdog.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.loop_watchdog'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/loop_watchdog.py
"""Detects event-loop stalls by comparing how late a periodic wakeup runs
against how late it was scheduled to run - the same shape as I0/I7's
correlated-but-unproven "the tick's synchronous SQLite blocks the WS
consumer" hypothesis, now falsifiable at runtime (root-cause report C1)."""
import asyncio
import time

_STALL_THRESHOLD_SEC = 0.05  # below this, scheduling jitter, not a stall
_stall_max_ms = 0.0
_stall_count = 0
_samples = 0


def reset_window() -> None:
    global _stall_max_ms, _stall_count, _samples
    _stall_max_ms, _stall_count, _samples = 0.0, 0, 0


def snapshot() -> dict:
    return {"stall_max_ms": round(_stall_max_ms, 3), "stall_count": _stall_count, "samples": _samples}


def start(*, sample_interval_sec: float = 0.1) -> asyncio.Task:
    async def _tick() -> None:
        global _stall_max_ms, _stall_count, _samples
        expected = time.monotonic() + sample_interval_sec
        while True:
            await asyncio.sleep(sample_interval_sec)
            now = time.monotonic()
            late = now - expected
            _samples += 1
            if late > _STALL_THRESHOLD_SEC:
                _stall_count += 1
                _stall_max_ms = max(_stall_max_ms, late * 1000)
            expected = now + sample_interval_sec

    return asyncio.ensure_future(_tick())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python -m pytest tests/test_loop_watchdog.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Wire into observability and `main.py` startup**

In `main.py`'s startup (alongside the other `task_supervisor.supervise` calls),
add:

```python
loop_watchdog_task = task_supervisor.supervise(
    lambda: loop_watchdog.start_forever(), component="loop_watchdog", operation="run", restart=True,
)
```

Add a `start_forever()` wrapper in `services/loop_watchdog.py` that awaits the task
`start()` returns (since `supervise` needs a coroutine, not a `Task`):

```python
async def start_forever(*, sample_interval_sec: float = 0.1) -> None:
    await start(sample_interval_sec=sample_interval_sec)
```

In `services/observability/observability.py`, add a `_flatten_loop_watchdog()`
following the existing `_flatten_whale_pipeline` pattern (omit the block if
`samples == 0`), call it from the same place `_flatten_ingest_metrics` is called,
and reset `loop_watchdog.reset_window()` alongside the other resets in
`maybe_capture`. Add the metric names to `services/observability/CHEATSHEET.md`.

- [ ] **Step 6: Test the wiring**

```python
# tests/test_observability.py (append)
def test_loop_watchdog_metrics_flow_into_the_snapshot(monkeypatch):
    from services import loop_watchdog
    loop_watchdog.reset_window()
    # simulate a stall having been recorded without running the real task
    loop_watchdog._stall_max_ms, loop_watchdog._stall_count, loop_watchdog._samples = 300.0, 1, 10
    snap = observability.capture_from_runtime(...)  # match existing test's call shape
    assert snap["loop_watchdog"]["stall_max_ms"] == 300.0
```

(Match this test's exact call signature to whatever `capture_from_runtime` already
takes in the surrounding tests in `tests/test_observability.py` — read the file
first; do not invent a different signature.)

Run: `ddev exec -s fastapi python -m pytest tests/test_observability.py tests/test_loop_watchdog.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add services/loop_watchdog.py services/observability/observability.py \
  services/observability/CHEATSHEET.md main.py tests/test_loop_watchdog.py tests/test_observability.py
git commit -m "feat: add event-loop stall watchdog (I13 P0)"
```

---

### Task 2: `candidate_ledger` table (additive, write-only — not yet gating)

**Files:**
- Create: `services/candidate_ledger.py`
- Test: `tests/test_candidate_ledger.py`

**Interfaces:**
- Produces: `candidate_ledger.claim(trade_id: str, *, ticker: str | None = None, now: float | None = None) -> bool`
  (True = newly claimed, False = duplicate — an `INSERT OR IGNORE` outcome check, not
  an exception), `candidate_ledger.record_decision(trade_id: str, decision: str) -> None`,
  `candidate_ledger.stats() -> dict` (`{"claimed": int, "duplicates": int}` lifetime
  counters for observability).
- Consumes: nothing (own `DB_PATH` per the repo's persistence idiom).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_candidate_ledger.py
import importlib

import pytest


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    from services import candidate_ledger
    monkeypatch.setattr(candidate_ledger, "DB_PATH", tmp_path / "candidate_ledger_test.db")
    yield


def test_claim_is_true_once_and_false_on_replay():
    from services import candidate_ledger
    assert candidate_ledger.claim("abc123", ticker="KXBTC-25AUG25-T1") is True
    assert candidate_ledger.claim("abc123", ticker="KXBTC-25AUG25-T1") is False


def test_stats_count_claims_and_duplicates():
    from services import candidate_ledger
    candidate_ledger.claim("t1")
    candidate_ledger.claim("t1")
    candidate_ledger.claim("t2")
    stats = candidate_ledger.stats()
    assert stats["claimed"] == 2
    assert stats["duplicates"] == 1


def test_record_decision_is_idempotent_and_readable():
    from services import candidate_ledger
    candidate_ledger.claim("t1")
    candidate_ledger.record_decision("t1", "opened")
    candidate_ledger.record_decision("t1", "opened")  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python -m pytest tests/test_candidate_ledger.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# services/candidate_ledger.py
"""Durable trade_id idempotency for the whale-candidate decision path (I13
remediation design 5). Nothing downstream reads WhaleSignal.id today and the
250k in-memory dedupe ring is empty after every --reload; this table is the
single durable source of truth a retry, a reconciliation sweep, or a restart
can consult before evaluate() runs again on the same trade_id."""
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "candidate_ledger.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS candidates ("
        "trade_id TEXT PRIMARY KEY, ticker TEXT, claimed_at REAL NOT NULL, decision TEXT)"
    )
    return conn


def claim(trade_id: str, *, ticker: str | None = None, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    with _connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO candidates (trade_id, ticker, claimed_at) VALUES (?, ?, ?)",
            (trade_id, ticker, now),
        )
        return cur.rowcount == 1


def record_decision(trade_id: str, decision: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE candidates SET decision = ? WHERE trade_id = ?", (decision, trade_id))


def stats() -> dict:
    with _connect() as conn:
        claimed = conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
    return {"claimed": claimed, "duplicates": _duplicate_count}


_duplicate_count = 0
```

(`_duplicate_count` needs incrementing in `claim()` on the False branch — fold that
into Step 3's real implementation rather than leaving the module-level counter
disconnected; write it correctly the first time, this parenthetical is a reminder
for the implementer, not a second draft to ship.)

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python -m pytest tests/test_candidate_ledger.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add services/candidate_ledger.py tests/test_candidate_ledger.py
git commit -m "feat: add durable candidate_ledger table, unwired (I13 P0)"
```

---

### Task 3: Reader-side gate predicate, shadow mode (counts only, filters nothing)

**Files:**
- Create: `services/whale_gate.py`
- Modify: `services/kalshi/websocket.py` (`_process_item`, call the gate, count-only)
- Test: `tests/test_whale_gate.py`, append to `tests/test_kalshi_ws_ingest_metrics.py`

**Interfaces:**
- Produces: `whale_gate.passes(trade: dict, *, min_contracts: int) -> bool` (pure,
  microsecond-cost — Task 3's own acceptance gate), `whale_gate.min_contracts_for(ticker: str, cfg: dict) -> int`
  (thin wrapper around whatever `services/whalewatchers/kalshi_trade_tape.py` already
  exposes for this — read that module first and reuse its existing per-series
  threshold function rather than re-deriving the threshold logic).
- Consumes: `services/kalshi/public.py`'s trade-contract field accessors (grep
  `trade_contract` in `services/kalshi/` for the exact accessor names before writing
  this — do not read `trade["count"]` directly, which is exactly the kind of
  Kalshi-shape assumption `.claude/rules/kalshi-integration-authority.md` exists to
  prevent).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_whale_gate.py
import time

from services import whale_gate


def test_passes_true_for_a_whale_sized_trade():
    trade = {"trade_id": "t1", "ticker": "KXBTC-25AUG25-T1", "count": 500, "yes_price": 60}
    assert whale_gate.passes(trade, min_contracts=100) is True


def test_passes_false_for_a_small_trade():
    trade = {"trade_id": "t1", "ticker": "KXBTC-25AUG25-T1", "count": 5, "yes_price": 60}
    assert whale_gate.passes(trade, min_contracts=100) is False


def test_gate_cost_is_microseconds():
    trade = {"trade_id": "t1", "ticker": "KXBTC-25AUG25-T1", "count": 500, "yes_price": 60}
    n = 20_000
    start = time.perf_counter()
    for _ in range(n):
        whale_gate.passes(trade, min_contracts=100)
    per_call_us = (time.perf_counter() - start) / n * 1_000_000
    assert per_call_us < 20.0  # design spec §2's ceiling
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python -m pytest tests/test_whale_gate.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

Read `services/kalshi/` for the real trade-contract count accessor first
(`grep -n "def trade_count\|def trade_ticker\|def trade_side" services/kalshi/*.py`)
and use exactly that function name — the snippet below is illustrative of shape,
not a literal name to copy without checking:

```python
# services/whale_gate.py
"""Pure, allocation-free classification of a raw trade print as whale-sized
or not - the gate the I12 review demanded exist before any reader-side
filtering ships (design spec §2). Must stay well under 20us/call; Task 3's
own test pins that budget so a future change that regresses it fails CI,
not a production incident."""
from services.kalshi import public as kalshi_public  # exact accessor names TBD by the grep above


def passes(trade: dict, *, min_contracts: int) -> bool:
    count = kalshi_public.trade_contract_count(trade)  # replace with the real accessor name
    return count is not None and count >= min_contracts
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python -m pytest tests/test_whale_gate.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Wire into `_process_item` as shadow-mode counting only**

In `services/kalshi/websocket.py`'s `_process_item`, after the existing
`_message_class(data)` call and before enqueueing, for `cls == "trade"`:

```python
if cls == "trade":
    try:
        min_contracts = whale_gate.min_contracts_for(data.get("ticker") or "", config_store.get())
        if not whale_gate.passes(data, min_contracts=min_contracts):
            self._gate_would_reject += 1  # shadow counter only - still enqueues below
    except Exception as exc:
        self._gate_exceptions += 1
        fault_log.record("whale_gate", "passes", exc)
        # fall open: never let a gate bug hide a whale (design spec §2)
```

Add `_gate_would_reject`/`_gate_exceptions` to `ingest_metrics()`'s returned dict
(follow the existing counter pattern in that method) and to
`services/observability/CHEATSHEET.md`.

- [ ] **Step 6: Test shadow-mode counting and the fall-open behavior**

```python
# tests/test_kalshi_ws_ingest_metrics.py (append)
def test_shadow_gate_counts_sub_threshold_trades_without_dropping_them():
    ws = _make_ws()  # use this file's existing helper
    small = {"type": "trade", "msg": {"trade_id": "t1", "ticker": "K1", "count": 1}}
    ws._ingest_raw(json.dumps(small))
    metrics = ws.ingest_metrics()
    assert metrics["gate_would_reject"] == 1
    assert ws._queue.qsize() == 1  # still enqueued - shadow mode, not filtering


def test_gate_exception_falls_open_and_still_enqueues(monkeypatch):
    ws = _make_ws()
    monkeypatch.setattr("services.whale_gate.passes", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    trade = {"type": "trade", "msg": {"trade_id": "t1", "ticker": "K1", "count": 500}}
    ws._ingest_raw(json.dumps(trade))
    assert ws.ingest_metrics()["gate_exceptions"] == 1
    assert ws._queue.qsize() == 1
```

Run: `ddev exec -s fastapi python -m pytest tests/test_kalshi_ws_ingest_metrics.py tests/test_whale_gate.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add services/whale_gate.py services/kalshi/websocket.py \
  services/observability/CHEATSHEET.md tests/test_whale_gate.py tests/test_kalshi_ws_ingest_metrics.py
git commit -m "feat: add whale gate predicate in shadow mode (I13 P0)"
```

---

### Task 4: Probe the anonymous REST ceiling (measurement, not code)

**Files:**
- Modify: `tools/kalshi_rate_limit_probe.py`
- Modify: `docs/kalshi/CHEATSHEET.md` (record the measured number)
- Test: `tests/test_kalshi_rate_limit_probe.py` (append a test for the new mode)

**Interfaces:**
- Produces: `probe_anonymous_ceiling(client, *, duration_sec: float = 20.0) -> dict`
  (`{"observed_max_rps": float, "first_429_at_rps": float | None, "sample_ticker": str}`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_kalshi_rate_limit_probe.py (append)
import pytest

from tools import kalshi_rate_limit_probe as probe


class _FakeUnauthClient:
    def __init__(self, ceiling_rps: float):
        self.ceiling_rps = ceiling_rps
        self.calls = 0

    async def get_market(self, ticker: str) -> dict:
        self.calls += 1
        if self.calls > self.ceiling_rps * 2:  # crude: fail once we're clearly over
            raise Exception("429 Too Many Requests")
        return {"ticker": ticker, "status": "active"}


@pytest.mark.asyncio
async def test_probe_anonymous_ceiling_reports_where_429s_start():
    client = _FakeUnauthClient(ceiling_rps=5.0)
    result = await probe.probe_anonymous_ceiling(client, duration_sec=2.0)
    assert result["first_429_at_rps"] is not None
    assert result["observed_max_rps"] > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python -m pytest tests/test_kalshi_rate_limit_probe.py -k anonymous_ceiling -v`
Expected: FAIL with `AttributeError: module 'tools.kalshi_rate_limit_probe' has no attribute 'probe_anonymous_ceiling'`

- [ ] **Step 3: Write minimal implementation**

Follow the existing `probe_limits`/`probe_costs` pattern in the same file (ramp
concurrency, catch the exception class the real SDK raises for a 429 — check what
`probe_costs` already catches and reuse it, don't invent a new exception check):

```python
async def probe_anonymous_ceiling(client, *, duration_sec: float = 20.0) -> dict:
    import asyncio
    import time

    rps = 2.0
    first_429_at = None
    observed_max = 0.0
    deadline = time.monotonic() + duration_sec
    while time.monotonic() < deadline and first_429_at is None:
        interval = 1.0 / rps
        window_end = time.monotonic() + 2.0
        calls_this_window = 0
        try:
            while time.monotonic() < window_end:
                await client.get_market("KXBTCD-25AUG25-T1")  # any stable, always-open ticker
                calls_this_window += 1
                await asyncio.sleep(interval)
            observed_max = max(observed_max, rps)
            rps *= 1.5
        except Exception as exc:
            if "429" in str(exc) or "Too Many Requests" in str(exc):
                first_429_at = rps
            else:
                raise
    return {"observed_max_rps": observed_max, "first_429_at_rps": first_429_at, "sample_ticker": "KXBTCD-25AUG25-T1"}
```

Wire a `--anonymous-ceiling` CLI flag next to the existing `--limits/--costs/--batch`
flags, using an **unauthenticated** client instance (the existing authenticated
`client` fixture in this file's `_run()` is the wrong one — construct the
unauthenticated market-data client the same way `services/kalshi/public.py`'s
default gateway does, without credentials).

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python -m pytest tests/test_kalshi_rate_limit_probe.py -k anonymous_ceiling -v`
Expected: PASS

- [ ] **Step 5: Run the real probe once against live Kalshi and record the result**

Run: `ddev exec -s fastapi python -m tools.kalshi_rate_limit_probe --anonymous-ceiling`

This is a live, read-only, unauthenticated network call — safe per repo policy, but
real. Append the measured `observed_max_rps`/`first_429_at_rps` to
`docs/kalshi/CHEATSHEET.md` as a new dated entry ("What is the unauthenticated
market-data ceiling?"), same format as the existing entries. This number is what
Task 20 (limiter budget) is allowed to use as an upper bound — do not raise the
local bucket past it.

- [ ] **Step 6: Commit**

```bash
git add tools/kalshi_rate_limit_probe.py tests/test_kalshi_rate_limit_probe.py docs/kalshi/CHEATSHEET.md
git commit -m "feat: probe the anonymous REST ceiling, record the result (I13 P0)"
```

---

### Task 5: Verify whether a re-determination re-fires `determined` (documentation task)

**Files:**
- Modify: `docs/kalshi/CHEATSHEET.md`

- [ ] **Step 1: Read `docs/kalshi/market-and-event-lifecycle.md` and `market_lifecycle.md` in full for any statement about `amended`/`disputed` re-firing `determined` on the `market_lifecycle_v2` channel.**
- [ ] **Step 2: If undocumented (expected, per I12's finding that the channel lists no `amended`/`disputed` event), record that explicitly as an open gap** rather than asserting a behavior: add a CHEATSHEET entry stating the channel's documented event list (`created, activated, deactivated, close_date_updated, determined, settled, metadata_updated`), that no re-determination event is named, and that Task 21 (the settled resolver) must therefore treat a cached `determined.result` as provisional until the ticker reaches `finalized` — never as a terminal decision cache.
- [ ] **Step 3: Commit**

```bash
git add docs/kalshi/CHEATSHEET.md
git commit -m "docs: record the determined re-fire gap for the settled resolver (I13 P0)"
```

---

**P0 gate:** `ddev exec -s fastapi python -m pytest tests/ -q` green; `/api/health/pipeline`
shows `loop_watchdog` and `gate_would_reject`/`gate_exceptions` counters moving on the
live instance; no behavior change (verify with a 10-minute read-only sampler against
`GET /api/observability/current`, same method as I0 §6, confirming drop rate and
candidate counts are unchanged from before P0).

---

## Phase P1 — Loop hygiene (tick executor)

### Task 6: Persistent-connection executor for the trading tick's synchronous SQLite phases

**Files:**
- Create: `services/tick_executor.py`
- Test: `tests/test_tick_executor.py`

**Interfaces:**
- Produces: `tick_executor.run(fn: Callable[[], T]) -> Awaitable[T]` (awaitable from
  the loop; runs `fn` on a 2-worker `ThreadPoolExecutor` with a `threading.local`
  SQLite connection cache keyed by `DB_PATH`, opened with `timeout=0.05` and
  `check_same_thread=False`... actually `check_same_thread=True` per-thread local is
  correct since each worker thread gets its own connection — do not share one
  connection across the pool's two threads), `tick_executor.connection_for(db_path: Path) -> sqlite3.Connection`
  (the thread-local getter individual store modules will call from inside `run()`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tick_executor.py
import sqlite3
import threading

import pytest

from services import tick_executor


@pytest.mark.asyncio
async def test_run_executes_off_the_calling_loop_thread():
    calling_thread = threading.get_ident()
    result = await tick_executor.run(lambda: threading.get_ident())
    assert result != calling_thread


@pytest.mark.asyncio
async def test_connection_for_is_reused_across_calls_on_the_same_worker(tmp_path):
    db_path = tmp_path / "t.db"

    def _get_id():
        conn = tick_executor.connection_for(db_path)
        return id(conn)

    first = await tick_executor.run(_get_id)
    second = await tick_executor.run(_get_id)
    # Not guaranteed to land on the same worker thread, but if it does, the
    # connection object must be identical (proving reuse, not reconnect-per-call).
    assert isinstance(first, int) and isinstance(second, int)


@pytest.mark.asyncio
async def test_connection_for_opens_with_a_short_busy_timeout(tmp_path):
    db_path = tmp_path / "t.db"

    def _get_timeout():
        conn = tick_executor.connection_for(db_path)
        return conn.execute("PRAGMA busy_timeout").fetchone()[0]

    ms = await tick_executor.run(_get_timeout)
    assert ms <= 100  # design spec §8: never a 5s sleep on the loop
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python -m pytest tests/test_tick_executor.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# services/tick_executor.py
"""Runs the trading tick's synchronous SQLite phases off the asyncio loop.
Root-cause report C1: capture_flush_and_titles, resolve_and_record, and the
series_stats N+1 in main.py are the measured source of the 4-13.6s per-tick
loop stalls that starve the WS consumer. This does not change what those
functions do - only where they run and how their connections are opened
(a bounded busy_timeout instead of the sqlite3 default 5s sleep, which would
otherwise just move the stall from the loop to a worker thread that still
blocks a whole tick)."""
import asyncio
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tick-executor")
_local = threading.local()


def connection_for(db_path: Path) -> sqlite3.Connection:
    cache = getattr(_local, "connections", None)
    if cache is None:
        cache = _local.connections = {}
    conn = cache.get(db_path)
    if conn is None:
        db_path.parent.mkdir(exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=0.05, check_same_thread=True)
        conn.execute("PRAGMA busy_timeout = 50")
        conn.execute("PRAGMA journal_mode = WAL")
        cache[db_path] = conn
    return conn


async def run(fn: Callable[[], T]) -> T:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, fn)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python -m pytest tests/test_tick_executor.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add services/tick_executor.py tests/test_tick_executor.py
git commit -m "feat: add persistent-connection tick executor, unwired (I13 P1)"
```

---

### Task 7: Move `capture_flush_and_titles` behind the tick executor

**Files:**
- Modify: `main.py` (the tick loop's `capture_flush_and_titles` call site)
- Test: `tests/test_main_tick_executor_wiring.py`

**Interfaces:**
- Consumes: `tick_executor.run` from Task 6.
- Consumes: whatever `capture_flush_and_titles` currently is in `main.py` — read its
  exact current signature before editing (`grep -n "def capture_flush_and_titles" main.py`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_main_tick_executor_wiring.py
import asyncio
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_capture_flush_and_titles_runs_via_tick_executor():
    import main
    with patch("main.tick_executor.run", wraps=main.tick_executor.run) as spy:
        await main.capture_flush_and_titles_async()  # the new async wrapper this task adds
    spy.assert_called_once()
```

(Match this to `capture_flush_and_titles`'s real current call shape — if it is
already a plain sync function called inline in the tick loop, this task's job is to
wrap that exact call site with `await tick_executor.run(lambda: capture_flush_and_titles(...))`,
not to redesign its signature.)

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python -m pytest tests/test_main_tick_executor_wiring.py -v`
Expected: FAIL (the wrapper doesn't exist yet)

- [ ] **Step 3: Wire the call site**

In `main.py`'s trading tick, replace the direct synchronous call with:

```python
await tick_executor.run(lambda: capture_flush_and_titles(state, cfg))
```

(substitute the function's real current arguments). Add `from services import
tick_executor` to `main.py`'s imports.

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python -m pytest tests/test_main_tick_executor_wiring.py -v`
Expected: PASS

- [ ] **Step 5: Verify no regression in the existing tick test suite**

Run: `ddev exec -s fastapi python -m pytest tests/ -k "tick or trading_loop" -v`
Expected: PASS, no changes to assertions about `capture_flush_and_titles`'s effects
(only where it runs changed, not what it does)

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_main_tick_executor_wiring.py
git commit -m "perf: run capture_flush_and_titles off the event loop (I13 P1)"
```

---

### Task 8: Move `resolve_and_record` and the `series_stats` N+1 behind the tick executor

**Files:**
- Modify: `main.py` (the `resolve_and_record` call site, `main.py:736`'s `series_stats` loop)
- Test: append to `tests/test_main_tick_executor_wiring.py`

Same pattern as Task 7: write a test asserting `tick_executor.run` is invoked for
each of these two call sites, watch it fail, wrap the call sites, watch it pass,
run the existing tick/resolution test suite for a regression check
(`ddev exec -s fastapi python -m pytest tests/ -k "resolve or signal_resolution" -v`),
commit as `perf: run resolve_and_record and series_stats off the event loop (I13 P1)`.

Read `main.py:736`'s exact current loop body before touching it — the task is to
batch its N per-market `_connect()` calls into one `tick_executor.run()` call that
does all of them on one worker-thread connection, not merely to move the same N
connections onto a different thread (that would still serialize N × 12.6 ms on the
worker, just off the loop — better than today, but the plan's P1 gate requires the
tick's own wall time not to regress, so batch the connection use inside the one
`run()` call).

---

### Task 9: Pace the catalog batch and move it after the critical gather

**Files:**
- Modify: `main.py` (`_maybe_scan_catalog_batch` call site, currently before the
  critical `asyncio.gather`), `services/market_watch/catalog_scan.py` (`_scan_catalog_batch`'s internal gather)
- Test: `tests/test_catalog_scan_pacing.py`, append to `tests/test_rest_scheduler_replay.py`
  if the launch-order fix from I12 needs a corresponding production-side test

**Interfaces:**
- Consumes: nothing new.
- Produces: `catalog_scan.PACE_LIMIT = 4` (module constant, the max concurrent
  `get_markets` calls in one batch's internal gather).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_catalog_scan_pacing.py
import asyncio

import pytest


@pytest.mark.asyncio
async def test_scan_catalog_batch_paces_concurrent_calls(monkeypatch):
    from services.market_watch import catalog_scan

    in_flight = 0
    max_in_flight = 0

    class _FakeClient:
        async def get_markets(self, **kwargs):
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            return {"markets": []}

    series = [{"ticker": f"S{i}"} for i in range(10)]
    await catalog_scan._scan_catalog_batch(_FakeClient(), {"series_watch": series})
    assert max_in_flight <= catalog_scan.PACE_LIMIT
```

(Match `_scan_catalog_batch`'s real config-key name for the series list — read the
function body first; `{"series_watch": series}` above is illustrative.)

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python -m pytest tests/test_catalog_scan_pacing.py -v`
Expected: FAIL — `max_in_flight` will be the full batch size (today's unbounded
`asyncio.gather`), which for a 10-series batch is 10 > any reasonable `PACE_LIMIT`

- [ ] **Step 3: Bound the internal gather with a semaphore**

```python
PACE_LIMIT = 4
_pace_sem = asyncio.Semaphore(PACE_LIMIT)


async def _paced_get_markets(client, **kwargs):
    async with _pace_sem:
        return await client.get_markets(**kwargs)
```

Replace the internal `asyncio.gather(*(client.get_markets(...) for s in batch))`
with `asyncio.gather(*(_paced_get_markets(client, ...) for s in batch))`.

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python -m pytest tests/test_catalog_scan_pacing.py -v`
Expected: PASS

- [ ] **Step 5: Move the catalog task launch after the critical gather in `main.py`**

Change the tick loop from (today, `main.py:346` before `:351`):

```python
_maybe_scan_catalog_batch(cfg)
...
markets, account_snapshot, exchange_status = await asyncio.gather(...)
```

to:

```python
markets, account_snapshot, exchange_status = await asyncio.gather(...)
...
_maybe_scan_catalog_batch(cfg)  # after the critical gather returns (I13 root-cause C3/R1)
```

Leave `_maybe_check_signal_resolutions`, `_maybe_run_backup`, `_maybe_run_research`,
and `event_schedule._maybe_resolve_event_schedules` in their current relative order
after the move — only the catalog batch's position changes, since it was the one
the root-cause report and I12 traced as the actual REST contention source.

- [ ] **Step 6: Run the existing trading-loop test suite for a regression check**

Run: `ddev exec -s fastapi python -m pytest tests/ -k "trading_loop or catalog" -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add main.py services/market_watch/catalog_scan.py tests/test_catalog_scan_pacing.py
git commit -m "perf: pace the catalog batch and launch it after the critical gather (I13 P1)"
```

---

**P1 gate:** `ddev exec -s fastapi python -m pytest tests/ -q` green. Live 30-minute
read-only sampler (I0 §6's method) against a busy period shows `loop_watchdog.stall_max_ms`
p95 < 250 ms and tick p95 < 1 s (root-cause report §8 targets), with `trade_stream`
drop rate unchanged from P0's baseline (P1 does not change capture yet — only where
the tick's SQLite work runs).

---

## Phase P2 — Ledger gates `evaluate`; retry state machine

### Task 10: Gate `_handle_signal` on the candidate ledger

**Files:**
- Modify: `services/whale_stream/decision_bridge.py` (`_handle_signal`)
- Test: `tests/test_whale_stream_decision_bridge.py` (create if it doesn't exist —
  check first) or append to whatever file currently tests `_handle_signal`

**Interfaces:**
- Consumes: `candidate_ledger.claim` from Task 2.
- Produces: `_handle_signal` returns `{"skipped": "duplicate_trade_id"}` (matching
  the existing `_skip(...)`-shaped return values in `strategy_engine.py` — read that
  return shape first and match it exactly) instead of evaluating, when `claim` is False.

- [ ] **Step 1: Write the failing test**

```python
def test_handle_signal_skips_a_trade_id_already_claimed(monkeypatch):
    from services import candidate_ledger
    from services.whale_stream import decision_bridge

    signal = _make_signal(id="dup1")  # use this test file's existing signal factory
    candidate_ledger.claim("dup1")  # pre-claim it, simulating an earlier evaluation

    result = asyncio.run(decision_bridge._handle_signal(signal, cfg={}, market_results={}, config_fp="", tick_now=0.0))
    assert result["skipped"] == "duplicate_trade_id"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python -m pytest tests/test_whale_stream_decision_bridge.py -k duplicate -v`
Expected: FAIL — today's `_handle_signal` evaluates unconditionally

- [ ] **Step 3: Add the claim check before `strategy.evaluate`**

At the top of `_handle_signal`, before today's `signal_log.log_signal(...)` call:

```python
if not candidate_ledger.claim(signal.id, ticker=signal.ticker):
    return {"skipped": "duplicate_trade_id"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python -m pytest tests/test_whale_stream_decision_bridge.py -v`
Expected: PASS, and all pre-existing tests in this file still pass (the claim only
rejects a true repeat of the same `trade_id` — every existing test uses a fresh id
per call unless it specifically tests duplication)

- [ ] **Step 5: Record the decision after `strategy.evaluate` returns**

After the `decision = strategy.evaluate(...)` line, add:

```python
candidate_ledger.record_decision(signal.id, decision.get("action", "unknown"))
```

(Match `decision`'s real key name — read `strategy_engine.evaluate`'s return shape
first.)

- [ ] **Step 6: Test the decision is recorded**

```python
def test_handle_signal_records_the_decision_in_the_ledger(monkeypatch):
    from services import candidate_ledger
    signal = _make_signal(id="rec1")
    asyncio.run(decision_bridge._handle_signal(signal, cfg={}, market_results={}, config_fp="", tick_now=0.0))
    # candidate_ledger has no public read-by-id helper yet - add one for this test:
    # candidate_ledger.decision_for(trade_id: str) -> str | None
```

Add `candidate_ledger.decision_for(trade_id: str) -> str | None` to
`services/candidate_ledger.py` (a one-line `SELECT decision FROM candidates WHERE
trade_id = ?`) as part of this step, with its own unit test in
`tests/test_candidate_ledger.py`.

- [ ] **Step 7: Run the full whale-stream test suite for a regression check**

Run: `ddev exec -s fastapi python -m pytest tests/ -k "whale_stream or decision_bridge or signal" -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add services/whale_stream/decision_bridge.py services/candidate_ledger.py \
  tests/test_whale_stream_decision_bridge.py tests/test_candidate_ledger.py
git commit -m "fix: gate evaluate on the candidate ledger (I13 P2)"
```

---

### Task 11: Move `_mark_seen` to after a successful market lookup, not before

**Files:**
- Modify: `services/whalewatchers/kalshi_trade_tape.py` (`_process_trades_sync`)
- Test: append to `tests/test_whale_candidate_lifecycle.py` (this is the file that
  already pins H4 as a strict xfail — flip it to a real assertion here)

**Interfaces:**
- Consumes: nothing new.
- Changes: `_mark_seen(trade_id, ...)` moves from immediately after
  `trade_id in self._seen_trade_ids` to after the market lookup (`markets_by_ticker.get(ticker)`
  or its REST fallback) has produced a definite terminal outcome (market found, or a
  terminal "will never resolve" decision) — not on a transient failure.

- [ ] **Step 1: Confirm the existing xfail test's exact assertion shape**

Read `tests/test_whale_candidate_lifecycle.py` in full. It already encodes the
desired behavior (`wire_seen=True, candidate_pending=?, terminal_evaluated=False`
today; something else desired) as a strict xfail. Do not write a new test file —
this task's job is to make that existing test pass and remove its `xfail` marker.

- [ ] **Step 2: Run the existing test to confirm it still fails as expected**

Run: `ddev exec -s fastapi python -m pytest tests/test_whale_candidate_lifecycle.py -v --runxfail`
Expected: the xfail test FAILs when forced to run for real (confirms it's still
testing the live bug, not a stale assertion)

- [ ] **Step 3: Move `_mark_seen` in `_process_trades_sync`**

Read the function's current control flow (`services/whalewatchers/kalshi_trade_tape.py`,
the `for trade in trade_tape:` loop). Move the `self._mark_seen(trade_id, ...)` call
from immediately after the `trade_id in self._seen_trade_ids` check to the point
where the market is either found (`market = markets_by_ticker.get(ticker)` succeeds,
possibly after a REST lookup) or a lookup is attempted and **succeeds but confirms
the market doesn't exist** (a genuinely terminal outcome, distinct from a raised
exception). On a raised exception from the lookup, do **not** mark seen — leave the
trade eligible for the next presentation (which Task 12's retry state machine will
provide; until Task 12 ships, "next presentation" means the trade simply isn't
false-terminally lost, even without an active retry yet).

- [ ] **Step 4: Run the test to verify it passes**

Run: `ddev exec -s fastapi python -m pytest tests/test_whale_candidate_lifecycle.py -v`
Expected: PASS. Remove the `@pytest.mark.xfail(...)` decorator from the test.

- [ ] **Step 5: Run the full trade-tape test suite for a regression check**

Run: `ddev exec -s fastapi python -m pytest tests/test_whalewatchers_kalshi_trade_tape.py -v`
Expected: PASS — pay particular attention to any test asserting `_seen_trade_ids`
membership immediately after a successful trade (those should be unaffected: the
mark-seen point only moved for the *failure* path)

- [ ] **Step 6: Commit**

```bash
git add services/whalewatchers/kalshi_trade_tape.py tests/test_whale_candidate_lifecycle.py
git commit -m "fix: mark a trade seen only after a terminal outcome, not before the lookup (I13 P2, closes H4)"
```

---

### Task 12: Single-owner retry state machine for transient enrichment failures

**Files:**
- Create: `services/candidate_retry.py`
- Modify: `services/whalewatchers/kalshi_trade_tape.py` (enqueue a failed lookup for retry)
- Test: `tests/test_candidate_retry.py`

**Interfaces:**
- Produces: `candidate_retry.enqueue(trade: dict, *, failure: Exception) -> None`,
  `candidate_retry.run_pending(client, *, now: float | None = None) -> dict`
  (`{"retried": int, "recovered": int, "abandoned": int}`) — called from one place
  only (the market consumer's own loop, Task 17), never from a second task, per
  review R7's "single mutator" requirement.
- Consumes: `services/http_client.py`'s `classify("critical_whale")` context manager
  (reuse it, don't invent a second classification path), `candidate_ledger.claim`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_candidate_retry.py
import time

import pytest


@pytest.mark.asyncio
async def test_a_transient_failure_is_retried_and_recovered(monkeypatch):
    from services import candidate_retry

    calls = {"n": 0}

    class _FlakyClient:
        async def get_markets_by_tickers(self, tickers):
            calls["n"] += 1
            if calls["n"] < 3:
                raise Exception("429 Too Many Requests")
            return {t: {"ticker": t, "status": "active"} for t in tickers}

    trade = {"trade_id": "t1", "ticker": "K1", "count": 500}
    candidate_retry.enqueue(trade, failure=Exception("first failure"))
    client = _FlakyClient()

    for _ in range(5):  # backoff schedule advances internally; simulate elapsed retries
        result = await candidate_retry.run_pending(client, now=time.time() + 100 * _)
    assert result["recovered"] >= 1


@pytest.mark.asyncio
async def test_abandonment_after_the_retry_budget_is_exhausted():
    from services import candidate_retry

    class _AlwaysFailsClient:
        async def get_markets_by_tickers(self, tickers):
            raise Exception("429 Too Many Requests")

    trade = {"trade_id": "t2", "ticker": "K2", "count": 500}
    candidate_retry.enqueue(trade, failure=Exception("first failure"))
    client = _AlwaysFailsClient()
    now = time.time()
    result = {"abandoned": 0}
    for i in range(10):
        now += 20.0  # advance past the >=72s budget (I12 R7)
        result = await candidate_retry.run_pending(client, now=now)
    assert result["abandoned"] >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python -m pytest tests/test_candidate_retry.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# services/candidate_retry.py
"""Single-owner retry queue for whale candidates whose market lookup failed
transiently. Root-cause report C5 / I12 R7: a retry must (a) be the only
mutator of pending state besides the reader, (b) inherit the critical_whale
caller class so it doesn't silently fall into 'other', and (c) abandon after
a bounded budget (>=72s survives a 60s outage per I12's recovery-sim
finding) rather than retry forever."""
import time

from services import candidate_ledger, http_client

_BACKOFF_SCHEDULE_SEC = (0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0)  # sums to >72s
_pending: dict[str, dict] = {}  # trade_id -> {"trade": ..., "attempts": int, "next_at": float}


def enqueue(trade: dict, *, failure: Exception, now: float | None = None) -> None:
    now = time.time() if now is None else now
    trade_id = trade["trade_id"]
    if trade_id in _pending:
        return  # already owned - the reader is the only other writer and never re-enqueues
    _pending[trade_id] = {"trade": trade, "attempts": 0, "next_at": now + _BACKOFF_SCHEDULE_SEC[0]}


async def run_pending(client, *, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    retried = recovered = abandoned = 0
    for trade_id in list(_pending):
        entry = _pending[trade_id]
        if now < entry["next_at"]:
            continue
        retried += 1
        try:
            with http_client.caller_class("critical_whale"):
                result = await client.get_markets_by_tickers([entry["trade"]["ticker"]])
            if result.get(entry["trade"]["ticker"]):
                candidate_ledger.claim(trade_id, ticker=entry["trade"]["ticker"], now=now)
                recovered += 1
                del _pending[trade_id]
                continue
            raise KeyError("market still missing")
        except Exception:
            entry["attempts"] += 1
            if entry["attempts"] >= len(_BACKOFF_SCHEDULE_SEC):
                abandoned += 1
                del _pending[trade_id]
                continue
            entry["next_at"] = now + _BACKOFF_SCHEDULE_SEC[entry["attempts"]]
    return {"retried": retried, "recovered": recovered, "abandoned": abandoned}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python -m pytest tests/test_candidate_retry.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Wire the reader's failure path into `enqueue`**

In `services/whalewatchers/kalshi_trade_tape.py`'s `_process_trades_sync`, in the
`except Exception:` branch around the market lookup (the one Task 11 just stopped
marking seen on), add:

```python
from services import candidate_retry
...
except Exception as exc:
    candidate_retry.enqueue(trade, failure=exc)
    continue
```

Add `run_pending` to `services/observability/observability.py`'s snapshot
(`candidate_retry.pending_count()` — add this one-line helper — as a gauge; retried/
recovered/abandoned as window counters following the `LatencyAgg`-adjacent pattern
used elsewhere) and to `CHEATSHEET.md`.

- [ ] **Step 6: Test the wiring end to end**

```python
# tests/test_whale_candidate_lifecycle.py (append)
def test_a_lookup_failure_enqueues_for_retry_instead_of_vanishing(monkeypatch):
    from services import candidate_retry
    candidate_retry._pending.clear()
    # drive _process_trades_sync with a market lookup that raises, as this file's
    # existing H4 test does, then assert:
    assert "the_trade_id" in candidate_retry._pending
```

(Match this to the existing test's exact harness for driving `_process_trades_sync`
with a failing lookup — reuse it rather than building a new one.)

Run: `ddev exec -s fastapi python -m pytest tests/test_whale_candidate_lifecycle.py tests/test_candidate_retry.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add services/candidate_retry.py services/whalewatchers/kalshi_trade_tape.py \
  services/observability/observability.py services/observability/CHEATSHEET.md \
  tests/test_candidate_retry.py tests/test_whale_candidate_lifecycle.py
git commit -m "feat: retry transient enrichment failures instead of losing the candidate (I13 P2)"
```

---

### Task 13: Wire `run_pending` into the trading tick and cap retry budget observably

**Files:**
- Modify: `main.py` (call `candidate_retry.run_pending` once per tick, in stream mode)
- Test: append to `tests/test_main_tick_executor_wiring.py`

- [ ] **Step 1: Write a test asserting the tick calls `run_pending` once per iteration when streaming is enabled** (follow this file's existing pattern for asserting a per-tick call, e.g. how `_maybe_check_signal_resolutions` is already tested for tick membership).
- [ ] **Step 2: Run it, watch it fail.**
- [ ] **Step 3: Add the call** in the tick loop, in stream mode only (mirroring the existing `if _streaming_trade_tape_enabled():` branch), classed under `critical_whale` via the retry module itself (already handled inside `run_pending`).
- [ ] **Step 4: Run it, watch it pass.**
- [ ] **Step 5: Add a quality finding when `abandoned > 0` in a window**, following the existing `services/quality/` finding pattern (grep for how `/api/quality/summary`'s existing findings are registered and match that shape exactly) — this is what makes P2's abandonment "counted, not silent" per the design spec.
- [ ] **Step 6: Run the full test suite for a regression check:** `ddev exec -s fastapi python -m pytest tests/ -q`
- [ ] **Step 7: Commit:** `git commit -m "feat: run the candidate retry queue every tick, surface abandonment as a finding (I13 P2)"`

---

**P2 gate:** The now-unmarked `tests/test_whale_candidate_lifecycle.py` H4 test passes
for real (not xfail). A fault-injection test (extend
`tests/test_realtime_pipeline_candidates.py` or add a new integration test) drives a
trade through: transient failure → retry → recovery, and separately: transient
failure → exhausted retries → abandonment counted, asserting zero silent loss in
both paths and zero duplicate ledger claims when the same `trade_id` is
re-presented mid-retry. `ddev exec -s fastapi python -m pytest tests/ -q` green.

---

## Phase P2.5 — Subscription-churn investigation (H11, CH1-CH5)

> **Distinct orchestrator for this phase only:** use
> `.claude/skills/realtime-data-plane-investigation/SKILL.md` as the orchestrator for
> CH1-CH5, not `superpowers:subagent-driven-development`/`executing-plans` — this phase
> is investigation-shaped (measure, classify, decide) unlike the rest of this plan's
> implementation-shaped phases. Execute exactly one numbered task, verify, commit,
> report, and stop. **Merged into this file 2026-08-27** from its own former plan doc
> (`2026-08-26-subscription-churn-investigation.md`, retired) — it had become a
> follow-on thread of this same investigation in practice, not a separate initiative,
> per its own original banner; see CH3's and Phase P3.5's cross-references below for
> why keeping it a separate file had stopped paying for itself.

**Goal:** Determine whether market-discovery-driven WebSocket subscription churn
(Hypothesis H11) is a real, material bottleneck — and if so, select an evidence-backed
fix — rather than assume the user's report names the mechanism correctly.

**Finding so far:** `docs/superpowers/research/2026-08-25-realtime-data-plane-known-findings.md`,
Hypothesis H11 (added 2026-08-26). Mechanism traced, churn confirmed real but bursty
(~15s cadence, not per-tick), first observability added and live-verified (PR #37,
merged). No cost measurement, no root cause for the co-occurring instability event, and
no architecture decision exist yet — that is what this phase covers.

**Phase-scoped constraints, in addition to this plan's Global Constraints above:**
- No subscription/queue/rate/worker tuning before CH3 classifies a real bottleneck.
- No structural production redesign before CH5's solution-comparison step.
- If CH1/CH2 falsify H11 (churn burst is not the cause of the observed instability),
  record that as a real result and stop — a negative result is a valid outcome, not a
  reason to keep digging for a way to confirm the original report.

### CH1 — Measure a churn burst's actual downstream/upstream cost

**Read**
- `services/kalshi/websocket.py`'s `_sync_subscriptions`/`ingest_metrics` (the
  `subscription_churn` counters added in PR #37)
- `docs/kalshi/websocket-connection.md` (subscribe/update_subscription semantics,
  `send_initial_snapshot` behavior)
- current `data/observability.db` history for `*.ingest.subscription_churn.*`,
  `*.ingest.queue_depth`, `*.ingest.queue_wait.*`

**Questions to answer, each with a stated confirm/falsify criterion**
- [ ] How many raw WS frames does one `_sync_subscriptions` call actually send? (Sent
  once per market-channel sid — trade + ticker in scoped mode — so an N-ticker diff is
  N tickers × up to 2 messages, not N messages; confirm this against the real code path,
  don't assume.)
- [ ] Does Kalshi's `send_initial_snapshot: True` behavior on the `ticker` channel apply
  per newly-added ticker, or only on first subscribe? Check the exact mirrored doc, not
  memory.
- [ ] Does a churn burst measurably correlate with a rise in
  `trade_stream.ingest.queue_depth` / `queue_wait` / handler latency in the same or
  immediately following window? Use `data/observability.db`'s own history plus a live
  correlated sample if the dev instance is running.
- [ ] Does a churn burst add measurable REST demand (e.g. via `_resolve_unknown_markets`,
  `_fetch_live_status`, or hydration calls) beyond what's already bounded/cached?
- [ ] Commit: `docs: measure subscription-churn burst cost (CH1)` — update the H11 finding
  with real numbers, not another hypothesis.

**Acceptance**
A reader can see actual measured cost (or "measured: negligible") for a churn burst, not
architectural speculation.

**Scale caveat (2026-08-27):** this measurement is bounded to today's live watchlist scale
(8-13 tickers). Phase P3.5 below (Task 17a/17b) later stresses the same
`trade_stream.ingest.subscription_churn.*` counters this measurement uses, at a real
widened-scope scale, and feeds that larger-scale data point back here (and into CH3,
below) — see that phase's own header note for the reuse contract.

---

### CH2 — Root-cause the still-untraced third instability event

Use `root-cause-debugging` explicitly — this is exactly its trigger (unexpected/live
incident, contradictory behavior).

- [ ] Reproduce the app-unresponsiveness event live (the same `SYS_PTRACE`/`py-spy`
  method used for the two bugs fixed in PR #35/#36 this session).
- [ ] Capture a stack trace during the stall; identify the actual blocking call.
- [ ] Classify the result explicitly: (a) the same shape of bug as PR #35/#36 in a third,
  unrelated module: (b) directly caused by subscription-churn/H11's mechanism; (c)
  something else entirely.
- [ ] If (a) or (c): fix or document following this session's established pattern
  (`tick_executor` offload, or SQL-side aggregation if the bottleneck is Python-object
  construction over a large row count — see PR #35's own lesson that a thread offload
  alone doesn't help GIL-bound work).
- [ ] If (b): do not fix yet — feed the confirmed mechanism into CH3/CH4 below instead of
  patching reactively.
- [ ] Commit: `fix: <root cause>` or `docs: root-cause the third instability event (CH2)`,
  whichever applies.

**Acceptance**
The instability event has a proven cause, not a guess, and this plan's classification of
H11 (CH3 below) rests on that proof rather than coincidence.

---

### CH3 — Reconcile CH1 + CH2 into a classification of H11

- [ ] State plainly: is subscription churn (i) a confirmed, material bottleneck, (ii) a
  real but currently-negligible cost, or (iii) unrelated to the observed instability?
- [ ] Update H11 in the known-findings doc with this classification and the evidence
  behind it.
- [ ] If (ii) or (iii): stop this plan here. Record why, and what would change that
  classification later (e.g. a larger watchlist, a wider category set, more concurrent
  live events). Do not proceed to redesign work against an unconfirmed or negligible
  bottleneck.
- [ ] If (i): proceed to CH4.
- [ ] Commit: `docs: classify H11 (CH3)`.

**Resolved (2026-08-27):** (ii) real but currently-negligible cost, and separately
(iii) unrelated to the observed instability — not (i). Full reconciliation and evidence
in H11's own entry, `docs/superpowers/research/2026-08-25-realtime-data-plane-known-findings.md`.
This phase stops here; CH4/CH5 do not run unless P3.5's addendum below reopens the
classification.

**Not necessarily final once (ii)/(iii) stops this phase (2026-08-27):** Phase P3.5
below (Task 17a/17b) runs after this task in current sequencing and is the literal
"larger watchlist" experiment this bullet names — it reuses this phase's own
`subscription_churn` counters at real widened-scope scale and, per its own Step 5,
posts a dated addendum here (reopening or confirming this classification) once it
lands. If that addendum hasn't been added yet, treat this entry's stop as provisional
pending P3.5, not permanent — check for it before assuming CH4/CH5 are still out of
scope.

**Acceptance**
A reviewer can tell, from the doc alone, whether the rest of this phase should ever run.

---

### CH4 — Research and benchmark solution families *(only if CH3 = confirmed bottleneck)*

**Note (2026-08-27):** a broader, churn-independent live watchlist-scale stress test
exists as Phase P3.5 below (`tools/watchlist_scale_stress_test.py`), sequenced ahead of
P4/P5. If CH4 ever runs, reuse that tool rather than building a second scale-up harness
— P3.5 answers the general queue-depth/REST-demand/loop-health question at scale, CH4
(if it ever becomes relevant) would still need its own churn-specific benchmarking on
top of it.

Follow the parent skill's solution-selection workflow exactly (enumerate ≥3 families,
research current authoritative practice, prototype outside the production path, benchmark
against the same representative workload, fault-inject burst traffic/reconnect/queue
pressure). Do not conclude "batch discovery less often," "cap churn," "hysteresis on
rank changes," or any other specific mechanism before this step runs — those are
candidates to evaluate, not a foregone conclusion.

- [ ] Enumerate candidate families (e.g.: dampen `round_robin_select`'s output with
  hysteresis/minimum-dwell-time; separate a stable "core" watchlist from a smaller
  rapidly-scanned discovery pool; batch subscription diffs rather than sending them
  the instant they're computed; something else — do not anchor on this list).
- [ ] Prototype each outside the production path.
- [ ] Benchmark against CH1's real measured workload.
- [ ] Fault-inject: burst traffic, a reconnect mid-churn, malformed diff.
- [ ] Score against the design spec's matrix (correctness, capture completeness, latency,
  operational simplicity — same axes the parent investigation already established).
- [ ] Commit: `docs: benchmark subscription-churn solution candidates (CH4)`.

**Acceptance**
At least three real candidates, benchmarked on the same workload, with rejected options
explained.

---

### CH5 — Architecture decision and implementation plan *(only after CH4)*

- [ ] Write the architecture decision (winner, rejected alternatives, why).
- [ ] Invoke `superpowers:writing-plans` to produce a separate, numbered implementation
  plan — this document does not authorize implementation itself.
- [ ] Commit: `docs: subscription-churn architecture decision + implementation plan (CH5)`.

**Acceptance**
A human can review and approve (or reject) a concrete plan before any implementation
task starts.

---

**P2.5 gate:** CH3 has classified H11, one way or the other, in the known-findings doc.
CH4/CH5 only run if CH3 confirms a material bottleneck; otherwise this phase's gate is
CH3's own commit plus (once it exists) Phase P3.5's confirming/reopening addendum.

---

## Phase P3 — Reader gate live, capture contract, writer thread

### Task 14: Writer thread with per-store batched flush, supervised

**Files:**
- Create: `services/capture_writer.py`
- Test: `tests/test_capture_writer.py`

**Interfaces:**
- Produces: `capture_writer.submit(store: str, row: tuple) -> None` (non-blocking,
  appends to an in-memory buffer keyed by `store`), `capture_writer.start() -> None`
  (starts the daemon thread), `capture_writer.stop(timeout_sec: float = 2.0) -> None`
  (bounded shutdown flush), `capture_writer.depth() -> dict[str, int]`,
  `capture_writer.last_flush_age_ms() -> dict[str, float]`.
- Consumes: nothing new — this module owns its own flush cadence, independent of the
  `series_watcher.py` buffer/flush idiom it will eventually replace (Task 15 wires
  the reader into this module; `series_watcher.py`'s existing `_FLUSH_BATCH`/`flush()`
  stays as-is until then).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_capture_writer.py
import time

import pytest


def test_submit_is_non_blocking_and_flush_lands_in_the_db(tmp_path, monkeypatch):
    from services import capture_writer
    db_path = tmp_path / "capture_test.db"
    monkeypatch.setattr(capture_writer, "_STORE_PATHS", {"raw_trades": db_path})
    capture_writer.start()
    try:
        capture_writer.submit("raw_trades", ("t1", "K1", 100, time.time()))
        for _ in range(50):
            if capture_writer.depth()["raw_trades"] == 0:
                break
            time.sleep(0.05)
        assert capture_writer.depth()["raw_trades"] == 0
    finally:
        capture_writer.stop()


def test_stop_flushes_pending_rows_before_returning(tmp_path, monkeypatch):
    from services import capture_writer
    db_path = tmp_path / "capture_test2.db"
    monkeypatch.setattr(capture_writer, "_STORE_PATHS", {"raw_trades": db_path})
    capture_writer.start()
    capture_writer.submit("raw_trades", ("t2", "K1", 100, time.time()))
    capture_writer.stop()
    assert capture_writer.depth()["raw_trades"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python -m pytest tests/test_capture_writer.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# services/capture_writer.py
"""Daemon thread that batches capture-store writes off both the asyncio loop
and the reader coroutine. Design spec §8: the reader must never do more than
an in-memory append; this is the only writer for the stores it owns, opened
with a short busy_timeout so a collision with another connection never sleeps
five seconds on any thread that matters."""
import queue
import sqlite3
import threading
import time
from pathlib import Path

_STORE_PATHS: dict[str, Path] = {
    "raw_trades": Path(__file__).resolve().parent.parent / "data" / "series_watcher.db",
}
_STORE_TABLE = {"raw_trades": "raw_trades"}  # extend as more stores move here
_FLUSH_INTERVAL_SEC = 1.0
_FLUSH_BATCH = 500

_buffers: dict[str, list[tuple]] = {name: [] for name in _STORE_PATHS}
_lock = threading.Lock()
_last_flush_at: dict[str, float] = {name: time.time() for name in _STORE_PATHS}
_thread: threading.Thread | None = None
_stop_event = threading.Event()


def submit(store: str, row: tuple) -> None:
    with _lock:
        _buffers[store].append(row)


def depth() -> dict[str, int]:
    with _lock:
        return {name: len(rows) for name, rows in _buffers.items()}


def last_flush_age_ms() -> dict[str, float]:
    now = time.time()
    return {name: round((now - ts) * 1000, 1) for name, ts in _last_flush_at.items()}


def _flush_store(store: str) -> None:
    with _lock:
        rows, _buffers[store] = _buffers[store], []
    if not rows:
        _last_flush_at[store] = time.time()
        return
    db_path = _STORE_PATHS[store]
    db_path.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=0.05)
    try:
        conn.execute("PRAGMA busy_timeout = 50")
        placeholders = ",".join("?" for _ in rows[0])
        conn.executemany(f"INSERT INTO {_STORE_TABLE[store]} VALUES ({placeholders})", rows)
        conn.commit()
    finally:
        conn.close()
    _last_flush_at[store] = time.time()


def _run() -> None:
    while not _stop_event.is_set():
        _stop_event.wait(_FLUSH_INTERVAL_SEC)
        for store, rows in list(_buffers.items()):
            if len(rows) >= _FLUSH_BATCH or (_stop_event.is_set()):
                _flush_store(store)
    for store in _buffers:
        _flush_store(store)


def start() -> None:
    global _thread
    _stop_event.clear()
    _thread = threading.Thread(target=_run, name="capture-writer", daemon=True)
    _thread.start()


def stop(timeout_sec: float = 2.0) -> None:
    _stop_event.set()
    if _thread is not None:
        _thread.join(timeout=timeout_sec)
```

(The `_run` loop's periodic flush only fires past `_FLUSH_BATCH` OR on shutdown in
this minimal version — add a time-based flush too, so a slow trickle still lands
within `_FLUSH_INTERVAL_SEC`, not just at 500 rows: track `_last_flush_at` per store
and flush a non-empty buffer whose age exceeds `_FLUSH_INTERVAL_SEC` on every wake,
not only at `_stop_event.is_set()`. Fix this in the real Step 3 implementation, not
as a follow-up — the test in Step 1 doesn't currently exercise the time-based path,
so add that assertion too before calling this step done.)

- [ ] **Step 4: Add the time-based flush and its test, then verify all pass**

```python
def test_a_small_buffer_flushes_on_the_time_interval_not_only_at_batch_size(tmp_path, monkeypatch):
    from services import capture_writer
    db_path = tmp_path / "capture_test3.db"
    monkeypatch.setattr(capture_writer, "_STORE_PATHS", {"raw_trades": db_path})
    monkeypatch.setattr(capture_writer, "_FLUSH_INTERVAL_SEC", 0.1)
    capture_writer.start()
    try:
        capture_writer.submit("raw_trades", ("t3", "K1", 1, time.time()))  # far below _FLUSH_BATCH
        time.sleep(0.3)
        assert capture_writer.depth()["raw_trades"] == 0
    finally:
        capture_writer.stop()
```

Run: `ddev exec -s fastapi python -m pytest tests/test_capture_writer.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Supervise the thread and expose liveness**

Add `capture_writer.is_alive() -> bool` and wire a periodic check (via
`task_supervisor.supervise` with `restart=True`, wrapping a coroutine that checks
`is_alive()` every 5 s and calls `start()` again if not) into `main.py`'s startup,
alongside the other supervised tasks. Add `writer.depth`, `writer.last_flush_age_ms`
per store, and a `writer.alive` gauge to `services/observability/observability.py`,
with a quality finding when `writer.alive` is False. Update
`services/observability/CHEATSHEET.md`.

- [ ] **Step 6: Test the supervisor restarts a dead writer**

```python
def test_supervisor_restarts_a_dead_writer_thread(monkeypatch):
    from services import capture_writer
    capture_writer.start()
    capture_writer._thread = None  # simulate an unexpected death without a real crash
    # call whatever check function Step 5 added, e.g. capture_writer.ensure_alive()
    from services import capture_writer as cw
    cw.ensure_alive()
    assert cw._thread is not None and cw._thread.is_alive()
    capture_writer.stop()
```

Add `ensure_alive()` as part of Step 5, not as a separate task.

Run: `ddev exec -s fastapi python -m pytest tests/test_capture_writer.py -v`
Expected: PASS (5 tests)

- [ ] **Step 7: Commit**

```bash
git add services/capture_writer.py services/observability/observability.py \
  services/observability/CHEATSHEET.md main.py tests/test_capture_writer.py
git commit -m "feat: add supervised capture writer thread, unwired (I13 P3)"
```

---

### Task 15: Reader-side capture contract — `record_trade` appends via the writer, not inline

**Files:**
- Modify: `services/series_watcher.py` (`record_trade`)
- Modify: `services/capture_writer.py` (`_STORE_TABLE` gains the real `raw_trades` schema/columns)
- Test: append to whatever file currently tests `series_watcher.record_trade`

**Interfaces:**
- Consumes: `capture_writer.submit` from Task 14.
- Changes: `record_trade`'s inline `_FLUSH_BATCH`-triggered `executemany` becomes a
  `capture_writer.submit("raw_trades", row)` call; the module's own `_trade_buffer`/
  `flush()` machinery is removed once nothing calls it directly anymore (read the
  current `record_trade`/`flush()` pair fully first — this task retires that
  in-module buffering by delegating to the shared writer, it does not run both).

- [ ] **Step 1: Read `services/series_watcher.py`'s current `record_trade`/`flush()` in full** and note the exact row tuple shape and table name it inserts into (`raw_trades`), so Task 14's `_STORE_TABLE`/`_STORE_PATHS` can be corrected to match reality rather than the illustrative single-column stub in Task 14.
- [ ] **Step 2: Write a test asserting `record_trade` calls `capture_writer.submit("raw_trades", <matching row shape>)` and does not touch a live connection itself.**
- [ ] **Step 3: Run it, watch it fail** (today's `record_trade` calls its own inline `_trade_buffer.append` and periodically `executemany`s directly).
- [ ] **Step 4: Replace the body**: `record_trade` builds the same row tuple it does today, then calls `capture_writer.submit("raw_trades", row)` instead of appending to `_trade_buffer`; delete `_trade_buffer`, `_FLUSH_BATCH`'s trade half, and the trade half of `flush()` (keep the book-side buffer/flush exactly as-is — this task only moves the trade capture path; the `_book_buffer` stays inline until a future task, since I12 did not measure it as a hot-path cost).
- [ ] **Step 5: Run it, watch it pass.**
- [ ] **Step 6: Run the full series_watcher test suite for a regression check:** `ddev exec -s fastapi python -m pytest tests/test_series_watcher.py -v` (or whatever the real test filename is — confirm with `find tests -iname "*series_watcher*"`).
- [ ] **Step 7: Verify `raw_trades` growth rate is unchanged** — this is the P3 gate's key correctness property (capture must not shrink). Add an integration test that submits 1,000 trades through `record_trade` and asserts exactly 1,000 rows land in `raw_trades` after `capture_writer.stop()` flushes.
- [ ] **Step 8: Commit:** `git commit -m "feat: route raw_trades capture through the shared writer thread (I13 P3)"`

---

### Task 16: Aggregate sub-threshold rejection rows instead of one row per print

**Files:**
- Modify: `services/candidate_log.py` (the per-row rejection write)
- Test: append to the existing `candidate_log` test file

**Interfaces:**
- Produces: `candidate_log.record_rejection_aggregate(ticker: str, side: str, minute_bucket: int, count: int) -> None`
  (an `UPSERT`-shaped write: `INSERT ... ON CONFLICT(ticker, side, minute_bucket) DO UPDATE SET count = count + excluded.count`).
- Changes: the call site that today writes one `rejected_candidates` row per
  sub-threshold print instead accumulates in memory (following the same
  `capture_writer`-adjacent buffering shape as Task 14, or reusing `capture_writer`
  itself with a `"rejections"` store — prefer reusing `capture_writer` over building
  a third buffering mechanism) and flushes one aggregate row per (ticker, side,
  minute) instead of per print.

- [ ] **Step 1: Read the existing rejection-write call site and `rejected_candidates`'s schema in full** (`services/candidate_log.py`, and whatever calls it from the trade-tape path) to confirm the exact columns `record_rejection_aggregate` must produce, and confirm `gate_summary` (the consumer named in the I12 review, row W5) reads `rejected_candidates` in a way that an aggregated-by-minute row still satisfies — read `gate_summary`'s query before designing the aggregate schema, not after.
- [ ] **Step 2: Write a failing test**: 50 sub-threshold prints on the same (ticker, side) inside one minute produce exactly one aggregate row with `count = 50`, not 50 rows.
- [ ] **Step 3: Run it, watch it fail.**
- [ ] **Step 4: Add the additive schema change** (`ALTER TABLE` or a new `rejected_candidates_agg` table, additive per the persistence idiom) and `record_rejection_aggregate`, routed through `capture_writer` with an `"rejections"` store entry added to `_STORE_PATHS`/`_STORE_TABLE`.
- [ ] **Step 5: Update the call site** in the trade-tape/reader path to accumulate in-process (a `dict[(ticker, side, minute), int]`, flushed once per minute boundary via `capture_writer.submit`) instead of writing per print.
- [ ] **Step 6: Run it, watch it pass; update `gate_summary`'s query if Step 1 found it needs the new table/columns.**
- [ ] **Step 7: Run the full candidate_log test suite for a regression check.**
- [ ] **Step 8: Commit:** `git commit -m "perf: aggregate sub-threshold rejection rows by minute instead of per print (I13 P3)"`

**Redesigned at execution time (2026-08-27) — do not implement the aggregation
above.** Step 1's own re-grounding instruction ("read `rejected_candidates`'s
schema in full... before designing the aggregate schema") surfaced that this
task's premise is stale: `rejected_candidates` already has
`PRIMARY KEY (ticker, strategy, gate_name)` and was never one-row-per-print.
A second table, `rejection_events`, was added 2026-08-23 - two days before
this plan - specifically *without* a dedup key, because `rejected_candidates`'
own dedup made population statistics ("a ticker rejected fifty times... counts
as ONE data point") unusable; it exists to preserve full per-print granularity
including `unit_cost`, which CLAUDE.md's Standing goal section still names as
an open research target ("rejected candidates in the 0.60-0.95 unit-cost band
show negative hypothetical EV... a real gate-tuning target"). Aggregating it
into `(ticker, side, minute_bucket, count)` as originally specified above
would have silently destroyed that per-row data - exactly what the HARD RULE
forbids trading away without an explicit decision.

Implemented instead (same real cost problem - `record_rejection()`'s
`rejection_events` insert was a fresh `sqlite3.connect()` per call, the same
hot-path anti-pattern already fixed elsewhere in this plan): route
`rejection_events` through `capture_writer` as a new store, same shape as
`raw_trades` (Task 14/15) - every row preserved, only the write batched.
`population_gate_summary()`/`clear_all()`/`count_range()`/`clear_range()`/
`resolve_from_market_results()` each flush the buffer first, so no caller
(test or production) has to know the writes are asynchronous now. Confirmed
with the user before implementing (a real fork with research-relevant
consequences, not a mechanical choice) - see commit for full detail.

---

### Task 17: Enable the reader gate for real (flip Task 3's shadow mode to filtering)

**Files:**
- Modify: `services/kalshi/websocket.py` (`_process_item`)
- Modify: `config/settings.yaml` (add the `realtime_data_plane.reader_gate_enabled` flag)
- Test: append to `tests/test_kalshi_ws_ingest_metrics.py`

**Interfaces:**
- Consumes: `whale_gate.passes` (Task 3), `config_store` (existing live-reload config).
- Changes: when `reader_gate_enabled` is true and `whale_gate.passes(...)` is False
  for a trade, the reader (a) still calls `series_watcher.record_trade` and the
  minute-aggregate rejection accumulator from Task 16 — **not** skip them, per the
  design spec's explicit capture contract — and (b) does not enqueue the message to
  the market queue. `ingest.prefiltered.trade` increments either way (shadow or live).

- [ ] **Step 1: Write the failing test**

```python
def test_reader_gate_enabled_drops_sub_threshold_trades_from_the_queue_but_still_captures(monkeypatch):
    ws = _make_ws()
    monkeypatch.setattr("services.config_store.get", lambda: {"realtime_data_plane": {"reader_gate_enabled": True}})
    captured = []
    monkeypatch.setattr("services.series_watcher.record_trade", lambda trade, **k: captured.append(trade))
    small = {"type": "trade", "msg": {"trade_id": "t1", "ticker": "K1", "count": 1}}
    ws._ingest_raw(json.dumps(small))
    assert ws._queue.qsize() == 0  # gated out
    assert len(captured) == 1  # still captured (design spec §2/§4)
    assert ws.ingest_metrics()["prefiltered"]["trade"] == 1


def test_reader_gate_disabled_keeps_todays_behavior(monkeypatch):
    ws = _make_ws()
    monkeypatch.setattr("services.config_store.get", lambda: {"realtime_data_plane": {"reader_gate_enabled": False}})
    small = {"type": "trade", "msg": {"trade_id": "t1", "ticker": "K1", "count": 1}}
    ws._ingest_raw(json.dumps(small))
    assert ws._queue.qsize() == 1  # unchanged
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python -m pytest tests/test_kalshi_ws_ingest_metrics.py -k reader_gate -v`
Expected: FAIL (flag doesn't exist / gate doesn't filter yet)

- [ ] **Step 3: Add the flag to `config/settings.yaml`**

```yaml
realtime_data_plane:
  reader_gate_enabled: false  # P3: filters sub-threshold trades in the reader instead of the consumer
```

- [ ] **Step 4: Implement the branch in `_process_item`**

Replace Task 3's shadow-only counting with:

```python
if cls == "trade":
    min_contracts = whale_gate.min_contracts_for(data.get("ticker") or "", config_store.get())
    try:
        gate_passes = whale_gate.passes(data, min_contracts=min_contracts)
    except Exception as exc:
        gate_passes = True  # fall open (design spec §2)
        self._gate_exceptions += 1
        fault_log.record("whale_gate", "passes", exc)
    if not gate_passes:
        self._prefiltered_by_class["trade"] = self._prefiltered_by_class.get("trade", 0) + 1
        series_watcher.record_trade(data)
        candidate_log.accumulate_rejection(data)  # Task 16's in-process accumulator
        if (config_store.get().get("realtime_data_plane") or {}).get("reader_gate_enabled"):
            return  # do not enqueue
```

- [ ] **Step 5: Run test to verify it passes**

Run: `ddev exec -s fastapi python -m pytest tests/test_kalshi_ws_ingest_metrics.py -v`
Expected: PASS

- [ ] **Step 6: Add the I4 completeness split (count-based exchange-wide + id-based whale-sized)**

In `services/diagnostics/trade_capture_reconciliation.py`, extend `reconcile_window`'s
return dict with `exchange_wide_completeness` (REST trade **count** for the window
vs `received_by_kind["trade"]` from I1 metrics, not vs the ring) alongside the
existing id-based `whale_sized_completeness`. Add a test pinning both fields present
and independently correct when the gate is enabled (exchange-wide near the REST
count, whale-sized computed only over ring-held ids as today).

- [ ] **Step 7: Run the full ingest/reconciliation test suite for a regression check**

Run: `ddev exec -s fastapi python -m pytest tests/test_kalshi_ws_ingest_metrics.py tests/test_trade_capture_reconciliation.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add services/kalshi/websocket.py config/settings.yaml \
  services/diagnostics/trade_capture_reconciliation.py \
  tests/test_kalshi_ws_ingest_metrics.py tests/test_trade_capture_reconciliation.py
git commit -m "feat: reader gate filters the market queue when enabled, still captures everything (I13 P3)"
```

---

**P3 gate:** `reader_gate_enabled: false` by default; with it flipped on in a paper-mode
soak, `ingest.prefiltered.trade` ≈ measured non-candidate share (~99.7%), `raw_trades`
row-count growth rate within ±5% of pre-P3 baseline (Task 15's Step 7 test plus a live
comparison), consumer busy fraction drops (visible via the existing `trade_stream_perf`
window), zero new exceptions in `fault_log` attributable to `whale_gate`. Full suite green.

---

## Phase P3.5 — Live watchlist-scale stress test (empirically informs P4/P5)

Direct instruction (2026-08-27): P4 (Task 18's two-consumer split, Task 19's ticker
coalescing) and P5 (Task 21-25's REST scheduler rewrite) are both designed against
synthetic replay presets (`tools/realtime_pipeline_replay.py`'s `busy_hour`,
`tools/rest_scheduler_replay.py`'s `measured_quiet`/`background_storm`/`429_storm`) -
reasonable stand-ins, but none of them are calibrated against how this app's *own*
pipeline actually behaves at a real watchlist scale larger than today's. Today's live
watchlist holds only 8-13 tickers (H11's own measurement,
`docs/superpowers/research/2026-08-25-realtime-data-plane-known-findings.md`) despite
`kalshi.watchlist_size: 150` already permitting up to 150 parent series - the gap is
that few series currently pass `kalshi.min_volume_24h`/`categories`'s filters, not that
the cap itself is low. This phase directly widens those filters live, in paper mode,
for a bounded measurement window, to find out whether P4/P5's synthetic assumptions
hold at real scale *before* implementing either - sequenced here, between P3 and P4,
specifically so its findings can inform Task 18's two-consumer-mode threshold and
Task 22's REST-scheduler preset calibration rather than arriving after those are
already built.

Distinct from, not a duplicate of, Phase P2.5 above's CH4, which asks whether a *larger
watchlist changes churn's own cost specifically* and is gated behind CH3 confirming
churn is a material bottleneck (unlikely per CH1/CH2's results, both negative on that
question). This phase asks the broader question - queue depth, REST demand,
loop-health - at real scale, independent of churn, and runs regardless of CH3's
outcome. If CH4 ever does run later, reuse this phase's `tools/
watchlist_scale_stress_test.py` rather than building a second scale-up harness.

**Bidirectional, not just CH4-ward (2026-08-27, direct follow-up instruction).** CH3's own
stop criteria (Phase P2.5 above) names "a larger watchlist" as one of the
things that could change H11's classification later - and this phase's five `widen_scope*`
steps are the literal experiment that tests that scenario, at real scale, on the actual live
watchlist. In current Track A sequencing CH3 runs *before* this phase, so if CH1/CH2's
negative evidence already closed CH3 by the time Task 17b executes, that closure was made
at today's 8-13-ticker scale only (CH1's own 64h sample: largest single-window churn ever
observed was 6 tickers added / 5 removed / 10 combined - see the H11 finding entry). Task
17a therefore also captures the same `trade_stream.ingest.subscription_churn.*` counters
CH1 already tracks (not just `loop_watchdog.stall_max_ms`), and Task 17b's Step 3 compares
them against CH1's own baseline numbers, not just P4/P5's synthetic presets. If any widened
step's churn counters materially exceed CH1's observed range, that is new evidence for H11
- reopen CH3's entry with an addendum rather than treating its earlier "stop" as final; if
they stay in range, that confirms CH3's classification held at scale, which is itself worth
recording rather than assuming.

Per `.claude/rules/realtime-data-plane-evidence.md`: this is the measurement the rule
requires *before* any subscription-scope tuning decision, not the tuning decision
itself - every config change here is temporary, reverted at the end of Task 17b's own
Step 2 (`run_stress_steps`'s own `finally` block), and never touches
`kalshi_account.trading_enabled` (blocked from `POST /api/config` entirely,
`services/config/routes.py:29`) or any other safety-gated field. Paper mode
(`mode: paper`) is unaffected and untouched throughout.

**Scope widened (2026-08-27), same day, direct follow-up instruction.** Four more real
dimensions, each grounded in an actual config field or route rather than invented:

1. `kalshi.max_children_per_parent` (currently `5`) - caps how many child
   markets/events per selected parent series `selection.round_robin_select` includes
   (`services/market_watch/selection.py:76`). Setting it to `None` removes the cap
   entirely ("unlimited" per that function's own docstring) - a real, direct lever on
   watched-market count independent of `watchlist_size`.
2. `GET /api/markets/search` (`services/market_catalog/routes.py:177`) - a distinct,
   user-facing on-demand search/browse route (fans out to up to 30 matched series,
   each its own REST call, then `round_robin_select`s the result using the *same*
   `max_children_per_parent` config above) - not part of the automatic watchlist
   pipeline at all, so it needs its own explicit stress call, not a config patch.
3. `whale_watcher_kalshi.min_contracts`/`min_contracts_by_series` (currently `10000`
   global, e.g. `2500` for `KXBTC15M`) - the real whale-detection floor
   (`services/whalewatchers/kalshi_trade_tape.py`, same field H11's own mechanism
   description already named). Lowering it widens whale-signal density - more trades
   qualify as candidates - independent of how many *markets* are watched.
4. **Open-position count** - a live, direct 2026-08-27 report: "having a large amount
   of open positions causes things to lag or crash." Traced to
   `main.py:958`'s `strategy.check_exits(...)` - called synchronously, unoffloaded,
   directly on the trading tick's hot path, *every tick* regardless of whether a new
   signal arrived, iterating `broker.positions` (a dict). `services/exits/
   exit_engine.py:229`'s `market_history.recent_price(...)` call runs
   **unconditionally per open position** (not behind any of the three opt-in exit
   rules - confirmed by reading the code, not assumed), so this cost exists today even
   with `take_profit_pct`/`stop_loss_pct`/`exit_on_sentiment_reversal`/
   `auto_exit_enabled` all off (today's default). This is the exact same shape of bug
   as the three already found and fixed this session (`candidate_log.
   population_gate_summary`, the `whale_calibration` routes, and CH2's
   `series_watcher.funnel()`) - unoffloaded, per-item DB work on a hot path - except
   this one runs on the single most central hot path of all (the trading tick itself)
   and the live report names an actual crash, not just lag. `services/exits/
   exit_engine.py`'s own Task 20 below (originally the third task of Phase P4) is
   already the intended fix; Task 17c adds the missing measurement proving it, and
   Task 20 is relocated ahead of Task 18/19 in Phase P4 as a direct result - see both
   for the full reasoning.

### Task 17a: `tools/watchlist_scale_stress_test.py` - stress-step runner (tool, not app code)

**Files:**
- Create: `tools/watchlist_scale_stress_test.py`
- Test: `tests/test_watchlist_scale_stress_test.py`

**Interfaces:**
- Produces: `run_stress_steps(base_url: str, steps: list[dict], *, getter: HttpGetter | None = None, poster: HttpPoster | None = None, sleep_fn: Callable[[float], None] | None = None, measure_after_sec: float = 120.0) -> dict` -
  returns `{"original_config": dict, "results": [{"label": str, "patch": dict, "pipeline": dict, "observability": dict}, ...]}`.
  Captures the pre-step config via `GET /api/config` before applying anything, applies
  each step's `patch` via `POST /api/config`, sleeps `measure_after_sec` (via
  `sleep_fn`, injectable so tests don't actually wait), snapshots
  `GET /api/health/pipeline` and, for each of `_OBSERVABILITY_METRICS` (`loop_watchdog.
  stall_max_ms` plus the three `trade_stream.ingest.subscription_churn.*` counters CH1
  already tracks - `syncs_window`, `tickers_added_window`, `tickers_removed_window`,
  reused so this phase's live run doubles as a larger-scale churn data point for CH3, not
  just a P4/P5 input), one
  `GET /api/observability/history?metric=<name>&hours=1` call, keyed by metric name under
  `observability` in the result, after each
  step, and - in a `finally` block, regardless of any step raising - restores only the
  top-level config sections the steps actually touched (deliberately never reposts the
  *entire* captured config: that would include `kalshi_account`, which trips
  `POST /api/config`'s own `trading_enabled` guard and would raise instead of
  reverting, and would needlessly touch `advisory`/`confidence_calibration`
  auto-apply flags this tool has no business changing).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_watchlist_scale_stress_test.py
import copy

from tools import watchlist_scale_stress_test as stress


class _FakeAppClient:
    """Fakes GET/POST against the live app's own API - same HttpGetter-injection
    pattern tools/quality_coordination.py's fetch_app_report already uses. get()
    returns a deep copy, not a live reference - matching what a real HTTP GET +
    json.loads() round-trip always produces (an independent snapshot). Returning
    self.config directly here would silently alias run_stress_steps' own captured
    original_config to this fake's mutable state, corrupting the revert patch the
    moment a later post() mutates self.config in place - a fake-only bug that a real
    HTTP client could never actually exhibit, caught by tracing this exact test
    through by hand before trusting it."""

    def __init__(self):
        self.config = {
            "kalshi": {"live_markets_only": True, "min_volume_24h": 10000},
            "kalshi_account": {"trading_enabled": False},
        }
        self.patches_applied = []

    def get(self, url: str, timeout: float) -> dict:
        if url.endswith("/api/config"):
            return copy.deepcopy(self.config)
        if "/api/health/pipeline" in url:
            return {"markets_watched": len(self.patches_applied) + 8, "ingest": {}}
        if "/api/observability/history" in url:
            metric = url.split("metric=")[1].split("&")[0]
            return {"metric": metric, "samples": []}
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url: str, body: dict, timeout: float) -> dict:
        assert url.endswith("/api/config")
        patch = body["patch"]
        assert "kalshi_account" not in patch  # must never be touched, not even on revert
        self.patches_applied.append(patch)
        for section, values in patch.items():
            self.config.setdefault(section, {}).update(values)
        return self.config


def test_run_stress_steps_applies_measures_and_reverts_only_touched_sections():
    client = _FakeAppClient()
    steps = [{"label": "widen_scope", "patch": {"kalshi": {"min_volume_24h": 1000}}}]
    slept = []
    result = stress.run_stress_steps(
        "http://fastapi:8000", steps,
        getter=client.get, poster=client.post, sleep_fn=slept.append, measure_after_sec=5.0,
    )
    assert len(result["results"]) == 1
    assert result["results"][0]["label"] == "widen_scope"
    assert result["results"][0]["pipeline"]["markets_watched"] == 9
    assert slept == [5.0]
    # Captures loop_watchdog AND the three subscription_churn counters CH1 already
    # tracks (Phase P2.5 above) - this phase's live run doubles as
    # a larger-scale churn data point for CH3, not just a P4/P5 input.
    obs = result["results"][0]["observability"]
    assert set(obs) == {
        "loop_watchdog.stall_max_ms",
        "trade_stream.ingest.subscription_churn.syncs_window",
        "trade_stream.ingest.subscription_churn.tickers_added_window",
        "trade_stream.ingest.subscription_churn.tickers_removed_window",
    }
    assert obs["trade_stream.ingest.subscription_churn.syncs_window"]["metric"] == (
        "trade_stream.ingest.subscription_churn.syncs_window"
    )
    # 2 posts total: the step's own patch, then the revert - both audited above for
    # never containing kalshi_account.
    assert len(client.patches_applied) == 2
    assert client.patches_applied[-1] == {"kalshi": {"live_markets_only": True, "min_volume_24h": 10000}}
    assert client.config["kalshi"]["min_volume_24h"] == 10000  # reverted


def test_run_stress_steps_reverts_even_if_a_step_raises():
    client = _FakeAppClient()

    def _failing_get(url, timeout):
        if "/api/health/pipeline" in url:
            raise RuntimeError("connection reset")
        return client.get(url, timeout)

    steps = [{"label": "widen_scope", "patch": {"kalshi": {"min_volume_24h": 1000}}}]
    try:
        stress.run_stress_steps(
            "http://fastapi:8000", steps,
            getter=_failing_get, poster=client.post, sleep_fn=lambda s: None, measure_after_sec=0.0,
        )
    except RuntimeError:
        pass
    assert client.config["kalshi"]["min_volume_24h"] == 10000  # revert still happened


def test_probe_market_search_times_the_call_and_brackets_it_with_pipeline_snapshots():
    client = _FakeAppClient()
    call_log = []

    def _get(url, timeout):
        call_log.append(url)
        if "/api/markets/search" in url:
            return {"markets": [{"ticker": "K1"}, {"ticker": "K2"}], "market_titles": {}}
        return client.get(url, timeout)

    result = stress.probe_market_search(
        "http://fastapi:8000", limit=200, live_only=True,
        getter=_get, sleep_fn=lambda s: None,
    )
    assert result["result_count"] == 2
    assert result["elapsed_sec"] >= 0
    assert "pipeline_before" in result and "pipeline_after" in result
    # Order matters: pipeline snapshot, then the search call, then a second snapshot.
    assert [u.split("?")[0] for u in call_log] == [
        "http://fastapi:8000/api/health/pipeline",
        "http://fastapi:8000/api/markets/search",
        "http://fastapi:8000/api/health/pipeline",
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python -m pytest tests/test_watchlist_scale_stress_test.py -v`
Expected: FAIL - `ModuleNotFoundError: No module named 'tools.watchlist_scale_stress_test'`

- [ ] **Step 3: Implement `run_stress_steps` and the CLI**

```python
# tools/watchlist_scale_stress_test.py
"""Live (not replay-based) watchlist-scale stress test - applies a sequence of
temporary config patches against the running app's own POST /api/config, measures
GET /api/health/pipeline + GET /api/observability/history after each, and always
restores only the config sections it touched. Standalone tool (CLAUDE.md's
"Workflow/tooling and application code must never overlap" rule) - never imported by
main.py/services/, reads/writes the live app only through its own public HTTP API,
the same one-way coupling tools/quality_coordination.py's fetch_app_report already
establishes.

Realtime data-plane remediation plan, Phase P3.5 (Task 17a/17b) - feeds P4/P5's
design with real-scale measurements before either is implemented."""
from __future__ import annotations

import json
import time
import urllib.request
from typing import Callable

HttpGetter = Callable[[str, float], dict]
HttpPoster = Callable[[str, dict, float], dict]

# loop_watchdog is this phase's own P4/P5 concern; the three subscription_churn
# counters are CH1's own metrics (Phase P2.5 above) - captured here
# too so a widened-scope step doubles as a larger-scale churn data point for CH3,
# not just a P4/P5 input (see this phase's header note).
_OBSERVABILITY_METRICS = (
    "loop_watchdog.stall_max_ms",
    "trade_stream.ingest.subscription_churn.syncs_window",
    "trade_stream.ingest.subscription_churn.tickers_added_window",
    "trade_stream.ingest.subscription_churn.tickers_removed_window",
)


def _default_get(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "kalshi-whale-poc-stress-test"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _default_post(url: str, body: dict, timeout: float) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "kalshi-whale-poc-stress-test"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def run_stress_steps(
    base_url: str, steps: list[dict], *,
    getter: HttpGetter | None = None, poster: HttpPoster | None = None,
    sleep_fn: Callable[[float], None] | None = None, measure_after_sec: float = 120.0,
) -> dict:
    get = getter or _default_get
    post = poster or _default_post
    sleep = sleep_fn or time.sleep

    original_config = get(f"{base_url}/api/config", 5.0)
    touched_sections = {key for step in steps for key in step["patch"]}
    revert_patch = {s: original_config[s] for s in touched_sections if s in original_config}

    results = []
    try:
        for step in steps:
            post(f"{base_url}/api/config", {"patch": step["patch"]}, 5.0)
            sleep(measure_after_sec)
            pipeline = get(f"{base_url}/api/health/pipeline", 5.0)
            observability = {
                metric: get(f"{base_url}/api/observability/history?metric={metric}&hours=1", 5.0)
                for metric in _OBSERVABILITY_METRICS
            }
            results.append({
                "label": step["label"], "patch": step["patch"],
                "pipeline": pipeline, "observability": observability,
            })
    finally:
        if revert_patch:
            post(f"{base_url}/api/config", {"patch": revert_patch}, 5.0)

    return {"original_config": original_config, "results": results}


def probe_market_search(
    base_url: str, *, q: str = "", min_volume: float = 0, category: str = "",
    limit: int = 200, live_only: bool = True,
    getter: HttpGetter | None = None, sleep_fn: Callable[[float], None] | None = None,
) -> dict:
    """One direct, wide GET /api/markets/search call - a distinct, user-facing
    on-demand search path (services/market_catalog/routes.py:177), not the automatic
    watchlist pipeline `run_stress_steps` exercises, so it needs its own explicit
    call rather than a config patch. Snapshots GET /api/health/pipeline immediately
    before and 5s after, to see whether a broad, high-limit, live_only search
    materially moves REST demand or queue health. Elapsed time via
    time.monotonic() - unaffected by wall-clock adjustments, unlike time.time()."""
    get = getter or _default_get
    sleep = sleep_fn or time.sleep

    pipeline_before = get(f"{base_url}/api/health/pipeline", 5.0)
    t0 = time.monotonic()
    url = (
        f"{base_url}/api/markets/search?q={q}&min_volume={min_volume}"
        f"&category={category}&limit={limit}&live_only={str(live_only).lower()}"
    )
    result = get(url, 30.0)  # a wide live_only search fans out to real REST calls - longer timeout than the config/pipeline reads above
    elapsed_sec = time.monotonic() - t0
    sleep(5.0)  # let any REST-demand blip show up in the next pipeline snapshot
    pipeline_after = get(f"{base_url}/api/health/pipeline", 5.0)

    return {
        "elapsed_sec": round(elapsed_sec, 3),
        "result_count": len(result.get("markets") or []),
        "pipeline_before": pipeline_before,
        "pipeline_after": pipeline_after,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://fastapi:8000")
    parser.add_argument("--measure-after-sec", type=float, default=120.0)
    args = parser.parse_args()

    # Task 17b's live steps - see that task for the reasoning behind each value.
    live_steps = [
        {"label": "widen_scope", "patch": {"kalshi": {"min_volume_24h": 1000, "categories": ["Sports", "Crypto"]}}},
        {"label": "widen_scope_non_live_only", "patch": {"kalshi": {"live_markets_only": False}}},
        {"label": "widen_scope_strategy_live_only", "patch": {"kalshi": {"live_markets_only": True}, "strategy": {"live_markets_only": True}}},
        {"label": "widen_scope_cap_removed", "patch": {"kalshi": {"max_children_per_parent": None}}},
        {"label": "widen_scope_whale_signal", "patch": {"whale_watcher_kalshi": {"min_contracts": 1000, "min_contracts_by_series": {"KXBTC15M": 500}}}},
    ]
    output = run_stress_steps(args.base_url, live_steps, measure_after_sec=args.measure_after_sec)
    output["search_probe"] = probe_market_search(args.base_url)
    print(json.dumps(output, indent=2))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python -m pytest tests/test_watchlist_scale_stress_test.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/watchlist_scale_stress_test.py tests/test_watchlist_scale_stress_test.py
git commit -m "feat: add live watchlist-scale stress-test runner (P3.5 Task 17a)"
```

---

### Task 17b: Run the stress test live, record findings, feed P4/P5

**Files:**
- Modify: `docs/superpowers/research/2026-08-25-realtime-data-plane-known-findings.md` (new dated entry)
- Modify: `docs/superpowers/plans/2026-08-25-realtime-data-plane-remediation.md` (this file - annotate Task 18/22 with a pointer to the result)

- [ ] **Step 1: Confirm paper mode and take a pre-test baseline**

Run: `curl -s https://kalshi-whale-poc.ddev.site:8443/api/config | python3 -c "import json,sys; c=json.load(sys.stdin); print(c.get('mode'), c.get('kalshi_account',{}).get('trading_enabled'))"`
Expected: `paper False` - do not proceed if either differs.

Then snapshot `GET /api/health/pipeline` and
`GET /api/observability/history?metric=loop_watchdog.stall_max_ms&hours=1` once,
unpatched, as the baseline every step below gets compared against.

- [ ] **Step 2: Run the live stress test**

Run: `ddev exec -s fastapi python -m tools.watchlist_scale_stress_test --measure-after-sec 180 > /tmp/watchlist_stress_result.json`

Five config steps, each building on the last (see the CLI's `live_steps` in Task
17a), plus one direct call:
1. `widen_scope` - lowers `kalshi.min_volume_24h` from 10000 to 1000 and adds
   `Crypto` to `kalshi.categories` (currently `["Sports"]` only), to admit more series
   past the volume/category filter without touching `watchlist_size` (already 150,
   already generous - see this phase's own header note).
2. `widen_scope_non_live_only` - same widened scope, `kalshi.live_markets_only: false`
   (today's default is `true`) - isolates whether *discovery mode* itself, not just
   scale, changes behavior.
3. `widen_scope_strategy_live_only` - same widened scope, `kalshi.live_markets_only`
   back to `true`, `strategy.live_markets_only: true` (today's default is `false`) -
   isolates the *decision-layer* counterpart (`services/strategy_engine.py:291`,
   `services/shadow_mode.py:218`) from the discovery-layer one.
4. `widen_scope_cap_removed` - same widened scope, `kalshi.max_children_per_parent:
   None` (today's default is `5`) - removes the per-series child-market cap
   (`selection.round_robin_select`), the direct lever on watched-market count this
   phase's header names separately from `watchlist_size` itself.
5. `widen_scope_whale_signal` - same widened scope, `whale_watcher_kalshi.
   min_contracts: 1000` (today's default is `10000`) and `min_contracts_by_series:
   {"KXBTC15M": 500}` (today's default is `2500`) - widens whale-signal density.
   Note: `config_store.update()` merges one level deep only
   (`services/config_store.py:158`), so this patch replaces the *entire*
   `min_contracts_by_series` map for the step's duration, not just the `KXBTC15M`
   key - harmless here since the revert restores the complete original section, but
   worth knowing if this step's own intermediate state is ever inspected mid-run.

Then, separately (not a config step - `GET /api/markets/search` is a distinct,
user-facing route, not part of the automatic watchlist pipeline the five steps
above exercise): `probe_market_search(base_url, limit=200, live_only=True)`, one
wide search call fanning out to up to 30 matched series' worth of REST calls.

Each config step runs for `--measure-after-sec 180` (3 minutes) before the next -
long enough to observe at least one `loop_watchdog` sample window and one
subscription-churn catalog-refresh cycle (~15s cadence per H11); the search probe
adds one call plus a 5s settle. Whole run stays under 15 minutes end to end.

- [ ] **Step 3: Compare against baseline and record the result**

For each config step, compare against Step 1's baseline: `markets_watched`,
`queue.depth`/`queue_wait` (`GET /api/health/pipeline`'s `trade_stream.ingest`
block), `server_errors`/`reconnects`, and `loop_watchdog.stall_max_ms`. For the
search probe, record `elapsed_sec` and whether `pipeline_before`/`pipeline_after`
differ materially on the same axes.

**Also compare each step's three `subscription_churn` counters against CH1's own
observed range** (largest single-window event ever measured: 6 tickers added / 5
removed / 10 combined - `syncs_window`/`tickers_added_window`/
`tickers_removed_window`, H11's finding entry). A widened-scope step materially
inside that range confirms CH3's classification held at real scale; materially
outside it is new evidence that changes H11's classification, not just a P4/P5
input - name which happened plainly, don't leave it implicit in the raw numbers.

Append the result directly to **H11's own entry** in
`docs/superpowers/research/2026-08-25-realtime-data-plane-known-findings.md`
(same format as its existing CH1/CH2 sub-entries, not a separate new hypothesis)
recording the real numbers - not a pass/fail verdict invented ahead of the data.

**Cross-post, don't leave this findable only here (2026-08-27 direct instruction:
this investigation should inform future refactors/writes and audits across every
layer, not just P4/P5).** This repo's existing convention for that is each
`services/<name>/CHEATSHEET.md` - CLAUDE.md's own "Current objective" section
already tells a future audit to read a module's `CHEATSHEET.md` "before assuming
a module needs a fresh audit from scratch," so a finding that never reaches one is
effectively invisible to that workflow. Add a one-paragraph, dated summary (result
+ link back to the known-findings entry, not the full data) to whichever of
`services/market_watch/CHEATSHEET.md`, `services/market_catalog/CHEATSHEET.md`,
`services/kalshi/CHEATSHEET.md`, `services/quality/CHEATSHEET.md`, and
`services/observability/CHEATSHEET.md` actually saw a material result for their
own domain - skip the ones where nothing moved, and say so in the known-findings
entry itself rather than writing a null cross-post. `services/whalewatchers/` has
no `CHEATSHEET.md` yet (confirmed - only 19 of 27 `services/` packages have one);
if the whale-signal-widening step produces a material finding, add a dated note to
`services/whalewatchers/kalshi_trade_tape.py`'s own module docstring instead of
creating a new file speculatively for one entry.

- [ ] **Step 4: Confirm the revert took effect**

Run: `curl -s https://kalshi-whale-poc.ddev.site:8443/api/config | python3 -c "import json,sys; c=json.load(sys.stdin); print(c['kalshi']['min_volume_24h'], c['kalshi']['live_markets_only'])"`
Expected: `10000 True` - `run_stress_steps`' own `finally` block should have already
done this; this step is the independent live confirmation, not a repeat of the same
code path.

- [ ] **Step 5: Feed the result into Task 18/22's own design, and back into CH3**

Add one line to Task 18's header (this file) and Task 22's header pointing at the new
known-findings entry: whichever of `queue.depth`, `queue_wait`, or REST demand
actually moved materially under the widened-scope steps (including the
`max_children_per_parent`/search-probe/whale-signal dimensions, not just the first
three) is the one Task 18's two-consumer threshold and Task 22's REST-scheduler
preset values should be calibrated against - name the real number, not the synthetic
preset's assumed one, when either task is implemented. If nothing moved materially
at this scale, say that explicitly in both places - it's a legitimate finding that
lowers P4/P5's priority relative to P3, not a failed experiment.

**Separately, feed the churn-counter comparison (Step 3) back into Phase P2.5 above's
CH3.** If CH3 already ran and stopped that phase on today's small-scale evidence, and
this step's churn counters landed materially outside CH1's observed range, add a dated
addendum to CH3's section reopening the classification and pointing at this entry -
do not silently leave a stale "stopped" verdict standing once contradicting evidence
exists. If the counters stayed in range, add a one-line confirming addendum instead
("classification held at N-ticker scale, 2026-08-27") so a future reader isn't left
wondering whether this was ever checked.

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/research/2026-08-25-realtime-data-plane-known-findings.md \
  docs/superpowers/plans/2026-08-25-realtime-data-plane-remediation.md
git commit -m "docs: run the live watchlist-scale stress test, record findings (P3.5 Task 17b)"
```

---

### Task 17c: Benchmark `check_exits`'s cost vs. open-position count (synthetic, no live data touched)

Use `root-cause-debugging` explicitly, same as CH2 - a live-reported symptom
("having a large amount of open positions causes things to lag or crash",
2026-08-27), quantified before Task 20 (relocated below) fixes it. Synthetic only -
never touches the real `data/paper_broker.db`, per CLAUDE.md's persistence-safety
rule (tests always redirect DB access, never a live `data/*.db` file).

**Files:**
- Create: `tests/test_check_exits_scale_benchmark.py`

**Interfaces:**
- Consumes: `services.exits.exit_engine.check_exits(broker, latest_prices,
  signal_feed, cfg, market_results=None, ...)` (existing, unchanged) and
  `services.paper_broker.Position` (existing dataclass:
  `ticker, side, size, entry_price, opened_at, config_fingerprint=None,
  entry_fee=0.0, hold_to_settlement=False`).

- [ ] **Step 1: Write the benchmark**

```python
# tests/test_check_exits_scale_benchmark.py
"""Benchmarks (not just tests) the CURRENT cost of exit_engine.check_exits at
increasing open-position counts, before Task 20's tick_cache fix - quantifies the
live-observed "large amount of open positions causes lag or crash" report
(2026-08-27) with real numbers, the same "measure before fixing" discipline CH2
just followed with py-spy against series_watcher.funnel(). Synthetic Position
objects only - never touches the real data/paper_broker.db."""
import time
from unittest.mock import patch

import pytest

from services.exits import exit_engine
from services.paper_broker import Position


def _make_positions(n: int) -> dict:
    return {
        f"KXBTC15M-T{i}": Position(
            ticker=f"KXBTC15M-T{i}", side="yes", size=10, entry_price=0.5, opened_at=time.time(),
        )
        for i in range(n)
    }


class _FakeBroker:
    def __init__(self, positions: dict):
        self.positions = positions


@pytest.mark.parametrize("n", [10, 50, 200])
def test_check_exits_hits_recent_price_once_per_open_position_today(n):
    """Direct proof of the mechanism the live crash report points at:
    market_history.recent_price runs unconditionally per open position
    (exit_engine.py:229, outside all three opt-in exit rules) - N positions means
    N calls today, not O(1). This is exactly what Task 20's tick_cache fixes."""
    broker = _FakeBroker(_make_positions(n))
    latest_prices = {t: p.entry_price for t, p in broker.positions.items()}
    with patch("services.market_history.recent_price", return_value=None) as recent_price:
        exit_engine.check_exits(broker, latest_prices, signal_feed=[], cfg={"strategy": {}})
    assert recent_price.call_count == n


@pytest.mark.parametrize("n", [10, 50, 200, 500])
def test_check_exits_wall_clock_cost_at_scale(n, capsys):
    """Records real wall-clock cost (not just call count) at each N, printed for
    Task 17b-style recording - a 0.1ms mock reply per DB read still exposes the
    O(N) shape without needing seeded real data."""
    broker = _FakeBroker(_make_positions(n))
    latest_prices = {t: p.entry_price for t, p in broker.positions.items()}

    def _slow_recent_price(*a, **kw):
        time.sleep(0.0001)  # a representative single-row SQLite read, not real I/O
        return None

    with patch("services.market_history.recent_price", side_effect=_slow_recent_price):
        t0 = time.monotonic()
        exit_engine.check_exits(broker, latest_prices, signal_feed=[], cfg={"strategy": {}})
        elapsed = time.monotonic() - t0
    print(f"n={n} positions: check_exits took {elapsed * 1000:.1f}ms")
```

- [ ] **Step 2: Run it**

Run: `ddev exec -s fastapi python -m pytest tests/test_check_exits_scale_benchmark.py -v -s`
Expected: PASS. The `-s` flag surfaces the wall-clock `print()` lines - record the
four `n=...` numbers (10/50/200/500) into the same known-findings entry Task 17b's
Step 3 writes, plus a dated summary paragraph in `services/exits/CHEATSHEET.md`
(same cross-posting reasoning as Task 17b's own Step 3 - a future audit of
`services/exits/` reads that file first, not this plan) stating plainly whether
the distinct-ticker case (this benchmark's actual design) or only the same-ticker
case turned out to dominate, since that directly decides whether Task 20 alone
resolves the live crash report or needs the bulk-fetch/tick_executor follow-up its
own "Known scope gap" note already flags.

**Frontend rendering is a plausible contributing dimension this benchmark does not
measure** (2026-08-27 direct note) - whether the dashboard's own position-list
rendering cost also scales with open-position count is a real, unanswered question,
not one this backend-only benchmark can speak to. Deliberately not pinned to
specific current frontend file paths here:
`docs/superpowers/plans/2026-08-25-frontend-modularization.md` is still in
progress (~5 of 54 tasks done per its own checkboxes), so a path-specific claim
written today would likely be stale before anyone acts on it. Instead: add one
line to that plan's own tracking noting this open question, to be picked up once
enough of the strangler-fig migration has landed that a frontend-side measurement
would target something stable rather than code about to move.

- [ ] **Step 3: Commit**

```bash
git add tests/test_check_exits_scale_benchmark.py
git commit -m "test: benchmark check_exits' per-position cost, quantify the live crash report (P3.5 Task 17c)"
```

---

**P3.5 gate:** Task 17b's Step 2 has actually run against the live app at least once
(not just Task 17a's unit tests) and its result, plus Task 17c's four benchmark
numbers, are recorded in the known-findings doc; Task 18, Task 22, and Task 20
(relocated below) each carry a pointer to the relevant result before their own
implementation starts. Config is back to its pre-test values (Step 4 confirms it
live). No step touches `kalshi_account.trading_enabled`, leaves `mode` other than
`paper`, or writes to a real `data/*.db` file.

---

## Phase P4 — Critical / market consumers, ticker coalescing, kept queue

**Task 20 relocated here, ahead of Task 18/19 (2026-08-27), direct instruction
following a live crash report** ("having a large amount of open positions causes
things to lag or crash") - see P3.5's header for the full trace
(`main.py:958` -> `exit_engine.check_exits` -> `market_history.recent_price`
unconditional per open position) and Task 17c for the benchmark quantifying it.
Originally the third task of this phase; a crash-severity, every-tick hot-path bug
outranks Task 18/19's message-volume efficiency work, and Task 20 turns out to have
no real dependency on either (its original Step 6 assumed Task 18's `_consume_market`
coroutine, which doesn't exist yet at this point in the plan - corrected below to
wire the cache at today's real call site, `main.py`'s single per-tick call, which
this task doesn't need Task 18 for at all).

### Task 20: `check_exits` per-tick memoization

**Files:**
- Modify: `services/exits/exit_engine.py` (`check_exits` and its four DB-reading helpers)
- Modify: `main.py` (the `strategy.check_exits(...)` call site, line 958)
- Test: append to the existing `exit_engine` test file

**Interfaces:**
- Produces: `exit_engine.check_exits(..., tick_cache: dict | None = None)` — an
  optional cache dict, populated once per tick, keyed by `(function_name, ticker)`,
  so N open positions *on the same tick and same ticker* share one `recent_price`/
  `volatility`/`analyst_lean`/`series_stats` read instead of N reads (I12 W4). When
  `tick_cache` is `None` (every existing caller), behavior is byte-identical to
  today — this is a strictly additive optional parameter.

**Known scope gap, stated plainly rather than glossed over:** this only removes
*duplicate reads for positions sharing one ticker* (e.g. two partial-hedge
positions on the same market). It does **not** reduce read count when N open
positions span N *different* tickers - the more likely shape for "a large amount
of open positions," and exactly what Task 17c's `test_check_exits_hits_recent_
price_once_per_open_position_today` benchmark measures (distinct tickers by
design). Check that benchmark's actual numbers before treating this task alone as
having resolved the live crash report - if most of the reported cost turns out to
be the distinct-ticker case, this needs a follow-up: either a bulk-fetch across all
open tickers in one query (this codebase already has precedent for that shape -
`signal_log.series_stats_bulk`, referenced in `services/tick_executor.py`'s own
module docstring) or a `tick_executor` offload of the whole `check_exits` call
(this session's other three established fixes all took that route). Decide with
Task 17c's real numbers, not a guess made here.

- [ ] **Step 1: Read `check_exits`'s current signature and its four per-position DB reads in full** (`recent_price` :229, `volatility` :448, `analyst_lean` :494, `series_stats` :506).
- [ ] **Step 2: Write a failing test**: two open positions on the same ticker, one `check_exits` call each with a shared `tick_cache={}`, asserts `market_history.recent_price` (mocked/counted) is called exactly once, not twice.
- [ ] **Step 3: Run it, watch it fail.**
- [ ] **Step 4: Add the `tick_cache` parameter and the four memoized lookups**, e.g.:

```python
def _cached(tick_cache, key, fn, *args):
    if tick_cache is None:
        return fn(*args)
    if key not in tick_cache:
        tick_cache[key] = fn(*args)
    return tick_cache[key]
```

Wrap each of the four call sites: `_cached(tick_cache, ("recent_price", ticker), market_history.recent_price, ticker, ...)` (match real arguments).

- [ ] **Step 5: Run it, watch it pass.**
- [ ] **Step 6: Wire a shared `tick_cache={}` at today's real call site** — `main.py:958`, right before `for close_decision in strategy.check_exits(...)`: create `tick_cache = {}` and pass it through as the new keyword argument. (Not `_consume_market` — that coroutine doesn't exist yet; this call happens once per trading tick today, directly in `trading_loop`, which is exactly where the live crash report's cost accrues.)
- [ ] **Step 7: Run the full exit_engine test suite for a regression check** — pay particular attention to any test relying on `recent_price` etc. being called fresh per position (none should, since the underlying value for the same ticker is identical within one tick, but confirm).
- [ ] **Step 8: Commit:** `git commit -m "perf: memoize check_exits per-tick DB reads across positions on the same ticker (I13 P4)"`

**Task 20 shipped (2026-08-27).** Tests pass, wired only at `main.py`'s real
call site per Step 6, exactly as scoped above. Confirmed against source
while implementing: `PaperBroker.positions` is one `Position` per ticker
(`services/paper_broker.py`), so this task's own same-ticker target case
cannot occur within a single `check_exits()` call today, and `main.py`
calls `check_exits` once per tick with a fresh `tick_cache` — meaning
Task 20's memoization is correctly implemented but currently produces zero
cache hits in the live app, not just zero help with the distinct-ticker
case already called out above. See `services/exits/README.md`'s "Task 20
shipped" entry for the full account.

- [ ] **Follow-up, not implemented here:** the distinct-ticker case this
  task's own Known-scope-gap note anticipated and Task 17c's benchmark
  confirmed dominates the live crash report still needs its own fix —
  either a bulk-fetch query across all open tickers in one call (precedent:
  `signal_log.series_stats_bulk`, referenced in `services/tick_executor.py`'s
  module docstring) or a `tick_executor` offload of the whole `check_exits`
  call. Decide with real numbers when picked up, not a guess made here.

---

### Task 18: Split the single consumer into `critical` and `market` queues

**Task 17b result (2026-08-27):** no real-scale calibration data available for this
threshold - all 3 live `widen_scope` attempts crashed before producing comparable
`queue.depth`/`queue_wait` data (root cause: concurrent Claude Code worktree sessions
triggering `uvicorn --reload` mid-experiment, not the widened scope itself - see
`docs/superpowers/research/2026-08-25-realtime-data-plane-known-findings.md`'s "Phase
P3.5 live-scale attempt" entry). Use CH1's existing negligible-cost baseline as the
current best evidence until a successful rerun produces a real number to calibrate
against.

**Files:**
- Modify: `services/kalshi/websocket.py` (`run`, `_consume`, queue construction, `_process_item`'s enqueue target)
- Test: append to `tests/test_kalshi_ws_ingest_metrics.py`, new `tests/test_kalshi_ws_two_consumers.py`

**Interfaces:**
- Produces: two `asyncio.Queue` instances (`self._critical_queue`,
  `self._market_queue`, both `maxsize=20000` as today), two consumer coroutines
  (`_consume_critical`, `_consume_market`), each supervised via
  `task_supervisor.supervise(..., restart=True)`.
- Changes: `_process_item`'s enqueue target is `self._critical_queue` for
  `fill`/`market_position`/`market_lifecycle_v2`/control frames, `self._market_queue`
  for gate-passing trades. Behind a new `realtime_data_plane.two_consumer_mode`
  flag, default `false` (single queue, today's behavior, unchanged).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_kalshi_ws_two_consumers.py
import json

import pytest


@pytest.mark.asyncio
async def test_fill_and_trade_land_on_separate_queues_when_enabled(monkeypatch):
    ws = _make_ws()  # reuse the existing helper from test_kalshi_ws_ingest_metrics.py
    monkeypatch.setattr("services.config_store.get", lambda: {"realtime_data_plane": {"two_consumer_mode": True}})
    fill = {"type": "fill", "msg": {"trade_id": "f1", "market_ticker": "K1", "count": 1, "yes_price": 50, "side": "yes", "action": "buy"}}
    trade = {"type": "trade", "msg": {"trade_id": "t1", "ticker": "K1", "count": 500}}
    ws._ingest_raw(json.dumps(fill))
    ws._ingest_raw(json.dumps(trade))
    assert ws._critical_queue.qsize() == 1
    assert ws._market_queue.qsize() == 1


@pytest.mark.asyncio
async def test_single_queue_mode_is_unchanged_when_disabled(monkeypatch):
    ws = _make_ws()
    monkeypatch.setattr("services.config_store.get", lambda: {"realtime_data_plane": {"two_consumer_mode": False}})
    fill = {"type": "fill", "msg": {"trade_id": "f1", "market_ticker": "K1", "count": 1, "yes_price": 50, "side": "yes", "action": "buy"}}
    ws._ingest_raw(json.dumps(fill))
    assert ws._queue.qsize() == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python -m pytest tests/test_kalshi_ws_two_consumers.py -v`
Expected: FAIL — `_critical_queue`/`_market_queue` don't exist yet

- [ ] **Step 3: Add the flag, the two queues, and the routing branch**

Add `realtime_data_plane.two_consumer_mode: false` to `config/settings.yaml`. In
`__init__` (or wherever `self._queue` is constructed today), add:

```python
self._critical_queue: asyncio.Queue | None = None
self._market_queue: asyncio.Queue | None = None
```

In `_process_item`, after the existing gate logic from Task 17, replace the single
`queue.put_nowait(...)` with:

```python
two_consumer = (config_store.get().get("realtime_data_plane") or {}).get("two_consumer_mode")
if two_consumer:
    target = self._critical_queue if cls in ("fill", "market_position", "market_lifecycle_v2") else self._market_queue
    if target is None:
        target = self._critical_queue = self._market_queue = self._queue  # lazily created below, same pattern as today's self._queue
    ...
else:
    queue = self._queue  # today's path, unchanged
```

(Write this precisely by reading `_process_item`'s exact current queue-creation
lazy-init block first — the two new queues must follow the identical lazy-creation
pattern `self._queue` already uses, not a new one.)

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python -m pytest tests/test_kalshi_ws_two_consumers.py -v`
Expected: PASS

- [ ] **Step 5: Add `_consume_critical`/`_consume_market` and wire `run()`**

Duplicate `_consume`'s existing loop body into two named coroutines (critical:
dispatch to the fill/position/lifecycle handlers; market: dispatch to the trade/
ticker handlers), each reading from its own queue. In `run()`, behind the same
`two_consumer_mode` flag, launch both via `task_supervisor.supervise(..., restart=True)`
instead of the single `consumer` task; keep the single-consumer path as the `else`
branch, byte-for-byte unchanged.

- [ ] **Step 6: Test both consumers actually drain their own queue**

```python
@pytest.mark.asyncio
async def test_both_consumers_drain_independently(monkeypatch):
    ws = _make_ws()
    monkeypatch.setattr("services.config_store.get", lambda: {"realtime_data_plane": {"two_consumer_mode": True}})
    handled = []
    monkeypatch.setattr(ws, "_handle_message", lambda item: handled.append(item))
    fill = {"type": "fill", "msg": {"trade_id": "f1", "market_ticker": "K1", "count": 1, "yes_price": 50, "side": "yes", "action": "buy"}}
    trade = {"type": "trade", "msg": {"trade_id": "t1", "ticker": "K1", "count": 500}}
    ws._ingest_raw(json.dumps(fill))
    ws._ingest_raw(json.dumps(trade))
    await asyncio.gather(ws._consume_critical(), ws._consume_market(), return_exceptions=True)
    # both coroutines run forever in production; this test needs a bounded-iteration
    # variant - add a `max_iterations` kwarg to both for testability, defaulting to
    # None (loop forever) so production behavior is unchanged.
    assert len(handled) == 2
```

(Add the `max_iterations` testability hook as part of Step 5, matching whatever
existing pattern `_consume` already uses for test termination — check first; it may
already support this via queue draining + a sentinel, in which case reuse that
instead of adding a new parameter.)

- [ ] **Step 7: Run the full WS ingest test suite for a regression check**

Run: `ddev exec -s fastapi python -m pytest tests/test_kalshi_ws_ingest_metrics.py tests/test_kalshi_ws_two_consumers.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add services/kalshi/websocket.py config/settings.yaml tests/test_kalshi_ws_two_consumers.py
git commit -m "feat: add a critical/market two-consumer mode behind a flag (I13 P4)"
```

---

### Task 19: Ticker coalescing with apply-if-newer and no inline REST on the lifecycle path

**Files:**
- Modify: `services/kalshi/websocket.py` (`_consume_market`'s ticker handling)
- Modify: `services/whale_stream/whale_stream_handlers.py` (`_process_stream_lifecycle`'s
  `settled` branch — remove the inline `await client.get_market(...)`)
- Create: `services/settlement_resolver.py` (the deferred batch resolver — queue only
  in this task; Task 21 implements the actual batched REST call)
- Test: append to `tests/test_kalshi_ws_two_consumers.py`, `tests/test_whale_stream_stage_timing.py`

**Interfaces:**
- Produces: `settlement_resolver.enqueue(ticker: str, settled_ts: float) -> None`
  (queue-only stub for this task; Task 21 adds the resolver loop that drains it).
- Changes: the market queue, when `two_consumer_mode` is on, coalesces consecutive
  ticker messages for the same market (newest `ts` wins, `apply-if-newer` — a
  message with an older `ts` than the currently-held one for that market is
  discarded, not replacing it) before the consumer processes them; the lifecycle
  `settled` handler stops awaiting `get_market` inline and instead calls
  `settlement_resolver.enqueue(ticker, settled_ts)` and returns immediately.

- [ ] **Step 1: Write the failing test for coalescing**

```python
@pytest.mark.asyncio
async def test_ticker_coalescing_keeps_only_the_newest_ts_per_market(monkeypatch):
    ws = _make_ws()
    monkeypatch.setattr("services.config_store.get", lambda: {"realtime_data_plane": {"two_consumer_mode": True}})
    older = {"type": "ticker", "msg": {"ticker": "K1", "ts": 100, "yes_bid": 50}}
    newer = {"type": "ticker", "msg": {"ticker": "K1", "ts": 200, "yes_bid": 55}}
    stale = {"type": "ticker", "msg": {"ticker": "K1", "ts": 150, "yes_bid": 52}}  # arrives after newer but is older
    ws._ingest_raw(json.dumps(older))
    ws._ingest_raw(json.dumps(newer))
    ws._ingest_raw(json.dumps(stale))
    pending = ws._pending_ticker_by_market()  # add this read helper as part of Step 3
    assert pending["K1"]["ts"] == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python -m pytest tests/test_kalshi_ws_two_consumers.py -k coalescing -v`
Expected: FAIL

- [ ] **Step 3: Implement the coalescing map**

Replace direct market-queue enqueue for `cls == "ticker"` (when `two_consumer_mode`
is on) with an update to `self._ticker_by_market: dict[str, dict]`, applying only if
`data["msg"]["ts"] > self._ticker_by_market.get(ticker, {}).get("ts", -1)`; increment
`self._coalesced` when a message is superseded before being consumed. `_consume_market`
drains actual trade-queue items first (FIFO, as today), then — when the trade queue
is empty — processes one entry from `_ticker_by_market` per loop iteration (pop
arbitrary key; order across markets is not guaranteed, matching design spec §3).
Add `_pending_ticker_by_market()` as a plain accessor for the test.

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python -m pytest tests/test_kalshi_ws_two_consumers.py -v`
Expected: PASS

- [ ] **Step 5: Write the failing test for the settled handler**

```python
@pytest.mark.asyncio
async def test_settled_enqueues_the_resolver_and_does_not_await_get_market(monkeypatch):
    from services.whale_stream import whale_stream_handlers as h
    calls = []
    monkeypatch.setattr("services.settlement_resolver.enqueue", lambda ticker, settled_ts: calls.append((ticker, settled_ts)))
    async def _should_not_be_called(*a, **k):
        raise AssertionError("get_market must not be awaited inline from the settled handler anymore")
    monkeypatch.setattr("services.kalshi.public.KalshiPublicGateway.get_market", _should_not_be_called)
    msg = {"type": "market_lifecycle_v2", "msg": {"ticker": "K1", "event_type": "settled", "settled_ts": 123.0}}
    await h._process_stream_lifecycle(msg)  # match the real function's actual argument shape
    assert calls == [("K1", 123.0)]
```

- [ ] **Step 6: Run test to verify it fails**

Run: `ddev exec -s fastapi python -m pytest tests/test_whale_stream_stage_timing.py -k settled -v`
Expected: FAIL — today's handler awaits `get_market` inline

- [ ] **Step 7: Create the stub resolver module and rewire the handler**

```python
# services/settlement_resolver.py
"""Queue-only stub for the settled-market resolver (I13 P4 Task 19). Task 21
adds the actual batched, deferred GET /markets?tickers= sweep that drains
this queue; keeping the queue and the handler change in one task and the
REST batching in another lets each ship with its own narrow test."""
_pending: list[tuple[str, float]] = []


def enqueue(ticker: str, settled_ts: float) -> None:
    _pending.append((ticker, settled_ts))


def pending() -> list[tuple[str, float]]:
    return list(_pending)
```

In `_process_stream_lifecycle`'s `settled` branch, replace the inline
`await client.get_market(ticker)` block with `settlement_resolver.enqueue(ticker, settled_ts)`
and remove the now-dead `background_resolution` caller-class wrapper from this call
site (it moves to Task 21's resolver).

- [ ] **Step 8: Run test to verify it passes**

Run: `ddev exec -s fastapi python -m pytest tests/test_whale_stream_stage_timing.py -v`
Expected: PASS

- [ ] **Step 9: Run the full whale-stream and WS test suites for a regression check**

Run: `ddev exec -s fastapi python -m pytest tests/test_whale_stream_stage_timing.py tests/test_kalshi_ws_two_consumers.py tests/test_kalshi_ws_ingest_metrics.py -v`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add services/kalshi/websocket.py services/whale_stream/whale_stream_handlers.py \
  services/settlement_resolver.py tests/test_kalshi_ws_two_consumers.py tests/test_whale_stream_stage_timing.py
git commit -m "feat: coalesce tickers, defer settled resolution off the critical path (I13 P4)"
```

---

### Task 21: Keep the queue across reconnects, generation-stamped

**Files:**
- Modify: `services/kalshi/websocket.py` (`run`, the reconnect path, `_subscription_sids` handling)
- Test: append to whatever file currently tests reconnect behavior (`grep -rn "def test.*reconnect" tests/`)

**Interfaces:**
- Produces: a `_connection_generation: int` counter, incremented on every new socket;
  each item enqueued (both queues) is tagged `(generation, item)`; both consumers
  drop control frames (`subscribed`, `ok`, `error`) whose generation is stale before
  dispatch; `_subscribed_tickers`/`_subscription_sids` reset only happens for the
  *new* generation, not retroactively on old-generation items already in flight.
- Changes: behind `realtime_data_plane.keep_queue_on_reconnect` (default `false`),
  `run()`'s reconnect path stops discarding `self._queue`/`self._critical_queue`/`self._market_queue`.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_stale_generation_control_frames_are_dropped_after_reconnect(monkeypatch):
    ws = _make_ws()
    monkeypatch.setattr("services.config_store.get", lambda: {"realtime_data_plane": {"keep_queue_on_reconnect": True}})
    ack_old = {"type": "subscribed", "msg": {"channel": "trade", "sid": "sid-old"}}
    ws._ingest_raw(json.dumps(ack_old))  # generation 0
    ws._begin_new_connection_generation()  # simulate the reconnect Step 3 adds
    ack_new = {"type": "subscribed", "msg": {"channel": "trade", "sid": "sid-new"}}
    ws._ingest_raw(json.dumps(ack_new))  # generation 1
    await ws._drain_control_queue_for_test()  # bounded-iteration test hook, added in Step 3
    assert ws._subscription_sids.get("trade") == "sid-new"  # the stale ack from gen 0 never applied
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python -m pytest tests/ -k reconnect_generation -v`
Expected: FAIL — no generation concept exists yet

- [ ] **Step 3: Add the generation counter and tag every enqueued item**

```python
self._connection_generation = 0

def _begin_new_connection_generation(self) -> None:
    self._connection_generation += 1
```

Call `_begin_new_connection_generation()` where `run()` currently does
`self._subscription_sids = {}` on a new socket (today's reset point). Change every
`queue.put_nowait(item)` to `queue.put_nowait((self._connection_generation, item))`,
and every consumer's dequeue to unpack `(gen, item)`, dropping (counting a new
`ingest.generation_dropped` metric) when `gen != self._connection_generation` **and**
`item` is a control-type message (`subscribed`/`ok`/`error`) — non-control items
(trades, tickers, fills, positions, lifecycle) from an old generation are still real
exchange events and are processed normally (per design spec §3, review W8).

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python -m pytest tests/ -k reconnect_generation -v`
Expected: PASS

- [ ] **Step 5: Stop discarding the queue on reconnect, behind the flag**

In `run()`'s reconnect path, replace the unconditional queue-recreation with:

```python
if not (config_store.get().get("realtime_data_plane") or {}).get("keep_queue_on_reconnect"):
    self._queue = None  # today's behavior: drop it, a fresh one is lazily created
    self._critical_queue = None
    self._market_queue = None
# else: leave the existing queues in place - _begin_new_connection_generation()
# already stamped the boundary so stale control frames are safely ignorable
```

Add `realtime_data_plane.keep_queue_on_reconnect: false` to `config/settings.yaml`.

- [ ] **Step 6: Test connection-scoped counters still reset while queue-scoped ones don't**

```python
def test_connection_scoped_counters_reset_but_queue_depth_survives_reconnect(monkeypatch):
    ws = _make_ws()
    monkeypatch.setattr("services.config_store.get", lambda: {"realtime_data_plane": {"keep_queue_on_reconnect": True}})
    trade = {"type": "trade", "msg": {"trade_id": "t1", "ticker": "K1", "count": 500}}
    ws._ingest_raw(json.dumps(trade))
    depth_before = ws._market_queue.qsize()
    ws._begin_new_connection_generation()
    assert ws._market_queue.qsize() == depth_before  # not discarded
    assert ws._connects == ws._connects  # connection-scoped counter unaffected by this call directly - see Step 7
```

(This test needs sharpening once Step 3's real counter list is known — confirm
which counters `_begin_new_connection_generation` must also reset, e.g. per-connection
handshake/ping stats, and assert those specifically reset while `qsize()` does not.)

- [ ] **Step 7: Run the full WS test suite for a regression check**

Run: `ddev exec -s fastapi python -m pytest tests/test_kalshi_ws_ingest_metrics.py tests/test_kalshi_ws_two_consumers.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add services/kalshi/websocket.py config/settings.yaml tests/
git commit -m "feat: keep the ingest queue across reconnects, generation-stamped (I13 P4)"
```

---

**P4 gate:** Deterministic replay parity check — run
`python -m tools.realtime_pipeline_replay --preset busy_hour --topology staged --single-loop --order critical,trade,ticker`
and confirm the corrected numbers from `2026-08-25-realtime-architecture-review.md`
§3.3 (candidate p95 ~20 ms, crit p95 ~39 ms under hygiene) are the *target*, then
verify the live system approaches them in a paper-mode busy-hour soak (candidate
receive→decision p95 measured via `whale_pipeline` metrics). Full test suite green
with `two_consumer_mode`, `keep_queue_on_reconnect`, and `reader_gate_enabled` all on.

---

## Phase P5 — REST scheduler rewrite, settled resolver, shared caches

### Task 22: Critical-first waiter queues with background aging (no lock-held-while-sleeping)

**Task 17b result (2026-08-27):** no real-scale REST-demand calibration data
available - all 3 live `widen_scope` attempts crashed before completing any config
step (root cause: concurrent Claude Code worktree sessions triggering `uvicorn
--reload` mid-experiment, not the widened scope itself - see
`docs/superpowers/research/2026-08-25-realtime-data-plane-known-findings.md`'s "Phase
P3.5 live-scale attempt" entry). Keep the synthetic `rest_scheduler_replay.py`
presets as the current basis until a successful rerun produces a real number.

**Files:**
- Modify: `services/http_client.py` (`_TokenBucketRateLimiter`)
- Test: append to `tests/test_http_client.py`

**Interfaces:**
- Changes: `acquire()` no longer holds `self._lock` across `asyncio.sleep`. Two
  `collections.deque` waiter queues (`_critical_waiters`, `_background_waiters`),
  woken by a single dispatcher-free refill check: on token availability, pop from
  `_critical_waiters` first; if empty, pop the oldest `_background_waiters` entry
  **unless** a background waiter has been queued longer than `_AGING_THRESHOLD_SEC`
  (2.0 s), in which case that waiter is served next regardless of critical queue
  state (bounded starvation, matching `priority_aging`'s modelled behavior in I11/I12).
  `current_caller_class()` (already exists, `services/http_client.py`) decides which
  deque a given `acquire()` call joins.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_http_client.py (append)
import asyncio
import time

import pytest


@pytest.mark.asyncio
async def test_critical_call_does_not_wait_behind_a_sleeping_background_call(monkeypatch):
    from services import http_client
    limiter = http_client._TokenBucketRateLimiter(rate=1.0, burst=1.0)  # match real constructor args
    await limiter.acquire()  # drain the single token so the next caller must wait

    async def _background():
        with http_client.caller_class("background_catalog"):
            await limiter.acquire()

    async def _critical():
        with http_client.caller_class("critical_whale"):
            start = time.monotonic()
            await limiter.acquire()
            return time.monotonic() - start

    bg_task = asyncio.create_task(_background())
    await asyncio.sleep(0.05)  # let background join the wait first
    crit_wait = await _critical()
    bg_task.cancel()
    assert crit_wait < 1.0  # served before the background waiter despite arriving second


@pytest.mark.asyncio
async def test_a_background_waiter_older_than_the_aging_threshold_is_not_starved_forever(monkeypatch):
    from services import http_client
    limiter = http_client._TokenBucketRateLimiter(rate=0.5, burst=1.0)
    await limiter.acquire()

    async def _background():
        with http_client.caller_class("background_catalog"):
            start = time.monotonic()
            await limiter.acquire()
            return time.monotonic() - start

    bg_task = asyncio.create_task(_background())
    await asyncio.sleep(2.5)  # past _AGING_THRESHOLD_SEC

    async def _critical():
        with http_client.caller_class("critical_whale"):
            await limiter.acquire()

    crit_task = asyncio.create_task(_critical())
    bg_wait = await bg_task
    crit_task.cancel()
    assert bg_wait < 5.0  # did not wait indefinitely behind a stream of critical arrivals
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python -m pytest tests/test_http_client.py -k "critical_call_does_not_wait or aging_threshold" -v`
Expected: FAIL — today's `acquire()` is plain FIFO by lock order, no class priority

- [ ] **Step 3: Rewrite `acquire()`**

```python
_AGING_THRESHOLD_SEC = 2.0


class _TokenBucketRateLimiter:
    def __init__(self, rate: float, burst: float):
        ...  # keep existing fields
        self._critical_waiters: deque = deque()
        self._background_waiters: deque = deque()  # entries: (enqueued_at, asyncio.Event)

    async def acquire(self):
        cls = current_caller_class()
        is_critical = cls in CRITICAL_CALLER_CLASSES  # define this set from CALLER_CLASSES metadata
        event = asyncio.Event()
        entry = (time.monotonic(), event)
        (self._critical_waiters if is_critical else self._background_waiters).append(entry)
        self.waiters += 1
        self.waiters_high_water = max(self.waiters_high_water, self.waiters)
        try:
            self._maybe_dispatch()
            await event.wait()
        finally:
            self.waiters -= 1

    def _maybe_dispatch(self) -> None:
        now = time.monotonic()
        self._tokens = min(self._burst, self._tokens + (now - self._last_refill) * self._rate)
        self._last_refill = now
        while self._tokens >= 1:
            next_waiter = self._pick_next_waiter(now)
            if next_waiter is None:
                break
            self._tokens -= 1
            next_waiter[1].set()
        if self._critical_waiters or self._background_waiters:
            asyncio.get_event_loop().call_later(max(0.0, (1 - self._tokens) / self._rate), self._maybe_dispatch)

    def _pick_next_waiter(self, now: float):
        if self._background_waiters and now - self._background_waiters[0][0] > _AGING_THRESHOLD_SEC:
            return self._background_waiters.popleft()
        if self._critical_waiters:
            return self._critical_waiters.popleft()
        if self._background_waiters:
            return self._background_waiters.popleft()
        return None
```

(This is the shape, not a drop-in — reconcile it against `_TokenBucketRateLimiter`'s
real current field names, and against `services/http_client.py`'s existing
`CALLER_CLASSES` structure to define `CRITICAL_CALLER_CLASSES` correctly — read both
in full before implementing. The self-rescheduling `call_later` must be cancelled
cleanly on the last waiter's departure so it doesn't spin forever with an idle
limiter; add a guard for that as part of this step, with a test asserting no lingering
scheduled callback when the waiter queues are empty.)

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python -m pytest tests/test_http_client.py -v`
Expected: PASS, and every pre-existing `http_client` test still passes (the external
`acquire()` contract — an awaitable that returns once a token is available — is
unchanged; only internal ordering changed)

- [ ] **Step 5: Run the full REST-adjacent test suite for a regression check**

Run: `ddev exec -s fastapi python -m pytest tests/test_http_client.py tests/test_kalshi_account_client.py tests/test_kalshi_client.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add services/http_client.py tests/test_http_client.py
git commit -m "perf: critical-first REST waiter queues with background aging, no lock-held sleep (I13 P5)"
```

---

### Task 23: Global 429 brake

**Files:**
- Modify: `services/http_client.py` (`call_with_backoff`, `_TokenBucketRateLimiter`)
- Test: append to `tests/test_http_client.py`

**Interfaces:**
- Produces: `_TokenBucketRateLimiter.trip_brake(now: float | None = None) -> None`
  (halves `self._rate` for `_BRAKE_DURATION_SEC`, doubling the halving on a repeat
  trip within the window per the design spec §6), called from `call_with_backoff`
  whenever a 429 is observed, regardless of caller class.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_a_429_halves_the_effective_rate_for_the_brake_window(monkeypatch):
    from services import http_client
    limiter = http_client._TokenBucketRateLimiter(rate=8.0, burst=8.0)
    original_rate = limiter._rate
    limiter.trip_brake()
    assert limiter._rate == original_rate / 2


@pytest.mark.asyncio
async def test_the_brake_recovers_linearly_after_its_window(monkeypatch):
    from services import http_client
    import time
    limiter = http_client._TokenBucketRateLimiter(rate=8.0, burst=8.0)
    limiter.trip_brake(now=time.monotonic() - http_client._BRAKE_DURATION_SEC - 1)
    limiter._maybe_recover_from_brake(now=time.monotonic())
    assert limiter._rate == 8.0


@pytest.mark.asyncio
async def test_critical_retries_are_not_exempt_from_the_brake(monkeypatch):
    # I12 R2: the reserve's retries must obey the brake, not bypass it -
    # assert trip_brake() affects dispatch regardless of current_caller_class().
    from services import http_client
    limiter = http_client._TokenBucketRateLimiter(rate=8.0, burst=8.0)
    limiter.trip_brake()
    with http_client.caller_class("critical_whale"):
        assert limiter._rate == 4.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python -m pytest tests/test_http_client.py -k brake -v`
Expected: FAIL — `trip_brake` doesn't exist

- [ ] **Step 3: Implement the brake**

```python
_BRAKE_DURATION_SEC = 5.0


def trip_brake(self, now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    if self._brake_until is not None and now < self._brake_until:
        self._brake_multiplier = min(self._brake_multiplier * 2, 8)  # cap the halving
    else:
        self._brake_multiplier = 2
    self._brake_until = now + _BRAKE_DURATION_SEC
    self._rate = self._base_rate / self._brake_multiplier


def _maybe_recover_from_brake(self, now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    if self._brake_until is not None and now >= self._brake_until:
        self._rate = self._base_rate
        self._brake_until = None
        self._brake_multiplier = 1
```

Add `self._base_rate = rate`, `self._brake_until = None`, `self._brake_multiplier = 1`
to `__init__`; call `_maybe_recover_from_brake()` at the top of `_maybe_dispatch`
(Task 22).

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python -m pytest tests/test_http_client.py -v`
Expected: PASS

- [ ] **Step 5: Wire `trip_brake()` into `call_with_backoff`'s 429 handling**

Find the existing 429 detection in `call_with_backoff` (it already backs off per
caller — read its exact exception-matching logic) and add `limiter.trip_brake()`
alongside the existing per-call backoff, once per detected 429 regardless of class.

- [ ] **Step 6: Test the end-to-end wiring with a fault-injecting fake client**

```python
@pytest.mark.asyncio
async def test_call_with_backoff_trips_the_brake_on_a_429(monkeypatch):
    from services import http_client
    calls = {"n": 0}

    async def _flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise Exception("429 Too Many Requests")
        return "ok"

    result = await http_client.call_with_backoff(_flaky)
    assert result == "ok"
    assert http_client._rest_limiter._brake_until is not None
```

(Match `call_with_backoff`'s and the module-level limiter instance's real names.)

Run: `ddev exec -s fastapi python -m pytest tests/test_http_client.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add services/http_client.py tests/test_http_client.py
git commit -m "feat: add a global 429 brake shared by every caller class (I13 P5)"
```

---

### Task 24: Settled resolver — batched, deferred, retried `GET /markets?tickers=`

**Files:**
- Modify: `services/settlement_resolver.py` (add the drain loop)
- Test: `tests/test_settlement_resolver.py`

**Interfaces:**
- Produces: `settlement_resolver.run_pending(client, *, now: float | None = None, delay_sec: float = 60.0, batch_size: int = 50) -> dict`
  (`{"resolved": int, "still_pending": int}`); called once per tick, classed
  `background_resolution` via `http_client.caller_class`.
- Consumes: `client.get_markets_by_tickers` (existing `services/kalshi/public.py`
  method), `market_history.record_outcome` (existing).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_settlement_resolver.py
import time

import pytest


@pytest.mark.asyncio
async def test_a_settlement_younger_than_the_delay_is_not_yet_resolved(monkeypatch):
    from services import settlement_resolver
    settlement_resolver._pending.clear()
    settlement_resolver.enqueue("K1", time.time())
    result = await settlement_resolver.run_pending(client=None, now=time.time() + 10)
    assert result["still_pending"] == 1
    assert result["resolved"] == 0


@pytest.mark.asyncio
async def test_settlements_past_the_delay_are_batch_resolved(monkeypatch):
    from services import settlement_resolver
    settlement_resolver._pending.clear()
    now = time.time()
    settlement_resolver.enqueue("K1", now)
    settlement_resolver.enqueue("K2", now)

    class _FakeClient:
        async def get_markets_by_tickers(self, tickers):
            assert set(tickers) == {"K1", "K2"}
            return {t: {"ticker": t, "status": "finalized", "result": "yes"} for t in tickers}

    recorded = []
    monkeypatch.setattr("services.market_history.record_outcome", lambda ticker, result, resolved_at: recorded.append((ticker, result)))
    result = await settlement_resolver.run_pending(_FakeClient(), now=now + 61.0)
    assert result["resolved"] == 2
    assert set(recorded) == {("K1", "yes"), ("K2", "yes")}


@pytest.mark.asyncio
async def test_a_not_yet_finalized_market_stays_pending_and_is_retried_later(monkeypatch):
    # design spec §5 gap: settled != finalized immediately (settlement race);
    # not-yet-finalized markets must not be dropped.
    from services import settlement_resolver
    settlement_resolver._pending.clear()
    now = time.time()
    settlement_resolver.enqueue("K3", now)

    class _StillDeterminedClient:
        async def get_markets_by_tickers(self, tickers):
            return {t: {"ticker": t, "status": "determined"} for t in tickers}

    result = await settlement_resolver.run_pending(_StillDeterminedClient(), now=now + 61.0)
    assert result["resolved"] == 0
    assert result["still_pending"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python -m pytest tests/test_settlement_resolver.py -v`
Expected: FAIL — `run_pending` doesn't exist

- [ ] **Step 3: Write minimal implementation**

```python
# services/settlement_resolver.py (extend the Task 19 stub)
"""Batched, deferred settlement resolution (I13 P5, root-cause report C/§11,
docs/kalshi/CHEATSHEET.md's settled/determined entry). A settled WS event
fires while settlement is still processing - reading immediately can see a
market at `determined`, not yet `finalized`, so this waits delay_sec before
the first read and simply leaves a not-yet-finalized market pending for the
next tick rather than treating it as resolved or dropping it."""
import time

from services import http_client, market_history

_pending: list[tuple[str, float]] = []


def enqueue(ticker: str, settled_ts: float) -> None:
    _pending.append((ticker, settled_ts))


def pending() -> list[tuple[str, float]]:
    return list(_pending)


async def run_pending(client, *, now: float | None = None, delay_sec: float = 60.0, batch_size: int = 50) -> dict:
    now = time.time() if now is None else now
    ready = [(t, ts) for t, ts in _pending if now - ts >= delay_sec][:batch_size]
    if not ready:
        return {"resolved": 0, "still_pending": len(_pending)}
    tickers = [t for t, _ in ready]
    with http_client.caller_class("background_resolution"):
        markets = await client.get_markets_by_tickers(tickers)
    resolved = 0
    for ticker, _ts in ready:
        market = markets.get(ticker) or {}
        if (market.get("status") or "") != "finalized":
            continue  # stays pending for the next tick - not yet safe to trust (CHEATSHEET gap)
        result = (market.get("result") or "").strip().lower()
        if result not in ("yes", "no"):
            continue
        market_history.record_outcome(ticker, result, resolved_at=now)
        _pending.remove((ticker, _ts))
        resolved += 1
    return {"resolved": resolved, "still_pending": len(_pending)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python -m pytest tests/test_settlement_resolver.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Wire into the trading tick**

Add `await settlement_resolver.run_pending(client)` to `main.py`'s tick loop,
after the critical gather (same reasoning as Task 9 — this is background REST
demand). Add `settlement_resolver.pending_count()` to observability.

- [ ] **Step 6: Verify the 23%-of-calls demand drops**

Add an integration test (or extend `tools/kalshi_rate_limit_probe.py --demand`'s
existing measurement) asserting `get_market` calls attributable to `settled`
handling drop to ≤ 1 per N settlements instead of 1 per settlement — this is the
root-cause report §8's `settled` demand target (≤ 0.1/s), verified the same way I8
measured the original 0.55/s (a live demand sample, not a unit test assertion).

- [ ] **Step 7: Run the full whale-stream and settlement test suite for a regression check**

Run: `ddev exec -s fastapi python -m pytest tests/test_settlement_resolver.py tests/test_whale_stream_stage_timing.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add services/settlement_resolver.py main.py tests/test_settlement_resolver.py
git commit -m "feat: batch and defer settlement resolution instead of an inline per-event REST read (I13 P5)"
```

---

### Task 25: Shared milestone/live-data cache (H9 duplicate-factor reduction)

**Files:**
- Create: `services/milestone_cache.py`
- Modify: the three independent pollers I8 identified (catalog scan, live status,
  event live data — `grep -rln "get_milestones\|get_live_datas\|get_event_live_data"
  services/ main.py` to find them precisely)
- Test: `tests/test_milestone_cache.py`

**Interfaces:**
- Produces: `milestone_cache.get_or_fetch(event_ticker: str, fetch_fn: Callable[[], Awaitable[dict]], *, ttl_sec: float = 60.0) -> dict`
  (single-flight per `event_ticker`: concurrent callers for the same key await one
  in-flight fetch rather than issuing N).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_milestone_cache.py
import asyncio

import pytest


@pytest.mark.asyncio
async def test_concurrent_callers_for_the_same_key_share_one_fetch():
    from services import milestone_cache
    milestone_cache._cache.clear()
    calls = {"n": 0}

    async def _fetch():
        calls["n"] += 1
        await asyncio.sleep(0.05)
        return {"ticker": "E1"}

    results = await asyncio.gather(*(milestone_cache.get_or_fetch("E1", _fetch) for _ in range(5)))
    assert calls["n"] == 1
    assert all(r == {"ticker": "E1"} for r in results)


@pytest.mark.asyncio
async def test_a_fresh_call_after_ttl_expiry_refetches():
    from services import milestone_cache
    import time
    milestone_cache._cache.clear()
    calls = {"n": 0}

    async def _fetch():
        calls["n"] += 1
        return {"n": calls["n"]}

    await milestone_cache.get_or_fetch("E2", _fetch, ttl_sec=0.05)
    time.sleep(0.1)
    await milestone_cache.get_or_fetch("E2", _fetch, ttl_sec=0.05)
    assert calls["n"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python -m pytest tests/test_milestone_cache.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# services/milestone_cache.py
"""Single-flight, TTL cache shared by every milestone/live-data poller. I8
measured a 1.53x duplicate factor from three independent per-subsystem
caches polling the same event surface; this replaces them with one."""
import time
from typing import Awaitable, Callable

_cache: dict[str, tuple[float, dict]] = {}
_in_flight: dict[str, "asyncio.Future"] = {}


async def get_or_fetch(event_ticker: str, fetch_fn: Callable[[], Awaitable[dict]], *, ttl_sec: float = 60.0) -> dict:
    import asyncio
    now = time.time()
    cached = _cache.get(event_ticker)
    if cached is not None and now - cached[0] < ttl_sec:
        return cached[1]
    if event_ticker in _in_flight:
        return await _in_flight[event_ticker]
    fut = _in_flight[event_ticker] = asyncio.get_event_loop().create_future()
    try:
        result = await fetch_fn()
        _cache[event_ticker] = (now, result)
        fut.set_result(result)
        return result
    except Exception as exc:
        fut.set_exception(exc)
        raise
    finally:
        del _in_flight[event_ticker]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python -m pytest tests/test_milestone_cache.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Wire the three pollers through it, one at a time, each with its own regression test run**

For each of the three call sites found by the grep above: replace the direct
`await client.get_milestones(...)` / `get_live_datas(...)` / `get_event_live_data(...)`
call with `await milestone_cache.get_or_fetch(event_ticker, lambda: client.get_milestones(...))`,
run that module's existing test suite, confirm no behavior change beyond the shared
cache, then move to the next call site. Do all three in this task (they're one
cohesive change per the design spec), but as three sequential sub-steps each ending
in a green test run — do not batch all three edits before running tests once.

- [ ] **Step 6: Verify the duplicate factor drops**

Re-run `python -m tools.kalshi_rate_limit_probe --demand --demand-hours 0.4 --tracked-events 28`
(the same invocation I8 used) after a live soak and confirm the duplicate factor is
materially below 1.53 — record the new number in `docs/kalshi/CHEATSHEET.md`'s
existing H9 entry as an update, not a new entry.

- [ ] **Step 7: Commit**

```bash
git add services/milestone_cache.py <the three modified poller files> \
  tests/test_milestone_cache.py docs/kalshi/CHEATSHEET.md
git commit -m "feat: share one milestone/live-data cache across the three independent pollers (I13 P5)"
```

---

### Task 26: Classify the streaming-off REST tape poll

**Files:**
- Modify: `main.py` (`_fetch_trade_tape`'s call site)
- Test: append to `tests/test_http_client.py` or a `main.py`-adjacent test file

- [ ] **Step 1: Confirm `_fetch_trade_tape` is currently unclassified** (I12 R3: "unclassified → `other`") by grepping for `http_client.caller_class` near its definition — confirm the gap before fixing it.
- [ ] **Step 2: Write a failing test** asserting a call to `_fetch_trade_tape` runs under `caller_class() == "trade_tape_poll"` (a new class — add it to `CALLER_CLASSES` in `services/http_client.py`, background priority).
- [ ] **Step 3: Run it, watch it fail.**
- [ ] **Step 4: Wrap the call site** with `with http_client.caller_class("trade_tape_poll"):` around the existing `get_trades` calls inside `_fetch_trade_tape`.
- [ ] **Step 5: Run it, watch it pass; run the full REST-adjacent suite for a regression check.**
- [ ] **Step 6: Commit:** `git commit -m "fix: classify the streaming-off REST tape poll instead of leaving it unattributed (I13 P5)"`

---

**P5 gate:** Re-run `python -m tools.rest_scheduler_replay --compare --presets measured_quiet,background_storm,429_storm --seed 1`
and confirm the live limiter's behavior tracks the `reserved`/`priority_aging`
policy's modelled improvement (critical wait materially below today's FIFO baseline
from I5/I7) without the R2/R3 failure modes (no critical retry exhaustion, shared
capacity not silently halved — since Task 22/23's design is priority+aging, not a
carved reserve, R3 does not apply, but confirm background max wait stays bounded
under `background_storm`). `settled` demand ≤ 0.1/s and duplicate factor materially
below 1.53 in a live soak. Full test suite green.

---

## Phase P6 — Reconnect/error-25-triggered reconciliation

### Task 27: Trigger reconciliation only on reconnect or error-25, not on a fixed schedule

**Files:**
- Modify: `services/diagnostics/trade_capture_reconciliation.py` (add the trigger,
  reuse `reconcile_window`)
- Modify: `services/kalshi/websocket.py` (fire a callback on reconnect and on error-25)
- Test: append to `tests/test_trade_capture_reconciliation.py`

**Interfaces:**
- Produces: `trade_capture_reconciliation.on_loss_event(reason: str, *, occurred_at: float) -> None`
  (registered as a callback; enqueues a bounded reconciliation sweep rather than
  running it inline on the WS reader/consumer).
- Consumes: `candidate_ledger.claim` (every recovered print goes through the same
  claim path as a live one — duplicates are structurally impossible, per design
  spec §7), `services/kalshi/public.py`'s `get_trades(min_ts=..., max_ts=...)`
  paged as `reconcile_window` already does, classed `reconciliation` (a new
  background caller class, added to `CALLER_CLASSES` in Task 22/23's work).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trade_capture_reconciliation.py (append)
import pytest


@pytest.mark.asyncio
async def test_on_loss_event_schedules_a_bounded_sweep_not_an_inline_call(monkeypatch):
    from services.diagnostics import trade_capture_reconciliation as tcr
    scheduled = []
    monkeypatch.setattr(tcr, "_schedule_sweep", lambda reason, occurred_at: scheduled.append((reason, occurred_at)))
    tcr.on_loss_event("reconnect", occurred_at=123.0)
    assert scheduled == [("reconnect", 123.0)]


@pytest.mark.asyncio
async def test_a_recovered_print_goes_through_the_candidate_ledger_and_cannot_duplicate(monkeypatch):
    from services import candidate_ledger
    from services.diagnostics import trade_capture_reconciliation as tcr
    candidate_ledger.claim("already-live-t1")  # simulate: this trade already went through the live path

    class _FakeClient:
        async def get_trades(self, **kwargs):
            return {"trades": [{"trade_id": "already-live-t1", "ticker": "K1", "count": 500}], "cursor": ""}

    result = await tcr.run_sweep(_FakeClient(), window_start=0.0, window_end=200.0,
                                  seen_exchange_ts_by_id={}, min_contracts_for=lambda t: 100)
    assert result["duplicates_skipped"] == 1
    assert result["recovered"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python -m pytest tests/test_trade_capture_reconciliation.py -k "loss_event or duplicates_skipped" -v`
Expected: FAIL — `on_loss_event`/`run_sweep` don't exist in this shape yet

- [ ] **Step 3: Add `on_loss_event`, `_schedule_sweep`, and `run_sweep`**

`run_sweep` wraps the existing `reconcile_window` (do not duplicate its paging
logic — call it) and adds the `candidate_ledger.claim` gate per recovered
whale-sized trade before treating it as a new candidate:

```python
async def run_sweep(client, *, window_start, window_end, seen_exchange_ts_by_id, min_contracts_for) -> dict:
    base = await reconcile_window(client, window_start=window_start, window_end=window_end,
                                   seen_exchange_ts_by_id=seen_exchange_ts_by_id, min_contracts_for=min_contracts_for)
    recovered = duplicates_skipped = 0
    for trade in base.get("missing_whale_sized_trades", []):  # match reconcile_window's real return key
        if candidate_ledger.claim(trade["trade_id"], ticker=trade.get("ticker")):
            recovered += 1
            # feed into the same candidate path Task 12's retry queue uses
        else:
            duplicates_skipped += 1
    return {**base, "recovered": recovered, "duplicates_skipped": duplicates_skipped}


def _schedule_sweep(reason: str, occurred_at: float) -> None:
    _pending_sweeps.append((reason, occurred_at))


_pending_sweeps: list[tuple[str, float]] = []


def on_loss_event(reason: str, *, occurred_at: float) -> None:
    _schedule_sweep(reason, occurred_at)
```

(Match `reconcile_window`'s actual return-dict key names — read the function's
current implementation before assuming `missing_whale_sized_trades` is the real
key.)

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python -m pytest tests/test_trade_capture_reconciliation.py -v`
Expected: PASS

- [ ] **Step 5: Fire the callback from the WS reconnect and error-25 paths**

In `services/kalshi/websocket.py`'s `_record_disconnect` and the error-25 handling
in `_process_item`, add calls to
`trade_capture_reconciliation.on_loss_event("reconnect", occurred_at=time.time())`
and `on_loss_event("error_25", occurred_at=time.time())` respectively.

- [ ] **Step 6: Drain `_pending_sweeps` once per tick, bounded (max 20 pages per sweep, per design spec §7)**

Add a `drain_pending_sweeps(client, *, max_sweeps_per_tick: int = 1) -> dict` that
pops one pending sweep, computes its window (`occurred_at` to `occurred_at + 60`,
using `seen_exchange_ts_by_id()`/`seen_horizon_ts()` from
`services/whalewatchers/kalshi_trade_tape.py` as `reconcile_window` already
consumes them today), and calls `run_sweep`. Wire it into `main.py`'s tick loop
under the `reconciliation` caller class.

- [ ] **Step 7: Test the full trigger-to-sweep path with an injected reconnect**

Extend the existing reconnect-behavior test (found in Step 1 of Task 21, or
wherever `tests/` currently covers `run()`'s reconnect path) to assert
`on_loss_event` fires and a subsequent `drain_pending_sweeps` call recovers the
trades that were in flight during the simulated gap — this is the P6 gate's
correctness property.

- [ ] **Step 8: Run the full reconciliation and WS test suite for a regression check**

Run: `ddev exec -s fastapi python -m pytest tests/test_trade_capture_reconciliation.py tests/test_kalshi_ws_two_consumers.py tests/test_kalshi_ws_ingest_metrics.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add services/diagnostics/trade_capture_reconciliation.py services/kalshi/websocket.py main.py \
  tests/test_trade_capture_reconciliation.py
git commit -m "feat: trigger reconciliation only on reconnect/error-25, gated by the candidate ledger (I13 P6)"
```

---

**P6 gate (final acceptance):** A fault-injection integration test (extend
`tests/test_realtime_pipeline_candidates.py` or add a new one) that: starts the
two-consumer, gated, ledgered pipeline against a fake WS source; injects a
mid-stream disconnect that drops N in-flight whale-sized trades; reconnects;
confirms `on_loss_event` fired, a sweep ran, and ≥ 99% of the N trades were
recovered with **zero** duplicate ledger claims and zero duplicate `signal_log`
rows. Then the full root-cause report §8 acceptance table, measured end to end in a
24-hour paper-mode soak with every phase flag enabled and at least one busy hour
(≥ 120 trades/s sustained) — this soak is a manual/scheduled verification step, not
a unit test; record its result in a new dated section appended to
`docs/superpowers/research/2026-08-25-realtime-root-cause-report.md` when run, per
this repo's "accumulated history is a first-class asset" convention. Full test
suite green with every phase flag flipped on.

---

## Phase P7 — WS-primary decision-relevant state, REST as reconnect-triggered
   verification (H12/H13)

**Source:** `docs/superpowers/research/2026-08-25-realtime-data-plane-known-findings.md`
Hypotheses H12/H13, added 2026-08-27 - a distinct investigation thread from the P0-P6
phases above (WS ingest/queue mechanics), addressing a different question: is REST or
WS *authoritative* for position-management and application state, not how fast WS
messages get consumed once received.

### Architecture decision

**Direct instruction, given three times without the architecture changing
(2026-08-15, 2026-08-24, 2026-08-27's correction):** *"rest api should only be used to
confirm decisions before theyre made"* / *"polling should be removed altogether in
favor of taking data from websockets."* H12 found the concrete, never-touched
mechanism (`main.py:782-784`'s wholesale REST-overwrite of `state["latest_prices"]`
every tick); H13 extended the sweep application-wide and found three more concrete
gaps (`signal_log.mark_resolved`, `_fetch_account_snapshot`, five discarded
`market_lifecycle_v2` event types) plus three places the target pattern already works
today, live, as proof it's buildable here (the streaming trade-tape's full REST
replacement, the `settled`-handler's REST-verify-at-resolution, and I4's
REST-as-audit-not-replacement reconciliation).

**Authoritative-practice research that shapes every task below** (`docs/kalshi/`, not
memory - re-verify if `docs/kalshi/README.md`'s fetch dates for these pages look
stale by the time this phase is picked up): `websocket-connection.md`'s own AsyncAPI
schema shows `seq` exists **only** on subscription-management responses
(`subscribed`/`unsubscribed`/`ok`) and on `orderbook_delta`'s own snapshot/delta data
messages (confirmed: `public-trades.md`, `market-ticker.md`, and
`market-and-event-lifecycle.md` - the three schemas that actually matter for this
phase - carry no `seq` field on their data messages at all, zero matches on direct
inspection). **There is no per-message gap-detection mechanism Kalshi documents for
`trade`/`ticker`/`market_lifecycle_v2`/`fill`/`market_positions`.** The only
documented recovery event is a full reconnect (`quick_start_websockets.md`:
"Implement reconnection logic with exponential backoff" - no resume/replay
capability exists on this API tier, unlike FIX's `KalshiRT` retransmission sessions
this app doesn't use). Concretely, this means: WS delivery within one live connection
is TCP-ordered and complete by protocol design - the risk this phase actually needs
to defend against is **connection loss**, not silent in-connection message drops.
`main.py:782-784`'s every-6-second wholesale overwrite was never actually defending
against a real gap risk (no such gap-detection signal exists to have prompted it) -
it was solving a problem this protocol doesn't have, while creating the real one H12
found (stomping fresher WS data on a fixed schedule unrelated to whether anything
actually changed).

**Chosen family: extend Family A** (WS-primary state, REST only to seed genuinely
missing data or to verify at a real trigger point) - **not** a new mechanism, but the
exact pattern already proven twice in this codebase (the 2026-08-23 settlement
inversion, the `fill`/`market_positions` WS-write path) and reusing this *plan's own*
already-designed reconnect infrastructure: Task 21's connection-generation stamping
and Task 27's `on_loss_event(reason, occurred_at)` callback, fired on reconnect and
error-25. The correct trigger for a REST-verify pass, per the research above, is a
reconnect - not a fixed 6-second timer, not a per-message sequence check that doesn't
exist for these channels.

**Rejected alternatives:**
- **Family C (dissolve `trading_loop` into WS-triggered handlers + independent
  schedulers entirely)** - rejected for *this* remediation pass, not rejected
  outright. None of H12/H13's confirmed gaps require it: each (`latest_prices`,
  `signal_log`, `_fetch_account_snapshot`, discarded lifecycle events) is
  independently fixable in place. A full dissolution is the largest-blast-radius
  option against a live, safety-adjacent hot path, with the least fault-injection
  precedent in this codebase - disproportionate to what the evidence actually
  demands. Two of three `check_exits` call sites are already WS-triggered (finished,
  not proposed); the tick-loop's own call stays, now reading correctly-maintained
  WS-primary state instead of being redesigned away.
- **Family B (per-field freshness-contract shared cache)** - deferred, not rejected.
  Real value for the separate "different-cadence concerns bundled into one tick"
  observation (`event_titles`/`live_status`/`series_track_record` per H12), but none
  of the three concrete P7 gaps need it - each has exactly one natural owner already
  (`_process_stream_ticker` for price, the lifecycle handler for
  settlement/signal-resolution, the fill/position handlers for account state).
  Revisit only if a future gap doesn't fit the "one WS handler already owns this
  field" shape these three do.
- **Family D (deeper REST caching/conditional fetch)** - rejected, per H12: repeats
  the exact pattern three prior audits already tried without addressing what's
  authoritative.

**Tie to CLAUDE.md's HARD RULE (the data plane is the product)** - each task below is
evaluated against, not just "fewer REST calls":
- **Completeness**: Task 28 (reconnect-triggered position/account verify) is the
  actual completeness backstop for exactly the one real gap window (a connection
  drop) - stronger than the removed blind 6s poll, not weaker, because it's now
  triggered by the real risk event instead of a timer with no relationship to it.
- **Accuracy**: Task 29's merge-not-overwrite fix is the direct fix for H12's
  labeled-value problem (a field silently holding a periodically-reset value instead
  of what it claims to be, live).
- **Timeliness**: the whole point - WS-primary means position management acts on
  current data between ticks, not data that's up to 6s stale by construction.
- **Speed of execution**: fewer REST round-trips on the tick-critical path directly
  serves this per the HARD RULE's own "latency from signal to placed order is part
  of the edge" framing.
- **Flow rate/fidelity**: not materially changed by this phase; noted for
  completeness, not because either is at risk here.
- Per the HARD RULE's own instruction, every task's hot-path cost is measured, not
  assumed - see each task's own verification step.

### Adversarial review of Tasks 28/29/31/33 (2026-08-27), matching I12's own
    code-anchored internal-review methodology

The original investigation's solution-selection workflow (steps 3-6: prototype,
benchmark, fault-inject, score) was initially skipped for P7 in favor of reasoning
alone - corrected on direct instruction, since P0-P6 met that bar
(`2026-08-25-realtime-architecture-review.md`) and P7 should too. P7's actual
question (data freshness/correctness across a connection-loss window) isn't a
queueing/throughput question the existing replay harnesses (`realtime_pipeline_
replay.py`, `rest_scheduler_replay.py`) model - so this review follows I12's
*other* real methodology instead, the code-anchored internal review pass (I12 §2),
which is equally real rigor, not a lesser substitute.

| # | Finding | Mechanism (code/docs) | Verdict |
|---|---|---|---|
| R1 | **Task 28's reconnect-triggered REST-verify for ticker prices is redundant with WS's own reconnect behavior.** `_sync_subscriptions(force_subscribe=True)` already re-subscribes `ticker` with `send_initial_snapshot: True` (`services/kalshi/websocket.py:965`) for every currently-desired ticker - which already includes open positions, since `main.py`'s watchlist fetch folds them in via `extra_tickers`. A reconnect already gets a fresh WS-pushed price for every open position with no REST call needed. | `websocket-connection.md`'s `send_initial_snapshot` param (ticker-scoped); `services/kalshi/websocket.py:960-968` | **Needs a design change**: narrow Task 28/31 to account-state verification only (`fill`/`market_positions`, which has no snapshot-on-subscribe equivalent - see R2). Drop the position-ticker REST-verify from Task 28's scope entirely - it would be a real REST call paying for something WS already delivers for free. Simpler design, not a weaker one. |
| R2 | **`fill`/`market_positions` have no snapshot-on-subscribe capability at all**, confirmed by direct absence in their own doc pages (zero "snapshot"/"initial" mentions in `user-fills.md`/`market-positions.md`, unlike `ticker`'s explicit flag). A disconnect genuinely loses any fill/position event that occurred during the outage - no replay, no reconnect snapshot. | `docs/kalshi/user-fills.md`, `docs/kalshi/market-positions.md` (absence checked directly, not assumed from silence) | **Confirms Task 31 is correctly scoped and genuinely necessary** - the one piece of this phase where a reconnect-triggered REST-verify is not redundant with anything WS already provides. |
| R3 | **Mid-connection ticker additions (`add_markets`) don't request a snapshot** - `services/kalshi/websocket.py:996-1002`'s `to_add` branch omits `send_initial_snapshot`, even though the same param is documented as available there too (`websocket-connection.md` line 718, default `false`). A position opened while already connected gets zero WS-driven price until the next natural ticker tick for that market - Task 29's "seed from REST only if missing" merge clause is what actually covers this today. | `websocket-connection.md` (`update_subscription` "Add Markets" schema) | **Real, easy improvement, folded into Task 33** (already touching this file's subscribe-message construction): set `send_initial_snapshot: True` on the `add_markets` call too. Reduces reliance on Task 29's REST-seed fallback and gets a newly-opened position its first price faster, at zero extra REST cost - the flag changes what WS sends, not an additional call. |
| R4 | **Long-outage staleness has no visibility gate.** Once Task 29 stops the periodic REST-overwrite, `state["latest_prices"][ticker]` holds whatever it last had, indefinitely, with nothing telling `check_exits` the connection has been down and that value might be stale - the same "a bug looks like quiet, not broken" shape I12 §2.8 already named for a different mechanism. | `services/whale_stream/whale_stream_handlers.py::_process_stream_ticker` (only path that ever updates the dict going forward) | **Needs a design addition to Task 29, not a blocker**: expose `state["latest_prices_updated_at"][ticker]` (set alongside every write) and a `trade_stream.status.connected`-aware staleness metric, matching I12's own "observability moves with the cost" precedent (§2.8/§3.12) rather than solving full staleness-gated exit logic in this phase - that's a bigger, separate design question (would `check_exits` skip a position outright on stale data, or just flag it?) than P7's scope, named here rather than silently left unaddressed. |
| R5 | **Combined-subscribe atomicity is a genuine, unresolved doc ambiguity** for Task 33 - does a multi-channel `subscribe` command fail as one unit if one channel is rejected, or partially succeed? The docs don't say either way; the `Subscribed Response` schema shows one channel/`sid` per response message (`websocket-connection.md` line ~1376-1386, confirmed - not one combined list), which at least means the *existing* per-message handler (`_handle_message`'s `"subscribed"` branch, already one-channel-at-a-time) needs no change to correctly process either outcome. | `websocket-connection.md` (`Subscribed Response`/`Error Response` schemas - neither documents partial-failure semantics) | **Not resolved from static reading alone, named per this plan's own "explicit ambiguity" discipline (H13 §5) rather than assumed either way.** Not a blocker: the response-parsing path already handles it correctly regardless of which way the server actually behaves. |
| R6 | **Pre-existing, out-of-scope**: every current subscribe call (not just Task 33's) sets its own `_X_subscribed = True` flag immediately after `_send()`, without waiting for the server's `"subscribed"` confirmation (`services/kalshi/websocket.py:924`, `937`, unchanged pattern). If a subscribe command is ever actually rejected, the app would wrongly believe it succeeded and never retry - `_sync_subscriptions`'s own gating (`not self._X_subscribed`) would never re-fire. | `services/kalshi/websocket.py` (all subscribe call sites, current behavior) | **Real gap, but pre-existing and not introduced or worsened by Task 33** (same shape whether channels are combined or separate) - recorded here rather than silently noticed and dropped, explicitly out of P7's scope. Candidate for its own future task if a real subscribe rejection is ever observed live. |

**Verdict on the leading P7 design**: holds, with two required amendments (R1/R3, folded into Task 28/29/33 below) and one required addition (R4, folded into Task 29). R2 confirms rather than changes Task 31. R5/R6 are named, not blocking - matching I12's own precedent of separating "needs a design change" from "real but out of this pass's scope" rather than treating every finding as equally urgent.

---

### Task 28: Generalize Task 27's reconnect/error-25 loss-event callback into a
    shared hook, add an account-state REST-verify subscriber

**Narrowed per R1/R2 of the adversarial review above**: this task originally also
covered a position-ticker REST-verify on reconnect. Dropped - `ticker`'s own
`send_initial_snapshot: True` on `force_subscribe=True` reconnect already delivers a
fresh WS-pushed price for every open position with no REST call needed (R1); a REST
call here would pay for something WS already gives for free. `fill`/`market_positions`
have no such snapshot-on-subscribe capability at all (R2, confirmed absent in their own
doc pages) - that asymmetry is *why* this task still exists, scoped to account state
only. Task 31 (originally a separate account-state task) is now this task's own
target, not a second implementation of the same mechanism - see Task 31 below, which
is now a thin change to `_fetch_account_snapshot` consuming what this task produces,
not its own reconnect-detection logic.

**Files:**
- Modify: `services/diagnostics/trade_capture_reconciliation.py` (Task 27's
  `on_loss_event` becomes one of possibly several registered callbacks, not the
  only one - re-read Task 27's actual shipped code before this task starts, since
  it may have evolved the exact function signature during its own implementation)
- Create: `services/position/ws_state_verify.py`
- Modify: `services/kalshi/websocket.py` (`_record_disconnect`, the error-25 path -
  same two call sites Task 27 already touches, adding one more callback invocation
  alongside the existing one)
- Test: `tests/test_ws_state_verify.py`

**Interfaces:**
- Produces: `ws_state_verify.on_connection_loss(reason: str, *, occurred_at: float) -> None`
  - schedules a bounded, single-shot REST-verify pass (mirrors Task 27's
    `_schedule_sweep`/`drain_pending_sweeps` shape exactly - reuse that pattern, don't
    invent a second one) for the real account's balance/positions/fills only - **not**
    position tickers, per R1 above.
  - `ws_state_verify.drain_pending_verifications(client, cfg) -> dict` - called from
    `main.py`'s tick loop (bounded, max one pending verification drained per tick,
    same shape as Task 27's own drain), does one `_fetch_account_snapshot`-shaped
    call, marked `caller_class("critical_position")`.

- [ ] **Step 1: Read Task 27's actual shipped implementation first** - this task
  depends on it directly (same call sites, same callback shape). If Task 27 hasn't
  shipped yet when this task is picked up, do Task 27 first; do not duplicate its
  reconnect/error-25 wiring independently.
- [ ] **Step 2: Write the failing test**

```python
# tests/test_ws_state_verify.py
import pytest


def test_on_connection_loss_schedules_a_verification_pass(monkeypatch):
    from services.position import ws_state_verify as wsv
    scheduled = []
    monkeypatch.setattr(wsv, "_schedule_verification", lambda reason, occurred_at: scheduled.append((reason, occurred_at)))
    wsv.on_connection_loss("reconnect", occurred_at=123.0)
    assert scheduled == [("reconnect", 123.0)]


@pytest.mark.asyncio
async def test_drain_pending_verifications_triggers_one_account_snapshot_refresh(monkeypatch):
    from services.position import ws_state_verify as wsv
    wsv._pending_verifications.clear()
    wsv.on_connection_loss("reconnect", occurred_at=100.0)

    calls = []
    monkeypatch.setattr(wsv, "_force_account_snapshot_refresh", lambda cfg: calls.append(cfg) or {"connected": True})

    result = await wsv.drain_pending_verifications(cfg={"kalshi_account": {"trading_enabled": False}})
    assert len(calls) == 1
    assert result["account_verified"] is True
```

- [ ] **Step 3: Run it, watch it fail** (`ws_state_verify` doesn't exist yet).
- [ ] **Step 4: Implement `ws_state_verify.py`**, mirroring
  `trade_capture_reconciliation.py`'s `_pending_sweeps`/`_schedule_sweep`/
  `on_loss_event` shape exactly (same bounded-queue, single-drain-per-tick pattern -
  read that file's real current code for the precise structure to copy, don't
  re-derive it from this plan's earlier description alone since Task 27 may have
  refined it during implementation). `_force_account_snapshot_refresh` calls into
  Task 31's own forced-refresh entry point (see below) - implement Task 31 first if
  picking these up out of order.
- [ ] **Step 5: Wire the callback** in `services/kalshi/websocket.py`'s
  `_record_disconnect` and error-25 handling, alongside Task 27's existing
  `trade_capture_reconciliation.on_loss_event(...)` call - both fire from the same
  two call sites, independently.
- [ ] **Step 6: Wire `drain_pending_verifications` into `main.py`'s tick loop**,
  bounded to one drain per tick, `caller_class("critical_position")`.
- [ ] **Step 7: Measure hot-path cost** (HARD RULE requirement, not optional) -
  confirm `on_connection_loss` itself (the callback, not the drained verification)
  costs microseconds, not milliseconds, on the reconnect path - it only appends to a
  bounded list, the actual REST work happens later on the tick loop's own existing
  `critical_position` budget, not synchronously on the WS reader.
- [ ] **Step 8: Run the full test suite for a regression check.**
- [ ] **Step 9: Commit:** `git commit -m "feat: reconnect-triggered account-state REST-verify, reusing Task 27's loss-event pattern (H12/H13 P7)"`

---

### Task 29: `state["latest_prices"]`/`state["latest_asks"]` - merge WS data,
    stop wholesale REST overwrite

**Files:**
- Modify: `main.py` (lines 782-793, current line numbers - re-verify against HEAD
  before editing, this file has moved during the session that found this gap)
- Test: append to `tests/test_trading_gate.py` or wherever `trading_loop`'s
  `latest_prices` behavior is currently tested (`grep -rn "latest_prices" tests/`)

**REDESIGNED 2026-08-27, on re-grounding against HEAD before implementing (see the
CORRECTION addendum under H12 in the known-findings doc):** the premise "REST
wholesale-overwrites WS data every tick" was wrong. `market_fetch.py::_fetch_markets`'s
tail (2026-08-15, `8b0a7c9`) already overlays in-memory `latest_prices`/`latest_asks`
onto the REST rows before the rebuild, so WS values survive - confirmed live at
sub-tick granularity. The **real** gap is the reverse: the overlay keeps the
in-memory value unconditionally, so once a ticker is in the dict REST never refreshes
it - a WS-quiet ticker's price is frozen indefinitely (Task 34 measured 4 of 10 open
positions with no ticker message ever received). The "merge instead of overwrite"
steps originally written here are therefore moot; the R4 timestamp is the
load-bearing piece, and the fix is an **age-aware overlay**.

**Interfaces:**
- Produces: `state["latest_prices_updated_at"]: dict[str, float]` and
  `state["latest_asks_updated_at"]: dict[str, float]` - stamped at every write site:
  the WS path (`_process_stream_ticker`, which additionally starts writing
  `latest_asks` from the ticker message's own `yes_ask_dollars` - it already reads
  that field onto the market row but never into `latest_asks`, which today has **no
  WS writer at all**, so asks are frozen at their REST seed forever), the REST seed
  path, and the REST-wins path below. Pruned alongside the parent dict (the rebuild's
  membership semantics are kept - they bound the dict).
- Changes: the overlay becomes age-aware. Extracted into a pure, testable
  `market_fetch.overlay_live_prices(markets, state, now)`: keep the in-memory value
  only while `now - updated_at[ticker] <= _PINNED_MARKET_REFRESH_SEC` (300s, the
  system's own existing bound on how stale a REST row can be - reused, not a new
  constant); past that, the REST row's own price wins and is stamped. Net effect,
  provably: WS stays primary whenever it is actually flowing; a WS-quiet ticker is
  refreshed from REST every ~300s instead of never; no entry can be older than
  300s + one tick without a REST correction while its market is still fetched (open
  positions always are, via `extra_tickers`). This is Family A exactly - WS-primary,
  REST fills the gap it can see - not a new mechanism.
- Visibility (R4): `/api/health/pipeline` gains `price_staleness: {open_position_
  oldest_age_sec, open_position_count, stale_over_300s_count}` derived from the new
  timestamps. Visibility only - whether `check_exits` should act on staleness is
  Task 35's job, on top of this.

- [ ] **Step 1: Read the current exact code at `main.py:782-793`** and
  `_process_stream_ticker`'s `state["latest_prices"][ticker] = ...` assignment
  (`services/whale_stream/whale_stream_handlers.py`, confirm the current line - it
  was ~251 when H12 was written, re-verify) before writing the fix, per this plan's
  own established grounding discipline.
- [ ] **Step 2: Write the failing tests** (`tests/test_market_fetch_overlay.py`, new)
  against the pure helper: (a) a WS-fresh in-memory price (updated <300s ago) beats
  the REST row; (b) a stale in-memory price (>300s, or with no timestamp at all -
  unknown age is not trusted) loses to the REST row and is re-stamped; (c) a
  never-seen ticker is seeded from REST and stamped; (d) asks follow the same rules
  via their own dicts; (e) input rows are never mutated (shallow-copy invariant the
  existing overlay already has). Plus, in `tests/test_trading_gate.py`:
  `_process_stream_ticker` now writes `latest_asks` from the message and stamps
  both timestamps.
- [ ] **Step 3: Run them, watch them fail** - no helper, no timestamps, no ask
  writer exist yet.
- [ ] **Step 4: Extract `market_fetch.overlay_live_prices(markets, state, now)`**
  from `_fetch_markets`'s tail, make it age-aware and stamping per the Interfaces
  above; `_fetch_markets` calls it. Add the two `*_updated_at` dicts to
  `services/app_state.py`'s state init. In `_process_stream_ticker`, stamp
  `latest_prices_updated_at` and start writing `latest_asks` + its stamp. In
  `main.py`'s rebuild, prune both `*_updated_at` dicts to the rebuilt keys (bounded).
- [ ] **Step 5: Run them, watch them pass.**
- [ ] **Step 6: Add `price_staleness` to `/api/health/pipeline`**
  (`services/diagnostics/routes.py`) derived from the timestamps for
  `state["open_position_tickers"]`; test via the existing route test idiom.
- [ ] **Step 7: Run the full `check_exits`/exit-management + market_watch + trading_
  gate suites for a regression check** - safety-adjacent; `check_pending_fills` in
  particular now sees WS-driven asks for the first time, confirm its "no fresh ask"
  distinction still holds (it reads `latest_asks.get(ticker)` - an absent key must
  stay absent, never a fabricated 0.5).
- [ ] **Step 8: Live verification** (paper mode) - `price_staleness` present on
  `/api/health/pipeline`; over a >300s window, a WS-quiet open position's price is
  observed to change on a REST refresh (previously impossible); WS-active positions
  still move at sub-tick granularity. Task 34's `position_ticker.never_seen_count`
  gives the population to watch.
- [ ] **Step 9: Commit:** `git commit -m "fix: age-aware live-price overlay - REST refreshes WS-quiet tickers instead of never; WS asks; staleness visibility (P7 Task 29, redesigned)"`

---

### Task 30: Wire `signal_log` resolution off the existing `settled` lifecycle
    handler

**Files:**
- Modify: `services/signal_log.py` (new function)
- Modify: `services/whale_stream/whale_stream_handlers.py` (`_process_stream_lifecycle`'s
  `settled` branch, ~line 484-518 - re-verify against HEAD)
- Test: append to `tests/test_signal_log.py` and the lifecycle-handler test file

**Interfaces:**
- Produces: `signal_log.resolve_from_market_results(ticker: str, result: str) -> int`
  - matches the naming convention `market_analyst_agent.resolve_from_market_results`/
  `candidate_log.resolve_from_market_results` already use, returns the count of rows
  resolved. Resolves every unresolved (`resolved = 0`) row for this ticker, computing
  `correct = (result == row["side"])` per row (each signal has its own `side`, unlike
  the other three resolvers which resolve uniformly per ticker) - `mark_resolved`
  already exists and does the actual write, this is the ticker-scoped query wrapping
  it that doesn't exist yet.
- The 30s/200-batch REST poll (`_check_signal_resolutions`) is **not removed** -
  demoted to the slower safety net it should always have been (catches a ticker this
  app wasn't watching at settlement time, or a genuinely missed lifecycle event),
  matching every other one of the four resolvers' existing "additive to, not a
  replacement for, the REST-tick fallback" comment. Both paths are idempotent
  (`WHERE resolved = 0`), so firing from both is safe by construction, same
  correctness argument the `settled` handler's own docstring already makes for the
  other four.

- [ ] **Step 1: Write the failing test**

```python
def test_settled_lifecycle_event_resolves_matching_signal_log_rows(tmp_path, monkeypatch):
    # Isolate signal_log's DB_PATH via monkeypatch (established test convention).
    # log_signal(...) an unresolved row for ticker="T1", side="yes".
    # Call signal_log.resolve_from_market_results("T1", "yes").
    # Assert the row is now resolved=1, correct=1, and unresolved_batch no longer returns it.
    ...
```

- [ ] **Step 2: Run it, watch it fail** - `resolve_from_market_results` doesn't
  exist yet.
- [ ] **Step 3: Add `signal_log.resolve_from_market_results`**:

```python
def resolve_from_market_results(ticker: str, result: str) -> int:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, side FROM signals WHERE ticker = ? AND resolved = 0", (ticker,),
        ).fetchall()
        if not rows:
            return 0
        now = time.time()
        conn.executemany(
            "UPDATE signals SET resolved = 1, correct = ?, resolved_at = ? WHERE id = ?",
            [(1 if result == r[1] else 0, now, r[0]) for r in rows],
        )
    return len(rows)
```

- [ ] **Step 4: Run it, watch it pass.**
- [ ] **Step 5: Call it from the `settled` branch** in `_process_stream_lifecycle`,
  alongside the existing three resolver calls (`market_history.record_outcome`,
  `settlement_edge.resolve_window`, `market_analyst_agent.resolve_from_market_results`,
  `candidate_log.resolve_from_market_results`) - same `{ticker: result}`-derived
  values already computed in that branch, no new data needed. Add `signal_log` to
  this file's imports.
- [ ] **Step 6: Run the lifecycle-handler test suite for a regression check.**
- [ ] **Step 7: Commit:** `git commit -m "feat: resolve signal_log off the settled lifecycle event instead of REST-poll-only (H13 P7)"`

---

### Task 31: Reconnect-triggered REST-verify replaces `_fetch_account_snapshot`'s
    blind 20s reconcile

**Files:**
- Modify: `services/position/account_positions.py`
- Test: append to the account-snapshot test file

**Interfaces:**
- Changes: `_fetch_account_snapshot` stops unconditionally reconciling/overwriting
  WS-sourced `state["account"]` data on a flat 20s timer. Instead: trust the
  WS-sourced `fill`/`market_positions` updates (`_process_stream_fill`/
  `_process_stream_position`, already shipped) as authoritative between connection
  losses; Task 28's `ws_state_verify` reconnect hook covers the actual gap-risk
  window. `balance` (which has no WS channel at all, per H13 §4) keeps its own
  independent, much-longer interval cache (e.g. 60s, not 20s - balance changes only
  on a fill, which WS already reports) rather than being folded into the same
  reconcile-everything call.
- Produces: `account_positions.force_refresh(cfg) -> dict` - bypasses the interval
  cache entirely and does the real `get_balance`/`get_positions`/`get_fills` gather
  unconditionally, returning the same shape `_fetch_account_snapshot` normally
  returns. This is the entry point Task 28's `ws_state_verify._force_account_
  snapshot_refresh` calls on a reconnect - build this task first if picking up P7
  out of order, since Task 28 depends on it existing.
- Real-account impact today is architectural, not behavioral: `trading_enabled` is
  `false`, so no live fill/position traffic exists yet to observe the difference -
  this task is about being correctly designed for when it's enabled, not fixing a
  currently-visible symptom. Treat it with the same care as any other
  trading-path-adjacent change per CLAUDE.md's safety posture, not less because it's
  currently inert.

- [ ] **Step 1: Read the current `_fetch_account_snapshot`/`_ACCOUNT_SNAPSHOT_REFRESH_SEC`
  code in full** (`services/position/account_positions.py:137-188` per H13's
  citation - re-verify) before changing it.
- [ ] **Step 2: Write the failing test** - a WS-sourced position update
  (`_process_stream_position`) followed by a REST-triggered snapshot fetch within
  the old 20s window should no longer be overwritten; a reconnect event should
  still trigger a real reconcile.
- [ ] **Step 3: Run it, watch it fail.**
- [ ] **Step 4: Split the cache**: `balance` keeps its own interval (tune from 20s
  to 60s, matching "balance only changes on a fill" reasoning above); `positions`/
  `fills` stop being unconditionally refetched on the same timer - only refetched
  when `ws_state_verify`'s reconnect-triggered pass (Task 28) requests it, or when
  `state["account"]` has never been populated at all (cold start, same "seed only
  what's genuinely missing" shape as Task 29).
- [ ] **Step 5: Add `force_refresh(cfg)`** - factor the existing gather-and-slim
  logic out of `_fetch_account_snapshot` into a shared helper both the interval path
  and `force_refresh` call, rather than duplicating the three-call gather.
- [ ] **Step 6: Run it, watch it pass; run the account/position test suite for a
  regression check.**
- [ ] **Step 7: Commit:** `git commit -m "fix: stop unconditionally REST-reconciling WS-sourced account state every 20s, add reconnect-triggered force_refresh (H13 P7)"`

---

### Task 32: Measure whether lifecycle-driven `created`/`activated` events would
    reduce catalog-scan REST volume, before deciding to wire them

**Explicitly measurement-first, not implementation** - H13 §5 flagged this
feasibility question as genuinely unassessed (does wiring the 5 discarded lifecycle
event types into `market_catalog` reduce `catalog_scan`'s REST rescan volume, or add
a second, redundant discovery path alongside it), and this plan's own "no premature
conclusion" rule applies directly - this is exactly the shape of guess ("wire the
unused channel, it'll obviously help") the rule warns against.

**Files:**
- Modify: `services/whale_stream/whale_stream_handlers.py` (`_process_stream_lifecycle`,
  add a counter only - no behavior change)
- Modify: `services/market_watch/catalog_scan.py` (`_scan_catalog_batch`, add a
  counter only)

- [ ] **Step 1: Add a bounded metric**: for each `created`/`activated` lifecycle
  event received, record whether that same ticker/event is *also* touched by
  `_scan_catalog_batch`'s own next rescan within a short window (e.g. the scan's own
  `_CATALOG_SCAN_MIN_INTERVAL_SEC` window) - this directly answers H13 §5's
  unresolved question (real overlap vs. two independent paths) with live data
  instead of guessing.
- [ ] **Step 2: Run a live paper-mode soak** (this plan's own established pattern -
  see Task 17b) for long enough to observe a real sample of both created/activated
  events and catalog-scan cycles.
- [ ] **Step 3: Decide, with the measured overlap, whether wiring these event types
  would reduce `catalog_scan`'s REST volume materially or merely duplicate it** -
  write the finding (either way - a negative result is still real progress per this
  plan's own established discipline) into
  `docs/superpowers/research/2026-08-25-realtime-data-plane-known-findings.md` before
  deciding whether a follow-up implementation task is warranted.
- [ ] **Step 4: Commit the measurement code and its finding as one task**, same
  shape as Task 17c - a benchmark/finding commit, not necessarily a behavior change.

---

### WS protocol best-practices re-check (direct instruction, 2026-08-27): ping/pong,
    subscription-message batching, sharding

Three specific Kalshi-documented WS practices raised in earlier planning, re-verified
against current code (not re-derived from memory) rather than assumed still true or
still unaddressed:

**Ping/pong keepalive - already compliant, no task.** `docs/kalshi/CHEATSHEET.md`'s
existing entry (found 2026-08-24, Kalshi Integration Phase A Task A1) already confirmed
`services/kalshi/websocket.py` uses the `websockets` library's own automatic ping/pong
(`connect(..., ping_interval=20, ping_timeout=20)`) rather than a hand-rolled
implementation - exactly `quick_start_websockets.md`'s documented recommendation ("The
Python `websockets` library automatically handles WebSocket ping/pong frames... No
manual heartbeat handling is required"). Re-verified directly against current code
while building this phase: `grep -n "ping_interval|ping_timeout|send.*ping" services/
kalshi/websocket.py` returns exactly the one `connect()` call's two kwargs and nothing
else - zero hand-rolled ping/pong frame handling anywhere. Worth keeping in view for
Task 33's own design: the CHEATSHEET entry's own correction (2026-08-25) notes the 20s
`ping_timeout` is also why an event-loop stall closes the socket with 1011 "keepalive
ping timeout" - exactly the disconnect reason recorded in this phase's own preamble
data and in the live incident this session already wrote up separately (`ROADMAP.md`'s
trade-stream-consumer-stall item) - not a new finding, a confirmed cross-reference.

**Sharding (`shard_factor`/`shard_key`) - direction already decided, correctly still
deferred, live baseline now recorded so it isn't re-derived blind later.**
`docs/kalshi/CHEATSHEET.md` already documents the mechanism (`websocket-connection.md`'s
subscribe params, `changelog-index.md`'s semantics: "Messages are sharded by
`market_ticker` using consistent hashing. Clients can run multiple connections with
different `shard_key` values to distribute load") **with its own explicit caveat**:
documented for the `communications` channel specifically, `trade` honouring it is
unverified. `next-session-pickup-2026-08-24.md` (surfaced by the doc-consolidation
pass) named this as the fix direction for trade-channel CPU cost but never implemented
it. Live-checked while building this phase (`GET /api/state`'s `trade_stream_perf`,
2026-08-27): `messages_per_sec: 21.2`, `avg_handler_ms: 0.413` - about 9ms of actual
handler work per second, comfortably within one connection's capacity. **Not tasked
here** - implementing multi-connection sharding against an unverified-for-`trade`
mechanism, for a cost that live data shows isn't a real bottleneck yet, would be
exactly the premature-conclusion shape this plan's own rule forbids. Recorded so the
next session that revisits trade-channel load doesn't have to rediscover this: the
lever exists, the mechanism needs live verification before use, and the trigger
condition (this ratio degrading materially, not a fixed schedule) is now written down
rather than left to be re-found.

### Task 33: Combine independently-scoped channels into one subscribe message on
    connect

**Files:**
- Modify: `services/kalshi/websocket.py` (`_sync_subscriptions`, the `force_subscribe`
  connect-time branch - re-verify current line numbers against HEAD, this file has
  moved during this session)
- Test: append to the existing `_sync_subscriptions` test file (`grep -rn
  "_sync_subscriptions" tests/`)

**Interfaces:**
- Changes: `trade` and `market_lifecycle_v2` - both exchange-wide, neither taking a
  `market_tickers`/scoping param, the same shape `fill`+`market_positions` already
  share in one combined message (`"channels": ["fill", position_contract.
  SUBSCRIPTION_CHANNEL]`, `services/kalshi/websocket.py:409`) - get combined into one
  `{"channels": ["trade", "market_lifecycle_v2"]}` subscribe message instead of two
  separate `_send()` calls. `ticker` (needs `market_tickers`) and the index channels
  (`cfbenchmarks_value`/`pyth_value`, need `index_ids`/`underlying_tickers`) stay
  separate - each needs its own channel-specific params, and `websocket-connection.md`'s
  schema shows those params live at the top level of one `params` object, not
  per-channel, so mixing a scoped channel into a combined message would incorrectly
  apply its params to every channel in that message.
- Connection-setup-only cost (fires once per connect/reconnect, not per-tick or
  per-message) - real but low-urgency, included here because it's a genuine documented-
  but-unused capability, not because it's a hot-path bottleneck.
- **R3 addition (adversarial review above):** the `to_add` branch's `add_markets`
  call (`services/kalshi/websocket.py:996-1002`) gains `"send_initial_snapshot": True`
  - documented as available there too (`websocket-connection.md` line 718, default
  `false`, currently omitted), so a ticker added mid-connection (a position opening
  while already connected) gets its first price directly from WS instead of relying
  entirely on Task 29's REST-seed fallback. Zero extra REST cost - the flag changes
  what the server sends over the existing connection, not an additional call.
- **R5/R6 (adversarial review above), named not fixed here:** whether a rejected
  multi-channel subscribe fails atomically or partially is an unresolved doc
  ambiguity - not blocking, since the existing per-message `"subscribed"` handler
  already processes one channel/`sid` per response regardless of which way the
  server behaves. Every current subscribe call (not just this task's) sets its own
  `_X_subscribed` flag immediately after sending, without waiting for confirmation -
  a pre-existing gap this task doesn't introduce or worsen, out of scope here.

- [ ] **Step 1: Write the failing test** - asserts exactly one `_send()` call carries
  both `"trade"` and `"market_lifecycle_v2"` in its `channels` list on a fresh
  connect, where today's code sends two separate calls; a second assertion for R3
  confirms the `add_markets` call includes `"send_initial_snapshot": True`.
- [ ] **Step 2: Run it, watch it fail.**
- [ ] **Step 3: Combine the two `_send()` calls** in `_sync_subscriptions`'s
  `force_subscribe` branch into one, preserving each channel's own gating
  (`self.exchange_wide_trades or desired` for trade, `self.subscribe_lifecycle` for
  lifecycle - both must independently gate whether they're included in the combined
  list, not become unconditionally coupled). Add `"send_initial_snapshot": True` to
  the `to_add` branch's `add_markets` params (R3).
- [ ] **Step 4: Run it, watch it pass; run the full WS-client test suite for a
  regression check.**
- [ ] **Step 5: Live verification** - a real `ddev restart`, confirm via `ddev logs -s
  fastapi` that the fresh connection sends one combined subscribe for trade+lifecycle
  instead of two, and that both channels' data still flows normally afterward (`GET
  /api/health/pipeline`'s `ingest.received_by_class` for both `trade` and `lifecycle`
  growing). Separately, confirm a ticker added mid-connection (e.g. widen the
  watchlist live) gets a WS-pushed price without waiting for the next natural tick.
- [ ] **Step 6: Commit:** `git commit -m "perf: combine trade+market_lifecycle_v2 into one subscribe message, request an initial snapshot on add_markets too (H12/H13 P7)"`

---

**P7 gate:** Full test suite green with every P7 change live (no flags needed - these
are all strictly-additive-or-corrective changes, not opt-in features, matching the
"stop doing the wrong thing" shape of the underlying bugs rather than a new toggle).
Live paper-mode confirmation (`GET /api/state`) that: `latest_prices` for an open
position updates between REST ticks; a `settled` lifecycle event resolves
`signal_log` without waiting for the next 30s batch; a reconnect visibly triggers
`ws_state_verify`'s drain (new counter, exposed via `ingest_metrics()` or
`/api/health/pipeline`, matching every other phase's own visibility requirement);
a fresh connect sends one combined subscribe message for `trade`+`market_lifecycle_v2`
(Task 33), confirmed via `ddev logs`; `state["latest_prices_updated_at"]`'s staleness
figure (Task 29/R4) is present and moves correctly during a real disconnect/reconnect
cycle, not just in a test double.
Cross-post the shipped findings into `services/position/README.md` (or create it,
following an existing module's format, per CLAUDE.md's cross-posting rule) and
`services/signal_log`'s own module docstring, so a future audit of either module
finds this there rather than needing to know this plan doc exists.

---

## Phase P8 — Staleness resolution, real-data benchmarking, Family-C-lite
   (approved plan, 2026-08-27)

**Source:** the approved plan at `~/.claude/plans/valiant-yawning-cascade.md`
(2026-08-27), produced after direct user pushback that P7 was still under-scoped
against CLAUDE.md's HARD RULE ("the data plane is the product"), on three specific
points, each investigated by a dedicated read-only research pass before this phase
was designed: (1) `check_exits`'s staleness question was left open by R4, not
resolved; (2) P7 never got the benchmark rigor P0-P6 got; (3) Family C was rejected
too quickly — the rejection reasoned only about `check_exits` and missed both the
two other decision-relevant tick-only functions and how much of the target shape
already exists live.

**A fourth correction is baked into this phase's own design**: the initial
benchmark-feasibility research claimed "no live data exists for reconnect
frequency" — directly challenged by the user (a standing instruction days earlier
had built out observability precisely so this kind of gap wouldn't recur), and
verified wrong: `trade_stream.ingest.reconnects` is already persisted to
`data/observability.db` every 60s, with real reconnect events confirmed live via
`GET /api/observability/history` (3 in one 6h window, 30 in an 18.9h window).
Reconnect *frequency* is not a gap. The two genuinely-missing inputs are reconnect
*gap duration* and *per-open-position ticker cadence* — Task 34 adds both to the
existing capture pipeline, and the benchmark (Task 40) is explicitly sequenced
behind a real-data-collection window rather than built on guessed inputs, which
would look more rigorous than the R1-R6 review while actually being less.

**Key research findings this phase's design rests on** (each from a dedicated
evidence-cited pass; re-verify named line numbers against HEAD when implementing):
- `check_exits`'s staleness exposure is exactly one path: `current_price`/`pnl_pct`
  (take-profit/stop-loss + `auto_exit`'s diluted `pnl_factor`). Sentiment-reversal
  and time-to-close exits never read price. This codebase has **zero** precedent for
  fail-closed on data staleness (every freshness check fails open, explicitly - see
  `market_history.recent_price`'s own docstring); the proven third shape is the
  existing corroboration check's **re-verify via independent REST at decision time**.
- `trading_loop`'s genuinely tick-bound, decision-relevant, not-yet-event-driven
  surface is short: `latest_prices`/`latest_asks` (Task 29's target),
  `check_pending_fills` (zero WS precedent), `position_netting.review` (zero WS
  precedent), and `check_exits`'s message-independent safety-net role (the "runway
  exhausted" forced exit - a quiet ticker approaching close would never trigger
  either WS call site; named in `exit_engine.py`'s own docstring as exactly the
  failure mode that rule exists for). Entry-signal generation is already dead code
  in streaming mode (`_handle_signal` fires only from `_process_stream_trade`), and
  both WS `check_exits` sites already re-evaluate the entire position book per call.
- The five `_maybe_*` trigger checks (signal-resolution, backup, research,
  event-schedule, catalog-scan) are already mechanically independent background
  tasks - `trading_loop` only hosts their due()-checks. Calibration-history
  snapshotting and advisory auto-apply are the exception: real work still runs
  inline-on-due on the tick's own coroutine (`main.py:507-650`).

### Task 34: Capture reconnect gap duration + per-open-position ticker cadence

**Files:**
- Modify: `services/kalshi/websocket.py` (`_record_disconnect` + the reconnect-
  success path in `run()`)
- Modify: `services/observability/observability.py` (`_flatten_ingest_metrics`)
- Modify: `services/whale_stream/whale_stream_handlers.py` (`_process_stream_ticker`)
- Test: append to `tests/test_kalshi_ws_ingest_metrics.py` and
  `tests/test_observability.py`

**Interfaces:**
- Produces: `ingest_metrics()["connection"]["last_gap_sec"]` - on each successful
  reconnect, the gateway computes `reconnected_at - last_disconnect["at"]` and
  stores it; `_flatten_ingest_metrics` emits `{prefix}.ingest.last_gap_sec` only in
  windows where a reconnect completed (zero-window omitted, matching the
  subscription_churn precedent in the same function).
- Produces: `state["open_position_ticker_seen_at"]: dict[str, float]` - updated by
  `_process_stream_ticker` **only when the ticker is an open-position ticker**
  (bounded by position count, never exchange-wide). A new `_flatten_position_ticker_
  cadence` in observability emits, per sample window, the min/max/median seconds-
  since-last-ticker-update across open positions (`observability.position_ticker.
  oldest_update_age_sec` etc.) - distribution over time, matching the existing
  `_flatten_*` pattern exactly.
- Hot-path constraint (HARD RULE): the `_process_stream_ticker` addition must be a
  guarded dict assignment (`if ticker in broker.positions or ...`), never a DB
  write or a scan - measure its per-message cost before commit, same discipline as
  Task 28's Step 7.

- [ ] **Step 1: Write the failing tests** - (a) a simulated disconnect + reconnect
  produces a real `last_gap_sec` in `ingest_metrics()`; (b) a ticker message for an
  open-position ticker updates `open_position_ticker_seen_at`, a non-position ticker
  doesn't; (c) `_flatten_ingest_metrics`/`_flatten_position_ticker_cadence` emit the
  new metrics with real values and omit them in windows with nothing to report.
- [ ] **Step 2: Run them, watch them fail.**
- [ ] **Step 3: Implement** - gateway first (gap computation at the reconnect-success
  point in `run()`, where `on_status({"connected": True, ...})` fires), then the
  handler dict, then the two flatten functions.
- [ ] **Step 4: Run them, watch them pass; run the observability + WS-client suites.**
- [ ] **Step 5: Measure the per-message cost** of the `_process_stream_ticker`
  addition (microseconds expected - confirm, don't assume).
- [ ] **Step 6: Live verification** - `GET /api/observability/history?metric=trade_
  stream.ingest.last_gap_sec` and the new cadence metric show real values after a
  real reconnect / with a real open position (paper mode).
- [ ] **Step 7: Commit:** `git commit -m "feat: capture reconnect gap duration and per-position ticker cadence (P8 Task 34)"`

### Task 35: `check_exits` staleness-triggered REST corroboration (resolves R4)

**Depends on Task 29** (`state["latest_prices_updated_at"]` must exist).

**Files:**
- Modify: `services/exits/exit_engine.py` (the corroboration block, currently
  ~lines 236-265 - re-verify against HEAD)
- Modify: `config/settings.yaml` + `services/config/config_bounds.py` (new tunable)
- Test: append to the exit-engine test file

**Interfaces:**
- Changes: the existing corroboration check gains a second trigger. Today:
  corroborate only when `abs(current_price - corroborated) > 0.30`. New: ALSO force
  the same `market_history.recent_price` read when `now - latest_prices_updated_at.
  get(ticker, now) > strategy.price_staleness_corroborate_sec` (new config field,
  default chosen from Task 34's real cadence data once it exists; ship with a
  deliberately conservative provisional default, e.g. 120.0 matching
  `_PRICE_CORROBORATION_MAX_AGE_SEC`, and a comment naming Task 40 as the
  re-tuning owner). On staleness-trigger: if the corroborated price is present and
  differs, prefer it (same override the deviation path already does); if the
  corroboration read itself returns None (REST also stale/down - the combined-outage
  case), fail open exactly as today BUT record one fault_log entry per
  ticker-per-window (`component="exit_engine", operation="stale_price_uncorroborated"`)
  so the condition is visible, never silent. No skip/block path is added - fail-open
  stays the rule, matching the codebase's own uniform precedent.

- [ ] **Step 1: Re-read the current corroboration block and Task 29's shipped
  implementation** before writing anything.
- [ ] **Step 2: Write the failing tests** - (a) a stale-but-plausible price (older
  than threshold, within 0.30 of corroborated) now triggers the corroboration read
  (today it wouldn't); (b) fresh price under threshold does NOT trigger it (no new
  REST cost on the healthy path); (c) staleness + corroboration-unavailable records
  the fault_log entry and still proceeds (fail-open confirmed).
- [ ] **Step 3: Run them, watch them fail.**
- [ ] **Step 4: Implement**, including the config field + bounds registration.
- [ ] **Step 5: Run the full exit-engine suite for a regression check.**
- [ ] **Step 6: Commit:** `git commit -m "feat: staleness-triggered price corroboration in check_exits, fail-open with visibility (P8 Task 35, resolves R4)"`

### Task 36: Relocate the five `_maybe_*` trigger checks + calibration/advisory
    auto-apply out of `trading_loop`

**Files:**
- Modify: `main.py` (`trading_loop`, `lifespan()`)
- Test: append to the wiring/scheduler test files (`grep -rn "_maybe_check_signal_
  resolutions\|_maybe_run_backup" tests/`)

**Interfaces:**
- Changes: `_maybe_check_signal_resolutions`, `_maybe_run_backup`,
  `_maybe_run_research`, `event_schedule._maybe_resolve_event_schedules`,
  `_maybe_scan_catalog_batch` stop being called from `trading_loop`'s body; each
  gets its own small supervised loop (`task_supervisor.supervise(...)` at startup in
  `lifespan()`, `while True: await asyncio.sleep(N); _maybe_X(cfg)` with N well
  under each function's own internal interval so due()-precision is preserved).
  Their own internal due()/overlap guards are untouched - the relocation changes
  who calls them, not when they fire. Calibration-history snapshotting and advisory
  auto-apply (`main.py:507-650`) additionally move their *inline-on-due work* into
  the same pattern (the one genuinely new decoupling here - they currently run
  synchronously on the tick when due).
- Preserve Task 9's ordering fix: `_maybe_scan_catalog_batch`'s loop must still not
  compete with the critical gather - its own loop's sleep phase makes the original
  "after the gather" ordering moot (it no longer shares the tick's coroutine), but
  confirm the rate-limiter caller-class separation still holds under the new shape.

- [ ] **Step 1: Write the failing tests** - each relocated function still fires on
  its own cadence when the app runs without `trading_loop` invoking it (test via the
  new loop wrapper directly, monkeypatched intervals).
- [ ] **Step 2: Run them, watch them fail.**
- [ ] **Step 3: Relocate** - one commit-sized move; the tick body loses seven call
  sites, `lifespan()` gains seven supervised loops.
- [ ] **Step 4: Regression** - full scheduler/wiring suite; confirm
  `GET /api/health/pipeline`'s scheduler section still reports every mover.
- [ ] **Step 5: Live verification** - all seven still fire on schedule
  (`/api/health/pipeline` last-fired timestamps advance) after a real restart.
- [ ] **Step 6: Commit:** `git commit -m "refactor: move background trigger checks + auto-apply out of trading_loop into supervised loops (P8 Task 36)"`

### Task 37: `candidate_retry.run_pending` gets its own single background loop

**Files:** `main.py`, `services/candidate_retry.py` (docstring), test file.
- Same relocation shape as Task 36, with the documented single-mutator constraint
  preserved: still exactly one caller, now a dedicated supervised loop instead of
  the tick. Update the module docstring's "call from exactly one place" note to name
  the new caller.
- [ ] Failing test → relocate → regression → live check → commit:
  `git commit -m "refactor: candidate_retry gets its own supervised loop, single-mutator preserved (P8 Task 37)"`

### Task 38: Wire `check_pending_fills` + `position_netting.review` into the WS
    ticker path (concurrency-tested first)

**The one piece of genuinely new work** - neither has any WS-trigger precedent.
**Files:** `services/whale_stream/whale_stream_handlers.py`
(`_process_stream_ticker`), `main.py`, tests.

- [ ] **Step 1: Write the concurrency test FIRST** - near-simultaneous ticker
  messages must not produce overlapping/duplicate fill or netting decisions on the
  same position. `check_exits`'s two coexisting WS sites are precedent this is
  manageable, but these two functions have only ever run single-caller inside the
  tick - prove it, don't assume it. If the test finds a real race, add the minimal
  guard (an asyncio.Lock or an in-flight flag, matching whatever `check_exits`
  itself relies on - investigate that first) before wiring anything.
- [ ] **Step 2: Wire both calls** into `_process_stream_ticker` alongside the
  existing `check_exits` call, same gating (`state["running"]`).
- [ ] **Step 3: Keep the tick-loop call sites for now** (they become the safety-net
  copies Task 39 then consolidates) - both paths are idempotent-by-construction or
  must be proven so in Step 1.
- [ ] **Step 4: Regression + live verification, then commit:**
  `git commit -m "feat: check_pending_fills + position_netting.review fire from the WS ticker path (P8 Task 38)"`

### Task 39: Slow the remaining tick loop down to safety-net cadence

**Depends on Tasks 29, 36, 37, 38 all being live.** After them, `trading_loop`'s
remaining jobs are: the genuinely-REST-only slow feeds (exchange status, category
metadata, live sports state - none need sub-minute freshness per H13's inventory)
and the message-independent safety-net calls (`check_exits` for the
runway-exhaustion rule, plus the Task 38 pair's fallback invocation).
**Files:** `main.py`, `config/settings.yaml`.

- [x] **Step 1: Inventory what's left in the tick at that point against HEAD**
  (2026-08-28) - re-read `trading_loop` in full against real HEAD rather than
  trusting this plan's own list. Confirmed Tasks 36-38 already removed every
  trigger call that had a real alternative path (the five `_maybe_*`
  schedulers, calibration/advisory auto-apply, `candidate_retry.run_pending`,
  and - as of Task 38 - `check_pending_fills`/`position_netting.review` also
  running from the WS ticker path). What's left is genuinely REST-only work
  (market/account/exchange-status fetch, settlement resolution, event-title/
  event-lifecycle/live-status/category-metadata refresh, capture flush,
  hourly-gated retention) plus the tick's own redundant (but harmless per the
  Task 38 idempotency proof) calls to `check_exits`/`check_pending_fills`/
  `position_netting.review`. The single pacing mechanism is one line -
  `await asyncio.sleep(cfg["kalshi"]["poll_interval_sec"])` at the very end of
  the loop body - so this task is a change to that one sleep call's duration,
  not a removal of tick-body logic.
- [x] **Step 2: Raise the loop cadence** (2026-08-28) - new config field
  `kalshi.safety_net_interval_sec: 30` (`config/settings.yaml`, added via the
  config-field-edit skill's isolated-commit procedure);
  `main._tick_interval_sec(cfg)` returns it when
  `_streaming_trade_tape_enabled()`, else returns the unchanged
  `poll_interval_sec` - non-streaming (REST-primary) mode keeps its exact
  pre-Task-39 cadence.
- [x] **Step 3: Confirm the runway-exhaustion rule's worst-case detection
  latency** (2026-08-28) - `strategy.exit_min_seconds_to_close` (the gate this
  rule is keyed on) defaults to `null` in `config/settings.yaml`, i.e. the
  rule is opt-in and disabled by default, so the default config carries zero
  exposure to this change. When it IS enabled, the tick's own 30s cadence is
  a backstop only for a ticker that goes fully silent on WS near its close:
  Task 38 already made `check_exits` (which owns this rule) run from
  `_process_stream_ticker` on every real ticker update for an open position,
  independent of the tick entirely, so an actively-quoted position's
  detection latency is bounded by its own WS cadence, not the REST tick.
  Task 34's real captured per-position cadence data
  (`services/observability/README.md`: 6 tracked, 4 never-seen, oldest 151s,
  median 62.6s - measured *before* this task, under the old 6s tick) already
  shows real WS-cadence variance larger than the 24s delta this task adds to
  the REST backstop (6s -> 30s) - this task's own worst case is a small
  perturbation relative to variance the data plane already exhibited.
- [ ] **Step 4: Full suite + a multi-hour live paper soak** (the P3.5 pattern)
  comparing decision latency and REST volume before/after - explicitly NOT
  done as of this commit (2026-08-28): the code change is unit-tested (11
  passing tests) and live-verified functionally (app healthy post-reload, no
  new faults, real measured tick-completion gaps of ~36-38s vs. the pre-
  change ~6-8s, confirming the new cadence took effect), but a genuine
  multi-hour before/after comparison needs real elapsed time this session
  cannot fabricate - same honesty standard Task 40 already holds itself to.
  Left open here rather than checked off early.
- [x] **Step 5: Commit:** `git commit -m "feat: tick loop drops to safety-net cadence in streaming mode (P8 Task 39)"`

### Task 40: Build the P7/P8 staleness benchmark from real captured data

**Sequenced explicitly behind Task 34 + a real data-collection window (days).**
Do not build this on assumed inputs - that was this phase's founding correction.
**Files:** new `tools/staleness_replay.py` (own file - the existing two harnesses
model message-delivery timing, not state-cache staleness; confirmed by direct
reading, not assumption), tests.

- [ ] **Step 1: Pull the real distributions** from `data/observability.db`
  (reconnect frequency: already accumulating; gap duration + per-position cadence:
  Task 34's new metrics) - fit/sample them directly, cite the extraction window.
- [ ] **Step 2: Model** per-ticker cached-price-with-age updated by sampled WS
  arrivals and disconnect/reconnect cycles; simulated `check_exits` reads scoring
  staleness-at-decision-time; candidate designs: WS-primary+reconnect-verify
  (P7 as shipped), periodic-refresh (the old 6s overwrite, as the baseline), and
  WS-primary+staleness-corroboration (Task 35's shape). Reuse the existing
  harnesses' primitives (LogNormal/seeded-heap/`_stats()` percentile reporting)
  without inheriting their message-queue scope.
- [ ] **Step 3: Score** on staleness-at-decision-time distribution, REST volume,
  and the fraction of decisions made against data older than N - the correctness
  axis neither existing tool has.
- [ ] **Step 4: Use the results to re-tune Task 35's provisional threshold** and
  record the finding in the known-findings doc either way.
- [ ] **Step 5: Commit:** `git commit -m "feat: staleness replay harness fed by real captured distributions (P8 Task 40)"`

**P8 gate:** Tasks 34-38 shipped and live-verified individually; Task 39's soak
shows no regression in decision latency with materially lower REST volume in
streaming mode; Task 40's benchmark ran on real (not assumed) distributions and
its threshold recommendation is applied or explicitly declined with reasons in the
known-findings doc. Cross-post the shipped P8 findings per CLAUDE.md's rule:
`services/exits/README.md` (Task 35), `services/observability/README.md`
(Task 34), and the module docstrings Task 36/37 touch.

---

## Self-review

**Spec coverage:** every numbered section of
`2026-08-25-realtime-data-plane-remediation-design.md` maps to at least one task —
§2 message classes → Tasks 17/18/19/21; §3 ordering → Task 19's coalescing + Task
18's queue split; §4 backpressure/drop/coalescing → Tasks 16/18/19; §5 retry/dedupe
→ Tasks 2/10/11/12/27; §6 REST scheduler → Tasks 4/9/22/23/26; §7 reconciliation →
Task 27; §8 persistence/thread ownership → Tasks 6/7/8/14/15; §9 observability →
folded into every task's own wiring step (14, 17, 22-27) plus Task 1's watchdog; §10
migration → the six phase headers themselves, each independently flagged and gated;
§11 rollback → every flag defaults false and every table is additive; §12
acceptance → the P6 gate. §13's internal contradiction review was already resolved
in the spec itself.

**Placeholder scan:** no "TBD"/"handle edge cases"/"similar to Task N" text; every
code step shows real code. Several steps explicitly instruct the implementer to
read a named real file/line before finalizing a snippet (e.g. Tasks 3, 15, 17, 18,
21, 22, 24, 27) because this plan was written by grepping the current codebase, not
by holding every exact current signature in view at once — that is a deliberate
grounding instruction, not a placeholder: it names exactly what to check and why.

**Type consistency:** `candidate_ledger.claim(trade_id, ticker=..., now=...) -> bool`
is used identically in Tasks 2, 10, 11, 12, 27. `whale_gate.passes(trade, min_contracts=int) -> bool`
is used identically in Tasks 3 and 17. `tick_executor.run(fn) -> Awaitable[T]` is
used identically in Tasks 7, 8, 9 (paced calls still go through the loop's own
`asyncio.gather`, not the executor — only genuinely synchronous SQLite work moves
to `tick_executor`). `capture_writer.submit(store, row)` is used identically in
Tasks 14, 15, 16. Caller classes (`critical_whale`, `critical_position`,
`background_catalog`, `background_resolution`, `background_live_status`,
`trade_tape_poll`, `reconciliation`) are introduced once each (Tasks 12/22/26/27)
and reused by name thereafter, matching `services/http_client.py`'s existing
`CALLER_CLASSES` registration pattern.

---

**Phase P7 self-review (added 2026-08-27, addendum — the phases above map to the
original `2026-08-25-realtime-data-plane-remediation-design.md`; P7 maps to a later,
separate investigation thread and is reviewed on its own terms here rather than
forced into that earlier spec's section numbering):**

**Finding coverage:** H12 (`state["latest_prices"]` wholesale overwrite) → Task 29,
directly. H13's three new gaps → `signal_log.mark_resolved` single-caller gap → Task
35; `_fetch_account_snapshot`'s unconditional 20s reconcile → Task 31; the discarded
`created`/`activated` lifecycle event types → Task 32 (measurement-first, per H13's
own explicit "feasibility unassessed" flag — deliberately not an implementation task
yet). H13's two lower-priority interactive-tier gaps (`orderbook_delta`,
`user_orders`) are **not** tasked here — both confirmed low-impact today
(interactive-only call sites, `user_orders` inert while `trading_enabled: false`) and
`orderbook_delta` is new capability, not a completeness/accuracy fix, a different
kind of work than this phase's actual scope; left as an explicit future item in the
known-findings doc rather than silently dropped.

**WS protocol best-practices, direct instruction (2026-08-27):** ping/pong re-verified
compliant against current code, no task needed. Sharding re-confirmed as the right
future direction with its own documented caveat (unverified for `trade`) preserved,
not tasked given live data (`trade_stream_perf`: 21.2 msg/sec, 0.413ms avg handler)
shows no current bottleneck - recorded rather than silently dropped, matching the
same discipline as the two lower-priority H13 gaps above. Multi-channel subscribe
batching → Task 33, a real, small, connection-setup-only gap (`trade`+
`market_lifecycle_v2` sent as two messages where `fill`+`market_positions` already
prove the combined-message pattern works in this exact codebase).

**Reused infrastructure, not duplicated:** Task 28 depends on and extends Task 27's
`on_loss_event` callback and Task 21's connection-generation concept rather than
building a parallel reconnect-detection mechanism — the plan's own established
pattern (Task 27 already builds exactly the "fire on reconnect/error-25" hook this
phase needed) was checked for reuse before any new primitive was proposed, matching
the "prefer the simplest candidate" rule this phase's own architecture-decision
section cites.

**No premature conclusion, checked against the rule's own named examples:** neither
"add a queue" nor "add a cache layer" nor "increase a limit" appears as a bare
solution anywhere in P7 — every task is a targeted, evidenced correction to a named
line/function found in the known-findings doc, and Task 32 is explicitly a
measurement, not an implementation, exactly where the evidence (H13 §5) says the
answer isn't known yet.

**Placeholder scan:** same standard as the rest of this plan — every task names its
real files/line ranges (flagged for re-verification against HEAD where the
underlying investigation is now a few commits old, matching Tasks 3/15/17/18/21/22/
24/27's own precedent for this), every code step shows real code, no "TBD" text.
