"""Utilities to inspect a market's event/series context and live/settlement state.

Primary entrypoint:
    async def inspect_market_event(client: KalshiClient, ticker: str) -> dict

Returns a dict with keys: market, event, milestones, live_status (widget_status/ source),
siblings (other markets sharing the same event_ticker), mutually_exclusive (from get_event/title_cache),
and a short diagnosis of whether this looks like a mutually-exclusive event whose siblings
should be treated as complementary (one winner) vs independent props.

Usage (inside project, ddev fastapi container):
    python - <<'PY'
    import asyncio
    from services.kalshi_client import KalshiClient
    from services import event_inspector

    async def main():
        client = KalshiClient('https://api.kalshi.com')
        report = await event_inspector.inspect_market_event(client, 'KXMLBGAME-26AUG111905SEANYY-NYY')
        import json; print(json.dumps(report, indent=2))
        await client.close()
    asyncio.run(main())
    PY

"""
from __future__ import annotations

import asyncio
import math
from typing import Dict, Any, List

from services.kalshi_client import KalshiClient
from services import title_cache
from services.market_catalog import market_catalog


async def inspect_market_event(client: KalshiClient, ticker: str) -> Dict[str, Any]:
    """Fetch market, its event, milestones/live-data, and sibling markets.

    The returned dict is intended for human inspection and for programmatic
    gating logic (e.g. treat mutually-exclusive events differently). Fields
    are tolerant of missing API pieces and will be None or empty when absent.
    """
    report: Dict[str, Any] = {"ticker": ticker, "market": None, "event": None, "milestones": [],
                              "live_status": None, "siblings": [], "mutually_exclusive": None,
                              "diagnosis": None}

    try:
        market = await client.get_market(ticker)
    except Exception as e:  # pragma: no cover - network/runtime-bound
        report["diagnosis"] = f"failed to fetch market: {e}"
        return report

    if not isinstance(market, dict):
        report["diagnosis"] = "market fetch returned non-dict"
        return report

    report["market"] = market
    event_ticker = market.get("event_ticker")
    series_ticker = market.get("series_ticker")

    # fetch event metadata (mutually_exclusive flag lives here)
    if event_ticker:
        try:
            ev = await client.get_event(event_ticker)
            report["event"] = ev.get("event") if isinstance(ev, dict) else None
            # persist into title cache as main.py does elsewhere so callers can read it
            if isinstance(ev, dict) and ev.get("event"):
                title_cache.save_event_titles({event_ticker: {
                    "title": ev["event"].get("title"),
                    "sub_title": ev["event"].get("sub_title"),
                    "category": ev["event"].get("category"),
                    "mutually_exclusive": ev["event"].get("mutually_exclusive"),
                    "competition": (ev["event"].get("product_metadata") or {}).get("competition"),
                    "competition_scope": (ev["event"].get("product_metadata") or {}).get("competition_scope"),
                }})
                report["mutually_exclusive"] = ev["event"].get("mutually_exclusive")
        except Exception:
            pass

    # milestones + live-data (widget_status) for the event, if any
    if event_ticker:
        try:
            ms_list = await client.get_milestones_for_event(event_ticker, limit=5)
            report["milestones"] = ms_list or []
            if ms_list:
                # poll the first milestone's live-data (what _fetch_live_status does)
                ms = ms_list[0]
                if ms.get("id") and ms.get("type"):
                    ld = await client.get_live_data(ms["type"], ms["id"])
                    details = (ld.get("live_data") or {}).get("details") if isinstance(ld, dict) else None
                    if details and details.get("widget_status"):
                        report["live_status"] = {"widget_status": details.get("widget_status"), "details": details}
        except Exception:
            pass

    # siblings: other markets under the same event (via series fetch then filter)
    siblings: List[Dict[str, Any]] = []
    if series_ticker:
        try:
            # fetch a reasonably-large page for the series (the SDK supports up to 100)
            markets = await client.get_markets(limit=100, status="open", series_ticker=series_ticker)
            for m in markets:
                if m.get("event_ticker") == event_ticker:
                    siblings.append(m)
        except Exception:
            pass

    # If series-level fetch found none, a milestone may list related_event_tickers
    if not siblings and report.get("milestones"):
        ms0 = report["milestones"][0]
        related = ms0.get("related_event_tickers") if isinstance(ms0, dict) else None
        if related:
            # represent as simple dicts with ticker only; callers can fetch full market data if needed
            siblings = [{"ticker": t} for t in related if t != event_ticker]
    report["siblings"] = siblings

    # quick heuristics: do siblings look like a complementary 2-outcome pair?
    if siblings:
        yes_prices = [float((s.get("yes_bid_dollars") or 0)) for s in siblings]
        # If two siblings and prices sum to ~1.0, it's likely a two-outcome mutual pair
        if len(yes_prices) == 2 and math.isclose(sum(yes_prices), 1.0, rel_tol=1e-2, abs_tol=0.02):
            report["diagnosis"] = "likely 2-outcome mutually-exclusive (prices sum ~1.0)"
            if report["mutually_exclusive"] is None:
                report["mutually_exclusive"] = True
        elif report["mutually_exclusive"]:
            report["diagnosis"] = "event marked mutually_exclusive by Kalshi"
        else:
            report["diagnosis"] = "no strong mutual-exclusivity signal"

    return report
