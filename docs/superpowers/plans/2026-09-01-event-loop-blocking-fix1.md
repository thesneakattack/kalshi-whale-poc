# Fix 1: Eliminate the Inline Synchronous Flush Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `record_cfbenchmarks()`/`record_pyth()`/`record_observation()`/
`record()`/`record_book()` from blocking the asyncio event loop when their
buffer fills — the confirmed, live root cause of an app-wide, endpoint-agnostic
stall (13-minute reproduction this session; `GET /api/trading-history`, which
does zero DB I/O, hung too, ruling out any per-endpoint cause).

**Architecture:** Four functions across four files currently call `flush()`
inline and synchronously (real disk I/O, no `await` point) from inside async
WS-message-handling code, with no `await` on the call itself. Each is changed
to return a `should_flush: bool` instead of acting on it; its async caller
schedules the flush via `asyncio.create_task(tick_executor.run(flush))` —
fire-and-forget, reusing the exact pool `main.py`'s own earlier-today commits
(`d87fd5b`/`9b4bd80`) already established as the accepted mechanism for these
same modules' *scheduled* flush. This is completing an already-adopted
pattern, not introducing a new one.

**Tech Stack:** Python 3, `asyncio` (stdlib), `services.tick_executor`
(already exists, unchanged by this plan) — no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-01-event-loop-blocking-elimination-design.md`
("Fix 1" section — this plan implements Fix 1 only; Fix 2 is a separate plan).

## Global Constraints

- No change to `flush()` itself, `_buffer_lock`, `_FLUSH_BATCH` thresholds, or
  any already-correct scheduled per-tick flush path, in any of the four files.
- No change to `series_watcher.py`'s `record_trade` (already correct — routes
  through `capture_writer.submit()`) or any read-only function in that file.
- `asyncio.create_task(...)`, never `await tick_executor.run(flush)` directly
  at the call site — the caller must not block waiting for the flush to
  finish; that would reintroduce a smaller version of the same bug.
- This repo has no `pytest-asyncio`. Test async functions with
  `asyncio.run(module.async_func(...))` inside a plain `def test_...`, never
  `@pytest.mark.asyncio`/`async def test_...` (verified convention:
  `tests/test_index_stream_handlers.py`'s existing tests).

---

### Task 1: `services/index_feed/ingestion.py` — `record_cfbenchmarks`/`record_pyth` stop flushing inline

**Files:**
- Modify: `services/index_feed/ingestion.py:122-194` (`record_cfbenchmarks`,
  `record_pyth`)
- Modify: `services/whale_stream/index_stream_handlers.py` (add
  `from services import tick_executor` to imports; add `import asyncio`;
  `_process_stream_index`, currently at line 41)
- Test: `tests/test_index_feed.py` (extend existing file)
- Test: `tests/test_index_stream_handlers.py` (extend existing file)

**Interfaces:**
- Produces: `record_cfbenchmarks(msg, now=None) -> bool` and
  `record_pyth(msg, now=None) -> bool` keep their existing return type
  (`bool` = "was this message accepted/recorded"), but a `True` return no
  longer implies `flush()` was already called — callers must separately act
  on flush timing. No new function signature; behavior-only change, verified
  against every other caller of these two functions to confirm none of them
  currently depend on `flush()` having run by the time `record_cfbenchmarks`/
  `record_pyth` returns (`grep -rn "record_cfbenchmarks\|record_pyth"
  services/ main.py` — this task's Step 1 test literally is that check made
  concrete).

- [ ] **Step 1: Confirm no other caller depends on the inline flush**

Run: `grep -rn "record_cfbenchmarks\|record_pyth" services/ main.py tests/`

Expected: the only non-test, non-definition call sites are
`services/whale_stream/index_stream_handlers.py:54,57` (this task's own
target). If anything else calls these functions, stop and re-scope this task
— this plan assumes exactly these two call sites.

- [ ] **Step 2: Write the failing tests**

Add to `tests/test_index_feed.py` (check its existing fixture pattern first —
`grep -n "^def _" tests/test_index_feed.py` for the tmp-path/monkeypatch
helper this file already uses, and match it):

```python
def test_record_cfbenchmarks_returns_should_flush_without_flushing(monkeypatch, tmp_path):
    _index_feed(tmp_path, monkeypatch)  # existing fixture helper - adjust name to match what test_index_feed.py actually calls
    flush_calls = []
    monkeypatch.setattr(index_feed, "flush", lambda: flush_calls.append(1) or {"ticks": 0})
    # _FLUSH_BATCH is 200 - fill the buffer to exactly one short of the threshold first
    for i in range(index_feed._FLUSH_BATCH - 1):
        result = index_feed.record_cfbenchmarks({
            "index_id": "KXBTC", "avg_60s_data": {"value": "1.0"}, "data": {},
        })
        assert result is True
        assert flush_calls == []  # never called internally, regardless of buffer state
    # The row that crosses the threshold
    result = index_feed.record_cfbenchmarks({
        "index_id": "KXBTC", "avg_60s_data": {"value": "1.0"}, "data": {},
    })
    assert result is True
    assert flush_calls == []  # still never called - that's the whole point of this task


def test_record_pyth_returns_should_flush_without_flushing(monkeypatch, tmp_path):
    _index_feed(tmp_path, monkeypatch)
    flush_calls = []
    monkeypatch.setattr(index_feed, "flush", lambda: flush_calls.append(1) or {"ticks": 0})
    for i in range(index_feed._FLUSH_BATCH - 1):
        assert index_feed.record_pyth({"underlying_ticker": "BTC", "value_usd": "50000"}) is True
    assert index_feed.record_pyth({"underlying_ticker": "BTC", "value_usd": "50000"}) is True
    assert flush_calls == []
```

Note: these tests confirm `flush()` is never called by `record_cfbenchmarks`/
`record_pyth` themselves, at any buffer state — they don't yet assert
anything about `should_flush` being *returned*, since Step 3 changes the
return type. Write a second pair of tests after Step 3's implementation is in
place (Step 4 below), asserting the actual return value crosses from
implicit-`True`-always to a real signal.

- [ ] **Step 3: Run tests to verify they fail**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/event-loop-blocking-fix && python -m pytest tests/test_index_feed.py -k 'returns_should_flush' -v"`

Expected: FAIL — `flush_calls` is non-empty once the buffer crosses
`_FLUSH_BATCH`, because the current code still calls `flush()` inline.

- [ ] **Step 4: Implement — stop calling `flush()` inline**

