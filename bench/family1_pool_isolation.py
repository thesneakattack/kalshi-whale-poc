"""Issue #150 fix-family benchmark - Family 1 (dedicated smaller
ThreadPoolExecutor for the trade-scoring pipeline).

LOAD-BEARING DISCOVERY (verified via `git log --diff-filter=A --follow`,
not assumed from issue #150's 2026-08-28-era description): Family 1 has
ALREADY BEEN SHIPPED, independently of issue #150 ever being actioned:

  - services/whalewatchers/_scoring_pool.py (added 2026-09-01, "feat: add
    dedicated worker pool + connection cache for whale-scoring reads",
    commit 3ff41d4) gives the WS-trade path (fetch_signals -> _scoring_pool
    .run() -> loop.run_in_executor(_executor, fn)) its OWN
    ThreadPoolExecutor(max_workers=4) - explicitly NOT the shared default
    asyncio.to_thread pool (that module's own docstring: "Deliberately its
    own pool ... and not Python's default asyncio.to_thread executor
    (shared process-wide with unrelated work, unbounded up to 20 threads on
    this container)").
  - services/whalewatchers/_candidate_retry_pool.py (added 2026-09-04,
    "feat: dedicated 1-worker pool for candidate-retry scoring (#563)",
    commit c6f3295; score_recovered_trade wired onto it two minutes later
    the same day by commit 9b55c1b, "fix: route candidate-retry scoring
    off the shared WS scoring pool (#563)") gives candidate_retry.run_pending's
    score_recovered_trade call a SEPARATE ThreadPoolExecutor(max_workers=1).

Both predate this investigation and both postdate issue #150's filing
(2026-08-28). issue #150's own body still describes the pre-fix shape
("await asyncio.to_thread(self._process_trades_timed, ...)") - that
description, and services/kalshi/websocket.py's own code comments quoting
it verbatim, are now stale relative to the actual call graph
(fetch_signals -> _scoring_pool.run(), confirmed by direct source read of
services/whalewatchers/kalshi_trade_tape.py's fetch_signals()).

This script does not "decide whether to build" Family 1 - it already
exists. What it verifies, with real asyncio/concurrent.futures primitives
(never a simulation dissimilar from the real code), is:

  1. The isolation claim the shipped design rests on: does a hung task
     that permanently occupies one worker slot (issue #150's own
     confirmed, unfixed mechanism - asyncio.wait_for cancelling the
     wrapper task does NOT stop the underlying OS thread, per CPython's
     own Future.cancel() docs) actually fail to starve UNRELATED
     to_thread work when isolated onto its own small pool, the way it
     would on the shared default pool?
  2. The remaining, NOT eliminated risk this design accepts by its own
     docstring ("if #145/#150 keeps happening, all 4 workers eventually
     get stuck ... bounded and observable ... not eliminating #145/#150
     itself"): how many consecutive real 10s timeouts (issue #150's
     measured live rate: 30 in one observed window) does it take to
     exhaust a 4-worker dedicated pool, vs a 20-worker shared default one?
  3. The overhead a small dedicated pool costs against the shared default
     at realistic message volume - thread creation and per-submission
     dispatch overhead, not a "should help" guess.

Every number here comes from real threading.Thread /
concurrent.futures.ThreadPoolExecutor / asyncio.wait_for objects on this
machine - never a hand-rolled timing simulation.
"""
import asyncio
import json
import os
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

OUT_DIR = os.path.join(os.path.dirname(__file__), "out")

# Real, measured container sizing from issue #150's own comment thread
# (2026-08-30 measurement, quoted verbatim): os.cpu_count() = 16 ->
# min(32, 16 + 4) = 20. Not re-measured here (this benchmark runs on the
# investigating machine, not the live container) - used as the documented
# real value, labeled as such rather than re-derived from this machine's
# own (different) os.cpu_count().
_LIVE_CONTAINER_SHARED_POOL_SIZE = 20
_SHIPPED_DEDICATED_POOL_SIZE = 4  # services/whalewatchers/_scoring_pool.py's actual max_workers
_HANDLER_TIMEOUT_SEC_REAL = 10.0  # services/kalshi/websocket.py's _HANDLER_TIMEOUT_SEC


def _hang_forever_task(started_flag: threading.Event, stop_flag: threading.Event) -> str:
    """Models issue #150's confirmed leak: a worker thread submitted via
    to_thread/run_in_executor that keeps running after its asyncio-side
    awaiter has been cancelled by wait_for's timeout. Real OS thread, real
    blocking wait (not a busy-loop) - stop_flag lets the test end
    deterministically instead of leaking a real thread past the test."""
    started_flag.set()
    stop_flag.wait(30.0)
    return "hung task finished (test teardown, not a real completion)"


