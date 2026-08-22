"""
Thin wrapper around Kalshi's public (unauthenticated) market-data endpoints,
backed by Kalshi's official kalshi_python_async SDK (migrated 2026-08-08 —
see ROADMAP.md/status.html for why: a hand-rolled auth bug had gone
undetected until a real key made it testable, and the SDK removes
request-signing/schema drift as a bug class going forward). Read-only:
listing markets, fetching a market's current prices/volume, its order book,
and exchange status. No API key is required for any of this.

If you later add real order execution, that's a *separate*, authenticated
client (RSA-PSS signed requests, still via the SDK) — deliberately not part
of this file so "read market data" and "place real orders" can never be
accidentally mixed; see services/kalshi_account_client.py.

Every method here returns plain dicts (via .model_dump(mode="json")), not
the SDK's typed Pydantic objects — keeps main.py, the dashboard's JSON
responses, and the test suite unchanged; the SDK's real, verified field
names flow through as-is either way, since Pydantic's field names already
match the wire JSON.
"""
import asyncio
from urllib.parse import urljoin

import kalshi_python_async as kpa
from pydantic import ValidationError as PydanticValidationError

from services.http_client import call_with_backoff, get_client
from services.signal_log import series_of

# self.timeout is intentionally unused below except where noted. The SDK's
# read-endpoint methods (get_markets/get_market/get_market_orderbook/
# get_exchange_status) have explicit, strictly-validated signatures with no
# _request_timeout parameter and Configuration exposes no global timeout
# knob either (verified 2026-08-08 by introspecting the installed package,
# not assumed) - passing one raises a pydantic ValidationError instead of
# being silently accepted. Of the two order-write methods (in
# kalshi_account_client.py), only create_order_v2 (a **kwargs signature)
# accepts it; cancel_order_v2 has the same strict signature as the read
# methods here and rejects it too. Falls back to the SDK/aiohttp's own
# default timeout for everything here.


