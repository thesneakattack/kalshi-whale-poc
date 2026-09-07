import sqlite3
import tempfile
import threading
from pathlib import Path

import pytest

from services import db


def _fresh_registry(monkeypatch):
    """Each test gets its own schema registry - db.py's module-level
    _SCHEMAS is otherwise shared mutable state across tests."""
    monkeypatch.setattr(db, "_SCHEMAS", {})


def _widgets(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS widgets (id INTEGER PRIMARY KEY)")


class _RecordingConnection:
    """Wraps a real sqlite3.Connection to track close() calls without
    mutating the connection object itself.

    Deviation from the plan's literal Step 1 test text
    (docs/archive/lane-5-runtime-infrastructure/plans/2026-09-03-persistence-layer-db-migration-implementation.md (moved there 2026-09-06, planning-lanes migration), Task 1): the plan's own text monkeypatches
    `conn.close` directly on a real sqlite3.Connection instance, but that
    raises `AttributeError: 'sqlite3.Connection' object attribute 'close'
    is read-only` on this container's Python (3.13, confirmed empirically
    via TDD's own RED step, not assumed) - `close` is not instance-settable
    on that C type. This repo already has a working, already-merged pattern
    for this exact shape (tests/test_signal_log.py's
    test_connect_closes_its_connection, Tier 0's own Task 5): wrap the real
    connection instead of mutating it, delegate everything else via
    __getattr__, and also delegate __enter__/__exit__ since db.connect()'s
    body does `with conn:` for its own commit/rollback semantics (the
    fd-closing `finally: conn.close()` is a separate, outer step)."""

    def __init__(self, inner, closed: list):
        self._inner = inner
        self._closed = closed

    def close(self):
        self._closed.append(True)
        self._inner.close()

    def __enter__(self):
        self._inner.__enter__()
        return self

    def __exit__(self, *exc_info):
        return self._inner.__exit__(*exc_info)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _closing_sqlite_connect(closed: list, *, real_connect=None):
    """real_connect defaults to sqlite3.connect - but by test time that name
    already refers to tests/support/runtime_isolation.py's own
    _guarded_connect (installed once, repo-wide, at collection time), not
    the true unwrapped function. That's fine for every test below except
    the corrupted-file one: _guarded_connect does its own
    conn.execute("PRAGMA synchronous=OFF") immediately inside itself, so a
    corrupted file's DatabaseError fires there - before this wrapper ever
    gets a connection to attach close-tracking to, and before db.py's own
    conn = sqlite3.connect(db_path) line (deliberately outside its try:)
    even returns. Real, unwrapped sqlite3.connect() is lazy (verified
    directly: it does not touch the file's contents at all, only the first
    real execute() does) - production is unaffected, only this specific
    test's simulation of "connect against a corrupted file" needs the true
    original to accurately reproduce that laziness."""
    real_connect = real_connect or sqlite3.connect

    def _tracking_connect(*args, **kwargs):
        return _RecordingConnection(real_connect(*args, **kwargs), closed)

    return _tracking_connect


def test_connect_closes_on_normal_exit(tmp_path, monkeypatch):
    _fresh_registry(monkeypatch)
    closed = []
    monkeypatch.setattr(db.sqlite3, "connect", _closing_sqlite_connect(closed))
    with db.connect(tmp_path / "t.db") as conn:
        conn.execute("SELECT 1")
    assert closed == [True]


def test_connect_closes_even_on_exception(tmp_path, monkeypatch):
    _fresh_registry(monkeypatch)
    closed = []
    monkeypatch.setattr(db.sqlite3, "connect", _closing_sqlite_connect(closed))
    with pytest.raises(ValueError):
        with db.connect(tmp_path / "t.db") as conn:
            raise ValueError("caller-side failure")
    assert closed == [True]


def test_connect_closes_on_setup_failure_before_yield(tmp_path, monkeypatch):
    """PR #501's own lesson, generalized: a failure between connect() and
    yield (here, a schema init_fn that raises) must not leak the connection."""
    _fresh_registry(monkeypatch)
    closed = []
    monkeypatch.setattr(db.sqlite3, "connect", _closing_sqlite_connect(closed))

    def _boom(conn: sqlite3.Connection) -> None:
        raise RuntimeError("schema init failed")

    db.register_schema("boom", _boom)
    with pytest.raises(RuntimeError):
        with db.connect(tmp_path / "t.db", tables=("boom",)):
            pass
    assert closed == [True]


def test_register_schema_creates_table_and_is_idempotent(tmp_path, monkeypatch):
    _fresh_registry(monkeypatch)
    db.register_schema("widgets", _widgets)
    db_path = tmp_path / "t.db"
    with db.connect(db_path, tables=("widgets",)) as conn:
        conn.execute("INSERT INTO widgets DEFAULT VALUES")
    # Second connect() with the same table re-runs init_fn - must not error
    # or wipe the row.
    with db.connect(db_path, tables=("widgets",)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM widgets").fetchone()[0] == 1


def test_no_tables_arg_runs_no_schema(tmp_path, monkeypatch):
    _fresh_registry(monkeypatch)
    db.register_schema("widgets", _widgets)
    db_path = tmp_path / "t.db"
    with db.connect(db_path) as conn:  # tables=() default - no init_fn runs
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("SELECT * FROM widgets")


def test_wal_and_busy_timeout_pragmas_applied(tmp_path, monkeypatch):
    _fresh_registry(monkeypatch)
    with db.connect(tmp_path / "t.db", busy_timeout_ms=1234) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 1234


def test_add_column_if_missing_adds_once(tmp_path, monkeypatch):
    _fresh_registry(monkeypatch)
    db.register_schema("t", lambda conn: conn.execute(
        "CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY)"
    ))
    with db.connect(tmp_path / "t.db", tables=("t",)) as conn:
        db.add_column_if_missing(conn, "t", "extra", "REAL")
        db.add_column_if_missing(conn, "t", "extra", "REAL")  # no error second time
        cols = {row[1] for row in conn.execute("PRAGMA table_info(t)")}
        assert "extra" in cols


def test_add_column_if_missing_survives_a_concurrent_racer(tmp_path, monkeypatch):
    """PRAGMA table_info (read) then ALTER TABLE ADD COLUMN (write) is not
    atomic across connections: two threads can both see the column missing
    before either commits its ALTER, and the loser's own ALTER then raises
    'duplicate column name' - a real OperationalError, not a benign no-op,
    caught by every caller's own broad `except Exception` and silently
    dropping that caller's whole write (found via services/fault_log.py's
    issue #605 fix, whose record_fault() is genuinely called from concurrent
    OS threads - same shape services/fault_log.py's own
    _ensure_null_exc_type_dedup_index already had to solve once for its
    CREATE UNIQUE INDEX race, see that function's docstring)."""
    import threading

    _fresh_registry(monkeypatch)
    db_path = tmp_path / "race.db"
    db.register_schema("t", lambda conn: conn.execute(
        "CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY)"
    ))
    # Single seed connection first, deliberately (matches services/
    # fault_log.py's test_concurrent_first_writes_after_upgrade_do_not_
    # lose_a_call): converting a database to WAL mode needs exclusive
    # access, and a database that's never been opened once hitting 8-way
    # concurrency on its very first connection is a different, already-
    # tracked bug (issue #549), out of scope here - this test isolates the
    # one race add_column_if_missing is actually responsible for.
    with db.connect(db_path, tables=("t",)):
        pass

    n_threads = 8
    barrier = threading.Barrier(n_threads)
    errors: list[BaseException] = []
    errors_lock = threading.Lock()

    def _add_column():
        barrier.wait()  # force every thread to race the same missing-column check together
        try:
            with db.connect(db_path, tables=("t",)) as conn:
                db.add_column_if_missing(conn, "t", "extra", "REAL")
        except BaseException as exc:  # noqa: BLE001 - the race under test raises sqlite3.OperationalError
            with errors_lock:
                errors.append(exc)

    threads = [threading.Thread(target=_add_column) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"a concurrent add_column_if_missing call raised instead of no-op'ing: {errors}"
    with db.connect(db_path, tables=("t",)) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(t)")}
        assert "extra" in cols


def test_connect_creates_nested_parent_directory(tmp_path, monkeypatch):
    """The prototype's mkdir(parents=True) - a tmp_path-based test can hand
    connect() a not-yet-existing nested directory; exist_ok=True alone
    raises FileNotFoundError in that case."""
    _fresh_registry(monkeypatch)
    nested = tmp_path / "a" / "b" / "c.db"
    with db.connect(nested) as conn:
        conn.execute("SELECT 1")
    assert nested.exists()


# --- Gate 0 must-fix tests (design spec, "Required fixes to the prototype") ---


def test_register_schema_raises_on_genuine_conflict(monkeypatch):
    _fresh_registry(monkeypatch)

    def _init_a(conn):
        conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY)")

    def _init_b(conn):
        conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, x TEXT)")

    db.register_schema("t", _init_a)
    with pytest.raises(ValueError):
        db.register_schema("t", _init_b)


def test_register_schema_same_callable_twice_is_not_a_conflict(monkeypatch):
    """Re-importing a module that calls register_schema at its own top
    level must not raise - only a genuinely different callable does."""
    _fresh_registry(monkeypatch)
    db.register_schema("widgets", _widgets)
    db.register_schema("widgets", _widgets)  # no error


def test_connect_on_corrupted_db_file_still_closes(tmp_path, monkeypatch):
    """Real incident this app already had: data/fault_log.db,
    market_history/record_snapshot_from_ticker, DatabaseError 'database disk
    image is malformed,' 2026-09-02 18:21-20:44 UTC. The exception must
    propagate through finally: conn.close() the same as any other exception."""
    from tests.support.runtime_isolation import _original_connect

    _fresh_registry(monkeypatch)
    bad = tmp_path / "corrupt.db"
    bad.write_bytes(b"not a sqlite file" * 100)
    closed = []
    monkeypatch.setattr(
        db.sqlite3, "connect", _closing_sqlite_connect(closed, real_connect=_original_connect)
    )
    with pytest.raises(sqlite3.DatabaseError):
        with db.connect(bad) as conn:
            conn.execute("SELECT * FROM sqlite_master")
    assert closed == [True]


def test_connect_before_registering_module_imported_raises_keyerror(tmp_path, monkeypatch):
    """table-name-only keying trades C2's silent 'no such table' for a loud
    KeyError - better, but must be a named, tested behavior, not an
    accident. A caller passing tables=("unregistered",) before the owning
    module's register_schema call has run gets this, not a silent no-op."""
    _fresh_registry(monkeypatch)
    with pytest.raises(KeyError):
        with db.connect(tmp_path / "t.db", tables=("never_registered",)):
            pass


def test_connect_and_register_schema_are_thread_safe_under_concurrent_registration(monkeypatch):
    """Two modules genuinely racing to register different init_fns for the
    same table name must raise deterministically, not depend on scheduling
    (the reason _SCHEMAS needs a lock, not just correctness under a single
    thread)."""
    _fresh_registry(monkeypatch)
    errors = []

    def _register(suffix):
        def _init(conn):
            conn.execute(f"CREATE TABLE IF NOT EXISTS t{suffix} (id INTEGER PRIMARY KEY)")
        try:
            db.register_schema("race_table", _init)
        except ValueError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_register, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # Exactly one registration wins; every other thread's distinct closure
    # is a genuine conflict and must raise - never a silent last-write-wins,
    # never a crash/hang from unsynchronized dict access.
    assert len(errors) == 19


def test_monkeypatched_db_path_finds_registered_schema(tmp_path, monkeypatch):
    """The C2 regression test: this repo's universal
    monkeypatch.setattr(mod, "DB_PATH", tmp_path/...) convention (64 test
    files) must keep working - table-name-only keying (not the prototype's
    db_path-keyed registry) is why this works."""
    _fresh_registry(monkeypatch)
    db.register_schema("widgets", _widgets)
    real_path = tmp_path / "real" / "app.db"
    monkeypatched_path = tmp_path / "test" / "isolated.db"
    with db.connect(monkeypatched_path, tables=("widgets",)) as conn:
        conn.execute("INSERT INTO widgets DEFAULT VALUES")
        assert conn.execute("SELECT COUNT(*) FROM widgets").fetchone()[0] == 1
    assert not real_path.exists()


def test_table_name_uniqueness_across_full_migration_scope():
    """Gate 0's static check: enumerate every table name this migration's
    own plan intends to register across all in-scope modules and assert
    they're pairwise distinct - catches a planned collision before any
    module's migration task is even written, not discovered incrementally.
    This fixture is the authoritative table-name list for this plan; keep it
    current as tasks land (see this plan's own per-task 'Tables' line)."""
    table_names = [
        # Task 2 (capture_writer.py-owned, shared across two registering modules)
        "raw_trades", "rejected_candidates", "rejection_events",
        # Task 3: candidate_log.py registers rejected_candidates/rejection_events
        # (already listed above - same table, same shared init_fn, not a
        # second distinct entry)
        # Task 4: series_watcher.py registers raw_trades (ditto) plus:
        "book_snapshots",
        # Task 5
        "metric_samples",
        # Task 6
        "risk_meta",
        # Task 7 (verified directly, `grep -n "CREATE TABLE" services/paper_broker.py`
        # - 4 tables, not the 2 this plan's first draft assumed before that check)
        "broker_meta", "positions", "trades", "pending_orders",
        # Task 8
        "candidates",
        # Task 9
        "series_status",
        # Task 10
        "trade_category",
        # Task 11 (module is named settlement_edge.py; its table is not -
        # verified directly, `grep -n "CREATE TABLE" services/settlement_edge.py`)
        "window_observations",
        # Task 12
        "backup_runs",
        # Task 13
        "signal_state", "coordination_runs", "cleanup_actions",
    ]
    assert len(table_names) == len(set(table_names)), (
        "duplicate table name across this migration's own planned registrations"
    )


def test_lock_contention_raises_operational_error_matching_capture_writer_pattern(monkeypatch):
    """Adopted from fix/db-foundation-must-fix-tests (e74096a) - see this task's own
    'Disposition' note above. capture_writer.py's _flush_store already handles exactly
    this shape live (real "database is locked" faults in data/fault_log.db): catch
    sqlite3.OperationalError, retain the batch for the next flush cycle rather than
    blocking or crashing. Verifies db.connect() raises a real, catchable
    OperationalError under genuine lock contention (not something else, not a hang)
    and that busy_timeout_ms is actually overridable per call - capture_writer's own
    _CALLER_BUSY_TIMEOUT_MS (50ms) is 100x shorter than db.py's 5000ms default by
    design (fail-fast, not block-then-retry)."""
    _fresh_registry(monkeypatch)
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "t.db"
        with db.connect(db_path):
            pass  # create the file first

        locker = sqlite3.connect(str(db_path), timeout=0)
        locker.execute("BEGIN EXCLUSIVE")
        try:
            retained = []
            try:
                with db.connect(db_path, busy_timeout_ms=50) as conn:
                    conn.execute("CREATE TABLE never_reached (id INTEGER)")
            except sqlite3.OperationalError as exc:
                retained.append(exc)
            assert retained, "expected db.connect() to raise OperationalError under a held exclusive lock"
            assert "locked" in str(retained[0]).lower()
        finally:
            locker.rollback()
            locker.close()

        # Lock released - a normal connect() now succeeds, confirming the
        # failed attempt above didn't leave anything stuck.
        with db.connect(db_path) as conn:
            conn.execute("SELECT 1")
