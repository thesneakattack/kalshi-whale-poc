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


def test_stall_captures_a_stack_and_records_it_off_the_loop(monkeypatch):
    """The 2026-09-02 incident had zero attribution for what was blocking
    the loop - services/loop_watchdog.py's own docstring already tracks
    magnitude/count but nothing about *what*. This is the fix (§4.3 of
    docs/superpowers/research/2026-09-02-architecture-audit-second-pass.md):
    capture the main thread's stack on the stall path itself and record it
    via fault_log's existing traceback slot, off the event loop so the
    diagnostic write can never become a new instance of the #210 blocking-
    sqlite-on-the-loop bug class this app has already fixed once elsewhere."""
    calls = []

    def _fake_record_fault(*args, **kwargs):
        try:
            asyncio.get_running_loop()
            on_loop = True
        except RuntimeError:
            on_loop = False
        calls.append((args, kwargs, on_loop))
        return True

    monkeypatch.setattr(loop_watchdog.fault_log, "record_fault", _fake_record_fault)

    async def run():
        task = loop_watchdog.start(sample_interval_sec=0.01)
        await asyncio.sleep(0.05)
        time.sleep(0.2)  # blocks the loop - forces a real stall, same as the existing test above
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    loop_watchdog.reset_window()
    asyncio.run(run())

    assert calls, "expected at least one fault_log.record_fault call for the forced stall"
    args, kwargs, on_loop = calls[0]
    assert args[0] == "loop_watchdog"
    assert args[1] == "stall"
    assert not on_loop, "the fault_log write must run off the event loop (asyncio.to_thread)"
    assert kwargs.get("severity") == "warn"
    assert kwargs.get("tb"), "expected a non-empty captured stack string"
