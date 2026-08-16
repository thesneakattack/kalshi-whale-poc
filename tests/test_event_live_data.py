import asyncio
import time

import main


class _FakeLiveDataClient:
    def __init__(self, response=None, raises=False):
        self.calls = []
        self._response = response if response is not None else {
            "live_data": {"type": "score", "details": {"home": 1}, "is_historical": False,
                           "default_range": "1h", "range_options": ["1h", "1d"]},
        }
        self._raises = raises

    async def get_event_live_data(self, event_ticker):
        self.calls.append(event_ticker)
        if self._raises:
            raise RuntimeError("404 Not Found")
        return self._response


def _market(ticker="TICK-A", event_ticker="EVT-A"):
    return {"ticker": ticker, "event_ticker": event_ticker}


# --- repoll-cache behavior (2026-08-15 tick_duration fix) - this used to
# call get_event_live_data() for every unique event on the watchlist, every
# tick, forever, unconditionally, with zero caching - confirmed live: ALL
# events on the current real watchlist 404 from this endpoint every single
# time (Kalshi's live_data feed doesn't cover crypto/politics/single-market
# events, and evidently not this preseason sports window either), so this
# was pure waste on every tick, not caution. See main._EVENT_LIVE_DATA_REPOLL_SEC. --

def test_fetch_event_live_data_polls_a_new_event_with_no_cache():
    main.state["event_live_data_cache"].clear()
    fake = _FakeLiveDataClient()
    result = asyncio.run(main._fetch_event_live_data(fake, [_market()]))
    assert result == {"EVT-A": {
        "type": "score", "details": {"home": 1}, "is_historical": False,
        "default_range": "1h", "range_options": ["1h", "1d"],
    }}
    assert fake.calls == ["EVT-A"]


def test_fetch_event_live_data_reuses_a_recent_cached_value_without_polling():
    main.state["event_live_data_cache"].clear()
    main.state["event_live_data_cache"]["EVT-A"] = {
        "data": {"type": "score", "details": {}, "is_historical": None, "default_range": None, "range_options": []},
        "checked_at": time.time(),
    }
    fake = _FakeLiveDataClient()
    result = asyncio.run(main._fetch_event_live_data(fake, [_market()]))
    assert result["EVT-A"]["type"] == "score"
    assert fake.calls == []  # trusted the cache, never polled


def test_fetch_event_live_data_repolls_once_the_cache_entry_is_stale():
    main.state["event_live_data_cache"].clear()
    stale_check = time.time() - main._EVENT_LIVE_DATA_REPOLL_SEC - 1
    main.state["event_live_data_cache"]["EVT-A"] = {
        "data": {"type": "score", "details": {}, "is_historical": None, "default_range": None, "range_options": []},
        "checked_at": stale_check,
    }
    fake = _FakeLiveDataClient()
    result = asyncio.run(main._fetch_event_live_data(fake, [_market()]))
    assert result["EVT-A"]["type"] == "score"
    assert fake.calls == ["EVT-A"]  # due for a light re-poll


def test_fetch_event_live_data_caches_a_404_and_does_not_repoll_immediately():
    # The real, confirmed-live case: every event on the watchlist 404s from
    # this endpoint right now. A failure must still stamp checked_at so the
    # very next tick doesn't immediately retry - only the repoll cadence
    # should govern retries, same as a successful-but-empty response.
    main.state["event_live_data_cache"].clear()
    fake = _FakeLiveDataClient(raises=True)
    result = asyncio.run(main._fetch_event_live_data(fake, [_market()]))
    assert result == {}
    assert fake.calls == ["EVT-A"]
    assert main.state["event_live_data_cache"]["EVT-A"]["data"] is None

    result2 = asyncio.run(main._fetch_event_live_data(fake, [_market()]))
    assert result2 == {}
    assert fake.calls == ["EVT-A"]  # unchanged - no second call


def test_fetch_event_live_data_empty_live_data_is_treated_like_no_data():
    main.state["event_live_data_cache"].clear()
    fake = _FakeLiveDataClient(response={"live_data": {}})
    result = asyncio.run(main._fetch_event_live_data(fake, [_market()]))
    assert result == {}
    assert main.state["event_live_data_cache"]["EVT-A"]["data"] is None


def test_fetch_event_live_data_no_event_tickers_returns_empty_without_a_client_call():
    main.state["event_live_data_cache"].clear()
    fake = _FakeLiveDataClient()
    result = asyncio.run(main._fetch_event_live_data(fake, [{"ticker": "NO-EVENT"}]))
    assert result == {}
    assert fake.calls == []
