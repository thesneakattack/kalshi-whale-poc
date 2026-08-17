"""services/settlement_edge.py - the hypothesis test, and its refusal to
return a verdict before it has earned one.
"""
import sqlite3

import pytest

from services import settlement_edge as se


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    from services import index_feed

    monkeypatch.setattr(se, "DB_PATH", tmp_path / "settlement_edge.db")
    monkeypatch.setattr(se, "_buffer", [])
    monkeypatch.setattr(index_feed, "DB_PATH", tmp_path / "index_feed.db")
    monkeypatch.setattr(index_feed, "_latest", {})
    monkeypatch.setattr(index_feed, "_tick_buffer", [])
    yield


_SPEC = {
    "supported": True, "ticker": "KXBTC15M-A", "index_id": "BRTI", "strike": 63500.0,
    "comparison": ">=", "close_time": "2026-08-17T06:00:00Z", "settlement_timer_seconds": 1,
}


def _projection(known, partial, spot, required):
    return {"status": "accumulating", "observations_known": known, "partial_average": partial,
            "spot": spot, "required_remaining": required,
            "gap_from_spot": required - spot, "seconds_remaining": 60 - known}


def test_only_records_while_the_window_is_open():
    assert se.record_observation("KXBTC15M-A", _SPEC,
                                 {"status": "outside_window"}, 0.5) is False
    assert se.record_observation("KXBTC15M-A", _SPEC,
                                 {"status": "determined"}, 0.5) is False
    assert se.record_observation("KXBTC15M-A", _SPEC,
                                 _projection(30, 63490.0, 63495.0, 63510.0), 0.5) is True


