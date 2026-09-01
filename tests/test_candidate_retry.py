"""services/candidate_retry.py - single-owner retry queue for whale
candidates whose market lookup failed transiently (realtime data-plane
remediation plan, P2 Task 12; root-cause report C5, closes the H4 defect
Task 11 stopped short-circuiting on the seen dedupe ring but didn't yet
give a durable path back to evaluation for).

Code-review fix (finding #1, /code-review high pass against PR #23):
run_pending's recovery branch used to call candidate_ledger.claim()
directly and count it as "recovered" without ever constructing a
WhaleSignal or routing it through decision_bridge._handle_signal - a
recovered candidate was permanently claimed (blocking any later natural
re-presentation) without ever actually being evaluated or traded, while
still reporting success. Fixed by having run_pending accept the active
whale-watcher provider (for provider.score_recovered_trade - see
services/whalewatchers/base.py) and decision_bridge._handle_signal itself
as parameters (avoiding a circular import: candidate_retry is imported by
services/whalewatchers/kalshi_trade_tape.py, which app_state/decision_
bridge transitively import), so a recovered trade is scored through the
exact same pipeline a first-try trade uses and evaluated through the exact
same path a first-try signal uses. run_pending itself no longer calls
candidate_ledger.claim() at all - _handle_signal already performs that
claim as its own dedup gate immediately before evaluating, so the claim
and the evaluation happen atomically inside one call, never one without
the other."""
import asyncio
import time

import pytest

from services import candidate_ledger, signal_log
from services.confidence_scoring import WhaleSignal
from services.whale_stream import decision_bridge


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    from services import candidate_retry
    # candidate_ledger + signal_log: real _handle_signal (used by the
    # evaluation tests below) writes to both. Redirected locally too (on
    # top of conftest.py's session-wide install_runtime_isolation) so this
    # file's own trade_id claims can never collide with another test file's
    # claims in the same session - same convention as
    # tests/test_whale_stream_decision_bridge.py's own _isolated fixture.
    monkeypatch.setattr(candidate_ledger, "DB_PATH", tmp_path / "candidate_ledger.db")
    monkeypatch.setattr(signal_log, "DB_PATH", tmp_path / "signal_log.db")
    monkeypatch.setattr(candidate_retry, "_pending", {})
    yield


def _trade(trade_id="t1", ticker="K1"):
    return {"trade_id": trade_id, "ticker": ticker, "count_fp": "5000.00"}


def _base_cfg():
    return {"mode": "paper", "risk": {"starting_bankroll": 1000.0}}


class _NoSignalProvider:
    """Stands in for a whale-watcher provider whose score_recovered_trade
    finds nothing worth evaluating (WhaleWatcherProvider's own default) -
    used by the tests below that only care about the retry/backoff/
    abandonment machinery, not evaluation itself."""
    async def score_recovered_trade(self, trade, market, cfg, now):
        return []


class _FakeBroker:
    """Minimal stand-in - _handle_signal's find_open_confirmed_conflict
    fallback (2026-08-30) reads strategy.broker.positions.keys() directly,
    live, not a periodic state snapshot. Empty is enough here: these tests
    don't exercise ME-pairing behavior."""
    def __init__(self):
        self.positions = {}


class _FakeStrategy:
    """Stands in for services.app_state's real FollowTheWhaleStrategy -
    same pattern as test_whale_stream_decision_bridge.py's own fake:
    _handle_signal only needs .evaluate() to return an action dict shaped
    like strategy_engine.StrategyEngine._skip()'s real output."""
    def __init__(self, decision):
        self.decision = decision
        self.calls = 0
        self.broker = _FakeBroker()

    def evaluate(self, signal, cfg, **kwargs):
        self.calls += 1
        return self.decision


async def _noop_handle_signal(signal, cfg, market_results, config_fp, tick_now):
    """A handle_signal stand-in that does nothing - proves run_pending
    itself no longer claims the trade_id on recovery; only whatever
    handle_signal does (in production, decision_bridge._handle_signal's own
    internal candidate_ledger.claim()) can claim it."""
    return None


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
        result = asyncio.run(candidate_retry.run_pending(
            client, _NoSignalProvider(), _noop_handle_signal,
            _base_cfg(), {}, "", now, now=now,
        ))
        if result["recovered"] or result["abandoned"]:
            break
    assert result["recovered"] == 1
    assert result["abandoned"] == 0
    assert "t1" not in candidate_retry._pending  # removed from the queue once recovered


