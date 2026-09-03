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


def test_candidate_log_summary_runs_population_gate_summary_via_tick_executor(monkeypatch):
    calls = []

    async def _spy_run(fn):
        calls.append(fn)
        return fn()

    monkeypatch.setattr(analytics_routes.tick_executor, "run", _spy_run)
    monkeypatch.setattr(analytics_routes.candidate_log, "gate_summary", lambda: [{"stub": "gates"}])
    monkeypatch.setattr(
        analytics_routes.candidate_log, "population_gate_summary", lambda min_samples: [{"stub": "pop"}]
    )

    result = asyncio.run(analytics_routes.get_candidate_log_summary())

    assert len(calls) == 1
    assert result == {"gates": [{"stub": "gates"}], "population_gates": [{"stub": "pop"}]}


def test_candidate_log_summary_population_gates_is_cached_within_ttl(monkeypatch):
    """Task 6b of docs/superpowers/plans/2026-09-03-tier1-backend-hygiene.md:
    population_gate_summary() cannot be scoped with since_ts either (same
    total-sample-gate reasoning as resolved_signals_with_factors(), verified
    in this task's own research), so the fix is a short-TTL cache on the
    ROUTE - confirmed live cost is real (~4.8s currently, this route's own
    existing comment documents the pre-2026-08-26-fix 17-38s figure)."""
    calls = []

    async def _spy_run(fn):
        calls.append(1)
        return fn()

    monkeypatch.setattr(analytics_routes.tick_executor, "run", _spy_run)
    monkeypatch.setattr(analytics_routes.candidate_log, "gate_summary", lambda: [{"stub": "gates"}])
    monkeypatch.setattr(
        analytics_routes.candidate_log, "population_gate_summary", lambda min_samples: [{"stub": "pop"}]
    )

    asyncio.run(analytics_routes.get_candidate_log_summary())
    asyncio.run(analytics_routes.get_candidate_log_summary())

    assert len(calls) == 1, "second call within TTL should reuse the cached population_gates result"


def test_candidate_log_summary_population_gates_recomputes_after_ttl_expires(monkeypatch):
    """Sibling of the above - confirms the cache is time-bounded, not
    permanent, by advancing a monkeypatched time.time() past
    _POPULATION_GATES_CACHE_TTL_SEC between the two calls."""
    calls = []

    async def _spy_run(fn):
        calls.append(1)
        return fn()

    monkeypatch.setattr(analytics_routes.tick_executor, "run", _spy_run)
    monkeypatch.setattr(analytics_routes.candidate_log, "gate_summary", lambda: [{"stub": "gates"}])
    monkeypatch.setattr(
        analytics_routes.candidate_log, "population_gate_summary", lambda min_samples: [{"stub": "pop"}]
    )

    fake_now = [1_000_000.0]
    monkeypatch.setattr(analytics_routes.time, "time", lambda: fake_now[0])

    asyncio.run(analytics_routes.get_candidate_log_summary())
    fake_now[0] += analytics_routes._POPULATION_GATES_CACHE_TTL_SEC + 1
    asyncio.run(analytics_routes.get_candidate_log_summary())

    assert len(calls) == 2, "a call after the TTL has elapsed should recompute, not reuse the stale cache"
