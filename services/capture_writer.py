"""Daemon thread that batches capture-store writes off both the asyncio loop
and the reader coroutine. Design spec / realtime-data-plane-remediation.md
Phase P3 Task 14: the reader must never do more than an in-memory append;
this is the sole writer for the stores it owns. The busy_timeout depends on
whose thread is flushing (issue #211, below): the daemon thread waits out a
lock for one flush cycle, and hands the batch back to its buffer rather
than dropping it if the lock outlasts that; flush_now() on a caller's
thread keeps the short budget so a collision never sleeps on a thread that
matters.

Retain on lock (issue #211, 2026-08-30). series_watcher.db is NOT single-
writer today: series_watcher.flush() (book_snapshots INSERT - on the tick
executor every tick, and on the websocket handler's thread whenever its
buffer fills) and series_watcher.prune() (a full-scan DELETE, since no
index on book_snapshots leads with observed_at - on the first tick after
every process start, then hourly, on the event loop) share the file, and
SQLite's write lock is per file, not per table. Measured on the same bind
mount with synthetic tables of the live shapes (the live file is 22.8GB
and was not copied): a single WAL commit 4.7ms median; the 500-row book
INSERT 20-44ms; the prune on a 190k-row book table ~90ms warm. So the
old 50ms budget lost to the prune every time and to the book flush on
its slow tail. Live: 227 `database is locked` faults since 2026-08-27,
every one on raw_trades; 24 of the 45 log-timestamped ones fell within
120s of an hourly prune mark (a uniform spread would put ~1.5 there);
460 raw_trades rows lost in one 18.2h process lifetime
(series_watcher.capture_stats().dropped_rows - 12 faults, ~38 rows each,
the ~1s cadence's typical batch). Now a SQLITE_BUSY/SQLITE_LOCKED failure
puts the batch back at the FRONT of its buffer for the next cycle
(_retain), the daemon thread waits up to _DAEMON_BUSY_TIMEOUT_MS before
that happens, and the retained buffer is capped at _MAX_RETAINED_ROWS with
the overflow counted in its own counter (overflow_dropped_count) - a drop
path with a different cause must never hide inside dropped_count's number.
Any other failure still drops the batch (a schema or disk error would fail
identically on every retry and only fill the buffer to the cap).
loss_snapshot() is what /api/health/pipeline and tools/soak_analyzer.py
read.

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
RAW_TRADES_DDL_SQL = """
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
"""
# Shared with services/series_watcher.py's _connect()/_ensure_schema_aio()
# (2026-09-03, Task 3c of docs/archive/lane-5-runtime-infrastructure/plans/2026-09-03-tier1-backend-hygiene.md, moved there 2026-09-06, planning-lanes migration) - previously three independent hand-typed copies (this
# module plus series_watcher.py's own sync AND async schema-init
# functions), with a self-documented "keep the two DDL blocks in sync by
# hand" comment in series_watcher.py. This module owns the constant
# because it has no import dependency on series_watcher.py/candidate_log.py
# (they both already import IT) - the only direction that doesn't create
# a circular import.
REJECTION_EVENTS_DDL_SQL = """
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
        unit_cost REAL,
        sample_weight REAL NOT NULL DEFAULT 1.0
    )
