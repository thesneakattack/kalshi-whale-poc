"""
services/market_events/event_schedule.py - the real event-schedule resolver
(4-source waterfall: strike_date -> milestone -> text -> web search) plus
the 2026-08-24 background-resolution wiring (second sub-unit of the
close-time fix, see tests/test_market_lookup.py for the first) that finally
calls it from main.py's trading loop. Zero test coverage existed for this
whole module before this file - a genuine pre-existing gap, not just new-
code coverage for what this session adds.

DB_PATH is redirected to a fresh tmp file per test (on top of conftest.py's
own module-scope redirect, which only protects the very first import in the
process) - same isolation convention as every other *.db-backed module in
this suite.
"""
import asyncio
import time

import pytest

from services.market_events import event_schedule


class FakeClient:
    def __init__(self, milestones_map=None):
        self._milestones = milestones_map or {}

    async def get_milestones_for_event(self, event_ticker):
        return self._milestones.get(event_ticker, [])


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch, tmp_path):
    monkeypatch.setattr(event_schedule, "DB_PATH", tmp_path / "event_schedule.db")


# ---- resolve_one's 4-source waterfall ------------------------------------

def test_resolve_one_strike_date_wins_first_and_costs_nothing_else(monkeypatch):
    async def _boom(*a, **kw):
        raise AssertionError("should not be reached - strike_date already answered it")
    monkeypatch.setattr(event_schedule, "_milestone_schedule", _boom)
    start_ts, end_ts, source = asyncio.run(event_schedule.resolve_one(
        FakeClient(), "EVT", event_strike_date="2026-09-16T18:00:00Z",
    ))
    assert source == event_schedule.SOURCE_STRIKE_DATE
    assert start_ts == end_ts  # point-in-time: both bounds are the same moment


def test_resolve_one_milestone_wins_over_text_and_search():
    client = FakeClient({"EVT": [{"start_date": "2026-08-13T11:00:00Z", "end_date": None}]})
    _, _, source = asyncio.run(event_schedule.resolve_one(
        client, "EVT", texts=["scheduled for Aug 20, 2026"], search_query="whatever",
    ))
    assert source == event_schedule.SOURCE_MILESTONE


def test_resolve_one_text_wins_over_search(monkeypatch):
    async def _fail_search(query):
        raise AssertionError("should not reach web search - text already answered it")
    monkeypatch.setattr(event_schedule, "_web_search_schedule", _fail_search)
    _, _, source = asyncio.run(event_schedule.resolve_one(
        FakeClient(), "EVT", texts=["the game originally scheduled for Aug 15, 2026"],
        search_query="whatever",
    ))
    assert source == event_schedule.SOURCE_TEXT


def test_resolve_one_falls_back_to_web_search_as_last_resort(monkeypatch):
    async def _fake_search(query):
        assert query == "my query"
        return (1000.0, 2000.0)
    monkeypatch.setattr(event_schedule, "_web_search_schedule", _fake_search)
    result = asyncio.run(event_schedule.resolve_one(
        FakeClient(), "EVT", search_query="my query",
    ))
    assert result == (1000.0, 2000.0, event_schedule.SOURCE_SEARCH)


def test_resolve_one_web_search_skipped_when_disabled(monkeypatch):
    async def _fail_search(query):
        raise AssertionError("web search disabled - should not be called")
    monkeypatch.setattr(event_schedule, "_web_search_schedule", _fail_search)
    result = asyncio.run(event_schedule.resolve_one(
        FakeClient(), "EVT", search_query="my query", web_search_enabled=False,
    ))
    assert result == (None, None, event_schedule.SOURCE_NONE)


def test_resolve_one_returns_none_source_when_nothing_found_anywhere():
    result = asyncio.run(event_schedule.resolve_one(FakeClient(), "EVT"))
    assert result == (None, None, event_schedule.SOURCE_NONE)


def test_resolve_one_a_milestone_lookup_failure_is_treated_as_not_found_not_fatal():
    class BoomingClient:
        async def get_milestones_for_event(self, event_ticker):
            raise RuntimeError("network blip")
    result = asyncio.run(event_schedule.resolve_one(BoomingClient(), "EVT"))
    assert result == (None, None, event_schedule.SOURCE_NONE)


# ---- needs_resolution TTL math --------------------------------------------

def test_needs_resolution_true_when_no_cached_entry():
    assert event_schedule.needs_resolution(None, now=1000.0) is True


