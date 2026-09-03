"""
Every swallowed exception, in one durable place.

Direct instruction (2026-08-17): "all errors, exceptions, faults, edge
cases, etc. should be logged for analysis later, not just put into an empty
bucket."

WHY THIS EXISTS, WITH A REAL EXAMPLE FROM THE SAME DAY

Capture paths in this app run on the websocket handler and the trading
loop, where an uncaught exception costs the stream or the tick. So they are
written defensively: `try: ... except Exception: return False`. That is the
right instinct and the wrong implementation, because it makes a broken
component indistinguishable from an idle one.

`services/game_state.py` shipped with `event_type` added to its CREATE
TABLE but no guarded ALTER, so on a process whose table already existed
every INSERT carried 16 values against 15 columns and raised. flush()
caught it, returned a drop count nobody read, and the store sat at zero
rows - which looked exactly like "no games are on right now." It was found
only by manually calling flush() and reading its return value. Hours of
data were lost to a fault that never surfaced anywhere.

That is the failure mode this module removes. The `except` blocks stay -
they must - but the exception goes somewhere first.

DESIGN

- **Deduplicated, not append-only.** A fault on the websocket path can
  repeat thousands of times a minute. Rows are keyed by (component,
  operation, exc_type, message) with a count and first/last timestamps, so
  a storm collapses into one row with count=48,201 rather than burying
  everything else. The count IS the signal.
- **Never raises, ever.** A logger that can throw inside an `except` block
  would turn a handled fault into a crash. Every path here is wrapped, and
  failure to record is silently accepted - this is the one place in the app
  where swallowing is correct, because there is nowhere left to report to.
- **Traceback kept for the first occurrence.** Enough to debug, without
  storing the same 20 lines ten thousand times.
- **Not just exceptions.** `record_fault()` takes a plain message too, for
  edge cases that aren't errors but are worth knowing about - a field that
  arrived unparseable, a projection refused for want of data, a market that
  couldn't be resolved.
"""
import contextlib
import sqlite3
import time
import traceback
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "fault_log.db"

# Cap on stored traceback text - enough to locate the failure, bounded so a
# deep recursion can't write a megabyte.
_MAX_TRACEBACK_CHARS = 4000
_MAX_MESSAGE_CHARS = 500


