import asyncio
import time

import pytest

from services import candidate_log, market_analyst_agent, market_history, series_evaluator, signal_log
from services.whalewatchers.kalshi_trade_tape import KalshiTradeTapeProvider, _notional_usd, _taker_side


@pytest.fixture(autouse=True)
def _redirect_signal_log_db(tmp_path, monkeypatch):
    # fetch_signals() now queries signal_log.recent_sides_for_ticker() for
    # agreement_factor and signal_log.cluster_factor() for cluster_factor -
    # redirect before any test can touch the real data/signal_log.db
    # (CLAUDE.md's live-db warning), same pattern
    # tests/test_signal_log.py/test_market_history.py already use.
    monkeypatch.setattr(signal_log, "DB_PATH", tmp_path / "signal_log.db")
    # fetch_signals() also now queries market_history.momentum() for
    # trend_factor - same real-db-isolation reasoning.
    monkeypatch.setattr(market_history, "DB_PATH", tmp_path / "market_history.db")
    # fetch_signals() also now queries market_analyst_agent.analyst_lean()
    # for analyst_factor - same real-db-isolation reasoning.
    monkeypatch.setattr(market_analyst_agent, "DB_PATH", tmp_path / "market_analyst.db")
    # fetch_signals() now also calls series_evaluator.record_trade_observed()
    # for every newly-seen real trade - same real-db-isolation reasoning.
    # Found and fixed live during Item 1's own implementation (2026-08-10):
    # this fixture was missing the redirect, so every test run in this file
    # was writing to the real data/series_evaluator.db - the exact bug class
    # this app's own "preserve real data" standing rule exists to prevent.
    monkeypatch.setattr(series_evaluator, "DB_PATH", tmp_path / "series_evaluator.db")
    # fetch_signals() now also calls candidate_log.record_rejection() when
    # the min_notional_usd gate fails - same real-db-isolation reasoning.
    monkeypatch.setattr(candidate_log, "DB_PATH", tmp_path / "candidate_log.db")


def _market(ticker="TICK-A", volume_24h_fp="10000", close_time=None):
    m = {"ticker": ticker, "volume_24h_fp": volume_24h_fp}
    if close_time is not None:
        m["close_time"] = close_time
    return m


def _trade(
    trade_id="t1", ticker="TICK-A", count_fp="100.00", yes_price_dollars="0.6000",
    no_price_dollars="0.4000", taker_side="yes", created_time="2026-08-08T12:00:00Z",
    is_block_trade=False,
):
    return {
        "trade_id": trade_id, "ticker": ticker, "count_fp": count_fp,
        "yes_price_dollars": yes_price_dollars, "no_price_dollars": no_price_dollars,
        "taker_side": taker_side, "created_time": created_time, "is_block_trade": is_block_trade,
    }


def test_enabled_is_always_true_no_credentials_needed():
    provider = KalshiTradeTapeProvider()
    assert provider.enabled is True


def test_notional_usd_is_side_aware():
    # yes taker: notional = count * yes_price
    yes_trade = _trade(count_fp="100.00", yes_price_dollars="0.60", no_price_dollars="0.40", taker_side="yes")
    assert _notional_usd(yes_trade, "yes") == pytest.approx(60.0)
    # no taker: notional = count * no_price, NOT count * yes_price - the
    # exact bug class this app already shipped and fixed once (open_position's
    # no-side cost bug) applied here to real-trade classification.
    no_trade = _trade(count_fp="100.00", yes_price_dollars="0.60", no_price_dollars="0.40", taker_side="no")
    assert _notional_usd(no_trade, "no") == pytest.approx(40.0)


# ---- taker-side resolution (2026-08-17 audit) ----
# docs/kalshi/get-trades.md deprecates taker_side and names
# taker_outcome_side/taker_book_side canonical. The old code read only the
# deprecated field and defaulted anything unreadable to "no" - wrong
# direction and wrong notional, silently, on every signal.

def test_taker_side_prefers_the_canonical_outcome_field():
    t = {"taker_outcome_side": "yes", "taker_side": "no"}
    assert _taker_side(t) == "yes"


def test_taker_side_falls_back_to_book_side_vocabulary():
    # docs: 'bid' == outcome 'yes', 'ask' == outcome 'no'
    assert _taker_side({"taker_book_side": "bid"}) == "yes"
    assert _taker_side({"taker_book_side": "ask"}) == "no"


