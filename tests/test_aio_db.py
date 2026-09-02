"""services/diagnostics/_aio_db.py - the shared, loop-scoped aiosqlite
connection cache services/diagnostics/diagnostics.py and
services/series_watcher.py's read-only functions share.
"""
import asyncio
import contextlib
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
    """_aio_db keeps a pool of connections per (loop, db_path), handed out
    round-robin - not a single one - so identity across the first several
    calls legitimately differs (fast-follow to #420, 2026-09-02: live-
    measured that 5 concurrent identical requests against real data
    serialized on a single connection's one worker thread for minutes).
    The pool is ELASTIC (grows under concurrent demand, see the dedicated
    growth test below), but a run of purely SEQUENTIAL calls - no two ever
    in flight at once - never observes concurrent demand, so it settles at
    exactly _MIN_POOL_SIZE and stays there. What must still hold is the
    actual cache contract: no NEW connection is opened once every slot is
    filled - round-robin wraps back to objects already returned, not
    growing without bound just because more calls keep arriving one at a
    time."""
    db_path = tmp_path / "t.db"

    async def _run():
        seen = [await _aio_db.connection_for(db_path) for _ in range(_aio_db._MIN_POOL_SIZE)]
        wrapped = await _aio_db.connection_for(db_path)
        return len({id(c) for c in seen}) == _aio_db._MIN_POOL_SIZE, wrapped in seen

    distinct_count_matches_pool_size, wraps_to_a_seen_connection = asyncio.run(_run())
    assert distinct_count_matches_pool_size
    assert wraps_to_a_seen_connection
    asyncio.run(_aio_db.reset())


def test_connection_for_grows_the_pool_under_real_concurrent_demand(tmp_path):
    """The elastic half of the contract: _MIN_POOL_SIZE is only a floor.
    When more callers are genuinely concurrent for the same key than the
    current pool holds, the pool grows (lazily, capped at _MAX_POOL_SIZE)
    rather than making the extra callers round-robin over too few
    connections - live-measured need, 2026-09-02: 5 concurrent identical
    requests against real data serialized for minutes even at a fixed pool
    of 2. A slow schema_init holds each slot's creation open long enough
    to force genuine overlap, so this actually exercises concurrency
    rather than asserting on timing alone."""
    db_path = tmp_path / "t.db"
    assert _aio_db._MAX_POOL_SIZE > _aio_db._MIN_POOL_SIZE, (
        "test assumes real headroom to grow into"
    )
    concurrency = min(_aio_db._MAX_POOL_SIZE, _aio_db._MIN_POOL_SIZE + 3)

    async def _schema_init(conn):
        await asyncio.sleep(0.05)  # widens the overlap window deterministically
        await conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER)")

    async def _run():
        conns = await asyncio.gather(*[
            _aio_db.connection_for(db_path, schema_init=_schema_init)
            for _ in range(concurrency)
        ])
        return len({id(c) for c in conns})

    distinct = asyncio.run(_run())
    assert distinct > _aio_db._MIN_POOL_SIZE, (
        f"only {distinct} distinct connections were created for {concurrency} "
        "genuinely concurrent callers - the pool did not grow past its floor"
    )
    assert distinct <= _aio_db._MAX_POOL_SIZE
    asyncio.run(_aio_db.reset())