@contextlib.contextmanager
def _connect():
    """Every existing `with _connect() as conn:` call site (4 of them -
    services/fault_log.py:256, 292, 330, 342) keeps working unchanged -
    this yields the same conn as before, but now closes it on exit
    (2026-09-03, Task 6 of docs/superpowers/plans/
    2026-09-03-tier0-live-incident-remediation.md): `with conn:` alone
    commits/rolls back a transaction, it never closes the connection. This
    module is one of Tier 0's two confirmed-stuck live routes
    (GET /api/health/faults) and, per CLAUDE.md, the store every other
    diagnostic in this app writes to - so it is exercised constantly.

    The `try:` starts immediately after `sqlite3.connect()` succeeds, not
    after the PRAGMA/schema-init setup below (2026-09-03 follow-up fix): a
    setup failure would otherwise leave `conn` open with nothing left to
    close it."""
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        # busy_timeout FIRST, before journal_mode (issue #543 follow-up:
        # found by testing the concurrency fix, not reasoned out in
        # advance): converting a fresh database to WAL mode itself needs
        # exclusive access, so on a database no connection has ever opened
        # in WAL mode yet, multiple threads racing to connect for the first
        # time can have every statement past the first one fail with
        # "database is locked" - including this PRAGMA itself - if
        # busy_timeout isn't already in effect. A connection's busy_timeout
        # defaults to 0 (fail instantly) until this statement runs, so it
        # must be the very first thing executed on any new connection, not
        # the second. Same 5000ms default services/db.py already uses.
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS faults (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                component TEXT NOT NULL,
                operation TEXT NOT NULL,
                severity TEXT NOT NULL,
                exc_type TEXT,
                message TEXT,
                first_traceback TEXT,
                context TEXT,
                count INTEGER NOT NULL DEFAULT 1,
                first_seen REAL NOT NULL,
                last_seen REAL NOT NULL,
                UNIQUE (component, operation, exc_type, message)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_faults_last ON faults (last_seen DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_faults_component ON faults (component, last_seen DESC)")
        _ensure_null_exc_type_dedup_index(conn)
        with conn:
            yield conn
    finally:
        conn.close()


def _ensure_null_exc_type_dedup_index(conn: sqlite3.Connection) -> None:
    """Fixes issue #543: the table-level `UNIQUE (component, operation,
    exc_type, message)` above never fires when `exc_type IS NULL` - SQL NULL
    is never equal to NULL for uniqueness purposes - and `record_fault()`
    always passes `exc_type=None` (it's the non-exception path). Every
    `record_fault()` call with a fixed message therefore inserted a new row
    instead of deduping (confirmed live, as measured when this fix was
    written: 57,021 rows / 1 distinct message for `loop_watchdog`'s stall
    fault alone - a moving, ever-growing number until this fix ships, not a
    fixed constant to keep in sync). `record()` (the exception path)
    always passes a real `exc_type` and already dedupes correctly through the
    constraint above - this only covers the gap that constraint can't reach.

    A partial unique index scoped to `WHERE exc_type IS NULL` closes the gap
    without touching the existing constraint or its rows: same table, same
    key shape, just narrowed to the one case NULL breaks. Purely additive -
    no ALTER, no table rebuild.

    Guarded, not unconditional: a database that already has pre-fix duplicate
    NULL-exc_type rows (any production `fault_log.db` older than this fix)
    would make `CREATE UNIQUE INDEX` itself raise `IntegrityError` on first
    creation - `IF NOT EXISTS` only skips *re*-creation once the index
    exists, it does not skip a constraint violation on the first attempt
    (verified directly, not assumed). So: try the cheap fast path first: if
    it succeeds, either the index already exists (near-every call, negligible
    cost) or the database was already clean. Only on IntegrityError -
    meaning duplicates are actually present - does this run the one-time
    merge below and retry.

    Both statements below carry `IF NOT EXISTS` - not just the first
    (independent adversarial review of this fix, must-fix #1, 2026-09-03):
    `fault_log.record_fault()` genuinely runs from concurrent OS threads,
    not just interleaved coroutines - `loop_watchdog.py`'s stall capture
    dispatches it via `asyncio.to_thread`, and `GET /api/health/faults`
    (diagnostics/routes.py) does the same for `summary()`/`recent()`. Two
    threads can both observe the pre-fix duplicates and both raise
    IntegrityError on their first attempt before either commits a fix; both
    then run the (idempotent - a second pass over an already-merged table
    finds nothing `HAVING COUNT(*) > 1`) merge, and without IF NOT EXISTS
    here, the thread that loses the race hits `CREATE UNIQUE INDEX` on an
    index the winner already created, raising `OperationalError` (not
    `IntegrityError` - not caught by the `except` above, escaping to the
    caller's broad `except Exception` and silently dropping that one
    `record_fault()`/`summary()`/`recent()` call).

    `BEGIN IMMEDIATE` around the merge+retry (found the hard way: fixing
    must-fix #1 above alone made the *first* race's symptom - a silently
    dropped call - go away, but a second, subtler race was still there,
    caught by running this fix's own new concurrency test rather than
    assuming green meant done): `_merge_duplicate_null_exc_type_rows`'s
    first statement, `CREATE TEMP TABLE ... AS SELECT`, is DDL - confirmed
    directly that Python's sqlite3 module does not open a transaction for
    DDL (`conn.in_transaction` stays `False` across it, unlike DML) - so
    its `SUM(count)`/`MAX(last_seen)` read was not atomic with the UPDATE/
    DELETE that use it. Two connections could both read the same pre-merge
    duplicate rows, both compute the same aggregate, and the second one to
    write would overwrite rather than add to the first one's numbers -
    undercounting silently, no exception at all (reproduced directly: 8
    concurrent threads against 20 pre-existing rows landed on 21 or 23
    instead of 28 in roughly 1 of 8 real trials before this fix). `BEGIN
    IMMEDIATE` takes the write lock before the read, so the read-then-write
    below runs as one atomic unit against every other writer - the
    `busy_timeout` set in `_connect()` above is what this waits on instead
    of failing instantly when another connection already holds that lock.
    Verified empirically (dozens of trials, exact count every time) after
    this fix - see `test_concurrent_first_writes_after_upgrade_do_not_
    lose_a_call` in tests/test_fault_log.py, which catches both races."""
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_faults_dedup_null_exc_type "
            "ON faults (component, operation, message) WHERE exc_type IS NULL"
        )
    except sqlite3.IntegrityError:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _merge_duplicate_null_exc_type_rows(conn)
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_faults_dedup_null_exc_type "
                "ON faults (component, operation, message) WHERE exc_type IS NULL"
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def _merge_duplicate_null_exc_type_rows(conn: sqlite3.Connection) -> None:
    """One-time cleanup for rows `record_fault()` wrote before the dedup fix
    above - never deletes information, only consolidates it, per CLAUDE.md's
    "accumulated history" rule for data/*.db files: for each (component,
    operation, message) group of exc_type-NULL rows, the earliest-inserted
    row (lowest id, matching how `_write`'s own ON CONFLICT already treats
    every field it doesn't explicitly recompute - first insert wins) is kept
    and updated with count=SUM(count) and last_seen=MAX(last_seen) across the
    whole group; the rest are dropped. first_traceback, context, and severity
    are left exactly as the kept row already had them - unchanged, not
    reselected - matching `_write`'s own ON CONFLICT, which never updates
    those fields on a repeat either. Real exception rows (exc_type NOT NULL,
    already deduped correctly by the table constraint) and already-unique
    exc_type-NULL rows (HAVING COUNT(*) > 1 excludes them) are untouched."""
    conn.execute(
        """
        CREATE TEMP TABLE _fault_dedupe_merge AS
        SELECT component, operation, message,
               MIN(id) AS keeper_id,
               SUM(count) AS total_count,
               MAX(last_seen) AS max_last_seen
        FROM faults
        WHERE exc_type IS NULL
        GROUP BY component, operation, message
        HAVING COUNT(*) > 1
        """
    )
    conn.execute(
        """
        UPDATE faults
        SET count = (SELECT total_count FROM _fault_dedupe_merge m WHERE m.keeper_id = faults.id),
            last_seen = (SELECT max_last_seen FROM _fault_dedupe_merge m WHERE m.keeper_id = faults.id)
        WHERE id IN (SELECT keeper_id FROM _fault_dedupe_merge)
        """
    )
    # `IS`, not `=`, for message (should-fix #2, adversarial review): the
    # `message` column has no NOT NULL constraint, and SQL `=` against a
    # NULL is never true - a NULL-message duplicate group would inflate the
    # keeper's count in the UPDATE above but then never get its extra rows
    # deleted here, double-counting. Currently unreachable in practice
    # (record()/record_fault() both pass message through str(), so a
    # caller's None becomes the literal string "None", never a real SQL
    # NULL - confirmed live: 0 rows have message IS NULL today) but the
    # column itself doesn't guarantee that, and `IS` costs nothing here.
    conn.execute(
        """
        DELETE FROM faults
        WHERE exc_type IS NULL
          AND id NOT IN (SELECT keeper_id FROM _fault_dedupe_merge)
          AND EXISTS (
              SELECT 1 FROM _fault_dedupe_merge m
              WHERE m.component = faults.component AND m.operation = faults.operation
                AND m.message IS faults.message
          )
        """
    )
    conn.execute("DROP TABLE _fault_dedupe_merge")


def record(component: str, operation: str, exc: BaseException,
           context: str | None = None, severity: str = "error",
           now: float | None = None) -> bool:
    """Log one swallowed exception. Returns whether it was stored.

    Call this from inside an `except` block, immediately before whatever
    the handler was already going to do:

        except Exception as exc:
            fault_log.record("series_watcher", "record_trade", exc)
            return False
    """
    try:
        return _write(
            component, operation, severity, type(exc).__name__,
            str(exc)[:_MAX_MESSAGE_CHARS],
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-_MAX_TRACEBACK_CHARS:],
            context, now,
        )
    except Exception:
        # Nowhere left to report to. See the module docstring: this is the
        # one place where silence is the correct behaviour, because raising
        # here would convert a handled fault into an unhandled one.
        return False


