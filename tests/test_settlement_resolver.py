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
    settlement_resolver._non_binary_by_result.clear()
    settlement_resolver._non_binary_recent.clear()
    yield
    settlement_resolver._pending.clear()
    settlement_resolver._non_binary_by_result.clear()
    settlement_resolver._non_binary_recent.clear()


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


def test_a_raising_store_resolver_is_contained_retried_and_eventually_dropped(monkeypatch):
    # Review finding (PR #198): a raising store must not crash run_pending -
    # that would crash-loop the supervised scheduler and permanently starve
    # every settlement enqueued behind the poisoned ticker.
    recorded = []
    _patch_all_resolvers(monkeypatch, recorded)
    def _poisoned_record(ticker, result, resolved_at):
        if ticker == "POISON":
            raise RuntimeError("db locked")
        recorded.append(("market_history", ticker, result))
    monkeypatch.setattr("services.market_history.record_outcome", _poisoned_record)
    faults = []
    monkeypatch.setattr("services.fault_log.record", lambda *a, **k: faults.append(a) or True)
    monkeypatch.setattr("services.fault_log.record_fault", lambda *a, **k: faults.append(a) or True)
    now = time.time()
    settlement_resolver.enqueue("POISON", now, now=now)
    settlement_resolver.enqueue("OK", now, now=now)
    client = _FakeClient({
        "POISON": {"ticker": "POISON", "status": "finalized", "result": "yes"},
        "OK": {"ticker": "OK", "status": "finalized", "result": "no"},
    })

    result = asyncio.run(settlement_resolver.run_pending(client, now=now + 61.0, max_attempts=2))

    # The healthy ticker in the same batch still resolves.
    assert result["resolved"] == 1
    assert ("signal_log", "OK", "no") in recorded
    assert result["still_pending"] == 1  # POISON retried, not lost, not crashing
    assert any("resolve:POISON" in str(f) for f in faults)

    # Second failure hits max_attempts=2: dropped, counted, fault-logged.
    before_dropped = settlement_resolver.snapshot()["dropped_total"]
    result = asyncio.run(settlement_resolver.run_pending(client, now=now + 122.0, max_attempts=2))
    assert result["still_pending"] == 0
    assert settlement_resolver.snapshot()["dropped_total"] == before_dropped + 1
    assert any("dropped_after_max_attempts" in str(f) for f in faults)


def test_a_raising_candidate_log_resolver_does_not_block_other_tickers(monkeypatch):
    """Same containment guarantee as
    test_a_raising_store_resolver_is_contained_retried_and_eventually_dropped
    above, exercised specifically through candidate_log - issue #601 / PR
    #603's Family-2 fix touched only services/candidate_log.py's
    resolve_from_market_results (a ticker-scoped direct UPDATE replacing an
    unfiltered full-table-scanning SELECT); this confirms that fix left
    run_pending()'s per-ticker isolation untouched. That isolation lives
    entirely in settlement_resolver.py's own try/except around each
    ticker's tick_executor.run(_resolve_one_sync) call (module docstring:
    "a store resolver that raises is contained to its own ticker... must
    never crash the loop") - it does not depend on anything internal to
    candidate_log's query shape, so this must hold regardless of which of
    the five stores is the one that raises."""
    recorded = []
    _patch_all_resolvers(monkeypatch, recorded)

    def _poisoned_candidate_log(results):
        ticker, result = next(iter(results.items()))
        if ticker == "POISON":
            raise RuntimeError("db locked")
        recorded.append(("candidate_log", ticker, result))
        return 1

    monkeypatch.setattr("services.candidate_log.resolve_from_market_results", _poisoned_candidate_log)
    faults = []
    monkeypatch.setattr("services.fault_log.record", lambda *a, **k: faults.append(a) or True)
    monkeypatch.setattr("services.fault_log.record_fault", lambda *a, **k: faults.append(a) or True)
    now = time.time()
    settlement_resolver.enqueue("POISON", now, now=now)
    settlement_resolver.enqueue("OK", now, now=now)
    client = _FakeClient({
        "POISON": {"ticker": "POISON", "status": "finalized", "result": "yes"},
        "OK": {"ticker": "OK", "status": "finalized", "result": "no"},
    })

    result = asyncio.run(settlement_resolver.run_pending(client, now=now + 61.0, max_attempts=2))

    # The healthy ticker in the same batch still resolves even though
    # candidate_log raised for POISON.
    assert result["resolved"] == 1
    assert ("candidate_log", "OK", "no") in recorded
    assert result["still_pending"] == 1  # POISON retried, not lost, not crashing
    assert any("resolve:POISON" in str(f) for f in faults)


