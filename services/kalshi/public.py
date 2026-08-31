"""Documented public (unauthenticated) Kalshi read gateway — Phase A Task A6.

The one implementation of public wire semantics behind the integration
boundary: request/response envelope handling, SDK/raw-HTTP compatibility
logic, and per-operation batching, all backed by Kalshi's official
kalshi_python_async SDK (constructed via services/kalshi/transport.py) and
the shared services/http_client.py backoff/limiter/telemetry stack.

services/kalshi_client.py was the compatibility facade (deleted at zero
callers, C8): it subclassed
this gateway (delegation without re-implementation) and adds only the
market-selection *policy* methods, which are application concerns scheduled
to move into services/market_watch/ at A7 — the adapter batches and fetches
efficiently, but does not decide what the strategy should watch.

Covers only operations actually used at current HEAD (design-spec rule:
do not implement unused Kalshi endpoints). Every operation's exact mirrored
doc sources are declared in CONTRACT_DOCS below, enforced by
tools/quality_audit/kalshi_contract_docs.py in CI and aggregated/validated
at runtime by services/kalshi/provenance.py.

Every method returns plain dicts (via .model_dump(mode="json")), not the
SDK's typed Pydantic objects — keeps main.py, the dashboard's JSON
responses, and the test suite unchanged; the SDK's real, verified field
names flow through as-is either way, since Pydantic's field names already
match the wire JSON.
"""
from typing import Any
from urllib.parse import urljoin

from pydantic import ValidationError as PydanticValidationError

from services.http_client import call_with_backoff, get_client
from services.kalshi import transport
from services.kalshi.provenance import ContractDocs

# self.timeout is intentionally unused below except where noted. The SDK's
# read-endpoint methods (get_markets/get_market/get_market_orderbook/
# get_exchange_status) have explicit, strictly-validated signatures with no
# _request_timeout parameter and Configuration exposes no global timeout
# knob either (verified 2026-08-08 by introspecting the installed package,
# not assumed) - passing one raises a pydantic ValidationError instead of
# being silently accepted. Falls back to the SDK/aiohttp's own default
# timeout for everything here except _get_json's raw-HTTP path.

CONTRACT_DOCS: dict[str, ContractDocs] = {
    "get_markets": ("docs/kalshi/get-markets.md", "docs/kalshi/pagination.md"),
    "get_series_list": ("docs/kalshi/get-series-list.md",),
    "get_market": ("docs/kalshi/get-market.md",),
    "get_markets_by_tickers": ("docs/kalshi/get-markets.md", "docs/kalshi/rate_limits.md"),
    "get_orderbook": ("docs/kalshi/get-market-orderbook.md",),
    "get_event": ("docs/kalshi/get-event.md",),
    "get_events": ("docs/kalshi/get-events.md",),
    "get_milestones_for_event": ("docs/kalshi/get-milestones.md",),
    "get_milestones_bulk": ("docs/kalshi/get-milestones.md",),
    "get_live_data": ("docs/kalshi/get-live-data-with-type.md",),
    "get_live_datas": ("docs/kalshi/get-multiple-live-data.md",),
    "get_candlesticks": ("docs/kalshi/get-market-candlesticks.md",),
    "get_trades": ("docs/kalshi/get-trades.md",),
    "get_exchange_status": ("docs/kalshi/get-exchange-status.md",),
    "get_event_live_data": ("docs/kalshi/get-event-live-data.md",),
    "get_tags_for_series_categories": ("docs/kalshi/get-tags-for-series-categories.md",),
    "get_filters_for_sports": ("docs/kalshi/get-filters-for-sports.md",),
    "get_multivariate_events": ("docs/kalshi/get-multivariate-events.md",),
    "get_multivariate_event_collections": ("docs/kalshi/get-multivariate-event-collections.md",),
    "get_series_fee_changes": ("docs/kalshi/get-series-fee-changes.md",),
    "get_structured_targets": ("docs/kalshi/get-structured-targets.md",),
}


