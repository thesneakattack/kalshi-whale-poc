import asyncio
import time

from services import market_history
import main


class FakeClient:
    def __init__(self, milestones_map, live_map, market_map):
        self._milestones = milestones_map
        self._live = live_map
        self._markets = market_map

    async def get_milestones_for_event(self, event_ticker):
        return self._milestones.get(event_ticker, [])

    async def get_live_data(self, ms_type, ms_id):
        return self._live.get(ms_id, {})

    async def get_market(self, ticker):
        return self._markets.get(ticker)


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
        "ms1": {"live_data": {"details": {"winner": "Outcome Two", "related_event_tickers": ["EVT1-OUTCOME1", "EVT1-OUTCOME2"]}}}
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

    market_results = asyncio.run(main.propagate_milestone_winners(fake, markets))

    # Expect EVT1-OUTCOME2 mapped to yes, EVT1-OUTCOME1 to no
    assert market_results.get("EVT1-OUTCOME2") == "yes"
    assert market_results.get("EVT1-OUTCOME1") == "no"
    # And outcomes recorded for both
    assert ("EVT1-OUTCOME2", "yes") in recorded
    assert ("EVT1-OUTCOME1", "no") in recorded
