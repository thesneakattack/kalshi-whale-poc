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


def test_trade_sizes_no_side_off_inverted_price(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    # price is always the yes price - a NO signal's real unit cost is
    # (1-price)=0.8/contract here, not 0.2/contract.
    decision = strategy.evaluate(_signal(confidence=0.8, price=0.2, side="no"), _cfg(max_position_pct=0.05))
    assert decision["action"] == "trade"
    expected_contracts = int(10000.0 * 0.05 / 0.8)
    assert decision["trade"]["size"] == expected_contracts
    assert broker.bankroll == 10000.0 - expected_contracts * 0.8


def test_skip_when_halted(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    risk.manual_halt("testing")
    decision = strategy.evaluate(_signal(confidence=0.9), _cfg())
    assert decision["action"] == "skip"
    assert "halted" in decision["reason"]


def test_skip_when_market_already_resolved(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(_signal(confidence=0.9), _cfg(), market_results={"TICK-A": "yes"})
    assert decision["action"] == "skip"
    assert "already resolved" in decision["reason"]
    assert broker.bankroll == 10000.0


def test_trades_when_market_results_has_no_entry_for_this_ticker(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(_signal(confidence=0.9, price=0.5), _cfg(), market_results={"OTHER-TICKER": "yes"})
    assert decision["action"] == "trade"


def test_skip_when_cooldown_active(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    strategy.evaluate(_signal(confidence=0.9, price=0.5), _cfg())
    # Close it first so the "already open" check (evaluated before cooldown)
    # doesn't mask what this test is actually isolating.
    broker.close_position("TICK-A", exit_price=0.5, reason="test")
    decision = strategy.evaluate(_signal(confidence=0.9, price=0.5), _cfg())
    assert decision["action"] == "skip"
    assert "cooldown" in decision["reason"]


def test_skip_when_position_already_open_on_ticker(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    strategy.evaluate(_signal(confidence=0.9, price=0.5), _cfg())
    assert "TICK-A" in broker.positions
    decision = strategy.evaluate(_signal(confidence=0.9, price=0.5), _cfg())
    assert decision["action"] == "skip"
    assert "already open" in decision["reason"]
    assert len(broker.trade_log) == 1  # the second signal never touched the broker


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


# ---- check_exits (active position management) ----------------------------

def _signal_dict(**overrides):
    return _signal(**overrides).to_dict()


def test_check_exits_does_nothing_when_unconfigured(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # Price moved massively in the position's favor, but nothing is configured.
    decisions = strategy.check_exits({"TICK-A": 0.99}, [], _cfg())
    assert decisions == []
    assert "TICK-A" in broker.positions


def test_check_exits_take_profit_closes_position(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # +50% of cost basis: (0.75 - 0.5) * 100 / (0.5 * 100) = 0.5
    decisions = strategy.check_exits({"TICK-A": 0.75}, [], _cfg(take_profit_pct=0.5))
    assert len(decisions) == 1
    assert decisions[0]["action"] == "close"
    assert decisions[0]["ticker"] == "TICK-A"
    assert "take-profit" in decisions[0]["reason"]
    assert "TICK-A" not in broker.positions
    assert broker.bankroll == 1000.0 * 10 - 50 + 75  # started 10000, cost 50, cash back 75


def test_check_exits_take_profit_does_not_trigger_below_threshold(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # only +10% of cost basis, threshold is 50%
    decisions = strategy.check_exits({"TICK-A": 0.55}, [], _cfg(take_profit_pct=0.5))
    assert decisions == []
    assert "TICK-A" in broker.positions


def test_check_exits_stop_loss_closes_position(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # -40% of cost basis: (0.3 - 0.5) * 100 / 50 = -0.4
    decisions = strategy.check_exits({"TICK-A": 0.3}, [], _cfg(stop_loss_pct=0.3))
    assert len(decisions) == 1
    assert "stop-loss" in decisions[0]["reason"]
    assert "TICK-A" not in broker.positions


def test_check_exits_stop_loss_does_not_trigger_above_limit(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # only -10% loss, limit is -30%
    decisions = strategy.check_exits({"TICK-A": 0.45}, [], _cfg(stop_loss_pct=0.3))
    assert decisions == []


def test_check_exits_sentiment_reversal_closes_position(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # Position is YES, but recent signals lean heavily NO.
    feed = [
        _signal_dict(ticker="TICK-A", side="no", size=8000),
        _signal_dict(ticker="TICK-A", side="no", size=7000),
        _signal_dict(ticker="TICK-A", side="yes", size=1000),
    ]
    decisions = strategy.check_exits(
        {"TICK-A": 0.5}, feed,
        _cfg(exit_on_sentiment_reversal=True, exit_sentiment_min_signals=3, exit_sentiment_lean_pct=65),
    )
    assert len(decisions) == 1
    assert "sentiment reversed" in decisions[0]["reason"]
    assert "TICK-A" not in broker.positions


def test_check_exits_sentiment_reversal_needs_enough_signals(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # Heavily against the position, but only 2 signals when 3 are required.
    feed = [
        _signal_dict(ticker="TICK-A", side="no", size=8000),
        _signal_dict(ticker="TICK-A", side="no", size=7000),
    ]
    decisions = strategy.check_exits(
        {"TICK-A": 0.5}, feed,
        _cfg(exit_on_sentiment_reversal=True, exit_sentiment_min_signals=3, exit_sentiment_lean_pct=65),
    )
    assert decisions == []


def test_check_exits_sentiment_reversal_needs_strong_enough_lean(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # Roughly balanced, well under the 65% reversal bar.
    feed = [
        _signal_dict(ticker="TICK-A", side="no", size=5100),
        _signal_dict(ticker="TICK-A", side="yes", size=4900),
        _signal_dict(ticker="TICK-A", side="no", size=100),
    ]
    decisions = strategy.check_exits(
        {"TICK-A": 0.5}, feed,
        _cfg(exit_on_sentiment_reversal=True, exit_sentiment_min_signals=3, exit_sentiment_lean_pct=65),
    )
    assert decisions == []


def test_check_exits_sentiment_reversal_off_by_default(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    feed = [_signal_dict(ticker="TICK-A", side="no", size=9000) for _ in range(5)]
    decisions = strategy.check_exits({"TICK-A": 0.5}, feed, _cfg())  # exit_on_sentiment_reversal not set
    assert decisions == []


def test_check_exits_handles_multiple_positions_independently(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")  # will hit take-profit
    broker.open_position("TICK-B", "yes", size=100, price=0.5, reason="entry")  # stays flat, shouldn't close
    decisions = strategy.check_exits(
        {"TICK-A": 0.75, "TICK-B": 0.5}, [], _cfg(take_profit_pct=0.5),
    )
    assert len(decisions) == 1
    assert decisions[0]["ticker"] == "TICK-A"
    assert "TICK-A" not in broker.positions
    assert "TICK-B" in broker.positions


def test_check_exits_no_side_position_uses_correct_direction(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "no", size=100, price=0.4, reason="entry")  # cost = 100*(1-0.4) = 60
    # yes price drops 0.4 -> 0.1: NO side gains. pnl = (0.4-0.1)*100 = 30, pnl_pct = 30/60 = 0.5
    decisions = strategy.check_exits({"TICK-A": 0.1}, [], _cfg(take_profit_pct=0.5))
    assert len(decisions) == 1
    assert "TICK-A" not in broker.positions


# ---- check_exits: auto_exit (automated multi-factor exit confidence) -----

def test_check_exits_auto_exit_off_by_default(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # +30% unrealized gain - well past what auto_exit's default gain
    # reference would score as full pressure - but auto_exit_enabled isn't set.
    decisions = strategy.check_exits({"TICK-A": 0.65}, [], _cfg())
    assert decisions == []
    assert "TICK-A" in broker.positions


def test_check_exits_auto_exit_pnl_gain_triggers_at_threshold(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # pnl_pct = (0.65-0.5)*100 / 50 = 0.3; default gain reference 0.5 ->
    # pnl_factor = 0.3/0.5 = 0.6. No signal_feed at all, so sentiment/
    # staleness factors are entirely absent (not zero) from the average -
    # confidence is exactly the pnl factor, 0.6, which meets the default
    # 0.6 threshold. If a missing factor were wrongly scored as 0 instead
    # of omitted, this would average down to 0.24 and never trigger -
    # this is the case that proves it isn't.
    decisions = strategy.check_exits({"TICK-A": 0.65}, [], _cfg(auto_exit_enabled=True))
    assert len(decisions) == 1
    assert decisions[0]["action"] == "close"
    assert "auto-exit" in decisions[0]["reason"]
    assert "TICK-A" not in broker.positions


def test_check_exits_auto_exit_pnl_gain_below_threshold_no_trigger(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # pnl_pct = 0.2 -> pnl_factor = 0.2/0.5 = 0.4, below the 0.6 threshold.
    decisions = strategy.check_exits({"TICK-A": 0.6}, [], _cfg(auto_exit_enabled=True))
    assert decisions == []
    assert "TICK-A" in broker.positions


def test_check_exits_auto_exit_pnl_loss_triggers(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # pnl_pct = -0.3; default loss reference is 0.3 -> pnl_factor = 1.0.
    decisions = strategy.check_exits({"TICK-A": 0.35}, [], _cfg(auto_exit_enabled=True))
    assert len(decisions) == 1
    assert "auto-exit" in decisions[0]["reason"]
    assert "TICK-A" not in broker.positions


def test_check_exits_auto_exit_pnl_alone_insufficient_with_sentiment_present(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # pnl_factor = 0.1/0.5 = 0.2. Whale flow on this ticker has fully
    # reversed against the held side (sentiment_factor = 1.0), but its
    # weight is zeroed here to isolate: pnl alone shouldn't be enough.
    feed = [_signal_dict(ticker="TICK-A", side="no", size=1000) for _ in range(3)]
    decisions = strategy.check_exits(
        {"TICK-A": 0.55}, feed,
        _cfg(auto_exit_enabled=True, auto_exit_sentiment_weight=0, auto_exit_staleness_weight=0),
    )
    assert decisions == []


def test_check_exits_auto_exit_sentiment_factor_contributes_to_confidence(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # Same pnl_factor (0.2) as the test above - this time sentiment's
    # default weight (1.0) is left on. confidence = (0.2*1 + 1.0*1)/2 = 0.6,
    # crossing the default threshold where pnl alone (0.2) did not.
    feed = [_signal_dict(ticker="TICK-A", side="no", size=1000) for _ in range(3)]
    decisions = strategy.check_exits(
        {"TICK-A": 0.55}, feed,
        _cfg(auto_exit_enabled=True, auto_exit_staleness_weight=0),
    )
    assert len(decisions) == 1
    assert "sentiment=100%" in decisions[0]["reason"]


def test_check_exits_auto_exit_staleness_factor_triggers_close(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # Price hasn't moved (pnl_factor weighted to 0) - the only thing arguing
    # for closing is that the last whale print on this ticker was an hour
    # ago, twice the default 1800s (30min) stale_after window, so
    # staleness_factor caps at 1.0.
    feed = [_signal_dict(ticker="TICK-A", side="yes", size=1000, timestamp=time.time() - 3600)]
    decisions = strategy.check_exits(
        {"TICK-A": 0.5}, feed,
        _cfg(auto_exit_enabled=True, auto_exit_pnl_weight=0, auto_exit_sentiment_weight=0),
    )
    assert len(decisions) == 1
    assert "staleness=100%" in decisions[0]["reason"]


def test_check_exits_auto_exit_yields_to_hard_triggers_when_both_configured(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # Both take_profit_pct and auto_exit_enabled would independently close
    # this position - take_profit_pct is a hard rail checked first, so the
    # reason should be the explicit take-profit one, not auto-exit's.
    decisions = strategy.check_exits(
        {"TICK-A": 0.75}, [], _cfg(take_profit_pct=0.1, auto_exit_enabled=True),
    )
    assert len(decisions) == 1
    assert "take-profit" in decisions[0]["reason"]
    assert "auto-exit" not in decisions[0]["reason"]


# ---- check_exits: market settlement (unconditional, not opt-in) ----------

def test_check_exits_settles_winning_position_at_full_dollar(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")  # cost 50, bankroll -> 9950
    decisions = strategy.check_exits({"TICK-A": 0.5}, [], _cfg(), market_results={"TICK-A": "yes"})
    assert len(decisions) == 1
    assert "settled YES" in decisions[0]["reason"]
    assert "won" in decisions[0]["reason"]
    assert "TICK-A" not in broker.positions
    assert broker.bankroll == 9950.0 + 100.0  # full $1/contract payout, not the stale 0.5 latest_price


def test_check_exits_settles_losing_position_at_zero(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")  # cost 50, bankroll -> 9950
    decisions = strategy.check_exits({"TICK-A": 0.5}, [], _cfg(), market_results={"TICK-A": "no"})
    assert len(decisions) == 1
    assert "settled NO" in decisions[0]["reason"]
    assert "lost" in decisions[0]["reason"]
    assert "TICK-A" not in broker.positions
    assert broker.bankroll == 9950.0  # nothing paid back


def test_check_exits_settles_no_side_position_correctly(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "no", size=100, price=0.4, reason="entry")  # cost 100*(1-0.4)=60, bankroll -> 9940
    decisions = strategy.check_exits({"TICK-A": 0.4}, [], _cfg(), market_results={"TICK-A": "no"})
    assert len(decisions) == 1
    assert "won" in decisions[0]["reason"]
    assert broker.bankroll == 9940.0 + 100.0


def test_check_exits_ignores_unresolved_market_results(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    for result in ("", None, "unknown"):
        decisions = strategy.check_exits({"TICK-A": 0.5}, [], _cfg(), market_results={"TICK-A": result})
        assert decisions == []
    assert "TICK-A" in broker.positions


def test_check_exits_settlement_takes_priority_over_opt_in_triggers(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # take_profit_pct would also fire on this price move, but the market has
    # actually settled - settlement should win, not the opt-in take-profit path.
    decisions = strategy.check_exits(
        {"TICK-A": 0.75}, [], _cfg(take_profit_pct=0.1), market_results={"TICK-A": "yes"},
    )
    assert len(decisions) == 1
    assert "settled YES" in decisions[0]["reason"]
    assert "take-profit" not in decisions[0]["reason"]


def test_check_exits_settlement_defaults_to_no_market_results(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    broker.open_position("TICK-A", "yes", size=100, price=0.5, reason="entry")
    # No market_results arg at all - existing call sites/tests shouldn't break.
    decisions = strategy.check_exits({"TICK-A": 0.5}, [], _cfg())
    assert decisions == []
    assert "TICK-A" in broker.positions


# --- config_fingerprint passthrough (docs/advisory-engine-plan.md) ---------

def test_evaluate_threads_config_fingerprint_into_open_position(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(_signal(confidence=0.8, price=0.5), _cfg(), config_fingerprint="fp-abc")
    assert decision["action"] == "trade"
    assert broker.positions["TICK-A"].config_fingerprint == "fp-abc"
    assert broker.trade_log[0].config_fingerprint == "fp-abc"


def test_evaluate_config_fingerprint_defaults_to_none(tmp_path, monkeypatch):
    strategy, broker, risk = _strategy(tmp_path, monkeypatch)
    decision = strategy.evaluate(_signal(confidence=0.8, price=0.5), _cfg())
    assert decision["action"] == "trade"
    assert broker.positions["TICK-A"].config_fingerprint is None
