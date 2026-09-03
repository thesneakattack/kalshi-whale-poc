"""services/fault_log.py — the store that exists because a bare `except`
already cost this app hours of data.

game_state shipped with `event_type` added to its CREATE TABLE but no
guarded ALTER, so every INSERT raised on a pre-existing table. flush()
caught it, returned a drop count nobody read, and the store sat at zero rows
looking exactly like "no games are on right now."
"""
import sqlite3
import time

import pytest

from services import fault_log as fl


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(fl, "DB_PATH", tmp_path / "fault_log.db")
    yield


def _boom(msg="table game_states has 15 columns but 16 values were supplied"):
    try:
        raise sqlite3.OperationalError(msg)
    except sqlite3.OperationalError as exc:
        return exc


def test_records_an_exception_with_its_traceback():
    assert fl.record("game_state", "flush", _boom(), context="1 row dropped") is True
    rows = fl.recent()
    assert len(rows) == 1
    r = rows[0]
    assert r["component"] == "game_state" and r["operation"] == "flush"
    assert r["exc_type"] == "OperationalError"
    assert "16 values" in r["message"]
    assert "OperationalError" in r["first_traceback"]
    assert r["context"] == "1 row dropped"
    assert r["count"] == 1


def test_repeats_increment_a_count_instead_of_flooding_the_table():
    """A fault on the websocket path can repeat thousands of times a
    minute. One row with count=N is the signal; N rows is noise that buries
    everything else."""
    for _ in range(500):
        fl.record("series_watcher", "record_trade", _boom("disk I/O error"))
    rows = fl.recent()
    assert len(rows) == 1
    assert rows[0]["count"] == 500
    assert rows[0]["first_seen"] <= rows[0]["last_seen"]


def test_the_first_traceback_is_kept_not_the_latest():
    """The original stack is the one worth having; later repeats add
    frequency, not information."""
    fl.record("m", "op", _boom("first"), now=100.0)
    first_tb = fl.recent()[0]["first_traceback"]
    fl.record("m", "op", _boom("first"), now=200.0)
    row = fl.recent()[0]
    assert row["first_traceback"] == first_tb
    assert row["first_seen"] == 100.0 and row["last_seen"] == 200.0


def test_distinct_faults_stay_distinct():
    fl.record("a", "op1", _boom("x"))
    fl.record("a", "op2", _boom("x"))
    fl.record("b", "op1", _boom("x"))
    fl.record("a", "op1", _boom("y"))
    assert len(fl.recent()) == 4


def test_non_exception_edge_cases_are_recordable():
    """Not everything worth knowing is an exception - an unparseable field
    or a refused projection is an edge case, not an error."""
    assert fl.record_fault("kalshi_trade_tape", "parse_price",
                           "yes_price_dollars absent", severity="warn") is True
    r = fl.recent()[0]
    assert r["severity"] == "warn" and r["exc_type"] is None
    assert "absent" in r["message"]


def test_summary_surfaces_the_loudest_faults():
    for _ in range(10):
        fl.record("index_feed", "flush", _boom("locked"))
    fl.record("game_state", "record", _boom("bad column"))
    fl.record_fault("provider", "resolve", "market not returned")

    s = fl.summary()
    assert s["distinct_faults"] == 3
    assert s["total_occurrences"] == 12
    assert s["by_component"]["index_feed"] == 10
    assert s["by_severity"]["error"] == 11 and s["by_severity"]["warn"] == 1
    assert s["most_frequent"][0]["component"] == "index_feed"


def test_recent_filters_by_component_and_time():
    fl.record("a", "op", _boom("x"), now=100.0)
    fl.record("b", "op", _boom("y"), now=200.0)
    assert len(fl.recent(component="a")) == 1
    assert len(fl.recent(since_ts=150.0)) == 1


