import asyncio
from unittest.mock import patch

import main
from services.app_state import state


def test_scheduler_skips_when_disabled():
    state["quality_coordination"] = {"running": False, "last_started_at": 0.0, "task": None}
    cfg = {"quality_coordination": {"enabled": False}}
    with patch("main.task_supervisor") as mock_supervisor:
        main._maybe_run_quality_coordination(cfg)
    mock_supervisor.supervise.assert_not_called()


EPOCH = 1_800_000_000.0  # a realistic time.time() scale — real epoch seconds, not small test
#                          numbers, because last_started_at=0.0 is a sentinel that only reliably
#                          means "always overdue" when compared against a real epoch-scale value
#                          (exactly how the real, already-proven backup.py pattern this mirrors
#                          works in production) — small mock values like 1000.0 would silently
#                          break that property and pass for the wrong reason.


def test_scheduler_fires_a_supervised_background_task_once_then_waits_for_interval():
    state["quality_coordination"] = {"running": False, "last_started_at": 0.0, "task": None}
    cfg = {"quality_coordination": {"enabled": True, "interval_sec": 3600}}
    with patch("main.task_supervisor") as mock_supervisor, \
         patch("main.latest_run_at", return_value=None), \
         patch("main.time") as mock_time:
        mock_time.time.return_value = EPOCH
        main._maybe_run_quality_coordination(cfg)  # first check: due (never run, no history)
        mock_time.time.return_value = EPOCH + 5
        main._maybe_run_quality_coordination(cfg)  # 5s later, well under the 3600s interval
    assert mock_supervisor.supervise.call_count == 1
    assert mock_supervisor.supervise.call_args.kwargs["component"] == "quality_coordination"


def test_scheduler_skips_while_a_run_is_already_in_flight():
    """Same 'running' guard as backup's state["backup"]["running"] — a slow run must not
    overlap with the next interval tick firing a second one."""
    state["quality_coordination"] = {"running": True, "last_started_at": EPOCH, "task": None}
    cfg = {"quality_coordination": {"enabled": True, "interval_sec": 1}}
    with patch("main.task_supervisor") as mock_supervisor, \
         patch("main.time") as mock_time:
        mock_time.time.return_value = EPOCH + 3600  # well past due, but a run is in flight
        main._maybe_run_quality_coordination(cfg)
    mock_supervisor.supervise.assert_not_called()


def test_scheduler_seeds_last_started_at_from_persisted_history_on_cold_start():
    """The cold-start-reload fix: if in-memory state is unseeded (0.0) but a prior run is
    already recorded on disk, the scheduler must not treat that as newly overdue."""
    state["quality_coordination"] = {"running": False, "last_started_at": 0.0, "task": None}
    cfg = {"quality_coordination": {"enabled": True, "interval_sec": 3600}}
    with patch("main.task_supervisor") as mock_supervisor, \
         patch("main.latest_run_at", return_value=EPOCH), \
         patch("main.time") as mock_time:
        mock_time.time.return_value = EPOCH + 5  # 5s after the persisted last run, not due
        main._maybe_run_quality_coordination(cfg)
    mock_supervisor.supervise.assert_not_called()
    assert state["quality_coordination"]["last_started_at"] == EPOCH


def test_background_wrapper_runs_the_blocking_work_via_to_thread():
    """The actual off-event-loop guarantee — assert the wrapper awaits asyncio.to_thread
    around the blocking fetch/audit call, not a bare synchronous call.

    Two adaptations from the brief's literal test, both mechanical, neither changing what's
    asserted:
    1. This repo has no pytest-asyncio (or any async test plugin) installed, and no other
       test in this suite declares `async def test_...` directly - the established
       convention (see tests/test_backup.py's _call_maybe_run_backup) is a plain sync test
       that drives the coroutine itself via asyncio.run(). Same convention here.
    2. `patch("main.asyncio.to_thread")` auto-detects as an AsyncMock (the real
       asyncio.to_thread is itself `async def`) - unittest.mock's documented behavior since
       Python 3.8. Assigning a manually-constructed coroutine to its `return_value` (the
       brief's literal code) double-wraps it: awaiting the mock's own call returns that
       coroutine object *unawaited*, which _run_quality_coordination_background then
       discards (it doesn't use to_thread's result), leaking it - confirmed via
       `-W error::RuntimeWarning`, which reproduced "coroutine ... was never awaited"
       even in isolation. Left unset, AsyncMock's default return_value is a plain
       MagicMock, not a coroutine, so no such leak - assert_called_once() still proves
       the same thing (to_thread was actually invoked, not bypassed)."""
    from main import _run_quality_coordination_background

    state["quality_coordination"] = {"running": True, "last_started_at": 0.0, "task": None}
    with patch("main.asyncio.to_thread") as mock_to_thread:
        asyncio.run(_run_quality_coordination_background())
    mock_to_thread.assert_called_once()
    assert state["quality_coordination"]["running"] is False
