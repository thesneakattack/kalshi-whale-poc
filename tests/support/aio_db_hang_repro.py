"""Standalone reproduction of issue #586, run as a subprocess (never
imported into the pytest process itself) so a real regression bounds to a
`subprocess.run(..., timeout=...)` failure in the parent test instead of
hanging the whole suite - see tests/test_aio_db.py's
test_close_all_at_process_exit_bounds_a_dead_workers_close for the caller.

Reproduces the exact mechanism: a query still in flight on an aiosqlite
connection's dedicated worker thread when ITS OWN event loop closes leaves
that thread trying to report back to a now-closed loop twice in a row
(`_connection_worker_thread`'s success path, then its own except-handler's
retry of the same call) - both raise `RuntimeError: Event loop is closed`,
the second escapes uncaught, and the worker thread dies. `connection_for()`
is used (not a bare `aiosqlite.connect()`) so this exercises the module's
real cache path, and `_close_all_at_process_exit()` is called directly
(rather than only relying on process-exit timing) so the elapsed time is
measured precisely against `_CLOSE_ALL_TIMEOUT_SEC`.

Usage: python tests/support/aio_db_hang_repro.py <db_path> <close_timeout_sec>
Exits 0 and prints "PASS" plus the measured elapsed time on success; a
process that never terminates on its own means the fix has regressed.
"""
import asyncio
import sys
import threading
import time
from pathlib import Path

from services.diagnostics import _aio_db


def main() -> None:
    db_path = Path(sys.argv[1])
    close_timeout_sec = float(sys.argv[2])
    _aio_db._CLOSE_ALL_TIMEOUT_SEC = close_timeout_sec

    loop1 = asyncio.new_event_loop()

    async def _setup_and_cancel_in_flight_query():
        conn = await _aio_db.connection_for(db_path)
        # A Python function run via a real SQL query, executed on the
        # connection's worker thread - long enough that it is still
        # running when loop1 closes below.
        await conn.create_function("slow", 0, lambda: (time.sleep(2.0) or 1))
        task = loop1.create_task(conn.execute("SELECT slow()"))
        await asyncio.sleep(0.1)  # let the query actually reach the worker thread
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    loop1.run_until_complete(_setup_and_cancel_in_flight_query())
    loop1.close()  # the worker thread is still inside slow()'s time.sleep(2.0)

    # Give the worker thread time to finish sleeping and hit the
    # closed-loop RuntimeError in its own except path, killing it.
    time.sleep(2.5)
    alive = [t for t in threading.enumerate() if t is not threading.main_thread()]
    if alive:
        print(f"FAIL: worker thread did not crash as expected: {[t.name for t in alive]}",
              file=sys.stderr)
        sys.exit(2)
    if not _aio_db._connections:
        print("FAIL: expected one cached connection going into the exit hook", file=sys.stderr)
        sys.exit(2)

    t0 = time.monotonic()
    _aio_db._close_all_at_process_exit()
    elapsed = time.monotonic() - t0

    if _aio_db._connections:
        print("FAIL: _connections was not cleared", file=sys.stderr)
        sys.exit(2)

    print(f"PASS elapsed={elapsed:.3f}", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
