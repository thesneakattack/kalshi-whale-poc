import asyncio
import time

from services import market_history
import main


class FakeClient:
    def __init__(self, milestones_map, live_map, market_map, structured_targets_map=None):
        self._milestones = milestones_map
        self._live = live_map  # milestone_id -> {"details": {...}} (flat, matching get_live_datas' real shape)
        self._markets = market_map
        # structured_target_id (uuid) -> resolved StructuredTarget dict
        # (kalshi-category-data-completeness Task 9) - defaults to {} so
        # every pre-existing FakeClient(...) call site (none of which pass
        # a custom_strike at all) is unaffected: get_structured_targets is
        # only ever called when propagate_milestone_winners finds a
        # non-empty custom_strike dict to resolve.
        self._structured_targets = structured_targets_map or {}
        self.milestone_calls = []
        self.live_data_calls = []
        self.market_calls = []
        self.market_call_batches = []  # one entry per get_markets_by_tickers call
        self.structured_targets_calls = []

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

    async def get_structured_targets(self, ids):
        # Batched (kalshi-category-data-completeness Task 9) - resolves
        # custom_strike UUIDs to their real name/type.
        self.structured_targets_calls.extend(ids)
        return {i: self._structured_targets[i] for i in ids if i in self._structured_targets}


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


def test_propagate_milestone_winner_none_for_a_settlement_input_type(monkeypatch):
    # truflation is a D2 no-op type (Task 5, milestone_live_data.py's
    # _EXTRACTORS): even with a raw `winner` key present in live-data
    # details, the wired call site must not surface it as a market result -
    # it's an index series, not a resolution event. Matches
    # test_propagate_milestone_winner's own fixture shape above, just with
    # a settlement-input `type` instead of the synthetic "winner_decl".
    markets = [{"ticker": "EVT1-OUTCOME1", "event_ticker": "EVT1", "result": ""}]
    milestones_map = {
        "EVT1": [{"id": "ms1", "type": "truflation", "related_event_tickers": ["EVT1-OUTCOME1"]}]
    }
    live_map = {
        "ms1": {"details": {"winner": "should not surface", "related_event_tickers": ["EVT1-OUTCOME1"]}}
    }
    fake = FakeClient(milestones_map, live_map, market_map={})

    monkeypatch.setattr(market_history, "record_outcome", lambda *a, **k: None)

    main.state["milestone_cache"].clear()
    market_results = asyncio.run(main.propagate_milestone_winners(fake, markets))

    assert "EVT1-OUTCOME1" not in market_results
    # Confirms the skip happened because extract() nulled the winner, not
    # because `related` was ever empty (the actual no-op reason) - if
    # get_markets_by_tickers were called it would mean events_with_winner
    # wrongly included this event.
    assert fake.market_calls == []


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


# --- structured custom_strike resolution (kalshi-category-data-completeness
# Task 9, targets_and_milestones.md:73-86) - a strike_type: "structured"
# related market's custom_strike dict holds a structured-target UUID, not a
# plain string; the old str(winner).lower() in str(v).lower() substring
# match against that raw UUID can never succeed (134/149 sampled real
# markets are this type - the vast majority of real winner-propagation
# traffic was silently falling through to the weaker yes_sub_title/title
# match below, or failing outright). These fixtures deliberately carry no
# yes_sub_title/no_sub_title/title at all, so a passing assertion can only
# be explained by the new custom_strike resolution path, not the pre-
# existing fallback. -------------------------------------------------------

def test_propagate_milestone_winner_resolves_structured_custom_strike(monkeypatch):
    markets = [{"ticker": "EVT1-OUTCOME1", "event_ticker": "EVT1", "result": ""}]
    milestones_map = {
        "EVT1": [{"id": "ms1", "type": "winner_decl", "related_event_tickers": ["EVT1-OUTCOME1"]}]
    }
    live_map = {
        "ms1": {"details": {"winner": "Team Alpha", "related_event_tickers": ["EVT1-OUTCOME1"]}}
    }
    # Kalshi's own example key is "basketball_team" (targets_and_milestones.md)
    # - deliberately not "target" here, to prove the resolution path doesn't
    # hardcode a key name, matching the existing cs.values() generality.
    market_map = {
        "EVT1-OUTCOME1": {
            "ticker": "EVT1-OUTCOME1",
            "strike_type": "structured",
            "custom_strike": {"basketball_team": "uuid-1"},
        },
    }
    structured_targets_map = {"uuid-1": {"id": "uuid-1", "name": "Team Alpha", "type": "team"}}
    fake = FakeClient(milestones_map, live_map, market_map, structured_targets_map=structured_targets_map)
    monkeypatch.setattr(market_history, "record_outcome", lambda *a, **k: None)

    main.state["milestone_cache"].clear()
    main.state["structured_targets_cache"].clear()
    market_results = asyncio.run(main.propagate_milestone_winners(fake, markets))

    # catalog_scan.propagate_milestone_winners's real return shape is
    # ticker -> "yes"/"no" (its own docstring) - "Team Alpha" is the
    # fixture's declared winner and structured_targets resolves uuid-1 ->
    # "Team Alpha" -> EVT1-OUTCOME1's own ticker, so it maps to "yes".
    assert market_results["EVT1-OUTCOME1"] == "yes"
    assert fake.structured_targets_calls == ["uuid-1"]


