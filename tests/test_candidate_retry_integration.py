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


class _FakeStrategy:
    """Stands in for services.app_state's real FollowTheWhaleStrategy - see
    test_whale_stream_decision_bridge.py's own docstring for why a real
    broker/risk stack isn't needed to prove ledger-gating behavior."""

    def __init__(self):
        self.calls = 0

    def evaluate(self, signal, cfg, **kwargs):
        self.calls += 1
        return {"action": "trade", "signal": signal.to_dict(), "reason": None}


# --- plain paths, no race ---------------------------------------------------

def test_recovery_path_reaches_a_terminal_outcome_with_zero_silent_loss():
    provider = KalshiTradeTapeProvider()
    client = _FlakyClient(fail_first=1)

    assert _fetch(provider, client, _whale_print()) == []  # first pass: lookup fails, enqueued
    assert candidate_retry.pending_count() == 1

    result = asyncio.run(candidate_retry.run_pending(client, now=time.time() + 1.0))
    assert result["recovered"] == 1
    assert candidate_retry.pending_count() == 0
    assert candidate_ledger.claim("whale-int-1") is False  # claimed by recovery


def test_abandonment_path_is_counted_with_zero_silent_loss():
    provider = KalshiTradeTapeProvider()
    client = _FlakyClient(fail_first=999)  # never recovers within this test

    assert _fetch(provider, client, _whale_print("whale-int-2")) == []
    assert candidate_retry.pending_count() == 1

    now = time.time()
    result = {"abandoned": 0}
    for _ in range(10):
        now += 20.0  # advance past the ~91.5s backoff budget
        result = asyncio.run(candidate_retry.run_pending(client, now=now))
    assert result["abandoned"] >= 1
    assert candidate_retry.pending_count() == 0
    # Abandonment is a counted, observable outcome (candidate_retry.snapshot's
    # window counter), not a silent drop - Task 13's own quality-finding
    # wiring turns this into a QualityFinding when nonzero.
    assert candidate_retry.snapshot()["abandoned"] >= 1


# --- the race: same trade_id re-presented while still pending in the retry queue --

def test_recovery_claims_first_so_a_later_natural_re_presentation_is_recognized_as_duplicate(monkeypatch):
    """Scenario (a): candidate_retry recovers and claims the ledger BEFORE
    the trade naturally reappears. The later re-presentation must reach
    decision_bridge._handle_signal's gate and be skipped as a duplicate -
    strategy.evaluate() must never run for it."""
    provider = KalshiTradeTapeProvider()
    client = _FlakyClient(fail_first=1)
    fake_strategy = _FakeStrategy()
    monkeypatch.setattr(decision_bridge, "strategy", fake_strategy)

    assert _fetch(provider, client, _whale_print()) == []  # call 1: fails, enqueued
    recovery = asyncio.run(candidate_retry.run_pending(client, now=time.time() + 1.0))  # call 2: succeeds, claims
    assert recovery["recovered"] == 1

    # Later trade tape poll re-presents the SAME trade_id - still not in
    # _seen_trade_ids (Task 11's fix never marked it), so the reader
    # processes it again for real, and this time the market resolves
    # (client call 3 succeeds - _market_cache was never populated by
    # candidate_retry's own lookup, so the reader does a fresh one).
    second_pass_signals = _fetch(provider, client, _whale_print())
    assert client.calls == 3

    for signal in second_pass_signals:
        result = asyncio.run(decision_bridge._handle_signal(
            signal, {"mode": "paper", "risk": {"starting_bankroll": 1000.0}}, {}, "", time.time(),
        ))
        assert result["action"] == "skip" and result["reason"] == "duplicate_trade_id"
    # Whether or not the reader itself produced a signal this pass (it
    # may, if the print alone clears every real gate), evaluate() must
    # never have run for this trade_id - the ledger already owns it.
    assert fake_strategy.calls == 0


def test_natural_re_presentation_claims_first_so_recovery_is_a_harmless_no_op_after(monkeypatch):
    """Scenario (b): the trade naturally re-presents and reaches real
    evaluation BEFORE candidate_retry gets around to retrying the same
    still-pending entry. evaluate() must run exactly once (from the real
    path), and candidate_retry's own later claim attempt (idempotent
    INSERT OR IGNORE) must not cause a second evaluation or raise."""
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
    # its next run_pending call independently succeeds (call 3) and calls
    # candidate_ledger.claim() again, which is a harmless idempotent
    # no-op (INSERT OR IGNORE returns False - already claimed).
    recovery = asyncio.run(candidate_retry.run_pending(client, now=time.time() + 1.0))
    assert client.calls == 3
    assert recovery["recovered"] == 1  # candidate_retry's own bookkeeping still calls this recovered
    assert candidate_ledger.claim("whale-int-1") is False  # still just the one real claim underneath
    assert fake_strategy.calls == 1  # unchanged - no second evaluation happened