def test_taker_side_still_accepts_the_legacy_field_alone():
    assert _taker_side({"taker_side": "no"}) == "no"


def test_taker_side_returns_none_rather_than_guessing():
    # The critical one: an unreadable trade must not become a confident NO.
    assert _taker_side({}) is None
    assert _taker_side({"taker_side": ""}) is None
    assert _taker_side({"taker_outcome_side": "maybe"}) is None


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


def test_fetch_signals_below_notional_threshold_logs_a_rejected_candidate():
    provider = KalshiTradeTapeProvider()
    trade = _trade(count_fp="100.00", yes_price_dollars="0.60", taker_side="yes")
    ctx = {"markets": [_market()], "trade_tape": [trade], "cfg": {}}
    asyncio.run(provider.fetch_signals(market_context=ctx))
    gates = candidate_log.gate_summary()
    assert len(gates) == 1
    assert gates[0]["strategy"] == "whale_watcher"
    assert gates[0]["gate_name"] == "min_notional_usd"
    assert gates[0]["rejected_count"] == 1


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


def test_fetch_signals_captures_raw_context_alongside_the_factor_breakdown():
    provider = KalshiTradeTapeProvider()
    trade = _trade(count_fp="10000.00", yes_price_dollars="0.60", no_price_dollars="0.40", taker_side="yes")
    ctx = {"markets": [_market(volume_24h_fp="15000")], "trade_tape": [trade], "cfg": {}}
    signals = asyncio.run(provider.fetch_signals(market_context=ctx))
    raw = signals[0].raw_context
    assert raw["notional_usd"] == pytest.approx(6000.0)  # 10000 * 0.60
    assert raw["spread"] == 0.0  # no yes_ask_dollars in the fixture - falls back to price itself
    assert raw["volume_24h"] == 15000.0


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


def test_per_series_threshold_overrides_the_global_default():
    # $60 notional clears a $10 series-specific override but not the $2500 global default
    provider = KalshiTradeTapeProvider()
    trade = _trade(ticker="TICK-A", count_fp="100.00", yes_price_dollars="0.60", taker_side="yes")
    ctx = {
        "markets": [_market(ticker="TICK-A")], "trade_tape": [trade],
        "cfg": {"whale_watcher_kalshi": {"min_notional_usd_by_series": {"TICK": 10}}},
    }
    signals = asyncio.run(provider.fetch_signals(market_context=ctx))
    assert len(signals) == 1


def test_per_series_override_only_applies_to_its_own_series():
    # same $60 notional, but the override is keyed to a different series -
    # must fall back to the (unmet) $2500 global default, not the override.
    provider = KalshiTradeTapeProvider()
    trade = _trade(ticker="TICK-A", count_fp="100.00", yes_price_dollars="0.60", taker_side="yes")
    ctx = {
        "markets": [_market(ticker="TICK-A")], "trade_tape": [trade],
        "cfg": {"whale_watcher_kalshi": {"min_notional_usd_by_series": {"OTHER": 10}}},
    }
    signals = asyncio.run(provider.fetch_signals(market_context=ctx))
    assert signals == []


def test_series_with_no_override_falls_back_to_configured_global_default():
    provider = KalshiTradeTapeProvider()
    trade = _trade(ticker="TICK-A", count_fp="100.00", yes_price_dollars="0.60", taker_side="yes")  # $60 notional
    ctx = {
        "markets": [_market(ticker="TICK-A")], "trade_tape": [trade],
        "cfg": {"whale_watcher_kalshi": {"min_notional_usd": 10, "min_notional_usd_by_series": {"OTHER": 5000}}},
    }
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


def test_signal_carries_a_full_factor_breakdown():
    provider = KalshiTradeTapeProvider()
    trade = _trade(count_fp="10000.00", yes_price_dollars="0.60", taker_side="yes")
    ctx = {"markets": [_market()], "trade_tape": [trade], "cfg": {}}
    signals = asyncio.run(provider.fetch_signals(market_context=ctx))
    assert len(signals) == 1
    factors = signals[0].factors
    assert factors is not None
    for key in ("depth_factor", "unusualness_factor", "proximity_factor", "context_factor", "agreement_factor", "score"):
        assert key in factors
        assert 0.0 <= factors[key] <= 1.0


