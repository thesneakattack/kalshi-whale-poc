import calendar
import time

from services import regime_analytics as ra


def _row(entry_timestamp, won=True, realized_pnl=10.0):
    return {
        "entry_timestamp": entry_timestamp, "won": won, "realized_pnl": realized_pnl,
        "hold_sec": 100.0, "left_on_table": None, "cost_basis": 50.0,
        "close_type": "settled_win" if won else "settled_loss",
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
