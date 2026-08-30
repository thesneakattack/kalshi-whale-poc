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
from services.app_state import state
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
        self.last_kwargs = None

    def evaluate(self, signal, cfg, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
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


def test_handle_signal_routes_claim_and_record_decision_through_tick_executor(monkeypatch):
    """Code-review fix (finding #4): candidate_ledger.claim()/
    record_decision() are on the exchange-wide hot path (a claim per
    whale-sized print) and must run via tick_executor.run() (off the
    event loop), not called directly on it, consistent with this plan's
    own P1 work. Proven by spying on tick_executor.run and asserting it
    was actually invoked for both calls (not bypassed), with the real
    candidate_ledger writes still landing correctly through it."""
    decision = {"action": "trade", "signal": {}, "reason": None}
    fake_strategy = _FakeStrategy(decision)
    monkeypatch.setattr(decision_bridge, "strategy", fake_strategy)

    routed_fns = []

    async def _spy_run(fn):
        routed_fns.append(fn)
        return fn()

    monkeypatch.setattr(decision_bridge.tick_executor, "run", _spy_run)

    signal = _make_signal(id="viatickexec1")
    result = asyncio.run(decision_bridge._handle_signal(signal, _base_cfg(), {}, "", 0.0))

    assert len(routed_fns) == 2  # claim() and record_decision(), both routed
    assert result is decision
    assert fake_strategy.calls == 1
    # And the real ledger effects still happened correctly through that routing.
    assert candidate_ledger.decision_for("viatickexec1") == "trade"
    assert candidate_ledger.claim("viatickexec1") is False


def test_handle_signal_skip_still_only_routes_the_claim_check(monkeypatch):
    """A duplicate must never reach record_decision() at all - claim()
    alone (routed through tick_executor.run) is enough to know it's a
    skip, so only one call should be routed for this path."""
    candidate_ledger.claim("dup-tickexec")  # pre-claim it
    fake_strategy = _FakeStrategy({"action": "skip", "signal": {}, "reason": "should never run"})
    monkeypatch.setattr(decision_bridge, "strategy", fake_strategy)

    routed_fns = []

    async def _spy_run(fn):
        routed_fns.append(fn)
        return fn()

    monkeypatch.setattr(decision_bridge.tick_executor, "run", _spy_run)

    signal = _make_signal(id="dup-tickexec")
    result = asyncio.run(decision_bridge._handle_signal(signal, _base_cfg(), {}, "", 0.0))

    assert result["reason"] == "duplicate_trade_id"
    assert len(routed_fns) == 1  # only the claim() check - never reaches record_decision
    assert fake_strategy.calls == 0


def _set_me_state(monkeypatch, market_titles, event_titles, open_position_tickers, me_pairs=None):
    monkeypatch.setitem(state, "market_titles", market_titles)
    monkeypatch.setitem(state, "event_titles", event_titles)
    monkeypatch.setitem(state, "open_position_tickers", open_position_tickers)
    monkeypatch.setitem(state, "me_pairs", me_pairs or {})


def test_handle_signal_passes_broad_me_complement_when_watchlist_missed_it(monkeypatch):
    """The exact ATP-match failure mode this fix closes: BUS is a fresh
    candidate never on the watchlist, so state["me_pairs"] (built from the
    narrow per-tick markets list) has nothing for it - but
    market_titles/event_titles (the broad, persisted caches) and
    open_position_tickers (BON already open) are enough for the new
    fallback to find the conflict."""
    decision = {"action": "trade", "signal": {}, "reason": None}
    fake_strategy = _FakeStrategy(decision)
    monkeypatch.setattr(decision_bridge, "strategy", fake_strategy)
    _set_me_state(
        monkeypatch,
        market_titles={
            "BON": {"event_ticker": "EVT-1"},
            "BUS": {"event_ticker": "EVT-1"},
        },
        event_titles={"EVT-1": {"mutually_exclusive": True}},
        open_position_tickers={"BON"},
    )

    signal = _make_signal(id="bus1", ticker="BUS", side="yes")
    asyncio.run(decision_bridge._handle_signal(signal, _base_cfg(), {}, "", 0.0))

    assert fake_strategy.last_kwargs["me_complement"] == "BON"


def test_handle_signal_prefers_existing_me_pairs_hit_over_the_new_fallback(monkeypatch):
    """state["me_pairs"] (the existing, narrower mechanism) still wins when
    it already has an answer - the new check is a fallback, not a
    replacement, and must not override it."""
    decision = {"action": "trade", "signal": {}, "reason": None}
    fake_strategy = _FakeStrategy(decision)
    monkeypatch.setattr(decision_bridge, "strategy", fake_strategy)
    _set_me_state(
        monkeypatch,
        market_titles={"BUS": {"event_ticker": "EVT-1"}},  # no BON entry at all
        event_titles={"EVT-1": {"mutually_exclusive": True}},
        open_position_tickers=set(),  # nothing open - the new check alone would find nothing
        me_pairs={"BUS": "BON-FROM-OLD-MECHANISM"},
    )

    signal = _make_signal(id="bus2", ticker="BUS", side="yes")
    asyncio.run(decision_bridge._handle_signal(signal, _base_cfg(), {}, "", 0.0))

    assert fake_strategy.last_kwargs["me_complement"] == "BON-FROM-OLD-MECHANISM"


def test_handle_signal_me_complement_is_none_when_neither_mechanism_finds_a_conflict(monkeypatch):
    decision = {"action": "trade", "signal": {}, "reason": None}
    fake_strategy = _FakeStrategy(decision)
    monkeypatch.setattr(decision_bridge, "strategy", fake_strategy)
    _set_me_state(
        monkeypatch, market_titles={}, event_titles={}, open_position_tickers=set(),
    )

    signal = _make_signal(id="bus3", ticker="BUS", side="yes")
    asyncio.run(decision_bridge._handle_signal(signal, _base_cfg(), {}, "", 0.0))

    assert fake_strategy.last_kwargs["me_complement"] is None
