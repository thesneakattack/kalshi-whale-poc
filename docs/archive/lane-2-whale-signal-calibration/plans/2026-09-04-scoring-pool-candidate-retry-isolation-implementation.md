# `_scoring_pool`/candidate-retry Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `candidate_retry`'s scoring path its own dedicated 1-worker thread pool so it no longer shares `services/whalewatchers/_scoring_pool.py`'s 4-worker pool with the WS-trade-scoring path.

**Architecture:** A new `services/whalewatchers/_candidate_retry_pool.py` holding a single-worker `ThreadPoolExecutor` and a `run()` wrapper mirroring `_scoring_pool.run`'s signature exactly. `kalshi_trade_tape.py`'s `score_recovered_trade()` — the only entry point on the candidate-retry path — submits to it instead of `_scoring_pool`. `fetch_signals()` (the WS path) is untouched and keeps `_scoring_pool` to itself. Nothing about connection caching changes: the three downstream modules that cache read connections call `_scoring_pool.cached_read_connection(...)` **by name**, and that function keys its cache on `threading.local()` — thread-keyed, not pool-keyed — so a thread from the new pool transparently gets its own cache slot with no code change in those modules.

**Tech Stack:** Python 3.13, `concurrent.futures.ThreadPoolExecutor`, `asyncio.run_in_executor`, pytest.

