import asyncio
import time

from services import market_history
import main


class FakeClient:
    def __init__(self, milestones_map, live_map, market_map):
        self._milestones = milestones_map
        self._live = live_map  # milestone_id -> {"details": {...}} (flat, matching get_live_datas' real shape)
        self._markets = market_map
        self.milestone_calls = []
        self.live_data_calls = []
        self.market_calls = []
        self.market_call_batches = []  # one entry per get_markets_by_tickers call

    async def get_milestones_for_event(self, event_ticker):
        self.milestone_calls.append(event_ticker)
        return self._milestones.get(event_ticker, [])

    async def get_live_datas(self, milestone_ids):
        # Batched (2026-08-16) - replaces the old per-milestone get_live_data.
        self.live_data_calls.extend(milestone_ids)
        return {mid: self._live[mid] for mid in milestone_ids if mid in self._live}

    async def get_markets_by_tickers(self, tickers):
        # Batched (2026-08-16) - replaces the old per-event gather of
        # individual get_market() calls.
        self.market_calls.extend(tickers)
        self.market_call_batches.append(list(tickers))
        return {t: self._markets[t] for t in tickers if t in self._markets}


def test_propagate_milestone_winners_only_includes_a_markets_own_result_once_finalized(monkeypatch):
    # 2026-08-23 fix (services/exits/README.md's audit finding,
    # docs/kalshi/market_lifecycle.md): Kalshi sets `result` the instant a
    # market is "determined", well before "finalized" - the result "may be
    # disputed" during the settlement-timer window in between. A market
    # still at "determined" (or any non-finalized status) must not appear in
    # market_results at all, even though its `result` field is already set.
    markets = [
        {"ticker": "DETERMINED-ONLY", "event_ticker": "EVT9", "result": "yes", "status": "determined"},
        {"ticker": "ALREADY-FINAL", "event_ticker": "EVT9", "result": "no", "status": "finalized"},
        {"ticker": "STILL-OPEN", "event_ticker": "EVT9", "result": "", "status": "active"},
    ]
    fake = FakeClient({}, {}, {})

    main.state["milestone_cache"].clear()
    market_results = asyncio.run(main.propagate_milestone_winners(fake, markets))

    assert "DETERMINED-ONLY" not in market_results
    assert market_results.get("ALREADY-FINAL") == "no"
    assert "STILL-OPEN" not in market_results


def test_propagate_milestone_winner(monkeypatch):
    # Setup markets: two related event tickers A and B mapping to two markets
    markets = [
        {"ticker": "EVT1-OUTCOME1", "event_ticker": "EVT1", "result": ""},
        {"ticker": "EVT1-OUTCOME2", "event_ticker": "EVT1", "result": ""},
    ]

    # Milestone for EVT1 returns an id 'ms1' and type 'winner_decl'
    milestones_map = {
        "EVT1": [{"id": "ms1", "type": "winner_decl", "related_event_tickers": ["EVT1-OUTCOME1", "EVT1-OUTCOME2"]}]
    }
    # live_data for ms1 contains details.winner matching OUTCOME2's yes_sub_title
    live_map = {
        "ms1": {"details": {"winner": "Outcome Two", "related_event_tickers": ["EVT1-OUTCOME1", "EVT1-OUTCOME2"]}}
    }
    # Market objects where OUTCOME2's yes_sub_title contains 'Outcome Two'
    market_map = {
        "EVT1-OUTCOME1": {"ticker": "EVT1-OUTCOME1", "yes_sub_title": "Outcome One", "title": "Event 1 - Outcome One"},
        "EVT1-OUTCOME2": {"ticker": "EVT1-OUTCOME2", "yes_sub_title": "Outcome Two", "title": "Event 1 - Outcome Two"},
    }

    fake = FakeClient(milestones_map, live_map, market_map)

    recorded = []

    def fake_record_outcome(ticker, result, resolved_at=None):
        recorded.append((ticker, result))

    monkeypatch.setattr(market_history, "record_outcome", fake_record_outcome)

    main.state["milestone_cache"].clear()
    market_results = asyncio.run(main.propagate_milestone_winners(fake, markets))

    # Expect EVT1-OUTCOME2 mapped to yes, EVT1-OUTCOME1 to no
    assert market_results.get("EVT1-OUTCOME2") == "yes"
    assert market_results.get("EVT1-OUTCOME1") == "no"
    # And outcomes recorded for both
    assert ("EVT1-OUTCOME2", "yes") in recorded
    assert ("EVT1-OUTCOME1", "no") in recorded


