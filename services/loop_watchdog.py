"""Detects event-loop stalls by comparing how late a periodic wakeup runs
against how late it was scheduled to run - the same shape as the realtime
data-plane investigation's correlated-but-unproven "the tick's synchronous
SQLite blocks the WS consumer" hypothesis, now falsifiable at runtime
(root-cause report C1)."""
import asyncio
import sys
import threading
import time
import traceback

from services import fault_log

_STALL_THRESHOLD_SEC = 0.05  # below this, scheduling jitter, not a stall
_stall_max_ms = 0.0
_stall_count = 0
_samples = 0


def _capture_stall_traceback() -> str:
    """Cheap (microseconds - §4.3 of docs/superpowers/research/2026-09-02-
    architecture-audit-second-pass.md), runs only on the stall path, never
    on the hot 0.1s sample tick. sys._current_frames() is a snapshot of
    every live thread's current frame, safe to call from any thread; this
    app runs its event loop on the process's main thread, so
    threading.main_thread().ident is the right key."""
    frame = sys._current_frames().get(threading.main_thread().ident)
    if frame is None:
        return "<main thread frame unavailable>"
    return "".join(traceback.format_stack(frame))


_pending_fault_writes: set[asyncio.Task] = set()


def _record_stall_fault_background(tb: str) -> None:
    """Fire-and-forget the fault_log write so a slow SQLite write cannot
    delay _tick()'s own next asyncio.sleep() and inflate stall_count with
    a self-inflicted phantom stall (adversarial review of this plan,
    Finding F13) - but still retain a reference to the created Task
    (matching Task 8a's own 'retain a reference so it isn't GC'd, but
    don't block on it' idiom, applied here rather than imported from it
    since Task 8a hasn't landed yet when this task runs)."""
    task = asyncio.create_task(asyncio.to_thread(
        fault_log.record_fault, "loop_watchdog", "stall",
        "event loop stall detected (see first_traceback for the "
        "captured stack)", severity="warn", tb=tb,
    ))
    _pending_fault_writes.add(task)
    task.add_done_callback(_pending_fault_writes.discard)


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
                # Stack capture (2026-09-03, Task 1 of docs/superpowers/
                # plans/2026-09-03-tier1-backend-hygiene.md): the 2026-09-02
                # incident had zero visibility into *what* was blocking the
                # loop. Capture is synchronous and cheap (sys._current_
                # frames/format_stack, no I/O); the fault_log WRITE is
                # fire-and-forget via _record_stall_fault_background() so
                # this diagnostic can never itself become a blocking-sqlite-
                # on-the-loop bug NOR delay this same _tick() coroutine's own
                # next asyncio.sleep(), which an inline `await
                # asyncio.to_thread(...)` would (adversarial review Finding
                # F13: a slow write delaying the watchdog's own next sample
                # could inflate stall_count with a self-inflicted phantom
                # stall - exactly the metric this task exists to make more
                # trustworthy).
                # Message is a fixed string (not late-value-dependent) so
                # fault_log's own dedup key (component, operation, exc_type,
                # message) collapses every stall into ONE row that
                # accumulates count - same tradeoff every other fault_log
                # row already makes (first_traceback is the FIRST capture,
                # not necessarily the most recent - a known limitation,
                # not silently glossed over: see this task's own Global
                # Constraints-adjacent note in the plan).
                tb = _capture_stall_traceback()
                _record_stall_fault_background(tb)
            expected = now + sample_interval_sec

    return asyncio.ensure_future(_tick())


async def start_forever(*, sample_interval_sec: float = 0.1) -> None:
    """Awaits the task start() returns - task_supervisor.supervise needs a
    zero-arg coroutine function, not a bare Task, so this is the wrapper it
    calls."""
    await start(sample_interval_sec=sample_interval_sec)
