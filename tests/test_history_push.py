"""services/history_push.py - the History tab's event-driven push
mechanism (docs/archive/lane-8-frontend-dashboard/specs/2026-09-03-history-event-driven-design.md,
moved there 2026-09-06, planning-lanes migration).

Three groups of tests, per the implementation task's own rigor bar for this
module (a genuinely new mechanism on a path adjacent to trading-critical
code):

1. Core mechanism - the leading-edge coalescing shape (same pattern as
   app_state.bump_generation, independently scoped/rate-limited per §4.2).
2. Threading correctness - the crux the design's adversarial review already
   proved necessary (§4.3): candidate_ledger.claim()/record_decision() run
   on a tick_executor worker thread, not the event loop, so a plain
   asyncio.create_task() call from inside mark_history_changed() would raise
   RuntimeError the first time it fired from that thread. This file proves
   both halves directly - the raw crash, and the run_coroutine_threadsafe
   fix - rather than trusting the design doc's own repro on paper.
3. Trigger-point tests for each real write function live in that module's
   own test file (test_paper_broker.py, test_candidate_ledger.py,
   test_signal_log.py, test_calibration_history.py,
   test_config_performance.py) - not duplicated here.
"""
import asyncio
import threading

import pytest

from services import history_push
from services.ws_manager import ws_manager


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    monkeypatch.setattr(history_push, "_last_broadcast_ts", 0.0)
    monkeypatch.setattr(history_push, "_main_loop", None)
    yield


def _spy_broadcast(monkeypatch):
    calls = []

    async def fake_broadcast(message):
        calls.append(message)

    monkeypatch.setattr(ws_manager, "broadcast", fake_broadcast)
    return calls


# --- 1. Core mechanism: leading-edge coalescing -----------------------------


def test_mark_history_changed_broadcasts_on_first_call(monkeypatch):
    calls = _spy_broadcast(monkeypatch)

    async def _run():
        history_push.mark_history_changed()
        await asyncio.sleep(0)  # let the scheduled create_task() run

    asyncio.run(_run())
    assert calls == [{"type": "history_updated"}]


def test_mark_history_changed_coalesces_within_the_interval(monkeypatch):
    """Two calls in the same instant (e.g. a trade close and its
    candidate-ledger decision landing back to back) must produce exactly
    one broadcast, not two - the whole point of coalescing."""
    calls = _spy_broadcast(monkeypatch)
    fake_now = [1000.0]
    monkeypatch.setattr(history_push.time, "time", lambda: fake_now[0])

    async def _run():
        history_push.mark_history_changed()
        history_push.mark_history_changed()  # same instant, must be suppressed
        await asyncio.sleep(0)

    asyncio.run(_run())
    assert len(calls) == 1


def test_mark_history_changed_fires_again_after_the_interval_elapses(monkeypatch):
    calls = _spy_broadcast(monkeypatch)
    fake_now = [1000.0]
    monkeypatch.setattr(history_push.time, "time", lambda: fake_now[0])

    async def _run():
        history_push.mark_history_changed()
        await asyncio.sleep(0)
        fake_now[0] += history_push._COALESCE_INTERVAL_SEC + 0.1
        history_push.mark_history_changed()
        await asyncio.sleep(0)

    asyncio.run(_run())
    assert len(calls) == 2


def test_coalesce_interval_matches_the_frontend_constant():
    """Design §4.2: deliberately reuses HISTORY_INSIGHTS_REFRESH_MS = 30000
    unchanged, not a new measurement - this is the dimensional-analysis
    anchor tying the backend coalescing window to that frontend constant."""
    assert history_push._COALESCE_INTERVAL_SEC == 30.0


def test_broadcast_message_has_no_payload_beyond_the_type_tag(monkeypatch):
    """§4.2: "No payload beyond the type tag" - the frontend does a full
    re-fetch on receipt, not a partial update from an embedded payload."""
    calls = _spy_broadcast(monkeypatch)

    async def _run():
        history_push.mark_history_changed()
        await asyncio.sleep(0)

    asyncio.run(_run())
    assert calls[0] == {"type": "history_updated"}


# --- 2. Threading correctness: the crux ------------------------------------


