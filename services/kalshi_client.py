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

import kalshi_python_async as kpa

from services.http_client import call_with_backoff

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
        config = kpa.Configuration(host=base_url.rstrip("/"))
        self._client = kpa.KalshiClient(config)

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
        well-titled, actively-trading markets this way)."""
        resp = await call_with_backoff(self._client.get_series_list, include_volume=True)
        series = [s.model_dump(mode="json") for s in resp.series]
        if category:
            series = [s for s in series if (s.get("category") or "").lower() == category.lower()]
        return series

    async def get_market(self, ticker: str) -> dict:
        resp = await call_with_backoff(self._client.get_market, ticker)
        return resp.market.model_dump(mode="json")

    async def get_orderbook(self, ticker: str) -> dict:
        resp = await call_with_backoff(self._client.get_market_orderbook, ticker)
        return resp.model_dump(mode="json")

    async def get_top_volume_markets(self, n: int, min_volume: float, series_tickers: list[str]) -> list[dict]:
        """Given a list of already-known-active series (see get_series_list
        and main.py's cached _get_top_series - deliberately not fetched in
        here, since the series list is ~12,500 entries and expensive enough
        (~1s) to need caching across poll ticks, which belongs in main.py's
        persistent state, not a client that's reconstructed fresh every
        tick), fetch each series' open markets concurrently and return the
        top n by 24h volume.

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
        assumed.

        Selection is round-robin across distinct events, not a flat top-n-
        by-volume sort - confirmed live as a real gap, not hypothetical: a
        single high-volume multi-outcome event (an 8-market golf tournament)
        can have every one of its own sub-markets individually rank in the
        global top N, silently monopolizing the entire watchlist and
        crowding out every other series even when dozens of other markets
        are trading, some of them actually live, right now. A flat per-event
        cap (tried first, replaced here) fixes that but creates the mirror
        problem - it can needlessly truncate a genuinely multi-outcome
        event's sub-markets even when nothing else is competing for the
        slots. Round-robin self-sizes instead: within each event, markets
        are still taken highest-volume-first, but one from every event
        before a second one from any - so the effective "per-event share"
        naturally shrinks as more distinct events compete for the same n
        slots, and naturally grows toward n when few or one event
        dominates the real candidate pool, without a hardcoded number
        tuned for one scenario at the expense of the other."""
        if not series_tickers:
            return []
        results = await asyncio.gather(
            *(self.get_markets(limit=100, status="open", series_ticker=t) for t in series_tickers),
            return_exceptions=True,
        )
        markets = []
        for r in results:
            if isinstance(r, list):
                markets.extend(r)
        markets = [m for m in markets if float(m.get("volume_24h_fp") or 0) >= min_volume]
        markets.sort(key=lambda m: float(m.get("volume_24h_fp") or 0), reverse=True)

        groups: dict[str, list[dict]] = {}
        event_order: list[str] = []
        for m in markets:
            key = m.get("event_ticker") or m.get("ticker")
            if key not in groups:
                groups[key] = []
                event_order.append(key)
            groups[key].append(m)

        selected = []
        round_idx = 0
        while len(selected) < n:
            took_any = False
            for key in event_order:
                group = groups[key]
                if round_idx < len(group):
                    selected.append(group[round_idx])
                    took_any = True
                    if len(selected) >= n:
                        break
            if not took_any:
                break  # every event's markets exhausted before filling n
            round_idx += 1
        return selected

    async def get_event(self, event_ticker: str) -> dict:
        """The event's own title/subtitle/category — distinct from, and
        often more useful than, any one sibling market's own title (a
        multi-outcome event's individual markets often carry a long
        combo-leg title, not a clean event name). Used to label grouped
        markets in the dashboard (ROADMAP.md Phase 0.5)."""
        resp = await call_with_backoff(self._client.get_event, event_ticker)
        return resp.model_dump(mode="json")

    async def get_milestones_for_event(self, event_ticker: str, limit: int = 5) -> list[dict]:
        """The real-world scheduled thing (a specific game/match) tied to an
        event - id/type/start_date, needed to then ask get_live_data for the
        actual live/finished status. Confirmed via the SDK's own docstring
        that related_event_ticker is a real, supported filter, not guessed."""
        resp = await call_with_backoff(self._client.get_milestones, limit=limit, related_event_ticker=event_ticker)
        return [m.model_dump(mode="json") for m in resp.milestones]

    async def get_live_data(self, milestone_type: str, milestone_id: str) -> dict:
        """The actual live/scheduled/finished status for one milestone.
        details.widget_status is the real, verified signal - confirmed
        directly against a real AFL match at its actual live start time:
        "none" before it starts, "live" while in progress, "finished" once
        over (details.status mirrors this as "scheduled"/"inprogress"/
        "closed"). Not documented anywhere as an enum - caught by actually
        watching a real match go live, not assumed from the field name."""
        resp = await call_with_backoff(self._client.get_live_data, type=milestone_type, milestone_id=milestone_id)
        return resp.model_dump(mode="json")

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

    async def get_trades(self, ticker: str | None = None, limit: int = 25) -> dict:
        resp = await call_with_backoff(self._client.get_trades, ticker=ticker, limit=limit)
        return resp.model_dump(mode="json")

    async def get_exchange_status(self) -> dict:
        """Public, unauthenticated. Real shape includes exchange_active/
        trading_active at top level plus a per-shard exchange_index_statuses
        breakdown - lets the dashboard distinguish "the exchange is closed"
        from "the strategy found nothing," which otherwise look identical
        from the outside."""
        resp = await call_with_backoff(self._client.get_exchange_status)
        return resp.model_dump(mode="json")
