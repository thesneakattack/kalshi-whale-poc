import calendar
import time

import pytest

from services import regime_analytics as ra
from services import trade_category as tc


@pytest.fixture(autouse=True)
def _redirect_trade_category_db(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "DB_PATH", tmp_path / "trade_category.db")


def _row(entry_timestamp, won=True, realized_pnl=10.0, ticker="TICK-A"):
    return {
        "entry_timestamp": entry_timestamp, "won": won, "realized_pnl": realized_pnl,
        "hold_sec": 100.0, "left_on_table": None, "cost_basis": 50.0,
        "close_type": "settled_win" if won else "settled_loss", "ticker": ticker,
    }


def _ts_at_hour(hour_utc):
    # A fixed, known Wednesday (2026-08-12) at the given UTC hour.
    return calendar.timegm((2026, 8, 12, hour_utc, 0, 0, 0, 0, 0))


def _ts_on_weekday(tm_wday):
    # 2026-08-10 is a Monday (tm_wday=0); offset from there.
    return calendar.timegm((2026, 8, 10, 12, 0, 0, 0, 0, 0)) + tm_wday * 86400


# --- by_hour_of_day -----------------------------------------------------

def test_by_hour_of_day_groups_correctly():
    rows = [_row(_ts_at_hour(9)), _row(_ts_at_hour(9), won=False), _row(_ts_at_hour(14))]
    result = ra.by_hour_of_day(rows)
    by_hour = {r["hour_utc"]: r for r in result}
    assert by_hour[9]["total_closed"] == 2
    assert by_hour[9]["win_rate_pct"] == 50.0
    assert by_hour[14]["total_closed"] == 1


def test_by_hour_of_day_skips_rows_with_no_entry_timestamp():
    rows = [_row(None), _row(_ts_at_hour(9))]
    result = ra.by_hour_of_day(rows)
    assert len(result) == 1
    assert result[0]["total_closed"] == 1


def test_by_hour_of_day_only_returns_hours_with_data():
    rows = [_row(_ts_at_hour(3))]
    result = ra.by_hour_of_day(rows)
    assert len(result) == 1
    assert result[0]["hour_utc"] == 3


def test_by_hour_of_day_sorted_ascending():
    rows = [_row(_ts_at_hour(20)), _row(_ts_at_hour(2)), _row(_ts_at_hour(11))]
    result = ra.by_hour_of_day(rows)
    assert [r["hour_utc"] for r in result] == [2, 11, 20]


def test_by_hour_of_day_empty_rows_returns_empty_list():
    assert ra.by_hour_of_day([]) == []


# --- by_day_of_week -------------------------------------------------------

def test_by_day_of_week_groups_correctly():
    rows = [_row(_ts_on_weekday(0)), _row(_ts_on_weekday(0), won=False), _row(_ts_on_weekday(5))]
    result = ra.by_day_of_week(rows)
    by_dow = {r["day_of_week"]: r for r in result}
    assert by_dow[0]["total_closed"] == 2
    assert by_dow[5]["total_closed"] == 1


def test_by_day_of_week_sorted_ascending():
    rows = [_row(_ts_on_weekday(6)), _row(_ts_on_weekday(1))]
    result = ra.by_day_of_week(rows)
    assert [r["day_of_week"] for r in result] == [1, 6]


def test_by_day_of_week_skips_rows_with_no_entry_timestamp():
    rows = [_row(None)]
    assert ra.by_day_of_week(rows) == []


# --- by_category (2026-08-10, category-at-entry-time capture) --------------

def test_by_category_groups_correctly():
    tc.record_category("TICK-A", "Sports")
    tc.record_category("TICK-B", "Politics")
    rows = [_row(1000.0, ticker="TICK-A"), _row(1000.0, won=False, ticker="TICK-A"), _row(1000.0, ticker="TICK-B")]
    result = ra.by_category(rows)
    by_cat = {r["category"]: r for r in result}
    assert by_cat["Sports"]["total_closed"] == 2
    assert by_cat["Politics"]["total_closed"] == 1


def test_by_category_excludes_tickers_with_no_recorded_category():
    # A trade entered before trade_category.py existed, or otherwise never
    # captured - excluded, not backfilled with a guess.
    rows = [_row(1000.0, ticker="TICK-UNKNOWN")]
    assert ra.by_category(rows) == []


