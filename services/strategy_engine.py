"""
The only file that decides *whether* a whale signal becomes a trade.
Swap this out to change strategy without touching the broker, risk
manager, or data sources.
"""
import time

from services import kalshi_fees, market_analyst_agent, signal_log
from services.whale_simulator import WhaleSignal
from services.paper_broker import PaperBroker, Position
from services.risk_manager import RiskManager

# Same freshness window services/market_strategy.py and services/
# whalewatchers/kalshi_trade_tape.py already use for analyst_lean() on the
# entry side - the event being estimated is far more stable than a
# market's own price, so this doesn't need to be tight, just not stale
# enough to be estimating a different market state entirely.
_ANALYST_FRESHNESS_SEC = 24 * 3600


def close_if_settled(broker: PaperBroker, ticker: str, pos: Position, result: str | None) -> dict | None:
    """Shared by every strategy's check_exits (FollowTheWhaleStrategy below,
    and services/market_strategy.py's MarketNativeStrategy) - closes a
    position at the terminal price the instant its market has actually
    settled, regardless of any other exit config. Extracted here rather
    than left inline/duplicated so this stays the single place this math
    lives: terminal_price = 1.0 if result == "yes" else 0.0, NOT "1.0 if
    won else 0.0" - the latter double-applies close_position's own side
    inversion for a "no" position and silently zeros out a winning "no"
    position's payout (a real bug this app shipped and fixed once already,
    see ROADMAP.md - reusing this function is what keeps a second strategy
    from reintroducing it). Returns None if the market hasn't resolved, or
    if the position was already gone by the time this ran (a poll tick's
    exit check racing a position that closed this same tick shouldn't
    crash the loop)."""
    result = (result or "").strip().lower()
    if result not in ("yes", "no"):
        return None
    won = result == pos.side
    terminal_price = 1.0 if result == "yes" else 0.0
    reason = f"market settled {result.upper()} - position {'won' if won else 'lost'}"
    trade = broker.close_position(ticker, terminal_price, reason)
    if trade is None:
        return None
    return {"action": "close", "ticker": ticker, "trade": trade.to_dict(), "reason": reason}


