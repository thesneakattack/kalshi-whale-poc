"""services/diagnostics/_aio_db.py - the shared, loop-scoped aiosqlite
connection cache services/diagnostics/diagnostics.py and
services/series_watcher.py's read-only functions share.
"""
import asyncio
import sqlite3

import pytest

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
    asyncio.run(_aio_db.reset())


def test_connection_for_is_cached_within_the_same_loop(tmp_path):
    db_path = tmp_path / "t.db"

    async def _run():
        first = await _aio_db.connection_for(db_path)
        second = await _aio_db.connection_for(db_path)
        return first is second

    assert asyncio.run(_run()) is True
    asyncio.run(_aio_db.reset())


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

    async def _run():
        # Loop A (this asyncio.run()'s own loop) opens both connections and
        # keeps live references.
        conn_a = await _aio_db.connection_for(db_a)
        conn_b = await _aio_db.connection_for(db_b)
        await conn_a.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER)")
        await conn_b.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER)")
        await conn_a.commit()
        await conn_b.commit()

        # Loop B: a genuinely separate event loop - run in a worker thread
        # (not nested inside loop A, which isn't allowed) so it has its own
        # context to create and run. Reopens db_a under its OWN cache key,
        # then closes only ITS OWN loop's entries.
        def _run_loop_b_in_thread():
            loop_b = asyncio.new_event_loop()
            try:
                async def _loop_b_work():
                    conn_a_on_b = await _aio_db.connection_for(db_a)
                    await _aio_db.close_for_current_loop()
                    return conn_a_on_b

                closed_conn = loop_b.run_until_complete(_loop_b_work())

                # The connection loop B itself opened and then closed via
                # close_for_current_loop() must actually be closed (a second
                # use raises) - proves close_for_current_loop() really closes,
                # not just evicts from the dict. (Restores the check the
                # previous rewrite dropped.)
                async def _use_after_close():
                    await closed_conn.execute("SELECT 1")

                # Expect this to raise - the connection was closed
                try:
                    loop_b.run_until_complete(_use_after_close())
                    return False  # Didn't raise - test should fail
                except Exception:
                    return True  # Raised as expected
            finally:
                loop_b.close()

        # Run loop B in a separate thread where there's no running event loop
        closed_really = await asyncio.to_thread(_run_loop_b_in_thread)
        assert closed_really

        # Back under loop A's own still-running context (never crossing
        # loops to use a connection, which is exactly the hazard this
        # module exists to prevent) - loop A's own conn_a/conn_b must
        # still be open and usable, positively proving
        # close_for_current_loop() under loop B left loop A's entries
        # untouched (not just a dict-length proxy for that claim).
        await conn_a.execute("SELECT 1")
        await conn_b.execute("SELECT 1")

    asyncio.run(_run())
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


def test_connection_for_reopens_a_connection_closed_out_from_under_it(tmp_path):
    # Finding I1 (PR adversarial review, 2026-09-01): connection_for()'s
    # docstring claimed the same contract as _scoring_pool's
    # cached_read_connection(), which liveness-probes and evicts-and-reopens,
    # while connection_for() returned whatever was cached forever. Before
    # this module existed each call opened its own connection, so breakage
    # self-healed; without the probe the diagnostics subsystem would report
    # "unreadable" permanently and quietly instead.
    db_path = tmp_path / "t.db"

    async def _run():
        first = await _aio_db.connection_for(db_path)
        await first.execute("CREATE TABLE t (id INTEGER)")
        await first.commit()

        # Close it directly, bypassing _aio_db entirely, so the cache is left
        # holding a dead connection object - "closed out from under us
        # elsewhere", the case the sibling module recovers from.
        await first.close()

        second = await _aio_db.connection_for(db_path)
        assert second is not first, "returned the dead cached connection instead of reopening"

        # Not just a different object: a genuinely usable one, against the
        # same file (the table opened above must be visible through it).
        rows = await second.execute_fetchall("SELECT COUNT(*) FROM t")
        return rows[0][0]

    assert asyncio.run(_run()) == 0
    asyncio.run(_aio_db.reset())


def test_schema_init_failure_closes_the_connection_and_caches_nothing(tmp_path):
    # Finding I2 (PR adversarial review, 2026-09-01): a raising schema_init
    # left the connection neither cached nor closed, so its NON-daemon
    # aiosqlite worker thread survived for the rest of the process - once per
    # failed call, ~8 per run_offline() on a route polled every ~5s, while
    # every caller degraded "honestly" through its except sqlite3.Error branch.
    db_path = tmp_path / "t.db"
    handed_to_schema_init = []

    async def _boom(conn):
        handed_to_schema_init.append(conn)
        raise sqlite3.DatabaseError("simulated: disk image malformed")

    async def _run():
        with pytest.raises(sqlite3.DatabaseError):
            await _aio_db.connection_for(db_path, schema_init=_boom)

        # Nothing cached for THIS key, so a later call retries cleanly rather
        # than being stuck with a half-initialised connection. Scoped to this
        # db_path rather than asserting the whole dict is empty: an absolute
        # assertion on a module global would couple this test to collection
        # order and to any other test that left an entry behind.
        assert not [k for k in _aio_db._connections if k[1] == db_path]

        # And the connection really was closed, not merely dropped on the
        # floor. Closing is what sends aiosqlite's worker thread its stop
        # sentinel, so proving "closed" is proving the thread does not leak.
        # A closed aiosqlite connection raises ValueError (not any
        # sqlite3.Error) on reuse - verified against the installed 0.22.1.
        assert len(handed_to_schema_init) == 1
        with pytest.raises(ValueError):
            await handed_to_schema_init[0].execute_fetchall("SELECT 1")

        # The retry path genuinely works afterwards.
        conn = await _aio_db.connection_for(db_path)
        rows = await conn.execute_fetchall("SELECT 1")
        return rows[0][0]

    assert asyncio.run(_run()) == 1
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
