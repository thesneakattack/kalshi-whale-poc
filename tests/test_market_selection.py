"""Frozen market-selection policy behavior — moved with the implementation
from services/kalshi_client.py to services/market_watch/selection.py
(Kalshi Integration Phase A Task A7). Every test here predates the move
(tests/test_kalshi_client.py's policy section, frozen per the A7 plan
before the code moved); only the call target changed
(selection.round_robin_select instead of KalshiClient.round_robin_select,
candidate_markets/top_volume_markets taking the gateway as a parameter).
The gateway stand-in is a minimal object with get_markets — the only
vendor surface the policy layer consumes.
"""
import asyncio

from services.market_watch import selection


def _market(ticker, event_ticker, volume):
    return {"ticker": ticker, "event_ticker": event_ticker, "volume_24h_fp": str(volume)}


class _FakeGateway:
    """The one vendor method selection consumes, faked per-test."""

    def __init__(self, markets_by_series=None):
        self._by_series = markets_by_series or {}

    async def get_markets(self, limit, status, series_ticker=None):
        return self._by_series.get(series_ticker, [])


def test_selected_series_includes_every_child_by_default():
    # Direct request: "i want every child" - no cap unless
    # max_children_per_parent is explicitly set. Includes every event under
    # the series, not just its highest-volume one.
    markets = [_market(f"A-{i}", f"EVT-A{i}", 100 - i) for i in range(10)]
    result = selection.round_robin_select(markets, n=1)
    assert len(result) == 10
    assert [m["ticker"] for m in result] == [f"A-{i}" for i in range(10)]


def test_max_children_per_parent_caps_highest_volume_first():
    # Direct request: "i want the ability to cap" + "children markets
    # prioritized by volume."
    markets = [_market(f"A-{i}", f"EVT-A{i}", 100 - i) for i in range(10)]
    result = selection.round_robin_select(markets, n=1, max_children_per_parent=3)
    assert [m["ticker"] for m in result] == ["A-0", "A-1", "A-2"]


def test_a_dominant_series_dominant_parents_prefers_higher_volume_series_when_n_is_limited():
    # Three distinct series competing for 2 slots - A and B should win
    # (higher best-child volume), C should be excluded entirely.
    a_markets = [_market(f"A-{i}", f"EVT-A{i}", 1000 - i) for i in range(8)]
    b_markets = [_market("B-1", "EVT-B1", 500), _market("B-2", "EVT-B2", 400)]
    c_markets = [_market("C-1", "EVT-C1", 50)]
    candidates = a_markets + b_markets + c_markets  # already volume-sorted, as candidate_markets produces

    result = selection.round_robin_select(candidates, n=2)
    series = {m["ticker"].split("-")[0] for m in result}
    assert series == {"A", "B"}
    assert not any(m["ticker"].startswith("C-") for m in result)


def test_real_wyndham_regression_one_dominant_series_costs_exactly_one_slot():
    # Direct regression: a whole tournament (23 real head-to-head pairings,
    # all sharing series "KXPGAH2H") must cost exactly one watchlist slot,
    # not 23 - the exact confirmed-live bug this whole feature fixed.
    golf = [_market(f"KXPGAH2H-EVT{i}-X", f"KXPGAH2H-EVT{i}", 1000 - i) for i in range(23)]
    dota = [_market("KXDOTA2MAP-A-X", "KXDOTA2MAP-A", 500)]
    soccer = [_market("KXCLUBFBTTS-A-X", "KXCLUBFBTTS-A", 300)]
    candidates = golf + dota + soccer

    result = selection.round_robin_select(candidates, n=3)
    series = {m["ticker"].split("-")[0] for m in result}
    assert series == {"KXPGAH2H", "KXDOTA2MAP", "KXCLUBFBTTS"}  # all 3 series fit in 3 slots
    assert len(result) == 23 + 1 + 1  # golf's 23 pairings all came along for free under its one slot


