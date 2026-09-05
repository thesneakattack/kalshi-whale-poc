"""main._sparse_price_dict - issue #577's root fix for trading_loop's
latest_prices/latest_asks rebuild. Before this fix, latest_prices alone
defaulted a missing/falsy bid to a fabricated 0.5 (`float(m.get(...) or
0.5)`), indistinguishable from a real 0.5 bid and, worse, ALSO fabricated
on a real 0.0 bid since `or` is a falsy check, not a missing check.
latest_asks already built its dict the honest way; this function is the
one rule both dicts now share, tested directly here since trading_loop
itself is an unrunnable while-True (see test_main_scheduler_loops.py's
own docstring for why main.py's giant loop functions get pulled apart
into pure, separately-tested pieces instead)."""
import main


def test_a_real_nonzero_bid_is_kept():
    markets = [{"ticker": "T1", "yes_bid_dollars": "0.6200"}]
    assert main._sparse_price_dict(markets, "yes_bid_dollars") == {"T1": 0.62}


def test_a_real_zero_bid_is_kept_not_fabricated_to_0_5():
    # The exact regression this issue is about: 0.0 is falsy, so the old
    # `m.get("yes_bid_dollars") or 0.5` silently turned a real 0.0 bid into
    # a fabricated 0.5. This function must preserve it.
    markets = [{"ticker": "T1", "yes_bid_dollars": "0.0000"}]
    assert main._sparse_price_dict(markets, "yes_bid_dollars") == {"T1": 0.0}


def test_a_real_zero_bid_as_a_float_is_also_kept():
    # Wire type is documented as string (docs/kalshi/market-ticker.md), but
    # this app's own overlay_live_prices mixes in float values from
    # latest_prices itself - the parser must handle both.
    markets = [{"ticker": "T1", "yes_bid_dollars": 0.0}]
    assert main._sparse_price_dict(markets, "yes_bid_dollars") == {"T1": 0.0}


def test_a_missing_bid_key_is_omitted_not_defaulted():
    markets = [{"ticker": "T1"}]
    assert main._sparse_price_dict(markets, "yes_bid_dollars") == {}


def test_a_none_bid_is_omitted():
    markets = [{"ticker": "T1", "yes_bid_dollars": None}]
    assert main._sparse_price_dict(markets, "yes_bid_dollars") == {}


def test_an_empty_string_bid_is_omitted():
    markets = [{"ticker": "T1", "yes_bid_dollars": ""}]
    assert main._sparse_price_dict(markets, "yes_bid_dollars") == {}


def test_a_malformed_bid_is_omitted_not_raised():
    markets = [{"ticker": "T1", "yes_bid_dollars": "not-a-number"}]
    assert main._sparse_price_dict(markets, "yes_bid_dollars") == {}


def test_a_market_with_no_ticker_is_skipped_entirely():
    markets = [{"yes_bid_dollars": "0.50"}, {"ticker": "", "yes_bid_dollars": "0.50"}]
    assert main._sparse_price_dict(markets, "yes_bid_dollars") == {}


def test_mixed_batch_only_real_prices_survive():
    markets = [
        {"ticker": "REAL", "yes_bid_dollars": "0.71"},
        {"ticker": "ZERO", "yes_bid_dollars": "0.0000"},
        {"ticker": "MISSING"},
        {"ticker": "EMPTY", "yes_bid_dollars": ""},
    ]
    assert main._sparse_price_dict(markets, "yes_bid_dollars") == {"REAL": 0.71, "ZERO": 0.0}


def test_works_the_same_for_the_ask_field_matching_latest_asks_prior_behavior():
    markets = [
        {"ticker": "T1", "yes_ask_dollars": "0.66"},
        {"ticker": "T2", "yes_ask_dollars": None},
    ]
    assert main._sparse_price_dict(markets, "yes_ask_dollars") == {"T1": 0.66}
