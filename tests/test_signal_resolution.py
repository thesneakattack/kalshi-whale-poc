import asyncio
import time

from services import signal_log

import main


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(signal_log, "DB_PATH", tmp_path / "signal_log.db")


class _FakeMarketsClient:
    """Records the batches it's asked for - proves _check_signal_resolutions
    makes ONE batched get_markets_by_tickers call instead of N individual
    get_market() calls, the real fix for the 2026-08-16 confirmed-live
    signal-resolution backlog (26,903 of 32,480 signals unresolved,
    26,824 currently due for a check, against a 10-per-30s-check pace that
    would have taken ~22h just for one pass)."""

    def __init__(self, markets_by_ticker):
        self._markets = markets_by_ticker
        self.batch_calls = []  # list of ticker-list batches requested

    async def get_markets_by_tickers(self, tickers):
        self.batch_calls.append(list(tickers))
        return {t: self._markets[t] for t in tickers if t in self._markets}


def test_check_signal_resolutions_batches_instead_of_one_call_per_ticker(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    now = time.time()
    for i in range(5):
        signal_log.log_signal(f"TICK-{i}", "yes", 1000, 0.8, "simulated", seen_at=now - 1000)
    fake = _FakeMarketsClient({
        f"TICK-{i}": {"ticker": f"TICK-{i}", "result": "yes" if i % 2 == 0 else ""} for i in range(5)
    })
    asyncio.run(main._check_signal_resolutions(fake))
    assert len(fake.batch_calls) == 1  # one batched call, not 5 individual ones
    assert set(fake.batch_calls[0]) == {f"TICK-{i}" for i in range(5)}


def test_check_signal_resolutions_marks_resolved_markets_correctly(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    now = time.time()
    signal_log.log_signal("TICK-YES", "yes", 1000, 0.8, "simulated", seen_at=now - 1000)
    signal_log.log_signal("TICK-NO", "yes", 1000, 0.8, "simulated", seen_at=now - 1000)  # wrong side
    signal_log.log_signal("TICK-PENDING", "yes", 1000, 0.8, "simulated", seen_at=now - 1000)
    fake = _FakeMarketsClient({
        "TICK-YES": {"ticker": "TICK-YES", "result": "yes"},
        "TICK-NO": {"ticker": "TICK-NO", "result": "no"},
        # TICK-PENDING deliberately absent - not yet settled
    })
    asyncio.run(main._check_signal_resolutions(fake))
    stats = signal_log.stats(days=30)
    assert stats["resolved"] == 2
    assert stats["correct"] == 1  # only TICK-YES's side matched the real result


def test_check_signal_resolutions_leaves_a_market_kalshi_never_returned_unresolved(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    now = time.time()
    signal_log.log_signal("TICK-GONE", "yes", 1000, 0.8, "simulated", seen_at=now - 1000)
    fake = _FakeMarketsClient({})  # ticker not returned - renamed/deleted, or just not settled yet
    asyncio.run(main._check_signal_resolutions(fake))
    stats = signal_log.stats(days=30)
    assert stats["resolved"] == 0


def test_check_signal_resolutions_respects_the_larger_batch_size(tmp_path, monkeypatch):
    # The whole point of the fix: batching makes it cheap to check far more
    # than the old limit=10 per cycle.
    _isolate(tmp_path, monkeypatch)
    now = time.time()
    n = main._SIGNAL_RESOLUTION_BATCH_SIZE + 20
    for i in range(n):
        signal_log.log_signal(f"TICK-{i}", "yes", 1000, 0.8, "simulated", seen_at=now - 1000)
    fake = _FakeMarketsClient({})
    asyncio.run(main._check_signal_resolutions(fake))
    assert len(fake.batch_calls[0]) == main._SIGNAL_RESOLUTION_BATCH_SIZE  # capped, not all n


def test_check_signal_resolutions_no_items_makes_no_call(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    fake = _FakeMarketsClient({})
    asyncio.run(main._check_signal_resolutions(fake))
    assert fake.batch_calls == []
