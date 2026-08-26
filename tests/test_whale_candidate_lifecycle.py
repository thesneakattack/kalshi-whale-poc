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

H4 FIXED (realtime data-plane remediation plan, P2 Task 11, 2026-08-26):
_process_trades_sync no longer marks a trade seen until its market lookup
has produced a definite outcome (found, or a genuine negative) - a
transient failure this round (services.whalewatchers.kalshi_trade_tape.
KalshiTradeTapeProvider._resolve_failed_tickers) leaves the trade
unmarked, so a later presentation of the same trade_id gets a real
chance once the lookup recovers. The dedupe model itself is unchanged
(plan I3: "Do not redesign the dedupe model in this task") - only WHEN a
trade_id enters it moved. The tests below now assert the fixed
behavior directly; the desired-behavior test that used to pin this as a
strict xfail now passes for real.
"""
import asyncio

import pytest

from services import candidate_ledger, candidate_log, candidate_retry, market_analyst_agent, market_history, series_evaluator, signal_log
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
    monkeypatch.setattr(candidate_ledger, "DB_PATH", tmp_path / "candidate_ledger.db")
    monkeypatch.setattr(candidate_retry, "_pending", {})


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


def test_transient_lookup_failure_leaves_the_trade_unmarked_pending_retry():
    """H4 FIXED (realtime data-plane remediation plan, P2 Task 11): a
    transient lookup failure no longer marks the trade seen - it stays
    candidate_pending, eligible for a later presentation, instead of being
    permanently short-circuited by the seen dedupe ring."""
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
    # mark_seen never fires for a transient miss - wire_seen=False,
    # candidate_pending=True (still resolvable on a later call), matching
    # the market_unresolved rejection row, which stays a diagnostic, not a
    # decision.
    assert order == []
    assert "whale-1" not in provider._seen_trade_ids
    assert _rejections(_OFFLIST) == ["market_unresolved"]


def test_re_presenting_the_same_trade_after_the_lookup_recovers_is_evaluated():
    """H4 FIXED: the same trade_id's second presentation is no longer
    dedupe-suppressed by _seen_trade_ids (it was never marked seen after
    the first, failed attempt) and reaches a real terminal evaluation once
    the lookup recovers."""
    provider = KalshiTradeTapeProvider()
    client = _FlakyClient(fail_first=1)
    assert _run(provider, client, _whale_print()) == []          # first pass: lookup fails, stays pending
    second = _run(provider, client, _whale_print())               # same trade_id again: lookup now succeeds
    assert client.calls == 2 and len(second) == 1 and second[0].id == "whale-1"
    assert "whale-1" in provider._seen_trade_ids  # now terminally evaluated and marked seen


def test_a_later_different_print_on_the_same_market_recovers_both_the_market_and_the_earlier_trade():
    """H4 FIXED: whale-1's earlier transient failure no longer permanently
    loses it - once the market resolves (triggered here by whale-2's
    print), whale-1's own next presentation reaches a real evaluation too."""
    provider = KalshiTradeTapeProvider()
    client = _FlakyClient(fail_first=1)
    assert _run(provider, client, _whale_print("whale-1")) == []
    later = _run(provider, client, _whale_print("whale-2"))
    assert client.calls == 2 and len(later) == 1 and later[0].id == "whale-2"
    # The market is now cached and whale-2 evaluated - whale-1's next
    # presentation is no longer lost either, since it was never marked seen:
    recovered = _run(provider, client, _whale_print("whale-1"))
    assert client.calls == 2  # market already cached - no new lookup needed
    assert len(recovered) == 1 and recovered[0].id == "whale-1"


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


# --- H4 fixed: the desired behavior, no longer an xfail ---------------------

def test_desired_a_transiently_failed_candidate_is_eventually_evaluated_when_the_lookup_recovers():
    provider = KalshiTradeTapeProvider()
    client = _FlakyClient(fail_first=1)
    first = _run(provider, client, _whale_print())
    second = _run(provider, client, _whale_print())
    assert len(first) + len(second) == 1  # evaluated exactly once, on whichever pass the context was available


# --- P2 Task 12: a transient lookup failure enqueues for retry --------------

def test_a_lookup_failure_enqueues_for_retry_instead_of_vanishing():
    provider = KalshiTradeTapeProvider()
    client = _FlakyClient(fail_first=1)
    assert _run(provider, client, _whale_print()) == []
    assert candidate_retry.pending_count() == 1
    assert "whale-1" in candidate_retry._pending
    assert candidate_retry._pending["whale-1"]["trade"]["ticker"] == _OFFLIST
