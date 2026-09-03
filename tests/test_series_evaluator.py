import time

from services import series_evaluator as se
from services import signal_log


def _se(tmp_path, monkeypatch):
    monkeypatch.setattr(se, "DB_PATH", tmp_path / "series_evaluator.db")
    monkeypatch.setattr(signal_log, "DB_PATH", tmp_path / "signal_log.db")
    return se


def _row(tmp_path, monkeypatch, series):
    with se._connect() as conn:
        return conn.execute(
            "SELECT status, first_seen_at, trades_observed, strike_count, next_eligible_at "
            "FROM series_status WHERE series = ?",
            (series,),
        ).fetchone()


# --- record_trade_observed ---------------------------------------------------

def test_record_trade_observed_creates_a_fresh_observing_row(tmp_path, monkeypatch):
    _se(tmp_path, monkeypatch)
    se.record_trade_observed("SER-A", now=1000.0)
    status, first_seen_at, trades_observed, strike_count, next_eligible_at = _row(tmp_path, monkeypatch, "SER-A")
    assert status == "observing"
    assert first_seen_at == 1000.0
    assert trades_observed == 1
    assert strike_count == 0
    assert next_eligible_at is None


def test_record_trade_observed_increments_an_existing_observing_row(tmp_path, monkeypatch):
    _se(tmp_path, monkeypatch)
    se.record_trade_observed("SER-A", now=1000.0)
    se.record_trade_observed("SER-A", now=1010.0)
    se.record_trade_observed("SER-A", now=1020.0)
    _, first_seen_at, trades_observed, _, _ = _row(tmp_path, monkeypatch, "SER-A")
    assert trades_observed == 3
    assert first_seen_at == 1000.0  # unchanged by later observations


def test_record_trade_observed_is_a_noop_for_an_approved_series(tmp_path, monkeypatch):
    _se(tmp_path, monkeypatch)
    se.record_trade_observed("SER-A", now=1000.0)
    with se._connect() as conn:
        conn.execute("UPDATE series_status SET status = 'approved' WHERE series = ?", ("SER-A",))
    se.record_trade_observed("SER-A", now=2000.0)
    status, _, trades_observed, _, _ = _row(tmp_path, monkeypatch, "SER-A")
    assert status == "approved"
    assert trades_observed == 1  # unchanged - approved is sticky, nothing left to observe for


def test_record_trade_observed_is_a_noop_while_still_serving_backoff(tmp_path, monkeypatch):
    _se(tmp_path, monkeypatch)
    se.record_trade_observed("SER-A", now=1000.0)
    with se._connect() as conn:
        conn.execute(
            "UPDATE series_status SET status = 'rejected', strike_count = 1, next_eligible_at = ? WHERE series = ?",
            (5000.0, "SER-A"),
        )
    se.record_trade_observed("SER-A", now=2000.0)  # before next_eligible_at
    status, _, trades_observed, strike_count, next_eligible_at = _row(tmp_path, monkeypatch, "SER-A")
    assert status == "rejected"
    assert trades_observed == 1  # unchanged
    assert next_eligible_at == 5000.0


def test_record_trade_observed_reactivates_after_backoff_served_keeping_strikes(tmp_path, monkeypatch):
    _se(tmp_path, monkeypatch)
    se.record_trade_observed("SER-A", now=1000.0)
    with se._connect() as conn:
        conn.execute(
            "UPDATE series_status SET status = 'rejected', strike_count = 2, next_eligible_at = ? WHERE series = ?",
            (5000.0, "SER-A"),
        )
    se.record_trade_observed("SER-A", now=6000.0)  # after next_eligible_at
    status, first_seen_at, trades_observed, strike_count, next_eligible_at = _row(tmp_path, monkeypatch, "SER-A")
    assert status == "observing"
    assert first_seen_at == 6000.0  # fresh observation window
    assert trades_observed == 1
    assert strike_count == 2  # carried forward - automatic reactivation, not a human reset
    assert next_eligible_at is None


# --- record_trades_observed_bulk (2026-08-11) ---------------------------------
# Same effect as record_trade_observed() called once per trade, but one
# connection for the whole batch - real, confirmed-live incident: the
# per-trade version's per-call _connect() blocked the async event loop for
# minutes once trade-tape volume went uncapped.

