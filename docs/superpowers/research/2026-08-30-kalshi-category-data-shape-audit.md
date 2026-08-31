# Kalshi category-specific data shapes — completeness audit (Stage 1: investigate)

**Task.** Stage 1 of a 9-stage delegated pipeline. Systematically catalog every
Kalshi-documented field/endpoint/data shape that is category-specific or
category-relevant across all of this app's traded categories, cross-reference each
against what the app actually captures, and produce a gap analysis. No code changes;
research only.

**Trigger.** A prior session anchored on `get-filters-for-sports.md` (sports-only)
instead of checking whether a general, all-category mechanism existed — it did
(`get-tags-for-series-categories.md`). User: *"i feel like other categories have
other category_specific fields or data shapes that we are leaving out. stuff that
shouldn't have been left out after doing the api analysis."*

**Method.** (1) Read all 241 entries of `docs/kalshi/llms.txt` and opened every page
whose *concept* could be category-scoped, not only ones with a category word in the
title — the exact failure mode that produced this task. (2) For each candidate,
read the mirrored page's OpenAPI/AsyncAPI schema. (3) Cross-referenced against
`services/` by grep + reading the call sites. (4) Where the docs could not settle a
question, measured against live data: read-only queries on the running app's own
`data/*.db`, and unauthenticated public GETs against
`https://external-api.kalshi.com/trade-api/v2`. Every measurement below names its
source and its date. Nothing here is inferred from model memory.

**Verdict vocabulary.** `CAPTURED` — the app already reads/stores/uses it.
`GAP-PURSUE` — real gap, worth a Stage 3 design. `GAP-SKIP` — real gap, evidence
says not worth pursuing (reason given). `NEEDS-LIVE` — cannot be judged without a
measurement not made here.

**Revision, 2026-08-30 (post-Stage-2).** Stage 2's review
(`…-kalshi-category-data-shape-audit-review.md`) falsified three findings that were all
drawn from **one page** of the paginated `/milestones` endpoint and generalised into
type-level properties. They have been re-derived here against the **entire population**:
275 pages at `limit=500` → **137,200 milestones**, then `GET /live_data/batch` run over
every one of those 137,200 ids → **112,501 live-data payloads**. No sampling anywhere in
the corrected rows. Rewritten below and marked **⟲**: **S3**, **S4**, **P1**, **P3**,
§2.7's justification, and §4's ranking; **D1** gains the two fee-changes endpoints Stage 2
found missing. Everything unmarked is Stage 1's original text, which Stage 2
independently reproduced. Still outstanding from Stage 2's review and deliberately *not*
touched by this narrow pass: the `Heatwaves` example in §0/W4 (it is in the taxonomy; the
"+4" count is right, only the prose example leaked), X14's "the one market-data endpoint"
uniqueness clause (`get-market-orderbook.md` and `get-multiple-market-orderbooks.md`
declare `security` too), and the mixed local/UTC date convention.

---

## 0. Headline correction: the "general mechanism" is not authoritative

The lead this task was handed — `/search/tags_by_categories` as *the* general,
all-category mechanism — is real but is a **UI facet-filter vocabulary, not the
category or tag taxonomy**. Three independent measurements:

| Measurement | Result |
|---|---|
| Live `GET /search/tags_by_categories` (unauth, 2026-08-31T00:0xZ) | names **14** categories |
| Live `/series` as the app itself fetched it (`data/series_cache.db`, `fetched_at` 2026-08-30T22:54Z, 10,351 volume-positive series) | carries **17** distinct `category` values |
| Categories in the taxonomy but empty/null | `Companies`, `Social`, `Transportation` (3 of the 14) |
| Real categories **absent from the taxonomy entirely** | `World` (87 series), `Health` (48), `Education` (1) |
| Per-category tags observed on real `Series.tags` but **absent from the taxonomy** | every non-empty category: Politics +23, Entertainment +22, Financials +13, Elections +12, Economics +8, Sports +8, Science and Technology +7, Health +6, Commodities +5, World +5, Climate and Weather +4, Mentions +4, Companies +3, Crypto +2, Transportation +1 |

Concrete examples of real tags the taxonomy omits: `Heatwaves` (12 Climate series),
`Agriculture` (14 Commodities series), `Foreign Elections`, `Trump Agenda`,
`Trump Policies`, `Culture war`, `Retail/consumer spending`, `Mortgages`,
`FDA Approval`, `Airlines & aviation`, `Olympics`, `UFC`, `Head to Heads`.

**Authority ordering this establishes** (and which the rest of this audit uses):

1. `Series.category` and `Series.tags` on the real `/series` objects
   (`get-series.md:137-147`, `get-series-list.md`) are the ground truth.
   `terms.md:11-13` says so directly — "A series belongs to one category…
   subcategories are often represented as tags" — and `terms.md:29` closes with
   "rely on fields like `series_ticker`, `event_ticker`, `category`, and `tags`."
2. `/search/tags_by_categories` is a *discovery aid* for browsing, explicitly framed
   that way in `terms.md:13` ("to **discover** tags grouped by category"). Treating
   it as the vocabulary is what makes categories disappear.
3. `/search/filters_by_sport` is a Sports-only sub-facet of (2).

This is a **docs/live discrepancy worth a `docs/kalshi/CHEATSHEET.md` entry**: the
taxonomy endpoint is neither a superset nor an enumeration of what `/series` returns.

A second, independent contract violation sits next to it: `terms.md:29` says
**"do not parse ticker strings to infer relationships."** `services/signal_log.py:152-161`
(`series_of`) does exactly that (`ticker.split("-")[0]`), and the CHEATSHEET already
records one shipped bug from it (`KXMVECROSSCATEGORY0-SHARD1` → `KXMVECROSSCATEGORY0`,
entry "Where does the KXMVECROSSCATEGORY0-SHARD1 NFL-combo maker-fee exemption live").
The correct chain (`market.event_ticker` → `event.series_ticker`) is already persisted
in `title_cache.event_titles.series_ticker` (`services/title_cache.py:118` region).

---

## 1. Cross-category mechanisms (the general layer)

