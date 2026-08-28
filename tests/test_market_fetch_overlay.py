"""P7 Task 29 (redesigned 2026-08-27): the live-price overlay in
services/market_watch/market_fetch.py is age-aware. WS stays primary while
it is actually flowing; a WS-quiet ticker's in-memory price stops being
copied forward forever and is refreshed from the REST row once it is older
than the system's own REST-staleness bound (_PINNED_MARKET_REFRESH_SEC).
Pure helper, no network, no state module - a plain dict stands in for
services.app_state.state."""
from services.market_watch import market_fetch
from services.market_watch.discovery_cache import _PINNED_MARKET_REFRESH_SEC as BOUND

NOW = 10_000.0


def _state(prices=None, asks=None, prices_at=None, asks_at=None) -> dict:
    return {
        "latest_prices": dict(prices or {}),
        "latest_asks": dict(asks or {}),
        "latest_prices_updated_at": dict(prices_at or {}),
        "latest_asks_updated_at": dict(asks_at or {}),
    }


def test_ws_fresh_price_beats_the_rest_row_and_keeps_its_stamp():
    state = _state(prices={"K1": 0.60}, prices_at={"K1": NOW - 5.0})
    rows = [{"ticker": "K1", "yes_bid_dollars": "0.40"}]

    out = market_fetch.overlay_live_prices(rows, state, now=NOW)

    assert out[0]["yes_bid_dollars"] == 0.60
    assert state["latest_prices_updated_at"]["K1"] == NOW - 5.0  # untouched


def test_stale_in_memory_price_loses_to_the_rest_row_and_is_restamped():
    state = _state(prices={"K1": 0.60}, prices_at={"K1": NOW - BOUND - 1.0})
    rows = [{"ticker": "K1", "yes_bid_dollars": "0.40"}]

    out = market_fetch.overlay_live_prices(rows, state, now=NOW)

    assert out[0]["yes_bid_dollars"] == "0.40"  # REST's own value, untouched type
    assert state["latest_prices_updated_at"]["K1"] == NOW


def test_known_price_with_no_timestamp_is_not_trusted_and_loses_to_rest():
    # Legacy/unknown age: an entry with no stamp (e.g. seeded before this shipped)
    # must not be copied forward as if fresh.
    state = _state(prices={"K1": 0.60})
    rows = [{"ticker": "K1", "yes_bid_dollars": "0.40"}]

    out = market_fetch.overlay_live_prices(rows, state, now=NOW)

    assert out[0]["yes_bid_dollars"] == "0.40"
    assert state["latest_prices_updated_at"]["K1"] == NOW


def test_never_seen_ticker_keeps_the_rest_value_and_is_stamped():
    state = _state()
    rows = [{"ticker": "K2", "yes_bid_dollars": "0.30"}]

    out = market_fetch.overlay_live_prices(rows, state, now=NOW)

    assert out[0]["yes_bid_dollars"] == "0.30"
    assert state["latest_prices_updated_at"]["K2"] == NOW


def test_asks_follow_the_same_rules_through_their_own_dicts():
    state = _state(
        asks={"FRESH": 0.55, "STALE": 0.65},
        asks_at={"FRESH": NOW - 1.0, "STALE": NOW - BOUND - 1.0},
    )
    rows = [
        {"ticker": "FRESH", "yes_ask_dollars": "0.50"},
        {"ticker": "STALE", "yes_ask_dollars": "0.70"},
        {"ticker": "NOASK"},  # a row with no ask stays without one - never fabricated
    ]

    out = market_fetch.overlay_live_prices(rows, state, now=NOW)

    assert out[0]["yes_ask_dollars"] == 0.55
    assert out[1]["yes_ask_dollars"] == "0.70"
    assert state["latest_asks_updated_at"]["STALE"] == NOW
    assert "yes_ask_dollars" not in out[2]


def test_input_rows_are_never_mutated():
    state = _state(prices={"K1": 0.60}, prices_at={"K1": NOW})
    rows = [{"ticker": "K1", "yes_bid_dollars": "0.40"}]

    market_fetch.overlay_live_prices(rows, state, now=NOW)

    assert rows[0]["yes_bid_dollars"] == "0.40"
