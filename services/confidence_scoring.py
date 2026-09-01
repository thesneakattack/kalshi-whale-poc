"""
The shared whale-signal record type (WhaleSignal) and the composite
confidence-scoring formula (composite_confidence/composite_confidence_
breakdown) every whale-watcher provider scores a trade through - real
(services/whalewatchers/kalshi_trade_tape.py and friends) and simulated
(services/whale_simulator.py) alike.

Split out of services/whale_simulator.py 2026-08-23 (per-module audit,
CLAUDE.md's "module quality" pass): despite the module name, this was never
simulator-only code. Every real whale-watcher provider already imported
WhaleSignal and composite_confidence_breakdown straight out of a file
literally called "simulator" - a real naming/cohesion problem, not just a
size one. The formula itself is a pure function of its inputs (no
randomness) precisely so it can be shared as-is between a real trade and a
fake one; only WhaleSimulator._score_confidence adds synthetic noise on top,
which is why that noise-adding step stayed behind in whale_simulator.py
rather than moving here.

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
from dataclasses import dataclass, asdict
from datetime import datetime

from services import kalshi_fees


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
    # number to learn from - see services/whale_calibration/confidence_calibration.py.
    factors: dict | None = None
    # Raw, unscored inputs behind the factor breakdown above - notional
    # dollar size, market spread, market 24h volume, as they existed at
    # signal-creation time. Gap 8 of docs/config-tuning-data-gaps-2026-08-
    # 10.md: factors above are already-derived 0-1 scores, so a future
    # analysis can ask "does depth_factor discriminate" but never "would a
    # differently-shaped transform of the same raw depth data discriminate
    # better" - re-deriving the raw inputs after the fact is unreliable
    # (market_catalog/market_history are watchlist-scoped and rotate).
    # Deliberately a separate field, not folded into factors - factors'
    # every value is a 0-1 score confidence_calibration.py buckets by
    # tertile; a raw dollar figure mixed into that dict would corrupt that
    # bucketing. Only populated by providers that compute one - None for
    # the simulator, same convention as factors itself.
    raw_context: dict | None = None
    close_time: str | None = None

    def to_dict(self):
        return asdict(self)


# A print within ~2 days of a market's own close/resolution reads as more
# informationally loaded than the same print months out — informed money
# moving right before an outcome is revealed, vs. no particular urgency.
_CLOSE_PROXIMITY_WINDOW_SEC = 48 * 3600
# Depth factor's exponential-saturation rate (see composite_confidence_breakdown):
# chosen so a print of exactly one day's volume scores 0.9, matching roughly
# where the old hard cap used to bind, while never hard-plateauing beyond it.
_DEPTH_SATURATION_K = math.log(10)


@dataclass
class ConfidenceBreakdown:
    """Every factor that went into a composite_confidence score, not just
    the final blended number - what services/whale_calibration/confidence_calibration.py
    needs to later ask "which of these factors actually predicted a correct
    call", something the plain float alone can't answer after the fact.

    Each factor is float | None in practice: None means a real provider
    looked and found nothing to report (e.g. depth_factor when the market
    has no reportable 24h volume, or an explicit agreement_factor=None from
    a caller with no signal-agreement concept for this print) - honest
    absence, not coerced to a fabricated neutral 0.5. score renormalizes
    over only the present factors (see composite_confidence_breakdown)."""
    depth_factor: float | None
    unusualness_factor: float | None
    proximity_factor: float | None
    context_factor: float | None
    agreement_factor: float | None
    cluster_factor: float | None
    trend_factor: float | None
    analyst_factor: float | None
    block_trade_factor: float | None
    score: float

    def to_dict(self) -> dict:
        return asdict(self)


# The weights this formula shipped with, before any real calibration data
# existed to check them against - now the fallback default (and the
# reference point services/whale_calibration/confidence_calibration.py's report compares a
# live config override against), not the only source of truth. See
# config/settings.yaml's whale_confidence_weights section - config.
# block_trade_factor added 2026-08-15 (consuming docs/kalshi/public-trades.md
# in full for the first time this session surfaced a real gap: Kalshi's own
# is_block_trade flag on every real trade - an authoritative first-party
# signal, not an inferred one - was being parsed by
# services/kalshi_trade_ws.py and then never read by anything). Given a real
# starting weight rather than 0, same "ships with a reasoned value, gets
# recalibrated once real data exists" precedent as cluster_factor/
# trend_factor/analyst_factor when each was added - the other 8 weights
# proportionally scaled down (×0.85) to make room, preserving their
# pre-existing relative proportions rather than resetting them.
DEFAULT_WEIGHTS = {
    "depth_factor": 0.18, "unusualness_factor": 0.08, "proximity_factor": 0.11,
    "context_factor": 0.07, "agreement_factor": 0.11, "cluster_factor": 0.11,
    "trend_factor": 0.07, "analyst_factor": 0.13, "block_trade_factor": 0.15,
}


def composite_confidence_breakdown(
    market: dict, markets: list[dict], size: float, price: float, now: float,
    agreement_factor: float | None = 0.5, cluster_factor: float = 0.0, trend_factor: float | None = 0.5,
    analyst_factor: float = 0.5, block_trade_factor: float = 0.0,
    weights: dict | None = None, side: str = "yes",
) -> ConfidenceBreakdown:
    """Matches Polywhaler's stated "Insider Score" shape (see ROADMAP.md),
    extended per docs/prediction-market-strategy-alignment-plan.md: trade
    size relative to market depth, how unusual the price is, proximity to
    resolution, broader market context, whether recent whale prints on this
    same market agree, whether this print looks like part of an active
    accumulation run, whether it agrees with or fights the market's own
    recent real price trend, and whether it agrees with the market analyst
    LLM's own independent read of this market, if a fresh one exists. Each
    factor normalized to 0-1, weighted-summed. Pure function of its inputs -
    no randomness - so it's shared as-is between the simulator (which adds
    noise on top, see WhaleSimulator._score_confidence) and any real
    whale-watcher provider scoring an actual trade
    (services/whalewatchers/kalshi_trade_tape.py).

    weights defaults to DEFAULT_WEIGHTS (this formula's original,
    unvalidated weights) when the caller doesn't pass config -
    services/whalewatchers/kalshi_trade_tape.py (the real provider) passes
    config["whale_confidence_weights"] once real calibration data exists to
    set it from; a caller passing a partial dict only overrides the keys it
    names, falling back to DEFAULT_WEIGHTS for the rest, so a config that's
    missing a newer factor's key (e.g. before cluster_factor existed)
    degrades to that factor's original weight rather than silently scoring
    it as 0. Direct finding (2026-08-10, services/whale_calibration/confidence_calibration.py
    enabled against real data for the first time): unusualness_factor and
    agreement_factor both showed NEGATIVE discrimination against ~9200 real
    resolved signals (the "high" bucket for each actually won LESS often
    than the "low" bucket) - see config/settings.yaml's
    whale_confidence_weights comment for the exact reasoning behind the
    values actually shipped there.

    agreement_factor, cluster_factor, trend_factor, and analyst_factor are
    all the caller's responsibility to compute (this function has no access
    to signal history, price history, or market_analyst_agent's own DB):

    - agreement_factor defaults to 0.5 (neutral: neither agreement nor
      disagreement) when the caller has no real signal-agreement concept to
      offer, e.g. the simulator, or a real provider scoring a market with no
      recent prior prints to compare against - same "missing data isn't
      scored as agreement or disagreement" idiom already used elsewhere in
      this app (e.g. auto_exit_confidence).
    - cluster_factor defaults to 0.0, NOT 0.5 - unlike agreement_factor,
      "no similar-sized recent prints nearby" is itself informative here,
      not merely unknown (see services/signal_log.py's cluster_factor()).
      An isolated large print, with nothing else like it nearby, is exactly
      the profile Barclay & Warner's stealth-trading research found real
      informed traders avoid presenting - see
      docs/prediction-market-strategy-alignment-plan.md Part 2.1.
    - trend_factor defaults to 0.5 (neutral), same reasoning as
      agreement_factor - no real price-trend data, or a flat trend, reads
      the same as "can't judge fighting-the-trend risk either way," not as
      evidence of anything (see services/whalewatchers/kalshi_trade_tape.py's
      _trend_factor())."""
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
    # market_volume <= 0 means no reportable 24h volume at all - undefined,
    # not badly-defined - so depth_factor is None (honest absence) rather
    # than the old max(market_volume, 1.0) floor, which silently forced
    # depth_ratio = size (i.e. depth_factor -> ~1.0, a false-confident
    # maximum) for a market with zero volume on record.
    if market_volume <= 0:
        depth_factor = None
    else:
        depth_ratio = size / market_volume
        depth_factor = 1.0 - math.exp(-_DEPTH_SATURATION_K * depth_ratio)

    # (2) How unusual the price is — interpret the traded-side price so
    # that a "no"-side print at a 1c yes-price (yes=0.01) is treated the
    # same as a "yes"-side print at 99c (yes=0.99). Use the traded side's
    # implied probability when measuring distance from a coinflip. This
    # makes the market's current outcome estimate (the cost) an explicit
    # input to the confidence computation.
    traded_side_price = kalshi_fees.unit_cost(side, price)
    unusualness_factor = 1.0 - abs(traded_side_price - 0.5) * 2

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

    # (6) Does this print look like it's part of an active accumulation run
    # (a tight, size-consistent sequence of prints on this same ticker/side),
    # rather than a single conspicuous block with nothing else like it
    # nearby? Computed by the caller (services/signal_log.py's
    # cluster_factor(), needs real signal-log history this function doesn't
    # have access to) - defaults to 0.0, not 0.5, since "isolated" is itself
    # informative here, not merely unknown (see the docstring above).

    # (7) Does this print's direction agree with, or fight, the market's own
    # recent real price trend? A big print consistent with where the price
    # has already been drifting is a different animal from one trying to
    # reverse an established trend - the classic manipulation-risk
    # distinction (docs/prediction-market-strategy-alignment-plan.md Part
    # 2.4) that nothing in this scoring model checked before now. A caution
    # factor, not a block - manipulator presence isn't unambiguously
    # accuracy-destroying (Hanson 2009) - so this nudges the score, it
    # doesn't gate it. Computed by the caller from real price history
    # (services/market_history.py's momentum(), which this function has no
    # access to) - defaults to 0.5 (neutral: no trend data, or a flat
    # trend, reads the same as "can't judge fighting-the-trend risk either
    # way").

    # (8) Does this print's direction agree with the market analyst LLM's
    # own independent probability estimate for this market, if one exists
    # and is fresh? Direct request (2026-08-09): "whenever the market
    # analysis agent runs I want it to inform the various engines... so
    # they can run the added logic without consuming AI tokens" - this is
    # that wiring for the whale-confidence score specifically. Computed by
    # the caller (services/market_analyst_agent.py's analyst_lean(), needs
    # that module's own DB this function has no access to) - defaults to
    # 0.5 (neutral: no analysis has been manually triggered for this market
    # yet, or the one on file is too stale to trust - see
    # market_analyst_agent.analyst_lean()'s own freshness window). Unlike
    # the other six factors, this one is populated only when a human
    # deliberately spent a real API call analyzing this specific market -
    # neutral is the overwhelmingly common case, not an edge case.

    # (9) Was this trade designated a block trade by Kalshi itself?
    # (docs/kalshi/public-trades.md's is_block_trade field, consumed in full
    # for the first time 2026-08-15 - previously parsed by services/
    # kalshi_trade_ws.py and never read anywhere downstream). Unlike every
    # other factor here, this isn't inferred from this app's own math - it's
    # Kalshi's own first-party classification of the trade. Defaults to 0.0,
    # not 0.5 - same "known-and-negative is itself informative, not merely
    # unknown" idiom as cluster_factor: Kalshi tells every real trade's
    # block-trade status explicitly, so "no" is a real, known answer, not
    # missing data. Computed by the caller (services/whalewatchers/
    # kalshi_trade_tape.py, the only real, non-simulated caller with an
    # actual is_block_trade field to read) - the simulator has no equivalent
    # concept and always passes the default.

    # Renormalized weighted average over only the PRESENT factors - an
    # absent (None) factor is left out of both the numerator and the
    # denominator entirely, not treated as a 0. Flat weighted-sum against a
    # fixed denominator (the old behavior) would silently punish a signal
    # for a provider having nothing to report on one factor, exactly the
    # same "coerce absence into a bad score" bug this task exists to close
    # for depth_factor/agreement_factor/trend_factor (design §5.2). Falls
    # back to 0.5 (maximally uncertain) only in the degenerate case where
    # every weighted factor for this row is absent - never a crash, never a
    # fabricated 0.
    factor_values = {
        "depth_factor": depth_factor, "unusualness_factor": unusualness_factor,
        "proximity_factor": proximity_factor, "context_factor": context_factor,
        "agreement_factor": agreement_factor, "cluster_factor": cluster_factor,
        "trend_factor": trend_factor, "analyst_factor": analyst_factor,
        "block_trade_factor": block_trade_factor,
    }
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    present = {name: value for name, value in factor_values.items() if value is not None}
    weight_sum = sum(w[name] for name in present)
    if weight_sum > 0:
        score = sum(w[name] * value for name, value in present.items()) / weight_sum
    else:
        score = 0.5  # every weighted factor absent for this row - maximally
                      # uncertain, not a crash or a fabricated 0 (design §5.2)
    return ConfidenceBreakdown(
        depth_factor=depth_factor, unusualness_factor=unusualness_factor,
        proximity_factor=proximity_factor, context_factor=context_factor,
        agreement_factor=agreement_factor, cluster_factor=cluster_factor,
        trend_factor=trend_factor, analyst_factor=analyst_factor,
        block_trade_factor=block_trade_factor,
        score=min(max(score, 0.0), 1.0),
    )


def composite_confidence(
    market: dict, markets: list[dict], size: float, price: float, now: float,
    agreement_factor: float = 0.5, cluster_factor: float = 0.0, trend_factor: float = 0.5,
    analyst_factor: float = 0.5, block_trade_factor: float = 0.0,
    weights: dict | None = None, side: str = "yes",
) -> float:
    """The blended score only - see composite_confidence_breakdown for the
    full per-factor detail. Kept as its own function so every existing
    caller that only ever wanted a plain float (WhaleSimulator, tests)
    doesn't need to change."""
    return composite_confidence_breakdown(
        market, markets, size, price, now, agreement_factor=agreement_factor, cluster_factor=cluster_factor,
        trend_factor=trend_factor, analyst_factor=analyst_factor, block_trade_factor=block_trade_factor,
        weights=weights, side=side,
    ).score
