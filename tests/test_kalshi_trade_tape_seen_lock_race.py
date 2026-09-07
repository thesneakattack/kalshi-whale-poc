"""issue #546 (2026-09-03, filed while researching the Option B trade-
resolve consumer-blocking fix - docs/archive/lane-2-whale-signal-
calibration/research/2026-09-03-trade-resolve-consumer-blocking-solution-
comparison.md, moved there 2026-09-07, planning-lanes migration, §3 finding F6):
self._seen_trade_ids/_seen_order are touched by _process_trades_sync, which
runs on worker threads, from TWO independently-scheduled callers - the WS
stream's own consumer (fetch_signals -> _process_trades_timed) and
main.py's _candidate_retry_loop (score_recovered_trade). They submit to
two different pools as of issue #563 (_scoring_pool.py and
_candidate_retry_pool.py respectively; they shared one 4-worker pool when
this race was found), which does NOT close this race: both still mutate
the SAME provider instance's _seen_trade_ids/_seen_order from different OS
threads, so the lock below is still what closes it.
_process_trades_sync's own docstring used to claim "only
ever touched from within one in-flight fetch_signals() call at a time" -
correct when written (2026-08-11, the tick loop awaited each call
serially), silently invalidated by two later, unrelated commits that never
revisited it (P8 Task 37 gave candidate_retry its own supervised task;
_scoring_pool.py's own addition put both paths on one shared pool).

The actual hazard (confirmed by direct source tracing on branch
fix/seen-trade-ids-concurrency-race-investigation, root-cause doc
docs/archive/lane-2-whale-signal-calibration/research/2026-09-03-seen-
trade-ids-concurrency-race-root-cause.md (moved there 2026-09-07,
planning-lanes migration), read for this
fix): a check-then-act race on the "already seen?" gate for the SAME
trade_id. Two threads can both read `trade_id not in self._seen_trade_ids`
as True before either marks it, both proceed to score, both may emit a
signal for the same real trade - a genuine duplicate, not merely a
bookkeeping inconsistency. Locking only INSIDE _mark_seen's own body does
NOT close this window - the pre-check happens several lines earlier in
_process_trades_sync, outside _mark_seen entirely. The fix instead guards
the whole check-through-mark span in _process_trades_sync's own per-trade
loop under one self._seen_lock (see KalshiTradeTapeProvider.__init__'s own
comment and _process_trades_sync's docstring).

This file proves the fix does something, not just that a lock object
exists (the same falsification discipline this repo has used throughout
this incident):

  test_two_concurrent_calls_for_the_same_trade_id_only_one_proceeds
      forces the exact interleaving that used to be racy (a delay injected
      between the check and the mark, held while the REAL
      provider._seen_lock is held) and shows exactly one of two truly
      concurrent OS threads proceeds past the gate for the same trade_id.

  test_the_same_race_reproduces_when_the_lock_is_bypassed
      swaps in a no-op stand-in for self._seen_lock (simulating the
      pre-fix "no lock at all" state) and shows BOTH threads proceed - the
      double-processing mechanism issue #546 root-caused, reproduced
      directly against the real provider, not a reimplementation.
"""
import threading
import time

from services import signal_log
from services.whalewatchers.kalshi_trade_tape import KalshiTradeTapeProvider

_CFG = {"whale_watcher_kalshi": {"min_contracts": 50}}


def _sub_threshold_trade(trade_id="race-1", ticker="RACE-TICK"):
    # count_fp well below min_contracts, and no market entry for this
    # ticker anywhere in this test - _process_trades_sync's own "not
    # market" branch is reached right after the gate via _prescan_count's
    # pure-arithmetic path, with NO further DB access on this specific
    # combination (count below threshold skips candidate_log.record_
    # rejection too - see this file's own analysis in the PR/self-review).
    # Deliberately DB-free so this test measures ONLY the gate's own
    # behavior, not scoring-pipeline or SQLite lock-contention timing.
    return {
        "trade_id": trade_id, "ticker": ticker, "count_fp": "1.00",
        "yes_price_dollars": "0.6000", "no_price_dollars": "0.4000",
        "taker_outcome_side": "yes", "created_time": "2026-09-03T12:00:00Z",
    }


