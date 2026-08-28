"""Runs the trading tick's synchronous SQLite phases off the asyncio loop.
Root-cause report C1: capture_flush_and_titles, resolve_and_record, and the
series_stats N+1 in main.py are the measured source of the 4-13.6s per-tick
loop stalls that starve the WS consumer. This does not change what those
functions do - only where they run (run(), genuinely wired into main.py's
_flush_trade_capture_async / _resolve_and_record_settlements_async /
_build_series_track_record_async and services/whale_stream/decision_bridge.
_handle_signal's candidate_ledger claim()/record_decision() calls).

Realtime data-plane remediation plan, P1 Task 6.

connection_for() status (code-review finding #3/#9, /code-review high pass
against PR #23 - investigated, not wired in, and this is a deliberate
result, not an oversight left unfinished): the bounded-busy-timeout
connection cache below has ZERO production callers. Every module this was
meant for (series_watcher, market_analyst_agent/candidate_log/
market_history/settlement_edge via _resolve_and_record_settlements,
signal_log.series_stats_bulk) still opens its own connection through its
own module's plain _connect(). That was investigated directly rather than
assumed "should be mechanical, just swap the connect call" (the finding's
own framing) - it is not, for two concrete reasons found on inspection of
the real call graph, not intuition:

  1. Schema initialization gap - connection_for() runs no DDL at all (no
     CREATE TABLE IF NOT EXISTS), unlike every module's own _connect(). A
     thread's first-ever touch of a given db_path through connection_for()
     on a schema-less database would raise "no such table" rather than
     silently create it. In practice data/*.db files persist across
     restarts (CLAUDE.md), so this is rare, but it is a real regression
     from every _connect()'s current idempotent-on-every-call guarantee,
     not a hypothetical.

  2. Real cross-thread write contention, not just tick-executor-internal
     concurrency - none of the five target modules is single-caller.
     market_analyst_agent/candidate_log/market_history/settlement_edge are
     each also written directly from the event loop (strategy_engine.py's
     candidate_log.record_rejection calls, whale_stream_handlers.py's
     market_history/settlement_edge writes, several FastAPI routes in
     services/analytics|diagnostics|history/routes.py) and, for
     series_watcher specifically, from two genuinely different OS threads
     after this same PR's own finding #2 fix (record_trade's
     batch-triggered inline flush() on the event loop racing the
     tick_executor's scheduled one). connection_for()'s 50ms busy_timeout
     was chosen for THIS module's own worker pool (fail fast rather than
     block a worker for up to the sqlite3 default 5s) - wiring it into a
     module with real concurrent writers from OTHER threads/contexts would
     very plausibly convert what is today a silent, harmless wait (the
     default 5s busy_timeout absorbing normal lock contention) into a
     newly-common "database is locked" exception on those writes instead -
     a worse, and for series_watcher specifically a directly
     regression-causing (see finding #2's own "zero silent loss" test
     coverage), failure mode than the loop-stall problem this module
     exists to fix. CLAUDE.md's data-plane HARD RULE explicitly forbids
     tuning a timeout/retry parameter without measuring the actual
     bottleneck first; no live-traffic measurement of real lock
     contention on these specific tables exists, so this was not forced in
     on an unverified "should help" basis.

Both connection_for() and run() remain fully implemented and covered by
their own unit tests (tests/test_tick_executor.py) - they are correct,
tested infrastructure genuinely ready to use the day a module here is
either (a) restructured so its tick-executor-routed callers use a
dedicated, schema-initialized, single-writer-thread connection separate
from that module's other callers, or (b) proven safe by real measurement
of lock-contention frequency under load. Until then, this file's own
run() is what's wired in (moving the blocking WORK off the event loop,
the P1 Task 6/7/8 fix), and each target module keeps its own _connect()
(the CONNECTION itself, still a plain 5s-default-busy-timeout open) -
two genuinely different concerns, not one deferred implementation of the
other."""
import asyncio
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tick-executor")
_local = threading.local()


def connection_for(db_path: Path) -> sqlite3.Connection:
    """Thread-local SQLite connection cache keyed by db_path. Each of the
    executor's worker threads gets its own connection (sqlite3 connections
    are not safe to share across threads) and reuses it across calls rather
    than reconnecting every tick."""
    cache = getattr(_local, "connections", None)
    if cache is None:
        cache = _local.connections = {}
    conn = cache.get(db_path)
    if conn is None:
        db_path.parent.mkdir(exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=0.05, check_same_thread=True)
        conn.execute("PRAGMA busy_timeout = 50")
        conn.execute("PRAGMA journal_mode = WAL")
        cache[db_path] = conn
    return conn


async def run(fn: Callable[[], T]) -> T:
    """Await fn() on the tick executor's worker pool instead of the calling
    event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, fn)
