import asyncio
import contextlib

import pytest

from services import candidate_log as cl
from services import capture_writer as cw


@pytest.fixture(autouse=True)
def _redirect_db(tmp_path, monkeypatch):
    db_path = tmp_path / "candidate_log.db"
    monkeypatch.setattr(cl, "DB_PATH", db_path)
    # Both rejected_candidates (P3 Task 17) and rejection_events (P3 Task
    # 16) route through capture_writer now - point both stores at the
    # same isolated tmp path, and reset their module-global buffers/
    # counters (same cross-test-pollution reasoning as
    # test_series_watcher.py's own fixture). rejected_candidates is
    # upsert-mode (a dict buffer), rejection_events is insert-mode (a
    # list) - see capture_writer.py's own _empty_buffer().
    monkeypatch.setattr(cw, "_STORE_PATHS", {"rejected_candidates": db_path, "rejection_events": db_path})
    monkeypatch.setattr(cw, "_buffers", {"rejected_candidates": {}, "rejection_events": []})
    monkeypatch.setattr(cw, "_last_flush_at", {"rejected_candidates": 0.0, "rejection_events": 0.0})
    monkeypatch.setattr(cw, "_dropped_counts", {"rejected_candidates": 0, "rejection_events": 0})


def test_record_rejection_creates_unresolved_row():
    cl.record_rejection("TICK-A", "market_native", "max_spread", 0.08, 0.05, now=1000.0)
    gates = cl.gate_summary()
    assert len(gates) == 1
    assert gates[0]["strategy"] == "market_native"
    assert gates[0]["gate_name"] == "max_spread"
    assert gates[0]["rejected_count"] == 1
    assert gates[0]["resolved_count"] == 0


def test_repeated_rejection_updates_in_place_not_a_new_row():
    cl.record_rejection("TICK-A", "market_native", "max_spread", 0.08, 0.05, now=1000.0)
    cl.record_rejection("TICK-A", "market_native", "max_spread", 0.09, 0.05, now=2000.0)
    cl.record_rejection("TICK-A", "market_native", "max_spread", 0.10, 0.05, now=3000.0)
    gates = cl.gate_summary()
    assert len(gates) == 1
    assert gates[0]["rejected_count"] == 1  # one row, not three


def test_different_gates_and_tickers_produce_separate_rows():
    cl.record_rejection("TICK-A", "market_native", "max_spread", 0.08, 0.05)
    cl.record_rejection("TICK-A", "market_native", "min_volume_24h", 100, 500)
    cl.record_rejection("TICK-B", "whale_follow", "entry_threshold", 0.5, 0.6)
    gates = cl.gate_summary()
    assert len(gates) == 3


def test_resolve_from_market_results_marks_resolved_and_records_result():
    cl.record_rejection("TICK-A", "market_native", "max_spread", 0.08, 0.05)
    resolved = cl.resolve_from_market_results({"TICK-A": "yes"})
    assert resolved == 1
    gates = cl.gate_summary()
    assert gates[0]["resolved_count"] == 1
    assert gates[0]["yes_count"] == 1
    assert gates[0]["no_count"] == 0


def test_resolve_ignores_tickers_not_yet_settled():
    cl.record_rejection("TICK-A", "market_native", "max_spread", 0.08, 0.05)
    resolved = cl.resolve_from_market_results({"TICK-A": ""})
    assert resolved == 0
    resolved = cl.resolve_from_market_results({"TICK-A": None})
    assert resolved == 0
    resolved = cl.resolve_from_market_results({})
    assert resolved == 0
    gates = cl.gate_summary()
    assert gates[0]["resolved_count"] == 0


def test_resolved_row_is_not_overwritten_by_a_later_rejection():
    cl.record_rejection("TICK-A", "market_native", "max_spread", 0.08, 0.05, now=1000.0)
    cl.resolve_from_market_results({"TICK-A": "yes"})
    # A later call for the same ticker/gate (shouldn't normally happen once
    # a market has resolved, since both strategies skip resolved tickers
    # before reaching any gate - but the WHERE resolved=0 guard should hold
    # regardless).
    cl.record_rejection("TICK-A", "market_native", "max_spread", 0.20, 0.05, now=9999.0)
    gates = cl.gate_summary()
    assert gates[0]["resolved_count"] == 1
    assert gates[0]["yes_count"] == 1


