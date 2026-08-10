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
        auto_exit_threshold=0.6, exit_sentiment_lean_pct=65, exit_sentiment_min_signals=3,
    )
    strategy.update(overrides)
    return {"strategy": strategy, "market_strategy": {"min_momentum_delta": 0.03}}


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


# --- new per-field suggestion functions (merged in from the old
# trade_analytics.compute_insights, see advisory_engine.py's docstring) ------

def test_auto_exit_threshold_recommendation_raises_when_net_positive():
    rows = [_row(close_type="auto_exit", realized_pnl=10.0) for _ in range(3)]
    rec = ae._auto_exit_threshold_recommendation(rows, _cfg()["strategy"])
    assert rec is not None
    assert rec["config_path"] == "strategy.auto_exit_threshold"
    assert rec["suggested_value"] == 0.65
    assert rec["n"] == 3


def test_auto_exit_threshold_recommendation_lowers_when_net_negative():
    rows = [_row(close_type="auto_exit", realized_pnl=-10.0) for _ in range(3)]
    rec = ae._auto_exit_threshold_recommendation(rows, _cfg()["strategy"])
    assert rec["suggested_value"] == 0.55


def test_auto_exit_threshold_recommendation_none_below_minimum_sample():
    rows = [_row(close_type="auto_exit", realized_pnl=10.0) for _ in range(2)]
    assert ae._auto_exit_threshold_recommendation(rows, _cfg()["strategy"]) is None


def test_sentiment_exit_recommendations_tightens_both_fields_when_net_negative():
    rows = [_row(close_type="sentiment_reversal", realized_pnl=-5.0) for _ in range(3)]
    recs = ae._sentiment_exit_recommendations(rows, _cfg()["strategy"])
    paths = {r["config_path"]: r for r in recs}
    assert paths["strategy.exit_sentiment_lean_pct"]["suggested_value"] == 70
    assert paths["strategy.exit_sentiment_min_signals"]["suggested_value"] == 5


def test_sentiment_exit_recommendations_loosens_both_fields_when_net_positive():
    rows = [_row(close_type="sentiment_reversal", realized_pnl=5.0) for _ in range(3)]
    recs = ae._sentiment_exit_recommendations(rows, _cfg()["strategy"])
    paths = {r["config_path"]: r for r in recs}
    assert paths["strategy.exit_sentiment_lean_pct"]["suggested_value"] == 60
    assert paths["strategy.exit_sentiment_min_signals"]["suggested_value"] == 1


def test_sentiment_exit_recommendations_none_below_minimum_sample():
    rows = [_row(close_type="sentiment_reversal", realized_pnl=-5.0) for _ in range(2)]
    assert ae._sentiment_exit_recommendations(rows, _cfg()["strategy"]) == []


def test_momentum_exit_recommendation_uses_market_strategy_prefix_not_strategy():
    # Real bug fixed by the merge: the old compute_insights-based renderer
    # hardcoded a "strategy." prefix on this topic, but min_momentum_delta
    # actually lives under market_strategy.*.
    rows = [_row(close_type="momentum_reversal", realized_pnl=-3.0) for _ in range(3)]
    rec = ae._momentum_exit_recommendation(rows, _cfg()["market_strategy"])
    assert rec is not None
    assert rec["config_path"] == "market_strategy.min_momentum_delta"
    assert rec["suggested_value"] == 0.04


def test_momentum_exit_recommendation_lowers_when_net_positive():
    rows = [_row(close_type="momentum_reversal", realized_pnl=3.0) for _ in range(3)]
    rec = ae._momentum_exit_recommendation(rows, _cfg()["market_strategy"])
    assert rec["suggested_value"] == 0.02


def test_momentum_exit_recommendation_none_below_minimum_sample():
    rows = [_row(close_type="momentum_reversal", realized_pnl=-3.0) for _ in range(2)]
    assert ae._momentum_exit_recommendation(rows, _cfg()["market_strategy"]) is None


# --- generate_recommendations (the unified entrypoint) -----------------------
# No blanket gate anymore (2026-08-10) - per-field suggestions read the full
# trade history and hedge on their own per-field sample size, same as
# compute_insights() always did; only _cross_variant_recommendations still
# needs the current variant to clear min_resolved_trades, tested separately
# above.

