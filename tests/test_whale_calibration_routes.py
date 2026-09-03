"""services/whale_calibration/routes.py - offload wiring for the
calibration status/report/apply routes.

Root-caused 2026-08-26 via a live py-spy stack trace (ROADMAP.md's
event-loop-stall entry, a second incident found while investigating
WS-subscription churn): GET /api/confidence-calibration/status fetched
and JSON-parsed every resolved-with-factors signal just to call len() on
the result, and GET /api/confidence-calibration/report /
POST /api/confidence-calibration/apply ran the full per-factor bucket
analysis (confidence_calibration._bucket_win_rates) directly on the
event loop, on every dashboard poll. This only covers the wiring;
resolved_with_factors_count()'s own behavior is covered by
tests/test_signal_log.py, and generate_calibration_report()'s by
tests/test_confidence_calibration.py.
"""
import asyncio

import pytest

from services.whale_calibration import routes as calibration_routes


def _cfg():
    return {
        "confidence_calibration": {"enabled": True, "min_resolved_signals": 30},
        "whale_confidence_weights": {"depth_factor": 0.5},
    }


@pytest.fixture(autouse=True)
def _reset_report_cache():
    """Task 6b of docs/superpowers/plans/2026-09-03-tier1-backend-hygiene.md
    added a module-level _report_cache to get_confidence_calibration_report.
    Without this reset, one test's cached (mocked) report can leak into the
    next test that calls the same route within the 30s TTL, since
    _report_cache is a plain module attribute that persists across test
    functions in the same pytest process - not something any pre-existing
    test in this file accounted for before caching existed."""
    calibration_routes._report_cache = {"cached_at": None, "value": None}
    yield
    calibration_routes._report_cache = {"cached_at": None, "value": None}


def test_status_uses_resolved_with_factors_count_not_the_full_fetch(monkeypatch):
    monkeypatch.setattr(calibration_routes.config_store, "get", lambda: _cfg())
    monkeypatch.setattr(calibration_routes.signal_log, "resolved_with_factors_count", lambda: 42)

    def _boom():
        raise AssertionError("resolved_signals_with_factors() should not be called by /status")

    monkeypatch.setattr(calibration_routes.signal_log, "resolved_signals_with_factors", _boom)

    result = asyncio.run(calibration_routes.get_confidence_calibration_status())

    assert result["resolved_count"] == 42


def test_report_runs_via_tick_executor(monkeypatch):
    calls = []

    async def _spy_run(fn):
        calls.append(fn)
        return fn()

    monkeypatch.setattr(calibration_routes.tick_executor, "run", _spy_run)
    monkeypatch.setattr(calibration_routes.config_store, "get", lambda: _cfg())
    monkeypatch.setattr(calibration_routes.signal_log, "resolved_signals_with_factors", lambda: [])
    monkeypatch.setattr(
        calibration_routes.confidence_calibration, "generate_calibration_report",
        lambda rows, min_signals, weights: {"report": None, "gated_reason": "stub", "resolved_count": 0},
    )
    monkeypatch.setattr(
        calibration_routes.evidence_provenance, "current_completeness_state",
        lambda: {"degraded": False, "defects": [], "checked_at": 0.0},
    )

    result = asyncio.run(calibration_routes.get_confidence_calibration_report())

    assert len(calls) == 1
    assert result == {
        "report": None, "gated_reason": "stub", "resolved_count": 0,
        "evidence_provenance": {"degraded": False, "defects": [], "checked_at": 0.0},
    }


def test_apply_runs_via_tick_executor(monkeypatch):
    calls = []

    async def _spy_run(fn):
        calls.append(fn)
        return fn()

    monkeypatch.setattr(calibration_routes.tick_executor, "run", _spy_run)
    monkeypatch.setattr(calibration_routes.config_store, "get", lambda: _cfg())
    monkeypatch.setattr(calibration_routes.signal_log, "resolved_signals_with_factors", lambda: [])
    monkeypatch.setattr(
        calibration_routes.confidence_calibration, "generate_calibration_report",
        lambda rows, min_signals, weights: {
            "report": {"suggested_weights": {"depth_factor": 0.9}, "resolved_count": 5},
            "gated_reason": None, "resolved_count": 5,
        },
    )
    monkeypatch.setattr(
        calibration_routes.confidence_calibration, "blended_weights_for_auto_apply",
        lambda current, suggested: {"depth_factor": 0.9},
    )
    monkeypatch.setattr(calibration_routes.config_store, "update", lambda patch: None)
    monkeypatch.setattr(calibration_routes.config_performance, "fingerprint", lambda cfg: "fp")
    monkeypatch.setattr(calibration_routes.config_performance, "log_applied_change", lambda **kwargs: None)
    monkeypatch.setattr(calibration_routes, "bump_generation", lambda: None)

    result = asyncio.run(calibration_routes.apply_confidence_calibration_suggestion())

    assert len(calls) == 1
    assert result == {"applied": True, "new_weights": {"depth_factor": 0.9}}


