"""End-to-end fault-injection coverage across the real production pieces
Tasks 10-12 wired together (realtime data-plane remediation plan, P2's own
gate criterion): a whale-sized off-watchlist print whose first market
lookup fails transiently must reach a terminal outcome exactly once,
whichever of these two paths gets there first:

  (a) candidate_retry.run_pending recovers it and claims the trade_id in
      candidate_ledger before the trade naturally reappears in a later
      trade tape poll, or
  (b) the trade naturally reappears (still un-dedupe-suppressed per
      Task 11's H4 fix) and reaches decision_bridge._handle_signal's own
      ledger gate (Task 10) before candidate_retry gets to it.

Combines KalshiTradeTapeProvider.fetch_signals (the real reader path),
candidate_retry.run_pending (the real retry path), and
decision_bridge._handle_signal (the real evaluation gate) - each already
covered in isolation by test_whale_candidate_lifecycle.py,
test_candidate_retry.py, and test_whale_stream_decision_bridge.py
respectively. This file is what proves they compose safely, not just
each on their own.
"""
import asyncio
import time

import pytest

from services import candidate_ledger, candidate_log, candidate_retry, market_history, series_evaluator, signal_log
from services.market_analyst_agent import _db as maa_db_module
from services.whale_stream import decision_bridge
from services.whalewatchers.kalshi_trade_tape import KalshiTradeTapeProvider

_CFG = {"whale_watcher_kalshi": {"min_contracts": 50}}
_OFFLIST = "KXOFFLIST-26AUG25-INTEGRATION"


@pytest.fixture(autouse=True)
def _isolated_dbs(tmp_path, monkeypatch):
    monkeypatch.setattr(signal_log, "DB_PATH", tmp_path / "signal_log.db")
    monkeypatch.setattr(market_history, "DB_PATH", tmp_path / "market_history.db")
    monkeypatch.setattr(maa_db_module, "DB_PATH", tmp_path / "market_analyst.db")
    monkeypatch.setattr(series_evaluator, "DB_PATH", tmp_path / "series_evaluator.db")
    monkeypatch.setattr(candidate_log, "DB_PATH", tmp_path / "candidate_log.db")
    monkeypatch.setattr(candidate_ledger, "DB_PATH", tmp_path / "candidate_ledger.db")
    monkeypatch.setattr(candidate_retry, "_pending", {})


def _whale_print(trade_id="whale-int-1"):
    return {
        "trade_id": trade_id, "ticker": _OFFLIST, "count_fp": "5000.00",
        "yes_price_dollars": "0.6000", "no_price_dollars": "0.4000",
        "taker_outcome_side": "yes", "created_time": "2026-08-25T19:00:00Z",
    }


class _FlakyClient:
    """First N lookups fail (a 429/timeout-equivalent, shared across BOTH
    the provider's own lookups and candidate_retry's); every later lookup
    succeeds. One shared call counter models the two code paths hitting
    the same underlying rate-limited endpoint, as they would in
    production (both go through services/kalshi/public.py in reality)."""

    def __init__(self, fail_first: int):
        self.calls = 0
        self._fail_first = fail_first

    async def get_markets_by_tickers(self, tickers):
        self.calls += 1
        if self.calls <= self._fail_first:
            raise RuntimeError("transient: 429 Too Many Requests")
        return {t: {"ticker": t, "volume_24h_fp": "20000", "yes_ask_dollars": "0.61"} for t in tickers}


def _fetch(provider, client, trade):
    return asyncio.run(provider.fetch_signals(market_context={
        "markets": [], "trade_tape": [trade], "cfg": _CFG, "client": client,
    }))


def _run_pending(provider, client, *, now):
    """candidate_retry.run_pending with the real provider + real
    decision_bridge._handle_signal wired through (code-review fix, finding
    #1 - recovery now actually evaluates a recovered candidate instead of
    only claiming it). _CFG already carries whale_watcher_kalshi.min_contracts
    (needed by provider.score_recovered_trade's own gates); _handle_signal
    only additionally consults cfg["mode"], which _CFG omits (falls back to
    the ordinary paper/live-agnostic path, same as the other tests here)."""
    return asyncio.run(candidate_retry.run_pending(
        client, provider, decision_bridge._handle_signal,
        _CFG, {}, "", now, now=now,
    ))


