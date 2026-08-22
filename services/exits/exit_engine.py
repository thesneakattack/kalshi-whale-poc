"""
Exit-decision logic for the whale-follow strategy, extracted from
services/strategy_engine.py (2026-08-22 modularization pass, Phase 1/9 -
"exit position management" as its own concern, entry gating stays in
strategy_engine.py). FollowTheWhaleStrategy.check_exits/validate_pending_fill
stay on the class as thin public methods (call sites in main.py/tests are
unaffected); check_exits's real implementation lives here as a free function
taking `broker` explicitly instead of `self`.

close_if_settled is also imported directly by services/market_strategy.py's
own MarketNativeStrategy.check_exits - kept here as the one shared
settlement-close primitive both strategies use, not duplicated.
"""
import time

from services import kalshi_fees, market_analyst_agent, market_history, signal_log
from services import config_overrides
from services.paper_broker import PaperBroker, Position

# Same freshness window services/market_strategy.py and services/
# whalewatchers/kalshi_trade_tape.py already use for analyst_lean() on the
# entry side - the event being estimated is far more stable than a
# market's own price, so this doesn't need to be tight, just not stale
# enough to be estimating a different market state entirely.
_ANALYST_FRESHNESS_SEC = 24 * 3600

# Stop-loss/take-profit price corroboration (2026-08-17, direct instruction
# to fix immediately after confirming a real WTA position - Cirstea/
# Kalinskaya - was liquidated via stop-loss at exit_price 0.0 one tick
# after market_history's own independently REST-polled price had sat
# pinned at 0.99 for 13+ minutes. See check_exits' own comment at the
# corroboration call site for the full incident writeup.
#
# _MAX_AGE_SEC: how stale a market_history snapshot may be and still count
# as corroboration. Generous relative to a healthy ~6-20s REST tick, so a
# genuinely brief slow tick doesn't spuriously disable protection; tight
# enough that a truly stale snapshot (a ticker that fell out of the
# watchlist) can't offer false comfort.
_PRICE_CORROBORATION_MAX_AGE_SEC = 120.0
# _MAX_DEVIATION: how far the WS-sourced current_price may disagree with a
# fresh, independent REST snapshot before it's distrusted. 0.30 is
# deliberately wide - it must never block a genuine, large, fast real
# move (a real match-deciding swing can legitimately cover 20-30 points in
# seconds) while still catching the demonstrated failure (a 0.99 gap:
# 0.99 real vs 0.0 fabricated) with enormous margin. A real fast move that
# also outruns this margin will simply be confirmed on the very next tick,
# once market_history's own REST poll catches up - a few seconds of delay
# on a rare legitimate case, against eliminating instant liquidation of a
# winning position on a garbage single tick. Clearly favorable trade.
_PRICE_CORROBORATION_MAX_DEVIATION = 0.30


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


