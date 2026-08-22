# Diagnostics module — cheat sheet

Owns: `diagnostics.py` (`run_offline`'s bundle of read-only integrity
checks — `check_threshold_integrity`, `check_price_band_adherence`,
`check_runway_at_entry`, `check_coverage`, `check_config_bounds`,
`config_epochs`/`performance_by_epoch`, `selectivity_curve`) + `routes.py`
(`/api/diagnostics/*`, `/api/archive/*`, `/api/health/*`, `/api/index/*`).
Moved into this package 2026-08-22 (modularization Phase 8/9) from
`services/diagnostics.py` + the top-level `routers/diagnostics_routes.py`
— the latter was the literal Phase-0 template for the prior 7-phase
main.py modularization ("the pattern, so the next group is mechanical" —
its own docstring), left as a one-off top-level directory ever since while
every later concern landed under `services/<name>/`. Normalized here, and
`routers/` (now empty) is deleted.

**`series_watcher.py` and `settlement_edge.py` deliberately stay flat, not
moved in** — checked directly before deciding, not assumed: both have real
importers outside diagnostics (`services/whale_stream/whale_stream_handlers.py`
calls `series_watcher.record_trade`/`record_book` on every streamed
trade/ticker message; `services/whale_stream/index_stream_handlers.py`
calls `settlement_edge`'s observation recorder). They're the live-stream
**capture** path first and a diagnostics **data source** second —
diagnostics only ever calls their read-side functions
(`funnel`/`reconcile`/`capture_stats`, `edge_report`/`stats`). Moving them
would misrepresent that split.

## This is where CLAUDE.md's HARD COMMANDMENT actually gets measured

`GET /api/diagnostics/series/{series}` (`series_watcher.reconcile()`) is
the one place in this app that reports whale accuracy and realised win
rate **as a pair with the entry-cost decomposition already spelled out**
(selection loss, exit loss — see `series_watcher.py`'s own module
docstring for the three-stage breakdown: SELECTION, EXIT, PRICE). Any
future work on the 70%/70% target should start by reading this route's
output, not by re-deriving accuracy/win-rate numbers ad hoc elsewhere.
`check_threshold_integrity`/`check_price_band_adherence`/
`check_runway_at_entry` all judge live config against real historical
signal data, epoch-aware (`config_epochs`/`performance_by_epoch` — a
config value judged against history is judged against what was actually
live *at that historical timestamp*, not today's value retroactively
applied).

## No direct Kalshi API surface — except one real cost center

Every offline check reads this app's own already-captured data
(`signal_log`, `config_performance`, `paper_broker`, `market_catalog`).
The one exception: `check_coverage` (`GET /api/diagnostics/coverage`)
makes 1-2 real `GET /markets/trades` calls, exchange-wide — the only way
to measure how much real whale flow this app never sees at all, since
that's definitionally not in any local store. Kept as its own route
(`/api/diagnostics/coverage`, not folded into `/api/diagnostics`'
always-safe offline set) for exactly this reason.

## Handoff — who calls this module, who it calls

- **Upstream:** dashboard-only, `GET`/`POST` routes; nothing in
  `main.py`'s `trading_loop` calls into this module directly (unlike
  advisory/whale_calibration) — diagnostics is purely on-demand
  inspection, no live-loop coupling to worry about.
- **`GET /api/health/pipeline`**: the one route that reaches into
  `series_watcher.DB_PATH`/`index_feed.DB_PATH`/`settlement_edge.DB_PATH`/
  `game_state.DB_PATH`/`signal_log.DB_PATH`/`candidate_log.DB_PATH`
  directly via a local `sqlite3.connect()`, to report last-write-age per
  store — deliberately reads the **live objects'** own ingest counters
  (`trade_stream.messages_received`, etc.), not a fresh process's zeroed
  ones, per its own docstring's 2026-08-17 finding.
- **Downstream:** purely read-only with respect to trading — no
  `config_store.update()` anywhere in this module, unlike
  advisory/whale_calibration's auto-apply write-back loops.
