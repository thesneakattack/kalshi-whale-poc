import sqlite3
import time


def _sample_trade_row(trade_id: str = "t1", ticker: str = "K1"):
    """Matches services/series_watcher.py's real 16-column raw_trades row
    shape (record_trade's own `row` tuple), not a placeholder - Task 15
    routes that exact row through capture_writer.submit(), so this test
    should already exercise the real shape rather than a fake one."""
    now = time.time()
    return (
        trade_id, ticker, "K", now,
        now, "yes", "yes", "yes", "yes", 10.0,
        0.5, 0.5, 5.0, 0, 0,
        "{}",
    )


def _row_count_when_ready(db_path, table: str, attempts: int = 50, delay: float = 0.05,
                          at_least: int = 1) -> int:
    """Polls the DB directly rather than capture_writer.depth() - depth()
    hits 0 the instant _flush_store clears the in-memory buffer, which
    happens BEFORE the DB write completes (deliberate: submit() must never
    block on disk I/O), so depth()==0 is not a valid "flush landed" signal.
    Under normal load the gap is sub-millisecond and invisible; under
    testmon's coverage instrumentation it was wide enough to fail this
    test's original depth()-based poll outright - a real race in the test,
    not in capture_writer itself. at_least: the count that means "landed"
    for a table that already holds rows - the default returns on the first
    non-zero count, which a pre-seeded table satisfies before any flush."""
    for _ in range(attempts):
        try:
            conn = sqlite3.connect(db_path)
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            finally:
                conn.close()
            if count >= at_least:
                return count
        except sqlite3.OperationalError:
            pass  # table not created yet
        time.sleep(delay)
    return 0


def test_submit_is_non_blocking_and_flush_lands_in_the_db(tmp_path, monkeypatch):
    from services import capture_writer
    db_path = tmp_path / "capture_test.db"
    monkeypatch.setattr(capture_writer, "_STORE_PATHS", {"raw_trades": db_path})
    capture_writer.start()
    try:
        capture_writer.submit("raw_trades", _sample_trade_row("t1"))
        assert _row_count_when_ready(db_path, "raw_trades") == 1
    finally:
        capture_writer.stop()


def test_stop_flushes_pending_rows_before_returning(tmp_path, monkeypatch):
    from services import capture_writer
    db_path = tmp_path / "capture_test2.db"
    monkeypatch.setattr(capture_writer, "_STORE_PATHS", {"raw_trades": db_path})
    capture_writer.start()
    capture_writer.submit("raw_trades", _sample_trade_row("t2"))
    capture_writer.stop()
    assert capture_writer.depth()["raw_trades"] == 0
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM raw_trades").fetchone()[0] == 1
    conn.close()


def test_a_small_buffer_flushes_on_the_time_interval_not_only_at_batch_size(tmp_path, monkeypatch):
    from services import capture_writer
    db_path = tmp_path / "capture_test3.db"
    monkeypatch.setattr(capture_writer, "_STORE_PATHS", {"raw_trades": db_path})
    monkeypatch.setattr(capture_writer, "_FLUSH_INTERVAL_SEC", 0.1)
    capture_writer.start()
    try:
        capture_writer.submit("raw_trades", _sample_trade_row("t3"))  # far below _FLUSH_BATCH
        time.sleep(0.3)
        assert capture_writer.depth()["raw_trades"] == 0
    finally:
        capture_writer.stop()


def test_duplicate_trade_id_is_ignored_and_does_not_kill_the_thread(tmp_path, monkeypatch):
    """raw_trades' real schema has trade_id as PRIMARY KEY. A re-presented
    trade_id (reconnect replay) is a real scenario, not a hypothetical -
    series_watcher.flush() already handles it via INSERT OR IGNORE. A raw
    INSERT here would raise IntegrityError inside the daemon thread's
    target function, which Python does not propagate anywhere - the
    thread just dies silently, exactly the failure mode Step 5's liveness
    supervision exists to catch, but better to not lose the batch at all."""
    from services import capture_writer
    db_path = tmp_path / "capture_test4.db"
    monkeypatch.setattr(capture_writer, "_STORE_PATHS", {"raw_trades": db_path})
    capture_writer.start()
    try:
        capture_writer.submit("raw_trades", _sample_trade_row("dup"))
        capture_writer.submit("raw_trades", _sample_trade_row("dup"))
        assert _row_count_when_ready(db_path, "raw_trades") == 1
        assert capture_writer.is_alive()
    finally:
        capture_writer.stop()


