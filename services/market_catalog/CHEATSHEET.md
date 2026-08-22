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

## Audit finding: `market_lifecycle_v2`'s `close_date_updated` reaches the
in-memory overlay, not this module's persisted rows — still an open,
already-tracked gap

Not a new finding — cross-checked against the still-open ROADMAP.md item
("A real, permanent fix for the close_time-mutability gap") while writing
this cheat sheet, confirming it's still accurate post-move: Kalshi's
`market_lifecycle_v2` WebSocket channel (`docs/kalshi/market_lifecycle.md`)
emits `close_date_updated` when a market's close time is revised, and
`main.py`'s `_process_stream_lifecycle` already applies that event to the
**in-memory** `state["markets"]` overlay. It is **not** wired into this
module's SQLite `markets` table — `market_catalog.upsert_markets()` only
ever gets fresh `close_ts` on the next incremental scan of that series
(potentially hours away, per the module's own scan-batch design), not the
instant Kalshi emits the revision. Every query in this module
(`candidates_in_window`/`open_candidates`/`open_markets_for_series`)
filters on `close_ts > now` using whatever's currently persisted — a
market whose close time got pushed out is invisible to that filter until
its next scan catches up. Real, disclosed limitation, not fixed here (out
of scope for a modularization pass, and ROADMAP.md already scopes the fix
as its own dedicated pass, deliberately not bundled with the earlier
in-memory half).

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