class KalshiClient:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")
        config = kpa.Configuration(host=base_url.rstrip("/"))
        self._client = kpa.KalshiClient(config)

    async def _get_json(self, path: str, params: dict | None = None) -> dict:
        async def do_get():
            resp = await get_client().get(urljoin(self.base_url + "/", path.lstrip("/")), params=params, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()

        return await call_with_backoff(do_get)

    async def close(self):
        """main.py constructs a fresh KalshiClient every poll tick (so a live
        change to kalshi.base_url takes effect immediately, same as before
        this migration) - the SDK client wraps its own aiohttp session, so
        each one needs closing after use or the sessions leak over a
        long-running process."""
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
        kwargs = {"limit": limit, "status": status}
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
        confirmed via the SDK's own docstring. This is the real fix for
        market discovery: browsing individual markets directly (even a
        full 1000-market page, even paging through 50,000+) can be
        entirely combo markets at zero volume, confirmed repeatedly -
        Kalshi's combo/MVE markets are generated in bulk and vastly
        outnumber real ones in that flat ordering. Series-level volume
        doesn't have that problem and is one call, not thousands - then
        get_markets(series_ticker=...) against just the real, currently
        active series returns clean single-outcome markets directly
        (verified: KXMLBGAME, KXBTCD, KXATPMATCH all returned real,
        well-titled, actively-trading markets this way).

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
        data = await self._get_json("/series", params={"include_volume": True})
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

    async def get_markets_by_tickers(self, tickers: list[str]) -> dict[str, dict]:
        """Batched market lookup - one call per up-to-50 tickers instead of
        N individual get_market() calls (docs/kalshi/get-markets.md's
        documented `tickers` filter, confirmed via the installed SDK's own
        get_markets signature - not previously wired into this wrapper at
        all). status is deliberately left unset (Kalshi: "leave empty to
        return markets with any status") so already-settled/finalized
        markets are still returned - the exact case
        main._check_signal_resolutions needs (checking whether a market
        has resolved yet), which get_markets' own default status="open"
        would silently exclude. Returns ticker -> market dict; a ticker
        Kalshi doesn't return (renamed/deleted) just isn't in the result,
        same as a failed get_market() being skipped by its own caller."""
        if not tickers:
            return {}
        out: dict[str, dict] = {}
        for i in range(0, len(tickers), self._MARKETS_BY_TICKERS_BATCH_SIZE):
            chunk = tickers[i:i + self._MARKETS_BY_TICKERS_BATCH_SIZE]
            resp = await call_with_backoff(
                self._client.get_markets, tickers=",".join(chunk), limit=len(chunk),
            )
            for m in resp.markets:
                d = m.model_dump(mode="json")
                if d.get("ticker"):
                    out[d["ticker"]] = d
        return out

    async def get_orderbook(self, ticker: str) -> dict:
        resp = await call_with_backoff(self._client.get_market_orderbook, ticker)
        return resp.model_dump(mode="json")

    _CANDIDATE_FETCH_CONCURRENCY = 10  # 2026-08-15 direct incident: an unbounded
    # asyncio.gather here (one real request per series, all fired at once) hit
    # Kalshi's real rate limiter hard enough to push tick duration past 15-20s
    # and rack up hundreds of 429s per tick once top_series_per_category grew
    # past a small handful - call_with_backoff retries each 429 individually
    # but does nothing to bound how many requests go out simultaneously in the
    # first place. A semaphore-bounded batch lets the *candidate pool* grow
    # (more category coverage) without growing the actual concurrent burst
    # Kalshi sees - a reasoned starting value, not a documented Kalshi limit
    # (their own rate-limit docs specify backoff behavior, not a number).

    async def get_candidate_markets(self, min_volume: float, series_tickers: list[str]) -> list[dict]:
        """Given a list of already-known-active series (see get_series_list
        and main.py's cached _get_top_series - deliberately not fetched in
        here, since the series list is ~12,500 entries and expensive enough
        (~1s) to need caching across poll ticks, which belongs in main.py's
        persistent state, not a client that's reconstructed fresh every
        tick), fetch each series' open markets concurrently, volume-filter,
        and sort by 24h volume descending - the full candidate pool, no
        cutoff. Split out from what used to be get_top_volume_markets's own
        first half specifically so a caller needing to filter the pool
        further before final selection (main.py's live-markets-only
        discovery needs live status checked across the whole candidate pool,
        not just whatever round-robin would have already cut it down to -
        see round_robin_select below) can do so on real candidates, not an
        already-truncated top n.

        This replaced an earlier approach (browse individual markets
        directly, sorted/filtered after the fact) that turned out
        fundamentally unreliable: Kalshi auto-generates a huge number of
        "MVE" (combo) markets, and confirmed directly - repeatedly, with
        real numbers - a flat browse of even 50,000+ markets can still
        return zero with any real volume, because combos vastly outnumber
        real markets in that ordering. Querying by series sidesteps the
        problem entirely rather than trying to filter around it: real
        series (KXMLBGAME, KXBTCD, KXATPMATCH, ...) reliably return clean,
        real, well-titled markets when queried directly - verified, not
        assumed."""
        if not series_tickers:
            return []
        semaphore = asyncio.Semaphore(self._CANDIDATE_FETCH_CONCURRENCY)

        async def _bounded_fetch(ticker: str):
            async with semaphore:
                return await self.get_markets(limit=100, status="open", series_ticker=ticker)

        results = await asyncio.gather(
            *(_bounded_fetch(t) for t in series_tickers),
            return_exceptions=True,
        )
        markets = []
        for r in results:
            if isinstance(r, list):
                markets.extend(r)
        markets = [m for m in markets if float(m.get("volume_24h_fp") or 0) >= min_volume]
        markets.sort(key=lambda m: float(m.get("volume_24h_fp") or 0), reverse=True)
        return markets

    @staticmethod
    def round_robin_select(markets: list[dict], n: int, max_children_per_parent: int | None = None) -> list[dict]:
        """Selects up to n *parent series* (e.g. "KXPGAH2H" - see
        services/signal_log.series_of, the same ticker-prefix definition
        used everywhere else in this app rather than a second one that
        could drift), then includes every child market of each selected
        series - every event/pairing under it, every ticker on each - highest-
        volume first, capped at max_children_per_parent if set (None =
        unlimited, direct choice: "i want the ability to cap but for now i
        want every child").

        Direct, explicit instruction settled this after two earlier
        attempts: grouping at event_ticker crowded the whole watchlist with
        one tournament's individual pairings (confirmed live: 42 of 47 real
        slots were one PGA tournament's head-to-head matchups, each its own
        event_ticker); grouping at event_ticker with series-level round-
        robin fairness matched Kalshi's own documented hierarchy (Category >
        Series > Event > Market, no tournament level - confirmed against
        Kalshi's API docs) but still let two *different*, concurrently-live
        matches sharing one series (two separate Dota2 games, both under
        "KXDOTA2MAP") each count separately against the watchlist size -
        confirmed live, and rejected: "i dont want those pairings to count
        against the watchlist count, only the parent series." Series-level
        grouping is what's shipped: a whole series, however many concurrent
        events it happens to have live right now, costs exactly one slot -
        the explicit, known tradeoff being that two unrelated same-series
        matches are watched together as one "parent" rather than counted as
        two, which is what the direct instruction above asked for.

        n means distinct *series*, not individual markets - a single
        selected series can contribute many more than 1 market to the
        result if it has many events/children, which is the explicit point.
        Series are ranked by their own best (highest-volume) child -
        markets is already volume-sorted on input (see
        get_candidate_markets), so a series's first child is its best one.
        Never padded - fewer than n series (or fewer children than
        max_children_per_parent) if the real candidate pool doesn't have
        that many, same "never fabricate to hit a number" idiom as
        everywhere else in this app."""
        groups: dict[str, list[dict]] = {}
        parent_order: list[str] = []
        for m in markets:
            ticker = m.get("ticker") or ""
            key = series_of(ticker) if ticker else (m.get("event_ticker") or ticker)
            if key not in groups:
                groups[key] = []
                parent_order.append(key)
            groups[key].append(m)

        selected: list[dict] = []
        for key in parent_order[:n]:
            children = groups[key]
            if max_children_per_parent is not None:
                children = children[:max_children_per_parent]
            selected.extend(children)
        return selected

    async def get_top_volume_markets(
        self, n: int, min_volume: float, series_tickers: list[str], max_children_per_parent: int | None = None,
    ) -> list[dict]:
        """Fetch the full candidate pool then round-robin-select up to n
        parent markets (see round_robin_select for what "parent" means and
        why) - see get_candidate_markets and round_robin_select for what
        each half actually does and why each is its own piece now."""
        candidates = await self.get_candidate_markets(min_volume, series_tickers)
        return self.round_robin_select(candidates, n, max_children_per_parent)

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
        events = []
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
        milestone map in main.py, which _fetch_live_status and
        propagate_milestone_winners now consult before ever falling back to
        their own per-event get_milestones_for_event call - see that
        function's docstring for the full architecture and why this is an
        additive fast path, not a hard replacement."""
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
        Superseded as the main tick loop's own call site by the batched
        get_live_datas below (2026-08-16 audit finding B3.2) - kept for any
        one-off single-milestone lookup."""
        resp = await call_with_backoff(self._client.get_live_data, type=milestone_type, milestone_id=milestone_id)
        return resp.model_dump(mode="json")

    _LIVE_DATAS_BATCH_SIZE = 100  # Kalshi's documented max milestone_ids per
    # get_live_datas call (docs/kalshi/get-live-data.md).

    async def get_live_datas(self, milestone_ids: list[str]) -> dict[str, dict]:
        """Batched form of get_live_data - one call per up-to-100 milestone
        ids instead of N individual get_live_data() calls. Live-verified
        2026-08-15 (docs/kalshi/get-live-data.md, finding B3.2): 3
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
                # in docs/kalshi/get-live-data.md's response shape. null
                # and [] mean the same thing here - no live data for this
                # chunk - so this degrades to skipping just this chunk
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
    ) -> dict:
        # min_ts (real, SDK-confirmed param - "filter items after this Unix
        # timestamp") lets a caller fetch every trade since a known
        # watermark instead of just "the most recent N," which used to
        # silently drop real trades on any market busy enough to produce
        # more than N of them within one poll interval. cursor is the real
        # pagination token (empty string on the response = no more pages) -
        # see main.py's _fetch_trade_tape, which pages through every
        # ticker's full result set rather than keeping only the first page.
        resp = await call_with_backoff(
            self._client.get_trades, ticker=ticker, limit=limit, min_ts=min_ts, cursor=cursor,
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
        return await self._get_json(f"/live_data/events/{event_ticker}", params=params)

    async def get_tags_for_series_categories(self) -> dict:
        return await self._get_json("/search/tags_by_categories")

    async def get_filters_for_sports(self) -> dict:
        return await self._get_json("/search/filters_by_sport")