def test_flush_now_synchronously_flushes_without_the_thread_running(tmp_path, monkeypatch):
    from services import capture_writer
    db_path = tmp_path / "capture_test5.db"
    monkeypatch.setattr(capture_writer, "_STORE_PATHS", {"raw_trades": db_path})
    monkeypatch.setattr(capture_writer, "_buffers", {"raw_trades": [_sample_trade_row("t5")]})
    monkeypatch.setattr(capture_writer, "_last_flush_at", {"raw_trades": 0.0})

    result = capture_writer.flush_now("raw_trades")

    assert result == {"flushed": 1}
    assert capture_writer.depth()["raw_trades"] == 0
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM raw_trades").fetchone()[0] == 1
    conn.close()


def test_flush_failure_is_counted_in_dropped_count_not_raised(tmp_path, monkeypatch):
    """A store with no DDL registered and no pre-existing table is a clean
    way to force a genuine flush failure (no such table) without mocking
    sqlite3 internals - proves _flush_store's never-raises contract for a
    real failure, not just the duplicate-trade_id case above (which never
    actually reaches the except branch, since INSERT OR IGNORE prevents
    the IntegrityError it's guarding against)."""
    from services import capture_writer
    db_path = tmp_path / "capture_test6.db"
    monkeypatch.setattr(capture_writer, "_STORE_PATHS", {"nonexistent_store": db_path})
    monkeypatch.setattr(capture_writer, "_STORE_TABLE", {"nonexistent_store": "nonexistent_table"})
    monkeypatch.setattr(capture_writer, "_STORE_DDL", {})  # no DDL - table never gets created
    monkeypatch.setattr(capture_writer, "_buffers", {"nonexistent_store": [("a", "b")]})
    monkeypatch.setattr(capture_writer, "_last_flush_at", {"nonexistent_store": 0.0})
    monkeypatch.setattr(capture_writer, "_dropped_counts", {"nonexistent_store": 0})

    capture_writer._flush_store("nonexistent_store")  # must not raise

    assert capture_writer.dropped_count()["nonexistent_store"] == 1
    assert capture_writer.depth()["nonexistent_store"] == 0  # buffer still cleared, not re-added


# --- upsert-mode stores (P3 Task 17: rejected_candidates) ---

def _rejected_candidates_row(ticker="TICK-A", strategy="whale_watcher", gate_name="min_contracts",
                              observed_value=10.0, threshold_value=20.0, side="yes",
                              rejected_at=None, unit_cost=0.5):
    """Matches services/candidate_log.py's real UPSERT VALUES shape (8
    columns - resolved is hardcoded 0 in _STORE_UPSERT_SQL, result/
    resolved_at aren't part of the INSERT at all)."""
    return (ticker, strategy, gate_name, observed_value, threshold_value, side,
            rejected_at if rejected_at is not None else time.time(), unit_cost)


def test_upsert_store_collapses_repeated_submissions_to_the_same_key_in_memory(tmp_path, monkeypatch):
    from services import capture_writer
    db_path = tmp_path / "candidate_log_test.db"
    monkeypatch.setattr(capture_writer, "_STORE_PATHS", {"rejected_candidates": db_path})
    monkeypatch.setattr(capture_writer, "_buffers", {"rejected_candidates": {}})
    monkeypatch.setattr(capture_writer, "_last_flush_at", {"rejected_candidates": 0.0})

    capture_writer.submit("rejected_candidates", _rejected_candidates_row(observed_value=10.0, rejected_at=1000.0))
    capture_writer.submit("rejected_candidates", _rejected_candidates_row(observed_value=15.0, rejected_at=2000.0))
    capture_writer.submit("rejected_candidates", _rejected_candidates_row(observed_value=20.0, rejected_at=3000.0))
    assert capture_writer.depth()["rejected_candidates"] == 1  # same (ticker, strategy, gate_name) key

    capture_writer.flush_now("rejected_candidates")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM rejected_candidates").fetchall()
    conn.close()
    assert len(rows) == 1  # not 3
    assert rows[0]["observed_value"] == 20.0  # latest wins, not first


