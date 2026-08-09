import re
import time

import pytest

from services import market_history as mh
from services import paper_broker as pb_module
from services import risk_manager as rm_module
from services.kalshi_fees import taker_fee
from services.market_strategy import MarketNativeStrategy
from services.trade_analytics import _ENTRY_CONF_RE


def _strategy(tmp_path, monkeypatch, bankroll=10000.0, kill_switch_enabled=True, max_daily_loss_pct=0.1):
    monkeypatch.setattr(pb_module, "DB_PATH", tmp_path / "paper_broker.db")
    monkeypatch.setattr(rm_module, "DB_PATH", tmp_path / "risk_state.db")
    monkeypatch.setattr(mh, "DB_PATH", tmp_path / "market_history.db")
    broker = pb_module.PaperBroker(starting_bankroll=bankroll)
    risk = rm_module.RiskManager(bankroll, max_daily_loss_pct, kill_switch_enabled)
    return MarketNativeStrategy(broker, risk), broker, risk


def _iso(now: float, offset_sec: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + offset_sec))


def _market(ticker="TICK-A", yes_bid=0.5, yes_ask=0.52, volume=1000, now=None, close_in_sec=7200, close_time="_default"):
    now = now if now is not None else time.time()
    return {
        "ticker": ticker,
        "yes_bid_dollars": yes_bid,
        "yes_ask_dollars": yes_ask,
        "volume_24h_fp": volume,
        "close_time": _iso(now, close_in_sec) if close_time == "_default" else close_time,
    }


def _permissive_cfg(**overrides):
    strategy = dict(
        enabled=True, max_position_pct=0.05, cooldown_sec=300,
        min_price=0.15, max_price=0.85, max_spread=0.10, min_volume_24h=100,
        momentum_lookback_sec=1800, min_momentum_delta=0.03, min_seconds_to_close=600,
        entry_confidence_threshold=0.0,  # accept any confidence for these tests
        take_profit_pct=None, stop_loss_pct=None, exit_on_momentum_reversal=False,
    )
    strategy.update(overrides)
    return {"market_strategy": strategy}


def _seed_momentum(tmp_path, ticker, now, from_price, to_price, span_sec=1800):
    mh.record_snapshots([{"ticker": ticker, "yes_price": from_price, "spread": 0.01,
                           "volume_24h": 1000, "time_to_close_sec": 7200}], timestamp=now - span_sec)
    mh.record_snapshots([{"ticker": ticker, "yes_price": to_price, "spread": 0.01,
                           "volume_24h": 1000, "time_to_close_sec": 7200}], timestamp=now)


# --- evaluate_all / _evaluate_one gating -------------------------------------

