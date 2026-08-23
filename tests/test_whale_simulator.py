import time

from services.confidence_scoring import composite_confidence
from services.whale_simulator import WhaleSimulator


def _market(ticker="TICK-A", volume_24h_fp="10000", yes_bid_dollars="0.5", close_time=None, event_ticker=None):
    m = {"ticker": ticker, "volume_24h_fp": volume_24h_fp, "yes_bid_dollars": yes_bid_dollars}
    if close_time is not None:
        m["close_time"] = close_time
    if event_ticker is not None:
        m["event_ticker"] = event_ticker
    return m


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


def test_composite_confidence_matches_score_confidence_shape():
    # WhaleSimulator._score_confidence should equal the shared
    # services.confidence_scoring.composite_confidence plus noise in
    # [-0.1, 0.1] - confirms the 2026-08-23 extraction of that formula into
    # its own module didn't change the simulator's own behavior. Per-factor
    # correctness of the shared formula itself is covered directly, with no
    # noise involved, in tests/test_confidence_scoring.py.
    sim = WhaleSimulator()
    market = _market(volume_24h_fp="10000")
    now = time.time()
    base = composite_confidence(market, [market], size=5000, price=0.5, now=now)
    scored = [sim._score_confidence(market, [market], size=5000, price=0.5, now=now) for _ in range(200)]
    assert all(abs(s - base) <= 0.1 + 1e-9 for s in scored)  # noise is always within +/-0.1 of the shared base
    assert min(scored) < base < max(scored)  # and it actually varies the result, not a no-op
