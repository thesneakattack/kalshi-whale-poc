import pytest

from services import mutual_exclusivity
from services.mutual_exclusivity import find_me_pairs, find_open_confirmed_conflict, me_pairing_stats


@pytest.fixture(autouse=True)
def _reset_me_pairing_stats():
    # Same xdist cross-test leak shape this branch already found and fixed
    # once for state["milestone_by_event"] (tests/test_trading_gate.py) -
    # _me_pairing_stats is a bare module-level dict with no reset between
    # tests otherwise, so an absolute-value assertion in one test would be
    # polluted by whatever ran earlier in the same pytest-xdist worker
    # (code-review finding, 2026-08-30).
    mutual_exclusivity._me_pairing_stats["me_pairing_unknown_total"] = 0
    yield


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


def test_find_open_confirmed_conflict_returns_the_open_sibling():
    market_titles = {
        "BON": {"event_ticker": "EVT-1"},
        "BUS": {"event_ticker": "EVT-1"},
    }
    event_titles = {"EVT-1": {"mutually_exclusive": True}}
    assert find_open_confirmed_conflict("BUS", market_titles, event_titles, {"BON"}) == "BON"


def test_find_open_confirmed_conflict_none_when_no_sibling_open():
    market_titles = {
        "BON": {"event_ticker": "EVT-1"},
        "BUS": {"event_ticker": "EVT-1"},
    }
    event_titles = {"EVT-1": {"mutually_exclusive": True}}
    assert find_open_confirmed_conflict("BUS", market_titles, event_titles, set()) is None


def test_find_open_confirmed_conflict_none_when_confirmed_false_and_not_counted():
    # Kalshi's own confirmed False is a real, determined non-conflict - the
    # opposite of "unknown," so it must NOT inflate me_pairing_unknown_total.
    market_titles = {
        "BON": {"event_ticker": "EVT-1"},
        "BUS": {"event_ticker": "EVT-1"},
    }
    event_titles = {"EVT-1": {"mutually_exclusive": False}}
    before = me_pairing_stats()["me_pairing_unknown_total"]
    assert find_open_confirmed_conflict("BUS", market_titles, event_titles, {"BON"}) is None
    assert me_pairing_stats()["me_pairing_unknown_total"] == before


def test_find_open_confirmed_conflict_counts_flag_not_yet_backfilled():
    # mutually_exclusive=None (an event_titles entry exists, but Kalshi's
    # own flag hasn't been backfilled yet) is genuinely undetermined, not a
    # confirmed non-conflict (code-review fix, 2026-08-30: this used to be
    # silently indistinguishable from a real False).
    market_titles = {
        "BON": {"event_ticker": "EVT-1"},
        "BUS": {"event_ticker": "EVT-1"},
    }
    event_titles = {"EVT-1": {"mutually_exclusive": None}}
    before = me_pairing_stats()["me_pairing_unknown_total"]
    assert find_open_confirmed_conflict("BUS", market_titles, event_titles, {"BON"}) is None
    assert me_pairing_stats()["me_pairing_unknown_total"] == before + 1


def test_find_open_confirmed_conflict_counts_missing_event_titles_entry():
    # event_titles has NO entry at all for the event - the same "not yet
    # fetched" meaning as an explicit None flag, same counting treatment.
    market_titles = {
        "BON": {"event_ticker": "EVT-1"},
        "BUS": {"event_ticker": "EVT-1"},
    }
    before = me_pairing_stats()["me_pairing_unknown_total"]
    assert find_open_confirmed_conflict("BUS", market_titles, {}, {"BON"}) is None
    assert me_pairing_stats()["me_pairing_unknown_total"] == before + 1


def test_find_open_confirmed_conflict_counts_missing_event_ticker_on_market_titles():
    # market_titles has an entry for the candidate, but it carries no
    # event_ticker yet - a different "can't tell" shape than a wholly
    # missing market_titles entry, same counting treatment.
    market_titles = {"BON": {}, "BUS": {"event_ticker": "EVT-1"}}
    event_titles = {"EVT-1": {"mutually_exclusive": True}}
    before = me_pairing_stats()["me_pairing_unknown_total"]
    assert find_open_confirmed_conflict("BON", market_titles, event_titles, {"BUS"}) is None
    assert me_pairing_stats()["me_pairing_unknown_total"] == before + 1


