import asyncio

from services.kalshi_client import KalshiClient


def _client():
    return KalshiClient(base_url="https://example.invalid/trade-api/v2", timeout=1.0)


def _market(ticker, event_ticker, volume):
    return {"ticker": ticker, "event_ticker": event_ticker, "volume_24h_fp": str(volume)}


class _FakeModel:
    """Stands in for the SDK's real Pydantic response models - only
    model_dump(mode="json") is ever called on these, so that's all that
    needs faking."""

    def __init__(self, data):
        self._data = data

    def model_dump(self, mode="json"):
        return self._data


# round_robin_select's "parent" is the *series* (e.g. "KXPGAH2H" - see
# services.signal_log.series_of, the same ticker-prefix definition used
# everywhere else in this app). n means distinct series, not individual
# markets; each selected series contributes every one of its own markets
# (every event/pairing under it, every ticker on each), highest-volume
# first, capped at max_children_per_parent if set.
#
# This settled after two earlier attempts, both rejected for real, live-
# confirmed reasons: grouping at event_ticker let one tournament's many
# individual pairings crowd out the whole watchlist (42 of 47 real slots
# were one PGA tournament's head-to-heads). Grouping at event_ticker with
# series-level round-robin fairness matched Kalshi's own documented
# hierarchy (no tournament level exists - Category > Series > Event >
# Market) but still let two *different*, concurrently-live matches sharing
# one series (two separate Dota2 games under "KXDOTA2MAP") each count
# against the watchlist size separately - rejected by direct instruction:
# "i dont want those pairings to count against the watchlist count, only
# the parent series." Series-level grouping is what's shipped - the known,
# accepted tradeoff being that two unrelated same-series matches are
# watched together as one "parent" rather than counted as two.

def test_selected_series_includes_every_child_by_default():
    # Direct request: "i want every child" - no cap unless
    # max_children_per_parent is explicitly set. Includes every event under
    # the series, not just its highest-volume one.
    markets = [_market(f"A-{i}", f"EVT-A{i}", 100 - i) for i in range(10)]
    result = KalshiClient.round_robin_select(markets, n=1)
    assert len(result) == 10
    assert [m["ticker"] for m in result] == [f"A-{i}" for i in range(10)]


def test_max_children_per_parent_caps_highest_volume_first():
    # Direct request: "i want the ability to cap" + "children markets
    # prioritized by volume."
    markets = [_market(f"A-{i}", f"EVT-A{i}", 100 - i) for i in range(10)]
    result = KalshiClient.round_robin_select(markets, n=1, max_children_per_parent=3)
    assert [m["ticker"] for m in result] == ["A-0", "A-1", "A-2"]


def test_a_dominant_series_dominant_parents_prefers_higher_volume_series_when_n_is_limited():
    # Three distinct series competing for 2 slots - A and B should win
    # (higher best-child volume), C should be excluded entirely.
    a_markets = [_market(f"A-{i}", f"EVT-A{i}", 1000 - i) for i in range(8)]
    b_markets = [_market("B-1", "EVT-B1", 500), _market("B-2", "EVT-B2", 400)]
    c_markets = [_market("C-1", "EVT-C1", 50)]
    candidates = a_markets + b_markets + c_markets  # already volume-sorted, as get_candidate_markets produces

    result = KalshiClient.round_robin_select(candidates, n=2)
    series = {m["ticker"].split("-")[0] for m in result}
    assert series == {"A", "B"}
    assert not any(m["ticker"].startswith("C-") for m in result)


def test_real_wyndham_regression_one_dominant_series_costs_exactly_one_slot():
    # Direct regression: a whole tournament (23 real head-to-head pairings,
    # all sharing series "KXPGAH2H") must cost exactly one watchlist slot,
    # not 23 - the exact confirmed-live bug this whole feature fixed.
    golf = [_market(f"KXPGAH2H-EVT{i}-X", f"KXPGAH2H-EVT{i}", 1000 - i) for i in range(23)]
    dota = [_market("KXDOTA2MAP-A-X", "KXDOTA2MAP-A", 500)]
    soccer = [_market("KXCLUBFBTTS-A-X", "KXCLUBFBTTS-A", 300)]
    candidates = golf + dota + soccer

    result = KalshiClient.round_robin_select(candidates, n=3)
    series = {m["ticker"].split("-")[0] for m in result}
    assert series == {"KXPGAH2H", "KXDOTA2MAP", "KXCLUBFBTTS"}  # all 3 series fit in 3 slots
    assert len(result) == 23 + 1 + 1  # golf's 23 pairings all came along for free under its one slot


