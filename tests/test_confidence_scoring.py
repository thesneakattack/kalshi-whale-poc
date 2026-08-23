import time
from datetime import datetime, timedelta, timezone

import pytest

from services.confidence_scoring import DEFAULT_WEIGHTS, composite_confidence, composite_confidence_breakdown


def _market(ticker="TICK-A", volume_24h_fp="10000", yes_bid_dollars="0.5", close_time=None, event_ticker=None):
    m = {"ticker": ticker, "volume_24h_fp": volume_24h_fp, "yes_bid_dollars": yes_bid_dollars}
    if close_time is not None:
        m["close_time"] = close_time
    if event_ticker is not None:
        m["event_ticker"] = event_ticker
    return m


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def test_composite_confidence_is_deterministic_no_noise():
    # The shared, real-provider-facing function (services/whalewatchers/
    # kalshi_trade_tape.py) must not have WhaleSimulator's synthetic noise
    # mixed in - a real trade's confidence shouldn't have fake uncertainty
    # injected into it. Same inputs must always produce the exact same score.
    market = _market(volume_24h_fp="10000")
    now = time.time()
    scores = {composite_confidence(market, [market], size=5000, price=0.5, now=now) for _ in range(50)}
    assert len(scores) == 1


def test_confidence_higher_for_price_near_coinflip_than_extreme():
    market = _market(volume_24h_fp="10000")
    now = time.time()
    coinflip = composite_confidence(market, [market], size=5000, price=0.50, now=now)
    extreme = composite_confidence(market, [market], size=5000, price=0.98, now=now)
    assert coinflip > extreme


def test_confidence_higher_near_close_time_than_far_out():
    market_soon = _market(volume_24h_fp="10000", close_time=_iso(datetime.now(timezone.utc) + timedelta(hours=1)))
    market_far = _market(volume_24h_fp="10000", close_time=_iso(datetime.now(timezone.utc) + timedelta(days=60)))
    now = time.time()
    soon = composite_confidence(market_soon, [market_soon], size=5000, price=0.5, now=now)
    far = composite_confidence(market_far, [market_far], size=5000, price=0.5, now=now)
    assert soon > far


def test_confidence_handles_missing_or_malformed_close_time():
    market = _market(volume_24h_fp="10000", close_time="not-a-real-timestamp")
    now = time.time()
    # Should not raise - malformed close_time contributes nothing rather than crashing.
    score = composite_confidence(market, [market], size=5000, price=0.5, now=now)
    assert 0.0 <= score <= 1.0

    market_none = _market(volume_24h_fp="10000")
    score2 = composite_confidence(market_none, [market_none], size=5000, price=0.5, now=now)
    assert 0.0 <= score2 <= 1.0


def test_confidence_higher_for_larger_share_of_market_depth():
    market = _market(volume_24h_fp="10000")
    now = time.time()
    small = composite_confidence(market, [market], size=100, price=0.5, now=now)
    large = composite_confidence(market, [market], size=9000, price=0.5, now=now)
    assert large > small


def test_confidence_higher_for_busiest_market_in_batch():
    # Isolates the context factor (this market's volume vs. the busiest one
    # in the batch) from the depth factor (size vs. this market's own
    # volume) by using a size proportional to each market's own volume, so
    # depth_factor comes out equal for both and only context_factor differs.
    busy = _market(ticker="BUSY", volume_24h_fp="100000")
    quiet = _market(ticker="QUIET", volume_24h_fp="1000")
    markets = [busy, quiet]
    now = time.time()
    busy_score = composite_confidence(busy, markets, size=5000, price=0.5, now=now)
    quiet_score = composite_confidence(quiet, markets, size=50, price=0.5, now=now)
    assert busy_score > quiet_score


# ---- trend_factor (docs/prediction-market-strategy-alignment-plan.md Part 2.4) ----

def test_trend_factor_pass_through_raises_score_above_neutral_default():
    market = _market(volume_24h_fp="10000")
    now = time.time()
    neutral = composite_confidence(market, [market], size=5000, price=0.5, now=now)  # trend_factor defaults to 0.5
    with_trend = composite_confidence(market, [market], size=5000, price=0.5, now=now, trend_factor=1.0)
    against_trend = composite_confidence(market, [market], size=5000, price=0.5, now=now, trend_factor=0.0)
    assert against_trend < neutral < with_trend