def check_exits(
    broker: PaperBroker, latest_prices: dict, signal_feed: list, cfg: dict, market_results: dict | None = None,
    opened_since: float | None = None, category_by_ticker: dict | None = None,
    close_times: dict | None = None,
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

    opened_since (real live bug, 2026-08-11 - direct report: whale
    trades opening and never showing up as open positions): main.py
    calls this once per tick, right after that same tick's signal loop
    may have just opened brand-new positions, using the SAME
    latest_prices snapshot that was fetched at the top of the tick -
    before this tick's trade-tape read, which can carry a real trade a
    few seconds *newer* than the quote poll on a fast-moving market. A
    position's entry_price can therefore already be more current than
    latest_prices[ticker], so marking it to market against that stale
    snapshot manufactures a fake swing out of nothing but polling
    order, not an actual price move - confirmed live on a 15-minute
    BTC market: a whale bought yes at 0.82 while latest_prices still
    held a 0.67 quote from moments earlier, computing an instant fake
    -21% "loss" and stop-lossing a position that went on to settle at
    0.999. Passing tick_now here as opened_since skips any position
    with pos.opened_at >= opened_since - i.e. anything opened this same
    tick - leaving it untouched until the next tick's latest_prices has
    actually caught up to its own entry price. None (the default)
    checks every position regardless of age, matching every existing
    caller/test.

    Returns decision dicts in the same shape evaluate() returns for a
    trade, so main.py can log them into decision_feed/stats the same
    way.

    category_by_ticker (2026-08-15, per-series/category tuning direct
    request - services/config_overrides.py): resolved PER POSITION
    inside the loop below, not once up front like the old flat
    strat_cfg - different open positions can belong to different
    series/categories, and the resolved dict is also what's passed
    into _exit_confidence so every auto_exit_* weight/reference is
    override-aware too, not just the three hard-rule fields."""
    base_cfg = cfg["strategy"]
    overrides = cfg.get("strategy_overrides")
    category_by_ticker = category_by_ticker or {}
    # ticker -> close_time string, resolved by main.py from the markets
    # it already fetched this tick (zero new API calls, same
    # "already-fetched, don't fetch again" discipline as
    # category_by_ticker above). Position itself doesn't carry a
    # close_time, and it couldn't safely: close_time is mutable
    # (docs/kalshi/market_lifecycle.md's close_date_updated event), so
    # a value captured at entry could be stale by exit time.
    close_times = close_times or {}
    exit_now = time.time()

    market_results = market_results or {}
    decisions = []
    # Snapshot first - closing a position mutates broker.positions,
    # which we'd otherwise be iterating over while mutating.
    for ticker, pos in list(broker.positions.items()):
        result = (market_results.get(ticker) or "").strip().lower()
        if result in ("yes", "no"):
            closed = close_if_settled(broker, ticker, pos, result)
            if closed is not None:
                decisions.append(closed)
            continue  # settled - the opt-in checks below no longer apply to this position

        if opened_since is not None and pos.opened_at >= opened_since:
            continue  # opened this same tick - latest_prices predates its entry_price, see opened_since above

        strat_cfg = config_overrides.resolve(
            base_cfg, overrides, category=category_by_ticker.get(ticker), series=signal_log.series_of(ticker),
        )
        take_profit_pct = strat_cfg.get("take_profit_pct")
        stop_loss_pct = strat_cfg.get("stop_loss_pct")
        exit_on_reversal = strat_cfg.get("exit_on_sentiment_reversal", False)
        min_signals = strat_cfg.get("exit_sentiment_min_signals", 3)
        reversal_lean_pct = strat_cfg.get("exit_sentiment_lean_pct", 65)
        auto_exit_enabled = strat_cfg.get("auto_exit_enabled", False)
        auto_exit_threshold = strat_cfg.get("auto_exit_threshold", 0.6)
        exit_min_seconds_to_close = strat_cfg.get("exit_min_seconds_to_close")

        current_price = latest_prices.get(ticker, pos.entry_price)
        # Corroborate against market_history's independently
        # REST-polled price before trusting a single websocket tick for
        # a stop-loss/take-profit decision (2026-08-17, direct
        # instruction: "fix this immediately"). Real, confirmed-live
        # incident: a WTA position (Cirstea/Kalinskaya) was liquidated
        # via stop-loss at exit_price 0.0 one tick after market_history
        # had sat pinned at 0.99 for 13+ minutes - the real market
        # believed this position was a near-lock winner, and per the
        # user's own direct confirmation of the real match result, it
        # was. `latest_prices` is written by the websocket ticker
        # handler with no corroboration at all; a single garbage quote
        # (a thin, in-play sports order book briefly presenting a
        # near-zero top-of-book price) was trusted completely and
        # instantly destroyed a winning position. Confirmed the same
        # night on 3 of 3 checked tennis positions and 0 of 0 crypto
        # ones in the same window - scoped to corroboration for
        # everyone, not just tennis, since the failure mode (trusting
        # one unconfirmed tick) is general even though this particular
        # trigger looks illiquidity-specific.
        #
        # "No recent snapshot" (market_history.recent_price returns
        # None) changes nothing - fails open, exactly like before this
        # fix, so an illiquid/newly-discovered ticker with no REST
        # history yet is never blocked from having its stop-loss work.
        corroborated = market_history.recent_price(
            ticker, _PRICE_CORROBORATION_MAX_AGE_SEC, as_of=exit_now,
        )
        if corroborated is not None and abs(current_price - corroborated) > _PRICE_CORROBORATION_MAX_DEVIATION:
            current_price = corroborated
        # broker.cost_basis(), not pos.size * pos.entry_price directly -
        # that formula is only correct for the yes side; see
        # PaperBroker.cost_basis's docstring and open_position's unit_cost.
        cost_basis = broker.cost_basis(ticker)
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
        close_fee = kalshi_fees.taker_fee(pos.size, current_price, ticker=ticker)
        pnl_pct = (broker.mark_to_market(ticker, current_price) - pos.entry_fee - close_fee) / cost_basis

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
        elif (
            exit_min_seconds_to_close
            and (secs_left := market_history.seconds_to_close(close_times.get(ticker), exit_now)) is not None
            and secs_left <= exit_min_seconds_to_close
        ):
            # Time-to-close forced decision (ROADMAP #1's second gate).
            # take_profit/stop_loss/auto_exit are all purely price-driven,
            # so nothing forced a decision as runway ran out - a position
            # could sit through its market's final seconds and settle
            # unmanaged. Safe as an elif (unlike exit_on_reversal below,
            # see that branch's comment): this condition tests both
            # "enabled" and "actually triggered" in one expression, so it
            # never consumes the chain's one shot without setting a reason.
            reason = (
                f"runway exhausted: {secs_left:.0f}s to close "
                f"(floor {exit_min_seconds_to_close:.0f}s) — closing rather than riding to settlement"
            )
        else:
            # Real bug found 2026-08-14: this used to be `elif
            # exit_on_reversal:` / `elif auto_exit_enabled:` - two
            # separate elif branches off the SAME chain as take_profit/
            # stop_loss above. Those two are safe as elif because their
            # own condition already tests both "enabled" and "actually
            # triggered" in one expression. exit_on_reversal wasn't: the
            # elif only tested the enabled flag, so whenever it was
            # True the branch was taken unconditionally, and if the
            # sentiment check inside then found nothing (the common
            # case - most ticks, most tickers), reason stayed None and
            # the chain had already used its one shot, so `elif
            # auto_exit_enabled` below never even ran - auto_exit was
            # completely unreachable for the entire time
            # exit_on_sentiment_reversal was also on (which, per
            # config/settings.yaml's history, was simultaneously true
            # with auto_exit_enabled for a long stretch). Docstring
            # above (`auto_exit_enabled`'s own line) always said this
            # should run "only when none of the three hard rules
            # already decided to close" - decided, not merely enabled.
            if exit_on_reversal:
                lean = _whale_lean(ticker, signal_feed)
                if lean and lean["count"] >= min_signals:
                    opposite_pct = (100 - lean["yes_pct"]) if pos.side == "yes" else lean["yes_pct"]
                    if opposite_pct >= reversal_lean_pct:
                        reason = (
                            f"whale sentiment reversed: {opposite_pct:.0f}% of {lean['count']} recent "
                            f"prints now lean against this {pos.side} position"
                        )
            if reason is None and auto_exit_enabled:
                confidence, factors = _exit_confidence(pos, pnl_pct, ticker, signal_feed, strat_cfg)
                if confidence >= auto_exit_threshold:
                    breakdown = ", ".join(f"{name}={factor:.0%}" for name, (factor, _weight) in factors.items())
                    reason = (
                        f"auto-exit: composite confidence {confidence:.0%} >= {auto_exit_threshold:.0%} "
                        f"threshold (factors: {breakdown})"
                    )

        if reason is None:
            continue

        trade = broker.close_position(ticker, current_price, reason)
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
    - series_track_record: this series' real whale-follow win rate
      (signal_log.series_stats, same source the entry-side
      min_whale_winrate_pct filter already uses) - independent evidence a
      currently-open position sits in a series that's proven unreliable
      for this strategy, even if this specific position's own price/
      sentiment/analyst factors haven't yet turned against it. Off by
      default (auto_exit_series_track_record_weight: 0.0) - new,
      unvalidated against real data yet, same "ships fully built, opt-in"
      precedent as kelly_fraction_of_cap.

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
    w_series = strat_cfg.get("auto_exit_series_track_record_weight", 0.0)

    # Volatility-normalize the pnl reference points (2026-08-14 direct
    # request - "look at it from all angles... volatility" - a real gap:
    # gain_ref/loss_ref were flat percentages applied identically to a
    # slow-moving political market and a fast 15-minute crypto market, so
    # the same raw move read as equally "decisive" on both, a real
    # contributor to the whipsaw pattern found in this session's trade-
    # history review (auto-exit firing on a ticker's normal noise, not a
    # genuine reversal). Wider references (harder to trigger) when this
    # ticker is currently more volatile than the configured "typical"
    # baseline, tighter when it's calmer than usual. vol_ratio clamped to
    # [0.25, 4.0] so one noisy volatility reading can't send a reference
    # to zero or to an unreachable extreme. Strictly backward compatible:
    # vol_ratio falls back to 1.0 (today's unscaled behavior) whenever
    # there isn't yet enough snapshot history to measure volatility, or
    # when auto_exit_normal_volatility is set to 0/null (explicit opt-out,
    # same "0 disables" convention as kelly_fraction_of_cap).
    normal_vol = strat_cfg.get("auto_exit_normal_volatility", 0.02)
    vol_lookback = strat_cfg.get("auto_exit_volatility_lookback_sec", 1800)
    vol = market_history.volatility(ticker, vol_lookback) if normal_vol else None
    # `vol == 0` is treated as NO READING, not as "perfectly calm"
    # (2026-08-17). volatility() returns None when there aren't enough
    # snapshots, but a real 0.0 when there are and the price never moved -
    # and measured live, 142 of 183 well-sampled markets sat at exactly
    # 0.0, because a price that hasn't ticked in 30 minutes usually means
    # nobody is trading it, not that it is genuinely placid.
    #
    # Feeding that zero through made vol_ratio pin to its 0.25 floor for
    # 78% of markets - a constant, not a discriminator - permanently
    # quartering gain_ref and loss_ref so pnl_factor saturated on a ~24%
    # move instead of the configured ~95%. The auto-exit believed nearly
    # every position was at a P&L extreme. That was invisible from config,
    # since every knob involved looked reasonable; only the ratio's own
    # distribution showed it.
    #
    # No value of auto_exit_normal_volatility could fix it either - a zero
    # numerator clamps to the floor regardless - so this belongs at the
    # point of use, and falling back to 1.0 restores exactly the "unscaled
    # behaviour" this block's own comment above says it intends.
    vol_ratio = (
        max(0.25, min(4.0, vol / normal_vol))
        if vol is not None and vol > 0 and normal_vol else 1.0
    )
    gain_ref *= vol_ratio
    loss_ref *= vol_ratio

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

    # Off by default (see docstring) - only queried at all when a nonzero
    # weight opts in, so a position-management tick doesn't pay for a
    # series_stats() read on every open position for a factor nobody's
    # using yet.
    if w_series > 0:
        min_resolved = strat_cfg.get("min_resolved_for_whale_filter", 5)
        series_record = signal_log.series_stats(ticker, days=30)
        if series_record["resolved"] >= min_resolved and series_record["win_rate"] is not None:
            # Anchored at 50% (coin-flip = neutral), same convention as the
            # sentiment/analyst_divergence factors above - a series win
            # rate at or above 50% contributes no pressure; below it scales
            # linearly up to full pressure at 0%.
            series_factor = max(0.0, min(1.0, (50 - series_record["win_rate"]) / 50))
            factors["series_track_record"] = (series_factor, w_series)

    total_weight = sum(w for _, w in factors.values())
    confidence = (sum(f * w for f, w in factors.values()) / total_weight) if total_weight > 0 else 0.0
    return confidence, factors
