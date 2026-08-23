"""services/settlement_edge_entry.py - the "act on it" half of the measured
settlement-projection edge (services/settlement_edge.py never trades - see
its own docstring). Off by default; these tests lock in every gate that
has to hold before this opens a position, plus the hold_to_settlement
exemption in services/exits/exit_engine.py that makes holding to real
settlement possible at all.
"""
import pytest

from services import market_history as mh_module
from services import paper_broker as pb
from services import risk_manager as rm
from services import settlement_edge_entry as see
from services.exits import exit_engine


@pytest.fixture(autouse=True)
def _isolate_market_history(tmp_path, monkeypatch):
    # exit_engine.check_exits corroborates every price against
    # market_history.recent_price unconditionally (2026-08-17 incident) -
    # without this, every check_exits test below would read/write the real
    # data/market_history.db (CLAUDE.md: tests never touch a real data/*.db
    # file).
    monkeypatch.setattr(mh_module, "DB_PATH", tmp_path / "market_history.db")


def _broker(tmp_path, monkeypatch, starting_bankroll=1000.0):
    monkeypatch.setattr(pb, "DB_PATH", tmp_path / "paper_broker.db")
    return pb.PaperBroker(starting_bankroll=starting_bankroll)


def _risk(tmp_path, monkeypatch, starting_bankroll=1000.0):
    monkeypatch.setattr(rm, "DB_PATH", tmp_path / "risk_state.db")
    return rm.RiskManager(starting_bankroll, max_daily_loss_pct=0.5, kill_switch_enabled=True)


_SPEC = {
    "supported": True, "ticker": "KXBTC15M-A", "index_id": "BRTI", "strike": 63500.0,
    "comparison": ">=", "close_time": "2026-08-17T06:00:00Z",
}

_CFG_ENABLED = {
    "settlement_edge_entry": {
        "enabled": True, "min_observations_known": 45, "min_probability": 0.95,
        "min_edge": 0.05, "max_position_pct": 0.1, "volatility_lookback_sec": 3600,
    },
}


def _projection(known=50, required=63450.0, spot=63500.0):
    """A confident, easy-YES projection by default: index sitting comfortably
    above what the rest of the window needs to average, deep into the
    high-observations-known bucket."""
    return {
        "status": "accumulating", "index_id": "BRTI", "observations_known": known,
        "observations_total": 60, "partial_average": 63490.0, "spot": spot,
        "required_remaining": required, "gap_from_spot": required - spot,
    }


@pytest.fixture(autouse=True)
def _volatility(monkeypatch):
    from services import index_feed

    monkeypatch.setattr(index_feed, "recent_volatility", lambda *a, **k: 5.0)