"""
# sample_weight (2026-09-05, issue #532): candidate_log.py's record_rejection()
# Bernoulli-samples min_contracts rejections at write time rather than
# recording every one (29.5M of 29.8M total rows, unbounded growth - see
# that issue for the measurement). A sampled row's weight is 1/sample_rate,
# so summing sample_weight instead of COUNT(*) recovers an unbiased
# estimate of the true population size; every other gate is never sampled
# and keeps weight 1.0, so its sum is identical to a plain COUNT(*).
# Default 1.0 matters for the ALTER TABLE ADD COLUMN migration path
# (candidate_log.py's _connect()/_ensure_schema_aio(), same pattern
# unit_cost used) - every pre-existing row predates sampling and must be
# counted as a real, unsampled observation, not silently zero-weighted.
# Shared with services/candidate_log.py's _connect() (same reasoning as
# RAW_TRADES_DDL_SQL above). Matches candidate_log.py's real schema
# exactly, unit_cost baked in from the start here (candidate_log.py's own
# _add_column_if_missing migration stays, as a no-op safety net for any
# pre-existing file created before this shared constant existed).
REJECTED_CANDIDATES_DDL_SQL = """
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
"""


def init_raw_trades(conn: sqlite3.Connection) -> None:
    """Stable, importable function object for services/db.py's
    register_schema("raw_trades", ...) - defined once here, not as a local
    closure in series_watcher.py, so a second registration attempt for this
    table name (e.g. a future consumer) compares equal under `is` rather
    than raising a spurious conflict (2026-09-03 persistence-layer db.py
    migration, D2)."""
    conn.execute(RAW_TRADES_DDL_SQL)


def init_rejected_candidates(conn: sqlite3.Connection) -> None:
    """Same reasoning as init_raw_trades, for candidate_log.py's
    register_schema("rejected_candidates", ...)."""
    conn.execute(REJECTED_CANDIDATES_DDL_SQL)


def init_rejection_events(conn: sqlite3.Connection) -> None:
    """Same reasoning as init_raw_trades, for candidate_log.py's
    register_schema("rejection_events", ...)."""
    conn.execute(REJECTION_EVENTS_DDL_SQL)


_STORE_DDL: dict[str, str] = {
    "raw_trades": RAW_TRADES_DDL_SQL,
    "rejection_events": REJECTION_EVENTS_DDL_SQL,
    "rejected_candidates": REJECTED_CANDIDATES_DDL_SQL,
}
_FLUSH_INTERVAL_SEC = 1.0
_FLUSH_BATCH = 500
# Busy-wait budgets (ms) for a flush that finds the file's write lock held.
# The daemon thread (_run) is the one thread nothing waits on, so it waits
# out the contending writers: one flush cycle (_FLUSH_INTERVAL_SEC), 10x
# the slowest hold measured on this mount (the hourly prune, ~90ms warm)
# and 25x the per-tick one (the book flush, 20-44ms). Not sqlite3's 5s
# default: stop() joins this thread for _STOP_JOIN_SEC, and a flush still
# mid-wait past that join would die with the process holding a batch that
# is in neither the file nor the buffer - the one silent loss path this
# module would have left. A lock held longer than a cycle is the retain
# path's job, not the busy handler's: the batch goes back to the buffer
# and is retried on the next cycle, counted in lock_retry_count().
# flush_now() runs on its CALLER's thread - candidate_log.
# resolve_from_market_results on the tick executor (the tick waits on
# it), the gate summaries and the clear_* reset routes on the event loop -
# and keeps the original 50ms, as does the daemon once _stop_event is set.
_DAEMON_BUSY_TIMEOUT_MS = 1000
_CALLER_BUSY_TIMEOUT_MS = 50
# How long stop() joins the thread. Must outlast _DAEMON_BUSY_TIMEOUT_MS
# plus a write (tests/test_capture_writer.py pins the relation).
_STOP_JOIN_SEC = 2.0
# Rows a store may hold back for retry after lock collisions before the
# oldest are discarded (counted in _overflow_dropped_counts, never
# silently). Sized from /api/observability/summary, 2026-08-30: peak
# captured ingest ~460 rows/s (writer.depth.raw_trades max 459 at the 1s
# flush cadence) x the longest stall the contending writers' tick phase
# has shown (tick.phase.capture_flush_and_titles_sec max 224s) = ~103k
# rows, x2 margin: ~400s of peak ingest. ~1KB per raw_trades row in
# memory, so ~200MB worst case - reached only if the file's lock is held
# for minutes, which no measured writer does; overflow_dropped_count() is
# the detector if one ever does.
_MAX_RETAINED_ROWS = 200_000

def _empty_buffer(store: str):
    return {} if store in _STORE_KEY else []


def _key_of(store: str, row: tuple) -> tuple:
    return tuple(row[i] for i in _STORE_KEY[store])


_buffers: dict[str, dict | list] = {name: _empty_buffer(name) for name in _STORE_PATHS}
_lock = threading.Lock()
_last_flush_at: dict[str, float] = {name: time.time() for name in _STORE_PATHS}
# Three counters, three causes - each is a different question for the
# reader of loss_snapshot(): rows discarded on a non-retryable flush
# failure; rows discarded because the retained buffer hit its cap; and
# how many batches were handed back for retry after a lock collision.
_dropped_counts: dict[str, int] = {name: 0 for name in _STORE_PATHS}
_overflow_dropped_counts: dict[str, int] = {name: 0 for name in _STORE_PATHS}
_lock_retry_counts: dict[str, int] = {name: 0 for name in _STORE_PATHS}
_thread: threading.Thread | None = None
_stop_event = threading.Event()


def submit(store: str, row: tuple) -> None:
    with _lock:
        if store in _STORE_KEY:
            _buffers.setdefault(store, {})[_key_of(store, row)] = row  # latest wins in memory
        else:
            _buffers.setdefault(store, []).append(row)


def depth() -> dict[str, int]:
    with _lock:
        return {name: len(rows) for name, rows in _buffers.items()}


def last_flush_age_ms() -> dict[str, float]:
    now = time.time()
    return {name: round((now - ts) * 1000, 1) for name, ts in _last_flush_at.items()}


def dropped_count() -> dict[str, int]:
    """Cumulative rows lost per store to a NON-retryable flush failure (the
    batch is discarded - see _flush_store). Does not include rows the
    retained-buffer cap discarded; those are overflow_dropped_count(), a
    different cause under a different name. Lets a consumer like
    series_watcher.capture_stats() report accurate loss for the stores it
    delegates here, the same way it already tracked its own _dropped_rows
    before Task 15 moved raw_trades' flush into this module."""
    return dict(_dropped_counts)