def test_logging_never_raises_even_on_a_broken_store(monkeypatch, tmp_path):
    """A logger that throws inside an `except` block turns a handled fault
    into an unhandled one. This is the one place where swallowing is
    correct - there is nowhere left to report to."""
    def _explode():
        raise RuntimeError("no db")

    monkeypatch.setattr(fl, "_connect", _explode)
    assert fl.record("m", "op", _boom()) is False
    assert fl.record_fault("m", "op", "msg") is False
    assert fl.recent() == []
    assert fl.prune(retention_hours=336) == 0


def test_oversized_message_and_traceback_are_bounded():
    huge = "x" * 50_000
    fl.record("m", "op", _boom(huge))
    r = fl.recent()[0]
    assert len(r["message"]) <= fl._MAX_MESSAGE_CHARS
    assert len(r["first_traceback"]) <= fl._MAX_TRACEBACK_CHARS


# --- prune -------------------------------------------------------------

def test_prune_removes_faults_older_than_retention_and_keeps_the_rest():
    now = time.time()
    fl.record("a", "op", _boom("stale"), now=now - 400 * 3600)  # older than 336h
    fl.record("b", "op", _boom("fresh"), now=now - 1 * 3600)  # recent

    fl.prune(retention_hours=336, now=now)

    remaining = fl.recent()
    assert [r["component"] for r in remaining] == ["b"]


def test_prune_keys_off_last_seen_not_first_seen():
    """A fault first seen long ago but still recurring must survive prune -
    last_seen is the signal that it's still active, not first_seen."""
    now = time.time()
    fl.record("a", "op", _boom("x"), now=now - 400 * 3600)  # first_seen: stale
    fl.record("a", "op", _boom("x"), now=now - 1 * 3600)  # last_seen: recent (same fault, repeats)

    fl.prune(retention_hours=336, now=now)

    assert len(fl.recent()) == 1


def test_prune_returns_the_deleted_row_count():
    now = time.time()
    fl.record("a", "op", _boom("stale"), now=now - 400 * 3600)
    fl.record("b", "op", _boom("also stale"), now=now - 400 * 3600)
    fl.record("c", "op", _boom("fresh"), now=now - 1 * 3600)

    assert fl.prune(retention_hours=336, now=now) == 2


def test_connect_closes_its_connection(monkeypatch):
    """Same fd-leak class as Tasks 2-5 - fault_log.py's own _connect() had
    the identical non-closing shape, and this module is the one CLAUDE.md
    tells every session to read first (services/fault_log.py's own callers
    include GET /api/health/faults, one of only two routes this plan's own
    live re-verification found stuck). DB_PATH is already redirected by
    this file's autouse _isolated fixture - no need to set it here.

    Deviates from the plan's literal snippet (`conn.close = _close`
    monkeypatched directly onto the connection instance): verified live in
    this container (Python 3.13.15, sqlite3 module 2.6.0) that
    `sqlite3.Connection` instances have no `__dict__`
    (`'sqlite3.Connection' object has no attribute 'foo' and no __dict__
    for setting new attributes`), so instance-attribute assignment of
    `close` raises `AttributeError: attribute 'close' is read-only` before
    the test body even runs - not the intended `closed == []` assertion
    failure. Patching `sqlite3.Connection.close` at the class level also
    fails (`TypeError: cannot set 'close' attribute of immutable type
    'sqlite3.Connection'` - it's a non-heap C type). A thin wrapper
    returned in place of the real connection is the only working
    substitute that still exercises the exact call sequence `_connect()`
    performs (`with conn: yield conn` then `conn.close()` in `finally`)."""
    import sqlite3

    closed = []
    real_connect = sqlite3.connect

    class _TrackingConn:
        def __init__(self, real):
            self.__dict__["_real"] = real

        def __getattr__(self, name):
            return getattr(self.__dict__["_real"], name)

        def __enter__(self):
            self.__dict__["_real"].__enter__()
            return self

        def __exit__(self, *exc):
            return self.__dict__["_real"].__exit__(*exc)

        def close(self):
            closed.append(True)
            self.__dict__["_real"].close()

    def _tracking_connect(*args, **kwargs):
        return _TrackingConn(real_connect(*args, **kwargs))

    monkeypatch.setattr(fl.sqlite3, "connect", _tracking_connect)

    with fl._connect() as conn:
        conn.execute("SELECT 1")

    assert closed == [True]


