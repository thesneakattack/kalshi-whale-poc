import pytest

from services import backtest


# --- entry_threshold_sweep ---------------------------------------------------

def test_entry_threshold_sweep_default_thresholds_span_0_to_95():
    result = backtest.entry_threshold_sweep([])
    thresholds = [r["threshold"] for r in result]
    assert thresholds[0] == 0.0
    assert thresholds[-1] == 0.95
    assert len(thresholds) == 20


def test_entry_threshold_sweep_none_win_rate_when_nothing_clears_the_bar():
    rows = [{"confidence": 0.3, "correct": True}]
    result = backtest.entry_threshold_sweep(rows, thresholds=[0.9])
    assert result == [{"threshold": 0.9, "n": 0, "win_rate_pct": None}]


def test_entry_threshold_sweep_counts_signals_at_or_above_threshold():
    rows = [
        {"confidence": 0.9, "correct": True},
        {"confidence": 0.6, "correct": True},
        {"confidence": 0.6, "correct": False},
        {"confidence": 0.3, "correct": False},
    ]
    result = backtest.entry_threshold_sweep(rows, thresholds=[0.0, 0.6, 0.9])
    by_threshold = {r["threshold"]: r for r in result}
    assert by_threshold[0.0]["n"] == 4
    assert by_threshold[0.6]["n"] == 3  # excludes the 0.3-confidence row
    assert by_threshold[0.6]["win_rate_pct"] == pytest.approx(66.7, abs=0.1)
    assert by_threshold[0.9]["n"] == 1
    assert by_threshold[0.9]["win_rate_pct"] == 100.0


def test_entry_threshold_sweep_boundary_is_inclusive():
    rows = [{"confidence": 0.6, "correct": True}]
    result = backtest.entry_threshold_sweep(rows, thresholds=[0.6])
    assert result[0]["n"] == 1  # >= 0.6, not > 0.6


# --- min_whale_winrate_pct_sweep ---------------------------------------------

def test_min_whale_winrate_sweep_excludes_low_winrate_series_at_higher_floors():
    series_stats = {
        "AAA": {"resolved": 20, "win_rate": 60.0},
        "BBB": {"resolved": 20, "win_rate": 30.0},
    }
    signal_rows = (
        [{"series": "AAA", "correct": True}] * 12 + [{"series": "AAA", "correct": False}] * 8
        + [{"series": "BBB", "correct": True}] * 6 + [{"series": "BBB", "correct": False}] * 14
    )
    result = backtest.min_whale_winrate_pct_sweep(series_stats, signal_rows, floors=[0, 40, 70])
    by_floor = {r["floor"]: r for r in result}
    assert by_floor[0]["excluded_series_count"] == 0
    assert by_floor[0]["n"] == 40  # both series' signals pooled
    assert by_floor[40]["excluded_series_count"] == 1  # BBB (30% < 40%) excluded
    assert by_floor[40]["n"] == 20  # only AAA's signals remain
    assert by_floor[40]["win_rate_pct"] == 60.0
    assert by_floor[70]["excluded_series_count"] == 2  # both below 70%
    assert by_floor[70]["n"] == 0
    assert by_floor[70]["win_rate_pct"] is None


def test_min_whale_winrate_sweep_respects_min_resolved_for_filter():
    # BBB has a bad win rate but too few resolved signals to trust it - same
    # sample-size hedge strategy_engine.py's real gate applies.
    series_stats = {
        "AAA": {"resolved": 20, "win_rate": 60.0},
        "BBB": {"resolved": 3, "win_rate": 0.0},
    }
    signal_rows = [{"series": "AAA", "correct": True}] * 12 + [{"series": "AAA", "correct": False}] * 8
    signal_rows += [{"series": "BBB", "correct": False}] * 3
    result = backtest.min_whale_winrate_pct_sweep(series_stats, signal_rows, floors=[50], min_resolved_for_filter=10)
    assert result[0]["excluded_series_count"] == 0  # BBB not excluded - under the resolved-count floor
    assert result[0]["n"] == 23


def test_min_whale_winrate_sweep_default_floors_span_0_to_100():
    result = backtest.min_whale_winrate_pct_sweep({}, [])
    floors = [r["floor"] for r in result]
    assert floors[0] == 0
    assert floors[-1] == 100
