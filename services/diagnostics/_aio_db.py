"""Shared, loop-scoped aiosqlite connection POOL for
services/diagnostics/diagnostics.py and services/series_watcher.py's
read-only functions (event-loop-blocking-elimination Fix 2,
docs/superpowers/specs/2026-09-01-event-loop-blocking-elimination-design.md).
A variable number of persistent aiosqlite.Connections per (event loop,
db_path) pair - see "Elastic sizing" below - opened on first use, handed
out round-robin, and reused.

LIVE-MEASURED, NOT ASSUMED (fast-follow to #420, 2026-09-02): the original
version of this module used exactly 1 connection per (loop, file) - its own
docstring named 2-per-file as an available escape hatch "if concurrent-
caller queueing is ever measured as a real problem", deliberately not
applied speculatively per CLAUDE.md's data-plane HARD RULE. It was then
measured, live, the same day: 5 concurrent GET /api/quality/summary
requests against real data/*.db volumes (23,957-124,859 rows per table)
serialized on aiosqlite's one-worker-thread-per-connection model for
MINUTES.

Elastic sizing, not a second fixed number: `_MIN_POOL_SIZE` (2) is the
floor a quiet key never drops below; `_MAX_POOL_SIZE` (10) is a cap chosen
as cheap headroom (a diagnostics-only, low-frequency workload can afford a
handful of idle connections far more easily than another multi-minute
stall), not derived from a specific capacity measurement - a later
investigation may want to revisit it alongside whatever else it measures.
The pool grows lazily, per key, based on `_in_flight` - a live count of
concurrent connection_for() callers for that exact key - the first time
real demand exceeds the pool's current size, and never shrinks back down.
(An earlier attempt at citing services/whalewatchers/_scoring_pool.py as a
"2-per-file precedent" for a FIXED size was wrong on the fact - that pool
actually runs 4 workers - and wrong on the reasoning even corrected: its
own docstring says its exact worker count "is not a load-bearing capacity
guess" for a structurally different, per-trade hot-path workload. Elastic
sizing here doesn't need that precedent to justify itself.)

Read-only, low-frequency callers (dashboard polls at ~5s; services/research/
research.py's on-demand report) - not the per-trade whale-scoring hot path.

MEASURED, in order of what actually mattered (2026-09-02 - two mechanisms
were live-measured as real, and one prior claim about a third was
independently falsified by adversarial review before merge):

  1. Query cost dominates. `check_confidence_input_coverage()`
     (services/diagnostics/diagnostics.py) was the one check in that file
     with no purpose-matched `since_ts` default, so a caller that didn't
     pass one (run_offline()'s own default) fell through to
     signal_log.resolved_signals_with_factors()'s unscoped scan - 124,859
     rows, already flagged in docs/open-decisions.md's Task 9 entry with
     this exact fix agreed but not yet applied. Bounding it to a 24h window
     (matching this file's own check_threshold_integrity/
     check_price_band_adherence/check_runway_at_entry convention) dropped a
     single run_offline() call from ~20.4s to 1.88s, and 5 concurrent calls
     from 269.79s to 43.89s against real production data - by far the
     largest single improvement measured across this whole fast-follow.
  2. Connection-pool depth. RESOLVED for identical-endpoint concurrent load
     by the elastic pool above (confirmed via direct inspection: config_
     performance.db grew 2->4, signal_log.db 2->3 under real 5-way
     concurrent load). Measured HONESTLY, not oversold: growing the pool
     alone (before finding 1's fix was applied) did NOT meaningfully reduce
     total wall time for 5 concurrent calls against real data (still
     ~4.5min) - the dominant cost there was finding 1, not connection
     count. The pool is still worth keeping: it demonstrably helps once
     finding 1's fix is also in place (43.89s, not the >90s a fixed
     single-connection or fixed-2-connection pool alone would still show),
     and it is real, cheap insurance against a future unbounded query this
     fast-follow didn't happen to catch.
  3. On-loop CPU / no yield points - CLAIMED, THEN FALSIFIED. An earlier
     version of this fast-follow added `await asyncio.sleep(0)` between
     each of run_offline()'s ~14 sequential checks, reasoning that nothing
     yielded control between them. Adversarial review disproved this
     directly: aiosqlite's own `_execute` already `return`s via
     `await future`, so every one of those checks' awaited DB calls was
     ALREADY yielding control back to the loop - measured at ~1,100 real
     yields per run_offline() call, identically with or without the added
     sleep(0)s. The sleep(0) calls and the test asserting they mattered
     were both removed rather than left in on a claim that didn't survive
     scrutiny - see services/diagnostics/diagnostics.py's run_offline() for
     the correction note. This is recorded here, not deleted from history,
     specifically so a future session doesn't independently reach for the
     same disproven fix.

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

_MIN_POOL_SIZE = 2
_MAX_POOL_SIZE = 10

# Each key maps to a list of slots, sized ELASTICALLY between _MIN_POOL_SIZE
# and _MAX_POOL_SIZE - nothing is reserved up front. A slot is either a live
# aiosqlite.Connection or None (not yet created, or evicted after a
# liveness-probe failure and awaiting replacement under the lock below).
_connections: dict[tuple[asyncio.AbstractEventLoop, Path], list[aiosqlite.Connection | None]] = {}
_round_robin: dict[tuple[asyncio.AbstractEventLoop, Path], int] = {}
_locks: dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}

# Guard-rail against burst throttling, NOT a root-cause fix (2026-09-02
# fast-follow to #420): counts connection_for() calls currently in flight
# per key, on this loop. When it exceeds the pool's current size, the pool
# grows (lazily, up to _MAX_POOL_SIZE) rather than making the extra callers
# queue for a fixed handful of workers - live-measured need: 5 concurrent
# identical requests against real data (23,957-124,859 rows/table) still
# took minutes even after bumping the prior fixed 1-connection design to a
# fixed 2, because the fix's actual target - real per-query cost against
# real data volumes - is untouched by connection count alone. Grows only,
# never shrinks: this module's total footprint even at _MAX_POOL_SIZE across
# every DB file diagnostics.py/series_watcher.py touch is a handful of
# threads, a deliberately cheap price for not compounding a genuine burst
# into a multi-minute stall. The actual root cause (per-query cost at real
# row counts) is untouched here on purpose - see the module's "MEASURED
# TRADEOFF" section above.
_in_flight: dict[tuple[asyncio.AbstractEventLoop, Path], int] = {}


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

    def _all_conns() -> list[aiosqlite.Connection]:
        # Pool-aware since the fast-follow above: each value is now a list
        # of slots, some possibly None (never created, or mid-eviction).
        return [c for pool in _connections.values() for c in pool if c is not None]

    async def _close_all() -> None:
        for conn in _all_conns():
            with contextlib.suppress(Exception):
                await conn.close()
        _connections.clear()
        _locks.clear()

    try:
        asyncio.run(_close_all())
    except Exception:
        # asyncio.run(_close_all()) failed - fall back to a loop-free close
        # so this doesn't reproduce C1's hang. If left unclosed here, every
        # cached connection keeps its non-daemon worker thread alive and
        # threading._shutdown() joins it forever (see this function's own
        # docstring above) - a raised exception here is NOT the benign
        # "leaked thread" cost an earlier version of this comment claimed;
        # it IS the hang. Connection.stop() (aiosqlite/core.py) needs no
        # running event loop: it wraps the future creation in its own
        # try/except and puts the stop sentinel on the connection's plain
        # SimpleQueue regardless, so the worker thread still exits even
        # though we can't cleanly await close() here. Reproduced/verified,
        # not assumed (PR adversarial review finding F1, 2026-09-01): a
        # forced asyncio.run failure hangs (EXIT=124) without this fallback
        # and exits cleanly (EXIT=0) with it.
        for conn in _all_conns():
            with contextlib.suppress(Exception):
                conn.stop()
        _connections.clear()
        _locks.clear()


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
    """schema_init, when given, runs exactly once per slot - only on that
    slot's first-ever open, never on a cache hit - same contract as
    services/whalewatchers/_scoring_pool.py's cached_read_connection(). Pass
    nothing when the target DB file's schema is already guaranteed to exist
    by its own write-path module (every diagnostics.py caller); pass one
    when the caller previously relied on a plain sqlite3.connect()-adjacent
    helper that also ran CREATE TABLE IF NOT EXISTS on every call
    (series_watcher.py's three converted read functions, replacing
    _connect()'s self-healing schema creation - dropping this silently
    would collapse the "no data yet" vs. "store unreadable" distinction
    diagnostics.py's checks are designed around; adversarial review finding
    A, 2026-09-01). schema_init's own DDL is idempotent (CREATE TABLE IF NOT
    EXISTS) so running it once per pool slot, not once per key, is correct -
    each slot is a genuinely separate connection.

    Hands back one connection per (loop, db_path), round-robin, from a pool
    sized ELASTICALLY between _MIN_POOL_SIZE and _MAX_POOL_SIZE based on
    observed concurrent demand for this exact key - see the module
    docstring's live-measured rationale. Nothing is reserved up front: a
    quiet key stays at _MIN_POOL_SIZE forever; a bursty one grows, lazily,
    the first time concurrent callers actually exceed its current size, and
    never shrinks back down (a diagnostics-only, low-frequency workload can
    afford to keep a few extra idle connections far more cheaply than it can
    afford another multi-minute stall).

    The selected connection is liveness-probed before being handed back, and
    a dead one is evicted and reopened in its own slot - the self-healing
    half of the contract named above, which this function previously claimed
    but did not implement (PR adversarial review finding I1, 2026-09-01).
    Before this module existed every call opened its own connection, so any
    transient breakage healed on the next call; without the probe a broken
    cached connection would be returned forever and the diagnostics
    subsystem would report "unreadable" permanently and quietly, which is
    the exact silent-degradation shape diagnostics.py exists to avoid.

    Design rule keeping this race-free (2026-09-02 fast-follow, round 2 -
    the first version of this elastic pool shipped two real concurrency
    bugs, caught by adversarial review before merge, not in production):
    `_round_robin[key]` is advanced ONLY in the same step that hands back a
    connection this function has itself just confirmed live or just
    created - never speculatively, never before a probe's outcome is known.
    The FAST path below never mutates `pool`/`_round_robin` on a failed
    probe; it just falls through to the lock. Every eviction, every
    creation, and every final return happens under the lock, for the exact
    index that will be returned - so no OTHER coroutine's fast-path probe
    (which never takes the lock) can null a slot in the gap between "the
    locked section decided to return index N" and "the locked section
    actually reads pool[N]", because that gap no longer exists: reading and
    validating pool[N] IS what happens under the lock, immediately before
    return, not before it."""
    key = _key(db_path)
    _in_flight[key] = _in_flight.get(key, 0) + 1
    try:
        pool = _connections.get(key)
        # Fast path requires BOTH a fully-filled pool AND enough of it to
        # cover current demand - a pool with no None slots can still be
        # under-sized if more callers are concurrently in flight for this
        # key than it has connections; that case must reach the lock below,
        # where growth happens, rather than round-robin over too few
        # connections and reproduce the exact stall this guard-rail exists
        # to prevent.
        if pool is not None and None not in pool and _in_flight[key] <= len(pool):
            # _round_robin[key] is read but NOT advanced here - only a
            # confirmed-successful probe below earns the advance. Two
            # concurrent fast-path callers reading the same un-advanced
            # index and probing the same connection is safe (aiosqlite
            # serialises operations on one connection onto its own worker
            # thread) and merely redundant, never incorrect.
            idx = _round_robin.get(key, 0) % len(pool)
            conn = pool[idx]
            try:
                # execute_fetchall, not execute: same liveness signal as
                # _scoring_pool's conn.execute("SELECT 1"), but one worker
                # round-trip instead of two and no aiosqlite.Cursor wrapper
                # left unclosed on a connection that lives for the whole
                # process.
                await conn.execute_fetchall("SELECT 1")
                _round_robin[key] = idx + 1
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
                # Deliberately NO mutation of pool/_round_robin here - fall
                # through to the lock, which owns every eviction. Mutating
                # here (as an earlier version did) is exactly the race the
                # adversarial review caught: this coroutine could null a
                # slot, or advance the counter past it, while a DIFFERENT
                # coroutine's locked section is mid-way through deciding
                # what to return, handing back a connection nobody validated
                # or a None outright.
                pass
        async with _lock_for_current_loop():
            pool = _connections.get(key)
            # Elastic sizing: grow toward however many callers are
            # concurrently asking for this exact key right now, capped at
            # _MAX_POOL_SIZE, floored at _MIN_POOL_SIZE - never shrinks
            # (existing valid slots are kept, only new None slots are ever
            # appended). Re-read _in_flight[key] here (under the lock, not
            # the value captured before acquiring it): more callers may have
            # arrived while this one was waiting on the lock, and they
            # should not have to wait a second round-trip to be sized for.
            target_size = min(_MAX_POOL_SIZE, max(_MIN_POOL_SIZE, _in_flight[key]))
            if pool is None:
                pool = [None] * target_size
                _connections[key] = pool
                _round_robin.setdefault(key, 0)
            elif len(pool) < target_size:
                pool.extend([None] * (target_size - len(pool)))

            # Everything past this point - probing the selected slot,
            # evicting it if dead, creating a replacement if empty - is a
            # single lock-held sequence for the ONE index being returned.
            # No other index is touched proactively; a pool slot that
            # nothing has round-robined to yet stays None until it is,
            # which is fine (lazy-fill, not eager-fill) and means there is
            # only ever one connection-creation await sequence per call,
            # not a loop of them - fewer await points under the lock is
            # less race surface, not just less code.
            idx = _round_robin.get(key, 0) % len(pool)
            conn = pool[idx]
            if conn is not None:
                try:
                    await conn.execute_fetchall("SELECT 1")
                    _round_robin[key] = idx + 1
                    return conn
                except (ValueError, sqlite3.ProgrammingError):
                    # Same liveness signal as the fast path above - the
                    # locked section MUST also probe before trusting a
                    # non-None slot, not just check "is it None": a slot
                    # can be non-None yet dead if nothing has round-robined
                    # to it (and therefore probed it) since it died. This is
                    # exactly finding C2 from the adversarial review - the
                    # first version of this pool's locked path only ever
                    # created connections for None slots and otherwise
                    # trusted "not None" as "alive", which is not the same
                    # claim.
                    with contextlib.suppress(Exception):
                        await conn.close()
                    pool[idx] = None

            new_conn = await aiosqlite.connect(db_path)
            new_conn.row_factory = aiosqlite.Row
            if schema_init is not None:
                try:
                    await schema_init(new_conn)
                except BaseException:
                    # Without this the connection is neither cached nor
                    # closed, so its non-daemon worker thread lives for
                    # the rest of the process - once per failed call.
                    # funnel() runs once per watched series per
                    # run_offline(), on a route polled every ~5s, so an
                    # unreadable series_watcher.db leaked ~8 threads
                    # every 5s indefinitely while every caller degraded
                    # "honestly" via its except sqlite3.Error branch
                    # (PR adversarial review finding I2, 2026-09-01).
                    # Suppressed on close so a cleanup failure cannot
                    # mask the real schema error.
                    with contextlib.suppress(Exception):
                        await new_conn.close()
                    # If every other slot is also still empty (this was
                    # the pool's first-ever fill attempt and it failed
                    # on its one and only touched slot so far), drop the
                    # whole pool entry rather than leaving a still-cached
                    # key that points at nothing - "nothing cached for
                    # this key" should mean exactly that, so a later
                    # retry starts a clean pool creation instead of
                    # finding a stale all-None list here. A
                    # partially-filled pool (some other slot already
                    # succeeded on an earlier call) is left alone - those
                    # connections are real and usable.
                    if all(slot is None for slot in pool) and _connections.get(key) is pool:
                        del _connections[key]
                        _round_robin.pop(key, None)
                    raise
            pool[idx] = new_conn
            _round_robin[key] = idx + 1
            return pool[idx]
    finally:
        _in_flight[key] -= 1
        if _in_flight[key] <= 0:
            _in_flight.pop(key, None)


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
        pool = _connections.pop(key, None)
        if pool is not None:
            for conn in pool:
                if conn is not None:
                    await conn.close()
        _round_robin.pop(key, None)
    _locks.pop(loop, None)


async def reset() -> None:
    """Test-only: close every cached connection regardless of loop, and
    clear the lock and round-robin registries. Each test monkeypatches
    DB_PATH to a fresh tmp_path, so a connection cached from a prior test
    would otherwise point at an already-deleted file."""
    for pool in list(_connections.values()):
        for conn in pool:
            if conn is not None:
                await conn.close()
    _connections.clear()
    _round_robin.clear()
    _locks.clear()
