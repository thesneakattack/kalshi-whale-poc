# Market watch module — cheat sheet

Decides which markets/events `trading_loop` sees each tick (pinned
watchlist + volume-ranked discovery + background catalog scan), fetches
their live prices/exchange/milestone status, and tracks per-event
live-data for milestone-based outcome resolution. The largest single
extraction of the prior main.py modularization pass (Phase 7, ~1,300
lines in one file) and, per that plan's own investigation, the tick-
duration bottleneck — `tick_phase_timings.market_fetch` is consistently
the dominant phase (see `GET /api/health/pipeline`).

**Split internally 2026-08-22 (modularization Phase 9/9)** — that one
1,337-line file was itself the largest file in the whole `services/` tree,
so it got the same "modularize the modularized" treatment this session
already gave `services/analytics/`. Cohesion-based siblings, no behavior
change (verified byte-for-byte, same as Phase 7's original extraction):
- `catalog_scan.py` — milestone-based winner propagation
  (`propagate_milestone_winners`), the series cache
  (`_get_series_cache`/`_get_top_series`), and the background task that
  incrementally builds `market_catalog` (`_scan_catalog_batch` +
  `_maybe_scan_catalog_batch`/`_scan_catalog_batch_background`).
- `discovery_cache.py` — category metadata (`_fetch_category_metadata`),
  cached pinned-ticker fetches (`_cached_market_fetch`), and the automatic
  watchlist discovery pipeline (`_refresh_discovery_cache` +
  `_maybe_refresh_discovery_cache`/`_refresh_discovery_cache_background`).
- `market_fetch.py` — `_fetch_markets`, the per-tick orchestrator that
  assembles the actual watchlist from the pieces above (pinned + discovery
  + `extra_tickers`, deduped, series-grouped, live-price overlaid). The
  one file with real cross-sibling imports (`discovery_cache.py`,
  `live_status.py`), since it's the top-level thing the other pieces feed
  into — everything else in this split is a leaf.
- `live_status.py` — `_fetch_live_status` (the milestone/live-data
  system), `_fetch_exchange_status`.
- `event_metadata.py` — `_fetch_event_titles`, `_fetch_event_live_data`
  (the crypto/commodity/weather feed, documented NOT to serve Sports).

`__init__.py` re-exports every name external callers need, so
`from services.market_watch import X` stays the one import line to use
(`main.py`'s own import line is otherwise unchanged from before this
split — only the module path dropped `.market_watch`).

No routes of its own — every function here is called either from
`trading_loop` (which stays in `main.py`) or from the one other route that
reaches into it directly, `services/market_catalog/routes.py`'s
`/api/markets/search`.

## Relevant Kalshi API docs

- `docs/kalshi/get-markets.md` / `get-series.md` / `get-series-list.md` —
  the discovery/catalog surface: `catalog_scan._get_series_cache`/
  `_get_top_series` volume-rank series via `get_series_list`;
  `market_fetch._fetch_markets`/`discovery_cache._cached_market_fetch`/
  `catalog_scan._scan_catalog_batch` pull markets via `get_markets`/
  `get_markets_by_tickers`.
- `docs/kalshi/get-exchange-status.md` — `live_status._fetch_exchange_status`.
- `docs/kalshi/get-events.md` — `event_metadata._fetch_event_titles`.
- `docs/kalshi/get-live-data.md` / `get-event-live-data.md` — the
  milestone/live-data system: `catalog_scan.propagate_milestone_winners`
  (`get_milestones_for_event` + `get_live_datas`) and
  `event_metadata._fetch_event_live_data` (`get_event_live_data`)
  respectively — two independent per-event REST loops, both repoll-cached
  (see `_MILESTONE_REPOLL_SEC`/`_EVENT_LIVE_DATA_REPOLL_SEC`) after being
  found live as the bulk of an earlier ~27s tick_duration plateau.
  `live_status._fetch_live_status` also calls `get_milestones_for_event` +
  `get_live_datas` (the sports live-status path), independent of the
  winner-propagation one in `catalog_scan.py`.
- `docs/kalshi/get-tags-for-series-categories.md` /
  `get-live-data.md`'s sports filters endpoint —
  `discovery_cache._fetch_category_metadata` calls
  `get_tags_for_series_categories`/`get_filters_for_sports` to build the
  sport→competition map `trade_category.py`'s whale-confidence subcategory
  tier reads (see `docs/kalshi/CHEATSHEET.md`'s "What sport/league does an
  event belong to?" entry — this is the *correct*, documented answer that
  entry's own mistake was found against).
- `docs/kalshi/market_lifecycle.md` — referenced in
  `discovery_cache._maybe_refresh_discovery_cache`'s docstring re:
  `close_date_updated`/status transitions the discovery cache has to
  tolerate; the live *subscription* to this channel lives in
  `services/whale_stream/whale_stream_handlers.py`, not here (this module
  is REST-only).

## Handoff — who calls this module, who it calls

- **Upstream:** `trading_loop`'s `market_fetch`/`resolve_and_record` phase
  bodies (`main.py`) call `_fetch_markets` (`market_fetch.py`),
  `_fetch_exchange_status` (`live_status.py`), `propagate_milestone_winners`
  (`catalog_scan.py`); `_fetch_live_status` (`live_status.py`)/
  `_fetch_event_titles`/`_fetch_event_live_data` (`event_metadata.py`) are
  gathered together in the `event_and_tradetape_fetch` phase. All imported
  via the package's own `__init__.py` re-exports (`from
  services.market_watch import ...`), not per-file paths.
  `services/market_catalog/routes.py`'s `/api/markets/search` calls
  `_get_series_cache` (`catalog_scan.py`)/`_fetch_live_status`
  (`live_status.py`)/`_slim_market` (`market_fetch.py`) directly for
  on-demand browse/search, independent of the tick loop.
- **Internal (within this package):** `market_fetch._fetch_markets` is the
  one real cross-sibling dependency — it calls
  `discovery_cache._cached_market_fetch`/`_maybe_refresh_discovery_cache`
  and `live_status._fetch_live_status`/`_LIVE_STATUS_LOOKAHEAD_SEC`/
  `_LIVE_STATUS_LOOKBACK_SEC` directly (not through `__init__.py`, to avoid
  any risk of an import cycle through the package root). Every other
  sibling file is a leaf with no imports from another sibling.
- **Downstream:**
  - `services.app_state` — `state` (six keys: `series_cache`,
    `catalog_scan`, `discovery_cache`, `market_object_cache`,
    `live_status_cache`, `milestone_cache`, plus reads/writes of
    `latest_prices`/`latest_asks`/`event_titles`/`live_game_state`/
    `event_live_data_cache`/`category_metadata`) and `bump_generation()`.
  - `services.market_lookup._sport_for_event` — the one cross-module
    helper dependency found only once the block was actually extracted
    from `main.py` (Phase 7) — used when recording live game state
    (`live_status.py`/`event_metadata.py`) so `trade_category.py`'s
    subcategory tier gets a sport, not just a raw competition string.
  - `market_catalog` (background near-term catalog: `next_series_to_scan`,
    `upsert_markets`, `candidates_in_window`, `open_candidates`,
    `open_markets_for_series` — `catalog_scan.py`/`discovery_cache.py`/
    `market_fetch.py`), `series_cache` (persists the volume-ranked series
    list — `catalog_scan.py`), `series_evaluator.ineligible_series`
    (whale-worthiness gating — `discovery_cache.py`/`market_fetch.py`),
    `event_lifecycle.phase_ranked` (regime-based series preference —
    `discovery_cache.py`), `signal_log.series_of` (ticker→series lookup —
    `market_fetch.py`), `game_state.record` (sports live-state capture —
    `live_status.py`/`event_metadata.py`), `market_history.record_outcome`
    (settlement from a resolved milestone winner — `catalog_scan.py`) —
    all already-clean, already-flat service modules, imported directly.
- **Not a dependency, despite being adjacent in the tick:**
  `account_positions._fetch_account_snapshot` runs concurrently with
  `_fetch_markets` (same `asyncio.gather` in `trading_loop`) but neither
  module calls into the other — confirmed via a full AST free-variable scan
  of the extracted block before writing this file (Phase 7), re-confirmed
  by re-reading the whole file before this internal split (Phase 9).

## Where the tick-duration finding from this session lives now

`tick_phase_timings.market_fetch` (`GET /api/health/pipeline`) is the
dominant phase-timing contributor, per the modularization plan's own
"which future perf fix becomes tractable where" table. This module is
where that fix would plug in — nothing about tick performance was changed
in this extraction itself (pure move, verified byte-for-byte via a full
`pytest` pass plus a live tick-timing sample before/after, both
statistically unchanged).