def test_records_both_forecasts_at_the_same_instant():
    """Neither predictor may be reconstructed later - hindsight would leak
    straight into the comparison this module exists to make."""
    se.record_observation("KXBTC15M-A", _SPEC,
                          _projection(30, 63490.0, 63495.0, 63510.0), 0.62, now=1000.0)
    se.flush()
    with sqlite3.connect(se.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM window_observations").fetchone()
    assert row["market_yes_price"] == pytest.approx(0.62)
    assert row["partial_average"] == pytest.approx(63490.0)
    assert row["required_remaining"] == pytest.approx(63510.0)
    assert row["observations_known"] == 30
    assert row["settled_yes"] is None          # not known yet, and not guessed


def test_resolve_window_stamps_every_observation_of_that_market():
    for k in (10, 20, 30):
        se.record_observation("KXBTC15M-A", _SPEC,
                              _projection(k, 63490.0, 63495.0, 63510.0), 0.6)
    se.record_observation("KXBTC15M-B", _SPEC | {"ticker": "KXBTC15M-B"},
                          _projection(10, 1.0, 1.0, 1.0), 0.4)
    se.flush()

    assert se.resolve_window("KXBTC15M-A", settled_yes=True) == 3
    with sqlite3.connect(se.DB_PATH) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM window_observations WHERE settled_yes IS NULL").fetchone()[0] == 1


def test_projected_probability_is_higher_when_the_index_sits_above_what_it_needs():
    row_easy = {"required_remaining": 63400.0, "spot": 63500.0, "observations_known": 30}
    row_hard = {"required_remaining": 63600.0, "spot": 63500.0, "observations_known": 30}
    p_easy = se.projected_probability(row_easy, index_volatility=5.0)
    p_hard = se.projected_probability(row_hard, index_volatility=5.0)
    assert 0.5 < p_easy <= 1.0
    assert 0.0 <= p_hard < 0.5
    # Exactly at the requirement, it's a coin flip - the random walk's
    # expected future value is where it stands now.
    assert se.projected_probability(
        {"required_remaining": 63500.0, "spot": 63500.0, "observations_known": 30},
        index_volatility=5.0) == pytest.approx(0.5)


def test_certainty_grows_as_the_window_fills():
    """The same dollar gap is far more decisive with 5 seconds left than
    with 50, because fewer observations remain to move the average."""
    early = se.projected_probability(
        {"required_remaining": 63450.0, "spot": 63500.0, "observations_known": 10}, 5.0)
    late = se.projected_probability(
        {"required_remaining": 63450.0, "spot": 63500.0, "observations_known": 55}, 5.0)
    assert late > early


def test_projected_probability_needs_volatility_and_says_so():
    row = {"required_remaining": 1.0, "spot": 2.0, "observations_known": 30}
    assert se.projected_probability(row, index_volatility=None) is None
    assert se.projected_probability({**row, "observations_known": 60}, 5.0) is None


def test_report_refuses_a_verdict_before_it_has_earned_one():
    assert se.edge_report()["status"] == "insufficient"

    se.record_observation("KXBTC15M-A", _SPEC,
                          _projection(30, 63490.0, 63495.0, 63510.0), 0.6)
    se.flush()
    se.resolve_window("KXBTC15M-A", settled_yes=True)
    out = se.edge_report(min_samples=200)
    # One resolved observation is not evidence, and the module says so
    # rather than declaring a winner off a sample of one.
    assert out["status"] == "insufficient"


def test_report_scores_both_forecasts_once_there_is_enough_data(monkeypatch):
    from services import index_feed

    monkeypatch.setattr(index_feed, "recent_volatility", lambda *a, **k: 5.0)
    # The projection is right every time (index far above what it needs, and
    # it settles yes); the market is stubbornly at 0.5.
    for i in range(10):
        ticker = f"KXBTC15M-{i}"
        for k in (20, 40):
            se.record_observation(ticker, _SPEC | {"ticker": ticker},
                                  _projection(k, 63490.0, 63600.0, 63400.0), 0.5)
        se.flush()
        se.resolve_window(ticker, settled_yes=True)

    out = se.edge_report(min_samples=5)
    assert out["status"] == "projection_beats_market"
    assert out["scored_observations"] == 20
    assert out["projection_brier"] < out["market_brier"]
    assert out["projection_better_by"] > 0
    assert set(out["by_observations_known"]) == {"16-30", "31-45"}


def test_report_is_honest_when_the_market_is_the_better_forecaster():
    """The negative result has to be reportable, or the tool is worthless."""
    from services import index_feed

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(index_feed, "recent_volatility", lambda *a, **k: 5.0)
        for i in range(10):
            ticker = f"KXBTC15M-{i}"
            # Projection is confidently wrong; market is confidently right.
            se.record_observation(ticker, _SPEC | {"ticker": ticker},
                                  _projection(30, 63490.0, 63600.0, 63400.0), 0.02)
            se.flush()
            se.resolve_window(ticker, settled_yes=False)
        out = se.edge_report(min_samples=5)
    assert out["status"] == "market_beats_projection"
    assert out["projection_better_by"] < 0


# --- the two bugs found by inspecting captured rows, 2026-08-17 ----------

def test_window_matching_rejects_a_market_settling_at_a_different_close():
    """The q15 window opens before EVERY quarter-hour. Matching only on
    index_id recorded 59 observations against KXBTCD markets whose close
    was 863 minutes away - the average accumulating toward 06:00 says
    nothing about a market settling at 17:00."""
    from services import index_feed

    entry = {"q15_window_end_ts_ms": 1_755_000_000_000}
    close_ts = 1_755_000_000.0
    assert index_feed.window_matches_close(entry, close_ts) is True
    assert index_feed.window_matches_close(entry, close_ts + 30) is True     # clock skew
    assert index_feed.window_matches_close(entry, close_ts + 900) is False   # next window
    assert index_feed.window_matches_close(entry, close_ts + 863 * 60) is False
    # No window open, or no close known: never a match, never a guess.
    assert index_feed.window_matches_close({"q15_window_end_ts_ms": None}, close_ts) is False
    assert index_feed.window_matches_close(entry, None) is False


def test_unresolved_tickers_drives_resolution_from_this_store_not_the_watchlist():
    """A KXBTC15M market was found finalized six minutes after close with
    its observations still unresolved, because it had already rotated out
    of the discovery watchlist. Resolution has to come from the one place
    that remembers the window happened."""
    se.record_observation("KXBTC15M-A", _SPEC,
                          _projection(30, 63490.0, 63495.0, 63510.0), 0.6, now=1000.0)
    se.flush()
    close = se.close_ts(_SPEC)

    # Too soon after close - not yet worth spending an API call on.
    assert se.unresolved_tickers(older_than_sec=120, now=close + 10) == []
    assert se.unresolved_tickers(older_than_sec=120, now=close + 300) == ["KXBTC15M-A"]

    se.resolve_window("KXBTC15M-A", settled_yes=True)
    assert se.unresolved_tickers(older_than_sec=120, now=close + 300) == []


def test_mismatched_observations_are_removable_and_resolved_ones_are_not():
    """The cleanup identifies bad rows by their own recorded fields - an
    observation taken 863 minutes before the window it claims - not by
    ticker prefix."""
    se.record_observation("KXBTCD-X", _SPEC | {"ticker": "KXBTCD-X"},
                          _projection(30, 1.0, 1.0, 1.0), 0.5, now=1000.0)
    se.record_observation("KXBTC15M-A", _SPEC,
                          _projection(30, 63490.0, 63495.0, 63510.0), 0.6,
                          now=se.close_ts(_SPEC) - 30)
    se.flush()

    assert se.drop_mismatched_observations() == 1
    with sqlite3.connect(se.DB_PATH) as conn:
        remaining = [r[0] for r in conn.execute("SELECT ticker FROM window_observations")]
    assert remaining == ["KXBTC15M-A"]
