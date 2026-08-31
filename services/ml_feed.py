"""
Scaffolding only - not wired into any route, not called from anywhere in
this app yet. Direct request (2026-08-08): "eventually I want to be able to
feed this heuristic suggestion data, market data, historical data, portfolio
data, recommendation engine data, to a machine-learning agent that will work
WITH these things, not as a replacement... add some scaffolding for that but
let's not pursue that until the project is already finished."

This module is the shape of that future export - one point-in-time bundle
combining everything a future ML agent would read *alongside* (not instead
of) services/advisory/advisory_engine.py's rule-based recommendations. No model, no
training pipeline, no feature engineering lives here - that's the actual
future work this is scaffolding for. build_context_snapshot() is a pure
function so it's cheap to keep correct as the data sources it wraps evolve;
see tests/test_ml_feed.py, which is the only thing exercising it right now.

When that future work actually starts: add a route that calls this (e.g.
GET /api/advisory/ml-feed), decide on a real persistence/export story if the
ML agent needs historical snapshots rather than just the current one, and
build the actual model/agent as a *new* module that reads this bundle - this
one should stay a pure, dependency-free assembler.
"""
import time


def build_context_snapshot(
    cfg: dict,
    portfolio: dict,
    market_snapshot: dict,
    trade_history_rows: list[dict],
    whale_track_record: dict,
    advisory: dict,
    candlestick_volatility: dict | None = None,
) -> dict:
    """Assembles one JSON-serializable bundle. Each argument is the exact
    shape an existing endpoint/service already produces - nothing here
    re-derives or reshapes them, so this can't silently drift from what a
    human already sees on the dashboard:

    - cfg: config_store.get() - the live strategy/risk/whale-signal config.
    - portfolio: PaperBroker.state(latest_prices) - bankroll, equity, open
      positions, recent trades ("portfolio data").
    - market_snapshot: whatever subset of main.py's `state` describes
      current market conditions the caller wants included (e.g.
      {"markets": state["markets"], "latest_prices": state["latest_prices"],
      "live_status": state["live_status"]}) - "market data".
    - trade_history_rows: trade_analytics.build_trade_history(...) output -
      "historical data".
    - whale_track_record: signal_log.stats() - whale-signal accuracy over
      time, independent of any one trade.
    - advisory: {"recommendations": advisory_engine.generate_recommendations(...)["recommendations"],
      "variant_summaries": advisory_engine.variant_summaries(...)} -
      "recommendation engine data", exactly as a human would see it on the
      History tab. A future ML agent is meant to read this as one more
      input alongside the others, not have its output silently overridden
      by it - see the module docstring's "work WITH, not replace" framing.

    Returns a plain dict - no numpy/pandas types, nothing that needs a
    custom JSON encoder, so it's trivially cacheable/loggable/diffable
    whenever this actually gets consumed."""
    return {
        "schema_version": 1,
        "generated_at": time.time(),
        "config": cfg,
        "portfolio": portfolio,
        "market_snapshot": market_snapshot,
        "trade_history": trade_history_rows,
        "whale_track_record": whale_track_record,
        "advisory": advisory,
        "candlestick_volatility": candlestick_volatility or {},
    }
