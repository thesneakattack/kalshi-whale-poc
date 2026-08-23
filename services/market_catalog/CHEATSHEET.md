# Market catalog module — cheat sheet

Owns: `market_catalog.py` (the incrementally-scanned, persisted picture of
Kalshi's near-term real markets — `data/market_catalog.db`, one row per
market, `_MAX_PAST_HORIZON_SEC`/`_MAX_FUTURE_HORIZON_SEC`-bounded, not a
full catalog by design) + `routes.py` (per-market drill-down: orderbook,
candlesticks, trades, detail; catalog scan status; the series-based search
endpoint). Split out of `main.py` 2026-08-22 (modularization Phase 6/9) —
these routes were never assigned to any of the prior 7-phase effort's 6
named concerns; they depend on `market_catalog`/`title_cache`/
`market_watch`'s series cache, the natural home. 13 real importers across
the codebase updated to the new `services.market_catalog.market_catalog`
path.

## Relevant Kalshi API docs — the heaviest real API surface of this whole
modularization pass

Every route in `routes.py` maps directly to a documented endpoint —
checked against each page while writing this, not assumed:

- `GET /api/markets/{ticker}/orderbook` → `docs/kalshi/get-market-orderbook.md`.
- `GET /api/markets/{ticker}/candlesticks` → `docs/kalshi/get-market-candlesticks.md`
  (needs `series_ticker`, resolved via `get-event.md`'s response first —
  a market object alone doesn't carry it).
- `GET /api/markets/{ticker}/trades` → `docs/kalshi/get-trades.md`.
- `GET /api/markets/{ticker}/detail` → `docs/kalshi/get-market.md` +
  `get-event.md` (sibling markets in the same event).
- `GET /api/markets/search` → `docs/kalshi/get-series-list.md` (series-level
  text match, not a flat market browse — see the route's own docstring for
  why: Kalshi's combo/MVE markets are generated in bulk enough that a flat
  browse of tens of thousands can still miss real matches) +
  `get-markets.md` (per-series fetch once candidates are narrowed).
- `market_catalog.py`'s own persisted `status` field is checked against
  `'active'`, never `'open'` — confirmed directly against
  `docs/kalshi/market-and-event-lifecycle.md`'s filter-value table:
  `'open'` is only ever a `GET /markets?status=` query **filter** value,
  mapped server-side to `'active'` in real market objects. The comment in
  `candidates_in_window`/`open_candidates` documents this was dead code
  before the fix — a real instance of exactly the "did we correctly
  understand what the data means" mistake CLAUDE.md's own "Bug pattern to
  watch for" section warns about.

## Audit finding, closed 2026-08-23: `market_lifecycle_v2` now reaches this
module's persisted rows, not just the in-memory overlay

Was an open, already-tracked gap (see git history on this section for the
original finding) — `close_date_updated` only ever updated `main.py`'s
in-memory `state["markets"]` overlay, so `market_catalog.upsert_markets()`
learned a revised `close_ts` only on that series' next incremental scan
(potentially hours away). Fixed via a new `apply_lifecycle_update(ticker,
*, close_ts=None, status=None)` (this file) - a targeted single-row UPDATE,
not a full `upsert_markets`-style replace - called from
`services/whale_stream/whale_stream_handlers.py`'s `_process_stream_lifecycle`
for all three of `close_date_updated` (`close_ts`), `determined` (`status`
-> `"determined"`), and `settled` (`status` -> `"finalized"`, the same
values a real REST market object's own `status` field would show per
`docs/kalshi/market_lifecycle.md`'s status table). No-op (returns `False`,
counted separately from `close_time_updates_applied`/`outcomes_resolved_
via_lifecycle` via `lifecycle_stream_stats.catalog_updates_applied`) for a
ticker with no existing row - expected given this catalog is near-term-
horizon-bounded by design, not an error. Verified live before shipping:
real captured message shapes from `ddev logs` (`kalshi_trade_ws.py`'s own
"first real shape" log) cross-checked against
`docs/kalshi/market-and-event-lifecycle.md`'s schema first - `determined`
really does carry `result`, `settled` does not.

**Correction, later the same day**: the first version of this fix used
`determined` as the trigger that also resolves this app's own
market_history/settlement_edge/market_analyst_agent/candidate_log outcome
tables, reasoning purely from data availability ("only `determined` carries
`result`"). That was wrong on correctness grounds -
`docs/kalshi/market_lifecycle.md` is explicit that `determined` is not
terminal (the result "may be disputed" during the settlement-timer window,
and can flip via `determined` -> `disputed` -> `amended` before
`finalized`) - see `services/exits/CHEATSHEET.md`'s audit finding, which
named the identical bug shape for `close_if_settled` and got fixed the same
pass. `determined` now only updates this module's persisted `status`
column; `settled` does the actual resolving, via a fresh single-ticker
`client.get_market(ticker)` REST read (since the `settled` payload itself
has no `result` field) gated on that read confirming
`status == "finalized"` - see `services/whale_stream/whale_stream_handlers.py`'s
`_process_stream_lifecycle` docstring for the full detail. Closing the
off-watchlist-ticker gap (below) is unaffected by this correction - it was
always about *which tickers* get resolved via this channel, not *when* is
safe to trust the result: those were previously fed exclusively from that
tick's REST-fetched `markets` list, structurally blind to any ticker that
rotates off the live watchlist/discovery scope before it settles (routine
for short-lived series like KXBTC15M) - `market_lifecycle_v2` is
exchange-wide, so this reaches every ticker the app ever touched,
watchlisted or not.

## Handoff — who calls this module, who it calls

- **Upstream (writes the catalog):** `services/market_watch/market_watch.py`'s
  `_scan_catalog_batch` calls `market_catalog.next_series_to_scan`/
  `upsert_markets`/`mark_scanned` once per trading-loop tick — the
  incremental background scan this whole module exists to serve.
- **Downstream (reads the catalog):** `market_watch.py`'s discovery-cache
  path (`open_candidates`) and live-status path (`candidates_in_window`),
  `services/event_inspector.py`, `services/diagnostics.py`'s
  `_close_ts_for_tickers` (a local, function-scoped import — the one store
  that persists a queryable `close_ts`), and this module's own `routes.py`
  (`GET /api/market-catalog/status`, `GET /api/markets/search`'s
  `live_only` path).
- `routes.py`'s search/detail/orderbook/candlesticks/trades routes go
  straight to a fresh `KalshiClient` call each time — deliberately NOT
  served from the catalog (which is near-term-scoped and volume-filtered
  by design), except `search_markets`' `live_only=True` path, which
  prefers the catalog when it already has near-term data for the matched
  series and falls back to a fresh REST fetch otherwise.
