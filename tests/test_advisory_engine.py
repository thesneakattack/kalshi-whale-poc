import pytest

from services import advisory_engine as ae
from services import trade_analytics


def _row(**overrides):
    base = dict(
        ticker="TICK-A", config_fingerprint="fp1", entry_confidence=0.6,
        won=True, close_type="settled_win", realized_pnl=10.0, hold_sec=100.0,
        left_on_table=None, cost_basis=50.0,
    )
    base.update(overrides)
    return base


def _cfg(**overrides):
    strategy = dict(
        name="follow_the_whale", entry_threshold=0.5, max_position_pct=0.05,
        cooldown_sec=300, take_profit_pct=None, stop_loss_pct=None,
    )
    strategy.update(overrides)
    return {"strategy": strategy}


# --- variant_summaries -------------------------------------------------------

def test_variant_summaries_groups_by_fingerprint():
    rows = [
        _row(config_fingerprint="fp1"), _row(config_fingerprint="fp1"),
        _row(config_fingerprint="fp2"),
    ]
    summaries = ae.variant_summaries(rows)
    assert set(summaries.keys()) == {"fp1", "fp2"}
    assert summaries["fp1"]["total_closed"] == 2
    assert summaries["fp2"]["total_closed"] == 1


def test_variant_summaries_skips_rows_with_no_fingerprint():
    rows = [_row(config_fingerprint="fp1"), _row(config_fingerprint=None)]
    summaries = ae.variant_summaries(rows)
    assert list(summaries.keys()) == ["fp1"]
    assert summaries["fp1"]["total_closed"] == 1


# --- _entry_threshold_recommendation -----------------------------------------

def _confidence_split_rows(n_low, low_win, n_high, high_win):
    rows = []
    for i in range(n_low):
        rows.append(_row(entry_confidence=0.3, won=(i < low_win)))
    for i in range(n_high):
        rows.append(_row(entry_confidence=0.9, won=(i < high_win)))
    return rows


def test_entry_threshold_recommendation_fires_on_meaningful_gap():
    rows = _confidence_split_rows(n_low=4, low_win=0, n_high=4, high_win=4)  # 0% vs 100%
    rec = ae._entry_threshold_recommendation(rows, current_value=0.5)
    assert rec is not None
    assert rec["config_path"] == "strategy.entry_threshold"
    assert rec["current_value"] == 0.5
    assert rec["suggested_value"] == 0.75  # best bucket's floor
    assert rec["n"] == 8
    assert rec["confidence_label"] == trade_analytics.confidence_label(8)


def test_entry_threshold_recommendation_none_when_gap_small():
    rows = _confidence_split_rows(n_low=4, low_win=2, n_high=4, high_win=2)  # 50% vs 50%
    assert ae._entry_threshold_recommendation(rows, current_value=0.5) is None


def test_entry_threshold_recommendation_none_when_already_at_suggested_floor():
    rows = _confidence_split_rows(n_low=4, low_win=0, n_high=4, high_win=4)
    # current_value already at/above the best bucket's floor (0.75) - nothing to suggest.
    assert ae._entry_threshold_recommendation(rows, current_value=0.8) is None


def test_entry_threshold_recommendation_id_deterministic():
    rows = _confidence_split_rows(n_low=4, low_win=0, n_high=4, high_win=4)
    rec1 = ae._entry_threshold_recommendation(rows, current_value=0.5)
    rec2 = ae._entry_threshold_recommendation(rows, current_value=0.5)
    assert rec1["id"] == rec2["id"]

    # A different sample size (n) changes the id even though the suggested
    # value itself (a discrete bucket floor) comes out the same - the id is
    # meant to identify *this specific derived recommendation*, not just
    # the value it suggests.
    more_rows = _confidence_split_rows(n_low=5, low_win=0, n_high=5, high_win=5)
    rec3 = ae._entry_threshold_recommendation(more_rows, current_value=0.5)
    assert rec3["suggested_value"] == rec1["suggested_value"]
    assert rec3["id"] != rec1["id"]


# --- _longshot_bonus_recommendation --------------------------------------

