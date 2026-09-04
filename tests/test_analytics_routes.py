"""services/analytics/routes.py - GET /api/candidate-log/summary offload.

Root-caused 2026-08-26 via a live py-spy stack trace during a real
17-38s event-loop stall (ROADMAP.md's "Path to production" entry):
population_gate_summary() runs an unbounded scan of rejection_events
(6.2M rows and growing, no retention) and was called directly and
synchronously from this route - every dashboard poll blocked the event
loop for the full duration of that scan. This only covers the wiring
(does the route hand the call to tick_executor instead of running it
inline) - population_gate_summary()'s own behavior/output is already
covered by tests/test_candidate_log.py and its scaling behavior by
tests/test_performance_regressions.py.
"""
import asyncio

import pytest

from services import tick_executor
from services.analytics import routes as analytics_routes


@pytest.fixture(autouse=True)
def _reset_population_gates_cache():
    """Task 6b of docs/superpowers/plans/2026-09-03-tier1-backend-hygiene.md
    added a module-level _population_gates_cache to get_candidate_log_
    summary. Without this reset, one test's cached (mocked) population_gates
    result could leak into the next test that calls this route within the
    30s TTL, since _population_gates_cache is a plain module attribute that
    persists across test functions in the same pytest process."""
    analytics_routes._population_gates_cache = {"cached_at": None, "value": None}
    yield
    analytics_routes._population_gates_cache = {"cached_at": None, "value": None}


def test_candidate_log_summary_never_routes_population_gates_through_tick_executor(monkeypatch):
    """Deliberately the INVERSE of the assertion this test made until issue
    #410 (it was named ..._runs_population_gate_summary_via_tick_executor).

    docs/superpowers/specs/2026-09-04-issue-410-pool-vs-aiosqlite-design.md
    Sec 5 asks for exactly this inversion as its permanent
    detection-for-recurrence: the route must reach candidate_log's native
    aiosqlite path, never tick_executor's 2-worker pool, which is shared
    with candidate_ledger.claim()/record_decision() on the live per-signal
    decision path. A future refactor that silently routes this 15-22s query
    back onto that pool fails here instead of quietly costing trade
    latency.

    Patches the shared services.tick_executor module itself rather than an
    attribute on the route module - services/analytics/routes.py no longer
    imports it at all, and patching the shared module catches a re-added
    call arriving by any import path, not just the one name this module
    used to bind."""
    tick_executor_calls = []
    async_calls = []

    async def _spy_run(fn):
        tick_executor_calls.append(fn)
        return fn()

    async def _stub_async(min_samples):
        async_calls.append(min_samples)
        return [{"stub": "pop"}]

    monkeypatch.setattr(tick_executor, "run", _spy_run)
    monkeypatch.setattr(analytics_routes.candidate_log, "gate_summary", lambda: [{"stub": "gates"}])
    monkeypatch.setattr(analytics_routes.candidate_log, "population_gate_summary_async", _stub_async)

    result = asyncio.run(analytics_routes.get_candidate_log_summary())

    assert tick_executor_calls == [], "population_gates must not go through tick_executor any more"
    assert async_calls == [30], "the async aiosqlite path should have been called once, with min_samples"
    assert result == {"gates": [{"stub": "gates"}], "population_gates": [{"stub": "pop"}]}


def test_candidate_log_summary_population_gates_is_cached_within_ttl(monkeypatch):
    """Task 6b of docs/superpowers/plans/2026-09-03-tier1-backend-hygiene.md:
    population_gate_summary() cannot be scoped with since_ts either (same
    total-sample-gate reasoning as resolved_signals_with_factors(), verified
    in this task's own research), so the fix is a short-TTL cache on the
    ROUTE - confirmed live cost is real (~4.8s currently, this route's own
    existing comment documents the pre-2026-08-26-fix 17-38s figure)."""
    calls = []

    async def _spy_async(min_samples):
        calls.append(1)
        return [{"stub": "pop"}]

    monkeypatch.setattr(analytics_routes.candidate_log, "gate_summary", lambda: [{"stub": "gates"}])
    monkeypatch.setattr(analytics_routes.candidate_log, "population_gate_summary_async", _spy_async)

    asyncio.run(analytics_routes.get_candidate_log_summary())
    asyncio.run(analytics_routes.get_candidate_log_summary())

    assert len(calls) == 1, "second call within TTL should reuse the cached population_gates result"