class _CountingConn:
    """Wraps a real sqlite3.Connection, counting write statements issued
    through it - used to pin down how many round trips resolve_from_
    market_results makes against the file, since round-trip count is what
    determines how long its write transaction holds candidate_log.db's
    single file-level lock (issue #211-adjacent: capture_writer's own
    daemon thread flushes the same file on a 1s budget and loses the race
    when this function's transaction runs long)."""

    def __init__(self, real_conn):
        self._real = real_conn
        self.write_statements: list[str] = []

    def _note(self, sql):
        if sql.strip().upper().startswith(("UPDATE", "INSERT", "DELETE")):
            self.write_statements.append(sql)

    def execute(self, sql, *args, **kwargs):
        self._note(sql)
        return self._real.execute(sql, *args, **kwargs)

    def executemany(self, sql, *args, **kwargs):
        self._note(sql)
        return self._real.executemany(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_resolve_from_market_results_batches_updates_instead_of_one_per_row(monkeypatch):
    """resolve_from_market_results used to issue one UPDATE per resolved
    row (both rejected_candidates and rejection_events) in a Python loop,
    holding candidate_log.db's write lock open for the whole loop - live-
    confirmed 2026-09-01 via /api/health/faults: capture_writer's daemon
    thread (1s busy budget) collided with it 91 times in one recent
    window, all logged as 'rejected_candidates: N row(s) retained on
    lock'. Batching via executemany keeps the write phase to a small,
    bounded number of statements regardless of how many rows resolve in
    one tick, shrinking the collision window."""
    n = 25
    for i in range(n):
        cl.record_rejection(f"TICK-{i}", "whale_follow", "entry_threshold", 0.5, 0.6, now=1000.0)
    market_results = {f"TICK-{i}": "yes" for i in range(n)}

    real_connect = cl._connect
    wrapped = []

    @contextlib.contextmanager
    def _spy_connect(*args, **kwargs):
        # real_connect is itself a @contextlib.contextmanager (services/db.py
        # migration) - calling it directly returns a _GeneratorContextManager,
        # not a connection, so this spy must enter it properly (`with`) to
        # get the actual connection before wrapping it, rather than wrapping
        # the context-manager object itself.
        with real_connect(*args, **kwargs) as real_conn:
            conn = _CountingConn(real_conn)
            wrapped.append(conn)
            yield conn

    monkeypatch.setattr(cl, "_connect", _spy_connect)

    resolved = cl.resolve_from_market_results(market_results)

    assert resolved == n
    assert len(wrapped) == 1
    # One batched UPDATE per table (rejected_candidates, rejection_events),
    # never one execute() per resolved row.
    assert len(wrapped[0].write_statements) <= 2, (
        f"expected batched UPDATEs, got {len(wrapped[0].write_statements)}: "
        f"{wrapped[0].write_statements}"
    )
    gates = cl.gate_summary()
    assert gates[0]["resolved_count"] == n


def test_hypothetical_win_rate_none_when_no_side_known():
    cl.record_rejection("TICK-A", "market_native", "max_spread", 0.08, 0.05, side=None)
    cl.resolve_from_market_results({"TICK-A": "yes"})
    gates = cl.gate_summary()
    assert gates[0]["hypothetical_win_rate"] is None
    assert gates[0]["hypothetical_win_rate_n"] == 0


def test_hypothetical_win_rate_computed_when_side_known():
    cl.record_rejection("TICK-A", "whale_follow", "entry_threshold", 0.5, 0.6, side="yes")
    cl.record_rejection("TICK-B", "whale_follow", "entry_threshold", 0.5, 0.6, side="yes")
    cl.record_rejection("TICK-C", "whale_follow", "entry_threshold", 0.5, 0.6, side="no")
    cl.resolve_from_market_results({"TICK-A": "yes", "TICK-B": "no", "TICK-C": "no"})
    gates = cl.gate_summary()
    assert gates[0]["hypothetical_win_rate_n"] == 3
    # TICK-A (side yes, result yes) wins, TICK-B (side yes, result no) loses,
    # TICK-C (side no, result no) wins -> 2/3 = 66.7%
    assert gates[0]["hypothetical_win_rate"] == pytest.approx(66.7, abs=0.1)


def test_unit_cost_none_when_not_supplied():
    cl.record_rejection("TICK-A", "market_native", "max_spread", 0.08, 0.05)
    gates = cl.gate_summary()
    assert gates[0]["avg_unit_cost"] is None
    assert gates[0]["avg_unit_cost_n"] == 0


def test_unit_cost_averaged_across_rows_with_a_known_value():
    cl.record_rejection("TICK-A", "whale_follow", "min_contracts", 10, 20, side="yes", unit_cost=0.9)
    cl.record_rejection("TICK-B", "whale_follow", "min_contracts", 10, 20, side="yes", unit_cost=0.7)
    # A rejection that couldn't supply unit_cost (e.g. price itself was
    # unparseable) shouldn't drag the average down or inflate the count.
    cl.record_rejection("TICK-C", "whale_follow", "min_contracts", 10, 20, side="yes")
    gates = cl.gate_summary()
    assert gates[0]["avg_unit_cost"] == pytest.approx(0.8)
    assert gates[0]["avg_unit_cost_n"] == 2


def test_clear_all_wipes_every_row():
    cl.record_rejection("TICK-A", "market_native", "max_spread", 0.08, 0.05)
    cl.record_rejection("TICK-B", "whale_follow", "entry_threshold", 0.5, 0.6)
    cl.clear_all()
    assert cl.gate_summary() == []


def test_gate_summary_sorted_by_resolved_count_descending():
    cl.record_rejection("TICK-A", "market_native", "gate_a", 1, 2)
    cl.record_rejection("TICK-B", "market_native", "gate_b", 1, 2)
    cl.resolve_from_market_results({"TICK-B": "yes"})
    gates = cl.gate_summary()
    assert gates[0]["gate_name"] == "gate_b"  # 1 resolved, sorts first
    assert gates[1]["gate_name"] == "gate_a"  # 0 resolved


# ---- population statistics (rejection_events) - 2026-08-23 ---------------
# ROADMAP.md's own "unusable for population statistics" gap: rejected_
# candidates dedups on (ticker, strategy, gate_name), so a ticker rejected
# repeatedly by the same gate only ever counts once. rejection_events fixes
# that additively - these tests lock in that it's a true, undeduped
# population, and that every existing danger-zone/reset operation stays in
# sync between the two tables.

def test_record_rejection_batches_rejection_events_through_capture_writer_not_synchronously():
    """The actual P3 Task 16 change, proven directly rather than only
    through population_gate_summary() (which flushes internally, so it
    would pass even if this regressed back to a synchronous write). A
    fresh row must sit in capture_writer's buffer - genuinely not yet in
    the table - until something flushes it; every real reader flushing
    internally is what makes that invisible to callers, not the absence of
    batching. record_rejection() no longer calls candidate_log._connect()
    at all (P3 Task 17 - rejected_candidates' own write moved off it too),
    so neither table exists on disk yet at this point, not just empty."""
    import sqlite3

    cl.record_rejection("TICK-A", "market_native", "max_spread", 0.08, 0.05, now=1000.0)
    assert cw.depth()["rejection_events"] == 1
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        with sqlite3.connect(cl.DB_PATH) as conn:
            conn.execute("SELECT COUNT(*) FROM rejection_events").fetchone()
    cw.flush_now("rejection_events")
    assert cw.depth()["rejection_events"] == 0
    with sqlite3.connect(cl.DB_PATH) as conn:
        assert conn.execute("SELECT COUNT(*) FROM rejection_events").fetchone()[0] == 1


def test_record_rejection_batches_rejected_candidates_through_capture_writer_too():
    """Same proof as the rejection_events test above, for the other table
    (P3 Task 17 - rejected_candidates' UPSERT moved off the synchronous
    connect() path too, once the reader-gate call site needed
    record_rejection() safe to call directly from the WS reader hot
    path). gate_summary() flushes internally, so it alone wouldn't catch
    a regression back to a synchronous write."""
    import sqlite3

    cl.record_rejection("TICK-A", "market_native", "max_spread", 0.08, 0.05, now=1000.0)
    assert cw.depth()["rejected_candidates"] == 1
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        with sqlite3.connect(cl.DB_PATH) as conn:
            conn.execute("SELECT COUNT(*) FROM rejected_candidates").fetchone()
    cw.flush_now("rejected_candidates")
    assert cw.depth()["rejected_candidates"] == 0
    with sqlite3.connect(cl.DB_PATH) as conn:
        assert conn.execute("SELECT COUNT(*) FROM rejected_candidates").fetchone()[0] == 1


def test_repeated_rejection_grows_the_population_table_not_deduped():
    """The exact gap gate_summary() can't answer - three rejections of the
    same ticker/gate must be three population rows, not one."""
    cl.record_rejection("TICK-A", "market_native", "max_spread", 0.08, 0.05, now=1000.0)
    cl.record_rejection("TICK-A", "market_native", "max_spread", 0.09, 0.05, now=2000.0)
    cl.record_rejection("TICK-A", "market_native", "max_spread", 0.10, 0.05, now=3000.0)
    pop = cl.population_gate_summary(min_samples=0)
    assert len(pop) == 1
    assert pop[0]["rejected_count"] == 3  # not 1, unlike gate_summary()'s dedup


def test_population_gate_summary_reports_insufficient_below_min_samples():
    cl.record_rejection("TICK-A", "whale_follow", "entry_threshold", 0.5, 0.6, side="yes")
    cl.resolve_from_market_results({"TICK-A": "yes"})
    pop = cl.population_gate_summary(min_samples=5)
    assert pop[0]["status"] == "insufficient"
    assert pop[0]["hypothetical_win_rate"] is None
    assert pop[0]["hypothetical_win_rate_n"] == 1


def test_population_gate_summary_ready_once_min_samples_met():
    for i in range(5):
        side = "yes" if i < 4 else "no"
        cl.record_rejection(f"TICK-{i}", "whale_follow", "entry_threshold", 0.5, 0.6, side=side)
        cl.resolve_from_market_results({f"TICK-{i}": "yes"})
    pop = cl.population_gate_summary(min_samples=5)
    assert pop[0]["status"] == "ready"
    assert pop[0]["hypothetical_win_rate_n"] == 5
    assert pop[0]["hypothetical_win_rate"] == pytest.approx(80.0)  # 4/5 sided-matched


def test_population_gate_summary_averages_unit_cost_across_the_undeduped_population():
    """Unlike gate_summary()'s dedup, every repeated rejection of the same
    ticker/gate is its own row in rejection_events - avg_unit_cost here
    must average across all of them, not just the most recent."""
    cl.record_rejection("TICK-A", "whale_watcher", "min_contracts", 10, 20, side="yes", unit_cost=0.9, now=1000.0)
    cl.record_rejection("TICK-A", "whale_watcher", "min_contracts", 12, 20, side="yes", unit_cost=0.5, now=2000.0)
    pop = cl.population_gate_summary(min_samples=0)
    assert pop[0]["rejected_count"] == 2
    assert pop[0]["avg_unit_cost"] == pytest.approx(0.7)
    assert pop[0]["avg_unit_cost_n"] == 2


def test_population_resolution_is_batched_by_ticker_not_row():
    """A ticker rejected many times by the same gate must all resolve
    together from one market_results entry - not just the most recent row,
    the way rejected_candidates' single row would suggest."""
    for i in range(4):
        cl.record_rejection("TICK-A", "market_native", "max_spread", 0.05 + i * 0.01, 0.05, now=1000.0 + i)
    cl.resolve_from_market_results({"TICK-A": "yes"})
    pop = cl.population_gate_summary(min_samples=0)
    assert pop[0]["rejected_count"] == 4
    assert pop[0]["resolved_count"] == 4


def test_clear_all_wipes_the_population_table_too():
    cl.record_rejection("TICK-A", "market_native", "max_spread", 0.08, 0.05)
    cl.clear_all()
    assert cl.population_gate_summary(min_samples=0) == []


def test_count_range_and_clear_range_include_population_rows():
    # One rejection lands in both tables per call - a fresh ticker/gate, so
    # rejected_candidates gets exactly one row too (no dedup collapse to
    # worry about here).
    cl.record_rejection("TICK-A", "market_native", "max_spread", 0.08, 0.05, now=1000.0)
    cl.record_rejection("TICK-B", "whale_follow", "entry_threshold", 0.5, 0.6, now=2000.0)
    # 2 rows in rejected_candidates + 2 in rejection_events = 4.
    assert cl.count_range(before=1500.0) == 2
    assert cl.count_range() == 4
    deleted = cl.clear_range(before=1500.0)
    assert deleted == 2  # one from each table for TICK-A only
    remaining_gates = cl.gate_summary()
    assert len(remaining_gates) == 1
    assert remaining_gates[0]["gate_name"] == "entry_threshold"
    remaining_population = cl.population_gate_summary(min_samples=0)
    assert len(remaining_population) == 1
    assert remaining_population[0]["gate_name"] == "entry_threshold"


def test_connect_creates_rejected_candidates_with_unit_cost_from_ddl(tmp_path, monkeypatch):
    """Task 3c: candidate_log.py's _connect() now creates rejected_candidates
    (and rejection_events) from capture_writer's shared, unit_cost-inclusive
    DDL directly, rather than relying on _add_column_if_missing to backfill
    it after a bare CREATE - on a FRESH db the column exists from the start.
    _add_column_if_missing stays as a no-op safety net for pre-existing
    files (unchanged, not removed by this task)."""
    import sqlite3

    monkeypatch.setattr(cl, "DB_PATH", tmp_path / "candidate_log.db")
    with cl._connect() as conn:
        rc_cols = [r[1] for r in conn.execute("PRAGMA table_info(rejected_candidates)").fetchall()]
        re_cols = [r[1] for r in conn.execute("PRAGMA table_info(rejection_events)").fetchall()]
    assert "unit_cost" in rc_cols
    assert "unit_cost" in re_cols


def test_population_gate_summary_includes_edge_gate_rejections(tmp_path, monkeypatch):
    """Task 9 (docs/superpowers/plans/2026-09-03-strategy-edge-gate-
    implementation.md): confirms design §3.3/§6's claim that
    candidate_log's existing counterfactual-tracking machinery picks up
    edge_gate rejections with zero new plumbing - a real test of that
    claim, not a repeat of the trust the design document already
    extended it. record_rejection()/population_gate_summary() are both
    already generic over gate_name (Task 8 needed no candidate_log
    changes at all), so this is expected to pass immediately."""
    monkeypatch.setattr(cl, "DB_PATH", tmp_path / "candidate_log.db")
    cl.record_rejection("TICK-A", "whale_follow", "edge_gate", -0.02, 0.04, side="yes", unit_cost=0.55)
    summary = cl.population_gate_summary(min_samples=1)
    gate_names = {row["gate_name"] for row in summary}
    assert "edge_gate" in gate_names


def test_connect_closes_its_connection(tmp_path, monkeypatch, _redirect_db):
    """_RecordingConnection wraps the real connection instead of mutating
    conn.close directly - that raises AttributeError on this container's
    Python (sqlite3.Connection.close is read-only), the same defect Task
    1's own implementation (PR #518) found and fixed against
    tests/test_signal_log.py's proven pattern (Tier 0's own Task 5),
    applied here for this module's test file."""
    import sqlite3

    closed = []
    real_connect = sqlite3.connect

    class _RecordingConnection:
        def __init__(self, inner):
            self._inner = inner

        def close(self):
            closed.append(True)
            self._inner.close()

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, *exc_info):
            return self._inner.__exit__(*exc_info)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def _tracking_connect(*args, **kwargs):
        return _RecordingConnection(real_connect(*args, **kwargs))

    monkeypatch.setattr(cl.db.sqlite3, "connect", _tracking_connect)
    with cl._connect() as conn:
        conn.execute("SELECT 1")
    assert closed == [True]