def test_connect_closes_on_setup_failure(monkeypatch):
    """Same setup-failure leak class as market_history.py's analogous test:
    fault_log.py's _connect() has no separate _init_schema() to monkeypatch
    (its CREATE TABLE/INDEX statements are inline conn.execute() calls), so
    this test makes the very first execute() (the PRAGMA) raise instead -
    the try/finally must still close the connection even when the first
    setup statement fails, not just when yield's body raises."""
    import sqlite3

    closed = []
    real_connect = sqlite3.connect

    class _TrackingConn:
        def __init__(self, real):
            self.__dict__["_real"] = real

        def __getattr__(self, name):
            return getattr(self.__dict__["_real"], name)

        def __enter__(self):
            self.__dict__["_real"].__enter__()
            return self

        def __exit__(self, *exc):
            return self.__dict__["_real"].__exit__(*exc)

        def close(self):
            closed.append(True)
            self.__dict__["_real"].close()

        def execute(self, *args, **kwargs):
            raise RuntimeError("PRAGMA failed")

    def _tracking_connect(*args, **kwargs):
        return _TrackingConn(real_connect(*args, **kwargs))

    monkeypatch.setattr(fl.sqlite3, "connect", _tracking_connect)

    raised = None
    try:
        with fl._connect():
            pass
    except RuntimeError as exc:
        raised = exc

    assert raised is not None and "PRAGMA failed" in str(raised)
    assert closed == [True], "connection must be closed even when setup (the first execute()) raises before the try block"


def test_record_fault_stores_an_explicit_traceback():
    """record_fault's tb param (added by Task 1 of docs/superpowers/plans/
    2026-09-03-tier1-backend-hygiene.md) stores a pre-formatted stack/
    traceback string into the same first_traceback slot record() populates
    from a real exception - for a captured stack (loop_watchdog's stall
    attribution), not a raised one."""
    fl.record_fault("test_component", "test_op", "something worth knowing",
                     tb="Traceback (most recent call last):\n  fake stack\n")
    row = fl.recent(component="test_component", limit=1)[0]
    assert row["first_traceback"] == "Traceback (most recent call last):\n  fake stack\n"


# --- issue #543: NULL exc_type defeats the UNIQUE constraint -----------

def test_record_fault_with_the_same_message_dedupes_into_one_row():
    """The bug: record_fault() always passes exc_type=None, and SQL NULL is
    never equal to NULL for UNIQUE purposes, so the table-level constraint
    never fired for this path - every call inserted a new row instead of
    incrementing count. Confirmed live: loop_watchdog's stall fault alone
    had 55,635 rows for 1 distinct message (issue #543)."""
    for _ in range(500):
        fl.record_fault("loop_watchdog", "stall", "event loop stalled")
    rows = fl.recent()
    assert len(rows) == 1
    assert rows[0]["count"] == 500
    assert rows[0]["exc_type"] is None


def test_record_fault_different_messages_stay_distinct():
    fl.record_fault("a", "op", "message one")
    fl.record_fault("a", "op", "message two")
    fl.record_fault("a", "op2", "message one")
    fl.record_fault("b", "op", "message one")
    assert len(fl.recent()) == 4


def test_record_fault_dedup_coexists_with_record_dedup_on_the_same_key():
    """Two independent dedup paths (the table's own UNIQUE constraint for
    record()'s real exc_type, the new partial index for record_fault()'s
    NULL exc_type) must not cross-contaminate rows that share component,
    operation, and message but differ only in whether it's an exception."""
    fl.record("shared", "op", _boom("same text"))
    fl.record_fault("shared", "op", "same text")
    fl.record("shared", "op", _boom("same text"))
    fl.record_fault("shared", "op", "same text")
    rows = fl.recent()
    assert len(rows) == 2
    by_type = {r["exc_type"]: r["count"] for r in rows}
    assert by_type["OperationalError"] == 2
    assert by_type[None] == 2