In `services/index_feed/ingestion.py`, change lines 156-161 (inside
`record_cfbenchmarks`):

```python
        with _buffer_lock:
            _tick_buffer.append(row)
            should_flush = len(_tick_buffer) >= _FLUSH_BATCH
        if should_flush:
            flush()
        return True
```

to:

```python
        with _buffer_lock:
            _tick_buffer.append(row)
            should_flush = len(_tick_buffer) >= _FLUSH_BATCH
        return should_flush
```

Wait — this changes the return type's meaning (was "accepted", now
"accepted AND buffer-full"), which breaks the "returns whether the row was
ACCEPTED" contract every existing caller and the module's own docstring
promises. Do NOT collapse these into one boolean. Instead, return a 2-tuple
and keep backward-compat impossible to get wrong — Python's plain bool
`True`/`False` return is exactly what Step 1's grep confirmed nothing else
depends on beyond "was this accepted", so change the signature explicitly:

```python
def record_cfbenchmarks(msg: dict, now: float | None = None) -> tuple[bool, bool]:
    """Persist one `cfbenchmarks_value` message and update the in-memory
    latest. Never raises - this runs on the websocket handler.

    Returns (accepted, should_flush) - accepted is the original "was this
    row recorded" signal; should_flush tells the caller a buffer-full flush
    is now due. The caller (not this function) is responsible for scheduling
    that flush off the event loop - see services/whale_stream/
    index_stream_handlers.py's _process_stream_index, and this module's own
    write-path-capacity-fix-adjacent history at flush()'s own docstring for
    why an inline synchronous flush() call here is exactly the bug this
    signature change fixes."""
    try:
        index_id = msg.get("index_id")
        if not index_id:
            return False, False
        now = now if now is not None else time.time()
        avg60 = msg.get("avg_60s_data") or {}
        q15 = msg.get("last_60s_windowed_average_15min") or {}
        spot = _parse_cf_data(msg.get("data"))
        entry = {
            "index_id": index_id,
            "source": "cfbenchmarks",
            "observed_at": now,
            "received_at_ms": msg.get("received_at"),
            "value": spot,
            "avg_60s_value": _float(avg60.get("value")),
            "avg_60s_window_size": avg60.get("window_size"),
            "q15_value": _float(q15.get("value")) if q15 else None,
            "q15_window_size": q15.get("window_size") if q15 else None,
            "q15_window_end_ts_ms": q15.get("window_end_ts_exclusive") if q15 else None,
        }
        _latest[index_id] = entry
        row = (
            index_id, "cfbenchmarks", now, msg.get("received_at"), None, spot,
            entry["avg_60s_value"], entry["avg_60s_window_size"],
            entry["q15_value"], entry["q15_window_size"],
            json.dumps(msg, default=str),
        )
        with _buffer_lock:
            _tick_buffer.append(row)
            should_flush = len(_tick_buffer) >= _FLUSH_BATCH
        return True, should_flush
    except Exception as exc:
        fault_log.record("index_feed", "record", exc)
        return False, False
```

Apply the identical transformation to `record_pyth` (lines 167-194):

```python
def record_pyth(msg: dict, now: float | None = None) -> tuple[bool, bool]:
    """Persist one `pyth_value` message. Pyth carries no windowed averages -
    it is a straight price for an underlying ticker - so the settlement-
    projection fields stay NULL and only `value` is populated.

    Returns (accepted, should_flush) - see record_cfbenchmarks's docstring
    for the full explanation of this signature."""
    try:
        ticker = msg.get("underlying_ticker")
        if not ticker:
            return False, False
        now = now if now is not None else time.time()
        value = _float(msg.get("value_usd"))
        _latest[ticker] = {
            "index_id": ticker, "source": "pyth", "observed_at": now,
            "received_at_ms": msg.get("received_at"), "source_ts_ms": msg.get("source_ts_ms"),
            "value": value, "avg_60s_value": None, "avg_60s_window_size": None,
            "q15_value": None, "q15_window_size": None,
        }
        row = (
            ticker, "pyth", now, msg.get("received_at"), msg.get("source_ts_ms"),
            value, None, None, None, None, json.dumps(msg, default=str),
        )
        with _buffer_lock:
            _tick_buffer.append(row)
            should_flush = len(_tick_buffer) >= _FLUSH_BATCH
        return True, should_flush
    except Exception:
        return False, False
```

- [ ] **Step 5: Update Step 2's tests for the new 2-tuple return, run, verify PASS**

Replace the two tests written in Step 2 with:

```python
def test_record_cfbenchmarks_returns_should_flush_without_flushing(monkeypatch, tmp_path):
    _index_feed(tmp_path, monkeypatch)
    flush_calls = []
    monkeypatch.setattr(index_feed, "flush", lambda: flush_calls.append(1) or {"ticks": 0})
    for i in range(index_feed._FLUSH_BATCH - 1):
        accepted, should_flush = index_feed.record_cfbenchmarks({
            "index_id": "KXBTC", "avg_60s_data": {"value": "1.0"}, "data": {},
        })
        assert accepted is True
        assert should_flush is False
    accepted, should_flush = index_feed.record_cfbenchmarks({
        "index_id": "KXBTC", "avg_60s_data": {"value": "1.0"}, "data": {},
    })
    assert accepted is True
    assert should_flush is True  # crossed _FLUSH_BATCH
    assert flush_calls == []  # never called internally - the caller's job now


def test_record_pyth_returns_should_flush_without_flushing(monkeypatch, tmp_path):
    _index_feed(tmp_path, monkeypatch)
    flush_calls = []
    monkeypatch.setattr(index_feed, "flush", lambda: flush_calls.append(1) or {"ticks": 0})
    for i in range(index_feed._FLUSH_BATCH - 1):
        accepted, should_flush = index_feed.record_pyth({"underlying_ticker": "BTC", "value_usd": "50000"})
        assert accepted is True
        assert should_flush is False
    accepted, should_flush = index_feed.record_pyth({"underlying_ticker": "BTC", "value_usd": "50000"})
    assert accepted is True
    assert should_flush is True
    assert flush_calls == []
```

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/event-loop-blocking-fix && python -m pytest tests/test_index_feed.py -k 'returns_should_flush' -v"`
Expected: PASS (both tests)

- [ ] **Step 6: Run the full `test_index_feed.py` suite for a regression check**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/event-loop-blocking-fix && python -m pytest tests/test_index_feed.py -v"`