def test_status_includes_evidence_provenance_block(monkeypatch):
    monkeypatch.setattr(calibration_routes.config_store, "get", lambda: _cfg())
    monkeypatch.setattr(calibration_routes.signal_log, "resolved_with_factors_count", lambda: 0)
    monkeypatch.setattr(
        calibration_routes.evidence_provenance, "current_completeness_state",
        lambda: {"degraded": False, "defects": [], "checked_at": 0.0},
    )

    result = asyncio.run(calibration_routes.get_confidence_calibration_status())

    assert result["evidence_provenance"] == {"degraded": False, "defects": [], "checked_at": 0.0}


def test_report_includes_evidence_provenance_block(monkeypatch):
    monkeypatch.setattr(calibration_routes.config_store, "get", lambda: _cfg())
    monkeypatch.setattr(calibration_routes.signal_log, "resolved_signals_with_factors", lambda: [])
    monkeypatch.setattr(
        calibration_routes.confidence_calibration, "generate_calibration_report",
        lambda rows, min_n, weights: {"report": None, "gated_reason": "not enough data", "resolved_count": 0},
    )
    monkeypatch.setattr(
        calibration_routes.evidence_provenance, "current_completeness_state",
        lambda: {"degraded": True, "defects": [{"component": "capture_writer"}], "checked_at": 5.0},
    )

    result = asyncio.run(calibration_routes.get_confidence_calibration_report())

    assert result["evidence_provenance"]["degraded"] is True


def test_confidence_calibration_report_is_cached_within_ttl(monkeypatch):
    """Task 6b of docs/superpowers/plans/2026-09-03-tier1-backend-
    hygiene.md: resolved_signals_with_factors() cannot be scoped with
    since_ts (it computes a total-sample gate, verified in this task's own
    research), so the fix is a short-TTL cache on the ROUTE, not a query
    bound - confirmed live cost is real (~1s at 103k+ rows per signal_
    log.py's own docstring)."""
    calls = []
    monkeypatch.setattr(calibration_routes.config_store, "get", lambda: _cfg())
    monkeypatch.setattr(
        calibration_routes.signal_log, "resolved_signals_with_factors", lambda: calls.append(1) or []
    )
    monkeypatch.setattr(
        calibration_routes.confidence_calibration, "generate_calibration_report",
        lambda rows, min_n, weights: {"report": None, "gated_reason": "stub", "resolved_count": 0},
    )
    monkeypatch.setattr(
        calibration_routes.evidence_provenance, "current_completeness_state",
        lambda: {"degraded": False, "defects": [], "checked_at": 0.0},
    )
    monkeypatch.setattr(calibration_routes, "_report_cache", {"cached_at": None, "value": None})

    asyncio.run(calibration_routes.get_confidence_calibration_report())
    asyncio.run(calibration_routes.get_confidence_calibration_report())

    assert len(calls) == 1, "second call within TTL should reuse the cached result"


def test_confidence_calibration_report_recomputes_after_ttl_expires(monkeypatch):
    """Sibling of the above - confirms the cache is time-bounded, not
    permanent, by advancing a monkeypatched time.time() past
    _REPORT_CACHE_TTL_SEC between the two calls."""
    calls = []
    monkeypatch.setattr(calibration_routes.config_store, "get", lambda: _cfg())
    monkeypatch.setattr(
        calibration_routes.signal_log, "resolved_signals_with_factors", lambda: calls.append(1) or []
    )
    monkeypatch.setattr(
        calibration_routes.confidence_calibration, "generate_calibration_report",
        lambda rows, min_n, weights: {"report": None, "gated_reason": "stub", "resolved_count": 0},
    )
    monkeypatch.setattr(
        calibration_routes.evidence_provenance, "current_completeness_state",
        lambda: {"degraded": False, "defects": [], "checked_at": 0.0},
    )
    monkeypatch.setattr(calibration_routes, "_report_cache", {"cached_at": None, "value": None})

    fake_now = [1_000_000.0]
    monkeypatch.setattr(calibration_routes.time, "time", lambda: fake_now[0])

    asyncio.run(calibration_routes.get_confidence_calibration_report())
    fake_now[0] += calibration_routes._REPORT_CACHE_TTL_SEC + 1
    asyncio.run(calibration_routes.get_confidence_calibration_report())

    assert len(calls) == 2, "a call after the TTL has elapsed should recompute, not reuse the stale cache"