def test_connect_still_creates_both_tables_indexes_and_unit_cost_columns(tmp_path, monkeypatch, _redirect_db):
    with cl._connect() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert {"rejected_candidates", "rejection_events"} <= tables
        indexes = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )}
        assert "idx_rejection_events_gate" in indexes
        assert "idx_rejection_events_unresolved" in indexes
        rc_cols = {r[1] for r in conn.execute("PRAGMA table_info(rejected_candidates)")}
        re_cols = {r[1] for r in conn.execute("PRAGMA table_info(rejection_events)")}
        assert "unit_cost" in rc_cols
        assert "unit_cost" in re_cols


def test_connect_sets_explicit_busy_timeout_pragma(tmp_path, monkeypatch, _redirect_db):
    with cl._connect() as conn:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


# --- population_gate_summary_async (issue #410) ---------------------------
#
# docs/superpowers/specs/2026-09-04-issue-410-pool-vs-aiosqlite-design.md
# moves GET /api/candidate-log/summary's 15-22s scan off tick_executor's
# 2-worker pool (shared with candidate_ledger.claim()/record_decision() on
# the live per-signal decision path) and onto aiosqlite. These cover the
# async path's own behaviour; the route wiring is
# tests/test_analytics_routes.py's job.


@pytest.fixture(autouse=True)
def _reset_aio_db_cache():
    """BINDING requirement of the design's Sec 3, not optional hygiene:
    aiosqlite gives every cached connection a NON-daemon OS thread and
    CPython's shutdown joins those, so a module that opens one and never
    resets it can print "N passed" and then hang forever (PR adversarial
    review finding C1, 2026-09-01). Same fixture tests/test_diagnostics.py,
    tests/test_diagnostics_routes.py, tests/test_series_watcher.py and
    tests/test_main_tick_executor_wiring.py already carry. It also stops one
    test's connection - cached against a tmp_path DB that is deleted at
    teardown - from being handed to the next test."""
    yield
    from services.diagnostics import _aio_db
    asyncio.run(_aio_db.reset())


