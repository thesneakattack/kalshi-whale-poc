"""
Market watch - discovery/catalog scanning, live market fetch, and
live-status/milestone/event tracking. Was one 1,337-line file
(market_watch.py, itself the largest single extraction of the prior
7-phase main.py modularization); split internally 2026-08-22
(modularization Phase 9/9) into cohesion-based siblings:

- catalog_scan.py - milestone-based winner propagation, the series cache,
  the background task that incrementally builds market_catalog.
- discovery_cache.py - category metadata, cached pinned-ticker fetches,
  the automatic watchlist discovery pipeline.
- market_fetch.py - _fetch_markets, the per-tick orchestrator that
  assembles the actual watchlist from the pieces above.
- live_status.py - live/scheduled/finished status per event via Kalshi's
  milestone/live-data system, plus exchange status.
- event_metadata.py - event title/metadata caching, the crypto/commodity/
  weather live-data feed.
- selection.py - market-selection policy (candidate filtering, series-
  level round-robin, watchlist caps), moved out of the vendor client at
  Kalshi Integration Phase A Task A7.

Every name external callers need is re-exported here, so
`from services.market_watch import X` is the one import line to use -
internal cross-sibling imports (market_fetch.py depends on
discovery_cache.py and live_status.py) go directly file-to-file. See
CHEATSHEET.md.
"""
from services.market_watch.catalog_scan import (  # noqa: F401
    _CATALOG_SCAN_BATCH_SIZE, _CATALOG_SCAN_MIN_INTERVAL_SEC, _get_series_cache, _get_top_series,
    _maybe_scan_catalog_batch, _MILESTONE_REPOLL_SEC, _scan_catalog_batch, _scan_catalog_batch_background,
    _SERIES_CACHE_TTL_SEC, propagate_milestone_winners,
)
from services.market_watch.discovery_cache import (  # noqa: F401
    _cached_market_fetch, _DISCOVERY_REFRESH_SEC, _DISCOVERY_TERMINAL_STATUSES, _fetch_category_metadata,
    _maybe_refresh_discovery_cache, _PINNED_MARKET_REFRESH_SEC, _refresh_discovery_cache,
    _refresh_discovery_cache_background,
)
from services.market_watch.event_metadata import (  # noqa: F401
    _EVENT_LIVE_DATA_EXCLUDED_CATEGORIES, _EVENT_LIVE_DATA_REPOLL_SEC, _fetch_event_live_data,
    _fetch_event_titles,
)
from services.market_watch.live_status import (  # noqa: F401
    _fetch_exchange_status, _fetch_live_status, _LIVE_STATUS_LOOKAHEAD_SEC, _LIVE_STATUS_LOOKBACK_SEC,
    _LIVE_STATUS_MAX_POLL_PER_TICK, _LIVE_STATUS_REPOLL_SEC, _LIVE_STATUS_TERMINAL,
)
from services.market_watch.market_fetch import _fetch_markets, _MARKET_FIELDS, _slim_market  # noqa: F401
from services.market_watch import selection  # noqa: F401
