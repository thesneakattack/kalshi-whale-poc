"""
Acts on the edge services/settlement_edge.py measured and confirmed real
(2026-08-23: 27,534 scored observations across 478 distinct settlement
windows, market Brier 0.1046 vs projection Brier 0.0171 overall, widening
to 0.002 vs 0.1021 once 46-59 of the 60 settlement observations are known -
see GET /api/diagnostics/settlement-edge). settlement_edge.py's own
docstring is explicit that it "never trades... records and scores" by
design; this module is the deliberate, separate "act on it" half
ROADMAP.md's "Path to production" section called for once the verdict
flipped from "insufficient" to a real, well-sampled edge.

Off by default (settlement_edge_entry.enabled) - same "ships fully built,
opt-in" precedent as every other new engine in this app.

WHY THIS IS A SEPARATE ENTRY PATH, NOT A whale_follow OPTION

services/strategy_engine.py's FollowTheWhaleStrategy.evaluate() is
triggered by a whale print and enforces strategy.min_seconds_to_close
(default 300s) - a position needs enough runway left for a price-driven
exit (take-profit/stop-loss/auto-exit) to have a chance to act before
close. That gate is exactly correct for a strategy whose only tool is
watching price move, and ROADMAP.md's #1-priority 2026-08-16 fix built it
for exactly that reason. This is different in kind: it is triggered by the
index feed's own tick (once/sec while a settlement window is open - see
services/whale_stream/index_stream_handlers.py), and it never tries to
manage the position afterward at all. It enters (at most once per window)
specifically INSIDE that same runway floor, and holds deliberately to the
real $1/$0 settlement - marked via Position.hold_to_settlement so
services/exits/exit_engine.py's runway-floor forced exit leaves it alone
instead of closing it early at a market price that hasn't yet converged to
the now-mostly-known outcome.

WHAT COUNTS AS "AN EDGE WORTH PAYING FEES ON"

projected_probability() (services/settlement_edge.py) gives a calibrated
P(yes) from the known part of the settlement average plus a crude
random-walk model of the rest. Three independent conditions all have to
hold before this trades:

  1. min_observations_known - only inside the bucket the Brier comparison
     actually validated as strongly edge-positive (default 45, i.e. the
     46-59-known bucket).
  2. min_probability - the projection itself must be confident (near 0 or
     near 1), not just "better than the market's guess" - a 55%/45% edge
     over a 50/50 market is real by the Brier math but not worth a taker
     fee on a binary outcome this close to a coin flip.
  3. min_edge - the market's own price for that side must sit far enough
     below the projected probability to be worth paying the taker fee and
     accepting execution risk on. If the market has already priced in
     most of what the index feed knows, there is nothing left to capture.

Deliberately does NOT reuse strategy.min_unit_cost/max_unit_cost - those
are tuned for whale-follow's own preferred price band (0.65-0.8, off real
trade-history analysis of THAT strategy) and have nothing to do with this
one. The only universal, non-configurable floor this respects is
services/config_bounds.is_tradeable_unit_cost - CLAUDE.md's "whale bets at
cost 0 or 100c are just plain wrong" invariant applies to every strategy
that opens a position, not just whale-follow.
"""
from services.config_bounds import is_tradeable_unit_cost
from services import index_feed, settlement_edge
from services.paper_broker import PaperBroker
from services.risk_manager import RiskManager


def evaluate_entry(
    ticker: str, spec: dict, projection: dict, market_yes_price: float | None,
    cfg: dict, broker: PaperBroker, risk: RiskManager, config_fingerprint: str | None = None,
) -> dict | None:
    """One market's settlement window, one index tick. Returns a "trade"
    decision dict (same shape strategy_engine.evaluate() returns) if this
    tick opened a position, or None if it didn't - unlike evaluate(), this
    deliberately does NOT return a "skip" dict for every rejected tick: it
    runs once per second per open settlement window regardless of whether
    settlement_edge_entry is even enabled, and logging a skip decision for
    every one of those would flood decision_feed with noise nobody reads
    (contrast with a whale signal, which is itself a meaningful event worth
    recording even when skipped).

    spec is services/index_feed.settlement_spec()'s output for this ticker
    (index_id/strike/comparison/close_time). projection is
    index_feed.settlement_projection(spec["index_id"], spec["strike"])'s
    live output for the current tick - the caller (services/whale_stream/
    index_stream_handlers.py) already computes both to feed
    settlement_edge.record_observation, so this reuses them rather than
    re-deriving."""
    se_cfg = cfg.get("settlement_edge_entry") or {}
    if not se_cfg.get("enabled", False):
        return None
    if projection.get("status") != "accumulating":
        return None
    if market_yes_price is None:
        return None
    # Never duplicate/overwrite an existing position or resting order on
    # this ticker - same "don't silently discard cost basis" concern
    # strategy_engine.evaluate()'s own same-ticker check exists for.
    if ticker in broker.positions or ticker in broker.pending_orders:
        return None

    k = projection.get("observations_known")
    min_k = se_cfg.get("min_observations_known", 45)
    if not k or k < min_k:
        return None

    vol = index_feed.recent_volatility(
        projection["index_id"], lookback_sec=se_cfg.get("volatility_lookback_sec", 3600),
    )
    p_yes = settlement_edge.projected_probability(projection, vol)
    if p_yes is None:
        return None

    min_probability = se_cfg.get("min_probability", 0.95)
    if p_yes >= min_probability:
        side, p_side, market_price_side = "yes", p_yes, market_yes_price
    elif (1.0 - p_yes) >= min_probability:
        side, p_side, market_price_side = "no", 1.0 - p_yes, 1.0 - market_yes_price
    else:
        return None  # not confident enough either direction

    min_edge = se_cfg.get("min_edge", 0.05)
    edge = p_side - market_price_side
    if edge < min_edge:
        return None  # market already prices this in; nothing left to capture

    unit_cost = market_price_side
    if not is_tradeable_unit_cost(unit_cost):
        return None

    max_size = risk.max_trade_size(broker.bankroll, se_cfg.get("max_position_pct", 0.02))
    contracts = int(max_size / unit_cost) if unit_cost > 0 else 0
    if contracts <= 0:
        return None

    total = projection.get("observations_total", index_feed.SETTLEMENT_WINDOW_TICKS)
    reason = (
        f"settlement-edge: p({side})={p_side:.3f} vs market {market_price_side:.3f} "
        f"(edge {edge:.3f}, {k}/{total} observations known)"
    )
    trade = broker.open_position(
        ticker=ticker, side=side, size=contracts, price=market_yes_price, reason=reason,
        config_fingerprint=config_fingerprint, hold_to_settlement=True,
    )
    if trade is None:
        # Execution-layer risk guard fired (risk.halted, or the portfolio
        # exposure cap) - same as any other open_position() caller.
        return None
    return {"action": "trade", "trade": trade.to_dict(), "reason": reason}
