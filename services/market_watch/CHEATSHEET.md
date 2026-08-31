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

`propagate_milestone_winners`'s `custom_strike` block (previously
`catalog_scan.py:154-160`, referenced above by the `esports_match` entry)
used to substring-match the raw `winner` string against `custom_strike`'s
raw dict values directly — for a `strike_type: "structured"` related
market those values are structured-target UUIDs, not display strings, so
that match could never succeed (`docs/kalshi/CHEATSHEET.md`'s new
`custom_strike`/structured-targets entry: 134/149 sampled real markets are
this type — the majority of real winner-propagation traffic, not an edge
case). Fixed by resolving every distinct UUID seen across all related
markets this tick, once, via the new `KalshiPublicGateway.
get_structured_targets()` (`services/kalshi/public.py`), cached in
`state["structured_targets_cache"]` (flat, no-TTL, incrementally grown —
a structured target's id→name mapping is permanent reference data once
learned, unlike `category_metadata`'s TTL'd tags/filters, which Kalshi
actively revises), and matching `winner` against each resolved target's
real `name` instead of the raw UUID. The pre-existing
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
kwarg) — `test_get_events_default_omits_with_milestones_for_existing_
callers` (`tests/test_kalshi_client.py`) pins this. When `False`, no
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
