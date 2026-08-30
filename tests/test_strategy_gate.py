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


# --- silent-fallback fix (issue #267) --------------------------------------
# Before this, a signal whose ticker had no market_titles/event_titles entry
# at all silently defaulted mutually_exclusive to False - the same bare
# `except Exception: pass` also swallowed a genuine exception during the
# lookup. Neither case incremented a counter or wrote a fault. The gate's
# fail-open behavior is unchanged by this fix; only its visibility is new:
# services.strategy_engine.me_gate_unknown_total (a lifetime, monotone
# counter) and a fault_log row, deduplicated once per event per
# observability window (services.strategy_engine.reset_window(), rolled by
# services/observability/observability.py's maybe_capture - see
# services/exits/exit_engine.py's _stale_uncorroborated_logged for the same
# established shape).

def _trade_cfg():
    return {"strategy": {
        "entry_threshold": 0.0, "special_market_min_seconds_to_close": 300,
        "kelly_fraction_of_cap": 0.0, "max_position_pct": 0.1, "cooldown_sec": 0,
    }}


def _future_close_time(seconds=600):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + seconds))


def test_me_gate_no_market_titles_entry_fails_open_and_is_counted_and_logged(tmp_path, monkeypatch):
    from services import fault_log, strategy_engine

    broker, risk = _make_broker_and_risk(tmp_path, monkeypatch)
    strat = FollowTheWhaleStrategy(broker, risk)
    monkeypatch.setattr(strategy_engine, "_me_gate_stats", {"me_gate_unknown_total": 0})
    monkeypatch.setattr(strategy_engine, "_me_gate_unknown_logged", set())
    recorded = []
    monkeypatch.setattr(fault_log, "record_fault", lambda *a, **k: recorded.append((a, k)) or True)

    sig = WhaleSignal(
        id="me1", ticker="UNK-1", side="yes", size=10, price=0.5, confidence=1.0,
        timestamp=time.time(), close_time=_future_close_time(),
    )
    res = strat.evaluate(
        sig, _trade_cfg(), is_live=False, market_results={}, latest_prices={},
        markets=[{"ticker": "UNK-1"}], market_titles={}, event_titles={},
    )

    # (a) the gate still fails open exactly as before - a missing lookup
    # never blocks the trade, it only used to do so silently.
    assert res["action"] == "trade"
    # (b) the counter increments.
    assert strategy_engine.me_gate_stats()["me_gate_unknown_total"] == 1
    # (c) a fault is logged on first occurrence for this ticker/event.
    assert len(recorded) == 1
    args, kwargs = recorded[0]
    assert args[0] == "strategy_engine"
    assert args[1] == "me_gate_unknown"
    assert kwargs.get("severity") == "warn"


def test_me_gate_second_signal_same_event_does_not_log_a_second_fault(tmp_path, monkeypatch):
    """(d) once-per-event-per-window dedup: two different tickers that both
    resolve to the same event, neither of which event_titles has an entry
    for, must increment the counter twice but log the fault only once."""
    from services import fault_log, strategy_engine

    broker, risk = _make_broker_and_risk(tmp_path, monkeypatch)
    strat = FollowTheWhaleStrategy(broker, risk)
    monkeypatch.setattr(strategy_engine, "_me_gate_stats", {"me_gate_unknown_total": 0})
    monkeypatch.setattr(strategy_engine, "_me_gate_unknown_logged", set())
    recorded = []
    monkeypatch.setattr(fault_log, "record_fault", lambda *a, **k: recorded.append((a, k)) or True)

    market_titles = {
        "UNK-A": {"event_ticker": "EVT-SHARED"},
        "UNK-B": {"event_ticker": "EVT-SHARED"},
    }
    close_time = _future_close_time()

    sig_a = WhaleSignal(
        id="me2a", ticker="UNK-A", side="yes", size=10, price=0.5, confidence=1.0,
        timestamp=time.time(), close_time=close_time,
    )
    res_a = strat.evaluate(
        sig_a, _trade_cfg(), is_live=False, market_results={}, latest_prices={},
        markets=[{"ticker": "UNK-A"}], market_titles=market_titles, event_titles={},
    )
    assert res_a["action"] == "trade"
    assert strategy_engine.me_gate_stats()["me_gate_unknown_total"] == 1
    assert len(recorded) == 1

    sig_b = WhaleSignal(
        id="me2b", ticker="UNK-B", side="yes", size=10, price=0.5, confidence=1.0,
        timestamp=time.time(), close_time=close_time,
    )
    res_b = strat.evaluate(
        sig_b, _trade_cfg(), is_live=False, market_results={}, latest_prices={},
        markets=[{"ticker": "UNK-B"}], market_titles=market_titles, event_titles={},
    )
    assert res_b["action"] == "trade"
    # Counter still increments every occurrence...
    assert strategy_engine.me_gate_stats()["me_gate_unknown_total"] == 2
    # ...but the SAME event does not get a second fault row this window.
    assert len(recorded) == 1