def test_block_trade_factor_reflects_kalshis_own_is_block_trade_flag():
    # docs/kalshi/public-trades.md's is_block_trade - Kalshi's own real
    # first-party signal, found 2026-08-15 to have been parsed by services/
    # kalshi_trade_ws.py and never consumed anywhere downstream until now.
    provider = KalshiTradeTapeProvider()
    trade = _trade(count_fp="10000.00", yes_price_dollars="0.60", taker_side="yes", is_block_trade=True)
    ctx = {"markets": [_market()], "trade_tape": [trade], "cfg": {}}
    signals = asyncio.run(provider.fetch_signals(market_context=ctx))
    assert signals[0].factors["block_trade_factor"] == 1.0


def test_block_trade_factor_is_zero_when_not_a_block_trade():
    provider = KalshiTradeTapeProvider()
    trade = _trade(count_fp="10000.00", yes_price_dollars="0.60", taker_side="yes", is_block_trade=False)
    ctx = {"markets": [_market()], "trade_tape": [trade], "cfg": {}}
    signals = asyncio.run(provider.fetch_signals(market_context=ctx))
    assert signals[0].factors["block_trade_factor"] == 0.0


def test_agreement_factor_is_neutral_with_no_recent_history():
    provider = KalshiTradeTapeProvider()
    trade = _trade(count_fp="10000.00", yes_price_dollars="0.60", taker_side="yes")
    ctx = {"markets": [_market()], "trade_tape": [trade], "cfg": {}}
    signals = asyncio.run(provider.fetch_signals(market_context=ctx))
    assert signals[0].factors["agreement_factor"] == 0.5


def test_agreement_factor_reflects_recent_same_side_signals_on_the_same_ticker():
    signal_log.log_signal("TICK-A", "yes", 500, 0.7, "kalshi_trade_tape")
    signal_log.log_signal("TICK-A", "yes", 500, 0.7, "kalshi_trade_tape")
    signal_log.log_signal("TICK-A", "no", 500, 0.7, "kalshi_trade_tape")

    provider = KalshiTradeTapeProvider()
    trade = _trade(count_fp="10000.00", yes_price_dollars="0.60", taker_side="yes")
    ctx = {"markets": [_market()], "trade_tape": [trade], "cfg": {}}
    signals = asyncio.run(provider.fetch_signals(market_context=ctx))
    # 2 of 3 recent signals on this ticker agreed (yes) with this new yes print
    assert signals[0].factors["agreement_factor"] == pytest.approx(2 / 3)


def test_agreement_factor_ignores_signals_on_a_different_ticker():
    signal_log.log_signal("OTHER-TICKER", "no", 500, 0.7, "kalshi_trade_tape")

    provider = KalshiTradeTapeProvider()
    trade = _trade(ticker="TICK-A", count_fp="10000.00", yes_price_dollars="0.60", taker_side="yes")
    ctx = {"markets": [_market(ticker="TICK-A")], "trade_tape": [trade], "cfg": {}}
    signals = asyncio.run(provider.fetch_signals(market_context=ctx))
    assert signals[0].factors["agreement_factor"] == 0.5  # no history on TICK-A itself


# ---- cluster_factor wiring (docs/prediction-market-strategy-alignment-plan.md Part 2.1) ----

def test_cluster_factor_is_zero_with_no_recent_similar_prints():
    provider = KalshiTradeTapeProvider()
    trade = _trade(count_fp="10000.00", yes_price_dollars="0.60", taker_side="yes")
    ctx = {"markets": [_market()], "trade_tape": [trade], "cfg": {}}
    signals = asyncio.run(provider.fetch_signals(market_context=ctx))
    assert signals[0].factors["cluster_factor"] == 0.0


def test_cluster_factor_reflects_recent_size_compatible_prints_on_the_same_ticker():
    signal_log.log_signal("TICK-A", "yes", 9500, 0.7, "kalshi_trade_tape")
    signal_log.log_signal("TICK-A", "yes", 10500, 0.7, "kalshi_trade_tape")

    provider = KalshiTradeTapeProvider()
    trade = _trade(count_fp="10000.00", yes_price_dollars="0.60", taker_side="yes")
    ctx = {"markets": [_market()], "trade_tape": [trade], "cfg": {}}
    signals = asyncio.run(provider.fetch_signals(market_context=ctx))
    assert signals[0].factors["cluster_factor"] == pytest.approx(2 / 3)


# ---- trend_factor wiring (docs/prediction-market-strategy-alignment-plan.md Part 2.4) ----

