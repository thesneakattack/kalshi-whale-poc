import pytest
from services import candlestick_volatility as cv


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    monkeypatch.setattr(cv, "DB_PATH", tmp_path / "candlestick_volatility.db")


def _bar(end_period_ts, yes_bid_close, yes_bid_open=None, yes_bid_high=None, yes_bid_low=None,
         price_close=None, volume_fp=10.0, open_interest_fp=5.0):
    # Defaults let existing single-value tests keep passing unchanged
    # (open/high/low collapse to the same value as close when not given
    # explicitly) while dedicated OHLC tests pass all four.
    yes_bid_open = yes_bid_close if yes_bid_open is None else yes_bid_open
    yes_bid_high = yes_bid_close if yes_bid_high is None else yes_bid_high
    yes_bid_low = yes_bid_close if yes_bid_low is None else yes_bid_low
    return {
        "end_period_ts": end_period_ts,
        "yes_bid": {
            "open_dollars": yes_bid_open, "high_dollars": yes_bid_high,
            "low_dollars": yes_bid_low, "close_dollars": yes_bid_close,
        },
        "price": {"close_dollars": price_close},
        "volume_fp": volume_fp,
        "open_interest_fp": open_interest_fp,
    }


def test_record_candles_stores_full_yes_bid_ohlc_not_just_close():
    bars = [_bar(1000.0, yes_bid_close=0.42, yes_bid_open=0.38, yes_bid_high=0.45, yes_bid_low=0.36, price_close=None)]
    n = cv.record_candles("TICK-A", "SERIES-A", 60, bars, fetched_at=2000.0)
    assert n == 1
    with cv._connect() as conn:
        row = conn.execute(
            "SELECT yes_bid_open, yes_bid_high, yes_bid_low, yes_bid_close FROM candles WHERE ticker = ?",
            ("TICK-A",),
        ).fetchone()
    assert row == (0.38, 0.45, 0.36, 0.42)


def test_record_candles_skips_a_bar_missing_end_period_ts_or_yes_bid_close():
    bars = [
        {"yes_bid": {"close_dollars": 0.5}, "price": {}, "volume_fp": 1, "open_interest_fp": 1},
        {"end_period_ts": 1100.0, "yes_bid": {}, "price": {}, "volume_fp": 1, "open_interest_fp": 1},
        _bar(1200.0, 0.55),
    ]
    n = cv.record_candles("TICK-A", "SERIES-A", 60, bars, fetched_at=2000.0)
    assert n == 1
    # A bar with open_dollars/high_dollars/low_dollars present but close_dollars
    # missing is also skipped - close is the one currently used by volatility(),
    # so a bar without it is as unusable as one missing end_period_ts.
    bad_bar = {
        "end_period_ts": 1300.0,
        "yes_bid": {"open_dollars": 0.5, "high_dollars": 0.5, "low_dollars": 0.5},
        "price": {}, "volume_fp": 1, "open_interest_fp": 1,
    }
    n2 = cv.record_candles("TICK-A", "SERIES-A", 60, [bad_bar], fetched_at=2000.0)
    assert n2 == 0


def test_record_candles_upserts_idempotently_on_overlapping_refetch():
    cv.record_candles("TICK-A", "SERIES-A", 60, [_bar(1000.0, 0.40)], fetched_at=2000.0)
    cv.record_candles("TICK-A", "SERIES-A", 60, [_bar(1000.0, 0.48)], fetched_at=2100.0)
    with cv._connect() as conn:
        rows = conn.execute(
            "SELECT yes_bid_close FROM candles WHERE ticker = ? AND end_period_ts = ?",
            ("TICK-A", 1000.0),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 0.48


def test_volatility_none_with_fewer_than_three_bars_in_window():
    cv.record_candles("TICK-A", "SERIES-A", 60, [_bar(1000.0, 0.40), _bar(1060.0, 0.42)], fetched_at=2000.0)
    assert cv.volatility("TICK-A", lookback_sec=200, as_of=1100.0) is None


def test_volatility_computes_population_stdev_of_consecutive_close_deltas():
    bars = [_bar(1000.0, 0.40), _bar(1060.0, 0.42), _bar(1120.0, 0.39), _bar(1180.0, 0.45)]
    cv.record_candles("TICK-A", "SERIES-A", 60, bars, fetched_at=2000.0)
    deltas = [0.02, -0.03, 0.06]
    mean = sum(deltas) / 3
    expected = (sum((d - mean) ** 2 for d in deltas) / 3) ** 0.5
    result = cv.volatility("TICK-A", lookback_sec=200, as_of=1180.0)
    assert result == pytest.approx(expected)


def test_volatility_ignores_bars_outside_the_lookback_window():
    bars = [_bar(500.0, 0.10), _bar(1000.0, 0.40), _bar(1060.0, 0.42), _bar(1120.0, 0.39)]
    cv.record_candles("TICK-A", "SERIES-A", 60, bars, fetched_at=2000.0)
    result = cv.volatility("TICK-A", lookback_sec=200, as_of=1120.0)
    deltas = [0.02, -0.03]
    mean = sum(deltas) / 2
    expected = (sum((d - mean) ** 2 for d in deltas) / 2) ** 0.5
    assert result == pytest.approx(expected)


def test_volatility_ignores_bars_after_as_of():
    bars = [_bar(1000.0, 0.40), _bar(1060.0, 0.42), _bar(1120.0, 0.39), _bar(9999.0, 0.99)]
    cv.record_candles("TICK-A", "SERIES-A", 60, bars, fetched_at=2000.0)
    result = cv.volatility("TICK-A", lookback_sec=200, as_of=1120.0)
    assert result is not None and result < 0.5


def test_volatility_respects_period_interval_min_filter():
    cv.record_candles("TICK-A", "SERIES-A", 60, [_bar(1000.0, 0.40), _bar(1060.0, 0.42), _bar(1120.0, 0.39)], fetched_at=2000.0)
    cv.record_candles("TICK-A", "SERIES-A", 1, [_bar(1000.0, 0.90), _bar(1010.0, 0.10), _bar(1020.0, 0.90)], fetched_at=2000.0)
    result_60 = cv.volatility("TICK-A", lookback_sec=200, as_of=1120.0, period_interval_min=60)
    result_1 = cv.volatility("TICK-A", lookback_sec=200, as_of=1120.0, period_interval_min=1)
    assert result_60 is not None and result_1 is not None
    assert result_60 != result_1


def test_last_fetched_at_returns_the_most_recent_fetched_at_for_a_ticker():
    cv.record_candles("TICK-A", "SERIES-A", 60, [_bar(1000.0, 0.40)], fetched_at=2000.0)
    cv.record_candles("TICK-A", "SERIES-A", 60, [_bar(1060.0, 0.42)], fetched_at=2500.0)
    assert cv.last_fetched_at("TICK-A") == 2500.0


def test_last_fetched_at_returns_none_for_an_unfetched_ticker():
    assert cv.last_fetched_at("NEVER-FETCHED") is None


def test_bar_count_and_clear_all():
    cv.record_candles("TICK-A", "SERIES-A", 60, [_bar(1000.0, 0.40)], fetched_at=2000.0)
    cv.record_candles("TICK-B", "SERIES-B", 60, [_bar(1000.0, 0.40)], fetched_at=2000.0)
    assert cv.bar_count() == 2
    assert cv.bar_count("TICK-A") == 1
    cv.clear_all()
    assert cv.bar_count() == 0
