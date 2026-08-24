"""Tests for services/market_analyst_agent/_db.py - the shared layer
(clear_all()'s cross-table behavior specifically; DB_PATH/_connect() are
otherwise exercised implicitly by every other market_analyst_agent test).
"""
from services import market_analyst_agent as maa
from services.market_analyst_agent import _db as maa_db


def _agent(tmp_path, monkeypatch):
    monkeypatch.setattr(maa_db, "DB_PATH", tmp_path / "market_analyst.db")
    return maa


def test_clear_all_wipes_every_analysis(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    agent.record_analysis("TICK-A", "TICK", 0.5, 0.6, 0.7, "r", "m")
    agent.clear_all()
    assert agent.total_count() == 0


def test_clear_all_wipes_series_analyses_too(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    agent.record_analysis("TICK-A", "TICK", 0.5, 0.6, 0.7, "r", "m")
    agent.record_series_analysis("KXTICK", "s", [], "m")
    agent.clear_all()
    assert agent.total_count() == 0
    assert agent.recent_series_analyses() == []


def test_clear_all_wipes_full_spectrum_analyses_too(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    agent.record_analysis("TICK-A", "TICK", 0.5, 0.6, 0.7, "r", "m")
    agent.record_full_spectrum_analysis("s", [], "m")
    agent.clear_all()
    assert agent.total_count() == 0
    assert agent.recent_full_spectrum_analyses() == []
