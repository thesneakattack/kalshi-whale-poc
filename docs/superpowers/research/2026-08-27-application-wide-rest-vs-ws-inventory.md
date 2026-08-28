# Application-wide REST-vs-WebSocket inventory

**Date:** 2026-08-27. **Status:** inventory only — no architecture, fix, or priority
ranking. Produced in response to a direct standing instruction (originally 2026-08-15,
restated 2026-08-27): this application should be decoupled from REST polling as its
architecture, in favor of WebSocket data, with REST used only to verify WS data at
decision-execution time or to fill genuine gaps WS can't cover — and a direct
correction that three prior passes (2026-08-15, 2026-08-17, 2026-08-23) each claimed to
address this but, checked against current code, only ever touched position management,
not "the entire application."

This document does two things the prior passes did not: (1) enumerates every one of
Kalshi's 12 documented WebSocket channels and states, with a citation, whether this app
subscribes to each one at all; (2) sweeps every REST call site in the application (not
just `main.py`/`trading_loop`) and states, per site, whether a WS channel carries
equivalent-or-sufficient data and whether that channel is actually wired to reduce this
specific call.

Every claim below cites a `docs/kalshi/<file>.md` location for what a channel documents
carrying, and a `services/<file>.py:<line>` (or `main.py:<line>`) location for what the
code currently does. Where a determination could not be made from static reading alone,
that is stated explicitly rather than guessed.

## 0. What already exists and is reused here, not re-derived

- `docs/superpowers/research/2026-08-25-realtime-data-plane-baseline.md` §3 ("Exact
  current REST demand graph") — the starting REST-call inventory. Every row from that
  table appears in §2 below; none dropped. That document did not attempt WS-equivalence
  classification per row — that is this document's contribution.
- `docs/kalshi/CHEATSHEET.md` — several entries reused directly, most importantly "Why
  don't whale trades on most markets ever reach the app?" (exchange-wide `trade`
  subscription), "Which `ticker`-channel fields does the app keep, and which did it
  drop?" (field-level `ticker` channel gap, `orderbook_delta` flagged there as unused),
  and "How do you subscribe to the WHOLE exchange, not just a watchlist?" (channel
  scoping rules).
- `docs/superpowers/research/2026-08-25-realtime-data-plane-known-findings.md` §H12 —
  the position-management-specific finding (three prior audits, only one real
  inversion: settlement). Cited, not restated, wherever this document's own findings
  overlap with it. This document's job was everything H12's own scope did not cover —
  §3.2 and §3.3 below are the two genuinely new findings this sweep produced beyond
  H12's scope.
- Live confirmation (2026-08-27, `GET /api/health/pipeline` via
  `https://kalshi-whale-poc.ddev.site:8443`): `trade_stream: {enabled: true, connected:
  true, mode: "stream"}`, `ingest.exchange_wide: true` — the exchange-wide/streaming
  configuration this document assumes as "current live state" is confirmed live, not
  inferred from config defaults alone.

## 1. The complete Kalshi WebSocket channel list

Verified complete against `docs/kalshi/websocket-connection.md`'s own AsyncAPI
`channels` enum (lines 94–107), not trusted from memory or from the task's own seed
list: the 12 channels below are exactly and exhaustively what that enum contains — no
channel is missing from the list this document was seeded with, and none is renamed.
(Two further channel-shaped things exist in the mirror — `list_subscriptions`, a
command/reply, not a data channel; and the margin/FIX product families under
`docs/kalshi/margin-ws/` and `docs/kalshi/fix/` — excluded here as out of scope: this
app trades the standard, non-margin Predictions product only, confirmed by
`services/app_state.py`'s `KalshiStreamGateway` construction using the standard
`wss://external-api-ws.kalshi.com/trade-api/ws/v2` URL,
`services/kalshi/websocket.py:76`.)

Subscription status is verified by grepping every `KalshiStreamGateway(` construction
site in the app (`grep -rn 'KalshiStreamGateway(' --include='*.py' .` outside
`tests/`) — there are exactly two, both in `services/app_state.py:129` (`trade_stream`)
and `services/app_state.py:153` (`index_stream`) — and by reading every `channels`
value ever sent in `services/kalshi/websocket.py`'s `_send`/`run`/`_sync_subscriptions`
(lines 409, 915, 935, 949, 955, 965, 987).