def test_needs_resolution_false_for_a_fresh_found_entry():
    cached = {"source": event_schedule.SOURCE_MILESTONE, "resolved_at": 1000.0}
    assert event_schedule.needs_resolution(cached, now=1000.0 + 3600) is False


def test_needs_resolution_true_once_a_found_entry_exceeds_the_found_retry_ttl():
    cached = {"source": event_schedule.SOURCE_MILESTONE, "resolved_at": 1000.0}
    now = 1000.0 + event_schedule._FOUND_RETRY_SEC + 1
    assert event_schedule.needs_resolution(cached, now) is True


def test_needs_resolution_a_none_tombstone_uses_the_shorter_ttl():
    cached = {"source": event_schedule.SOURCE_NONE, "resolved_at": 1000.0}
    just_past_none_ttl = 1000.0 + event_schedule._NONE_RETRY_SEC + 1
    assert just_past_none_ttl < 1000.0 + event_schedule._FOUND_RETRY_SEC  # sanity: the two TTLs really differ
    assert event_schedule.needs_resolution(cached, just_past_none_ttl) is True


# ---- save/load_all persistence round trip ---------------------------------

def test_save_and_load_all_round_trip():
    event_schedule.save("EVT-A", 1000.0, 2000.0, event_schedule.SOURCE_MILESTONE, resolved_at=500.0)
    event_schedule.save("EVT-B", None, None, event_schedule.SOURCE_NONE, resolved_at=600.0)
    loaded = event_schedule.load_all()
    assert loaded["EVT-A"] == {
        "start_ts": 1000.0, "end_ts": 2000.0, "source": event_schedule.SOURCE_MILESTONE, "resolved_at": 500.0,
    }
    assert loaded["EVT-B"]["source"] == event_schedule.SOURCE_NONE


def test_save_upserts_on_conflict_instead_of_duplicating():
    event_schedule.save("EVT-A", 1.0, 2.0, event_schedule.SOURCE_TEXT, resolved_at=1.0)
    event_schedule.save("EVT-A", 3.0, 4.0, event_schedule.SOURCE_MILESTONE, resolved_at=2.0)
    loaded = event_schedule.load_all()
    assert loaded == {
        "EVT-A": {"start_ts": 3.0, "end_ts": 4.0, "source": event_schedule.SOURCE_MILESTONE, "resolved_at": 2.0},
    }


# ---- parse_date_range_from_text patterns -----------------------------------

def test_parse_date_range_from_text_a_range():
    start_ts, end_ts = event_schedule.parse_date_range_from_text("August 13-16, 2026")
    assert end_ts > start_ts


def test_parse_date_range_from_text_a_single_date_has_no_end():
    result = event_schedule.parse_date_range_from_text("originally scheduled for Aug 15, 2026")
    assert result[1] is None


def test_parse_date_range_from_text_no_year_uses_ref_year():
    assert event_schedule.parse_date_range_from_text("(Aug 15)", ref_year=2026) is not None


def test_parse_date_range_from_text_no_year_and_no_ref_year_finds_nothing():
    assert event_schedule.parse_date_range_from_text("(Aug 15)") is None


def test_parse_date_range_from_text_no_match_returns_none():
    assert event_schedule.parse_date_range_from_text("no dates here at all") is None


def test_parse_date_range_from_text_empty_input_returns_none():
    assert event_schedule.parse_date_range_from_text(None) is None
    assert event_schedule.parse_date_range_from_text("") is None


# ---- trade_window_is_open --------------------------------------------------

def test_trade_window_is_open_none_when_start_missing():
    assert event_schedule.trade_window_is_open(1000.0, None, 2000.0, pre_event_hours=1.0) is None


def test_trade_window_is_open_none_when_end_missing():
    assert event_schedule.trade_window_is_open(1000.0, 500.0, None, pre_event_hours=1.0) is None


def test_trade_window_is_open_true_within_the_pre_event_window():
    start_ts = 10000.0
    now = start_ts - 1800  # 30 min before start, inside a 1h pre-event window
    assert event_schedule.trade_window_is_open(now, start_ts, start_ts + 3600, pre_event_hours=1.0) is True


def test_trade_window_is_open_false_before_the_pre_event_window():
    start_ts = 10000.0
    now = start_ts - 7200  # 2h before start, outside a 1h pre-event window
    assert event_schedule.trade_window_is_open(now, start_ts, start_ts + 3600, pre_event_hours=1.0) is False