def test_me_gate_window_reset_allows_the_same_event_to_log_again(tmp_path, monkeypatch):
    """services.strategy_engine.reset_window() (rolled by observability's
    maybe_capture) clears the dedup set but never the lifetime counter -
    a persistent problem must keep re-announcing itself across windows,
    not go silent forever after its first occurrence."""
    from services import fault_log, strategy_engine

    broker, risk = _make_broker_and_risk(tmp_path, monkeypatch)
    strat = FollowTheWhaleStrategy(broker, risk)
    monkeypatch.setattr(strategy_engine, "_me_gate_stats", {"me_gate_unknown_total": 0})
    monkeypatch.setattr(strategy_engine, "_me_gate_unknown_logged", set())
    recorded = []
    monkeypatch.setattr(fault_log, "record_fault", lambda *a, **k: recorded.append((a, k)) or True)

    close_time = _future_close_time()
    sig = WhaleSignal(
        id="me3", ticker="UNK-1", side="yes", size=10, price=0.5, confidence=1.0,
        timestamp=time.time(), close_time=close_time,
    )
    strat.evaluate(
        sig, _trade_cfg(), is_live=False, market_results={}, latest_prices={},
        markets=[{"ticker": "UNK-1"}], market_titles={}, event_titles={},
    )
    assert len(recorded) == 1

    strategy_engine.reset_window()

    sig2 = WhaleSignal(
        id="me3b", ticker="UNK-1", side="yes", size=10, price=0.5, confidence=1.0,
        timestamp=time.time(), close_time=close_time,
    )
    strat.evaluate(
        sig2, _trade_cfg(), is_live=False, market_results={}, latest_prices={},
        markets=[{"ticker": "UNK-1"}], market_titles={}, event_titles={},
    )
    assert strategy_engine.me_gate_stats()["me_gate_unknown_total"] == 2  # lifetime, never reset
    assert len(recorded) == 2  # new window: logged again


def test_me_gate_exception_during_lookup_fails_open_and_is_counted_and_logged(tmp_path, monkeypatch):
    """The bare `except Exception: pass` also used to swallow a genuine
    inspection failure identically to the missing-lookup case - now it is
    counted/logged too, under a distinct operation tag so the two remain
    separately queryable at /api/health/faults."""
    from services import fault_log, strategy_engine

    broker, risk = _make_broker_and_risk(tmp_path, monkeypatch)
    strat = FollowTheWhaleStrategy(broker, risk)
    monkeypatch.setattr(strategy_engine, "_me_gate_stats", {"me_gate_unknown_total": 0})
    monkeypatch.setattr(strategy_engine, "_me_gate_unknown_logged", set())
    recorded = []
    monkeypatch.setattr(fault_log, "record_fault", lambda *a, **k: recorded.append((a, k)) or True)

    # event_ticker resolves and event_titles HAS an entry for it (so the
    # missing-lookup branch never fires) but that entry is malformed - not
    # a dict - so `.get("mutually_exclusive")` raises inside the try.
    market_titles = {"BAD-1": {"event_ticker": "EVT-BAD"}}
    event_titles = {"EVT-BAD": "not-a-dict"}

    sig = WhaleSignal(
        id="me4", ticker="BAD-1", side="yes", size=10, price=0.5, confidence=1.0,
        timestamp=time.time(), close_time=_future_close_time(),
    )
    res = strat.evaluate(
        sig, _trade_cfg(), is_live=False, market_results={}, latest_prices={},
        markets=[{"ticker": "BAD-1"}], market_titles=market_titles, event_titles=event_titles,
    )

    assert res["action"] == "trade"  # still fails open
    assert strategy_engine.me_gate_stats()["me_gate_unknown_total"] == 1
    assert len(recorded) == 1
    args, kwargs = recorded[0]
    assert args[0] == "strategy_engine"
    assert args[1] == "me_gate_inspection_error"


def test_me_gate_known_and_genuinely_not_mutually_exclusive_is_not_counted_or_logged(tmp_path, monkeypatch):
    """The normal case (issue #267 point 3): market_titles/event_titles
    both resolve, and the event genuinely is not mutually_exclusive.
    Nothing about this is a defect, so neither the counter nor fault_log
    should move."""
    from services import fault_log, strategy_engine

    broker, risk = _make_broker_and_risk(tmp_path, monkeypatch)
    strat = FollowTheWhaleStrategy(broker, risk)
    monkeypatch.setattr(strategy_engine, "_me_gate_stats", {"me_gate_unknown_total": 0})
    monkeypatch.setattr(strategy_engine, "_me_gate_unknown_logged", set())
    recorded = []
    monkeypatch.setattr(fault_log, "record_fault", lambda *a, **k: recorded.append((a, k)) or True)

    market_titles = {"OK-1": {"event_ticker": "EVT-OK"}}
    event_titles = {"EVT-OK": {"collateral_return_type": None, "mutually_exclusive": False}}

    sig = WhaleSignal(
        id="me5", ticker="OK-1", side="yes", size=10, price=0.5, confidence=1.0,
        timestamp=time.time(), close_time=_future_close_time(),
    )
    res = strat.evaluate(
        sig, _trade_cfg(), is_live=False, market_results={}, latest_prices={},
        markets=[{"ticker": "OK-1"}], market_titles=market_titles, event_titles=event_titles,
    )

    assert res["action"] == "trade"
    assert strategy_engine.me_gate_stats()["me_gate_unknown_total"] == 0
    assert recorded == []
