"""
Multivariate (combo) event discovery - GET /events/multivariate and GET
/multivariate_event_collections (docs/kalshi/get-multivariate-events.md,
docs/kalshi/get-multivariate-event-collections.md; issue #268).

Why a sibling module, not an extension of catalog_scan.py's existing
per-series scan: catalog_scan._get_series_cache filters get_series_list()
down to series with volume_fp > 0 before anything else runs - live-verified
2026-08-30 that EVERY multivariate-producing series (all 16 sampled,
KXMVECROSSCATEGORY-SHARD1 included) reports volume_fp=0.00 on its own
/series entry even while its dynamically-created markets carry real
trading activity (Kalshi tracks volume per generated market/event, not on
the parent series). That single filter - correct for the ~9,400 regular
series it's built for - silently excludes every MVE series regardless of
kalshi.categories, which is the exact mechanism behind issue #268's
finding: KXMVECROSSCATEGORY held 13,841 of 95,535 logged signals (14.5%)
with zero rows in market_catalog.db, and the 2026-08-30 kalshi.categories
widening (a different, already-applied mechanism - docs/open-decisions.md)
could not have fixed this no matter which categories it added, because the
series never reaches _get_top_series's category bucket at all.

Two further, confirmed-live shape differences from a regular per-series
scan (not assumed from the doc pages' prose alone):

- occurrence_datetime is always null on a multivariate market (2,000+ real
  KXMVECROSSCATEGORY-SHARD1 markets sampled via GET /events/multivariate,
  zero exceptions) - a combo has no single "occurrence" moment by
  construction, since its legs can span unrelated events/times.
  market_catalog.upsert_markets' occurrence_ts-required skip would
  silently drop every MVE row, reproducing this exact gap - see
  market_catalog.upsert_mve_markets, which anchors the same near-term-
  horizon bound on close_ts instead (the field that IS always populated).
- get_series_list()'s own ticker naming/category fields are NOT a
  reliable way to discover which series currently produce MVE events:
  KXCITIESWEATHER appeared as a real collection's series_ticker with
  neither "MVE" in its name nor category "Exotics" (confirmed live
  2026-08-30). get_multivariate_event_collections is the authoritative,
  small (~1,389 rows / 16 distinct series_tickers, confirmed live), TTL-
  cacheable discovery source instead.

Rate/batch discipline (catalog_scan.py's own established concern - see its
module comments on the 2026-08-15 tick_duration incident from unbounded
per-tick scanning): the collections list is fetched at most once per
_MVE_COLLECTIONS_CACHE_TTL_SEC (mirrors catalog_scan._SERIES_CACHE_TTL_SEC's
reasoning - collections rarely change). The events scan itself issues
exactly one bounded get_multivariate_events call per known MVE series per
cycle (one page, limit=_MVE_EVENTS_PAGE_LIMIT, no unbounded cursor
pagination), paced through the same PACE_LIMIT-style semaphore pattern
catalog_scan._paced_get_markets already established, so this never bursts
more than _MVE_SCAN_PACE_LIMIT concurrent requests into the shared token
bucket. With ~16 known MVE series today, that's a modest, bounded addition
(comparable to catalog_scan's own _CATALOG_SCAN_BATCH_SIZE=10 per-tick
regular batch), run on its own _MVE_SCAN_MIN_INTERVAL_SEC cadence -
independent of kalshi.categories, since MVE isn't a category-scoped
concept in the same sense a regular series is.

Known limitation, recorded rather than silently worked around (docs/kalshi/
CHEATSHEET.md has the dated entry): /events/multivariate has no
documented status or recency filter at all (only limit/cursor/
series_ticker/collection_ticker/with_nested_markets), and pagination order
is not simply chronological or status-correlated - a base (unsharded)
series ticker can be entirely historical/finalized while a `-SHARDN`
sibling under the same collection is overwhelmingly active. One page per
series per cycle, resampled every cycle, was confirmed live to already
surface the large majority of currently-active combos for the
currently-relevant shard (1,818 of 2,000 sampled markets were "active" on
page 1 of KXMVECROSSCATEGORY-SHARD1) - good enough to close the 14.5%
"zero catalog rows" gap without guessing at a deeper pagination strategy
the endpoint gives no evidence it needs yet. If quality_audit or
/api/quality/summary later show continued signal-without-catalog-row gaps
specifically on an MVE series, that would be the measured trigger to widen
page coverage - not a guess made now.
"""
import asyncio
import time

from services import http_client, task_supervisor, title_cache
from services.app_state import state
from services.kalshi.public import KalshiPublicGateway
from services.market_catalog import market_catalog

