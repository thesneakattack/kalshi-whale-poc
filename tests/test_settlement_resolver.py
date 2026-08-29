"""P4 Task 19/24 (realtime-data-plane-remediation): the settled-market
resolver. The lifecycle `settled` branch used to await get_market() inline
on the serial WS consumer - measured at 23% of all REST demand (I8) and the
driver of the settlement-cascade drop episodes (71/81 started at :00-:09,
lifecycle handler window-max 19x elevated - 2026-08-29 correlation).
This module replaces it: enqueue at the handler, drain in batches after a
delay (docs/kalshi/market_lifecycle.md: a REST read at the instant `settled`
arrives can legitimately see a not-yet-finalized market, so delay + retry is
contractual), resolve through the same five stores the inline path fed.
Contract docs read for this file: docs/kalshi/get-markets.md,
docs/kalshi/rate_limits.md, docs/kalshi/market-and-event-lifecycle.md,
docs/kalshi/market_lifecycle.md."""
import asyncio
import time

import pytest

from services import settlement_resolver


@pytest.fixture(autouse=True)
def _clean_pending():
    settlement_resolver._pending.clear()
    yield
    settlement_resolver._pending.clear()


def _patch_all_resolvers(monkeypatch, recorded):
    """Patch the five resolver stores the inline settled branch fed - the
    drain loop must feed the same five (P7 Task 30 added signal_log; a
    resolver that fed fewer would silently narrow resolution coverage)."""
    monkeypatch.setattr(
        "services.market_history.record_outcome",
        lambda ticker, result, resolved_at: recorded.append(("market_history", ticker, result)))
    monkeypatch.setattr(
        "services.settlement_edge.resolve_window",
        lambda ticker, yes: recorded.append(("settlement_edge", ticker, "yes" if yes else "no")) or 1)
    monkeypatch.setattr(
        "services.market_analyst_agent.resolve_from_market_results",
        lambda results: recorded.append(("market_analyst_agent", *next(iter(results.items())))) or 1)
    monkeypatch.setattr(
        "services.candidate_log.resolve_from_market_results",
        lambda results: recorded.append(("candidate_log", *next(iter(results.items())))) or 1)
    monkeypatch.setattr(
        "services.signal_log.resolve_from_market_results",
        lambda ticker, result: recorded.append(("signal_log", ticker, result)) or 1)


class _FakeClient:
    def __init__(self, markets_by_ticker):
        self._markets = markets_by_ticker
        self.calls = []

    async def get_markets_by_tickers(self, tickers):
        self.calls.append(sorted(tickers))
        return {t: self._markets[t] for t in tickers if t in self._markets}


def test_a_settlement_younger_than_the_delay_is_not_yet_resolved():
    now = time.time()
    settlement_resolver.enqueue("K1", now, now=now)
    result = asyncio.run(settlement_resolver.run_pending(client=None, now=now + 10))
    assert result["still_pending"] == 1
    assert result["resolved"] == 0


def test_settlements_past_the_delay_are_batch_resolved(monkeypatch):
    recorded = []
    _patch_all_resolvers(monkeypatch, recorded)
    now = time.time()
    settlement_resolver.enqueue("K1", now, now=now)
    settlement_resolver.enqueue("K2", now, now=now)
    client = _FakeClient({
        "K1": {"ticker": "K1", "status": "finalized", "result": "yes"},
        "K2": {"ticker": "K2", "status": "finalized", "result": "no"},
    })

    result = asyncio.run(settlement_resolver.run_pending(client, now=now + 61.0))

    assert result["resolved"] == 2
    assert result["still_pending"] == 0
    assert client.calls == [["K1", "K2"]]  # ONE batched call, not two get_market()s
    stores_hit = {(store, ticker) for store, ticker, _ in recorded}
    for store in ("market_history", "settlement_edge", "market_analyst_agent", "candidate_log", "signal_log"):
        assert (store, "K1") in stores_hit and (store, "K2") in stores_hit
    assert ("market_history", "K2", "no") in recorded