def test_retries_are_spaced_by_delay_sec_not_by_caller_cadence(monkeypatch):
    # Review finding (PR #198): without not_before, a 5s scheduler loop
    # burned the whole max_attempts budget in ~50s of wall clock. An
    # attempt must only be consumed after delay_sec has re-elapsed.
    recorded = []
    _patch_all_resolvers(monkeypatch, recorded)
    now = time.time()
    settlement_resolver.enqueue("K1", now, now=now)
    client = _FakeClient({"K1": {"ticker": "K1", "status": "determined"}})

    asyncio.run(settlement_resolver.run_pending(client, now=now + 61.0))  # attempt 1
    for tick in (66.0, 71.0, 100.0):  # scheduler keeps firing every few seconds
        asyncio.run(settlement_resolver.run_pending(client, now=now + tick))

    assert settlement_resolver._pending["K1"]["attempts"] == 1  # backoff held; cadence didn't burn budget
    asyncio.run(settlement_resolver.run_pending(client, now=now + 122.0))  # delay re-elapsed
    assert settlement_resolver._pending["K1"]["attempts"] == 2


# --- issue #208: the drop counter conflated a defect with expected skips ---
# `dropped_total` was bumped by two opposite branches: retry exhaustion (a
# real completeness defect) and a finalized market with no binary outcome
# (correct behaviour). All 64 drops observed live on 2026-08-30 came from
# the second - fault_log held zero settlement_resolver rows over 8 days of
# retention, and the retry branch always fault-logs. Contract read for
# these tests: docs/kalshi/market_lifecycle.md:68 (`result` is yes | no |
# scalar) and docs/kalshi/changelog-index.md:3245-3246 (a scalar-settled
# market currently returns "" and will read "scalar" after the next
# release - so both spellings must stay distinguishable).


def test_a_max_attempts_giveup_counts_as_a_defect_not_a_skip(monkeypatch):
    recorded = []
    _patch_all_resolvers(monkeypatch, recorded)
    faults = []
    monkeypatch.setattr("services.fault_log.record_fault", lambda *a, **k: faults.append(a) or True)
    before = settlement_resolver.snapshot()
    now = time.time()
    settlement_resolver.enqueue("GONE", now, now=now)
    client = _FakeClient({})

    for attempt in range(1, 4):
        asyncio.run(settlement_resolver.run_pending(
            client, now=now + 61.0 * attempt, max_attempts=3))

    after = settlement_resolver.snapshot()
    assert after["dropped_after_max_attempts"] == before["dropped_after_max_attempts"] + 1
    assert after["skipped_non_binary_result"] == before["skipped_non_binary_result"]
    assert any("dropped_after_max_attempts" in str(f) for f in faults)


def test_a_non_binary_result_counts_as_a_skip_not_a_defect(monkeypatch):
    recorded = []
    _patch_all_resolvers(monkeypatch, recorded)
    faults = []
    monkeypatch.setattr("services.fault_log.record_fault", lambda *a, **k: faults.append(a) or True)
    monkeypatch.setattr("services.fault_log.record", lambda *a, **k: faults.append(a) or True)
    before = settlement_resolver.snapshot()
    now = time.time()
    settlement_resolver.enqueue("SCALAR", now, now=now)
    client = _FakeClient({"SCALAR": {"ticker": "SCALAR", "status": "finalized", "result": "scalar"}})

    asyncio.run(settlement_resolver.run_pending(client, now=now + 61.0))

    after = settlement_resolver.snapshot()
    assert after["skipped_non_binary_result"] == before["skipped_non_binary_result"] + 1
    assert after["dropped_after_max_attempts"] == before["dropped_after_max_attempts"]
    # Expected behaviour is not a fault: nothing is written to fault_log.
    assert faults == []


