"""
Generates fake "whale" order-flow prints against whatever markets are
currently on the watchlist. This is a stand-in for a real whale-watcher
feed/API — same output shape, so swapping it out later is a one-file change.

A real implementation would live at services/whale_live.py and expose the
same generate(markets) -> list[WhaleSignal] interface.

Sizing and confidence scoring are modeled on real Kalshi/Polymarket
whale-tracker products, researched directly (see ROADMAP.md), not guessed:
- WhaleScanr sizes a "whale" relative to each market's own trade-size
  distribution ("roughly the top few percent of that market's trades, plus
  an absolute dollar floor"), not one flat number across every market.
- Polywhaler's "Insider Score" is a composite of trade size relative to
  market depth, how unusual the price/timing is, proximity to resolution,
  and broader market context — not a single factor.
"""
import math
import random
import time
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime


@dataclass
class WhaleSignal:
    id: str
    ticker: str
    side: str          # "yes" | "no"
    size: int          # fake contract count
    price: float        # 0-1 implied probability at time of print
    confidence: float   # 0-1 synthetic confidence score
    timestamp: float
    # Per-factor confidence breakdown (see composite_confidence_breakdown),
    # only populated by providers that compute one - None for the simulator.
    # Persisted alongside the signal (services/signal_log.py's factors_json)
    # so a future calibration pass has more than just the final blended
    # number to learn from - see services/confidence_calibration.py.
    factors: dict | None = None

    def to_dict(self):
        return asdict(self)


# What fraction of a market's own 24h volume a single "whale" print
# represents, before the configured absolute floor/cap apply — WhaleScanr's
# "top few percent of this market's own trades," not a flat cutoff.
_WHALE_VOLUME_FRACTION = 0.03
# Spread around that scaled center. Log-normal, not uniform: real large-trade
# sizes cluster with a heavy right tail (most prints near the middle of the
# plausible range, a rarer few much bigger) rather than being equally likely
# across the whole span the way random.randint would generate them.
_SIZE_LOGNORM_SIGMA = 0.6
# A print within ~2 days of a market's own close/resolution reads as more
# informationally loaded than the same print months out — informed money
# moving right before an outcome is revealed, vs. no particular urgency.
_CLOSE_PROXIMITY_WINDOW_SEC = 48 * 3600
# Depth factor's exponential-saturation rate (see composite_confidence_breakdown):
# chosen so a print of exactly one day's volume scores 0.9, matching roughly
# where the old hard cap used to bind, while never hard-plateauing beyond it.
_DEPTH_SATURATION_K = math.log(10)


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
        confidence = self._score_confidence(market, candidates, size, price, now)

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
        self, market: dict, markets: list[dict], size: int, price: float, now: float
    ) -> float:
        """Composite score plus a little synthetic noise, so repeated
        simulated prints against the same market don't all land on the exact
        same number - see composite_confidence() below for the formula
        itself, shared with real whale-watcher providers (which report it
        as-is, with no noise added - a real trade's confidence shouldn't
        have fake uncertainty injected into it). agreement_factor is left at
        its neutral default here - the simulator has no real signal history
        worth checking agreement against."""
        base = composite_confidence(market, markets, size, price, now)
        noise = random.uniform(-0.1, 0.1)
        return min(max(base + noise, 0.0), 1.0)


@dataclass
class ConfidenceBreakdown:
    """Every factor that went into a composite_confidence score, not just
    the final blended number - what services/confidence_calibration.py
    needs to later ask "which of these factors actually predicted a correct
    call", something the plain float alone can't answer after the fact."""
    depth_factor: float
    unusualness_factor: float
    proximity_factor: float
    context_factor: float
    agreement_factor: float
    score: float

    def to_dict(self) -> dict:
        return asdict(self)


