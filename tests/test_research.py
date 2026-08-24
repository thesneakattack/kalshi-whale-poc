"""
services/research/research.py - evidence-triggered orchestration of seven
existing read-only analyzers into one persisted snapshot (QCP Task 16,
docs/superpowers/plans/2026-08-24-quality-control-plane.md).

build_report's own composition tests monkeypatch every analyzer it calls
(same instruction as this task's own plan: "Write report-composition tests
using monkeypatched existing analyzers") rather than exercising the real
seven modules end to end - those modules already have their own test
files; this file's job is proving research.py wires them together
correctly and never calls an apply/update method, not re-testing their
internals.

research.py imports services.app_state (for broker.trade_log), which
constructs PaperBroker/RiskManager/etc. at import time - safe here without
a manual per-file DB_PATH dance because tests/conftest.py's
install_runtime_isolation() already redirects every registered module
(including services.research.research, registered in this same QCP task)
before any test file is collected. See tests/test_backup.py for the
precedent this follows.
"""
import asyncio
import time
import types

import pytest

from services.app_state import broker, state
from services.research import research


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(research, "DB_PATH", tmp_path / "research_reports.db")
    state["research"] = {"running": False, "task": None, "checkpoints": None}
    monkeypatch.setattr(broker, "trade_log", [])
    yield


def _fake_trade(reason: str, excluded: bool = False):
    return types.SimpleNamespace(reason=reason, excluded=excluded, to_dict=lambda: {"reason": reason})


# --- should_run: OR semantics + no-prior-checkpoint behavior ---------------

_RESEARCH_CFG = {"research": {"enabled": True, "min_new_resolved_signals": 100, "min_new_closed_trades": 50}}


def test_should_run_true_when_resolved_signals_grew_past_threshold():
    checkpoints = {"resolved_signals": 10, "closed_trades": 5}
    current = {"resolved_signals": 110, "closed_trades": 5}  # +100 resolved
    assert research.should_run(_RESEARCH_CFG, checkpoints, current) is True


def test_should_run_true_when_closed_trades_grew_past_threshold():
    checkpoints = {"resolved_signals": 10, "closed_trades": 5}
    current = {"resolved_signals": 10, "closed_trades": 55}  # +50 closed trades
    assert research.should_run(_RESEARCH_CFG, checkpoints, current) is True


def test_should_run_false_when_below_both_thresholds():
    checkpoints = {"resolved_signals": 10, "closed_trades": 5}
    current = {"resolved_signals": 50, "closed_trades": 20}  # +40 / +15, below both
    assert research.should_run(_RESEARCH_CFG, checkpoints, current) is False


def test_should_run_treats_an_empty_checkpoint_as_a_fresh_baseline_of_zero():
    # Documented choice (research.py's own should_run docstring): {} means
    # every current count is entirely new - a real first run is legitimate
    # if history already clears a threshold, not noise to suppress.
    current = {"resolved_signals": 150, "closed_trades": 0}
    assert research.should_run(_RESEARCH_CFG, {}, current) is True

    current_below = {"resolved_signals": 10, "closed_trades": 5}
    assert research.should_run(_RESEARCH_CFG, {}, current_below) is False


def test_should_run_uses_configured_thresholds_not_hardcoded_defaults():
    cfg = {"research": {"min_new_resolved_signals": 5, "min_new_closed_trades": 1000}}
    assert research.should_run(cfg, {}, {"resolved_signals": 5, "closed_trades": 0}) is True
    assert research.should_run(cfg, {}, {"resolved_signals": 4, "closed_trades": 0}) is False


# --- current_counts: cheap real counters -----------------------------------

def test_current_counts_counts_close_trades_and_excludes_flagged_ones(monkeypatch):
    monkeypatch.setattr(research.signal_log, "total_count", lambda resolved_only=False: 42)
    broker.trade_log[:] = [
        _fake_trade("opened: long"),
        _fake_trade("closed: profit target"),
        _fake_trade("closed: stop loss"),
        _fake_trade("closed: erroneous", excluded=True),  # must not count
    ]

    counts = research.current_counts()

    assert counts == {"resolved_signals": 42, "closed_trades": 2}


# --- build_report: composition over monkeypatched analyzers ---------------