def test_record_trades_observed_bulk_creates_fresh_rows_with_the_batch_count(tmp_path, monkeypatch):
    _se(tmp_path, monkeypatch)
    se.record_trades_observed_bulk({"SER-A": 5, "SER-B": 2}, now=1000.0)
    status_a, first_seen_a, observed_a, _, _ = _row(tmp_path, monkeypatch, "SER-A")
    assert status_a == "observing" and first_seen_a == 1000.0 and observed_a == 5
    status_b, first_seen_b, observed_b, _, _ = _row(tmp_path, monkeypatch, "SER-B")
    assert status_b == "observing" and first_seen_b == 1000.0 and observed_b == 2


def test_record_trades_observed_bulk_increments_an_existing_observing_row(tmp_path, monkeypatch):
    _se(tmp_path, monkeypatch)
    se.record_trade_observed("SER-A", now=1000.0)
    se.record_trades_observed_bulk({"SER-A": 4}, now=1010.0)
    _, first_seen_at, trades_observed, _, _ = _row(tmp_path, monkeypatch, "SER-A")
    assert trades_observed == 5  # 1 (initial) + 4 (batch)
    assert first_seen_at == 1000.0  # unchanged by later observations


def test_record_trades_observed_bulk_is_a_noop_for_an_approved_series(tmp_path, monkeypatch):
    _se(tmp_path, monkeypatch)
    se.record_trade_observed("SER-A", now=1000.0)
    with se._connect() as conn:
        conn.execute("UPDATE series_status SET status = 'approved' WHERE series = ?", ("SER-A",))
    se.record_trades_observed_bulk({"SER-A": 10}, now=2000.0)
    status, _, trades_observed, _, _ = _row(tmp_path, monkeypatch, "SER-A")
    assert status == "approved"
    assert trades_observed == 1  # unchanged - approved is sticky

def test_record_trades_observed_bulk_is_a_noop_while_still_serving_backoff(tmp_path, monkeypatch):
    _se(tmp_path, monkeypatch)
    se.record_trade_observed("SER-A", now=1000.0)
    with se._connect() as conn:
        conn.execute(
            "UPDATE series_status SET status = 'rejected', strike_count = 1, next_eligible_at = ? WHERE series = ?",
            (5000.0, "SER-A"),
        )
    se.record_trades_observed_bulk({"SER-A": 3}, now=2000.0)  # before next_eligible_at
    status, _, trades_observed, _, next_eligible_at = _row(tmp_path, monkeypatch, "SER-A")
    assert status == "rejected"
    assert trades_observed == 1  # unchanged
    assert next_eligible_at == 5000.0


def test_record_trades_observed_bulk_reactivation_credits_the_whole_batch(tmp_path, monkeypatch):
    _se(tmp_path, monkeypatch)
    se.record_trade_observed("SER-A", now=1000.0)
    with se._connect() as conn:
        conn.execute(
            "UPDATE series_status SET status = 'rejected', strike_count = 2, next_eligible_at = ? WHERE series = ?",
            (5000.0, "SER-A"),
        )
    se.record_trades_observed_bulk({"SER-A": 7}, now=6000.0)  # after next_eligible_at
    status, first_seen_at, trades_observed, strike_count, next_eligible_at = _row(tmp_path, monkeypatch, "SER-A")
    assert status == "observing"
    assert first_seen_at == 6000.0  # fresh observation window
    assert trades_observed == 7  # the whole batch, not just 1 - all of it occurred inside the fresh window
    assert strike_count == 2  # carried forward
    assert next_eligible_at is None


def test_record_trades_observed_bulk_empty_dict_is_a_noop(tmp_path, monkeypatch):
    _se(tmp_path, monkeypatch)
    se.record_trades_observed_bulk({}, now=1000.0)
    with se._connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM series_status").fetchone()[0]
    assert count == 0


# --- ineligible_series --------------------------------------------------------

