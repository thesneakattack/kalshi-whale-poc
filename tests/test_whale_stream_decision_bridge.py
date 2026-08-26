"""services/whale_stream/decision_bridge.py's _handle_signal - the
candidate ledger now actually gates strategy.evaluate() instead of just
counting (realtime data-plane remediation plan, P2 Task 10). Root-cause
report C6: WhaleSignal.id (== trade_id) was never read anywhere downstream,
so nothing stopped the same real trade_id from being evaluated twice.

Every DB_PATH involved is registered in tests/support/runtime_isolation.py
and redirected session-wide by conftest.py's install_runtime_isolation();
this file additionally redirects candidate_ledger/signal_log to a
per-test tmp_path so trade_id claims made by other tests in the same
session can never collide with this file's own trade_ids.
"""
import asyncio

import pytest

from services import candidate_ledger, signal_log
from services.confidence_scoring import WhaleSignal
from services.whale_stream import decision_bridge


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(candidate_ledger, "DB_PATH", tmp_path / "candidate_ledger.db")
    monkeypatch.setattr(signal_log, "DB_PATH", tmp_path / "signal_log.db")
    yield


def _make_signal(id="t1", ticker="KXBTC15M-26AUG17-B1", side="yes"):
    return WhaleSignal(
        id=id, ticker=ticker, side=side, size=1000, price=0.6, confidence=0.8, timestamp=1_755_000_000.0,
    )


class _FakeStrategy:
    """Stands in for services.app_state's real FollowTheWhaleStrategy -
    _handle_signal only needs .evaluate() to return an action dict shaped
    like strategy_engine.StrategyEngine._skip()'s real output; driving the
    real strategy would require a fully configured broker/risk stack this
    test doesn't need to prove ledger gating."""
    def __init__(self, decision):
        self.decision = decision
        self.calls = 0

    def evaluate(self, signal, cfg, **kwargs):
        self.calls += 1
        return self.decision


def _base_cfg():
    return {"mode": "paper", "risk": {"starting_bankroll": 1000.0}}


def test_handle_signal_skips_a_trade_id_already_claimed(monkeypatch):
    candidate_ledger.claim("dup1")  # pre-claim it, simulating an earlier evaluation
    fake_strategy = _FakeStrategy({"action": "skip", "signal": {}, "reason": "should never run"})
    monkeypatch.setattr(decision_bridge, "strategy", fake_strategy)

    signal = _make_signal(id="dup1")
    result = asyncio.run(decision_bridge._handle_signal(signal, _base_cfg(), {}, "", 0.0))

    assert result["action"] == "skip"
    assert result["reason"] == "duplicate_trade_id"
    assert fake_strategy.calls == 0  # evaluate() must never run for a duplicate


def test_handle_signal_evaluates_and_records_a_fresh_trade_id(monkeypatch):
    decision = {"action": "trade", "signal": {}, "reason": None}
    fake_strategy = _FakeStrategy(decision)
    monkeypatch.setattr(decision_bridge, "strategy", fake_strategy)

    signal = _make_signal(id="fresh1")
    result = asyncio.run(decision_bridge._handle_signal(signal, _base_cfg(), {}, "", 0.0))

    assert result is decision
    assert fake_strategy.calls == 1
    assert candidate_ledger.decision_for("fresh1") == "trade"


def test_handle_signal_records_skip_action_in_the_ledger(monkeypatch):
    decision = {"action": "skip", "signal": {}, "reason": "market has already resolved"}
    fake_strategy = _FakeStrategy(decision)
    monkeypatch.setattr(decision_bridge, "strategy", fake_strategy)

    signal = _make_signal(id="skip1")
    asyncio.run(decision_bridge._handle_signal(signal, _base_cfg(), {}, "", 0.0))

    assert candidate_ledger.decision_for("skip1") == "skip"
