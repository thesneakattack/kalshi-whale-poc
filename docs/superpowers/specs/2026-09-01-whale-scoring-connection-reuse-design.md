# Whale-Scoring Per-Trade Connection Reuse — Design (Revision 2)

Revision 2, written against the required fix list in
`2026-09-01-whale-scoring-connection-reuse-design-review.md` (NO-GO on revision 1).
Every factual citation in revision 1 held up under independent adversarial
re-verification; the causal analysis had a real gap that changes the core mechanism
(section 4) and scope (section 1a, section 4's `score_recovered_trade` coverage) —
addressed below, not just patched around.

## 1. Problem, grounded in source and live telemetry

`docs/next-action.md`'s top item (2026-09-01): `capture_writer`/`candidate_log`'s
write path was believed unable to sustain full legitimate trade volume, proven live
when a peer session's PR #397 dropped `KXBTC15M`'s effective `min_contracts` threshold
3000→173 (a ~17x cut on this app's highest-frequency series). Result: tick duration
6.63s→241.63s peak, `kalshi_websocket` repeated `consumer_stalled_forced_reconnect`,
the ingest queue reached 17,451/20,000 capacity, signal generation stopped for ~6
minutes. Reverted as an emergency stopgap; three named candidate mechanisms were left
for follow-up investigation (capture_writer's flush cadence, candidate_log's per-print
work, `kalshi_trade_tape.py`'s prescan/resolve pipeline cost).

This spec root-causes the mechanism precisely, not by picking among those three but
by tracing the actual call graph and correlating it against live telemetry:

- `services/whalewatchers/kalshi_trade_tape.py`'s `_process_trades_sync()` — called
  once per incoming WS trade message via `asyncio.to_thread` (Python's default
  executor, not `services.tick_executor`'s pool) — runs four scoring reads for every
  trade that passes the `min_contracts` gate: `signal_log.recent_sides_for_ticker()`,
  `signal_log.cluster_factor()`, `_trend_factor()` (→ `market_history.momentum()`),
  `_analyst_factor()` (→ `market_analyst_agent.per_market.analyst_lean()`).
- Each of these opens a **fresh SQLite connection** per call, confirmed directly:
  `signal_log.py:250` (`with _connect() as conn:`), `signal_log.py:280` (same),
  `market_history.py:57`'s `_connect(db_path)`, `market_analyst_agent/_db.py:16`'s
  `_connect()`. Every one of the three `_connect()` implementations does
  `sqlite3.connect(DB_PATH)` (no `timeout=` — Python's 5.0s default busy timeout) plus
  `PRAGMA journal_mode=WAL` and a `CREATE TABLE IF NOT EXISTS`, on every single call.
- `candidate_log.record_rejection()` — the majority-case path for the many trades
  that get *rejected* rather than scored — was checked and ruled out: already fixed
  to be fully async/batched via `capture_writer` (P3 Task 17, 2026-08-27), zero
  synchronous DB I/O. Not part of this problem.
- Live telemetry (`GET /api/observability/summary`, read during this design):
  `whale_pipeline.stage.provider.window_avg_ms` — the stage timing exactly
  `whale_provider.fetch_signals()`, which wraps `_process_trades_sync()` — spikes to
  ~924ms average in one measured window, versus ~1ms for `capture` and ~0.2ms for
  `config`. `handler_total` correspondingly reaches a 20.3s max. This is independent,
  correlated confirmation of the same location the source points to.

Dropping BTC15M's threshold didn't just admit more data — it multiplied how many
trades hit this four-fresh-connections-across-three-files code path, on the app's
single highest-frequency series, from worker threads that can run concurrently
against the same SQLite files.

## 1a. A second, more severe instance of the same call path — found in revision 2

`services/candidate_retry.py`'s `run_pending()` (invoked from `main.py`'s own
`_candidate_retry_loop`) calls `provider.score_recovered_trade(trade, market, cfg,
now)` for a candidate whose market lookup failed on first try. This reaches
`_process_trades_sync` — the same four scoring functions — but **synchronously, on
the event-loop thread**, not via `asyncio.to_thread` at all. A slow call here blocks
the entire event loop (every async task, not just one worker-thread call) for its
duration, which is strictly worse than the WS-message path revision 1 analyzed. This
was missed in revision 1's root-cause narrative and is brought into scope here (see
section 4) — the fix targets the shared underlying functions, so covering this path
costs nothing extra and closes a worse bug than the one originally targeted.

**Also found, pre-existing and explicitly out of scope:** `_process_trades_sync`'s
own docstring claims `self._seen_trade_ids`/`self._seen_order` are "only ever
touched from within one in-flight `fetch_signals()` call at a time" — false once
`score_recovered_trade`'s event-loop-thread path is considered alongside a
worker-thread call running concurrently. Real, but a pre-existing thread-safety gap
unrelated to connection lifecycle; noted here as a pointer for a future item, not
addressed by this spec.

## 2. Non-goals

- `capture_writer`'s flush cadence/batch size and `services.tick_executor`'s own
  2-worker pool sizing are **not** touched here. Increasing `tick_executor`'s worker
  count was considered and rejected: SQLite serializes writers at the file level
  regardless of Python thread count (WAL allows concurrent readers alongside one
  writer, never concurrent writers to the same file), so more threads contending for
  the same lock plausibly makes lock-acquisition retry/backoff worse, not better —
  and per this repo's own HARD RULE, thread-count is not a parameter to guess-adjust
  without first identifying the measured mechanism, which this spec does instead.
- Re-raising `KXBTC15M`/`KXBTCD`/`KXETH15M`'s `min_contracts_by_series` back toward
  their computed P90 values is explicitly **out of scope** for this spec — that's
  the next-action item's own stated follow-up, gated on this fix actually landing
  and being validated, not bundled into it.
- `services/tick_executor.py`'s existing `connection_for()` utility is **not reused
  as-is** — see section 4 for why, and what this spec builds instead.

## 3. Why the obvious fix (reuse `tick_executor.connection_for()`) doesn't apply

`connection_for()` (`services/tick_executor.py:84-99`) is a thread-local SQLite
connection cache — genuinely correct, tested infrastructure, already built for this
exact *kind* of problem. But its own docstring states the precondition for safe use:
a module must be either "(a) restructured so its tick-executor-routed callers use a
dedicated, schema-initialized, single-writer-thread connection separate from that
module's other callers, or (b) proven safe by real measurement of lock-contention
frequency under load." Checked directly against all three files this fix touches —
none is single-caller:

- `signal_log.db` is also written from the event loop directly
  (`services/whale_stream/decision_bridge.py`'s `log_signal()` call, synchronous,
  not on a worker thread).
- `market_history.db` is written from `whale_stream_handlers.py` itself (a
  *different* function, `_process_stream_ticker`, via `record_snapshot_from_ticker`),
  plus `settlement_resolver.py`, `catalog_scan.py`, `main.py`.
- `market_analyst.db` is written from `market_analyst_orchestrator.py`.

None qualifies for (a). This spec's fix is therefore built to satisfy (b) instead —
a real, measured validation gate, not a code-review-only judgment call (see section 6).

A second, independent problem with reusing `connection_for()` directly: its cached
connections use a **50ms busy timeout** (`tick_executor.py:96`,
`PRAGMA busy_timeout = 50`) — **100x shorter** than the 5.0s Python default every one
of these three modules' own `_connect()` already uses (confirmed: none of
`signal_log.py:48`, `market_history.py`'s `_connect()`, or `market_analyst_agent/_db.py:18`
passes an explicit `timeout=`). Reusing `connection_for()` as-is would give the cached
*reader* 100x less patience under contention than the *writer* it's contending with —
a plausible new "database is locked" source on the reader side specifically, separate
from whether total write volume changes at all. This spec's cache uses the modules'
own existing 5.0s convention, not `tick_executor`'s tuning (which was chosen for a
different context and should stay scoped to it).

## 4. Design

**Two changes, not one: where this work runs, and how its connections are cached.**
Revision 1 only changed connection lifecycle and left the work running on Python's
shared default executor (via `asyncio.to_thread`). That's what let issue #145/#150
(a timed-out handler's OS thread isn't actually freed — confirmed live,
`handler_timeouts_total: 45` for the `trade` class, a 20.3s max handler duration
against the 10s timeout) turn this fix's own cache into a compounding leak: each
stuck thread would hold 3 open connections forever. Revision 2 fixes this by moving
the work onto a small, dedicated, explicitly-bounded pool instead — not
`tick_executor`'s shared 2-worker pool (already shared by
`candidate_ledger.claim()`/`record_decision()`, which gate whale-signal detection,
and shouldn't gain a new competitor), a **third, separate** pool sized only for this
workload.

**New: `services/whalewatchers/_scoring_pool.py`.**

```python
"""Dedicated worker pool + thread-local connection cache for
kalshi_trade_tape.py's per-trade scoring work (both the WS-message path,
_process_stream_trade -> fetch_signals, and the candidate-retry path,
score_recovered_trade - see docs/superpowers/specs/2026-09-01-whale-scoring-
connection-reuse-design.md section 1a/4 for why both are in scope).

Deliberately its own pool, not services.tick_executor's shared one (that pool
is also used by candidate_ledger.claim()/record_decision(), which gate every
whale signal - this workload shouldn't compete with it) and not Python's
default asyncio.to_thread executor (shared process-wide with unrelated work
like backup.py/research.py, and unbounded up to 20 threads on this
container - too large to keep issue #145/#150's known thread-leak blast
radius small and observable). 4 workers: this call path normally needs ~1
concurrently (the WS consumer drains one queue item at a time), so this is
headroom for legitimate brief overlap plus the candidate-retry path, not a
guess at a load-bearing capacity number - if #145/#150's leak keeps
happening, all 4 workers eventually get stuck and further scoring work
queues (a visible backlog/latency symptom) rather than spawning unbounded
new OS threads and connections silently, which is the actual property this
design needs: bounded and observable, not eliminated (#145/#150 itself is a
separate, already-tracked bug this spec does not fix).

Connections are cached thread-local, tuned to the 5.0s busy-timeout
convention services/signal_log.py, services/market_history.py, and
services/market_analyst_agent/_db.py's own _connect() functions already use
- not services.tick_executor.connection_for()'s 50ms, which was chosen for
a different (write-capable) context. A parameterized connection_for() was
considered (add busy_timeout_ms/schema_init args, reuse across both pools)
and rejected for this revision: this pool's threads are a genuinely
different, deliberately-separate set from tick_executor's, so a second
small self-contained cache scoped to its own pool is at least as clear as
threading a parameter through a shared one, at the same code cost."""
import asyncio
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="whale-scoring")
_local = threading.local()


async def run(fn: Callable[[], T]) -> T:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, fn)


def cached_read_connection(db_path: Path, schema_init: Callable[[sqlite3.Connection], None]) -> sqlite3.Connection:
    """schema_init runs once, only on this thread's first connect to this
    db_path - the exact "first use per thread runs DDL, reuse skips it"
    behavior revision 1's prose claimed but its code didn't implement."""
    cache = getattr(_local, "connections", None)
    if cache is None:
        cache = _local.connections = {}
    conn = cache.get(db_path)
    if conn is None:
        conn = sqlite3.connect(db_path)  # Python's 5.0s default busy timeout, matching this cache's three target modules' own _connect()
        conn.execute("PRAGMA journal_mode=WAL")
        schema_init(conn)
        cache[db_path] = conn
    return conn
```

**Modify `signal_log.py`, `market_history.py`, `services/market_analyst_agent/_db.py`:**
each gains one new read-only helper (e.g. `signal_log._scoring_read_connection()`)
that calls `_scoring_pool.cached_read_connection(DB_PATH, schema_init=<the module's
own existing schema-creation logic, factored out of its plain _connect() into a
small reusable function both paths call>)` — used *only* by
`recent_sides_for_ticker`/`cluster_factor`/`momentum`/`analyst_lean`. Every other
function in these three modules keeps its existing plain `_connect()` unchanged.

**Modify `kalshi_trade_tape.py`'s `fetch_signals()`:** replace
`asyncio.to_thread(self._process_trades_timed, ...)` with
`await _scoring_pool.run(lambda: self._process_trades_timed(...))`.

**Modify `kalshi_trade_tape.py`'s `score_recovered_trade()`:** this function is
currently synchronous; per section 1a, its call into `_process_trades_sync` now
needs to go through the same pool too, making it `async def`. Verified its one call
site (`candidate_retry.py:167`, `for signal in provider.score_recovered_trade(trade,
market, cfg, now):`) — `run_pending()` is already `async def` (`candidate_retry.py:103`),
so this becomes `for signal in await provider.score_recovered_trade(...)`: a
one-line change at a single call site, not a ripple through multiple callers.

**No change to `_process_trades_sync`'s own logic, gates, or scoring math** — this is
purely a connection-lifecycle and execution-context change. Every query still
executes fresh against live data at call time; nothing is cached, batched, or
deferred.

**WAL checkpoint-starvation:** verified, not assumed — none of the four scoring
functions holds a transaction open across calls; each executes one `SELECT`, fully
drains it via `.fetchall()`, and returns within the same call. No long-running
reader snapshot is ever held that could block a writer's checkpoint.

## 5. Resilience

**Corrected in revision 2** — the file-relocation scenario revision 1's mitigation
targeted isn't the realistic risk. On Linux, deleting/moving a file out from under an
open `sqlite3` connection does not raise `OperationalError`; the open connection
keeps working against the now-unlinked inode while a fresh connection would silently
open an empty new file — a retry-on-exception wouldn't even fire for this case. It's
also not practically reachable in this app: `data/*.db` files are live, permanent,
never deleted while the process runs (CLAUDE.md's own standing invariant) — so this
isn't a real operational risk here, and the spec says so plainly rather than
implying a mitigation covers it.

What the cache does need to handle: `sqlite3.ProgrammingError` from genuine API
misuse (e.g., a connection closed elsewhere by mistake) — catch it at each of the
four call sites, evict the stale cache entry, retry once with a fresh
`cached_read_connection()` call. Cheap, and matches the actual failure mode this
cache could realistically hit, rather than one it can't.

## 6. Testing strategy — the required measured validation gate

Per `connection_for()`'s own documented bar (satisfying its option (b), since none of
the three files qualifies for (a)): **this must be validated against real, measured
lock-contention frequency under load before being trusted, not shipped on code
review alone.**

- Unit: `cached_read_connection()` returns the same connection object on repeated
  calls with the same `db_path` from the same thread, and a *different* connection
  object when called from a different thread (spin up 2 real threads, assert
  `id(conn)` differs — proves thread-locality, not just asserts the docstring).
  The retry-on-broken-connection fallback (section 5) gets its own test: close the
  cached connection out from under it, confirm the next call transparently recovers.
- Integration: extend the existing test fixtures for `recent_sides_for_ticker`/
  `cluster_factor`/`momentum`/`analyst_lean` to run under the new cached path and
  confirm identical results to the current fresh-connection path for the same
  inputs — this is a pure connection-lifecycle change, so behavior must be bit-for-bit
  unchanged.
- **Required before merge, not optional:** a real, scripted load test against a
  paper-mode soak, replaying a burst profile comparable to the actual incident.
  `data/observability.db` stores windowed aggregates (`count`/`min`/`max`/`avg`),
  not a raw per-message trace, so this is a representative synthetic profile derived
  from those aggregates (peak sustained trades/sec over the incident's measured
  window), not a literal message-by-message replay — corrected from revision 1's
  overstated "replay the real historical rate" framing. Measure
  `capture_writer_health`/lock-fault occurrence rate and `last_tick_duration_sec`
  before and after this change, under otherwise-identical conditions, plus worker
  count in `_scoring_pool`'s executor (should stay at or near 4, not silently grow -
  the pool itself has a fixed size, but confirm nothing bypasses it). A clean run
  alone (no faults, tick duration recovers) is the pass criterion for *this* fix
  specifically; it does not by itself prove the original `min_contracts_by_series`
  thresholds are now safe to re-raise, and it does not fix issues #145/#150
  themselves (this design bounds their blast radius, it doesn't resolve the
  underlying cancellation gap) — both stay separate, explicitly out-of-scope items.

