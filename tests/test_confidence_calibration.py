import pytest

from services import confidence_calibration as cc


def _row(depth, unusualness, proximity, context, agreement, correct, confidence=0.5, cluster=0.0, trend=0.5):
    return {
        "confidence": confidence,
        "correct": correct,
        "factors": {
            "depth_factor": depth, "unusualness_factor": unusualness,
            "proximity_factor": proximity, "context_factor": context,
            "agreement_factor": agreement, "cluster_factor": cluster,
            "trend_factor": trend, "score": confidence,
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


def test_ranked_by_discrimination_puts_the_real_signal_first():
    rows = _discriminating_dataset(n_per_bucket=10)
    result = cc.generate_calibration_report(rows, min_resolved_signals=30)
    assert result["report"]["ranked_by_discrimination"][0] == "depth_factor"


def test_suggested_weights_favor_the_discriminating_factor():
    rows = _discriminating_dataset(n_per_bucket=10)
    result = cc.generate_calibration_report(rows, min_resolved_signals=30)
    weights = result["report"]["suggested_weights"]
    assert weights is not None
    assert weights["depth_factor"] > cc.CURRENT_WEIGHTS["depth_factor"]
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


def test_overall_win_rate_and_confidence_label_present():
    rows = _discriminating_dataset(n_per_bucket=10)  # 20 wrong, 10 right = 33.3%
    result = cc.generate_calibration_report(rows, min_resolved_signals=30)
    assert result["report"]["overall_win_rate"] == pytest.approx(33.3, abs=0.5)
    assert result["report"]["confidence_label"] == "higher"  # n=30


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