_MVE_COLLECTIONS_CACHE_TTL_SEC = 3600  # Collections are the static combo
# templates (associated_events, is_ordered, size_min/size_max) - they
# change far less often than the events/markets dynamically created from
# them. Same TTL and "don't re-fetch until stale" reasoning as
# catalog_scan._SERIES_CACHE_TTL_SEC.


async def _get_mve_series_cache(client: KalshiPublicGateway) -> list[str]:
    """Distinct series_tickers across every known multivariate event
    collection (any status - a collection that's stopped accepting NEW
    combos can still have existing markets open until their own
    close_time, so status='open' alone would risk under-covering). Cached
    in state["mve_series_cache"], refreshed at most once per
    _MVE_COLLECTIONS_CACHE_TTL_SEC - mirrors catalog_scan._get_series_cache
    exactly."""
    cache = state["mve_series_cache"]
    if time.time() - cache["fetched_at"] > _MVE_COLLECTIONS_CACHE_TTL_SEC or not cache["series_tickers"]:
        collections = await client.get_multivariate_event_collections()
        series_tickers = sorted({c["series_ticker"] for c in collections if c.get("series_ticker")})
        cache["series_tickers"] = series_tickers
        cache["fetched_at"] = time.time()
    return cache["series_tickers"]


_MVE_EVENTS_PAGE_LIMIT = 200  # Documented max (docs/kalshi/
# get-multivariate-events.md's own `limit` schema) - one page per series
# per cycle, see this module's own docstring for why that's a reasoned,
# not-yet-measured-insufficient bound.

_MVE_SCAN_PACE_LIMIT = 4  # Same bounded-concurrency reasoning as
# catalog_scan.PACE_LIMIT - independent of how many MVE series exist, caps
# how many of this batch's own calls run concurrently against the shared
# token bucket (services/http_client.py).


def _event_title_fields(event: dict) -> dict:
    """event_titles-shaped dict from one raw multivariate EventData -
    deliberately the same field set services/market_watch/event_metadata.
    py's _fetch_event_titles already extracts from plain get_event(s),
    since strategy_engine.evaluate() and every other event_titles consumer
    read state["event_titles"]/title_cache.event_titles the same way
    regardless of which source populated a given entry. This is the ONLY
    place a combo event's own title/sub_title/mutually_exclusive can come
    from - get_events' own doc says "excludes multivariate events," so
    _fetch_event_titles never sees these regardless of what's on the
    watchlist.

    competition/competition_scope are always None here (product_metadata
    is null on every real multivariate event sampled live 2026-08-30,
    unlike a regular event) - not a guess, a confirmed-absent field for
    this shape, same "legitimately absent, not a backfill signal" case
    event_metadata.py's own docstring already documents for non-competitor
    regular events."""
    product_metadata = event.get("product_metadata") or {}
    return {
        "title": event.get("title") or event.get("event_ticker"),
        "sub_title": event.get("sub_title"),
        "category": event.get("category"),
        "series_ticker": event.get("series_ticker"),
        "available_on_brokers": event.get("available_on_brokers"),
        "collateral_return_type": event.get("collateral_return_type"),
        "mutually_exclusive": event.get("mutually_exclusive"),
        "competition": product_metadata.get("competition"),
        "competition_scope": product_metadata.get("competition_scope"),
        "product_metadata": product_metadata,
        "settlement_sources": event.get("settlement_sources") or [],
        "strike_date": event.get("strike_date"),
        "strike_period": event.get("strike_period"),
        "fee_type_override": event.get("fee_type_override"),
        "fee_multiplier_override": event.get("fee_multiplier_override"),
        "last_updated_ts": event.get("last_updated_ts"),
    }


def _market_rows_from_event(event: dict) -> list[dict]:
    """This event's own nested markets (present only when
    with_nested_markets=True was passed, and only once Kalshi has actually
    created a market under this event - confirmed live 2026-08-30 that a
    meaningful fraction of multivariate events carry an empty markets list).
    Each row is tagged with the PARENT event's series_ticker/category,
    since the Market schema itself carries neither (confirmed against the
    real schema in docs/kalshi/get-multivariate-events.md and a live
    response) - unlike a regular per-series scan batch, where one
    series_ticker/category already applies to every market in the batch."""
    rows = []
    for m in event.get("markets") or []:
        row = dict(m)
        row["series_ticker"] = event.get("series_ticker")
        row["category"] = event.get("category")
        rows.append(row)
    return rows


async def _fetch_one_series(client: KalshiPublicGateway, pace_sem: asyncio.Semaphore, series_ticker: str) -> dict:
    async with pace_sem:
        return await client.get_multivariate_events(
            series_ticker=series_ticker, with_nested_markets=True, limit=_MVE_EVENTS_PAGE_LIMIT,
        )