def test_find_open_confirmed_conflict_ignores_a_different_event():
    market_titles = {
        "BON": {"event_ticker": "EVT-1"},
        "OTHER": {"event_ticker": "EVT-2"},
    }
    event_titles = {
        "EVT-1": {"mutually_exclusive": True},
        "EVT-2": {"mutually_exclusive": True},
    }
    assert find_open_confirmed_conflict("BON", market_titles, event_titles, {"OTHER"}) is None


def test_find_open_confirmed_conflict_never_returns_the_candidate_itself():
    market_titles = {"BON": {"event_ticker": "EVT-1"}}
    event_titles = {"EVT-1": {"mutually_exclusive": True}}
    assert find_open_confirmed_conflict("BON", market_titles, event_titles, {"BON"}) is None


def test_find_open_confirmed_conflict_counts_missing_market_titles_entry():
    before = me_pairing_stats()["me_pairing_unknown_total"]
    assert find_open_confirmed_conflict("UNKNOWN", {}, {}, {"BON"}) is None
    after = me_pairing_stats()["me_pairing_unknown_total"]
    assert after == before + 1


def test_find_open_confirmed_conflict_does_not_count_a_genuine_no_conflict():
    market_titles = {"BON": {"event_ticker": "EVT-1"}, "BUS": {"event_ticker": "EVT-1"}}
    event_titles = {"EVT-1": {"mutually_exclusive": True}}
    before = me_pairing_stats()["me_pairing_unknown_total"]
    assert find_open_confirmed_conflict("BUS", market_titles, event_titles, set()) is None
    assert me_pairing_stats()["me_pairing_unknown_total"] == before


def test_find_open_confirmed_conflict_does_not_block_an_n_way_event():
    # The bound this function shares with find_me_pairs above (and with
    # position_netting.find_groups' identical `len(members) != 2` proxy):
    # two positions already open on one confirmed-ME event means the app is
    # holding a SUBSET of a larger N-way field (a golf tournament, a
    # multi-candidate election), not a head-to-head pair - out of scope for
    # this entry-side gate. Live-verified 2026-08-30: 314 mutually_exclusive
    # events in data/title_cache.db have 3+ cached sibling markets, up to 81
    # outcomes (KXPGATOUR-WYC26). Without the bound, GOLFER-C would be
    # blocked here purely because GOLFER-A trivially matches the event
    # first.
    market_titles = {
        "GOLFER-A": {"event_ticker": "EVT-1"},
        "GOLFER-B": {"event_ticker": "EVT-1"},
        "GOLFER-C": {"event_ticker": "EVT-1"},
    }
    event_titles = {"EVT-1": {"mutually_exclusive": True}}
    assert find_open_confirmed_conflict(
        "GOLFER-C", market_titles, event_titles, {"GOLFER-A", "GOLFER-B"},
    ) is None


def test_find_open_confirmed_conflict_still_blocks_the_genuine_second_leg():
    # The other side of the same bound: exactly ONE other open position on
    # the event is the real head-to-head case this gate exists for (the
    # verified KXATPMATCH-26AUG28BUSBON failure), and it still blocks.
    market_titles = {
        "BON": {"event_ticker": "EVT-1"},
        "BUS": {"event_ticker": "EVT-1"},
        "UNRELATED": {"event_ticker": "EVT-2"},
    }
    event_titles = {"EVT-1": {"mutually_exclusive": True}, "EVT-2": {"mutually_exclusive": True}}
    # An open position on a DIFFERENT event doesn't count toward the bound.
    assert find_open_confirmed_conflict(
        "BUS", market_titles, event_titles, {"BON", "UNRELATED"},
    ) == "BON"


def test_find_open_confirmed_conflict_n_way_bound_ignores_the_candidate_itself():
    # A re-entry signal on a ticker already open must not inflate the
    # same-event count: candidate + 1 real sibling is still the 2-outcome
    # case, not an N-way field.
    market_titles = {
        "BON": {"event_ticker": "EVT-1"},
        "BUS": {"event_ticker": "EVT-1"},
    }
    event_titles = {"EVT-1": {"mutually_exclusive": True}}
    assert find_open_confirmed_conflict(
        "BUS", market_titles, event_titles, {"BON", "BUS"},
    ) == "BON"