def test_propagate_milestone_winner_only_fetches_missing_structured_target_ids(monkeypatch):
    # The caching-shape judgment call this task made (state[
    # "structured_targets_cache"]: flat, no-TTL, incrementally grown - see
    # that key's own comment in app_state.py) - a uuid resolved on an
    # earlier tick (or, as here, pre-seeded) must never be re-fetched, only
    # the ids actually missing from the cache.
    markets = [
        {"ticker": "EVT1-OUTCOME1", "event_ticker": "EVT1", "result": ""},
        {"ticker": "EVT2-OUTCOME1", "event_ticker": "EVT2", "result": ""},
    ]
    milestones_map = {
        "EVT1": [{"id": "ms1", "type": "winner_decl", "related_event_tickers": ["EVT1-OUTCOME1"]}],
        "EVT2": [{"id": "ms2", "type": "winner_decl", "related_event_tickers": ["EVT2-OUTCOME1"]}],
    }
    live_map = {
        "ms1": {"details": {"winner": "Team Alpha", "related_event_tickers": ["EVT1-OUTCOME1"]}},
        "ms2": {"details": {"winner": "Team Beta", "related_event_tickers": ["EVT2-OUTCOME1"]}},
    }
    market_map = {
        "EVT1-OUTCOME1": {
            "ticker": "EVT1-OUTCOME1", "strike_type": "structured",
            "custom_strike": {"basketball_team": "uuid-1"},
        },
        "EVT2-OUTCOME1": {
            "ticker": "EVT2-OUTCOME1", "strike_type": "structured",
            "custom_strike": {"basketball_team": "uuid-2"},
        },
    }
    structured_targets_map = {
        "uuid-1": {"id": "uuid-1", "name": "Team Alpha", "type": "team"},
        "uuid-2": {"id": "uuid-2", "name": "Team Beta", "type": "team"},
    }
    fake = FakeClient(milestones_map, live_map, market_map, structured_targets_map=structured_targets_map)
    monkeypatch.setattr(market_history, "record_outcome", lambda *a, **k: None)

    main.state["milestone_cache"].clear()
    main.state["structured_targets_cache"].clear()
    # uuid-1 already resolved (an earlier tick's work) - only uuid-2 is new.
    main.state["structured_targets_cache"]["uuid-1"] = {"id": "uuid-1", "name": "Team Alpha", "type": "team"}

    market_results = asyncio.run(main.propagate_milestone_winners(fake, markets))

    assert market_results["EVT1-OUTCOME1"] == "yes"
    assert market_results["EVT2-OUTCOME1"] == "yes"
    assert fake.structured_targets_calls == ["uuid-2"]


def test_propagate_milestone_winner_structured_market_falls_back_when_unresolved(monkeypatch):
    # Regression-testing standard for this plan: the existing yes_sub_title/
    # no_sub_title/title fallback must keep working unchanged - here for a
    # structured market whose custom_strike uuid get_structured_targets
    # simply doesn't return (a real "Kalshi doesn't return this id" case,
    # same skip-not-crash convention as get_markets_by_tickers/get_events).
    # No cache entry for the uuid means the resolution loop matches
    # nothing, and control falls through to the yes_sub_title match below.
    markets = [{"ticker": "EVT1-OUTCOME1", "event_ticker": "EVT1", "result": ""}]
    milestones_map = {
        "EVT1": [{"id": "ms1", "type": "winner_decl", "related_event_tickers": ["EVT1-OUTCOME1"]}]
    }
    live_map = {
        "ms1": {"details": {"winner": "Team Alpha", "related_event_tickers": ["EVT1-OUTCOME1"]}}
    }
    market_map = {
        "EVT1-OUTCOME1": {
            "ticker": "EVT1-OUTCOME1",
            "strike_type": "structured",
            "custom_strike": {"basketball_team": "uuid-unresolvable"},
            "yes_sub_title": "Team Alpha",
        },
    }
    fake = FakeClient(milestones_map, live_map, market_map, structured_targets_map={})
    monkeypatch.setattr(market_history, "record_outcome", lambda *a, **k: None)

    main.state["milestone_cache"].clear()
    main.state["structured_targets_cache"].clear()
    market_results = asyncio.run(main.propagate_milestone_winners(fake, markets))

    assert market_results["EVT1-OUTCOME1"] == "yes"
    assert fake.structured_targets_calls == ["uuid-unresolvable"]