async def _scan_mve_batch(client: KalshiPublicGateway, cfg: dict) -> None:
    """Incrementally refreshes market_catalog.db + title_cache's
    market_titles/event_titles for every known MVE series - see this
    module's own docstring for the full "why" (root cause, rate
    discipline, known limitation). Independent of cfg["kalshi"]
    ["categories"] by design: MVE isn't a category-scoped concept the way
    a regular series is (cfg is still threaded through, matching every
    other _scan_*_batch signature in this package, in case a future
    MVE-specific config knob is added - none exists yet)."""
    series_tickers = await _get_mve_series_cache(client)
    if not series_tickers:
        return
    pace_sem = asyncio.Semaphore(_MVE_SCAN_PACE_LIMIT)
    results = await asyncio.gather(
        *(_fetch_one_series(client, pace_sem, s) for s in series_tickers),
        return_exceptions=True,
    )

    now = time.time()
    new_event_titles: dict[str, dict] = {}
    new_market_titles: dict[str, dict] = {}
    market_rows: list[dict] = []
    for series_ticker, result in zip(series_tickers, results):
        if not isinstance(result, dict):
            # Same "don't mark scanned, don't lose the series silently"
            # posture as catalog_scan._scan_catalog_batch's own failure
            # handling - no logging framework exists yet in this app
            # (that finding, not new here), so stdout via `ddev logs -s
            # fastapi` is the visibility path.
            print(f"[mve_scan] scan failed for {series_ticker!r}, will retry next cycle: {result!r}")
            continue
        for event in result.get("events") or []:
            event_ticker = event.get("event_ticker")
            if not event_ticker:
                continue
            new_event_titles[event_ticker] = _event_title_fields(event)
            for row in _market_rows_from_event(event):
                ticker = row.get("ticker")
                if not ticker:
                    continue
                market_rows.append(row)
                title_fields = title_cache.market_title_fields(row)
                new_market_titles[ticker] = {**title_fields, "event_ticker": row.get("event_ticker")}

    # Same wiring as main.py's own tick loop (state update + persist,
    # event_titles before market_titles) for regular events/markets -
    # strategy_engine.evaluate() reads state["event_titles"]/
    # state["market_titles"] directly (the full accumulated caches, not a
    # watchlist-scoped subset), so an in-memory update here is visible to
    # the very next signal evaluated, with no restart required; the
    # title_cache.db write is what survives a restart/reload.
    if new_event_titles:
        state["event_titles"].update(new_event_titles)
        title_cache.save_event_titles(new_event_titles)
    if new_market_titles:
        state["market_titles"].update(new_market_titles)
        title_cache.save_market_titles(new_market_titles)
    if market_rows:
        market_catalog.upsert_mve_markets(market_rows, updated_at=now)


_MVE_SCAN_MIN_INTERVAL_SEC = 60  # How often a new background batch may be
# KICKED OFF - the shared token-bucket rate limiter is what actually keeps
# the real aggregate call rate safe (same relationship catalog_scan.
# _CATALOG_SCAN_MIN_INTERVAL_SEC's own comment documents). Longer than
# catalog_scan's 15s: the known MVE series list is small (~16, confirmed
# live 2026-08-30) and every one of them is scanned every cycle (no
# least-recently-scanned rotation needed, unlike the ~9,400-series regular
# catalog) - 60s keeps aggregate call volume comparable to the existing
# batch while still refreshing well inside a typical in-game combo-trading
# window.


def _maybe_scan_mve_batch(cfg: dict) -> None:
    """Triggers _scan_mve_batch as an independent background task on its
    own steady interval - mirrors catalog_scan._maybe_scan_catalog_batch
    exactly (same overlap guard shape, same task_supervisor wiring)."""
    mve_state = state["mve_scan"]
    now_ts = time.time()
    due = now_ts - mve_state["last_started_at"] > _MVE_SCAN_MIN_INTERVAL_SEC
    if due and not mve_state["scanning"]:
        mve_state["scanning"] = True
        mve_state["last_started_at"] = now_ts
        mve_state["task"] = task_supervisor.supervise(
            lambda: _scan_mve_batch_background(cfg),
            component="mve_scan", operation="scan_batch",
        )


@http_client.classify("background_catalog")
async def _scan_mve_batch_background(cfg: dict) -> None:
    """Owns its own KalshiPublicGateway, same reasoning as
    catalog_scan._scan_catalog_batch_background (the calling tick's own
    client closes at the end of that same tick)."""
    mve_state = state["mve_scan"]
    client = KalshiPublicGateway(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])
    try:
        await _scan_mve_batch(client, cfg)
    finally:
        mve_state["scanning"] = False
        await client.close()