def _patch_every_analyzer(monkeypatch, *, advisory_enabled: bool = True):
    monkeypatch.setattr(research.diagnostics, "run_offline", lambda cfg, now=None: {"overall": "ok"})
    monkeypatch.setattr(research.trade_analytics, "build_trade_history", lambda trade_log: [{"stub": "row"}])
    monkeypatch.setattr(research.trade_analytics, "compute_summary", lambda rows: {"total_closed": len(rows)})
    monkeypatch.setattr(research.signal_log, "resolved_signals_with_factors", lambda: [{"stub": "signal"}])
    monkeypatch.setattr(
        research.confidence_calibration, "generate_calibration_report",
        lambda rows, min_resolved_signals, current_weights=None: {"report": {"resolved_count": len(rows)}},
    )
    monkeypatch.setattr(research.config_performance, "fingerprint", lambda cfg: "fp-stub")
    monkeypatch.setattr(research.config_performance, "all_variants", lambda: [{"fingerprint": "fp-stub"}])
    monkeypatch.setattr(research.config_performance, "all_last_applied_by_path", lambda: {})
    monkeypatch.setattr(research.config_performance, "applied_changes_count", lambda: 3)
    monkeypatch.setattr(research.config_performance, "recent_applied_changes", lambda limit=50, offset=0: [])
    monkeypatch.setattr(research.candidate_log, "gate_summary", lambda: [{"stub": "gate"}])
    monkeypatch.setattr(research.candidate_log, "population_gate_summary", lambda min_samples=30: [{"stub": "pop_gate"}])
    monkeypatch.setattr(research, "_series_evaluator_rows_for_advisory", lambda cfg: [{"stub": "series_row"}])
    monkeypatch.setattr(research.regime_analytics, "by_category", lambda rows: [{"stub": "category"}])
    monkeypatch.setattr(research.suggestion_decisions, "declined_ids", lambda: set())
    monkeypatch.setattr(research.series_evaluator, "overview", lambda: [{"stub": "series_eval"}])
    monkeypatch.setattr(research.settlement_edge, "edge_report", lambda min_samples=30: {"verdict": "stub"})

    advisory_calls = []

    def fake_generate_recommendations(*args, **kwargs):
        advisory_calls.append((args, kwargs))
        return {"recommendations": [], "resolved_count": 0}

    monkeypatch.setattr(research.advisory_engine, "generate_recommendations", fake_generate_recommendations)
    return advisory_calls


def test_build_report_includes_every_documented_section(monkeypatch):
    _patch_every_analyzer(monkeypatch)
    cfg = {"advisory": {"enabled": True, "min_resolved_trades_per_variant": 30}, "confidence_calibration": {}}

    report = research.build_report(cfg, now=1000.0)

    assert report["generated_at"] == 1000.0
    assert report["diagnostics"] == {"overall": "ok"}
    assert report["trade_analytics"] == {"total_closed": 1}
    assert report["confidence_calibration"]["report"]["resolved_count"] == 1
    assert report["advisory"] == {"recommendations": [], "resolved_count": 0}
    assert report["candidate_population_gate"] == [{"stub": "pop_gate"}]
    assert report["series_evaluator"] == [{"stub": "series_eval"}]
    assert report["settlement_edge"] == {"verdict": "stub"}
    assert report["config_epoch"]["current_fingerprint"] == "fp-stub"
    assert report["config_epoch"]["applied_changes_count"] == 3


def test_build_report_skips_advisory_generation_when_disabled(monkeypatch):
    advisory_calls = _patch_every_analyzer(monkeypatch)
    cfg = {"advisory": {"enabled": False}, "confidence_calibration": {}}

    report = research.build_report(cfg, now=1000.0)

    assert advisory_calls == []  # generate_recommendations never called at all
    assert report["advisory"] == {
        "recommendations": [], "gated_reason": "advisory engine is disabled", "resolved_count": None,
    }


def test_build_report_never_touches_config_store_or_logs_an_applied_change(monkeypatch):
    _patch_every_analyzer(monkeypatch)
    update_calls = []
    log_calls = []
    monkeypatch.setattr(research.config_performance, "log_applied_change", lambda **kw: log_calls.append(kw))
    from services.config_store import config_store
    monkeypatch.setattr(config_store, "update", lambda *a, **kw: update_calls.append((a, kw)))
    cfg = {"advisory": {"enabled": True, "min_resolved_trades_per_variant": 30}, "confidence_calibration": {}}

    research.build_report(cfg, now=1000.0)

    assert update_calls == []
    assert log_calls == []


