# Market events module — cheat sheet

Owns: `event_lifecycle.py` (pre-tail/mid-series/post-tail/no_occurrence
activity-phase classification — structural, via `mutually_exclusive` +
sibling count, not a sport-specific guess), `event_schedule.py` (resolves
an event's real-world start time via a 4-source waterfall — `strike_date`,
Kalshi milestone `start_date`, rules-text regex, web search — for cases
where Kalshi's own open/close/occurrence timestamps don't reflect it),
`event_inspector.py` (a standalone, ad-hoc diagnostic script — no other
module imports it; run manually inside the container, see its own
docstring). Split out of flat `services/*.py` 2026-08-22 (modularization
Phase 7/9) — 7 combined real importers updated (`main.py`, `app_state.py`,
`services/whale_stream/decision_bridge.py`, `services/market_watch/
market_watch.py`, plus tests). No `routes.py` — none of the three back a
dedicated API route; consumed internally, same shape as
`services/whale_stream/` (also route-less).

## Relevant Kalshi API docs

- `docs/kalshi/market_lifecycle.md` — the `market_lifecycle_v2` WebSocket
  channel and status state machine `event_lifecycle.py`'s `classify_phase`
  is built against (a market past its own `close_time` is always
  `post_tail`, mirroring that channel's `determined`/`finalized`
  transition).
- `docs/kalshi/get-event.md` — `strike_date`, `event_schedule.py`'s
  cheapest/first-tried source (already sitting in `state["event_titles"]`
  from `main.py`'s own `get_event()` call, zero extra network cost).
- Kalshi's milestone API (`services/kalshi_client.py`'s
  `get_milestones_for_event`) — `event_schedule.py`'s second source,
  `start_date`/`end_date`, covers `Sports, Elections, Esports, Crypto`
  milestone categories (confirmed live per the module's own docstring;
  `"Politics"` is not a real category value).

## Audit finding (already resolved, cited here rather than re-derived):
the web-search fallback is still the right call, not a stopgap that
should've been replaced by now

Checked `docs/kalshi/get-event-metadata.md` while writing this cheat
sheet, expecting to find a possible newer/better source for
`event_schedule.py`'s 4th-tier web-search fallback. That page already
carries its own dated finding (2026-08-15 audit, not new): `GET
/events/{event_ticker}/metadata` returns **"no schedule/start-time data of
any kind"** — pure visual/structural metadata (image URLs, settlement
sources, `competition`/`competition_scope`) — and explicitly concludes
"this endpoint isn't a 5th source worth adding." `event_schedule.py`'s own
4-source waterfall (`strike_date` → milestone → rules-text → web search)
remains the right approach; the web-search leg is a deliberate last
resort for real gaps in Kalshi's own data (mostly multi-day tournaments
whose winner-market never states a date anywhere), not an unfinished
integration. Re-confirmed still accurate as of this pass, not stale.

## Handoff — who calls this module, who it calls

- **`event_lifecycle.classify_phase`/`phase_ranked`**: called from
  `services/market_watch/market_watch.py`'s discovery-cache ranking (a
  mid-series candidate outranks a similar-volume pre-tail/post-tail one,
  per `docs/hardening-and-accuracy-roadmap-2026-08-11.md` Part 1) and from
  `services/whale_stream/decision_bridge.py`'s `_handle_signal` (a second,
  schedule-based `is_live` signal alongside the milestone-based one, for
  markets — multi-day tournaments — Kalshi's milestone API doesn't track
  at all).
- **`event_schedule`**: `load_all()` hydrates `state["event_schedules"]`
  at `app_state.py` import time. `resolve_one`/`needs_resolution` — true as
  of 2026-08-24, previously false on this page — are now called from
  `main.py`'s trading loop via `_maybe_resolve_event_schedules(cfg)`
  (wired in alongside `_maybe_scan_catalog_batch`/`_maybe_run_backup`), a
  background batch resolver scoped to long-window events
  (`_events_needing_resolution`, `_LONG_WINDOW_THRESHOLD_SEC`) and capped
  at `config/settings.yaml`'s `event_schedule.max_resolutions_per_tick`;
  each result is applied to `state["event_schedules"]` in place and
  persisted via `save()`. Its real consumer is `services/market_lookup.py`'s
  `effective_close_time()` (tier 2 of that resolver's precedence — see that
  module's docstring), not a direct `main.py` call site. **Deliberately
  still NOT wired**, a separate and intentionally out-of-scope gap:
  `trade_window_is_open` has no caller anywhere in the app — the
  is_live/trade-window gates it was designed for don't consult it yet.
- **`event_inspector.inspect_market_event`**: no programmatic caller —
  invoked manually via a `python -c` one-liner inside the `fastapi`
  container (see the module's own docstring) for ad-hoc debugging of one
  market's event/series/mutual-exclusivity context. Already imports
  `services.market_catalog.market_catalog` (Phase 6's new path) and
  `services.title_cache`.
