import calendar
import time

from services import risk_manager as rm


def _risk(tmp_path, monkeypatch, starting_bankroll=1000.0, max_daily_loss_pct=0.1, kill_switch_enabled=True):
    monkeypatch.setattr(rm, "DB_PATH", tmp_path / "risk_state.db")
    return rm.RiskManager(starting_bankroll, max_daily_loss_pct, kill_switch_enabled)


def _noon_of_seeded_day(manager) -> float:
    """A `now` guaranteed to fall on the UTC calendar date the manager itself
    seeded day_start_date from, so `now + 60` is provably the same day and
    `now + 86400` provably the next one. Sampling time.time() in the test
    instead raced the manager's own wall-clock seed: Woodpecker pipeline 135
    (2026-08-25) took day1 at 23:59:57Z, so day1 + 60 was a real UTC date
    rollover and the "same day" assertion failed on a docs-only push."""
    return calendar.timegm(time.strptime(manager.day_start_date, "%Y-%m-%d")) + 12 * 3600


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


# --- automatic daily rollover -----------------------------------------------

def test_check_daily_loss_stays_halted_within_the_same_day(tmp_path, monkeypatch):
    # day1 must land on the *same* UTC calendar date the freshly-constructed
    # RiskManager itself seeded day_start_date from, for this to actually
    # exercise "no rollover yet" rather than accidentally triggering one
    # from date mismatch alone - derived from the manager, not time.time().
    risk = _risk(tmp_path, monkeypatch, starting_bankroll=1000.0, max_daily_loss_pct=0.1)
    day1 = _noon_of_seeded_day(risk)
    risk.check_daily_loss(890.0, now=day1)
    assert risk.halted is True
    # Later the same UTC day - still halted, no rollover yet.
    assert risk.check_daily_loss(1000.0, now=day1 + 60) is False
    assert risk.halted is True


def test_check_daily_loss_auto_rolls_over_on_a_new_utc_day(tmp_path, monkeypatch):
    risk = _risk(tmp_path, monkeypatch, starting_bankroll=1000.0, max_daily_loss_pct=0.1)
    day1 = _noon_of_seeded_day(risk)
    risk.check_daily_loss(890.0, now=day1)
    assert risk.halted is True
    day2 = day1 + 86400  # 24h later - a new UTC calendar date
    # The real bug this fixes: this must NOT stay permanently halted.
    assert risk.check_daily_loss(890.0, now=day2) is True
    assert risk.halted is False
    assert risk.day_start_bankroll == 890.0  # rebased to the bankroll at rollover time


def test_rollover_rebases_day_start_bankroll_even_without_a_prior_halt(tmp_path, monkeypatch):
    risk = _risk(tmp_path, monkeypatch, starting_bankroll=1000.0, max_daily_loss_pct=0.1)
    day1 = _noon_of_seeded_day(risk)
    risk.check_daily_loss(980.0, now=day1)  # small loss, never halts
    day2 = day1 + 86400
    risk.check_daily_loss(950.0, now=day2)
    assert risk.day_start_bankroll == 950.0  # rebased, not still 1000.0


def test_rollover_persists_across_a_restart(tmp_path, monkeypatch):
    db_path = tmp_path / "risk_state.db"
    monkeypatch.setattr(rm, "DB_PATH", db_path)
    risk = rm.RiskManager(starting_bankroll=1000.0, max_daily_loss_pct=0.1, kill_switch_enabled=True)
    day1 = _noon_of_seeded_day(risk)
    risk.check_daily_loss(890.0, now=day1)
    assert risk.halted is True

    day2 = day1 + 86400
    resumed = rm.RiskManager(starting_bankroll=1000.0, max_daily_loss_pct=0.1, kill_switch_enabled=True)
    assert resumed.halted is True  # restart alone doesn't roll over
    assert resumed.check_daily_loss(890.0, now=day2) is True
    assert resumed.halted is False


def test_a_pre_existing_row_with_no_day_start_date_does_not_immediately_rollover(tmp_path, monkeypatch):
    # Migration safety: a row written before day_start_date existed (NULL)
    # must seed from *today*, not be treated as instantly stale - an
    # existing halt shouldn't silently clear itself the moment this code
    # ships, only at a genuine subsequent day boundary.
    db_path = tmp_path / "risk_state.db"
    monkeypatch.setattr(rm, "DB_PATH", db_path)
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE risk_meta (id INTEGER PRIMARY KEY CHECK (id = 1), day_start_bankroll REAL NOT NULL, "
        "halted INTEGER NOT NULL, halt_reason TEXT)"
    )
    conn.execute("INSERT INTO risk_meta VALUES (1, 1000.0, 1, 'pre-existing halt')")
    conn.commit()
    conn.close()

    risk = rm.RiskManager(starting_bankroll=1000.0, max_daily_loss_pct=0.1, kill_switch_enabled=True)
    assert risk.halted is True
    same_day_now = time.time()
    assert risk.check_daily_loss(1000.0, now=same_day_now) is False  # still halted, no rollover today
    assert risk.halted is True


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


# --- per-instance db_path (supports more than one independent kill switch) --

def test_explicit_db_path_overrides_module_default(tmp_path, monkeypatch):
    monkeypatch.setattr(rm, "DB_PATH", tmp_path / "should-not-be-used" / "risk_state.db")
    explicit_path = tmp_path / "explicit" / "other_risk_state.db"
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


# --- check_total_exposure (2026-08-23 gap-check finding: no portfolio-wide
# exposure cap existed, only per-trade max_trade_size and opt-in
# per-series position counts) ------------------------------------------------

def test_check_total_exposure_unset_is_always_true(tmp_path, monkeypatch):
    risk = _risk(tmp_path, monkeypatch)
    assert risk.max_total_exposure_pct is None
    assert risk.check_total_exposure(current_exposure=999999.0, prospective_cost=999999.0, bankroll=1000.0) is True


def test_check_total_exposure_true_when_within_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(rm, "DB_PATH", tmp_path / "risk_state.db")
    risk = rm.RiskManager(
        starting_bankroll=1000.0, max_daily_loss_pct=0.1, kill_switch_enabled=True, max_total_exposure_pct=0.5,
    )
    assert risk.check_total_exposure(current_exposure=200.0, prospective_cost=100.0, bankroll=1000.0) is True


def test_check_total_exposure_false_when_cap_would_be_exceeded(tmp_path, monkeypatch):
    monkeypatch.setattr(rm, "DB_PATH", tmp_path / "risk_state.db")
    risk = rm.RiskManager(
        starting_bankroll=1000.0, max_daily_loss_pct=0.1, kill_switch_enabled=True, max_total_exposure_pct=0.5,
    )
    assert risk.check_total_exposure(current_exposure=400.0, prospective_cost=200.0, bankroll=1000.0) is False


def test_check_total_exposure_exactly_at_the_cap_is_true(tmp_path, monkeypatch):
    monkeypatch.setattr(rm, "DB_PATH", tmp_path / "risk_state.db")
    risk = rm.RiskManager(
        starting_bankroll=1000.0, max_daily_loss_pct=0.1, kill_switch_enabled=True, max_total_exposure_pct=0.5,
    )
    assert risk.check_total_exposure(current_exposure=400.0, prospective_cost=100.0, bankroll=1000.0) is True
