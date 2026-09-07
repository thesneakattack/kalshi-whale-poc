import pytest


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    from services import candidate_ledger
    monkeypatch.setattr(candidate_ledger, "DB_PATH", tmp_path / "candidate_ledger_test.db")
    monkeypatch.setattr(candidate_ledger, "_duplicate_count", 0)
    yield


def test_claim_is_true_once_and_false_on_replay():
    from services import candidate_ledger
    assert candidate_ledger.claim("abc123", ticker="KXBTC-25AUG25-T1") is True
    assert candidate_ledger.claim("abc123", ticker="KXBTC-25AUG25-T1") is False


def test_stats_count_claims_and_duplicates():
    from services import candidate_ledger
    candidate_ledger.claim("t1")
    candidate_ledger.claim("t1")
    candidate_ledger.claim("t2")
    stats = candidate_ledger.stats()
    assert stats["claimed"] == 2
    assert stats["duplicates"] == 1


def test_record_decision_is_idempotent_and_readable():
    from services import candidate_ledger
    candidate_ledger.claim("t1")
    candidate_ledger.record_decision("t1", "opened")
    candidate_ledger.record_decision("t1", "opened")  # must not raise


def test_decision_for_reads_back_the_recorded_decision():
    from services import candidate_ledger
    candidate_ledger.claim("t1")
    candidate_ledger.record_decision("t1", "trade")
    assert candidate_ledger.decision_for("t1") == "trade"


def test_decision_for_is_none_for_an_unclaimed_or_undecided_trade_id():
    from services import candidate_ledger
    assert candidate_ledger.decision_for("never-claimed") is None
    candidate_ledger.claim("claimed-not-decided")
    assert candidate_ledger.decision_for("claimed-not-decided") is None


def test_connect_enables_wal_mode():
    """Code-review fix (finding #4): every sibling persistence module
    (signal_log, risk_manager, paper_broker) enables WAL mode - this one
    was missed, despite claim()/record_decision() sitting on the
    exchange-wide hot path (a claim per whale-sized print), the same
    bursty-write shape the 2026-08-11 rollback-journal-mode outage was."""
    from services import candidate_ledger
    with candidate_ledger._connect() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_connect_closes_its_connection(monkeypatch):
    """Task 8 of docs/archive/lane-5-runtime-infrastructure/plans/2026-09-03-persistence-layer-db-migration-implementation.md (moved there 2026-09-06, planning-lanes migration): candidate_ledger migrates onto services/
    db.py's closing connect(), same as every other module in this plan.
    _RecordingConnection wraps the real connection instead of mutating
    conn.close directly - that raises AttributeError on this container's
    Python (sqlite3.Connection.close is read-only), the same defect Task
    1's own implementation (PR #518) found and fixed against
    tests/test_signal_log.py's proven pattern (Tier 0's own Task 5),
    applied here via Task 3's precedent (tests/test_candidate_log.py)."""
    import sqlite3
    from services import candidate_ledger

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

    monkeypatch.setattr(candidate_ledger.db.sqlite3, "connect", _tracking_connect)
    with candidate_ledger._connect() as conn:
        conn.execute("SELECT 1")
    assert closed == [True]


def test_connect_still_creates_candidates_table():
    from services import candidate_ledger
    with candidate_ledger._connect() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "candidates" in tables


def test_connect_sets_explicit_busy_timeout_pragma():
    from services import candidate_ledger
    with candidate_ledger._connect() as conn:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


# --- history-push trigger point (docs/archive/lane-8-frontend-dashboard/specs/
# 2026-09-03-history-event-driven-design.md, moved there 2026-09-06,
# planning-lanes migration, §2/§4.3) -----------------------------------


def test_record_decision_notifies_history_push(monkeypatch):
    """loadCandidateLogSummary is candidate-decision-driven per the design's
    own trigger table - record_decision() must call
    history_push.mark_history_changed() on every real write."""
    from services import candidate_ledger, history_push

    calls = []
    monkeypatch.setattr(history_push, "mark_history_changed", lambda: calls.append(1))

    candidate_ledger.claim("t1")
    candidate_ledger.record_decision("t1", "trade")

    assert calls == [1]