def test_concurrent_events_under_one_series_are_watched_together_as_one_parent():
    # The explicit, accepted tradeoff: two different, concurrently-live
    # matches sharing one series (two separate Dota2 games, both under
    # "KXDOTA2MAP") are watched together under that series's single slot,
    # not counted as two - direct instruction: "i dont want those pairings
    # to count against the watchlist count, only the parent series."
    markets = [
        _market("KXDOTA2MAP-ILLJEN-2-JEN", "KXDOTA2MAP-ILLJEN-2", 100),
        _market("KXDOTA2MAP-ILLJEN-2-ILL", "KXDOTA2MAP-ILLJEN-2", 95),
        _market("KXDOTA2MAP-NHSPIRIT-2-NH", "KXDOTA2MAP-NHSPIRIT-2", 90),
        _market("KXDOTA2MAP-NHSPIRIT-2-SPIRIT", "KXDOTA2MAP-NHSPIRIT-2", 85),
    ]
    result = KalshiClient.round_robin_select(markets, n=1)
    assert len(result) == 4  # both matches' markets included under the one series slot
    assert {m["event_ticker"] for m in result} == {"KXDOTA2MAP-ILLJEN-2", "KXDOTA2MAP-NHSPIRIT-2"}


def test_tickers_sharing_a_prefix_are_the_same_parent_even_with_no_event_ticker():
    # No event_ticker at all - falls back to the ticker prefix (series).
    # Two tickers sharing a prefix collide into one parent, matching the
    # series-level grouping used everywhere else in this function.
    markets = [{"ticker": "SOLO-1", "volume_24h_fp": "50"}, {"ticker": "SOLO-2", "volume_24h_fp": "40"}]
    result = KalshiClient.round_robin_select(markets, n=1)
    assert {m["ticker"] for m in result} == {"SOLO-1", "SOLO-2"}


def test_round_robin_select_never_pads_below_n():
    a_markets = [_market("A-1", "EVT-A", 100)]
    result = KalshiClient.round_robin_select(a_markets, n=10)
    assert result == a_markets  # only one real series available - stays at 1, not padded to 10


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


# get_top_volume_markets is get_candidate_markets + round_robin_select
# composed together (see main.py's live-markets-only discovery, which needs
# to filter the candidate pool between the two steps) - these confirm each
# half independently, on top of get_top_volume_markets' existing coverage
# above confirming the composition still behaves identically end to end.

def test_get_candidate_markets_returns_full_volume_sorted_pool_no_cutoff(monkeypatch):
    markets = [_market(f"A-{i}", "EVT-A", 100 - i) for i in range(10)]

    async def fake_get_markets(limit, status, series_ticker=None):
        return markets if series_ticker == "SER-A" else []

    client = _client()
    monkeypatch.setattr(client, "get_markets", fake_get_markets)

    result = asyncio.run(client.get_candidate_markets(min_volume=0, series_tickers=["SER-A"]))
    assert len(result) == 10  # no n/cutoff - the whole real candidate pool
    assert [m["ticker"] for m in result] == [f"A-{i}" for i in range(10)]  # volume-sorted desc


def test_get_candidate_markets_applies_min_volume_filter(monkeypatch):
    markets = [_market("A-1", "EVT-A", 500), _market("A-2", "EVT-A", 5)]

    async def fake_get_markets(limit, status, series_ticker=None):
        return markets if series_ticker == "SER-A" else []

    client = _client()
    monkeypatch.setattr(client, "get_markets", fake_get_markets)

    result = asyncio.run(client.get_candidate_markets(min_volume=100, series_tickers=["SER-A"]))
    assert [m["ticker"] for m in result] == ["A-1"]


def test_get_candidate_markets_no_series_tickers_returns_empty():
    client = _client()
    result = asyncio.run(client.get_candidate_markets(min_volume=0, series_tickers=[]))
    assert result == []


def test_get_top_volume_markets_threads_max_children_per_parent(monkeypatch):
    a_markets = [_market(f"A-{i}", "EVT-A", 100 - i) for i in range(10)]

    async def fake_get_markets(limit, status, series_ticker=None):
        return a_markets if series_ticker == "SER-A" else []

    client = _client()
    monkeypatch.setattr(client, "get_markets", fake_get_markets)

    result = asyncio.run(client.get_top_volume_markets(
        n=1, min_volume=0, series_tickers=["SER-A"], max_children_per_parent=2,
    ))
    assert [m["ticker"] for m in result] == ["A-0", "A-1"]


# --- Batched SDK wrappers (2026-08-16 API-doc audit findings B3.1/B3.2 +
# the signal-resolution backlog fix) - get_events/get_live_datas/
# get_markets_by_tickers each replace what used to be N individual calls at
# real call sites in main.py with one (or a handful of chunked) requests.
# get_milestones_bulk is the category-scoped bulk listing (finding B3.3),
# distinct from the existing per-event get_milestones_for_event. -----------

