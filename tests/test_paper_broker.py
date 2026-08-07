import time

import pytest

from services import paper_broker as pb


def _broker(tmp_path, monkeypatch, starting_bankroll=1000.0):
    monkeypatch.setattr(pb, "DB_PATH", tmp_path / "paper_broker.db")
    return pb.PaperBroker(starting_bankroll=starting_bankroll)


def test_fresh_broker_uses_starting_bankroll(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch, starting_bankroll=1000.0)
    assert broker.bankroll == 1000.0
    assert broker.starting_bankroll == 1000.0
    assert broker.positions == {}
    assert broker.trade_log == []


def test_open_position_deducts_cost_and_logs_trade(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch, starting_bankroll=1000.0)
    trade = broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="test")
    assert broker.bankroll == 950.0
    assert trade.size == 100
    assert trade.price == 0.5
    assert "TICK-A" in broker.positions
    assert broker.positions["TICK-A"].size == 100
    assert len(broker.trade_log) == 1


def test_open_position_cost_capped_at_bankroll(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch, starting_bankroll=10.0)
    # size*price = 1000*0.5 = 500, far more than the $10 available - the fill
    # should shrink to fit rather than overdraw the account.
    trade = broker.open_position("TICK-A", "yes", size=1000, price=0.5, reason="test")
    assert broker.bankroll == 0.0
    assert trade.size == int(10.0 / 0.5)


def test_can_trade_respects_cooldown(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=10, price=0.5, reason="test")
    assert broker.can_trade("TICK-A", cooldown_sec=300) is False
    assert broker.can_trade("TICK-B", cooldown_sec=300) is True  # different ticker, never traded


def test_can_trade_true_after_cooldown_elapses(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=10, price=0.5, reason="test")
    broker.last_trade_time["TICK-A"] = time.time() - 301
    assert broker.can_trade("TICK-A", cooldown_sec=300) is True


def test_mark_to_market_yes_side_profit_and_loss(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="test")
    assert broker.mark_to_market("TICK-A", 0.6) == pytest.approx(10.0)
    assert broker.mark_to_market("TICK-A", 0.4) == pytest.approx(-10.0)


def test_mark_to_market_no_side_is_inverted(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "no", size=100, price=0.5, reason="test")
    # a "no" position profits when the price falls, not rises
    assert broker.mark_to_market("TICK-A", 0.4) == pytest.approx(10.0)
    assert broker.mark_to_market("TICK-A", 0.6) == pytest.approx(-10.0)


def test_mark_to_market_no_position_is_zero(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    assert broker.mark_to_market("NOPE", 0.5) == 0.0


def test_equity_combines_bankroll_and_unrealized_pnl(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch, starting_bankroll=1000.0)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="test")
    # bankroll now 950, position worth 100*0.6=60 vs entry cost 50 -> +10 unrealized
    assert broker.equity({"TICK-A": 0.6}) == pytest.approx(960.0)


def test_state_recent_trades_most_recent_first(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=10, price=0.5, reason="first")
    broker.last_trade_time["TICK-A"] = 0  # bypass cooldown just for this test
    broker.open_position("TICK-B", "yes", size=10, price=0.5, reason="second")
    state = broker.state({})
    assert state["recent_trades"][0]["reason"] == "second"
    assert state["recent_trades"][1]["reason"] == "first"


def test_persistence_across_new_instance(tmp_path, monkeypatch):
    db_path = tmp_path / "paper_broker.db"
    monkeypatch.setattr(pb, "DB_PATH", db_path)

    broker = pb.PaperBroker(starting_bankroll=1000.0)
    trade = broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="test")

    # Simulate a real restart: a brand-new instance, same DB_PATH, constructed
    # with a *different* starting_bankroll - the persisted state must win
    # over it, the way main.py always re-passes config's value on startup.
    resumed = pb.PaperBroker(starting_bankroll=999999.0)
    assert resumed.bankroll == broker.bankroll
    assert resumed.starting_bankroll == 1000.0
    assert "TICK-A" in resumed.positions
    assert resumed.positions["TICK-A"].size == trade.size
    assert len(resumed.trade_log) == 1
    assert resumed.trade_log[0].id == trade.id
    assert resumed.can_trade("TICK-A", cooldown_sec=300) is False  # cooldown survived too


def test_reset_wipes_state_and_persists_the_wipe(tmp_path, monkeypatch):
    db_path = tmp_path / "paper_broker.db"
    monkeypatch.setattr(pb, "DB_PATH", db_path)

    broker = pb.PaperBroker(starting_bankroll=1000.0)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="test")
    broker.reset(starting_bankroll=500.0)

    assert broker.bankroll == 500.0
    assert broker.positions == {}
    assert broker.trade_log == []

    # A fresh instance after reset must not resurrect the wiped position.
    resumed = pb.PaperBroker(starting_bankroll=1.0)
    assert resumed.bankroll == 500.0
    assert resumed.positions == {}
    assert resumed.trade_log == []
