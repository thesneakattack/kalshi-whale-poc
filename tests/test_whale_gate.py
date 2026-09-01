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


def test_min_contracts_for_cost_is_microseconds_for_an_off_watchlist_ticker():
    # Final whole-branch review finding: this function's OWN hot-path cost
    # was never measured by this file's existing test_gate_cost_is_
    # microseconds above, which only times whale_gate.passes() - the
    # function that actually regressed (kalshi-category-data-completeness
    # Task 3's series_of()/title_cache.series_ticker_for() DB round trip,
    # fixed in two rounds, see title_cache.py's own module-level comment)
    # went uncovered by any perf guard. min_contracts_for() is called
    # unconditionally on the exchange-wide trade-tape hot path
    # (services/kalshi/websocket.py's _gate_check_and_maybe_filter, even in
    # shadow mode - reader_gate_enabled is checked AFTER this call, not
    # before), and config/settings.yaml's trade_stream_exchange_wide: true
    # means the DOMINANT real case is a ticker whose market_titles/
    # event_titles were never indexed (genuinely off this app's watchlist
    # scope) - series_of()'s prefix fallback, not a resolved series. Pin
    # that exact case against this file's own documented 20us ceiling.
    cfg = {"whale_watcher_kalshi": {"min_contracts": 100}}
    n = 20_000
    start = time.perf_counter()
    for i in range(n):
        whale_gate.min_contracts_for(f"KXNEVERSEEN{i}-25AUG25-T1", cfg)
    per_call_us = (time.perf_counter() - start) / n * 1_000_000
    assert per_call_us < 20.0  # design spec Sec 2's ceiling, same as test_gate_cost_is_microseconds
