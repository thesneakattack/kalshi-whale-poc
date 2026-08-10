"""
MarketNativeStrategy - a second, independent automated paper-trading
strategy that decides entries purely from real Kalshi market data (price,
spread, volume, momentum, time-to-close). No whale signal input at all,
unlike FollowTheWhaleStrategy.

Direct request (2026-08-08): "market data is real, the whale data is
fake... there's no reason why I shouldn't start storing and analyzing
market data now... when the whale watch data becomes available and the
paper/shadow positions use real market and real whale watch data, those
personal trades and whale watch data should then be incorporated." This
strategy exists to start generating a real, whale-independent trade-history
dataset (entry conditions -> realized P&L) for the advisory/ML groundwork
(docs/advisory-engine-plan.md) to eventually learn from, using its own
capital pool (own PaperBroker/RiskManager, own data/*.db files - see
main.py's wiring) so its performance is cleanly measurable on its own and
never contaminates the whale-follow strategy's paper account. Once a real
whale-watcher provider is connected (services/whalewatchers/), its trades
and this strategy's trades become two independently-tracked, genuinely
real datasets that can be compared or combined - see
docs/advisory-engine-plan.md's future-ML section.

Off by default (market_strategy.enabled: false) - same "ships fully built,
disabled until deliberately turned on" precedent as advisory.enabled and
kalshi_account.trading_enabled elsewhere in this app. Paper-only by
construction (PaperBroker never places a real order), so unlike real
trading this has no separate confirmation-phrase gate - the same risk
class as the existing whale-follow paper strategy.

Heuristic (deliberately simple and explainable, not fitted/trained - same
"rule-based first" decision as services/advisory_engine.py): momentum
continuation (follow the market's own recent real price movement, via
services/market_history.py's logged snapshots) gated by three risk-
reducing filters - a moderate, genuinely-uncertain price band (avoids
near-certain 5c/95c markets with poor risk/reward), a tight bid/ask spread
(liquidity/pricing-quality proxy), and a minimum 24h volume floor (avoids
thin/illiquid markets) - plus a minimum time-to-close so there's still room
to react if wrong. Composite entry confidence blends momentum strength,
liquidity, and spread quality into one 0-1 score, same weighted-factor
mental model as whale_simulator._score_confidence and
strategy_engine._exit_confidence.

Exits mirror FollowTheWhaleStrategy.check_exits' shape (settlement first
via strategy_engine.close_if_settled, then opt-in take-profit/stop-loss),
plus an optional momentum-reversal exit - the market-native analog of
"whale sentiment reversed," using market_history.momentum() instead of the
whale signal_feed. No auto-exit composite algorithm yet (a possible future
extension, matching FollowTheWhaleStrategy's auto_exit_enabled).
"""
from services import kalshi_fees, market_analyst_agent, market_history
from services.paper_broker import PaperBroker
from services.risk_manager import RiskManager
from services.strategy_engine import close_if_settled, open_position_count_in_series

# How fresh a market_analyst_agent estimate must be to fold into this
# strategy's entry confidence - same freshness window as the whale-follow
# side's analyst_factor (services/whalewatchers/kalshi_trade_tape.py), for
# the same reason: the event being estimated is far more stable than the
# market's own price.
_ANALYST_FRESHNESS_SEC = 24 * 3600


def _entry_confidence(mom: dict, volume: float, spread: float, strat_cfg: dict, side: str, ticker: str) -> tuple[float, dict]:
    """Composite 0-1 score, same weighted-factor idiom as
    whale_simulator._score_confidence / strategy_engine._exit_confidence.
    momentum/liquidity/spread are always present here (momentum is already
    required non-None by the caller's gate; volume/spread are always
    numeric), so those three alone would need no weighting - a plain
    average. The analyst factor is different: it's only ever present when
    someone has actually spent a real API call analyzing this specific
    ticker recently (services/market_analyst_agent.py, direct request
    2026-08-09 - "inform the various engines... without consuming AI
    tokens"), which is the overwhelmingly uncommon case. Folding it into a
    always-4-wide average would permanently water down every ordinary
    momentum-only entry by a phantom neutral factor; instead it's only
    added to the averaged set at all when a fresh estimate exists, so
    today's momentum-only behavior is completely unchanged unless someone
    has actually asked the analyst about this exact market."""
    momentum_factor = min(1.0, abs(mom["delta"]) / max(strat_cfg["min_momentum_delta"] * 3, 1e-9))
    liquidity_factor = min(1.0, volume / max(strat_cfg["min_volume_24h"] * 3, 1e-9))
    max_spread = max(strat_cfg["max_spread"], 1e-9)
    spread_factor = min(1.0, max(0.0, (max_spread - spread) / max_spread))
    factors = {"momentum": momentum_factor, "liquidity": liquidity_factor, "spread": spread_factor}

    lean = market_analyst_agent.analyst_lean(ticker, max_age_sec=_ANALYST_FRESHNESS_SEC)
    if lean is not None:
        factors["analyst"] = lean if side == "yes" else (1.0 - lean)

    confidence = sum(factors.values()) / len(factors)
    return confidence, factors


