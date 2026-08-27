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

Two store modes (P3 Task 17 added the second): "insert" stores
(raw_trades, rejection_events) append every row to a plain list and flush
via INSERT OR IGNORE - duplicates within the buffer are all kept, dedup
happens at the DB via a PRIMARY KEY/AUTOINCREMENT. "upsert" stores
(rejected_candidates) buffer as a dict keyed by _STORE_KEY's column
indices instead of a list - a second submit() for the same key overwrites
the first IN MEMORY (latest observed value wins, matching this store's
own real semantics), and flush executes _STORE_UPSERT_SQL (an
INSERT...ON CONFLICT DO UPDATE, same statement candidate_log.py used to
run synchronously) rather than a plain INSERT. Both modes share the same
_lock, _flush_store, dropped-count accounting, and never-raises contract.

Always INSERT OR IGNORE for insert-mode stores, matching series_watcher.
flush()'s own handling of raw_trades' trade_id PRIMARY KEY: a
re-presented trade_id (reconnect replay) is a real, expected scenario,
not a hypothetical - a plain INSERT would raise IntegrityError inside
this thread's target function, which Python does not propagate anywhere
the caller could see. _flush_store catches its own exceptions (never
lets one store's failure starve the others or kill the thread) and
counts a failed batch in _dropped_counts, visible via dropped_count().

Sets PRAGMA journal_mode=WAL on every connection (added alongside Task
17's changes, closing a gap present since Task 14): WAL is a per-file
setting that persists once any connection sets it, and every store's
owning module (series_watcher.py, candidate_log.py) already sets it via
their own _connect() - but only when something calls that. A store whose
only writer is this module (nothing else ever calls the owning module's
_connect() first) would otherwise depend on incidental call ordering to
end up in WAL mode at all, which is exactly the "bursty write took the
app down under rollback-journal mode on 2026-08-11" failure class
CLAUDE.md documents - not hypothetical for this codebase specifically."""
import logging
import sqlite3
import threading
import time
from pathlib import Path

from services import fault_log

logger = logging.getLogger(__name__)

_STORE_PATHS: dict[str, Path] = {
    "raw_trades": Path(__file__).resolve().parent.parent / "data" / "series_watcher.db",
    "rejection_events": Path(__file__).resolve().parent.parent / "data" / "candidate_log.db",
    "rejected_candidates": Path(__file__).resolve().parent.parent / "data" / "candidate_log.db",
}
_STORE_TABLE = {
    "raw_trades": "raw_trades", "rejection_events": "rejection_events",
    "rejected_candidates": "rejected_candidates",
}
# Upsert-mode stores only: which positional indices of a submitted row
# tuple form the natural key, for in-memory latest-wins collapsing before
# a submitted row ever reaches _STORE_UPSERT_SQL below. A store not listed
# here is insert-mode (plain list buffer, INSERT OR IGNORE on flush).
_STORE_KEY: dict[str, tuple[int, ...]] = {
    "rejected_candidates": (0, 1, 2),  # ticker, strategy, gate_name
}
# Upsert-mode stores only: the exact statement _flush_store executemany()s
# for this store instead of the generic INSERT OR IGNORE. Must match the
# row shape submit() receives for this store exactly (positionally).
_STORE_UPSERT_SQL: dict[str, str] = {
    # Identical to candidate_log.record_rejection()'s former synchronous
    # UPSERT - moved here verbatim (P3 Task 17), not redesigned. The
    # "WHERE rejected_candidates.resolved = 0" clause is the real
    # invariant (a resolved row is never overwritten by a later
    # rejection) - enforced by SQLite per row regardless of how many
    # times, or how few, this module's own in-memory key-collapse means a
    # given key is actually written.
    "rejected_candidates": """
        INSERT INTO rejected_candidates
            (ticker, strategy, gate_name, observed_value, threshold_value, side, rejected_at, resolved, unit_cost)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
        ON CONFLICT(ticker, strategy, gate_name) DO UPDATE SET
            observed_value = excluded.observed_value,
            threshold_value = excluded.threshold_value,
            side = excluded.side,
            rejected_at = excluded.rejected_at,
            unit_cost = excluded.unit_cost
        WHERE rejected_candidates.resolved = 0
    """,
}
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
    # Matches services/candidate_log.py's real schema exactly (including
    # unit_cost, which that module adds via ALTER TABLE after its own
    # initial CREATE - baked directly into this DDL instead, since a fresh
    # table created by THIS module's own _flush_store (e.g. a clean test
    # tmp_path) needs the full shape from the start, not a two-step
    # migration). id is INTEGER PRIMARY KEY AUTOINCREMENT - submit() rows
    # pass None for it so SQLite assigns the next value, same as omitting
    # the column entirely.
    "rejection_events": """
        CREATE TABLE IF NOT EXISTS rejection_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            strategy TEXT NOT NULL,
            gate_name TEXT NOT NULL,
            observed_value REAL,
            threshold_value REAL,
            side TEXT,
            rejected_at REAL NOT NULL,
            resolved INTEGER NOT NULL DEFAULT 0,
            result TEXT,
            resolved_at REAL,
            unit_cost REAL
        )
    """,
    # Matches candidate_log.py's real schema exactly, same "bake unit_cost
    # into the initial CREATE" reasoning as rejection_events above.
    "rejected_candidates": """
        CREATE TABLE IF NOT EXISTS rejected_candidates (
            ticker TEXT NOT NULL,
            strategy TEXT NOT NULL,
            gate_name TEXT NOT NULL,
            observed_value REAL,
            threshold_value REAL,
            side TEXT,
            rejected_at REAL NOT NULL,
            resolved INTEGER NOT NULL DEFAULT 0,
            result TEXT,
            resolved_at REAL,
            unit_cost REAL,
            PRIMARY KEY (ticker, strategy, gate_name)
        )
    """,
}
_FLUSH_INTERVAL_SEC = 1.0
_FLUSH_BATCH = 500

def _empty_buffer(store: str):
    return {} if store in _STORE_KEY else []


_buffers: dict[str, dict | list] = {name: _empty_buffer(name) for name in _STORE_PATHS}
_lock = threading.Lock()
_last_flush_at: dict[str, float] = {name: time.time() for name in _STORE_PATHS}
_dropped_counts: dict[str, int] = {name: 0 for name in _STORE_PATHS}
_thread: threading.Thread | None = None
_stop_event = threading.Event()


def submit(store: str, row: tuple) -> None:
    with _lock:
        if store in _STORE_KEY:
            key = tuple(row[i] for i in _STORE_KEY[store])
            _buffers.setdefault(store, {})[key] = row  # latest wins in memory
        else:
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
    upsert_mode = store in _STORE_KEY
    with _lock:
        buf = _buffers[store]
        rows = list(buf.values()) if upsert_mode else buf
        _buffers[store] = _empty_buffer(store)
    if not rows:
        _last_flush_at[store] = time.time()
        return
    try:
        db_path = _STORE_PATHS[store]
        db_path.parent.mkdir(exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=0.05)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout = 50")
            if store in _STORE_DDL:
                conn.execute(_STORE_DDL[store])
            if upsert_mode:
                conn.executemany(_STORE_UPSERT_SQL[store], rows)
            else:
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
