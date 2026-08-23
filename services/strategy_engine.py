"""
The only file that decides *whether* a whale signal becomes a trade.
Swap this out to change strategy without touching the broker, risk
manager, or data sources.
"""
import time
from typing import NamedTuple

from services import candidate_log, config_overrides, market_history, signal_log
from services.whale_simulator import WhaleSignal
from services.paper_broker import PaperBroker
from services.risk_manager import RiskManager
from services.exits import exit_engine

# One shared definition with the provider, which refuses to LOG such a
# print in the first place - see config_bounds for the full reasoning.
from services.config_bounds import (  # noqa: E402
    MAX_TRADEABLE_UNIT_COST, MIN_TRADEABLE_UNIT_COST, is_tradeable_unit_cost,
)

# Fallback only - the live value is strategy.close_window_sec in
# config/settings.yaml (2026-08-14 direct report: this was a hardcoded
# constant with no config knob, so the only way to change the execution
# window was editing source). Kept here so cfg dicts that don't set the
# field (tests, ml_feed's synthetic configs) still get a sane default.
_MAX_CLOSE_WINDOW_SEC = 2 * 3600


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


def _effective_entry_threshold(
    strat_cfg: dict, price: float, is_live: bool, seconds_to_close: float | None,
) -> tuple[float, bool]:
    """The confidence bar a signal must clear before it can trade.

    Favorite-longshot bias, confirmed on real Kalshi data (Bürgi, Deng &
    Whelan 2025 - see docs/prediction-markets-research-reference.md Part
    1.2): longshot-priced contracts (near $0 or $1) are systematically
    overpriced relative to their real win rate, and the bias is far worse
    for takers - this app's real order path (services/
    kalshi_account_client.py defaults to time_in_force="immediate_or_
    cancel") - than makers. A flat entry_threshold applied the same way at
    every price point ignores this; a signal priced in longshot territory
    needs to clear a higher bar, not the same one. market_strategy.py
    already handles this differently (a hard min_price/max_price exclusion
    band) - this is the whale-follow strategy's own gap to close,
    graduated rather than a hard cutoff since a strong enough signal can
    still be worth it even in that zone.

    Shared by evaluate() (which also keeps effective_threshold around for
    kelly_scaled_max_size, below) and validate_pending_fill() (which
    re-derives it at the fill-time price, not the placement-time one - see
    _validate_entry_price's own docstring for why that re-derivation
    exists at all)."""
    longshot_zone = strat_cfg.get("longshot_price_threshold", 0.15)
    longshot_bonus = strat_cfg.get("longshot_entry_threshold_bonus", 0.15)
    longshot_close_window = strat_cfg.get("longshot_close_window_sec", 900)
    is_longshot = price <= longshot_zone or price >= (1 - longshot_zone)
    is_near_close = seconds_to_close is not None and seconds_to_close <= longshot_close_window
    if is_longshot and (is_live or is_near_close):
        longshot_bonus = 0.0
    effective_threshold = strat_cfg["entry_threshold"] + (longshot_bonus if is_longshot else 0.0)
    return effective_threshold, is_longshot


class EntryValidation(NamedTuple):
    ok: bool
    gate_name: str | None = None
    observed: float | None = None
    threshold: float | None = None
    reason: str | None = None


