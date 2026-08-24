"""Real bug found live (2026-08-24, direct report: "market evaluator is
disabled but it still feeds things to the advisory module") - see
services.analytics.market_analyst_orchestrator._series_evaluator_rows_for_
advisory's own docstring for the incident. These tests cover just that
gating function, not the full _series_evaluator_overview_with_crosscheck
enrichment (already exercised indirectly via services/analytics/routes.py's
GET /api/series-evaluator/status live usage).
"""
from services.analytics import market_analyst_orchestrator as orch
from services import series_evaluator


def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(series_evaluator, "DB_PATH", tmp_path / "series_evaluator.db")


def test_returns_none_when_series_evaluator_disabled(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    cfg = {"series_evaluator": {"enabled": False}, "strategy": {"min_whale_winrate_pct": 40, "min_resolved_for_whale_filter": 10}}
    assert orch._series_evaluator_rows_for_advisory(cfg) is None


def test_returns_none_when_series_evaluator_key_missing(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    # A config dict missing the section entirely (e.g. an older/partial
    # config) must degrade to "disabled", not raise or default to enabled.
    cfg = {"strategy": {"min_whale_winrate_pct": 40, "min_resolved_for_whale_filter": 10}}
    assert orch._series_evaluator_rows_for_advisory(cfg) is None


def test_returns_real_rows_when_enabled(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    series_evaluator.record_trade_observed("KXTICK")
    cfg = {"series_evaluator": {"enabled": True}, "strategy": {"min_whale_winrate_pct": 40, "min_resolved_for_whale_filter": 10}}
    rows = orch._series_evaluator_rows_for_advisory(cfg)
    assert rows is not None
    assert any(r["series"] == "KXTICK" for r in rows)
