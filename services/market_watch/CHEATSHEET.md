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
  (`get_events(..., with_milestones=True)` as of Task 10 below,
  previously `get_milestones_for_event` + `get_live_datas`) and
  `event_metadata._fetch_event_live_data` (`get_event_live_data`)
  respectively — two independent per-event-discovery loops, both repoll-
  cached (see `_MILESTONE_REPOLL_SEC`/`_EVENT_LIVE_DATA_REPOLL_SEC`) after
  being found live as the bulk of an earlier ~27s tick_duration plateau.
  `live_status._fetch_live_status` also calls `get_live_datas` (the sports
  live-status path), independent of the winner-propagation one in
  `catalog_scan.py`. **Its milestone-discovery call is conditional as of
  2026-08-30, not unconditional as this line used to say, and is
  `get_events(needs_fetch, with_milestones=True)` as of Task 10 below (was
  `get_milestones_for_event`):** it first checks
  `state["milestone_by_event"]` (`milestone_scan.py`, below) and only
  falls back to the batched REST call for events that broad cache hasn't
  covered.
- `docs/kalshi/get-milestones.md` — `milestone_scan._scan_milestone_batch`
  (`get_milestones_bulk`, category-scoped and batched) and
  `services/market_events/event_inspector.py`/`event_schedule.py`'s
  per-event `get_milestones_for_event`, all the same `GET /milestones`
  endpoint with different filters. `catalog_scan`/`live_status` used to
  call `get_milestones_for_event` too but no longer do (Task 10 below,
  `docs/kalshi/get-events.md`'s `with_milestones` param instead). See the
  `min_updated_ts` entry in `docs/kalshi/CHEATSHEET.md` before passing a
  watermark — it must be an integer.
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

## Age-aware live-price overlay (P7 Task 29, redesigned 2026-08-27)

`market_fetch.overlay_live_prices(markets, state, now)` - extracted from
`_fetch_markets`'s tail, now a pure, tested helper (`tests/test_market_fetch_
overlay.py`). The 2026-08-15 overlay kept the in-memory `state["latest_prices"]`/
`latest_asks` value **unconditionally** whenever the ticker was already in the
dict. Consequence, found while re-grounding the task against HEAD (and
contradicting the earlier H12 write-up, corrected in the known-findings doc): once
a ticker entered the dict, REST never refreshed it - a WS-quiet ticker's price was
copied forward every tick, forever. P8 Task 34's first live sample measured the
population directly: 4 of 10 open positions had received no ticker message in the
process's lifetime. `latest_asks` was worse - no WS writer existed at all, so
asks were frozen at their REST seed.

Now: every writer stamps `state["latest_prices_updated_at"]`/`latest_asks_updated_
at`; a value older than `_PINNED_MARKET_REFRESH_SEC` (300s - the system's own
existing bound on REST-row staleness, reused, not a new constant) or with no stamp
at all (unknown age is not trusted) loses to the REST row and is restamped. WS
stays primary while flowing; a quiet ticker is refreshed from REST every ~300s
instead of never. A REST row with no price of its own cannot win (a stale-but-real
value beats a fabricated default). `_process_stream_ticker` now also writes
`latest_asks` from the message's `yes_ask_dollars`, stamped, leaving a message with
no ask untouched so `check_pending_fills`' "absent means no fresh ask" contract
holds. `/api/health/pipeline` exposes `price_staleness` (per-open-position stamp
ages) - live on shipping: 9/9 positions stamped, oldest 19.5s, 0 over 300s, where
before this the never-WS-seen positions had no bound at all.

## Multivariate (combo) event discovery (mve_scan.py, issue #268, 2026-08-30)

New sibling module, not an extension of `catalog_scan.py`'s regular
per-series scan: `catalog_scan._get_series_cache` filters `get_series_list()`
to `volume_fp > 0` before any category logic runs, and every real MVE
series (`KXMVECROSSCATEGORY`/`-SHARD1` and 14 others, confirmed live)
reports `volume_fp: "0.00"` on its own `/series` entry regardless of real
trading activity on its dynamically-created markets - that single filter
silently excluded every MVE series from the regular scan no matter which
`kalshi.categories` were configured. Full root-cause detail, live-verified
endpoint shape, and the base-ticker-vs-`-SHARDn` gotcha:
`docs/kalshi/CHEATSHEET.md`'s "How do you actually discover multivariate
(combo) markets" entry (2026-08-30). Writes to `market_catalog.db` via
`market_catalog.upsert_mve_markets` (a `close_ts`-anchored sibling of
`upsert_markets`, since MVE markets never carry `occurrence_datetime`) and
to `title_cache`'s `market_titles`/`event_titles` the same way
`event_metadata._fetch_event_titles` does for regular events - runs on its
own scheduler trigger (`main._SCHEDULER_TRIGGERS`'s `mve_scan` entry),
independent of `catalog_scan`.

## Broad milestone discovery (milestone_scan.py, 2026-08-30, entry-gate-me-pairing-and-netting-remediation Part 3)

Second new sibling module of the same shape as `mve_scan.py` above — its
own `main._SCHEDULER_TRIGGERS` entry (`milestone_scan`), its own
`_maybe_scan_milestone_batch` due-interval + overlap guard, its own
`KalshiPublicGateway` inside `_scan_milestone_batch_background`, its own
`/api/health/pipeline` `schedulers.milestone_scan` row. Interval:
`_MILESTONE_SCAN_MIN_INTERVAL_SEC` (300s).

What it does: one `get_milestones_bulk(category, min_updated_ts=...)` call
per configured `kalshi.categories` entry — **the first and only caller of
that gateway method, which had been implemented and tested with zero
callers anywhere in the app** — and maps every `related_event_ticker` of
every returned milestone into `state["milestone_by_event"]`
(`event_ticker -> milestone_id`, last-write-wins, memory-only). One
category's call is worth ~1,483 distinct event tickers (live-verified
2026-08-15, recorded in `public.get_milestones_bulk`'s own docstring), so
this is a batched, watchlist-independent alternative to N per-event
`get_milestones_for_event` calls.

Two things worth knowing before touching it:

- **Watermarks are per category** (`state["milestone_scan"]["watermarks"]`,
  `category -> int Unix seconds`), advanced only by that category's own
  successful call. A single shared scalar (the first version) advanced
  even on a cycle where every call failed, which would have permanently
  skipped whatever changed while a category was down. Absent key = never
  successfully scanned = ask for everything.
- **This does NOT broaden live-status coverage** — `_fetch_live_status`
  still chooses which events to poll from its own watchlist-scoped
  `markets` argument and only consults this cache for events it already
  chose, so `game_state` sees the same event set as before; the measured
  effect is fewer redundant per-event REST calls. Real broadening is an
  open follow-up (`docs/open-decisions.md`, 2026-08-30). Also deliberately
  NOT rewired: `catalog_scan.propagate_milestone_winners`, which shares the
  identical narrow pattern but feeds settlement-outcome data that real
  trading decisions consume.

## `esports_match` live-data shape, confirmed against real payloads (2026-08-31, Task 7 of kalshi-category-data-completeness)

`milestone_live_data.py`'s `_esports_match` extractor was verified, not
guessed, against four real captured lifecycles in `data/game_state.db`'s
`game_states` table (CS2/Dota2/LoL/Valorant tickers, each spanning
pregame → live → finished) — the table this app's own
`live_status.py:254` already populates on every live-data poll, so the
data pre-existed and needed no new watch. Confirmed shape, for this
milestone `type` specifically:

- `widget_status` is a real, reliable 3-state field: `"created"` (pregame,
  scores both 0) → `"live"` → `"complete"` (finished). `is_live` is
  perfectly redundant with it in every captured row (`true` iff
  `widget_status == "live"`) and strictly less informative (can't tell
  "not started" from "finished" the way the 3-state field can).
- `winner` never appears — confirmed absent on every captured payload,
  matching the census (`docs/superpowers/research/2026-08-30-kalshi-
  category-data-shape-audit.md` S4/D2: `esports_match` is 9,385/10,706 of
  the exchange-wide no-`winner` figure).
- **No team-identity field of any kind appears in this payload**, checked
  including `home_stats`/`away_stats` (numeric per-map stat blocks —
  kills, gold, a per-map 0/1 `winner` flag keyed by side, never by name).
  `home_score`/`away_score` (final best-of-N map tally) tell you which
  *side* won once finished, but not a name. `docs/kalshi/
  targets_and_milestones.md:28` explains why no better answer exists in
  scope: a resolvable `home_team_id`/`away_team_id` lives on the
  **milestone** object's own separate `details` field, never on the
  **live-data** `details` this module receives (`ld.get("details")` from
  `get_live_datas`/`get-multiple-live-data.md` — a different object
  entirely, despite the same field name).
- Consequence: `catalog_scan.py`'s only consumer of `winner`
  (`catalog_scan.py:129`, matched as a name substring against a related
  market's `custom_strike`/`yes_sub_title`/`no_sub_title`, `catalog_scan.py:
  154-167`) cannot be fed a usable value for this type from live-data
  alone — a `"home"`/`"away"` side label would silently never match
  anything there, which is worse than the honest `None` this extractor
  returns. `esports_match` therefore ships with the same shape as
  `golf_tournament`: real `status` pass-through, permanent `winner: None`.
  Resolving `esports_match` winners for real would need the milestone
  object's `details`/structured-target IDs threaded into a *different*
  code path than this one — out of this task's scope (its brief forbade a
  signature change), and not attempted here.

## `political_race` live-data status vocabulary, confirmed against real payloads (2026-08-31, Task 8 of kalshi-category-data-completeness)

`milestone_live_data.py`'s `_political_race` extractor was verified
against a real **live** pull, not this app's own captured history: unlike
`esports_match` above, `data/game_state.db`'s `game_states` table has
**zero** `political_race` rows (`SELECT COUNT(*) FROM game_states WHERE
event_type = 'political_race'` → 0, and a `raw_json` scan for
`race_call_status`/`tabulation_status`/`candidates`/`votehub` across all
9,072 rows, every `event_type` including `NULL`, also → 0) — this app has
never actually captured one. So the mapping came from a fresh,
unauthenticated `get_milestones_bulk(category="Elections")` +
`get_live_datas()` pull (`docs/kalshi/get-milestones.md` +
`get-multiple-live-data.md`; `category` filters by category **string**,
not milestone `type` — the wrapper only exposes `category`, so
`"Elections"` was used, matching this task's own census-derived
`political_race → Elections` mapping): 500 Elections milestones fetched,
410 carrying live data. Confirmed shape:

- `race_call_status` values observed: `"Called"` (272/410 — a winner
  decided, `winner` populated with a real candidate-id string),
  `"Too Early to Call"` (2/410 — ballots being counted, no verdict),
  `"Runoff"` (7/410 — no single winner, `winner` `""`), `""` (116/410 —
  pre-race: `candidates: {}`, `reporting_percentage: "0.0"`), and the key
  missing entirely (13/410 — a "votehub-only" payload, `{"provider":
  "votehub", "status": "created", "votehub": {...FEC campaign-finance
  data...}}`, no race-call field at all; the real shape has three keys,
  not the census's simplified one-key sketch, but still carries no status
  signal). `tabulation_status` is perfectly correlated with
  `race_call_status`'s emptiness in every observed row and adds no
  independent signal for the tri-state mapping. The top-level `details.
  status` field is unconditionally `"created"` across all 410 payloads —
  zero information.
- `winner` is a **candidate-ID string** (e.g.
  `"a7d2a5af-72a1-46cb-a3b0-bf639f346fb9"`, a key into the `candidates`
  dict), never a name — ID-to-name resolution is the design spec's D4
  structured-target lookup, not yet built, out of this task's scope.
- Mapped into this app's tri-state (verified exact lowercase strings at
  `live_status.py:216` before reusing them): `"Called"` → `"finished"`;
  `"Too Early to Call"` → `"live"`; `""` → `"none"`; missing key → `None`
  (honest no-signal, not a guessed `"none"`).
- `"Runoff"` → `"none"` (fix-round 2; **not** `"live"`, and **not** Python
  `None` either — both were tried and both were wrong). First cut mapped
  it to `"live"`, reasoning only about `live_status.py`'s own polling
  completeness (a single snapshot can't observe whether a `"Runoff"`-
  called race later flips to `"Called"` once an actual runoff election
  concludes, and `_LIVE_STATUS_TERMINAL` treats `"finished"` as "never
  poll this event again"). Task review caught the real cost that
  reasoning missed: this `status` value also flows into
  `decision_bridge.py`'s `is_live`, which **bypasses real
  `strategy_engine.py` entry-risk gates** (`close_window_sec`, the
  ROADMAP #1 minimum-runway protection, the special-market gate, the
  longshot threshold bonus) — and a race in "Runoff" is dead time before
  a *separately scheduled future* runoff election, the opposite of
  in-play. Second cut mapped it to Python `None` (this module's own
  "genuine uncertainty" convention elsewhere) — re-review caught that
  `_fetch_live_status` only writes a status into its `confirmed[et]` dict
  when it's **truthy** (`if status: confirmed[et] = status`); a falsy
  `None` silently falls through to the **schedule fallback** a few lines
  later, which re-derives `"live"` from `now` vs `occurrence_datetime`
  alone for any event past its scheduled start — reproducing the exact
  bug with zero net effect, since a real "Runoff" confirmation is by
  definition past that time. `"none"` (the truthy string, same value the
  pre-race `""` case already uses) flows straight into `confirmed[et]`
  and is used as-is, never reaching the fallback — closing the gap while
  staying out of `_LIVE_STATUS_TERMINAL`, so polling completeness is
  unaffected. Regression-tested end to end (not just the extractor in
  isolation) at `tests/test_trading_gate.py::
  test_fetch_live_status_political_race_runoff_does_not_fall_through_to_schedule_live`.

## `catalog_scan.py`'s `custom_strike` match now resolves structured-target UUIDs (2026-08-31, Task 9 of kalshi-category-data-completeness)

**Correction, final whole-branch review (2026-08-31), read before trusting anything
below:** this whole block currently does not run against real winners in production.
`related_market_by_ticker` (`catalog_scan.py`, populated a few lines above the
`custom_strike` block this section describes) is built from `ms.get(
"related_event_tickers")` fed into `get_markets_by_tickers` - but `related_event_tickers`
is documented as EVENT tickers (`docs/kalshi/get-events.md:352-356`) while
`get_markets_by_tickers`/`get_markets`'s own `tickers` filter is documented as MARKET
tickers (`docs/kalshi/get-markets.md:227-231`). Live-verified against 710 real
related_event_tickers: 0 markets returned (a control call with real market tickers
worked correctly). This is a PRE-EXISTING gap - `git blame` traces the exact line shape
to commit `eeab71a`, before this whole plan - not something Tasks 9/14 introduced, but
their new logic (this section's own `custom_strike` block, Task 14's `candidate_id_
mapping` widening) currently sits on top of it and cannot execute against real data
until it's fixed separately. See `related_market_by_ticker`'s own comment in
`catalog_scan.py` and `docs/open-decisions.md` for the tracked follow-up. The rest of
this section describes the code's intended behavior once that gap is closed, not
verified current production behavior.

`propagate_milestone_winners`'s `custom_strike` block (previously
`catalog_scan.py:154-160`, referenced above by the `esports_match` entry)
used to substring-match the raw `winner` string against `custom_strike`'s
raw dict values directly — for a `strike_type: "structured"` related
market those values are structured-target UUIDs, not display strings, so
that match could never succeed (`docs/kalshi/CHEATSHEET.md`'s new
`custom_strike`/structured-targets entry: 134/149 sampled real markets are
this type — the majority of real winner-propagation traffic, not an edge
case). Fixed by resolving every distinct UUID seen across all
`strike_type: "structured"` related markets this tick (final whole-branch
review fix round: the original shipped code collected from every related
market regardless of `strike_type` - a `"custom"` market's `custom_strike`
holds a plain display-name string, not a UUID, live-verified against real
markets; the plan's own Task 9 Step 3 said `strike_type == "structured"`
from the start), once, via the new `KalshiPublicGateway.
get_structured_targets()` (`services/kalshi/public.py`), cached in
`state["structured_targets_cache"]` (flat, no-TTL, incrementally grown —
a structured target's id→name mapping is permanent reference data once
learned, unlike `category_metadata`'s TTL'd tags/filters, which Kalshi
actively revises), and matching `winner` against each resolved target's
real `name` instead of the raw UUID. `winner` itself can also BE a UUID
(fix round: `political_race`'s `details.winner` is a candidate-ID string
per the Task 8 entry above, never an already-resolved name the way
Sports' team-name `winner` is - comparing that raw UUID against a
resolved *name* could never match, so Task 14 below was silently not
load-bearing until this fix) - resolved through the same
`structured_targets_cache` first when it's itself a cached UUID; Sports'
original case is unaffected (a plain name like `"Team Alpha"` is never a
cache key, so the lookup returns nothing and the comparison falls back to
`winner` unchanged). The pre-existing
`yes_sub_title`/`no_sub_title`/`title` fallback beneath this block is
unchanged — an id `get_structured_targets` doesn't resolve (not yet
fetched, or Kalshi doesn't return it) is skipped, same skip-not-crash
convention as `get_markets_by_tickers`/`get_events`, falling through to
that fallback exactly as before. The `esports_match` finding above still
stands: `winner` is `None` for that milestone type regardless, so no
`custom_strike` resolution ever runs for it — this fix only helps types
where `winner` is a real declared name.

## `get_events(with_milestones=True)` replaces N per-event milestone polling (2026-08-31, Task 10 of kalshi-category-data-completeness)

`propagate_milestone_winners` (`catalog_scan.py`) and `_fetch_live_status`
(`live_status.py`) both used to discover an event's milestone via
`client.get_milestones_for_event(et)`, one REST call per event ticker
(still batched Python-side via `asyncio.gather`, but N real round trips).
Both now call the new `KalshiPublicGateway.get_events(tickers,
with_milestones=True)` (`services/kalshi/public.py`) instead — one REST
call for the whole batch. `get_milestones_for_event` itself is NOT
removed — `services/market_events/event_inspector.py` and
`services/market_events/event_schedule.py` still call it for their own,
different reasons (`docs/kalshi/get-milestones.md`'s per-event lookup is
still the right tool there); only these two call sites' own polling
loops changed.

**The response-shape trap** (`docs/kalshi/get-events.md:114-118,187-201,
315-390`, live-verified against the installed SDK's own
`GetEventsResponse`/`Milestone` model_fields): `with_milestones=True`
does NOT put milestones inline on each event. `GetEventsResponse.
milestones` is a **top-level array sibling to `events`**, and each
`Milestone` carries `related_event_tickers`/`primary_event_tickers`
(both plural arrays — no singular `event_ticker` to key off). `EventData`
itself has no `milestones` field at all. So `get_events` builds the join
itself before returning: index every milestone under each ticker in its
`related_event_tickers` + `primary_event_tickers` (one pass, a dict
build, not a nested scan), then attach the matched list to every
returned event dict as `event["milestones"]` (empty list, never a
missing key, when nothing matches). `GetEventsResponse.milestones` is
also genuinely `Optional[List[Milestone]]`/`required=False` — the same
nullable-response-field shape Task 9's `get_structured_targets` hit —
guarded with `resp.milestones or []` from the start.

**Additive, not a breaking change:** `with_milestones` defaults to
`False`, but `get_events` always sends it explicitly (never omits the
kwarg) — `test_get_events_default_sends_with_milestones_false_for_
existing_callers` (`tests/test_kalshi_client.py`) pins this. When `False`, no
`milestones` key is added to the returned dicts at all — every
pre-existing caller (`services/market_watch/event_metadata.py`'s
`_fetch_event_titles`, and every other pre-Task-10 test) sees byte-for-
byte the same return shape as before.

**Milestone ordering, a judgment call:** both call sites only ever use
the *first* entry in an event's milestone list (`ms_list[0]`), matching
the old per-event endpoint's own `ms_result[0]` convention. The top-level
`milestones` array carries no documented ordering guarantee (checked the
full `get-events.md` schema section - no "sorted"/"ordered by" language
anywhere), so this is Kalshi's own list-return order, not a claim that it
matches the old per-event endpoint's ordering byte-for-byte — those are
two different endpoints/filters with no documented relationship between
their orderings. Documented at `get_events`' own docstring and both call
sites; not silently assumed.

**`live_status.py`'s scope is narrower than `catalog_scan.py`'s,
deliberately:** `_fetch_live_status` applies the new batched call only to
`needs_fetch` (the cache-miss remainder after `state["milestone_by_event"]`'s
broad-cache short-circuit — see the "Broad milestone discovery" entry
above), not the full `to_poll` list — applying it to all of `to_poll`
would re-fetch milestones for events that cache already resolved,
discarding that already-landed REST-call reduction (entry-gate-me-
pairing-and-netting-remediation, commit `56ae320`). `catalog_scan.py`'s
`propagate_milestone_winners` has no such broad-cache path, so it applies
the batched call to its own full `to_poll` equivalent directly.

## `_get_series_cache` polls `min_updated_ts` deltas after the first sync, merges rather than replaces (2026-08-31, Task 11 of kalshi-category-data-completeness)

`get_series_list` (`services/kalshi/public.py`) gained two additive,
omit-when-unset params: `min_updated_ts` (int, Unix seconds) and
`include_product_metadata` (bool) — `docs/kalshi/get-series-list.md:
100-108`. A genuine first-ever sync (`cache["series"]` empty) still calls
`get_series_list()` with zero args, a full fetch byte-identical to before
this task. Every later refresh instead watermarks on
`_series_watermark(cache["series"])` and, when computable, calls
`get_series_list(min_updated_ts=..., include_product_metadata=True)` —
Kalshi then legitimately returns only the series whose metadata changed
since the watermark, not the whole ~9,400-series catalog.

**The load-bearing fix this task exists for:** `_get_series_cache` used to
assign the fetched batch straight to `cache["series"]`
(`cache["series"] = series`), correct only because that batch was always
the *complete* list. Once a refresh can legitimately return a partial
delta, that assignment would silently drop every unchanged series out of
`_get_top_series`/`_scan_catalog_batch`/`search_markets` — the exact
completeness regression CLAUDE.md's data-plane HARD RULE forbids. Fixed by
rebuilding a ticker-keyed dict seeded from the existing cache, upserting
every series the response actually returned, and re-deriving the sorted
list from that dict before it's assigned back and persisted
(`series_cache.save(...)` is always called with this fully-merged list,
never the raw fetch result — `series_cache`'s own blob table does a full
`ON CONFLICT DO UPDATE` overwrite, so handing it a partial list would lose
every series not in that tick's delta from the persisted blob even though
the in-memory dict was merged correctly).

**A genuinely different unit on each side of `min_updated_ts`,
confirmed directly (not the same field re-echoed):** the request param is
`type: integer, format: int64` (Unix seconds); the response's
`Series.last_updated_ts` is `type: string, format: date-time` (ISO-8601,
`get-series-list.md:228-231`). `_series_watermark` converts via
`datetime.fromisoformat(...).timestamp()`, then truncates to `int` —
`docs/kalshi/CHEATSHEET.md`'s "Can a `min_updated_ts` watermark be a
float" entry live-verified a bare HTTP 400 for a fractional value on this
same parameter family, with no local exception to catch it.

**Exclusive-boundary overlap, a judgment call (not in the original task
brief):** `min_updated_ts` is documented "Filter series with metadata
updated **after** this Unix timestamp" — exclusive, same semantic
`milestone_scan.py`'s own `_WATERMARK_OVERLAP_SEC` already guards against
for the identical parameter family. `_SERIES_WATERMARK_OVERLAP_SEC` (5s,
matching that precedent) subtracts a few seconds before use, so two series
updates landing in the same integer second can't split across "already
visible to this fetch" / "not yet visible" and silently exclude the
latter forever once the watermark advances past that second. Free here:
the extra re-fetched overlap lands in the merge as a harmless upsert of an
already-known series.

**Zero-volume-in-a-delta, a judgment call (the task brief flagged this as
unaddressed and left it to judgment):** the pre-merge filter
(`float(s.get("volume_fp") or 0) > 0`) used to run against the full
catalog every refresh, so a series whose volume dropped to zero was
naturally excluded again on the next full rebuild — a silent self-correct.
Under the delta model, a series that dropped to zero volume still shows up
in the delta (its metadata changed), but a bare pre-merge filter would
just skip it, leaving a stale, no-longer-true higher-volume entry in the
merged cache forever. `_get_series_cache` instead treats a zero-volume
entry *in the raw delta* as a removal signal — `merged.pop(ticker, None)`
— rather than a silent no-op. A ticker simply absent from this cycle's
delta (unrelated series, unchanged) is untouched either way.

**Every existing test kept passing unmodified** (regression-testing
standard for this plan): the fee-changes tests' `_FakeFeeChangesClient.
get_series_list(self)` takes zero args, so `_get_series_cache` must call
`client.get_series_list()` with no `min_updated_ts=None` kwarg at all on
the first-sync path — not just an equivalent default value — confirmed by
running the pre-existing suite unchanged. `test_kalshi_client.py`,
`test_catalog_scan_pacing.py`, `test_series_cache.py`: 54 passed.

**Periodic full resync, a real gap found in the final whole-branch review
(fix round, not in the original task):** `min_updated_ts` filters on
Kalshi's own "metadata updated" definition (`get-series-list.md:100-108`),
and `Series.last_updated_ts` is explicitly "when this series' **metadata**
was last updated" (`:228-231`) — trading volume moving is not documented
as a metadata update, and a live, read-only probe of this app's own
`data/series_cache.db` confirmed the two are decoupled in practice
(`KXNCAAMBGAME`: 5.9 billion lifetime `volume_fp` **contracts** —
get-series-list.md's own field description, not dollars — a top-10 series
by volume, with `last_updated_ts` 147 days stale). Since `state["series_cache"]` is
seeded from the persisted DB at every process start
(`services/app_state.py`'s own comment), a delta refresh fires on
effectively every restart once the DB has any history — meaning, without
a fix, a series whose metadata stops changing would have its `volume_fp`
frozen FOREVER, and a series that starts at zero volume could never
re-enter the cache at all: a silent, permanent completeness/accuracy
regression on the exact key `_get_top_series` ranks the whole automatic
watchlist by. Fixed with `_SERIES_CACHE_FULL_RESYNC_SEC` (24h, an
in-memory-only `cache["last_full_sync_at"]`, not persisted — a restart
just means it defaults to due, the same safe direction as a genuine
first-ever sync): a full, unfiltered `get_series_list()` call fires at
least once per this interval regardless of the watermark, bounding the
staleness window instead of leaving it unbounded.

## `political_race` candidate resolution via `candidate_id_mapping` (2026-08-31, Task 14 of kalshi-category-data-completeness)

**Correction, final whole-branch review (2026-08-31):** same caveat as the Task 9
section above - this widens the UUID pool fed into `structured_targets_cache`, but the
actual winner-matching loop it feeds (`catalog_scan.py`'s `custom_strike` block)
currently never receives real markets to match against, due to a pre-existing,
separate gap in `related_market_by_ticker`'s own data-fetching (see that entry). This
task's own contribution (`candidate_id_mapping` reaching the resolution batch, and the
UUID-vs-name comparison fix noted below) is correct and tested in isolation; it is
blocked from mattering in production until the separate gap is fixed.

Extends Task 9's `structured_targets_cache` mechanism to a second UUID
source specific to `political_race` milestones: `candidate_id_mapping`, a
field on the MILESTONE's own `details` (from `get_events(...,
with_milestones=True)`'s join, Task 10 — not the separate live-data
`details` fetched via `get_live_datas`, where `winner`/`race_call_status`
live). Not documented anywhere in `docs/kalshi/` (`grep -rn
"candidate_id_mapping" docs/kalshi/*.md` returns zero hits) — the plan's
own Step 1 test sketch guessed the wrong shape (`{candidate_uuid: market_
ticker}`, and a `custom_strike` key of `"candidate"`), corrected by a
real, live, read-only pull against Kalshi's production API before this
task's implementer was dispatched.

**Real, live-verified shape** (a real, currently-open Massachusetts Senate
primary, milestone id `2967c0f7-57f5-47ee-be41-f92df9dc699a`, event
`KXSENATEMAR-26`): `candidate_id_mapping` is `{pol_id: candidate_
structured_target_uuid}` — Kalshi's own internal numeric politician id
mapped to that candidate's structured-target UUID, the SAME UUID space
`get_structured_targets` resolves. That candidate's own market's
`custom_strike` carries the identical UUID under the key `"politician"`
(not `"candidate"`) — confirmed by direct comparison. **A confirmed,
permanent, out-of-scope limitation:** 3 of that same real race's 4
candidates have `strike_type: "custom"` markets with a plain-name
`custom_strike` (e.g. `{"Candidate": "Lewis Evangelidis"}`) and are absent
from `candidate_id_mapping`/`candidate_ids`/`pol_ids` entirely — Kalshi
itself hasn't assigned them a structured target, so there is no UUID
anywhere in this milestone's data to resolve them with. This task cannot
fix that and does not attempt to; those candidates keep falling through
to the pre-existing `yes_sub_title`/`no_sub_title`/`title` fallback,
unchanged.

**The genuine, if modest, value added:** for every `political_race`
milestone in scope this tick, `candidate_id_mapping`'s VALUES (never the
`pol_id` KEYS, which aren't resolvable via `get_structured_targets` at
all) are folded into the SAME `custom_strike_ids`/`get_structured_targets`
batch Task 9 built — zero extra REST calls (the milestone dict is already
in scope; `details` is a required field on the SDK's `Milestone` schema).
Widens `structured_targets_cache` for a race where a candidate is
registered in `candidate_id_mapping` but their own market doesn't yet
carry a resolvable `custom_strike` UUID this particular tick (a
data-population lag) — worst case redundant with what the market-level
scan already found, best case pre-warms a name the market-level scan
alone would have missed.

**Fix round, final whole-branch review:** the originally shipped code only
widened the UUID pool; it never fixed the fact that `winner` itself is a
UUID for `political_race` (see the Task 9 entry above's "winner itself can
also BE a UUID" addendum) — so the pool-widening this task adds was never
actually load-bearing until that companion fix landed in the same round.

## Unmapped `race_call_status` values default to `"none"`, not Python `None` (2026-08-31, fix round on Task 8 of kalshi-category-data-completeness)

`_POLITICAL_RACE_STATUS`'s 4 known values (see the Task 8 entry above)
came from one 500-milestone pull on one day — an unobserved real value
(e.g. Kalshi introducing `"Contested"`) would otherwise fall through
`.get()`'s dict-default to Python `None`, which is falsy and silently
reproduces the exact `is_live` entry-gate-bypass bug Task 8's two fix
rounds closed for `"Runoff"` specifically (`live_status.py`'s `confirmed
[et]` check only routes a truthy value; a falsy `None` falls to the
schedule fallback, which re-derives `"live"` for any event past its
`occurrence_datetime`). `_political_race` now distinguishes this case
from the genuinely-missing-`race_call_status`-key case (still honest
`None`, unchanged): an unmapped-but-present value gets the same `"none"`
string default `"Runoff"` uses, fault-logged once per distinct value per
process (`_record_unmapped_political_race_status`, mirroring
`_record_default_path_type`'s existing shape exactly) rather than a
silent, unbounded-vocabulary guess.
