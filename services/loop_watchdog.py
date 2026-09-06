"""Detects event-loop stalls by comparing how late a periodic wakeup runs
against how late it was scheduled to run - the same shape as the realtime
data-plane investigation's correlated-but-unproven "the tick's synchronous
SQLite blocks the WS consumer" hypothesis, now falsifiable at runtime
(root-cause report C1)."""
import asyncio
import faulthandler
import os
import tempfile
import threading
import time

from services import fault_log

_STALL_THRESHOLD_SEC = 0.05  # below this, scheduling jitter, not a stall

# Issue #605: comfortably above ordinary scheduling jitter (a single missed
# 0.1s-cadence tick alone can't reach this - it would need several
# consecutive ticks to go missing), comfortably below the observed 9.3-9.6s
# stall bursts, so it reliably fires WHILE still blocked rather than after -
# re-armed every healthy tick (see start()) so it only ever actually elapses
# during a genuine multi-tick stall, never during normal operation.
_DEFAULT_CAPTURE_ARM_SEC = 0.5
_stall_max_ms = 0.0
_stall_count = 0
_samples = 0


def _extract_main_thread_stack(dump_text: str) -> str | None:
    """faulthandler.dump_traceback_later's output (verified directly
    against the running Python 3.13's real output before writing this, not
    assumed) interleaves every live thread's stack, most-recent-call-first,
    each block headed 'Thread 0x<16-hex-digit ident> (most recent call
    first):' and separated by a blank line - a leading 'Timeout (...)!'
    line precedes the first block. This app runs its event loop on the
    process's main thread (loop_watchdog's own long-standing assumption),
    so the main thread's block is the one actually blocking the loop -
    every other thread's block is a normal background worker, not the
    stall. threading.main_thread().ident formatted as 16 lowercase hex
    digits (:016x) is required to match faulthandler's own zero-padded
    format - Python's bare hex() does not zero-pad and will not match."""
    marker = f"Thread 0x{threading.main_thread().ident:016x}"
    idx = dump_text.find(marker)
    if idx == -1:
        return None
    end = dump_text.find("\n\n", idx)
    block = dump_text[idx:] if end == -1 else dump_text[idx:end]
    return block.strip() or None


_pending_fault_writes: set[asyncio.Task] = set()


def _record_stall_fault_background(tb: str | None) -> None:
    """Fire-and-forget the fault_log write so a slow SQLite write cannot
    delay _tick()'s own next asyncio.sleep() and inflate stall_count with
    a self-inflicted phantom stall (adversarial review of this plan,
    Finding F13) - but still retain a reference to the created Task
    (matching Task 8a's own 'retain a reference so it isn't GC'd, but
    don't block on it' idiom, applied here rather than imported from it
    since Task 8a hasn't landed yet when this task runs).

    tb is None (issue #605) when this stall ended before the capture-arm
    timer fired - genuinely nothing to report, not an error. fault_log's
    own COALESCE-based ON CONFLICT (see its _write() docstring) keeps
    whatever last_traceback a previous, longer stall already captured
    rather than blanking it out."""
    task = asyncio.create_task(asyncio.to_thread(
        fault_log.record_fault, "loop_watchdog", "stall",
        "event loop stall detected (see last_traceback for the most "
        "recent captured stack, or first_traceback for the very first)",
        severity="warn", tb=tb,
    ))
    _pending_fault_writes.add(task)
    task.add_done_callback(_pending_fault_writes.discard)


def reset_window() -> None:
    global _stall_max_ms, _stall_count, _samples
    _stall_max_ms, _stall_count, _samples = 0.0, 0, 0


def snapshot() -> dict:
    return {"stall_max_ms": round(_stall_max_ms, 3), "stall_count": _stall_count, "samples": _samples}


