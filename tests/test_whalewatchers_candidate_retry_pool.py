"""Issue #563 / docs/superpowers/specs/2026-09-03-scoring-pool-candidate-
retry-isolation-design.md: candidate_retry's scoring work gets its own
1-worker pool so it no longer shares _scoring_pool.py's 4 workers with the
WS-trade-scoring path. Mirrors tests/test_whalewatchers_scoring_pool.py's
own shape for the sibling pool it was modeled on."""
import asyncio
import threading
import time

import pytest

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
