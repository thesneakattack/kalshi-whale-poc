import asyncio
import sqlite3
import threading

from services.whalewatchers import _scoring_pool


def test_run_executes_on_a_worker_thread_not_the_event_loop():
    result_thread_name = {}

    def _work():
        result_thread_name["name"] = threading.current_thread().name

    asyncio.run(_scoring_pool.run(_work))
    assert result_thread_name["name"].startswith("whale-scoring")


def test_cached_read_connection_reuses_same_object_within_one_thread(tmp_path):
    db_path = tmp_path / "test.db"
    init_calls = []

    def schema_init(conn):
        init_calls.append(1)
        conn.execute("CREATE TABLE IF NOT EXISTS t (x INTEGER)")

    conn1 = _scoring_pool.cached_read_connection(db_path, schema_init)
    conn2 = _scoring_pool.cached_read_connection(db_path, schema_init)
    assert conn1 is conn2
    assert len(init_calls) == 1  # schema_init only ran on the first call


def test_cached_read_connection_is_thread_local(tmp_path):
    db_path = tmp_path / "test.db"
    conns = {}

    def grab(key):
        conns[key] = _scoring_pool.cached_read_connection(db_path, lambda c: None)

    t1 = threading.Thread(target=grab, args=("a",))
    t2 = threading.Thread(target=grab, args=("b",))
    t1.start(); t1.join()
    t2.start(); t2.join()
    assert conns["a"] is not conns["b"]


def test_cached_read_connection_uses_default_busy_timeout(tmp_path):
    db_path = tmp_path / "test.db"
    conn = _scoring_pool.cached_read_connection(db_path, lambda c: None)
    # sqlite3's C-level default is 5.0s when no timeout= is passed to connect()
    row = conn.execute("PRAGMA busy_timeout").fetchone()
    assert row[0] == 5000  # milliseconds


def test_cached_read_connection_recovers_from_a_closed_connection(tmp_path):
    db_path = tmp_path / "test.db"
    conn1 = _scoring_pool.cached_read_connection(db_path, lambda c: None)
    conn1.close()  # simulate the connection being closed out from under the cache
    conn2 = _scoring_pool.cached_read_connection(db_path, lambda c: None)
    assert conn2 is not conn1
    conn2.execute("SELECT 1")  # must be usable, not the stale closed one
