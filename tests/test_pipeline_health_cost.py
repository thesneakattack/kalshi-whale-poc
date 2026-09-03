"""
Cost guards for GET /api/health/pipeline (issue #210).

The endpoint that CLAUDE.md names as investigation entry #2 degraded from
instant to 41.9s to a 504 over one day. Two independent defects, both
measured on the live 22.3GB data/series_watcher.db on 2026-08-30:

  * `SELECT COUNT(*) FROM raw_trades` took 70.5s (30,787,297 rows) and
    `SELECT MAX(observed_at) FROM raw_trades` took 7.5s - SQLite has no
    O(1) COUNT(*), and no index leads with observed_at.
  * Both ran on blocking sqlite3 inside an `async def` with no thread hop,
    so those were seconds the whole event loop was stopped. The diagnostic
    was degrading the app it measures (`last_tick_duration_sec` 81.3s).

These tests are the permanent recurrence detection for both: the SQL the
probe issues is asserted directly, and the route is asserted to do its DB
work off the event loop.
"""
import asyncio
import sqlite3
import time

import pytest

from services.diagnostics import store_stats


def _seed(path, *, rows: int, col: str = "observed_at", deleted: int = 0) -> float:
    """A rowid table shaped like the real capture stores: append-only
    inserts, an ascending timestamp column, and no index leading with it."""
    conn = sqlite3.connect(path)
    conn.execute(f"CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, k TEXT, {col} REAL NOT NULL)")
    conn.execute(f"CREATE INDEX idx_events_k ON events (k, {col})")
    base = time.time() - rows
    conn.executemany(
        f"INSERT INTO events (k, {col}) VALUES (?, ?)",
        [("k", base + i) for i in range(rows)],
    )
    conn.commit()
    if deleted:
        conn.execute("DELETE FROM events WHERE id <= ?", (deleted,))
        conn.commit()
    conn.close()
    return base + rows - 1


class _RecordingConnection:
    """Wraps a real connection so a test can assert on the SQL issued and
    on whether the connection was closed."""

    def __init__(self, inner, log, closed):
        self._inner, self._log, self._closed = inner, log, closed

    def execute(self, sql, *args):
        self._log.append(" ".join(sql.split()))
        return self._inner.execute(sql, *args)

    def close(self):
        self._closed.append(True)
        self._inner.close()

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _record(monkeypatch):
    """Install after seeding: store_stats reaches sqlite3.connect through
    the shared module object, so this seam catches every caller."""
    log, closed = [], []
    real_connect = sqlite3.connect

    def _connect(database, *args, **kwargs):
        return _RecordingConnection(real_connect(database, *args, **kwargs), log, closed)

    monkeypatch.setattr(store_stats.sqlite3, "connect", _connect)
    return log, closed


def test_large_store_never_issues_a_full_count_or_bare_max(tmp_path, monkeypatch):
    """The 70.5s + 7.5s pair. Above the row limit neither may be issued -
    this is the assertion that fails if the O(n) form is ever restored."""
    db = tmp_path / "big.db"
    _seed(db, rows=200)
    log, _ = _record(monkeypatch)

    store_stats.store_stats(db, "events", "observed_at", time.time(), row_limit=50)

    joined = " | ".join(log)
    assert "SELECT COUNT(*) FROM events" not in joined
    assert "SELECT MAX(observed_at) FROM events" not in joined
    assert "SELECT MAX(rowid) FROM events" in joined


def test_large_store_labels_its_row_count_as_approximate(tmp_path):
    """An approximate number presented as exact is the failure this repo
    already shipped twice. MAX(rowid) is an upper bound - deleted rows keep
    their rowid spent - so the payload has to say so in the same object."""
    db = tmp_path / "big.db"
    _seed(db, rows=200, deleted=40)

    out = store_stats.store_stats(db, "events", "observed_at", time.time(), row_limit=50)

    assert out["rows"] == 200          # MAX(rowid), the upper bound
    assert out["rows_exact"] is False
    assert out["rows_method"] == "max_rowid"
    assert "approximate" in out["rows_note"].lower()
    assert out["last_write_exact"] is False
    assert out["last_write_method"] == "recent_rowid_window"


