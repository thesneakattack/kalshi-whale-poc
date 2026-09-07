"""Option B precondition (2026-09-03 live-incident fix, docs/archive/lane-2-
whale-signal-calibration/research/2026-09-03-trade-resolve-consumer-
blocking-solution-comparison.md, moved there 2026-09-07, planning-lanes
migration, §3): _resolve_unknown_markets used to reset
`self._resolve_failed_tickers = set()` unconditionally at the top of every
call, on the assumption that exactly one call is ever in flight at a time -
true while the WS consumer was strictly serial, false the moment bounded
concurrency (services/kalshi/websocket.py's _dispatch_trade_concurrent) lets
more than one fetch_signals()/_resolve_unknown_markets() call overlap.

Under the old shape, a second call starting while a first was still
awaiting its own REST response would wipe the first's failure tracking out
from under it before it was ever consumed - silently reintroducing the
exact "permanently unresolvable, no retry" bug this module's own H4 fix
(P2 Task 11) already paid down once, just via a different trigger.

Fixed by making the failed-ticker set a per-call return value instead of
instance (self.) state - concurrent calls then have no shared mutable
state to race over for this purpose at all. This file proves both halves:

  test_a_slow_failing_resolve_is_not_corrupted_by_a_concurrent_call
      exercises the ACTUAL current _resolve_unknown_markets with two
      overlapping calls, arranged so a second call's own work (including,
      pre-fix, a `self._resolve_failed_tickers = set()` reset) runs while
      the first is still suspended on its own REST await - and asserts the
      first call's own returned failure set is exactly its own, unaffected.

  test_old_shared_self_state_shape_would_have_lost_the_slow_calls_failure
      is a minimal, self-contained reproduction of the OLD shape's actual
      race (reset-at-top-of-call mutating self.), independent of the
      current fixed implementation, so the mechanism this bug used to have
      is demonstrated directly rather than only asserted in prose.
"""
import asyncio
import time

import pytest

from services import candidate_log, candidate_retry, capture_writer, market_analyst_agent, market_history, series_evaluator, signal_log
from services.market_analyst_agent import _db as maa_db_module
from services.whalewatchers.kalshi_trade_tape import KalshiTradeTapeProvider

_CFG = {"whale_watcher_kalshi": {"min_contracts": 10}}


@pytest.fixture(autouse=True)
def _isolated_dbs(tmp_path, monkeypatch):
    # Same real-db-isolation reasoning as every other kalshi_trade_tape.py
    # test file - _resolve_unknown_markets' own prescan touches nothing,
    # but the surrounding fetch_signals/_process_trades_sync machinery
    # these tests exercise indirectly does.
    monkeypatch.setattr(signal_log, "DB_PATH", tmp_path / "signal_log.db")
    monkeypatch.setattr(market_history, "DB_PATH", tmp_path / "market_history.db")
    monkeypatch.setattr(maa_db_module, "DB_PATH", tmp_path / "market_analyst.db")
    monkeypatch.setattr(series_evaluator, "DB_PATH", tmp_path / "series_evaluator.db")
    candidate_log_db_path = tmp_path / "candidate_log.db"
    monkeypatch.setattr(candidate_log, "DB_PATH", candidate_log_db_path)
    monkeypatch.setattr(capture_writer, "_STORE_PATHS", {
        "rejected_candidates": candidate_log_db_path, "rejection_events": candidate_log_db_path,
    })
    monkeypatch.setattr(capture_writer, "_buffers", {"rejected_candidates": {}, "rejection_events": []})
    monkeypatch.setattr(capture_writer, "_last_flush_at", {"rejected_candidates": 0.0, "rejection_events": 0.0})
    monkeypatch.setattr(capture_writer, "_dropped_counts", {"rejected_candidates": 0, "rejection_events": 0})
    monkeypatch.setattr(candidate_retry, "_pending", {})


def _trade(trade_id, ticker, count_fp="5000.00"):
    return {
        "trade_id": trade_id, "ticker": ticker, "count_fp": count_fp,
        "yes_price_dollars": "0.6000", "no_price_dollars": "0.4000",
        "taker_outcome_side": "yes", "created_time": "2026-09-03T12:00:00Z",
    }


class _ControlledClient:
    """One ticker ("SLOW-TICK") pauses on release_slow before raising a
    simulated transient failure; every other ticker resolves immediately
    and successfully - models a real REST call actually in flight (awaiting
    the network) while a second, independent call runs to completion."""

    def __init__(self):
        self.release_slow = asyncio.Event()
        self.calls: list[list[str]] = []

    async def get_markets_by_tickers(self, tickers):
        self.calls.append(list(tickers))
        if tickers == ["SLOW-TICK"]:
            await self.release_slow.wait()
            raise RuntimeError("simulated transient failure (429/timeout-equivalent)")
        return {t: {"ticker": t, "volume_24h_fp": "10000"} for t in tickers}


