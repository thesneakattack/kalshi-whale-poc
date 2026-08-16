import pytest

from services import trade_category as tc


@pytest.fixture(autouse=True)
def _redirect_db(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "DB_PATH", tmp_path / "trade_category.db")


def test_record_and_lookup_a_category():
    tc.record_category("TICK-A", "Sports", now=1000.0)
    assert tc.categories_for_tickers(["TICK-A"]) == {"TICK-A": "Sports"}


def test_record_category_noop_when_category_is_none():
    tc.record_category("TICK-A", None, now=1000.0)
    assert tc.categories_for_tickers(["TICK-A"]) == {}


def test_record_category_noop_when_category_is_empty_string():
    tc.record_category("TICK-A", "", now=1000.0)
    assert tc.categories_for_tickers(["TICK-A"]) == {}


def test_record_category_noop_when_ticker_is_empty():
    tc.record_category("", "Sports", now=1000.0)
    assert tc.categories_for_tickers([""]) == {}


def test_re_recording_updates_the_category():
    tc.record_category("TICK-A", "Sports", now=1000.0)
    tc.record_category("TICK-A", "Politics", now=2000.0)
    assert tc.categories_for_tickers(["TICK-A"]) == {"TICK-A": "Politics"}


def test_categories_for_tickers_bulk_lookup():
    tc.record_category("TICK-A", "Sports", now=1000.0)
    tc.record_category("TICK-B", "Politics", now=1000.0)
    result = tc.categories_for_tickers(["TICK-A", "TICK-B", "TICK-C"])
    assert result == {"TICK-A": "Sports", "TICK-B": "Politics"}  # TICK-C never recorded, absent not None


def test_categories_for_tickers_empty_list_returns_empty_dict():
    assert tc.categories_for_tickers([]) == {}


def test_clear_all_wipes_every_category():
    tc.record_category("TICK-A", "Sports", now=1000.0)
    tc.clear_all()
    assert tc.categories_for_tickers(["TICK-A"]) == {}


# --- subcategory (2026-08-16, series -> subcategory -> category fallback) --

def test_record_and_lookup_a_subcategory():
    tc.record_category("TICK-A", "Sports", now=1000.0, subcategory="Baseball")
    assert tc.subcategories_for_tickers(["TICK-A"]) == {"TICK-A": "Baseball"}


def test_subcategory_defaults_to_none_when_not_given():
    tc.record_category("TICK-A", "Sports", now=1000.0)
    assert tc.subcategories_for_tickers(["TICK-A"]) == {}


def test_subcategory_excluded_from_bulk_lookup_when_null():
    tc.record_category("TICK-A", "Sports", now=1000.0, subcategory="Baseball")
    tc.record_category("TICK-B", "Politics", now=1000.0)  # no subcategory - Politics doesn't have one
    result = tc.subcategories_for_tickers(["TICK-A", "TICK-B"])
    assert result == {"TICK-A": "Baseball"}


def test_re_recording_category_without_subcategory_does_not_clear_a_known_one():
    # A later call that only knows the category (e.g. the market-native
    # strategy's own record_category call site, which has no subcategory
    # concept) shouldn't blow away a subcategory a whale-follow entry
    # already recorded for the same ticker.
    tc.record_category("TICK-A", "Sports", now=1000.0, subcategory="Baseball")
    tc.record_category("TICK-A", "Sports", now=2000.0)
    assert tc.subcategories_for_tickers(["TICK-A"]) == {"TICK-A": "Baseball"}


def test_re_recording_with_a_new_subcategory_updates_it():
    tc.record_category("TICK-A", "Sports", now=1000.0, subcategory="Baseball")
    tc.record_category("TICK-A", "Sports", now=2000.0, subcategory="Football")
    assert tc.subcategories_for_tickers(["TICK-A"]) == {"TICK-A": "Football"}


def test_subcategories_for_tickers_empty_list_returns_empty_dict():
    assert tc.subcategories_for_tickers([]) == {}


def test_clear_all_wipes_subcategory_too():
    tc.record_category("TICK-A", "Sports", now=1000.0, subcategory="Baseball")
    tc.clear_all()
    assert tc.subcategories_for_tickers(["TICK-A"]) == {}
