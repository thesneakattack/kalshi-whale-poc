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
    # 2026-08-16 category-routing fix reads state["event_titles"] - clear it
    # so a leftover "Sports" category from an earlier test file's shared
    # global state (e.g. test_trading_gate.py's own "EVT-A" fixtures)
    # can't silently change these tests' polling behavior.
    main.state["event_titles"].clear()
    fake = _FakeLiveDataClient()
    result = asyncio.run(main._fetch_event_live_data(fake, [_market()]))
    assert result == {"EVT-A": {
        "type": "score", "details": {"home": 1}, "is_historical": False,
        "default_range": "1h", "range_options": ["1h", "1d"],
    }}
    assert fake.calls == ["EVT-A"]


def test_fetch_event_live_data_reuses_a_recent_cached_value_without_polling():
    main.state["event_live_data_cache"].clear()
    # 2026-08-16 category-routing fix reads state["event_titles"] - clear it
    # so a leftover "Sports" category from an earlier test file's shared
    # global state (e.g. test_trading_gate.py's own "EVT-A" fixtures)
    # can't silently change these tests' polling behavior.
    main.state["event_titles"].clear()
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
    # 2026-08-16 category-routing fix reads state["event_titles"] - clear it
    # so a leftover "Sports" category from an earlier test file's shared
    # global state (e.g. test_trading_gate.py's own "EVT-A" fixtures)
    # can't silently change these tests' polling behavior.
    main.state["event_titles"].clear()
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
    # 2026-08-16 category-routing fix reads state["event_titles"] - clear it
    # so a leftover "Sports" category from an earlier test file's shared
    # global state (e.g. test_trading_gate.py's own "EVT-A" fixtures)
    # can't silently change these tests' polling behavior.
    main.state["event_titles"].clear()
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
    # 2026-08-16 category-routing fix reads state["event_titles"] - clear it
    # so a leftover "Sports" category from an earlier test file's shared
    # global state (e.g. test_trading_gate.py's own "EVT-A" fixtures)
    # can't silently change these tests' polling behavior.
    main.state["event_titles"].clear()
    fake = _FakeLiveDataClient(response={"live_data": {}})
    result = asyncio.run(main._fetch_event_live_data(fake, [_market()]))
    assert result == {}
    assert main.state["event_live_data_cache"]["EVT-A"]["data"] is None


def test_fetch_event_live_data_no_event_tickers_returns_empty_without_a_client_call():
    main.state["event_live_data_cache"].clear()
    # 2026-08-16 category-routing fix reads state["event_titles"] - clear it
    # so a leftover "Sports" category from an earlier test file's shared
    # global state (e.g. test_trading_gate.py's own "EVT-A" fixtures)
    # can't silently change these tests' polling behavior.
    main.state["event_titles"].clear()
    fake = _FakeLiveDataClient()
    result = asyncio.run(main._fetch_event_live_data(fake, [{"ticker": "NO-EVENT"}]))
    assert result == {}


# --- category routing (2026-08-16 API-doc audit finding B2) - this endpoint
# is documented/live-confirmed to serve crypto price charts/commodity
# timeseries/weather observations, and confirmed live to 404 100% of the
# time for real sports tickers - not a bug, structurally the wrong data
# source for sports (the real source is the milestone-keyed
# get_live_data(s) - see state["live_game_state"]). Calling it for a known-
# Sports event is pure confirmed waste, unlike an unknown-category event
# (see the "no client call at all" test below vs. the "still polls" one). --

def test_fetch_event_live_data_skips_a_known_sports_event_entirely():
    main.state["event_live_data_cache"].clear()
    main.state["event_titles"].clear()
    main.state["event_titles"]["EVT-A"] = {"category": "Sports"}
    fake = _FakeLiveDataClient()
    result = asyncio.run(main._fetch_event_live_data(fake, [_market()]))
    assert result == {}
    assert fake.calls == []  # never even attempted - confirmed-wasteful category


