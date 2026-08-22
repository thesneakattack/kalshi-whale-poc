"""
The only file that decides *whether* a whale signal becomes a trade.
Swap this out to change strategy without touching the broker, risk
manager, or data sources.
"""
import time

from services import candidate_log, config_overrides, kalshi_fees, market_analyst_agent, market_history, signal_log
from services.whale_simulator import WhaleSignal
from services.paper_broker import PaperBroker, Position
from services.risk_manager import RiskManager

# One shared definition with the provider, which refuses to LOG such a
# print in the first place - see config_bounds for the full reasoning.
from services.config_bounds import (  # noqa: E402
    MAX_TRADEABLE_UNIT_COST, MIN_TRADEABLE_UNIT_COST, is_tradeable_unit_cost,
)

# Same freshness window services/market_strategy.py and services/
# whalewatchers/kalshi_trade_tape.py already use for analyst_lean() on the
# entry side - the event being estimated is far more stable than a
# market's own price, so this doesn't need to be tight, just not stale
# enough to be estimating a different market state entirely.
_ANALYST_FRESHNESS_SEC = 24 * 3600
# Fallback only - the live value is strategy.close_window_sec in
# config/settings.yaml (2026-08-14 direct report: this was a hardcoded
# constant with no config knob, so the only way to change the execution
# window was editing source). Kept here so cfg dicts that don't set the
# field (tests, ml_feed's synthetic configs) still get a sane default.
_MAX_CLOSE_WINDOW_SEC = 2 * 3600

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


def open_position_count_in_series(broker: PaperBroker, ticker: str) -> int:
    """How many of broker's currently-open positions belong to the same
    series (signal_log.series_of) as ticker - shared by both strategies'
    entry gates (deep-scan finding 2, 2026-08-10). Before this, the only
    per-position entry gate was per-ticker (`if signal.ticker in
    self.broker.positions`) - nothing aggregated by series, so a burst of
    correlated signals (a whole tournament, an election contract family)
    could each individually clear max_position_pct while collectively
    representing a much bigger bet on one real-world outcome than the risk
    config implies. Takes a ticker rather than a pre-derived series so
    callers (including services/market_strategy.py, which doesn't
    otherwise import signal_log) don't need their own series_of() call."""
    series = signal_log.series_of(ticker)
    return sum(1 for open_ticker in broker.positions if signal_log.series_of(open_ticker) == series)


def kelly_scaled_max_size(max_size: float, confidence: float, effective_threshold: float, kelly_fraction: float) -> float:
    """Scales max_size (the max_position_pct hard ceiling) down toward
    zero as confidence approaches effective_threshold from above, full
    ceiling at confidence 1.0 - deep-scan finding 1 (2026-08-10): position
    sizing was pure fixed-fraction before this, a signal that barely
    cleared the entry bar and one at near-perfect confidence got
    identically sized positions, throwing away the entire composite
    confidence score the instant the entry decision was made.

    kelly_fraction (strategy.kelly_fraction_of_cap / market_strategy.
    kelly_fraction_of_cap) is a fractional-Kelly dial, NOT full literal
    Kelly - confidence is a 0-1 heuristic score, not a calibrated win
    probability with known payout odds, so this is a linear interpolation
    between two ends, not the Kelly formula itself. At 0.0 (the default)
    this returns max_size unchanged - nothing about existing behavior
    changes unless deliberately turned on, same "ships fully built,
    opt-in" precedent as every other optional engine in this app
    (advisory.enabled, market_analyst.enabled, confidence_calibration.
    enabled, ...). At 1.0, full linear scaling: a signal right at the
    threshold gets close to 0 size, one at confidence 1.0 gets the full
    ceiling. Values between blend the two. max_position_pct/
    max_trade_size stays the hard ceiling this only ever shrinks toward,
    never exceeds - this never returns more than max_size.

    kelly_fraction is treated as "off" for None the same as for 0 - real
    live bug (2026-08-15): callers read this via strat_cfg.get(
    "kelly_fraction_of_cap", 0.0), which only applies that default when
    the key is *missing*; a key present with value null (Python None, a
    normal way to try to "turn a field off" in this app's own config
    conventions - take_profit_pct/stop_loss_pct/max_open_positions_per_
    series all treat null that way) reached here unchanged and crashed
    `None <= 0` on every signal that reached this call - which also
    skipped that tick's check_exits/position_netting.review, since all
    three share one try block in main.py's tick loop. kelly_fraction_of_
    cap: null was the committed config default until this same fix."""
    if kelly_fraction is None or kelly_fraction <= 0 or effective_threshold >= 1.0:
        return max_size
    raw_scale = min(1.0, max(0.0, (confidence - effective_threshold) / (1.0 - effective_threshold)))
    scale = 1.0 - kelly_fraction * (1.0 - raw_scale)
    return max_size * scale


