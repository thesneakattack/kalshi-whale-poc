# Market watch module — cheat sheet

Owns: `market_watch.py` — decides which markets/events `trading_loop` sees
each tick (pinned watchlist + volume-ranked discovery + background catalog
scan), fetches their live prices/exchange/milestone status, and tracks
per-event live-data for milestone-based outcome resolution. The largest
single extraction of the main.py modularization pass (Phase 7, ~1,300
lines) and, per that plan's own investigation, the current tick-duration
bottleneck — `tick_phase_timings.market_fetch` is consistently the
dominant phase (see `GET /api/health/pipeline`).

No routes of its own — every function here is called either from
`trading_loop` (which stays in `main.py`) or from the one other route that
reaches into it directly, `/api/markets/search`.

## Relevant Kalshi API docs

- `docs/kalshi/get-markets.md` / `get-series.md` / `get-series-list.md` —
  the discovery/catalog surface: `_get_series_cache`/`_get_top_series`
  volume-rank series via `get_series_list`, `_fetch_markets`/
  `_cached_market_fetch`/`_scan_catalog_batch` pull markets via
  `get_markets`/`get_markets_by_tickers`.
- `docs/kalshi/get-exchange-status.md` — `_fetch_exchange_status`.
- `docs/kalshi/get-events.md` — `_fetch_event_titles`.
- `docs/kalshi/get-live-data.md` / `get-event-live-data.md` — the
  milestone/live-data system: `propagate_milestone_winners`
  (`get_milestones_for_event` + `get_live_datas`) and
  `_fetch_event_live_data` (`get_event_live_data`) respectively — two
  independent per-event REST loops, both repoll-cached (see
  `_MILESTONE_REPOLL_SEC`/`_EVENT_LIVE_DATA_REPOLL_SEC`) after being found
  live as the bulk of an earlier ~27s tick_duration plateau.
- `docs/kalshi/get-tags-for-series-categories.md` /
  `get-live-data.md`'s sports filters endpoint — `_fetch_category_metadata`
  calls `get_tags_for_series_categories`/`get_filters_for_sports` to build
  the sport→competition map `trade_category.py`'s whale-confidence
  subcategory tier reads (see `docs/kalshi/CHEATSHEET.md`'s "What
  sport/league does an event belong to?" entry — this is the *correct*,
  documented answer that entry's own mistake was found against).
- `docs/kalshi/market_lifecycle.md` — referenced in `_maybe_refresh_
  discovery_cache`'s docstring re: `close_date_updated`/status transitions
  the discovery cache has to tolerate; the live *subscription* to this
  channel lives in `services/whale_stream/whale_stream_handlers.py`, not
  here (this module is REST-only).

## Handoff — who calls this module, who it calls

- **Upstream:** `trading_loop`'s `market_fetch`/`resolve_and_record` phase
  bodies (`main.py`) call `_fetch_markets`, `_fetch_exchange_status`,
  `propagate_milestone_winners`; `_fetch_live_status`/`_fetch_event_titles`/
  `_fetch_event_live_data` are gathered together in the
  `event_and_tradetape_fetch` phase. `/api/markets/search` calls
  `_get_series_cache`/`_fetch_live_status`/`_slim_market` directly for
  on-demand browse/search, independent of the tick loop.
- **Downstream:**
  - `services.app_state` — `state` (six keys: `series_cache`,
    `catalog_scan`, `discovery_cache`, `market_object_cache`,
    `live_status_cache`, `milestone_cache`, plus reads/writes of
    `latest_prices`/`latest_asks`/`event_titles`/`live_game_state`/
    `event_live_data_cache`/`category_metadata`) and `bump_generation()`.
  - `services.market_lookup._sport_for_event` — the one cross-module
    helper dependency found only once the block was actually extracted
    (the modularization plan flagged this as worth re-checking, and it
    was right to) — used when recording live game state so
    `trade_category.py`'s subcategory tier gets a sport, not just a raw
    competition string.
  - `market_catalog` (background near-term catalog: `next_series_to_scan`,
    `record_batch`, `candidates_in_window`, `scan_progress`),
    `series_cache` (persists the volume-ranked series list),
    `series_evaluator.ineligible_series` (whale-worthiness gating on which
    series discovery will select), `event_lifecycle.phase_ranked` (regime-
    based series preference), `signal_log.series_of` (ticker→series
    lookup for per-series selection/logging), `game_state.record`
    (sports live-state capture keyed by milestone data), `market_history.
    record_outcome` (settlement from a resolved milestone winner) — all
    already-clean, already-flat service modules, imported directly.
- **Not a dependency, despite being adjacent in the tick:**
  `account_positions._fetch_account_snapshot` runs concurrently with
  `_fetch_markets` (same `asyncio.gather` in `trading_loop`) but neither
  module calls into the other — confirmed via a full AST free-variable scan
  of the extracted block before writing this file, not assumed from the
  plan doc's pre-extraction guess.

## Where the tick-duration finding from this session lives now

`tick_phase_timings.market_fetch` (`GET /api/health/pipeline`) is the
dominant phase-timing contributor, per the modularization plan's own
"which future perf fix becomes tractable where" table. This module is
where that fix would plug in — nothing about tick performance was changed
in this extraction itself (pure move, verified byte-for-byte via a full
`pytest` pass plus a live tick-timing sample before/after, both
statistically unchanged).