def test_upsert_store_never_overwrites_an_already_resolved_row(tmp_path, monkeypatch):
    """The real invariant this UPSERT SQL exists to enforce (candidate_log.
    record_rejection's own former docstring): once resolved, a row is
    frozen - a later rejection of the same key must not un-resolve or
    mutate it. Proven against the real UPSERT SQL, not assumed."""
    from services import capture_writer
    db_path = tmp_path / "candidate_log_test2.db"
    monkeypatch.setattr(capture_writer, "_STORE_PATHS", {"rejected_candidates": db_path})
    monkeypatch.setattr(capture_writer, "_buffers", {"rejected_candidates": {}})
    monkeypatch.setattr(capture_writer, "_last_flush_at", {"rejected_candidates": 0.0})

    capture_writer.submit("rejected_candidates", _rejected_candidates_row(observed_value=10.0))
    capture_writer.flush_now("rejected_candidates")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE rejected_candidates SET resolved = 1, result = 'yes' "
        "WHERE ticker = 'TICK-A' AND strategy = 'whale_watcher' AND gate_name = 'min_contracts'",
    )
    conn.commit()
    conn.close()

    capture_writer.submit("rejected_candidates", _rejected_candidates_row(observed_value=999.0))
    capture_writer.flush_now("rejected_candidates")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM rejected_candidates").fetchone()
    conn.close()
    assert row["observed_value"] == 10.0  # unchanged, not overwritten by the post-resolve rejection
    assert row["resolved"] == 1


def test_upsert_store_different_keys_produce_separate_rows(tmp_path, monkeypatch):
    from services import capture_writer
    db_path = tmp_path / "candidate_log_test3.db"
    monkeypatch.setattr(capture_writer, "_STORE_PATHS", {"rejected_candidates": db_path})
    monkeypatch.setattr(capture_writer, "_buffers", {"rejected_candidates": {}})
    monkeypatch.setattr(capture_writer, "_last_flush_at", {"rejected_candidates": 0.0})

    capture_writer.submit("rejected_candidates", _rejected_candidates_row(ticker="TICK-A"))
    capture_writer.submit("rejected_candidates", _rejected_candidates_row(ticker="TICK-B"))
    capture_writer.submit("rejected_candidates", _rejected_candidates_row(gate_name="entry_threshold"))
    assert capture_writer.depth()["rejected_candidates"] == 3

    capture_writer.flush_now("rejected_candidates")
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM rejected_candidates").fetchone()[0] == 3
    conn.close()


def test_flush_sets_wal_journal_mode_on_a_fresh_db(tmp_path, monkeypatch):
    """Closes a real gap present since Task 14: _flush_store never set
    journal_mode itself, relying on the owning module's own _connect()
    having run first to leave the file in WAL - true today only
    incidentally. A store whose only writer is ever this module needs
    this set directly, the same "bursty write took the app down under
    rollback-journal mode" class of incident CLAUDE.md documents."""
    from services import capture_writer
    db_path = tmp_path / "wal_test.db"
    monkeypatch.setattr(capture_writer, "_STORE_PATHS", {"raw_trades": db_path})
    monkeypatch.setattr(capture_writer, "_buffers", {"raw_trades": [_sample_trade_row("wal1")]})
    monkeypatch.setattr(capture_writer, "_last_flush_at", {"raw_trades": 0.0})

    capture_writer.flush_now("raw_trades")

    conn = sqlite3.connect(db_path)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode.lower() == "wal"


def test_supervisor_restarts_a_dead_writer_thread():
    from services import capture_writer
    capture_writer.start()
    try:
        capture_writer._thread = None  # simulate an unexpected death without a real crash
        capture_writer.ensure_alive()
        assert capture_writer._thread is not None and capture_writer._thread.is_alive()
    finally:
        capture_writer.stop()


