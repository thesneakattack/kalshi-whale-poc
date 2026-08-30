# Diagnostics module — reference

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
  directly, now through `store_stats.store_stats()` (read-only, one worker
  thread per store) rather than a local `sqlite3.connect()` on the event
  loop — see the cost section below. It deliberately reads the **live
  objects'** own ingest counters (`trade_stream.messages_received`, etc.),
  not a fresh process's zeroed ones, per its own docstring's 2026-08-17
  finding.
- **Downstream:** purely read-only with respect to trading — no
  `config_store.update()` anywhere in this module, unlike
  advisory/whale_calibration's auto-apply write-back loops.

## REST-vs-WebSocket trade capture reconciliation (realtime data-plane I4, 2026-08-25)

`trade_capture_reconciliation.reconcile_window(...)` + the manual route
`GET /api/diagnostics/trade-capture?minutes=5&lag_sec=60&max_pages=10`.
The one measurement that can say how complete WebSocket capture actually
is: fetch Kalshi's exchange-wide REST record of a bounded exchange-time
window (`GET /markets/trades`, no `ticker`, `limit=1000`, `min_ts`/`max_ts`,
cursor until empty — docs/kalshi/get-trades.md) and check every
`trade_id` against the whale provider's own seen-record
(`KalshiTradeTapeProvider.seen_exchange_ts_by_id()`, the dedupe ring with
exchange timestamps). Reports REST count / WS count / intersection /
missing ids / whale-sized missing ids / completeness ratios, with the
ingest queue evidence (drops, error 25, reconnects, oldest-message age)
attached, and explicit caveats: paging truncation (counts become lower
bounds), an empty REST window (completeness is `None`, never 100%), and
a seen-record horizon newer than the window start (misses before it may
be dedupe-ring eviction — at ~250 k ids the ring holds only ~10–30 min of
exchange-wide flow, so keep windows short and recent). The window ends
`lag_sec` before now so a print still queued is not counted as missed.
Never scheduled — manual/interactive only; each run costs at most
`max_pages` REST pages against the shared read limiter.

**First live reading (2026-08-25, after 4 min of untouched uptime,
`minutes=2&lag_sec=60&max_pages=30`):** REST 12,737 trades in 120 s (13
pages, not truncated, ≈106/s; 29 whale-sized), WS-seen 12,855 in the same
window, intersection 12,737 → **capture completeness 1.00, whale-sized
29/29**, with `dropped_messages=0`, no error 25, no reconnects and an empty
queue for the whole period. Two lessons for interpretation: (1) the very
first attempt, 4 s after a `--reload`, reported 0% because the fresh
provider's seen-record did not yet reach back to the window — the horizon
caveat exists for exactly that; always check `caveats` before believing a
ratio; (2) 118 WS-only ids (`ws.not_in_rest`) appeared at the window edges
— consistent with `min_ts`/`max_ts` being whole seconds against WS `ts_ms`,
so treat `not_in_rest` as boundary noise unless it grows with the window.
A 3-minute window at this flow rate needs >10 pages; size `max_pages` to
the rate or the result is only a lower bound (and says so).

## The cost of `/api/health/pipeline` itself (issue #210, 2026-08-30)

The endpoint CLAUDE.md sends every investigation to went instant → 41.9s →
504 in one day, and dragged `last_tick_duration_sec` to 81.3s with it. Two
independent defects in the same four lines, both measured on the live
stores:

| probe | shipped form | measured | replacement | measured |
|---|---|---|---|---|
| `stores.raw_trades` rows | `SELECT COUNT(*)` over 30.8M rows / 22.3GB | **70.5s** cold, 97.2s under load | `SELECT MAX(rowid)` | **0.03–0.07s** |
| `stores.raw_trades` last write | `SELECT MAX(observed_at)` — no index leads with it | **7.5s** | max over the newest 5 000 rowids | **<1ms** |
| `buffered_unwritten.series_watcher_trades` | `series_watcher.capture_stats()` — two series-filtered `COUNT(*)`s over `raw_trades` | **4.5s** warm, 34.2s under load | `capture_writer.depth()` (in-memory, the value `capture_stats` was forwarding anyway) | **0ms** |

Whole store block, live DBs, same machine, back to back:
**131.6s blocking the event loop → 0.28s warm / 1.7s cold, off the loop.**

Two things this is *not*. It is not a smaller payload — every field the old
handler returned is still returned. And it is not an exact number quietly
turned into a guess: above `store_stats.EXACT_ROW_LIMIT` (2 000 000 rowids,
from a measured ~2.3 µs/row full-index scan) the store reports
`rows_exact: false`, `rows_method: "max_rowid"` and a `rows_note` saying it
is an upper bound. Every store under the limit still reports an exact
`COUNT(*)`, labelled exact. `?exact_rows=true` forces the exact form
everywhere — measured at **191s** on `raw_trades`, which is why it is opt-in.

`stores_probe_ms` in the payload is the permanent recurrence detection:
the block reports its own wall cost, so the next regression shows up in the
same read rather than in someone's stopwatch. Guards live in
`tests/test_pipeline_health_cost.py`, which asserts the SQL issued and that
no probe runs on the event loop.

## `capture_writer`: rows the capture daemon lost, by cause (issue #211, 2026-08-30)

`faults_last_24h.by_component.capture_writer` counts collisions, not
missing history. Until 2026-08-30 a `database is locked` on the daemon's
flush discarded the batch (up to 500 `raw_trades` rows; 460 rows measured
lost in one 18.2h process, 227 faults since 2026-08-27), because the
writer opened `series_watcher.db` with a 50ms busy budget while the file's
other writers - `series_watcher.flush()`'s book INSERT (20-44ms hold per
tick) and `series_watcher.prune()`'s full-scan DELETE (~90ms warm, hourly
and on every start; 24 of 45 timestamped faults sat within 120s of a prune
mark) - hold the per-file write lock longer than that. The daemon now
retains the batch on a lock and retries next cycle, and the payload's
`capture_writer` block (`services/capture_writer.loss_snapshot()`,
in-memory, 0ms) is the completeness read:

| field | meaning |
|---|---|
| `dropped_rows` (per store) | rows discarded by a non-retryable flush failure - loss |
| `overflow_dropped_rows` (per store) | rows discarded because the retained buffer hit `max_retained_rows` during a long lock hold - loss, under its own name |
| `lock_retries` (per store) | batches handed back to the buffer after a lock collision - churn, not loss |
| `depth`, `max_retained_rows`, `counter_scope` | current backlog, the cap, and the reminder that every counter resets with the process |

`tools/soak_analyzer.py`'s `capture_writer_health` gates on the two loss
counters and treats a 24h fault the counters cannot account for as a
partial source (UNKNOWN, never PASS).
