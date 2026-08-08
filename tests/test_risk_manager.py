from services import risk_manager as rm


def _risk(tmp_path, monkeypatch, starting_bankroll=1000.0, max_daily_loss_pct=0.1, kill_switch_enabled=True):
    monkeypatch.setattr(rm, "DB_PATH", tmp_path / "risk_state.db")
    return rm.RiskManager(starting_bankroll, max_daily_loss_pct, kill_switch_enabled)


def test_fresh_risk_manager_seeds_day_start_from_starting_bankroll(tmp_path, monkeypatch):
    risk = _risk(tmp_path, monkeypatch, starting_bankroll=1000.0)
    assert risk.day_start_bankroll == 1000.0
    assert risk.halted is False
    assert risk.halt_reason is None


def test_check_daily_loss_within_limit_keeps_trading(tmp_path, monkeypatch):
    risk = _risk(tmp_path, monkeypatch, starting_bankroll=1000.0, max_daily_loss_pct=0.1)
    assert risk.check_daily_loss(950.0) is True  # only 5% down
    assert risk.halted is False


def test_check_daily_loss_trips_kill_switch_past_threshold(tmp_path, monkeypatch):
    risk = _risk(tmp_path, monkeypatch, starting_bankroll=1000.0, max_daily_loss_pct=0.1)
    assert risk.check_daily_loss(890.0) is False  # 11% down, past the 10% cap
    assert risk.halted is True
    assert "11.0%" in risk.halt_reason


def test_check_daily_loss_stays_halted_once_tripped(tmp_path, monkeypatch):
    risk = _risk(tmp_path, monkeypatch, starting_bankroll=1000.0, max_daily_loss_pct=0.1)
    risk.check_daily_loss(890.0)
    assert risk.halted is True
    # even if bankroll recovers, stays halted until an explicit resume
    assert risk.check_daily_loss(1000.0) is False


def test_kill_switch_disabled_never_halts(tmp_path, monkeypatch):
    risk = _risk(tmp_path, monkeypatch, starting_bankroll=1000.0, max_daily_loss_pct=0.1, kill_switch_enabled=False)
    assert risk.check_daily_loss(1.0) is True  # 99.9% down, but the switch is off
    assert risk.halted is False


def test_manual_halt_and_resume(tmp_path, monkeypatch):
    risk = _risk(tmp_path, monkeypatch)
    risk.manual_halt("testing")
    assert risk.halted is True
    assert risk.halt_reason == "testing"
    risk.resume()
    assert risk.halted is False
    assert risk.halt_reason is None


def test_reset_day_clears_halt_and_rebases_baseline(tmp_path, monkeypatch):
    risk = _risk(tmp_path, monkeypatch, starting_bankroll=1000.0, max_daily_loss_pct=0.1)
    risk.check_daily_loss(890.0)
    assert risk.halted is True
    risk.reset_day(700.0)
    assert risk.halted is False
    assert risk.day_start_bankroll == 700.0


def test_max_trade_size(tmp_path, monkeypatch):
    risk = _risk(tmp_path, monkeypatch)
    assert risk.max_trade_size(1000.0, 0.05) == 50.0


def test_persistence_across_restart_keeps_halt_and_baseline(tmp_path, monkeypatch):
    db_path = tmp_path / "risk_state.db"
    monkeypatch.setattr(rm, "DB_PATH", db_path)

    risk = rm.RiskManager(starting_bankroll=1000.0, max_daily_loss_pct=0.1, kill_switch_enabled=True)
    risk.check_daily_loss(890.0)
    assert risk.halted is True

    # Simulate a restart with a *different* configured starting_bankroll, the
    # way main.py always re-passes config/settings.yaml's value on startup -
    # persisted state must win, same guarantee as paper_broker's.
    resumed = rm.RiskManager(starting_bankroll=999999.0, max_daily_loss_pct=0.1, kill_switch_enabled=True)
    assert resumed.halted is True
    assert resumed.halt_reason == risk.halt_reason
    assert resumed.day_start_bankroll == 1000.0


def test_persistence_across_restart_without_halt(tmp_path, monkeypatch):
    db_path = tmp_path / "risk_state.db"
    monkeypatch.setattr(rm, "DB_PATH", db_path)

    rm.RiskManager(starting_bankroll=1000.0, max_daily_loss_pct=0.1, kill_switch_enabled=True)
    resumed = rm.RiskManager(starting_bankroll=1.0, max_daily_loss_pct=0.1, kill_switch_enabled=True)
    assert resumed.halted is False
    assert resumed.day_start_bankroll == 1000.0


# --- per-instance db_path (services/market_strategy.py's own kill switch) ---

def test_explicit_db_path_overrides_module_default(tmp_path, monkeypatch):
    monkeypatch.setattr(rm, "DB_PATH", tmp_path / "should-not-be-used" / "risk_state.db")
    explicit_path = tmp_path / "explicit" / "market_risk_state.db"
    risk = rm.RiskManager(starting_bankroll=500.0, max_daily_loss_pct=0.1, kill_switch_enabled=True, db_path=explicit_path)
    risk.manual_halt("test")
    assert explicit_path.exists()
    assert not (tmp_path / "should-not-be-used").exists()


def test_two_risk_manager_instances_with_different_db_paths_do_not_collide(tmp_path, monkeypatch):
    monkeypatch.setattr(rm, "DB_PATH", tmp_path / "risk_a.db")
    risk_a = rm.RiskManager(starting_bankroll=1000.0, max_daily_loss_pct=0.1, kill_switch_enabled=True)
    risk_b = rm.RiskManager(
        starting_bankroll=5000.0, max_daily_loss_pct=0.1, kill_switch_enabled=True, db_path=tmp_path / "risk_b.db",
    )
    risk_a.manual_halt("halted a")

    assert risk_a.halted is True
    assert risk_b.halted is False

    resumed_a = rm.RiskManager(starting_bankroll=999999.0, max_daily_loss_pct=0.1, kill_switch_enabled=True)
    resumed_b = rm.RiskManager(
        starting_bankroll=999999.0, max_daily_loss_pct=0.1, kill_switch_enabled=True, db_path=tmp_path / "risk_b.db",
    )
    assert resumed_a.halted is True
    assert resumed_b.halted is False
