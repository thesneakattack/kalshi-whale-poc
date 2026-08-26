import time

from services import whale_gate


def test_passes_true_for_a_whale_sized_trade():
    trade = {"trade_id": "t1", "market_ticker": "KXBTC-25AUG25-T1", "count_fp": "500", "yes_price_dollars": "0.60"}
    assert whale_gate.passes(trade, min_contracts=100) is True


def test_passes_false_for_a_small_trade():
    trade = {"trade_id": "t1", "market_ticker": "KXBTC-25AUG25-T1", "count_fp": "5", "yes_price_dollars": "0.60"}
    assert whale_gate.passes(trade, min_contracts=100) is False


def test_passes_false_rather_than_raising_when_count_is_missing():
    trade = {"trade_id": "t1", "market_ticker": "KXBTC-25AUG25-T1"}
    assert whale_gate.passes(trade, min_contracts=100) is False


def test_gate_cost_is_microseconds():
    trade = {"trade_id": "t1", "market_ticker": "KXBTC-25AUG25-T1", "count_fp": "500", "yes_price_dollars": "0.60"}
    n = 20_000
    start = time.perf_counter()
    for _ in range(n):
        whale_gate.passes(trade, min_contracts=100)
    per_call_us = (time.perf_counter() - start) / n * 1_000_000
    assert per_call_us < 20.0  # design spec Sec 2's ceiling


def test_min_contracts_for_uses_the_series_override_when_present():
    cfg = {"whale_watcher_kalshi": {"min_contracts": 100, "min_contracts_by_series": {"KXBTC15M": 250}}}
    assert whale_gate.min_contracts_for("KXBTC15M-25AUG2513-T50000", cfg) == 250


def test_min_contracts_for_falls_back_to_the_global_default():
    cfg = {"whale_watcher_kalshi": {"min_contracts": 100}}
    assert whale_gate.min_contracts_for("SOME-OTHER-TICKER", cfg) == 100
