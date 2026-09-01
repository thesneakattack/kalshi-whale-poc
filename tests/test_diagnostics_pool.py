import asyncio
import threading
import time

from services.diagnostics import _diagnostics_pool


def test_run_executes_on_a_dedicated_diagnostics_thread():
    result_thread_name = {}

    def _work():
        result_thread_name["name"] = threading.current_thread().name

    asyncio.run(_diagnostics_pool.run(_work))
    assert result_thread_name["name"].startswith("diagnostics")


def test_two_concurrent_calls_do_not_serialize_on_one_worker():
    def _slow():
        time.sleep(0.2)
        return time.monotonic()

    async def _both():
        return await asyncio.gather(_diagnostics_pool.run(_slow), _diagnostics_pool.run(_slow))

    start = time.monotonic()
    asyncio.run(_both())
    elapsed = time.monotonic() - start
    assert elapsed < 0.35  # both ran concurrently on the pool's 2 workers, not queued behind each other