# --- retain-on-lock (issue #211) ---
#
# Production mechanism, measured 2026-08-30: series_watcher.db has more than
# one writer (this module's raw_trades flush; series_watcher.flush()'s
# book_snapshots INSERT; series_watcher.prune()'s hourly full-scan DELETE,
# which also runs on every process start). SQLite's write lock is per file,
# every one of those commits holds it longer than 50ms on this disk (a
# single WAL commit measured at a 36ms floor), so a collision with the old
# 50ms budget meant `database is locked` at executemany and a discarded
# batch - 224 faults, 346 rows measured lost in one 11h process lifetime.

def _fresh_counters(monkeypatch, capture_writer, stores):
    """Isolate every per-store counter this module keeps, so a test reads
    only what it caused (module state is global across the test process)."""
    for name in ("_dropped_counts", "_overflow_dropped_counts", "_lock_retry_counts"):
        monkeypatch.setattr(capture_writer, name, {s: 0 for s in stores})
    monkeypatch.setattr(capture_writer, "_last_flush_at", {s: 0.0 for s in stores})


def _hold_write_lock(db_path):
    """A second connection holding the file's single write lock - what
    series_watcher.prune()/flush() do to this module in production (WAL:
    readers and no-op DDL pass, a second writer gets SQLITE_BUSY once its
    busy_timeout runs out - probed 2026-08-30, tmp DB)."""
    holder = sqlite3.connect(db_path)
    holder.execute("PRAGMA journal_mode=WAL")
    holder.execute("BEGIN IMMEDIATE")
    return holder


def _release(holder):
    holder.rollback()
    holder.close()


def test_lock_collision_retains_the_batch_instead_of_dropping_it(tmp_path, monkeypatch):
    """A lock is a timing failure, not a data failure: the rows stay
    buffered and land on the next flush that gets the lock. Nothing is
    counted as dropped; the collision is counted as a retry."""
    from services import capture_writer
    db_path = tmp_path / "lock_test.db"
    monkeypatch.setattr(capture_writer, "_STORE_PATHS", {"raw_trades": db_path})
    monkeypatch.setattr(capture_writer, "_buffers", {"raw_trades": [_sample_trade_row("seed")]})
    _fresh_counters(monkeypatch, capture_writer, ["raw_trades"])
    capture_writer.flush_now("raw_trades")  # creates the file and the table

    holder = _hold_write_lock(db_path)
    try:
        for i in range(3):
            capture_writer.submit("raw_trades", _sample_trade_row(f"locked{i}"))
        capture_writer._flush_store("raw_trades")  # must not raise
        assert capture_writer.depth()["raw_trades"] == 3  # retained, not cleared
        assert capture_writer.dropped_count()["raw_trades"] == 0
        assert capture_writer.overflow_dropped_count()["raw_trades"] == 0
        assert capture_writer.lock_retry_count()["raw_trades"] == 1
    finally:
        _release(holder)

    capture_writer.flush_now("raw_trades")
    assert capture_writer.depth()["raw_trades"] == 0
    conn = sqlite3.connect(db_path)
    ids = sorted(r[0] for r in conn.execute("SELECT trade_id FROM raw_trades"))
    conn.close()
    assert ids == ["locked0", "locked1", "locked2", "seed"]


def test_retained_rows_stay_ahead_of_rows_submitted_after_the_collision(tmp_path, monkeypatch):
    """Re-queued rows go back to the FRONT of the buffer: a row submitted
    while the lock was held is written after the older row the lock merely
    delayed, so the archive's insert order still follows arrival order."""
    from services import capture_writer
    db_path = tmp_path / "order_test.db"
    monkeypatch.setattr(capture_writer, "_STORE_PATHS", {"raw_trades": db_path})
    monkeypatch.setattr(capture_writer, "_buffers", {"raw_trades": [_sample_trade_row("seed")]})
    _fresh_counters(monkeypatch, capture_writer, ["raw_trades"])
    capture_writer.flush_now("raw_trades")

    holder = _hold_write_lock(db_path)
    try:
        capture_writer.submit("raw_trades", _sample_trade_row("older"))
        capture_writer._flush_store("raw_trades")
        capture_writer.submit("raw_trades", _sample_trade_row("newer"))
    finally:
        _release(holder)
    capture_writer.flush_now("raw_trades")

    conn = sqlite3.connect(db_path)
    order = [r[0] for r in conn.execute("SELECT trade_id FROM raw_trades ORDER BY rowid")]
    conn.close()
    assert order == ["seed", "older", "newer"]