def test_a_not_yet_finalized_market_stays_pending_and_is_retried_later(monkeypatch):
    # design spec §5 gap + market_lifecycle.md: settled != finalized
    # immediately (settlement-processing race); the ticker must survive to a
    # later run, then resolve when the REST read finally shows finalized.
    recorded = []
    _patch_all_resolvers(monkeypatch, recorded)
    now = time.time()
    settlement_resolver.enqueue("K3", now, now=now)

    still_determined = _FakeClient({"K3": {"ticker": "K3", "status": "determined"}})
    result = asyncio.run(settlement_resolver.run_pending(still_determined, now=now + 61.0))
    assert result["resolved"] == 0
    assert result["still_pending"] == 1
    assert recorded == []

    finalized = _FakeClient({"K3": {"ticker": "K3", "status": "finalized", "result": "yes"}})
    result = asyncio.run(settlement_resolver.run_pending(finalized, now=now + 122.0))
    assert result["resolved"] == 1
    assert result["still_pending"] == 0
    assert ("market_history", "K3", "yes") in recorded


def test_enqueueing_the_same_ticker_twice_resolves_it_once(monkeypatch):
    recorded = []
    _patch_all_resolvers(monkeypatch, recorded)
    now = time.time()
    settlement_resolver.enqueue("K1", now, now=now)
    settlement_resolver.enqueue("K1", now + 1, now=now + 1)
    client = _FakeClient({"K1": {"ticker": "K1", "status": "finalized", "result": "yes"}})

    result = asyncio.run(settlement_resolver.run_pending(client, now=now + 62.0))

    assert result["resolved"] == 1
    assert client.calls == [["K1"]]
    assert [r for r in recorded if r[0] == "market_history"] == [("market_history", "K1", "yes")]


def test_a_ticker_kalshi_never_returns_is_dropped_after_max_attempts(monkeypatch):
    # get_markets_by_tickers: "a ticker Kalshi doesn't return (renamed/
    # deleted) just isn't in the result" - without a bound it would retry
    # forever and _pending would grow for the life of the process.
    recorded = []
    _patch_all_resolvers(monkeypatch, recorded)
    now = time.time()
    settlement_resolver.enqueue("GONE", now, now=now)
    client = _FakeClient({})

    for attempt in range(1, 4):
        result = asyncio.run(settlement_resolver.run_pending(
            client, now=now + 61.0 * attempt, max_attempts=3))
    assert result["still_pending"] == 0
    assert recorded == []


def test_a_non_yes_no_result_is_dropped_not_retried(monkeypatch):
    # The inline path gave up on a scalar result (result not in yes/no);
    # retrying it would never change the answer.
    recorded = []
    _patch_all_resolvers(monkeypatch, recorded)
    now = time.time()
    settlement_resolver.enqueue("SCALAR", now, now=now)
    client = _FakeClient({"SCALAR": {"ticker": "SCALAR", "status": "finalized", "result": "scalar"}})

    result = asyncio.run(settlement_resolver.run_pending(client, now=now + 61.0))

    assert result["resolved"] == 0
    assert result["still_pending"] == 0
    assert recorded == []


def test_a_batch_larger_than_batch_size_is_split_across_runs(monkeypatch):
    recorded = []
    _patch_all_resolvers(monkeypatch, recorded)
    now = time.time()
    for i in range(3):
        settlement_resolver.enqueue(f"K{i}", now, now=now)
    client = _FakeClient({f"K{i}": {"ticker": f"K{i}", "status": "finalized", "result": "yes"} for i in range(3)})

    result = asyncio.run(settlement_resolver.run_pending(client, now=now + 61.0, batch_size=2))
    assert result["resolved"] == 2
    assert result["still_pending"] == 1
    assert len(client.calls[0]) == 2

    result = asyncio.run(settlement_resolver.run_pending(client, now=now + 62.0, batch_size=2))
    assert result["resolved"] == 1
    assert result["still_pending"] == 0


def test_a_failed_batch_read_leaves_everything_pending(monkeypatch):
    recorded = []
    _patch_all_resolvers(monkeypatch, recorded)
    now = time.time()
    settlement_resolver.enqueue("K1", now, now=now)

    class _RaisingClient:
        async def get_markets_by_tickers(self, tickers):
            raise RuntimeError("network error")

    result = asyncio.run(settlement_resolver.run_pending(_RaisingClient(), now=now + 61.0))
    assert result["resolved"] == 0
    assert result["still_pending"] == 1  # a transient network error must not lose the ticker
    assert recorded == []