def test_population_gate_summary_async_matches_the_sync_version_exactly():
    """The two paths answer the same question from the same rows, so any
    divergence is a bug by definition - they share _POPULATION_GATE_SQL and
    _summarize_population_rows precisely so this can be asserted."""
    for i in range(6):
        side = "yes" if i < 4 else "no"
        cl.record_rejection(f"TICK-{i}", "whale_follow", "entry_threshold", 0.5, 0.6, side=side, unit_cost=0.4)
        cl.resolve_from_market_results({f"TICK-{i}": "yes"})
    cl.record_rejection("TICK-X", "market_native", "max_spread", 0.08, 0.05, now=1000.0)

    sync_result = cl.population_gate_summary(min_samples=5)
    async_result = asyncio.run(cl.population_gate_summary_async(min_samples=5))

    assert async_result == sync_result
    assert any(g["status"] == "ready" for g in async_result)


def test_population_gate_summary_async_self_heals_a_missing_schema(tmp_path, monkeypatch):
    """_connect() re-runs its DDL on every call, so the sync read path has
    always repaired a missing table rather than raising. The async path must
    keep that property via _ensure_schema_aio - without it, "no data yet"
    becomes a hard error (the exact collapse _aio_db.connection_for()'s
    schema_init parameter exists to prevent)."""
    fresh = tmp_path / "nonexistent" / "candidate_log.db"
    fresh.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cl, "DB_PATH", fresh)

    assert asyncio.run(cl.population_gate_summary_async(min_samples=0)) == []


