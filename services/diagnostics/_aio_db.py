"""Shared, loop-scoped aiosqlite connection cache for
services/diagnostics/diagnostics.py and services/series_watcher.py's
read-only functions (event-loop-blocking-elimination Fix 2,
docs/superpowers/specs/2026-09-01-event-loop-blocking-elimination-design.md).
One persistent aiosqlite.Connection per (event loop, db_path) pair, opened
on first use and reused.

Read-only, low-frequency callers (dashboard polls at ~5s; services/research/
research.py's on-demand report), not the per-trade whale-scoring hot path
(services/whalewatchers/_scoring_pool.py uses 2 connections/file for that
reason) - a single connection per file is enough headroom here. If
concurrent-caller queueing is ever measured as a real problem, the same
2-per-file escape hatch is available there, not guessed preemptively here
(CLAUDE.md's data-plane HARD RULE).

MEASURED TRADEOFF, stated rather than left implicit (PR adversarial review,
2026-09-01 - neither number changes behaviour here, both are recorded so the
next session inherits fact instead of assertion):

  1. Concurrency. aiosqlite serialises every operation on a connection onto
     that connection's single worker thread, so one connection per (loop,
     file) means diagnostics.run_offline()'s reads against a given DB file
     now queue behind each other. The _diagnostics_pool this replaced gave
     run_offline() 2 concurrent workers. The design spec is explicit that
     matching prior throughput would mean >1 connection per file (hence 2
     for the scoring pool). With GET /api/quality/summary measured at 20.4s
     wall against a ~5s dashboard poll, several requests overlap, so
     per-request latency can degrade under overlap even though aggregate
     throughput is roughly unchanged. The "enough headroom here" call above
     is therefore a deliberate simplicity choice, NOT a measured result.
  2. On-loop CPU. run_offline()'s pure-Python aggregation used to run on a
     worker thread and now runs on the event loop; only the SQL and two
     explicitly wrapped calls are off it. Measured per run_offline() call
     against the real data/*.db: check_threshold_integrity ~78ms,
     selectivity_curve ~86ms, check_confidence_input_coverage ~89ms,
     trade_analytics.build_trade_history ~24ms across 16 calls - i.e. at
     least ~280ms of contiguous, un-awaited on-loop CPU, with several other
     checks plus funnel()/reconcile() unmeasured on top. Net: a large win
     for GET /api/diagnostics and /api/diagnostics/series/{s} (previously
     ~20s fully on-loop), a regression for GET /api/quality/summary
     (previously 0ms on-loop, via the pool). Deliberately not "fixed" here
     with asyncio.gather or a second connection per file: that is a
     behaviour change needing its own measurement, not a drive-by.

Keyed by (event loop object, db_path), not db_path alone - for connection
LIFETIME/ownership reasons, not loop affinity. services/research/
research.py's build_report() calls into this module from a plain sync
function that itself runs via asyncio.to_thread(run_and_store, cfg) - a
worker thread with no running event loop of its own - and reaches
diagnostics.run_offline() through its own throwaway asyncio.run() call,
after which it calls close_for_current_loop(). Keyed by path alone, that
cleanup would close connections still in use by the main app's long-lived
loop, and entries belonging to an already-dead throwaway loop could never
be told apart from the main loop's to be cleaned up at all. The loop key is
what makes "close exactly what this loop opened" expressible.

An aiosqlite.Connection is NOT bound to the loop that created it in the
pinned version (0.22.1) - verified by reading the installed
aiosqlite/core.py, not recalled: Connection.__init__ warns that it "no
longer uses the `loop` parameter", Connection._execute creates its future
via asyncio.get_event_loop().create_future() on whatever loop is CALLING,
and the worker thread returns results with
future.get_loop().call_soon_threadsafe(...). The transport is a plain
SimpleQueue on a plain Thread. An earlier version of this docstring claimed
the opposite ("aiosqlite's internal read/write queue is loop-bound") and
was factually wrong (PR adversarial review finding I3, 2026-09-01). That
cross-loop freedom is load-bearing here, not trivia: it is exactly why the
process-exit hook below can close connections opened under other, already-
finished loops from one fresh asyncio.run() loop.

Keying by the loop object itself (not id()) is collision-proof: holding the
loop object as a dict key keeps a strong reference to it, structurally
preventing CPython from reusing its address for an unrelated new loop.

The lock guarding first-open-per-key is ALSO scoped per loop (a dict of
locks, not one shared asyncio.Lock) for the identical reason: asyncio's own
synchronization primitives are themselves not safe to use across two
different event loops (a waiter Future created under one loop, released by
another, calls that other loop's internals non-threadsafely) - a single
shared Lock would silently reintroduce the exact cross-loop hazard this
whole module exists to eliminate for Connection objects specifically
(adversarial review finding C, 2026-09-01)."""
import asyncio
import atexit
import contextlib
import sqlite3
import threading
from pathlib import Path
from typing import Awaitable, Callable

