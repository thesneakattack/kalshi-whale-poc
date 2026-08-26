"""Runs the trading tick's synchronous SQLite phases off the asyncio loop.
Root-cause report C1: capture_flush_and_titles, resolve_and_record, and the
series_stats N+1 in main.py are the measured source of the 4-13.6s per-tick
loop stalls that starve the WS consumer. This does not change what those
functions do - only where they run and how their connections are opened
(a bounded busy_timeout instead of the sqlite3 default 5s sleep, which would
otherwise just move the stall from the loop to a worker thread that still
blocks a whole tick).

Realtime data-plane remediation plan, P1 Task 6."""
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