| # | Kalshi source | What it is | What the app does | Verdict |
|---|---|---|---|---|
| X1 | `get-tags-for-series-categories.md` (`GET /search/tags_by_categories`) | category → tag-vocabulary map | Fetched hourly (`services/market_watch/discovery_cache.py:55-92`), cached in `state["category_metadata"]`. Consumed in exactly two places: `main.py:870-873` stamps `event_meta["category_tags"] = tags_by_categories.get(category)` on **every** event, and `frontend/src/js/shared-utils.js:478` builds an autocomplete list. | **CAPTURED but mis-used.** `docs/kalshi/CHEATSHEET.md`'s own first entry already records that `category_tags` "carries zero per-event signal — it's the identical full facet-filter vocabulary listed on every event in a category." The app is writing a constant onto every event object and calling it metadata. |
| X2 | `get-series.md:140-147` / `get-series-list.md` — `Series.tags` | the **real** per-series subcategory list | `get_series_list` fetches raw JSON (`services/kalshi/public.py:134`) so `tags` survives into `state["series_cache"]` and onto disk (`services/series_cache.py:31`, whole objects as a JSON blob). **Zero readers**: `grep -rn 'tags' services main.py` finds only `tags_by_categories`/`category_tags`. | **GAP-PURSUE.** 8,329 of 10,351 live series carry real tags (measured 2026-08-30T22:54Z). This is the single richest category-shaped signal already sitting in the app's own storage, entirely unread. |
| X3 | `get-series-list.md` query params: `category`, **`tags`**, **`include_product_metadata`**, `include_volume`, **`min_updated_ts`** | server-side filtering + a delta-poll watermark | `services/kalshi/public.py:134` sends only `include_volume=True`, then filters by category **client-side** (`:136-137`). `tags`, `include_product_metadata` and `min_updated_ts` are never sent. | **GAP-PURSUE (efficiency + fidelity).** `min_updated_ts` is documented as "use this to efficiently poll for changes"; the app re-pulls all ~13.6k series every hour instead. `include_product_metadata=false` is why series-level `product_metadata` is **null on all 10,351 cached series** (measured) — an object the app has never once seen. `exchange_sharding.md:82` even gives the canonical `tags` usage: `GET /series?category=Sports&tags=Tennis,Baseball`. |
| X4 | `get-series.md:191-198` — `Series.additional_prohibitions` | per-series list of who is legally barred from trading the contract | Persisted verbatim in `data/series_cache.db`'s blob. **Zero readers anywhere** (`grep -rn additional_prohibitions services main.py tools tests frontend static` → no output). | **GAP-PURSUE.** Present on **10,350 of 10,351** live series. `CLAUDE.md`'s standing open-gap list names "zero category-level legal-risk awareness" — this field *is* that data, already downloaded. Measured shapes are sharply category-specific: Elections series carry 13–22 clauses naming election officials, Decision Desk staff, FEC commissioners, registered lobbyists; Sports series name league players/coaches/staff; Entertainment names `Rotten Tomatoes`; the Economics/Financials default pair is just source-agency employees + MNPI holders. |
| X5 | `get-series.md:148-156` (series) and `get-events.md:283-290` (event) — `settlement_sources` | the official source(s) a market resolves against | Event-level: fetched and persisted (`services/market_watch/event_metadata.py:119`, `services/title_cache.py` `settlement_sources_json`). Series-level: in the `series_cache.db` blob, unread. Neither is consumed by any decision. | **GAP-SKIP for now.** Already captured at the event level; a Stage 3 use (e.g. "this Economics series settles off BLS, which publishes at 08:30 ET") is real but is a *modeling* project, not a data-capture gap. |
| X6 | `targets_and_milestones.md:71-86`, `get-structured-targets.md`, `get-structured-target.md` | `strike_type: "structured"` + `custom_strike: {<target_type>: <uuid>}` resolves to a real-world entity (team, player, competitor, candidate) via `GET /structured_targets` | **No code path exists.** `grep -rn 'structured_target' services main.py tools tests` → no output. The one `custom_strike` reader, `services/market_watch/catalog_scan.py:142-148`, does `any(str(winner).lower() in str(v).lower() for v in cs.values())` — a substring match against what are, in fact, UUIDs. | **GAP-PURSUE.** Live evidence from `docs/kalshi/markets.json` (149 real markets): **134/149 are `strike_type: "structured"`**, and `custom_strike` keys are the entity type — `golf_competitor` (68), `baseball_team` (27), `tennis_competitor` (23), `football_team` (11), `basketball_team` (10), `ufc_competitor` (9), `round_digits` (1). The values are UUIDs, so the `catalog_scan.py:144` branch **cannot ever match** on a structured market; it silently falls through to `yes_sub_title`/`title` substring matching. The `custom_strike` **key** alone is a free, per-market, category-specific entity-type label the app discards. |
| X7 | `get-events.md:114-118` — `GET /events?with_milestones=true` | returns the milestone array inline with events, zero extra calls (`targets_and_milestones.md:45`) | `services/kalshi/public.py:223` calls `get_events(tickers=…, limit=…)` — the flag is never passed. | **GAP-PURSUE.** See M1 below; this is the cheapest of three ways to fix the same thing. |
| X8 | `get-milestones.md:86-136` — `GET /milestones` with `category`, `competition`, `type`, `min_updated_ts` | bulk, category-scoped milestone listing | `services/kalshi/public.py:241-254` (`get_milestones_bulk`) exists, is documented as feeding "`_sync_milestones_bulk`'s local event_ticker → milestone map in main.py", and **that consumer has never existed** (`git log -S"_sync_milestones_bulk" -- main.py` → empty; grep across the repo → only the docstring). | **GAP-PURSUE (dead code + REST cost).** `services/market_watch/live_status.py:145-147` instead issues one `get_milestones_for_event` REST call **per event per poll**. The unused method's own live-verified docstring: one bulk call returned 200 milestones covering 1,483 distinct event tickers. |
| X9 | `exchange_sharding.md:12, 73-83` — `exchange_index` | **exchange shards are assigned by category**: 1 = Exotics (Combos), 2 = Crypto, 3 = Sports∩{Tennis, Baseball}, 0 = everything else. Live since 2026-08-24 (`:22`). Present on `GET /markets`, `GET /events`, and the lifecycle WS (`:53`). | Portfolio side fixed 2026-08-30 (issue #251 — `services/position/account_positions.py:48,88`). **Market/event side: not persisted anywhere.** `market_catalog.markets` has no such column (schema read live); `title_cache.event_titles` has none; `services/market_watch/market_fetch.py:33-48`'s `_MARKET_FIELDS` whitelist drops it. | **GAP-PURSUE.** This is the most literally "category-specific" field in the whole API — a category→shard routing key. `exchange_sharding.md:26` — "Programmatic traders must **preallocate collateral on a given exchange shard before order placement**" — makes it a hard prerequisite for the ROADMAP's live-trading program, not a nicety. |
| X10 | `market-and-event-lifecycle.md:313, 681, 821` | the `market_lifecycle_v2` **channel** carries three message types: `market_lifecycle_v2`, **`event_lifecycle`** (new event: `event_ticker`, `exchange_index`, `title`, `subtitle`, `collateral_return_type`, `series_ticker`, `strike_date`, `strike_period`), and **`event_fee_update`** (`fee_type_override`, `fee_multiplier_override`) | `services/kalshi/websocket.py:747` handles only `msg_type == "market_lifecycle_v2"`. `_CLASS_BY_MESSAGE_TYPE` (`:109-123`) has no entry for the other two, so both land in `_OTHER_CLASS` and are dropped. | **GAP-PURSUE.** `event_lifecycle` is the real-time complement to the 300 s REST discovery refresh — new events arrive with their `series_ticker` and `exchange_index` attached. `event_fee_update` is the push channel for exactly the override the app just wired from REST in issue #264. **Grep trap worth naming:** `grep -rn event_lifecycle services` returns many hits, all of them the *unrelated local module* `services/market_events/event_lifecycle.py` — this gap looks covered and is not. |
| X11 | `multivariate-market-and-event-lifecycle.md` (`multivariate_market_lifecycle`) | separate WS channel; "Only emits lifecycle updates for multivariate events", incl. **event creation** | Not subscribed. `services/kalshi/websocket.py:1402-1416` subscribes `market_lifecycle_v2` only. MVE discovery is REST polling (`services/market_watch/mve_scan.py`, issue #268). | **GAP-PURSUE.** Measured impact: MVE series are **14.87 % of all 97,541 logged signals** (`data/signal_log.db` joined to `series_cache`, 2026-08-30 — `KXMVECROSSCATEGORY` 14,302 + `KXMVECROSSCATEGORY0` 121 + `KXMVESPORTSMULTIGAMEEXTENDED` 78). #268 fixed *discovery*; this channel is the *timeliness* half, and `get-multivariate-events.md` has no recency/status filter at all (CHEATSHEET, Gotcha 2), which is exactly the hole a push channel fills. |
| X12 | `fixed_point_migration.md:39-72` — `price_level_structure` / `price_ranges` | per-market tick grid. "Structures are **assigned per market** — for example, multivariate (combo) markets use `center_deci_edge_centi_cent`." Changes are pushed as `price_level_structure_updated` on the lifecycle channel. | Field survives WS normalization (`tests/test_kalshi_trade_ws.py:304-309`) but **nothing reads `price_ranges`**. `services/kalshi/websocket.py:750-760` logs `price_level_structure_updated` once and does nothing. `_MARKET_FIELDS` drops both. | **GAP-SKIP while in paper mode; GAP-PURSUE before live.** Measured in `docs/kalshi/markets.json`: `linear_cent` 76, `tapered_deci_cent` 73 — a near-even split even in one Sports-heavy sample. The doc's own instruction is "Do not key pricing logic off this name… a client that reads `price_ranges` is automatically compatible" — the app currently reads neither, i.e. it assumes whole-cent everywhere. Harmless against a simulated broker; an invalid-price rejection against a real one. |
| X13 | `get-incentives.md` (`GET /incentive_programs`) | per-market liquidity/volume reward programs, filterable by `status`/`type` | No code path (`grep -rn incentive services main.py tools` → no output). | **NEEDS-LIVE.** Directly relevant to a *whale-signal* app: an incentive program is a documented reason for large volume that is **not** informed flow. Whether it materially contaminates this app's signal population is unmeasured; one `GET /incentive_programs?status=active` cross-joined against `signal_log.signals.ticker` settles it in one call. |
| X14 | `get-event-forecast-percentile-history.md` (`GET /series/{s}/events/{t}/forecast_percentile_history`) | the historical market-implied distribution across a scalar-strike event's market ladder, at up to 10 percentiles, down to 5-second intervals | No code path. | **GAP-PURSUE (highest analytical ceiling, lowest certainty).** Applies to every category whose events are a ladder of numeric strikes: Economics (CPI/jobs), Climate and Weather (temperature bands), Crypto (price bands), Financials (index levels), Commodities. A whale print on one rung is a bet on the whole distribution; this endpoint is the only documented way to see the distribution move. **Caveat to verify first:** this is the one market-data endpoint in the mirror that declares `security: kalshiAccessKey` (`:141-144`) — it is authenticated, unlike the rest of the public read path this app uses. |
| X15 | `get-event-metadata.md` (`GET /events/{t}/metadata`) | `competition`, `competition_scope`, `image_url`, `featured_image_url`, `market_details[].color_code` | Not called. `competition`/`competition_scope` are instead read off `event.product_metadata` (`services/market_watch/event_metadata.py:116-117`, live-confirmed there). | **GAP-SKIP.** The only decision-relevant fields on this endpoint are already obtained for free from the batched `get_events` call; the rest is presentation. |

---

## 2. Per-category findings

### 2.1 Sports

Best-covered category, and still the source of the sharpest measured defect in this
audit.

| # | Source | Finding | Verdict |
|---|---|---|---|
| S1 | `get-filters-for-sports.md` (`/search/filters_by_sport`) | sport → `{scopes, competitions{competition: {scopes}}}` + `sport_ordering` | **CAPTURED.** `discovery_cache.py:84-88` builds the `sport_by_competition` reverse map; `services/market_lookup.py:133-134` consumes it. Correctly identified in-repo as sports-only. |
| S2 | `live-data-get-live-data.md`, `get-multiple-live-data.md` — milestone live data | per-milestone in-game state | **CAPTURED** (`live_status.py:163`, batched, repoll-cached) — **but only one key is ever read out of a payload the exchange documents as varying by type; see S3/S4 (⟲ both re-derived) and P3.** |
| S3 **⟲** | full-population live-data census, 2026-08-30 (revision note above) | **Stage 1's claim here is falsified and withdrawn.** `details.widget_status` — which `live_status.py:169` reads as *the* live-status signal — is present on **49,764 of 49,764** live `tennis_tournament_singles` payloads (the transcribed key list was a strict subset of the real one). No tennis event falls through; the stated impact was zero. The real hole is elsewhere and is **entirely non-Sports**: seven types return live data carrying `status` but **no `widget_status`** — `political_race` 810, `one_off_milestone` 420, `company_report` 337, `truflation` 241, `artist_streams` 139, `kpis` 14, `tv_views` 1 = **1,962 of 112,501 live milestones (1.7 %)**, rising to **814 of the 2,869 forward-dated ones (28.4 %)**, where `political_race` alone is 520. | **GAP-PURSUE, re-scoped.** Those seven are the types that silently fall through to `live_status.py:216`'s schedule guess (`"none" if now < occ else "live"`) — the path the module's own docstring calls a deliberate last resort. All seven attach to series in categories the app already configures, verified along the documented `event_ticker → event.series_ticker → Series.category` chain (24 events sampled per type): `political_race` → Elections/Politics, `company_report` → Mentions, `truflation` → Economics, `artist_streams` → Entertainment. |
| S4 **⟲** | same census | **Golf confirmed; tennis falsified and withdrawn; a far larger hole found.** `details.winner` — which `catalog_scan.py:117` requires to map a resolved event — is absent from `golf_tournament` (**0 of 169** live; keys `current_round`, `leaderboard`, `round_label`, `status`, `widget_status`) and **present** on tennis (49,764/49,764, alongside `round_winners`). Eight types return live data with no `winner`: **`esports_match` 9,385**, `one_off_milestone` 420, `company_report` 337, `truflation` 241, `golf_tournament` 169, `artist_streams` 139, `kpis` 14, `tv_views` 1 = **10,706 of 112,501 (9.5 %)**, and **382 of 2,869 (13.3 %)** forward-dated. | **GAP-PURSUE — bigger than Stage 1 claimed, for a different reason.** The dominant type is `esports_match` (11,406 milestones, 8.3 % of the population), not golf (172, 0.13 %), and its related events resolve to **Sports** series — a configured category — so `propagate_milestone_winners` is silently unable to resolve any esports event today and `market_history.record_outcome` never fires for them. The outcome is derivable there, just not from the one key the code looks for: `esports_match` carries `widget_status`, `home_score`/`away_score`, `is_live`, `home_periods`/`away_periods`, `series_stats` and `player_stats`. Golf's `leaderboard` and tennis's `round_winners` are the same story — per-type answers, not absences. |
| S5 | `get-game-stats.md` (`/live_data/milestone/{id}/game_stats`) | full play-by-play | **GAP-SKIP** — already assessed in-repo on 2026-08-15 ("much heavier/more detailed than anything this app currently needs", `get-game-stats.md:33-40`). No reason to revisit. |
| S6 | `live-data-get-live-data.md:77-86` — `include_player_stats` | player-level stats for Pro Football / Pro Basketball / College Men's Basketball milestones | Never passed (`services/kalshi/public.py:269`, `:292`). | **GAP-SKIP.** Same reasoning as S5, and the app has no player-level model to feed. |
| S7 | `exchange_sharding.md:82` | **Tennis and Baseball specifically** are being migrated to shard 3 — a *tag*-level, not category-level, split | Nothing reads `exchange_index` on markets/events (X9). | Folded into **X9**. |

### 2.2 Crypto

| # | Source | Finding | Verdict |
|---|---|---|---|
| C1 | `cfbenchmarks-value.md` (WS `cfbenchmarks_value`) | real-time index values + `last_60s_windowed_average_15min` — literally the settlement input for `KXBTC15M` | **CAPTURED**, and well: `services/index_feed/`, `settlement_algebra.py`, `websocket.py:1424-1430`, `config/settings.yaml` `index_feed.index_ids: [BRTI, ETHUSD_RTI]`, plus a reconnect-gap REST backfill (issue #260). |
| C2 | `rest-passthrough.md` (`/cfbenchmarks/history/values`) | index history passthrough | **CAPTURED** (`services/index_feed/backfill.py`), with its 50-token cost and entitlement caveat already recorded in `docs/kalshi/CHEATSHEET.md`. |
| C3 | `pyth-value.md` (WS `pyth_value`) | real-time Pyth prices per `underlying_ticker` | Handler exists (`websocket.py:733-735` → `index_feed.record_pyth`), but `config/settings.yaml` `index_feed.underlying_tickers: []` and `websocket.py:1431` only sends the subscribe **`if self.underlying_tickers`** — so **no Pyth subscription is ever created**. | **GAP-PURSUE.** |
| C4 | `pyth-value.md:25-40` | `underlying_tickers: ["all"]` receives every available underlying; a bare subscribe + `underlying_list` action discovers what is streaming. The app implements exactly this discovery pattern for CF Benchmarks (`websocket.py:546-565`, `request_index_list`) — **and has no `underlying_list` equivalent** (`grep -n underlying_list websocket.py` → only the passive message-class entry at `:122`). Circular: no subscription → no `sid` → discovery is impossible even if requested. | **GAP-PURSUE**, same fix as C3. |
| C5 | `pyth-value.md:164, 289-291` | the doc's own examples are **`Metal.XAU/USD`** and **`Metal.XAG/USD`** — gold and silver | The Pyth feed is **not crypto-only**; it is the underlying-price feed for **Commodities** too. See CM1. | Reclassifies C3/C4 as cross-category. |
| C6 | `get-event-live-data.md:69-72` | event-keyed live data: "crypto price charts, commodity price timeseries, and weather observations" | **CAPTURED** (`services/market_watch/event_metadata.py:158-220`), with `**ld` spread so unknown fields survive, and persisted via `game_state.record`. Sports is the one excluded category and the exclusion is live-justified in-place (`:137-154`). Good precedent — this is what "don't drop what the exchange sent" looks like. |
| C7 | `fixed_point_migration.md:72` | combo markets use `center_deci_edge_centi_cent`; crypto series are the other common non-`linear_cent` population | See **X12**. |

### 2.3 Climate and Weather

| # | Source | Finding | Verdict |
|---|---|---|---|
| W1 | `get-weather-index.md` (`GET /live_data/weather/{city}`) | **The** Climate-and-Weather-specific endpoint. "the canonical minute-resolution series behind hourly temperature markets. City-keyed and **independent of any event**." Returns `{city, config_version, units, timeseries[{t, v, status, contributors, stations[]}]}`. `status` ∈ `normal`/`degraded`/`incomplete`; with `detailed=true`, every member station's raw reading and QC disposition (`ok`/`missing`/`late`/`range`/`rate_spatial`/`extreme`/`pending`) and source (`hf_asos`/`metar`). Windowed by `from`/`to`/`last_sec`. | **No code path** (`grep -rn 'live_data/weather\|weather_index' services main.py tools` → no output). **GAP-PURSUE.** |
| W2 | same page, and the direct analogy to C1 | This is the **exact structural twin of the CF Benchmarks index feed the app already built for crypto**: a minute-resolution canonical series that *is* the settlement input, not a prediction of it, with a documented quorum/quality model on top. `services/index_feed/settlement_algebra.py` exists precisely to project a settlement from such a series. The weather equivalent has no counterpart. | **GAP-PURSUE — this is the single clearest "we built it for one category and never asked what the others' version is."** |
| W3 | `get-weather-index.md:7`, `:72-76` | "Minutes where the index quorum failed carry no value and are never returned as points, **so gaps in the series are real gaps**" | A data-plane-completeness property the exchange states explicitly. Any consumer must not interpolate. | Design constraint for W1/W2. |
| W4 | `tags_by_categories` live vs `Series.tags` | taxonomy says 7 tags; real series carry `Heatwaves` (12 series), `Energy`, `KPIs`, `Space`, `Companies` besides | Instance of **X2**/§0. |
| W5 | `get-event-live-data.md` | weather observations do arrive via the event-keyed live-data path | **CAPTURED** (C6). W1 is the *canonical index*; this is the per-event view. Not redundant. |

### 2.4 Economics · Financials · Commodities

| # | Source | Finding | Verdict |
|---|---|---|---|
| E1 | `get-event-forecast-percentile-history.md` | market-implied distribution over a strike ladder | **GAP-PURSUE** — see **X14**. Economics (CPI/payrolls/GDP) and Financials (index levels) are the archetypal ladder events. |
| E2 | `get-series.md:148-156` | Economics/Financials series' `settlement_sources` name the releasing agency and its URL — the release *schedule* is the tradeable event | Persisted (event-level), unread. See **X5**. |
| CM1 | `pyth-value.md:164, 289-291` | Pyth underlyings include `Metal.XAU/USD`, `Metal.XAG/USD` | Commodities has a real-time underlying-price feed the app is not subscribed to (C3/C4). **GAP-PURSUE.** |
| CM2 | `get-event-live-data.md:69-72` | "commodity price timeseries" served event-keyed | **CAPTURED** (C6). |
| CM3 | live `Series.tags` | Commodities' real tags include `Agriculture` (14 series) and `Metals` (19), neither reachable from the taxonomy's 5 entries for the category | Instance of **X2**. |
| E3 | `get-event-candlesticks.md` (`/series/{s}/events/{t}/candlesticks`) | "aggregated data across **all markets** corresponding to an event" — returns `market_tickers` + per-market candlesticks in one call | `services/kalshi/public.py:314-327` only wraps the **per-market** `get_market_candlesticks`. | **GAP-SKIP.** Real, but it is a REST-efficiency win on a path the app barely uses (one dashboard route), not a missing data shape. Note it if E1 ships, since a ladder needs every sibling market's history anyway. |

### 2.5 Politics · Elections · Mentions

| # | Source | Finding | Verdict |
|---|---|---|---|
| P1 **⟲** | full-population pagination, 2026-08-30 — 275 pages × `limit=500` (`get-milestones.md` documents `limit` max 500 and `cursor`) | **Stage 1's "of 200" figures were one page of a 275-page endpoint and are struck; every denominator built on them is void.** The population is **137,200 milestones, 44 types, 17 distinct `category` strings**, spanning `start_date` 2020-01-01 → 2033-02-07 (133,423 past, 3,777 forward of the probe). Categories: Sports 123,521 (90.0 %), Mentions 3,405, **Esports 2,728**, Financials 2,271, Economics 1,854, **Elections 1,118**, Entertainment 825, **Companies 594**, Commodities 320, Finance 205, Science and Technology 150, Politics 78, Climate and Weather 64, Crypto 34, Technology 30, World 2, Science & Technology 1. Plurality type is `tennis_tournament_singles` 49,875 (36.4 %), then `one_off_milestone` 16,054, `basketball_game` 15,564, `soccer_tournament_multi_leg` 15,546, `esports_match` 11,406. | Milestones are **not** Sports-only, by a far wider margin than any partial sample showed — and neither partial sample sized it right: `political_race` is **917**, not 3; `company_report` is **530**, not 1. `get-milestones.md:86-93`'s documented `Esports` category is real (2,728) and still absent from the `/series` category vocabulary. Two vocabulary defects visible only at full scale: the milestone `category` field carries `Financials`/`Finance` and `Science and Technology`/`Science & Technology`/`Technology` as *distinct* strings, and `Companies` appears as a milestone **type** (23) as well as a category — so this field is not safe to join against `Series.category` without normalisation. |
| P2 | same probe | Elections milestone `details` keys: **`candidate_id_mapping`, `candidate_ids`, `state`, `main_game_event_ticker`** — the structural twin of Sports' `home_team_id`/`away_team_id`, i.e. structured-target IDs for candidates. | **GAP-PURSUE**, and it is the same fix as **X6** (structured-target resolution) applied to Elections. |
| P3 **⟲** | `GET /live_data/batch` over **all 137,200** milestone ids (100 per call, `get-multiple-live-data.md`) | **Stage 1's claim here is falsified and withdrawn — and it was the most consequential error in the document, because it was written as durable guidance to future sessions.** The two `not_found` responses it generalised from were n=1 per type; some ids 404 and most do not. Measured over the whole population: `political_race` returns live data on **810 of 917** milestones — 774 of them carrying `candidates`, `winner`, `winners`, `race_call_status`, `reporting_percentage`, `tabulation_status`, `last_updated`, provider `votehub` — and `company_report` on **337 of 530** (`company_name`, `events`, `latest_event`, `next_event`, `quartr_company_id`, `provider`, `status`). Non-Sports live data totals **4,307 payloads**: Esports 2,345, Elections 810, Economics 355, Companies 341, Mentions 292, Entertainment 140, Crypto 21, Financials 3. | **GAP-PURSUE — reopened as a Stage 3 candidate (was GAP-SKIP).** Unauthenticated election-night tabulation and race-call state, over a category holding **1,389 volume-positive series**, is real, free and directly decision-relevant to a whale-signal app; `political_race` is **520 of the 2,869 forward-dated live milestones (18.1 %)** — the largest forward-dated non-Sports type. Size it honestly: the value is episodic and election-calendar-driven, an argument about timing rather than existence. The census also surfaces two live-data families no earlier pass saw at all — **`truflation`** (241 live: `indicator`, `category`, `latest_value`, `timeseries`, `target_date`, `series_key`, on Economics/Crypto events) and **`artist_streams`** (139 live: `timeseries_daily`/`timeseries_weekly`, `current_total`, `period_start`/`period_end`, `target_week_finalized`, on Entertainment events) — both settlement-input series arriving through the milestone path the app already calls, from which it reads exactly one key. See D3. |
| P4 | `Series.additional_prohibitions`, measured | Elections series carry the longest and most specific prohibition lists in the whole corpus (13–22 clauses: election officials, Decision Desk employees, FEC commissioners, registered lobbyists, state legislators on election committees, campaign staff). Politics/Elections are also the categories `CLAUDE.md` flags for legal exposure. | Instance of **X4**, and its strongest case. |
| P5 | live `Series.tags` | Politics observes **31** real tags against the taxonomy's 8 — the worst ratio of any category. Missing: `Trump Agenda`, `Trump Policies`, `Culture war`, `Foreign Elections`, `Primaries`, `Public Health`, `Health Tech`, `Drug Prices`, and more. | Instance of **X2**. |
| M1 | `get-events.md:114-118` + `get-milestones.md` + `targets_and_milestones.md:41-51` | three documented routes to milestones: inline on `get_events` (free), bulk by category (`get_milestones_bulk`, built-and-unused), per-event (`get_milestones_for_event`, the only one wired). | **GAP-PURSUE** — see **X7**/**X8**. |
| MN1 | Mentions | No Mentions-specific endpoint exists in the mirror. The category's real per-series tags are `Earnings` (179), `Politicians` (52), `Sports` (16), `Trump` (11), `Entertainment` (7), `SOTU`, `CEOs`. Config already special-cases three Mentions series in `whale_watcher_kalshi.min_contracts_by_series` (`KXTRUMPSAY`, `KXTRUMPMENTION`, `KXMAMDANIMENTION` at 500). | **GAP-SKIP** as an endpoint question; the `tags` route (X2) is what would generalize those three hand-listed tickers to the whole `Politicians`/`Trump` tag. |

### 2.6 Entertainment · Science and Technology

| # | Source | Finding | Verdict |
|---|---|---|---|
| EN1 | live `Series.tags` | Entertainment observes **49** real tags vs the taxonomy's 27, including per-entity ones the taxonomy could never enumerate: `Morgan Wallen` (42 series), `Taylor Swift`, `Love Island USA`, `Love Island UK`, `Eurovision`, `Cannes`, `Anime Awards`, `Tonys`, `VMA`, `StockX`, `Bezel`. | Instance of **X2**. These are the natural grouping key for "how have whales done on *this artist's* markets" — a question `series_of()`'s ticker-prefix hack cannot answer. |
| EN2 | `Series.additional_prohibitions`, measured | 126 Entertainment series name `Rotten Tomatoes` as a prohibited party — i.e. the settlement source's own staff. | Instance of **X4**. |
| ST1 | Science and Technology | No category-specific endpoint. Real tags: `AI` (98), `Space` (27), `Public Health` (25), `Compute` (18), `AI Indices` (8). | **GAP-SKIP** as an endpoint question; instance of **X2**. |

### 2.7 Categories the app does not configure

`config/settings.yaml`'s **live** value (uncommitted in the primary checkout — note
that `origin/main`'s committed copy still reads `categories: [Sports]`, a real
config/HEAD divergence worth flagging on its own) lists 11 categories. Live `/series`
carries 17.

| Category | Volume-positive series | Signals in `signal_log.db` |
|---|---|---|
| Companies | 90 | 0 |
| World | 87 | 0 |
| Health | 48 | 0 |
| Social | 34 | 0 |
| Transportation | 19 | 0 |
| Education | 1 | 0 |
| *Exotics* | (MVE; 14 rows in `market_catalog`) | 14,501 (14.87 %) via MVE tickers |

**Verdict: GAP-SKIP for the six unconfigured categories — on share of universe, not
on signal count. ⟲** The original justification here ("measured across all 97,541
logged signals, **zero** originate from any of them") was **circular and is withdrawn**:
the category filter at `services/market_watch/catalog_scan.py:337-341` — cited two lines
below as the thing to change — narrows the series universe to `cfg["kalshi"]["categories"]`
*before* discovery runs:

```python
categories = cfg["kalshi"].get("categories")
if categories:
    all_series = [s for s in all_series if s.get("category") in categories]
```

An unconfigured category therefore **cannot** emit a signal. The zero restates the
filter; it is not evidence about the value of widening. It is also drawn from a signal
history that is overwhelmingly the Sports-only era, since the widening to 11 categories
landed hours before this audit.

The non-circular argument is the size of the prize, and the data for it was already in
hand: the six carry **279 volume-positive series between them against 10,073 for the
eleven configured — 2.7 % of a 10,352-series universe** (independently re-fetched
2026-08-30T23:51Z; the 10,351 quoted elsewhere in this document is the same measurement
an hour earlier, one series apart), with `Education` at a single series. That is a real
basis for deprioritising. The filter also lives at
`services/market_watch/discovery_cache.py:234`; widening remains a one-line config change
available whenever it is wanted. Recorded so a future session does not re-derive the
question.

`Exotics` is the exception and is **already handled** — `mve_scan.py` deliberately
runs its own discovery path independent of `kalshi.categories` (issue #268), for the
reason the CHEATSHEET documents: every MVE-producing series reports `volume_fp: "0.00"`,
so `_get_series_cache`'s `volume_fp > 0` filter (`catalog_scan.py:205`) drops it
before category logic ever runs. **CAPTURED.**

---

## 3. What was checked and found genuinely category-neutral

Recorded so the next pass does not re-open them: orders/order-groups/portfolio/
subaccounts/RFQ/block-trade/API-key/FCM/margin-perps/FIX (all account plumbing);
`get-trades`/`public-trades` (`is_block_trade` **is** already consumed —
`services/whalewatchers/kalshi_trade_tape.py:729-734` → `block_trade_factor`);
`get-historical-*` (archive mirrors of live endpoints, category-neutral);
`get-exchange-schedule`/`maintenance_and_pauses` (no category dimension in the
schema); `rate_limits` (per-shard write budgets are the only category-adjacent part,
and that is X9); `orderbook_responses`/`subpenny-pricing` (X12 covers the live part);
`market_settlement`/`fee_rounding` (already in the CHEATSHEET).

---

## 4. Rough directions for Stage 3

Originally three, now four (D4 is the direction the falsified P3 had closed off), ordered
by evidence strength rather than size. Each is a direction, not a design.

**Ranking after the revision: D1 → D2 → D3 → D4; the original three keep their order. ⟲**
Stage 2 recommended demoting
D2 below D3, on the basis that D2's two headline figures collapsed (S3 falsified outright,
S4 surviving only as golf at 20 of 1,200). That recommendation rested on a 1,200-milestone
sample — 0.9 % of the population — which, like Stage 1's 200, missed the type that matters
most: **`esports_match` appears nowhere in Stage 2's twelve-type census**, and it is the
single largest `winner` hole on the exchange (9,385 live payloads). Measured over all
137,200 milestones, D2's defects are **larger** than Stage 1 claimed, not smaller:
**10,706 live milestones (9.5 %) missing `winner`** and **1,962 (1.7 %) missing
`widget_status`**, rising to 13.3 % and 28.4 % on the forward-dated slice, and every
deviating type attaches to a series category the app has configured. D2 stays at #2. What
changes is its *framing*, not its rank — see the rewritten D2 below.

**D1 — A real series-metadata store, replacing the opaque `series_cache.db` blob.**
The single highest ratio of value-already-downloaded to work-required. `Series.tags`,
`additional_prohibitions`, `settlement_sources`, `fee_type`, `frequency`,
`contract_terms_url` are **already on disk for all 10,351 live series** and have zero
readers (X2, X4, X5). Turning that blob into a queryable table (additive, per
`CLAUDE.md`'s schema rule) would in one step: give every signal a real
category **and** subcategory dimension to segment on (replacing `series_of()`'s
ticker-prefix hack, which `terms.md:29` explicitly forbids); make the
`min_contracts_by_series` hand-list generalizable to a tag; and put the
category-level legal-risk data `CLAUDE.md` names as an open gap in front of a human
for the first time. Natural companions: send `min_updated_ts` and
`include_product_metadata` on the `/series` fetch (X3), and stop writing the constant
`category_tags` onto every event (X1).

**⟲ Added on revision — the fee-change endpoints, missed by the original sweep.**
`get-series-fee-changes.md` (`GET /series/fee_changes`, params `series_ticker` and
`show_historical`, i.e. it exposes scheduled *and* historical changes, not just the
current value) and `get-event-fee-changes.md` (`GET /events/fee_changes`) are both in
`llms.txt` (`:30`, `:45`) and neither has a code path —
`grep -rn fee_changes services main.py tools tests` → no output. They are the REST
complement to X10's pushed `event_fee_update`, and `get-event-fee-changes.md:7` states
the semantics X10's row needs and does not cite: *"Event fees are an override layered on
top of the parent series' fee structure. If `fee_type_override` and
`fee_multiplier_override` are null, that indicates the override is cleared."* This is
category-*relevant* rather than category-specific — `get-series.md:172-184` documents
`fee_type` values that are structurally category-shaped, with
`quadratic_with_combo_maker_fees` as the combo variant, and the CHEATSHEET already
carries a shipped bug about combo maker-fee exemption. A modest addition, and it belongs
here because D1 already proposes carrying `fee_type` forward off the series object.

**D2 ⟲ — Two single-key assumptions applied to a per-type-variable payload. The
deviating types are not the ones this audit originally named, and most of the biggest
are not Sports at all.**
Stage 1's design instinct was right and is kept verbatim; only its sizing and its
"stop treating Sports as one shape" framing were artifacts of the one-page sample.
`live_status.py:169` reads `details.widget_status` and `catalog_scan.py:117` reads
`details.winner` — two single-key assumptions applied to a `details` object the exchange
documents as "flexible JSON [that] varies by milestone type"
(`targets_and_milestones.md:28`). Measured over all 137,200 milestones / 112,501 live
payloads:

| hole | types | live milestones | share of live | forward-dated |
|---|---|---|---|---|
| no `details.winner` | `esports_match` 9,385 · `one_off_milestone` 420 · `company_report` 337 · `truflation` 241 · `golf_tournament` 169 · `artist_streams` 139 · `kpis` 14 · `tv_views` 1 | **10,706** | 9.5 % | 382 (13.3 %) |
| no `details.widget_status` | `political_race` 810 · `one_off_milestone` 420 · `company_report` 337 · `truflation` 241 · `artist_streams` 139 · `kpis` 14 · `tv_views` 1 | **1,962** | 1.7 % | 814 (28.4 %) |

Tennis — Stage 1's headline — has neither hole (49,764/49,764 on both keys). The
single largest one is **`esports_match`**, whose events resolve to **Sports** series, so
`propagate_milestone_winners` is silently unable to resolve any of the 9,385 today; the
next largest, `political_race`/`company_report`/`truflation`/`artist_streams`, sit in
Elections, Mentions, Economics and Entertainment — all configured categories, none of
them Sports. The right shape is what Stage 1 said: a per-type extractor with an explicit
"this type has no winner/status key" branch, plus a counter so the next unknown type
surfaces as a metric instead of silence — 44 types exist today, 30 return live data and
14 return none, so the unknown-type case is the normal case, not the edge. And the
per-type answers are present, not absent: `esports_match` has `home_score`/`away_score`
+ `is_live`, `golf_tournament` has `leaderboard`, tennis has `round_winners`,
`political_race` has `race_call_status`/`winners`. Cheap adjacent wins in the same area:
`get_events(with_milestones=True)` (X7) removes the per-event milestone call entirely,
and either that or `get_milestones_bulk` (X8) retires dead code — and note that X8's
docstring figure ("one bulk call returned 200 milestones") is one page of 275, so the
REST-cost argument for it is stronger than stated. Structured-target resolution (X6)
belongs here too — it is what makes `custom_strike`'s UUIDs mean something, and
`catalog_scan.py:144`'s substring match against a UUID is a demonstrable no-op today.

**D3 — Ask each category what its version of the crypto index feed is.**
The app built a genuinely excellent settlement-grade data path for exactly one
category (`services/index_feed/`, C1/C2) and never asked the same question elsewhere.
Two concrete answers exist: **Climate and Weather** has `/live_data/weather/{city}`
(W1/W2) — same shape, same "the series *is* the settlement input" property, same
need for a `settlement_algebra` projection, plus an explicit quorum/QC model; and
**Commodities** has Pyth `Metal.XAU/USD`/`Metal.XAG/USD` (C5/CM1), reachable by
flipping `index_feed.underlying_tickers` off empty and adding the `underlying_list`
discovery call the CF Benchmarks path already has (C3/C4). A third, higher-ceiling
and lower-certainty answer for the ladder categories is the forecast-percentile
history (X14/E1) — but verify its authentication requirement first, since it is the
only market-data endpoint in the mirror that declares one.

**⟲ Added on revision — two more answers, already arriving on a path the app calls.**
The full-population live-data census (P3) shows the milestone live-data endpoint is
*itself* a cross-category settlement-input feed, and the app reads exactly one key out of
it. **Economics/Crypto**: `truflation` milestones (244 population, 241 live) carry
`indicator`, `latest_value`, `target_date`, `series_key` and a `timeseries` — an inflation
index in the same shape as the CF Benchmarks series. **Entertainment**: `artist_streams`
(675 / 139 live) carry `timeseries_daily`, `timeseries_weekly`, `current_total`,
`period_start`/`period_end` and `target_week_finalized` — the streaming counts that
series such as `KXARTISTSTREAMSU` ("Will artist have more streams this week?") settle
against; 242 series carry "stream" in ticker or title on the same `/series` fetch, most
of them Entertainment, so treat that as an upper bound on the addressable set rather than
a count. Neither of the two needs a new endpoint or a new
subscription; both need D2's per-type extractor to stop discarding everything that is not
`widget_status`. That makes D2 a prerequisite for part of D3 rather than a competitor
to it.

**⟲ D4 (new, from the P3 reversal) — Elections milestone live data.** P3 previously told
the next session not to build this. It is real: `race_call_status`,
`reporting_percentage`, `tabulation_status`, `candidates`, `winner`, `winners`,
unauthenticated, on 810 of 917 `political_race` milestones over a category holding 1,389
volume-positive series, and **18.1 % of all forward-dated live milestones**. Ranked last
of the four only because its value is episodic and election-calendar-driven — a timing
argument, not an existence one. It shares P2's structured-candidate-ID shape, so it lands
naturally alongside X6.

**Deliberately not proposed:** widening `kalshi.categories` (§2.7 ⟲ — 2.7 % of the
volume-positive series universe; note the original "zero signals" justification was
circular and has been withdrawn there),
game-stats/player-stats (S5/S6 — already assessed and declined),
and any change to `exchange_index` handling beyond persistence (X9) — the collateral
preallocation it implies is a Program 3+ concern and `CLAUDE.md` says not to
prioritize that over paper-mode correctness.

---

## Appendix — evidence provenance

- `docs/kalshi/` mirror, 241 pages, synced by `tools/kalshi_docs_sync`
  (`docs/kalshi/README.md`).
- `docs/kalshi/category_metadata.json` — repo fixture, `fetched_at`
  2026-08-12T04:02:03Z. Superseded for this audit by a live re-fetch.
- Live unauthenticated GETs against `https://external-api.kalshi.com/trade-api/v2`,
  2026-08-31 UTC: `/search/tags_by_categories`, `/milestones?limit=200`,
  `/live_data/milestone/{id}` × 9 (one per observed milestone type). **⟲ The
  `/milestones?limit=200` page and the nine single-instance `/live_data/milestone/{id}`
  probes are the sampling defect Stage 2 caught; every finding that rested on them (S3,
  S4, P1, P3) has been re-derived from the full-population census below and their
  original numbers are void.**
- **⟲ Full-population milestone census, 2026-08-30 18:45–19:0x CDT
  (2026-08-30T23:45Z–2026-08-31T00:0xZ), unauthenticated:** `GET /milestones?limit=500`
  paginated by `cursor` to exhaustion — **275 pages, 137,200 milestones, 137,200 unique
  ids**, `start_date` 2020-01-01 → 2033-02-07 — then `GET /live_data/batch` with 100
  `milestone_ids` per call over **every** one of those ids (1,372 calls), yielding
  **112,501 live-data payloads**. Per-type `details`-key tallies are counted across the
  whole census, not sampled. Type→category attribution spot-checked along the documented
  `event_ticker → event.series_ticker → Series.category` chain (`GET /events/{ticker}`,
  24 events per deviating type). Also re-fetched: `GET /series?include_volume=true`
  (13,632 series, 10,352 volume-positive) for §2.7's replacement figure.
- Read-only queries against the running app's own stores, 2026-08-30/31:
  `data/series_cache.db` (10,351 series, `fetched_at` 2026-08-30T22:54:27Z),
  `data/market_catalog.db` (`markets` schema + category counts),
  `data/signal_log.db` (97,541 signals), `data/title_cache.db` (schema).
  No writes; the app was running throughout.
- `docs/kalshi/markets.json` — repo fixture of 149 real market objects, used for
  `strike_type`/`custom_strike`/`price_level_structure` distributions.