class FollowTheWhaleStrategy:
    def __init__(self, broker: PaperBroker, risk: RiskManager):
        self.broker = broker
        self.risk = risk

    def evaluate(
        self, signal: WhaleSignal, cfg: dict, is_live: bool | None = None, market_results: dict | None = None,
        config_fingerprint: str | None = None, latest_prices: dict | None = None,
    ) -> dict:
        """Returns a decision dict describing what happened (trade or skip + why).
        is_live comes from main.py's milestone/live-data lookup (see
        _fetch_live_status) - None/False means "not currently live" (either
        confirmed finished/scheduled, or no live-status data for this market
        at all, e.g. it's not a live-event-style market). market_results is
        the same ticker -> "yes"/"no"/""/None mapping check_exits uses to
        settle already-open positions - checked here too so a whale print
        against a market that's already resolved (a narrow but real window:
        e.g. it settled between polls, or it's only in this tick's markets
        list because an unrelated open position pulled it in) doesn't open a
        brand-new position with a predetermined, already-known outcome."""
        strat_cfg = cfg["strategy"]

        # Audit finding (2026-08-09): this used to be self.broker.equity({})
        # - an empty prices dict makes every open position's mark_to_market
        # fall back to its own entry_price (see PaperBroker.equity's
        # docstring), so total_unrealized_pnl was silently always exactly
        # 0 and this call was mathematically identical to just passing
        # self.broker.bankroll directly. The kill switch was therefore
        # checking only *realized* daily loss, blind to however large an
        # unrealized drawdown was currently sitting in open positions - a
        # portfolio could be deep underwater on paper and this would never
        # stop new trades from opening until something actually closed.
        # Passing the real latest_prices makes this true daily *equity*
        # loss, the correct, more protective definition for a safety rail.
        if not self.risk.check_daily_loss(self.broker.equity(latest_prices or {})):
            return self._skip(signal, f"halted: {self.risk.halt_reason}")

        result = ((market_results or {}).get(signal.ticker) or "").strip().lower()
        if result in ("yes", "no"):
            return self._skip(signal, "market has already resolved")

        if strat_cfg.get("live_markets_only") and not is_live:
            return self._skip(signal, "market is not currently live")

        # Manual override on top of the automatic win-rate filter below - for
        # a series the user has out-of-band reason to distrust before it's
        # racked up enough resolved signals for the automatic cutoff to ever
        # trigger. Same series definition as the automatic filter
        # (signal_log.series_of), not a second one that could drift.
        excluded_series = strat_cfg.get("excluded_series") or []
        series = signal_log.series_of(signal.ticker)
        if series in excluded_series:
            return self._skip(signal, f'series "{series}" is manually excluded')

        # Favorite-longshot bias, confirmed on real Kalshi data (Bürgi, Deng
        # & Whelan 2025 - see docs/prediction-markets-research-reference.md
        # Part 1.2): longshot-priced contracts (near $0 or $1) are
        # systematically overpriced relative to their real win rate, and
        # the bias is far worse for takers - this app's real order path
        # (services/kalshi_account_client.py defaults to
        # time_in_force="immediate_or_cancel") - than makers. A flat
        # entry_threshold applied the same way at every price point ignores
        # this; a signal priced in longshot territory needs to clear a
        # higher bar, not the same one. market_strategy.py already handles
        # this differently (a hard min_price/max_price exclusion band) -
        # this is the whale-follow strategy's own gap to close, graduated
        # rather than a hard cutoff since a strong enough signal can still
        # be worth it even in that zone.
        longshot_zone = strat_cfg.get("longshot_price_threshold", 0.15)
        longshot_bonus = strat_cfg.get("longshot_entry_threshold_bonus", 0.15)
        is_longshot = signal.price <= longshot_zone or signal.price >= (1 - longshot_zone)
        effective_threshold = strat_cfg["entry_threshold"] + (longshot_bonus if is_longshot else 0.0)
        if signal.confidence < effective_threshold:
            reason = f"confidence {signal.confidence} below threshold ({effective_threshold:.2f}"
            reason += " - longshot zone)" if is_longshot else ")"
            return self._skip(signal, reason)

        # Avoid this whale's picks on markets like this one once they've proven
        # unreliable here — but only once there's enough resolved history to
        # trust, so one unlucky result doesn't blacklist a whole category.
        min_resolved = strat_cfg.get("min_resolved_for_whale_filter", 5)
        min_winrate = strat_cfg.get("min_whale_winrate_pct", 40)
        record = signal_log.series_stats(signal.ticker, days=30)
        if record["resolved"] >= min_resolved and record["win_rate"] is not None and record["win_rate"] < min_winrate:
            return self._skip(
                signal,
                f'whale win rate for "{record["series"]}"-type markets is {record["win_rate"]:.0f}% '
                f'over {record["resolved"]} resolved signals (below {min_winrate}% minimum) — avoiding',
            )

        # open_position() unconditionally overwrites self.broker.positions[ticker]
        # with no check of its own - without this, a signal on a ticker that
        # already has an open position (cooldown elapsed, but nothing has
        # closed it yet) would silently replace that position, discarding its
        # cost basis with zero accounting trail (no close trade, no realized
        # P&L, the bankroll just permanently down that amount). Confirmed this
        # actually happened in live trade history before this check existed.
        if signal.ticker in self.broker.positions:
            return self._skip(signal, "position already open on this market")

        if not self.broker.can_trade(signal.ticker, strat_cfg["cooldown_sec"]):
            return self._skip(signal, "cooldown active for this market")

        max_size = self.risk.max_trade_size(self.broker.bankroll, strat_cfg["max_position_pct"])
        # signal.price is always the YES price (see whale_simulator.py) - a NO
        # print's real per-contract cost is (1 - price), not price itself.
        # Sizing off the wrong unit cost here doesn't just mis-price a NO
        # trade, it also breaks the max_position_pct risk cap: open_position
        # caps spend at whatever bankroll remains, so an inflated `contracts`
        # request for a NO side would silently blow past the intended
        # position-size limit instead of being capped by it.
        unit_cost = signal.price if signal.side == "yes" else (1 - signal.price)
        contracts = int(max_size / unit_cost) if unit_cost > 0 else 0
        if contracts <= 0:
            return self._skip(signal, "position size rounds to zero")

        trade = self.broker.open_position(
            ticker=signal.ticker,
            side=signal.side,
            size=contracts,
            price=signal.price,
            reason=f"whale print {signal.size} @ {signal.price} (conf {signal.confidence})",
            config_fingerprint=config_fingerprint,
        )
        return {
            "action": "trade",
            "signal": signal.to_dict(),
            "trade": trade.to_dict(),
        }

    def _skip(self, signal: WhaleSignal, reason: str) -> dict:
        return {"action": "skip", "signal": signal.to_dict(), "reason": reason}

    def check_exits(
        self, latest_prices: dict, signal_feed: list, cfg: dict, market_results: dict | None = None,
    ) -> list[dict]:
        """Actively manages already-open positions instead of leaving them
        untouched until settlement - direct request: this app had zero exit
        logic before this. A position, once opened, just sat there forever;
        "unrealized P&L" never became realized, gains were never locked in,
        losing positions were never cut.

        market_results (ticker -> "yes"/"no"/""/None, straight from Kalshi's
        market.result field) is checked first and unconditionally, not
        behind any opt-in flag: once a market has actually settled there is
        no reason to leave the position open regardless of what take-profit/
        stop-loss/etc. are configured - correctness, not strategy. Before
        this, a position on a market nobody actively exited would just sit
        in broker.positions forever even after Kalshi paid it out, which
        also meant win/loss history for anything not actively managed was
        never recorded. A settled position closes at the terminal price -
        $1/contract if the result matches the held side, $0 if not - same
        cash-back math close_position() already uses for the yes/no
        conventions.

        Everything below market_results is the actively-managed layer,
        still entirely opt-in - three independent checks, all off by
        default (existing behavior doesn't change unless explicitly
        configured - a safety-conscious default for a change that closes
        real paper positions):

        - take_profit_pct: close once unrealized gain reaches this
          fraction of cost basis (e.g. 0.5 = close a 50c entry once it's
          worth 75c, instead of holding for a possible-but-not-guaranteed
          full $1 at settlement).
        - stop_loss_pct: close once unrealized loss reaches this fraction
          of cost basis.
        - exit_on_sentiment_reversal: close if whale sentiment on this
          ticker has flipped decisively against the position's side, using
          the same recent signal_feed the dashboard's own whale-lean
          displays already read from - not a second definition of "lean."
        - auto_exit_enabled: a fourth, independent layer on top of the
          three hard rules above - a tweakable composite "exit confidence"
          score (see _exit_confidence) blending unrealized P&L magnitude,
          whale-sentiment reversal strength, and how long it's been since
          whale interest in this ticker went quiet, into one 0-1 number,
          the same mental model this app's entry side already uses for
          signal.confidence. Only evaluated when none of the three hard
          rules already decided to close - those stay the simple,
          predictable safety rails; this is the adaptive layer for
          everything in between.

        Returns decision dicts in the same shape evaluate() returns for a
        trade, so main.py can log them into decision_feed/stats the same
        way."""
        strat_cfg = cfg["strategy"]
        take_profit_pct = strat_cfg.get("take_profit_pct")
        stop_loss_pct = strat_cfg.get("stop_loss_pct")
        exit_on_reversal = strat_cfg.get("exit_on_sentiment_reversal", False)
        min_signals = strat_cfg.get("exit_sentiment_min_signals", 3)
        reversal_lean_pct = strat_cfg.get("exit_sentiment_lean_pct", 65)
        auto_exit_enabled = strat_cfg.get("auto_exit_enabled", False)
        auto_exit_threshold = strat_cfg.get("auto_exit_threshold", 0.6)

        market_results = market_results or {}
        decisions = []
        # Snapshot first - closing a position mutates self.broker.positions,
        # which we'd otherwise be iterating over while mutating.
        for ticker, pos in list(self.broker.positions.items()):
            result = (market_results.get(ticker) or "").strip().lower()
            if result in ("yes", "no"):
                closed = close_if_settled(self.broker, ticker, pos, result)
                if closed is not None:
                    decisions.append(closed)
                continue  # settled - the opt-in checks below no longer apply to this position

            current_price = latest_prices.get(ticker, pos.entry_price)
            # broker.cost_basis(), not pos.size * pos.entry_price directly -
            # that formula is only correct for the yes side; see
            # PaperBroker.cost_basis's docstring and open_position's unit_cost.
            cost_basis = self.broker.cost_basis(ticker)
            if cost_basis <= 0:
                continue
            # Fee-inclusive, not just mark_to_market()'s raw price-move P&L -
            # audit finding (2026-08-09): take_profit_pct/stop_loss_pct/
            # auto_exit's pnl_factor were all being compared against a
            # number that ignored both the entry fee already paid and the
            # close fee this exit itself would incur, the exact same
            # "fee-blind P&L" gap already fixed once for Trading History's
            # realized_pnl (see PaperBroker.close_position) - a stop_loss_pct
            # of 0.10 should mean "never lose more than 10% of what was put
            # in," not "never lose more than 10% of the raw price move
            # before fees make it worse." services/kalshi_fees.py's formula
            # is symmetric/deterministic, so estimating the not-yet-incurred
            # close fee here is exact, not a guess.
            close_fee = kalshi_fees.taker_fee(pos.size, current_price)
            pnl_pct = (self.broker.mark_to_market(ticker, current_price) - pos.entry_fee - close_fee) / cost_basis

            reason = None
            if take_profit_pct is not None and pnl_pct >= take_profit_pct:
                reason = (
                    f"take-profit hit: unrealized gain {pnl_pct:.0%} of cost basis "
                    f"(target {take_profit_pct:.0%})"
                )
            elif stop_loss_pct is not None and pnl_pct <= -stop_loss_pct:
                reason = (
                    f"stop-loss hit: unrealized loss {-pnl_pct:.0%} of cost basis "
                    f"(limit {stop_loss_pct:.0%})"
                )
            elif exit_on_reversal:
                lean = _whale_lean(ticker, signal_feed)
                if lean and lean["count"] >= min_signals:
                    opposite_pct = (100 - lean["yes_pct"]) if pos.side == "yes" else lean["yes_pct"]
                    if opposite_pct >= reversal_lean_pct:
                        reason = (
                            f"whale sentiment reversed: {opposite_pct:.0f}% of {lean['count']} recent "
                            f"prints now lean against this {pos.side} position"
                        )
            elif auto_exit_enabled:
                confidence, factors = _exit_confidence(pos, pnl_pct, ticker, signal_feed, strat_cfg)
                if confidence >= auto_exit_threshold:
                    breakdown = ", ".join(f"{name}={factor:.0%}" for name, (factor, _weight) in factors.items())
                    reason = (
                        f"auto-exit: composite confidence {confidence:.0%} >= {auto_exit_threshold:.0%} "
                        f"threshold (factors: {breakdown})"
                    )

            if reason is None:
                continue

            trade = self.broker.close_position(ticker, current_price, reason)
            if trade is None:
                continue  # defensive - shouldn't happen mid-tick, but a no-op is safe if it did
            decisions.append({"action": "close", "ticker": ticker, "trade": trade.to_dict(), "reason": reason})

        return decisions


