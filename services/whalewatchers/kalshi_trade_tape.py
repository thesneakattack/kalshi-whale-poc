"""
Real (not simulated) whale-watcher provider — Kalshi has no public trader
identity or leaderboard (trades are anonymous member-to-member, confirmed
directly during research, not assumed), so this is strictly size-based whale
detection: a real trade printed on the exchange whose notional dollar value
clears a configurable threshold. See docs/kalshi-whale-provider-and-
strategy-porting-plan.md Part 1 for the full research and design writeup.

Needs no credentials and calls no external API of its own — it classifies
data this app already fetches every trading-loop tick (main.py's
_fetch_trade_tape(), already populating state["trade_tape"] before this
provider runs), passed in via fetch_signals()'s market_context param. Zero
extra API cost, same "already-fetched, don't fetch again" discipline
services/market_history.py uses.
"""
import time
from collections import deque
from datetime import datetime

from services import market_analyst_agent, market_history, signal_log
from services.whale_simulator import WhaleSignal, composite_confidence_breakdown
from services.whalewatchers.base import WhaleWatcherProvider

_DEFAULT_MIN_NOTIONAL_USD = 2500.0
# Trend-consistency window - matches market_strategy's own default
# momentum_lookback_sec, since both are asking the same underlying question
# (what has this market's price actually been doing lately) over a
# comparable timeframe.
_TREND_LOOKBACK_SEC = 1800
# How large a real price move (in dollars, e.g. 0.05 = 5 cents) counts as
# a "fully" trend-consistent or trend-fighting move - beyond this, trend_
# factor saturates at 1.0/0.0 rather than continuing to scale.
_TREND_FULL_SCALE = 0.05
# get_trades(ticker, limit=10) returns the same recent trades tick after
# tick until they age out of that window — without this, one real trade
# would re-emit as a fresh whale signal on every poll until it fell off the
# last-10 list. Bounded so a long-running process doesn't grow this
# unbounded; old entries age out in insertion order once the cap is hit.
_MAX_SEEN_TRADE_IDS = 5000
# How far back to look for other real whale prints on the same market when
# scoring composite_confidence_breakdown's agreement_factor - shorter than
# diagnostikon/polymarket-whale-momentum-trader's 48h default (see
# docs/kalshi-whale-provider-and-strategy-porting-plan.md Part 2), since
# Kalshi's fastest markets (5/15-minute crypto windows) resolve well within
# 48h and a print from two days ago has little bearing on one right now.
_AGREEMENT_LOOKBACK_SEC = 6 * 3600
# How far back to look for size-compatible same-ticker/same-side prints when
# scoring cluster_factor - matches find_clusters()'s own 30min default
# window, much tighter than agreement_factor's 6h: this is asking "is this
# part of a tight, likely-single-actor accumulation run right now," not
# "has this market's sentiment leaned this way today."
_CLUSTER_LOOKBACK_SEC = 30 * 60
# How fresh a market_analyst_agent estimate must be to count toward
# analyst_factor - the event being estimated is far more stable than a
# market's own price, so this doesn't need agreement_factor/cluster_factor's
# tight windows; just not stale enough to be estimating a different market
# state entirely (see market_analyst_agent.analyst_lean()'s own docstring).
_ANALYST_FRESHNESS_SEC = 24 * 3600


def _notional_usd(trade: dict) -> float:
    """Real dollar size of a trade, side-aware - the same lesson this app
    already paid for once (ROADMAP.md: open_position charged size * price
    unconditionally, but a no-side position's real cost is size * (1 -
    price)). A trade's notional is count * whichever price the taker
    actually paid, not always the yes price."""
    count = float(trade.get("count_fp") or 0)
    taker_side = str(trade.get("taker_side") or "").lower()
    price_key = "yes_price_dollars" if taker_side == "yes" else "no_price_dollars"
    price = float(trade.get(price_key) or 0)
    return count * price


