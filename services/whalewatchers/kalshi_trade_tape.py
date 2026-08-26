"""
Real (not simulated) whale-watcher provider — Kalshi has no public trader
identity or leaderboard (trades are anonymous member-to-member, confirmed
directly during research, not assumed), so this is strictly size-based whale
detection: a real trade printed on the exchange whose CONTRACT COUNT clears
a configurable threshold. See docs/kalshi-whale-provider-and-
strategy-porting-plan.md Part 1 for the full research and design writeup.

Contract count, not notional dollar value, is the gate (ROADMAP.md, shipped
2026-08-22 per docs/next-session-pickup-2026-08-17.md). A fixed dollar
threshold is geometrically biased toward near-certain prices — $2,500 buys
125,000 contracts at 2c but only 2,505 at 99.8c — so it was never finding
informed traders, only whoever could afford to buy a near-certainty in
size. Measured across 145,785 real captured prints: the dollar gate's picks
sat at mean unit cost 0.926 (75.9% of them ≥0.95, the band that bleeds
money); a `count >= 5,000` selector lands at mean 0.759, with 27.3% inside
the only profitable band versus 8.6% for the dollar gate. Real dollar
notional is still computed and recorded (`_notional_usd`, `raw_context`)
for diagnostics — it just no longer gates anything.

Needs no credentials and calls no external API of its own — it classifies
data this app already fetches every trading-loop tick (main.py's
_fetch_trade_tape(), already populating state["trade_tape"] before this
provider runs), passed in via fetch_signals()'s market_context param. Zero
extra API cost, same "already-fetched, don't fetch again" discipline
services/market_history.py uses.
"""
import asyncio
import time
from collections import deque
from datetime import datetime

from services import candidate_log, candidate_retry, config_bounds, market_analyst_agent, market_history, series_evaluator, signal_log
from services import whale_pipeline_perf
from services import http_client
from services.kalshi.contracts import trade as trade_contract
from services.confidence_scoring import WhaleSignal, composite_confidence_breakdown
from services.whalewatchers.base import WhaleWatcherProvider

_DEFAULT_MIN_CONTRACTS = 5000.0
# Trend-consistency window - how far back to look when asking "what has
# this market's price actually been doing lately."
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
_MAX_SEEN_TRADE_IDS = 250000
# Raised 5,000 -> 250,000 on 2026-08-17. This ring is what guarantees a
# print is evaluated exactly once, and it has to outlive every path that
# can re-present the same trade: the REST tape poll deliberately re-fetches
# an overlapping window every tick ("a little re-fetched overlap is
# harmless, the provider dedupes by trade_id" - see _fetch_trade_tape), and
# the websocket path captures the same prints independently.
#
# At 5,000 it no longer did. Measured: 1,640 prints/min across the WATCHED
# series alone, so the ring cycled every ~183 seconds - and the provider
# sees exchange-wide flow, many times that, so eviction was happening in
# well under a minute. Any trade re-presented after eviction would be
# scored and emitted a SECOND time as a fresh whale signal, corrupting both
# the feed and signal_log's own accuracy statistics.
#
# 250,000 ids is a few hours of exchange-wide flow and a few tens of MB of
# strings - cheap next to the alternative, which is silent double-counting
# that looks exactly like real signal volume.
# How long an on-demand market lookup stays usable before it's re-fetched
# (_resolve_unknown_markets). Short enough that volume_24h/close_time can't
# go badly stale on a fast-moving market, long enough that a market printing
# repeatedly costs one call rather than one per print.
_MARKET_CACHE_TTL_SEC = 300
_MAX_MARKET_CACHE = 2000
# Ceiling on markets resolved in a single fetch_signals() call - see
# _resolve_unknown_markets. get_markets_by_tickers batches 50/request, so
# this is 2 requests worst case.
_MAX_ONDEMAND_MARKET_FETCH = 100
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


