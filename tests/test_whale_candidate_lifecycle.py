"""Whale-candidate lifecycle under transient enrichment failure (realtime
data-plane investigation task I3 - hypothesis H4 in
docs/superpowers/research/2026-08-25-realtime-data-plane-known-findings.md).

The sequence under test, exactly as the findings file states it:

    whale-sized trade on a market outside the watchlist arrives
    -> candidate (clears the contract-count prescan)
    -> REST market lookup (get_markets_by_tickers) raises a transient error
    -> is the trade marked seen?
    -> is a later retry / re-presentation ever evaluated?

Three observable lifecycle states are distinguished from the provider's own
state, not from log text:

    wire_seen           trade_id is in _seen_trade_ids (dedupe ring)
    candidate_pending   ticker is still resolvable on a later call
                        (no negative cache entry, no seen-mark)
    terminal_evaluated  a WhaleSignal was emitted, or a real gate
                        (min_contracts / price range / ...) rejected it

These tests characterize CURRENT behavior. The dedupe model is deliberately
not redesigned here (plan I3: "Do not redesign the dedupe model in this
task"); the strict-xfail test at the bottom pins the desired behavior so a
future fix flips it visibly instead of silently.
"""
import asyncio

import pytest

from services import candidate_log, market_analyst_agent, market_history, series_evaluator, signal_log
from services.market_analyst_agent import _db as maa_db_module
from services.whalewatchers.kalshi_trade_tape import KalshiTradeTapeProvider

pytestmark = pytest.mark.usefixtures("_isolated_dbs")


@pytest.fixture
def _isolated_dbs(tmp_path, monkeypatch):
    monkeypatch.setattr(signal_log, "DB_PATH", tmp_path / "signal_log.db")
    monkeypatch.setattr(market_history, "DB_PATH", tmp_path / "market_history.db")
    monkeypatch.setattr(maa_db_module, "DB_PATH", tmp_path / "market_analyst.db")
    monkeypatch.setattr(series_evaluator, "DB_PATH", tmp_path / "series_evaluator.db")
    monkeypatch.setattr(candidate_log, "DB_PATH", tmp_path / "candidate_log.db")


_CFG = {"whale_watcher_kalshi": {"min_contracts": 50}}
_OFFLIST = "KXOFFLIST-26AUG25-X"


def _whale_print(trade_id="whale-1"):
    return {
        "trade_id": trade_id, "ticker": _OFFLIST, "count_fp": "5000.00",
        "yes_price_dollars": "0.6000", "no_price_dollars": "0.4000",
        "taker_outcome_side": "yes", "created_time": "2026-08-25T19:00:00Z",
    }


class _FlakyClient:
    """First lookup fails (a 429/timeout-equivalent); every later lookup
    succeeds and returns a real market for the ticker."""

    def __init__(self, fail_first: int = 1):
        self.calls = 0
        self._fail_first = fail_first

    async def get_markets_by_tickers(self, tickers):
        self.calls += 1
        if self.calls <= self._fail_first:
            raise RuntimeError("transient: 429 Too Many Requests")
        return {t: {"ticker": t, "volume_24h_fp": "20000", "yes_ask_dollars": "0.61"} for t in tickers}


def _run(provider, client, trade):
    return asyncio.run(provider.fetch_signals(market_context={
        "markets": [], "trade_tape": [trade], "cfg": _CFG, "client": client,
    }))


def _rejections(ticker):
    if not candidate_log.DB_PATH.exists():
        return []
    import sqlite3
    with sqlite3.connect(candidate_log.DB_PATH) as conn:
        return [r[0] for r in conn.execute(
            "SELECT gate_name FROM rejected_candidates WHERE ticker = ?", (ticker,)).fetchall()]


# --- characterization of the current mechanism ----------------------------

def test_control_a_whale_print_whose_lookup_succeeds_is_terminally_evaluated():
    provider = KalshiTradeTapeProvider()
    signals = _run(provider, _FlakyClient(fail_first=0), _whale_print())
    assert len(signals) == 1 and signals[0].ticker == _OFFLIST  # terminal_evaluated: signal emitted
    assert "whale-1" in provider._seen_trade_ids  # wire_seen