def _whale_lean(ticker: str, signal_feed: list[dict]) -> dict | None:
    """Same definition of "lean" as the dashboard's own computeWhaleLean
    (static/index.html) - which side recent prints on this ticker have
    mostly favored, and by how much. Kept in one place conceptually (same
    inputs, same math) even though it has to live in two languages.

    Weighted by size * confidence, not size alone - direct request
    (2026-08-09): "apply the same methodologies... to all the heuristics."
    The entry side already treats a low-confidence print as weaker evidence
    than a high-confidence one (composite_confidence_breakdown's whole
    point); a "sentiment reversal" exit built by simply summing raw size
    was blind to that same distinction - three noisy, low-confidence prints
    against the held side would count exactly as much as three high-
    confidence ones, even though Barclay & Warner's stealth-trading
    research (the same finding cluster_factor is grounded in) is precisely
    about which prints are more likely to reflect real information."""
    matches = [s for s in signal_feed if s.get("ticker") == ticker]
    if not matches:
        return None
    yes_weight = sum(s["size"] * s["confidence"] for s in matches if s.get("side") == "yes")
    no_weight = sum(s["size"] * s["confidence"] for s in matches if s.get("side") == "no")
    total = yes_weight + no_weight
    return {"count": len(matches), "yes_pct": (yes_weight / total * 100) if total else 50.0}