def test_recovery_routes_a_producible_signal_through_real_handle_signal(monkeypatch):
    """The core regression this fix closes (code-review finding #1): a
    recovered candidate must actually be evaluated - a real trading
    decision recorded - not merely claimed and dropped while reporting
    success. Uses the REAL decision_bridge._handle_signal (not a fake), so
    this proves the full plumbing: score_recovered_trade produces a
    WhaleSignal, run_pending awaits handle_signal with it, _handle_signal's
    own claim()+strategy.evaluate()+record_decision() path actually runs."""
    from services import candidate_retry

    decision = {"action": "trade", "signal": {}, "reason": None}
    fake_strategy = _FakeStrategy(decision)
    monkeypatch.setattr(decision_bridge, "strategy", fake_strategy)

    class _ScoringProvider:
        async def score_recovered_trade(self, trade, market, cfg, now):
            return [WhaleSignal(
                id=trade["trade_id"], ticker=trade["ticker"], side="yes",
                size=5000, price=0.6, confidence=0.8, timestamp=now,
            )]

    class _RecoversImmediately:
        async def get_markets_by_tickers(self, tickers):
            return {t: {"ticker": t, "status": "active"} for t in tickers}

    candidate_retry.enqueue(_trade("t2", "K2"), failure=Exception("first failure"))
    result = asyncio.run(candidate_retry.run_pending(
        _RecoversImmediately(), _ScoringProvider(), decision_bridge._handle_signal,
        _base_cfg(), {}, "", 0.0, now=time.time() + 1.0,
    ))

    assert result["retried"] == 1
    assert result["recovered"] == 1
    assert fake_strategy.calls == 1  # the recovered trade was actually evaluated, not just claimed
    assert candidate_ledger.decision_for("t2") == "trade"  # a real decision, not just a claim
    assert candidate_ledger.claim("t2") is False  # _handle_signal's own claim() already owns it


def test_recovery_with_no_producible_signal_still_counts_as_recovered_but_claims_nothing(monkeypatch):
    """A market can resolve successfully yet still fail a downstream gate
    (e.g. price moved out of the tradeable range while the trade was
    pending retry) - score_recovered_trade returning [] is a real "no
    signal" outcome, not a bug. run_pending must still report it as
    recovered (the market DID resolve) but must not claim a trade_id that
    was never evaluated - that would permanently block a future natural
    re-presentation for no reason."""
    from services import candidate_retry

    class _RecoversImmediately:
        async def get_markets_by_tickers(self, tickers):
            return {t: {"ticker": t, "status": "active"} for t in tickers}

    candidate_retry.enqueue(_trade("t2b", "K2B"), failure=Exception("first failure"))
    result = asyncio.run(candidate_retry.run_pending(
        _RecoversImmediately(), _NoSignalProvider(), _noop_handle_signal,
        _base_cfg(), {}, "", 0.0, now=time.time() + 1.0,
    ))

    assert result["recovered"] == 1
    assert "t2b" not in candidate_retry._pending
    # Nothing ever claimed this trade_id - run_pending itself must not
    # claim on a bare successful resolution with no signal to evaluate.
    assert candidate_ledger.claim("t2b") is True


def test_run_pending_awaits_score_recovered_trade():
    """Regression for the write-path capacity fix (Task 6): score_recovered_trade
    became a coroutine function (Task 5, so it can run its scoring work on the
    dedicated whale-scoring pool instead of the event loop) - run_pending must
    await it, not iterate its return value directly. A provider whose
    score_recovered_trade is async but never actually awaited would raise
    TypeError before this fix (confirmed: this exact failure was reproduced
    while implementing this task, before the `await` was added)."""
    from services import candidate_retry

    called = []

    class _AsyncScoringProvider:
        async def score_recovered_trade(self, trade, market, cfg, now):
            called.append(True)
            return [WhaleSignal(
                id=trade["trade_id"], ticker=trade["ticker"], side="yes",
                size=5000, price=0.6, confidence=0.8, timestamp=now,
            )]

    class _RecoversImmediately:
        async def get_markets_by_tickers(self, tickers):
            return {t: {"ticker": t, "status": "active"} for t in tickers}

    candidate_retry.enqueue(_trade("t_async", "K_ASYNC"), failure=Exception("first failure"))
    result = asyncio.run(candidate_retry.run_pending(
        _RecoversImmediately(), _AsyncScoringProvider(), _noop_handle_signal,
        _base_cfg(), {}, "", 0.0, now=time.time() + 1.0,
    ))

    assert called == [True]
    assert result["recovered"] == 1


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
        result = asyncio.run(candidate_retry.run_pending(
            client, _NoSignalProvider(), _noop_handle_signal,
            _base_cfg(), {}, "", now, now=now,
        ))
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
    result = asyncio.run(candidate_retry.run_pending(
        _CountingClient(), _NoSignalProvider(), _noop_handle_signal,
        _base_cfg(), {}, "", now, now=now,
    ))
    assert result["retried"] == 0
    assert calls["n"] == 0
    assert "t6" in candidate_retry._pending