def _validate_entry_price(
    side: str, price: float, confidence: float, effective_threshold: float,
    strat_cfg: dict, is_longshot: bool = False,
) -> EntryValidation:
    """Every price/confidence/band-dependent admission check a signal must
    clear before it becomes a position - shared by evaluate()'s market-
    order path and check_pending_fills()'s limit-fill path (via
    FollowTheWhaleStrategy.validate_pending_fill) so a resting order that
    fills at a moved price is held to the same bar a fresh signal at that
    price would be.

    Root-caused the still-open "four-entry gate bypass" ROADMAP item: four
    real entries at unit costs 0.97, 1.00, 0.20, 0.97, one of them at conf
    0.25 against a 0.495 threshold - check_pending_fills previously called
    open_position() with none of this re-checked at all, only whatever the
    order looked like at placement time."""
    if confidence < effective_threshold:
        reason = f"confidence {confidence} below threshold ({effective_threshold:.2f}"
        reason += " - longshot zone)" if is_longshot else ")"
        return EntryValidation(False, "entry_threshold", confidence, effective_threshold, reason)

    unit_cost = price if side == "yes" else (1 - price)

    # HARD VALIDITY FLOOR - not a tunable preference, and deliberately
    # checked before the configurable band below so no config value can
    # ever widen past it (direct instruction, 2026-08-17: "whale bets at
    # cost 0 or 100c are just plain wrong. youre not even allowed to open
    # positions at that point, even 1c/99c"). A contract at unit cost 1.00
    # pays at most 1.00, so its best case is breaking even and its worst is
    # total loss - there is no price at which that is a trade.
    if not is_tradeable_unit_cost(unit_cost):
        return EntryValidation(
            False, "tradeable_price_range", unit_cost, MIN_TRADEABLE_UNIT_COST,
            f"unit cost {unit_cost:.4f} is outside the tradeable range "
            f"{MIN_TRADEABLE_UNIT_COST}-{MAX_TRADEABLE_UNIT_COST} — a contract this close to "
            f"0 or 1 has no achievable edge, whatever the signal says",
        )

    min_unit_cost = strat_cfg.get("min_unit_cost")
    max_unit_cost = strat_cfg.get("max_unit_cost")
    if min_unit_cost is not None and unit_cost < min_unit_cost:
        return EntryValidation(
            False, "min_unit_cost", unit_cost, min_unit_cost,
            f"price {unit_cost:.2f} is below the minimum unit cost of {min_unit_cost:.2f}",
        )
    if max_unit_cost is not None and unit_cost > max_unit_cost:
        return EntryValidation(
            False, "max_unit_cost", unit_cost, max_unit_cost,
            f"price {unit_cost:.2f} is above the maximum unit cost of {max_unit_cost:.2f}",
        )

    return EntryValidation(True)


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

        # strat_cfg["entry_threshold"] is already category/series-resolved
        # (see this method's own docstring + config_overrides.resolve()
        # call above) - no separate lookup needed here. effective_threshold
        # is kept around past the price-band validation below too, for
        # kelly_scaled_max_size's sizing curve further down.
        effective_threshold, is_longshot = _effective_entry_threshold(
            strat_cfg, signal.price, bool(is_live), seconds_to_close,
        )

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

        # Confidence + price-band gate, both re-derivable at a moved price -
        # see _validate_entry_price's own docstring for why this is a
        # shared function rather than inlined here (the "four-entry gate
        # bypass" fix: check_pending_fills's limit-fill path calls the same
        # function via validate_pending_fill, at the fill-time price,
        # instead of trusting whatever passed at placement time). Real
        # trade-history analysis (606 settled trades, 2026-08-15) is what
        # motivated the unit_cost band in the first place: money is made
        # almost entirely in the unit_cost 0.5-0.8 range (+$1,152/230
        # trades) and lost everywhere else, including the counterintuitive
        # 0.8-1.0 band (-$314/208 trades despite 78-94% win rates - a win
        # only pays a few cents while a loss costs nearly the full dollar
        # paid). The hard 0/1 floor inside _validate_entry_price is a
        # separate, non-configurable invariant (direct instruction,
        # 2026-08-17: "whale bets at cost 0 or 100c are just plain wrong")
        # motivated by the same four real entries (unit costs 0.97, 1.00,
        # 0.20, 0.97) this whole fix closes the remaining gap on.
        validation = _validate_entry_price(
            signal.side, signal.price, signal.confidence, effective_threshold, strat_cfg, is_longshot=is_longshot,
        )
        if not validation.ok:
            candidate_log.record_rejection(
                signal.ticker, "whale_follow", validation.gate_name,
                validation.observed, validation.threshold, side=signal.side,
            )
            return self._skip(signal, validation.reason)

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
                signal_seen_at=signal.timestamp, confidence=signal.confidence,
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
        if trade is None:
            # Execution-layer risk guard fired (self.risk.halted, or the
            # portfolio exposure cap) - belt-and-suspenders against the
            # check_daily_loss() gate above this function already passed;
            # this only fires if that gate and this one somehow disagree,
            # e.g. a future bug in either check.
            return self._skip(signal, f"halted: {self.risk.halt_reason}" if self.risk.halted else "exposure cap")
        return {
            "action": "trade",
            "signal": signal.to_dict(),
            "trade": trade.to_dict(),
        }

    def _skip(self, signal: WhaleSignal, reason: str) -> dict:
        return {"action": "skip", "signal": signal.to_dict(), "reason": reason}

    def validate_pending_fill(
        self, ticker: str, side: str, price: float, confidence: float | None, cfg: dict,
        category: str | None = None, is_live: bool = False, seconds_to_close: float | None = None,
    ) -> tuple[bool, str | None]:
        """Callback wired into PaperBroker.check_pending_fills (main.py) -
        see _validate_entry_price's own docstring for the "four-entry gate
        bypass" bug this closes. A resting limit order was validated once,
        at placement time, against the price it was PLACED at - but it
        fills later, at whatever price the market has moved to by then,
        with nothing re-checking that fill price against the same gates a
        fresh signal at that price would have to clear. Re-resolves
        strat_cfg the same way evaluate() does (category/series-aware, see
        evaluate's own docstring) since a fill can land well after the tick
        that placed the order."""
        series = signal_log.series_of(ticker)
        strat_cfg = config_overrides.resolve(cfg["strategy"], cfg.get("strategy_overrides"), category=category, series=series)
        effective_threshold, is_longshot = _effective_entry_threshold(strat_cfg, price, is_live, seconds_to_close)
        validation = _validate_entry_price(side, price, confidence or 0.0, effective_threshold, strat_cfg, is_longshot=is_longshot)
        if not validation.ok:
            candidate_log.record_rejection(
                ticker, "whale_follow", validation.gate_name, validation.observed, validation.threshold, side=side,
            )
        return validation.ok, validation.reason

    def check_exits(
        self, latest_prices: dict, signal_feed: list, cfg: dict, market_results: dict | None = None,
        opened_since: float | None = None, category_by_ticker: dict | None = None,
        close_times: dict | None = None,
    ) -> list[dict]:
        """Actively manages already-open positions - real implementation now
        lives in services/exits/exit_engine.py (2026-08-22 modularization
        pass, Phase 1/9: exit position management as its own concern). Kept
        as a public method here, unchanged signature, so main.py's/tests'
        existing `strategy.check_exits(...)` call sites don't need to
        change - see exit_engine.check_exits's own docstring for the full
        behavior (settlement close, take-profit/stop-loss, time-to-close
        forced exit, sentiment-reversal, auto-exit composite scoring)."""
        return exit_engine.check_exits(
            self.broker, latest_prices, signal_feed, cfg, market_results=market_results,
            opened_since=opened_since, category_by_ticker=category_by_ticker, close_times=close_times,
        )