def test_retained_buffer_is_capped_and_the_overflow_is_counted_apart_from_failures(tmp_path, monkeypatch):
    """The cap is a NEW drop path and gets its own counter, so a reader can
    tell "lost because the lock outlasted what the buffer could hold" from
    "lost to a non-retryable flush failure". Oldest rows go first: the
    retained buffer always holds the newest cap-many rows."""
    from services import capture_writer
    db_path = tmp_path / "cap_test.db"
    monkeypatch.setattr(capture_writer, "_STORE_PATHS", {"raw_trades": db_path})
    monkeypatch.setattr(capture_writer, "_buffers", {"raw_trades": [_sample_trade_row("seed")]})
    monkeypatch.setattr(capture_writer, "_MAX_RETAINED_ROWS", 5)
    _fresh_counters(monkeypatch, capture_writer, ["raw_trades"])
    capture_writer.flush_now("raw_trades")

    holder = _hold_write_lock(db_path)
    try:
        for i in range(8):
            capture_writer.submit("raw_trades", _sample_trade_row(f"r{i}"))
        capture_writer._flush_store("raw_trades")
        assert capture_writer.depth()["raw_trades"] == 5
        assert capture_writer.overflow_dropped_count()["raw_trades"] == 3
        assert capture_writer.dropped_count()["raw_trades"] == 0
        assert capture_writer.lock_retry_count()["raw_trades"] == 1
    finally:
        _release(holder)
    capture_writer.flush_now("raw_trades")

    conn = sqlite3.connect(db_path)
    ids = sorted(r[0] for r in conn.execute("SELECT trade_id FROM raw_trades"))
    conn.close()
    assert ids == ["r3", "r4", "r5", "r6", "r7", "seed"]


def test_a_non_lock_failure_still_drops_the_batch_and_never_retries_it(tmp_path, monkeypatch):
    """Only SQLITE_BUSY/SQLITE_LOCKED are retryable. A schema failure (here:
    no such table, no DDL registered) would fail identically forever, so
    retaining it would only fill the buffer to the cap and then report the
    loss under the wrong name."""
    from services import capture_writer
    db_path = tmp_path / "nonlock_test.db"
    monkeypatch.setattr(capture_writer, "_STORE_PATHS", {"nonexistent_store": db_path})
    monkeypatch.setattr(capture_writer, "_STORE_TABLE", {"nonexistent_store": "nonexistent_table"})
    monkeypatch.setattr(capture_writer, "_STORE_DDL", {})
    monkeypatch.setattr(capture_writer, "_buffers", {"nonexistent_store": [("a", "b"), ("c", "d")]})
    _fresh_counters(monkeypatch, capture_writer, ["nonexistent_store"])

    capture_writer._flush_store("nonexistent_store")

    assert capture_writer.dropped_count()["nonexistent_store"] == 2
    assert capture_writer.depth()["nonexistent_store"] == 0
    assert capture_writer.lock_retry_count()["nonexistent_store"] == 0
    assert capture_writer.overflow_dropped_count()["nonexistent_store"] == 0


def test_upsert_store_lock_collision_keeps_latest_value_per_key_across_the_retry(tmp_path, monkeypatch):
    """Upsert-mode stores buffer as a dict. A retained batch merges back
    under the same latest-wins rule submit() already applies in memory: a
    value submitted after the collision beats the retained one for the
    same key, and other keys are simply kept."""
    from services import capture_writer
    db_path = tmp_path / "upsert_lock_test.db"
    monkeypatch.setattr(capture_writer, "_STORE_PATHS", {"rejected_candidates": db_path})
    monkeypatch.setattr(capture_writer, "_buffers", {"rejected_candidates": {}})
    _fresh_counters(monkeypatch, capture_writer, ["rejected_candidates"])
    capture_writer.submit("rejected_candidates", _rejected_candidates_row(ticker="SEED"))
    capture_writer.flush_now("rejected_candidates")

    holder = _hold_write_lock(db_path)
    try:
        capture_writer.submit("rejected_candidates", _rejected_candidates_row(ticker="A", observed_value=10.0))
        capture_writer._flush_store("rejected_candidates")
        assert capture_writer.depth()["rejected_candidates"] == 1
        assert capture_writer.lock_retry_count()["rejected_candidates"] == 1
        capture_writer.submit("rejected_candidates", _rejected_candidates_row(ticker="A", observed_value=20.0))
        capture_writer.submit("rejected_candidates", _rejected_candidates_row(ticker="B", observed_value=30.0))
        assert capture_writer.depth()["rejected_candidates"] == 2
    finally:
        _release(holder)
    capture_writer.flush_now("rejected_candidates")

    conn = sqlite3.connect(db_path)
    rows = dict(conn.execute("SELECT ticker, observed_value FROM rejected_candidates"))
    conn.close()
    assert rows == {"SEED": 10.0, "A": 20.0, "B": 30.0}


