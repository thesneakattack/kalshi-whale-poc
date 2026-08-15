import time
from datetime import datetime, timedelta, timezone

import pytest

from services.whale_simulator import DEFAULT_WEIGHTS, WhaleSimulator, composite_confidence, composite_confidence_breakdown


def _market(ticker="TICK-A", volume_24h_fp="10000", yes_bid_dollars="0.5", close_time=None, event_ticker=None):
    m = {"ticker": ticker, "volume_24h_fp": volume_24h_fp, "yes_bid_dollars": yes_bid_dollars}
    if close_time is not None:
        m["close_time"] = close_time
    if event_ticker is not None:
        m["event_ticker"] = event_ticker
    return m


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def test_maybe_generate_returns_none_for_no_markets():
    sim = WhaleSimulator()
    assert sim.maybe_generate([], avg_interval_sec=1) is None


def test_maybe_generate_produces_valid_signal_fields(monkeypatch):
    sim = WhaleSimulator(size_range=(1000, 20000))
    monkeypatch.setattr("random.expovariate", lambda _: 0.0)  # always due to emit
    markets = [_market()]
    sig = sim.maybe_generate(markets, avg_interval_sec=1)
    assert sig is not None
    assert sig.ticker == "TICK-A"
    assert sig.side in ("yes", "no")
    assert 1000 <= sig.size <= 20000
    assert 0.0 <= sig.price <= 1.0
    assert 0.0 <= sig.confidence <= 1.0


def test_pacing_suppresses_immediate_repeat_calls(monkeypatch):
    sim = WhaleSimulator()
    monkeypatch.setattr("random.expovariate", lambda _: 0.0)  # first call: always due
    first = sim.maybe_generate([_market()], avg_interval_sec=1)
    assert first is not None

    monkeypatch.setattr("random.expovariate", lambda _: 9999.0)  # second call: not due yet
    second = sim.maybe_generate([_market()], avg_interval_sec=1)
    assert second is None


def test_live_only_restricts_generation_to_live_markets(monkeypatch):
    sim = WhaleSimulator()
    monkeypatch.setattr("random.expovariate", lambda _: 0.0)
    quiet = _market(ticker="QUIET", event_ticker="EVT-QUIET")
    live = _market(ticker="LIVE", event_ticker="EVT-LIVE")
    live_status = {"EVT-QUIET": "none", "EVT-LIVE": "live"}

    for _ in range(20):
        sig = sim.maybe_generate(
            [quiet, live], avg_interval_sec=1, live_status=live_status, live_only=True,
        )
        assert sig is not None
        assert sig.ticker == "LIVE"
        sim._last_emit = 0.0  # force "due" again for the next draw


def test_live_only_with_no_live_markets_emits_nothing(monkeypatch):
    sim = WhaleSimulator()
    monkeypatch.setattr("random.expovariate", lambda _: 0.0)
    quiet = _market(ticker="QUIET", event_ticker="EVT-QUIET")
    live_status = {"EVT-QUIET": "none"}

    sig = sim.maybe_generate([quiet], avg_interval_sec=1, live_status=live_status, live_only=True)
    assert sig is None


def test_live_only_off_ignores_live_status(monkeypatch):
    sim = WhaleSimulator()
    monkeypatch.setattr("random.expovariate", lambda _: 0.0)
    quiet = _market(ticker="QUIET", event_ticker="EVT-QUIET")
    live_status = {"EVT-QUIET": "none"}

    sig = sim.maybe_generate([quiet], avg_interval_sec=1, live_status=live_status, live_only=False)
    assert sig is not None
    assert sig.ticker == "QUIET"


def test_live_only_defaults_to_off_when_live_status_omitted(monkeypatch):
    sim = WhaleSimulator()
    monkeypatch.setattr("random.expovariate", lambda _: 0.0)
    market = _market(event_ticker="EVT-A")
    sig = sim.maybe_generate([market], avg_interval_sec=1)
    assert sig is not None


def test_size_is_relative_to_market_volume_not_flat():
    # Same absolute floor/cap, wildly different market volumes - a thin
    # market's typical print should land far below a high-volume market's,
    # not be equally likely across one shared flat range (WhaleScanr's real
    # methodology, see ROADMAP.md).
    sim = WhaleSimulator(size_range=(100, 1_000_000))

    thin = _market(volume_24h_fp="1000")
    busy = _market(volume_24h_fp="5000000")

    thin_sizes = [sim._size_for(thin) for _ in range(200)]
    busy_sizes = [sim._size_for(busy) for _ in range(200)]

    assert sum(thin_sizes) / len(thin_sizes) < sum(busy_sizes) / len(busy_sizes)


def test_size_respects_absolute_floor_and_cap():
    sim = WhaleSimulator(size_range=(500, 5000))
    # Near-zero volume should still floor at the configured minimum.
    tiny = _market(volume_24h_fp="0")
    for _ in range(50):
        assert 500 <= sim._size_for(tiny) <= 5000
    # Enormous volume should still cap at the configured maximum.
    huge = _market(volume_24h_fp="999999999")
    for _ in range(50):
        assert 500 <= sim._size_for(huge) <= 5000


