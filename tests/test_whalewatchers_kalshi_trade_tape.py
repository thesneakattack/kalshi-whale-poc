import asyncio

import pytest

from services.whalewatchers.kalshi_trade_tape import KalshiTradeTapeProvider, _notional_usd


def _market(ticker="TICK-A", volume_24h_fp="10000", close_time=None):
    m = {"ticker": ticker, "volume_24h_fp": volume_24h_fp}
    if close_time is not None:
        m["close_time"] = close_time
    return m


def _trade(
    trade_id="t1", ticker="TICK-A", count_fp="100.00", yes_price_dollars="0.6000",
    no_price_dollars="0.4000", taker_side="yes", created_time="2026-08-08T12:00:00Z",
):
    return {
        "trade_id": trade_id, "ticker": ticker, "count_fp": count_fp,
        "yes_price_dollars": yes_price_dollars, "no_price_dollars": no_price_dollars,
        "taker_side": taker_side, "created_time": created_time,
    }


def test_enabled_is_always_true_no_credentials_needed():
    provider = KalshiTradeTapeProvider()
    assert provider.enabled is True


def test_notional_usd_is_side_aware():
    # yes taker: notional = count * yes_price
    yes_trade = _trade(count_fp="100.00", yes_price_dollars="0.60", no_price_dollars="0.40", taker_side="yes")
    assert _notional_usd(yes_trade) == pytest.approx(60.0)
    # no taker: notional = count * no_price, NOT count * yes_price - the
    # exact bug class this app already shipped and fixed once (open_position's
    # no-side cost bug) applied here to real-trade classification.
    no_trade = _trade(count_fp="100.00", yes_price_dollars="0.60", no_price_dollars="0.40", taker_side="no")
    assert _notional_usd(no_trade) == pytest.approx(40.0)


def test_fetch_signals_returns_empty_with_no_market_context():
    provider = KalshiTradeTapeProvider()
    assert asyncio.run(provider.fetch_signals()) == []
    assert asyncio.run(provider.fetch_signals(market_context={})) == []


def test_fetch_signals_skips_trades_below_notional_threshold():
    provider = KalshiTradeTapeProvider()
    # count 100 * yes_price 0.60 = $60 notional, well under the $2500 default
    trade = _trade(count_fp="100.00", yes_price_dollars="0.60", taker_side="yes")
    ctx = {"markets": [_market()], "trade_tape": [trade], "cfg": {}}
    assert asyncio.run(provider.fetch_signals(market_context=ctx)) == []


def test_fetch_signals_emits_signal_above_threshold():
    provider = KalshiTradeTapeProvider()
    # count 10000 * yes_price 0.60 = $6000, clears the $2500 default
    trade = _trade(count_fp="10000.00", yes_price_dollars="0.60", no_price_dollars="0.40", taker_side="yes")
    ctx = {"markets": [_market()], "trade_tape": [trade], "cfg": {}}
    signals = asyncio.run(provider.fetch_signals(market_context=ctx))
    assert len(signals) == 1
    sig = signals[0]
    assert sig.ticker == "TICK-A"
    assert sig.side == "yes"
    assert sig.size == 10000
    assert sig.price == 0.6
    assert 0.0 <= sig.confidence <= 1.0
    assert sig.id == "t1"


def test_fetch_signals_price_is_always_the_yes_price_regardless_of_side():
    provider = KalshiTradeTapeProvider()
    trade = _trade(count_fp="10000.00", yes_price_dollars="0.60", no_price_dollars="0.40", taker_side="no")
    ctx = {"markets": [_market()], "trade_tape": [trade], "cfg": {}}
    signals = asyncio.run(provider.fetch_signals(market_context=ctx))
    assert len(signals) == 1
    assert signals[0].side == "no"
    assert signals[0].price == 0.6  # yes-side price by convention, not 1 - price


def test_threshold_is_configurable_via_cfg():
    provider = KalshiTradeTapeProvider()
    trade = _trade(count_fp="100.00", yes_price_dollars="0.60", taker_side="yes")  # $60 notional
    ctx = {"markets": [_market()], "trade_tape": [trade], "cfg": {"whale_watcher_kalshi": {"min_notional_usd": 10}}}
    signals = asyncio.run(provider.fetch_signals(market_context=ctx))
    assert len(signals) == 1


def test_same_trade_id_is_not_re_emitted_on_a_later_call():
    provider = KalshiTradeTapeProvider()
    trade = _trade(count_fp="10000.00", yes_price_dollars="0.60", taker_side="yes")
    ctx = {"markets": [_market()], "trade_tape": [trade], "cfg": {}}
    first = asyncio.run(provider.fetch_signals(market_context=ctx))
    assert len(first) == 1
    # Same trade still present in this tick's trade_tape (it hasn't aged out
    # of get_trades' last-N window yet) - must not re-fire.
    second = asyncio.run(provider.fetch_signals(market_context=ctx))
    assert second == []


def test_trade_skipped_when_its_market_is_not_in_this_ticks_markets_list():
    provider = KalshiTradeTapeProvider()
    trade = _trade(ticker="UNKNOWN-TICKER", count_fp="10000.00", yes_price_dollars="0.60", taker_side="yes")
    ctx = {"markets": [_market(ticker="TICK-A")], "trade_tape": [trade], "cfg": {}}
    assert asyncio.run(provider.fetch_signals(market_context=ctx)) == []


def test_malformed_trade_is_skipped_not_crashing():
    provider = KalshiTradeTapeProvider()
    malformed = {"trade_id": "bad1", "ticker": "TICK-A", "count_fp": "not-a-number", "taker_side": "yes"}
    ctx = {"markets": [_market()], "trade_tape": [malformed], "cfg": {}}
    assert asyncio.run(provider.fetch_signals(market_context=ctx)) == []


def test_trade_with_no_trade_id_is_skipped():
    provider = KalshiTradeTapeProvider()
    trade = _trade(count_fp="10000.00", yes_price_dollars="0.60", taker_side="yes")
    trade["trade_id"] = None
    ctx = {"markets": [_market()], "trade_tape": [trade], "cfg": {}}
    assert asyncio.run(provider.fetch_signals(market_context=ctx)) == []
