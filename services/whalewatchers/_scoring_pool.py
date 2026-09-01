"""Dedicated worker pool + thread-local connection cache for
kalshi_trade_tape.py's per-trade scoring work (both the WS-message path,
_process_stream_trade -> fetch_signals, and the candidate-retry path,
score_recovered_trade). See docs/superpowers/specs/2026-09-01-whale-scoring-
connection-reuse-design.md for the full design.

Deliberately its own pool, not services.tick_executor's shared one (also
used by candidate_ledger.claim()/record_decision(), which gate every whale
signal) and not Python's default asyncio.to_thread executor (shared
process-wide with unrelated work, unbounded up to 20 threads on this
container). 4 workers: this call path normally needs ~1 concurrently (the
WS consumer drains one queue item at a time) - headroom for legitimate
brief overlap plus the candidate-retry path, not a load-bearing capacity
guess. If issue #145/#150 (a timed-out handler's OS thread isn't actually
freed - separate, already-tracked, not fixed here) keeps happening, all 4
workers eventually get stuck and further scoring work queues (a visible
backlog/latency symptom) rather than spawning unbounded new OS threads and
connections silently - bounded and observable is this design's actual
goal, not eliminating #145/#150 itself.

Connections cached here use Python's 5.0s default busy timeout (no
explicit timeout= to sqlite3.connect()), matching signal_log.py/
market_history.py/market_analyst_agent/_db.py's own _connect() - not
services.tick_executor.connection_for()'s 50ms, tuned for a different
(write-capable) context."""
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
    db_path - never on a cache hit."""
    cache = getattr(_local, "connections", None)
    if cache is None:
        cache = _local.connections = {}
    conn = cache.get(db_path)
    if conn is not None:
        try:
            conn.execute("SELECT 1")
            return conn
        except sqlite3.ProgrammingError:
            del cache[db_path]  # evict a connection closed out from under us elsewhere, fall through to reopen
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    schema_init(conn)
    cache[db_path] = conn
    return conn
