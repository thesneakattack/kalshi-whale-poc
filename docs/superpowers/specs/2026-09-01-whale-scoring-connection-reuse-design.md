# Whale-Scoring Per-Trade Connection Reuse — Design

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

**New: `services/whalewatchers/_scoring_connection_cache.py`.** A thread-local
connection cache scoped exclusively to this hot path — not `tick_executor.py`, not a
change to `signal_log.py`/`market_history.py`/`market_analyst_agent`'s own `_connect()`
(those stay exactly as-is for every other, event-loop-side caller):

```python
"""Thread-local SQLite connection cache for kalshi_trade_tape.py's per-trade
scoring reads only (recent_sides_for_ticker, cluster_factor, _trend_factor,
_analyst_factor) - see docs/superpowers/specs/2026-09-01-whale-scoring-
connection-reuse-design.md for why this is a purpose-built cache, not a reuse
of services.tick_executor.connection_for(): none of signal_log.db/
market_history.db/market_analyst.db is single-writer (each is also written
from the event loop directly, by a different caller than this one), so
tick_executor's own documented safety precondition for connection reuse
isn't met by simply importing its cache - this one is scoped so ONLY these
four read-only call sites ever touch it, and tuned to the 5.0s busy-timeout
convention those three modules' own _connect() already uses, not
tick_executor's unrelated 50ms choice."""
import sqlite3
import threading
from pathlib import Path

_local = threading.local()


def cached_read_connection(db_path: Path) -> sqlite3.Connection:
    cache = getattr(_local, "connections", None)
    if cache is None:
        cache = _local.connections = {}
    conn = cache.get(db_path)
    if conn is None:
        conn = sqlite3.connect(db_path)  # Python's 5.0s default busy timeout, matching every caller of this cache's own target modules
        conn.execute("PRAGMA journal_mode=WAL")
        cache[db_path] = conn
    return conn
```

**Modify `signal_log.py`, `market_history.py`, `services/market_analyst_agent/_db.py`:**
each gains one new read-only helper (e.g. `signal_log._connect_for_scoring_read()`)
that calls `cached_read_connection(DB_PATH)` instead of opening fresh — used *only*
by `recent_sides_for_ticker`/`cluster_factor`/`momentum`/`analyst_lean`, the four
functions this hot path actually calls. Every other function in these three modules
keeps its existing plain `_connect()` unchanged. This is deliberately narrow: the
schema-creation (`CREATE TABLE IF NOT EXISTS`) still runs on a connection's *first*
use per thread (satisfying `connection_for()`'s own schema-init concern — these
tables are long-lived and already exist in every real deployment, but the DDL call
itself is cheap and idempotent, so it costs nothing to keep it on first touch rather
than assume the table exists).

**No change to `_process_trades_sync`'s own logic, gates, or scoring math** — this is
purely a connection-lifecycle change. Every query still executes fresh against live
data at call time; nothing is cached, batched, or deferred.

## 5. Resilience

If a cached connection ever goes bad (e.g., the underlying file was moved/deleted
mid-run — not expected, but not impossible), the cache holds a broken connection
until process restart, which is worse than today's per-call fresh-connect behavior
in that one specific failure mode. Mitigation: wrap each of the four call sites'
cached-connection use in a single retry-once-with-a-fresh-connection fallback (catch
`sqlite3.OperationalError`/`sqlite3.ProgrammingError`, evict the cache entry, retry
once) — cheap, and closes the one regression this design would otherwise introduce
relative to the current always-fresh behavior.

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
- **Required before merge, not optional:** a real, scripted load test replaying a
  volume comparable to the actual incident (BTC15M-shaped burst — the real historical
  message rate from the incident window is available via `data/observability.db`'s
  captured metrics, use that rather than an invented number) against a paper-mode
  soak, measuring `capture_writer_health`/lock-fault occurrence rate and
  `last_tick_duration_sec` before and after this change, under otherwise-identical
  conditions. A clean run alone (no faults, tick duration recovers) is the pass
  criterion for *this* fix specifically; it does not by itself prove the original
  `min_contracts_by_series` thresholds are now safe to re-raise — that's the
  separate, explicitly out-of-scope follow-up item.

## 7. Rollback

Additive: one new module, three small new helper functions in existing modules, no
change to any existing function's signature or behavior for any *other* caller, no
schema change, no config field. Revertible with a normal `git revert`.

## 8. Spec self-review

- Placeholder scan: none — every file, line number, and busy-timeout value above was
  read from current source during this design.
- Internal consistency: section 3's rejection of naive `connection_for()` reuse and
  section 4's design directly address both stated reasons (multi-caller files,
  timeout mismatch) — the design doesn't just note the problems, it resolves both.
- Scope check: single cohesive unit (one connection-lifecycle change across three
  already-related call sites feeding one scoring pipeline) — does not need further
  decomposition.
- Ambiguity check: "proven safe by real measurement" (connection_for()'s own
  language) is made concrete in section 6 as a specific required load test with a
  named pass criterion, not left as a vague aspiration.