Expected: some pre-existing tests will now FAIL, since `record_cfbenchmarks`/
`record_pyth`'s return type changed from `bool` to `tuple[bool, bool]`. Read
each failure, find its assertion on the old plain-`bool` return (e.g.
`assert index_feed.record_cfbenchmarks(...) is True`), and update it to
unpack the tuple (e.g. `accepted, _ = index_feed.record_cfbenchmarks(...);
assert accepted is True`). This is a real, expected consequence of the
signature change, not a regression to work around — fix each one directly in
`tests/test_index_feed.py`, matching the existing test's actual intent (most
only cared about `accepted`, not flush timing).

- [ ] **Step 7: Write the failing caller-side test**

Add to `tests/test_index_stream_handlers.py`. First check
`ish._process_stream_index`'s exact current signature and how `state`/
`config_store` fixtures are set up elsewhere in this file
(`grep -n "state\[" tests/test_index_stream_handlers.py` and
`grep -n "^def _" tests/test_index_stream_handlers.py`), then match that
setup:

```python
def test_process_stream_index_schedules_flush_via_tick_executor_when_told(monkeypatch):
    from services import index_feed, tick_executor

    scheduled = []

    def fake_record_cfbenchmarks(msg, now=None):
        return True, True  # accepted, should_flush=True

    monkeypatch.setattr(index_feed, "record_cfbenchmarks", fake_record_cfbenchmarks)

    real_create_task = asyncio.create_task

    def spy_create_task(coro):
        scheduled.append(coro)
        return real_create_task(coro)

    monkeypatch.setattr(asyncio, "create_task", spy_create_task)

    tick_executor_calls = []

    async def fake_tick_executor_run(fn):
        tick_executor_calls.append(fn)
        return fn()

    monkeypatch.setattr(tick_executor, "run", fake_tick_executor_run)

    asyncio.run(ish._process_stream_index("cfbenchmarks_value", {"index_id": "KXBTC"}))

    assert len(scheduled) == 1
    assert len(tick_executor_calls) == 1
    assert tick_executor_calls[0] is index_feed.flush
```

- [ ] **Step 8: Run the caller-side test to verify it fails**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/event-loop-blocking-fix && python -m pytest tests/test_index_stream_handlers.py -k schedules_flush_via_tick_executor -v"`

Expected: FAIL — `_process_stream_index` still unpacks a single `bool`
(`ValueError: too many values to unpack` or similar), since Step 4 hasn't
touched this file yet.

- [ ] **Step 9: Implement the caller-side scheduling**

In `services/whale_stream/index_stream_handlers.py`, add to the imports:

```python
import asyncio

from services import index_feed, settlement_edge, settlement_edge_entry, tick_executor
```

(the `tick_executor` addition to the existing `from services import ...`
line; `asyncio` as a new standalone import above `time`).

Change `_process_stream_index` (currently lines 41-58):

```python
async def _process_stream_index(msg_type: str, msg: dict) -> None:
    """..."""  # docstring unchanged except the line claiming "this never
    # writes on the event loop" - see Step 10 below, remove/correct that
    # sentence rather than leaving a comment that's now literally true again
    # (the write itself is offloaded) but was false when this task started.
    if msg_type == "cfbenchmarks_value":
        _, should_flush = index_feed.record_cfbenchmarks(msg)
        if should_flush:
            asyncio.create_task(tick_executor.run(index_feed.flush))
        await _record_settlement_observations(msg.get("index_id"))
    elif msg_type == "pyth_value":
        _, should_flush = index_feed.record_pyth(msg)
        if should_flush:
            asyncio.create_task(tick_executor.run(index_feed.flush))
```

- [ ] **Step 10: Correct the docstring's now-inaccurate claim**

