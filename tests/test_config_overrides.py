import pytest

from services.config_overrides import merge_override, resolve


def test_resolve_returns_global_defaults_when_no_overrides():
    strat_cfg = {"entry_threshold": 0.75, "cooldown_sec": 120}
    assert resolve(strat_cfg, None) == strat_cfg
    assert resolve(strat_cfg, {}) == strat_cfg
    assert resolve(strat_cfg, {}, category="Sports", series="KXNFLGAME") == strat_cfg


def test_resolve_applies_category_layer():
    strat_cfg = {"entry_threshold": 0.75, "cooldown_sec": 120}
    overrides = {"by_category": {"Crypto": {"cooldown_sec": 30}}}
    resolved = resolve(strat_cfg, overrides, category="Crypto")
    assert resolved["cooldown_sec"] == 30
    assert resolved["entry_threshold"] == 0.75  # unset field falls through


def test_resolve_series_wins_over_category():
    strat_cfg = {"stop_loss_pct": 0.2}
    overrides = {
        "by_category": {"Sports": {"stop_loss_pct": 0.15}},
        "by_series": {"KXPGATOUR": {"stop_loss_pct": 0.05}},
    }
    resolved = resolve(strat_cfg, overrides, category="Sports", series="KXPGATOUR")
    assert resolved["stop_loss_pct"] == 0.05


def test_resolve_series_only_field_falls_through_to_category():
    strat_cfg = {"entry_threshold": 0.75, "cooldown_sec": 120}
    overrides = {
        "by_category": {"Sports": {"entry_threshold": 0.7}},
        "by_series": {"KXPGATOUR": {"cooldown_sec": 60}},
    }
    resolved = resolve(strat_cfg, overrides, category="Sports", series="KXPGATOUR")
    assert resolved["entry_threshold"] == 0.7  # from category, series didn't touch it
    assert resolved["cooldown_sec"] == 60  # from series


def test_resolve_no_match_falls_through_to_global():
    strat_cfg = {"entry_threshold": 0.75}
    overrides = {"by_category": {"Sports": {"entry_threshold": 0.7}}}
    resolved = resolve(strat_cfg, overrides, category="Politics", series="KXPRESNOMD")
    assert resolved["entry_threshold"] == 0.75


def test_resolve_never_mutates_inputs():
    strat_cfg = {"entry_threshold": 0.75}
    overrides = {"by_category": {"Sports": {"entry_threshold": 0.7}}}
    resolve(strat_cfg, overrides, category="Sports")
    assert strat_cfg == {"entry_threshold": 0.75}
    assert overrides == {"by_category": {"Sports": {"entry_threshold": 0.7}}}


def test_merge_override_preserves_other_entries():
    overrides = {
        "by_category": {"Sports": {"entry_threshold": 0.7}},
        "by_series": {"KXNFLGAME": {"cooldown_sec": 60}},
    }
    result = merge_override(overrides, "by_series", "KXPGATOUR", "stop_loss_pct", 0.05)
    assert result["by_series"]["KXNFLGAME"] == {"cooldown_sec": 60}  # untouched
    assert result["by_series"]["KXPGATOUR"] == {"stop_loss_pct": 0.05}
    assert result["by_category"] == {"Sports": {"entry_threshold": 0.7}}  # untouched


def test_merge_override_preserves_other_fields_on_same_key():
    overrides = {"by_series": {"KXPGATOUR": {"stop_loss_pct": 0.05}}}
    result = merge_override(overrides, "by_series", "KXPGATOUR", "cooldown_sec", 60)
    assert result["by_series"]["KXPGATOUR"] == {"stop_loss_pct": 0.05, "cooldown_sec": 60}


def test_merge_override_from_empty_overrides():
    result = merge_override(None, "by_category", "Crypto", "entry_threshold", 0.6)
    assert result == {"by_category": {"Crypto": {"entry_threshold": 0.6}}, "by_series": {}}


def test_merge_override_rejects_unknown_scope():
    with pytest.raises(ValueError):
        merge_override({}, "by_market", "X", "field", 1)


def test_merge_override_never_mutates_input():
    overrides = {"by_series": {"KXNFLGAME": {"cooldown_sec": 60}}}
    merge_override(overrides, "by_series", "KXNFLGAME", "stop_loss_pct", 0.1)
    assert overrides == {"by_series": {"KXNFLGAME": {"cooldown_sec": 60}}}