def test_trade_window_is_open_false_after_the_end():
    start_ts, end_ts = 10000.0, 13600.0
    assert event_schedule.trade_window_is_open(end_ts + 1, start_ts, end_ts, pre_event_hours=1.0) is False


# ---- _events_needing_resolution (2026-08-24) -------------------------------

def _far_close(now):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 30 * 86400))


def _near_close(now):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 3600))


def test_events_needing_resolution_skips_short_window_markets():
    now = time.time()
    markets = [{"ticker": "T1", "event_ticker": "EVT", "close_time": _near_close(now)}]
    assert event_schedule._events_needing_resolution(markets, {}, now) == []


def test_events_needing_resolution_includes_long_window_markets_due_for_resolution():
    now = time.time()
    markets = [{"ticker": "T1", "event_ticker": "EVT", "close_time": _far_close(now)}]
    assert event_schedule._events_needing_resolution(markets, {}, now) == ["EVT"]


def test_events_needing_resolution_skips_events_with_a_fresh_cached_schedule():
    now = time.time()
    markets = [{"ticker": "T1", "event_ticker": "EVT", "close_time": _far_close(now)}]
    event_schedules = {"EVT": {"source": event_schedule.SOURCE_MILESTONE, "resolved_at": now}}
    assert event_schedule._events_needing_resolution(markets, event_schedules, now) == []


def test_events_needing_resolution_qualifies_via_any_sibling_market_not_just_the_first():
    now = time.time()
    markets = [
        {"ticker": "T1", "event_ticker": "EVT", "close_time": _near_close(now)},
        {"ticker": "T2", "event_ticker": "EVT", "close_time": _far_close(now)},
    ]
    assert event_schedule._events_needing_resolution(markets, {}, now) == ["EVT"]


def test_events_needing_resolution_dedups_and_preserves_first_seen_order():
    now = time.time()
    far = _far_close(now)
    markets = [
        {"ticker": "T1", "event_ticker": "EVT-B", "close_time": far},
        {"ticker": "T2", "event_ticker": "EVT-A", "close_time": far},
        {"ticker": "T3", "event_ticker": "EVT-B", "close_time": far},
    ]
    assert event_schedule._events_needing_resolution(markets, {}, now) == ["EVT-B", "EVT-A"]


def test_events_needing_resolution_skips_markets_with_no_event_ticker():
    now = time.time()
    markets = [{"ticker": "T1", "close_time": _far_close(now)}]
    assert event_schedule._events_needing_resolution(markets, {}, now) == []


# ---- _resolve_event_schedules -----------------------------------------------

def test_resolve_event_schedules_mutates_in_place_and_persists():
    now = time.time()
    markets = [{"ticker": "T1", "event_ticker": "EVT", "close_time": _far_close(now)}]
    event_titles = {"EVT": {"title": "Some Event", "strike_date": None}}
    event_schedules = {}
    client = FakeClient({"EVT": [{"start_date": "2026-09-01T00:00:00Z", "end_date": None}]})
    cfg = {"event_schedule": {"max_resolutions_per_tick": 5, "web_search_enabled": True}}

    asyncio.run(event_schedule._resolve_event_schedules(
        client, cfg, markets, event_titles, event_schedules, {},
    ))

    assert event_schedules["EVT"]["source"] == event_schedule.SOURCE_MILESTONE
    assert event_schedule.load_all()["EVT"]["source"] == event_schedule.SOURCE_MILESTONE


def test_resolve_event_schedules_respects_the_per_tick_cap(monkeypatch):
    now = time.time()
    far = _far_close(now)
    markets = [{"ticker": f"T{i}", "event_ticker": f"EVT-{i}", "close_time": far} for i in range(3)]
    resolved_calls = []

    async def _fake_resolve_one(client, event_ticker, **kwargs):
        resolved_calls.append(event_ticker)
        return None, None, event_schedule.SOURCE_NONE

    monkeypatch.setattr(event_schedule, "resolve_one", _fake_resolve_one)
    cfg = {"event_schedule": {"max_resolutions_per_tick": 2, "web_search_enabled": True}}

    asyncio.run(event_schedule._resolve_event_schedules(FakeClient(), cfg, markets, {}, {}, {}))

    assert len(resolved_calls) == 2


