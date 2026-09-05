"""Issue #150 fix-family benchmark - Family 2 (root-cause the SQLite hang).

Investigates issue #150's own proposed root cause: "a busy_timeout gap, a
real lock rather than an ordinary SQLite wait that would otherwise resolve"
inside kalshi_trade_tape.py's _process_trades_sync (the function
services/kalshi/websocket.py's _process_item wraps in
asyncio.wait_for(..., timeout=10)).

Source-reading (not memory) established, before any benchmark ran, exactly
which connections _process_trades_sync's hot path opens:

  - candidate_log.record_rejection(): fully non-blocking as of 2026-08-27's
    P3 Task 17 - routes through capture_writer.submit(), zero synchronous
    SQLite I/O. Not a candidate for this hang at all any more.
  - signal_log.recent_sides_for_ticker / cluster_factor,
    market_history.momentum (via _trend_factor), market_analyst_agent's
    analyst_lean (via _analyst_factor): all four read through a
    thread-local CACHED connection (services/whalewatchers/_scoring_pool.py's
    cached_read_connection(), added 2026-09-01 - "Task 4/Task 2, write-path
    capacity fix"). schema_init (CREATE TABLE/INDEX IF NOT EXISTS) runs only
    on that thread's FIRST connect to that db_path, never on a cache hit -
    so these are genuinely bare `SELECT` autocommit reads on every
    subsequent call, not a fresh sqlite3.connect()+DDL every time.
  - series_evaluator.record_trades_observed_bulk(): _process_trades_sync
    builds a trades_observed_by_series dict intended to be flushed through
    this function once per fetch_signals() call (its own docstring, and a
    module-level comment, both say so) via services/db.py's connect()
    (explicit "PRAGMA busy_timeout = 5000") - but the actual call was
    NEVER ADDED. Confirmed by direct source read (the dict is built at
    kalshi_trade_tape.py:620/668 and never referenced again before the
    function's `return signals`), by a repo-wide grep (the only real call
    sites are this module's own tests, never application code), and live
    against data/series_evaluator.db (read-only query): every one of 38
    rows shows trades_observed=0 and the newest first_seen_at is ~594
    hours (~24.75 days) old - the table was seeded once and has not moved
    since. This is a separate, real data-plane-completeness bug (reported
    as its own GitHub issue, not fixed here - out of this docs-only
    investigation's scope) - and it means the WS-trade hot path currently
    performs ZERO synchronous writes at all, not one, which makes the
    "ordinary SQLite lock contention" hypothesis for issue #150's hang
    even weaker than a single-write analysis would suggest.
  - Every other _connect()-style helper touched transitively (candidate_log,
    series_evaluator) goes through services/db.py's connect(), which sets
    the same explicit PRAGMA busy_timeout=5000. signal_log.py/
    market_history.py/market_analyst_agent/_db.py's own bare
    `sqlite3.connect(db_path)` (no explicit timeout=) calls rely on
    Python's own documented sqlite3.connect() default timeout=5.0 parameter
    instead of an explicit PRAGMA - functionally the same mechanism
    (confirmed empirically below, not assumed from the docstring), since
    the sqlite3 module's `timeout` constructor argument is implemented as
    exactly the busy-handler/busy_timeout equivalent.
  - Every one of these DBs runs in WAL mode (set idempotently on every
    connect across every module read for this benchmark).

So the literal claim issue #150 raised as a Family-2 candidate - "a
busy_timeout gap" - has NO gap to find in the current source: every
connection in this path already has an effective ~5s busy-wait ceiling,
either explicit or via Python's own default. This script does not assume
that from the docstrings; it empirically verifies it (Sections 1-2), then
uses the confirmed mechanism to check whether a *different*, real
structural path to a stall longer than the 5s per-call ceiling exists
(Sections 3-4): can several independently-bounded busy-waits, or WAL's
own reader/writer semantics, still add up past the 10s outer
asyncio.wait_for deadline?

Every DB here is a disposable tmp file created by this script - never a
copy of, and never touches, any live data/*.db file.
"""
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time

OUT_DIR = os.path.join(os.path.dirname(__file__), "out")


def _fresh_db(path: str, wal: bool = True) -> None:
    conn = sqlite3.connect(path)
    if wal:
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (k TEXT PRIMARY KEY, v INTEGER)")
    conn.execute("INSERT INTO t VALUES ('row', 0)")
    conn.commit()
    conn.close()