def record_fault(component: str, operation: str, message: str,
                 context: str | None = None, severity: str = "warn",
                 now: float | None = None, tb: str | None = None) -> bool:
    """Log something worth knowing that isn't an exception - a field that
    arrived unparseable, a projection refused for want of data, a market
    that couldn't be resolved. Same deduplication, same never-raises
    contract.

    tb (2026-09-03, Task 1 of docs/superpowers/plans/2026-09-03-tier1-
    backend-hygiene.md): stores a pre-formatted traceback/stack string into
    the same first_traceback slot record() populates from a real
    exception - added for services/loop_watchdog.py's stall-attribution
    capture, a non-exception event (a captured stack, not a raised one)
    that still needs a first_traceback for attribution. Every existing
    call site omits it and behaves exactly as before (None -> unchanged
    null column, since _write's ON CONFLICT never updates first_traceback
    on a repeat anyway - see this module's own module docstring)."""
    try:
        return _write(component, operation, severity, None,
                      str(message)[:_MAX_MESSAGE_CHARS],
                      tb[:_MAX_TRACEBACK_CHARS] if tb else None,
                      context, now)
    except Exception:
        return False


def _write(component: str, operation: str, severity: str, exc_type: str | None,
           message: str | None, tb: str | None, context: str | None,
           now: float | None) -> bool:
    now = now if now is not None else time.time()
    with _connect() as conn:
        # ON CONFLICT keeps the FIRST traceback (the one with the original
        # stack) and bumps the count - a repeat adds evidence of frequency,
        # not another copy of the same stack.
        #
        # Two ON CONFLICT targets, chained (issue #543): the table's own
        # UNIQUE constraint never fires when exc_type IS NULL (record_fault()
        # always passes None there - see _ensure_null_exc_type_dedup_index's
        # docstring above), so a second target names the partial index that
        # covers exactly that case. SQLite requires each target to name a
        # real constraint/index verbatim, including a partial index's own
        # WHERE clause - only one target ever actually matches a given row,
        # the other is simply not triggered.
        conn.execute(
            """
            INSERT INTO faults (component, operation, severity, exc_type, message,
                                first_traceback, context, count, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT (component, operation, exc_type, message) DO UPDATE SET
                count = count + 1,
                last_seen = excluded.last_seen
            ON CONFLICT (component, operation, message) WHERE exc_type IS NULL DO UPDATE SET
                count = count + 1,
                last_seen = excluded.last_seen
            """,
            (component, operation, severity, exc_type, message, tb, context, now, now),
        )
    return True