def test_resolve_event_schedules_sources_texts_from_market_object_cache(monkeypatch):
    now = time.time()
    markets = [{"ticker": "T1", "event_ticker": "EVT", "close_time": _far_close(now)}]
    market_object_cache = {"T1": {"rules_primary": "the game originally scheduled for Aug 15, 2026"}}
    captured = {}

    async def _fake_resolve_one(client, event_ticker, *, event_strike_date=None, texts=None,
                                 search_query=None, ref_year=None, web_search_enabled=True):
        captured["texts"] = texts
        return None, None, event_schedule.SOURCE_NONE

    monkeypatch.setattr(event_schedule, "resolve_one", _fake_resolve_one)
    cfg = {"event_schedule": {"max_resolutions_per_tick": 5, "web_search_enabled": True}}

    asyncio.run(event_schedule._resolve_event_schedules(
        FakeClient(), cfg, markets, {}, {}, market_object_cache,
    ))

    assert "the game originally scheduled for Aug 15, 2026" in captured["texts"]


def test_resolve_event_schedules_is_a_noop_when_nothing_is_due():
    result = asyncio.run(event_schedule._resolve_event_schedules(
        FakeClient(), {"event_schedule": {}}, [], {}, {}, {},
    ))
    assert result is None
    assert event_schedule.load_all() == {}


# ---- _maybe_resolve_event_schedules gating ----------------------------------

async def _call_maybe_resolve(cfg, state):
    import services.app_state as app_state_module
    original = app_state_module.state
    app_state_module.state = state
    try:
        event_schedule._maybe_resolve_event_schedules(cfg)
        await asyncio.sleep(0.05)  # let any scheduled background task actually run
    finally:
        app_state_module.state = original


def _fresh_state(**overrides):
    base = {
        "event_schedule_scan": {"running": False, "last_started_at": 0.0, "task": None},
        "markets": [], "event_titles": {}, "event_schedules": {}, "market_object_cache": {},
    }
    base.update(overrides)
    return base


def test_maybe_resolve_disabled_never_fires():
    state = _fresh_state()
    cfg = {"event_schedule": {"enabled": False, "max_resolutions_per_tick": 5}}
    asyncio.run(_call_maybe_resolve(cfg, state))
    assert state["event_schedule_scan"]["running"] is False
    assert state["event_schedule_scan"]["last_started_at"] == 0.0


def test_maybe_resolve_fires_when_due_and_updates_tracker(monkeypatch):
    # Fakes both KalshiPublicGateway construction and resolve_one so this exercises
    # only the gating/tracker/task-lifecycle wiring, not real network I/O
    # (which resolve_one's own tests above already cover in isolation).
    class FakeKalshiClient:
        def __init__(self, base_url, timeout):
            pass

        async def close(self):
            pass

    async def _fake_resolve_one(client, event_ticker, **kwargs):
        return None, None, event_schedule.SOURCE_NONE

    monkeypatch.setattr(event_schedule, "KalshiPublicGateway", FakeKalshiClient)
    monkeypatch.setattr(event_schedule, "resolve_one", _fake_resolve_one)

    now = time.time()
    state = _fresh_state(markets=[{"ticker": "T1", "event_ticker": "EVT", "close_time": _far_close(now)}])
    cfg = {
        "kalshi": {"base_url": "https://example.invalid", "request_timeout_sec": 5},
        "event_schedule": {"enabled": True, "max_resolutions_per_tick": 5, "web_search_enabled": False},
    }
    asyncio.run(_call_maybe_resolve(cfg, state))
    assert state["event_schedule_scan"]["last_started_at"] > 0.0
    assert state["event_schedule_scan"]["running"] is False  # released by the background wrapper's finally


def test_maybe_resolve_does_not_launch_a_second_batch_while_one_is_running():
    state = _fresh_state()
    state["event_schedule_scan"]["running"] = True
    cfg = {"event_schedule": {"enabled": True}}
    started_at_before = state["event_schedule_scan"]["last_started_at"]
    asyncio.run(_call_maybe_resolve(cfg, state))
    assert state["event_schedule_scan"]["last_started_at"] == started_at_before  # never kicked off


def test_maybe_resolve_respects_the_min_interval_between_kickoffs():
    state = _fresh_state()
    state["event_schedule_scan"]["last_started_at"] = time.time()  # just started
    cfg = {"event_schedule": {"enabled": True}}
    asyncio.run(_call_maybe_resolve(cfg, state))
    assert state["event_schedule_scan"]["running"] is False  # not due yet, no new task