def test_concurrent_events_under_one_series_are_watched_together_as_one_parent():
    # The explicit, accepted tradeoff: two different, concurrently-live
    # matches sharing one series (two separate Dota2 games, both under
    # "KXDOTA2MAP") are watched together under that series's single slot,
    # not counted as two - direct instruction: "i dont want those pairings
    # to count against the watchlist count, only the parent series."
    markets = [
        _market("KXDOTA2MAP-ILLJEN-2-JEN", "KXDOTA2MAP-ILLJEN-2", 100),
        _market("KXDOTA2MAP-ILLJEN-2-ILL", "KXDOTA2MAP-ILLJEN-2", 95),
        _market("KXDOTA2MAP-NHSPIRIT-2-NH", "KXDOTA2MAP-NHSPIRIT-2", 90),
        _market("KXDOTA2MAP-NHSPIRIT-2-SPIRIT", "KXDOTA2MAP-NHSPIRIT-2", 85),
    ]
    result = selection.round_robin_select(markets, n=1)
    assert len(result) == 4  # both matches' markets included under the one series slot
    assert {m["event_ticker"] for m in result} == {"KXDOTA2MAP-ILLJEN-2", "KXDOTA2MAP-NHSPIRIT-2"}


def test_tickers_sharing_a_prefix_are_the_same_parent_even_with_no_event_ticker():
    # No event_ticker at all - falls back to the ticker prefix (series).
    # Two tickers sharing a prefix collide into one parent, matching the
    # series-level grouping used everywhere else in this function.
    markets = [{"ticker": "SOLO-1", "volume_24h_fp": "50"}, {"ticker": "SOLO-2", "volume_24h_fp": "40"}]
    result = selection.round_robin_select(markets, n=1)
    assert {m["ticker"] for m in result} == {"SOLO-1", "SOLO-2"}


def test_round_robin_select_never_pads_below_n():
    a_markets = [_market("A-1", "EVT-A", 100)]
    result = selection.round_robin_select(a_markets, n=10)
    assert result == a_markets  # only one real series available - stays at 1, not padded to 10


def test_min_volume_filters_before_selection():
    gateway = _FakeGateway({"SER-A": [_market("A-1", "EVT-A", 500), _market("A-2", "EVT-A", 5)]})

    result = asyncio.run(selection.top_volume_markets(gateway, n=10, min_volume=100, series_tickers=["SER-A"]))
    assert [m["ticker"] for m in result] == ["A-1"]


def test_no_series_tickers_returns_empty():
    result = asyncio.run(selection.top_volume_markets(_FakeGateway(), n=10, min_volume=0, series_tickers=[]))
    assert result == []


# top_volume_markets is candidate_markets + round_robin_select composed
# together (see main.py's live-markets-only discovery, which needs to
# filter the candidate pool between the two steps) - these confirm each
# half independently, on top of top_volume_markets' coverage above
# confirming the composition behaves identically end to end.

def test_candidate_markets_returns_full_volume_sorted_pool_no_cutoff():
    gateway = _FakeGateway({"SER-A": [_market(f"A-{i}", "EVT-A", 100 - i) for i in range(10)]})

    result = asyncio.run(selection.candidate_markets(gateway, min_volume=0, series_tickers=["SER-A"]))
    assert len(result) == 10  # no n/cutoff - the whole real candidate pool
    assert [m["ticker"] for m in result] == [f"A-{i}" for i in range(10)]  # volume-sorted desc


def test_candidate_markets_applies_min_volume_filter():
    gateway = _FakeGateway({"SER-A": [_market("A-1", "EVT-A", 500), _market("A-2", "EVT-A", 5)]})

    result = asyncio.run(selection.candidate_markets(gateway, min_volume=100, series_tickers=["SER-A"]))
    assert [m["ticker"] for m in result] == ["A-1"]


def test_candidate_markets_no_series_tickers_returns_empty():
    result = asyncio.run(selection.candidate_markets(_FakeGateway(), min_volume=0, series_tickers=[]))
    assert result == []


def test_top_volume_markets_threads_max_children_per_parent():
    gateway = _FakeGateway({"SER-A": [_market(f"A-{i}", "EVT-A", 100 - i) for i in range(10)]})

    result = asyncio.run(selection.top_volume_markets(
        gateway, n=1, min_volume=0, series_tickers=["SER-A"], max_children_per_parent=2,
    ))
    assert [m["ticker"] for m in result] == ["A-0", "A-1"]
