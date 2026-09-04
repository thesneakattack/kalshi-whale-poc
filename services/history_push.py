"""The History tab's event-driven push mechanism
(docs/superpowers/specs/2026-09-03-history-event-driven-design.md).

Replaces the History tab's old fixed-interval frontend polling with a
push-triggered notification: a real write to trade_log/signal_log/
candidate_ledger/calibration_history/config_performance calls
mark_history_changed(), which fires (at most once per _COALESCE_INTERVAL_SEC)
a no-payload {"type": "history_updated"} message over the existing
ws_manager/`/api/ws` connection (design §3 - reuse, not a second
connection). The frontend does a full re-fetch of the History tab's ~10
loaders on receipt (design §4.4) - this module only owns the "should I
notify right now" decision and getting that notification safely onto the
event loop.

Modeled on services/ws_manager.py's own extraction shape (design §4.2): a
self-contained leaf module with zero app-specific imports (asyncio, time,
threading, logging, and services.ws_manager only), so any module in this
app's dependency graph - including services/paper_broker.py,
services/candidate_ledger.py, services/signal_log.py,
services/whale_calibration/calibration_history.py, and
services/config/config_performance.py, none of which import
services/app_state.py today - can import this one without creating a
circular import (design §4.1 explains why reusing app_state.bump_generation
was rejected, both semantically and structurally).

Threading (design §4.3 - the crux correctness requirement, already proven
necessary by that design's adversarial review): candidate_ledger.claim()/
record_decision() run on a tick_executor worker thread, not the event loop
(services/whale_stream/decision_bridge.py awaits both via
tick_executor.run(), which wraps a plain ThreadPoolExecutor - see
services/tick_executor.py). A worker thread has no event loop of its own,
so asyncio.create_task() raises "RuntimeError: no running event loop" if
called from there - see tests/test_history_push.py's own repro of exactly
that crash. mark_history_changed() is a single thread-safe choke point
(design §4.3 option 2, the recommended shape): called from the event-loop
thread it uses the cheap asyncio.create_task() path; called from any other
thread it dispatches via asyncio.run_coroutine_threadsafe() against the
loop reference captured once at startup via set_main_loop() (called from
main.py's lifespan(), confirmed async def, before anything can race it).
"""
import asyncio
import logging
import threading
import time

from services.ws_manager import ws_manager

logger = logging.getLogger(__name__)

# Deliberately reuses HISTORY_INSIGHTS_REFRESH_MS (frontend/src/js/
# history-core.js) unchanged, not a new measurement - design §4.2 explains
# why: that 30s value is already the product of a real, measured fix (the
# 504-storm finding) and is already coordinated with the 30s server-side
# cache TTL on the tick_executor-routed endpoints (Task 6b). Shrinking it
# here without new measurement would violate the data-plane HARD RULE's
# "never change... cache TTL... because it should help."
_COALESCE_INTERVAL_SEC = 30.0

_last_broadcast_ts = 0.0
_lock = threading.Lock()

# Captured once, from main.py's lifespan() (an async def, confirmed at
# main.py's own module level) via set_main_loop() below. None until then -
# every call site that might run before startup (or in a bare unit test
# that never calls set_main_loop()) must degrade to a safe no-op, not raise,
# since this is a fire-and-forget dashboard notification that must never be
# allowed to take down a trading-critical write (design §7).
_main_loop: asyncio.AbstractEventLoop | None = None


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Call once, from main.py's lifespan(), on the event loop this app's
    single uvicorn worker actually runs on. Every later off-loop-thread
    call to mark_history_changed() dispatches its broadcast against this
    captured reference via asyncio.run_coroutine_threadsafe()."""
    global _main_loop
    _main_loop = loop


def mark_history_changed(*, now: float | None = None) -> None:
    """Call from any thread, at the return path of a real write this app's
    History tab depends on (trade close/open, signal resolution, candidate
    decision, a calibration snapshot, an applied config change - design
    §2's trigger table). Leading-edge coalesced, same shape as
    services/app_state.py's bump_generation(): an isolated change notifies
    near-immediately; a sustained burst of changes degrades to at most one
    broadcast per _COALESCE_INTERVAL_SEC."""
    global _last_broadcast_ts
    now = time.time() if now is None else now
    with _lock:
        if now - _last_broadcast_ts < _COALESCE_INTERVAL_SEC:
            return
        _last_broadcast_ts = now
    _dispatch_broadcast()


def _dispatch_broadcast() -> None:
    # No payload beyond the type tag (design §4.2) - the frontend does a
    # full re-fetch of the History tab's own loaders on receipt rather than
    # trying to reconstruct what changed from a partial payload.
    message = {"type": "history_updated"}
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        # Already on the event loop thread (the common case - design §4.3
        # confirms signal_log.mark_resolved, PaperBroker.close_position's
        # hottest call site, and the auto-apply/calibration-snapshot paths
        # are all event-loop-side): the same fire-and-forget
        # asyncio.create_task() shape every existing ws_manager.broadcast()
        # call site already uses (services/whale_stream/decision_bridge.py).
        loop.create_task(ws_manager.broadcast(message))
        return
    if _main_loop is not None and not _main_loop.is_closed():
        # Off the event loop thread (candidate_ledger.claim()/
        # record_decision(), running on tick_executor's ThreadPoolExecutor -
        # design §4.3's proven-necessary case). asyncio.create_task() would
        # raise RuntimeError here; run_coroutine_threadsafe is the
        # documented cross-thread-safe way to schedule a coroutine onto a
        # loop running in a different thread.
        asyncio.run_coroutine_threadsafe(ws_manager.broadcast(message), _main_loop)
        return
    # No running loop in this thread and no main loop captured yet (startup
    # race, or a bare unit test of a write function that never called
    # set_main_loop()) - degrade to a no-op rather than raise. The design's
    # own 5-minute safety-net poll (§4.4) bounds the staleness this causes;
    # silently dropping one notification is far cheaper than crashing the
    # trading-critical write this was called from.
    logger.warning("history_push: no event loop available to dispatch history_updated broadcast")
