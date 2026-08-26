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
