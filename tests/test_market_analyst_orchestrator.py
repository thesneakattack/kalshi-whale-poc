"""Real bug found live (2026-08-24, direct report: "market evaluator is
disabled but it still feeds things to the advisory module") - see
services.analytics.market_analyst_orchestrator._series_evaluator_rows_for_
advisory's own docstring for the incident. These tests cover just that
gating function, not the full _series_evaluator_overview_with_crosscheck
enrichment (already exercised indirectly via services/analytics/routes.py's
GET /api/series-evaluator/status live usage).
"""
import asyncio

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


class _FakeClient:
    """Minimal KalshiPublicGateway stand-in for _analyze_market_uncached's
    two await points below the generate_recommendations call this test
    cares about - no event_ticker, so the optional get_event() enrichment
    branch is skipped entirely."""

    async def get_market(self, ticker):
        return {"event_ticker": None, "yes_bid_dollars": 0.5}


_ANALYZE_CFG = {
    "advisory": {
        "enabled": True,
        # Read via adv_cfg["min_resolved_trades_per_variant"] (bracket
        # indexing) before generate_recommendations is ever called -
        # omitting it raises KeyError before the mock runs (same shape as
        # test_main_scheduler_loops.py's _ADVISORY_CFG comment).
        "min_resolved_trades_per_variant": 10,
    },
}


def _wire_analyze_market_uncached_collaborators(monkeypatch, declined: set[str]):
    """Task 3a's own disclosed placeholder, filled in from
    _analyze_market_uncached's real collaborator list (services/analytics/
    market_analyst_orchestrator.py:66-121, read fresh before writing this):
    trade_analytics.build_trade_history, generate_recommendations itself,
    suggestion_decisions.declined_ids, and every other collaborator
    generate_recommendations' OWN arguments are built from
    (candidate_log.gate_summary, config_performance.*,
    _series_evaluator_overview_with_crosscheck, regime_analytics.by_category)
    - all evaluated eagerly as call arguments even though
    generate_recommendations is mocked. ml_feed.build_context_snapshot and
    market_analyst_agent.analyze_market are mocked too, both reached AFTER
    the call under test, purely so this test doesn't also depend on their
    own unrelated collaborators (broker.state/signal_log.stats, which run
    fine against this suite's isolated per-test DBs, but aren't this test's
    concern)."""
    calls = []
    monkeypatch.setattr(orch.trade_analytics, "build_trade_history", lambda rows: [])
    monkeypatch.setattr(orch.advisory_engine, "generate_recommendations",
                         lambda *a, **k: calls.append(k) or {"recommendations": []})
    monkeypatch.setattr(orch.suggestion_decisions, "declined_ids", lambda: declined)
    monkeypatch.setattr(orch.candidate_log, "gate_summary", lambda: {})
    monkeypatch.setattr(orch.config_performance, "fingerprint", lambda cfg: "fp")
    monkeypatch.setattr(orch.config_performance, "all_variants", lambda: [])
    monkeypatch.setattr(orch.config_performance, "all_last_applied_by_path", lambda: {})
    monkeypatch.setattr(orch, "_series_evaluator_overview_with_crosscheck", lambda cfg: [])
    monkeypatch.setattr(orch.regime_analytics, "by_category", lambda rows: [])
    monkeypatch.setattr(orch.ml_feed, "build_context_snapshot", lambda **kw: {})

    async def _fake_analyze_market(*a, **k):
        return None  # short-circuits _analyze_market_uncached right after the call under test

    monkeypatch.setattr(orch.market_analyst_agent, "analyze_market", _fake_analyze_market)
    return calls


def test_analyze_market_uncached_passes_declined_ids(monkeypatch):
    """Task 3a: the LLM market-analyst's own context snapshot includes
    advisory recommendations (services/ml_feed.py's 'work WITH, not
    replace' framing) - a declined suggestion showing up here can feed
    back into a fresh LLM-generated recommendation that re-proposes the
    same thing a human already said no to."""
    calls = _wire_analyze_market_uncached_collaborators(monkeypatch, {"decl-x"})

    result = asyncio.run(orch._analyze_market_uncached(
        _FakeClient(), {}, _ANALYZE_CFG, "TEST-TICKER", 0.0, "fake-key",
    ))

    assert result == {"ok": False, "reason": "The model call failed or declined to answer — see server logs."}
    assert len(calls) == 1
    assert calls[0].get("declined_ids") == {"decl-x"}


def test_build_full_spectrum_context_passes_declined_ids(tmp_path, monkeypatch):
    """Same fix, same reasoning, the full-spectrum (all-series) LLM
    context builder's own generate_recommendations call
    (services/analytics/market_analyst_orchestrator.py:261-318, a sync
    function with no early-return gate - it runs to completion regardless,
    so the collaborators below generate_recommendations
    (series_evaluator.overview/signal_log.*/config_performance.
    recent_applied_changes/broker.state/regime_analytics.by_hour_of_day)
    are left real against this suite's isolated per-test DBs rather than
    mocked, since they aren't this test's concern and this app's other
    tests already exercise them safely the same way."""
    _isolated(tmp_path, monkeypatch)
    calls = _wire_analyze_market_uncached_collaborators(monkeypatch, {"decl-y"})

    result = orch._build_full_spectrum_context(_ANALYZE_CFG)

    assert result["advisory_recommendations"] == []
    assert len(calls) == 1
    assert calls[0].get("declined_ids") == {"decl-y"}
