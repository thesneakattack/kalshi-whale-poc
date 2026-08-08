import time

from services import shadow_mode as sm


def _trader(tmp_path, monkeypatch, default_bankroll=10000.0):
    monkeypatch.setattr(sm, "DB_PATH", tmp_path / "shadow_mode.db")
    return sm.ShadowTrader(default_bankroll=default_bankroll)


def _signal(**overrides):
    class _Sig:
        pass
    defaults = dict(id="sig1", ticker="TICK-A", side="yes", size=10000, price=0.5, confidence=0.8, timestamp=time.time())
    defaults.update(overrides)
    s = _Sig()
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


def _cfg(**overrides):
    strategy = dict(
        entry_threshold=0.65, max_position_pct=0.05, cooldown_sec=300,
        min_whale_winrate_pct=40, min_resolved_for_whale_filter=5,
    )
    strategy.update(overrides.pop("strategy", {}))
    risk = dict(max_daily_loss_pct=0.1, kill_switch_enabled=True)
    risk.update(overrides.pop("risk", {}))
    return {"strategy": strategy, "risk": risk}


def _no_opinion_series_stats(monkeypatch):
    monkeypatch.setattr(sm.signal_log, "series_stats", lambda ticker, days=30: {
        "series": ticker.split("-")[0], "resolved": 0, "correct": 0, "win_rate": None,
    })


def test_fresh_trader_seeds_day_start_from_default_bankroll(tmp_path, monkeypatch):
    trader = _trader(tmp_path, monkeypatch, default_bankroll=10000.0)
    assert trader.day_start_bankroll == 10000.0
    assert trader.halted is False


def test_logs_an_intended_trade_when_conditions_met(tmp_path, monkeypatch):
    trader = _trader(tmp_path, monkeypatch)
    _no_opinion_series_stats(monkeypatch)
    row = trader.evaluate(_signal(confidence=0.8, price=0.5), _cfg(), reference_bankroll=10000.0, bankroll_source="real_account")
    assert row is not None
    assert row["ticker"] == "TICK-A"
    assert row["bankroll_source"] == "real_account"
    assert row["reference_bankroll"] == 10000.0
    assert row["size"] == int(10000.0 * 0.05 / 0.5)
    assert trader.stats()["total_shadow_trades"] == 1
    assert trader.recent(10)[0]["id"] == row["id"]


def test_does_not_log_below_confidence_threshold(tmp_path, monkeypatch):
    trader = _trader(tmp_path, monkeypatch)
    _no_opinion_series_stats(monkeypatch)
    row = trader.evaluate(_signal(confidence=0.5), _cfg(entry_threshold=0.65), reference_bankroll=10000.0, bankroll_source="real_account")
    assert row is None
    assert trader.stats()["total_shadow_trades"] == 0


def test_does_not_log_when_halted(tmp_path, monkeypatch):
    trader = _trader(tmp_path, monkeypatch)
    _no_opinion_series_stats(monkeypatch)
    trader.halted = True
    trader.halt_reason = "testing"
    row = trader.evaluate(_signal(confidence=0.9), _cfg(), reference_bankroll=10000.0, bankroll_source="real_account")
    assert row is None


def test_daily_loss_trips_the_shadow_kill_switch(tmp_path, monkeypatch):
    trader = _trader(tmp_path, monkeypatch, default_bankroll=10000.0)
    _no_opinion_series_stats(monkeypatch)
    # reference bankroll is down 11% from the 10000 day-start baseline
    row = trader.evaluate(_signal(confidence=0.9), _cfg(risk={"max_daily_loss_pct": 0.1}), reference_bankroll=8900.0, bankroll_source="real_account")
    assert row is None
    assert trader.halted is True
    assert "11.0%" in trader.halt_reason


def test_cooldown_prevents_immediate_relog_on_same_ticker(tmp_path, monkeypatch):
    trader = _trader(tmp_path, monkeypatch)
    _no_opinion_series_stats(monkeypatch)
    cfg = _cfg(cooldown_sec=300)
    first = trader.evaluate(_signal(confidence=0.9, price=0.5), cfg, reference_bankroll=10000.0, bankroll_source="real_account")
    second = trader.evaluate(_signal(confidence=0.9, price=0.5), cfg, reference_bankroll=10000.0, bankroll_source="real_account")
    assert first is not None
    assert second is None
    assert trader.stats()["total_shadow_trades"] == 1


