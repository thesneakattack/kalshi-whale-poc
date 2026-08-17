"""
services/config_bounds.py - physically-achievable ranges for cost-basis
fraction settings. A binary contract pays $1 or $0, which puts a hard
ceiling on take_profit_pct and stop_loss_pct that nothing enforced before.
"""
import pytest

from services import config_bounds


def test_max_gain_fraction_matches_the_settlement_arithmetic():
    # at c=0.5 a win doubles the stake; at c=0.8 it can only return 25%
    assert config_bounds.max_gain_fraction(0.5) == pytest.approx(1.0)
    assert config_bounds.max_gain_fraction(0.8) == pytest.approx(0.25)
    assert config_bounds.max_gain_fraction(0.95) == pytest.approx(0.0526, abs=1e-4)


def test_stop_loss_above_total_loss_is_flagged_unreachable():
    v = config_bounds.check({"stop_loss_pct": 1.75})
    assert len(v) == 1
    assert v[0]["field"] == "stop_loss_pct"
    assert v[0]["severity"] == "unreachable"


def test_stop_loss_at_or_below_one_is_fine():
    assert config_bounds.check({"stop_loss_pct": 0.9}) == []


def test_take_profit_beyond_the_cheapest_entry_is_unreachable():
    v = config_bounds.check({"take_profit_pct": 1.5, "min_unit_cost": 0.5, "max_unit_cost": 0.8})
    assert [x["severity"] for x in v] == ["unreachable"]


def test_take_profit_reachable_for_only_part_of_the_band_is_partial():
    # the real 2026-08-17 config: tp 0.8 with a 0.5-0.8 band is reachable
    # only for entries at c <= 0.556
    v = config_bounds.check({"take_profit_pct": 0.8, "min_unit_cost": 0.5, "max_unit_cost": 0.8})
    assert len(v) == 1
    assert v[0]["severity"] == "partial"
    assert "0.556" in v[0]["detail"]


def test_take_profit_reachable_across_the_whole_band_is_clean():
    v = config_bounds.check({"take_profit_pct": 0.2, "min_unit_cost": 0.5, "max_unit_cost": 0.8})
    assert v == []


def test_inverted_band_is_flagged():
    v = config_bounds.check({"min_unit_cost": 0.9, "max_unit_cost": 0.4})
    assert any(x["field"] == "min_unit_cost" for x in v)


def test_clamp_bounds_an_advisory_suggestion():
    val, note = config_bounds.clamp("take_profit_pct", 1.75, {"min_unit_cost": 0.5})
    assert val == 1.0
    assert note and "clamped" in note


def test_clamp_leaves_a_reachable_suggestion_alone():
    val, note = config_bounds.clamp("take_profit_pct", 0.4, {"min_unit_cost": 0.5})
    assert val == 0.4
    assert note is None


def test_check_all_covers_overrides_not_just_the_global_default():
    cfg = {
        "strategy": {"take_profit_pct": 0.2, "min_unit_cost": 0.5, "max_unit_cost": 0.8},
        "strategy_overrides": {"by_series": {"KXBTC15M": {"stop_loss_pct": 1.75}}},
    }
    v = config_bounds.check_all(cfg)
    assert any(x["scope"] == "series:KXBTC15M" and x["field"] == "stop_loss_pct" for x in v)