def start(*, sample_interval_sec: float = 0.1,
          capture_arm_sec: float = _DEFAULT_CAPTURE_ARM_SEC) -> asyncio.Task:
    """Issue #605: the previous capture mechanism (sys._current_frames,
    called from _tick() itself after asyncio.sleep() returns) could never
    see the actual blocking frame - by the time _tick() resumes, the block
    has already ended and control has already returned to the loop, so it
    could only ever show _tick()'s own resumption frame. faulthandler.
    dump_traceback_later runs on a genuine separate OS thread that fires
    independently of whether the main thread (this app's event loop) is
    currently blocked - re-armed every healthy tick (below) so it only
    ever actually elapses during a real multi-tick stall, at which point it
    dumps every thread's CURRENT frame - including the main thread's, still
    inside whatever synchronous call is blocking the loop - to dump_file,
    read back and reset by the stall-detection branch below.

    One dump_file per start() call (not a module global): each independent
    caller (production's one long-lived call, or each test's own short-
    lived one) gets its own isolated temp file, closed on cancellation.

    Cost, measured (not assumed) at production's 0.1s cadence, per the
    data-plane HARD RULE's "any diagnostic on the hot path is measured for
    runtime cost before it ships": 20,000 cancel+rearm cycles averaged
    ~99us each - about 0.1% of one 0.1s tick period, run once per healthy
    tick alongside work this loop already does every cycle.

    Reads dump_file via raw os.pread/os.ftruncate on its file descriptor,
    never through the buffered TextIOWrapper's own .seek()/.read()/
    .truncate() - independent adversarial review of this fix found that
    approach can SEGFAULT THE WHOLE PROCESS under contention (reproduced
    directly, both locally and in the actual production container):
    faulthandler's internal watchdog thread writes to the fd directly (it
    must be signal-safe, so it bypasses Python's buffering entirely), and
    unsynchronized concurrent access to the *buffered* object's internal
    state from a second thread corrupts it - a torn read was the assumed
    worst case, a crash is the actual one. pread()/ftruncate() operate
    directly on the fd with no buffered Python-level state to corrupt.
    ftruncate() alone does not reset the fd's own write offset (verified
    directly - a second dump after truncate(0) without an explicit
    lseek(0) writes at the stale offset, producing null-byte-padded
    garbage ahead of the real content), so lseek is required alongside it,
    not implied by it."""
    dump_file = tempfile.TemporaryFile(mode="w+")
    _dump_fd = dump_file.fileno()
    _MAX_DUMP_READ_BYTES = 1 << 20  # generous: this app's live thread count is nowhere near enough to fill 1MB

    def _rearm() -> None:
        faulthandler.cancel_dump_traceback_later()
        faulthandler.dump_traceback_later(capture_arm_sec, file=dump_file)

    def _read_and_reset_capture() -> str | None:
        raw = os.pread(_dump_fd, _MAX_DUMP_READ_BYTES, 0)
        if not raw:
            return None
        os.ftruncate(_dump_fd, 0)
        os.lseek(_dump_fd, 0, os.SEEK_SET)
        return _extract_main_thread_stack(raw.decode(errors="replace"))

    async def _tick() -> None:
        global _stall_max_ms, _stall_count, _samples
        expected = time.monotonic() + sample_interval_sec
        _rearm()
        try:
            while True:
                await asyncio.sleep(sample_interval_sec)
                now = time.monotonic()
                late = now - expected
                _samples += 1
                if late > _STALL_THRESHOLD_SEC:
                    _stall_count += 1
                    _stall_max_ms = max(_stall_max_ms, late * 1000)
                    # Message is a fixed string (not late-value-dependent)
                    # so fault_log's own dedup key (component, operation,
                    # exc_type, message) collapses every stall into ONE row
                    # that accumulates count - same tradeoff every other
                    # fault_log row already makes. Unlike first_traceback
                    # (frozen at the very first capture forever, by design -
                    # see fault_log._write's docstring), last_traceback
                    # updates on every call, so this diagnostic gets more
                    # useful the more it fires, not stuck on whatever
                    # happened to be captured first.
                    tb = _read_and_reset_capture()
                    _record_stall_fault_background(tb)
                _rearm()
                expected = now + sample_interval_sec
        finally:
            faulthandler.cancel_dump_traceback_later()
            dump_file.close()

    return asyncio.ensure_future(_tick())


async def start_forever(*, sample_interval_sec: float = 0.1,
                         capture_arm_sec: float = _DEFAULT_CAPTURE_ARM_SEC) -> None:
    """Awaits the task start() returns - task_supervisor.supervise needs a
    zero-arg coroutine function, not a bare Task, so this is the wrapper it
    calls."""
    await start(sample_interval_sec=sample_interval_sec, capture_arm_sec=capture_arm_sec)