# --- Section 1: does sqlite3.connect()'s default timeout=5.0 actually ------
# behave as a real busy-wait ceiling, with no explicit PRAGMA busy_timeout
# statement anywhere - exactly signal_log.py/market_history.py/
# market_analyst_agent/_db.py's own shape (bare `sqlite3.connect(db_path)`,
# no timeout= override)?

def section1_default_timeout_is_real(db_path: str) -> dict:
    _fresh_db(db_path)

    # Holder: opens its own connection (same bare shape as this app's
    # _connect() helpers), takes a real write lock via BEGIN IMMEDIATE, and
    # holds it for a FIXED wall-clock HOLD_SEC via time.sleep() - simulating
    # a writer that is not stuck, just legitimately mid-transaction (e.g.
    # capture_writer's own flush). Deliberately NOT synchronized off the
    # contender's own completion (an earlier draft of this benchmark made
    # that mistake - the holder waited on an Event only set AFTER the
    # contender's blocking call returned, so the holder could never release
    # before the contender's own ceiling and the "releases early" case was
    # untestable; time.sleep() on a fixed duration removes that coupling).
    HOLD_SEC = 3.0
    holder_ready = threading.Event()

    def _holder():
        conn = sqlite3.connect(db_path)  # bare - no explicit timeout=
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE t SET v = v + 1 WHERE k = 'row'")
        holder_ready.set()
        time.sleep(HOLD_SEC)
        conn.commit()
        conn.close()

    t = threading.Thread(target=_holder)
    t.start()
    holder_ready.wait(5.0)

    # Contender: bare sqlite3.connect(), NO explicit timeout= - exactly this
    # app's non-db.py connect helpers. Times how long until either the write
    # succeeds (holder released in time) or OperationalError("database is
    # locked") is raised.
    started = time.monotonic()
    raised_after = None
    succeeded_after = None
    try:
        conn2 = sqlite3.connect(db_path)  # default timeout=5.0, unstated
        conn2.execute("UPDATE t SET v = v + 1 WHERE k = 'row'")
        conn2.commit()
        succeeded_after = time.monotonic() - started
        conn2.close()
    except sqlite3.OperationalError as exc:
        raised_after = time.monotonic() - started
        error_text = str(exc)
    else:
        error_text = None

    t.join(10.0)

    return {
        "holder_held_for_sec": HOLD_SEC,
        "contender_raised_after_sec": raised_after,
        "contender_succeeded_after_sec": succeeded_after,
        "error_text": error_text,
        # This holder released BEFORE the default 5.0s ceiling (3.0s < 5.0s),
        # so a real busy_timeout equivalent means the contender should
        # SUCCEED shortly after ~3.0s, not raise. See section2 for the
        # ceiling itself (holder never releases).
        "expectation": "succeed shortly after holder releases (~3.0s), not raise",
    }


def section1b_default_timeout_ceiling(db_path: str) -> dict:
    """Same shape, but the holder NEVER releases within the busy window -
    isolates the actual ceiling Python's undocumented-at-the-callsite
    default enforces, with no explicit PRAGMA busy_timeout anywhere in
    either connection."""
    _fresh_db(db_path)
    holder_ready = threading.Event()
    stop_holder = threading.Event()

    def _holder():
        conn = sqlite3.connect(db_path)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE t SET v = v + 1 WHERE k = 'row'")
        holder_ready.set()
        stop_holder.wait(30.0)  # holds well past any busy_timeout ceiling
        conn.rollback()
        conn.close()

    t = threading.Thread(target=_holder)
    t.start()
    holder_ready.wait(5.0)

    started = time.monotonic()
    try:
        conn2 = sqlite3.connect(db_path)
        conn2.execute("UPDATE t SET v = v + 1 WHERE k = 'row'")
        conn2.commit()
        result = {"raised": False, "elapsed_sec": time.monotonic() - started}
        conn2.close()
    except sqlite3.OperationalError as exc:
        result = {"raised": True, "elapsed_sec": time.monotonic() - started, "error_text": str(exc)}

    stop_holder.set()
    t.join(10.0)
    return result


# --- Section 2: does an explicit `PRAGMA busy_timeout = 5000` (services/ ----
# db.py's exact statement) behave identically to Python's implicit default?