# ---- weights (deep-scan follow-up, 2026-08-10) - config-editable scoring ----
# weights - real finding: confidence_calibration.py enabled against ~9200
# real signals showed unusualness_factor/agreement_factor discriminating
# NEGATIVELY, so composite_confidence_breakdown's weights became
# config-editable instead of hardcoded constants.

def test_weights_default_to_default_weights_when_omitted():
    market = _market(volume_24h_fp="10000")
    now = time.time()
    default_call = composite_confidence(market, [market], size=5000, price=0.5, now=now)
    explicit_default = composite_confidence(market, [market], size=5000, price=0.5, now=now, weights=DEFAULT_WEIGHTS)
    assert default_call == explicit_default


def test_weights_override_changes_the_score():
    market = _market(volume_24h_fp="10000")
    now = time.time()
    base = composite_confidence(market, [market], size=5000, price=0.5, now=now, agreement_factor=1.0)
    # agreement_factor's weight raised way above default - the same input
    # factor should now move the score more.
    heavier_agreement = composite_confidence(
        market, [market], size=5000, price=0.5, now=now, agreement_factor=1.0,
        weights={**DEFAULT_WEIGHTS, "agreement_factor": 0.9},
    )
    assert heavier_agreement > base


def test_weights_partial_override_falls_back_to_default_for_missing_keys():
    # A caller passing a partial dict (e.g. config missing a newer factor's
    # key) must not silently score that factor as weight 0 - it should fall
    # back to DEFAULT_WEIGHTS for whatever it didn't override.
    market = _market(volume_24h_fp="10000")
    now = time.time()
    full_default = composite_confidence_breakdown(market, [market], size=5000, price=0.5, now=now)
    partial = composite_confidence_breakdown(
        market, [market], size=5000, price=0.5, now=now, weights={"depth_factor": DEFAULT_WEIGHTS["depth_factor"]},
    )
    assert full_default.score == pytest.approx(partial.score)


def test_weights_still_sum_reasonably_with_a_full_custom_dict():
    market = _market(volume_24h_fp="10000")
    now = time.time()
    custom = {
        "depth_factor": 0.25, "unusualness_factor": 0.03, "proximity_factor": 0.20,
        "context_factor": 0.13, "agreement_factor": 0.03, "cluster_factor": 0.13,
        "trend_factor": 0.08, "analyst_factor": 0.15,
    }
    breakdown = composite_confidence_breakdown(market, [market], size=5000, price=0.5, now=now, weights=custom)
    assert 0.0 <= breakdown.score <= 1.0


# ---- block_trade_factor (docs/kalshi/public-trades.md's is_block_trade -
# a real first-party Kalshi signal, added 2026-08-15 after finding it parsed
# by services/kalshi_trade_ws.py and never read anywhere downstream) --------

def test_block_trade_factor_defaults_to_zero_not_neutral():
    # Same "known-and-negative is itself informative" idiom as cluster_factor -
    # Kalshi tells every real trade's block-trade status explicitly, so "not
    # a block trade" is a real, known 0.0, not a neutral 0.5 the way an
    # entirely-absent signal (e.g. no analyst estimate on file) would be.
    market = _market(volume_24h_fp="10000")
    now = time.time()
    omitted = composite_confidence_breakdown(market, [market], size=5000, price=0.5, now=now)
    explicit_false = composite_confidence_breakdown(
        market, [market], size=5000, price=0.5, now=now, block_trade_factor=0.0,
    )
    assert omitted.block_trade_factor == explicit_false.block_trade_factor == 0.0


def test_block_trade_factor_true_raises_the_score():
    market = _market(volume_24h_fp="10000")
    now = time.time()
    not_block = composite_confidence(market, [market], size=5000, price=0.5, now=now, block_trade_factor=0.0)
    is_block = composite_confidence(market, [market], size=5000, price=0.5, now=now, block_trade_factor=1.0)
    assert is_block > not_block


def test_block_trade_factor_present_in_default_weights():
    # Real bug class this app has hit before (confidence_calibration.py's
    # _FACTOR_NAMES drifting out of sync with DEFAULT_WEIGHTS) - guard that
    # a newly added factor always has a real weight, not a silent 0/missing
    # key once someone edits DEFAULT_WEIGHTS again later.
    assert "block_trade_factor" in DEFAULT_WEIGHTS
    assert DEFAULT_WEIGHTS["block_trade_factor"] > 0