def test_daemon_thread_waits_out_a_short_lock_instead_of_bouncing_off_it(tmp_path, monkeypatch):
    """The daemon thread is the one thread nothing waits on, so it waits out
    a lock for one flush cycle (_DAEMON_BUSY_TIMEOUT_MS - 10x the slowest
    contending hold measured, the hourly prune). A lock held for 300ms -
    longer than the old 50ms budget that discarded the batch, far shorter
    than the daemon's - must end with the rows written and NO retry
    counted: the daemon waited, it did not bounce."""
    from services import capture_writer
    db_path = tmp_path / "daemon_wait_test.db"
    monkeypatch.setattr(capture_writer, "_STORE_PATHS", {"raw_trades": db_path})
    monkeypatch.setattr(capture_writer, "_buffers", {"raw_trades": [_sample_trade_row("seed")]})
    monkeypatch.setattr(capture_writer, "_FLUSH_INTERVAL_SEC", 0.05)
    _fresh_counters(monkeypatch, capture_writer, ["raw_trades"])
    capture_writer.flush_now("raw_trades")

    holder = _hold_write_lock(db_path)
    capture_writer.start()
    try:
        capture_writer.submit("raw_trades", _sample_trade_row("waited"))
        time.sleep(0.3)
        _release(holder)
        assert _row_count_when_ready(db_path, "raw_trades", at_least=2) == 2
        assert capture_writer.lock_retry_count()["raw_trades"] == 0
        assert capture_writer.dropped_count()["raw_trades"] == 0
    finally:
        capture_writer.stop()


def test_flush_now_on_a_callers_thread_keeps_the_short_budget(tmp_path, monkeypatch):
    """flush_now runs on the CALLER's thread (candidate_log's resolve and
    summary paths, some on the event loop). It keeps the 50ms budget and
    hands a locked batch back to the buffer for the daemon instead of
    stalling its caller for seconds."""
    from services import capture_writer
    db_path = tmp_path / "flush_now_budget_test.db"
    monkeypatch.setattr(capture_writer, "_STORE_PATHS", {"raw_trades": db_path})
    monkeypatch.setattr(capture_writer, "_buffers", {"raw_trades": [_sample_trade_row("seed")]})
    _fresh_counters(monkeypatch, capture_writer, ["raw_trades"])
    capture_writer.flush_now("raw_trades")

    holder = _hold_write_lock(db_path)
    try:
        capture_writer.submit("raw_trades", _sample_trade_row("held"))
        t0 = time.perf_counter()
        capture_writer.flush_now("raw_trades")
        assert time.perf_counter() - t0 < 1.0
        assert capture_writer.depth()["raw_trades"] == 1
        assert capture_writer.lock_retry_count()["raw_trades"] == 1
    finally:
        _release(holder)


def test_stop_under_a_held_lock_returns_promptly_and_keeps_the_rows_buffered(tmp_path, monkeypatch):
    """The shutdown pass uses the short budget too: main.py's lifespan joins
    this thread with a 2s timeout, and a 5s wait there would leave the
    thread blocked past the join with its buffer lost silently. Rows that
    cannot be written at exit stay buffered and counted as buffered - never
    as written, never as dropped."""
    from services import capture_writer
    db_path = tmp_path / "stop_lock_test.db"
    monkeypatch.setattr(capture_writer, "_STORE_PATHS", {"raw_trades": db_path})
    monkeypatch.setattr(capture_writer, "_buffers", {"raw_trades": [_sample_trade_row("seed")]})
    _fresh_counters(monkeypatch, capture_writer, ["raw_trades"])
    capture_writer.flush_now("raw_trades")

    holder = _hold_write_lock(db_path)
    try:
        capture_writer.start()
        capture_writer.submit("raw_trades", _sample_trade_row("at_exit"))
        t0 = time.perf_counter()
        capture_writer.stop()
        assert time.perf_counter() - t0 < 1.5
        assert capture_writer.depth()["raw_trades"] == 1
        assert capture_writer.dropped_count()["raw_trades"] == 0
    finally:
        _release(holder)


