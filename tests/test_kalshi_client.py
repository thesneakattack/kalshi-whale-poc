"""Wire-semantic tests for the public Kalshi read surface, exercised
through the KalshiPublicGateway compatibility facade (services/kalshi/public.py's
KalshiPublicGateway since A6). The market-selection policy tests that used
to live here moved with their implementation to
tests/test_market_selection.py at A7."""
import asyncio

from services.kalshi.public import KalshiPublicGateway


def _client():
    return KalshiPublicGateway(base_url="https://example.invalid/trade-api/v2", timeout=1.0)


class _FakeModel:
    """Stands in for the SDK's real Pydantic response models - only
    model_dump(mode="json") is ever called on these, so that's all that
    needs faking."""

    def __init__(self, data):
        self._data = data

    def model_dump(self, mode="json"):
        return self._data


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


# ---- Multivariate (combo) event discovery (issue #268) ---------------------
# docs/kalshi/get-multivariate-events.md ("GET /events/multivariate") and
# docs/kalshi/get-multivariate-event-collections.md ("GET
# /multivariate_event_collections") - live-verified 2026-08-30 against the
# real production API (unauthenticated, GET-only, security: [] on both
# OpenAPI paths) that both SDK methods exist on kalshi_python_async 3.27.0's
# composed KalshiClient (get_multivariate_events lives on EventsApi despite
# the "multivariate" name; get_multivariate_event_collections lives on
# MultivariateApi) and both accept exactly the kwargs the two doc pages
# document - confirmed by introspecting the installed SDK's method
# signatures directly, not assumed from the doc's parameter table alone.

def test_get_multivariate_events_returns_events_and_cursor(monkeypatch):
    client = _client()
    calls = []

    async def fake_get_multivariate_events(**kwargs):
        calls.append(kwargs)
        return type("R", (), {
            "events": [_FakeModel({"event_ticker": "EVT-A", "series_ticker": "KXMVECROSSCATEGORY-SHARD1"})],
            "cursor": "next-page-token",
        })()

    monkeypatch.setattr(client._client, "get_multivariate_events", fake_get_multivariate_events)
    result = asyncio.run(client.get_multivariate_events(series_ticker="KXMVECROSSCATEGORY-SHARD1", with_nested_markets=True))
    assert result == {
        "events": [{"event_ticker": "EVT-A", "series_ticker": "KXMVECROSSCATEGORY-SHARD1"}],
        "cursor": "next-page-token",
    }
    # collection_ticker/cursor omitted when unset - same "don't pass explicit
    # None" convention get_markets already established (explicit None
    # differs from omitted at the SDK's wire level for at least one other
    # operation, confirmed directly - see get_markets' own docstring).
    assert calls == [{"limit": 200, "with_nested_markets": True, "series_ticker": "KXMVECROSSCATEGORY-SHARD1"}]


def test_get_multivariate_events_passes_collection_ticker_and_cursor_when_set(monkeypatch):
    client = _client()
    calls = []

    async def fake_get_multivariate_events(**kwargs):
        calls.append(kwargs)
        return type("R", (), {"events": [], "cursor": ""})()

    monkeypatch.setattr(client._client, "get_multivariate_events", fake_get_multivariate_events)
    asyncio.run(client.get_multivariate_events(collection_ticker="KXMVECROSSCATEGORY-R", cursor="resume-token", limit=50))
    assert calls == [{"limit": 50, "with_nested_markets": False, "collection_ticker": "KXMVECROSSCATEGORY-R", "cursor": "resume-token"}]


def test_get_multivariate_event_collections_paginates_to_completion(monkeypatch):
    # Confirmed live 2026-08-30: this series' collections alone paginate
    # across multiple 200-row pages (1,389 total collections system-wide
    # across 7 pages at the documented max limit) - the gateway must follow
    # `cursor` until it comes back empty, not just return page 1.
    client = _client()
    pages = [
        type("R", (), {
            "multivariate_contracts": [_FakeModel({"collection_ticker": "C1", "series_ticker": "KXMVECROSSCATEGORY"})],
            "cursor": "page-2-token",
        })(),
        type("R", (), {
            "multivariate_contracts": [_FakeModel({"collection_ticker": "C2", "series_ticker": "KXMVECROSSCATEGORY-SHARD1"})],
            "cursor": "",
        })(),
    ]
    calls = []

    async def fake_get_multivariate_event_collections(**kwargs):
        calls.append(kwargs)
        return pages.pop(0)

    monkeypatch.setattr(client._client, "get_multivariate_event_collections", fake_get_multivariate_event_collections)
    result = asyncio.run(client.get_multivariate_event_collections())
    assert [c["collection_ticker"] for c in result] == ["C1", "C2"]
    assert calls[0].get("cursor") is None
    assert calls[1]["cursor"] == "page-2-token"


def test_get_multivariate_event_collections_passes_status_and_series_filters(monkeypatch):
    client = _client()
    calls = []

    async def fake_get_multivariate_event_collections(**kwargs):
        calls.append(kwargs)
        return type("R", (), {"multivariate_contracts": [], "cursor": ""})()

    monkeypatch.setattr(client._client, "get_multivariate_event_collections", fake_get_multivariate_event_collections)
    asyncio.run(client.get_multivariate_event_collections(status="open", series_ticker="KXMVECROSSCATEGORY"))
    assert calls == [{"limit": 200, "status": "open", "series_ticker": "KXMVECROSSCATEGORY"}]