def overflow_dropped_count() -> dict[str, int]:
    """Cumulative rows per store discarded because the buffer held back
    for retry after lock collisions exceeded _MAX_RETAINED_ROWS (oldest
    first). Non-zero means the lock outlasted what memory could hold - a
    real hole in the archive, reported apart from dropped_count() so the
    two causes are never summed into one unexplained number."""
    return dict(_overflow_dropped_counts)


def lock_retry_count() -> dict[str, int]:
    """Cumulative batches per store handed back to the buffer after a
    SQLITE_BUSY/SQLITE_LOCKED flush. Churn, not loss: how often this
    writer collided with another connection on the same file."""
    return dict(_lock_retry_counts)


def loss_snapshot() -> dict:
    """Every counter this module keeps, by name, for /api/health/pipeline
    and tools/soak_analyzer.py. All counters are in-memory and start at 0
    with the process (a uvicorn --reload restart included) - the fault log
    (services/fault_log.py) is the durable record of the failures."""
    return {
        "dropped_rows": dropped_count(),
        "overflow_dropped_rows": overflow_dropped_count(),
        "lock_retries": lock_retry_count(),
        "depth": depth(),
        "max_retained_rows": _MAX_RETAINED_ROWS,
        "counter_scope": "process lifetime",
    }


def _is_lock_error(exc: BaseException) -> bool:
    """SQLITE_BUSY / SQLITE_LOCKED (primary code, extended codes masked):
    another connection held the lock past the busy budget, so the rows are
    fine and only the moment was wrong. Read from sqlite3's own error
    attributes (CPython 3.11+; the container and CI run 3.13) rather than
    matched against the message text."""
    code = getattr(exc, "sqlite_errorcode", None)
    return code is not None and (code & 0xFF) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)


def _retain(store: str, rows: list, upsert_mode: bool) -> int:
    """Hands a batch that failed on a lock back to its buffer, AHEAD of
    anything submitted since (arrival order survives the retry), trimmed
    to _MAX_RETAINED_ROWS oldest-first. Returns how many rows the trim
    discarded; the caller counts them. Upsert-mode stores merge under the
    same latest-wins rule submit() applies: a row submitted after the
    collision beats the retained one for the same key."""
    with _lock:
        current = _buffers[store]
        if upsert_mode:
            merged = {_key_of(store, row): row for row in rows}
            merged.update(current)
            overflow = max(0, len(merged) - _MAX_RETAINED_ROWS)
            for key in list(merged)[:overflow]:
                del merged[key]
        else:
            merged = rows + current
            overflow = max(0, len(merged) - _MAX_RETAINED_ROWS)
            if overflow:
                merged = merged[overflow:]
        _buffers[store] = merged
        _lock_retry_counts[store] = _lock_retry_counts.get(store, 0) + 1
        _overflow_dropped_counts[store] = _overflow_dropped_counts.get(store, 0) + overflow
    return overflow