def test_small_store_still_reports_an_exact_count(tmp_path, monkeypatch):
    """Cheaper to compute, not less informative: exactness is kept
    everywhere it is affordable, and labelled as exact when it is."""
    db = tmp_path / "small.db"
    _seed(db, rows=200, deleted=40)
    log, _ = _record(monkeypatch)

    out = store_stats.store_stats(db, "events", "observed_at", time.time(), row_limit=10_000)

    assert out["rows"] == 160          # the real COUNT(*), not the rowid bound
    assert out["rows_exact"] is True
    assert out["rows_method"] == "count"
    assert out["last_write_exact"] is True
    assert out["last_write_method"] == "max"
    assert "rows_note" not in out
    assert "SELECT COUNT(*) FROM events" in " | ".join(log)


def test_exact_override_forces_the_exact_count_on_a_large_store(tmp_path):
    """The information is not removed, only moved off the default path:
    ?exact_rows=true still answers the expensive question."""
    db = tmp_path / "big.db"
    _seed(db, rows=200, deleted=40)

    out = store_stats.store_stats(db, "events", "observed_at", time.time(), row_limit=50, exact=True)

    assert out["rows"] == 160
    assert out["rows_exact"] is True


def test_last_write_age_is_correct_on_the_approximate_path(tmp_path):
    """The approximation is about *cost*, not about the answer: the newest
    row is inside the recent-rowid window, so the age is the real one."""
    db = tmp_path / "big.db"
    newest = _seed(db, rows=200)
    now = newest + 30.0

    out = store_stats.store_stats(db, "events", "observed_at", now, row_limit=50, recent_window=10)

    assert out["last_write_sec_ago"] == pytest.approx(30.0, abs=0.2)


def test_probe_closes_its_connection(tmp_path, monkeypatch):
    """`with sqlite3.connect(...)` commits a transaction, it does not close
    the connection - the old handler leaked one file handle per store per
    request, on the endpoint a soak run polls."""
    db = tmp_path / "small.db"
    _seed(db, rows=10)
    _, closed = _record(monkeypatch)

    store_stats.store_stats(db, "events", "observed_at", time.time())

    assert closed == [True]


def test_probe_opens_read_only_and_does_not_create_a_missing_store(tmp_path):
    """A diagnostic must never write to the data plane it reports on. The
    old read-write connect created an empty DB file for a store that did
    not exist yet."""
    missing = tmp_path / "absent.db"

    out = store_stats.store_stats(missing, "events", "observed_at", time.time())

    assert "error" in out
    assert not missing.exists()


def test_probe_reports_its_own_cost(tmp_path):
    """CLAUDE.md: any diagnostic on the hot path is measured for runtime
    cost. Self-reported per store, so a regression is visible in the
    payload itself rather than only in a stopwatch."""
    db = tmp_path / "small.db"
    _seed(db, rows=10)

    out = store_stats.store_stats(db, "events", "observed_at", time.time())

    assert isinstance(out["probe_ms"], float)
    assert out["probe_ms"] >= 0.0


def test_empty_store_reports_zero_rows_and_no_last_write(tmp_path):
    db = tmp_path / "empty.db"
    _seed(db, rows=0)

    out = store_stats.store_stats(db, "events", "observed_at", time.time())

    assert out["rows"] == 0
    assert out["last_write_sec_ago"] is None
    assert out["rows_exact"] is True


# ------------------------------------------------------- route-level guards