async def _bounded_call(loop, executor, fn, timeout: float):
    """Exactly services/kalshi/websocket.py's _process_item shape:
    asyncio.wait_for(...) around a run_in_executor future. Confirms
    real behavior, not assumed: after the TimeoutError, the executor's
    thread is still running _hang_forever_task underneath (issue #150's
    documented, unfixed mechanism) - this coroutine returning does NOT
    free the worker slot."""
    fut = loop.run_in_executor(executor, fn)
    try:
        return await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        return "TIMEOUT"


# --- Section 1: starvation of UNRELATED to_thread work on a saturated ------
# shared pool, vs isolation on a dedicated pool.

async def section1_starvation(pool_size: int, n_stuck: int, handler_timeout: float) -> dict:
    executor = ThreadPoolExecutor(max_workers=pool_size, thread_name_prefix="bench-shared")
    loop = asyncio.get_running_loop()
    stop_flags = [threading.Event() for _ in range(n_stuck)]
    started_flags = [threading.Event() for _ in range(n_stuck)]

    # Saturate the pool with n_stuck permanently-hung tasks, each wrapped
    # in the real _process_item wait_for(handler_timeout) shape - each one
    # times out at the event-loop layer but leaves its OS thread running,
    # occupying the slot (issue #150's confirmed mechanism).
    stuck_coros = [
        _bounded_call(loop, executor, lambda i=i: _hang_forever_task(started_flags[i], stop_flags[i]), handler_timeout)
        for i in range(n_stuck)
    ]
    stuck_results = await asyncio.gather(*stuck_coros)
    assert all(r == "TIMEOUT" for r in stuck_results)
    for f in started_flags:
        assert f.is_set()  # every stuck task genuinely started running on a real thread

    # Now submit ONE unrelated, fast to_thread task - the pool's real
    # remaining capacity is pool_size - n_stuck live (but leaked) threads.
    quick_started = time.monotonic()
    quick_result = await loop.run_in_executor(executor, lambda: "quick task done")
    quick_elapsed = time.monotonic() - quick_started

    for f in stop_flags:
        f.set()
    executor.shutdown(wait=True, cancel_futures=False)

    return {
        "pool_size": pool_size,
        "n_stuck_tasks": n_stuck,
        "handler_timeout_sec": handler_timeout,
        "quick_task_result": quick_result,
        "quick_task_wait_sec": round(quick_elapsed, 4),
        "pool_fully_saturated": n_stuck >= pool_size,
    }


