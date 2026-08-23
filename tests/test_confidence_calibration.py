import pytest

from services.whale_calibration import confidence_calibration as cc
from services.confidence_scoring import DEFAULT_WEIGHTS


def _row(depth, unusualness, proximity, context, agreement, correct, confidence=0.5, cluster=0.0, trend=0.5, analyst=0.5):
    return {
        "confidence": confidence,
        "correct": correct,
        "factors": {
            "depth_factor": depth, "unusualness_factor": unusualness,
            "proximity_factor": proximity, "context_factor": context,
            "agreement_factor": agreement, "cluster_factor": cluster,
            "trend_factor": trend, "analyst_factor": analyst, "score": confidence,
        },
    }


def _discriminating_dataset(n_per_bucket=3):
    """depth_factor cleanly predicts correctness (low third all wrong, high
    third all right); every other factor is held constant (no discrimination
    possible - always lands in one bucket, so gap_pts stays None for those),
    isolating depth_factor as the only real signal in this fixture."""
    rows = []
    for i in range(n_per_bucket):
        rows.append(_row(depth=0.1 + i * 0.01, unusualness=0.5, proximity=0.5, context=0.5, agreement=0.5, correct=False))
    for i in range(n_per_bucket):
        rows.append(_row(depth=0.5 + i * 0.01, unusualness=0.5, proximity=0.5, context=0.5, agreement=0.5, correct=False))
    for i in range(n_per_bucket):
        rows.append(_row(depth=0.9 + i * 0.01, unusualness=0.5, proximity=0.5, context=0.5, agreement=0.5, correct=True))
    return rows


def test_gated_below_threshold_returns_no_report():
    rows = _discriminating_dataset(n_per_bucket=2)  # 6 rows
    result = cc.generate_calibration_report(rows, min_resolved_signals=50)
    assert result["report"] is None
    assert result["resolved_count"] == 6
    assert "6/50" in result["gated_reason"]


def test_gated_exactly_at_threshold_returns_a_report():
    rows = _discriminating_dataset(n_per_bucket=10)  # 30 rows
    result = cc.generate_calibration_report(rows, min_resolved_signals=30)
    assert result["report"] is not None
    assert result["gated_reason"] is None
    assert result["resolved_count"] == 30


def test_discriminating_factor_is_flagged_with_correct_gap():
    rows = _discriminating_dataset(n_per_bucket=10)
    result = cc.generate_calibration_report(rows, min_resolved_signals=30)
    depth_report = next(f for f in result["report"]["per_factor"] if f["factor"] == "depth_factor")
    assert depth_report["buckets"]["low"]["win_rate"] == 0.0
    assert depth_report["buckets"]["high"]["win_rate"] == 100.0
    assert depth_report["gap_pts"] == 100.0
    assert depth_report["discriminates"] is True


def test_constant_factor_does_not_discriminate():
    rows = _discriminating_dataset(n_per_bucket=10)
    result = cc.generate_calibration_report(rows, min_resolved_signals=30)
    # unusualness_factor is constant (0.5) across every row in this fixture -
    # every row lands in the same bucket, so low/high can't both be
    # populated and gap_pts stays None (not zero - genuinely undetermined,
    # not "no gap").
    unusual_report = next(f for f in result["report"]["per_factor"] if f["factor"] == "unusualness_factor")
    assert unusual_report["gap_pts"] is None
    assert unusual_report["discriminates"] is None


def test_missing_factor_key_excluded_not_crashed():
    # Real bug found live (2026-08-10, consulting real historical data
    # while setting sensible config defaults): cluster_factor/trend_factor/
    # analyst_factor were all added to composite_confidence_breakdown
    # after this app had already logged its first ~9000 real signals, so
    # every one of those rows' factors dict genuinely lacks those three
    # keys - confirmed against the real data/signal_log.db, not assumed.
    # generate_calibration_report() used to crash with a bare KeyError the
    # first time this ran against real production history; it must now
    # exclude those rows from that specific factor's bucketing instead,
    # same "leave it out when absent" idiom used everywhere else in this
    # app for an optional factor.
    rows = []
    for i in range(30):
        row = _row(
            depth=0.1 + (i % 3) * 0.4, unusualness=0.5, proximity=0.5, context=0.5,
            agreement=0.5, correct=(i % 3 == 2),
        )
        del row["factors"]["cluster_factor"]
        del row["factors"]["trend_factor"]
        del row["factors"]["analyst_factor"]
        rows.append(row)
    result = cc.generate_calibration_report(rows, min_resolved_signals=30)
    assert result["report"] is not None  # did not crash
    cluster_report = next(f for f in result["report"]["per_factor"] if f["factor"] == "cluster_factor")
    assert cluster_report["buckets"] == {}
    assert cluster_report["gap_pts"] is None
    assert cluster_report["discriminates"] is None
    # depth_factor is present on every row and still discriminates normally.
    depth_report = next(f for f in result["report"]["per_factor"] if f["factor"] == "depth_factor")
    assert depth_report["discriminates"] is True


