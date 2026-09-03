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
    services/fault_log.py:129, 153, 191, 203) keeps working unchanged -
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
        with conn:
            yield conn
    finally:
        conn.close()


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
                 now: float | None = None) -> bool:
    """Log something worth knowing that isn't an exception - a field that
    arrived unparseable, a projection refused for want of data, a market
    that couldn't be resolved. Same deduplication, same never-raises
    contract."""
    try:
        return _write(component, operation, severity, None,
                      str(message)[:_MAX_MESSAGE_CHARS], None, context, now)
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
        conn.execute(
            """
            INSERT INTO faults (component, operation, severity, exc_type, message,
                                first_traceback, context, count, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT (component, operation, exc_type, message) DO UPDATE SET
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