| # | Channel | What it documents carrying | Doc citation | Subscribed by this app? |
|---|---|---|---|---|
| 1 | `trade` | Every executed public trade (ticker, side, price, size, timestamps) | `public-trades.md` lines 5–14 ("market specification optional... omit to receive all trades") | **Yes** — exchange-wide, no `market_tickers` filter (`services/kalshi/websocket.py:915-923`, gated by `config/settings.yaml`'s `kalshi.trade_stream_exchange_wide`, live-confirmed `true`) |
| 2 | `ticker` | Price/volume/open-interest updates per market | `market-ticker.md` lines 5–15 | **Yes** — watchlist-scoped only, by design (`services/kalshi/websocket.py:960-968`; scoping deliberately kept narrow per that file's own comment, lines 144-148, to avoid a genuine exchange-wide firehose) |
| 3 | `orderbook_delta` | Snapshot then incremental yes/no price-level depth (`yes_dollars_fp`/`no_dollars_fp` arrays on snapshot; `price_dollars`/`delta_fp`/`side` on delta) | `orderbook-updates.md` lines 5–19 (requirements), 69–148 (snapshot schema), 272–355 (delta schema) | **No** — zero references to `"orderbook_delta"` anywhere in application code outside this doc and `docs/kalshi/`. Already flagged unused in `docs/kalshi/CHEATSHEET.md`'s "How do you subscribe to the WHOLE exchange" entry. |
| 4 | `fill` | Your own order fills | `user-fills.md` lines 5–15 | **Yes** — account-wide (`services/kalshi/websocket.py:406-410`) |
| 5 | `market_positions` | Your own position changes | `market-positions.md` lines 5–18 | **Yes** — account-wide, same subscribe call as `fill` (`services/kalshi/websocket.py:406-410`; subscription channel name is plural `market_positions`, per-message `type` is singular `market_position` — see CHEATSHEET's "market_position or market_positions" entry) |
| 6 | `market_lifecycle_v2` | Market/event creation, (de)activation, close-date changes, determination, settlement, price-level-structure changes, metadata updates — 8 documented event types | `market-and-event-lifecycle.md` lines 5–14 | **Yes** — exchange-wide, config-gated (`services/kalshi/websocket.py:931-937`, `config/settings.yaml`'s `kalshi.market_lifecycle_stream_enabled`, default `true`) |
| 7 | `multivariate_market_lifecycle` | Same lifecycle shape as #6, scoped to multivariate (combo) events only | `multivariate-market-and-event-lifecycle.md` lines 5–16 | **No** — not applicable, not merely unused: zero references to `multivariate`/combo-market handling anywhere in application code (`grep -rln 'multivariate' services main.py` outside `docs/kalshi/` and `tests/` returns nothing). This app never places or tracks multivariate/combo orders. |
| 8 | `communications` | RFQ/quote lifecycle events | `communications.md` lines 5–17 | **No** — not applicable: zero references to RFQ/quote handling anywhere in application code (`grep -rln 'rfq\|RFQ' services main.py` outside docs/tests returns nothing). |
| 9 | `order_group_updates` | Order-group lifecycle/limit events | `order-group-updates.md` lines 5–14 | **No** — not applicable: zero references to order groups anywhere in application code. |
| 10 | `user_orders` | Your own order created/updated/canceled events | `user-orders.md` lines 5–15 | **No** — genuinely unused despite being applicable (this app does place orders, gated behind `trading_enabled`). See §3.6. |
| 11 | `cfbenchmarks_value` | Real-time CF Benchmarks index values (what several crypto series literally settle against) | `cfbenchmarks-value.md` lines 5–19 | **Yes** — live-active: `config/settings.yaml`'s `index_feed.index_ids: [BRTI, ETHUSD_RTI]` is non-empty, so `services/kalshi/websocket.py:944-950` actually sends the subscribe |
| 12 | `pyth_value` | Real-time Pyth prices for configured underlying tickers | `pyth-value.md` lines 5–18 | **Wired but currently inactive** — the code path exists (`services/kalshi/websocket.py:951-957`) but `config/settings.yaml`'s `index_feed.underlying_tickers: []` is empty, so the `if self.underlying_tickers:` guard never fires the subscribe. Not a gap against a REST call (no REST call this app makes maps to Pyth data) — recorded here only for subscription-status completeness. |

Net: **7 of 12 channels subscribed** (5 of those live-active on the default two
connections; `pyth_value` code-wired but currently inactive by config), **3 of 12 not
applicable** to anything this app does (`multivariate_market_lifecycle`,
`communications`, `order_group_updates`), **2 of 12 genuinely unused despite being
applicable** (`orderbook_delta`, `user_orders`) — both discussed as real gaps in §3.

## 2. REST call-site inventory

Extends the baseline doc's table (same rows retained; new columns added; several rows
split where the baseline collapsed multiple distinct REST-calling functions into one
line, e.g. `catalog_scan`). Swept via `grep -rn` for every `.get_<x>(`/`create_order`/
`cancel_order` call against `services/kalshi/public.py`, `account.py`,
`account_client.py`, `orders.py` across the whole of `services/`, `main.py`, and
`tools/` — not just `main.py`/`trading_loop`.

Legend for the added columns: **WS equiv?** — does a subscribed-or-subscribable
channel carry the same or sufficient data, per §1, with a doc citation; **Used to
reduce this call?** — is that channel actually wired to reduce/replace this specific
REST call today; **Gap?** — Yes only when a WS equivalent exists, is (or could
trivially be) subscribed, and is *not* wired to reduce this call.

### 2.1 Whale-critical

| Caller | Endpoint(s) | Cadence | WS equiv? | Used to reduce this call? | Gap? |
|---|---|---|---|---|---|
| `kalshi_trade_tape._resolve_unknown_markets` (`services/whalewatchers/kalshi_trade_tape.py:461`) | `get_markets_by_tickers` | whale-sized off-list print, cache miss | No — no channel serves "look up one ticker's full market object on demand"; `ticker`/`trade` only carry data for markets already known to the caller | n/a | **No** — genuinely REST-only |
| `candidate_retry.run_pending` (`services/candidate_retry.py:134`) | `get_markets_by_tickers` | once/tick, only for H4-unresolved candidates pending retry | Same as above | n/a | **No** |
| `whale_stream` settled handler (`services/whale_stream/whale_stream_handlers.py:501-506`) | `get_market` | once per `settled` lifecycle event (~0.06/s historical) | Partial — `market_lifecycle_v2`'s `settled` event is exactly what *triggers* this call, but `settled` carries no `result` field at all (`market-and-event-lifecycle.md`, confirmed in CHEATSHEET's "Does the lifecycle `settled` WS message carry the market's result?" entry) | Yes, already — this is a deliberate REST-as-verification-at-decision-time read, not a redundant poll | **No** — this is the pattern the standing instruction asks for, already implemented for outcome grading |

### 2.2 Position/risk-critical (tick-cadence, `main.py::trading_loop`)

| Caller | Endpoint(s) | Cadence | WS equiv? | Used to reduce this call? | Gap? |
|---|---|---|---|---|---|
| `_fetch_markets` → `_cached_market_fetch` (`services/market_watch/market_fetch.py:54`, `discovery_cache.py:95-140`) | `get_markets_by_tickers` | every 6 s tick call site; internally cached 300 s TTL per ticker (`_PINNED_MARKET_REFRESH_SEC`) | Partial — `ticker` supplies live price (already WS-overlaid, see next row), `market_lifecycle_v2` supplies close-time/status changes for 3 of 8 event types (see §2.3/§3.4). No channel carries the *other* structural fields this call fetches (`title`/`volume_24h_fp`/`event_ticker`/`strike_type`/`occurrence_datetime`/`can_close_early`/`expected_expiration_time` — none of the 12 channels document any of these) | Partially — price is WS-overlaid at the end of `_fetch_markets` (line 282-293); structural fields have no WS source at all | **No** for the structural-field fetch itself (genuinely necessary, and already decoupled from 6 s tick cadence via the 300 s cache) |
| `state["latest_prices"]`/`state["latest_asks"]` assignment (`main.py:782-793`) | (consumes the `_fetch_markets` REST result directly, not a separate call) | every 6 s tick, unconditional | Yes — `ticker` channel already pushes `yes_bid_dollars`/`yes_ask_dollars` continuously via `_process_stream_ticker` | **No** — this exact line **wholesale-replaces** the dict from that tick's REST result every 6 s, discarding whatever fresher WS ticker pushes arrived in between ticks | **Yes — already documented.** This is H12's own finding (`docs/superpowers/research/2026-08-25-realtime-data-plane-known-findings.md` §H12, "the mechanism that never got touched, three audits later"). Cited here, not re-derived. |
| `_fetch_account_snapshot` (`services/position/account_positions.py:154-188`) | `get_balance`, `get_positions`, `get_fills(limit=50)` | every tick, 20 s interval cache (`_ACCOUNT_SNAPSHOT_REFRESH_SEC`) | Yes — `fill` and `market_positions` channels are already subscribed (§1 #4/#5) and already wired: `_process_stream_fill`/`_process_stream_position` (`services/whale_stream/whale_stream_handlers.py:293-376`) write real WS-sourced updates into `state["account"]` | **Partially, and explicitly provisional by the code's own admission.** `_process_stream_fill`'s docstring (lines 317-323): "`_fetch_account_snapshot`'s own periodic REST poll... will naturally reconcile/overwrite this with verified data regardless... a WS-sourced fill only ever needs to survive until the next reconcile." `_fetch_account_snapshot`'s own comment (`account_positions.py:137-150`) gives the reason: fill/position WS parsing has never been verified against a real live message because `trading_enabled` has always been `false`, so a time-based REST safety net was chosen deliberately over trusting unverified WS parsing for real account money. | **Yes — new finding, not in H12's scope** (H12 covers only `latest_prices`/market metadata, not account fill/position state). Same "WS supplements, REST still overwrites" shape as the `latest_prices` bug, on a 20 s cycle instead of 6 s. Currently low-impact in practice (inert while `trading_enabled: false`, since no real fill can occur), but the shape is real and would apply the moment trading is enabled. |
| `_fetch_exchange_status` (`services/market_watch/live_status.py:225-233`) | `get_exchange_status` | every tick | No — confirmed by grep: zero `websocket`/`WebSocket`/`channel` mentions anywhere in `docs/kalshi/get-exchange-status.md` or `docs/kalshi/maintenance_and_pauses.md` | n/a | **No** — genuinely REST-only |
| `_fetch_trade_tape`/`_fetch_trades_for_ticker` (`services/whale_stream/whale_stream_handlers.py:538-608`) | `get_trades` (paged) | every tick, **only when NOT in streaming mode** (`main.py:660-674`, gated by `_streaming_trade_tape_enabled()`, `whale_stream_handlers.py:55-60`) | Yes — `trade` channel | **Yes, already, fully.** When streaming is active (live-confirmed default: `mode: "stream"`), this REST call is never invoked at all — `main.py`'s `else` branch is dead code on the live path. | **No — already a complete, shipped inversion.** Worth naming explicitly as a positive precedent: this is exactly the target architecture (REST branch fully replaced, not merely supplemented), already live for the highest-volume data class in the app. |

### 2.3 Background

| Caller | Endpoint(s) | Cadence | WS equiv? | Used to reduce this call? | Gap? |
|---|---|---|---|---|---|
| `_fetch_category_metadata` (`services/market_watch/discovery_cache.py:54-92`) | `get_tags_for_series_categories`, `get_filters_for_sports` | 1 h TTL | No — category/filter taxonomy is not pushed by any channel | n/a | **No** |
| `_refresh_discovery_cache`/`_refresh_discovery_cache_background` (`discovery_cache.py:196-321`) | `get_markets_by_tickers` (final confirmation pass before publishing the watchlist selection) | ≥300 s | Partial — the same status/close-time data this confirms is, for tickers `market_lifecycle_v2` already covers, potentially already fresher via `market_catalog.apply_lifecycle_update` (see next row) | This call still runs unconditionally as a deliberate final correctness gate ("confirmed no longer tradeable — drop before it ever reaches the watchlist", `discovery_cache.py:288`) rather than trusting the catalog's possibly-stale copy | **No** — this reads as intentional REST-as-verification-before-a-decision (which markets enter the watchlist), matching the target pattern, at a low cadence (≥300 s). See §4's first ambiguity note. |
| `propagate_milestone_winners` (`services/market_watch/catalog_scan.py:29-184`) | `get_milestones_for_event`, `get_live_datas`, `get_markets_by_tickers` | per event, 60 s repoll cache once resolved | No — milestone/live-data has no WS channel (confirmed: zero websocket mentions in `get-milestone.md`, `get-milestones.md`, `get-live-data-with-type.md`, `get-multiple-live-data.md`, `live-data-get-live-data.md`, `get-event-live-data.md`, `get-game-stats.md`) | n/a | **No** |
| `_get_series_cache` (`catalog_scan.py:191-210`) | `get_series_list` | 3600 s TTL | No — no channel lists series | n/a | **No** |
| `_scan_catalog_batch`/`_scan_catalog_batch_background` (`catalog_scan.py:317-427`) | `get_markets(series_ticker=…)` ×10/batch | ≥15 s kickoff (`_CATALOG_SCAN_MIN_INTERVAL_SEC`) | **Yes, and currently unexploited.** `market_lifecycle_v2`'s `created`/`activated` event types are exchange-wide and already received (`services/app_state.py`'s `lifecycle_stream_stats`; live cumulative count `created: 2,498` per the 2026-08-25 baseline). | **No.** `_process_stream_lifecycle` (`whale_stream_handlers.py:455-518`) only branches on 3 of the 8 documented event types (`close_date_updated`, `determined`, `settled`); `created`, `activated`, `deactivated`, `metadata_updated`, `price_level_structure_updated` are counted into `lifecycle_stream_stats.events_by_type` and otherwise discarded — never applied to `market_catalog`. | **Yes — new finding.** A WS signal already tells this app the instant a new market/event is created or (de)activated exchange-wide; the catalog only learns of it on this function's own periodic REST rescan of each series regardless. |
| `_fetch_event_titles` (`services/market_watch/event_metadata.py:17-90`) | `get_events` | cache-once per event | No — event title/metadata not pushed | n/a | **No** |
| `_fetch_event_live_data` (`event_metadata.py:157-` decorator line) | `get_event_live_data` | 60 s repoll per event | No — confirmed, no websocket mention | n/a | **No** |
| `_fetch_live_status` (`services/market_watch/live_status.py:61-222`) | `get_milestones_for_event`, `get_live_datas` | 5 min repoll, ≤10/tick | No — confirmed, no websocket mention | n/a | **No** |
| `event_schedule._maybe_resolve_event_schedules` (`services/market_events/event_schedule.py:245`) | `get_milestones_for_event` | ≥30 s kickoff, 12/24 h per-event retry | No | n/a | **No** |
| `_check_signal_resolutions`/`_check_signal_resolutions_background` (`main.py:170-261`) | `get_markets_by_tickers` | 30 s interval, batch 200 | **Yes, and currently unexploited — the clearest gap found in this sweep.** `market_lifecycle_v2`'s `determined`/`settled` events are exchange-wide, already flowing, and already drive outcome resolution for `market_history`, `settlement_edge`, `market_analyst_agent`, and `candidate_log` for the exact same tickers, via `_process_stream_lifecycle` (`whale_stream_handlers.py:484-518`). | **No.** `signal_log.mark_resolved` is called from exactly one place in the whole application: this REST-polling loop. Confirmed by `grep -rln mark_resolved` across `services/` and `main.py` — only `main.py` (the caller) and `services/signal_log.py` (the definition) reference it; zero references anywhere in `whale_stream_handlers.py` or any other WS-path file. | **Yes — new finding, the strongest one in this document.** A 4th store (`signal_log`) sits on the exact same lifecycle event stream that already resolves 4 other stores for the same tickers, and is the one left 100% REST-poll-dependent. |

### 2.4 Interactive / on-demand (human-triggered, not tick-cadence)

| Caller | Endpoint(s) | WS equiv? | Used to reduce this call? | Gap? |
|---|---|---|---|---|
| `market_catalog/routes.py:37` | `get_orderbook` | **Yes** — `orderbook_delta` (§1 #3) | No — `orderbook_delta` is not subscribed anywhere in the app | **Yes, but low-priority (interactive only, not tick-cadence).** Distinct in kind from every other gap in this document: `orderbook_delta` would supply genuinely new data (resting depth) this app has *never* had access to on a live basis — CHEATSHEET's ticker-channel-fields entry already notes `yes_bid_size_fp`/`yes_ask_size_fp` are the "only way to ask whether the book could actually have filled an entry at the quoted price" and are currently unavailable anywhere. |
| `market_catalog/routes.py:61,141` | `get_event` | No | n/a | **No** |
| `market_catalog/routes.py:67` | `get_candlesticks` | No — derived/historical aggregation, not pushed by any channel | n/a | **No** |
| `market_catalog/routes.py:86` | `get_trades` (bounded, ticker-scoped, on-demand page) | Yes — `trade` | Reasonable REST use for a bounded historical page on human request, not a live-feed need | **No** |
| `market_catalog/routes.py:133` | `get_market` | No (structural fields, as §2.2) | n/a | **No** |
| `market_catalog/routes.py:241,253` → `selection.candidate_markets`/`top_volume_markets` | `get_markets(series_ticker=…)` | No | Search-fallback path, only reached when the catalog itself has no results | **No** |
| `market_events/event_inspector.py:49,65,84,90,102` | `get_market`, `get_event`, `get_milestones_for_event`, `get_live_data`, `get_markets` | No for all five | n/a | **No** |
| `diagnostics/routes.py:49-59` `get_diagnostics_coverage` | `get_trades` (exchange-wide, 1-2 pages) | Yes — `trade` | **Deliberately used to verify WS capture, not replace it** — this route's whole purpose is measuring how much real exchange-wide whale flow the WS path never sees | **No — this is prior art already matching the target pattern**, see §3.7 |
| `diagnostics/trade_capture_reconciliation.py:124` | `get_trades` | Yes — `trade` | Same as above — I4's REST-vs-WS trade_id reconciliation check | **No — same prior-art note** |
| `position/routes.py:42` | `get_orders` | **Yes** — `user_orders` (§1 #10) | No — `user_orders` not subscribed | **Yes, but low-priority.** Real gap in principle; currently low-impact since `trading_enabled: false` means no real order can exist to track yet. |
| `analytics/routes.py:187,215,279` → `market_analyst_orchestrator.py:74,80` | `get_market`, `get_event` | No | n/a | **No** |

### 2.5 Account-level, not tick-cadence

| Caller | Endpoint(s) | WS equiv? | Gap? |
|---|---|---|---|
| `services/execution.py:68,79` | `account.get_positions()` (pre-order-placement check), `account.create_order(...)` | No WS command exists for order placement at all — see §4 | **No** — genuinely REST-only by protocol design |
| `services/kalshi/orders.py:130` | `cancel_order` | Same — no WS command | **No** |
| `tools/kalshi_rate_limit_probe.py` | Various (`get_api_limits`, `get_endpoint_costs`, `get_market`, `get_markets`, `get_markets_by_tickers`) | n/a — this is a standalone diagnostic tool (`tools/`, per CLAUDE.md's workflow/tooling separation), not application code | n/a, out of scope |

**Defined but never called anywhere in the application:** `get_milestones_bulk`
(`services/kalshi/public.py:239`) and `get_game_stats` — both exist on
`KalshiPublicGateway` with zero call sites in `services/`, `main.py`, or `tools/`
outside their own definitions and `tests/`. Not a REST-vs-WS question (they're unused
REST capability, not a live call this document classifies), noted here only for
completeness since the task asked for every call site the grep patterns could surface.

## 3. Real gaps — WS carries equivalent data, not wired to reduce this REST call

Ordered by cost/frequency, tick-cadence first (matching the task's instruction — that's
where the real payoff is).

### 3.1 `state["latest_prices"]`/`state["latest_asks"]` wholesale REST overwrite every 6 s tick
**Already documented — not new.** `main.py:782-793`. Cited in full in
`docs/superpowers/research/2026-08-25-realtime-data-plane-known-findings.md` §H12.
Included here only so this document's "real gaps" section is a complete picture; no new
evidence added beyond confirming the cited lines are unchanged.

### 3.2 `_fetch_account_snapshot`'s 20 s REST poll continues to "reconcile/overwrite" WS-sourced fill/position state regardless of freshness
**New finding, this sweep.** `services/position/account_positions.py:137-188`,
`services/whale_stream/whale_stream_handlers.py:293-376`. Same shape as §3.1 — WS
supplements, REST still periodically overwrites — but for account fill/position state,
not price. The code's own comments are explicit that this is a deliberate, reasoned
choice (unverified WS parsing for real-money account state, given `trading_enabled` has
never been on long enough to observe a real fill), not an oversight — recorded as a gap
in the sense the task asked for ("WS equivalent exists, not wired to reduce this call"),
with the caveat that its own rationale is on record and currently low-impact
(`trading_enabled: false`).

### 3.3 `market_lifecycle_v2`'s `determined`/`settled` events resolve 4 stores but not `signal_log`
**New finding, this sweep — the clearest gap found.** `main.py:170-261`
(`_check_signal_resolutions`), `services/whale_stream/whale_stream_handlers.py:484-518`
(`_process_stream_lifecycle`). The exact same lifecycle events, for the exact same
tickers, already resolve `market_history`, `settlement_edge`, `market_analyst_agent`,
and `candidate_log` the instant they arrive — `signal_log.mark_resolved` has exactly one
caller in the entire codebase, the 30 s/200-batch REST poll. Every settled ticker this
app was watching when it settled is, by construction, already known to the lifecycle
handler at that moment; `signal_log`'s own resolution for that same ticker currently
waits for the next 30 s REST batch regardless.

### 3.4 5 of 8 `market_lifecycle_v2` event types received over WS but never applied anywhere
**New finding, this sweep.** `services/whale_stream/whale_stream_handlers.py:455-518`.
`created`, `activated`, `deactivated`, `metadata_updated`, `price_level_structure_updated`
are counted into `lifecycle_stream_stats.events_by_type` (so their arrival is visible on
`/api/state`) and then discarded. `catalog_scan._scan_catalog_batch`
(`services/market_watch/catalog_scan.py:317-384`) is the only path by which a newly
created market/event enters `market_catalog`, on its own ≥15 s-kickoff/rate-limited
per-series REST rescan — even though the WS channel already announced the market's
existence, exchange-wide, the moment it was created.

### 3.5 `orderbook_delta` entirely unsubscribed
**Restates CHEATSHEET's existing note, with the REST call site now identified
precisely.** `market_catalog/routes.py:37` (`get_orderbook`, human-triggered,
interactive-tier only). Lower priority than 3.1-3.4 since it's not tick-cadence, but
distinct in kind: this is the one channel in the whole inventory that would add data
this app has never had (resting book depth), not merely a faster path to data it
already gets via REST.

### 3.6 `user_orders` entirely unsubscribed
`services/position/routes.py:42` (`get_orders`, human-triggered order-history panel).
Real gap in principle; near-zero live impact today since `kalshi_account.trading_enabled`
is `false`, so no real order exists yet for the channel to report on.

### 3.7 Positive precedent already in place — REST used only to verify WS data
Not a gap; recorded because it is direct evidence the target pattern already exists
somewhere in this codebase and can be pointed to as prior art. `diagnostics/routes.py`'s
`get_diagnostics_coverage` (lines 49-59) and `diagnostics/trade_capture_reconciliation.py`
(line 124, the I4 reconciliation check) both call REST `get_trades` specifically to
measure how much real exchange-wide flow the `trade` WS channel is missing — REST as a
verification/audit layer over WS, not a replacement for it. The whale-critical settled
handler (§2.1, third row) is the tick-cadence equivalent of the same pattern.

## 4. Genuinely REST-only — no WS equivalent, confirmed against docs

Every entry below was checked directly against the relevant `docs/kalshi/` page(s) for
an explicit WebSocket cross-reference (grepped for `websocket`/`WebSocket`/`channel`
mentions in each page) rather than assumed from the absence of a channel name resembling
the endpoint.

- **Live sports/game state** — `get_milestones_for_event`, `get_live_data(s)`,
  `get_event_live_data`, `get_game_stats`. Re-verified 2026-08-27 against
  `get-milestone.md`, `get-milestones.md`, `get-live-data-with-type.md`,
  `get-multiple-live-data.md`, `live-data-get-live-data.md`, `get-event-live-data.md`,
  `get-game-stats.md` — zero websocket mentions in any of them. Matches the 2026-08-17
  audit's original finding (`docs/next-session-pickup-2026-08-17.md`); still true.
- **Exchange status/maintenance** — `get_exchange_status`. Zero websocket mentions in
  `get-exchange-status.md`/`maintenance_and_pauses.md`.
- **Series/event/market browse and metadata** — `get_series_list`, `get_events`,
  `get_event`, `get_event_metadata`, `get_markets`/`get_market`'s *structural* fields
  (title, `strike_type`, `occurrence_datetime`, `can_close_early`,
  `expected_expiration_time`, category/tag metadata). No channel carries a market's or
  event's static descriptive metadata — the 12 channels are exclusively either
  transactional (trade/fill/order), state-transition (lifecycle), or numeric
  (ticker/orderbook/index feeds).
- **Candlesticks** — `get_candlesticks`/`batch-get-market-candlesticks`. Derived,
  aggregated historical data; no channel pushes pre-aggregated bars.
- **Category/filter taxonomy** — `get_tags_for_series_categories`,
  `get_filters_for_sports`. Static reference data, not market activity.
- **Order placement/cancellation** — `create_order`, `cancel_order`
  (`services/execution.py:79`, `services/kalshi/orders.py:79-130`). Confirmed by
  reading `docs/kalshi/websocket-connection.md`'s full AsyncAPI `operations` list
  (every `sendX`/`receiveX` operation, lines 54-1693): the only command operations that
  exist are `subscribe`, `unsubscribe`, `list_subscriptions`, `update_subscription`, and
  the CF Benchmarks/Pyth-specific subscription variants. No order-entry command exists
  anywhere in the WS protocol. `docs/kalshi/create-order-v2.md` and
  `docs/kalshi/cancel-order-v2.md` make no WebSocket reference either. This matches
  what CLAUDE.md's own "Safety invariants" section already implies (order placement is
  a REST-only code path,
  `services/kalshi_account_client.py`/`services/kalshi/orders.py`) — confirmed here
  against the protocol-level command list, not merely inferred from the current
  implementation's own shape.
- **Account API-limits/endpoint-costs introspection** — `get_api_limits`,
  `get_endpoint_costs` (used by `tools/kalshi_rate_limit_probe.py`, a standalone
  diagnostic tool, not application code). No channel equivalent; not applicable to a
  REST-reduction question in any case since these calls describe the REST layer itself.

## 5. Explicit ambiguities / what could not be determined from static reading alone

- **`_refresh_discovery_cache`'s confirmation-pass REST call (§2.3) vs. lifecycle-driven
  catalog freshness.** Whether the tickers this ≥300 s confirmation pass re-fetches via
  `get_markets_by_tickers` already have fresher status from `market_lifecycle_v2` at the
  moment of that call (making some fraction of the confirmation redundant) cannot be
  determined from static reading — it depends on real-time overlap between which
  tickers `_refresh_discovery_cache` selects and which tickers have had a lifecycle
  event land in the preceding ≥300 s window. A live correlation between
  `lifecycle_stream_stats`'s per-ticker event timestamps and this function's own
  confirmation-batch tickers would resolve it; not attempted here since this document is
  static-analysis scope.
- **Anonymous/public WS access.** All 12 channels in §1 were checked as this app's
  authenticated connection uses them. `websocket-connection.md`'s own text ("Some
  channels carry only public market data, but the connection itself still requires
  authentication") means there is no unauthenticated WS path to compare against
  `docs/kalshi/CHEATSHEET.md`'s already-measured unauthenticated REST ceiling — not
  relevant to this app's current architecture (it always authenticates), noted only so
  a future reader doesn't wonder why it's absent.
- **Whether `market_lifecycle_v2`'s unused event types (§3.4) would actually reduce
  `catalog_scan`'s REST volume if wired, or merely add a second, redundant, faster
  discovery path alongside the existing rescan.** This document identifies the gap (the
  events are received and discarded); it deliberately does not assess feasibility,
  design, or expected savings — that is architecture/solution work, explicitly out of
  scope per this task's instructions.