def test_propagate_milestone_winners_batches_related_market_lookups_across_events(monkeypatch):
    # Direct efficiency note (2026-08-16): "a lot of efficiency could be
    # gained by using batch calls to the API vs individual calls for
    # specific markets" - two independent events, each with their own
    # winner + related tickers, must resolve via ONE get_markets_by_tickers
    # call covering both events' tickers, not one gather per event.
    markets = [
        {"ticker": "EVT1-OUTCOME1", "event_ticker": "EVT1", "result": ""},
        {"ticker": "EVT1-OUTCOME2", "event_ticker": "EVT1", "result": ""},
        {"ticker": "EVT2-OUTCOME1", "event_ticker": "EVT2", "result": ""},
        {"ticker": "EVT2-OUTCOME2", "event_ticker": "EVT2", "result": ""},
    ]
    milestones_map = {
        "EVT1": [{"id": "ms1", "type": "winner_decl", "related_event_tickers": ["EVT1-OUTCOME1", "EVT1-OUTCOME2"]}],
        "EVT2": [{"id": "ms2", "type": "winner_decl", "related_event_tickers": ["EVT2-OUTCOME1", "EVT2-OUTCOME2"]}],
    }
    live_map = {
        "ms1": {"details": {"winner": "Outcome Two", "related_event_tickers": ["EVT1-OUTCOME1", "EVT1-OUTCOME2"]}},
        "ms2": {"details": {"winner": "Second One", "related_event_tickers": ["EVT2-OUTCOME1", "EVT2-OUTCOME2"]}},
    }
    market_map = {
        "EVT1-OUTCOME1": {"ticker": "EVT1-OUTCOME1", "yes_sub_title": "Outcome One"},
        "EVT1-OUTCOME2": {"ticker": "EVT1-OUTCOME2", "yes_sub_title": "Outcome Two"},
        "EVT2-OUTCOME1": {"ticker": "EVT2-OUTCOME1", "yes_sub_title": "First One"},
        "EVT2-OUTCOME2": {"ticker": "EVT2-OUTCOME2", "yes_sub_title": "Second One"},
    }
    fake = FakeClient(milestones_map, live_map, market_map)
    monkeypatch.setattr(market_history, "record_outcome", lambda *a, **k: None)

    main.state["milestone_cache"].clear()
    market_results = asyncio.run(main.propagate_milestone_winners(fake, markets))

    assert market_results.get("EVT1-OUTCOME2") == "yes"
    assert market_results.get("EVT2-OUTCOME2") == "yes"
    # One batched call covering every related ticker across both events -
    # not one gather-of-individual-calls per event.
    assert len(fake.market_call_batches) == 1
    assert set(fake.market_call_batches[0]) == {
        "EVT1-OUTCOME1", "EVT1-OUTCOME2", "EVT2-OUTCOME1", "EVT2-OUTCOME2",
    }


# --- repoll-cache behavior (2026-08-15 tick_duration fix) - this used to
# call get_milestones_for_event() for every event on the watchlist, every
# tick, forever, unconditionally - confirmed live as one of two per-event
# REST loops with zero caching that together accounted for the bulk of a
# ~27s tick_duration plateau. See main._MILESTONE_REPOLL_SEC. -------------

def _winner_fixture():
    markets = [
        {"ticker": "EVT1-OUTCOME1", "event_ticker": "EVT1", "result": ""},
        {"ticker": "EVT1-OUTCOME2", "event_ticker": "EVT1", "result": ""},
    ]
    milestones_map = {
        "EVT1": [{"id": "ms1", "type": "winner_decl", "related_event_tickers": ["EVT1-OUTCOME1", "EVT1-OUTCOME2"]}]
    }
    live_map = {
        "ms1": {"details": {"winner": "Outcome Two", "related_event_tickers": ["EVT1-OUTCOME1", "EVT1-OUTCOME2"]}}
    }
    market_map = {
        "EVT1-OUTCOME1": {"ticker": "EVT1-OUTCOME1", "yes_sub_title": "Outcome One", "title": "Event 1 - Outcome One"},
        "EVT1-OUTCOME2": {"ticker": "EVT1-OUTCOME2", "yes_sub_title": "Outcome Two", "title": "Event 1 - Outcome Two"},
    }
    return markets, FakeClient(milestones_map, live_map, market_map)


def test_propagate_milestone_winners_does_not_repoll_a_confirmed_winner(monkeypatch):
    main.state["milestone_cache"].clear()
    markets, fake = _winner_fixture()
    monkeypatch.setattr(market_history, "record_outcome", lambda *a, **k: None)

    first = asyncio.run(main.propagate_milestone_winners(fake, markets))
    assert first.get("EVT1-OUTCOME2") == "yes"
    assert fake.milestone_calls == ["EVT1"]

    second = asyncio.run(main.propagate_milestone_winners(fake, markets))
    # Reapplied purely from cache - result stays complete, zero new calls.
    assert second.get("EVT1-OUTCOME2") == "yes"
    assert second.get("EVT1-OUTCOME1") == "no"
    assert fake.milestone_calls == ["EVT1"]  # unchanged - no second call
    assert fake.live_data_calls == ["ms1"]
    assert fake.market_calls == ["EVT1-OUTCOME1", "EVT1-OUTCOME2"]


def test_propagate_milestone_winners_does_not_repoll_a_no_winner_event_within_window(monkeypatch):
    main.state["milestone_cache"].clear()
    markets = [{"ticker": "EVT2-OUTCOME1", "event_ticker": "EVT2", "result": ""}]
    fake = FakeClient(milestones_map={}, live_map={}, market_map={})  # EVT2 has no milestone at all

    first = asyncio.run(main.propagate_milestone_winners(fake, markets))
    # Not yet resolved (no status: not finalized) and no milestone winner
    # either - correctly absent from market_results, not present with "".
    assert "EVT2-OUTCOME1" not in first
    assert fake.milestone_calls == ["EVT2"]

    second = asyncio.run(main.propagate_milestone_winners(fake, markets))
    assert fake.milestone_calls == ["EVT2"]  # still not due for a re-check


def test_propagate_milestone_winners_repolls_a_no_winner_event_once_stale(monkeypatch):
    main.state["milestone_cache"].clear()
    stale_check = time.time() - main._MILESTONE_REPOLL_SEC - 1
    main.state["milestone_cache"]["EVT2"] = {
        "checked_at": stale_check, "winner_found": False, "related": None, "mapped_winner_ticker": None,
    }
    markets = [{"ticker": "EVT2-OUTCOME1", "event_ticker": "EVT2", "result": ""}]
    fake = FakeClient(milestones_map={}, live_map={}, market_map={})

    asyncio.run(main.propagate_milestone_winners(fake, markets))
    assert fake.milestone_calls == ["EVT2"]  # due for a light re-poll