def test_pipeline_health_runs_store_probes_off_the_event_loop(monkeypatch):
    """The second half of #210: blocking sqlite3 inside an `async def` with
    no thread hop stalled the whole app. asyncio.get_running_loop() only
    succeeds on a thread that is running the loop, so a probe that can call
    it is a probe still sitting on the hot path."""
    import main
    from fastapi.testclient import TestClient
    from services.diagnostics import routes

    on_loop = []

    def _probe(db_path, table, col, now, **kwargs):
        try:
            asyncio.get_running_loop()
            on_loop.append(table)
        except RuntimeError:
            pass
        return {"rows": 0, "rows_exact": True, "rows_method": "count",
                "last_write_sec_ago": None, "last_write_exact": True,
                "last_write_method": "max", "probe_ms": 0.0}

    monkeypatch.setattr(routes.store_stats, "store_stats", _probe)
    body = TestClient(main.app).get("/api/health/pipeline").json()

    assert on_loop == []
    assert set(body["stores"]) >= {"raw_trades", "book_snapshots", "signals", "rejections",
                                   "index_ticks", "settlement_observations", "game_states"}


def test_pipeline_health_reads_buffered_trades_without_counting_raw_trades(monkeypatch):
    """series_watcher.capture_stats() runs two COUNT(*)s over raw_trades
    (4.5s warm, measured 2026-08-30) and the route wanted one in-memory
    integer out of it. Read capture_writer's depth directly instead."""
    import main
    from fastapi.testclient import TestClient
    from services import capture_writer, series_watcher
    from services.diagnostics import routes

    def _forbidden(*a, **kw):
        raise AssertionError("capture_stats counts 30M rows; read capture_writer.depth() instead")

    monkeypatch.setattr(series_watcher, "capture_stats", _forbidden)
    monkeypatch.setattr(routes.series_watcher, "capture_stats", _forbidden)
    monkeypatch.setattr(capture_writer, "depth", lambda: {"raw_trades": 7})

    body = TestClient(main.app).get("/api/health/pipeline").json()

    assert body["buffered_unwritten"]["series_watcher_trades"] == 7


def test_pipeline_health_extends_index_stream_with_backfill_activity(monkeypatch):
    """Issue #260: index_stream's existing status surface is extended with
    the reconnect-gap backfill stats (services/index_feed/backfill.py),
    not replaced by a new top-level key - the requirement was to extend
    the existing index_feed-relevant section."""
    import main
    from fastapi.testclient import TestClient
    from services.diagnostics import routes

    monkeypatch.setattr(routes.index_feed_backfill, "stats", lambda: {
        "checks": 4, "gaps_detected": 1, "attempts": 1, "successes": 1,
        "failures": 0, "rows_backfilled": 12, "last_gap": None, "last_result": None,
    })
    main.state["index_stream_status"] = {"connected": True, "error": None, "ws_url": "wss://x"}

    body = TestClient(main.app).get("/api/health/pipeline").json()

    assert body["index_stream"]["connected"] is True  # existing field preserved
    assert body["index_stream"]["backfill"]["rows_backfilled"] == 12


def test_pipeline_health_reports_total_store_probe_cost(monkeypatch):
    """Permanent recurrence detection: the wall time the store block cost
    ships in the payload, so the next regression is visible in the same
    read that CLAUDE.md already sends every investigation to."""
    import main
    from fastapi.testclient import TestClient

    body = TestClient(main.app).get("/api/health/pipeline").json()

    assert isinstance(body["stores_probe_ms"], float)
    assert body["stores_exact_rows"] is False