class _NoOpLock:
    """Stands in for threading.Lock but performs no real exclusion -
    simulates the pre-fix state (no lock at all) so the race this fix
    closes can be reproduced directly against the real provider instead of
    only asserted in prose."""

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _run_concurrent_gate_race(provider: KalshiTradeTapeProvider, *, delay_sec: float = 0.05) -> int:
    """Two real OS threads both call _process_trades_sync for the SAME
    trade_id, with an artificial delay injected between the "already seen?"
    check and the mark decision (inside provider._mark_seen_locked, called
    by _process_trades_sync while holding provider._seen_lock) so the race
    window is wide enough to hit deterministically instead of relying on
    GIL-scheduling luck. A threading.Barrier makes both threads enter
    _process_trades_sync as close to simultaneously as the OS allows.

    Returns how many DISTINCT threads actually proceeded past the gate for
    this trade_id - identified via threading.get_ident() inside
    signal_log.series_of, a real call _process_trades_sync makes at least
    once (in practice, up to 3x: the loop body plus two separate
    min_contracts_for() evaluations on this sub-threshold/unresolved-market
    path) for any ticker-bearing trade that passes the gate, monkeypatched
    here to a cheap, DB-free counter instead of the real, title_cache-
    backed implementation - counting distinct threads rather than raw call
    count keeps this test robust to that per-trade call count, which is an
    implementation detail of the code after the gate, not of the gate
    itself."""
    trade = _sub_threshold_trade()
    proceeded_threads: set[int] = set()
    counter_lock = threading.Lock()

    def _fake_series_of(ticker):
        with counter_lock:
            proceeded_threads.add(threading.get_ident())
        return "RACE"

    real_mark_seen_locked = provider._mark_seen_locked

    def _slow_mark_seen_locked(trade_id, exchange_ts=None):
        time.sleep(delay_sec)
        real_mark_seen_locked(trade_id, exchange_ts=exchange_ts)

    provider._mark_seen_locked = _slow_mark_seen_locked
    original_series_of = signal_log.series_of
    signal_log.series_of = _fake_series_of
    try:
        barrier = threading.Barrier(2)
        results: list[list] = []

        def _worker():
            barrier.wait()
            signals = provider._process_trades_sync([trade], [], {}, _CFG, time.time())
            results.append(signals)

        t1 = threading.Thread(target=_worker)
        t2 = threading.Thread(target=_worker)
        t1.start()
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)
        assert not t1.is_alive() and not t2.is_alive(), "a thread never finished - possible deadlock"
        assert len(results) == 2
    finally:
        signal_log.series_of = original_series_of

    return len(proceeded_threads)


def test_two_concurrent_calls_for_the_same_trade_id_only_one_proceeds():
    provider = KalshiTradeTapeProvider()
    proceeded_threads = _run_concurrent_gate_race(provider)
    assert proceeded_threads == 1  # exactly one of the two concurrent calls scored this trade
    assert "race-1" in provider._seen_trade_ids  # the winner's mark stuck
    assert list(provider._seen_order) == [("race-1", provider._seen_order[0][1])]  # marked exactly once


def test_the_same_race_reproduces_when_the_lock_is_bypassed():
    provider = KalshiTradeTapeProvider()
    provider._seen_lock = _NoOpLock()  # simulate the pre-fix "no lock" state
    proceeded_threads = _run_concurrent_gate_race(provider)
    # BOTH threads pass the "already seen?" check before either marks -
    # the exact double-processing hazard issue #546 root-caused. This is
    # the falsifier: with the real lock (test above), this never happens;
    # with it bypassed, it reliably does.
    assert proceeded_threads == 2