def test_transient_lookup_failure_marks_the_trade_seen_before_any_evaluation():
    provider = KalshiTradeTapeProvider()
    client = _FlakyClient(fail_first=1)
    order = []
    real_mark_seen = provider._mark_seen

    def spy_mark_seen(trade_id, exchange_ts=None):
        order.append(("mark_seen", trade_id, client.calls))
        real_mark_seen(trade_id, exchange_ts=exchange_ts)

    provider._mark_seen = spy_mark_seen

    signals = _run(provider, client, _whale_print())

    assert signals == []
    assert client.calls == 1  # the lookup was attempted once and raised
    assert provider.stats["resolve_failures"] == 1
    # _mark_seen fired AFTER the failed lookup (calls==1 at that moment) and
    # with no evaluation possible (no market) - the trade is wire_seen but
    # was never terminal_evaluated: its only record is a market_unresolved
    # rejection row, which is a diagnostic, not a decision.
    assert order == [("mark_seen", "whale-1", 1)]
    assert "whale-1" in provider._seen_trade_ids
    assert _rejections(_OFFLIST) == ["market_unresolved"]


def test_re_presenting_the_same_trade_after_the_lookup_recovers_is_ignored_by_dedupe():
    provider = KalshiTradeTapeProvider()
    client = _FlakyClient(fail_first=1)
    assert _run(provider, client, _whale_print()) == []       # first pass: lookup fails
    assert _run(provider, client, _whale_print()) == []       # same trade_id again: lookup would now succeed
    assert client.calls == 1  # ...but the dedupe ring short-circuits before any lookup is even attempted
    assert _OFFLIST not in provider._market_cache  # nothing was cached on failure, so the market is still unknown
    # Lifecycle verdict for this trade_id: wire_seen=True, candidate_pending=False
    # (nothing will ever look it up again), terminal_evaluated=False.


def test_a_later_different_print_on_the_same_market_does_recover_the_market_but_not_the_lost_trade():
    provider = KalshiTradeTapeProvider()
    client = _FlakyClient(fail_first=1)
    assert _run(provider, client, _whale_print("whale-1")) == []
    later = _run(provider, client, _whale_print("whale-2"))
    assert client.calls == 2 and len(later) == 1 and later[0].id == "whale-2"
    # The market is now cached and whale-2 evaluated - but whale-1 stays lost:
    assert _run(provider, client, _whale_print("whale-1")) == []
    assert client.calls == 2


def test_a_negative_cache_entry_suppresses_lookups_for_the_ttl_even_though_the_trade_was_new():
    # A *successful* batch that omits the ticker (Kalshi returned nothing for
    # it) is cached as None for _MARKET_CACHE_TTL_SEC, so a whale print that
    # arrives inside that window is rejected as market_unresolved with no
    # lookup at all - the same terminal outcome via a second path.
    class _EmptyClient:
        def __init__(self):
            self.calls = 0

        async def get_markets_by_tickers(self, tickers):
            self.calls += 1
            return {}

    provider = KalshiTradeTapeProvider()
    client = _EmptyClient()
    assert _run(provider, client, _whale_print("whale-1")) == []
    assert _run(provider, client, _whale_print("whale-2")) == []
    assert client.calls == 1  # second whale print never looked up: negative cache
    assert _rejections(_OFFLIST) == ["market_unresolved"]  # deduped row (ON CONFLICT) - both trades ended here


# --- the desired behavior, pinned as a strict xfail until a fix lands -------

@pytest.mark.xfail(
    strict=True,
    reason="H4 proven defect (I3): a whale-sized off-watchlist print whose first market lookup "
           "fails transiently is marked seen before any evaluation and is never retried, so it "
           "can never reach a terminal decision. Fix belongs to the remediation plan, not I3.",
)
def test_desired_a_transiently_failed_candidate_is_eventually_evaluated_when_the_lookup_recovers():
    provider = KalshiTradeTapeProvider()
    client = _FlakyClient(fail_first=1)
    first = _run(provider, client, _whale_print())
    second = _run(provider, client, _whale_print())
    assert len(first) + len(second) == 1  # evaluated exactly once, on whichever pass the context was available
