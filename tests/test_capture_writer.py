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


def _row_count_when_ready(db_path, table: str, attempts: int = 50, delay: float = 0.05) -> int:
    """Polls the DB directly rather than capture_writer.depth() - depth()
    hits 0 the instant _flush_store clears the in-memory buffer, which
    happens BEFORE the DB write completes (deliberate: submit() must never
    block on disk I/O), so depth()==0 is not a valid "flush landed" signal.
    Under normal load the gap is sub-millisecond and invisible; under
    testmon's coverage instrumentation it was wide enough to fail this
    test's original depth()-based poll outright - a real race in the test,
    not in capture_writer itself."""
    for _ in range(attempts):
        try:
            conn = sqlite3.connect(db_path)
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            finally:
                conn.close()
            if count:
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