def test_a_slow_failing_resolve_is_not_corrupted_by_a_concurrent_call():
    """The real, current _resolve_unknown_markets: a slow call's own
    eventual failure set must reflect only its own batch, regardless of
    what a second call - which fully completes while the first is still
    suspended on its own REST await - does in the meantime."""
    async def scenario():
        provider = KalshiTradeTapeProvider()
        client = _ControlledClient()

        task_slow = asyncio.create_task(provider._resolve_unknown_markets(
            [_trade("slow-1", "SLOW-TICK")], {}, _CFG, client, time.time(),
        ))
        await asyncio.sleep(0)  # let it start and reach the await on release_slow
        assert client.calls == [["SLOW-TICK"]]

        # A second, fully independent call runs start-to-finish while the
        # first is still mid-flight - exactly the "later call starting
        # while an earlier one is still awaiting its REST response"
        # scenario the research doc's Correctness section names.
        fast_result = await provider._resolve_unknown_markets(
            [_trade("fast-1", "FAST-TICK")], {}, _CFG, client, time.time(),
        )

        client.release_slow.set()
        slow_result = await task_slow
        return fast_result, slow_result

    fast_result, slow_result = asyncio.run(scenario())
    assert fast_result == set()  # the fast call had no failures of its own
    assert slow_result == {"SLOW-TICK"}  # NOT wiped by the fast call's own work


def test_interleaving_the_other_way_is_also_correct():
    """Same scenario, roles reversed (the fast call starts and finishes
    first, then the slow one starts) - both orderings must be safe, not
    just the one that happens to trip the old bug most obviously."""
    async def scenario():
        provider = KalshiTradeTapeProvider()
        client = _ControlledClient()

        fast_result = await provider._resolve_unknown_markets(
            [_trade("fast-1", "FAST-TICK")], {}, _CFG, client, time.time(),
        )
        task_slow = asyncio.create_task(provider._resolve_unknown_markets(
            [_trade("slow-1", "SLOW-TICK")], {}, _CFG, client, time.time(),
        ))
        await asyncio.sleep(0)
        client.release_slow.set()
        slow_result = await task_slow
        return fast_result, slow_result

    fast_result, slow_result = asyncio.run(scenario())
    assert fast_result == set()
    assert slow_result == {"SLOW-TICK"}


def test_old_shared_self_state_shape_would_have_lost_the_slow_calls_failure():
    """Falsifier: a minimal, self-contained reproduction of the OLD
    mechanism (reset self.-state at the top of every call), independent of
    the current provider, to demonstrate the bug this fix actually closes -
    not just assert the fix exists. Mirrors _resolve_unknown_markets'
    pre-2026-09-03 shape exactly: `self._resolve_failed_tickers = set()`
    unconditionally at entry, `self._resolve_failed_tickers |= set(batch)`
    on failure, read back by the caller after the awaited call returns."""
    class _OldShapeResolver:
        def __init__(self):
            self._resolve_failed_tickers: set[str] = set()

        async def resolve(self, ticker: str, client) -> None:
            self._resolve_failed_tickers = set()  # the old unconditional reset
            try:
                await client.get_markets_by_tickers([ticker])
            except Exception:
                self._resolve_failed_tickers |= {ticker}

    async def scenario():
        resolver = _OldShapeResolver()
        client = _ControlledClient()

        task_slow = asyncio.create_task(resolver.resolve("SLOW-TICK", client))
        await asyncio.sleep(0)
        assert client.calls == [["SLOW-TICK"]]

        # The fast call's own entry into resolve() resets self.
        # _resolve_failed_tickers - while the slow call is still suspended
        # inside its own REST await, having not yet reached its own
        # `|= {ticker}` line.
        await resolver.resolve("FAST-TICK", client)

        client.release_slow.set()
        await task_slow
        return resolver._resolve_failed_tickers

    final_state = asyncio.run(scenario())
    # The slow call's own failure IS present here (nothing reset self.
    # AFTER its own `|=` ran) - this ordering alone doesn't prove the bug.
    # The actual defect is structural: prove it by re-running with the
    # reset happening AFTER the slow call's own `|=`, which is exactly as
    # likely under real concurrency (the doc's own framing: "a LATER call
    # starting while an EARLIER one is still awaiting its REST response
    # would wipe self._resolve_failed_tickers OUT FROM UNDER the earlier
    # call BEFORE it finishes populating/consuming it").
    assert final_state == {"SLOW-TICK"}  # sanity: this ordering happens not to lose it

    async def scenario_reset_after():
        resolver = _OldShapeResolver()
        client = _ControlledClient()

        task_slow = asyncio.create_task(resolver.resolve("SLOW-TICK", client))
        await asyncio.sleep(0)
        client.release_slow.set()
        # Let the slow call's own `|= {ticker}` run and complete FIRST...
        await task_slow
        assert resolver._resolve_failed_tickers == {"SLOW-TICK"}
        # ...then a later call for a DIFFERENT ticker on the SAME resolver
        # (e.g. the next print in the trade tape) resets self. out from
        # under the value _process_trades_sync has not read yet in the
        # real pipeline (there, the read happens on a worker thread AFTER
        # fetch_signals's own resolve stage already returned control to the
        # event loop, which is exactly the gap a second call can land in).
        await resolver.resolve("OTHER-TICK", client)
        return resolver._resolve_failed_tickers

    final_state_after = asyncio.run(scenario_reset_after())
    # The bug: SLOW-TICK's own recorded failure is gone - wiped by the
    # later call's unconditional reset before anything downstream ever
    # read it. This is the exact mechanism the real fix (returning a
    # per-call-local set instead of mutating self.) eliminates structurally
    # for _resolve_unknown_markets - there is no shared self. state left to
    # reset out from under a concurrent caller.
    assert final_state_after == set()  # OLD SHAPE: SLOW-TICK's failure is lost