def test_loss_snapshot_names_every_counter_for_the_pipeline_endpoint(tmp_path, monkeypatch):
    """/api/health/pipeline and tools/soak_analyzer.py read this one dict;
    the names are the contract the soak gate keys on."""
    from services import capture_writer
    monkeypatch.setattr(capture_writer, "_buffers", {"raw_trades": []})
    _fresh_counters(monkeypatch, capture_writer, ["raw_trades"])
    snap = capture_writer.loss_snapshot()
    assert snap["dropped_rows"] == {"raw_trades": 0}
    assert snap["overflow_dropped_rows"] == {"raw_trades": 0}
    assert snap["lock_retries"] == {"raw_trades": 0}
    assert snap["depth"] == {"raw_trades": 0}
    assert snap["max_retained_rows"] == capture_writer._MAX_RETAINED_ROWS
    assert snap["counter_scope"] == "process lifetime"


def test_daemon_busy_budget_stays_inside_the_stop_join():
    """The relation the shutdown path rests on: stop() joins for
    _STOP_JOIN_SEC, and a flush already blocked on the daemon budget when
    stop() is called must return inside it. Raising the budget to sqlite3's
    5s default without raising the join would reopen the one silent loss
    path this module has - a batch in a thread the process exits without.
    The budget also stays within one flush cycle: a longer hold is the
    retain path's job (retried next cycle), not the busy handler's."""
    import inspect
    from services import capture_writer
    assert capture_writer._DAEMON_BUSY_TIMEOUT_MS / 1000 < capture_writer._STOP_JOIN_SEC
    assert capture_writer._DAEMON_BUSY_TIMEOUT_MS <= capture_writer._FLUSH_INTERVAL_SEC * 1000
    assert inspect.signature(capture_writer.stop).parameters["timeout_sec"].default == capture_writer._STOP_JOIN_SEC


def test_stop_waits_out_a_flush_already_blocked_on_the_daemon_budget(tmp_path, monkeypatch):
    """stop() called while the daemon is already inside its busy wait: the
    thread must finish inside the join - the lock never frees here, so
    'finish' means the batch is back in the buffer and counted - rather
    than outlive the join in a thread the process then exits without."""
    from services import capture_writer
    db_path = tmp_path / "stop_midwait_test.db"
    monkeypatch.setattr(capture_writer, "_STORE_PATHS", {"raw_trades": db_path})
    monkeypatch.setattr(capture_writer, "_buffers", {"raw_trades": [_sample_trade_row("seed")]})
    monkeypatch.setattr(capture_writer, "_FLUSH_INTERVAL_SEC", 0.05)
    _fresh_counters(monkeypatch, capture_writer, ["raw_trades"])
    capture_writer.flush_now("raw_trades")

    holder = _hold_write_lock(db_path)
    try:
        capture_writer.start()
        thread = capture_writer._thread
        capture_writer.submit("raw_trades", _sample_trade_row("midwait"))
        for _ in range(100):  # until the daemon has popped the batch and is blocked in its busy wait
            if capture_writer.depth()["raw_trades"] == 0:
                break
            time.sleep(0.02)
        assert capture_writer.depth()["raw_trades"] == 0
        t0 = time.perf_counter()
        capture_writer.stop()
        assert time.perf_counter() - t0 < capture_writer._STOP_JOIN_SEC
        assert not thread.is_alive()
        assert capture_writer.depth()["raw_trades"] == 1
        assert capture_writer.dropped_count()["raw_trades"] == 0
        assert capture_writer.lock_retry_count()["raw_trades"] >= 1
    finally:
        _release(holder)