def test_pick_market_favors_higher_volume(monkeypatch):
    sim = WhaleSimulator()
    quiet = _market(ticker="QUIET", volume_24h_fp="10")
    busy = _market(ticker="BUSY", volume_24h_fp="1000000")
    markets = [quiet, busy]

    picks = [sim._pick_market(markets)["ticker"] for _ in range(500)]
    busy_count = picks.count("BUSY")
    # Overwhelmingly weighted toward the busy market, not anywhere near 50/50.
    assert busy_count > 450


def test_pick_market_still_reaches_zero_volume_markets():
    sim = WhaleSimulator()
    zero = _market(ticker="ZERO", volume_24h_fp="0")
    picks = [sim._pick_market([zero])["ticker"] for _ in range(5)]
    assert all(p == "ZERO" for p in picks)


def test_confidence_higher_for_price_near_coinflip_than_extreme():
    sim = WhaleSimulator()
    market = _market(volume_24h_fp="10000")
    markets = [market]
    now = time.time()

    # Both include +/-0.1 noise; average over many draws to compare the
    # underlying factor rather than one noisy sample.
    coinflip_avg = sum(
        sim._score_confidence(market, markets, size=5000, price=0.50, now=now) for _ in range(200)
    ) / 200
    extreme_avg = sum(
        sim._score_confidence(market, markets, size=5000, price=0.98, now=now) for _ in range(200)
    ) / 200
    assert coinflip_avg > extreme_avg


def test_confidence_higher_near_close_time_than_far_out():
    sim = WhaleSimulator()
    market_soon = _market(volume_24h_fp="10000", close_time=_iso(datetime.now(timezone.utc) + timedelta(hours=1)))
    market_far = _market(volume_24h_fp="10000", close_time=_iso(datetime.now(timezone.utc) + timedelta(days=60)))
    now = time.time()

    soon_avg = sum(
        sim._score_confidence(market_soon, [market_soon], size=5000, price=0.5, now=now) for _ in range(200)
    ) / 200
    far_avg = sum(
        sim._score_confidence(market_far, [market_far], size=5000, price=0.5, now=now) for _ in range(200)
    ) / 200
    assert soon_avg > far_avg


def test_confidence_handles_missing_or_malformed_close_time():
    sim = WhaleSimulator()
    market = _market(volume_24h_fp="10000", close_time="not-a-real-timestamp")
    now = time.time()
    # Should not raise - malformed close_time contributes nothing rather than crashing.
    score = sim._score_confidence(market, [market], size=5000, price=0.5, now=now)
    assert 0.0 <= score <= 1.0

    market_none = _market(volume_24h_fp="10000")
    score2 = sim._score_confidence(market_none, [market_none], size=5000, price=0.5, now=now)
    assert 0.0 <= score2 <= 1.0


def test_confidence_higher_for_larger_share_of_market_depth():
    sim = WhaleSimulator()
    market = _market(volume_24h_fp="10000")
    markets = [market]
    now = time.time()

    small_avg = sum(
        sim._score_confidence(market, markets, size=100, price=0.5, now=now) for _ in range(200)
    ) / 200
    large_avg = sum(
        sim._score_confidence(market, markets, size=9000, price=0.5, now=now) for _ in range(200)
    ) / 200
    assert large_avg > small_avg


def test_confidence_higher_for_busiest_market_in_batch():
    # Isolates the context factor (this market's volume vs. the busiest one
    # in the batch) from the depth factor (size vs. this market's own
    # volume) by using a size proportional to each market's own volume, so
    # depth_factor comes out equal for both and only context_factor differs.
    sim = WhaleSimulator()
    busy = _market(ticker="BUSY", volume_24h_fp="100000")
    quiet = _market(ticker="QUIET", volume_24h_fp="1000")
    markets = [busy, quiet]
    now = time.time()

    busy_avg = sum(
        sim._score_confidence(busy, markets, size=5000, price=0.5, now=now) for _ in range(200)
    ) / 200
    quiet_avg = sum(
        sim._score_confidence(quiet, markets, size=50, price=0.5, now=now) for _ in range(200)
    ) / 200
    assert busy_avg > quiet_avg


def test_composite_confidence_is_deterministic_no_noise():
    # The shared, real-provider-facing function (services/whalewatchers/
    # kalshi_trade_tape.py) must not have WhaleSimulator's synthetic noise
    # mixed in - a real trade's confidence shouldn't have fake uncertainty
    # injected into it. Same inputs must always produce the exact same score.
    market = _market(volume_24h_fp="10000")
    now = time.time()
    scores = {composite_confidence(market, [market], size=5000, price=0.5, now=now) for _ in range(50)}
    assert len(scores) == 1


def test_composite_confidence_matches_score_confidence_shape():
    # WhaleSimulator._score_confidence should equal composite_confidence
    # plus noise in [-0.1, 0.1] - confirms the extraction didn't change the
    # simulator's own behavior.
    sim = WhaleSimulator()
    market = _market(volume_24h_fp="10000")
    now = time.time()
    base = composite_confidence(market, [market], size=5000, price=0.5, now=now)
    scored = [sim._score_confidence(market, [market], size=5000, price=0.5, now=now) for _ in range(200)]
    assert all(abs(s - base) <= 0.1 + 1e-9 for s in scored)  # noise is always within +/-0.1 of the shared base
    assert min(scored) < base < max(scored)  # and it actually varies the result, not a no-op


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