def test_by_category_sorted_by_count_descending():
    tc.record_category("TICK-A", "Sports")
    tc.record_category("TICK-B", "Politics")
    rows = [_row(1000.0, ticker="TICK-A"), _row(1000.0, ticker="TICK-B"),
            _row(1000.0, ticker="TICK-B"), _row(1000.0, ticker="TICK-B")]
    result = ra.by_category(rows)
    assert result[0]["category"] == "Politics"  # 3 trades, vs Sports' 1
    assert result[0]["total_closed"] == 3


def test_by_category_empty_rows_returns_empty_list():
    assert ra.by_category([]) == []


# --- by_series (2026-08-16, "it makes more sense to do it by series") -----

def test_by_series_groups_by_ticker_prefix_not_exact_ticker():
    # A market ticker never recurs (KXBTC15M-26AUG161645-45 only exists
    # once) - the series prefix (KXBTC15M) is the real recurring unit, so
    # two different exact tickers sharing a series prefix must fold into
    # one bucket.
    rows = [
        _row(1000.0, ticker="KXBTC15M-26AUG161645-45"),
        _row(1000.0, won=False, ticker="KXBTC15M-26AUG161700-00"),
        _row(1000.0, ticker="KXMLBGAME-26AUG161410PHIMIN-PHI"),
    ]
    result = ra.by_series(rows)
    by_series = {r["series"]: r for r in result}
    assert by_series["KXBTC15M"]["total_closed"] == 2
    assert by_series["KXBTC15M"]["win_rate_pct"] == 50.0
    assert by_series["KXMLBGAME"]["total_closed"] == 1


def test_by_series_needs_no_recorded_category_at_all():
    # Unlike by_category/by_subcategory, series is a pure function of the
    # ticker already on the row - no trade_category.py lookup involved, so
    # this works even for a ticker that never got a category recorded.
    rows = [_row(1000.0, ticker="KXBTC15M-26AUG161645-45")]
    assert ra.by_series(rows)[0]["series"] == "KXBTC15M"


def test_by_series_sorted_by_count_descending():
    rows = [
        _row(1000.0, ticker="AAA-1"), _row(1000.0, ticker="BBB-1"),
        _row(1000.0, ticker="BBB-2"), _row(1000.0, ticker="BBB-3"),
    ]
    result = ra.by_series(rows)
    assert result[0]["series"] == "BBB"
    assert result[0]["total_closed"] == 3


def test_by_series_empty_rows_returns_empty_list():
    assert ra.by_series([]) == []


# --- by_subcategory (2026-08-16, series -> subcategory -> category) -------

def test_by_subcategory_groups_correctly():
    tc.record_category("TICK-A", "Sports", subcategory="Baseball")
    tc.record_category("TICK-B", "Sports", subcategory="Football")
    rows = [_row(1000.0, ticker="TICK-A"), _row(1000.0, won=False, ticker="TICK-A"), _row(1000.0, ticker="TICK-B")]
    result = ra.by_subcategory(rows)
    by_sub = {r["subcategory"]: r for r in result}
    assert by_sub["Baseball"]["total_closed"] == 2
    assert by_sub["Football"]["total_closed"] == 1


def test_by_subcategory_excludes_tickers_with_no_recorded_subcategory():
    # Politics has a category but no subcategory - excluded, not fabricated.
    tc.record_category("TICK-A", "Politics")
    rows = [_row(1000.0, ticker="TICK-A")]
    assert ra.by_subcategory(rows) == []


def test_by_subcategory_sorted_by_count_descending():
    tc.record_category("TICK-A", "Sports", subcategory="Baseball")
    tc.record_category("TICK-B", "Sports", subcategory="Football")
    rows = [_row(1000.0, ticker="TICK-A"), _row(1000.0, ticker="TICK-B"),
            _row(1000.0, ticker="TICK-B"), _row(1000.0, ticker="TICK-B")]
    result = ra.by_subcategory(rows)
    assert result[0]["subcategory"] == "Football"
    assert result[0]["total_closed"] == 3


def test_by_subcategory_empty_rows_returns_empty_list():
    assert ra.by_subcategory([]) == []
