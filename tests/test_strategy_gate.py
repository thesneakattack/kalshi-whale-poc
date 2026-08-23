import time

from services import paper_broker as pb_mod
from services import risk_manager as rm_mod
from services.confidence_scoring import WhaleSignal
from services.strategy_engine import FollowTheWhaleStrategy


def _make_broker_and_risk(tmp_path, monkeypatch):
    monkeypatch.setattr(pb_mod, "DB_PATH", tmp_path / "paper_broker.db")
    monkeypatch.setattr(rm_mod, "DB_PATH", tmp_path / "risk_state.db")
    broker = pb_mod.PaperBroker(starting_bankroll=10000.0)
    risk = rm_mod.RiskManager(starting_bankroll=10000.0, max_daily_loss_pct=0.5, kill_switch_enabled=False)
    return broker, risk


def test_special_market_gate_blocks_when_not_live(tmp_path, monkeypatch):
    broker, risk = _make_broker_and_risk(tmp_path, monkeypatch)
    strat = FollowTheWhaleStrategy(broker, risk)

    # Prepare a signal that closes soon
    close_ts = time.time() + 60  # 1 minute
    sig = WhaleSignal(id="s1", ticker="TICK-1", side="yes", size=100, price=0.5, confidence=0.99, timestamp=time.time(), close_time=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(close_ts)))

    # market entry with can_close_early True, passed directly (evaluate()'s
    # special-market gate takes these as explicit params, not main.state).
    markets = [{"ticker": "TICK-1", "can_close_early": True}]
    market_titles = {"TICK-1": {"event_ticker": "EVT1"}}
    event_titles = {"EVT1": {"collateral_return_type": None, "mutually_exclusive": False}}

    cfg = {"strategy": {"entry_threshold": 0.0, "special_market_min_seconds_to_close": 300}}

    res = strat.evaluate(
        sig, cfg, is_live=False, market_results={}, latest_prices={},
        markets=markets, market_titles=market_titles, event_titles=event_titles,
    )
    assert res["action"] == "skip"
    assert "special settlement" in res["reason"] or "Early-close" or "special" in res["reason"]


def test_special_market_allows_when_live(tmp_path, monkeypatch):
    broker, risk = _make_broker_and_risk(tmp_path, monkeypatch)
    strat = FollowTheWhaleStrategy(broker, risk)

    close_ts = time.time() + 60
    sig = WhaleSignal(id="s2", ticker="TICK-2", side="yes", size=10, price=0.5, confidence=1.0, timestamp=time.time(), close_time=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(close_ts)))

    # mark as special but also live
    markets = [{"ticker": "TICK-2", "can_close_early": True}]
    market_titles = {"TICK-2": {"event_ticker": "EVT2"}}
    event_titles = {"EVT2": {"collateral_return_type": "MECNET", "mutually_exclusive": True}}

    cfg = {"strategy": {"entry_threshold": 0.0, "special_market_min_seconds_to_close": 300, "kelly_fraction_of_cap": 0.0, "max_position_pct": 0.1, "cooldown_sec": 0}}

    res = strat.evaluate(
        sig, cfg, is_live=True, market_results={}, latest_prices={},
        markets=markets, market_titles=market_titles, event_titles=event_titles,
    )
    assert res["action"] in ("trade",)