def test_create_task_from_a_plain_worker_thread_raises_runtime_error():
    """Proves the exact failure design §4.3 identified: asyncio.create_task()
    needs a running event loop IN THE CALLING THREAD. A plain
    ThreadPoolExecutor worker (the same shape as tick_executor._executor,
    which runs candidate_ledger.claim()/record_decision()) has none of its
    own - calling create_task() there raises, it does not silently queue
    onto the main loop. This is the failure mode a naive
    "just call create_task() at the write point" implementation would hit
    the first time record_decision() fired for real."""
    errors = []

    def _worker():
        async def _coro():
            pass

        coro = _coro()
        try:
            asyncio.create_task(coro)
        except RuntimeError as exc:
            errors.append(exc)
            coro.close()  # never scheduled - avoid a "never awaited" warning

    async def _run():
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _worker)

    asyncio.run(_run())
    assert len(errors) == 1
    assert "no running event loop" in str(errors[0]).lower()


def test_mark_history_changed_from_the_event_loop_thread_uses_create_task(monkeypatch):
    """The common case (signal_log.mark_resolved, PaperBroker.close_position
    at its hottest call site, etc. - all confirmed event-loop-side in
    design §4.3) must keep using the cheap asyncio.create_task() path, not
    the thread-hop machinery, when there already is a running loop in the
    calling thread."""
    calls = _spy_broadcast(monkeypatch)

    async def _run():
        # No set_main_loop() call at all - if this path silently fell
        # through to run_coroutine_threadsafe with _main_loop is None, it
        # would log-and-skip instead of broadcasting, and this would fail.
        history_push.mark_history_changed()
        await asyncio.sleep(0)

    asyncio.run(_run())
    assert calls == [{"type": "history_updated"}]


def test_mark_history_changed_from_a_worker_thread_does_not_raise_and_still_broadcasts(monkeypatch):
    """The fix under direct test: mark_history_changed() called from a real
    OS thread with no event loop of its own (mirroring tick_executor.run()'s
    exact shape - this is how candidate_ledger.record_decision() actually
    gets called in production, per decision_bridge.py:129) must not raise,
    and must still deliver the broadcast onto the captured main loop via
    asyncio.run_coroutine_threadsafe - captured once at startup the same
    way main.py's lifespan() will call history_push.set_main_loop()."""
    received = threading.Event()
    calls = []

    async def fake_broadcast(message):
        calls.append(message)
        received.set()

    monkeypatch.setattr(ws_manager, "broadcast", fake_broadcast)

    raised = []

    def _call_from_worker_thread():
        try:
            history_push.mark_history_changed()
        except Exception as exc:  # pragma: no cover - only populated on failure
            raised.append(exc)

    async def _run():
        loop = asyncio.get_running_loop()
        history_push.set_main_loop(loop)
        thread = threading.Thread(target=_call_from_worker_thread)
        thread.start()
        thread.join(timeout=2)
        for _ in range(100):
            if received.is_set():
                break
            await asyncio.sleep(0.02)

    asyncio.run(_run())
    assert raised == []
    assert received.is_set(), "broadcast never landed on the main loop"
    assert calls == [{"type": "history_updated"}]


def test_mark_history_changed_from_a_worker_thread_without_a_captured_loop_does_not_raise(monkeypatch):
    """If mark_history_changed() is ever called from a non-loop thread
    before set_main_loop() has run (or after the loop closed), it must
    degrade to a no-op, not crash the calling thread - this is a
    fire-and-forget dashboard notification, never allowed to take down a
    candidate_ledger write."""
    calls = _spy_broadcast(monkeypatch)
    raised = []

    def _call_from_worker_thread():
        try:
            history_push.mark_history_changed()
        except Exception as exc:  # pragma: no cover
            raised.append(exc)

    thread = threading.Thread(target=_call_from_worker_thread)
    thread.start()
    thread.join(timeout=2)

    assert raised == []
    assert calls == []


def test_set_main_loop_records_the_given_loop():
    async def _run():
        loop = asyncio.get_running_loop()
        history_push.set_main_loop(loop)
        assert history_push._main_loop is loop

    asyncio.run(_run())