def test_fetch_event_live_data_still_polls_a_known_crypto_event():
    main.state["event_live_data_cache"].clear()
    main.state["event_titles"].clear()
    main.state["event_titles"]["EVT-A"] = {"category": "Crypto"}
    fake = _FakeLiveDataClient()
    result = asyncio.run(main._fetch_event_live_data(fake, [_market()]))
    assert fake.calls == ["EVT-A"]
    assert result["EVT-A"]["type"] == "score"


def test_fetch_event_live_data_still_polls_an_unknown_category_event():
    # A brand-new event whose category hasn't been learned yet (event_titles
    # not populated for it) must not be guess-excluded - only a CONFIRMED
    # Sports category skips the call, per this module's own "don't
    # fabricate" idiom.
    main.state["event_live_data_cache"].clear()
    main.state["event_titles"].clear()
    fake = _FakeLiveDataClient()
    result = asyncio.run(main._fetch_event_live_data(fake, [_market()]))
    assert fake.calls == ["EVT-A"]
    assert result["EVT-A"]["type"] == "score"


# --- _build_state_body()'s own event_live_data SEND throttle (Task 7 of
# docs/superpowers/plans/2026-09-03-tier1-backend-hygiene.md) - distinct
# from the repoll-cache tests above, which govern how often the *tick loop*
# fetches event live data. event_live_data is 87.3% of /api/state's payload
# (live-measured 2026-09-03) despite already being scoped to currently-
# relevant events (_scoped_event_live_data, 2026-08-21) - the underlying
# data itself only refreshes once every _EVENT_LIVE_DATA_REPOLL_SEC (60s),
# so resending it on every poll resends unchanged data most of the time.
# This throttles the HTTP send to match that existing 60s refresh ceiling. --

def test_event_live_data_is_throttled_to_its_own_repoll_cadence(monkeypatch):
    """First call after a bump includes event_live_data; a second call
    inside the 60s repoll window gets an empty dict (the frontend already
    Object.assign-merges rather than replaces, per polling-and-websocket.js,
    so this is a safe no-op, not a missing-data bug).

    Patches main._scoped_event_live_data directly (not main.state_view,
    which does not exist as an attribute of main - main.py imports the
    name directly via `from services.state_view import (_scoped_event_
    live_data, ...)`, confirmed by `grep -n state_view main.py` returning
    only the import statement itself; _build_state_body() calls the
    locally-bound name, so that is the only patch target that actually
    takes effect - same convention already used by this suite for
    monkeypatch.setattr(main, "bump_generation", ...) in
    tests/test_main_scheduler_loops.py).

    Mocks time.time() and advances it >1.0s between the two bump_generation()
    calls below - discovered necessary (not in the plan's literal test text)
    because bump_generation()'s own Task 7 coarsening (services/app_state.py)
    suppresses a second real-wall-clock call issued milliseconds after the
    first, which left state["generation"] unchanged and made
    _build_state_body()'s OWN memoization return the cached body1 for body2
    too - confirmed by running this test as originally written and observing
    that exact failure (`assert {'EVT-1': ...} == {}`) before adding the time
    mock. tests/conftest.py's _fresh_generation_bump_window fixture resets
    _last_bump_ts to 0.0 at test start (needed for cross-test isolation) but
    does not help WITHIN this test, since both calls here happen well inside
    one real second of each other regardless.
    """
    monkeypatch.setattr(main, "_scoped_event_live_data", lambda titles: {"EVT-1": {"score": 1}})
    monkeypatch.setattr(main, "_last_event_live_data_sent_at", 0.0)
    fake_now = [1_000_000.0]
    monkeypatch.setattr(time, "time", lambda: fake_now[0])
    main.bump_generation()

    body1 = main._build_state_body()
    assert body1["event_live_data"] == {"EVT-1": {"score": 1}}

    fake_now[0] += 2.0  # past bump_generation's 1.0s coarsening window, well
    # inside event_live_data's 60s repoll window - a genuine second real change.
    main.bump_generation()
    body2 = main._build_state_body()
    assert body2["event_live_data"] == {}, "should not re-send within the 60s repoll window"