class MarketNativeStrategy:
    def __init__(self, broker: PaperBroker, risk: RiskManager):
        self.broker = broker
        self.risk = risk

    def evaluate_all(self, markets: list[dict], now: float, cfg: dict, market_results: dict | None = None) -> list[dict]:
        """Scans every currently-fetched real market once per trading-loop
        tick and opens a position on any that clears every filter. Unlike
        FollowTheWhaleStrategy.evaluate() (one call per incoming signal),
        there's no discrete "signal" event here - only markets that
        actually qualify produce a decision; a market that doesn't isn't
        logged as a skip, since a skip-per-market-per-tick would flood the
        decision feed for zero benefit (this strategy is backend-only for
        now, see docs/advisory-engine-plan.md)."""
        strat_cfg = cfg["market_strategy"]
        if not strat_cfg.get("enabled"):
            return []
        # Real, this-tick prices from markets (already fetched, no extra
        # cost) rather than an empty dict - audit finding (2026-08-09), see
        # strategy_engine.py's own FollowTheWhaleStrategy.evaluate() for the
        # full explanation: equity({}) silently zeroes every position's
        # unrealized P&L, making the kill switch check only realized
        # bankroll, blind to any real unrealized drawdown currently sitting
        # in open positions.
        # `or 0.5` guards an empty-string yes_bid_dollars, not just a
        # missing/None one - a real, confirmed-live regression this exact
        # line caused once already (bare `is not None` lets "" through,
        # then float("") raises, aborting the whole tick before
        # _fetch_event_titles ever runs, so any market not already cached
        # silently falls back to showing its raw ticker instead of a
        # title - the same idiom every other yes_bid_dollars read in this
        # codebase already uses, e.g. main.py's own state["latest_prices"]
        # construction).
        latest_prices = {m["ticker"]: float(m.get("yes_bid_dollars") or 0.5) for m in markets if m.get("ticker")}
        if not self.risk.check_daily_loss(self.broker.equity(latest_prices)):
            return []  # halted - same hard rail as the whale strategy

        market_results = market_results or {}
        decisions = []
        for market in markets:
            decision = self._evaluate_one(market, now, strat_cfg, market_results)
            if decision is not None:
                decisions.append(decision)
        return decisions

    def _evaluate_one(self, market: dict, now: float, strat_cfg: dict, market_results: dict) -> dict | None:
        ticker = market.get("ticker")
        if not ticker:
            return None
        if ticker in self.broker.positions:
            return None
        # Concentration risk (deep-scan finding 2, 2026-08-10) - same shared
        # helper/reasoning as FollowTheWhaleStrategy.evaluate()'s own check;
        # this strategy's own separate bankroll/positions are just as
        # exposed to a burst of correlated markets (a whole tournament, an
        # election contract family) each individually clearing every other
        # filter here.
        max_open_per_series = strat_cfg.get("max_open_positions_per_series")
        if max_open_per_series and open_position_count_in_series(self.broker, ticker) >= max_open_per_series:
            return None
        result = (market_results.get(ticker) or "").strip().lower()
        if result in ("yes", "no"):
            return None
        if not self.broker.can_trade(ticker, strat_cfg["cooldown_sec"]):
            return None

        price = float(market.get("yes_bid_dollars") or 0.5)
        ask = float(market.get("yes_ask_dollars") or price)
        spread = max(ask - price, 0.0)
        volume = float(market.get("volume_24h_fp") or 0.0)

        if not (strat_cfg["min_price"] <= price <= strat_cfg["max_price"]):
            return None
        if spread > strat_cfg["max_spread"]:
            return None
        if volume < strat_cfg["min_volume_24h"]:
            return None

        seconds_to_close = market_history.seconds_to_close(market.get("close_time"), now)
        if seconds_to_close is None or seconds_to_close < strat_cfg["min_seconds_to_close"]:
            return None

        mom = market_history.momentum(ticker, strat_cfg["momentum_lookback_sec"], as_of=now)
        if mom is None or abs(mom["delta"]) < strat_cfg["min_momentum_delta"]:
            return None

        # Momentum-following: price is always the YES price throughout this
        # app (see paper_broker.py) - a rising yes_price means the market
        # itself is increasingly pricing YES as likely, so riding that means
        # buying yes; a falling yes_price means the opposite.
        side = "yes" if mom["delta"] > 0 else "no"
        confidence, factors = _entry_confidence(mom, volume, spread, strat_cfg, side, ticker)
        if confidence < strat_cfg["entry_confidence_threshold"]:
            return None

        max_size = self.risk.max_trade_size(self.broker.bankroll, strat_cfg["max_position_pct"])
        unit_cost = price if side == "yes" else (1 - price)
        contracts = int(max_size / unit_cost) if unit_cost > 0 else 0
        if contracts <= 0:
            return None

        breakdown = ", ".join(f"{name}={value:.0%}" for name, value in factors.items())
        # (conf X) kept as its own isolated parenthetical, matching
        # trade_analytics._ENTRY_CONF_RE exactly (it requires the number
        # immediately followed by ")") - the factor breakdown goes in a
        # separate bracket so entry_confidence still parses correctly if
        # this strategy's trades are ever run through trade_analytics.
        reason = (
            f"momentum {mom['delta']:+.2f} over {mom['span_sec']:.0f}s (conf {confidence:.2f}) [{breakdown}]"
        )
        trade = self.broker.open_position(ticker=ticker, side=side, size=contracts, price=price, reason=reason)
        return {"action": "trade", "ticker": ticker, "trade": trade.to_dict()}

    def check_exits(
        self, markets_by_ticker: dict, now: float, cfg: dict, market_results: dict | None = None,
    ) -> list[dict]:
        """Same layered-priority shape as FollowTheWhaleStrategy.check_exits:
        settlement first (hard rule, via the shared close_if_settled), then
        opt-in take-profit/stop-loss, then an opt-in momentum-reversal exit
        (market-native analog of whale sentiment reversal). Runs every tick
        regardless of market_strategy.enabled - if it's off, no positions
        exist to check, so this is a harmless no-op; if it was just turned
        off with positions still open, they're still cleanly exitable."""
        strat_cfg = cfg["market_strategy"]
        take_profit_pct = strat_cfg.get("take_profit_pct")
        stop_loss_pct = strat_cfg.get("stop_loss_pct")
        exit_on_reversal = strat_cfg.get("exit_on_momentum_reversal", False)
        reversal_lookback = strat_cfg.get("momentum_lookback_sec", 1800)

        market_results = market_results or {}
        decisions = []
        for ticker, pos in list(self.broker.positions.items()):
            result = market_results.get(ticker)
            closed = close_if_settled(self.broker, ticker, pos, result)
            if closed is not None:
                decisions.append(closed)
                continue
            if (result or "").strip().lower() in ("yes", "no"):
                continue  # settled but close raced/no-op'd - nothing left to check

            # `market.get(...) or pos.entry_price`, not a bare `is not None`
            # guard - the latter lets an empty-string yes_bid_dollars through
            # to float(""), which raises (see evaluate_all's own
            # latest_prices construction above for the same fix, and why it
            # matters: an uncaught exception here aborts the whole tick
            # before title-fetching runs).
            market = markets_by_ticker.get(ticker)
            current_price = float((market or {}).get("yes_bid_dollars") or pos.entry_price)
            cost_basis = self.broker.cost_basis(ticker)
            if cost_basis <= 0:
                continue
            # Fee-inclusive, not just mark_to_market()'s raw price-move P&L -
            # same audit finding (2026-08-09) as strategy_engine.py's own
            # check_exits: a stop_loss_pct/take_profit_pct should mean "X%
            # of what was actually put in," not "X% of the raw price move
            # before fees make it worse."
            close_fee = kalshi_fees.taker_fee(pos.size, current_price)
            pnl_pct = (self.broker.mark_to_market(ticker, current_price) - pos.entry_fee - close_fee) / cost_basis

            reason = None
            if take_profit_pct is not None and pnl_pct >= take_profit_pct:
                reason = (
                    f"take-profit hit: unrealized gain {pnl_pct:.0%} of cost basis (target {take_profit_pct:.0%})"
                )
            elif stop_loss_pct is not None and pnl_pct <= -stop_loss_pct:
                reason = (
                    f"stop-loss hit: unrealized loss {-pnl_pct:.0%} of cost basis (limit {stop_loss_pct:.0%})"
                )
            elif exit_on_reversal:
                mom = market_history.momentum(ticker, reversal_lookback, as_of=now)
                if mom is not None and mom["delta"] != 0:
                    favored_side = "yes" if mom["delta"] > 0 else "no"
                    if favored_side != pos.side:
                        reason = (
                            f"momentum reversed: {mom['delta']:+.2f} over {mom['span_sec']:.0f}s "
                            f"now favors {favored_side}"
                        )

            if reason is None:
                continue
            trade = self.broker.close_position(ticker, current_price, reason)
            if trade is None:
                continue
            decisions.append({"action": "close", "ticker": ticker, "trade": trade.to_dict(), "reason": reason})
        return decisions