def section2_explicit_pragma_matches_default(db_path: str) -> dict:
    _fresh_db(db_path)
    holder_ready = threading.Event()
    stop_holder = threading.Event()

    def _holder():
        conn = sqlite3.connect(db_path)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE t SET v = v + 1 WHERE k = 'row'")
        holder_ready.set()
        stop_holder.wait(30.0)
        conn.rollback()
        conn.close()

    t = threading.Thread(target=_holder)
    t.start()
    holder_ready.wait(5.0)

    started = time.monotonic()
    try:
        # services/db.py's connect() shape exactly: bare sqlite3.connect()
        # (no timeout= kwarg) followed by an explicit PRAGMA statement.
        conn2 = sqlite3.connect(db_path)
        conn2.execute("PRAGMA busy_timeout = 5000")
        conn2.execute("UPDATE t SET v = v + 1 WHERE k = 'row'")
        conn2.commit()
        result = {"raised": False, "elapsed_sec": time.monotonic() - started}
        conn2.close()
    except sqlite3.OperationalError as exc:
        result = {"raised": True, "elapsed_sec": time.monotonic() - started, "error_text": str(exc)}

    stop_holder.set()
    t.join(10.0)
    return result


# --- Section 3: under WAL, does a plain autocommit SELECT on a SEPARATE ----
# connection block behind a concurrent writer's open-but-uncommitted
# transaction, the way it would under the old rollback-journal mode this
# app moved away from on 2026-08-11? This is the concrete claim behind
# _process_trades_sync's four read calls (recent_sides_for_ticker,
# cluster_factor, momentum, analyst_lean) being "safe" from busy_timeout
# waits in the common case.

def section3_wal_reader_vs_writer(db_path: str) -> dict:
    _fresh_db(db_path, wal=True)
    holder_ready = threading.Event()
    stop_holder = threading.Event()
    HOLD_SEC = 4.0

    def _holder():
        conn = sqlite3.connect(db_path)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE t SET v = v + 1 WHERE k = 'row'")
        holder_ready.set()
        stop_holder.wait(HOLD_SEC)
        conn.commit()
        conn.close()

    t = threading.Thread(target=_holder)
    t.start()
    holder_ready.wait(5.0)

    started = time.monotonic()
    reader = sqlite3.connect(db_path)
    row = reader.execute("SELECT v FROM t WHERE k = 'row'").fetchone()
    elapsed = time.monotonic() - started
    reader.close()

    stop_holder.set()
    t.join(10.0)
    return {
        "reader_blocked_sec": elapsed,
        "writer_held_for_sec": HOLD_SEC,
        "value_seen": row[0] if row else None,
        "expectation": "reader returns almost immediately (<<4s) - WAL readers are not blocked by an in-flight writer",
    }


# --- Section 4: aggregate-latency mechanism. Even with busy_timeout -------
# correctly bounding EVERY individual call, can a SEQUENTIAL CHAIN of
# short-lived writers (each individually well under the 5s ceiling) still
# make a contender's OWN busy_timeout - measured from ITS first attempt,
# not refreshed per new holder - expire, even though no single holder ever
# held anywhere close to 5s? Modeled on the one real write
# _process_trades_sync's loop makes per call - series_evaluator.
# record_trades_observed_bulk() via services/db.py's connect() - which
# Option B (2026-09-03) allows up to _TRADE_DISPATCH_CONCURRENCY=4
# (services/kalshi/websocket.py) concurrent trade-dispatch tasks to invoke
# against the SAME series_status.db file; SQLite WAL still allows only one
# writer at a time, so a burst of these can genuinely queue.
#
# Deterministic handoff (not a race): holder A acquires first and holds for
# hold_a seconds; the instant A releases, holder B is signalled to acquire
# immediately (a head start over the contender's own organic retry
# backoff) and holds for hold_b. The contender starts its own single
# busy_timeout-bounded attempt at t=0, competing with A from the start.
#
# Note this scenario models a hypothetical multi-writer situation for
# completeness - it is NOT known to occur on the real series_status.db
# path today, since record_trades_observed_bulk (the only real write this
# investigation found in _process_trades_sync's call graph) turned out to
# never actually be invoked at all (see this file's own module docstring)
# - so today there is no writer here to contend with in the first place.