def test_evaluate_all_returns_nothing_when_disabled(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    now = time.time()
    _seed_momentum(tmp_path, "TICK-A", now, 0.4, 0.6)
    decisions = strategy.evaluate_all([_market(now=now)], now, _permissive_cfg(enabled=False))
    assert decisions == []
    assert broker.positions == {}


def test_evaluate_all_returns_nothing_when_halted(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch, bankroll=1000.0, max_daily_loss_pct=0.1)
    risk.manual_halt("test halt")
    now = time.time()
    _seed_momentum(tmp_path, "TICK-A", now, 0.4, 0.6)
    decisions = strategy.evaluate_all([_market(now=now)], now, _permissive_cfg())
    assert decisions == []


def test_trades_yes_when_momentum_positive(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    now = time.time()
    _seed_momentum(tmp_path, "TICK-A", now, 0.4, 0.6)
    decisions = strategy.evaluate_all([_market(now=now)], now, _permissive_cfg())
    assert len(decisions) == 1
    assert decisions[0]["action"] == "trade"
    assert "TICK-A" in broker.positions
    assert broker.positions["TICK-A"].side == "yes"


def test_trades_no_when_momentum_negative(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    now = time.time()
    _seed_momentum(tmp_path, "TICK-A", now, 0.6, 0.4)
    decisions = strategy.evaluate_all([_market(now=now)], now, _permissive_cfg())
    assert len(decisions) == 1
    assert broker.positions["TICK-A"].side == "no"


def test_reason_string_entry_confidence_parses_with_shared_regex(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    now = time.time()
    _seed_momentum(tmp_path, "TICK-A", now, 0.4, 0.6)
    strategy.evaluate_all([_market(now=now)], now, _permissive_cfg())
    reason = broker.trade_log[0].reason
    match = _ENTRY_CONF_RE.search(reason)
    assert match is not None
    assert 0.0 <= float(match.group(1)) <= 1.0


def test_skips_when_position_already_open(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    now = time.time()
    broker.open_position("TICK-A", "yes", size=10, price=0.5, reason="existing")
    _seed_momentum(tmp_path, "TICK-A", now, 0.4, 0.6)
    decisions = strategy.evaluate_all([_market(now=now)], now, _permissive_cfg())
    assert decisions == []


def test_skips_when_market_already_resolved(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    now = time.time()
    _seed_momentum(tmp_path, "TICK-A", now, 0.4, 0.6)
    decisions = strategy.evaluate_all([_market(now=now)], now, _permissive_cfg(), market_results={"TICK-A": "yes"})
    assert decisions == []


def test_skips_when_cooldown_active(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    now = time.time()
    broker.open_position("TICK-A", "yes", size=10, price=0.5, reason="entry")
    broker.close_position("TICK-A", 0.6, reason="test close")
    _seed_momentum(tmp_path, "TICK-A", now, 0.4, 0.6)
    decisions = strategy.evaluate_all([_market(now=now)], now, _permissive_cfg(cooldown_sec=300))
    assert decisions == []


def test_skips_when_price_outside_band(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    now = time.time()
    _seed_momentum(tmp_path, "TICK-A", now, 0.85, 0.95)  # yes_bid ends up at 0.95, above max_price
    decisions = strategy.evaluate_all(
        [_market(now=now, yes_bid=0.95, yes_ask=0.96)], now, _permissive_cfg(max_price=0.85),
    )
    assert decisions == []


def test_skips_when_spread_too_wide(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    now = time.time()
    _seed_momentum(tmp_path, "TICK-A", now, 0.4, 0.6)
    decisions = strategy.evaluate_all(
        [_market(now=now, yes_bid=0.6, yes_ask=0.75)], now, _permissive_cfg(max_spread=0.05),
    )
    assert decisions == []


def test_skips_when_volume_too_low(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    now = time.time()
    _seed_momentum(tmp_path, "TICK-A", now, 0.4, 0.6)
    decisions = strategy.evaluate_all(
        [_market(now=now, volume=10)], now, _permissive_cfg(min_volume_24h=500),
    )
    assert decisions == []


def test_skips_when_close_time_missing(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    now = time.time()
    _seed_momentum(tmp_path, "TICK-A", now, 0.4, 0.6)
    decisions = strategy.evaluate_all([_market(now=now, close_time=None)], now, _permissive_cfg())
    assert decisions == []


def test_skips_when_too_close_to_settlement(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    now = time.time()
    _seed_momentum(tmp_path, "TICK-A", now, 0.4, 0.6)
    decisions = strategy.evaluate_all(
        [_market(now=now, close_in_sec=120)], now, _permissive_cfg(min_seconds_to_close=3600),
    )
    assert decisions == []


def test_skips_when_no_momentum_data_yet(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    now = time.time()
    # No snapshots recorded at all for this ticker.
    decisions = strategy.evaluate_all([_market(now=now)], now, _permissive_cfg())
    assert decisions == []


def test_skips_when_momentum_below_minimum_delta(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    now = time.time()
    _seed_momentum(tmp_path, "TICK-A", now, 0.50, 0.51)  # delta 0.01, below default min 0.03
    decisions = strategy.evaluate_all([_market(now=now)], now, _permissive_cfg(min_momentum_delta=0.03))
    assert decisions == []


def test_skips_when_confidence_below_threshold(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    now = time.time()
    # Momentum just barely clears the minimum delta -> low momentum_factor,
    # combined with a high required confidence threshold, should skip.
    _seed_momentum(tmp_path, "TICK-A", now, 0.50, 0.531)
    decisions = strategy.evaluate_all(
        [_market(now=now)], now, _permissive_cfg(min_momentum_delta=0.03, entry_confidence_threshold=0.99),
    )
    assert decisions == []


def test_position_sized_from_max_position_pct(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch, bankroll=10000.0)
    now = time.time()
    _seed_momentum(tmp_path, "TICK-A", now, 0.4, 0.6)
    strategy.evaluate_all([_market(now=now, yes_bid=0.6, yes_ask=0.61)], now, _permissive_cfg(max_position_pct=0.05))
    expected_contracts = int(10000.0 * 0.05 / 0.6)
    assert broker.positions["TICK-A"].size == expected_contracts


# --- check_exits --------------------------------------------------------------

def test_check_exits_does_nothing_when_unconfigured(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    now = time.time()
    decisions = strategy.check_exits({"TICK-A": _market(now=now)}, now, _permissive_cfg())
    assert decisions == []
    assert "TICK-A" in broker.positions


def test_check_exits_settles_position_correctly(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "no", size=100, price=0.4, reason="entry")  # cost 100*(1-0.4)=60
    now = time.time()
    decisions = strategy.check_exits({}, now, _permissive_cfg(), market_results={"TICK-A": "no"})
    assert len(decisions) == 1
    assert "TICK-A" not in broker.positions
    # no position won -> cash back 100 * 1.0 (terminal no-side payout) = 100.
    # Settlement's terminal price (0.0/1.0) means zero close-leg fee (see
    # services/kalshi_fees.py) - only the entry leg's real fee applies here.
    entry_fee = taker_fee(100, 0.4)
    assert broker.bankroll == pytest.approx(10000.0 - 60 + 100 - entry_fee)


def test_check_exits_take_profit_closes_position(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    now = time.time()
    market = _market(ticker="TICK-A", yes_bid=0.75, now=now)
    decisions = strategy.check_exits({"TICK-A": market}, now, _permissive_cfg(take_profit_pct=0.2))
    assert len(decisions) == 1
    assert "take-profit" in decisions[0]["reason"]
    assert "TICK-A" not in broker.positions


def test_check_exits_stop_loss_closes_position(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    now = time.time()
    market = _market(ticker="TICK-A", yes_bid=0.3, now=now)
    decisions = strategy.check_exits({"TICK-A": market}, now, _permissive_cfg(stop_loss_pct=0.2))
    assert len(decisions) == 1
    assert "stop-loss" in decisions[0]["reason"]


def test_check_exits_momentum_reversal_off_by_default(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    now = time.time()
    _seed_momentum(tmp_path, "TICK-A", now, 0.6, 0.4)  # momentum now favors "no"
    decisions = strategy.check_exits({"TICK-A": _market(ticker="TICK-A", now=now)}, now, _permissive_cfg())
    assert decisions == []


def test_check_exits_momentum_reversal_closes_when_enabled(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    now = time.time()
    _seed_momentum(tmp_path, "TICK-A", now, 0.6, 0.4)  # momentum now favors "no" - opposite of held "yes"
    decisions = strategy.check_exits(
        {"TICK-A": _market(ticker="TICK-A", now=now)}, now,
        _permissive_cfg(exit_on_momentum_reversal=True),
    )
    assert len(decisions) == 1
    assert "momentum reversed" in decisions[0]["reason"]


def test_check_exits_momentum_reversal_no_close_when_same_direction(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    now = time.time()
    _seed_momentum(tmp_path, "TICK-A", now, 0.4, 0.6)  # momentum still favors "yes" - held side
    decisions = strategy.check_exits(
        {"TICK-A": _market(ticker="TICK-A", now=now)}, now,
        _permissive_cfg(exit_on_momentum_reversal=True),
    )
    assert decisions == []