def _flush_store(store: str, busy_timeout_ms: int = _CALLER_BUSY_TIMEOUT_MS) -> None:
    """Never raises: a failed capture-store write must not kill this
    daemon thread (a thread-target exception is silent - Python never
    propagates it anywhere) or stop other stores from flushing. A lock
    failure (SQLITE_BUSY/SQLITE_LOCKED after busy_timeout_ms) hands the
    batch back to the buffer for the next cycle via _retain (issue #211);
    any other failure drops the batch and counts it in _dropped_counts so
    the loss is visible (dropped_count()) instead of silent - same contract
    as series_watcher.flush() already established for its own (book-only,
    post-Task-15) buffer. Nothing is ever half-written: the batch is one
    transaction, so a failure before commit rolls it back whole and the
    retry re-presents every row (INSERT OR IGNORE / the UPSERT's own
    conflict clause keep that idempotent)."""
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
        conn = sqlite3.connect(db_path, timeout=busy_timeout_ms / 1000)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
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
            try:
                conn.close()
            except sqlite3.Error:
                # commit() above already made the batch durable (or the
                # write failed and is being handled by the except below);
                # a close-time error must not turn a written batch into a
                # retained one, which would re-insert AUTOINCREMENT-keyed
                # rows (rejection_events) as duplicates.
                logger.warning("capture_writer: close() failed after flushing %s", store, exc_info=True)
    except Exception as exc:
        if _is_lock_error(exc):
            overflow = _retain(store, rows, upsert_mode)
            logger.warning(
                "capture_writer flush of %s hit a lock after %dms: %d row(s) retained for retry, "
                "%d oldest dropped past the %d-row cap",
                store, busy_timeout_ms, len(rows) - overflow, overflow, _MAX_RETAINED_ROWS,
            )
            fault_log.record(
                "capture_writer", "flush_retained_on_lock", exc, severity="warn",
                context=f"{store}: {len(rows)} row(s) retained, {overflow} overflow-dropped",
            )
        else:
            _dropped_counts[store] = _dropped_counts.get(store, 0) + len(rows)
            logger.exception("capture_writer flush failed for store %s", store)
            fault_log.record("capture_writer", "flush", exc, context=store)
    finally:
        _last_flush_at[store] = time.time()


def flush_now(store: str) -> dict:
    """Synchronous, immediate flush of one store, bypassing the batch/time
    threshold - for tests and diagnostics that need a deterministic flush
    without starting the daemon thread or waiting on its cadence. Safe
    whether or not the thread is running: same _flush_store, same _lock.
    Runs on the caller's thread, so it keeps the short busy budget; a
    locked batch stays buffered for the daemon rather than stalling the
    caller."""
    n = len(_buffers.get(store, []))
    _flush_store(store, busy_timeout_ms=_CALLER_BUSY_TIMEOUT_MS)
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
                # Once stop() has been called, this last pass and the final
                # pass below both use the short budget: the join is
                # _STOP_JOIN_SEC, and the rows must be in the file or back
                # in the buffer before it returns, never in a thread it
                # gave up on (see the budgets above).
                budget = _CALLER_BUSY_TIMEOUT_MS if _stop_event.is_set() else _DAEMON_BUSY_TIMEOUT_MS
                _flush_store(store, busy_timeout_ms=budget)
    # Final pass at shutdown on the short budget. Whatever a held lock
    # keeps out of the file here is still in the buffer - lost with the
    # process, but logged as lost, never reported as written.
    for store in _buffers:
        _flush_store(store, busy_timeout_ms=_CALLER_BUSY_TIMEOUT_MS)
    left = {name: n for name, n in depth().items() if n}
    if left:
        logger.warning("capture_writer stopping with unwritten rows still buffered: %s", left)


def start() -> None:
    global _thread
    _stop_event.clear()
    _thread = threading.Thread(target=_run, name="capture-writer", daemon=True)
    _thread.start()


def stop(timeout_sec: float = _STOP_JOIN_SEC) -> None:
    """Sets the stop flag and joins the thread. The join outlasts
    _DAEMON_BUSY_TIMEOUT_MS on purpose: a flush already waiting on a lock
    when stop() is called finishes - written, or retained and logged as
    unwritten - before the join returns, so main.py's lifespan never exits
    with a batch in a thread the join gave up on."""
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
