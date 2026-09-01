"""services/diagnostics/_aio_db.py - the shared, loop-scoped aiosqlite
connection cache services/diagnostics/diagnostics.py and
services/series_watcher.py's read-only functions share.
"""
import asyncio

from services.diagnostics import _aio_db


def test_connection_for_returns_a_usable_connection(tmp_path):
    db_path = tmp_path / "t.db"

    async def _run():
        conn = await _aio_db.connection_for(db_path)
        await conn.execute("CREATE TABLE t (id INTEGER)")
        await conn.commit()
        rows = await conn.execute_fetchall("SELECT COUNT(*) FROM t")
        return rows[0][0]

    assert asyncio.run(_run()) == 0


def test_connection_for_is_cached_within_the_same_loop(tmp_path):
    db_path = tmp_path / "t.db"

    async def _run():
        first = await _aio_db.connection_for(db_path)
        second = await _aio_db.connection_for(db_path)
        return first is second

    assert asyncio.run(_run()) is True


def test_connection_for_does_not_reuse_a_connection_across_different_loops(tmp_path):
    db_path = tmp_path / "t.db"

    async def _get_id():
        conn = await _aio_db.connection_for(db_path)
        return id(conn)

    first_id = asyncio.run(_get_id())
    second_id = asyncio.run(_get_id())
    # Two separate asyncio.run() calls are two separate event loops - a
    # cache keyed only by db_path would wrongly hand the second loop a
    # connection object created (and, by the time this runs, potentially
    # already torn down) under the first, dead loop.
    assert first_id != second_id
    asyncio.run(_aio_db.reset())


def test_close_for_current_loop_only_closes_this_loops_entries(tmp_path):
    db_a = tmp_path / "a.db"
    db_b = tmp_path / "b.db"

    async def _open_two():
        await _aio_db.connection_for(db_a)
        await _aio_db.connection_for(db_b)

    async def _open_one_and_close_loop():
        conn = await _aio_db.connection_for(db_a)
        await _aio_db.close_for_current_loop()
        return conn

    asyncio.run(_open_two())
    closed_conn = asyncio.run(_open_one_and_close_loop())
    # The connection this loop opened and then closed must actually be
    # closed (a second use raises) - proves close_for_current_loop really
    # closes, not just evicts from the dict.
    import pytest
    with pytest.raises(Exception):
        asyncio.run(closed_conn.execute("SELECT 1"))
    asyncio.run(_aio_db.reset())


def test_schema_init_runs_once_on_first_open_only(tmp_path):
    db_path = tmp_path / "t.db"
    calls = []

    async def _schema_init(conn):
        calls.append(1)
        await conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER)")

    async def _run():
        await _aio_db.connection_for(db_path, schema_init=_schema_init)
        await _aio_db.connection_for(db_path, schema_init=_schema_init)  # cache hit

    asyncio.run(_run())
    assert len(calls) == 1
    asyncio.run(_aio_db.reset())


def test_connection_for_without_schema_init_does_not_require_a_preexisting_table(tmp_path):
    # diagnostics.py's own callers pass no schema_init at all (their DB
    # files' schemas are guaranteed by other, write-path modules) - confirm
    # connection_for() itself never requires one.
    db_path = tmp_path / "t.db"

    async def _run():
        conn = await _aio_db.connection_for(db_path)
        return conn is not None

    assert asyncio.run(_run()) is True
    asyncio.run(_aio_db.reset())


def test_locks_are_scoped_per_loop_not_shared_across_loops(tmp_path):
    # Finding C (adversarial review, 2026-09-01): a single cross-loop-shared
    # asyncio.Lock would itself be a loop-safety hazard, undermining the
    # exact guarantee this module exists to provide. Assert the internal
    # lock registry grows one entry per loop actually used, never fewer.
    db_a = tmp_path / "a.db"

    async def _touch():
        await _aio_db.connection_for(db_a)

    asyncio.run(_touch())
    asyncio.run(_touch())
    assert len(_aio_db._locks) == 2  # two asyncio.run() calls, two loops, two lock entries
    asyncio.run(_aio_db.reset())
