import time

from services import paper_broker as pb_module
from services import risk_manager as rm_module
from services import signal_log
from services.strategy_engine import FollowTheWhaleStrategy
from services.whale_simulator import WhaleSignal


def _signal(**overrides):
    defaults = dict(
        id="sig1", ticker="TICK-A", side="yes", size=10000,
        price=0.5, confidence=0.8, timestamp=time.time(),
    )
    defaults.update(overrides)
    return WhaleSignal(**defaults)


def _cfg(**overrides):
    strategy = dict(
        name="follow_the_whale", entry_threshold=0.65, max_position_pct=0.05,
        cooldown_sec=300, min_whale_winrate_pct=40, min_resolved_for_whale_filter=5,
        live_markets_only=False,
    )
    strategy.update(overrides)
    return {"strategy": strategy}


def _strategy(tmp_path, monkeypatch, bankroll=10000.0, kill_switch_enabled=True, max_daily_loss_pct=0.1):
    monkeypatch.setattr(pb_module, "DB_PATH", tmp_path / "paper_broker.db")
    monkeypatch.setattr(rm_module, "DB_PATH", tmp_path / "risk_state.db")
    broker = pb_module.PaperBroker(starting_bankroll=bankroll)
    risk = rm_module.RiskManager(bankroll, max_daily_loss_pct, kill_switch_enabled)
    # Default: no whale-filter opinion at all, so the filter branch is a
    # no-op unless a specific test overrides this.
    monkeypatch.setattr(signal_log, "series_stats", lambda ticker, days=30: {
        "series": ticker.split("-")[0], "window_days": days, "total_signals": 0,
        "resolved": 0, "correct": 0, "win_rate": None,
    })
    return FollowTheWhaleStrategy(broker, risk), broker, risk


def test_skip_below_confidence_threshold(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(_signal(confidence=0.5), _cfg(entry_threshold=0.65))
    assert decision["action"] == "skip"
    assert "confidence" in decision["reason"]
    assert broker.bankroll == 10000.0  # nothing traded


def test_trade_when_conditions_met(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(_signal(confidence=0.8, price=0.5), _cfg())
    assert decision["action"] == "trade"
    assert broker.bankroll < 10000.0
    assert "TICK-A" in broker.positions


def test_skip_when_halted(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    risk.manual_halt("testing")
    decision = strategy.evaluate(_signal(confidence=0.9), _cfg())
    assert decision["action"] == "skip"
    assert "halted" in decision["reason"]


def test_skip_when_cooldown_active(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    strategy.evaluate(_signal(confidence=0.9, price=0.5), _cfg())
    decision = strategy.evaluate(_signal(confidence=0.9, price=0.5), _cfg())
    assert decision["action"] == "skip"
    assert "cooldown" in decision["reason"]


def test_skip_when_whale_winrate_below_minimum(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    monkeypatch.setattr(signal_log, "series_stats", lambda ticker, days=30: {
        "series": "TICK", "window_days": days, "total_signals": 10,
        "resolved": 10, "correct": 2, "win_rate": 20.0,
    })
    decision = strategy.evaluate(
        _signal(confidence=0.9), _cfg(min_whale_winrate_pct=40, min_resolved_for_whale_filter=5)
    )
    assert decision["action"] == "skip"
    assert "win rate" in decision["reason"]


def test_trade_when_not_enough_resolved_to_trust_the_filter(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    monkeypatch.setattr(signal_log, "series_stats", lambda ticker, days=30: {
        "series": "TICK", "window_days": days, "total_signals": 2,
        "resolved": 2, "correct": 0, "win_rate": 0.0,
    })
    # win rate is 0%, but only 2 resolved - below the 5-resolved minimum
    # needed before the filter is trusted, so this should still trade.
    decision = strategy.evaluate(
        _signal(confidence=0.9, price=0.5), _cfg(min_whale_winrate_pct=40, min_resolved_for_whale_filter=5)
    )
    assert decision["action"] == "trade"


def test_skip_when_position_size_rounds_to_zero(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch, bankroll=1.0)
    decision = strategy.evaluate(_signal(confidence=0.9, price=0.99), _cfg(max_position_pct=0.05))
    assert decision["action"] == "skip"
    assert "zero" in decision["reason"]


def test_skip_when_live_markets_only_and_market_not_live(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(
        _signal(confidence=0.9, price=0.5), _cfg(live_markets_only=True), is_live=False
    )
    assert decision["action"] == "skip"
    assert "live" in decision["reason"]
    assert broker.bankroll == 10000.0  # nothing traded


def test_trade_when_live_markets_only_and_market_is_live(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(
        _signal(confidence=0.9, price=0.5), _cfg(live_markets_only=True), is_live=True
    )
    assert decision["action"] == "trade"


def test_live_markets_only_off_trades_regardless_of_is_live(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(
        _signal(confidence=0.9, price=0.5), _cfg(live_markets_only=False), is_live=False
    )
    assert decision["action"] == "trade"


def test_skip_when_series_manually_excluded(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(
        _signal(ticker="TICK-A", confidence=0.9, price=0.5), _cfg(excluded_series=["TICK"]),
    )
    assert decision["action"] == "skip"
    assert "TICK" in decision["reason"] and "excluded" in decision["reason"]
    assert broker.bankroll == 10000.0  # nothing traded


def test_trades_when_series_not_in_excluded_list(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(
        _signal(ticker="TICK-A", confidence=0.9, price=0.5), _cfg(excluded_series=["OTHER"]),
    )
    assert decision["action"] == "trade"


def test_empty_excluded_series_list_trades_normally(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(
        _signal(ticker="TICK-A", confidence=0.9, price=0.5), _cfg(excluded_series=[]),
    )
    assert decision["action"] == "trade"