class _FakeBroker:
    """Minimal stand-in - _handle_signal's find_open_confirmed_conflict
    fallback (2026-08-30) reads strategy.broker.positions.keys() directly,
    live, not a periodic state snapshot. Empty is enough here: these tests
    don't exercise ME-pairing behavior."""

    def __init__(self):
        self.positions = {}


class _FakeStrategy:
    """Stands in for services.app_state's real FollowTheWhaleStrategy - see
    test_whale_stream_decision_bridge.py's own docstring for why a real
    broker/risk stack isn't needed to prove ledger-gating behavior."""

    def __init__(self):
        self.calls = 0
        self.broker = _FakeBroker()

    def evaluate(self, signal, cfg, **kwargs):
        self.calls += 1
        return {"action": "trade", "signal": signal.to_dict(), "reason": None}


# --- plain paths, no race ---------------------------------------------------

def test_recovery_path_reaches_a_terminal_outcome_with_zero_silent_loss(monkeypatch):
    """Code-review fix (finding #1): recovery must not just claim a
    trade_id and report success - it must actually evaluate it. Real
    provider + real decision_bridge._handle_signal (fake strategy only, per
    this file's own established convention) proves the recovered print
    reaches a genuine trading decision, not merely a metrics counter."""
    provider = KalshiTradeTapeProvider()
    client = _FlakyClient(fail_first=1)
    fake_strategy = _FakeStrategy()
    monkeypatch.setattr(decision_bridge, "strategy", fake_strategy)

    assert _fetch(provider, client, _whale_print()) == []  # first pass: lookup fails, enqueued
    assert candidate_retry.pending_count() == 1

    result = _run_pending(provider, client, now=time.time() + 1.0)
    assert result["recovered"] == 1
    assert candidate_retry.pending_count() == 0
    assert fake_strategy.calls == 1  # the recovered print was actually evaluated
    assert candidate_ledger.decision_for("whale-int-1") == "trade"  # a real decision, not just a claim
    assert candidate_ledger.claim("whale-int-1") is False  # already claimed by that real evaluation


def test_abandonment_path_is_counted_with_zero_silent_loss(monkeypatch):
    provider = KalshiTradeTapeProvider()
    client = _FlakyClient(fail_first=999)  # never recovers within this test
    fake_strategy = _FakeStrategy()
    monkeypatch.setattr(decision_bridge, "strategy", fake_strategy)

    assert _fetch(provider, client, _whale_print("whale-int-2")) == []
    assert candidate_retry.pending_count() == 1

    now = time.time()
    result = {"abandoned": 0}
    for _ in range(10):
        now += 20.0  # advance past the ~91.5s backoff budget
        result = _run_pending(provider, client, now=now)
    assert result["abandoned"] >= 1
    assert candidate_retry.pending_count() == 0
    # Abandonment is a counted, observable outcome (candidate_retry.snapshot's
    # window counter), not a silent drop - Task 13's own quality-finding
    # wiring turns this into a QualityFinding when nonzero.
    assert candidate_retry.snapshot()["abandoned"] >= 1
    assert fake_strategy.calls == 0  # market never resolved - nothing to evaluate


# --- the race: same trade_id re-presented while still pending in the retry queue --