def section4_chained_holders_vs_contender(db_path: str, hold_a: float, hold_b: float) -> dict:
    _fresh_db(db_path, wal=True)
    a_ready = threading.Event()
    a_done = threading.Event()
    b_started = threading.Event()
    errors: list[str] = []

    def _holder_a():
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE t SET v = v + 1 WHERE k = 'row'")
            a_ready.set()
            time.sleep(hold_a)
            conn.commit()
            conn.close()
        except Exception as exc:  # pragma: no cover - diagnostic only
            errors.append(f"holder_a: {exc!r}")
        finally:
            a_done.set()

    def _holder_b():
        try:
            a_done.wait(30.0)
            conn = sqlite3.connect(db_path)
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("BEGIN IMMEDIATE")  # head start the instant A releases
            conn.execute("UPDATE t SET v = v + 1 WHERE k = 'row'")
            b_started.set()
            time.sleep(hold_b)
            conn.commit()
            conn.close()
        except Exception as exc:  # pragma: no cover - diagnostic only
            errors.append(f"holder_b: {exc!r}")

    ta = threading.Thread(target=_holder_a)
    tb = threading.Thread(target=_holder_b)
    ta.start()
    a_ready.wait(5.0)
    tb.start()

    started = time.monotonic()
    contender_conn = sqlite3.connect(db_path)
    contender_conn.execute("PRAGMA busy_timeout = 5000")
    try:
        contender_conn.execute("BEGIN IMMEDIATE")
        contender_conn.execute("UPDATE t SET v = v + 1 WHERE k = 'row'")
        contender_conn.commit()
        outcome = {"raised": False}
    except sqlite3.OperationalError as exc:
        outcome = {"raised": True, "error_text": str(exc)}
    elapsed = time.monotonic() - started
    contender_conn.close()

    ta.join(10.0)
    tb.join(10.0)

    return {
        "hold_a_sec": hold_a,
        "hold_b_sec": hold_b,
        "sum_of_holds_sec": hold_a + hold_b,
        "contender_busy_timeout_sec": 5.0,
        "contender_elapsed_sec": elapsed,
        "holder_errors": errors,
        **outcome,
        "note": (
            "contender's busy_timeout is measured from ITS OWN first BEGIN "
            "IMMEDIATE attempt at t=0, not refreshed per new holder that takes "
            "the lock; if hold_a + (any time B holds before contender's ceiling) "
            "exceeds 5s, the contender can still raise even though neither "
            "individual holder came close to 5s alone."
        ),
    }


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    results = {"generated_at": time.time(), "python_version": sys.version}
    with tempfile.TemporaryDirectory(prefix="bench_family2_") as tmp:
        results["section1_default_timeout_releases_in_time"] = section1_default_timeout_is_real(
            os.path.join(tmp, "s1.db"))
        results["section1b_default_timeout_ceiling"] = section1b_default_timeout_ceiling(
            os.path.join(tmp, "s1b.db"))
        results["section2_explicit_pragma_matches_default"] = section2_explicit_pragma_matches_default(
            os.path.join(tmp, "s2.db"))
        results["section3_wal_reader_vs_writer"] = section3_wal_reader_vs_writer(
            os.path.join(tmp, "s3.db"))
        # Each hold individually well under the 5s ceiling (2s, 2s); sum
        # (4s) still under 5s - contender should succeed.
        results["section4a_two_short_holds_sum_under_ceiling"] = section4_chained_holders_vs_contender(
            os.path.join(tmp, "s4a.db"), hold_a=2.0, hold_b=2.0)
        # Each hold individually well under the 5s ceiling (3s, 3s); sum
        # (6s) exceeds 5s - tests whether the contender's ceiling is a
        # fixed budget from its first attempt (raises) or refreshed per
        # new holder (would succeed once B's hold started).
        results["section4b_two_short_holds_sum_over_ceiling"] = section4_chained_holders_vs_contender(
            os.path.join(tmp, "s4b.db"), hold_a=3.0, hold_b=3.0)
        # Single holder well past the ceiling on its own - sanity check,
        # should match section1b/section2's single-holder ceiling behavior.
        results["section4c_single_long_hold_sanity_check"] = section4_chained_holders_vs_contender(
            os.path.join(tmp, "s4c.db"), hold_a=8.0, hold_b=0.1)

    out_path = os.path.join(OUT_DIR, "family2_busy_timeout_mechanism.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(json.dumps(results, indent=2, default=str))
    print(f"\nwrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