def test_pre_existing_duplicate_null_exc_type_rows_are_merged_on_first_connect(monkeypatch, tmp_path):
    """Simulates a real pre-fix production fault_log.db: many raw duplicate
    NULL-exc_type rows already on disk, written before this fix existed.
    CREATE UNIQUE INDEX itself raises IntegrityError against pre-existing
    duplicates (verified directly, not assumed) - IF NOT EXISTS only skips
    *re*-creation, not a first-creation constraint violation - so the module
    must detect that, merge the duplicates, and retry, on its very first
    _connect() after upgrading, not lose the fault store to a swallowed
    exception."""
    db_path = tmp_path / "legacy_fault_log.db"
    monkeypatch.setattr(fl, "DB_PATH", db_path)

    seed = sqlite3.connect(db_path)
    seed.execute(
        """
        CREATE TABLE faults (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            component TEXT NOT NULL, operation TEXT NOT NULL, severity TEXT NOT NULL,
            exc_type TEXT, message TEXT, first_traceback TEXT, context TEXT,
            count INTEGER NOT NULL DEFAULT 1, first_seen REAL NOT NULL, last_seen REAL NOT NULL,
            UNIQUE (component, operation, exc_type, message)
        )
        """
    )
    now = time.time()
    for i in range(50):
        seed.execute(
            "INSERT INTO faults (component, operation, severity, exc_type, message, "
            "first_traceback, count, first_seen, last_seen) VALUES (?,?,?,?,?,?,1,?,?)",
            ("loop_watchdog", "stall", "warn", None, "stalled",
             "original-stack" if i == 0 else None, now - (50 - i), now - (50 - i)),
        )
    # a real exception row and a non-duplicated record_fault row, to confirm
    # the merge only touches genuine NULL-exc_type duplicate groups
    seed.execute(
        "INSERT INTO faults (component, operation, severity, exc_type, message, "
        "count, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?)",
        ("other", "op", "error", "ValueError", "boom", 7, now - 5, now - 1),
    )
    seed.execute(
        "INSERT INTO faults (component, operation, severity, exc_type, message, "
        "count, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?)",
        ("solo", "op", "warn", None, "only once", 1, now, now),
    )
    seed.commit()
    seed.close()

    # This call goes through the real module code path - it must trigger the
    # guarded migration internally, not raise, and not silently swallow the
    # store into "no rows" the way an unguarded IntegrityError would (every
    # _connect() caller wraps exceptions broadly - see the module docstring).
    assert fl.record_fault("loop_watchdog", "stall", "stalled") is True

    rows = {r["component"]: r for r in fl.recent(limit=10)}
    assert len(rows) == 3
    assert rows["loop_watchdog"]["count"] == 51, "50 pre-existing + 1 new write must merge into one row"
    assert rows["loop_watchdog"]["exc_type"] is None
    assert rows["loop_watchdog"]["first_traceback"] == "original-stack", \
        "the earliest row's traceback must survive the merge, not be discarded"
    assert rows["other"]["count"] == 7 and rows["other"]["exc_type"] == "ValueError", \
        "a real exception row must be untouched by the NULL-exc_type merge"
    assert rows["solo"]["count"] == 1, "a non-duplicated record_fault row must be untouched"

    # A second call must take the fast IF NOT EXISTS path (index already
    # exists) and still dedupe correctly - the migration must not re-run.
    assert fl.record_fault("loop_watchdog", "stall", "stalled") is True
    assert fl.recent(component="loop_watchdog")[0]["count"] == 52


def test_record_fault_tb_defaults_to_none_for_every_existing_caller():
    """Every one of this function's other 10+ call sites omits tb - this
    pins that omitting it still behaves exactly as before (first_traceback
    stays NULL), so this additive param cannot be a silent behavior change
    for anything that doesn't pass it."""
    fl.record_fault("test_component2", "test_op2", "no traceback here")
    row = fl.recent(component="test_component2", limit=1)[0]
    assert row["first_traceback"] is None
