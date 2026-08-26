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