def test_dropped_total_stays_the_sum_so_the_conservation_identity_holds(monkeypatch):
    """tools/soak_analyzer.check_resolver_accounting balances
    enqueued == resolved + pending + dropped_total. Splitting the counter
    must not break that, so dropped_total keeps counting both branches."""
    recorded = []
    _patch_all_resolvers(monkeypatch, recorded)
    monkeypatch.setattr("services.fault_log.record_fault", lambda *a, **k: True)
    before = settlement_resolver.snapshot()
    now = time.time()
    settlement_resolver.enqueue("SCALAR", now, now=now)
    settlement_resolver.enqueue("GONE", now, now=now)
    client = _FakeClient({"SCALAR": {"ticker": "SCALAR", "status": "finalized", "result": "scalar"}})

    for attempt in range(1, 4):
        asyncio.run(settlement_resolver.run_pending(
            client, now=now + 61.0 * attempt, max_attempts=3))

    after = settlement_resolver.snapshot()
    assert after["dropped_after_max_attempts"] == before["dropped_after_max_attempts"] + 1
    assert after["skipped_non_binary_result"] == before["skipped_non_binary_result"] + 1
    assert after["dropped_total"] == before["dropped_total"] + 2
    assert (after["dropped_total"] - before["dropped_total"]
            == (after["dropped_after_max_attempts"] - before["dropped_after_max_attempts"])
            + (after["skipped_non_binary_result"] - before["skipped_non_binary_result"]))


def test_a_skip_records_the_ticker_and_the_observed_result_value(monkeypatch):
    """The gap that made "were those 64 all scalar?" unanswerable: the
    branch recorded neither ticker nor value."""
    recorded = []
    _patch_all_resolvers(monkeypatch, recorded)
    now = time.time()
    settlement_resolver.enqueue("SCALAR-A", now, now=now)
    client = _FakeClient({"SCALAR-A": {"ticker": "SCALAR-A", "status": "finalized", "result": "scalar"}})

    asyncio.run(settlement_resolver.run_pending(client, now=now + 61.0))

    snap = settlement_resolver.snapshot()
    assert snap["non_binary_by_result"] == {"scalar": 1}
    assert snap["non_binary_recent"][-1]["ticker"] == "SCALAR-A"
    assert snap["non_binary_recent"][-1]["result"] == "scalar"


def test_empty_and_literal_scalar_are_the_same_expected_case(monkeypatch):
    """docs/kalshi/changelog-index.md:3245-3246: a scalar market returns ""
    today and will read "scalar" after the next release, so BOTH must land
    in the expected-skip counter and neither may narrow the skip condition
    (it stays `result not in ("yes", "no")`). They stay distinguishable in
    the diagnostic map only - collapsing them would hide that migration,
    and conflating either with an absent field would hide a genuine
    upstream problem."""
    recorded = []
    _patch_all_resolvers(monkeypatch, recorded)
    before = settlement_resolver.snapshot()
    now = time.time()
    settlement_resolver.enqueue("EMPTY", now, now=now)
    settlement_resolver.enqueue("ABSENT", now, now=now)
    settlement_resolver.enqueue("SCALAR", now, now=now)
    client = _FakeClient({
        "EMPTY": {"ticker": "EMPTY", "status": "finalized", "result": ""},
        "ABSENT": {"ticker": "ABSENT", "status": "finalized"},
        "SCALAR": {"ticker": "SCALAR", "status": "finalized", "result": "scalar"},
    })

    asyncio.run(settlement_resolver.run_pending(client, now=now + 61.0))

    after = settlement_resolver.snapshot()
    # One expected case, three spellings: all three are skips, none a defect.
    assert after["skipped_non_binary_result"] == before["skipped_non_binary_result"] + 3
    assert after["dropped_after_max_attempts"] == before["dropped_after_max_attempts"]
    # Still individually attributable, so the "" -> "scalar" migration and a
    # genuinely absent field remain visible.
    assert after["non_binary_by_result"] == {"<empty>": 1, "<absent>": 1, "scalar": 1}


def test_the_observed_result_map_is_bounded_against_a_garbage_upstream_value(monkeypatch):
    """An upstream that starts returning unique junk must not grow an
    unbounded in-memory dict on the resolver loop."""
    recorded = []
    _patch_all_resolvers(monkeypatch, recorded)
    now = time.time()
    markets = {}
    for i in range(settlement_resolver._MAX_RESULT_KEYS + 15):
        ticker = f"JUNK{i}"
        settlement_resolver.enqueue(ticker, now, now=now)
        markets[ticker] = {"ticker": ticker, "status": "finalized", "result": f"junk-{i}"}
    client = _FakeClient(markets)

    asyncio.run(settlement_resolver.run_pending(
        client, now=now + 61.0, batch_size=len(markets)))

    by_result = settlement_resolver.snapshot()["non_binary_by_result"]
    assert len(by_result) <= settlement_resolver._MAX_RESULT_KEYS + 1
    assert by_result["__other__"] == 15
    assert sum(by_result.values()) == len(markets)
    assert len(settlement_resolver.snapshot()["non_binary_recent"]) <= 20
