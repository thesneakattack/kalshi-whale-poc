import time

from services import market_history as mh_module
from services import paper_broker as pb_module
from services.kalshi_fees import taker_fee
from services.paper_broker import PaperBroker
from services.position_netting import (
    classify, describe_groups, find_groups, payout_profile, review,
)


def _broker(tmp_path, monkeypatch, bankroll=100000.0):
    monkeypatch.setattr(pb_module, "DB_PATH", tmp_path / "paper_broker.db")
    monkeypatch.setattr(mh_module, "DB_PATH", tmp_path / "market_history.db")
    return PaperBroker(starting_bankroll=bankroll)


def _titles(pairs):
    """pairs: {ticker: event_ticker}."""
    return {t: {"event_ticker": et} for t, et in pairs.items()}


def test_find_groups_pairs_two_open_positions_on_confirmed_me_event(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    broker.open_position("A", "yes", 100, 0.5, "r")
    broker.open_position("B", "yes", 100, 0.5, "r")
    market_titles = _titles({"A": "EVT-1", "B": "EVT-1"})
    event_titles = {"EVT-1": {"mutually_exclusive": True}}
    groups = find_groups(broker.positions, market_titles, event_titles)
    assert len(groups) == 1
    assert groups[0]["event_ticker"] == "EVT-1"
    assert {t for t, _ in groups[0]["members"]} == {"A", "B"}
    assert groups[0]["include_outside"] is False  # exactly 2 - genuine complete pair


def test_find_groups_ignores_unconfirmed_or_false_flag(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    broker.open_position("A", "yes", 100, 0.5, "r")
    broker.open_position("B", "yes", 100, 0.5, "r")
    market_titles = _titles({"A": "EVT-1", "B": "EVT-1"})
    assert find_groups(broker.positions, market_titles, {"EVT-1": {"mutually_exclusive": False}}) == []
    assert find_groups(broker.positions, market_titles, {"EVT-1": {"mutually_exclusive": None}}) == []
    assert find_groups(broker.positions, market_titles, {}) == []


def test_find_groups_ignores_single_member_events(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    broker.open_position("A", "yes", 100, 0.5, "r")
    market_titles = _titles({"A": "EVT-1"})
    event_titles = {"EVT-1": {"mutually_exclusive": True}}
    assert find_groups(broker.positions, market_titles, event_titles) == []


def test_find_groups_include_outside_true_for_n_way(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    for t in ("A", "B", "C"):
        broker.open_position(t, "no", 100, 0.05, "r")
    market_titles = _titles({"A": "EVT-1", "B": "EVT-1", "C": "EVT-1"})
    event_titles = {"EVT-1": {"mutually_exclusive": True}}
    groups = find_groups(broker.positions, market_titles, event_titles)
    assert len(groups) == 1
    assert groups[0]["include_outside"] is True


def test_find_groups_respects_min_dwell_sec(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    broker.open_position("A", "yes", 100, 0.5, "r")
    broker.open_position("B", "yes", 100, 0.5, "r")
    broker.positions["A"].opened_at = time.time() - 1000
    broker.positions["B"].opened_at = time.time()  # just opened
    market_titles = _titles({"A": "EVT-1", "B": "EVT-1"})
    event_titles = {"EVT-1": {"mutually_exclusive": True}}
    groups = find_groups(broker.positions, market_titles, event_titles, min_dwell_sec=300)
    assert groups == []  # B hasn't dwelled long enough, so only 1 eligible member


def test_payout_profile_and_classify_locked_profit_for_underpriced_pair(tmp_path, monkeypatch):
    # Both sides bought at 0.3 - unit costs sum to 0.6 < 1, classic
    # arbitrage: whichever side wins, the group nets a positive payout.
    broker = _broker(tmp_path, monkeypatch)
    broker.open_position("A", "yes", 100, 0.3, "r")
    broker.open_position("B", "yes", 100, 0.3, "r")
    members = list(broker.positions.items())
    profile = payout_profile(members, include_outside=False)
    total_cost = 100 * 0.3 * 2
    total_fee = taker_fee(100, 0.3) * 2
    assert profile["A"] == profile["B"] == 100 - total_cost - total_fee
    assert classify(profile) == "locked_profit"


def test_payout_profile_and_classify_locked_loss_for_overpriced_pair(tmp_path, monkeypatch):
    # Both sides bought at 0.6 - unit costs sum to 1.2 > 1, guaranteed loss
    # regardless of outcome.
    broker = _broker(tmp_path, monkeypatch)
    broker.open_position("A", "yes", 100, 0.6, "r")
    broker.open_position("B", "yes", 100, 0.6, "r")
    members = list(broker.positions.items())
    profile = payout_profile(members, include_outside=False)
    total_cost = 100 * 0.6 * 2
    total_fee = taker_fee(100, 0.6) * 2
    assert profile["A"] == profile["B"] == 100 - total_cost - total_fee
    assert classify(profile) == "locked_loss"


def test_payout_profile_variable_for_unequal_sizing():
    # Same shape as the real UFC MAK/MGI pair found live: unequal sizes
    # mean the group is NOT locked either way.
    from services.paper_broker import Position
    a = Position(ticker="A", side="yes", size=200, entry_price=0.5, opened_at=time.time(), entry_fee=0.0)
    b = Position(ticker="B", side="yes", size=50, entry_price=0.5, opened_at=time.time(), entry_fee=0.0)
    members = [("A", a), ("B", b)]
    profile = payout_profile(members, include_outside=False)
    assert profile["A"] > 0  # A winning: keep the bigger leg's payout, lose the smaller leg's cost
    assert profile["B"] < 0  # B winning: keep only the smaller leg's payout
    assert classify(profile) == "variable"


def test_payout_profile_outside_bucket_for_n_way_subset():
    from services.paper_broker import Position
    members = [
        (t, Position(ticker=t, side="no", size=100, entry_price=0.05, opened_at=time.time(), entry_fee=0.0))
        for t in ("A", "B", "C")
    ]
    profile = payout_profile(members, include_outside=True)
    assert set(profile) == {"A", "B", "C", "__outside__"}
    total_cost = sum(100 * 0.95 for _ in members)
    # Outside wins - every NO position wins (none of the 3 tracked golfers won).
    assert profile["__outside__"] == 300 - total_cost
    # A wins - A's own NO leg loses (0), B and C's NO legs still win (100 each).
    assert profile["A"] == 200 - total_cost


def test_describe_groups_recommends_trimming_worse_leg_when_it_clears_the_bar(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    # Mirrors the real live UFC finding: a bigger, better-priced leg (A)
    # and a smaller, weaker leg (B) both effectively betting the same
    # direction once you look at current live prices.
    broker.open_position("A", "yes", 200, 0.76, "r")
    broker.open_position("B", "no", 50, 0.25, "r")
    market_titles = _titles({"A": "EVT-1", "B": "EVT-1"})
    event_titles = {"EVT-1": {"mutually_exclusive": True}}
    latest_prices = {"A": 0.75, "B": 0.24}
    cfg = {"position_netting": {"enabled": True, "min_dwell_sec": 0, "min_edge_improvement_usd": 1.0, "normal_volatility": None}}
    groups = describe_groups(broker, market_titles, event_titles, latest_prices, cfg)
    assert len(groups) == 1
    rec = groups[0]["recommendation"]
    assert rec["action"] == "trim_worst_leg"
    assert rec["tickers"] == ["B"]


def test_describe_groups_holds_when_no_action_clears_materiality_bar(tmp_path, monkeypatch):
    # Same shape as the trim-worthy test above (genuinely "variable", not
    # locked either way) but with an unreasonably high materiality bar -
    # the noise filter should refuse to act even though trimming B would
    # still nominally improve expected value.
    broker = _broker(tmp_path, monkeypatch)
    broker.open_position("A", "yes", 200, 0.76, "r")
    broker.open_position("B", "no", 50, 0.25, "r")
    market_titles = _titles({"A": "EVT-1", "B": "EVT-1"})
    event_titles = {"EVT-1": {"mutually_exclusive": True}}
    latest_prices = {"A": 0.75, "B": 0.24}
    cfg = {"position_netting": {"enabled": True, "min_dwell_sec": 0, "min_edge_improvement_usd": 1000000.0, "normal_volatility": None}}
    groups = describe_groups(broker, market_titles, event_titles, latest_prices, cfg)
    assert groups[0]["status"] == "variable"
    assert groups[0]["recommendation"]["action"] == "hold"


def test_describe_groups_locked_loss_recommends_close_all():
    from services.paper_broker import Position
    a = Position(ticker="A", side="yes", size=100, entry_price=0.6, opened_at=time.time(), entry_fee=0.0)
    b = Position(ticker="B", side="yes", size=100, entry_price=0.6, opened_at=time.time(), entry_fee=0.0)

    class _FakeBroker:
        positions = {"A": a, "B": b}

    market_titles = _titles({"A": "EVT-1", "B": "EVT-1"})
    event_titles = {"EVT-1": {"mutually_exclusive": True}}
    cfg = {"position_netting": {"enabled": True, "min_dwell_sec": 0}}
    groups = describe_groups(_FakeBroker(), market_titles, event_titles, {"A": 0.6, "B": 0.6}, cfg)
    rec = groups[0]["recommendation"]
    assert groups[0]["status"] == "locked_loss"
    assert rec["action"] == "close_all"
    assert set(rec["tickers"]) == {"A", "B"}


def test_review_is_a_noop_when_disabled(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    broker.open_position("A", "yes", 100, 0.6, "r")
    broker.open_position("B", "yes", 100, 0.6, "r")
    market_titles = _titles({"A": "EVT-1", "B": "EVT-1"})
    event_titles = {"EVT-1": {"mutually_exclusive": True}}
    cfg = {"position_netting": {"enabled": False}}
    decisions = review(broker, market_titles, event_titles, {"A": 0.6, "B": 0.6}, cfg)
    assert decisions == []
    assert "A" in broker.positions and "B" in broker.positions


def test_review_closes_locked_loss_group_when_enabled(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    broker.open_position("A", "yes", 100, 0.6, "r")
    broker.open_position("B", "yes", 100, 0.6, "r")
    market_titles = _titles({"A": "EVT-1", "B": "EVT-1"})
    event_titles = {"EVT-1": {"mutually_exclusive": True}}
    cfg = {"position_netting": {"enabled": True, "min_dwell_sec": 0}}
    decisions = review(broker, market_titles, event_titles, {"A": 0.6, "B": 0.6}, cfg)
    assert len(decisions) == 2
    assert {d["ticker"] for d in decisions} == {"A", "B"}
    assert all(d["source"] == "position_netting" for d in decisions)
    assert broker.positions == {}  # both legs closed