def test_ineligible_series_only_returns_series_still_serving_backoff(tmp_path, monkeypatch):
    _se(tmp_path, monkeypatch)
    se.record_trade_observed("SER-OBSERVING", now=1000.0)
    se.record_trade_observed("SER-APPROVED", now=1000.0)
    se.record_trade_observed("SER-STILL-BACKOFF", now=1000.0)
    se.record_trade_observed("SER-BACKOFF-EXPIRED", now=1000.0)
    with se._connect() as conn:
        conn.execute("UPDATE series_status SET status = 'approved' WHERE series = 'SER-APPROVED'")
        conn.execute(
            "UPDATE series_status SET status = 'rejected', next_eligible_at = 5000.0 WHERE series = 'SER-STILL-BACKOFF'"
        )
        conn.execute(
            "UPDATE series_status SET status = 'rejected', next_eligible_at = 500.0 WHERE series = 'SER-BACKOFF-EXPIRED'"
        )
    result = se.ineligible_series(now=2000.0)
    assert result == {"SER-STILL-BACKOFF"}


# --- evaluate_pending ---------------------------------------------------------

_CFG = {"series_evaluator": {
    "min_observation_sec": 3600, "min_trades_observed": 10, "max_observation_sec": 21600,
    "min_qualify_rate": 0.1, "backoff_base_sec": 1800, "backoff_multiplier": 2.0, "backoff_max_sec": 86400,
}}


def test_evaluate_pending_leaves_series_observing_when_not_enough_time_or_trades(tmp_path, monkeypatch):
    _se(tmp_path, monkeypatch)
    se.record_trade_observed("SER-A", now=1000.0)
    verdicts = se.evaluate_pending(_CFG, now=1500.0)  # 500s elapsed, well under min_observation_sec
    assert verdicts == []
    status, _, _, _, _ = _row(tmp_path, monkeypatch, "SER-A")
    assert status == "observing"


def test_evaluate_pending_auto_rejects_zero_trade_activity_at_max_observation(tmp_path, monkeypatch):
    _se(tmp_path, monkeypatch)
    with se._connect() as conn:
        conn.execute(
            "INSERT INTO series_status (series, status, first_seen_at, trades_observed) VALUES (?, 'observing', ?, 0)",
            ("SER-DEAD", 1000.0),
        )
    verdicts = se.evaluate_pending(_CFG, now=1000.0 + 21600)  # past max_observation_sec, 0 trades
    assert len(verdicts) == 1
    assert verdicts[0]["approved"] is False
    status, _, _, strike_count, next_eligible_at = _row(tmp_path, monkeypatch, "SER-DEAD")
    assert status == "rejected"
    assert strike_count == 1
    assert next_eligible_at == 1000.0 + 21600 + 1800  # first strike = backoff_base_sec


def test_evaluate_pending_approves_a_series_clearing_the_qualify_rate(tmp_path, monkeypatch):
    _se(tmp_path, monkeypatch)
    now = 1000.0
    with se._connect() as conn:
        conn.execute(
            "INSERT INTO series_status (series, status, first_seen_at, trades_observed) VALUES (?, 'observing', ?, 20)",
            ("SERGOOD", now),
        )
    # 3 of 20 observed trades qualified as real whale signals - rate 0.15 >= 0.1 threshold.
    # Ticker has no internal hyphen before the series prefix boundary - series_of()
    # splits on the FIRST hyphen only, so "SERGOOD-M1" -> series "SERGOOD" (a
    # ticker like "SER-GOOD-M1" would instead give series "SER", not what's intended).
    for i in range(3):
        signal_log.log_signal("SERGOOD-M1", "yes", 1000, 0.8, "kalshi_trade_tape", seen_at=now + 10)
    verdicts = se.evaluate_pending(_CFG, now=now + 3600)
    assert len(verdicts) == 1
    assert verdicts[0]["approved"] is True
    status, _, _, strike_count, next_eligible_at = _row(tmp_path, monkeypatch, "SERGOOD")
    assert status == "approved"
    assert strike_count == 0
    assert next_eligible_at is None


