from services.mutual_exclusivity import find_me_pairs


def _market(ticker, event_ticker, yes_bid=0.5):
    return {"ticker": ticker, "event_ticker": event_ticker, "yes_bid_dollars": yes_bid}


def test_pairs_two_siblings_when_kalshi_flag_is_true():
    markets = [
        _market("TEAMA", "EVT-1", 0.6),
        _market("TEAMB", "EVT-1", 0.4),
    ]
    event_titles = {"EVT-1": {"mutually_exclusive": True}}
    pairs = find_me_pairs(markets, event_titles)
    assert pairs == {"TEAMA": "TEAMB", "TEAMB": "TEAMA"}


def test_no_pair_when_kalshi_flag_is_explicitly_false():
    # Independent props sharing an event (e.g. two different players' prop
    # bets in the same game) - Kalshi says False, not a pair even if two
    # siblings happen to exist.
    markets = [
        _market("PROP-A", "EVT-1", 0.6),
        _market("PROP-B", "EVT-1", 0.4),
    ]
    event_titles = {"EVT-1": {"mutually_exclusive": False}}
    assert find_me_pairs(markets, event_titles) == {}


def test_price_sum_fallback_when_flag_not_yet_backfilled():
    markets = [
        _market("TEAMA", "EVT-1", 0.63),
        _market("TEAMB", "EVT-1", 0.37),
    ]
    event_titles = {"EVT-1": {"mutually_exclusive": None}}
    assert find_me_pairs(markets, event_titles) == {"TEAMA": "TEAMB", "TEAMB": "TEAMA"}


def test_price_sum_fallback_rejects_unrelated_siblings():
    markets = [
        _market("PROP-A", "EVT-1", 0.60),
        _market("PROP-B", "EVT-1", 0.55),  # sums to 1.15 - not a complementary pair
    ]
    event_titles = {"EVT-1": {"mutually_exclusive": None}}
    assert find_me_pairs(markets, event_titles) == {}


def test_missing_event_titles_entry_falls_back_to_price_heuristic():
    # A missing entry (event_titles={}) is indistinguishable from an
    # explicit mutually_exclusive=None here - both mean "we don't have
    # Kalshi's own flag yet," so both fall back to the same price-sum
    # heuristic rather than silently never pairing.
    markets = [_market("TEAMA", "EVT-1", 0.6), _market("TEAMB", "EVT-1", 0.4)]
    assert find_me_pairs(markets, {}) == {"TEAMA": "TEAMB", "TEAMB": "TEAMA"}


def test_no_pair_for_three_or_more_siblings():
    # N-way mutually-exclusive event (e.g. a tournament-winner market) -
    # mutually_exclusive can be True but there's no simple pairwise
    # complement, so this must not pair any of them.
    markets = [
        _market("GOLFER-A", "EVT-1", 0.3),
        _market("GOLFER-B", "EVT-1", 0.3),
        _market("GOLFER-C", "EVT-1", 0.4),
    ]
    event_titles = {"EVT-1": {"mutually_exclusive": True}}
    assert find_me_pairs(markets, event_titles) == {}


def test_no_pair_for_single_market_event():
    markets = [_market("SOLO", "EVT-1", 0.6)]
    event_titles = {"EVT-1": {"mutually_exclusive": True}}
    assert find_me_pairs(markets, event_titles) == {}


def test_multiple_independent_events_each_paired_correctly():
    markets = [
        _market("A1", "EVT-1", 0.6), _market("A2", "EVT-1", 0.4),
        _market("B1", "EVT-2", 0.7), _market("B2", "EVT-2", 0.3),
    ]
    event_titles = {"EVT-1": {"mutually_exclusive": True}, "EVT-2": {"mutually_exclusive": True}}
    pairs = find_me_pairs(markets, event_titles)
    assert pairs == {"A1": "A2", "A2": "A1", "B1": "B2", "B2": "B1"}


def test_markets_missing_ticker_or_event_ticker_are_ignored():
    markets = [
        {"ticker": "A1", "event_ticker": "EVT-1", "yes_bid_dollars": 0.5},
        {"ticker": None, "event_ticker": "EVT-1", "yes_bid_dollars": 0.5},
        {"ticker": "A3", "event_ticker": None, "yes_bid_dollars": 0.5},
    ]
    event_titles = {"EVT-1": {"mutually_exclusive": True}}
    assert find_me_pairs(markets, event_titles) == {}