def _exit_confidence(pos, pnl_pct: float, ticker: str, signal_feed: list[dict], strat_cfg: dict) -> tuple[float, dict]:
    """Composite 0-1 "how strongly do current conditions argue for closing
    this position right now" score - the tweakable algorithm behind
    auto_exit_enabled, direct request: exits should be automated using
    "sensible factors from kalshi and whale watch data," not just a single
    fixed cutoff. Three factors, each independently weighted and each
    normalized to its own 0-1 pressure scale before being averaged, so
    tuning one weight doesn't change what the others mean:

    - pnl: magnitude of unrealized P&L (from Kalshi's live price), scaled
      against separate configurable reference points for gains vs losses -
      a 50%+ unrealized gain maxes out the same as a 30%+ unrealized loss,
      by default, since either extreme is a real reason to act even without
      a hard take-profit/stop-loss configured.
    - sentiment: how decisively recent whale prints on this ticker (the
      same signal_feed/_whale_lean the dashboard's own whale-lean displays
      and exit_on_sentiment_reversal already read) have flipped against the
      side actually held. 50/50 = no pressure, 100% opposite = full
      pressure - absent entirely (not zero) when there's no whale data at
      all for this ticker, so silence isn't mistaken for agreement.
    - staleness: how long since the most recent whale print on this ticker
      (either side) - the original edge that justified the trade going
      quiet is itself a signal, independent of price or lean direction.
    - analyst_divergence: how far the market analyst agent's own most
      recent probability estimate (market_analyst_agent.analyst_lean(),
      the same cheap indexed-read helper already feeding entry confidence
      in services/market_strategy.py and services/whalewatchers/
      kalshi_trade_tape.py - never a fresh LLM call) has moved away from
      the side actually held. Deep-scan finding 2026-08-10: analyst_lean()
      was already informing whether to get IN to a position on both
      strategies, but nothing ever consulted it on whether to get OUT -
      a real asymmetry given the same cheap read was sitting right there.
      50/50 (lean == 0.5) = no pressure, fully opposite = full pressure,
      same "50/50 = no pressure" language as the sentiment factor above -
      absent entirely (not zero) when there's no fresh estimate on file
      for this ticker at all, same "don't penalize for missing data"
      idiom every other optional factor here already follows.

    Missing factors (e.g. no whale prints on this ticker at all) are left
    out of the average entirely rather than treated as 0 - same "don't
    penalize for missing data" principle as the entry side's
    min_resolved_for_whale_filter."""
    gain_ref = strat_cfg.get("auto_exit_gain_reference_pct", 0.5)
    loss_ref = strat_cfg.get("auto_exit_loss_reference_pct", 0.3)
    stale_after = strat_cfg.get("auto_exit_stale_after_sec", 1800)
    w_pnl = strat_cfg.get("auto_exit_pnl_weight", 1.0)
    w_sentiment = strat_cfg.get("auto_exit_sentiment_weight", 1.0)
    w_staleness = strat_cfg.get("auto_exit_staleness_weight", 0.5)
    w_analyst = strat_cfg.get("auto_exit_analyst_weight", 0.5)

    if pnl_pct >= 0:
        pnl_factor = min(1.0, pnl_pct / gain_ref) if gain_ref > 0 else 0.0
    else:
        pnl_factor = min(1.0, -pnl_pct / loss_ref) if loss_ref > 0 else 0.0
    factors = {"pnl": (pnl_factor, w_pnl)}

    lean = _whale_lean(ticker, signal_feed)
    if lean is not None:
        opposite_pct = (100 - lean["yes_pct"]) if pos.side == "yes" else lean["yes_pct"]
        sentiment_factor = max(0.0, min(1.0, (opposite_pct - 50) / 50))
        factors["sentiment"] = (sentiment_factor, w_sentiment)

    matches = [s for s in signal_feed if s.get("ticker") == ticker]
    if matches:
        last_seen = max(s.get("timestamp", 0) for s in matches)
        elapsed = max(0.0, time.time() - last_seen)
        staleness_factor = min(1.0, elapsed / stale_after) if stale_after > 0 else 0.0
        factors["staleness"] = (staleness_factor, w_staleness)

    lean_estimate = market_analyst_agent.analyst_lean(ticker, max_age_sec=_ANALYST_FRESHNESS_SEC)
    if lean_estimate is not None:
        divergence = (0.5 - lean_estimate) if pos.side == "yes" else (lean_estimate - 0.5)
        analyst_factor = max(0.0, min(1.0, divergence / 0.5))
        factors["analyst_divergence"] = (analyst_factor, w_analyst)

    total_weight = sum(w for _, w in factors.values())
    confidence = (sum(f * w for f, w in factors.values()) / total_weight) if total_weight > 0 else 0.0
    return confidence, factors
