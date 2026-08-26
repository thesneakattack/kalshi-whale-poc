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
