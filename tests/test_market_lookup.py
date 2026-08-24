"""services/market_lookup.py's effective_close_time/_close_time_by_ticker -
the 2026-08-24 fix for a direct bug report ("trading windows... will close
at the conclusion of that event vs. the scheduled market close time"). See
effective_close_time's own docstring for the precedence order and the live
incident this closes.
"""
from services import market_lookup
from services.app_state import state


def _isolated(monkeypatch):
    monkeypatch.setitem(state, "markets", [])
    monkeypatch.setitem(state, "event_schedules", {})


# ---- effective_close_time precedence ----------------------------------

def test_none_market_returns_none(monkeypatch):
    _isolated(monkeypatch)
    assert market_lookup.effective_close_time(None) is None


def test_empty_market_returns_none(monkeypatch):
    _isolated(monkeypatch)
    assert market_lookup.effective_close_time({}) is None


def test_expected_expiration_time_wins_over_everything(monkeypatch):
    _isolated(monkeypatch)
    monkeypatch.setitem(state, "event_schedules", {"EVT": {"end_ts": 111.0}})
    m = {
        "expected_expiration_time": "2026-09-01T00:00:00Z",
        "event_ticker": "EVT",
        "occurrence_datetime": "2026-08-30T00:00:00Z",
        "close_time": "2027-01-01T00:00:00Z",
    }
    assert market_lookup.effective_close_time(m) == "2026-09-01T00:00:00Z"


def test_event_schedule_end_ts_wins_when_no_expected_expiration(monkeypatch):
    _isolated(monkeypatch)
    monkeypatch.setitem(state, "event_schedules", {"EVT": {"end_ts": 222.0}})
    m = {
        "event_ticker": "EVT",
        "occurrence_datetime": "2026-08-30T00:00:00Z",
        "close_time": "2027-01-01T00:00:00Z",
    }
    assert market_lookup.effective_close_time(m) == 222.0


def test_event_schedule_tombstone_with_no_end_ts_falls_through(monkeypatch):
    # source="none" (nothing resolved) or a milestone-only resolve with no
    # end_date - both leave end_ts falsy, must fall through to the next
    # tier rather than being treated as "resolved but zero."
    _isolated(monkeypatch)
    monkeypatch.setitem(state, "event_schedules", {"EVT": {"end_ts": None, "source": "none"}})
    m = {
        "event_ticker": "EVT",
        "occurrence_datetime": "2026-08-30T00:00:00Z",
        "close_time": "2027-01-01T00:00:00Z",
    }
    assert market_lookup.effective_close_time(m) == "2026-08-30T00:00:00Z"


def test_occurrence_datetime_wins_over_close_time(monkeypatch):
    _isolated(monkeypatch)
    m = {"occurrence_datetime": "2026-08-30T00:00:00Z", "close_time": "2027-01-01T00:00:00Z"}
    assert market_lookup.effective_close_time(m) == "2026-08-30T00:00:00Z"


def test_close_time_is_the_final_fallback(monkeypatch):
    _isolated(monkeypatch)
    m = {"close_time": "2027-01-01T00:00:00Z"}
    assert market_lookup.effective_close_time(m) == "2027-01-01T00:00:00Z"


def test_no_event_ticker_skips_the_schedule_tier_safely(monkeypatch):
    _isolated(monkeypatch)
    monkeypatch.setitem(state, "event_schedules", {"EVT": {"end_ts": 333.0}})
    m = {"occurrence_datetime": "2026-08-30T00:00:00Z", "close_time": "2027-01-01T00:00:00Z"}
    assert market_lookup.effective_close_time(m) == "2026-08-30T00:00:00Z"


# ---- _close_time_by_ticker ----------------------------------------------

def test_close_time_by_ticker_mixed_batch(monkeypatch):
    _isolated(monkeypatch)
    monkeypatch.setitem(state, "event_schedules", {"EVT-A": {"end_ts": 444.0}})
    monkeypatch.setitem(state, "markets", [
        {"ticker": "TICK-A", "event_ticker": "EVT-A", "close_time": "2027-01-01T00:00:00Z"},
        {"ticker": "TICK-B", "close_time": "2026-09-01T00:00:00Z"},
        {"ticker": "TICK-C"},  # no close_time at all - must not appear in the result
    ])
    result = market_lookup._close_time_by_ticker()
    assert result == {"TICK-A": 444.0, "TICK-B": "2026-09-01T00:00:00Z"}


def test_close_time_by_ticker_skips_entries_with_no_ticker(monkeypatch):
    _isolated(monkeypatch)
    monkeypatch.setitem(state, "markets", [{"close_time": "2027-01-01T00:00:00Z"}])
    assert market_lookup._close_time_by_ticker() == {}