import aiosqlite

_connections: dict[tuple[asyncio.AbstractEventLoop, Path], aiosqlite.Connection] = {}
_locks: dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}


def _key(db_path: Path) -> tuple[asyncio.AbstractEventLoop, Path]:
    return (asyncio.get_running_loop(), db_path)


def _close_all_at_process_exit() -> None:
    """Close every cached connection before the interpreter joins threads.

    aiosqlite gives each Connection its own NON-daemon OS thread
    (aiosqlite/core.py: `Thread(target=_connection_worker_thread, ...)`, no
    daemon=True), and that thread only exits when it receives the stop
    sentinel that Connection.close()/stop() sends. Every connection this
    module caches and never closes therefore keeps a non-daemon thread
    alive, and CPython's shutdown JOINS every non-daemon thread - so a
    plain interpreter exit hangs forever. That is not hypothetical: before
    this hook existed, `pytest tests/test_diagnostics_routes.py` printed
    "7 passed" and then hung until killed (PR adversarial review finding
    C1, 2026-09-01). The full suite only exited by accident of collection
    order, which xdist's -n 4 and testmon selection do not preserve.

    threading._register_atexit, NOT atexit.register: threading._shutdown()
    runs the threading-atexit callbacks and only THEN joins non-daemon
    threads, whereas atexit.register() fires at interpreter shutdown, which
    is after that join has already blocked forever. Measured, not assumed -
    an identical probe registering the same closer both ways: atexit ->
    exit code 124 (killed at 15s, hook never ran), _register_atexit ->
    exit code 0 in under a second. This is the same mechanism, for the same
    reason, that concurrent.futures.thread uses for the ThreadPoolExecutor
    this module's predecessor (_diagnostics_pool) relied on; see its
    comment "Register for `_python_exit()` to be called just before joining
    all non-daemon threads. This is used instead of `atexit.register()`".

    Closing from a fresh asyncio.run() loop is sound precisely because
    aiosqlite 0.22.1 connections are not loop-bound (see module docstring):
    _execute builds its future on the CALLING loop, so a connection opened
    under a long-since-finished loop still closes cleanly here.
    """
    if not _connections:
        return

    async def _close_all() -> None:
        for conn in list(_connections.values()):
            with contextlib.suppress(Exception):
                await conn.close()
        _connections.clear()
        _locks.clear()

    # Suppressed: this runs during interpreter shutdown, where a raised
    # exception is unhelpful noise. A failure here costs a leaked thread
    # (the pre-existing behaviour), never a crash on a working exit path.
    with contextlib.suppress(Exception):
        asyncio.run(_close_all())


# _register_atexit is CPython-internal (present since 3.9, used by
# concurrent.futures itself). Guarded so a future Python that drops it
# degrades to plain atexit rather than failing at import; that fallback is
# strictly weaker - it cannot beat the non-daemon join - but it keeps the
# module importable, and reset()/close_for_current_loop() still work.
_register_threading_atexit = getattr(threading, "_register_atexit", None)
if _register_threading_atexit is not None:
    _register_threading_atexit(_close_all_at_process_exit)
else:  # pragma: no cover - CPython always provides it today
    atexit.register(_close_all_at_process_exit)


def _lock_for_current_loop() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _locks.get(loop)
    if lock is None:
        lock = _locks[loop] = asyncio.Lock()
    return lock


