"""Benchmarks (not just tests) the CURRENT cost of exit_engine.check_exits at
increasing open-position counts, before Task 20's tick_cache fix - quantifies the
live-observed "large amount of open positions causes lag or crash" report
(2026-08-27) with real numbers, the same "measure before fixing" discipline P3.5's
CH2 investigation used with py-spy against series_watcher.funnel(). Synthetic
Position objects only - never touches the real data/paper_broker.db.

Design note: every position here is a DISTINCT ticker (KXBTC15M-T0, T1, T2, ...),
deliberately - this is the shape Task 20's plan text calls out as the one its own
tick_cache fix (which memoizes per-tick reads *shared by positions on the same
ticker*) does NOT help with. See services/exits/README.md for the recorded
numbers and same-ticker-vs-distinct-ticker conclusion.
"""
import time
from unittest.mock import patch

import pytest

from services.exits import exit_engine
from services.paper_broker import Position


def _make_positions(n: int) -> dict:
    return {
        f"KXBTC15M-T{i}": Position(
            ticker=f"KXBTC15M-T{i}", side="yes", size=10, entry_price=0.5, opened_at=time.time(),
        )
        for i in range(n)
    }


class _FakeBroker:
    """Minimal broker double - just enough surface for check_exits' per-position
    loop to run past the recent_price() corroboration call (the mechanism under
    test) and then exit cleanly. cost_basis() returns 0.0 (<=0) so check_exits'
    own "if cost_basis <= 0: continue" (exit_engine.py, right after the
    recent_price call) short-circuits the rest of the loop body for each
    position - fee/mark-to-market/exit-rule logic isn't part of what this
    benchmark measures, and leaving it unmocked would raise AttributeError on a
    bare double like this one."""

    def __init__(self, positions: dict):
        self.positions = positions

    def cost_basis(self, ticker: str) -> float:
        return 0.0


@pytest.mark.parametrize("n", [10, 50, 200])
def test_check_exits_hits_recent_price_once_per_open_position_today(n):
    """Direct proof of the mechanism the live crash report points at:
    market_history.recent_price runs unconditionally per open position
    (exit_engine.py, in the per-position loop, ahead of and outside all three
    opt-in exit rules - take_profit_pct/stop_loss_pct/exit_on_sentiment_reversal/
    auto_exit_enabled) - N positions means N calls today, not O(1). This is
    exactly what Task 20's tick_cache fixes for the same-ticker case."""
    broker = _FakeBroker(_make_positions(n))
    latest_prices = {t: p.entry_price for t, p in broker.positions.items()}
    with patch("services.market_history.recent_price", return_value=None) as recent_price:
        exit_engine.check_exits(broker, latest_prices, signal_feed=[], cfg={"strategy": {}})
    assert recent_price.call_count == n


@pytest.mark.parametrize("n", [10, 50, 200, 500])
def test_check_exits_wall_clock_cost_at_scale(n, capsys):
    """Records real wall-clock cost (not just call count) at each N, printed for
    recording into services/exits/README.md - a 0.1ms mock reply per DB read
    still exposes the O(N) shape without needing seeded real data."""
    broker = _FakeBroker(_make_positions(n))
    latest_prices = {t: p.entry_price for t, p in broker.positions.items()}

    def _slow_recent_price(*a, **kw):
        time.sleep(0.0001)  # a representative single-row SQLite read, not real I/O
        return None

    with patch("services.market_history.recent_price", side_effect=_slow_recent_price):
        t0 = time.monotonic()
        exit_engine.check_exits(broker, latest_prices, signal_feed=[], cfg={"strategy": {}})
        elapsed = time.monotonic() - t0
    print(f"n={n} positions: check_exits took {elapsed * 1000:.1f}ms")


# ---- Task 20: tick_cache memoization (I13 P4) -----------------------------
#
# PaperBroker.positions is dict[str, Position] - one entry per ticker (see
# services/paper_broker.py) - so two Position objects can never literally
# coexist under the same ticker key inside one check_exits() call. What
# tick_cache actually protects against, in terms this real data model can
# reproduce, is the same shape from the other angle described in services/
# exits/README.md's "partial-hedge positions on the same market" note: two
# check_exits() passes for the same ticker, within the same tick, sharing
# one tick_cache dict - the second pass's read for that ticker is served
# from cache instead of re-querying.


def test_check_exits_shares_tick_cache_across_calls_on_the_same_ticker():
    """Two check_exits() calls for the same ticker, sharing one tick_cache={},
    should hit market_history.recent_price only once - not twice."""
    ticker = "KXBTC15M-A"
    pos = Position(ticker=ticker, side="yes", size=10, entry_price=0.5, opened_at=time.time())
    broker = _FakeBroker({ticker: pos})
    latest_prices = {ticker: pos.entry_price}
    tick_cache = {}
    with patch("services.market_history.recent_price", return_value=None) as recent_price:
        exit_engine.check_exits(broker, latest_prices, signal_feed=[], cfg={"strategy": {}}, tick_cache=tick_cache)
        exit_engine.check_exits(broker, latest_prices, signal_feed=[], cfg={"strategy": {}}, tick_cache=tick_cache)
    assert recent_price.call_count == 1


def test_check_exits_tick_cache_default_none_is_byte_identical_to_before():
    """tick_cache defaults to None - strictly additive, so every existing
    caller (main.py's stream-path call sites, every pre-Task-20 test) must
    see the exact same behavior as before this parameter existed: a fresh,
    uncached read on every call, not a cache hit on the second one."""
    ticker = "KXBTC15M-A"
    pos = Position(ticker=ticker, side="yes", size=10, entry_price=0.5, opened_at=time.time())
    broker = _FakeBroker({ticker: pos})
    latest_prices = {ticker: pos.entry_price}
    with patch("services.market_history.recent_price", return_value=None) as recent_price:
        exit_engine.check_exits(broker, latest_prices, signal_feed=[], cfg={"strategy": {}})
        exit_engine.check_exits(broker, latest_prices, signal_feed=[], cfg={"strategy": {}})
    assert recent_price.call_count == 2
