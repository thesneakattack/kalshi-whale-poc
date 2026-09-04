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


def test_check_daily_loss_zero_bankroll_baseline_does_not_crash(tmp_path, monkeypatch):
    """RiskManager's own missing guard (services/shadow_mode.py's
    ShadowTrader already has it: `if not self.day_start_bankroll: return
    True`) - the reachable path is reset_day(current_bankroll) setting the
    baseline from the live bankroll at each UTC date rollover, so a
    bankroll of exactly 0 at rollover would raise ZeroDivisionError in the
    REAL kill switch (the inert shadow copy was already protected - the
    safety asymmetry ran backwards). Task 3b of docs/superpowers/plans/
    2026-09-03-tier1-backend-hygiene.md."""
    risk = _risk(tmp_path, monkeypatch, starting_bankroll=0.0, max_daily_loss_pct=0.1)
    # Must not raise ZeroDivisionError, and must not halt on a baseline
    # that was never really a baseline (matches ShadowTrader's own
    # documented "return True" - trading continues, same forgiving default).
    assert risk.check_daily_loss(0.0) is True
    assert risk.halted is False


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


def test_connect_closes_its_connection(tmp_path, monkeypatch):
    """_RecordingConnection wraps the real connection instead of mutating
    conn.close directly - that raises AttributeError on this container's
    Python (sqlite3.Connection.close is read-only), the same defect Task
    1's own implementation (PR #518) found and fixed against
    tests/test_signal_log.py's proven pattern (Tier 0's own Task 5),
    applied here for this module's test file."""
    import sqlite3

    closed = []
    real_connect = sqlite3.connect

    class _RecordingConnection:
        def __init__(self, inner):
            self._inner = inner

        def close(self):
            closed.append(True)
            self._inner.close()

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, *exc_info):
            return self._inner.__exit__(*exc_info)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def _tracking_connect(*args, **kwargs):
        return _RecordingConnection(real_connect(*args, **kwargs))

    # Construct before patching: RiskManager.__init__ opens (and, post-fix,
    # correctly closes) its own connection to load risk_meta - patching
    # sqlite3.connect first would record that connection too, since
    # db.connect() patches at the module level, not per call site.
    tracker = rm.RiskManager(1000.0, 0.1, True, db_path=tmp_path / "rm.db")
    monkeypatch.setattr(rm.db.sqlite3, "connect", _tracking_connect)
    with tracker._connect() as conn:
        conn.execute("SELECT 1")
    assert closed == [True]


def test_connect_still_creates_table_with_day_start_date_column(tmp_path, monkeypatch):
    tracker = rm.RiskManager(1000.0, 0.1, True, db_path=tmp_path / "rm.db")
    with tracker._connect() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "risk_meta" in tables
        cols = {r[1] for r in conn.execute("PRAGMA table_info(risk_meta)")}
        assert "day_start_date" in cols


def test_connect_sets_explicit_busy_timeout_pragma(tmp_path, monkeypatch):
    tracker = rm.RiskManager(1000.0, 0.1, True, db_path=tmp_path / "rm.db")
    with tracker._connect() as conn:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_monkeypatched_db_path_still_works(tmp_path, monkeypatch):
    """This module's own DB_PATH-resolved-at-construction-time mechanism
    (:83-88's own comment) - confirm it still works after migration."""
    monkeypatch.setattr(rm, "DB_PATH", tmp_path / "monkeypatched_rm.db")
    tracker = rm.RiskManager(1000.0, 0.1, True)
    with tracker._connect() as conn:
        conn.execute("SELECT 1")
    assert (tmp_path / "monkeypatched_rm.db").exists()
