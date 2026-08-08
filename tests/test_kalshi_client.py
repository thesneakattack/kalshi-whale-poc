import asyncio

from services.kalshi_client import KalshiClient


def _client():
    return KalshiClient(base_url="https://example.invalid/trade-api/v2", timeout=1.0)


def _market(ticker, event_ticker, volume):
    return {"ticker": ticker, "event_ticker": event_ticker, "volume_24h_fp": str(volume)}


def test_round_robin_lets_a_thin_event_through_a_dominant_one(monkeypatch):
    # Event A has 8 markets, every one higher volume than either of event
    # B's - a flat top-n-by-volume sort would let A fill the whole result
    # and crowd B out entirely (the real bug this replaced a flat per-event
    # cap for, see kalshi_client.py's docstring).
    a_markets = [_market(f"A-{i}", "EVT-A", 1000 - i) for i in range(8)]
    b_markets = [_market("B-1", "EVT-B", 500), _market("B-2", "EVT-B", 400)]

    async def fake_get_markets(limit, status, series_ticker=None):
        return {"SER-A": a_markets, "SER-B": b_markets}.get(series_ticker, [])

    client = _client()
    monkeypatch.setattr(client, "get_markets", fake_get_markets)

    result = asyncio.run(client.get_top_volume_markets(n=4, min_volume=0, series_tickers=["SER-A", "SER-B"]))
    tickers = [m["ticker"] for m in result]
    event_tickers = {m["event_ticker"] for m in result}

    assert "EVT-A" in event_tickers
    assert "EVT-B" in event_tickers
    assert "B-1" in tickers and "B-2" in tickers  # B's only two markets both got through


def test_round_robin_prefers_higher_volume_within_each_event(monkeypatch):
    a_markets = [_market("A-LOW", "EVT-A", 100), _market("A-HIGH", "EVT-A", 900)]

    async def fake_get_markets(limit, status, series_ticker=None):
        return a_markets if series_ticker == "SER-A" else []

    client = _client()
    monkeypatch.setattr(client, "get_markets", fake_get_markets)

    result = asyncio.run(client.get_top_volume_markets(n=1, min_volume=0, series_tickers=["SER-A"]))
    assert [m["ticker"] for m in result] == ["A-HIGH"]


def test_single_dominant_event_can_still_fill_the_whole_watchlist(monkeypatch):
    # When nothing else is competing, an event's own sub-markets shouldn't
    # be arbitrarily truncated by some fixed per-event number - round-robin
    # with only one event present just keeps taking from that one event.
    a_markets = [_market(f"A-{i}", "EVT-A", 100 - i) for i in range(10)]

    async def fake_get_markets(limit, status, series_ticker=None):
        return a_markets if series_ticker == "SER-A" else []

    client = _client()
    monkeypatch.setattr(client, "get_markets", fake_get_markets)

    result = asyncio.run(client.get_top_volume_markets(n=6, min_volume=0, series_tickers=["SER-A"]))
    assert len(result) == 6
    assert [m["ticker"] for m in result] == [f"A-{i}" for i in range(6)]


def test_min_volume_filters_before_selection(monkeypatch):
    markets = [_market("A-1", "EVT-A", 500), _market("A-2", "EVT-A", 5)]

    async def fake_get_markets(limit, status, series_ticker=None):
        return markets if series_ticker == "SER-A" else []

    client = _client()
    monkeypatch.setattr(client, "get_markets", fake_get_markets)

    result = asyncio.run(client.get_top_volume_markets(n=10, min_volume=100, series_tickers=["SER-A"]))
    assert [m["ticker"] for m in result] == ["A-1"]


def test_no_series_tickers_returns_empty():
    client = _client()
    result = asyncio.run(client.get_top_volume_markets(n=10, min_volume=0, series_tickers=[]))
    assert result == []


def test_falls_back_to_ticker_when_no_event_ticker(monkeypatch):
    # Combo/solo markets with no event_ticker shouldn't collide into one
    # fake "group" together - each should be treated as its own group.
    markets = [
        {"ticker": "SOLO-1", "volume_24h_fp": "50"},
        {"ticker": "SOLO-2", "volume_24h_fp": "40"},
    ]

    async def fake_get_markets(limit, status, series_ticker=None):
        return markets if series_ticker == "SER-A" else []

    client = _client()
    monkeypatch.setattr(client, "get_markets", fake_get_markets)

    result = asyncio.run(client.get_top_volume_markets(n=10, min_volume=0, series_tickers=["SER-A"]))
    assert {m["ticker"] for m in result} == {"SOLO-1", "SOLO-2"}