def test_off_by_default_even_with_a_slam_dunk_projection(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    risk = _risk(tmp_path, monkeypatch)
    decision = see.evaluate_entry(
        "KXBTC15M-A", _SPEC, _projection(), 0.5, {"settlement_edge_entry": {"enabled": False}},
        broker, risk,
    )
    assert decision is None
    assert broker.positions == {}


def test_enters_yes_when_projection_is_confident_and_market_lags(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    risk = _risk(tmp_path, monkeypatch)
    # projected_probability(required=63450, spot=63500, k=50, vol=5.0) is
    # deep in the high-confidence-YES tail; market still prices it at 0.60.
    decision = see.evaluate_entry(
        "KXBTC15M-A", _SPEC, _projection(known=50, required=63450.0, spot=63500.0),
        0.60, _CFG_ENABLED, broker, risk,
    )
    assert decision is not None
    assert decision["action"] == "trade"
    assert decision["trade"]["side"] == "yes"
    assert "KXBTC15M-A" in broker.positions
    pos = broker.positions["KXBTC15M-A"]
    assert pos.hold_to_settlement is True


def test_enters_no_when_projection_is_confidently_against_yes(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    risk = _risk(tmp_path, monkeypatch)
    # Index sitting well below what it needs to clear the strike - a
    # confident NO.
    decision = see.evaluate_entry(
        "KXBTC15M-A", _SPEC, _projection(known=50, required=63600.0, spot=63500.0),
        0.60, _CFG_ENABLED, broker, risk,
    )
    assert decision is not None
    assert decision["trade"]["side"] == "no"
    assert broker.positions["KXBTC15M-A"].side == "no"


def test_skips_outside_the_accumulating_window(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    risk = _risk(tmp_path, monkeypatch)
    for status in ("outside_window", "determined", "unknown"):
        decision = see.evaluate_entry(
            "KXBTC15M-A", _SPEC, {"status": status}, 0.5, _CFG_ENABLED, broker, risk,
        )
        assert decision is None


def test_requires_minimum_observations_known(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    risk = _risk(tmp_path, monkeypatch)
    # Same easy-YES projection, but too early in the window - the measured
    # edge doesn't cover this bucket.
    decision = see.evaluate_entry(
        "KXBTC15M-A", _SPEC, _projection(known=20, required=63450.0, spot=63500.0),
        0.60, _CFG_ENABLED, broker, risk,
    )
    assert decision is None


def test_requires_confident_probability_not_just_a_better_forecast(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    risk = _risk(tmp_path, monkeypatch)
    # A tiny gap between required and spot late in the window is still only
    # a coin flip once run through projected_probability - real, but not
    # confident enough to pay a taker fee on.
    decision = see.evaluate_entry(
        "KXBTC15M-A", _SPEC, _projection(known=50, required=63500.5, spot=63500.0),
        0.50, _CFG_ENABLED, broker, risk,
    )
    assert decision is None


def test_requires_a_real_edge_over_the_markets_own_price(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    risk = _risk(tmp_path, monkeypatch)
    # Confident projection, but the market has already priced it in almost
    # exactly - nothing left to capture net of fees.
    decision = see.evaluate_entry(
        "KXBTC15M-A", _SPEC, _projection(known=50, required=63450.0, spot=63500.0),
        0.97, _CFG_ENABLED, broker, risk,
    )
    assert decision is None


def test_refuses_a_market_price_outside_the_tradeable_range(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    risk = _risk(tmp_path, monkeypatch)
    # A YES side priced at 0.99 has no achievable edge whatever the
    # projection says - same hard floor every strategy respects.
    decision = see.evaluate_entry(
        "KXBTC15M-A", _SPEC, _projection(known=50, required=63450.0, spot=63500.0),
        0.99, _CFG_ENABLED, broker, risk,
    )
    assert decision is None


def test_never_duplicates_an_existing_position_or_pending_order(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    risk = _risk(tmp_path, monkeypatch)
    broker.open_position("KXBTC15M-A", "yes", size=5, price=0.5, reason="whale entry")
    decision = see.evaluate_entry(
        "KXBTC15M-A", _SPEC, _projection(known=50, required=63450.0, spot=63500.0),
        0.60, _CFG_ENABLED, broker, risk,
    )
    assert decision is None


def test_respects_risk_halt_via_open_positions_own_execution_layer_guard(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    risk = _risk(tmp_path, monkeypatch)
    risk.manual_halt("test halt")
    broker.risk = risk
    decision = see.evaluate_entry(
        "KXBTC15M-A", _SPEC, _projection(known=50, required=63450.0, spot=63500.0),
        0.60, _CFG_ENABLED, broker, risk,
    )
    assert decision is None
    assert broker.positions == {}


# --- exit_engine's hold_to_settlement exemption -----------------------------

def test_hold_to_settlement_position_is_not_force_closed_at_the_runway_floor(tmp_path, monkeypatch):
    """The whole point of a settlement-edge entry is to hold to real
    settlement - if exit_min_seconds_to_close force-closed it a moment
    after entry (which it otherwise would, since this enters deep inside
    that same floor on purpose), the edge it was opened to capture would
    be capped instead of realized."""
    broker = _broker(tmp_path, monkeypatch)
    broker.open_position(
        "KXBTC15M-A", "yes", size=10, price=0.60, reason="settlement-edge entry",
        hold_to_settlement=True,
    )
    cfg = {"strategy": {"exit_min_seconds_to_close": 120}}
    decisions = exit_engine.check_exits(
        broker, {"KXBTC15M-A": 0.62}, signal_feed=[], cfg=cfg,
        close_times={"KXBTC15M-A": "2026-08-17T06:00:00Z"},
    )
    assert decisions == []
    assert "KXBTC15M-A" in broker.positions


def test_ordinary_position_is_still_force_closed_at_the_runway_floor(tmp_path, monkeypatch):
    """Unaffected-by-default check: a normal whale-follow position (
    hold_to_settlement=False) must still hit ROADMAP #1's runway-floor
    exit exactly as before - the new exemption is scoped to settlement-edge
    entries only, not a blanket weakening of this safety rail."""
    broker = _broker(tmp_path, monkeypatch)
    broker.open_position("KXBTC15M-A", "yes", size=10, price=0.60, reason="whale print")
    cfg = {"strategy": {"exit_min_seconds_to_close": 120}}
    decisions = exit_engine.check_exits(
        broker, {"KXBTC15M-A": 0.62}, signal_feed=[], cfg=cfg,
        close_times={"KXBTC15M-A": "2026-08-17T06:00:00Z"},
    )
    assert len(decisions) == 1
    assert "KXBTC15M-A" not in broker.positions


# --- Position.hold_to_settlement persistence --------------------------------

def test_hold_to_settlement_survives_a_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(pb, "DB_PATH", tmp_path / "paper_broker.db")
    broker = pb.PaperBroker(starting_bankroll=1000.0)
    broker.open_position(
        "KXBTC15M-A", "yes", size=10, price=0.60, reason="settlement-edge entry",
        hold_to_settlement=True,
    )
    reloaded = pb.PaperBroker(starting_bankroll=1000.0)
    assert reloaded.positions["KXBTC15M-A"].hold_to_settlement is True
