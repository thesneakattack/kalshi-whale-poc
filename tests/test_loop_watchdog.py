import asyncio
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from services import loop_watchdog

_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_extract_main_thread_stack_finds_the_main_threads_block():
    """faulthandler.dump_traceback_later's output interleaves every live
    thread's stack, most-recent-call-first, separated by a blank line, each
    headed 'Thread 0x<16-hex-digit ident> (most recent call first):' - a
    leading 'Timeout (...)!' line precedes the first block. Verified
    directly against real faulthandler output before writing this (not
    assumed): the ident is zero-padded to 16 lowercase hex digits, which
    doesn't match Python's own hex()'s unpadded output for the same int."""
    main_id = threading.main_thread().ident
    dump = (
        "Timeout (0:00:00.300000)!\n"
        f"Thread 0x{0xdeadbeef:016x} (most recent call first):\n"
        '  File "<string>", line 7 in bg\n'
        "\n"
        f"Thread 0x{main_id:016x} (most recent call first):\n"
        '  File "app.py", line 42 in blocked_call\n'
        '  File "main.py", line 11 in <module>\n'
    )
    block = loop_watchdog._extract_main_thread_stack(dump)
    assert "blocked_call" in block
    assert "bg" not in block, "must not include a background thread's frames"


def test_extract_main_thread_stack_returns_none_when_marker_absent():
    assert loop_watchdog._extract_main_thread_stack("") is None
    assert loop_watchdog._extract_main_thread_stack("no thread blocks here") is None


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
    docs/archive/lane-9-tooling-ci-process-governance/research/2026-09-02-architecture-audit-second-pass.md):
    capture the main thread's stack on the stall path itself and record it
    via fault_log's existing traceback slot, off the event loop so the
    diagnostic write can never become a new instance of the #210 blocking-
    sqlite-on-the-loop bug class this app has already fixed once elsewhere.

    Issue #605 found the original capture mechanism (sys._current_frames
    called from inside _tick() itself, after asyncio.sleep() returns) can
    never see the actual blocking frame: by the time _tick() resumes, the
    block has already ended and control has already returned to the loop -
    sys._current_frames() at that point can only ever show _tick()'s own
    resumption frame, not whatever blocked it. This test's assertion below
    (the blocking function's own name appears in the captured text) is the
    one the old mechanism could never pass - it could only ever assert
    non-empty, not correct."""
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

    def _blocking_call_the_capture_must_see():
        time.sleep(0.2)  # blocks the loop - forces a real stall, same as the existing test above

    async def run():
        task = loop_watchdog.start(sample_interval_sec=0.01, capture_arm_sec=0.05)
        await asyncio.sleep(0.05)
        _blocking_call_the_capture_must_see()
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
    tb = kwargs.get("tb")
    assert tb, "expected a non-empty captured stack string"
    assert "_blocking_call_the_capture_must_see" in tb, (
        "the capture must show the ACTUAL blocking frame, not the watchdog's own - "
        f"got: {tb!r}"
    )


def test_read_and_reset_capture_resets_the_file_offset_not_just_its_content(monkeypatch):
    """Deterministic regression test for a specific bug found while fixing
    PR #632's independent-adversarial-review finding: os.ftruncate(fd, 0)
    resets a file's CONTENT but not its WRITE OFFSET (verified directly,
    not assumed, via a standalone repro before writing this fix). Without
    an explicit os.lseek(fd, 0, os.SEEK_SET) alongside it, a later dump
    writes at the stale offset, extending the file with a zero-filled hole
    ahead of the real content rather than producing a clean capture at
    offset 0.

    An earlier version of this test asserted on the RETURNED string (no
    NUL bytes) - that passed even with the bug, because
    _extract_main_thread_stack's marker search starts AT the found "Thread
    0x..." header and naturally discards whatever leading garbage precedes
    it, masking the defect. Checking os.lseek's own return value (the
    resulting offset) at the moment _read_and_reset_capture calls it is the
    test that actually fails without it - querying the fd afterward doesn't
    work, since _tick()'s finally block closes it once the task is
    cancelled and awaited."""
    monkeypatch.setattr(loop_watchdog.fault_log, "record_fault", lambda *a, **kw: True)

    resulting_offsets: list[int] = []
    real_lseek = os.lseek

    def _tracking_lseek(fd, pos, how):
        result = real_lseek(fd, pos, how)
        if pos == 0 and how == os.SEEK_SET:
            resulting_offsets.append(result)
        return result

    monkeypatch.setattr(loop_watchdog.os, "lseek", _tracking_lseek)

    def _blocker():
        time.sleep(0.2)

    async def run():
        task = loop_watchdog.start(sample_interval_sec=0.01, capture_arm_sec=0.05)
        await asyncio.sleep(0.05)
        _blocker()
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    loop_watchdog.reset_window()
    asyncio.run(run())

    assert resulting_offsets, "expected _read_and_reset_capture's lseek(fd, 0, SEEK_SET) to run at least once"
    assert all(offset == 0 for offset in resulting_offsets), (
        f"expected the fd's write offset reset to 0 after every capture, got {resulting_offsets} - "
        "ftruncate(fd, 0) alone does not reset the offset, only the content"
    )


def test_stall_shorter_than_the_capture_arm_records_no_fabricated_traceback(monkeypatch):
    """The old mechanism always produced a (garbage) string, never honestly
    reporting 'no genuine capture available'. A stall the metrics threshold
    (0.05s) flags but that ends before the capture-arm timer (set here well
    above the forced block) ever fires must not fabricate a traceback -
    accuracy over always having *something* to show (CLAUDE.md: 'a value
    means exactly what its label says')."""
    calls = []
    monkeypatch.setattr(loop_watchdog.fault_log, "record_fault",
                         lambda *a, **kw: calls.append((a, kw)) or True)

    async def run():
        task = loop_watchdog.start(sample_interval_sec=0.01, capture_arm_sec=5.0)
        await asyncio.sleep(0.05)
        time.sleep(0.15)  # above the 0.05s metrics threshold, well below the 5.0s capture arm
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    loop_watchdog.reset_window()
    asyncio.run(run())

    assert calls, "expected the metrics threshold to still flag this as a stall"
    _, kwargs = calls[0]
    assert not kwargs.get("tb"), f"expected no fabricated capture below the arm threshold, got: {kwargs.get('tb')!r}"


def test_capture_survives_high_frequency_rearming_without_crashing():
    """Independent adversarial review of this fix (PR #632) found the
    original _read_and_reset_capture (dump_file.seek()/.read()/.truncate()
    through the buffered TextIOWrapper) can SEGFAULT THE WHOLE PROCESS
    under contention, not merely tear a read: faulthandler's internal
    watchdog thread writes to the file's raw fd directly (it must be
    signal-safe, so it bypasses Python's buffered wrapper entirely), while
    _tick() accesses the same buffered object from a different thread -
    unsynchronized concurrent access to that buffered object's internal
    state corrupts it. Reproduced directly by the review in both a local
    environment and the actual production container's Python 3.13.15, not
    assumed.

    A real crash kills whatever process it happens in - it can't be caught
    as a Python exception inside this test process, so this runs the real
    start()/_tick() code path in a SUBPROCESS under far more aggressive
    timing than production (1ms sample/capture-arm intervals vs prod's
    100ms/500ms) for a bounded few seconds, and asserts the subprocess is
    still alive at the end - a segfault shows up as a negative/139
    returncode, not a Python traceback."""
    script = """
import asyncio
import time

from services import loop_watchdog

async def hammer():
    task = loop_watchdog.start(sample_interval_sec=0.001, capture_arm_sec=0.001)
    deadline = time.monotonic() + 2.5
    while time.monotonic() < deadline:
        time.sleep(0.002)  # force frequent short stalls while the capture-arm timer refires at high frequency
        await asyncio.sleep(0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

asyncio.run(hammer())
print("SURVIVED", flush=True)
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=_REPO_ROOT,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"subprocess crashed (returncode={result.returncode} - negative means "
        f"killed by a signal, e.g. -11 is SIGSEGV): stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "SURVIVED" in result.stdout