# A13: direction resolution is boundary-owned. Same-object aliases (not
# wrappers), so the provider, series_watcher, and the boundary literally
# share one implementation and can never disagree about a trade's
# direction or its side-aware notional - the two semantics whose historical
# bugs (taker_side defaulting to "no"; cost without the no-side inversion)
# this module's own docstrings chronicle. The full precedence rules and
# never-guess rationale live with the implementation in
# services/kalshi/contracts/trade.py.
_taker_side = trade_contract.resolve_taker_outcome_side


def _price_dollars(trade: dict, key: str) -> float | None:
    """One price field, or None when the trade doesn't carry a usable one.

    Exists because `float(trade.get(key) or 0)` - the idiom this replaces -
    turns a MISSING price into 0.0, which is not a missing value but a
    perfectly valid-looking one. Downstream it becomes `signal.price = 0.0`,
    and for a no-side signal that is a unit cost of 1.00: a position paying
    the full dollar for a contract that can pay at most a dollar. Four such
    entries were found in real trade history (unit costs 0.97, 1.00, 0.20,
    0.97) and were not traceable to any gate, because nothing was wrong with
    the gates - the price handed to them was fabricated before they ever ran.

    This is the same failure this codebase has now paid for three times, and
    CLAUDE.md documents the other two: `taker_side` defaulting to "no" when
    the field went missing, and `cost = size * price` without the no-side
    inversion. In every case a missing or mis-derived value took on a
    plausible number instead of failing, so nothing looked broken until the
    money was already gone.

    Kalshi emits these as fixed-point STRINGS (docs/kalshi/get-trades.md's
    FixedPointDollars), so a real "0.00" is a non-empty string and would
    parse to 0.0 legitimately - that case is rejected too, one layer up, by
    the tradeable-range invariant in strategy_engine. Here the only job is
    to never invent a number that was not sent."""
    raw = trade.get(key)
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


_notional_usd = trade_contract.taker_notional_usd


def min_contracts_for(ticker: str, wwk_cfg: dict) -> float:
    """This series' whale threshold, or the global default. Public and used
    by BOTH the pre-scan in fetch_signals and the real gate in
    _process_trades_sync - one definition, so the pre-scan can never decide
    a print is worth resolving a market for that the real gate would then
    reject (or, worse, the reverse)."""
    default = float(wwk_cfg.get("min_contracts", _DEFAULT_MIN_CONTRACTS))
    by_series = wwk_cfg.get("min_contracts_by_series") or {}
    return float(by_series.get(signal_log.series_of(ticker), default))


def _prescan_count(trade: dict) -> tuple[str, float] | None:
    """(side, contract count) for one raw print, using nothing but the
    print itself - no DB, no market data, no state mutation. Deliberately
    cheap and side-effect free: this runs over EVERY message on an
    exchange-wide subscription, including the ~99.9% that will never clear
    a whale threshold, so it must not touch disk and must not mark anything
    seen. Mirrors _prescan_notional's old shape, but keys off count_fp
    directly rather than deriving a dollar notional - see this module's own
    docstring for why count, not dollars, is the gate."""
    side = _taker_side(trade)
    if side is None:
        return None
    count = _price_dollars(trade, "count_fp")
    return (side, count) if count is not None else None


