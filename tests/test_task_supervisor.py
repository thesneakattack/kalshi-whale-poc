"""services/task_supervisor.py — a background asyncio task that raises must
not just silently end. See the module's own docstring for the three
diagnosed incidents (6973974, a31ae51, 12323cc) this exists to catch.
"""
import asyncio

import pytest

from services import fault_log as fl
from services import task_supervisor


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(fl, "DB_PATH", tmp_path / "fault_log.db")
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
