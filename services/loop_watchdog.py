"""Detects event-loop stalls by comparing how late a periodic wakeup runs
against how late it was scheduled to run - the same shape as the realtime
data-plane investigation's correlated-but-unproven "the tick's synchronous
SQLite blocks the WS consumer" hypothesis, now falsifiable at runtime
(root-cause report C1)."""
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


async def start_forever(*, sample_interval_sec: float = 0.1) -> None:
    """Awaits the task start() returns - task_supervisor.supervise needs a
    zero-arg coroutine function, not a bare Task, so this is the wrapper it
    calls."""
    await start(sample_interval_sec=sample_interval_sec)