## 7. Rollback

Additive: one new module, three small new helper functions in existing modules, no
change to any existing function's signature or behavior for any *other* caller, no
schema change, no config field. Revertible with a normal `git revert`.

## 8. Spec self-review (revision 2)

- Placeholder scan: none — every file, line number, busy-timeout value, and thread
  count above was read from current source during this design.
- Internal consistency: section 4's dedicated pool directly resolves both item 1
  (leak boundedness) and item 2 (thread-count stability) from the review; section
  1a's `score_recovered_trade` scope addition is carried through consistently into
  section 4's implementation and section 6's test plan.
- Scope check: still one cohesive unit — bringing `score_recovered_trade` in scope
  (section 1a) is the same underlying mechanism, not a second project.
- Ambiguity check: "proven safe by real measurement" is concrete in section 6 with a
  named pass criterion; section 4's `is_new`/`schema_init` mechanism replaces
  revision 1's prose-only claim with an actual implementable signature.
- Fix-list recheck (required before trusting this revision, not accepted on its own
  completion claim): re-verified `candidate_retry.py`'s `run_pending()` is already
  `async def` and its one `score_recovered_trade` call site (`:167`) is a plain
  `for` loop — confirmed the "small, contained ripple" claim holds before stating it
  as fact, rather than asserting it from inference. All seven review items addressed;
  no new gap found in this pass beyond that one detail, which is now stated precisely
  rather than left general.