def recent(limit: int = 50, component: str | None = None,
           since_ts: float | None = None) -> list[dict]:
    """Most-recently-seen faults first. This is the read a session should
    start with: anything with a large count or a recent last_seen is
    actively happening right now."""
    try:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            where: list[str] = []
            params: list[object] = []
            if component:
                where.append("component = ?")
                params.append(component)
            if since_ts is not None:
                where.append("last_seen >= ?")
                params.append(since_ts)
            clause = ("WHERE " + " AND ".join(where)) if where else ""
            params.append(limit)
            return [dict(r) for r in conn.execute(
                f"SELECT * FROM faults {clause} ORDER BY last_seen DESC LIMIT ?", params)]
    except Exception:
        # Broad on purpose: the readers must not propagate either. If the
        # fault store itself is unreachable, a caller asking "what has been
        # failing" should get an empty answer, not a second exception.
        return []


def prune(retention_hours: float, now: float | None = None) -> int:
    """Drop rows whose last_seen (not first_seen) is older than the
    retention window - a still-recurring fault must survive even if it was
    first seen long ago, since last_seen is what marks it active. Same
    shape as every other capture/observability store's prune() (services/
    observability/observability.py, services/series_watcher.py, etc.),
    wired into main.py's hourly _maybe_prune_capture_stores sweep - the one
    store in that rotation without it until now.

    Wrapped like every other function in this module (module docstring:
    "Never raises, ever") - an unwrapped DELETE here would let a broken
    fault_log.db crash the hourly sweep inside the trading tick loop,
    which is exactly the failure mode this file exists to prevent
    elsewhere."""
    now = now if now is not None else time.time()
    cutoff = now - retention_hours * 3600
    try:
        with _connect() as conn:
            cur = conn.execute("DELETE FROM faults WHERE last_seen < ?", (cutoff,))
            return cur.rowcount
    except Exception as exc:
        record("fault_log", "prune", exc)
        return 0


def summary(since_ts: float | None = None) -> dict:
    """Counts by component and severity - the shape a health endpoint wants,
    so an active fault shows up as a number rather than as silence."""
    try:
        with _connect() as conn:
            clause, params = ("WHERE last_seen >= ?", [since_ts]) if since_ts is not None else ("", [])
            distinct, total = conn.execute(
                f"SELECT COUNT(*), COALESCE(SUM(count), 0) FROM faults {clause}", params).fetchone()
            by_component = dict(conn.execute(
                f"SELECT component, SUM(count) FROM faults {clause} GROUP BY component "
                "ORDER BY 2 DESC", params).fetchall())
            by_severity = dict(conn.execute(
                f"SELECT severity, SUM(count) FROM faults {clause} GROUP BY severity", params).fetchall())
            worst = conn.execute(
                f"SELECT component, operation, exc_type, message, count, last_seen "
                f"FROM faults {clause} ORDER BY count DESC LIMIT 5", params).fetchall()
    except Exception as exc:
        return {"error": str(exc)}
    return {
        "distinct_faults": distinct,
        "total_occurrences": total,
        "by_component": by_component,
        "by_severity": by_severity,
        "most_frequent": [
            {"component": c, "operation": o, "exc_type": t, "message": m,
             "count": n, "last_seen": ls}
            for c, o, t, m, n, ls in worst
        ],
    }