def test_trend_factor_is_neutral_with_no_real_price_history():
    provider = KalshiTradeTapeProvider()
    trade = _trade(count_fp="10000.00", yes_price_dollars="0.60", taker_side="yes")
    ctx = {"markets": [_market()], "trade_tape": [trade], "cfg": {}}
    signals = asyncio.run(provider.fetch_signals(market_context=ctx))
    assert signals[0].factors["trend_factor"] == 0.5


def test_trend_factor_is_high_when_yes_print_agrees_with_a_rising_price():
    now = time.time()
    market_history.record_snapshots([{"ticker": "TICK-A", "yes_price": 0.50}], timestamp=now - 1000)
    market_history.record_snapshots([{"ticker": "TICK-A", "yes_price": 0.55}], timestamp=now)

    provider = KalshiTradeTapeProvider()
    trade = _trade(count_fp="10000.00", yes_price_dollars="0.60", taker_side="yes", created_time=None)
    ctx = {"markets": [_market()], "trade_tape": [trade], "cfg": {}}
    signals = asyncio.run(provider.fetch_signals(market_context=ctx))
    assert signals[0].factors["trend_factor"] == 1.0  # price rose 5c, yes print agrees, saturates at max


def test_trend_factor_is_low_when_yes_print_fights_a_falling_price():
    now = time.time()
    market_history.record_snapshots([{"ticker": "TICK-A", "yes_price": 0.55}], timestamp=now - 1000)
    market_history.record_snapshots([{"ticker": "TICK-A", "yes_price": 0.50}], timestamp=now)

    provider = KalshiTradeTapeProvider()
    trade = _trade(count_fp="10000.00", yes_price_dollars="0.60", taker_side="yes", created_time=None)
    ctx = {"markets": [_market()], "trade_tape": [trade], "cfg": {}}
    signals = asyncio.run(provider.fetch_signals(market_context=ctx))
    assert signals[0].factors["trend_factor"] == 0.0  # price fell 5c, yes print fights it, saturates at min


def test_trend_factor_flips_for_the_no_side():
    # Same falling price as above, but a NO print now agrees with it rather
    # than fighting it - the exact same real trend should score oppositely
    # depending on which side the print is on.
    now = time.time()
    market_history.record_snapshots([{"ticker": "TICK-A", "yes_price": 0.55}], timestamp=now - 1000)
    market_history.record_snapshots([{"ticker": "TICK-A", "yes_price": 0.50}], timestamp=now)

    provider = KalshiTradeTapeProvider()
    trade = _trade(count_fp="10000.00", yes_price_dollars="0.40", taker_side="no", created_time=None)
    ctx = {"markets": [_market()], "trade_tape": [trade], "cfg": {}}
    signals = asyncio.run(provider.fetch_signals(market_context=ctx))
    assert signals[0].factors["trend_factor"] == 1.0


def test_analyst_factor_is_neutral_with_no_analysis_on_file():
    provider = KalshiTradeTapeProvider()
    trade = _trade(count_fp="10000.00", yes_price_dollars="0.60", taker_side="yes")
    ctx = {"markets": [_market()], "trade_tape": [trade], "cfg": {}}
    signals = asyncio.run(provider.fetch_signals(market_context=ctx))
    assert signals[0].factors["analyst_factor"] == 0.5


def test_analyst_factor_agrees_with_a_yes_leaning_estimate():
    market_analyst_agent.record_analysis(
        "TICK-A", "TICK", 0.5, estimated_probability=0.8, llm_confidence=0.7, reasoning="r", model="m",
    )
    provider = KalshiTradeTapeProvider()
    trade = _trade(count_fp="10000.00", yes_price_dollars="0.60", taker_side="yes")
    ctx = {"markets": [_market()], "trade_tape": [trade], "cfg": {}}
    signals = asyncio.run(provider.fetch_signals(market_context=ctx))
    assert signals[0].factors["analyst_factor"] == 0.8


def test_analyst_factor_flips_for_the_no_side():
    # Same estimate as above (leans yes at 0.8), but a NO print now
    # disagrees with it rather than agreeing - the analyst's read should
    # score oppositely depending on which side the print is on.
    market_analyst_agent.record_analysis(
        "TICK-A", "TICK", 0.5, estimated_probability=0.8, llm_confidence=0.7, reasoning="r", model="m",
    )
    provider = KalshiTradeTapeProvider()
    trade = _trade(count_fp="10000.00", yes_price_dollars="0.40", taker_side="no")
    ctx = {"markets": [_market()], "trade_tape": [trade], "cfg": {}}
    signals = asyncio.run(provider.fetch_signals(market_context=ctx))
    assert signals[0].factors["analyst_factor"] == pytest.approx(0.2)