def _longshot_split_rows(n_longshot, longshot_win, n_normal, normal_win):
    rows = []
    for i in range(n_longshot):
        rows.append(_row(entry_price=0.1, won=(i < longshot_win)))  # inside the default 15% longshot zone
    for i in range(n_normal):
        rows.append(_row(entry_price=0.5, won=(i < normal_win)))  # comfortably outside it
    return rows


def test_longshot_bonus_recommendation_fires_when_longshots_underperform():
    rows = _longshot_split_rows(n_longshot=4, longshot_win=0, n_normal=4, normal_win=4)  # 0% vs 100%
    rec = ae._longshot_bonus_recommendation(rows, _cfg()["strategy"])
    assert rec is not None
    assert rec["config_path"] == "strategy.longshot_entry_threshold_bonus"
    assert rec["current_value"] == 0.15  # advisory_engine's own default when unset
    assert rec["suggested_value"] == pytest.approx(0.25)
    assert rec["n"] == 8


def test_longshot_bonus_recommendation_none_when_gap_small():
    rows = _longshot_split_rows(n_longshot=4, longshot_win=2, n_normal=4, normal_win=2)  # 50% vs 50%
    assert ae._longshot_bonus_recommendation(rows, _cfg()["strategy"]) is None


def test_longshot_bonus_recommendation_none_with_too_few_rows_in_either_bucket():
    rows = _longshot_split_rows(n_longshot=2, longshot_win=0, n_normal=4, normal_win=4)  # only 2 longshot rows
    assert ae._longshot_bonus_recommendation(rows, _cfg()["strategy"]) is None


def test_longshot_bonus_recommendation_ignores_rows_with_no_entry_price():
    rows = _longshot_split_rows(n_longshot=4, longshot_win=0, n_normal=4, normal_win=4)
    rows.append(_row(entry_price=None, won=False))  # e.g. a trade with no matched entry - shouldn't crash or count
    rec = ae._longshot_bonus_recommendation(rows, _cfg()["strategy"])
    assert rec is not None
    assert rec["n"] == 8  # the entry_price=None row wasn't counted in either bucket


# --- _exit_pct_recommendation -------------------------------------------------

def test_take_profit_recommendation_suggests_higher_value():
    rows = [_row(close_type="take_profit", left_on_table=20.0) for _ in range(3)]
    rec = ae._exit_pct_recommendation(rows, "take_profit", "take_profit_pct", current_value=0.5)
    assert rec is not None
    assert rec["config_path"] == "strategy.take_profit_pct"
    assert rec["suggested_value"] == 0.7  # 0.5 + (20/100)
    assert rec["n"] == 3


def test_stop_loss_recommendation_suggests_tighter_value():
    rows = [_row(close_type="stop_loss", realized_pnl=-40.0, cost_basis=100.0) for _ in range(3)]
    # loss_frac = 40/100 = 0.4; overshoot past configured 0.3 limit = 0.1
    rec = ae._exit_pct_recommendation(rows, "stop_loss", "stop_loss_pct", current_value=0.3)
    assert rec is not None
    assert rec["suggested_value"] == 0.2
    assert rec["n"] == 3


def test_exit_pct_recommendation_none_when_current_value_is_none():
    rows = [_row(close_type="take_profit", left_on_table=20.0) for _ in range(3)]
    assert ae._exit_pct_recommendation(rows, "take_profit", "take_profit_pct", current_value=None) is None


def test_exit_pct_recommendation_none_below_minimum_sample():
    rows = [_row(close_type="take_profit", left_on_table=20.0) for _ in range(2)]  # only 2, need 3
    assert ae._exit_pct_recommendation(rows, "take_profit", "take_profit_pct", current_value=0.5) is None


# --- _cross_variant_recommendations -------------------------------------------

def _variant(fp, **config):
    return {"fingerprint": fp, "config": config, "first_seen_at": 0.0}


