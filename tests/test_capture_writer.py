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


def test_supervisor_restarts_a_dead_writer_thread():
    from services import capture_writer
    capture_writer.start()
    try:
        capture_writer._thread = None  # simulate an unexpected death without a real crash
        capture_writer.ensure_alive()
        assert capture_writer._thread is not None and capture_writer._thread.is_alive()
    finally:
        capture_writer.stop()