async def run_section1() -> dict:
    results = {}

    # 1a. Shared-default-shaped pool (20 workers, matching the real
    # container measurement), with a MODEST number of stuck tasks (5) that
    # would not fully saturate it - the quick task should get a free
    # worker immediately, no starvation.
    results["shared_pool_partial_saturation_5_of_20"] = await section1_starvation(
        pool_size=_LIVE_CONTAINER_SHARED_POOL_SIZE, n_stuck=5, handler_timeout=1.0)

    # 1b. Shared-default-shaped pool, FULLY saturated (20 stuck tasks on a
    # 20-worker pool) - every slot leaked, so an unrelated quick task must
    # wait for the pool to actually grow a new thread or a slot to free -
    # ThreadPoolExecutor does not grow past max_workers, and none of the 20
    # slots will ever free within this process's lifetime (issue #150's
    # literal "permanently" claim, before its own bound-on-the-leak
    # correction - see below). Bounded to a short test window: this proves
    # the STARVATION exists (the quick task cannot get a worker at all
    # within a generous real wait), not that it never completes.
    executor = ThreadPoolExecutor(max_workers=_LIVE_CONTAINER_SHARED_POOL_SIZE, thread_name_prefix="bench-full")
    loop = asyncio.get_running_loop()
    stop_flags = [threading.Event() for _ in range(_LIVE_CONTAINER_SHARED_POOL_SIZE)]
    started_flags = [threading.Event() for _ in range(_LIVE_CONTAINER_SHARED_POOL_SIZE)]
    stuck_coros = [
        _bounded_call(loop, executor, lambda i=i: _hang_forever_task(started_flags[i], stop_flags[i]), 1.0)
        for i in range(_LIVE_CONTAINER_SHARED_POOL_SIZE)
    ]
    await asyncio.gather(*stuck_coros)
    quick_fut = loop.run_in_executor(executor, lambda: "quick task done")
    try:
        quick_result = await asyncio.wait_for(quick_fut, timeout=3.0)
        starved = False
    except asyncio.TimeoutError:
        quick_result = None
        starved = True
    for f in stop_flags:
        f.set()
    executor.shutdown(wait=True, cancel_futures=False)
    results["shared_pool_fully_saturated_20_of_20"] = {
        "pool_size": _LIVE_CONTAINER_SHARED_POOL_SIZE,
        "n_stuck_tasks": _LIVE_CONTAINER_SHARED_POOL_SIZE,
        "unrelated_quick_task_starved_after_3s_wait": starved,
        "quick_task_result": quick_result,
    }

    # 1c. The shipped design: dedicated 4-worker pool for the trade-scoring
    # pipeline, saturated with 4 stuck tasks (matching
    # _TRADE_DISPATCH_CONCURRENCY's own real ceiling on how many trades can
    # be in flight at once) - and a SEPARATE pool (standing in for the
    # shared default, or any other app subsystem's own to_thread call)
    # proves totally unaffected, because it is a physically different
    # ThreadPoolExecutor object.
    dedicated = ThreadPoolExecutor(max_workers=_SHIPPED_DEDICATED_POOL_SIZE, thread_name_prefix="bench-dedicated")
    unrelated_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="bench-unrelated")
    stop_flags2 = [threading.Event() for _ in range(_SHIPPED_DEDICATED_POOL_SIZE)]
    started_flags2 = [threading.Event() for _ in range(_SHIPPED_DEDICATED_POOL_SIZE)]
    stuck_coros2 = [
        _bounded_call(loop, dedicated, lambda i=i: _hang_forever_task(started_flags2[i], stop_flags2[i]), 1.0)
        for i in range(_SHIPPED_DEDICATED_POOL_SIZE)
    ]
    await asyncio.gather(*stuck_coros2)
    quick_started = time.monotonic()
    quick_result = await loop.run_in_executor(unrelated_pool, lambda: "quick task done, on a DIFFERENT pool")
    quick_elapsed = time.monotonic() - quick_started
    for f in stop_flags2:
        f.set()
    dedicated.shutdown(wait=True, cancel_futures=False)
    unrelated_pool.shutdown(wait=True, cancel_futures=False)
    results["dedicated_pool_fully_saturated_unrelated_work_on_separate_pool"] = {
        "dedicated_pool_size": _SHIPPED_DEDICATED_POOL_SIZE,
        "n_stuck_tasks_on_dedicated_pool": _SHIPPED_DEDICATED_POOL_SIZE,
        "unrelated_pool_size": 4,
        "quick_task_result": quick_result,
        "quick_task_wait_sec": round(quick_elapsed, 4),
        "starved": False,
    }
    return results


# --- Section 2: how many real, live-measured timeouts (30, issue #150's ---
# own 2026-08-30 observation) would it take to fully exhaust each pool
# size - pure arithmetic on the real numbers, not a new assumption.

def section2_exhaustion_budget() -> dict:
    observed_timeouts_in_window = 30  # issue #150 comment, /api/health/pipeline, 2026-08-30
    return {
        "observed_handler_timeouts_in_one_window": observed_timeouts_in_window,
        "shared_default_pool_size": _LIVE_CONTAINER_SHARED_POOL_SIZE,
        "dedicated_pool_size": _SHIPPED_DEDICATED_POOL_SIZE,
        "shared_pool_fully_exhausted_by_observed_window": (
            observed_timeouts_in_window >= _LIVE_CONTAINER_SHARED_POOL_SIZE),
        "dedicated_pool_fully_exhausted_by_observed_window": (
            observed_timeouts_in_window >= _SHIPPED_DEDICATED_POOL_SIZE),
        "note": (
            "issue #150's own follow-up comment (2026-08-30) already falsified "
            "the 'permanent leak' framing empirically on the shared 20-worker "
            "pool: 30 observed timeouts > 20 workers, yet the app kept "
            "processing trades continuously, so timed-out threads DO "
            "eventually complete (most plausibly when whatever they were "
            "blocked on resolves) and release their slot - the real cost is "
            "TRANSIENT occupancy, not a permanent leak, on either pool size. "
            "A dedicated 4-worker pool has a much smaller aggregate 'shock "
            "absorber' before all its slots are transiently occupied at once "
            "(4 concurrent stuck tasks vs 20) - this is the real, non-eliminated "
            "tradeoff the shipped design's own docstring names: 'all 4 workers "
            "eventually get stuck ... a visible backlog/latency symptom' "
            "rather than a silent, unbounded, cross-subsystem starvation."
        ),
    }


# --- Section 3: overhead of a dedicated pool at realistic volume ----------