def test_connection_for_heals_every_dead_slot_not_just_the_first_one_probed(tmp_path):
    """Regression test for adversarial-review finding C2 (2026-09-02, round
    1 of this elastic pool): the first version's locked-section fill loop
    only ever CREATED connections for None slots and otherwise trusted any
    non-None slot as alive without probing it. Reproduced sequentially,
    with zero concurrency: fill a 2-slot pool, kill BOTH underlying
    connections from outside _aio_db (simulating "closed elsewhere",
    exactly what the liveness probe exists to catch), then confirm the
    NEXT _MIN_POOL_SIZE calls each return a genuinely live, usable
    connection - not a dead one that happened to not be the slot most
    recently round-robined to and probed."""
    db_path = tmp_path / "t.db"

    async def _run():
        # Fill exactly _MIN_POOL_SIZE slots (sequential calls never grow
        # past the floor - see test_connection_for_is_cached_within_the_
        # same_loop).
        seeded = [await _aio_db.connection_for(db_path) for _ in range(_aio_db._MIN_POOL_SIZE)]
        # Kill every one of them directly, bypassing _aio_db entirely - the
        # exact "closed out from under us elsewhere" scenario the liveness
        # probe exists for.
        for conn in seeded:
            await conn.close()
        # Every subsequent call must hand back something genuinely usable,
        # regardless of which slot round-robin lands on - not just the one
        # slot a fast-path probe happens to have already evicted.
        results = []
        for _ in range(_aio_db._MIN_POOL_SIZE):
            conn = await _aio_db.connection_for(db_path)
            assert conn is not None, "connection_for() returned None"
            rows = await conn.execute_fetchall("SELECT 1")
            results.append(rows[0][0])
        return results

    assert asyncio.run(_run()) == [1] * _aio_db._MIN_POOL_SIZE
    asyncio.run(_aio_db.reset())


def test_connection_for_never_returns_none_or_a_dead_connection_under_concurrency(tmp_path):
    """Regression test for adversarial-review finding C1 (2026-09-02, round
    1 of this elastic pool): the fast path could null a slot (on a failed
    probe) or advance _round_robin past it WITHOUT the lock, racing against
    a different coroutine's locked section that had already decided to
    return that exact index - the locked section's final `pool[idx]` read
    could then observe None (AttributeError on use) or an unprobed dead
    connection.

    Statistical stress test, not a single deterministic interleaving (the
    exact race is timing-dependent): repeatedly kill a random live slot
    from outside _aio_db WHILE firing many genuinely concurrent
    connection_for() calls via asyncio.gather, and assert every single
    result is non-None and independently confirmed alive by a real query -
    not just that the batch as a whole didn't raise."""
    db_path = tmp_path / "t.db"
    concurrency = _aio_db._MAX_POOL_SIZE

    async def _one_round():
        # Prime the pool close to its cap so most callers hit the fast
        # path, then kill a couple of slots concurrently with a fresh
        # burst of callers - the exact shape (an eviction racing a
        # different coroutine's return decision) the finding describes.
        await asyncio.gather(*[_aio_db.connection_for(db_path) for _ in range(concurrency)])
        pool = _aio_db._connections.get(_aio_db._key(db_path))
        assert pool is not None

        async def _kill_a_slot(i):
            conn = pool[i % len(pool)]
            if conn is not None:
                with contextlib.suppress(Exception):
                    await conn.close()

        async def _get_and_verify():
            conn = await _aio_db.connection_for(db_path)
            assert conn is not None, "connection_for() returned None under concurrency"
            rows = await conn.execute_fetchall("SELECT 1")
            assert rows[0][0] == 1

        await asyncio.gather(
            *[_kill_a_slot(i) for i in range(len(pool))],
            *[_get_and_verify() for _ in range(concurrency)],
        )

    async def _run():
        for _ in range(25):  # repeated rounds: the race is timing-dependent
            await _one_round()

    asyncio.run(_run())
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


def test_schema_init_runs_once_per_pool_slot_never_again_after_the_pool_fills(tmp_path):
    """schema_init's idempotent DDL runs once per slot, on that slot's own
    first-ever open - once per pool slot total per key, not once (fast-
    follow to #420, 2026-09-02: a pool of connections per file, not 1).
    Purely sequential calls never trigger elastic growth (see the dedicated
    growth test), so the pool settles at exactly _MIN_POOL_SIZE here. What
    must still hold is the actual "never again" half of the contract: once
    every slot is filled, further calls are pure cache hits with zero new
    schema_init invocations, however many more calls are made."""
    db_path = tmp_path / "t.db"
    calls = []

    async def _schema_init(conn):
        calls.append(1)
        await conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER)")

    async def _run():
        for _ in range(_aio_db._MIN_POOL_SIZE + 3):  # +3: well past a full pool
            await _aio_db.connection_for(db_path, schema_init=_schema_init)

    asyncio.run(_run())
    assert len(calls) == _aio_db._MIN_POOL_SIZE
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
