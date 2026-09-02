# Event-Loop-Blocking Elimination — Design

## Context

Two findings from this session, unified under one principle: **the asyncio
event loop must never be blocked by synchronous SQLite I/O — whether via a
shared thread pool under contention, or via a raw unawaited synchronous call
with zero offload at all.**

Earlier today's write-path capacity fix (PR #409, merged) addressed the first
failure mode — `services/tick_executor`'s shared 2-worker pool was starved by
non-critical work (whale-scoring per-trade connections, `diagnostics.
run_offline()`), fixed with dedicated pools and, where proven safe, connection
reuse.

A live, ongoing incident investigated this session (a 13-minute full stall,
`GET /api/state`/`GET /api/health/pipeline`/`GET /api/trading-history` all
unresponsive, self-recovered) traced to the *second* failure mode: a genuinely
unbounded, un-offloaded synchronous call directly on the event loop thread.
This explains the app-wide, endpoint-agnostic nature of the reported hangs
better than anything the first fix touched — `GET /api/trading-history`'s
handler does zero database I/O (pure in-memory iteration over
`broker.trade_log`), so its hanging rules out a per-endpoint DB bottleneck and
points at the event loop itself being blocked elsewhere.

This spec covers two fixes:

1. **Fix 1 (urgent, bounded):** eliminate the inline synchronous flush in
   `index_feed`/`settlement_edge`/`game_state`/`series_watcher`'s (the
   `record_book` sibling specifically) `record_*()` functions — four
   instances of the same bug, confirmed complete by an app-wide grep for the
   pattern's own signature, not assumed from the first three found.