def _representative_scoring_work() -> None:
    """Stand-in for one _process_trades_sync call's real per-trade cost -
    a handful of small SQLite-shaped operations. Not claiming this is THE
    real duration (that depends on live disk/lock state - see
    family2_busy_timeout_mechanism.py) - this isolates PURE POOL DISPATCH
    overhead (thread hand-off, GIL acquisition, future bookkeeping) by
    using a fixed, tiny, real computation instead of a live DB call, so the
    comparison is about the pool mechanism, not database variance."""
    total = 0
    for i in range(2000):
        total += i * i
    return total


async def section3_overhead(rate_per_sec: float, duration_sec: float, pool_size: int) -> dict:
    executor = ThreadPoolExecutor(max_workers=pool_size, thread_name_prefix=f"bench-overhead-{pool_size}")
    loop = asyncio.get_running_loop()
    n = int(rate_per_sec * duration_sec)
    interval = 1.0 / rate_per_sec
    latencies = []
    start = time.monotonic()
    for i in range(n):
        target = start + i * interval
        now = time.monotonic()
        if target > now:
            await asyncio.sleep(target - now)
        submit_ts = time.monotonic()
        await loop.run_in_executor(executor, _representative_scoring_work)
        latencies.append(time.monotonic() - submit_ts)
    total_elapsed = time.monotonic() - start
    executor.shutdown(wait=True)
    return {
        "pool_size": pool_size,
        "target_rate_per_sec": rate_per_sec,
        "n_submissions": n,
        "total_elapsed_sec": round(total_elapsed, 4),
        "mean_latency_ms": round(statistics.mean(latencies) * 1000, 4),
        "p50_latency_ms": round(statistics.median(latencies) * 1000, 4),
        "p95_latency_ms": round(sorted(latencies)[int(len(latencies) * 0.95)] * 1000, 4) if latencies else None,
        "max_latency_ms": round(max(latencies) * 1000, 4),
    }


def section3_thread_startup_cost(pool_size: int) -> dict:
    """One-time creation cost - thread spin-up, not per-submission. Forces
    every worker thread to actually start (submit a trivial task per
    worker and wait for all to complete) rather than measuring lazy
    executor construction, which allocates no threads until first use."""
    started = time.monotonic()
    executor = ThreadPoolExecutor(max_workers=pool_size, thread_name_prefix=f"bench-startup-{pool_size}")
    barrier = threading.Barrier(pool_size)
    futures = [executor.submit(barrier.wait) for _ in range(pool_size)]
    for f in futures:
        f.result(timeout=10.0)
    elapsed = time.monotonic() - started
    thread_count = threading.active_count()
    executor.shutdown(wait=True)
    return {
        "pool_size": pool_size,
        "all_workers_started_sec": round(elapsed, 5),
        "active_thread_count_while_running": thread_count,
    }


async def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    results = {"generated_at": time.time(), "python_version": sys.version}

    results["section1_starvation"] = await run_section1()
    results["section2_exhaustion_budget"] = section2_exhaustion_budget()

    # Realistic-volume overhead comparison. 27.3/sec is the one concrete,
    # documented real figure in this repo (services/whalewatchers/
    # kalshi_trade_tape.py:64's own comment: "1,640 prints/min across the
    # WATCHED series alone"); exchange-wide is described there as "many
    # times that" but not given
    # an exact figure, so 50/sec and 200/sec below are explicitly LABELED
    # stress-test assumptions, not measured live values.
    results["section3_thread_startup_cost_dedicated_4"] = section3_thread_startup_cost(_SHIPPED_DEDICATED_POOL_SIZE)
    results["section3_thread_startup_cost_shared_20"] = section3_thread_startup_cost(_LIVE_CONTAINER_SHARED_POOL_SIZE)

    results["section3_overhead_documented_rate_27.3_per_sec_dedicated_4"] = await section3_overhead(
        rate_per_sec=27.3, duration_sec=2.0, pool_size=_SHIPPED_DEDICATED_POOL_SIZE)
    results["section3_overhead_documented_rate_27.3_per_sec_shared_20"] = await section3_overhead(
        rate_per_sec=27.3, duration_sec=2.0, pool_size=_LIVE_CONTAINER_SHARED_POOL_SIZE)
    results["section3_overhead_ASSUMED_stress_rate_200_per_sec_dedicated_4"] = await section3_overhead(
        rate_per_sec=200.0, duration_sec=2.0, pool_size=_SHIPPED_DEDICATED_POOL_SIZE)
    results["section3_overhead_ASSUMED_stress_rate_200_per_sec_shared_20"] = await section3_overhead(
        rate_per_sec=200.0, duration_sec=2.0, pool_size=_LIVE_CONTAINER_SHARED_POOL_SIZE)

    out_path = os.path.join(OUT_DIR, "family1_pool_isolation.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(json.dumps(results, indent=2, default=str))
    print(f"\nwrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
