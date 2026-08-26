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

from services.analytics import routes as analytics_routes


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