def _parse_trade_time(created_time: str | None) -> float | None:
    if not created_time:
        return None
    try:
        return datetime.fromisoformat(created_time.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def _trend_factor(ticker: str, side: str, now: float) -> float:
    """Does this print's direction agree with, or fight, the market's own
    recent real price trend? Feeds composite_confidence_breakdown's
    trend_factor (see services/whale_simulator.py and
    docs/prediction-market-strategy-alignment-plan.md Part 2.4).

    market_history.momentum()'s delta is positive when price has been
    rising (favors yes) - signed to the print's own side so "with the
    trend" is always positive, then linearly scaled into 0-1 around a
    neutral 0.5 (no clear lean either way), saturating at the extremes
    rather than growing unbounded. Returns the neutral default (0.5) when
    there isn't yet enough real price history to judge - same as this
    function not being called at all, never guessed."""
    mom = market_history.momentum(ticker, _TREND_LOOKBACK_SEC, as_of=now)
    if mom is None:
        return 0.5
    signed_delta = mom["delta"] if side == "yes" else -mom["delta"]
    return 0.5 + 0.5 * min(max(signed_delta / _TREND_FULL_SCALE, -1.0), 1.0)


def _analyst_factor(ticker: str, side: str) -> float:
    """Does this print's direction agree with the market analyst LLM's own
    independent probability estimate for this market, if a fresh one
    exists? Feeds composite_confidence_breakdown's analyst_factor - direct
    request (2026-08-09): "whenever the market analysis agent runs I want
    it to inform the various engines... without consuming AI tokens." A
    single indexed SQLite read (market_analyst_agent.analyst_lean()), never
    a new LLM call - the whole point is this stays free to compute on
    every real trade tape print, unlike the agent itself. Returns the
    neutral default (0.5) when no market has ever been manually analyzed,
    or the one on file has aged out - same idiom as _trend_factor above."""
    lean = market_analyst_agent.analyst_lean(ticker, max_age_sec=_ANALYST_FRESHNESS_SEC)
    if lean is None:
        return 0.5
    return lean if side == "yes" else (1.0 - lean)


class KalshiTradeTapeProvider(WhaleWatcherProvider):
    name = "kalshi_trade_tape"

    def __init__(self):
        self._seen_trade_ids: set[str] = set()
        self._seen_order: deque[str] = deque()

    @property
    def enabled(self) -> bool:
        # No credentials needed - reads this app's own already-fetched
        # market data, passed in via market_context. Selecting this provider
        # at all (WHALE_WATCHER_PROVIDER=kalshi_trade_tape in .env) is what
        # opts in; once selected, it's always ready.
        return True

    def _mark_seen(self, trade_id: str) -> None:
        if trade_id in self._seen_trade_ids:
            return
        self._seen_trade_ids.add(trade_id)
        self._seen_order.append(trade_id)
        while len(self._seen_order) > _MAX_SEEN_TRADE_IDS:
            oldest = self._seen_order.popleft()
            self._seen_trade_ids.discard(oldest)

    async def fetch_signals(
        self, since_ts: float | None = None, market_context: dict | None = None,
    ) -> list[WhaleSignal]:
        market_context = market_context or {}
        markets = market_context.get("markets") or []
        trade_tape = market_context.get("trade_tape") or []
        cfg = market_context.get("cfg") or {}
        wwk_cfg = cfg.get("whale_watcher_kalshi") or {}
        default_min_notional = float(wwk_cfg.get("min_notional_usd", _DEFAULT_MIN_NOTIONAL_USD))
        min_notional_by_series = wwk_cfg.get("min_notional_usd_by_series") or {}

        markets_by_ticker = {m["ticker"]: m for m in markets if m.get("ticker")}
        now = time.time()
        signals: list[WhaleSignal] = []

        for trade in trade_tape:
            trade_id = trade.get("trade_id")
            if not trade_id or trade_id in self._seen_trade_ids:
                continue
            self._mark_seen(trade_id)  # evaluated once, regardless of outcome below

            ticker = trade.get("ticker")
            market = markets_by_ticker.get(ticker)
            if not market:
                continue  # can't score confidence without this market's own volume/close_time - skip, don't fabricate

            try:
                notional = _notional_usd(trade)
            except (TypeError, ValueError):
                continue
            # A single global threshold can't be right for both a
            # low-liquidity niche market and a high-volume political one
            # (ROADMAP.md) - series_of() reuses the same series definition
            # excluded_series/series_stats already key off, with the global
            # min_notional_usd as the fallback for any series with no
            # override set.
            min_notional = float(min_notional_by_series.get(
                signal_log.series_of(ticker), default_min_notional
            ))
            if notional < min_notional:
                continue

            try:
                # price is always the yes-side price by convention, same as
                # every other WhaleSignal in this app (whale_simulator.py,
                # confirmed in ROADMAP.md) - side carries direction separately.
                price = float(trade.get("yes_price_dollars") or 0)
                size = int(round(float(trade.get("count_fp") or 0)))
            except (TypeError, ValueError):
                continue

            side = "yes" if str(trade.get("taker_side") or "").lower() == "yes" else "no"

            # Do recent real prints on this exact market agree with this
            # one? No recent history at all is neutral (0.5) - not scored as
            # either agreement or disagreement, same idiom composite_
            # confidence_breakdown's other missing-data cases already use.
            recent_sides = signal_log.recent_sides_for_ticker(ticker, since_ts=now - _AGREEMENT_LOOKBACK_SEC)
            agreement_factor = (
                sum(1 for s in recent_sides if s == side) / len(recent_sides)
                if recent_sides else 0.5
            )

            # Does this print look like part of an active accumulation run
            # (Barclay & Warner's stealth-trading finding - see
            # docs/prediction-market-strategy-alignment-plan.md Part 2.1),
            # rather than a single conspicuous block with nothing else like
            # it nearby? 0.0 (not 0.5) when nothing qualifies - see
            # signal_log.cluster_factor's docstring for why "isolated" is
            # itself informative here.
            cluster = signal_log.cluster_factor(ticker, side, size, since_ts=now - _CLUSTER_LOOKBACK_SEC)

            # Does this print's direction agree with, or fight, the
            # market's own recent real price trend? See _trend_factor's
            # docstring - a caution factor, not a block.
            trend = _trend_factor(ticker, side, now)

            # Does this print's direction agree with the market analyst
            # LLM's own independent read of this market, if one exists and
            # is fresh? See _analyst_factor's docstring - a cheap DB read,
            # never a new API call.
            analyst = _analyst_factor(ticker, side)

            breakdown = composite_confidence_breakdown(
                market, markets, size, price, now,
                agreement_factor=agreement_factor, cluster_factor=cluster, trend_factor=trend,
                analyst_factor=analyst,
            )
            timestamp = _parse_trade_time(trade.get("created_time")) or now

            signals.append(WhaleSignal(
                id=trade_id,
                ticker=ticker,
                side=side,
                size=size,
                price=round(price, 2),
                confidence=round(breakdown.score, 2),
                timestamp=timestamp,
                factors=breakdown.to_dict(),
            ))

        return signals