def test_get_multivariate_event_collections_stops_at_the_page_safety_cap(monkeypatch):
    # Defensive bound (this module's own rate-discipline convention, e.g.
    # catalog_scan._CATALOG_SCAN_BATCH_SIZE) - a cursor that never empties
    # (a real Kalshi bug, or a test double misconfigured) must not spin the
    # scheduler loop forever.
    client = _client()
    client._MVE_COLLECTIONS_MAX_PAGES = 3
    calls = []

    async def fake_get_multivariate_event_collections(**kwargs):
        calls.append(kwargs)
        return type("R", (), {
            "multivariate_contracts": [_FakeModel({"collection_ticker": f"C{len(calls)}"})],
            "cursor": "always-more",
        })()

    monkeypatch.setattr(client._client, "get_multivariate_event_collections", fake_get_multivariate_event_collections)
    result = asyncio.run(client.get_multivariate_event_collections())
    assert len(calls) == 3
    assert len(result) == 3


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

    client = KalshiPublicGateway(base_url="https://example.invalid/trade-api/v2/", timeout=1.0)

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


def test_get_markets_by_tickers_explicit_batch_size_overrides_the_default_chunk(monkeypatch):
    # I8: the rate-limit probe measures 50/100/200-ticker requests through
    # this one parameter; production callers leave it None and keep the
    # conservative default (docs/kalshi/get-markets.md documents `tickers`
    # as a comma-separated filter with no per-call cap).
    client = _client()
    calls = []

    async def fake_get_markets(tickers, limit):
        calls.append((len(tickers.split(",")), limit))
        return type("R", (), {"markets": [_FakeModel({"ticker": t}) for t in tickers.split(",")]})()

    monkeypatch.setattr(client._client, "get_markets", fake_get_markets)
    tickers = [f"T{i}" for i in range(120)]
    result = asyncio.run(client.get_markets_by_tickers(tickers, batch_size=100))
    assert calls == [(100, 100), (20, 20)]
    assert len(result) == 120
    calls.clear()
    asyncio.run(client.get_markets_by_tickers(tickers))  # default path unchanged: 50-ticker chunks
    assert calls == [(50, 50), (50, 50), (20, 20)]


# ---- get_series_fee_changes (kalshi-category-data-completeness Task 2) -----
# docs/kalshi/get-series-fee-changes.md: GetSeriesFeeChangesResponse carries
# only series_fee_change_arr - no limit/cursor, so this is one unpaginated
# call, unlike get_events/get_live_datas/get_markets_by_tickers above.
# series_ticker is an optional filter the doc's own schema marks not
# required - omitted here on purpose to fetch the whole array in one call.
#
# Fetched raw via _get_json, not through the typed SDK client (code-review
# fix, 2026-08-31) - SeriesFeeChange.fee_type shares get_series_list's
# Series.fee_type FeeType enum, and get_series_list's own docstring
# documents that enum missing quadratic_with_combo_maker_fees in the
# installed SDK even though a real live series carries it; the SDK's
# Pydantic-validated get_series_fee_changes would raise on a single such
# scheduled change and fail the entire array. So this is tested the same
# way get_series_list's raw-fetch precedent would be: patch _get_json
# directly, not client._client's typed method.


def test_get_series_fee_changes_omits_series_ticker_for_the_full_array(monkeypatch):
    client = _client()
    calls = []

    async def fake_get_json(path, endpoint, params=None):
        calls.append((path, endpoint, params))
        return {"series_fee_change_arr": [
            {"id": "1", "series_ticker": "KXNFLGAME", "fee_type": "quadratic",
             "fee_multiplier": 1.0, "scheduled_ts": "2023-11-14T22:13:20+00:00"},
        ]}

    monkeypatch.setattr(client, "_get_json", fake_get_json)
    result = asyncio.run(client.get_series_fee_changes())
    # show_historical defaults True, no series_ticker filter passed at all
    assert calls == [("/series/fee_changes", "get_series_fee_changes", {"show_historical": True})]
    assert result == [{"id": "1", "series_ticker": "KXNFLGAME", "fee_type": "quadratic",
                        "fee_multiplier": 1.0, "scheduled_ts": "2023-11-14T22:13:20+00:00"}]


def test_get_series_fee_changes_recovers_every_entry_even_with_an_unmodeled_fee_type(monkeypatch):
    # The concrete failure mode the raw-fetch fix above exists to avoid:
    # a fee_type value the installed SDK's FeeType enum doesn't recognise
    # (e.g. quadratic_with_combo_maker_fees, confirmed live 2026-08-21 on
    # get_series_list) would raise inside the typed path and lose EVERY
    # entry in the array, not just the offending one. Raw JSON has no enum
    # to validate against, so an unrecognized string passes through like
    # any other field - this is the completeness property the fix restores.
    client = _client()

    async def fake_get_json(path, endpoint, params=None):
        return {"series_fee_change_arr": [
            {"id": "1", "series_ticker": "KXNFLGAME", "fee_type": "quadratic_with_combo_maker_fees",
             "fee_multiplier": 1.0, "scheduled_ts": "2023-11-14T22:13:20+00:00"},
            {"id": "2", "series_ticker": "KXOTHER", "fee_type": "flat",
             "fee_multiplier": 0.5, "scheduled_ts": "2023-11-14T22:13:20+00:00"},
        ]}

    monkeypatch.setattr(client, "_get_json", fake_get_json)
    result = asyncio.run(client.get_series_fee_changes())
    assert [r["series_ticker"] for r in result] == ["KXNFLGAME", "KXOTHER"]
    assert result[0]["fee_type"] == "quadratic_with_combo_maker_fees"