def test_report_current_weights_defaults_to_default_weights():
    rows = _discriminating_dataset(n_per_bucket=10)
    result = cc.generate_calibration_report(rows, min_resolved_signals=30)
    assert result["report"]["current_weights"] == DEFAULT_WEIGHTS


def test_report_current_weights_reflects_a_live_config_override():
    rows = _discriminating_dataset(n_per_bucket=10)
    custom = {**DEFAULT_WEIGHTS, "depth_factor": 0.5}
    result = cc.generate_calibration_report(rows, min_resolved_signals=30, current_weights=custom)
    assert result["report"]["current_weights"]["depth_factor"] == 0.5


def test_report_current_weights_fills_in_missing_keys_from_default():
    rows = _discriminating_dataset(n_per_bucket=10)
    result = cc.generate_calibration_report(rows, min_resolved_signals=30, current_weights={"depth_factor": 0.5})
    assert result["report"]["current_weights"]["depth_factor"] == 0.5
    assert result["report"]["current_weights"]["analyst_factor"] == DEFAULT_WEIGHTS["analyst_factor"]


def test_ranked_by_discrimination_puts_the_real_signal_first():
    rows = _discriminating_dataset(n_per_bucket=10)
    result = cc.generate_calibration_report(rows, min_resolved_signals=30)
    assert result["report"]["ranked_by_discrimination"][0] == "depth_factor"


def test_suggested_weights_favor_the_discriminating_factor():
    rows = _discriminating_dataset(n_per_bucket=10)
    result = cc.generate_calibration_report(rows, min_resolved_signals=30)
    weights = result["report"]["suggested_weights"]
    assert weights is not None
    assert weights["depth_factor"] > DEFAULT_WEIGHTS["depth_factor"]
    assert all(w >= cc._MIN_SUGGESTED_WEIGHT - 1e-9 for w in weights.values())  # nothing zeroed out
    assert sum(weights.values()) == pytest.approx(1.0, abs=0.05)


def test_suggested_weights_is_none_when_nothing_discriminates():
    # Every factor constant, correctness alternates with no relation to any
    # of them - no real signal to suggest weights from.
    rows = [
        _row(depth=0.5, unusualness=0.5, proximity=0.5, context=0.5, agreement=0.5, correct=(i % 2 == 0))
        for i in range(30)
    ]
    result = cc.generate_calibration_report(rows, min_resolved_signals=30)
    assert result["report"]["suggested_weights"] is None


# --- blended_weights_for_auto_apply (2026-08-10, auto-apply path) -----------

def test_blended_weights_none_when_no_suggestion():
    assert cc.blended_weights_for_auto_apply(DEFAULT_WEIGHTS, None) is None
    assert cc.blended_weights_for_auto_apply(DEFAULT_WEIGHTS, {}) is None


def test_blended_weights_leaves_factors_with_no_data_untouched():
    current = dict(DEFAULT_WEIGHTS)
    # Only depth_factor/unusualness_factor have a real suggestion - the
    # other 6 factors (including cluster/trend/analyst, which as of this
    # writing have zero real discrimination data) must keep their current
    # *relative* proportions to each other untouched - the raw numbers all
    # shift slightly on renormalization (checked separately below), but
    # cluster_factor:trend_factor's own ratio shouldn't move just because
    # depth_factor/unusualness_factor changed.
    suggested = {"depth_factor": 0.5, "unusualness_factor": 0.1}
    blended = cc.blended_weights_for_auto_apply(current, suggested)
    assert blended is not None
    current_ratio = current["cluster_factor"] / current["trend_factor"]
    blended_ratio = blended["cluster_factor"] / blended["trend_factor"]
    assert blended_ratio == pytest.approx(current_ratio, rel=0.01)  # blended() rounds to 4dp


def test_blended_weights_sums_to_one():
    current = dict(DEFAULT_WEIGHTS)
    suggested = {"depth_factor": 0.4, "unusualness_factor": 0.2, "proximity_factor": 0.1}
    blended = cc.blended_weights_for_auto_apply(current, suggested)
    assert sum(blended.values()) == pytest.approx(1.0, abs=1e-3)  # blended() rounds each value to 4dp


