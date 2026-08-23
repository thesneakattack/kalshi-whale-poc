"""
Generates fake "whale" order-flow prints against whatever markets are
currently on the watchlist. This is a stand-in for a real whale-watcher
feed/API — same output shape, so swapping it out later is a one-file change.

A real implementation would live at services/whale_live.py and expose the
same generate(markets) -> list[WhaleSignal] interface.

Sizing is modeled on WhaleScanr's real methodology (see ROADMAP.md): a
"whale" is sized relative to each market's own trade-size distribution
("roughly the top few percent of that market's trades, plus an absolute
dollar floor"), not one flat number shared across every market.

Confidence scoring itself - WhaleSignal, composite_confidence/composite_
confidence_breakdown, DEFAULT_WEIGHTS - moved to services/confidence_
scoring.py 2026-08-23 (per-module audit): that formula is a pure function
of its inputs, shared as-is with every real whale-watcher provider
(services/whalewatchers/kalshi_trade_tape.py and friends), not simulator-
specific logic. Only this module's own noise-adding layer
(_score_confidence, see its docstring) is genuinely simulator-only, so it
stayed here.
"""
import math
import random
import time
import uuid

from services.confidence_scoring import WhaleSignal, composite_confidence

# What fraction of a market's own 24h volume a single "whale" print
# represents, before the configured absolute floor/cap apply — WhaleScanr's
# "top few percent of this market's own trades," not a flat cutoff.
_WHALE_VOLUME_FRACTION = 0.03
# Spread around that scaled center. Log-normal, not uniform: real large-trade
# sizes cluster with a heavy right tail (most prints near the middle of the
# plausible range, a rarer few much bigger) rather than being equally likely
# across the whole span the way random.randint would generate them.
_SIZE_LOGNORM_SIGMA = 0.6


class WhaleSimulator:
    def __init__(self, size_range=(5000, 50000), bias="random"):
        self.size_range = size_range
        self.bias = bias
        self._last_emit = 0.0

    def maybe_generate(
        self,
        markets: list[dict],
        avg_interval_sec: float,
        live_status: dict | None = None,
        live_only: bool = False,
    ) -> WhaleSignal | None:
        """Randomly emits at most one signal per call, paced by avg_interval_sec.

        live_only (whale_signal.live_markets_only in config) restricts which
        markets can generate a print at all to ones currently flagged live —
        a stronger, upstream version of strategy.live_markets_only, which
        only gates whether a *signal that already exists* gets acted on.
        With this on, a quiet/pre-market/settled market never produces a
        whale print in the first place, not just never gets traded on.
        live_status is the same event_ticker -> "live"/"finished"/"none"
        mapping the LIVE badge and strategy.live_markets_only both already
        use (state["live_status"], see main.py's _fetch_live_status) -
        reused here rather than a second definition of "live"."""
        now = time.time()
        if now - self._last_emit < random.expovariate(1 / max(avg_interval_sec, 1)):
            return None
        candidates = markets
        if live_only:
            live_status = live_status or {}
            candidates = [m for m in markets if live_status.get(m.get("event_ticker")) == "live"]
        if not candidates:
            return None

        market = self._pick_market(candidates)
        side = self._pick_side(market)
        # yes_bid_dollars is Kalshi's real field (already 0-1) — "yes_bid" (cents)
        # doesn't exist on the live API, so this used to silently always fall
        # through to a random price instead of the market's actual one.
        yes_bid = float(market.get("yes_bid_dollars") or 0)
        price = yes_bid if yes_bid > 0 else random.uniform(0.05, 0.95)
        size = self._size_for(market)
        confidence = self._score_confidence(market, candidates, size, price, now, side=side)

        signal = WhaleSignal(
            id=str(uuid.uuid4())[:8],
            ticker=market.get("ticker", "UNKNOWN"),
            side=side,
            size=size,
            price=round(price, 2),
            confidence=round(confidence, 2),
            timestamp=now,
        )
        self._last_emit = now
        return signal

    def _pick_market(self, markets: list[dict]) -> dict:
        """Weighted toward higher-volume markets rather than a flat
        random.choice — real large/informed order flow concentrates where
        there's actual liquidity and price action, not spread evenly across
        every watchlist market regardless of how quiet it is. A market with
        no recorded volume yet still gets a small nonzero weight (a market
        that just opened shouldn't be permanently unreachable), it's just
        not favored over an active one."""
        weights = [max(float(m.get("volume_24h_fp") or 0), 1.0) for m in markets]
        return random.choices(markets, weights=weights, k=1)[0]

    def _pick_side(self, market: dict) -> str:
        yes_bid = float(market.get("yes_bid_dollars") or 0.5)
        if self.bias == "momentum":
            return "yes" if yes_bid >= 0.5 else "no"
        if self.bias == "contrarian":
            return "no" if yes_bid >= 0.5 else "yes"
        return random.choice(["yes", "no"])

    def _size_for(self, market: dict) -> int:
        """Relative to the chosen market's own recent activity, not a flat
        range shared by every market (see the WhaleScanr note above) —
        log-normal around _WHALE_VOLUME_FRACTION of the market's own 24h
        volume, so a "whale" in a thin market and a "whale" in a
        high-volume one land at genuinely different absolute sizes.
        size_range's two configured numbers act as an absolute floor/cap
        rather than a uniform pick range, keeping results sane at either
        extreme (near-zero volume, or one outsized market)."""
        lo, hi = self.size_range
        market_volume = float(market.get("volume_24h_fp") or 0)
        target_center = max(market_volume * _WHALE_VOLUME_FRACTION, lo)
        size = random.lognormvariate(math.log(target_center), _SIZE_LOGNORM_SIGMA)
        return int(min(max(size, lo), hi))

    def _score_confidence(
        self, market: dict, markets: list[dict], size: int, price: float, now: float, side: str = "yes"
    ) -> float:
        """Composite score plus a little synthetic noise, so repeated
        simulated prints against the same market don't all land on the exact
        same number - see services.confidence_scoring.composite_confidence
        for the formula itself, shared with real whale-watcher providers
        (which report it as-is, with no noise added - a real trade's
        confidence shouldn't have fake uncertainty injected into it).
        agreement_factor, cluster_factor, and trend_factor are all left at
        their defaults here - the simulator has no real signal history
        worth checking agreement or clustering against, and (unlike a real
        provider, which has services/market_history.py's real snapshots to
        work from for any real market) doesn't reach for real price-trend
        data just to score a synthetic print."""
        base = composite_confidence(market, markets, size, price, now, side=side)
        noise = random.uniform(-0.1, 0.1)
        return min(max(base + noise, 0.0), 1.0)