def test_population_gate_summary_async_creates_the_gate_index():
    """The 15-22s GROUP BY's real query plan is `SCAN rejection_events USING
    INDEX idx_rejection_events_gate` - verified live against the 28.7M-row
    table. If the async path's schema init ever stops creating that index,
    the query silently gets a different (worse) plan rather than failing, so
    assert it directly."""
    asyncio.run(cl.population_gate_summary_async(min_samples=0))
    with cl._connect() as conn:
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_rejection_events_gate" in indexes
    assert "idx_rejection_events_unresolved" in indexes


def test_population_gate_summary_async_yields_to_the_event_loop():
    """The whole point of the conversion: the read must yield to the loop
    rather than occupying it (or a tick_executor worker) for its full
    duration. A concurrently-scheduled coroutine must get to run WHILE the
    read is in flight. This is the design's Sec 5 "assert the aiosqlite read
    path yields" detection requirement.

    CORRECTED (adversarial review of this PR, finding F2): the original
    version of this test asserted `progressed` truthy only AFTER `await
    ticker` - by which point the ticker had unconditionally run to
    completion regardless of whether the read itself ever yielded control.
    Reproduced live: a coroutine whose body is a synchronous
    `time.sleep(0.05)` with zero internal awaits passes the original test
    body unchanged (`progressed` ends up populated purely because the test
    awaits the ticker afterward, not because anything ran concurrently with
    it). The fix snapshots progress the instant the read call returns,
    before the ticker is awaited - that is the only point that can
    distinguish "yielded during the read" from "ran only after"."""
    cl.record_rejection("TICK-A", "market_native", "max_spread", 0.08, 0.05, now=1000.0)
    progressed = []
    progress_at_read_return = []
    read_thread_ids = []

    # Wrap aiosqlite's execute_fetchall to record which OS thread actually
    # runs the SQL - aiosqlite serializes all operations for a connection
    # onto ONE dedicated worker thread, so this must never be the test's own
    # (main) thread. Closes the other half of F2: yielding control back to
    # the loop is necessary but not sufficient - the SQL itself must be off
    # the calling thread, not merely awaited.
    import threading
    from services.diagnostics import _aio_db as aio_db_module

    original_connection_for = aio_db_module.connection_for

    async def _spy_connection_for(db_path, schema_init=None):
        conn = await original_connection_for(db_path, schema_init=schema_init)
        # Patch the SYNC callable aiosqlite dispatches to its worker thread
        # (Connection._execute_fetchall), not the async wrapper
        # (execute_fetchall) that awaits it - the async wrapper resumes back
        # on the calling (event-loop/main) thread once the worker replies,
        # so recording the thread id there would always show the wrong
        # thread and silently pass. This is the actual callable that runs
        # ON the worker thread.
        original_sync_fetchall = conn._execute_fetchall

        def _spy_sync_fetchall(sql, parameters):
            read_thread_ids.append(threading.get_ident())
            return original_sync_fetchall(sql, parameters)

        conn._execute_fetchall = _spy_sync_fetchall
        return conn

    async def _exercise():
        async def _ticker():
            for _ in range(50):
                await asyncio.sleep(0)
                progressed.append(1)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(cl._aio_db, "connection_for", _spy_connection_for)
            ticker = asyncio.create_task(_ticker())
            result = await cl.population_gate_summary_async(min_samples=0)
        # THIS is the only meaningful checkpoint - taken before the ticker
        # is awaited, so it reflects only what ran DURING the read.
        progress_at_read_return.append(len(progressed))
        await ticker
        return result

    result = asyncio.run(_exercise())
    assert result != []
    assert progress_at_read_return[0] > 0, (
        "the ticker made zero progress before the read returned - "
        "the async read never actually yielded control to the loop"
    )
    assert read_thread_ids, "execute_fetchall spy never recorded a call - test is broken"
    assert threading.get_ident() not in read_thread_ids, (
        "the SQL ran on the test's own (main) thread, not aiosqlite's worker thread"
    )
