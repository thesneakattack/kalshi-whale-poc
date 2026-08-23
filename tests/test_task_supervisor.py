"""services/task_supervisor.py — a background asyncio task that raises must
not just silently end. See the module's own docstring for the three
diagnosed incidents (6973974, a31ae51, 12323cc) this exists to catch.
"""
import asyncio

import pytest

from services import fault_log as fl
from services import task_supervisor
from services.alerting import alerting


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(fl, "DB_PATH", tmp_path / "fault_log.db")
    # 2026-08-23: a restart=True crash now also calls alerting.record_alert
    # (see supervise's own except block) - without this redirect, a crash
    # simulated below would write into the real, live data/alert_log.db.
    monkeypatch.setattr(alerting, "DB_PATH", tmp_path / "alert_log.db")
    yield


def test_one_shot_task_records_fault_and_ends_without_reraising():
    async def _boom():
        raise ValueError("kaboom")

    async def run():
        task = task_supervisor.supervise(_boom, component="test_component", operation="one_shot")
        await task  # should not raise - the exception is caught inside supervise
        return task

    task = asyncio.run(run())
    assert task.done() and not task.cancelled()
    rows = fl.recent(component="test_component")
    assert len(rows) == 1
    assert rows[0]["exc_type"] == "ValueError"
    assert "kaboom" in rows[0]["message"]


def test_one_shot_task_that_succeeds_records_nothing():
    async def _ok():
        return None

    async def run():
        task = task_supervisor.supervise(_ok, component="test_component", operation="one_shot_ok")
        await task

    asyncio.run(run())
    assert fl.recent(component="test_component") == []


def test_restarting_task_retries_after_failures_and_keeps_running():
    calls = {"n": 0}

    async def _flaky():
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError(f"attempt {calls['n']} failed")
        await asyncio.sleep(3600)  # simulate a long-running loop once it's "up"

    async def run():
        task = task_supervisor.supervise(
            _flaky, component="test_component", operation="restarting",
            restart=True, restart_delay_sec=0,
        )
        for _ in range(200):
            if calls["n"] >= 3:
                break
            await asyncio.sleep(0.01)
        assert not task.done()  # still alive, now parked in the long sleep
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert calls["n"] == 3
    rows = fl.recent(component="test_component")
    # fault_log dedupes on (component, operation, exc_type, message) - each
    # attempt's message is distinct ("attempt 1 failed" vs "attempt 2
    # failed"), so both failures get their own row, each seen once.
    assert len(rows) == 2
    assert all(r["exc_type"] == "RuntimeError" and r["count"] == 1 for r in rows)


def test_restart_true_crash_also_raises_an_alert():
    # trading_loop/trade_stream/index_stream are the loops that must never
    # just stay dead - a crash there is exactly the "crash" case
    # ROADMAP.md's alerting item names directly, so it gets both fault_log
    # (the detailed record) and alerting (the "someone should know now"
    # signal), unlike a one-shot task's own failure below.
    async def _boom():
        raise ValueError("critical loop died")

    async def run():
        task = task_supervisor.supervise(
            _boom, component="test_component", operation="critical_loop",
            restart=True, restart_delay_sec=3600,
        )
        for _ in range(200):
            if alerting.active_alerts():
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    active = alerting.active_alerts()
    assert len(active) == 1
    assert active[0]["category"] == "crash"
    assert "test_component.critical_loop crashed" in active[0]["message"]
    assert "critical loop died" in active[0]["message"]


def test_one_shot_crash_does_not_raise_an_alert():
    async def _boom():
        raise ValueError("one-off background task failed")

    async def run():
        task = task_supervisor.supervise(_boom, component="test_component", operation="one_shot")
        await task  # restart=False - caught, logged to fault_log, task ends

    asyncio.run(run())
    assert alerting.active_alerts() == []


def test_cancellation_propagates_without_recording_a_fault():
    async def _sleep_forever():
        await asyncio.sleep(3600)

    async def run():
        task = task_supervisor.supervise(_sleep_forever, component="test_component", operation="cancel_me")
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert fl.recent(component="test_component") == []