# --- persistence: run_and_store / latest / recent --------------------------

def test_run_and_store_persists_a_row_readable_via_latest(monkeypatch):
    _patch_every_analyzer(monkeypatch)
    monkeypatch.setattr(research.signal_log, "total_count", lambda resolved_only=False: 7)
    broker.trade_log[:] = [_fake_trade("closed: profit target")]
    cfg = {"advisory": {"enabled": False}, "confidence_calibration": {}}

    report = research.run_and_store(cfg)

    last = research.latest()
    assert last is not None
    assert last["resolved_signals_count"] == 7
    assert last["closed_trades_count"] == 1
    assert last["report"] == report


def test_recent_returns_newest_first_and_respects_limit(monkeypatch):
    _patch_every_analyzer(monkeypatch)
    monkeypatch.setattr(research.signal_log, "total_count", lambda resolved_only=False: 0)
    cfg = {"advisory": {"enabled": False}, "confidence_calibration": {}}
    for _ in range(3):
        research.run_and_store(cfg)

    reports = research.recent(limit=2)

    assert len(reports) == 2
    assert reports[0]["generated_at"] >= reports[1]["generated_at"]


def test_latest_is_none_with_no_persisted_reports():
    assert research.latest() is None


# --- _maybe_run_research: cold-start seeding, same shape as backup's own --

async def _call_maybe_run_research(cfg):
    research._maybe_run_research(cfg)
    await asyncio.sleep(0.05)  # let any scheduled background task actually run


def test_disabled_never_fires_regardless_of_history(monkeypatch):
    _patch_every_analyzer(monkeypatch)
    monkeypatch.setattr(research.signal_log, "total_count", lambda resolved_only=False: 999)

    asyncio.run(_call_maybe_run_research({"research": {"enabled": False}}))

    assert research.latest() is None
    assert state["research"]["running"] is False


def test_cold_start_seeds_checkpoints_from_persisted_history_and_does_not_refire(monkeypatch):
    _patch_every_analyzer(monkeypatch)
    monkeypatch.setattr(research.signal_log, "total_count", lambda resolved_only=False: 10)
    research.run_and_store({"advisory": {"enabled": False}, "confidence_calibration": {}})
    assert state["research"]["checkpoints"] is None  # simulated cold start (fresh process)

    asyncio.run(_call_maybe_run_research(_RESEARCH_CFG))

    assert state["research"]["running"] is False  # 10 resolved, unchanged since the seed - not due
    assert len(research.recent(limit=10)) == 1  # no spurious second report
    assert state["research"]["checkpoints"] == {"resolved_signals": 10, "closed_trades": 0}


def test_cold_start_fires_when_new_evidence_exceeds_the_persisted_watermark(monkeypatch):
    _patch_every_analyzer(monkeypatch)
    monkeypatch.setattr(research.signal_log, "total_count", lambda resolved_only=False: 10)
    research.run_and_store({"advisory": {"enabled": False}, "confidence_calibration": {}})
    monkeypatch.setattr(research.signal_log, "total_count", lambda resolved_only=False: 200)  # +190, past 100

    asyncio.run(_call_maybe_run_research(_RESEARCH_CFG))

    assert state["research"]["running"] is False  # finished, flag released
    assert len(research.recent(limit=10)) == 2  # the real run fired, on top of the seed


def test_fresh_install_with_no_history_runs_once_when_current_counts_already_clear_a_threshold(monkeypatch):
    _patch_every_analyzer(monkeypatch)
    monkeypatch.setattr(research.signal_log, "total_count", lambda resolved_only=False: 150)
    assert research.latest() is None

    asyncio.run(_call_maybe_run_research(_RESEARCH_CFG))

    assert len(research.recent(limit=10)) == 1


def test_never_fires_twice_concurrently(monkeypatch):
    _patch_every_analyzer(monkeypatch)
    monkeypatch.setattr(research.signal_log, "total_count", lambda resolved_only=False: 150)
    state["research"]["running"] = True  # simulate an in-flight run

    asyncio.run(_call_maybe_run_research(_RESEARCH_CFG))

    assert research.latest() is None  # the already-"running" guard suppressed a second kickoff