def test_get_events_returns_flat_events_keyed_by_ticker(monkeypatch):
    client = _client()
    calls = []

    async def fake_get_events(tickers, limit):
        calls.append((tickers, limit))
        return type("R", (), {"events": [
            _FakeModel({"event_ticker": "EVT-A", "title": "A"}),
            _FakeModel({"event_ticker": "EVT-B", "title": "B"}),
        ]})()

    monkeypatch.setattr(client._client, "get_events", fake_get_events)
    result = asyncio.run(client.get_events(["EVT-A", "EVT-B"]))
    assert result == [{"event_ticker": "EVT-A", "title": "A"}, {"event_ticker": "EVT-B", "title": "B"}]
    assert calls == [("EVT-A,EVT-B", 2)]  # one batched call, not two


def test_get_events_empty_list_makes_no_call(monkeypatch):
    client = _client()

    async def fake_get_events(tickers, limit):
        raise AssertionError("should not be called for an empty list")

    monkeypatch.setattr(client._client, "get_events", fake_get_events)
    result = asyncio.run(client.get_events([]))
    assert result == []


def test_get_events_chunks_above_the_batch_size(monkeypatch):
    client = _client()
    client._EVENTS_BATCH_SIZE = 2  # shrink for a fast, deterministic test
    calls = []

    async def fake_get_events(tickers, limit):
        calls.append(tickers)
        return type("R", (), {"events": [_FakeModel({"event_ticker": t}) for t in tickers.split(",")]})()

    monkeypatch.setattr(client._client, "get_events", fake_get_events)
    result = asyncio.run(client.get_events(["A", "B", "C"]))
    assert calls == ["A,B", "C"]  # 3 tickers / batch size 2 -> 2 chunked calls
    assert [e["event_ticker"] for e in result] == ["A", "B", "C"]


def test_get_live_datas_returns_flat_shape_keyed_by_milestone_id(monkeypatch):
    client = _client()
    calls = []

    async def fake_get_live_datas(milestone_ids):
        calls.append(list(milestone_ids))
        return type("R", (), {"live_datas": [
            _FakeModel({"milestone_id": "ms1", "type": "football_game", "details": {"quarter": 4}}),
        ]})()

    monkeypatch.setattr(client._client, "get_live_datas", fake_get_live_datas)
    result = asyncio.run(client.get_live_datas(["ms1"]))
    assert result == {"ms1": {"milestone_id": "ms1", "type": "football_game", "details": {"quarter": 4}}}
    assert calls == [["ms1"]]


def test_get_live_datas_empty_list_makes_no_call(monkeypatch):
    client = _client()

    async def fake_get_live_datas(milestone_ids):
        raise AssertionError("should not be called for an empty list")

    monkeypatch.setattr(client._client, "get_live_datas", fake_get_live_datas)
    result = asyncio.run(client.get_live_datas([]))
    assert result == {}


def test_get_live_datas_chunks_above_the_batch_size(monkeypatch):
    client = _client()
    client._LIVE_DATAS_BATCH_SIZE = 2
    calls = []

    async def fake_get_live_datas(milestone_ids):
        calls.append(list(milestone_ids))
        return type("R", (), {"live_datas": [_FakeModel({"milestone_id": mid}) for mid in milestone_ids]})()

    monkeypatch.setattr(client._client, "get_live_datas", fake_get_live_datas)
    result = asyncio.run(client.get_live_datas(["ms1", "ms2", "ms3"]))
    assert calls == [["ms1", "ms2"], ["ms3"]]
    assert set(result.keys()) == {"ms1", "ms2", "ms3"}


def test_get_live_datas_skips_a_chunk_the_sdk_cant_parse(monkeypatch):
    # Real, live-confirmed API/SDK mismatch (2026-08-16, surfaced by
    # raising top_series_per_category): Kalshi returns "live_datas": null
    # for a chunk with nothing live in it, rather than [], but the SDK's
    # own GetLiveDatasResponse model requires a list field - parsing the
    # raw response throws a real pydantic.ValidationError inside the SDK
    # before this method ever sees a response object. null and [] mean
    # the same thing here, so a chunk that fails this way must be skipped,
    # not lose every other chunk's real data (and the whole tick's
    # state["error"]) over one malformed one.
    from pydantic import BaseModel

    class _StrictListResponse(BaseModel):
        live_datas: list

    client = _client()
    client._LIVE_DATAS_BATCH_SIZE = 2
    calls = []

    async def fake_get_live_datas(milestone_ids):
        calls.append(list(milestone_ids))
        if milestone_ids == ["ms1", "ms2"]:
            _StrictListResponse(live_datas=None)  # raises pydantic.ValidationError, matching the real SDK
        return type("R", (), {"live_datas": [_FakeModel({"milestone_id": mid}) for mid in milestone_ids]})()

    monkeypatch.setattr(client._client, "get_live_datas", fake_get_live_datas)
    result = asyncio.run(client.get_live_datas(["ms1", "ms2", "ms3"]))
    assert calls == [["ms1", "ms2"], ["ms3"]]
    assert set(result.keys()) == {"ms3"}