def test_generate_recommendations_reports_resolved_count_without_gating_output():
    rows = [_row(config_fingerprint="fp1") for _ in range(5)]
    result = ae.generate_recommendations(rows, _cfg(), "fp1", {}, min_resolved_trades=30)
    assert result["resolved_count"] == 5
    assert result["min_resolved_trades_per_variant"] == 30
    assert "gated_reason" not in result


def test_generate_recommendations_returns_within_variant_hints_regardless_of_resolved_count():
    rows = _confidence_split_rows(n_low=4, low_win=0, n_high=4, high_win=4)
    for r in rows:
        r["config_fingerprint"] = "fp1"
    variants = {"fp1": _variant("fp1", entry_threshold=0.5)}
    # min_resolved_trades set far above what this variant has - would have
    # blocked everything under the old blanket gate.
    result = ae.generate_recommendations(rows, _cfg(entry_threshold=0.5), "fp1", variants, min_resolved_trades=100)
    paths = [r["config_path"] for r in result["recommendations"]]
    assert "strategy.entry_threshold" in paths


def test_generate_recommendations_now_blends_other_variants_trades_for_within_variant_hints():
    # This is the exact bug fix the merge exists for: a suggestion for one
    # field used to be scoped to trades placed under the *exact* current
    # config fingerprint, so changing any other field reset the sample to
    # zero. Per-field suggestions now read the full trade history regardless
    # of which fingerprint each trade carries.
    # current_rows carry no entry_confidence at all - under the old
    # fp-filtered behavior there'd be nothing to bucket, so the only way
    # this recommendation can appear is if other_rows' fp-other trades are
    # now blended in too.
    current_rows = [_row(config_fingerprint="fp1", entry_confidence=None, won=True) for _ in range(5)]
    other_rows = _confidence_split_rows(n_low=4, low_win=0, n_high=4, high_win=4)
    for r in other_rows:
        r["config_fingerprint"] = "fp-other"
    rows = current_rows + other_rows
    result = ae.generate_recommendations(rows, _cfg(entry_threshold=0.5), "fp1", {}, min_resolved_trades=5)
    paths = [r["config_path"] for r in result["recommendations"]]
    assert "strategy.entry_threshold" in paths


def test_generate_recommendations_includes_market_strategy_suggestions_from_market_rows():
    rows = [_row(config_fingerprint="fp1") for _ in range(5)]
    market_rows = [_row(close_type="momentum_reversal", realized_pnl=-3.0) for _ in range(3)]
    result = ae.generate_recommendations(rows, _cfg(), "fp1", {}, min_resolved_trades=5, market_rows=market_rows)
    paths = [r["config_path"] for r in result["recommendations"]]
    assert "market_strategy.min_momentum_delta" in paths


# --- change_effect (Item 3D, 2026-08-10) --------------------------------------

def test_change_effect_none_when_fingerprint_unchanged():
    # market_strategy.*/risk.*/etc. changes always log the same fingerprint
    # on both sides - nothing to compare, not a bug.
    summaries = {"fp1": {"total_closed": 10, "win_rate_pct": 50.0, "total_realized_pnl": 5.0}}
    assert ae.change_effect("fp1", "fp1", summaries) is None


def test_change_effect_none_when_a_side_has_no_trades_yet():
    summaries = {"fp1": {"total_closed": 10, "win_rate_pct": 50.0, "total_realized_pnl": 5.0}}
    assert ae.change_effect("fp1", "fp2", summaries) is None  # fp2 not in summaries at all


def test_change_effect_reports_real_before_after_numbers():
    summaries = {
        "fp1": {"total_closed": 10, "win_rate_pct": 40.0, "total_realized_pnl": -20.0},
        "fp2": {"total_closed": 5, "win_rate_pct": 80.0, "total_realized_pnl": 15.0},
    }
    effect = ae.change_effect("fp1", "fp2", summaries)
    assert effect == {
        "before_win_rate_pct": 40.0, "before_n": 10, "before_realized_pnl": -20.0,
        "after_win_rate_pct": 80.0, "after_n": 5, "after_realized_pnl": 15.0,
    }