class KalshiPublicGateway:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")
        self._client = transport.build_public_client(base_url)

    async def _get_json(self, path: str, endpoint: str, params: dict | None = None) -> dict:
        # endpoint (2026-08-24, QCP Task 15): every _get_json call site shares
        # this one local "do_get" closure, so call_with_backoff's own
        # __name__-based endpoint-family default would collapse all of them
        # into one misleading "do_get" telemetry bucket - callers pass their
        # own low-cardinality label explicitly instead (see http_client.py's
        # http_metrics_snapshot).
        async def do_get():
            resp = await get_client().get(urljoin(self.base_url + "/", path.lstrip("/")), params=params, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()

        return await call_with_backoff(do_get, endpoint=endpoint)

    async def close(self):
        """main.py constructs a fresh client every poll tick (so a live
        change to kalshi.base_url takes effect immediately) - the SDK client
        wraps its own aiohttp session, so each one needs closing after use
        or the sessions leak over a long-running process."""
        await self._client.close()

    async def get_markets(
        self, limit: int = 20, status: str = "open", mve_filter: str | None = None, series_ticker: str | None = None
    ) -> list[dict]:
        # Optional kwargs (mve_filter, series_ticker) are only passed
        # through when actually set - passing mve_filter explicitly as
        # None (instead of omitting the kwarg) silently changed the result
        # set versus not passing it at all (271 markets with nonzero 24h
        # volume in a 1000-market sample vs. 0 - confirmed directly): the
        # SDK's generated client treats "explicitly None" and "not
        # provided" differently at the wire level. Not verified for
        # series_ticker specifically, but the same omit-when-unset pattern
        # costs nothing and sidesteps the risk.
        kwargs: dict[str, Any] = {"limit": limit, "status": status}
        if mve_filter is not None:
            kwargs["mve_filter"] = mve_filter
        if series_ticker is not None:
            kwargs["series_ticker"] = series_ticker
        resp = await call_with_backoff(self._client.get_markets, **kwargs)
        return [m.model_dump(mode="json") for m in resp.markets]

    async def get_series_list(self, category: str | None = None) -> list[dict]:
        """All of Kalshi's series (~12,500 as of 2026-08-08) with each one's
        own lifetime volume_fp and category - a series is a template for
        recurring events ("Pro Basketball Game", "Bitcoin price up/down"),
        confirmed via the SDK's own docstring.

        Fetched raw (via _get_json), not through the SDK's typed
        get_series_list - found live 2026-08-21: Kalshi now returns
        fee_type: "quadratic_with_combo_maker_fees" on at least one live
        series, which isn't in kalshi_python_async's FeeType enum (checked
        both the installed 3.27.0 and the latest published 3.28.0 - neither
        has it, and it isn't in docs/kalshi/ either, so this is Kalshi's
        live API ahead of its own published docs and SDK, not a stale
        pin). The SDK's Pydantic-validated get_series_list raises on that
        single bad series and fails the ENTIRE ~12,500-series response,
        every call, with no way to skip just the offending item short of
        reaching into SDK internals. Raw JSON has no such enum to
        validate against and needs no SDK version to catch up."""
        data = await self._get_json("/series", endpoint="get_series_list", params={"include_volume": True})
        series = data.get("series", [])
        if category:
            series = [s for s in series if (s.get("category") or "").lower() == category.lower()]
        return series

    async def get_market(self, ticker: str) -> dict:
        resp = await call_with_backoff(self._client.get_market, ticker)
        return resp.market.model_dump(mode="json")

    _MARKETS_BY_TICKERS_BATCH_SIZE = 50  # Kalshi's own real read-burst
    # capacity for this account is 600 tokens (docs/kalshi/rate_limits.md) -
    # a single get_markets request costs 10 tokens/market, no volume
    # discount for batching ("Batch requests are billed per item"), so one
    # oversized request could still exhaust the whole burst pool by itself
    # even though it only drains this app's own local rate limiter once.
    # 50/chunk (500 tokens) leaves real margin under that 600-token ceiling.

    async def get_markets_by_tickers(self, tickers: list[str], batch_size: int | None = None) -> dict[str, dict]:
        """Batched market lookup - one call per up-to-50 tickers instead of
        N individual get_market() calls (docs/kalshi/get-markets.md's
        documented `tickers` filter, confirmed via the installed SDK's own
        get_markets signature). status is deliberately left unset (Kalshi:
        "leave empty to return markets with any status") so already-
        settled/finalized markets are still returned - the exact case
        main._check_signal_resolutions needs (checking whether a market
        has resolved yet), which get_markets' own default status="open"
        would silently exclude. Returns ticker -> market dict; a ticker
        Kalshi doesn't return (renamed/deleted) just isn't in the result,
        same as a failed get_market() being skipped by its own caller."""
        if not tickers:
            return {}
        out: dict[str, dict] = {}
        # batch_size (I8, 2026-08-25): explicit per-request chunk for the
        # rate-limit probe's 50/100/200 measurements - docs/kalshi/
        # get-markets.md documents `tickers` as a comma-separated filter
        # with no per-call cap. Production callers leave it None and keep
        # the conservative default.
        step = int(batch_size) if batch_size else self._MARKETS_BY_TICKERS_BATCH_SIZE
        for i in range(0, len(tickers), step):
            chunk = tickers[i:i + step]
            resp = await call_with_backoff(
                self._client.get_markets, tickers=",".join(chunk), limit=len(chunk),
            )
            for m in resp.markets:
                d = m.model_dump(mode="json")
                if d.get("ticker"):
                    out[d["ticker"]] = d
        return out

    _STRUCTURED_TARGETS_BATCH_SIZE = 2000  # docs/kalshi/get-structured-targets.md's own
    # documented cap on the `ids` filter (`maxItems: 2000`, `style: form, explode: true`
    # -> repeated `?ids=uuid1&ids=uuid2...` query params - confirmed directly against the
    # installed SDK's StructuredTargetsApi._get_structured_targets_serialize, which sets
    # collection_formats={'ids': 'multi'} and appends the raw list as a single query-param
    # tuple, exploded by param_serialize). Deliberately NOT
    # _MARKETS_BY_TICKERS_BATCH_SIZE's 50 above - that number comes from get_markets'
    # own documented 10-tokens-per-market cost against Kalshi's 600-token read-burst
    # ceiling (see that constant's own comment); list-non-default-endpoint-costs.md
    # (GET /account/endpoint_costs) is a live runtime listing, not a static table this
    # doc mirror carries, and it names no non-default cost for structured_targets - so
    # the only real, currently-known ceiling for this endpoint is its own stated ids cap.

    async def get_structured_targets(self, ids: list[str]) -> dict[str, dict]:
        """Batched structured-target lookup - resolves the UUIDs Kalshi puts in a
        `strike_type: "structured"` market's `custom_strike` values to their real
        name/type (targets_and_milestones.md:73-86: "For strike_type: 'structured', the
        value inside custom_strike is a structured target ID. You can resolve it with the
        Get Structured Target endpoint" - this is that lookup's batched form, GET
        /structured_targets with a repeated `ids` filter, instead of N individual
        GET /structured_targets/{id} calls).

        page_size is passed explicitly as len(chunk): get-structured-targets.md documents
        page_size's own default as 100 (max 2000) - same "an unset default silently
        under-returns a bigger request" trap get_markets_by_tickers' own limit=len(chunk)
        already guards against above, just for this endpoint's page_size instead of
        get_markets' limit. Since every chunk is already <= the batch size above (which
        equals page_size's own documented max), one page per chunk is always enough; no
        cursor-following loop is needed the way get_multivariate_event_collections needs
        one for its own open-ended, not-id-filtered listing.

        Returns id -> structured target dict (mirrors get_markets_by_tickers' ticker-keyed
        shape, just keyed by `id` - this endpoint's own response field per StructuredTarget's
        schema, not `ticker`). An id Kalshi doesn't return just isn't in the result, same
        skip-not-crash convention as get_markets_by_tickers/get_events above."""
        if not ids:
            return {}
        out: dict[str, dict] = {}
        for i in range(0, len(ids), self._STRUCTURED_TARGETS_BATCH_SIZE):
            chunk = ids[i:i + self._STRUCTURED_TARGETS_BATCH_SIZE]
            resp = await call_with_backoff(
                self._client.get_structured_targets, ids=chunk, page_size=len(chunk),
            )
            for t in resp.structured_targets:
                d = t.model_dump(mode="json")
                if d.get("id"):
                    out[d["id"]] = d
        return out

    async def get_orderbook(self, ticker: str) -> dict:
        resp = await call_with_backoff(self._client.get_market_orderbook, ticker)
        return resp.model_dump(mode="json")

    async def get_event(self, event_ticker: str) -> dict:
        """The event's own title/subtitle/category — distinct from, and
        often more useful than, any one sibling market's own title (a
        multi-outcome event's individual markets often carry a long
        combo-leg title, not a clean event name). Used to label grouped
        markets in the dashboard (ROADMAP.md Phase 0.5). Superseded as the
        main tick loop's own call site by the batched get_events below
        (2026-08-16 API-doc audit finding B3.1) - kept as a single-item
        method since other call sites (e.g. propagate_milestone_winners's
        related-market mapping) still need a one-off lookup."""
        resp = await call_with_backoff(self._client.get_event, event_ticker)
        return resp.model_dump(mode="json")

    _EVENTS_BATCH_SIZE = 200  # Kalshi's own max limit/page-size for get_events
    # (docs/kalshi/get-events.md) - chunked defensively even though no real
    # call site has ever needed more than a typical watchlist's worth
    # (8-20 events) of not-yet-cached events in one tick.

    async def get_events(self, event_tickers: list[str]) -> list[dict]:
        """Batched form of get_event - one call for a whole list of event
        tickers instead of N individual get_event() calls. Live-verified
        2026-08-15 (docs/kalshi/get-events.md, docs/next-steps-2026-08-15-
        pt3.md finding B3.1): 3 individual calls = 0.36s wall, 1 batched
        call = 0.02s wall, all 3 events returned correctly, no misses.
        Returns the flat Event objects Kalshi's get_events response carries
        directly under "events" - NOT get_event()'s single-item {"event":
        {...}} wrapper shape, so callers read result[i]["event_ticker"]
        rather than result[i]["event"]["event_ticker"]. A ticker Kalshi
        doesn't return (e.g. renamed/removed) simply isn't in the result,
        same as a failed get_event() call being skipped by its own caller."""
        if not event_tickers:
            return []
        events: list[dict] = []
        for i in range(0, len(event_tickers), self._EVENTS_BATCH_SIZE):
            chunk = event_tickers[i:i + self._EVENTS_BATCH_SIZE]
            resp = await call_with_backoff(self._client.get_events, tickers=",".join(chunk), limit=len(chunk))
            events.extend(e.model_dump(mode="json") for e in resp.events)
        return events

    async def get_milestones_for_event(self, event_ticker: str, limit: int = 5) -> list[dict]:
        """The real-world scheduled thing (a specific game/match) tied to an
        event - id/type/start_date, needed to then ask get_live_data(s) for
        the actual live/finished status. Confirmed via the SDK's own
        docstring that related_event_ticker is a real, supported filter,
        not guessed. Kept as the per-event lookup (Kalshi's
        related_event_ticker filter only accepts one value, unlike
        get_events/get_live_datas' list filters - docs/kalshi/
        get-milestones.md, finding B3.3) - get_milestones_bulk below is a
        genuinely different, category-scoped strategy, not a drop-in batch
        replacement for this method."""
        resp = await call_with_backoff(self._client.get_milestones, limit=limit, related_event_ticker=event_ticker)
        return [m.model_dump(mode="json") for m in resp.milestones]

    async def get_milestones_bulk(
        self, category: str, min_updated_ts: int | None = None, limit: int = 500
    ) -> list[dict]:
        """Bulk, category-scoped milestone listing - NOT keyed to any one
        event, unlike get_milestones_for_event above. Live-verified
        2026-08-15 (docs/kalshi/get-milestones.md, finding B3.3): one call
        with category="Sports" and a 6h min_updated_ts watermark returned
        200 milestones covering 1,483 distinct related_event_tickers in a
        single request. Feeds _sync_milestones_bulk's local event_ticker ->
        milestone map in main.py."""
        resp = await call_with_backoff(
            self._client.get_milestones, limit=limit, category=category, min_updated_ts=min_updated_ts,
        )
        return [m.model_dump(mode="json") for m in resp.milestones]

    async def get_live_data(self, milestone_type: str, milestone_id: str) -> dict:
        """The actual live/scheduled/finished status for one milestone.
        details.widget_status is the real, verified signal - confirmed
        directly against a real AFL match at its actual live start time:
        "none" before it starts, "live" while in progress, "finished" once
        over (details.status mirrors this as "scheduled"/"inprogress"/
        "closed"). Not documented anywhere as an enum - caught by actually
        watching a real match go live, not assumed from the field name.

        NOTE (docs/kalshi/CHEATSHEET.md, recorded at A1): this calls the
        endpoint its own doc page flags as legacy ("prefer
        /live_data/milestone/{milestone_id}") - a known, recorded contract
        discrepancy, deliberately not migrated as an incidental refactor."""
        resp = await call_with_backoff(self._client.get_live_data, type=milestone_type, milestone_id=milestone_id)
        return resp.model_dump(mode="json")

    _LIVE_DATAS_BATCH_SIZE = 100  # Kalshi's documented max milestone_ids per
    # get_live_datas call (docs/kalshi/get-multiple-live-data.md).

    async def get_live_datas(self, milestone_ids: list[str]) -> dict[str, dict]:
        """Batched form of get_live_data - one call per up-to-100 milestone
        ids instead of N individual get_live_data() calls. Live-verified
        2026-08-15 (docs/kalshi/get-live-data-with-type.md +
        docs/kalshi/get-multiple-live-data.md, finding B3.2): 3
        individual calls = 0.99s wall, 1 batched call = 0.02s wall. Returns
        milestone_id -> {"type", "details", "milestone_id"} - the batch
        response's own flat per-item shape, NOT get_live_data()'s
        single-call {"live_data": {...}} wrapper, so callers read
        result[milestone_id]["details"] directly rather than
        result[milestone_id]["live_data"]["details"]."""
        if not milestone_ids:
            return {}
        live_datas: dict[str, dict] = {}
        for i in range(0, len(milestone_ids), self._LIVE_DATAS_BATCH_SIZE):
            chunk = milestone_ids[i:i + self._LIVE_DATAS_BATCH_SIZE]
            try:
                resp = await call_with_backoff(self._client.get_live_datas, milestone_ids=chunk)
            except PydanticValidationError:
                # Real, live-confirmed API/SDK mismatch (2026-08-16, surfaced
                # by raising top_series_per_category - more distinct live
                # events per tick means more chunks with nothing live in
                # them): Kalshi returns `"live_datas": null` for a chunk
                # with no live data available, rather than `[]`, but the
                # SDK's GetLiveDatasResponse model declares live_datas as a
                # required list, so parsing the raw response throws inside
                # the SDK before this method ever sees it. Not documented
                # in docs/kalshi/get-multiple-live-data.md's response shape.
                # null and [] mean the same thing here - no live data for
                # this chunk - so this degrades to skipping just this chunk
                # rather than losing every other chunk's real data (and the
                # whole tick's state["error"]) over one malformed one.
                continue
            for ld in resp.live_datas:
                d = ld.model_dump(mode="json")
                if d.get("milestone_id"):
                    live_datas[d["milestone_id"]] = d
        return live_datas

    async def get_candlesticks(
        self, series_ticker: str, ticker: str, start_ts: int, end_ts: int, period_interval: int
    ) -> dict:
        """period_interval is minutes - only 1, 60, or 1440 are valid (verified
        from the SDK's own docstring, not guessed). series_ticker is a
        required, strictly-validated field Kalshi's market objects don't
        carry directly (only event_ticker) - callers get it via get_event()
        first; see main.py's candlesticks endpoint."""
        resp = await call_with_backoff(
            self._client.get_market_candlesticks,
            series_ticker=series_ticker, ticker=ticker,
            start_ts=start_ts, end_ts=end_ts, period_interval=period_interval,
        )
        return resp.model_dump(mode="json")

    async def get_trades(
        self, ticker: str | None = None, limit: int = 25, min_ts: int | None = None, cursor: str | None = None,
        max_ts: int | None = None,
    ) -> dict:
        # min_ts (real, SDK-confirmed param - "filter items after this Unix
        # timestamp") lets a caller fetch every trade since a known
        # watermark instead of just "the most recent N," which used to
        # silently drop real trades on any market busy enough to produce
        # more than N of them within one poll interval. cursor is the real
        # pagination token (empty string on the response = no more pages) -
        # see main.py's _fetch_trade_tape, which pages through every
        # ticker's full result set rather than keeping only the first page.
        # max_ts (I4, 2026-08-25): docs/kalshi/get-trades.md's MaxTsQuery -
        # "Filter items before this Unix timestamp", int64 seconds, the
        # counterpart of min_ts above; accepted by the installed SDK's
        # MarketApi.get_trades (verified by introspection, 3.27.0). Lets a
        # caller bound a reconciliation window on both ends instead of
        # paging forward from min_ts until it runs past the end.
        resp = await call_with_backoff(
            self._client.get_trades, ticker=ticker, limit=limit, min_ts=min_ts, cursor=cursor, max_ts=max_ts,
        )
        return resp.model_dump(mode="json")

    async def get_exchange_status(self) -> dict:
        """Public, unauthenticated. Real shape includes exchange_active/
        trading_active at top level plus a per-shard exchange_index_statuses
        breakdown - lets the dashboard distinguish "the exchange is closed"
        from "the strategy found nothing," which otherwise look identical
        from the outside."""
        resp = await call_with_backoff(self._client.get_exchange_status)
        return resp.model_dump(mode="json")

    async def get_event_live_data(self, event_ticker: str, range_hint: str | None = None) -> dict:
        params = {"range": range_hint} if range_hint else None
        return await self._get_json(f"/live_data/events/{event_ticker}", endpoint="get_event_live_data", params=params)

    async def get_tags_for_series_categories(self) -> dict:
        return await self._get_json("/search/tags_by_categories", endpoint="get_tags_for_series_categories")

    async def get_filters_for_sports(self) -> dict:
        return await self._get_json("/search/filters_by_sport", endpoint="get_filters_for_sports")

    async def get_multivariate_events(
        self, series_ticker: str | None = None, collection_ticker: str | None = None,
        with_nested_markets: bool = False, limit: int = 200, cursor: str | None = None,
    ) -> dict:
        """Dynamically-created multivariate (combo) events - GET
        /events/multivariate (docs/kalshi/get-multivariate-events.md, issue
        #268). Distinct endpoint family from get_events/get_markets: plain
        get_events "excludes multivariate events" (its own doc's exact
        words), and a combo event's own title/sub_title/mutually_exclusive
        is only ever available here, never from get_event(s).

        series_ticker and collection_ticker are mutually exclusive per the
        doc ("Cannot be used together") - not enforced here (Kalshi's own
        400 on misuse is the real contract), same trust-the-server
        convention every other gateway method here already uses.

        with_nested_markets=True asks Kalshi to also embed each event's own
        Market objects under a "markets" key - one call gets both the
        event-level fields (title/sub_title/mutually_exclusive, needed for
        title_cache's event_titles) and the market-level fields (ticker/
        status/close_time/title, needed for market_catalog + market_titles)
        that services/market_watch/mve_scan.py needs, instead of a second
        round trip.

        Returns the raw {"events": [...], "cursor": ...} envelope (not just
        a flat list like get_events/get_markets) - unlike those two, which
        return at most one page each (~100-1000 rows, well within a single
        page), a real MVE series/collection can hold tens of thousands of
        historical events with no documented way to filter by recency or
        status (confirmed live 2026-08-30, see docs/kalshi/CHEATSHEET.md) -
        the caller decides how many pages are worth walking per scan cycle,
        this method stays a thin one-call wrapper like get_markets."""
        kwargs: dict[str, Any] = {"limit": limit, "with_nested_markets": with_nested_markets}
        if series_ticker is not None:
            kwargs["series_ticker"] = series_ticker
        if collection_ticker is not None:
            kwargs["collection_ticker"] = collection_ticker
        if cursor is not None:
            kwargs["cursor"] = cursor
        resp = await call_with_backoff(self._client.get_multivariate_events, **kwargs)
        return {
            "events": [e.model_dump(mode="json") for e in resp.events],
            "cursor": resp.cursor,
        }

    _MVE_COLLECTIONS_PAGE_SIZE = 200  # documented max (docs/kalshi/
    # get-multivariate-event-collections.md's own `limit` schema).
    _MVE_COLLECTIONS_MAX_PAGES = 50  # Defensive bound, same rate-discipline
    # convention as catalog_scan._CATALOG_SCAN_BATCH_SIZE - live-verified
    # 2026-08-30 the real corpus is ~1,389 collections / 7 pages at this
    # page size, so 50 pages (10,000 collections) is a wide, not a tight,
    # margin; exists only so a cursor that never empties (Kalshi bug or a
    # misbehaving test double) can't spin this call forever.

    async def get_multivariate_event_collections(
        self, status: str | None = None, series_ticker: str | None = None,
        associated_event_ticker: str | None = None,
    ) -> list[dict]:
        """Every multivariate event collection matching the given filters -
        GET /multivariate_event_collections (docs/kalshi/
        get-multivariate-event-collections.md, issue #268). A collection is
        the static template a combo is generated FROM (associated_events,
        is_ordered, size_min/size_max) - NOT itself a tradable event/market;
        get_multivariate_events above returns the actual dynamically-created
        instances. Paginates to completion internally (unlike
        get_multivariate_events, which returns one page) - live-verified
        2026-08-30 this corpus is small and stable (~1,389 rows across 16
        distinct series_tickers, a handful of get-series-list.md's ~13,600
        total series), so eagerly walking every page here is the cheap,
        one-time discovery step services/market_watch/mve_scan.py caches
        with a TTL, the same _SERIES_CACHE_TTL_SEC-style pattern
        catalog_scan._get_series_cache already uses for the ~9,400-series
        regular catalog. This is the authoritative way to discover which
        series currently produce MVE events - confirmed live these
        series_tickers do NOT reliably share a naming pattern or category
        (KXCITIESWEATHER appeared as a collection's series_ticker with
        neither "MVE" in its name nor category "Exotics"), so deriving this
        list from get_series_list()'s own ticker/category fields would
        silently miss real cases."""
        collections: list[dict] = []
        cursor: str | None = None
        for _ in range(self._MVE_COLLECTIONS_MAX_PAGES):
            kwargs: dict[str, Any] = {"limit": self._MVE_COLLECTIONS_PAGE_SIZE}
            if status is not None:
                kwargs["status"] = status
            if series_ticker is not None:
                kwargs["series_ticker"] = series_ticker
            if associated_event_ticker is not None:
                kwargs["associated_event_ticker"] = associated_event_ticker
            if cursor:
                kwargs["cursor"] = cursor
            resp = await call_with_backoff(self._client.get_multivariate_event_collections, **kwargs)
            collections.extend(c.model_dump(mode="json") for c in resp.multivariate_contracts)
            cursor = resp.cursor
            if not cursor:
                break
        return collections

    async def get_series_fee_changes(self, show_historical: bool = True) -> list[dict]:
        """Every scheduled series-level fee change (base + overrides), one
        unpaginated call - GET /series/fee_changes (docs/kalshi/
        get-series-fee-changes.md, kalshi-category-data-completeness Task
        2). GetSeriesFeeChangesResponse carries only series_fee_change_arr -
        no limit/cursor field on the response, unlike get_events/
        get_live_datas/get_multivariate_event_collections above, so there is
        nothing to page through. series_ticker (the doc's own optional
        filter) is deliberately never passed - omitting it, per the doc's
        `required: false`, returns the whole array in one shot, which is
        what population needs to backfill every series at once rather than
        one ticker at a time. show_historical defaults True (not the raw
        API's own documented default of False) so the merge below always
        sees every past scheduled change, not just ones still in the
        future - a series whose most recent change already took effect
        needs that past row to resolve its *current* fee, not just an
        upcoming one.

        A series that has never had a scheduled fee change simply does not
        appear in this array at all - confirmed via changelog-index.md's
        2025-09-21 "Scheduled Series Fees API Endpoint" entry ("Get a
        series' fee changes... ALL fee changes previous and upcoming will
        be shown"): this is a log of *changes*, not a full census of every
        series' current fee, so "ticker absent" means "never had a change,
        keep the raw Series.fee_type" rather than a malformed request (Step
        0 of Task 2's kalshi-contract-review, since the schema itself does
        not state this either way).

        Fetched raw (via _get_json), not through the SDK's typed
        get_series_fee_changes - code-review finding, 2026-08-31:
        SeriesFeeChange.fee_type (get-series-fee-changes.md's schema:
        `allOf: - $ref: '#/components/schemas/FeeType'`) uses the exact
        same FeeType enum as get_series_list's Series.fee_type, and
        get_series_list's own docstring above already documents that the
        installed SDK's FeeType enum (3.27.0, and 3.28.0 latest-published)
        is missing quadratic_with_combo_maker_fees even though a real live
        series carries it. A scheduled fee CHANGE of that type would hit
        the identical trap: the SDK's Pydantic-validated
        get_series_fee_changes raises on that single bad entry and fails
        the ENTIRE array, every call - and because this call sits inside
        _get_series_cache() below, that failure would abort the whole
        series-cache refresh (discarding the freshly-fetched series list
        too, not just the fee merge), leaving cache["fetched_at"] stale so
        every subsequent tick retries both REST calls instead of
        respecting the 1-hour TTL - a bigger, silent blast radius than
        just losing this task's own fee data. Raw JSON has no such enum to
        validate against, matching get_series_list's precedent exactly; the
        SDK's real, verified field names (id/series_ticker/fee_type/
        fee_multiplier/scheduled_ts) are also the raw wire JSON's field
        names (same equivalence this module's own top docstring already
        relies on for every model_dump(mode="json") call site), so the raw
        dicts returned here need no reshaping versus the typed path.

        Confirmed via the installed SDK (3.27.0) that
        ExchangeApi.get_series_fee_changes's real param is show_historical
        (not e.g. include_historical) - this repo's own precedent
        (get_series_list's docstring) shows the SDK has previously diverged
        from docs, so guessing the name here would repeat that mistake.
        scheduled_ts comes through as the wire's own ISO-8601 string, not
        an epoch number - the doc types it `format: date-time`
        (get-series-fee-changes.md:126-129), and raw JSON never converts a
        string field to anything else."""
        data = await self._get_json(
            "/series/fee_changes", endpoint="get_series_fee_changes",
            params={"show_historical": show_historical},
        )
        return data.get("series_fee_change_arr", [])