class FollowTheWhaleStrategy:
    def __init__(self, broker: PaperBroker, risk: RiskManager):
        self.broker = broker
        self.risk = risk

    def evaluate(
        self, signal: WhaleSignal, cfg: dict, is_live: bool | None = None, market_results: dict | None = None,
        config_fingerprint: str | None = None, latest_prices: dict | None = None, category: str | None = None,
        me_complement: str | None = None, market_titles: dict | None = None, event_titles: dict | None = None,
        markets: list | None = None,
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
        brand-new position with a predetermined, already-known outcome.

        category ("web of expertise" audit, 2026-08-11): main.py resolves
        this from state["market_titles"]/state["event_titles"] before
        calling in, same lookup services/trade_category.py's own
        record_category() already uses - optional (None from any caller
        that doesn't pass it, same backward-compatible default every other
        optional param here already follows). Together with this signal's
        own series (signal_log.series_of), resolved into an effective
        strat_cfg via services/config_overrides.py right below - every
        strategy.* field this method reads is therefore already
        category/series-aware, not just entry_threshold (this subsumes the
        older, single-field entry_threshold_by_category mechanism - see
        config/settings.yaml's strategy_overrides).

        market_titles/event_titles/markets: state["market_titles"]/
        state["event_titles"]/state["markets"] passed through explicitly by
        the caller, used only by the special-market conservative gate below
        (can_close_early/collateral_return_type/mutually_exclusive lookup).
        Previously read via a lazy `import main` reach-around; main.py's
        modularization pass replaced that with explicit params like every
        other input here.

        me_complement (2026-08-14 direct request): the other ticker in a
        confirmed 2-outcome mutually-exclusive pair (services/
        mutual_exclusivity.py), when signal.ticker is one half of one -
        main.py resolves this once per tick from already-fetched
        state["markets"]/state["event_titles"], zero new API calls. When
        given and a position is already open on that complement ticker,
        this signal is skipped - holding both halves of a genuine 2-way
        matchup (e.g. yes on "Team A to win" AND yes on "Team B to win")
        is a real offsetting-bet risk, the same "betting against yourself"
        shape as the whipsaw pattern found in this session's trade-history
        review, just across two different tickers instead of one ticker
        re-entered over time."""
        series = signal_log.series_of(signal.ticker)
        strat_cfg = config_overrides.resolve(cfg["strategy"], cfg.get("strategy_overrides"), category=category, series=series)

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

        # Whale watcher's input can include markets from a broad feed; only the
        # actual whale-follow auto-trades are restricted to markets closing
        # within strategy.close_window_sec. This keeps the upstream signal
        # source unrestricted while enforcing the requested execution window
        # here. If the market is currently LIVE (in-play), ignore the
        # scheduled close time protections — live status implies the
        # scheduled close may not be authoritative.
        close_window_sec = strat_cfg.get("close_window_sec", _MAX_CLOSE_WINDOW_SEC)
        seconds_to_close = market_history.seconds_to_close(signal.close_time, time.time())
        # allow signals with no close_time to proceed; only reject when a close_time
        # is present and it's outside the permitted window — but skip this rule
        # when the market is currently live (is_live truthy).
        if not is_live and seconds_to_close is not None and not (0 < seconds_to_close <= close_window_sec):
            candidate_log.record_rejection(
                signal.ticker, "whale_follow", "close_window",
                seconds_to_close, close_window_sec, side=signal.side,
            )
            return self._skip(signal, "close time is not within the trade window")

        # Entry-side MINIMUM runway (ROADMAP #1, 2026-08-16 — "otherwise I
        # can't trust any insights whatsoever"). close_window_sec above is
        # only an UPPER bound; nothing refused an entry once too little time
        # remained to actually manage the position before close, so a
        # position could open with seconds of runway and ride straight to
        # settlement having crossed none of take_profit/stop_loss/auto_exit.
        # The special_market_min_seconds_to_close grace below looks like it
        # covers this but doesn't - it only fires for markets with
        # can_close_early/collateral_return_type/mutually_exclusive set,
        # which plain crypto price-crossing markets (KXBTC15M) never have.
        # This one is universal and applies regardless of those flags.
        # Confirmed live cost of not having it (24h to 2026-08-17T00:00Z):
        # 755 KXBTC15M whale signals fired inside the final 60 seconds of
        # their market's life, and 12 of 25 stop-losses fired only after
        # price had already gapped >=10 points past the configured limit -
        # a percentage stop cannot help on a market that settles to zero.
        # Skipped when is_live, same reasoning the close_window check above
        # already uses: for an in-play event the scheduled close time isn't
        # authoritative.
        min_seconds_to_close = strat_cfg.get("min_seconds_to_close")
        if (
            not is_live
            and min_seconds_to_close
            and seconds_to_close is not None
            and seconds_to_close < min_seconds_to_close
        ):
            candidate_log.record_rejection(
                signal.ticker, "whale_follow", "min_seconds_to_close",
                seconds_to_close, min_seconds_to_close, side=signal.side,
            )
            return self._skip(
                signal,
                f"only {seconds_to_close:.0f}s of runway left before close "
                f"(minimum {min_seconds_to_close:.0f}s) — too late to manage a position",
            )

        # Conservative gate for markets with early-close or special settlement
        try:
            m_info = (market_titles or {}).get(signal.ticker) or {}
            et = m_info.get("event_ticker")
            ev = (event_titles or {}).get(et) or {}
            special_flags = {
                "can_close_early": False,
                "collateral_return_type": None,
                "mutually_exclusive": False,
            }
            # market-level can_close_early is exposed in state["markets"] slim maps
            for m in (markets or []):
                if m.get("ticker") == signal.ticker:
                    special_flags["can_close_early"] = bool(m.get("can_close_early"))
                    break
            special_flags["collateral_return_type"] = ev.get("collateral_return_type")
            special_flags["mutually_exclusive"] = bool(ev.get("mutually_exclusive"))
            # Only apply the special-market conservative gate when the market
            # is not currently live. If live, ignore scheduled close/grace
            # windows since the event is in-play and scheduled times may be
            # overridden by live milestones.
            if (special_flags["can_close_early"] or special_flags["collateral_return_type"] or special_flags["mutually_exclusive"]) and not is_live:
                grace = strat_cfg.get("special_market_min_seconds_to_close", 300)
                if seconds_to_close is not None and seconds_to_close < grace:
                    candidate_log.record_rejection(
                        signal.ticker, "whale_follow", "special_market_gate", seconds_to_close, grace, side=signal.side
                    )
                    return self._skip(signal, "market has special settlement/early-close — skipping close-in-time")
        except Exception:
            # best-effort only - don't break trading on inspection failure
            pass

        # Manual override on top of the automatic win-rate filter below - for
        # a series the user has out-of-band reason to distrust before it's
        # racked up enough resolved signals for the automatic cutoff to ever
        # trigger. Same series definition as the automatic filter
        # (signal_log.series_of), not a second one that could drift.
        excluded_series = strat_cfg.get("excluded_series") or []
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
        longshot_close_window = strat_cfg.get("longshot_close_window_sec", 900)
        is_longshot = signal.price <= longshot_zone or signal.price >= (1 - longshot_zone)
        is_near_close = (
            seconds_to_close is not None
            and seconds_to_close <= longshot_close_window
        )
        if is_longshot and (is_live or is_near_close):
            longshot_bonus = 0.0
        # strat_cfg["entry_threshold"] is already category/series-resolved
        # (see this method's own docstring + config_overrides.resolve()
        # call above) - no separate lookup needed here anymore.
        effective_threshold = strat_cfg["entry_threshold"] + (longshot_bonus if is_longshot else 0.0)
        if signal.confidence < effective_threshold:
            reason = f"confidence {signal.confidence} below threshold ({effective_threshold:.2f}"
            reason += " - longshot zone)" if is_longshot else ")"
            candidate_log.record_rejection(
                signal.ticker, "whale_follow", "entry_threshold",
                signal.confidence, effective_threshold, side=signal.side,
            )
            return self._skip(signal, reason)

        # Avoid this whale's picks on markets like this one once they've proven
        # unreliable here — but only once there's enough resolved history to
        # trust, so one unlucky result doesn't blacklist a whole category.
        min_resolved = strat_cfg.get("min_resolved_for_whale_filter", 5)
        min_winrate = strat_cfg.get("min_whale_winrate_pct", 40)
        record = signal_log.series_stats(signal.ticker, days=30)
        if record["resolved"] >= min_resolved and record["win_rate"] is not None and record["win_rate"] < min_winrate:
            candidate_log.record_rejection(
                signal.ticker, "whale_follow", "min_whale_winrate_pct",
                record["win_rate"], min_winrate, side=signal.side,
            )
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

        # Mutually-exclusive complement check (2026-08-14 direct request,
        # see this method's own me_complement docstring) - the same
        # "silently overwrites/duplicates exposure" risk as the same-ticker
        # check above, just across two tickers that are economically one
        # bet instead of one ticker held twice.
        if me_complement and me_complement in self.broker.positions:
            candidate_log.record_rejection(
                signal.ticker, "whale_follow", "mutually_exclusive_duplicate", 1.0, 0.0, side=signal.side,
            )
            return self._skip(
                signal, f'already holding a position on "{me_complement}", this market\'s mutually-exclusive complement',
            )

        # Price-band gate (2026-08-15, direct priority: "figure out why even
        # with a near 70% winrate only pennies are earned"). Real trade-
        # history analysis (606 settled trades) found the answer precisely:
        # bucketing every settled trade by unit_cost (the actual side-aware
        # price paid per contract - signal.price if yes, 1-signal.price if
        # no) showed real money is made almost entirely in one band and lost
        # everywhere else:
        #   unit_cost 0.1-0.5: net -$1,588 (155 trades) - buying cheap/
        #     underdog contracts, the classic favorite-longshot-bias losing
        #     side, already partially addressed by longshot_price_threshold/
        #     longshot_entry_threshold_bonus below but that only scrutinizes
        #     the extreme ends (<=5c/>=95c) - this data shows real losses
        #     extend across the whole sub-50c range, not just the extremes.
        #   unit_cost 0.5-0.8: net +$1,152 (230 trades, the only
        #     consistently profitable band)
        #   unit_cost 0.8-1.0: net -$314 (208 trades) - the counterintuitive
        #     half of the finding: 78-94% win rates in this band (buying
        #     heavy favorites) still net NEGATIVE, because a win only pays a
        #     few cents while a loss costs nearly the full dollar paid - the
        #     textbook "high win rate, thin edge" trap, not visible from win
        #     rate alone.
        # A hard band, not another graduated bonus - the existing longshot
        # bonus already tried "graduated" for the extremes and the losses
        # persisted well inside where that bonus ever applies. Same
        # min_price/max_price precedent market_strategy.py already uses
        # successfully, adapted to unit_cost (side-aware) since whale-follow
        # trades both sides under one signal.price (always the yes price).
        # None (either bound) means "no limit," same convention as every
        # other optional bound in this app.
        unit_cost = signal.price if signal.side == "yes" else (1 - signal.price)

        # HARD VALIDITY FLOOR - not a tunable preference, and deliberately
        # checked before the configurable band below so no config value can
        # ever widen past it (direct instruction, 2026-08-17: "whale bets at
        # cost 0 or 100c are just plain wrong. youre not even allowed to
        # open positions at that point, even 1c/99c").
        #
        # A contract at unit cost 1.00 pays at most 1.00, so its best case is
        # breaking even and its worst is total loss - there is no price at
        # which that is a trade. At 0.99 the whole position risks 99c to win
        # 1c, needing 99% accuracy just to break even (EV per contract is
        # exactly p - c). At the other end, unit cost 0.00 means the fill
        # carried no cost at all, which is a data artifact rather than a
        # trade - a print at these prices is overwhelmingly a settlement-
        # adjacent or malformed tick, not information about anything.
        #
        # Confirmed live rather than hypothesised: four real entries were
        # found in trade history at unit costs 0.97, 1.00, 0.20 and 0.97,
        # one of them carrying conf 0.25 against a 0.495 threshold - i.e.
        # they bypassed both the price band and the confidence gate by a
        # route not yet identified. This floor makes the whole class
        # unreachable regardless of which path is at fault, which is the
        # right shape of fix for an invariant that should never have been
        # expressible.
        if not is_tradeable_unit_cost(unit_cost):
            candidate_log.record_rejection(
                signal.ticker, "whale_follow", "tradeable_price_range",
                unit_cost, MIN_TRADEABLE_UNIT_COST, side=signal.side,
            )
            return self._skip(
                signal,
                f"unit cost {unit_cost:.4f} is outside the tradeable range "
                f"{MIN_TRADEABLE_UNIT_COST}-{MAX_TRADEABLE_UNIT_COST} — a contract this close to "
                f"0 or 1 has no achievable edge, whatever the signal says",
            )

        min_unit_cost = strat_cfg.get("min_unit_cost")
        max_unit_cost = strat_cfg.get("max_unit_cost")
        if min_unit_cost is not None and unit_cost < min_unit_cost:
            candidate_log.record_rejection(
                signal.ticker, "whale_follow", "min_unit_cost", unit_cost, min_unit_cost, side=signal.side,
            )
            return self._skip(signal, f"price {unit_cost:.2f} is below the minimum unit cost of {min_unit_cost:.2f}")
        if max_unit_cost is not None and unit_cost > max_unit_cost:
            candidate_log.record_rejection(
                signal.ticker, "whale_follow", "max_unit_cost", unit_cost, max_unit_cost, side=signal.side,
            )
            return self._skip(signal, f"price {unit_cost:.2f} is above the maximum unit cost of {max_unit_cost:.2f}")

        # Concentration risk (deep-scan finding 2, 2026-08-10): the check
        # above only ever guards the exact same ticker - nothing previously
        # stopped e.g. five different markets in the same tournament from
        # each individually clearing every other gate and collectively
        # becoming a much bigger bet on one real-world outcome than
        # max_position_pct's per-trade cap implies. None (the default) or 0
        # both mean "no limit," matching kalshi.max_children_per_parent's
        # existing null-means-unlimited convention elsewhere in this app.
        max_open_per_series = strat_cfg.get("max_open_positions_per_series")
        if max_open_per_series:
            open_in_series = open_position_count_in_series(self.broker, signal.ticker)
            if open_in_series >= max_open_per_series:
                return self._skip(
                    signal,
                    f'already at the max of {max_open_per_series} open position(s) on series "{series}"',
                )

        if not self.broker.can_trade(signal.ticker, strat_cfg["cooldown_sec"]):
            return self._skip(signal, "cooldown active for this market")

        max_size = self.risk.max_trade_size(self.broker.bankroll, strat_cfg["max_position_pct"])
        # Deep-scan finding 1 (2026-08-10): scales the ceiling above down
        # by how far this signal's confidence cleared effective_threshold
        # (the real bar it had to pass, including the longshot bonus if
        # applicable) - off by default (kelly_fraction_of_cap: 0.0), see
        # kelly_scaled_max_size's own docstring for the full reasoning
        # (including its own None-guard - `or 0.0` here is belt-and-
        # suspenders, not the only fix).
        kelly_fraction = strat_cfg.get("kelly_fraction_of_cap") or 0.0
        max_size = kelly_scaled_max_size(max_size, signal.confidence, effective_threshold, kelly_fraction)
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

        reason = f"whale print {signal.size} @ {signal.price} (conf {signal.confidence})"

        # Maker/limit-order path (2026-08-15, docs/profit-maximization-
        # assessment-2026-08-15.md direct request: fees were consuming
        # ~60% of gross profit on this book). Off by default - same
        # "ships fully built, opt-in" precedent as kelly_fraction_of_cap/
        # auto_exit_enabled/position_netting.enabled elsewhere in this
        # app. When on, rests a limit order AT the signal's own observed
        # price (a maker fill, 1/4 the taker rate - services/kalshi_fees.
        # py's maker_fee()) instead of taking the market immediately.
        # Deliberately no fallback to a market order on timeout - see
        # PaperBroker.check_pending_fills's own docstring for why this
        # never chases a price the signal that justified it has moved
        # past. This changes WHEN/AT WHAT FEE a signal that already
        # cleared every gate above gets filled, not whether it's worth
        # taking - identical sizing/entry logic either way.
        if strat_cfg.get("use_limit_orders", False):
            timeout_sec = strat_cfg.get("limit_order_timeout_sec", 60)
            order = self.broker.place_limit_order(
                ticker=signal.ticker, side=signal.side, size=contracts, limit_price=signal.price,
                reason=reason, expires_at=time.time() + timeout_sec, config_fingerprint=config_fingerprint,
                signal_seen_at=signal.timestamp,
            )
            if order is None:
                return self._skip(signal, "a limit order is already resting on this ticker")
            return {
                "action": "limit_order_placed",
                "signal": signal.to_dict(),
                "order": {
                    "ticker": order.ticker, "side": order.side, "size": order.size,
                    "limit_price": order.limit_price, "expires_at": order.expires_at,
                },
            }

        trade = self.broker.open_position(
            ticker=signal.ticker,
            side=signal.side,
            size=contracts,
            price=signal.price,
            reason=reason,
            config_fingerprint=config_fingerprint,
            signal_seen_at=signal.timestamp,
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
        # Snapshot first - closing a position mutates self.broker.positions,
        # which we'd otherwise be iterating over while mutating.
        for ticker, pos in list(self.broker.positions.items()):
            result = (market_results.get(ticker) or "").strip().lower()
            if result in ("yes", "no"):
                closed = close_if_settled(self.broker, ticker, pos, result)
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
            close_fee = kalshi_fees.taker_fee(pos.size, current_price, ticker=ticker)
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