The docstring's line "~1 message/sec/index, buffered the same way trade
capture is, so this never writes on the event loop" was false before this
task (that's the bug) and needs to state what's actually true now, not what
was aspirationally true before. Change:

```
    ~1 message/sec/index, buffered the same way trade capture is, so this
    never writes on the event loop.
```

to:

```
    ~1 message/sec/index, buffered the same way trade capture is. The
    buffer-full flush trigger is scheduled via tick_executor.run() +
    asyncio.create_task (never awaited directly here), so this never blocks
    the event loop - confirmed as a real, previously-live bug this exact
    docstring's old wording claimed was already true (event-loop-blocking
    elimination Fix 1, 2026-09-01).
```

- [ ] **Step 11: Run the caller-side test to verify it passes**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/event-loop-blocking-fix && python -m pytest tests/test_index_stream_handlers.py -k schedules_flush_via_tick_executor -v"`
Expected: PASS

- [ ] **Step 12: Run both full test files for a regression check**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/event-loop-blocking-fix && python -m pytest tests/test_index_feed.py tests/test_index_stream_handlers.py -v"`
Expected: PASS (all)

- [ ] **Step 13: Commit**

```bash
git add services/index_feed/ingestion.py services/whale_stream/index_stream_handlers.py tests/test_index_feed.py tests/test_index_stream_handlers.py
git commit -m "fix: record_cfbenchmarks/record_pyth stop blocking the event loop on buffer-full flush"
```

---

### Task 2: `services/settlement_edge.py` — `record_observation` stops flushing inline

**Files:**
- Modify: `services/settlement_edge.py:103-153` (`record_observation`)
- Modify: `services/whale_stream/index_stream_handlers.py:117-157`
  (`_record_settlement_observations` — already touched by Task 1 for its
  imports; this task only changes the `record_observation` call site inside
  it)
- Test: `tests/test_settlement_edge.py` (extend existing file)
- Test: `tests/test_index_stream_handlers.py` (extend existing file)

**Interfaces:**
- Consumes: `tick_executor` and `asyncio` imports already added to
  `index_stream_handlers.py` by Task 1 — do not re-add them.
- Produces: `record_observation(...) -> tuple[bool, bool]` (`accepted,
  should_flush`), same shape as Task 1's `record_cfbenchmarks`/`record_pyth`.

- [ ] **Step 1: Confirm no other caller depends on the inline flush**

Run: `grep -rn "record_observation" services/ main.py tests/`

Expected: the only non-test, non-definition call site is
`services/whale_stream/index_stream_handlers.py:152`. If anything else calls
this function, stop and re-scope.

- [ ] **Step 2: Write the failing test**

Add to `tests/test_settlement_edge.py` (check its existing fixture helper
first, same as Task 1's approach):

```python
def test_record_observation_returns_should_flush_without_flushing(monkeypatch, tmp_path):
    _settlement_edge(tmp_path, monkeypatch)  # match this file's actual existing fixture name
    flush_calls = []
    monkeypatch.setattr(settlement_edge, "flush", lambda: flush_calls.append(1) or {})
    spec = {"index_id": "KXBTC", "strike": 50000.0, "comparison": "greater"}
    projection = {"status": "accumulating", "observations_known": 10, "partial_average": 50100.0}
    for i in range(settlement_edge._FLUSH_BATCH - 1):
        accepted, should_flush = settlement_edge.record_observation("TICK-A", spec, projection, 0.55)
        assert accepted is True
        assert should_flush is False
    accepted, should_flush = settlement_edge.record_observation("TICK-A", spec, projection, 0.55)
    assert accepted is True
    assert should_flush is True
    assert flush_calls == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/event-loop-blocking-fix && python -m pytest tests/test_settlement_edge.py -k returns_should_flush -v"`
Expected: FAIL — current `record_observation` returns a plain `bool`.

- [ ] **Step 4: Implement**

In `services/settlement_edge.py`, change `record_observation` (lines
103-153):

```python
def record_observation(ticker: str, spec: dict, projection: dict,
                       market_yes_price: float | None, now: float | None = None) -> tuple[bool, bool]:
    """One paired forecast, taken while the settlement window is open.

    Records BOTH predictors at the same instant - the market's yes price and
    everything needed to derive the projection - so neither can be
    reconstructed later with hindsight leaking in. Only fires while
    projection["status"] == "accumulating"; outside the window there is
    nothing to compare.

    Returns (accepted, should_flush) - accepted is the original "was this
    row recorded" signal; should_flush tells the caller a buffer-full flush
    is now due, to be scheduled off the event loop (see services/
    whale_stream/index_stream_handlers.py's _record_settlement_observations)
    rather than called inline here - event-loop-blocking elimination Fix 1,
    2026-09-01."""
    try:
        if projection.get("status") != "accumulating":
            return False, False
        now = now if now is not None else time.time()
        window_end = close_ts(spec)
        row = (
            ticker, spec["index_id"], window_end or 0.0, now,
            (window_end - now) if window_end else None,
            projection["observations_known"], projection["partial_average"],
            spec["strike"], spec["comparison"], projection.get("spot"),
            projection.get("required_remaining"), projection.get("gap_from_spot"),
            market_yes_price,
        )
        with _buffer_lock:
            _buffer.append(row)
            should_flush = len(_buffer) >= _FLUSH_BATCH
        return True, should_flush
    except Exception as exc:
        global _record_errors
        _record_errors += 1
        fault_log.record("settlement_edge", "record_observation", exc)
        return False, False
```

- [ ] **Step 5: Run the test, verify it passes**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/event-loop-blocking-fix && python -m pytest tests/test_settlement_edge.py -k returns_should_flush -v"`
Expected: PASS

- [ ] **Step 6: Run the full file, fix any test now asserting the old plain-bool return**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/event-loop-blocking-fix && python -m pytest tests/test_settlement_edge.py -v"`

Same as Task 1 Step 6 — update any failing assertion to unpack the 2-tuple,
matching what that test actually cared about.

- [ ] **Step 7: Write the failing caller-side test**

Add to `tests/test_index_stream_handlers.py`:

```python
def test_record_settlement_observations_schedules_flush_via_tick_executor_when_told(monkeypatch):
    from services import settlement_edge, tick_executor

    # index_feed.latest/window_matches_close/settlement_projection and
    # state["markets"]/state["latest_prices"] all need real-enough stand-ins
    # to reach the record_observation call - check this file's existing
    # settlement-observation-adjacent tests (if any) for the established
    # fixture pattern via `grep -n "index_feed.latest\|window_matches_close"
    # tests/test_index_stream_handlers.py` before writing this from scratch;
    # if none exists yet, monkeypatch each of index_feed.latest (return a
    # dict with q15_window_size set), index_feed.window_matches_close
    # (return True), index_feed.settlement_projection (return a dict with
    # status: "accumulating"), and state["markets"]/state["latest_prices"]
    # directly to reach the call.
    scheduled = []
    real_create_task = asyncio.create_task

    def spy_create_task(coro):
        scheduled.append(coro)
        return real_create_task(coro)

    monkeypatch.setattr(asyncio, "create_task", spy_create_task)

    tick_executor_calls = []

    async def fake_tick_executor_run(fn):
        tick_executor_calls.append(fn)
        return fn()

    monkeypatch.setattr(tick_executor, "run", fake_tick_executor_run)
    monkeypatch.setattr(settlement_edge, "record_observation", lambda *a, **k: (True, True))

    asyncio.run(ish._record_settlement_observations("KXBTC"))

    assert len(scheduled) == 1
    assert tick_executor_calls[0] is settlement_edge.flush
```

- [ ] **Step 8: Run the caller-side test to verify it fails**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/event-loop-blocking-fix && python -m pytest tests/test_index_stream_handlers.py -k record_settlement_observations_schedules -v"`
Expected: FAIL

- [ ] **Step 9: Implement the caller-side scheduling**

In `services/whale_stream/index_stream_handlers.py`, change line 152 (inside
`_record_settlement_observations`) from:

```python
        settlement_edge.record_observation(ticker, spec, projection, market_price)
```

to:

```python
        _, should_flush = settlement_edge.record_observation(ticker, spec, projection, market_price)
        if should_flush:
            asyncio.create_task(tick_executor.run(settlement_edge.flush))
```

- [ ] **Step 10: Run the caller-side test to verify it passes**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/event-loop-blocking-fix && python -m pytest tests/test_index_stream_handlers.py -k record_settlement_observations_schedules -v"`
Expected: PASS

- [ ] **Step 11: Run both full test files for a regression check**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/event-loop-blocking-fix && python -m pytest tests/test_settlement_edge.py tests/test_index_stream_handlers.py -v"`
Expected: PASS (all)

- [ ] **Step 12: Commit**

```bash
git add services/settlement_edge.py services/whale_stream/index_stream_handlers.py tests/test_settlement_edge.py tests/test_index_stream_handlers.py
git commit -m "fix: record_observation stops blocking the event loop on buffer-full flush"
```

---

### Task 3: `services/game_state.py` — `record` stops flushing inline

**Files:**
- Modify: `services/game_state.py:226-299` (`record`)
- Modify: `services/market_watch/event_metadata.py` (add `from services
  import tick_executor` import; caller at line 215)
- Modify: `services/market_watch/live_status.py` (add `import asyncio` and
  `from services import tick_executor` imports; caller at line 296)
- Test: `tests/test_game_state.py` (extend existing file)
- Test: `tests/test_market_watch_event_metadata.py` (new file)
- Test: `tests/test_market_watch_live_status.py` (new file)

**Interfaces:**
- Produces: `record(...) -> tuple[bool, bool]` (`accepted, should_flush`),
  same shape as Tasks 1-2.

- [ ] **Step 1: Confirm the exact call sites**

Run: `grep -rn "game_state\.record(" services/ main.py tests/`

Expected: exactly two non-test, non-definition call sites -
`services/market_watch/event_metadata.py:215` and
`services/market_watch/live_status.py:296`. If anything else calls this
function, stop and re-scope.

- [ ] **Step 2: Write the failing test**

Add to `tests/test_game_state.py` (check its existing fixture helper first):

```python
def test_record_returns_should_flush_without_flushing(monkeypatch, tmp_path):
    _game_state(tmp_path, monkeypatch)  # match this file's actual existing fixture name
    flush_calls = []
    monkeypatch.setattr(game_state, "flush", lambda: flush_calls.append(1) or {})
    details = {"type": "football_game", "status": "in_progress", "home_score": 7, "away_score": 3}
    for i in range(game_state._FLUSH_BATCH - 1):
        accepted, should_flush = game_state.record(f"EVT-{i}", details)
        assert accepted is True
        assert should_flush is False
    accepted, should_flush = game_state.record(f"EVT-{game_state._FLUSH_BATCH}", details)
    assert accepted is True
    assert should_flush is True
    assert flush_calls == []
```

Note: `record`'s own dedup logic (same fingerprint = no-op) means each call
above needs a distinct `event_ticker` to actually append a new row each time
- the loop variable does that.

- [ ] **Step 3: Run test to verify it fails**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/event-loop-blocking-fix && python -m pytest tests/test_game_state.py -k returns_should_flush -v"`
Expected: FAIL

- [ ] **Step 4: Implement**

In `services/game_state.py`, change `record` (lines 226-299) — only the
signature, docstring return note, and the final block (lines 291-296)
change; the body in between (lines 242-290) is untouched:

```python
def record(event_ticker: str, details: dict, sport: str | None = None,
           event_type: str | None = None, now: float | None = None) -> tuple[bool, bool]:
    """Persist one live-data observation. Returns (accepted, should_flush) -
    accepted is False for an unchanged payload (deduplicated), one inside
    the per-event minimum interval, a missing event ticker, or any error;
    should_flush tells the caller a buffer-full flush is now due, to be
    scheduled off the event loop rather than called inline here -
    event-loop-blocking elimination Fix 1, 2026-09-01.

    Handles ANY live-data shape, not just games: Kalshi's `type` field
    distinguishes `football_game` from `crypto`, and the crypto payload
    carries OHLC candlesticks plus an underlying price timeseries rather
    than a score. Sport-specific columns stay NULL for those, and the whole
    payload lands in raw_json either way - a shape this module has never
    seen still gets its complete record stored from the day it appears,
    which is the entire discipline here.

    Never raises: called from the trading loop, where an exception would
    cost the tick."""
    try:
        if not event_ticker or not details:
            return False, False
        if (event_type or details.get("type")) == "crypto":
            return False, False
        now = now if now is not None else time.time()
        last = _last_write_at.get(event_ticker)
        if last is not None and (now - last) < _MIN_INTERVAL_SEC:
            return False, False
        fields = extract(details, sport)
        fp = _fingerprint(fields)
        if fp == "None|None|None|None|None|None|None|None":
            fp = str(hash(json.dumps(details, sort_keys=True, default=str)))
        if _last_fingerprint.get(event_ticker) == fp:
            return False, False
        _last_fingerprint[event_ticker] = fp
        _last_write_at[event_ticker] = now
        row = (
            event_ticker, sport, event_type or details.get("type"), now,
            fields["source_updated_ts"], fields["status"], fields["widget_status"],
            fields["home_score"], fields["away_score"], fields["period"],
            fields["period_label"], fields["clock"], fields["winner"],
            fields["last_play"], fields["last_play_ts"],
            json.dumps(details, default=str),
        )
        with _buffer_lock:
            _buffer.append(row)
            should_flush = len(_buffer) >= _FLUSH_BATCH
        return True, should_flush
    except Exception as exc:
        fault_log.record("game_state", "record", exc)
        return False, False
```

- [ ] **Step 5: Run the test, verify it passes**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/event-loop-blocking-fix && python -m pytest tests/test_game_state.py -k returns_should_flush -v"`
Expected: PASS

- [ ] **Step 6: Run the full file, fix any test asserting the old plain-bool return**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/event-loop-blocking-fix && python -m pytest tests/test_game_state.py -v"`

Same pattern as Tasks 1-2's Step 6.

- [ ] **Step 7: Write the failing caller-side test for `event_metadata.py`**

Create `tests/test_market_watch_event_metadata.py`. First read
`services/market_watch/event_metadata.py`'s `_fetch_event_live_data` in full
(`grep -n "async def _fetch_event_live_data" -A 60
services/market_watch/event_metadata.py`) to confirm its exact parameters and
what `state`/`cache` setup its own test needs - the snippet below assumes a
minimal reachable path, adjust the setup to match what that function actually
requires to reach the `game_state.record` call:

```python
"""Tests for services/market_watch/event_metadata.py's game_state.record()
call site - event-loop-blocking elimination Fix 1, 2026-09-01."""
import asyncio

import services.market_watch.event_metadata as em
from services import game_state, tick_executor
from services.app_state import state


def test_fetch_event_live_data_schedules_flush_via_tick_executor_when_told(monkeypatch):
    scheduled = []
    real_create_task = asyncio.create_task

    def spy_create_task(coro):
        scheduled.append(coro)
        return real_create_task(coro)

    monkeypatch.setattr(asyncio, "create_task", spy_create_task)

    tick_executor_calls = []

    async def fake_tick_executor_run(fn):
        tick_executor_calls.append(fn)
        return fn()

    monkeypatch.setattr(tick_executor, "run", fake_tick_executor_run)
    monkeypatch.setattr(game_state, "record", lambda *a, **k: (True, True))

    # Reach the call site - adjust to em._fetch_event_live_data's actual
    # required setup (client stand-in, markets list shape) confirmed from
    # its source above.
    class _FakeClient:
        async def get_event(self, event_ticker):
            return {"live_data": {"type": "football_game", "details": {"status": "in_progress"}}}

    asyncio.run(em._fetch_event_live_data(_FakeClient(), [{"event_ticker": "EVT-A", "ticker": "EVT-A-25"}]))

    assert len(scheduled) == 1
    assert tick_executor_calls[0] is game_state.flush
```

- [ ] **Step 8: Run the test to verify it fails**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/event-loop-blocking-fix && python -m pytest tests/test_market_watch_event_metadata.py -v"`
Expected: FAIL — either the fixture setup needs adjusting to actually reach
the call (fix the setup against the real function signature first, per
Step 7's own instruction to read the source before trusting this sketch), or
once it does reach the call, FAIL because `event_metadata.py` doesn't yet
schedule anything.

- [ ] **Step 9: Implement the caller-side scheduling in `event_metadata.py`**

Add to `services/market_watch/event_metadata.py`'s imports:

```python
from services import game_state, tick_executor
```

(or add `tick_executor` to whatever existing `from services import ...` line
already imports `game_state`, if one exists — confirm via `grep -n "^from
services import\|^import" services/market_watch/event_metadata.py` first;
`import asyncio` is already present per this task's own earlier verification
— do not re-add it.)

Change lines 214-219 from:

```python
            if live_data and (live_data.get("details") or {}):
                game_state.record(
                    et, live_data["details"],
                    sport=_sport_for_event(state["event_titles"].get(et) or {}),
                    event_type=live_data.get("type"),
                )
```

to:

```python
            if live_data and (live_data.get("details") or {}):
                _, should_flush = game_state.record(
                    et, live_data["details"],
                    sport=_sport_for_event(state["event_titles"].get(et) or {}),
                    event_type=live_data.get("type"),
                )
                if should_flush:
                    asyncio.create_task(tick_executor.run(game_state.flush))
```

- [ ] **Step 10: Run the test to verify it passes**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/event-loop-blocking-fix && python -m pytest tests/test_market_watch_event_metadata.py -v"`
Expected: PASS

- [ ] **Step 11: Write the failing caller-side test for `live_status.py`**

Create `tests/test_market_watch_live_status.py`. First read `services/
market_watch/live_status.py`'s `_fetch_live_status` in full (`grep -n "async
def _fetch_live_status" -A 260 services/market_watch/live_status.py | tail
-80` to see the section actually containing the `game_state.record` call
around line 296) to confirm the exact reachable path and required setup,
same caution as Step 7:

```python
"""Tests for services/market_watch/live_status.py's game_state.record()
call site - event-loop-blocking elimination Fix 1, 2026-09-01."""
import asyncio

import services.market_watch.live_status as ls
from services import game_state, tick_executor
from services.app_state import state


def test_fetch_live_status_schedules_flush_via_tick_executor_when_told(monkeypatch):
    scheduled = []
    real_create_task = asyncio.create_task

    def spy_create_task(coro):
        scheduled.append(coro)
        return real_create_task(coro)

    monkeypatch.setattr(asyncio, "create_task", spy_create_task)

    tick_executor_calls = []

    async def fake_tick_executor_run(fn):
        tick_executor_calls.append(fn)
        return fn()

    monkeypatch.setattr(tick_executor, "run", fake_tick_executor_run)
    monkeypatch.setattr(game_state, "record", lambda *a, **k: (True, True))

    # Reach the call site - adjust to _fetch_live_status's actual required
    # setup (client stand-in, markets list shape, milestone_live_data state)
    # confirmed from its source above before trusting this sketch.
    class _FakeClient:
        async def get_market(self, ticker):
            return {"market": {"ticker": ticker}}

    asyncio.run(ls._fetch_live_status(_FakeClient(), [{"ticker": "EVT-A-25", "event_ticker": "EVT-A"}]))

    assert len(scheduled) == 1
    assert tick_executor_calls[0] is game_state.flush
```

- [ ] **Step 12: Run the test to verify it fails**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/event-loop-blocking-fix && python -m pytest tests/test_market_watch_live_status.py -v"`
Expected: FAIL

- [ ] **Step 13: Implement the caller-side scheduling in `live_status.py`**

Add to `services/market_watch/live_status.py`'s imports:

```python
import asyncio

from services import tick_executor
```

Change lines 296-297 from:

```python
                game_state.record(et, details, sport=_sport_for_event(
                    state["event_titles"].get(et) or {}))
```

to:

```python
                _, should_flush = game_state.record(et, details, sport=_sport_for_event(
                    state["event_titles"].get(et) or {}))
                if should_flush:
                    asyncio.create_task(tick_executor.run(game_state.flush))
```

- [ ] **Step 14: Run the test to verify it passes**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/event-loop-blocking-fix && python -m pytest tests/test_market_watch_live_status.py -v"`
Expected: PASS

- [ ] **Step 15: Run all four touched test files for a regression check**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/event-loop-blocking-fix && python -m pytest tests/test_game_state.py tests/test_market_watch_event_metadata.py tests/test_market_watch_live_status.py tests/test_milestone_live_data.py -v"`
Expected: PASS (all)

- [ ] **Step 16: Commit**

```bash
git add services/game_state.py services/market_watch/event_metadata.py services/market_watch/live_status.py tests/test_game_state.py tests/test_market_watch_event_metadata.py tests/test_market_watch_live_status.py
git commit -m "fix: game_state.record stops blocking the event loop on buffer-full flush"
```

---

### Task 4: `services/series_watcher.py` — `record_book` stops flushing inline

**Files:**
- Modify: `services/series_watcher.py:303-344` (`record_book`)
- Modify: `services/whale_stream/whale_stream_handlers.py` (add `from
  services import tick_executor` import; caller at line 287, inside
  `_process_stream_ticker`)
- Test: `tests/test_series_watcher.py` (extend existing file)
- Test: `tests/test_whale_stream_stage_timing.py` (extend existing file —
  already imports `whale_stream_handlers as wsh`)

**Interfaces:**
- Produces: `record_book(...) -> tuple[bool, bool]` (`accepted,
  should_flush`), same shape as Tasks 1-3. `record_trade` (already correct,
  routes through `capture_writer.submit()`) is untouched — do not change its
  signature.

- [ ] **Step 1: Confirm the exact call site**

Run: `grep -rn "series_watcher\.record_book\|\.record_book(" services/ main.py tests/`

Expected: the only non-test, non-definition call site is
`services/whale_stream/whale_stream_handlers.py:287`, inside
`_process_stream_ticker`. If anything else calls this function, stop and
re-scope.

- [ ] **Step 2: Write the failing test**

Add to `tests/test_series_watcher.py` (check its existing fixture helper
first):

```python
def test_record_book_returns_should_flush_without_flushing(monkeypatch, tmp_path):
    _series_watcher(tmp_path, monkeypatch)  # match this file's actual existing fixture name
    monkeypatch.setattr(series_watcher, "watched_series", lambda cfg=None: ["KXTEST"])
    monkeypatch.setattr(series_watcher, "capture_enabled", lambda cfg=None: True)
    monkeypatch.setattr(series_watcher.signal_log, "series_of", lambda ticker: "KXTEST")
    flush_calls = []
    monkeypatch.setattr(series_watcher, "flush", lambda: flush_calls.append(1) or {})
    ticker_msg_base = {"market_ticker": "KXTEST-25", "price_dollars": "0.55"}
    for i in range(series_watcher._FLUSH_BATCH - 1):
        # book_snapshot_interval_sec throttles per-ticker - pass an explicit,
        # increasing `now` so each call clears the interval, matching how
        # this function's own throttle check works.
        accepted, should_flush = series_watcher.record_book(ticker_msg_base, now=1000.0 + i * 100)
        assert accepted is True
        assert should_flush is False
    accepted, should_flush = series_watcher.record_book(
        ticker_msg_base, now=1000.0 + series_watcher._FLUSH_BATCH * 100,
    )
    assert accepted is True
    assert should_flush is True
    assert flush_calls == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/event-loop-blocking-fix && python -m pytest tests/test_series_watcher.py -k returns_should_flush -v"`
Expected: FAIL — current `record_book` returns a plain `bool`.

- [ ] **Step 4: Implement**

In `services/series_watcher.py`, change `record_book` (lines 303-344) — only
the signature, docstring, and final block (lines 336-341) change; the body
in between is untouched:

```python
def record_book(ticker_msg: dict, cfg: dict | None = None, now: float | None = None) -> tuple[bool, bool]:
    """Persist one `ticker`-channel book snapshot in full — every field
    docs/kalshi/market-ticker.md documents, not just the two prices
    _process_stream_ticker keeps.

    Throttled per ticker (series_watcher.book_snapshot_interval_sec) since
    the channel fires on every field change. Same never-raises contract as
    record_trade.

    Returns (accepted, should_flush) - accepted is the original "was this
    row buffered" signal; should_flush tells the caller a buffer-full flush
    is now due, to be scheduled off the event loop rather than called
    inline here - event-loop-blocking elimination Fix 1, 2026-09-01. This
    function fires on every ticker-channel field change, making it plausibly
    the highest-frequency of the four functions this fix touches."""
    try:
        ticker = ticker_msg.get("market_ticker") or ticker_msg.get("ticker")
        if not ticker or not capture_enabled(cfg):
            return False, False
        series = signal_log.series_of(ticker)
        if series not in watched_series(cfg):
            return False, False

        now = now if now is not None else time.time()
        interval = float(_cfg_section(cfg).get("book_snapshot_interval_sec", _DEFAULT_BOOK_INTERVAL_SEC))
        last = _last_book_write.get(ticker)
        if last is not None and (now - last) < interval:
            return False, False
        _last_book_write[ticker] = now

        row = (
            ticker, series, now, _exchange_ts(ticker_msg),
            _float(ticker_msg.get("price_dollars")), _float(ticker_msg.get("yes_bid_dollars")),
            _float(ticker_msg.get("yes_ask_dollars")), _float(ticker_msg.get("yes_bid_size_fp")),
            _float(ticker_msg.get("yes_ask_size_fp")), _float(ticker_msg.get("volume_fp")),
            _float(ticker_msg.get("open_interest_fp")), _float(ticker_msg.get("dollar_volume")),
            _float(ticker_msg.get("dollar_open_interest")), _float(ticker_msg.get("last_trade_size_fp")),
            json.dumps(ticker_msg, default=str),
        )
        with _buffer_lock:
            _book_buffer.append(row)
            should_flush = len(_book_buffer) >= _FLUSH_BATCH
        return True, should_flush
    except Exception as exc:
        fault_log.record("series_watcher", "record_book", exc)
        return False, False
```

- [ ] **Step 5: Run the test, verify it passes**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/event-loop-blocking-fix && python -m pytest tests/test_series_watcher.py -k returns_should_flush -v"`
Expected: PASS

- [ ] **Step 6: Run the full file, fix any test asserting the old plain-bool return**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/event-loop-blocking-fix && python -m pytest tests/test_series_watcher.py -v"`

Same pattern as prior tasks' equivalent step.

- [ ] **Step 7: Write the failing caller-side test**

Add to `tests/test_whale_stream_stage_timing.py` — check this file's existing
setup for reaching `_process_stream_ticker` first (`grep -n
"_process_stream_ticker\|def _fresh_state\|state\[" tests/
test_whale_stream_stage_timing.py`), match its established fixture pattern:

```python
def test_process_stream_ticker_schedules_flush_via_tick_executor_when_told(monkeypatch):
    from services import series_watcher, tick_executor

    scheduled = []
    real_create_task = asyncio.create_task

    def spy_create_task(coro):
        scheduled.append(coro)
        return real_create_task(coro)

    monkeypatch.setattr(asyncio, "create_task", spy_create_task)

    tick_executor_calls = []

    async def fake_tick_executor_run(fn):
        tick_executor_calls.append(fn)
        return fn()

    monkeypatch.setattr(tick_executor, "run", fake_tick_executor_run)
    monkeypatch.setattr(series_watcher, "record_book", lambda *a, **k: (True, True))

    asyncio.run(wsh._process_stream_ticker({
        "ticker": "KXTEST-25", "yes_bid_dollars": "0.55",
    }))

    assert len(scheduled) == 1
    assert tick_executor_calls[0] is series_watcher.flush
```

- [ ] **Step 8: Run the test to verify it fails**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/event-loop-blocking-fix && python -m pytest tests/test_whale_stream_stage_timing.py -k process_stream_ticker_schedules -v"`
Expected: FAIL

- [ ] **Step 9: Implement the caller-side scheduling**

Confirm `services/whale_stream/whale_stream_handlers.py` already imports
`asyncio` (per this task's own Files section — it does, no change needed
there). Add to its imports:

```python
from services import tick_executor
```

(add to whatever existing `from services import ...` line is present, or as
its own line — confirm the file's current import block via `grep -n
"^from services import\|^import" services/whale_stream/
whale_stream_handlers.py` first).

Change line 287 from:

```python
    series_watcher.record_book(ticker_msg, config_store.get(), now)
```

to:

```python
    _, should_flush = series_watcher.record_book(ticker_msg, config_store.get(), now)
    if should_flush:
        asyncio.create_task(tick_executor.run(series_watcher.flush))
```

- [ ] **Step 10: Run the test to verify it passes**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/event-loop-blocking-fix && python -m pytest tests/test_whale_stream_stage_timing.py -k process_stream_ticker_schedules -v"`
Expected: PASS

- [ ] **Step 11: Run both full test files for a regression check**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/event-loop-blocking-fix && python -m pytest tests/test_series_watcher.py tests/test_whale_stream_stage_timing.py -v"`
Expected: PASS (all)

- [ ] **Step 12: Commit**

```bash
git add services/series_watcher.py services/whale_stream/whale_stream_handlers.py tests/test_series_watcher.py tests/test_whale_stream_stage_timing.py
git commit -m "fix: record_book stops blocking the event loop on buffer-full flush"
```

---

### Task 5: Full regression suite + required live validation

**Not a code task** — the empirical confirmation this fix actually resolves
the live symptom, per this repo's own standing practice (matching the
write-path capacity fix's Task 7/8 earlier today).

- [ ] **Step 1: Run the full local test suite**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/event-loop-blocking-fix && python -m pytest tests/ -q -m 'not slow'"`

Expected: same pass count as the pre-existing baseline (2991 passed, 6 failed
- the already-documented, pre-existing "no git binary in this dev container"
gap in `test_quality_coordination_cleanup_actions.py`, unrelated to this
fix) plus the new tests this plan added, all passing. Any OTHER failure is a
real regression - investigate before proceeding.

- [ ] **Step 2: `import main` sanity check**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/event-loop-blocking-fix && python -c 'import main' && echo IMPORT_OK"`
Expected: `IMPORT_OK`, no import errors from the new `tick_executor`/
`asyncio` imports across the 5 touched caller files.

- [ ] **Step 3: Deploy and capture a live before/after reading**

After this branch merges and the primary checkout's running app reloads it
(same procedure as the write-path capacity fix's own deployment earlier
today: `git merge origin/main` on primary, confirm via
`docker logs --timestamps ddev-kalshi-whale-poc-fastapi` that WatchFiles
picked up the changed files and the server process restarted), watch for at
least 15 minutes of real WS traffic (this fix's four functions fire on real
market data - ticker/index/settlement/game-state messages - not synthetic
load) and confirm via `docker logs --timestamps ddev-kalshi-whale-poc-fastapi`
that there is no repeat of the previously-observed complete request-logging
silence gap (this session's own reproduction: 2026-09-01 16:17:49-16:18:34,
13 minutes total stall). `GET /api/health/pipeline`'s `last_tick_duration_sec`
should stay in the single-to-low-double-digit-second range, not the 113-228s
range measured before this fix (that range came from a different, already-
fixed mechanism - the write-path capacity fix, PR #409 - so a return to that
specific range would mean THIS fix didn't address the residual stall, not
that the prior fix regressed).

- [ ] **Step 4: Record the result**

Write the outcome into `docs/next-action.md` (whatever this plan's item
currently occupies there) and `docs/open-decisions.md` if anything remains
open (e.g., if Step 3's watch period still shows any stall, however smaller
- that would mean a fifth instance of this bug class exists somewhere this
plan's app-wide grep (`should_flush = `) didn't catch, or a genuinely
different mechanism, and needs its own fresh investigation rather than being
silently left unrecorded).

---

## Plan self-review

**Spec coverage:** The spec's Fix 1 section, in full — root cause,
`create_task`-not-`await` design decision, all four files (including the
mid-conversation-discovered fourth instance, `series_watcher.record_book`),
and the "why removing the inline trigger outright is unsafe" reasoning (kept
as the backstop, just offloaded) — maps directly onto Tasks 1-4. The spec's
required Testing subsection (per-function + per-caller tests, existing
`asyncio.run()`-wrapping convention) maps onto every task's Steps 2/7 (or
2/11 for Task 3, which has two callers). Task 5 covers the spec's implicit
expectation (matching the write-path capacity fix's own precedent this
session) that a fix targeting a live incident gets a live, measured
confirmation before being trusted, not just green tests.

**Placeholder scan:** Every code block is real, transcribed-and-verified
current source with the actual diff applied, not a paraphrase — each
function's untouched middle section is reproduced in full in Steps 4/9/4/4
of Tasks 1-4 rather than eliding it, since an implementer working from one
task in isolation needs the whole function, not a diff fragment assuming
they have the original open. The three NEW test files (Task 3's
`event_metadata`/`live_status` tests, none pre-existing) are explicit about
needing the implementer to re-read the real target function's signature
before trusting the sketch, per this repo's own "read before writing" HARD
RULE — this is not a placeholder in the forbidden sense (it doesn't skip
telling the implementer what to do), but a scoping decision made explicit
because this plan's author could not independently re-verify every detail of
those two functions' exact reachable-call-path fixture requirements within
this planning pass without materially expanding its scope; flagging this
honestly rather than guessing at fixture shapes that might not compile.

**Type consistency:** `record_cfbenchmarks`/`record_pyth`/
`record_observation`/`record`/`record_book` all change from `bool` to
`tuple[bool, bool]` identically across Tasks 1-4; every caller-side change
unpacks with the same `_, should_flush = ...` pattern; every scheduled call
uses the identical `asyncio.create_task(tick_executor.run(<module>.flush))`
shape. `tick_executor.run`'s real signature
(`async def run(fn: Callable[[], T]) -> T`, confirmed against
`services/tick_executor.py:102`) matches every call site's usage
(`tick_executor.run(index_feed.flush)` etc. — passing the bound function
itself, no lambda needed, since none of the four `flush()` functions take
arguments).

**Gap found in this self-review, fixed inline rather than left for later:**
Task 1's Step 4 initially risked silently changing `record_cfbenchmarks`'s
return-type contract in a way that could look like a plain behavior change
rather than a deliberate signature change - added an explicit inline note
in that step (now present) calling out exactly why the naive single-bool
collapse was rejected, so an implementer reading only that step still gets
the reasoning, not just the corrected code.
