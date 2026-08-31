"""Real bug found live (2026-08-24, direct report: "market evaluator is
disabled but it still feeds things to the advisory module") - see
services.analytics.market_analyst_orchestrator._series_evaluator_rows_for_
advisory's own docstring for the incident. These tests cover just that
gating function, not the full _series_evaluator_overview_with_crosscheck
enrichment (already exercised indirectly via services/analytics/routes.py's
GET /api/series-evaluator/status live usage).
"""
import asyncio
import time

from services.analytics import market_analyst_orchestrator as orch
from services import market_analyst_agent, series_evaluator
from services import signal_log as signal_log_module
from services.market_analyst_agent import _db as maa_db_module


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


# ---- _analyze_market_uncached: candlestick_volatility wiring (Task 9) ----
# Same fake-client/DB-isolation shape as tests/test_trading_gate.py's
# _FakeAnalystKalshiClient/_isolate_market_analyst_dbs (that file drives the
# public _run_market_analyst_for_ticker wrapper; these drive
# _analyze_market_uncached directly, one layer in, since that's the exact
# function this task's new code block lives in) - kept local to this file
# rather than imported from test_trading_gate.py so the two test modules
# stay independent.

class _FakeCandlestickAnalystClient:
    """No event_ticker on the fake market, so the get_event bonus-fetch
    branch (already covered elsewhere) doesn't need exercising here too."""

    def __init__(self, market_detail):
        self._market_detail = market_detail

    async def get_market(self, ticker):
        return self._market_detail


def _isolate_market_analyst_dbs(tmp_path, monkeypatch):
    # _analyze_market_uncached's real code path touches both DBs
    # (market_analyst_agent.record_analysis + signal_log.stats() for the
    # snapshot's whale-track-record context) - redirected per-test so
    # nothing here can reach the real, live data/*.db files.
    monkeypatch.setattr(maa_db_module, "DB_PATH", tmp_path / "market_analyst.db")
    monkeypatch.setattr(signal_log_module, "DB_PATH", tmp_path / "signal_log.db")


async def _fake_analyze_market(market_detail, snapshot, model, api_key):
    return {"estimated_probability": 0.6, "confidence": 0.55, "reasoning": "r"}


def test_analyze_market_uncached_threads_candlestick_volatility_into_the_snapshot(tmp_path, monkeypatch):
    _isolate_market_analyst_dbs(tmp_path, monkeypatch)
    monkeypatch.setattr(market_analyst_agent, "analyze_market", _fake_analyze_market)
    monkeypatch.setattr(
        "services.analytics.market_analyst_orchestrator.candlestick_volatility.volatility",
        lambda ticker, lookback_sec, as_of=None: 0.0456,
    )
    captured = {}
    def _fake_build_context_snapshot(**kwargs):
        captured.update(kwargs)
        return {}
    monkeypatch.setattr(
        "services.analytics.market_analyst_orchestrator.ml_feed.build_context_snapshot",
        _fake_build_context_snapshot,
    )
    ticker = "TICK-A"
    cfg = {"advisory": {"enabled": False}, "candlestick_volatility": {"include_in_market_analyst_prompt": True}}
    fake_client = _FakeCandlestickAnalystClient({"title": "T", "yes_bid_dollars": "0.5"})

    result = asyncio.run(orch._analyze_market_uncached(
        fake_client, {"model": "claude-sonnet-5"}, cfg, ticker, time.time(), "fake-key",
    ))

    assert result["ok"] is True
    assert captured["candlestick_volatility"] == {ticker: 0.0456}


def test_analyze_market_uncached_passes_empty_dict_when_no_candlestick_history(tmp_path, monkeypatch):
    _isolate_market_analyst_dbs(tmp_path, monkeypatch)
    monkeypatch.setattr(market_analyst_agent, "analyze_market", _fake_analyze_market)
    monkeypatch.setattr(
        "services.analytics.market_analyst_orchestrator.candlestick_volatility.volatility",
        lambda ticker, lookback_sec, as_of=None: None,
    )
    captured = {}
    def _fake_build_context_snapshot(**kwargs):
        captured.update(kwargs)
        return {}
    monkeypatch.setattr(
        "services.analytics.market_analyst_orchestrator.ml_feed.build_context_snapshot",
        _fake_build_context_snapshot,
    )
    ticker = "TICK-A"
    cfg = {"advisory": {"enabled": False}, "candlestick_volatility": {"include_in_market_analyst_prompt": True}}
    fake_client = _FakeCandlestickAnalystClient({"title": "T", "yes_bid_dollars": "0.5"})

    result = asyncio.run(orch._analyze_market_uncached(
        fake_client, {"model": "claude-sonnet-5"}, cfg, ticker, time.time(), "fake-key",
    ))

    assert result["ok"] is True
    assert captured["candlestick_volatility"] == {}


def test_analyze_market_uncached_skips_candlestick_lookup_when_opt_out_is_false(tmp_path, monkeypatch):
    # cfg["candlestick_volatility"]["include_in_market_analyst_prompt"] = False.
    # Spy on candlestick_volatility.volatility to assert it is NEVER called
    # (not just that the result is discarded) - this is the independent
    # kill switch from this plan's Context/Global Constraints section, and
    # it must actually prevent the read, not merely suppress the output.
    _isolate_market_analyst_dbs(tmp_path, monkeypatch)
    monkeypatch.setattr(market_analyst_agent, "analyze_market", _fake_analyze_market)
    called = []
    monkeypatch.setattr(
        "services.analytics.market_analyst_orchestrator.candlestick_volatility.volatility",
        lambda *a, **k: called.append(1) or 0.05,
    )
    captured = {}
    monkeypatch.setattr(
        "services.analytics.market_analyst_orchestrator.ml_feed.build_context_snapshot",
        lambda **kwargs: captured.update(kwargs) or {},
    )
    ticker = "TICK-A"
    cfg = {"advisory": {"enabled": False}, "candlestick_volatility": {"include_in_market_analyst_prompt": False}}
    fake_client = _FakeCandlestickAnalystClient({"title": "T", "yes_bid_dollars": "0.5"})

    result = asyncio.run(orch._analyze_market_uncached(
        fake_client, {"model": "claude-sonnet-5"}, cfg, ticker, time.time(), "fake-key",
    ))

    assert result["ok"] is True
    assert called == []
    assert captured["candlestick_volatility"] == {}