**Spec:** `docs/superpowers/specs/2026-09-03-scoring-pool-candidate-retry-isolation-design.md` (merged, PR #566), plus its `-self-review.md` and `-consolidation.md` siblings. Parent problem tracked as issue #563.

## Global Constraints

- **CLAUDE.md data-plane HARD RULE**: no trade, signal, or recovered candidate may be dropped, delayed indefinitely, coalesced, or skipped by this change. This moves *which executor* runs existing work; it changes no gate, no scoring math, no ordering guarantee.
- **No capacity tuning without measurement**: `_scoring_pool`'s own `max_workers=4` is NOT changed by this plan. The new pool's `max_workers=1` is derived from a verified fact (`candidate_retry.run_pending()` submits strictly serially — one `await` per trade, no `gather`/`create_task` fan-out), not from a guess.
- **#546 must keep working**: `self._seen_lock` (`kalshi_trade_tape.py:273`) is an **instance** attribute — a real `threading.Lock`, not pool-scoped — so it still correctly serializes the check-through-mark span across threads from *either* pool. The race it closes remains possible after this change (two pools' threads still touch the same provider instance's `_seen_trade_ids`/`_seen_order`), so the lock stays and its tests stay meaningful.
- **Out of scope, do not fix here**: issue #565 (provider-instance divergence after an account reconnect) and issue #410 (`tick_executor` sharing with diagnostic routes). Both are separately tracked and orthogonal.
- **Exchange-wide hot path**: `score_recovered_trade` is not itself the exchange-wide hot path (`fetch_signals` is, and it is untouched), but both live in the same module — keep the diff minimal and additive.

---

### Task 1: The dedicated candidate-retry pool module

**Files:**
- Create: `services/whalewatchers/_candidate_retry_pool.py`
- Test: `tests/test_whalewatchers_candidate_retry_pool.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `async def run(fn: Callable[[], T]) -> T` in `services/whalewatchers/_candidate_retry_pool.py` — same signature and semantics as `services/whalewatchers/_scoring_pool.py`'s `run`, executing `fn` on a worker thread named with the `candidate-retry-scoring` prefix. Task 2 calls this.

- [ ] **Step 1: Write the failing test**

Create `tests/test_whalewatchers_candidate_retry_pool.py`:

```python
"""Issue #563 / docs/superpowers/specs/2026-09-03-scoring-pool-candidate-
retry-isolation-design.md: candidate_retry's scoring work gets its own
1-worker pool so it no longer shares _scoring_pool.py's 4 workers with the
WS-trade-scoring path. Mirrors tests/test_whalewatchers_scoring_pool.py's
own shape for the sibling pool it was modeled on."""
import asyncio
import threading

from services.whalewatchers import _candidate_retry_pool


def test_run_executes_on_a_worker_thread_not_the_event_loop():
    result_thread_name = {}

    def _work():
        result_thread_name["name"] = threading.current_thread().name

    asyncio.run(_candidate_retry_pool.run(_work))
    assert result_thread_name["name"].startswith("candidate-retry-scoring")


def test_run_returns_the_callables_value():
    assert asyncio.run(_candidate_retry_pool.run(lambda: 42)) == 42


def test_run_propagates_an_exception_rather_than_swallowing_it():
    def _boom():
        raise ValueError("propagate me")

    with pytest.raises(ValueError, match="propagate me"):
        asyncio.run(_candidate_retry_pool.run(_boom))


def test_pool_is_single_worker_so_two_submissions_never_run_concurrently():
    """The design's sizing claim, made falsifiable rather than asserted:
    run_pending() submits strictly serially, so one worker is exactly what
    this path needs. If max_workers is ever raised without revisiting that
    reasoning, this test fails loudly instead of the change passing
    silently."""
    concurrent_peak = {"value": 0, "current": 0}
    lock = threading.Lock()

    def _work():
        with lock:
            concurrent_peak["current"] += 1
            concurrent_peak["value"] = max(concurrent_peak["value"], concurrent_peak["current"])
        time.sleep(0.05)
        with lock:
            concurrent_peak["current"] -= 1

    async def _submit_three():
        await asyncio.gather(
            _candidate_retry_pool.run(_work),
            _candidate_retry_pool.run(_work),
            _candidate_retry_pool.run(_work),
        )

    asyncio.run(_submit_three())
    assert concurrent_peak["value"] == 1
```

Add these imports at the top of the file, above the `from services...` line: `import time` and `import pytest`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/scoring-pool-isolation-plan && python -m pytest tests/test_whalewatchers_candidate_retry_pool.py -q"`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.whalewatchers._candidate_retry_pool'`.

- [ ] **Step 3: Write the module**

Create `services/whalewatchers/_candidate_retry_pool.py`:

```python
"""Dedicated 1-worker pool for candidate_retry's scoring path
(kalshi_trade_tape.score_recovered_trade -> _process_trades_sync), split
out of services/whalewatchers/_scoring_pool.py per docs/superpowers/specs/
2026-09-03-scoring-pool-candidate-retry-isolation-design.md (issue #563).

Why a separate pool rather than more workers on the shared one: the two
callers are independently scheduled and have very different latency
requirements. The WS-trade path (_process_stream_trade -> fetch_signals)
is the exchange-wide real-time path, and since PR #555 it can have up to
_TRADE_DISPATCH_CONCURRENCY (4) trades dispatched concurrently - it can
legitimately want all 4 of _scoring_pool's workers by itself. The
candidate-retry path is a catch-up sweep on main.py's
_candidate_retry_loop, waking on an interval and only acting when
something is pending. Sharing meant a retry submission arriving during a
WS burst queued behind up to 4 real-time scoring calls, and - in the other
direction - a stuck retry thread (issue #145/#150's known, separately-
tracked thread-leak shape) permanently cost the WS path one of its 4
workers.

1 worker, not a guess: candidate_retry.run_pending() submits strictly
serially - one `await provider.score_recovered_trade(...)` per pending
trade in a plain `for` loop, no asyncio.gather/create_task fan-out - and
main.py's _candidate_retry_loop is a single supervised task that awaits
run_pending() to completion before its next iteration, so two run_pending()
calls can never overlap. A second worker would sit permanently idle. This
sizing depends on that serial-submission property: if run_pending() ever
fans out, revisit this number and the design doc above together, don't
just raise it (tests/test_whalewatchers_candidate_retry_pool.py's
single-worker test fails loudly if it's raised without that).

No cached_read_connection here, deliberately: the three modules that cache
read connections for this scoring path (signal_log.py, market_history.py,
market_analyst_agent/_db.py) call _scoring_pool.cached_read_connection()
by name, and that cache is keyed on threading.local() - thread-keyed, not
pool-keyed - so this pool's worker thread transparently gets its own cache
slot with no change in those modules. Duplicating the helper here would
create a second cache for the same three databases on the same thread."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypeVar

T = TypeVar("T")

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="candidate-retry-scoring")


async def run(fn: Callable[[], T]) -> T:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, fn)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/scoring-pool-isolation-plan && python -m pytest tests/test_whalewatchers_candidate_retry_pool.py -q"`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add services/whalewatchers/_candidate_retry_pool.py tests/test_whalewatchers_candidate_retry_pool.py
git commit -m "feat: dedicated 1-worker pool for candidate-retry scoring (#563)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Route `score_recovered_trade` onto it, and correct every now-stale claim about the sharing

**Files:**
- Modify: `services/whalewatchers/kalshi_trade_tape.py` (the `_scoring_pool.run(...)` call inside `score_recovered_trade`, plus its import line)
- Modify: `services/whalewatchers/_scoring_pool.py` (module docstring — its "both paths" scope and "4 workers ... plus the candidate-retry path" sizing rationale both become false with this task)
- Modify: `tests/test_whalewatchers_kalshi_trade_tape.py` (`test_score_recovered_trade_runs_on_the_dedicated_scoring_pool_and_scores_the_trade` asserts the *old* pool receives the call)
- Modify: `tests/test_kalshi_trade_tape_seen_lock_race.py` (module docstring states both paths submit "to the SAME 4-worker pool" — the premise, not the test, goes stale)

**Interfaces:**
- Consumes: `services/whalewatchers/_candidate_retry_pool.run` from Task 1.
- Produces: no new public interface. `score_recovered_trade`'s own signature, return type, and semantics are unchanged — only the executor its inner call runs on changes.

- [ ] **Step 1: Write the failing test**

In `tests/test_whalewatchers_kalshi_trade_tape.py`, **replace** `test_score_recovered_trade_runs_on_the_dedicated_scoring_pool_and_scores_the_trade` (currently at ~line 161) with:

```python
def test_score_recovered_trade_runs_on_the_candidate_retry_pool_and_scores_the_trade():
    """Issue #563: this path moved OFF _scoring_pool (which the WS-trade
    path keeps to itself) onto its own 1-worker pool. Asserts both halves -
    the new pool receives the submission AND the trade still scores
    identically - so a regression that routes it back to the shared pool
    fails here rather than silently reinstating the sharing."""
    import services.whalewatchers.kalshi_trade_tape as ktt_module

    retry_pool_calls = []
    scoring_pool_calls = []
    real_retry_run = ktt_module._candidate_retry_pool.run

    async def retry_spy(fn):
        retry_pool_calls.append(fn)
        return await real_retry_run(fn)

    async def scoring_spy(fn):
        scoring_pool_calls.append(fn)
        raise AssertionError("candidate-retry scoring must not use _scoring_pool")

    orig_retry_run = ktt_module._candidate_retry_pool.run
    orig_scoring_run = ktt_module._scoring_pool.run
    ktt_module._candidate_retry_pool.run = retry_spy
    ktt_module._scoring_pool.run = scoring_spy
    try:
        provider = KalshiTradeTapeProvider()
        trade = _trade(count_fp="10000.00", yes_price_dollars="0.60", taker_side="yes")
        market = _market()
        signals = asyncio.run(provider.score_recovered_trade(trade, market, {}, time.time()))
    finally:
        ktt_module._candidate_retry_pool.run = orig_retry_run
        ktt_module._scoring_pool.run = orig_scoring_run
    assert len(retry_pool_calls) == 1
    assert scoring_pool_calls == []
    assert len(signals) == 1
    assert signals[0].ticker == market["ticker"]


def test_the_two_scoring_paths_use_disjoint_worker_threads():
    """The actual isolation property this change delivers, made falsifiable
    rather than asserted in prose: the WS path's scoring and the
    candidate-retry path's scoring run on threads from different pools, so
    neither can occupy a worker the other needs. Before issue #563's fix
    both names started with 'whale-scoring'."""
    import services.whalewatchers.kalshi_trade_tape as ktt_module

    seen = {}
    real_process = KalshiTradeTapeProvider._process_trades_sync

    def _record_thread(self, *args, **kwargs):
        seen.setdefault("threads", []).append(threading.current_thread().name)
        return real_process(self, *args, **kwargs)

    monkeypatched = ktt_module.KalshiTradeTapeProvider
    orig = monkeypatched._process_trades_sync
    monkeypatched._process_trades_sync = _record_thread
    try:
        provider = KalshiTradeTapeProvider()
        market = _market()
        ws_trade = _trade(count_fp="10000.00", yes_price_dollars="0.60", taker_side="yes")
        asyncio.run(provider.fetch_signals(
            market_context={"markets": [market], "trade_tape": [ws_trade], "cfg": {}},
        ))
        retry_trade = _trade(
            trade_id="retry-1", count_fp="10000.00", yes_price_dollars="0.60", taker_side="yes",
        )
        asyncio.run(provider.score_recovered_trade(retry_trade, market, {}, time.time()))
    finally:
        monkeypatched._process_trades_sync = orig

    names = seen["threads"]
    assert len(names) == 2
    assert names[0].startswith("whale-scoring"), names
    assert names[1].startswith("candidate-retry-scoring"), names
```

Add `import threading` to the file's imports if it is not already present.

**Note on `_trade`'s signature:** it is this test file's own local helper. Read its definition before writing the second test — if it does not accept a `trade_id` keyword, pass whatever parameter it does expose for a distinct id, or construct the second trade dict inline. Do not assume the keyword exists.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/scoring-pool-isolation-plan && python -m pytest tests/test_whalewatchers_kalshi_trade_tape.py -k 'candidate_retry_pool or disjoint_worker_threads' -q"`
Expected: FAIL — `AttributeError: module 'services.whalewatchers.kalshi_trade_tape' has no attribute '_candidate_retry_pool'`.

- [ ] **Step 3: Read the exact current code before editing**

Run: `sed -n '385,420p' services/whalewatchers/kalshi_trade_tape.py` and `grep -n "_scoring_pool" services/whalewatchers/kalshi_trade_tape.py`
Confirm: `score_recovered_trade` ends with `return await _scoring_pool.run(lambda: self._process_trades_sync(...))`, and the module imports `_scoring_pool` at the top. Confirm `fetch_signals`'s own `_scoring_pool.run(...)` call (~line 364) is a *separate* call site that this task must leave alone.

- [ ] **Step 4: Apply the change**

In `services/whalewatchers/kalshi_trade_tape.py`, add `_candidate_retry_pool` to the existing `_scoring_pool` import (keep both — `fetch_signals` still needs `_scoring_pool`), e.g.:

```python
from services.whalewatchers import _candidate_retry_pool, _scoring_pool
```

Then, in `score_recovered_trade` only, change the submission from `_scoring_pool.run` to `_candidate_retry_pool.run` and extend the comment directly above it:

```python
        # Runs on candidate-retry's OWN 1-worker pool, not _scoring_pool
        # (issue #563, docs/superpowers/specs/2026-09-03-scoring-pool-
        # candidate-retry-isolation-design.md): fetch_signals' WS-trade
        # path keeps _scoring_pool's 4 workers to itself, since PR #555
        # lets it dispatch up to 4 trades concurrently and it can want all
        # 4. See _candidate_retry_pool.py's own docstring for the sizing.
        return await _candidate_retry_pool.run(
            lambda: self._process_trades_sync(
                [trade], [market], {ticker: market}, cfg, now, resolve_failed_tickers=set(),
            )
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/scoring-pool-isolation-plan && python -m pytest tests/test_whalewatchers_kalshi_trade_tape.py -q"`
Expected: the whole file passes, including the pre-existing `test_fetch_signals_runs_scoring_on_the_dedicated_scoring_pool` (unchanged — the WS path still uses `_scoring_pool`).

- [ ] **Step 6: Correct `_scoring_pool.py`'s now-stale docstring**

In `services/whalewatchers/_scoring_pool.py`, the first paragraph currently claims it serves "both the WS-message path ... and the candidate-retry path," and the sizing paragraph justifies 4 workers as "this call path normally needs ~1 concurrently (the WS consumer drains one queue item at a time) - headroom for legitimate brief overlap plus the candidate-retry path." Both are false after Step 4. Replace those two claims with:

```
"""Dedicated worker pool + thread-local connection cache for
kalshi_trade_tape.py's per-trade scoring work on the WS-message path
(_process_stream_trade -> fetch_signals). See docs/superpowers/specs/
2026-09-01-whale-scoring-connection-reuse-design.md for the original
design.

The candidate-retry path (score_recovered_trade) used to share this pool
and no longer does - it has its own services/whalewatchers/
_candidate_retry_pool.py as of issue #563 (docs/superpowers/specs/
2026-09-03-scoring-pool-candidate-retry-isolation-design.md). Note that
cached_read_connection() below is still called by BOTH paths' threads:
signal_log.py/market_history.py/market_analyst_agent/_db.py call it by
name, and it keys on threading.local(), so each pool's threads simply get
their own cache slots. That is deliberate, not leftover coupling.
```

Then, in the sizing paragraph, replace the "~1 concurrently ... plus the candidate-retry path" justification with:

```
4 workers: since PR #555's bounded-concurrency dispatch, the WS consumer
can have up to _TRADE_DISPATCH_CONCURRENCY (4, services/kalshi/
websocket.py) trades in flight at once, each reaching fetch_signals ->
run() here - so 4 matches that path's own structural ceiling rather than
being headroom over a single serial caller, which is what it was sized
against when written (that earlier "~1 concurrently" rationale predated
PR #555 and was stale by the time issue #563 was filed).
```

Keep the rest of the docstring (the `asyncio.to_thread` rejection, the #145/#150 bounded-and-observable reasoning, the busy-timeout paragraph) exactly as-is — all still true.

- [ ] **Step 7: Correct the seen-lock-race test's stale premise**

In `tests/test_kalshi_trade_tape_seen_lock_race.py`'s module docstring, the sentence "both submitting to the SAME 4-worker pool" is now false. **The test itself must not change** — the #546 race is still reachable after this change, because both paths still mutate the same provider instance's `_seen_trade_ids`/`_seen_order` from different threads; only the pool identity changed. Replace that clause with:

```
independently-scheduled callers - the WS stream's own consumer
(fetch_signals -> _process_trades_timed) and main.py's
_candidate_retry_loop (score_recovered_trade). They submit to two
different pools as of issue #563 (_scoring_pool.py and
_candidate_retry_pool.py respectively; they shared one 4-worker pool when
this race was found), which does NOT close this race: both still mutate
the SAME provider instance's _seen_trade_ids/_seen_order from different
OS threads, so the lock below is still what closes it.
```

- [ ] **Step 8: Run every affected test file**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/scoring-pool-isolation-plan && python -m pytest tests/test_whalewatchers_kalshi_trade_tape.py tests/test_whalewatchers_candidate_retry_pool.py tests/test_whalewatchers_scoring_pool.py tests/test_kalshi_trade_tape_seen_lock_race.py tests/test_kalshi_trade_tape_resolve_failed_tickers_concurrency.py tests/test_candidate_retry.py tests/test_candidate_retry_integration.py tests/test_signal_log.py tests/test_market_history.py tests/test_market_analyst_agent_per_market.py -q"`
Expected: all pass. The last three matter specifically because they exercise `_scoring_pool.cached_read_connection` — they prove the "thread-keyed cache still works from the new pool's thread" claim rather than leaving it as prose.

- [ ] **Step 9: Confirm the app still imports**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/scoring-pool-isolation-plan && python -c 'import main; print(\"ok\")'"`
Expected: `ok`.

- [ ] **Step 10: Commit**

```bash
git add services/whalewatchers/kalshi_trade_tape.py services/whalewatchers/_scoring_pool.py tests/test_whalewatchers_kalshi_trade_tape.py tests/test_kalshi_trade_tape_seen_lock_race.py
git commit -m "fix: route candidate-retry scoring off the shared WS scoring pool (#563)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Self-review (run by the plan's author, already done)

**Spec coverage:** the design doc's recommended Option 1 is Task 1 (the pool) + Task 2 (routing). Its named open detail — "does not decide the exact module name/location" — is decided here (`services/whalewatchers/_candidate_retry_pool.py`, matching the existing module-per-pool convention `_scoring_pool.py` established). Its predicted connection-cache cost ("+≤3 read connections on the new pool's thread") is not a code change, and Task 2 Step 8 covers the three cache-using modules' tests. Its explicit non-goals (issue #565, issue #410, `_scoring_pool`'s own worker count, the WS dispatch semaphore) are carried into Global Constraints as out-of-scope.

**Placeholder scan:** no TBDs; every code step carries real code. The one deliberate instruction-rather-than-literal-code step is Task 2 Step 1's note about `_trade`'s signature — that is a "read this before writing" instruction with a stated fallback, not a placeholder, because the helper is local to a test file whose exact signature the executor must confirm rather than assume.

**Type consistency:** `run(fn: Callable[[], T]) -> T` is defined identically in Task 1 and consumed under that exact name in Task 2. `_candidate_retry_pool` is the module name in both. `score_recovered_trade`'s signature is unchanged throughout.

**Gap found and fixed during this review:** the first draft did not account for `tests/test_whalewatchers_kalshi_trade_tape.py`'s existing `test_score_recovered_trade_runs_on_the_dedicated_scoring_pool_and_scores_the_trade`, which asserts the *old* pool receives the call and would fail after Task 2 — it is now explicitly replaced in Task 2 Step 1 rather than discovered as a surprise failure at Step 5.
