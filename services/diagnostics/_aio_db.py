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

Keyed by (event loop object, db_path), not db_path alone: services/research/
research.py's build_report() calls into this module from a plain sync
function that itself runs via asyncio.to_thread(run_and_store, cfg) - a
worker thread with no running event loop of its own - and reaches
diagnostics.run_offline() through its own throwaway asyncio.run() call.
An aiosqlite.Connection is bound to the event loop that created it; handing
a connection opened under the main app's long-lived loop to code running
under a different, temporary loop (or vice versa) is a real correctness
hazard, not a hypothetical - aiosqlite's internal read/write queue is
loop-bound. Keying by the loop object itself (not id()) means research.py's
throwaway loop always gets its own fresh connections, never the main loop's,
and is collision-proof: holding the loop object as a dict key keeps a strong
reference to it, structurally preventing CPython from reusing its address
for an unrelated new loop.

The lock guarding first-open-per-key is ALSO scoped per loop (a dict of
locks, not one shared asyncio.Lock) for the identical reason: asyncio's own
synchronization primitives are themselves not safe to use across two
different event loops (a waiter Future created under one loop, released by
another, calls that other loop's internals non-threadsafely) - a single
shared Lock would silently reintroduce the exact cross-loop hazard this
whole module exists to eliminate for Connection objects specifically
(adversarial review finding C, 2026-09-01)."""
import asyncio
from pathlib import Path
from typing import Awaitable, Callable

import aiosqlite

_connections: dict[tuple[asyncio.AbstractEventLoop, Path], aiosqlite.Connection] = {}
_locks: dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}


def _key(db_path: Path) -> tuple[asyncio.AbstractEventLoop, Path]:
    return (asyncio.get_running_loop(), db_path)


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
    A, 2026-09-01)."""
    key = _key(db_path)
    conn = _connections.get(key)
    if conn is not None:
        return conn
    async with _lock_for_current_loop():
        conn = _connections.get(key)
        if conn is None:
            conn = await aiosqlite.connect(db_path)
            conn.row_factory = aiosqlite.Row
            if schema_init is not None:
                await schema_init(conn)
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
    stale = [key for key in _connections if key[0] is loop]
    for key in stale:
        await _connections.pop(key).close()
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