2. **Fix 2:** replace the whale-scoring and diagnostics hand-rolled
   `ThreadPoolExecutor`+connection-cache pools (built earlier today, PR #409)
   with `aiosqlite`-based async-native connections, for the same
   already-proven-hot modules plus the diagnostics-widening scope agreed this
   session.

**Explicitly out of scope, noted as future work, not attempted here:** a
full application-wide migration to `aiosqlite`. 37 files across `services/`
open raw `sqlite3` connections today; only the modules Fix 2 names have
measured evidence of being a bottleneck. Converting the rest without
measurement would violate the data-plane HARD RULE's "identify the bottleneck
before changing the mechanism" — each remaining file becomes its own future,
separately-scoped, separately-measured task, decomposed the same way `docs/
next-action.md` already tracks `tools/kanban_sync`-style follow-ups.

## Fix 1: eliminate the inline synchronous flush

### Root cause (verified against source, not assumed from the investigation report)

`services/index_feed/ingestion.py`'s `record_cfbenchmarks()`/`record_pyth()`,
`services/settlement_edge.py`'s `record_observation()`, and `services/
game_state.py`'s `record()` are plain synchronous functions, each called with
no `await` from async WS-message-handling code (`services/whale_stream/
index_stream_handlers.py`, `services/market_watch/event_metadata.py`,
`services/market_watch/live_status.py`). Each appends to an in-memory buffer
under a lock, then — when the buffer reaches its threshold (`_FLUSH_BATCH`:
200/120/100 rows respectively) — calls `flush()` **inline, synchronously**,
which does real disk I/O (`conn.executemany(...)`) with no `await` point
during the write. `services/kalshi/websocket.py`'s `_HANDLER_TIMEOUT_SEC=10.0`
(`asyncio.wait_for`) cannot interrupt this — `wait_for` only cancels at an
`await` point, and this path has none while the write is in flight. `main.py`
itself already documents the historical failure mode this reproduces
(`main.py:987-991`): "a lock collision on any of these ... used to freeze the
whole event loop, not just this tick."

This is an **incomplete fix**, not a new problem: `main.py` commits
`d87fd5b`/`9b4bd80` (both from earlier today, before this session started)
moved the *scheduled* flush of these same three modules
(`_flush_secondary_capture_stores_async`, called once per tick loop
iteration) onto `tick_executor`'s worker pool. The inline,
buffer-full-triggered flush inside each `record_*()` function was never
converted and still runs synchronously wherever `record_*()` is called.

**A fourth instance, found by a targeted follow-up audit (grepping for the
same `should_flush = ` signature app-wide, not assumed from the first three):**
`services/series_watcher.py`'s `record_book()` has the identical shape —
append to `_book_buffer` under `_buffer_lock`, check `len(...) >=
_FLUSH_BATCH` (500), call `flush()` inline and synchronously if true. Its
sibling `record_trade()` was already migrated to the correct pattern
(`capture_writer.submit()` — pure append, a dedicated daemon thread owns the
actual flush, per Task 15) but `record_book()` was left on the old,
still-broken path. Confirmed live: `record_book()`'s one call site
(`services/whale_stream/whale_stream_handlers.py:287`) is inside `async def
_process_stream_ticker` — the ticker-channel handler, which "fires on every
field change" per `record_book()`'s own docstring, making this plausibly the
*highest-frequency* instance of the four, not a minor addendum. The app-wide
sweep for this exact pattern signature (`grep -rl "should_flush\s*="
services/`) returned exactly these four files — index_feed/ingestion.py,
settlement_edge.py, game_state.py, series_watcher.py — confirming this is the
complete set for this specific bug shape, not a partial list.

**Confirmed by the same audit that `capture_writer.py` (the module all four
of these were meant to converge toward per Task 15's precedent) is already
correct** — `submit()` only appends under a lock and returns immediately; a
dedicated daemon thread (`_FLUSH_INTERVAL_SEC=1.0`) owns all actual flushing,
completely decoupled from any caller. No change needed there; it's the
reference pattern the fix below approximates for the four still-broken
functions (via `tick_executor`+`create_task` rather than a dedicated thread,
since these callers are already inside the asyncio event loop, not a
separate thread needing its own polling loop).

### Why "just remove the inline trigger" is unsafe

The scheduled flush only runs once per tick loop iteration
(`main.py:992`). During exactly the kind of stall this bug causes, tick
iterations stop — so the scheduled flush stops too, and a buffer with no
inline safety net would grow unbounded for the stall's entire duration.
The inline trigger has to stay as a backstop; it has to stop blocking the
event loop while doing it.

### Fix

`record_cfbenchmarks()`/`record_pyth()`/`record_observation()`/`record()`
stop calling `flush()` directly. Each already computes a `should_flush`
boolean internally (buffer length vs. `_FLUSH_BATCH`) but currently acts on
it itself; the fix makes each function return that boolean to its caller
instead. Their async callers — already `await`-based coroutines — schedule
the flush without blocking themselves on its completion:
`asyncio.create_task(tick_executor.run(flush))`, fire-and-forget, reusing the
exact pool the peer session's own earlier commits already established as the
accepted mechanism for these three modules' scheduled flush. This is
completing an already-adopted pattern, not introducing a new one.

`create_task` (not `await tick_executor.run(flush)` directly) is deliberate:
the WS message handler that triggered the buffer-full condition doesn't need
to wait for the flush to actually finish before it can return and process the
next message — waiting would just reintroduce a smaller version of the same
blocking problem. `flush()`'s own `_buffer_lock`-protected swap-and-clear
(already correct, unchanged) means a second `record_*()` call landing before
the scheduled task actually runs appends to a fresh buffer, not a stale one.

### Files touched

- `services/index_feed/ingestion.py`: `record_cfbenchmarks`, `record_pyth`
  return `should_flush`; callers in `services/whale_stream/
  index_stream_handlers.py` schedule the flush.
- `services/settlement_edge.py`: `record_observation` returns
  `should_flush`; its one caller (`index_stream_handlers.py:152`) schedules
  the flush.
- `services/game_state.py`: `record` returns `should_flush`; its two callers
  (`event_metadata.py`, `live_status.py`) schedule the flush.
- `services/series_watcher.py`: `record_book` returns `should_flush`; its one
  caller (`whale_stream_handlers.py:287`, inside `_process_stream_ticker`)
  schedules the flush. `record_trade` (already correct, routes through
  `capture_writer.submit()`) and every read-only function in this file
  (in Fix 2's scope) are untouched by this fix.
- No change to `flush()` itself, `_buffer_lock`, `_FLUSH_BATCH` thresholds, or
  the already-correct scheduled per-tick flush path, in any of the four files.

### Testing

Each `record_*()` function gets a test confirming it returns `True` exactly
when the buffer crosses its threshold, and does NOT call `flush()` itself
(spy/monkeypatch). Each caller site gets a test confirming it schedules
`tick_executor.run(flush)` via `create_task` when told to, using this
repo's established `asyncio.run()`-wrapping-a-sync-`def`-test convention
(no `pytest-asyncio`).

## Fix 2: aiosqlite for whale-scoring + diagnostics

### Scope (as narrowed through this session's discussion)

**Whale-scoring pool:**
- `services/whalewatchers/_scoring_pool.py` — rewritten from
  `ThreadPoolExecutor`+thread-local connection cache to 2 persistent
  `aiosqlite.Connection`s per DB file (`signal_log.db`, `market_history.db`,
  `market_analyst.db`), opened once, alternated between the pair per call.
  2, not 1: preserves the burst/overlap headroom the original 4-worker design
  deliberately built in (WS-message path + candidate-retry path hitting the
  same file concurrently) rather than asserting a single connection is
  sufficient without measurement — corrected mid-session after an initial,
  under-evidenced claim that 1 was enough.
- `services/signal_log.py`, `services/market_history.py`, `services/
  market_analyst_agent/_db.py`+`per_market.py`: `recent_sides_for_ticker`,
  `cluster_factor`, `momentum`, `analyst_lean` become `async def`, using
  `aiosqlite` instead of raw `sqlite3`.
- `services/whalewatchers/kalshi_trade_tape.py`: `fetch_signals`/
  `score_recovered_trade` `await` these directly — the
  `_scoring_pool.run(sync_fn)` wrapper goes away (its own async-pool-dispatch
  role is now just "get a pooled connection," not "run a sync callable on a
  thread").

**Diagnostics widening:**
- `services/diagnostics/diagnostics.py`: the DB-touching `Check` functions
  `run_offline()` actually calls — `check_threshold_integrity`,
  `check_price_band_adherence`, `check_runway_at_entry`, `performance_by_epoch`,
  `selectivity_curve`, `check_confidence_input_coverage` (confirmed by reading
  `run_offline()`'s own body, not assumed from the file's full function list;
  `check_config_bounds` has no DB call, `config_epochs` isn't called by
  `run_offline()` at all and stays out of scope) — become `async def`, using
  `aiosqlite`.
- `services/series_watcher.py`: only the read-only functions `run_offline()`
  reaches (`funnel`, `reconcile`, `_signals_for_series`, `_trades_for_series`,
  `check_series_funnel`) become async. `record_trade`/`record_book`/`flush`/
  `prune` — the live write-path capture functions in the same file, called
  every tick from `main.py` — are untouched, unconverted, stay exactly as
  they are.
- `services/diagnostics/_diagnostics_pool.py` (built earlier today, PR #409):
  **deleted**. Once every function in `run_offline()`'s call graph is
  genuinely non-blocking, there's nothing left to isolate — the root cause
  Task 8 isolated (blocking work occupying a thread pool) is eliminated
  outright, not contained. Verified before committing to this: `run_offline()`'s
  target DB files (`signal_log.db`, `paper_broker.db`, `market_catalog.db`,
  `config_performance.db`) don't overlap with `tick_executor`'s other
  trading-critical writers (`candidate_log.db`, `candidate_ledger.db`), so
  removing the isolation pool doesn't reopen the WAL-lock-contention risk it
  was built to prevent.
- `services/quality/routes.py`: `await diagnostics.run_offline(cfg)` directly,
  no executor wrapper.

### Why aiosqlite, verified not assumed

`aiosqlite.connect()` returns a `Connection` proxy backed by **one dedicated
background thread per connection**, processing an internal FIFO queue — not
a worker pool (confirmed against the library's own source via Context7, not
recalled from memory). This means aiosqlite eliminates the raw
threading/callback boilerplate and thread-local connection-caching logic this
session hand-rolled for `_scoring_pool.py`/`_diagnostics_pool.py` earlier
today, but does **not** eliminate pooling as a design concern outright — a
single connection serializes all operations against that file onto one
thread, so matching or exceeding today's throughput still means opening more
than one connection per file (hence 2, not 1, for the scoring pool above).

### Measured tradeoffs of the shipped diagnostics implementation

Added 2026-09-01 after the Fix 2 diagnostics-widening PR's adversarial
review (findings I4/I5) measured what this section had only reasoned about.
Recorded as explicit, accepted tradeoffs — **no behaviour was changed in
response**; both would be behaviour changes needing their own measurement,
not a drive-by edit to a merging PR.

**1. `_aio_db` ships 1 connection per file, not the >1 this section argues
for.** The paragraph above says matching prior throughput means more than
one connection per file. `services/diagnostics/_aio_db.py` keeps exactly one
per `(loop, db_path)`, where the `_diagnostics_pool` it replaced gave
`run_offline()` 2 concurrent workers. With `GET /api/quality/summary`
measured at **20.4s wall** against a ~5s dashboard poll, several requests
overlap, so per-request latency can degrade under overlap even though
aggregate throughput is roughly unchanged. The module's "a single connection
per file is enough headroom here" line is a deliberate simplicity choice,
**asserted, not measured**.

**2. The conversion moves pure-Python aggregation onto the event loop.**
`run_offline()` previously ran entirely on `_diagnostics_pool`'s worker
thread; it now runs on the loop, with only the SQL and two explicitly
wrapped calls off it. Every aggregation loop between a fetch and its `Check`
has no `await` point, so it holds the loop for its full duration. Measured
against the real `data/*.db`, per `run_offline()` call:

| check | rows | I/O (off-loop) | pure-Python (now on-loop) |
|---|---|---|---|
| `check_threshold_integrity` | 23,957 | 64ms | **78ms** |
| `selectivity_curve` | 50,711 | 112ms | **86ms** |
| `check_confidence_input_coverage` | 124,859 | 1,043ms | **89ms** |
| `trade_analytics.build_trade_history` | 621 (one series) | — | **1.5ms × 16 ≈ 24ms** |

That is **≥ ~280ms of contiguous, un-awaited on-loop CPU per call** — a
lower bound: `check_price_band_adherence`, `check_runway_at_entry`,
`performance_by_epoch` and both `funnel()`/`reconcile()` aggregations were
not measured. Net effect: a large win for `GET /api/diagnostics` and
`GET /api/diagnostics/series/{series}` (previously running all ~20s on the
loop), a **regression** for `GET /api/quality/summary` (previously 0ms
on-loop, via the pool). Two orders of magnitude better than the 13-minute
stall this spec exists to fix, but a real cost, stated rather than implied.

### Testing

Same `asyncio.run()`-wrapping convention (no `pytest-asyncio` in this repo).
Existing `_scoring_read_connection`-pattern tests (cache reuse, thread-local
isolation, `ProgrammingError` recovery) get re-derived for aiosqlite's actual
failure modes — a broken/closed `aiosqlite.Connection` needs its own
reconnect-on-failure test, not a copy-paste of the old thread-local recovery
logic (that mechanism doesn't apply to a single shared async connection the
same way).

**Status 2026-09-01:** this reconnect requirement shipped only after the PR
adversarial review caught it missing (finding I1) — `connection_for()` had
claimed parity with `_scoring_pool.cached_read_connection()` while doing no
liveness probe at all. It now probes a cached connection before returning it
and evicts-and-reopens a dead one, covered by
`tests/test_aio_db.py::test_connection_for_reopens_a_connection_closed_out_from_under_it`.
The failure mode is genuinely different from the sync sibling's, exactly as
this paragraph anticipated: a closed `aiosqlite.Connection` raises
`ValueError` (`"no active connection"` from `Connection._conn`, or
`"Connection closed"` from `Connection._execute`), **not**
`sqlite3.ProgrammingError` — so it is not a `sqlite3.Error` subclass and
would have bypassed every caller's `except sqlite3.Error` degradation branch
and surfaced as a 500.

## Sequencing

Fix 1 ships first — smaller, more urgent, more directly evidenced against
the live incident, and independent of Fix 2 (touches entirely different
files). It gets its own complete implementation plan and PR now.

**Fix 2 gets its own separate implementation plan, written later, not
bundled into Fix 1's.** Writing-plans-stage research found the
diagnostics-widening portion genuinely larger and more interconnected than
scoped here: `diagnostics.py`'s `Check` functions share helper functions
(`_fetch_path_changes`, `_close_ts_for_tickers`) across multiple checks, each
opening their own raw connections — the aiosqlite conversion isn't cleanly
one-function-at-a-time the way the whale-scoring pool consumers are, and
needs its own design pass rather than being rushed into a combined plan.
This spec's unifying principle (both fixes address "the event loop must
never be blocked by synchronous SQLite I/O") still holds across two plans;
only the plan-writing and execution are now separate, not the architectural
framing.

## Non-goals, explicit

- No full-application aiosqlite migration. Tracked as future work only.
- No change to `_FLUSH_BATCH` thresholds, `flush()`'s own locking, or the
  scheduled per-tick flush path (`_flush_secondary_capture_stores_async`) —
  all already correct.
- No change to `series_watcher.py`'s `record_trade` (already correct) or its
  scheduled/periodic flush path — only `record_book`'s inline flush trigger
  changes, per Fix 1.
- No change to trading/risk/strategy logic anywhere in this scope.