async def connection_for(
    db_path: Path,
    schema_init: Callable[[aiosqlite.Connection], Awaitable[None]] | None = None,
) -> aiosqlite.Connection:
    """schema_init, when given, runs exactly once - only on this key's
    first-ever open, never on a cache hit - same contract as
    services/whalewatchers/_scoring_pool.py's cached_read_connection(). Pass
    nothing when the target DB file's schema is already guaranteed to exist
    by its own write-path module (every diagnostics.py caller); pass one
    when the caller previously relied on a plain sqlite3.connect()-adjacent
    helper that also ran CREATE TABLE IF NOT EXISTS on every call
    (series_watcher.py's three converted read functions, replacing
    _connect()'s self-healing schema creation - dropping this silently
    would collapse the "no data yet" vs. "store unreadable" distinction
    diagnostics.py's checks are designed around; adversarial review finding
    A, 2026-09-01).

    A cached connection is liveness-probed before being handed back, and a
    dead one is evicted and reopened - the self-healing half of the
    contract named above, which this function previously claimed but did
    not implement (PR adversarial review finding I1, 2026-09-01). Before
    this module existed every call opened its own connection, so any
    transient breakage healed on the next call; without the probe a broken
    cached connection would be returned forever and the diagnostics
    subsystem would report "unreadable" permanently and quietly, which is
    the exact silent-degradation shape diagnostics.py exists to avoid."""
    key = _key(db_path)
    conn = _connections.get(key)
    if conn is not None:
        try:
            # execute_fetchall, not execute: same liveness signal as
            # _scoring_pool's conn.execute("SELECT 1"), but one worker
            # round-trip instead of two and no aiosqlite.Cursor wrapper
            # left unclosed on a connection that lives for the whole
            # process.
            await conn.execute_fetchall("SELECT 1")
            return conn
        except (ValueError, sqlite3.ProgrammingError):
            # ValueError is the real dead-connection signal for aiosqlite,
            # NOT sqlite3.ProgrammingError as in the sync sibling: verified
            # against the installed 0.22.1 - Connection._conn raises
            # ValueError("no active connection") once close() has cleared
            # _connection, and Connection._execute raises
            # ValueError("Connection closed") when _running is False.
            # That ValueError is NOT a sqlite3.Error subclass, so it falls
            # straight through callers' `except sqlite3.Error` degradation
            # branches - which is why an unprobed dead connection would
            # surface as a 500 rather than degrade honestly.
            # sqlite3.ProgrammingError, by contrast, IS a sqlite3.Error
            # (MRO: ProgrammingError -> DatabaseError -> Error), so callers
            # would degrade on it normally; it is caught here only
            # defensively, for the underlying sqlite3 handle being closed
            # from inside the worker thread, and is in practice unreachable
            # since sqlite3 objects are thread-bound and nothing outside
            # that worker thread can close the handle.
            #
            # Evict only if this exact object is still cached: a concurrent
            # caller on this loop may already have replaced it with a fresh
            # healthy connection while we were awaiting the probe, and
            # deleting the key blindly would throw that one away.
            if _connections.get(key) is conn:
                del _connections[key]
            # Best-effort: reclaims the worker thread if the connection is
            # only half-dead. A no-op (returns immediately) when it is
            # already closed - verified, not assumed: close() short-circuits
            # on `self._connection is None`, and repeated closes on an
            # already-closed connection return OK rather than hanging.
            with contextlib.suppress(Exception):
                await conn.close()
    async with _lock_for_current_loop():
        conn = _connections.get(key)
        if conn is None:
            conn = await aiosqlite.connect(db_path)
            conn.row_factory = aiosqlite.Row
            if schema_init is not None:
                try:
                    await schema_init(conn)
                except BaseException:
                    # Without this the connection is neither cached nor
                    # closed, so its non-daemon worker thread lives for the
                    # rest of the process - once per failed call. funnel()
                    # runs once per watched series per run_offline(), on a
                    # route polled every ~5s, so an unreadable
                    # series_watcher.db leaked ~8 threads every 5s
                    # indefinitely while every caller degraded "honestly"
                    # via its except sqlite3.Error branch (PR adversarial
                    # review finding I2, 2026-09-01). Suppressed on close so
                    # a cleanup failure cannot mask the real schema error.
                    with contextlib.suppress(Exception):
                        await conn.close()
                    raise
            _connections[key] = conn
        return conn


async def close_for_current_loop() -> None:
    """Closes and evicts only the connections (and this loop's lock entry)
    opened under the currently running loop. services/research/research.py
    calls this once its diagnostics.run_offline() call returns, inside the
    same throwaway asyncio.run() invocation - without it, every research
    report generated over the process's lifetime would leak one connection
    per DB file touched (a new throwaway loop, and therefore a new cache
    key, every single call)."""
    loop = asyncio.get_running_loop()
    # list(_connections) snapshots the keys before filtering: connection_for()
    # can be inserting from a DIFFERENT loop on a different OS thread (that is
    # this module's whole reason for existing), and iterating the live dict
    # would then risk "RuntimeError: dictionary changed size during iteration"
    # inside research.py's finally - masking its report and skipping cleanup.
    # Unreproduced in ~1000 forced-interleaving attempts, fixed anyway because
    # it costs nothing (PR adversarial review finding M3, 2026-09-01).
    stale = [key for key in list(_connections) if key[0] is loop]
    for key in stale:
        # pop(key, None), not pop(key): connection_for()'s liveness probe is
        # now a second deleter of _connections alongside this function, so a
        # key present when the snapshot above was taken can in principle be
        # gone by the time we reach it, and a bare pop would raise KeyError
        # here - inside research.py's finally, masking its report. Not
        # reachable on today's call graph (same theoretical class as the M3
        # snapshot above); closed because it costs nothing.
        conn = _connections.pop(key, None)
        if conn is not None:
            await conn.close()
    _locks.pop(loop, None)


async def reset() -> None:
    """Test-only: close every cached connection regardless of loop, and
    clear the lock registry. Each test monkeypatches DB_PATH to a fresh
    tmp_path, so a connection cached from a prior test would otherwise
    point at an already-deleted file."""
    for conn in list(_connections.values()):
        await conn.close()
    _connections.clear()
    _locks.clear()