def test_pipeline_health_bounds_a_hung_store_probe(monkeypatch):
    """The live incident this task fixes: one store probe that never
    returns must not hang the whole route. A monkeypatched store_stats
    that blocks forever must still let the route respond, with that one
    store's slot showing a timeout error instead of a value.

    Asserts on the route's own self-reported stores_probe_ms rather than
    TestClient's wall-clock .get() duration (a deviation from this task's
    plan document, made after deterministically reproducing why the
    plan's literal `elapsed < 5.0` around TestClient(...).get(...) fails
    even against a correct fix): TestClient tears down a fresh event loop
    per call (unless used as a `with` context manager), and that teardown
    runs asyncio.run()'s shutdown_default_executor(), which BLOCKS until
    every thread in the default executor finishes - including the
    still-running, uncancellable OS thread behind this test's blocked
    probe (asyncio can cancel the *coroutine* awaiting a thread-pool
    future, per _bounded()'s docstring, but not the OS thread itself).
    Reproduced standalone (scratch scripts, not kept in-repo): the same
    monkeypatched route called directly via `asyncio.run(routes.
    get_pipeline_health())` returns its dict with `stores_probe_ms` ~
    204ms - matching the 0.2s bound - while the *process's* own exit is
    delayed ~5.2s by the leaked thread; TestClient exhibits the identical
    per-call delay for the same reason. That delay is a property of the
    test harness's own loop teardown, never of the real long-lived server
    process (which does not tear its loop down per request, so the HTTP
    response the plan's docstring describes - fast, with the OS thread
    merely leaking in the background - is what a live request actually
    gets). stores_probe_ms is computed inside the coroutine immediately
    after `await asyncio.gather(...)` returns, before any of that
    teardown machinery runs, so it reflects the real bound uncontaminated
    by it - and it is already the field this file's own
    test_pipeline_health_reports_total_store_probe_cost asserts on for
    the same reason.

    Second deviation, also verified rather than assumed: the plan's own
    version of _hangs_for_rejections() delegates every non-target table to
    the real store_stats.store_stats(), expecting those real reads to
    succeed. This worktree's data/ has no signal_log.db/series_watcher.db/
    etc. (confirmed via `ls data/` - only config_performance.db,
    event_schedule.db, fault_log.db, game_state.db, paper_broker.db,
    risk_state.db, series_cache.db, settlement_edge.db, shadow_mode.db,
    title_cache.db), so that real call returns store_stats's own documented
    "missing store" shape (test_probe_opens_read_only_and_does_not_create_a_
    missing_store, above in this file: {"error": "unable to open database
    file"}) - a real, correct, unrelated-to-this-task behavior that just
    happens to also satisfy `"error" in ...`, breaking the plan's `"error"
    not in body["stores"]["signals"]` assertion for a reason that has
    nothing to do with whether the timeout bound works. Replaced with a
    fixed valid dict (same shape services/diagnostics/store_stats.py
    documents and this file's own test_pipeline_health_runs_store_probes_
    off_the_event_loop already uses) for every non-target table, so the
    test no longer depends on which data/*.db files happen to exist in
    whatever environment runs it."""
    import main
    from fastapi.testclient import TestClient
    from services.diagnostics import routes

    def _hangs_for_rejections(db_path, table, col, now, **kwargs):
        if table == "rejected_candidates":
            time.sleep(routes.STORE_PROBE_TIMEOUT_SEC + 5)  # longer than the bound
            raise AssertionError("should have been cancelled/timed out before returning")
        return {"rows": 0, "rows_exact": True, "rows_method": "count",
                "last_write_sec_ago": None, "last_write_exact": True,
                "last_write_method": "max", "probe_ms": 0.0}

    monkeypatch.setattr(routes.store_stats, "store_stats", _hangs_for_rejections)
    monkeypatch.setattr(routes, "STORE_PROBE_TIMEOUT_SEC", 0.2)  # bound the test's own wall time

    body = TestClient(main.app).get("/api/health/pipeline").json()

    assert body["stores_probe_ms"] < 5000.0, (
        f"probe block should return near the 0.2s bound, took {body['stores_probe_ms']}ms"
    )
    assert "error" in body["stores"]["rejections"]
    assert "timed out" in body["stores"]["rejections"]["error"]
    # every other store still answered normally, proving one hung probe
    # doesn't take the others down with it
    assert "error" not in body["stores"]["signals"]
