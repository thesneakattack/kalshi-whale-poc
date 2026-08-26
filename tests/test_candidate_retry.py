"""services/candidate_retry.py - single-owner retry queue for whale
candidates whose market lookup failed transiently (realtime data-plane
remediation plan, P2 Task 12; root-cause report C5, closes the H4 defect
Task 11 stopped short-circuiting on the seen dedupe ring but didn't yet
give a durable path back to evaluation for)."""
import asyncio
import time

import pytest

from services import candidate_ledger


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    from services import candidate_retry
    monkeypatch.setattr(candidate_ledger, "DB_PATH", tmp_path / "candidate_ledger.db")
    monkeypatch.setattr(candidate_retry, "_pending", {})
    yield


def _trade(trade_id="t1", ticker="K1"):
    return {"trade_id": trade_id, "ticker": ticker, "count_fp": "5000.00"}


def test_a_transient_failure_is_retried_and_recovered():
    from services import candidate_retry

    calls = {"n": 0}

    class _FlakyClient:
        async def get_markets_by_tickers(self, tickers):
            calls["n"] += 1
            if calls["n"] < 3:
                raise Exception("429 Too Many Requests")
            return {t: {"ticker": t, "status": "active"} for t in tickers}

    candidate_retry.enqueue(_trade("t1", "K1"), failure=Exception("first failure"))
    client = _FlakyClient()

    result = {"retried": 0, "recovered": 0, "abandoned": 0}
    now = time.time()
    for i in range(6):  # backoff schedule advances internally; simulate elapsed retries
        now += 40.0
        result = asyncio.run(candidate_retry.run_pending(client, now=now))
        if result["recovered"] or result["abandoned"]:
            break
    assert result["recovered"] == 1
    assert result["abandoned"] == 0
    assert "t1" not in candidate_retry._pending  # removed from the queue once recovered
    assert candidate_ledger.claim("t1") is False  # recovery claims the trade_id


def test_recovery_claims_the_trade_id_in_the_ledger():
    from services import candidate_retry

    class _RecoversImmediately:
        async def get_markets_by_tickers(self, tickers):
            return {t: {"ticker": t, "status": "active"} for t in tickers}

    candidate_retry.enqueue(_trade("t2", "K2"), failure=Exception("first failure"))
    result = asyncio.run(candidate_retry.run_pending(_RecoversImmediately(), now=time.time() + 1.0))

    assert result["retried"] == 1
    assert result["recovered"] == 1
    assert candidate_ledger.decision_for("t2") is None  # claimed, but evaluate() itself is not this module's job
    assert candidate_ledger.claim("t2") is False  # already claimed - recovery must claim it


def test_abandonment_after_the_retry_budget_is_exhausted():
    from services import candidate_retry

    class _AlwaysFailsClient:
        async def get_markets_by_tickers(self, tickers):
            raise Exception("429 Too Many Requests")

    candidate_retry.enqueue(_trade("t3", "K3"), failure=Exception("first failure"))
    client = _AlwaysFailsClient()
    now = time.time()
    result = {"retried": 0, "recovered": 0, "abandoned": 0}
    for i in range(10):
        now += 20.0  # advance past the >=72s budget (I12 R7)
        result = asyncio.run(candidate_retry.run_pending(client, now=now))
    assert result["abandoned"] >= 1
    assert "t3" not in candidate_retry._pending  # removed from the queue once abandoned


def test_a_second_enqueue_of_the_same_trade_id_while_pending_is_a_no_op():
    from services import candidate_retry

    trade = _trade("t4", "K4")
    candidate_retry.enqueue(trade, failure=Exception("first"))
    first_entry = candidate_retry._pending["t4"]
    candidate_retry.enqueue(trade, failure=Exception("second - should be ignored"))
    assert candidate_retry._pending["t4"] is first_entry  # not replaced/reset


def test_pending_count_reports_the_queue_depth():
    from services import candidate_retry

    assert candidate_retry.pending_count() == 0
    candidate_retry.enqueue(_trade("t5", "K5"), failure=Exception("x"))
    assert candidate_retry.pending_count() == 1


def test_run_pending_skips_entries_not_yet_due():
    from services import candidate_retry

    calls = {"n": 0}

    class _CountingClient:
        async def get_markets_by_tickers(self, tickers):
            calls["n"] += 1
            return {t: {"ticker": t, "status": "active"} for t in tickers}

    now = time.time()
    candidate_retry.enqueue(_trade("t6", "K6"), failure=Exception("x"), now=now)
    # Immediately after enqueue, the first backoff interval hasn't elapsed yet.
    result = asyncio.run(candidate_retry.run_pending(_CountingClient(), now=now))
    assert result["retried"] == 0
    assert calls["n"] == 0
    assert "t6" in candidate_retry._pending
