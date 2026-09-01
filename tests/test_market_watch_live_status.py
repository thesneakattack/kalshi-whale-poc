"""Tests for services/market_watch/live_status.py's game_state.record()
call site - event-loop-blocking elimination Fix 1, 2026-09-01."""
import asyncio
import time
from datetime import datetime, timezone

import services.market_watch.live_status as ls
from services import game_state, tick_executor
from services.app_state import state


def _fresh_market(event_ticker: str) -> dict:
    now = time.time()
    occ = datetime.fromtimestamp(now - 100, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    close = datetime.fromtimestamp(now + 3600, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "event_ticker": event_ticker, "ticker": f"{event_ticker}-25",
        "occurrence_datetime": occ, "close_time": close,
    }


class _FakeClient:
    """No get_events call needed: state["milestone_by_event"] is
    pre-populated per test below, so _fetch_live_status's needs_fetch
    batch (which would otherwise call client.get_events) stays empty."""

    def __init__(self, live_datas):
        self._live_datas = live_datas

    async def get_live_datas(self, milestone_ids):
        return self._live_datas


def test_fetch_live_status_schedules_flush_via_tick_executor_when_told(monkeypatch):
    monkeypatch.setitem(state, "live_status_cache", {})
    monkeypatch.setitem(state, "live_game_state", {})
    monkeypatch.setitem(state, "event_titles", {})
    monkeypatch.setitem(state, "milestone_by_event", {"EVT-A": "MS-1"})

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

    client = _FakeClient({"MS-1": {"type": "football_game", "details": {"status": "in_progress"}}})

    asyncio.run(ls._fetch_live_status(client, [_fresh_market("EVT-A")]))

    assert len(scheduled) == 1
    assert tick_executor_calls[0] is game_state.flush


def test_fetch_live_status_does_not_schedule_a_flush_when_not_told(monkeypatch):
    monkeypatch.setitem(state, "live_status_cache", {})
    monkeypatch.setitem(state, "live_game_state", {})
    monkeypatch.setitem(state, "event_titles", {})
    monkeypatch.setitem(state, "milestone_by_event", {"EVT-B": "MS-2"})

    scheduled = []
    real_create_task = asyncio.create_task

    def spy_create_task(coro):
        scheduled.append(coro)
        return real_create_task(coro)

    monkeypatch.setattr(asyncio, "create_task", spy_create_task)
    monkeypatch.setattr(game_state, "record", lambda *a, **k: (True, False))

    client = _FakeClient({"MS-2": {"type": "football_game", "details": {"status": "in_progress"}}})

    asyncio.run(ls._fetch_live_status(client, [_fresh_market("EVT-B")]))

    assert scheduled == []
