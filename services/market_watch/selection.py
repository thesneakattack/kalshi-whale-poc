"""Market-selection policy — which series/markets are strategically
interesting (Kalshi Integration Phase A Task A7).

Moved verbatim out of services/kalshi_client.py: candidate filtering/
ranking, series-level round-robin parent selection, and the watchlist-cap
composition are *application* policy, not vendor semantics — the design
spec's placement rule: "The adapter may batch/fetch efficiently, but it
should not decide which markets are strategically interesting." The
vendor side (efficient batched fetching) stays on
services/kalshi/public.py's KalshiPublicGateway; these functions take that
gateway (or any object with its get_markets surface) as a parameter and
compose policy on top.
"""
import asyncio

from services.signal_log import series_of

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


async def candidate_markets(client, min_volume: float, series_tickers: list[str]) -> list[dict]:
    """Given a list of already-known-active series (see the gateway's
    get_series_list and main.py's cached _get_top_series - deliberately not
    fetched in here, since the series list is ~12,500 entries and expensive
    enough (~1s) to need caching across poll ticks, which belongs in
    main.py's persistent state, not a client that's reconstructed fresh
    every tick), fetch each series' open markets concurrently, volume-
    filter, and sort by 24h volume descending - the full candidate pool, no
    cutoff. Split from top_volume_markets' first half specifically so a
    caller needing to filter the pool further before final selection
    (main.py's live-markets-only discovery needs live status checked across
    the whole candidate pool, not just whatever round-robin would have
    already cut it down to - see round_robin_select below) can do so on
    real candidates, not an already-truncated top n.

    This replaced an earlier approach (browse individual markets directly,
    sorted/filtered after the fact) that turned out fundamentally
    unreliable: Kalshi auto-generates a huge number of "MVE" (combo)
    markets, and confirmed directly - repeatedly, with real numbers - a
    flat browse of even 50,000+ markets can still return zero with any
    real volume, because combos vastly outnumber real markets in that
    ordering. Querying by series sidesteps the problem entirely rather
    than trying to filter around it: real series (KXMLBGAME, KXBTCD,
    KXATPMATCH, ...) reliably return clean, real, well-titled markets when
    queried directly - verified, not assumed."""
    if not series_tickers:
        return []
    semaphore = asyncio.Semaphore(_CANDIDATE_FETCH_CONCURRENCY)

    async def _bounded_fetch(ticker: str):
        async with semaphore:
            return await client.get_markets(limit=100, status="open", series_ticker=ticker)

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
    markets is already volume-sorted on input (see candidate_markets),
    so a series's first child is its best one. Never padded - fewer than
    n series (or fewer children than max_children_per_parent) if the
    real candidate pool doesn't have that many, same "never fabricate to
    hit a number" idiom as everywhere else in this app."""
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


async def top_volume_markets(
    client, n: int, min_volume: float, series_tickers: list[str], max_children_per_parent: int | None = None,
) -> list[dict]:
    """Fetch the full candidate pool then round-robin-select up to n
    parent markets (see round_robin_select for what "parent" means and
    why) - see candidate_markets and round_robin_select for what each
    half actually does and why each is its own piece."""
    candidates = await candidate_markets(client, min_volume, series_tickers)
    return round_robin_select(candidates, n, max_children_per_parent)