def test_evaluate_pending_rejects_a_series_below_the_qualify_rate(tmp_path, monkeypatch):
    _se(tmp_path, monkeypatch)
    now = 1000.0
    with se._connect() as conn:
        conn.execute(
            "INSERT INTO series_status (series, status, first_seen_at, trades_observed) VALUES (?, 'observing', ?, 20)",
            ("SERBAD", now),
        )
    # 1 of 20 qualified - rate 0.05 < 0.1 threshold
    signal_log.log_signal("SERBAD-M1", "yes", 1000, 0.8, "kalshi_trade_tape", seen_at=now + 10)
    verdicts = se.evaluate_pending(_CFG, now=now + 3600)
    assert len(verdicts) == 1
    assert verdicts[0]["approved"] is False
    status, _, _, strike_count, next_eligible_at = _row(tmp_path, monkeypatch, "SERBAD")
    assert status == "rejected"
    assert strike_count == 1
    assert next_eligible_at == now + 3600 + 1800


def test_evaluate_pending_escalates_backoff_on_repeated_rejection(tmp_path, monkeypatch):
    _se(tmp_path, monkeypatch)
    with se._connect() as conn:
        conn.execute(
            "INSERT INTO series_status (series, status, first_seen_at, trades_observed, strike_count) "
            "VALUES (?, 'observing', ?, 0, 2)",
            ("SER-REPEAT", 1000.0),
        )
    now = 1000.0 + 21600
    verdicts = se.evaluate_pending(_CFG, now=now)
    assert verdicts[0]["approved"] is False
    _, _, _, strike_count, next_eligible_at = _row(tmp_path, monkeypatch, "SER-REPEAT")
    assert strike_count == 3
    # third strike (old strike_count=2): base * multiplier^2 = 1800 * 4 = 7200
    assert next_eligible_at == now + 7200


def test_evaluate_pending_backoff_is_capped_at_backoff_max_sec(tmp_path, monkeypatch):
    _se(tmp_path, monkeypatch)
    with se._connect() as conn:
        conn.execute(
            "INSERT INTO series_status (series, status, first_seen_at, trades_observed, strike_count) "
            "VALUES (?, 'observing', ?, 0, 10)",
            ("SER-CAPPED", 1000.0),
        )
    now = 1000.0 + 21600
    se.evaluate_pending(_CFG, now=now)
    _, _, _, _, next_eligible_at = _row(tmp_path, monkeypatch, "SER-CAPPED")
    assert next_eligible_at == now + 86400  # capped at backoff_max_sec, not 1800 * 2**10


# --- overview / reset / clear_all --------------------------------------------

def test_overview_lists_every_series_ever_evaluated(tmp_path, monkeypatch):
    _se(tmp_path, monkeypatch)
    se.record_trade_observed("SER-A", now=1000.0)
    se.record_trade_observed("SER-B", now=1000.0)
    result = se.overview()
    assert {r["series"] for r in result} == {"SER-A", "SER-B"}


def test_reset_returns_false_for_a_series_never_observed(tmp_path, monkeypatch):
    _se(tmp_path, monkeypatch)
    assert se.reset("SER-NEVER-SEEN") is False


def test_reset_is_a_deliberate_fresh_start_clearing_strike_count(tmp_path, monkeypatch):
    _se(tmp_path, monkeypatch)
    se.record_trade_observed("SER-A", now=1000.0)
    with se._connect() as conn:
        conn.execute(
            "UPDATE series_status SET status = 'rejected', strike_count = 5, next_eligible_at = 99999 WHERE series = ?",
            ("SER-A",),
        )
    assert se.reset("SER-A", now=2000.0) is True
    status, first_seen_at, trades_observed, strike_count, next_eligible_at = _row(tmp_path, monkeypatch, "SER-A")
    assert status == "observing"
    assert first_seen_at == 2000.0
    assert trades_observed == 0
    assert strike_count == 0  # cleared - unlike automatic reactivation, a manual reset is a fresh start
    assert next_eligible_at is None


def test_clear_all_wipes_every_row(tmp_path, monkeypatch):
    _se(tmp_path, monkeypatch)
    se.record_trade_observed("SER-A", now=1000.0)
    se.record_trade_observed("SER-B", now=1000.0)
    se.clear_all()
    assert se.overview() == []


# --- services/db.py migration (Task 9) -----------------------------------