def test_analyst_factor_ignores_a_stale_analysis():
    market_analyst_agent.record_analysis(
        "TICK-A", "TICK", 0.5, estimated_probability=0.9, llm_confidence=0.7, reasoning="r", model="m",
        analyzed_at=time.time() - 2 * 86400,  # 2 days old, past the 24h freshness window
    )
    provider = KalshiTradeTapeProvider()
    trade = _trade(count_fp="10000.00", yes_price_dollars="0.60", taker_side="yes")
    ctx = {"markets": [_market()], "trade_tape": [trade], "cfg": {}}
    signals = asyncio.run(provider.fetch_signals(market_context=ctx))
    assert signals[0].factors["analyst_factor"] == 0.5


# --- invalid data must never become a logged signal (2026-08-17) ---------

def test_missing_price_is_refused_not_defaulted_to_zero():
    """The root cause of positions at a unit cost of 1.00: the old
    `float(trade.get("yes_price_dollars") or 0)` turned a MISSING price into
    0.0, which is not a missing value but a plausible one. Every gate
    downstream then reasoned correctly about a number Kalshi never sent."""
    from services.whalewatchers.kalshi_trade_tape import _price_dollars

    assert _price_dollars({"yes_price_dollars": "0.61"}, "yes_price_dollars") == 0.61
    assert _price_dollars({}, "yes_price_dollars") is None
    assert _price_dollars({"yes_price_dollars": None}, "yes_price_dollars") is None
    assert _price_dollars({"yes_price_dollars": ""}, "yes_price_dollars") is None
    assert _price_dollars({"yes_price_dollars": "abc"}, "yes_price_dollars") is None


def test_notional_refuses_rather_than_inventing_a_zero():
    """A notional built from a fabricated zero reads as 'tiny trade' and is
    filtered out for the wrong reason - invisible in the rejection stats."""
    assert _notional_usd({"count_fp": "100", "yes_price_dollars": "0.6"}, "yes") == 60.0
    assert _notional_usd({"count_fp": "100"}, "yes") is None
    assert _notional_usd({"yes_price_dollars": "0.6"}, "yes") is None


def test_prints_at_the_price_extremes_never_become_signals(monkeypatch, tmp_path):
    """Not merely untraded - never LOGGED. main.py logs every signal this
    provider returns, and signal_log is what every accuracy statistic reads.
    Measured on real data: 40.7% of logged signals sat outside the tradeable
    range, they resolved 'correct' 99.2% of the time, and they inflated the
    headline whale accuracy from a true 77.5% to 86.3%."""
    from services import candidate_log, signal_log as sl
    from services import market_analyst_agent, market_history, series_evaluator

    for mod in (candidate_log, sl, market_history, series_evaluator, market_analyst_agent):
        monkeypatch.setattr(mod, "DB_PATH", tmp_path / f"{mod.__name__.split('.')[-1]}.db", raising=False)

    provider = KalshiTradeTapeProvider()
    markets = [{"ticker": "TICK-A", "volume_24h_fp": 100000, "yes_ask_dollars": 0.5,
                "close_time": None}]
    cfg = {"whale_watcher_kalshi": {"min_notional_usd": 100}}

    def trade(tid, yes_price, no_price, outcome):
        return {"trade_id": tid, "ticker": "TICK-A", "count_fp": "100000",
                "yes_price_dollars": yes_price, "no_price_dollars": no_price,
                "taker_outcome_side": outcome, "is_block_trade": False}

    tape = [
        trade("t1", "0.00", "1.00", "no"),   # unit cost 1.00
        trade("t2", "0.01", "0.99", "yes"),  # unit cost 0.01
        trade("t3", "0.99", "0.01", "yes"),  # unit cost 0.99
        trade("t4", "0.60", "0.40", "yes"),  # legitimate
    ]
    signals = provider._process_trades_sync(
        tape, markets, {"TICK-A": markets[0]}, cfg, time.time(),
    )
    assert [s.id for s in signals] == ["t4"], "only the tradeable print may become a signal"
