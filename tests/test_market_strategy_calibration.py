from services import market_strategy_calibration as msc


def _row(entry_confidence, won):
    return {"entry_confidence": entry_confidence, "won": won, "close_type": "settled_win" if won else "settled_loss"}


def test_gated_below_threshold_returns_no_report():
    rows = [_row(0.6, True) for _ in range(5)]
    result = msc.generate_calibration_report(rows, min_resolved_trades=20)
    assert result["report"] is None
    assert result["resolved_count"] == 5
    assert "5/20" in result["gated_reason"]


def test_gated_exactly_at_threshold_returns_a_report():
    rows = [_row(0.6, True) for _ in range(20)]
    result = msc.generate_calibration_report(rows, min_resolved_trades=20)
    assert result["report"] is not None
    assert result["gated_reason"] is None
    assert result["resolved_count"] == 20


def test_overall_win_rate_and_confidence_label_present():
    rows = [_row(0.6, True) for _ in range(10)] + [_row(0.6, False) for _ in range(10)]
    result = msc.generate_calibration_report(rows, min_resolved_trades=20)
    report = result["report"]
    assert report["overall_win_rate"] == 50.0
    assert report["confidence_label"] == "higher"  # n=20 >= 15
    assert report["with_confidence_count"] == 20


def test_rows_missing_entry_confidence_excluded_from_win_rate_but_counted_in_resolved():
    rows = [_row(0.6, True) for _ in range(15)] + [_row(None, True) for _ in range(5)]
    result = msc.generate_calibration_report(rows, min_resolved_trades=20)
    report = result["report"]
    assert report["resolved_count"] == 20  # every closed trade counts toward the gate
    assert report["with_confidence_count"] == 15  # only ones with a real confidence value


def test_overall_win_rate_none_when_no_row_has_confidence():
    rows = [_row(None, True) for _ in range(20)]
    result = msc.generate_calibration_report(rows, min_resolved_trades=20)
    assert result["report"]["overall_win_rate"] is None


def test_well_calibrated_band_shows_a_small_gap():
    # 60-70% band, predicted midpoint 65% - 2 of 3 win = 66.7% observed.
    rows = [_row(0.65, True), _row(0.65, True), _row(0.65, False)]
    rows += [_row(0.2, True) for _ in range(20)]  # pad resolved_count past the gate
    result = msc.generate_calibration_report(rows, min_resolved_trades=20)
    bands = {b["band"]: b for b in result["report"]["confidence_calibration"]}
    assert bands["60-70%"]["n"] == 3
    assert bands["60-70%"]["predicted_pct"] == 65.0
    assert bands["60-70%"]["observed_win_rate_pct"] == 66.7
    assert bands["60-70%"]["gap_pts"] == 1.7


def test_band_dropped_below_min_band_size():
    rows = [_row(0.65, True), _row(0.65, False)]  # only 2, below _MIN_BAND_SIZE=3
    rows += [_row(0.2, True) for _ in range(20)]
    result = msc.generate_calibration_report(rows, min_resolved_trades=20)
    bands = [b["band"] for b in result["report"]["confidence_calibration"]]
    assert "60-70%" not in bands


def test_rows_with_no_entry_confidence_excluded_from_bands():
    rows = [_row(None, True) for _ in range(3)]
    rows += [_row(0.2, True) for _ in range(20)]
    result = msc.generate_calibration_report(rows, min_resolved_trades=20)
    bands = [b["band"] for b in result["report"]["confidence_calibration"]]
    assert "<50%" in bands  # the padding rows still form a real band
    # the 3 None-confidence rows contributed to no band at all
    total_banded = sum(b["n"] for b in result["report"]["confidence_calibration"])
    assert total_banded == 20  # not 23