def composite_confidence_breakdown(
    market: dict, markets: list[dict], size: float, price: float, now: float,
    agreement_factor: float = 0.5,
) -> ConfidenceBreakdown:
    """Matches Polywhaler's stated "Insider Score" shape (see ROADMAP.md):
    trade size relative to market depth, how unusual the price is, proximity
    to resolution, broader market context, and whether recent whale prints
    on this same market agree. Each factor normalized to 0-1, weighted-
    summed. Pure function of its inputs - no randomness - so it's shared
    as-is between the simulator (which adds noise on top, see
    WhaleSimulator._score_confidence) and any real whale-watcher provider
    scoring an actual trade (services/whalewatchers/kalshi_trade_tape.py).

    agreement_factor is the caller's responsibility to compute (this
    function has no access to signal history) - defaults to 0.5 (neutral:
    neither agreement nor disagreement) when the caller has no real
    signal-agreement concept to offer, e.g. the simulator, or a real
    provider scoring a market with no recent prior prints to compare
    against - same "missing data isn't scored as agreement or disagreement"
    idiom already used elsewhere in this app (e.g. auto_exit_confidence)."""
    market_volume = float(market.get("volume_24h_fp") or 0)

    # (1) Size relative to THIS market's own activity - a 20,000-contract
    # print is unremarkable in a 2M-volume market, huge in a 5,000-volume
    # one. Exponential saturation, not a hard cap: the old `min(x, 1.0)`
    # meant a print of exactly one day's volume and a print of 100x that
    # scored identically (1.0) - a real, flagged weakness (a modest trade in
    # a thin market could trivially hit the same ceiling as a genuinely
    # enormous one, and nothing beyond the ceiling could ever differentiate
    # further). `1 - e^(-k*x)` has no plateau - it keeps inching toward 1.0
    # for arbitrarily larger prints - while still landing at the same ~0.9
    # for "exactly one day's volume" the old cap used to bind at, so this
    # isn't a wholesale rescale, just removing the cliff.
    depth_ratio = size / max(market_volume, 1.0)
    depth_factor = 1.0 - math.exp(-_DEPTH_SATURATION_K * depth_ratio)

    # (2) How unusual the price is - closer to a coin-flip (0.5) means the
    # market's genuinely undecided, so a big directional bet there is more
    # informationally loaded than one piling onto an already near-certain
    # 5c/95c market where there's little edge left to have.
    unusualness_factor = 1.0 - abs(price - 0.5) * 2

    # (3) Proximity to the market's own resolution/close time - no
    # close_time (or one already past) contributes nothing rather than
    # guessing.
    proximity_factor = 0.0
    close_time = market.get("close_time")
    if close_time:
        try:
            close_ts = datetime.fromisoformat(close_time.replace("Z", "+00:00")).timestamp()
            seconds_left = close_ts - now
            if 0 < seconds_left <= _CLOSE_PROXIMITY_WINDOW_SEC:
                proximity_factor = 1.0 - (seconds_left / _CLOSE_PROXIMITY_WINDOW_SEC)
        except (ValueError, AttributeError):
            pass

    # (4) Broader market context - is this one of the more actively-traded
    # markets in the current batch, or a thin outlier? A big print in an
    # already-busy market reads as more credible than the same print in the
    # quietest one on the list. Percentile rank among the batch, not a raw
    # ratio against the single busiest market - a real, flagged weakness of
    # the ratio approach: one outsized market in the batch could crush
    # every other market's context_factor toward zero even if they're all
    # reasonably active *relative to each other*. Rank is robust to that -
    # one outlier only nudges everyone else's rank slightly, never crushes
    # it.
    other_volumes = [float(m.get("volume_24h_fp") or 0) for m in markets]
    context_factor = (
        sum(1 for v in other_volumes if v <= market_volume) / len(other_volumes)
        if other_volumes else 0.5
    )

    # (5) Do recent whale prints on this same market agree with this one?
    # Independent same-direction prints plausibly share a real catalyst
    # rather than being noise - the core thesis behind
    # docs/kalshi-whale-provider-and-strategy-porting-plan.md Part 2's
    # whale-consensus idea, folded into the base confidence score itself
    # rather than only a separate future strategy. Computed by the caller
    # (needs real signal-log history this function doesn't have access to,
    # see services/whalewatchers/kalshi_trade_tape.py) - defaults to
    # neutral 0.5 here.

    score = (
        0.35 * depth_factor
        + 0.20 * unusualness_factor
        + 0.15 * proximity_factor
        + 0.15 * context_factor
        + 0.15 * agreement_factor
    )
    return ConfidenceBreakdown(
        depth_factor=depth_factor, unusualness_factor=unusualness_factor,
        proximity_factor=proximity_factor, context_factor=context_factor,
        agreement_factor=agreement_factor, score=min(max(score, 0.0), 1.0),
    )


def composite_confidence(
    market: dict, markets: list[dict], size: float, price: float, now: float,
    agreement_factor: float = 0.5,
) -> float:
    """The blended score only - see composite_confidence_breakdown for the
    full per-factor detail. Kept as its own function so every existing
    caller that only ever wanted a plain float (WhaleSimulator, tests)
    doesn't need to change."""
    return composite_confidence_breakdown(market, markets, size, price, now, agreement_factor).score