def test_whale_winrate_filter_blocks_a_bad_series(tmp_path, monkeypatch):
    trader = _trader(tmp_path, monkeypatch)
    monkeypatch.setattr(sm.signal_log, "series_stats", lambda ticker, days=30: {
        "series": "TICK", "resolved": 10, "correct": 2, "win_rate": 20.0,
    })
    row = trader.evaluate(
        _signal(confidence=0.9), _cfg(min_whale_winrate_pct=40, min_resolved_for_whale_filter=5),
        reference_bankroll=10000.0, bankroll_source="real_account",
    )
    assert row is None


def test_live_markets_only_blocks_a_non_live_market(tmp_path, monkeypatch):
    trader = _trader(tmp_path, monkeypatch)
    _no_opinion_series_stats(monkeypatch)
    row = trader.evaluate(
        _signal(confidence=0.9, price=0.5), _cfg(strategy={"live_markets_only": True}),
        reference_bankroll=10000.0, bankroll_source="real_account", is_live=False,
    )
    assert row is None


def test_live_markets_only_allows_a_live_market(tmp_path, monkeypatch):
    trader = _trader(tmp_path, monkeypatch)
    _no_opinion_series_stats(monkeypatch)
    row = trader.evaluate(
        _signal(confidence=0.9, price=0.5), _cfg(strategy={"live_markets_only": True}),
        reference_bankroll=10000.0, bankroll_source="real_account", is_live=True,
    )
    assert row is not None


def test_position_size_rounding_to_zero_is_not_logged(tmp_path, monkeypatch):
    trader = _trader(tmp_path, monkeypatch)
    _no_opinion_series_stats(monkeypatch)
    row = trader.evaluate(
        _signal(confidence=0.9, price=0.99), _cfg(max_position_pct=0.05),
        reference_bankroll=1.0, bankroll_source="real_account",
    )
    assert row is None


def test_persistence_across_restart_keeps_halt_and_baseline(tmp_path, monkeypatch):
    db_path = tmp_path / "shadow_mode.db"
    monkeypatch.setattr(sm, "DB_PATH", db_path)
    trader = sm.ShadowTrader(default_bankroll=10000.0)
    trader.halted = True
    trader.halt_reason = "manually set for test"
    trader._persist_risk()

    resumed = sm.ShadowTrader(default_bankroll=999999.0)  # different default - persisted state must win
    assert resumed.halted is True
    assert resumed.halt_reason == "manually set for test"
    assert resumed.day_start_bankroll == 10000.0


def test_reset_day_clears_halt(tmp_path, monkeypatch):
    trader = _trader(tmp_path, monkeypatch, default_bankroll=10000.0)
    trader.halted = True
    trader.halt_reason = "testing"
    trader.reset_day(7000.0)
    assert trader.halted is False
    assert trader.day_start_bankroll == 7000.0


def test_clear_wipes_trades_and_resets_baseline(tmp_path, monkeypatch):
    trader = _trader(tmp_path, monkeypatch, default_bankroll=10000.0)
    _no_opinion_series_stats(monkeypatch)
    trader.evaluate(_signal(confidence=0.9, price=0.5), _cfg(), reference_bankroll=10000.0, bankroll_source="real_account")
    assert trader.stats()["total_shadow_trades"] == 1

    trader.halted = True
    trader.halt_reason = "testing"
    trader.clear(7000.0)

    assert trader.stats()["total_shadow_trades"] == 0
    assert trader.recent(10) == []
    assert trader.halted is False
    assert trader.day_start_bankroll == 7000.0

    # a fresh instance re-reading the same DB should see the wipe too, not
    # just this in-memory trader
    resumed = sm.ShadowTrader(default_bankroll=999999.0)
    assert resumed.stats()["total_shadow_trades"] == 0
    assert resumed.day_start_bankroll == 7000.0