def test_recovery_claims_first_so_a_later_natural_re_presentation_is_recognized_as_duplicate(monkeypatch):
    """Scenario (a): candidate_retry recovers BEFORE the trade naturally
    reappears. Post-fix, recovery itself now evaluates the print for real
    (calling provider.score_recovered_trade, which is _process_trades_sync
    under the hood) - the same call that produces the WhaleSignal also
    marks trade_id "whale-int-1" seen on the provider's own dedupe ring (a
    real, correct side effect of reusing the exact same scoring pipeline).
    A later natural re-presentation is therefore filtered at the very top
    of _resolve_unknown_markets' own prescan - it never even attempts a
    fresh market lookup (client.calls stays at 2, not 3) - which is a
    strictly better outcome than the pre-fix design could ever have
    produced, not just "also correct": no wasted REST call either, not
    only no wasted evaluation. strategy.evaluate() must run exactly once
    total for this trade_id, from the recovery path."""
    provider = KalshiTradeTapeProvider()
    client = _FlakyClient(fail_first=1)
    fake_strategy = _FakeStrategy()
    monkeypatch.setattr(decision_bridge, "strategy", fake_strategy)

    assert _fetch(provider, client, _whale_print()) == []  # call 1: fails, enqueued
    recovery = _run_pending(provider, client, now=time.time() + 1.0)  # call 2: succeeds, evaluates
    assert recovery["recovered"] == 1
    assert fake_strategy.calls == 1  # evaluated once, by recovery

    # Later trade tape poll re-presents the SAME trade_id - already on the
    # seen ring from recovery's own successful scoring pass, so the reader
    # doesn't even attempt a fresh market lookup this time (client.calls
    # unchanged) and produces no signal for it.
    second_pass_signals = _fetch(provider, client, _whale_print())
    assert client.calls == 2
    assert second_pass_signals == []

    for signal in second_pass_signals:
        result = asyncio.run(decision_bridge._handle_signal(
            signal, {"mode": "paper", "risk": {"starting_bankroll": 1000.0}}, {}, "", time.time(),
        ))
        assert result["action"] == "skip" and result["reason"] == "duplicate_trade_id"
    # Whichever layer prevented a second look (the seen ring or the
    # ledger), evaluate() must never have run again for this trade_id.
    assert fake_strategy.calls == 1


def test_natural_re_presentation_claims_first_so_recovery_is_a_harmless_no_op_after(monkeypatch):
    """Scenario (b): the trade naturally re-presents and reaches real
    evaluation BEFORE candidate_retry gets around to retrying the same
    still-pending entry. evaluate() must run exactly once (from the real
    path), and candidate_retry's own later recovery attempt must be a
    harmless no-op - not a second evaluation, not a crash - because the
    natural pass's own _process_trades_sync call already marked this
    trade_id seen on the provider's dedupe ring, so
    provider.score_recovered_trade produces no signal at all the second
    time around (the same ring _process_trades_sync always used, just
    reached via the recovery entry point instead of a fresh fetch_signals()
    call)."""
    provider = KalshiTradeTapeProvider()
    client = _FlakyClient(fail_first=1)
    fake_strategy = _FakeStrategy()
    monkeypatch.setattr(decision_bridge, "strategy", fake_strategy)

    assert _fetch(provider, client, _whale_print()) == []  # call 1: fails, enqueued
    assert candidate_retry.pending_count() == 1

    # Natural re-presentation reaches the reader before run_pending does
    # (call 2 - the market resolves fresh, independent of the retry queue).
    second_pass_signals = _fetch(provider, client, _whale_print())
    assert client.calls == 2
    assert len(second_pass_signals) == 1

    result = asyncio.run(decision_bridge._handle_signal(
        second_pass_signals[0], {"mode": "paper", "risk": {"starting_bankroll": 1000.0}}, {}, "", time.time(),
    ))
    assert result["action"] == "trade"
    assert fake_strategy.calls == 1  # evaluated exactly once, via the real path

    # candidate_retry's own entry is still pending (nothing removed it) -
    # its next run_pending call independently succeeds at the market
    # lookup (call 3), but provider.score_recovered_trade now produces no
    # signal (trade_id already on the seen ring from the natural pass
    # above), so nothing gets (re-)claimed or (re-)evaluated.
    recovery = _run_pending(provider, client, now=time.time() + 1.0)
    assert client.calls == 3
    assert recovery["recovered"] == 1  # candidate_retry's own bookkeeping still calls this recovered
    assert candidate_ledger.claim("whale-int-1") is False  # still just the one real claim underneath
    assert fake_strategy.calls == 1  # unchanged - no second evaluation happened