def test_candidate_log_summary_population_gates_recomputes_after_ttl_expires(monkeypatch):
    """Sibling of the above - confirms the cache is time-bounded, not
    permanent, by advancing a monkeypatched time.time() past
    _POPULATION_GATES_CACHE_TTL_SEC between the two calls."""
    calls = []

    async def _spy_async(min_samples):
        calls.append(1)
        return [{"stub": "pop"}]

    monkeypatch.setattr(analytics_routes.candidate_log, "gate_summary", lambda: [{"stub": "gates"}])
    monkeypatch.setattr(analytics_routes.candidate_log, "population_gate_summary_async", _spy_async)

    fake_now = [1_000_000.0]
    monkeypatch.setattr(analytics_routes.time, "time", lambda: fake_now[0])

    asyncio.run(analytics_routes.get_candidate_log_summary())
    fake_now[0] += analytics_routes._POPULATION_GATES_CACHE_TTL_SEC + 1
    asyncio.run(analytics_routes.get_candidate_log_summary())

    assert len(calls) == 2, "a call after the TTL has elapsed should recompute, not reuse the stale cache"


def test_candidate_log_summary_cache_is_stamped_at_completion_not_request_receipt(monkeypatch):
    """Issue #410 cache-alignment bug (docs/superpowers/research/2026-09-04-
    issue-410-tick-executor-measurement.md Sec 3.4): `cached_at` was stamped
    with a `now` captured BEFORE awaiting the query, so a 15-22s query
    burned 53-73% of its own 30s TTL before the entry was even written.

    The frontend polls on a 30000ms throttle measured from when it last
    FIRED (frontend/src/js/main.js:79-98), so the next poll lands at
    T_fire + [30, 36)s - at or past a TTL window measured from the previous
    FIRE instant, meaning essentially every scheduled poll missed. Each miss
    re-occupies one of tick_executor's 2 workers, the same pool
    candidate_ledger.claim()/record_decision() uses on the live per-signal
    decision path.

    This reproduces the real steady-state pattern rather than the
    implementation detail: a 20s query, then a second poll 33s after the
    FIRST FIRED (mid-window). Stamping at completion makes that a hit."""
    calls = []
    fake_now = [1_000_000.0]
    query_duration_sec = 20.0

    async def _slow_async(min_samples):
        calls.append(1)
        fake_now[0] += query_duration_sec  # the query itself takes 20s
        return [{"stub": "pop"}]

    monkeypatch.setattr(analytics_routes.candidate_log, "gate_summary", lambda: [{"stub": "gates"}])
    monkeypatch.setattr(analytics_routes.candidate_log, "population_gate_summary_async", _slow_async)
    monkeypatch.setattr(analytics_routes.time, "time", lambda: fake_now[0])

    # Poll 1 fires at t=0, completes at t=20.
    asyncio.run(analytics_routes.get_candidate_log_summary())
    assert calls == [1]
    assert analytics_routes._population_gates_cache["cached_at"] == 1_000_000.0 + query_duration_sec, (
        "cached_at must be the completion instant, not the request-receipt instant"
    )

    # Poll 2 fires 33s after poll 1 FIRED - squarely inside the real
    # frontend's [30, 36)s window. 33 - 20 = 13s of cache age < 30s TTL.
    fake_now[0] = 1_000_000.0 + 33.0
    asyncio.run(analytics_routes.get_candidate_log_summary())

    assert len(calls) == 1, (
        "a poll landing in the frontend's real [30, 36)s window must hit the cache; "
        "stamping cached_at at request-receipt made every scheduled poll a miss"
    )
