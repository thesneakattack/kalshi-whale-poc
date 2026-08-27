import pytest

from services.advisory import advisory_engine as ae
from services.history import trade_analytics
from services import trade_category as tc


@pytest.fixture(autouse=True)
def _redirect_trade_category_db(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "DB_PATH", tmp_path / "trade_category.db")


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
    # left_on_table is a whole-position dollar total (cost_basis=50.0 here,
    # matching _row's default), not cents/contract - the suggestion must
    # normalize by cost_basis like the stop_loss branch does, not divide by
    # a flat 100 (real bug found and fixed 2026-08-14: that unit mismatch
    # let one large position's dollar total dominate the suggested value).
    rows = [_row(close_type="take_profit", left_on_table=20.0) for _ in range(3)]
    rec = ae._exit_pct_recommendation(rows, "take_profit", "take_profit_pct", current_value=0.5)
    assert rec is not None
    assert rec["config_path"] == "strategy.take_profit_pct"
    assert rec["suggested_value"] == 0.9  # 0.5 + (20/50 cost_basis)
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
    # n=100 each, not 10 - a 20pt gap needs real sample size behind it to
    # clear _comparability_margin_pts' real margin-of-error bar (2026-08-15
    # fix; the old flat 15pt tolerance let a 20pt gap through at n=10 too,
    # which isn't actually distinguishable from noise at that sample size).
    summaries = {
        "fp1": {"total_closed": 100, "win_rate_pct": 40.0},
        "fp2": {"total_closed": 100, "win_rate_pct": 60.0},
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
    assert rec["n"] == 100


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


def test_generate_recommendations_drops_declined_suggestion_by_id():
    rows = _confidence_split_rows(n_low=4, low_win=0, n_high=4, high_win=4)
    for r in rows:
        r["config_fingerprint"] = "fp1"
    variants = {"fp1": _variant("fp1", entry_threshold=0.5)}
    cfg = _cfg(entry_threshold=0.5)
    baseline = ae.generate_recommendations(rows, cfg, "fp1", variants, min_resolved_trades=100)
    rec = next(r for r in baseline["recommendations"] if r["config_path"] == "strategy.entry_threshold")
    result = ae.generate_recommendations(
        rows, cfg, "fp1", variants, min_resolved_trades=100, declined_ids={rec["id"]},
    )
    paths = [r["config_path"] for r in result["recommendations"]]
    assert "strategy.entry_threshold" not in paths


def test_generate_recommendations_declining_one_id_does_not_affect_others():
    rows = _confidence_split_rows(n_low=4, low_win=0, n_high=4, high_win=4)
    for r in rows:
        r["config_fingerprint"] = "fp1"
    variants = {"fp1": _variant("fp1", entry_threshold=0.5)}
    cfg = _cfg(entry_threshold=0.5)
    result = ae.generate_recommendations(
        rows, cfg, "fp1", variants, min_resolved_trades=100, declined_ids={"some-unrelated-id"},
    )
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


# --- stale-suggestion filter (direct bug report, 2026-08-11: "if i click ----
# apply it just gives me the same evaluation and same potential increase
# value... suggesting a massive bug") ----------------------------------------

def test_generate_recommendations_drops_suggestion_stale_since_last_apply():
    # All evidence entered before the field was last changed - clicking
    # Apply again would just repeat the exact same stale verdict.
    rows = _confidence_split_rows(n_low=4, low_win=0, n_high=4, high_win=4)  # every row entry_timestamp=None
    for r in rows:
        r["entry_timestamp"] = 1000.0
    result = ae.generate_recommendations(
        rows, _cfg(entry_threshold=0.5), "fp1", {}, min_resolved_trades=100,
        last_applied_by_path={"strategy.entry_threshold": 2000.0},  # changed AFTER every row entered
    )
    paths = [r["config_path"] for r in result["recommendations"]]
    assert "strategy.entry_threshold" not in paths


def test_generate_recommendations_keeps_suggestion_with_fresh_evidence_since_last_apply():
    rows = _confidence_split_rows(n_low=4, low_win=0, n_high=4, high_win=4)
    for r in rows:
        r["entry_timestamp"] = 1000.0
    rows[0]["entry_timestamp"] = 3000.0  # one trade entered AFTER the last change - real new evidence
    result = ae.generate_recommendations(
        rows, _cfg(entry_threshold=0.5), "fp1", {}, min_resolved_trades=100,
        last_applied_by_path={"strategy.entry_threshold": 2000.0},
    )
    paths = [r["config_path"] for r in result["recommendations"]]
    assert "strategy.entry_threshold" in paths


def test_generate_recommendations_never_stale_when_path_was_never_applied_before():
    rows = _confidence_split_rows(n_low=4, low_win=0, n_high=4, high_win=4)
    for r in rows:
        r["entry_timestamp"] = 1000.0
    result = ae.generate_recommendations(
        rows, _cfg(entry_threshold=0.5), "fp1", {}, min_resolved_trades=100,
        last_applied_by_path={"strategy.longshot_entry_threshold_bonus": 2000.0},  # a different path
    )
    paths = [r["config_path"] for r in result["recommendations"]]
    assert "strategy.entry_threshold" in paths


def test_generate_recommendations_with_no_last_applied_by_path_is_unaffected():
    # Omitting the param entirely (every pre-existing call site before this
    # fix) must behave exactly as before - no regression for callers that
    # haven't been updated.
    rows = _confidence_split_rows(n_low=4, low_win=0, n_high=4, high_win=4)
    result = ae.generate_recommendations(rows, _cfg(entry_threshold=0.5), "fp1", {}, min_resolved_trades=100)
    paths = [r["config_path"] for r in result["recommendations"]]
    assert "strategy.entry_threshold" in paths


# --- fresh_samples_since_change (2026-08-15 direct request: "the advisory
# should also take into consideration how many samples have been logged
# after the change") - upgrades _drop_stale_recommendations' binary gate
# into a real count attached to every surviving recommendation. -----------

def test_drop_stale_recommendations_attaches_fresh_sample_count():
    rows = _confidence_split_rows(n_low=4, low_win=0, n_high=4, high_win=4)
    for r in rows:
        r["entry_timestamp"] = 1000.0
    rows[0]["entry_timestamp"] = 3000.0
    rows[1]["entry_timestamp"] = 3100.0  # two trades entered after the change
    result = ae.generate_recommendations(
        rows, _cfg(entry_threshold=0.5), "fp1", {}, min_resolved_trades=100,
        last_applied_by_path={"strategy.entry_threshold": 2000.0},
    )
    rec = next(r for r in result["recommendations"] if r["config_path"] == "strategy.entry_threshold")
    assert rec["fresh_samples_since_change"] == 2


def test_drop_stale_recommendations_none_when_field_never_applied_before():
    rows = _confidence_split_rows(n_low=4, low_win=0, n_high=4, high_win=4)
    for r in rows:
        r["entry_timestamp"] = 1000.0
    result = ae.generate_recommendations(
        rows, _cfg(entry_threshold=0.5), "fp1", {}, min_resolved_trades=100,
        last_applied_by_path={"strategy.longshot_entry_threshold_bonus": 2000.0},
    )
    rec = next(r for r in result["recommendations"] if r["config_path"] == "strategy.entry_threshold")
    assert rec["fresh_samples_since_change"] is None  # never changed - not the same as zero fresh samples


# --- significance_z / significance_t (2026-08-15 direct request: "give me
# a statistical significance score in addition to the semantics") --------

def test_entry_threshold_recommendation_carries_a_real_z_score():
    rows = _confidence_split_rows(n_low=20, low_win=2, n_high=20, high_win=18)  # 10% vs 90%, n=20 each
    rec = ae._entry_threshold_recommendation(rows, current_value=0.5)
    assert rec is not None
    assert rec["significance_z"] is not None
    assert rec["significance_z"] > 1.96  # a real, large gap at n=20 each clears the standard 95% threshold


def test_auto_exit_threshold_recommendation_carries_a_real_t_score():
    pnls = [3.0, 4.0, 5.0, 6.0, 7.0, 4.0, 5.0, 6.0, 5.0, 4.0]  # real variance, consistently positive
    rows = [_row(close_type="auto_exit", realized_pnl=p) for p in pnls]
    rec = ae._auto_exit_threshold_recommendation(rows, _cfg()["strategy"])
    assert rec is not None
    assert rec["significance_t"] is not None
    assert rec["significance_t"] > 0  # consistently positive P&L -> positive t


def test_auto_exit_threshold_recommendation_t_score_none_when_no_variance():
    # every trade realized the exact same P&L - t is undefined, not fabricated
    rows = [_row(close_type="auto_exit", realized_pnl=5.0) for _ in range(5)]
    rec = ae._auto_exit_threshold_recommendation(rows, _cfg()["strategy"])
    assert rec["significance_t"] is None


# --- rejected-candidate counterfactual comparability (2026-08-15 fix) -------
# Real live bug: this comparison used to gate on a flat 15pt tolerance with
# no reference to sample size at all - see _comparability_margin_pts's own
# comment for the incident. These tests lock in the sample-size-aware
# replacement.

def test_rejected_candidate_recommendation_skips_a_small_but_real_gap_at_large_n():
    # The fix's real value, not the small-n direction: a 5pt gap would
    # never have cleared the old flat 15pt tolerance regardless of sample
    # size, so the old code would have suggested loosening this gate even
    # though n=5000 makes a 5pt gap a real, confident signal the gate is
    # working (margin of error only ~1.3pts here). Sample-size-aware
    # margin correctly skips where a flat number could not.
    gate_summaries = [{
        "strategy": "whale_follow", "gate_name": "min_whale_winrate_pct",
        "hypothetical_win_rate": 63.0, "hypothetical_win_rate_n": 5000,
    }]
    whale_summary = {"win_rate_pct": 68.0, "total_closed": 5000}
    recs = ae._rejected_candidate_recommendations(
        gate_summaries, _cfg(min_whale_winrate_pct=85)["strategy"], whale_summary,
    )
    assert recs == []


def test_rejected_candidate_recommendation_still_fires_at_small_n_when_uncertain():
    # By contrast: at small n, the margin is wide, so it's *harder* to
    # confidently prove the gate is working - consistent with this
    # function's own stated design ("comparably or better" is itself a
    # reason to suggest, not just "clearly better"). A 20pt gap at n=10
    # doesn't confidently show the gate earning its keep, so this still
    # (correctly) proceeds, same as before the fix - the fix's job is
    # making the bar sample-size-aware, not making small-n cases stricter.
    gate_summaries = [{
        "strategy": "whale_follow", "gate_name": "min_whale_winrate_pct",
        "hypothetical_win_rate": 40.0, "hypothetical_win_rate_n": 10,
    }]
    whale_summary = {"win_rate_pct": 60.0, "total_closed": 10}
    recs = ae._rejected_candidate_recommendations(
        gate_summaries, _cfg(min_whale_winrate_pct=85)["strategy"], whale_summary,
    )
    assert len(recs) == 1


def test_rejected_candidate_recommendation_matches_the_real_2026_08_15_incident():
    # The exact real numbers behind config_performance id 271 (applied
    # 2026-08-15): min_whale_winrate_pct's rejected pool (55.1%, n=49) vs
    # the book's actual 68.4% (n=607). Documented here, not silently
    # papered over: under this app's own established margin-of-error
    # convention (services/stats_power.py, the same one series_evaluator's
    # below_winrate_floor already uses), this specific gap is genuinely
    # borderline, not a clean violation - the real margin at n=49 is
    # ~13.9pts, leaving 55.1% just inside 68.4%'s interval (54.47 cutoff
    # vs 55.1 observed). The mechanism now reasons about this honestly
    # (sample-size-aware, tighter with more data) instead of via an
    # arbitrary flat number either way; whether 49 resolved rejections is
    # enough to act on at all is a separate, real judgment call - see
    # docs/profit-maximization-assessment-2026-08-15.md.
    gate_summaries = [{
        "strategy": "whale_follow", "gate_name": "min_whale_winrate_pct",
        "hypothetical_win_rate": 55.1, "hypothetical_win_rate_n": 49,
    }]
    whale_summary = {"win_rate_pct": 68.4, "total_closed": 607}
    recs = ae._rejected_candidate_recommendations(
        gate_summaries, _cfg(min_whale_winrate_pct=85)["strategy"], whale_summary,
    )
    assert len(recs) == 1  # borderline, not a clean skip - see comment above


# --- series_evaluator cross-reference ("web of expertise" audit, gap #4) ----

def _series_row(series, **overrides):
    base = dict(series=series, status="observing", strike_count=0, whale_resolved=10, whale_win_rate=50.0,
                below_winrate_floor=False)
    base.update(overrides)
    return base


def test_series_evaluator_recommendation_fires_for_a_rejected_series():
    rows = [_series_row("KXBAD", status="rejected", strike_count=2)]
    recs = ae._series_evaluator_recommendations(rows, {"excluded_series": []})
    assert len(recs) == 1
    assert recs[0]["config_path"] == "strategy.excluded_series"
    assert recs[0]["suggested_value"] == ["KXBAD"]
    assert "rejected" in recs[0]["rationale"]


def test_series_evaluator_recommendation_fires_for_below_winrate_floor():
    rows = [_series_row("KXBAD", below_winrate_floor=True, whale_win_rate=25.0)]
    recs = ae._series_evaluator_recommendations(rows, {"excluded_series": []})
    assert len(recs) == 1
    assert "25%" in recs[0]["rationale"]


def test_series_evaluator_recommendation_none_when_series_already_excluded():
    rows = [_series_row("KXBAD", status="rejected")]
    recs = ae._series_evaluator_recommendations(rows, {"excluded_series": ["KXBAD"]})
    assert recs == []


def test_series_evaluator_recommendation_none_when_neither_flag_set():
    rows = [_series_row("KXFINE", status="observing", below_winrate_floor=False)]
    recs = ae._series_evaluator_recommendations(rows, {"excluded_series": []})
    assert recs == []


def test_series_evaluator_recommendation_none_below_min_sample_size():
    rows = [_series_row("KXBAD", status="rejected", whale_resolved=2)]
    recs = ae._series_evaluator_recommendations(rows, {"excluded_series": []})
    assert recs == []


def test_series_evaluator_recommendation_preserves_existing_excluded_series():
    rows = [_series_row("KXBAD", status="rejected")]
    recs = ae._series_evaluator_recommendations(rows, {"excluded_series": ["KXOTHER"]})
    assert recs[0]["suggested_value"] == ["KXBAD", "KXOTHER"]
    assert recs[0]["current_value"] == ["KXOTHER"]


def test_generate_recommendations_includes_series_evaluator_suggestions():
    rows = [_row(config_fingerprint="fp1") for _ in range(5)]
    series_rows = [_series_row("KXBAD", status="rejected")]
    result = ae.generate_recommendations(
        rows, _cfg(), "fp1", {}, min_resolved_trades=5, series_evaluator_rows=series_rows,
    )
    paths = [r["config_path"] for r in result["recommendations"]]
    assert "strategy.excluded_series" in paths


# --- category-conditional recommendations ("web of expertise" audit, gap #2) --

def _category_row(category, total_closed, win_rate_pct):
    return {"category": category, "total_closed": total_closed, "win_rate_pct": win_rate_pct}


def test_category_recommendation_fires_when_category_underperforms():
    rows = [_category_row("Sports", 10, 20.0)]  # 20% vs an overall 60%
    recs = ae._category_conditional_recommendations(rows, {"entry_threshold": 0.5}, overall_win_rate=60.0)
    assert len(recs) == 1
    assert recs[0]["config_path"] == "strategy_overrides.by_category"
    assert recs[0]["suggested_value"] == {"Sports": {"entry_threshold": 0.55}}  # raised - more selective
    assert "worse" in recs[0]["rationale"]


def test_category_recommendation_fires_when_category_outperforms():
    rows = [_category_row("Politics", 10, 90.0)]  # 90% vs an overall 60%
    recs = ae._category_conditional_recommendations(rows, {"entry_threshold": 0.5}, overall_win_rate=60.0)
    assert recs[0]["suggested_value"] == {"Politics": {"entry_threshold": 0.45}}  # lowered - capture more
    assert "better" in recs[0]["rationale"]


def test_category_recommendation_none_when_gap_small():
    rows = [_category_row("Sports", 10, 55.0)]  # only 5pts off 60%
    recs = ae._category_conditional_recommendations(rows, {"entry_threshold": 0.5}, overall_win_rate=60.0)
    assert recs == []


def test_category_recommendation_none_below_min_sample():
    rows = [_category_row("Sports", 2, 0.0)]
    recs = ae._category_conditional_recommendations(rows, {"entry_threshold": 0.5}, overall_win_rate=60.0)
    assert recs == []


def test_category_recommendation_none_when_overall_win_rate_unknown():
    rows = [_category_row("Sports", 10, 0.0)]
    recs = ae._category_conditional_recommendations(rows, {"entry_threshold": 0.5}, overall_win_rate=None)
    assert recs == []


def test_category_recommendation_preserves_existing_overrides():
    rows = [_category_row("Sports", 10, 20.0)]
    strategy_overrides = {"by_category": {"Politics": {"entry_threshold": 0.4}}}
    recs = ae._category_conditional_recommendations(
        rows, {"entry_threshold": 0.5}, overall_win_rate=60.0, strategy_overrides=strategy_overrides,
    )
    assert recs[0]["suggested_value"] == {"Politics": {"entry_threshold": 0.4}, "Sports": {"entry_threshold": 0.55}}
    assert recs[0]["current_value"] == {"Politics": {"entry_threshold": 0.4}}


def test_generate_recommendations_includes_category_conditional_suggestions():
    rows = [_row(config_fingerprint="fp1", won=True) for _ in range(10)]
    category_rows = [_category_row("Sports", 10, 20.0)]
    result = ae.generate_recommendations(
        rows, _cfg(), "fp1", {}, min_resolved_trades=5, category_rows=category_rows,
    )
    paths = [r["config_path"] for r in result["recommendations"]]
    assert "strategy_overrides.by_category" in paths


# --- series-conditional recommendations (2026-08-16, series -> subcategory
# -> category fallback chain: "it makes more sense to do it by series... and
# fallback to category" / "theres a middle step... by subcategory") --------

def _segment_rows(ticker_prefix, n, win_n):
    return [_row(ticker=f"{ticker_prefix}-{i}", won=(i < win_n)) for i in range(n)]


def test_series_recommendation_uses_series_own_data_when_sufficient():
    # KXBAD has 10 of its own resolved trades, 20% win rate vs a 60% book -
    # plenty to trust on its own, no fallback needed.
    rows = _segment_rows("KXBAD", 10, win_n=2)
    recs = ae._series_conditional_recommendations(rows, {"entry_threshold": 0.5}, overall_win_rate=60.0)
    assert len(recs) == 1
    assert recs[0]["config_path"] == "strategy_overrides.by_series"
    assert recs[0]["suggested_value"] == {"KXBAD": {"entry_threshold": 0.55}}
    assert "its own data" in recs[0]["rationale"]


def test_series_recommendation_falls_back_to_subcategory_when_series_data_is_thin():
    # KXBAD itself only has 2 resolved trades (below the min-sample floor)
    # but shares a subcategory (Baseball) with KXOTHER, which together have
    # plenty - the suggestion should still fire for KXBAD, using Baseball's
    # win rate as the evidence.
    tc.record_category("KXBAD-0", "Sports", subcategory="Baseball")
    tc.record_category("KXBAD-1", "Sports", subcategory="Baseball")
    tc.record_category("KXOTHER-0", "Sports", subcategory="Baseball")
    for i in range(2, 10):
        tc.record_category(f"KXOTHER-{i}", "Sports", subcategory="Baseball")
    rows = _segment_rows("KXBAD", 2, win_n=0) + _segment_rows("KXOTHER", 10, win_n=2)
    recs = ae._series_conditional_recommendations(rows, {"entry_threshold": 0.5}, overall_win_rate=60.0)
    by_series = {r["suggested_value"] and list(r["suggested_value"].keys())[0]: r for r in recs}
    assert "KXBAD" in by_series
    assert '"Baseball" subcategory' in by_series["KXBAD"]["rationale"]


def test_series_recommendation_falls_back_to_category_when_subcategory_also_thin():
    # Neither KXBAD's own data nor its subcategory (Baseball, only 2 total
    # across both tickers) is enough - but the whole Sports category has
    # plenty, so that's the final fallback.
    tc.record_category("KXBAD-0", "Sports", subcategory="Baseball")
    tc.record_category("KXBAD-1", "Sports", subcategory="Baseball")
    for i in range(10):
        tc.record_category(f"KXOTHERSPORT-{i}", "Sports", subcategory="Tennis")
    rows = _segment_rows("KXBAD", 2, win_n=0) + _segment_rows("KXOTHERSPORT", 10, win_n=2)
    recs = ae._series_conditional_recommendations(rows, {"entry_threshold": 0.5}, overall_win_rate=60.0)
    by_series = {list(r["suggested_value"].keys())[0]: r for r in recs}
    assert "KXBAD" in by_series
    assert '"Sports" category' in by_series["KXBAD"]["rationale"]


def test_series_recommendation_none_when_no_tier_has_enough_data():
    tc.record_category("KXBAD-0", "Sports", subcategory="Baseball")
    rows = _segment_rows("KXBAD", 2, win_n=0)
    recs = ae._series_conditional_recommendations(rows, {"entry_threshold": 0.5}, overall_win_rate=60.0)
    assert recs == []


def test_series_recommendation_none_when_gap_small():
    rows = _segment_rows("KXBAD", 10, win_n=5)  # 50% vs 60%, only 10pts off
    recs = ae._series_conditional_recommendations(rows, {"entry_threshold": 0.5}, overall_win_rate=60.0)
    assert recs == []


def test_series_recommendation_none_when_overall_win_rate_unknown():
    rows = _segment_rows("KXBAD", 10, win_n=2)
    recs = ae._series_conditional_recommendations(rows, {"entry_threshold": 0.5}, overall_win_rate=None)
    assert recs == []


def test_series_recommendation_preserves_existing_series_overrides():
    rows = _segment_rows("KXBAD", 10, win_n=2)
    strategy_overrides = {"by_series": {"KXOTHER": {"entry_threshold": 0.4}}}
    recs = ae._series_conditional_recommendations(
        rows, {"entry_threshold": 0.5}, overall_win_rate=60.0, strategy_overrides=strategy_overrides,
    )
    assert recs[0]["suggested_value"] == {
        "KXOTHER": {"entry_threshold": 0.4}, "KXBAD": {"entry_threshold": 0.55},
    }
    assert recs[0]["current_value"] == {"KXOTHER": {"entry_threshold": 0.4}}


def test_generate_recommendations_includes_series_conditional_suggestions():
    # generate_recommendations computes the overall win rate from `rows`
    # itself (not from category_rows, which is caller-supplied/independent
    # here same as the category-conditional test above) - so KXBAD needs a
    # winning counterpart in the same rows to actually create a gap: 2/10
    # KXBAD wins + 10/10 KXGOOD wins = 12/20 = 60% overall vs KXBAD's own 20%.
    rows = _segment_rows("KXBAD", 10, win_n=2) + _segment_rows("KXGOOD", 10, win_n=10)
    category_rows = [_category_row("Sports", 10, 20.0)]  # gates the `if category_rows:` block
    result = ae.generate_recommendations(
        rows, _cfg(), "fp1", {}, min_resolved_trades=5, category_rows=category_rows,
    )
    paths = [r["config_path"] for r in result["recommendations"]]
    assert "strategy_overrides.by_series" in paths


# --- change_effect (Item 3D, 2026-08-10) --------------------------------------

def test_change_effect_none_when_fingerprint_unchanged():
    # risk.*/advisory.*/etc. changes always log the same fingerprint
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


# --- change_effect_windowed (Gap 3, docs/config-tuning-data-gaps-2026-08-10.md) ---

def _ts_row(entry_timestamp, **overrides):
    return _row(entry_timestamp=entry_timestamp, **overrides)


def test_change_effect_windowed_none_with_no_trades_before_the_change():
    rows = [_ts_row(2000.0, won=True, realized_pnl=5.0)]
    assert ae.change_effect_windowed("strategy.entry_threshold", 1000.0, rows) is None


def test_change_effect_windowed_none_with_no_trades_after_the_change():
    rows = [_ts_row(500.0, won=True, realized_pnl=5.0)]
    assert ae.change_effect_windowed("strategy.entry_threshold", 1000.0, rows) is None


def test_change_effect_windowed_splits_on_entry_timestamp_not_fingerprint():
    # Deliberately all one fingerprint - unlike change_effect(), this must
    # not require a fingerprint transition at all, since it's the only
    # effect measurement risk.*/advisory.*/etc. changes can ever get.
    rows = [
        _ts_row(100.0, config_fingerprint="fp1", won=False, realized_pnl=-10.0, close_type="stop_loss"),
        _ts_row(200.0, config_fingerprint="fp1", won=False, realized_pnl=-8.0, close_type="stop_loss"),
        _ts_row(1500.0, config_fingerprint="fp1", won=True, realized_pnl=12.0, close_type="settled_win"),
        _ts_row(1600.0, config_fingerprint="fp1", won=True, realized_pnl=9.0, close_type="settled_win"),
    ]
    effect = ae.change_effect_windowed("risk.max_daily_loss_pct", 1000.0, rows)
    assert effect == {
        "before_win_rate_pct": 0.0, "before_n": 2, "before_realized_pnl": -18.0,
        "after_win_rate_pct": 100.0, "after_n": 2, "after_realized_pnl": 21.0,
    }


def test_change_effect_windowed_boundary_trade_counts_as_after():
    rows = [
        _ts_row(999.0, won=False, realized_pnl=-5.0),
        _ts_row(1000.0, won=True, realized_pnl=5.0),  # exactly at applied_at
    ]
    effect = ae.change_effect_windowed("strategy.entry_threshold", 1000.0, rows)
    assert effect["before_n"] == 1
    assert effect["after_n"] == 1


def test_change_effect_windowed_ignores_rows_with_no_entry_timestamp():
    rows = [
        _row(entry_timestamp=None, won=True, realized_pnl=100.0),  # unattributed - excluded from both sides
        _ts_row(500.0, won=False, realized_pnl=-5.0),
        _ts_row(1500.0, won=True, realized_pnl=5.0),
    ]
    effect = ae.change_effect_windowed("strategy.entry_threshold", 1000.0, rows)
    assert effect["before_n"] == 1
    assert effect["after_n"] == 1
