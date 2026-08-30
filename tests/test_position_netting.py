import re
import sqlite3
import time

from services import market_history as mh_module
from services import paper_broker as pb_module
from services.kalshi_fees import taker_fee
from services.paper_broker import PaperBroker
from services.exits.position_netting import (
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


def _flat_history(tickers, now, price=0.5, count=4, step_sec=60):
    """count snapshots at one unchanging price - what an untraded market
    looks like. volatility() needs >=3 snapshots to return anything at
    all, so this is the shape that produces a real 0.0 (every consecutive
    delta is zero) rather than the None of "not enough history"."""
    for i in range(count):
        mh_module.record_snapshots(
            [{"ticker": t, "yes_price": price} for t in tickers],
            timestamp=now - (count - i) * step_sec,
        )


def _wiggly_history(ticker, now, prices=(0.50, 0.54, 0.50, 0.54), step_sec=60):
    """A ticker whose price actually moves - a genuine non-zero reading."""
    for i, p in enumerate(prices):
        mh_module.record_snapshots(
            [{"ticker": ticker, "yes_price": p}], timestamp=now - (len(prices) - i) * step_sec,
        )


def _variable_pair(broker):
    """The trim-worthy 'variable' group the two describe_groups tests
    above already use, factored out so the volatility tests below differ
    from them only in their volatility inputs."""
    broker.open_position("A", "yes", 200, 0.76, "r")
    broker.open_position("B", "no", 50, 0.25, "r")
    return (
        _titles({"A": "EVT-1", "B": "EVT-1"}),
        {"EVT-1": {"mutually_exclusive": True}},
        {"A": 0.75, "B": 0.24},
    )


def _net_cfg(**over):
    cfg = {
        "enabled": True, "min_dwell_sec": 0, "min_edge_improvement_usd": 10.0,
        "normal_volatility": 0.02, "volatility_lookback_sec": 1800,
    }
    cfg.update(over)
    return {"position_netting": cfg}


def test_materiality_bar_treats_a_zero_volatility_reading_as_no_reading(tmp_path, monkeypatch):
    # 2026-08-30 (issue #206): a real 0.0 from volatility() is "nobody has
    # traded this in the lookback window," not "this market is calm" - the
    # same defect exit_engine.py:526-547 was fixed for on 2026-08-17, where
    # 142 of 183 well-sampled live markets read exactly 0.0. Feeding it
    # through pinned vol_ratio to its 0.25 floor and QUARTERED the bar,
    # inverting this function's own stated intent (a less trustworthy price
    # read must require a BIGGER edge, not a 4x smaller one).
    #
    # Every other netting test passes normal_volatility=None, which returns
    # at the `if not normal_vol` guard before the filter is ever reached -
    # this one needs a real baseline AND (because the bar is set by
    # max(vols)) every member reading 0.0.
    broker = _broker(tmp_path, monkeypatch)
    market_titles, event_titles, latest_prices = _variable_pair(broker)
    now = time.time()
    _flat_history(("A", "B"), now)
    assert mh_module.volatility("A", 1800, as_of=now) == 0.0  # a real reading, not None
    assert mh_module.volatility("B", 1800, as_of=now) == 0.0

    groups = describe_groups(broker, market_titles, event_titles, latest_prices, _net_cfg(), now=now)
    assert groups[0]["status"] == "variable"
    # Unscaled min_edge_improvement_usd (vol_ratio 1.0, exit_engine's own
    # "no reading -> today's unscaled behaviour" fallback). Was $2.50.
    assert groups[0]["recommendation"]["materiality_bar_usd"] == 10.0


def test_all_stale_group_does_not_get_an_easier_bar_to_clear(tmp_path, monkeypatch):
    # The consequence of the above, at the only level that matters: with a
    # bar the group's $36.57 improvement sits between, the quartered bar
    # ($25.00) let netting churn fire on prices nothing had traded, while
    # the honest bar ($100.00) holds. Netting acted most easily exactly
    # where its live-price inputs were least trustworthy.
    broker = _broker(tmp_path, monkeypatch)
    market_titles, event_titles, latest_prices = _variable_pair(broker)
    now = time.time()
    _flat_history(("A", "B"), now)
    cfg = _net_cfg(min_edge_improvement_usd=100.0)
    rec = describe_groups(broker, market_titles, event_titles, latest_prices, cfg, now=now)[0]["recommendation"]
    assert rec["materiality_bar_usd"] == 100.0
    assert rec["action"] == "hold"


def test_materiality_bar_still_scales_up_on_a_genuine_noisy_reading(tmp_path, monkeypatch):
    # The other half of the contract, so the fix above can't be "over-
    # corrected" into ignoring volatility altogether or bailing whenever
    # any one member reads 0.0: a group with one stale member and one
    # genuinely noisy member is set by the noisy one (max(vols)), and a
    # ticker moving more than the configured normal RAISES the bar.
    broker = _broker(tmp_path, monkeypatch)
    market_titles, event_titles, latest_prices = _variable_pair(broker)
    now = time.time()
    _flat_history(("A",), now)
    _wiggly_history("B", now)
    vol_b = mh_module.volatility("B", 1800, as_of=now)
    assert mh_module.volatility("A", 1800, as_of=now) == 0.0
    assert vol_b > 0.02  # noisier than the configured normal baseline

    rec = describe_groups(broker, market_titles, event_titles, latest_prices, _net_cfg(), now=now)[0]["recommendation"]
    assert rec["materiality_bar_usd"] == round(10.0 * min(4.0, vol_b / 0.02), 2)
    assert rec["materiality_bar_usd"] > 10.0


# --- decision inputs as columns (issue #213, 2026-08-30) ------------------
# The bar, the improvement, and the vol_ratio that scaled the bar used to
# survive only inside the reason sentence; recovering the bar for the #206
# blast-radius analysis meant regex-parsing `bar \$([0-9.]+)` out of
# trades.reason. Prose is for the reader; columns are for the analysis
# (docs/data-layer-analysis-layer-contract.md). The sentence stays exactly
# as it was - the columns supplement it, and MUST agree with it.

_NETTING_PROSE = re.compile(
    r"estimated \$([0-9.]+) expected-value improvement over holding \(bar \$([0-9.]+)\)"
)


def test_describe_groups_surfaces_the_vol_ratio_that_scaled_the_bar(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    market_titles, event_titles, latest_prices = _variable_pair(broker)
    now = time.time()
    _flat_history(("A",), now)
    _wiggly_history("B", now)
    vol_b = mh_module.volatility("B", 1800, as_of=now)

    rec = describe_groups(broker, market_titles, event_titles, latest_prices, _net_cfg(), now=now)[0]["recommendation"]
    assert rec["vol_ratio"] == max(0.25, min(4.0, vol_b / 0.02))
    assert rec["vol_ratio"] > 1.0
    # Surfacing the ratio changed nothing about the bar itself.
    assert rec["materiality_bar_usd"] == round(10.0 * rec["vol_ratio"], 2)


def test_vol_ratio_is_exactly_one_whenever_the_bar_is_unscaled(tmp_path, monkeypatch):
    # Both unscaled paths - no baseline configured, and a baseline with no
    # usable reading (every member a real 0.0, PR #220's `v > 0` filter) -
    # report the ratio they actually applied: 1.0, never None and never
    # the 0.25 floor a zero reading used to pin it to.
    broker = _broker(tmp_path, monkeypatch)
    market_titles, event_titles, latest_prices = _variable_pair(broker)
    now = time.time()
    _flat_history(("A", "B"), now)

    no_reading = describe_groups(broker, market_titles, event_titles, latest_prices, _net_cfg(), now=now)
    assert no_reading[0]["recommendation"]["vol_ratio"] == 1.0
    assert no_reading[0]["recommendation"]["materiality_bar_usd"] == 10.0

    no_baseline = describe_groups(
        broker, market_titles, event_titles, latest_prices, _net_cfg(normal_volatility=None), now=now,
    )
    assert no_baseline[0]["recommendation"]["vol_ratio"] == 1.0
    assert no_baseline[0]["recommendation"]["materiality_bar_usd"] == 10.0


def test_review_persists_netting_decision_inputs_that_agree_with_the_reason_prose(tmp_path, monkeypatch):
    # The agreement test the issue asks for: the columns on the CLOSE row a
    # netting action writes must equal the numbers still embedded in that
    # same row's reason string. A mismatch would mean the column is
    # computed at a different point than the sentence - worse than the
    # regex it replaces.
    broker = _broker(tmp_path, monkeypatch)
    market_titles, event_titles, latest_prices = _variable_pair(broker)
    now = time.time()
    _flat_history(("A",), now)
    _wiggly_history("B", now)
    vol_b = mh_module.volatility("B", 1800, as_of=now)
    assert vol_b > 0.02
    cfg = _net_cfg(min_edge_improvement_usd=1.0)  # bar <= $4.00 - the trim clears it

    decisions = review(broker, market_titles, event_titles, latest_prices, cfg, now=now)
    assert [d["ticker"] for d in decisions] == ["B"]  # trim_worst_leg closes the weak leg only

    with sqlite3.connect(pb_module.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = {r["ticker"]: r for r in conn.execute(
            "SELECT ticker, reason, netting_improvement_usd, netting_bar_usd, netting_vol_ratio "
            "FROM trades WHERE reason LIKE 'closed: position netting%'"
        )}
    assert set(rows) == {"B"}
    row = rows["B"]
    m = _NETTING_PROSE.search(row["reason"])
    assert m, row["reason"]  # the sentence is unchanged - the columns supplement it
    assert row["netting_improvement_usd"] == float(m.group(1))
    assert row["netting_bar_usd"] == float(m.group(2))
    assert row["netting_vol_ratio"] == max(0.25, min(4.0, vol_b / 0.02))
    assert row["netting_bar_usd"] == round(1.0 * row["netting_vol_ratio"], 2)
    # The decision dict and the in-memory Trade carry the same three values.
    trade = decisions[0]["trade"]
    assert trade["netting_improvement_usd"] == row["netting_improvement_usd"]
    assert trade["netting_bar_usd"] == row["netting_bar_usd"]
    assert trade["netting_vol_ratio"] == row["netting_vol_ratio"]


def test_entry_rows_and_locked_loss_closes_leave_the_netting_inputs_null(tmp_path, monkeypatch):
    # NULL means "no bar was computed for this row" - true of every entry
    # and of a locked_loss close_all (the loss is fixed regardless of
    # timing, so no expected-value comparison or bar is involved). Never
    # 0.0, which would read as a real bar of zero dollars.
    broker = _broker(tmp_path, monkeypatch)
    broker.open_position("A", "yes", 100, 0.6, "r")
    broker.open_position("B", "yes", 100, 0.6, "r")
    market_titles = _titles({"A": "EVT-1", "B": "EVT-1"})
    event_titles = {"EVT-1": {"mutually_exclusive": True}}
    decisions = review(broker, market_titles, event_titles, {"A": 0.6, "B": 0.6}, _net_cfg())
    assert len(decisions) == 2 and broker.positions == {}

    with sqlite3.connect(pb_module.DB_PATH) as conn:
        rows = conn.execute(
            "SELECT reason, netting_improvement_usd, netting_bar_usd, netting_vol_ratio FROM trades"
        ).fetchall()
    assert len(rows) == 4  # two entries, two locked_loss closes
    assert all(r[1] is None and r[2] is None and r[3] is None for r in rows)
    assert sum(r[0].startswith("closed: position netting (locked_loss") for r in rows) == 2