def test_blended_weights_uses_suggested_value_for_covered_factors_proportionally():
    # Before renormalization, depth_factor's suggested share (0.5) is 5x
    # unusualness_factor's (0.1) - that 5:1 ratio must survive
    # renormalization even though the absolute numbers change.
    current = dict(DEFAULT_WEIGHTS)
    suggested = {"depth_factor": 0.5, "unusualness_factor": 0.1}
    blended = cc.blended_weights_for_auto_apply(current, suggested)
    assert blended["depth_factor"] / blended["unusualness_factor"] == pytest.approx(5.0, abs=0.01)


def test_overall_win_rate_and_confidence_label_present():
    rows = _discriminating_dataset(n_per_bucket=10)  # 20 wrong, 10 right = 33.3%
    result = cc.generate_calibration_report(rows, min_resolved_signals=30)
    assert result["report"]["overall_win_rate"] == pytest.approx(33.3, abs=0.5)
    assert result["report"]["confidence_label"] == "higher"  # n=30


def test_overall_win_rate_margin_of_error_present_and_reasonable():
    # 2026-08-15, real gap closed: the auto-apply gate was a bare sample-
    # size floor with no confidence-interval framing, despite services/
    # stats_power.py's real margin-of-error math already existing and
    # already being used for this exact question elsewhere (main.py's
    # series-evaluator win-rate cross-check).
    rows = _discriminating_dataset(n_per_bucket=10)  # n=30, ~33.3% win rate
    result = cc.generate_calibration_report(rows, min_resolved_signals=30)
    from services import stats_power
    expected = round(stats_power.margin_of_error_pts(30, result["report"]["overall_win_rate"]), 1)
    assert result["report"]["overall_win_rate_margin_pts"] == expected
    assert 0 < result["report"]["overall_win_rate_margin_pts"] < 50  # a real, finite, sane margin at n=30


# ---- overall confidence-score calibration (distinct from per-factor discrimination) ----

def test_well_calibrated_band_shows_a_small_gap():
    # 10 signals at ~65% confidence, 6 win (60%) and 4 lose - close to what
    # a genuinely well-calibrated 60-70%-confidence band should look like.
    rows = (
        [_row(0.5, 0.5, 0.5, 0.5, 0.5, correct=True, confidence=0.65) for _ in range(6)]
        + [_row(0.5, 0.5, 0.5, 0.5, 0.5, correct=False, confidence=0.65) for _ in range(4)]
    )
    result = cc.generate_calibration_report(rows, min_resolved_signals=10)
    bands = result["report"]["confidence_calibration"]
    band = next(b for b in bands if b["band"] == "60-70%")
    assert band["n"] == 10
    assert band["predicted_pct"] == 65.0
    assert band["observed_win_rate_pct"] == 60.0
    assert band["gap_pts"] == pytest.approx(-5.0)


def test_overconfident_band_shows_a_large_negative_gap():
    # 10 signals at ~85% confidence but only 3 actually win (30%) - exactly
    # the "confidence doesn't mean what it claims to" case this exists to
    # surface, distinct from whether any individual factor discriminates.
    rows = (
        [_row(0.5, 0.5, 0.5, 0.5, 0.5, correct=True, confidence=0.85) for _ in range(3)]
        + [_row(0.5, 0.5, 0.5, 0.5, 0.5, correct=False, confidence=0.85) for _ in range(7)]
    )
    result = cc.generate_calibration_report(rows, min_resolved_signals=10)
    band = next(b for b in result["report"]["confidence_calibration"] if b["band"] == "80-90%")
    assert band["observed_win_rate_pct"] == 30.0
    assert band["gap_pts"] < -40  # badly overconfident


def test_bands_with_too_few_signals_are_omitted():
    # 2 signals at ~95% confidence - below _MIN_BAND_SIZE (3), shouldn't be
    # reported as a finding on that thin a sample. Padded with an unrelated
    # well-sampled band so the report itself still gates open.
    rows = (
        [_row(0.5, 0.5, 0.5, 0.5, 0.5, correct=True, confidence=0.95) for _ in range(2)]
        + [_row(0.5, 0.5, 0.5, 0.5, 0.5, correct=True, confidence=0.55) for _ in range(10)]
    )
    result = cc.generate_calibration_report(rows, min_resolved_signals=12)
    labels = [b["band"] for b in result["report"]["confidence_calibration"]]
    assert "90-100%" not in labels
    assert "50-60%" in labels


def test_confidence_exactly_one_lands_in_top_band():
    rows = [_row(0.5, 0.5, 0.5, 0.5, 0.5, correct=True, confidence=1.0) for _ in range(3)]
    bands = cc._confidence_calibration_bands(rows)
    assert bands[0]["band"] == "90-100%"
    assert bands[0]["n"] == 3
