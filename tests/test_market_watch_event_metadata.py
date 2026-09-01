"""Tests for services/market_watch/event_metadata.py's game_state.record()
call site - event-loop-blocking elimination Fix 1, 2026-09-01."""
import asyncio

import services.market_watch.event_metadata as em
from services import game_state, tick_executor
from services.app_state import state


def test_fetch_event_live_data_schedules_flush_via_tick_executor_when_told(monkeypatch):
    # Isolate the shared, disk-backed state singleton for this one event
    # ticker - state["event_live_data_cache"] is a real module-level dict
    # that persists across calls within the process, and state["event_titles"]
    # is loaded from disk at import time, so a fabricated ticker gets its own
    # clean slate rather than risking stale cache hits from another test.
    monkeypatch.setitem(state, "event_live_data_cache", {})
    monkeypatch.setitem(state, "event_titles", {})

    scheduled = []
    real_create_task = asyncio.create_task

    def spy_create_task(coro):
        scheduled.append(coro)
        return real_create_task(coro)

    monkeypatch.setattr(asyncio, "create_task", spy_create_task)

    tick_executor_calls = []

    async def fake_tick_executor_run(fn):
        tick_executor_calls.append(fn)
        return fn()

    monkeypatch.setattr(tick_executor, "run", fake_tick_executor_run)
    monkeypatch.setattr(game_state, "record", lambda *a, **k: (True, True))
    monkeypatch.setattr(game_state, "flush", lambda: {"rows": 0})

    class _FakeClient:
        async def get_event_live_data(self, event_ticker, range_hint=None):
            return {"live_data": {"type": "football_game", "details": {"status": "in_progress"}}}

    asyncio.run(em._fetch_event_live_data(_FakeClient(), [{"event_ticker": "EVT-A", "ticker": "EVT-A-25"}]))

    assert len(scheduled) == 1
    assert tick_executor_calls[0] is game_state.flush


def test_fetch_event_live_data_does_not_schedule_a_flush_when_not_told(monkeypatch):
    monkeypatch.setitem(state, "event_live_data_cache", {})
    monkeypatch.setitem(state, "event_titles", {})

    scheduled = []
    real_create_task = asyncio.create_task

    def spy_create_task(coro):
        scheduled.append(coro)
        return real_create_task(coro)

    monkeypatch.setattr(asyncio, "create_task", spy_create_task)
    monkeypatch.setattr(game_state, "record", lambda *a, **k: (True, False))

    class _FakeClient:
        async def get_event_live_data(self, event_ticker, range_hint=None):
            return {"live_data": {"type": "football_game", "details": {"status": "in_progress"}}}

    asyncio.run(em._fetch_event_live_data(_FakeClient(), [{"event_ticker": "EVT-B", "ticker": "EVT-B-25"}]))

    assert scheduled == []
