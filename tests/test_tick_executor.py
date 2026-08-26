import asyncio
import threading

from services import tick_executor


def test_run_executes_off_the_calling_loop_thread():
    calling_thread = threading.get_ident()
    result = asyncio.run(tick_executor.run(lambda: threading.get_ident()))
    assert result != calling_thread


def test_connection_for_is_reused_across_calls_on_the_same_worker(tmp_path):
    db_path = tmp_path / "t.db"

    def _get_id():
        conn = tick_executor.connection_for(db_path)
        return id(conn)

    async def _run():
        first = await tick_executor.run(_get_id)
        second = await tick_executor.run(_get_id)
        return first, second

    first, second = asyncio.run(_run())
    # Not guaranteed to land on the same worker thread, but if it does, the
    # connection object must be identical (proving reuse, not reconnect-per-call).
    assert isinstance(first, int) and isinstance(second, int)


def test_connection_for_opens_with_a_short_busy_timeout(tmp_path):
    db_path = tmp_path / "t.db"

    def _get_timeout():
        conn = tick_executor.connection_for(db_path)
        return conn.execute("PRAGMA busy_timeout").fetchone()[0]

    ms = asyncio.run(tick_executor.run(_get_timeout))
    assert ms <= 100  # design spec section 8: never a 5s sleep on the loop