def test_cross_variant_recommends_differing_fields_from_better_variant():
    summaries = {
        "fp1": {"total_closed": 10, "win_rate_pct": 40.0},
        "fp2": {"total_closed": 10, "win_rate_pct": 60.0},
    }
    variants = {
        "fp1": _variant("fp1", entry_threshold=0.5, cooldown_sec=300),
        "fp2": _variant("fp2", entry_threshold=0.7, cooldown_sec=300),
    }
    recs = ae._cross_variant_recommendations("fp1", summaries, variants, min_resolved=5)
    assert len(recs) == 1  # only entry_threshold differs - cooldown_sec is identical, nothing to say
    rec = recs[0]
    assert rec["config_path"] == "strategy.entry_threshold"
    assert rec["current_value"] == 0.5
    assert rec["suggested_value"] == 0.7
    assert rec["compared_fingerprint"] == "fp2"
    assert rec["n"] == 10


def test_cross_variant_none_when_gap_too_small():
    summaries = {
        "fp1": {"total_closed": 10, "win_rate_pct": 50.0},
        "fp2": {"total_closed": 10, "win_rate_pct": 55.0},  # only 5pt gap
    }
    variants = {"fp1": _variant("fp1", entry_threshold=0.5), "fp2": _variant("fp2", entry_threshold=0.7)}
    assert ae._cross_variant_recommendations("fp1", summaries, variants, min_resolved=5) == []


def test_cross_variant_skips_variant_below_min_resolved():
    summaries = {
        "fp1": {"total_closed": 10, "win_rate_pct": 40.0},
        "fp2": {"total_closed": 3, "win_rate_pct": 90.0},  # below min_resolved
    }
    variants = {"fp1": _variant("fp1", entry_threshold=0.5), "fp2": _variant("fp2", entry_threshold=0.7)}
    assert ae._cross_variant_recommendations("fp1", summaries, variants, min_resolved=5) == []


def test_cross_variant_current_below_min_resolved_yields_nothing():
    summaries = {
        "fp1": {"total_closed": 3, "win_rate_pct": 40.0},  # current itself below threshold
        "fp2": {"total_closed": 10, "win_rate_pct": 90.0},
    }
    variants = {"fp1": _variant("fp1", entry_threshold=0.5), "fp2": _variant("fp2", entry_threshold=0.7)}
    assert ae._cross_variant_recommendations("fp1", summaries, variants, min_resolved=5) == []


# --- generate_recommendations (the gated entrypoint) --------------------------

def test_generate_recommendations_gated_below_threshold():
    rows = [_row(config_fingerprint="fp1") for _ in range(5)]
    result = ae.generate_recommendations(rows, _cfg(), "fp1", {}, min_resolved_trades=30)
    assert result["recommendations"] == []
    assert result["gated_reason"] is not None
    assert "5/30" in result["gated_reason"]
    assert result["resolved_count"] == 5


def test_generate_recommendations_not_gated_at_exact_boundary():
    rows = [_row(config_fingerprint="fp1") for _ in range(5)]
    result = ae.generate_recommendations(rows, _cfg(), "fp1", {}, min_resolved_trades=5)
    assert result["gated_reason"] is None


def test_generate_recommendations_returns_within_variant_hints_once_unlocked():
    rows = _confidence_split_rows(n_low=4, low_win=0, n_high=4, high_win=4)
    for r in rows:
        r["config_fingerprint"] = "fp1"
    variants = {"fp1": _variant("fp1", entry_threshold=0.5)}
    result = ae.generate_recommendations(rows, _cfg(entry_threshold=0.5), "fp1", variants, min_resolved_trades=5)
    assert result["gated_reason"] is None
    paths = [r["config_path"] for r in result["recommendations"]]
    assert "strategy.entry_threshold" in paths


def test_generate_recommendations_ignores_other_variants_trades_for_within_variant_hints():
    # Trades under a *different* fingerprint shouldn't influence the current
    # variant's own within-variant recommendations - the whole point of
    # scoping per-variant instead of blending like compute_insights does.
    current_rows = [_row(config_fingerprint="fp1", entry_confidence=0.6, won=True) for _ in range(5)]
    other_rows = _confidence_split_rows(n_low=4, low_win=0, n_high=4, high_win=4)
    for r in other_rows:
        r["config_fingerprint"] = "fp-other"
    rows = current_rows + other_rows
    result = ae.generate_recommendations(rows, _cfg(entry_threshold=0.5), "fp1", {}, min_resolved_trades=5)
    assert result["gated_reason"] is None
    paths = [r["config_path"] for r in result["recommendations"]]
    assert "strategy.entry_threshold" not in paths