def _trend_factor(ticker: str, side: str, now: float) -> float:
    """Does this print's direction agree with, or fight, the market's own
    recent real price trend? Feeds composite_confidence_breakdown's
    trend_factor (see services/confidence_scoring.py and
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
        # (trade_id, exchange_ts) in insertion order - the eviction ring for
        # _seen_trade_ids, and (I4) the WebSocket path's own record of WHAT it
        # evaluated and WHEN in exchange time, so REST-vs-WS reconciliation
        # (services/diagnostics/trade_capture_reconciliation.py) can bound a
        # window on the same clock Kalshi's created_time uses. Stored in the
        # deque that already exists rather than a second 250k-entry
        # structure; seen_exchange_ts_by_id() materializes it on demand for
        # the manual diagnostic only.
        self._seen_order: deque[tuple[str, float]] = deque()
        # ticker -> (fetched_at, market|None) for markets resolved on demand
        # because a whale-sized print arrived on a market outside the
        # watchlist (see _resolve_unknown_markets). None is cached too - a
        # negative result, so a ticker Kalshi won't return doesn't trigger a
        # fresh lookup on every subsequent print.
        self._market_cache: dict[str, tuple[float, dict | None]] = {}
        # Counters for /api/state - how much of the flow is arriving from
        # outside the watchlist, which is the whole point of going
        # exchange-wide and needs to be observable rather than assumed.
        self.stats = {"prescanned": 0, "whale_sized_offlist": 0, "markets_resolved": 0,
                      "resolve_failures": 0}
        # Tickers whose _resolve_unknown_markets lookup raised THIS ROUND
        # (H4 fix, realtime data-plane remediation plan P2 Task 11) - reset
        # every fetch_signals() call, consulted only by _process_trades_sync
        # within that same call to tell "this market's lookup transiently
        # failed" apart from "no lookup was ever attempted" (a sub-threshold
        # print, or a genuinely confirmed-absent market via _market_cache).
        # Only the former must be left unmarked-seen; the latter two keep
        # today's behavior unchanged.
        self._resolve_failed_tickers: set[str] = set()

    @property
    def enabled(self) -> bool:
        # No credentials needed - reads this app's own already-fetched
        # market data, passed in via market_context. Selecting this provider
        # at all (WHALE_WATCHER_PROVIDER=kalshi_trade_tape in .env) is what
        # opts in; once selected, it's always ready.
        return True

    def _mark_seen(self, trade_id: str, exchange_ts: float | None = None) -> None:
        if trade_id in self._seen_trade_ids:
            return
        self._seen_trade_ids.add(trade_id)
        self._seen_order.append((trade_id, float(exchange_ts) if exchange_ts is not None else time.time()))
        while len(self._seen_order) > _MAX_SEEN_TRADE_IDS:
            oldest, _ = self._seen_order.popleft()
            self._seen_trade_ids.discard(oldest)

    def seen_exchange_ts_by_id(self) -> dict[str, float]:
        """trade_id -> exchange timestamp for every trade still in the dedupe
        ring (I4). O(n) over the ring - for the manual reconciliation
        diagnostic, never the hot path."""
        return dict(self._seen_order)

    def seen_horizon_ts(self) -> float | None:
        """Exchange timestamp of the oldest trade still retained - a
        reconciliation window that starts before this cannot tell a real
        miss from an evicted id."""
        return self._seen_order[0][1] if self._seen_order else None

    async def fetch_signals(
        self, since_ts: float | None = None, market_context: dict | None = None,
    ) -> list[WhaleSignal]:
        # Thin async wrapper - all the real work (including every blocking
        # SQLite call this loop makes: record_rejection per rejected trade,
        # recent_sides_for_ticker/cluster_factor/_trend_factor/_analyst_
        # factor per qualifying one) runs in _process_trades_sync on a
        # worker thread via asyncio.to_thread, never on the event loop that
        # also has to keep serving /api/state and every other request.
        # Direct, confirmed-live incident (2026-08-11): removing the
        # trade-tape cap ("i want trade tape to be unlimited, never
        # capped") multiplied real per-tick trade volume 10-30x; every one
        # of those blocking DB round trips (each opening its own fresh
        # connection) ran synchronously in-line before this fix, and
        # thousands of them in one tick froze the entire app - not a
        # network/rate-limit issue, a single-threaded event loop fully
        # occupied by sequential disk I/O. Follow-up, explicit: "i want to
        # be able to handle this massive influx of data confidently...
        # this needs to be handled expertly" - moving the blocking work off
        # the event loop, not just reducing its volume, is what makes that
        # true regardless of how much real trade data flows through any
        # single tick from here on.
        market_context = market_context or {}
        markets = market_context.get("markets") or []
        trade_tape = market_context.get("trade_tape") or []
        cfg = market_context.get("cfg") or {}
        markets_by_ticker = {m["ticker"]: m for m in markets if m.get("ticker")}
        now = time.time()
        # Stage timing + counters (I2, services/whale_pipeline_perf.py). The
        # worker thread returns its own start/finish clocks and fills
        # `counts` in place; everything is recorded here on the event loop
        # after the await, so the perf singleton never sees a cross-thread
        # write.
        perf = whale_pipeline_perf.perf
        counts: dict[str, int] = {}
        resolve_started = time.monotonic()
        await self._resolve_unknown_markets(
            trade_tape, markets_by_ticker, cfg, market_context.get("client"), now, counts=counts,
        )
        submitted = time.monotonic()
        perf.record_stage("resolve", submitted - resolve_started)
        perf.record_count("to_thread_entries")
        signals, started, finished = await asyncio.to_thread(
            self._process_trades_timed, trade_tape, markets, markets_by_ticker, cfg, now, counts,
        )
        perf.record_stage("thread_wait", started - submitted)
        perf.record_stage("sync", finished - started)
        perf.record_counts(counts)
        return signals

    def _process_trades_timed(
        self, trade_tape: list[dict], markets: list[dict], markets_by_ticker: dict[str, dict],
        cfg: dict, now: float, counts: dict[str, int],
    ) -> tuple[list[WhaleSignal], float, float]:
        started = time.monotonic()
        signals = self._process_trades_sync(trade_tape, markets, markets_by_ticker, cfg, now, counts=counts)
        return signals, started, time.monotonic()

    def score_recovered_trade(
        self, trade: dict, market: dict, cfg: dict, now: float,
    ) -> list[WhaleSignal]:
        """See WhaleWatcherProvider.score_recovered_trade. Delegates
        straight to _process_trades_sync with a singleton trade/market pair
        rather than a fresh, second copy of the scoring logic - a recovered
        candidate (services/candidate_retry.py) is judged through the exact
        same pipeline (agreement/cluster/trend/analyst factors, composite
        confidence, the tradeable-price-range and min-contracts gates) as a
        trade whose market resolved on the first try. May return an empty
        list even on a successful market resolution - the trade can still
        fail a gate this pipeline applies (e.g. the market moved out of the
        tradeable price range while this trade was pending retry); that is
        a real "no signal" outcome, not a bug, and candidate_retry.
        run_pending's own "recovered" counter reflects "the market resolved"
        rather than "a signal was produced," consistent with this module's
        own docstring."""
        ticker = trade.get("ticker")
        if not ticker:
            return []
        return self._process_trades_sync([trade], [market], {ticker: market}, cfg, now)

    @http_client.classify("critical_whale")
    async def _resolve_unknown_markets(
        self, trade_tape: list[dict], markets_by_ticker: dict[str, dict], cfg: dict,
        client, now: float, counts: dict[str, int] | None = None,
    ) -> None:
        """Fetch market data for whale-sized prints on markets outside the
        watchlist, so an exchange-wide trade subscription actually produces
        signals instead of silently dropping ~98% of the flow.

        Before this, _process_trades_sync's `if not market: continue` made
        the trade websocket's own subscription list a hard ceiling on what
        this app could ever see - a print on any unwatched market was not
        filtered or logged, it was never received at all, and once it was
        received (exchange-wide) it was dropped one line into scoring for
        want of a volume figure.

        The ordering here is what makes it affordable, and it is the exact
        inverse of the old loop's: gate on CONTRACT COUNT FIRST (pure
        arithmetic on the print itself - no DB, no network), and only then
        resolve a market for the handful that survive. Measured on KXBTC15M
        2026-08-17: 2 of 5,162 prints cleared the $2,500 threshold that was
        live that day (since replaced by a contract-count gate - see this
        module's own docstring). Paying
        one batched REST call for those 2 is trivial; paying it for all
        5,162 would not be. Results are cached per ticker (negatives
        included), so a market that keeps printing is fetched once, not
        once per print.

        Degrades to today's behaviour - skip the print - when there is no
        client, when the fetch fails, or when Kalshi doesn't return the
        market. A missing market means the confidence score would have to
        be fabricated, and CLAUDE.md's standing rule is that this app
        skips rather than guesses."""
        # Reset every call (H4 fix, P2 Task 11) - stale state from a prior
        # tick must never leak into this one's mark-seen decision.
        self._resolve_failed_tickers = set()
        wwk_cfg = cfg.get("whale_watcher_kalshi") or {}
        wanted: set[str] = set()
        # Retained only so a lookup failure below (candidate_retry, P2 Task
        # 12) can enqueue every real trade behind a failed ticker - not just
        # the ticker itself. More than one whale print can share an
        # off-list ticker in the same tick.
        trades_by_ticker: dict[str, list[dict]] = {}
        for trade in trade_tape:
            ticker = trade.get("ticker")
            trade_id = trade.get("trade_id")
            if not ticker or not trade_id or trade_id in self._seen_trade_ids:
                continue
            self.stats["prescanned"] += 1
            if ticker in markets_by_ticker:
                continue
            cached = self._market_cache.get(ticker)
            if cached is not None and (now - cached[0]) < _MARKET_CACHE_TTL_SEC:
                if cached[1] is not None:
                    markets_by_ticker[ticker] = cached[1]
                continue
            scan = _prescan_count(trade)
            if scan is None or scan[1] < min_contracts_for(ticker, wwk_cfg):
                continue
            self.stats["whale_sized_offlist"] += 1
            if counts is not None:
                counts["offlist_candidates"] = counts.get("offlist_candidates", 0) + 1
            wanted.add(ticker)
            trades_by_ticker.setdefault(ticker, []).append(trade)

        if not wanted or client is None:
            return
        # Bounded per call: a burst of whale prints across many unwatched
        # markets must not turn into an unbounded batch against the shared
        # rate limiter (services/http_client.py) - the same lesson
        # _LIVE_STATUS_MAX_POLL_PER_TICK already encodes in main.py.
        # Anything over the cap is simply not resolved this round; the next
        # print on that market gets another chance.
        batch = sorted(wanted)[:_MAX_ONDEMAND_MARKET_FETCH]
        if counts is not None:
            counts["resolve_calls"] = counts.get("resolve_calls", 0) + 1
        try:
            fetched = await client.get_markets_by_tickers(batch)
        except Exception as exc:
            self.stats["resolve_failures"] += 1
            if counts is not None:
                counts["resolve_failures"] = counts.get("resolve_failures", 0) + 1
            # H4 fix (P2 Task 11): every ticker this round was trying to
            # resolve is now a TRANSIENT miss, not a confirmed negative -
            # _process_trades_sync must not mark_seen these trade_ids, or
            # a real whale print is lost forever to a 429/timeout that had
            # nothing to do with the trade itself.
            self._resolve_failed_tickers |= set(batch)
            # P2 Task 12: give every trade behind a failed ticker a durable
            # retry path instead of relying solely on the same trade_id
            # naturally reappearing in a later trade tape poll (not
            # guaranteed, and not bounded).
            for ticker in batch:
                for trade in trades_by_ticker.get(ticker, []):
                    candidate_retry.enqueue(trade, failure=exc)
            return
        for ticker in batch:
            market = fetched.get(ticker)
            self._market_cache[ticker] = (now, market)
            if market is not None:
                markets_by_ticker[ticker] = market
                self.stats["markets_resolved"] += 1
        # Unbounded growth guard - same shape as _MAX_SEEN_TRADE_IDS above.
        if len(self._market_cache) > _MAX_MARKET_CACHE:
            for ticker in sorted(self._market_cache, key=lambda t: self._market_cache[t][0])[:len(self._market_cache) // 2]:
                del self._market_cache[ticker]

    def _process_trades_sync(
        self, trade_tape: list[dict], markets: list[dict], markets_by_ticker: dict[str, dict],
        cfg: dict, now: float, counts: dict[str, int] | None = None,
    ) -> list[WhaleSignal]:
        """Synchronous by design - see fetch_signals' docstring above for
        why. Every blocking call in here (record_rejection,
        recent_sides_for_ticker, cluster_factor, _trend_factor,
        _analyst_factor, record_trades_observed_bulk) is exactly what used
        to run in-line on the event loop; nothing about the business logic
        itself changed, only where it executes. Safe to run on a worker
        thread: self._seen_trade_ids/_seen_order are only ever touched from
        within one in-flight fetch_signals() call at a time (the trading
        loop awaits each tick's whale-provider call before starting the
        next), and every SQLite connection opened below is created and used
        entirely within this same thread, never shared across threads."""
        wwk_cfg = cfg.get("whale_watcher_kalshi") or {}
        signals: list[WhaleSignal] = []
        # series_evaluator's denominator - "how many real trades has this
        # series actually produced," regardless of whether a given trade
        # goes on to qualify as a whale print below. Aggregated in Python
        # and written once via record_trades_observed_bulk() at the end of
        # this loop, not once per trade inline - direct, confirmed-live
        # incident (2026-08-11): removing the trade-tape cap multiplied
        # real per-tick trade volume 10-30x, and a per-trade DB round trip
        # (record_trade_observed's own fresh _connect() + CREATE TABLE
        # check every call) is synchronous, blocking the whole asyncio
        # event loop for its duration - thousands of those in one tick
        # froze the app for several minutes. Recorded unconditionally (not
        # gated behind series_evaluator.enabled) so the feature has real
        # accumulated history the moment it's turned on, same "preserve a
        # robust dataset" principle as everywhere else in this app.
        trades_observed_by_series: dict[str, int] = {}
        # Per-call counters returned to the event loop (I2) - a plain dict
        # so this thread never touches the perf singleton directly.
        if counts is None:
            counts = {}

        def _bump(counter: str, n: int = 1) -> None:
            counts[counter] = counts.get(counter, 0) + n

        for trade in trade_tape:
            trade_id = trade.get("trade_id")
            if not trade_id or trade_id in self._seen_trade_ids:
                continue
            _bump("trades")

            ticker = trade.get("ticker")
            if ticker:
                series = signal_log.series_of(ticker)
                trades_observed_by_series[series] = trades_observed_by_series.get(series, 0) + 1
            market = markets_by_ticker.get(ticker)
            # H4 fix (realtime data-plane remediation plan, P2 Task 11):
            # mark_seen only once the market lookup has produced a DEFINITE
            # outcome - found, or a genuine negative (this ticker was never
            # even attempted this round, e.g. a sub-threshold print, or
            # _resolve_unknown_markets' own negative cache already confirmed
            # Kalshi's response omits it). A ticker whose lookup was
            # attempted THIS round and raised (self._resolve_failed_tickers)
            # stays unmarked - a transient 429/timeout must never
            # permanently short-circuit a real whale print via the seen
            # dedupe ring; it gets a real chance on a later presentation
            # instead (this trade_id's next appearance in the trade tape,
            # or Task 12's retry queue).
            if market is not None or ticker not in self._resolve_failed_tickers:
                # Exchange time kept for REST-vs-WS reconciliation (I4).
                self._mark_seen(trade_id, exchange_ts=trade_contract.trade_exchange_ts(trade) or now)
            if not market:
                # Can't score confidence without this market's own
                # volume/close_time - skip, don't fabricate. Post
                # exchange-wide subscription this should be rare rather
                # than the norm: _resolve_unknown_markets has already
                # fetched any off-watchlist market whose print cleared the
                # contract-count gate, so reaching here means either a
                # sub-threshold print (expected, the overwhelming majority)
                # or a resolution that genuinely failed. Only the latter is
                # worth logging, and only that case is checked, so the hot
                # path stays free of a DB write per uninteresting trade.
                scan = _prescan_count(trade)
                if scan is None or not ticker or scan[1] < min_contracts_for(ticker, wwk_cfg):
                    _bump("offlist_skipped")
                if scan is not None and ticker:
                    side_n = scan
                    if side_n[1] >= min_contracts_for(ticker, wwk_cfg):
                        _bump("unresolved_market")
                        _bump("rejection_writes")
                        # Cheap dict-field read on data already in hand (no
                        # new DB/API call) so this rejection carries
                        # unit_cost too - see record_rejection's own
                        # docstring / ROADMAP.md on why that matters.
                        unresolved_price = _price_dollars(trade, "yes_price_dollars")
                        unresolved_unit_cost = (
                            (unresolved_price if side_n[0] == "yes" else (1.0 - unresolved_price))
                            if unresolved_price is not None else None
                        )
                        candidate_log.record_rejection(
                            ticker, "whale_watcher", "market_unresolved", side_n[1],
                            min_contracts_for(ticker, wwk_cfg), side=side_n[0], unit_cost=unresolved_unit_cost,
                        )
                continue

            # Resolved before the count gate so a rejection can be logged
            # with a real side, and so both the gate and the emitted signal
            # agree on direction. None means the trade carried no readable
            # direction at all - skip it rather than guessing (see
            # _taker_side's docstring).
            side = _taker_side(trade)
            if side is None:
                continue

            count = _price_dollars(trade, "count_fp")
            if count is None:
                # Unparseable count. Skip rather than let a missing figure
                # become 0, which would filter the trade out as "too small"
                # - the right answer for the wrong reason, and invisible in
                # the rejection stats.
                _bump("rejection_writes")
                candidate_log.record_rejection(
                    ticker, "whale_watcher", "unparseable_count", 0.0, 0.0, side=side,
                )
                continue

            # price is always the yes-side price by convention, same as
            # every other WhaleSignal in this app (confidence_scoring.py,
            # confirmed in ROADMAP.md) - side carries direction separately.
            # Parsed here, before the min_contracts gate below (moved up
            # from after it, 2026-08-23), so a min_contracts rejection - the
            # gate currently under live root-cause investigation,
            # ROADMAP.md's "entry gates select a worse subset" item - can
            # carry unit_cost too. This is a second field read on the same
            # trade dict already in hand, not a new DB/API call, so it's
            # free on the hot path (same "cheap and side-effect free"
            # reasoning as _prescan_count above).
            #
            # Parsed strictly (_price_dollars, no `or 0` default): a missing
            # price used to become 0.0, which is not a missing value but a
            # plausible one. That is how positions at a unit cost of 1.00
            # got opened - the app manufactured a price Kalshi never sent,
            # and every gate downstream then reasoned correctly about a
            # fabricated number.
            price = _price_dollars(trade, "yes_price_dollars")
            if price is None:
                _bump("rejection_writes")
                candidate_log.record_rejection(
                    ticker, "whale_watcher", "unparseable_price", 0.0, 0.0, side=side,
                )
                continue
            unit_cost = price if side == "yes" else (1.0 - price)

            # A single global threshold can't be right for both a
            # low-liquidity niche market and a high-volume political one
            # (ROADMAP.md) - series_of() reuses the same series definition
            # excluded_series/series_stats already key off, with the global
            # min_contracts as the fallback for any series with no override
            # set. Contract count, not dollar notional, is the gate - see
            # this module's own docstring for why.
            min_contracts = min_contracts_for(ticker, wwk_cfg)
            if count < min_contracts:
                _bump("below_threshold")
                _bump("rejection_writes")
                candidate_log.record_rejection(
                    ticker, "whale_watcher", "min_contracts", count, min_contracts,
                    side=side, unit_cost=unit_cost,
                )
                continue
            _bump("candidates")

            # Real dollar notional is no longer gated on, but is still
            # captured for diagnostics/raw_context below (a whale-sized
            # print's actual dollar cost is genuinely useful context, just
            # not the selection criterion anymore).
            try:
                notional = _notional_usd(trade, side)
            except (TypeError, ValueError):
                notional = None

            size = int(round(count))
            if size <= 0:
                continue

            # A print at (or next to) 0c/100c is not a weak signal, it is
            # wrong data (direct instruction, 2026-08-17: "theyre wrong,
            # intrinsic-data-wise... you shouldnt ever be seeing positions
            # being made like this at all... and not because of restrictions
            # but because of practicality").
            #
            # Rejected HERE, before the WhaleSignal is constructed, so it is
            # never logged - not merely never traded. That distinction is
            # the whole point: main.py logs every signal this provider
            # returns, signal_log is what every accuracy statistic reads,
            # and a near-certain print resolves "correct" almost always. So
            # logging them inflates the headline whale win rate with trades
            # that could never have been taken, which is worse than useless
            # - it is a number that looks like evidence.
            if not config_bounds.is_tradeable_unit_cost(unit_cost):
                _bump("rejection_writes")
                candidate_log.record_rejection(
                    ticker, "whale_watcher", "tradeable_price_range",
                    unit_cost, config_bounds.MIN_TRADEABLE_UNIT_COST, side=side, unit_cost=unit_cost,
                )
                continue

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

            # Was this trade designated a block trade by Kalshi itself
            # (docs/kalshi/public-trades.md's is_block_trade field)? A real
            # first-party signal - already parsed into every trade dict by
            # services/kalshi_trade_ws.py, previously never read by anything
            # downstream (found 2026-08-15 while consuming the API reference
            # docs in full). See composite_confidence_breakdown's own (9)
            # for why False scores 0.0, not neutral 0.5.
            is_block_trade = 1.0 if trade.get("is_block_trade") else 0.0

            breakdown = composite_confidence_breakdown(
                market, markets, size, price, now,
                agreement_factor=agreement_factor, cluster_factor=cluster, trend_factor=trend,
                analyst_factor=analyst, block_trade_factor=is_block_trade,
                weights=cfg.get("whale_confidence_weights"), side=side,
            )
            timestamp = trade_contract.trade_exchange_ts(trade) or now

            # Gap 8 of docs/config-tuning-data-gaps-2026-08-10.md - the raw
            # inputs behind the factor breakdown above, captured once here
            # rather than re-derived later (market_catalog/market_history
            # are watchlist-scoped and rotate, so they can't reliably answer
            # "what was this market's spread/volume at the exact moment
            # this signal fired" after the fact). yes_ask_dollars falls
            # back to price itself ("no ask data = assume no spread").
            yes_ask = float(market.get("yes_ask_dollars") or price)
            raw_context = {
                # No longer the gate (contract count is - see docstring),
                # but still real, useful context - and None rather than a
                # fabricated 0.0 on the rare print where price parsed but
                # notional's own internal parse still failed. See
                # signal_log.py's raw_notional_usd docstring: nullable,
                # only real providers with a raw_context populate it.
                "notional_usd": round(notional, 2) if notional is not None else None,
                "spread": round(max(yes_ask - price, 0.0), 4),
                "volume_24h": float(market.get("volume_24h_fp") or 0.0),
            }

            signals.append(WhaleSignal(
                id=trade_id,
                ticker=ticker,
                side=side,
                size=size,
                price=round(price, 2),
                confidence=round(breakdown.score, 2),
                timestamp=timestamp,
                factors=breakdown.to_dict(),
                raw_context=raw_context,
                close_time=market.get("close_time"),
            ))

        return signals