def test_connect_closes_its_connection(tmp_path, monkeypatch):
    """_RecordingConnection wraps the real connection instead of mutating
    conn.close directly - mutating it raises AttributeError on this
    container's Python (sqlite3.Connection.close is read-only), the same
    defect Task 1's own implementation (PR #518) found and fixed against
    tests/test_signal_log.py's proven pattern (Tier 0's own Task 5).
    real_connect captures whatever sqlite3.connect currently is, which
    under pytest is tests/support/runtime_isolation.py's _guarded_connect,
    not the raw stdlib one - the live-data guard stays in force underneath
    the spy."""
    import sqlite3

    _se(tmp_path, monkeypatch)
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

    monkeypatch.setattr(se.db.sqlite3, "connect", _tracking_connect)
    with se._connect() as conn:
        conn.execute("SELECT 1")
    assert closed == [True]


def test_connect_still_creates_table(tmp_path, monkeypatch):
    _se(tmp_path, monkeypatch)
    with se._connect() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "series_status" in tables
        cols = [r[1] for r in conn.execute("PRAGMA table_info(series_status)")]
        assert cols == [
            "series", "status", "first_seen_at", "trades_observed",
            "strike_count", "last_evaluated_at", "next_eligible_at",
        ]


def test_connect_sets_explicit_busy_timeout_pragma(tmp_path, monkeypatch):
    """D3: 5000ms is sqlite3's existing implicit default made explicit,
    not a new number invented for this module."""
    _se(tmp_path, monkeypatch)
    with se._connect() as conn:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_connect_closes_every_connection_across_repeated_calls(tmp_path, monkeypatch):
    """Originally written as an OS-level /proc/self/fd count diff (matching
    PR #540's fix-list ask). That version produced a REAL CI failure on
    this task's own PR (#545) under pytest-xdist's full-suite run:
    89 != 161, a DROP, not a rise. Root-caused, not dismissed as a flake
    (CLAUDE.md/memory: no-flake-classification-ever) - an un-.close()'d
    sqlite3.Connection does NOT release its fd via CPython refcounting
    alone (verified directly: dropping the last reference with gc
    disabled leaves the fd open), so it sits until the next cyclic GC
    pass. In a long-running xdist worker that has already executed
    thousands of prior tests, an unrelated GC cycle can fire at any
    point and reap OTHER tests' lingering garbage mid-measurement,
    moving the count for reasons having nothing to do with this module.

    Also verified the "obvious" fix (pinning gc.collect() at both
    snapshot points) is WRONG, not just inelegant: run against this
    file's own pre-migration (genuinely leaking) code, gc.collect()-
    pinning makes before==after too - it reaps the leaked-but-now-
    unreferenced connections along with the ambient noise, silently
    masking the exact defect this test exists to catch.

    This version sidesteps the whole class of problem: it counts actual
    .close() calls via the same spy idiom as
    test_connect_closes_its_connection above, extended across many
    calls, instead of reading ambient OS process state. Deterministic
    regardless of what any other test or the GC is doing."""
    import sqlite3

    _se(tmp_path, monkeypatch)
    opened = []
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
        conn = _RecordingConnection(real_connect(*args, **kwargs))
        opened.append(conn)
        return conn

    monkeypatch.setattr(se.db.sqlite3, "connect", _tracking_connect)
    for i in range(50):
        se.record_trade_observed(f"SER-FD-{i}", now=1000.0 + i)
    for _ in range(50):
        se.ineligible_series(now=2000.0)
    assert len(opened) == len(closed) == 100


def test_connect_creates_table_in_autocommit_not_a_transaction(tmp_path, monkeypatch):
    """No statement in this module's _connect() wrapper was reordered
    relative to before migration (single registered table, no extra
    in-wrapper statements) - this is a plain preservation pin, not a
    guard against a specific reordering risk the way Task 5's/Task 10's
    equivalent test is (those moved an extra statement inside db.connect()'s
    `with conn:`). Still asserted: Python's sqlite3 only emits an implicit
    BEGIN before INSERT/UPDATE/DELETE/REPLACE, so the registered init_fn's
    CREATE TABLE commits immediately regardless."""
    _se(tmp_path, monkeypatch)
    with se._connect() as conn:
        assert conn.in_transaction is False


def test_connect_enables_wal_mode(tmp_path, monkeypatch):
    # Real live incident (2026-08-11) - WAL mode lets readers proceed
    # concurrently with a writer instead of the whole DB serializing every
    # access against every other one, the standard hardening step for a
    # bursty-write workload like this one.
    _se(tmp_path, monkeypatch)
    with se._connect() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