def test_get_milestones_bulk_passes_category_and_watermark(monkeypatch):
    client = _client()
    calls = []

    async def fake_get_milestones(limit, category=None, min_updated_ts=None, related_event_ticker=None):
        calls.append((limit, category, min_updated_ts, related_event_ticker))
        return type("R", (), {"milestones": [_FakeModel({"id": "ms1", "category": "Sports"})]})()

    monkeypatch.setattr(client._client, "get_milestones", fake_get_milestones)
    result = asyncio.run(client.get_milestones_bulk("Sports", min_updated_ts=12345, limit=200))
    assert result == [{"id": "ms1", "category": "Sports"}]
    assert calls == [(200, "Sports", 12345, None)]  # not related_event_ticker-scoped


def test_get_markets_by_tickers_returns_dict_keyed_by_ticker(monkeypatch):
    client = _client()
    calls = []

    async def fake_get_markets(tickers, limit):
        calls.append((tickers, limit))
        return type("R", (), {"markets": [
            _FakeModel({"ticker": "TICK-A", "result": "yes"}),
            _FakeModel({"ticker": "TICK-B", "result": ""}),
        ]})()

    monkeypatch.setattr(client._client, "get_markets", fake_get_markets)
    result = asyncio.run(client.get_markets_by_tickers(["TICK-A", "TICK-B"]))
    assert result == {"TICK-A": {"ticker": "TICK-A", "result": "yes"}, "TICK-B": {"ticker": "TICK-B", "result": ""}}
    assert calls == [("TICK-A,TICK-B", 2)]


def test_get_markets_by_tickers_does_not_filter_by_status(monkeypatch):
    # get_markets() (the wrapper used everywhere else) defaults to
    # status="open" - get_markets_by_tickers must NOT do that, since
    # main._check_signal_resolutions specifically needs already-settled
    # markets to still come back.
    client = _client()
    seen_kwargs = {}

    async def fake_get_markets(**kwargs):
        seen_kwargs.update(kwargs)
        return type("R", (), {"markets": []})()

    monkeypatch.setattr(client._client, "get_markets", fake_get_markets)
    asyncio.run(client.get_markets_by_tickers(["TICK-A"]))
    assert "status" not in seen_kwargs


def test_get_markets_by_tickers_empty_list_makes_no_call(monkeypatch):
    client = _client()

    async def fake_get_markets(tickers, limit):
        raise AssertionError("should not be called for an empty list")

    monkeypatch.setattr(client._client, "get_markets", fake_get_markets)
    result = asyncio.run(client.get_markets_by_tickers([]))
    assert result == {}


def test_get_markets_by_tickers_chunks_above_the_batch_size(monkeypatch):
    client = _client()
    client._MARKETS_BY_TICKERS_BATCH_SIZE = 2
    calls = []

    async def fake_get_markets(tickers, limit):
        calls.append(tickers)
        return type("R", (), {"markets": [_FakeModel({"ticker": t}) for t in tickers.split(",")]})()

    monkeypatch.setattr(client._client, "get_markets", fake_get_markets)
    result = asyncio.run(client.get_markets_by_tickers(["A", "B", "C"]))
    assert calls == ["A,B", "C"]
    assert set(result.keys()) == {"A", "B", "C"}


# ---- A5 transport delegation (Kalshi Integration Phase A) ------------------


def test_construction_delegates_to_the_boundary_transport(monkeypatch):
    """A5: SDK-client construction is owned by services/kalshi/transport.py;
    the gateway (which this facade subclasses since A6) delegates instead
    of building kpa.Configuration itself, so there is exactly one
    construction implementation for boundary modules and facade to share."""
    from services.kalshi import transport

    sentinel = object()
    seen = []

    def fake_build(base_url):
        seen.append(base_url)
        return sentinel

    monkeypatch.setattr(transport, "build_public_client", fake_build)

    client = KalshiClient(base_url="https://example.invalid/trade-api/v2/", timeout=1.0)

    assert client._client is sentinel
    assert seen == ["https://example.invalid/trade-api/v2/"]
    assert client.base_url == "https://example.invalid/trade-api/v2"


def test_close_still_closes_the_sdk_client(monkeypatch):
    client = _client()
    closed = []

    async def fake_close():
        closed.append(True)

    monkeypatch.setattr(client._client, "close", fake_close)
    asyncio.run(client.close())
    assert closed == [True]
