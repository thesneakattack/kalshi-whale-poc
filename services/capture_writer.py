"""Daemon thread that batches capture-store writes off both the asyncio loop
and the reader coroutine. Design spec / realtime-data-plane-remediation.md
Phase P3 Task 14: the reader must never do more than an in-memory append;
this is the sole writer for the stores it owns, opened with a short
busy_timeout so a collision with another connection never sleeps five
seconds on any thread that matters.

Wired (P3 Task 15, 2026-08-27): services/series_watcher.py's record_trade
submits every raw_trades row here instead of its own local buffer. The
thread itself is started/stopped/supervised from main.py's lifespan -
_flush_store is a no-op on an empty buffer, so it's also safe to run with
zero producers, which was this module's own state for one commit (Task 14).

Owns its own DDL per store (CLAUDE.md's persistence idiom - CREATE TABLE IF
NOT EXISTS, additive only) even though "raw_trades" already exists via
services/series_watcher.py's own _connect(): CREATE TABLE IF NOT EXISTS
against an existing, identically-shaped table is a no-op, and this module
is meant to eventually own that table outright (see the module-level intent
above), so it should be able to create it cold too.

Always INSERT OR IGNORE, matching series_watcher.flush()'s own handling of
raw_trades' trade_id PRIMARY KEY: a re-presented trade_id (reconnect
replay) is a real, expected scenario, not a hypothetical - a plain INSERT
would raise IntegrityError inside this thread's target function, which
Python does not propagate anywhere the caller could see. _flush_store
catches its own exceptions (never lets one store's failure starve the
others or kill the thread) and counts a failed batch in _dropped_counts,
visible via dropped_count()."""
import logging
import sqlite3
import threading
import time
from pathlib import Path

from services import fault_log

logger = logging.getLogger(__name__)

_STORE_PATHS: dict[str, Path] = {
    "raw_trades": Path(__file__).resolve().parent.parent / "data" / "series_watcher.db",
}
_STORE_TABLE = {"raw_trades": "raw_trades"}  # extend as more stores move here
_STORE_DDL: dict[str, str] = {
    "raw_trades": """
        CREATE TABLE IF NOT EXISTS raw_trades (
            trade_id TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            series TEXT NOT NULL,
            observed_at REAL NOT NULL,
            exchange_ts REAL,
            taker_outcome_side TEXT,
            taker_book_side TEXT,
            taker_side_legacy TEXT,
            resolved_side TEXT,
            count_fp REAL,
            yes_price_dollars REAL,
            no_price_dollars REAL,
            notional_usd REAL,
            is_block_trade INTEGER,
            excluded INTEGER NOT NULL DEFAULT 0,
            raw_json TEXT NOT NULL
        )
    """,
}
_FLUSH_INTERVAL_SEC = 1.0
_FLUSH_BATCH = 500

_buffers: dict[str, list[tuple]] = {name: [] for name in _STORE_PATHS}
_lock = threading.Lock()
_last_flush_at: dict[str, float] = {name: time.time() for name in _STORE_PATHS}
_dropped_counts: dict[str, int] = {name: 0 for name in _STORE_PATHS}
_thread: threading.Thread | None = None
_stop_event = threading.Event()


def submit(store: str, row: tuple) -> None:
    with _lock:
        _buffers.setdefault(store, []).append(row)


def depth() -> dict[str, int]:
    with _lock:
        return {name: len(rows) for name, rows in _buffers.items()}


def last_flush_age_ms() -> dict[str, float]:
    now = time.time()
    return {name: round((now - ts) * 1000, 1) for name, ts in _last_flush_at.items()}


def dropped_count() -> dict[str, int]:
    """Cumulative rows lost to a failed flush per store (never retried -
    see _flush_store's own docstring). Lets a consumer like series_watcher.
    capture_stats() report accurate loss for the stores it delegates here,
    the same way it already tracked its own _dropped_rows before Task 15
    moved raw_trades' flush into this module."""
    return dict(_dropped_counts)


def _flush_store(store: str) -> None:
    """Never raises: a failed capture-store write must not kill this
    daemon thread (a thread-target exception is silent - Python never
    propagates it anywhere) or stop other stores from flushing. On
    failure the batch is dropped, not retried, and counted in
    _dropped_counts so the loss is visible (capture_writer.dropped_count())
    instead of silent - same contract as series_watcher.flush() already
    established for its own (book-only, post-Task-15) buffer."""
    with _lock:
        rows, _buffers[store] = _buffers[store], []
    if not rows:
        _last_flush_at[store] = time.time()
        return
    try:
        db_path = _STORE_PATHS[store]
        db_path.parent.mkdir(exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=0.05)
        try:
            conn.execute("PRAGMA busy_timeout = 50")
            if store in _STORE_DDL:
                conn.execute(_STORE_DDL[store])
            placeholders = ",".join("?" for _ in rows[0])
            conn.executemany(
                f"INSERT OR IGNORE INTO {_STORE_TABLE[store]} VALUES ({placeholders})", rows,
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        _dropped_counts[store] = _dropped_counts.get(store, 0) + len(rows)
        logger.exception("capture_writer flush failed for store %s", store)
        fault_log.record("capture_writer", "flush", exc, context=store)
    finally:
        _last_flush_at[store] = time.time()


def flush_now(store: str) -> dict:
    """Synchronous, immediate flush of one store, bypassing the batch/time
    threshold - for tests and diagnostics that need a deterministic flush
    without starting the daemon thread or waiting on its cadence. Safe
    whether or not the thread is running: same _flush_store, same _lock."""
    n = len(_buffers.get(store, []))
    _flush_store(store)
    return {"flushed": n}


def _run() -> None:
    while not _stop_event.is_set():
        _stop_event.wait(_FLUSH_INTERVAL_SEC)
        now = time.time()
        for store, rows in list(_buffers.items()):
            due = len(rows) >= _FLUSH_BATCH or _stop_event.is_set() or (
                rows and (now - _last_flush_at.get(store, 0)) >= _FLUSH_INTERVAL_SEC
            )
            if due:
                _flush_store(store)
    for store in _buffers:
        _flush_store(store)


def start() -> None:
    global _thread
    _stop_event.clear()
    _thread = threading.Thread(target=_run, name="capture-writer", daemon=True)
    _thread.start()


def stop(timeout_sec: float = 2.0) -> None:
    global _thread
    _stop_event.set()
    if _thread is not None:
        _thread.join(timeout=timeout_sec)
    # Reset to None so a clean stop() looks like "not currently running"
    # (was_started() False), not "started then crashed" - otherwise every
    # test that calls start()/stop() (as this module's own tests do) would
    # leave a dangling Thread object that looks crashed to any later test
    # in the same process that checks observability's dead-writer finding,
    # since Python test files share one process and this module's state is
    # global. Found via a real cross-file pollution failure, not by
    # inspection: test_capture_writer.py's own tests run before
    # test_observability.py's (alphabetical collection order) and left
    # was_started()==True, is_alive()==False behind.
    _thread = None


def is_alive() -> bool:
    return _thread is not None and _thread.is_alive()


def was_started() -> bool:
    """Whether start() has been called at least once in this process -
    distinguishes "never started, not an anomaly" from "started then
    died" for consumers like observability's dead-writer finding, which
    should not fire just because a process hasn't reached start() yet."""
    return _thread is not None


def ensure_alive() -> None:
    if not is_alive():
        logger.warning("capture_writer thread was dead; restarting")
        start()
