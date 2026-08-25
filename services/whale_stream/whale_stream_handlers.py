"""
The trade/ticker websocket callback layer - dispatches real-time exchange
messages into whale-signal detection, position-management exit checks, and
state bookkeeping. Extracted 2026-08-22 as part of main.py's modularization
pass. Wired once in main.py's lifespan() into trade_stream.run(...).

_process_stream_trade and _process_stream_ticker call strategy.check_exits(
...) directly - this cross-boundary call (position-management logic
invoked from the stream path, not just from trading_loop) is a real,
deliberate coupling from the original code, preserved exactly as-is here.
"""
import asyncio
import time

from services import candidate_log, config_performance, market_analyst_agent, market_history, series_watcher, settlement_edge
from services.account_positions import _slim_fill, _slim_position
from services.app_state import bump_generation, state, strategy, trade_stream, whale_provider
from services.config_store import config_store
from services.kalshi.public import KalshiPublicGateway
from services.market_catalog import market_catalog
from services.market_lookup import _category_by_ticker, _close_time_by_ticker
from services.whale_stream.decision_bridge import _handle_close_decision, _handle_signal
from services.ws_manager import ws_manager

_TRADE_TAPE_UI_CAP = 100  # display-only cap for state["trade_tape"] (the Trade
# Tape panel) - a human never needs to scroll more than this. Used to be the
# SAME cap whale-signal detection's input was truncated to as well (direct
# report, 2026-08-11: "i feel like... its not the only reason whale
# positions were undercounted" - correct: confirmed live, this cap was
# filling up within ~2 minutes under real load, meaning every trade past the
# 100 most-recent *platform-wide, across every watched market combined* was
# silently dropped before whale-filtering ever saw it, real size/threshold
# irrelevant). Detection input is not sliced to this at all anymore - see
# services/whale_stream/whale_stream_handlers._fetch_trade_tape (called from
# main.py's trading_loop), which is genuinely unbounded per direct
# follow-up: "i want trade tape to be unlimited, never capped, for
# whale-watch-worthy markets" (i.e. whatever's on the current watchlist -
# the same scope series_evaluator.py already judges as "whale-worthy").
_TRADE_TAPE_FETCH_LIMIT = 100  # per-ticker get_trades() page size - Kalshi's
# own API default. Not a data limit - _fetch_trades_for_ticker below pages
# via cursor until Kalshi itself reports no more pages, so a ticker with
# more real trades than one page holds still gets every one of them, not
# just the first 100.
_TRADE_TAPE_MAX_PAGES_PER_TICKER = 50  # pure infinite-loop circuit breaker
# (5000 trades on one ticker within one poll interval) in case the API ever
# returns a non-empty cursor forever - not a designed cap, astronomically
# above anything real trading volume should ever produce per ticker per
# tick; matches the documented "empty cursor = no more pages" contract.


def _streaming_trade_tape_enabled() -> bool:
    # Kalshi's websocket market-data stream is authenticated, so this can only
    # replace the polled trade tape when the real trade-tape provider is active
    # AND websocket credentials loaded successfully. Fallback stays on the
    # existing REST polling path otherwise.
    return whale_provider.name == "kalshi_trade_tape" and trade_stream.enabled


_stream_client_cache: dict[str, KalshiPublicGateway] = {}

# Trade-channel CPU quantification (2026-08-24, direct instruction: "the
# websocket stream is spiking CPU usage... just quantify it first" - not a
# resource-ceiling problem (Docker Desktop already has every host core
# allocated, no per-container limit), and more cores wouldn't help a
# single-threaded asyncio event loop's own per-message work anyway. Same
# reasoning as lifecycle_stream_stats (services/app_state.py) / kalshi_
# trade_tape.py's own self.stats: expose real, continuously-updating
# numbers on /api/state instead of guessing at a fix. _process_stream_trade
# runs on an EXCHANGE-WIDE trade subscription (docs/kalshi/CHEATSHEET.md's
# "whole exchange" entry) - live-sampled at ~180 msg/sec while the tick
# loop itself stays under a second (tick_phase_timings) - so this handler
# is the leading CPU-spike suspect, not the tick loop. Deliberately does
# NOT change any behavior - pure timing/counting around the existing code
# path, so this can land safely ahead of (and inform) whatever the actual
# fix turns out to be.
_perf_window_start = time.time()
_perf_counts = {"messages": 0, "fetch_signals_calls": 0}
_perf_seconds = {"handler_total": 0.0, "fetch_signals": 0.0}


def _record_trade_perf(handler_elapsed: float, fetch_signals_elapsed: float | None) -> None:
    """Accumulates one message's timing into the current 1-second window,
    then rolls the window into state["trade_stream_perf"] once it's been at
    least a second - a fixed reporting cadence regardless of message rate,
    so avg_handler_ms/messages_per_sec are directly comparable across
    windows instead of being skewed by how many messages happened to land
    in an arbitrarily-sized bucket."""
    global _perf_window_start
    _perf_counts["messages"] += 1
    _perf_seconds["handler_total"] += handler_elapsed
    if fetch_signals_elapsed is not None:
        _perf_counts["fetch_signals_calls"] += 1
        _perf_seconds["fetch_signals"] += fetch_signals_elapsed

    now = time.time()
    window_sec = now - _perf_window_start
    if window_sec < 1.0:
        return
    messages = _perf_counts["messages"]
    fetch_calls = _perf_counts["fetch_signals_calls"]
    state["trade_stream_perf"] = {
        "window_sec": round(window_sec, 2),
        "messages_per_sec": round(messages / window_sec, 1),
        "avg_handler_ms": round(_perf_seconds["handler_total"] / messages * 1000, 3) if messages else 0.0,
        "fetch_signals_calls_per_sec": round(fetch_calls / window_sec, 1),
        "avg_fetch_signals_ms": round(_perf_seconds["fetch_signals"] / fetch_calls * 1000, 3) if fetch_calls else 0.0,
        "updated_at": now,
    }
    _perf_window_start = now
    _perf_counts["messages"] = 0
    _perf_counts["fetch_signals_calls"] = 0
    _perf_seconds["handler_total"] = 0.0
    _perf_seconds["fetch_signals"] = 0.0


def _stream_market_client(cfg: dict) -> KalshiPublicGateway:
    """One reused KalshiPublicGateway for the websocket trade path.

    Everywhere else in this file constructs a KalshiPublicGateway per request
    handler, which is fine at request cadence. This path runs once per
    inbound trade message - on an exchange-wide subscription that is
    thousands per minute - so it gets a cached instance keyed by base URL
    (re-created if the config's base_url is ever edited live). The
    underlying HTTP connection pool and the shared rate limiter both live in
    services/http_client.py, so this shares them with every other caller
    exactly as a fresh instance would."""
    base_url = cfg["kalshi"]["base_url"]
    cached = _stream_client_cache.get(base_url)
    if cached is None:
        cached = KalshiPublicGateway(base_url, cfg["kalshi"]["request_timeout_sec"])
        _stream_client_cache.clear()
        _stream_client_cache[base_url] = cached
    return cached


async def _process_stream_trade(trade: dict) -> None:
    if not trade.get("trade_id"):
        return
    # See _record_trade_perf's docstring - this handler runs on an
    # exchange-wide subscription, so its own per-message cost is measured
    # (not guessed) via a real timer around every real invocation, cheap
    # trade_id discards above excluded since those never do any real work.
    _handler_started_at = time.monotonic()
    _fetch_signals_elapsed: float | None = None
    try:
        state["trade_tape"].insert(0, trade)
        state["trade_tape"] = state["trade_tape"][:_TRADE_TAPE_UI_CAP]
        state["trade_tape_last_fetch_ts"] = time.time()
        # Same reasoning as _process_stream_ticker's record_book: persist the
        # full print for watched series before the provider reduces it to a
        # side and a notional. state["trade_tape"] is a 200-entry in-memory ring
        # that dies with the process, so without this there is no record of what
        # the exchange actually printed - only of what survived the filters.
        series_watcher.record_trade(trade, config_store.get())
        if not state["running"] or not _streaming_trade_tape_enabled():
            bump_generation()
            return

        cfg_now = config_store.get()
        config_fp = config_performance.fingerprint(cfg_now)
        _fetch_signals_started_at = time.monotonic()
        signals = await whale_provider.fetch_signals(
            market_context={
                "markets": state["markets"], "trade_tape": [trade], "cfg": cfg_now,
                # Lets the provider resolve a market that isn't on the watchlist
                # when an off-list print clears the notional gate - required for
                # exchange-wide trades to produce signals at all, since scoring
                # needs the market's own volume/close_time and this app skips
                # rather than fabricates one. Gated behind the notional check
                # inside the provider, so it costs nothing on the ~99.9% of
                # prints that never qualify.
                "client": _stream_market_client(cfg_now),
            },
        )
        _fetch_signals_elapsed = time.monotonic() - _fetch_signals_started_at
        if not signals:
            bump_generation()
            return

        now = time.time()
        for signal in signals:
            await _handle_signal(signal, cfg_now, state.get("market_results") or {}, config_fp, now)
        for close_decision in strategy.check_exits(
            state["latest_prices"], state["signal_feed"], cfg_now, state.get("market_results") or {}, opened_since=now,
            category_by_ticker=_category_by_ticker(), close_times=_close_time_by_ticker(),
        ):
            await _handle_close_decision(close_decision)
        bump_generation()
    finally:
        _record_trade_perf(time.monotonic() - _handler_started_at, _fetch_signals_elapsed)


async def _process_stream_ticker(ticker_msg: dict) -> None:
    # Canonical key (A14): the stream gateway normalizes every message
    # before this callback (services/kalshi/contracts/ticker.py owns the
    # market_ticker alias) - handlers read vendor-neutral fields only.
    ticker = ticker_msg.get("ticker")
    if not ticker:
        return
    # opened_since=now (2026-08-16 self-review finding): this was the one
    # of check_exits' three call sites (main tick loop, _process_stream_trade,
    # here) missing the 2026-08-11 same-tick stale-price guard - see
    # services/strategy_engine.py's opened_since docstring for the original
    # incident. The ticker and trade WS channels are independent streams
    # with no ordering guarantee between them, so a ticker update reflecting
    # a moment before a whale's fill can still be processed right after
    # _process_stream_trade opens a position on that fresher fill price -
    # same stale-price-vs-fresh-entry shape as the original bug, just via
    # the ticker path instead of the tick-poll one.
    now = time.time()
    # Capture the WHOLE message before anything below narrows it (2026-08-17
    # direct instruction: "keep in mind all the api data you keep shaving off
    # that ends up making your tasks harder"). The two lines below keep
    # yes_bid_dollars/yes_ask_dollars and drop the other thirteen fields
    # docs/kalshi/market-ticker.md documents - yes_bid_size_fp/yes_ask_size_fp
    # (was there depth at the price I crossed), open_interest_fp/volume_fp
    # (how big was this print relative to the market), ts_ms (exchange-side
    # timing, not receive time). Every one of those is needed to explain why
    # a directionally-correct signal still lost money, and none of them were
    # recoverable after the fact. Self-throttling and never raises - see
    # series_watcher.record_book.
    series_watcher.record_book(ticker_msg, config_store.get(), now)
    try:
        state["latest_prices"][ticker] = float(ticker_msg.get("yes_bid_dollars") or ticker_msg.get("price_dollars") or 0.5)
    except (TypeError, ValueError):
        return
    matched_market = None
    for market in state["markets"]:
        if market.get("ticker") == ticker:
            market["yes_ask_dollars"] = ticker_msg.get("yes_ask_dollars")
            matched_market = market
            break
    if matched_market is not None:
        # Raises market_history's real time resolution using data already
        # in this message - see market_history.record_snapshot_from_ticker's
        # docstring and docs/next-session-pickup-2026-08-17.md's REST-vs-
        # websocket architecture finding (item #3, "smallest, lowest-risk").
        # volume_24h/close_time come from the cached REST market object
        # (matched_market), not the ticker message - the ws ticker channel
        # only carries all-time volume_fp (docs/kalshi/market-ticker.md),
        # and labeling that "volume_24h" would be exactly the kind of
        # mislabeled-value bug CLAUDE.md already documents twice.
        yes_bid_raw = ticker_msg.get("yes_bid_dollars")
        yes_ask_raw = ticker_msg.get("yes_ask_dollars")
        spread = None
        if yes_bid_raw is not None and yes_ask_raw is not None:
            try:
                spread = max(float(yes_ask_raw) - float(yes_bid_raw), 0.0)
            except (TypeError, ValueError):
                spread = None
        market_history.record_snapshot_from_ticker(
            ticker, state["latest_prices"][ticker], spread=spread,
            volume_24h=float(matched_market.get("volume_24h_fp") or 0.0),
            close_time=matched_market.get("close_time"), now=now,
        )
    if state["running"] and state.get("signal_feed"):
        cfg_now = config_store.get()
        for close_decision in strategy.check_exits(
            state["latest_prices"], state["signal_feed"], cfg_now, state.get("market_results") or {},
            opened_since=now, category_by_ticker=_category_by_ticker(), close_times=_close_time_by_ticker(),
        ):
            await _handle_close_decision(close_decision)
    bump_generation()


async def _process_stream_fill(fill_msg: dict) -> None:
    """2026-08-15 direct request: "the open positions should feed from the
    websocket stream and analysis trigger api calls for position
    management." Best-effort parsing, deliberately defensive throughout
    (dict.get() via the existing _slim_fill/_FILL_FIELDS, never assumes a
    field exists) - see services/kalshi_trade_ws.py's own comment on why
    this couldn't be verified against a real message for a long time (fill
    events need a real order fill; kalshi_account.trading_enabled is off,
    the standing P0 safety gate). If the real shape ever changes again,
    _slim_fill just returns Nones and the trade_id check below skips it -
    a safe no-op, not a crash or corrupted state, while the raw shape
    (logged once by kalshi_trade_ws.py) stays available to fix the field
    mapping once verified.

    Identity field is trade_id, not fill_id (2026-08-24 fix, QCP Task 13
    contract-fixture finding against docs/kalshi/user-fills.md - the real
    WS fill message has no fill_id field at all, only trade_id; fill_id is
    REST-Fill-schema-only naming for the same value). Before this fix,
    every real WS fill was silently discarded here - fill.get("fill_id")
    was always None, so this function no-opped on every single message,
    never observed because trading_enabled has always been off in
    practice. See services/account_positions.py's _FILL_FIELDS for the
    matching field-allowlist fix.

    Prepends to the existing state["account"]["fills"] list (same shape/
    cap the REST path already produces, so nothing downstream needs to
    know which source a given fill came from) - deduped by trade_id since
    _fetch_account_snapshot's own periodic REST poll (still running, now
    on a 20s cache - see that function's own comment) will naturally
    reconcile/overwrite this with verified data regardless, so a
    WS-sourced fill only ever needs to survive until the next reconcile."""
    from services.account_positions import _slim_fill

    if not state["account"].get("connected"):
        return
    fill = _slim_fill(fill_msg)
    if not fill.get("trade_id"):
        return  # doesn't look like a real fill message - never guess into real account state
    fills = (state["account"].get("fills") or {}).get("fills") or []
    if any(f.get("trade_id") == fill["trade_id"] for f in fills):
        return  # already have it - the REST reconciliation poll likely beat this message here
    state["account"]["fills"] = {"fills": ([fill] + fills)[:50]}
    bump_generation()


async def _process_stream_position(position_msg: dict) -> None:
    """Same best-effort/defensive shape as _process_stream_fill above -
    same "safe no-op if the real shape doesn't match, never corrupt real
    account state on a guess" reasoning.

    ticker fallback to market_ticker (2026-08-24 fix, QCP Task 13
    contract-fixture finding against docs/kalshi/market-positions.md - the
    real WS market_position message carries market_ticker, not ticker; the
    REST GetPositions MarketPosition schema is the one that uses ticker).
    Before this fix, position.get("ticker") was always None for a real WS
    position update, so this function no-opped on every single message.
    Normalizes the resolved value back onto position["ticker"] so every
    downstream consumer (frontend, _join_real_position_prices,
    _real_account_position_tickers) keeps reading the one key they already
    expect, regardless of which source populated it. See
    services/account_positions.py's _POSITION_FIELDS for the matching
    field-allowlist fix."""
    from services.account_positions import _slim_position

    if not state["account"].get("connected"):
        return
    position = _slim_position(position_msg)
    # Canonical key only (A14): the REST-vs-WS market_ticker alias is
    # resolved by services/kalshi/contracts/position.py at the gateway,
    # before this callback - re-interpreting it here is exactly the
    # presentation-layer alias knowledge the boundary exists to remove.
    ticker = position.get("ticker")
    if not ticker:
        return
    positions = state["account"].get("positions") or {"market_positions": [], "event_positions": []}
    market_positions = list(positions.get("market_positions") or [])
    for i, p in enumerate(market_positions):
        if p.get("ticker") == ticker:
            market_positions[i] = position
            break
    else:
        market_positions.append(position)
    state["account"]["positions"] = {**positions, "market_positions": market_positions}
    bump_generation()


async def _process_stream_lifecycle(msg: dict) -> None:
    """market_lifecycle_v2 (2026-08-17, docs/next-session-pickup-2026-08-17.md
    item #2 of the REST-vs-websocket architecture finding) - exchange-wide
    push notifications for market open/close/settlement
    (docs/kalshi/market-and-event-lifecycle.md), replacing part of what the
    6-second REST tick's own market-list fetch currently has to wait for.

    `close_date_updated` was the first slice wired (2026-08-17): the exact
    real, previously-diagnosed bug class (ROADMAP.md/CLAUDE.md's
    stale-close_time investigation) applied to the in-memory
    state["markets"] overlay only. `determined`/`settled` were deliberately
    left un-wired at the time - re-routing real outcome/P&L resolution onto
    a channel with zero live-verified message history would have been
    exactly the kind of partial-verification rush CLAUDE.md's own incident
    log warns against.

    Both gaps closed 2026-08-23, backed by real evidence this time, not
    just docs: `docs/kalshi/market-and-event-lifecycle.md`'s schema was
    cross-checked against real captured message shapes already sitting in
    `ddev logs` (`kalshi_trade_ws.py`'s own "first real shape" log line,
    2026-08-22 traffic - 2,256+ `determined` and 2,372+ `settled` events by
    the time this was written) - `determined` really does carry
    `result`/`determination_ts`/`settlement_value` exactly as documented;
    `settled` carries only `settled_ts`, no `result` field.
    1. `services/market_catalog/market_catalog.py`'s persisted `markets`
       table only ever got a fresh close_ts/status on that series' next
       incremental scan (potentially hours away) - `apply_lifecycle_update`
       now applies close_ts (close_date_updated) and status
       (determined -> "determined", settled -> "finalized", the same
       values a real REST market object's own `status` field would show
       per market_lifecycle.md's status table) the instant each event
       arrives.
    2. market_history/settlement_edge/market_analyst_agent/candidate_log's
       outcome resolution was fed exclusively from that tick's REST-fetched
       `markets` list - structurally blind to any ticker that rotates off
       the live watchlist/discovery scope before it settles (routine for
       short-lived series like KXBTC15M), regardless of poll frequency.
       market_lifecycle_v2 is exchange-wide, so a lifecycle event reaches
       every ticker this app ever touched, watchlisted or not.

    Corrected 2026-08-23, same day, later pass (services/exits/CHEATSHEET.md's
    audit finding, cross-referenced against market_lifecycle.md lines 21-24/
    36-38/68-72): the first version of this fix fired the four resolvers on
    `determined`, reasoning "settled carries no result field, so determined
    is the only trigger this needs" - true for data availability, but wrong
    for correctness. `determined` is not terminal: the docs are explicit
    that "the result may be disputed" during the settlement-timer window
    that follows, and can flip via `determined` -> `disputed` -> `amended`
    before `finalized` ("Settlement complete... Terminal state"). Resolving
    at `determined` meant a disputed-and-reversed market would already have
    graded a whale signal, an analyst call, and a rejected-candidate row
    against the wrong outcome, with no correction path - the exact gap
    services/exits/CHEATSHEET.md flagged for close_if_settled (fixed the
    same pass, see propagate_milestone_winners' docstring), just for these
    four resolvers instead of paper P&L. `determined` now only updates the
    persisted catalog status and stats, same as close_date_updated. `settled`
    now does the resolving: since its own WS payload has no `result` field
    (confirmed above) and Kalshi's lifecycle channel emits no distinct event
    for a dispute/amendment at all (not in market_lifecycle.md's own
    WebSocket event-type table), the only way to get a truly final,
    dispute-corrected result is a fresh single-ticker REST read at the
    moment `settled` arrives - `client.get_market(ticker)`, re-checked for
    `status == "finalized"` before trusting its `result`. One extra REST
    call per settlement, at the same ~0.06/s measured live rate as the
    `settled` event itself - negligible against Kalshi's rate budget, and
    still additive to (not a replacement for) the REST-tick fallback path,
    which now carries the identical finalized-only gate (see main.py and
    propagate_milestone_winners). All four resolvers stay idempotent
    (INSERT OR IGNORE / `WHERE resolved = 0` / `WHERE settled_yes IS NULL`),
    so firing from both paths remains safe.

    Every event_type still counts toward lifecycle_stream_stats
    (services/app_state.py) so real volume/shape is visible on
    /api/state without grepping logs."""
    from datetime import datetime, timezone

    event_type = msg.get("event_type")
    ticker = msg.get("ticker")  # canonical alias, normalized at the gateway (A14)
    if not event_type or not ticker:
        return
    stats = state["lifecycle_stream_stats"]
    stats["events_by_type"][event_type] = stats["events_by_type"].get(event_type, 0) + 1
    stats["last_event_at"] = time.time()

    if event_type == "close_date_updated":
        close_ts = msg.get("close_ts")
        if close_ts is None:
            return
        try:
            new_close_time = datetime.fromtimestamp(int(close_ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")
        except (TypeError, ValueError, OSError, OverflowError):
            return
        updated = False
        for market in state["markets"]:
            if market.get("ticker") == ticker:
                market["close_time"] = new_close_time
                updated = True
                break
        if updated:
            stats["close_time_updates_applied"] += 1
            bump_generation()
        if market_catalog.apply_lifecycle_update(ticker, close_ts=float(close_ts)):
            stats["catalog_updates_applied"] += 1
        return

    if event_type == "determined":
        # Catalog status only - NOT terminal, so no outcome resolution here
        # (2026-08-23 correction, see this function's own docstring). result
        # is set at this transition but can still be disputed/amended before
        # settled/finalized.
        if market_catalog.apply_lifecycle_update(ticker, status="determined"):
            stats["catalog_updates_applied"] += 1
        return

    if event_type == "settled":
        if market_catalog.apply_lifecycle_update(ticker, status="finalized"):
            stats["catalog_updates_applied"] += 1
        # settled carries no result field, and there is no distinct
        # dispute/amendment event to watch for either - a fresh REST read is
        # the only way to get a result that's actually final. Best-effort:
        # a failed/degraded fetch just leaves this ticker for the REST-tick
        # fallback path to catch if it ever resurfaces on the watchlist.
        try:
            client = _stream_market_client(config_store.get())
            market = await client.get_market(ticker)
        except Exception:
            return
        if (market.get("status") or "") != "finalized":
            return
        result = (market.get("result") or "").strip().lower()
        if result not in ("yes", "no"):
            return
        now = time.time()
        market_history.record_outcome(ticker, result, resolved_at=now)
        resolved = settlement_edge.resolve_window(ticker, result == "yes")
        resolved += market_analyst_agent.resolve_from_market_results({ticker: result})
        resolved += candidate_log.resolve_from_market_results({ticker: result})
        stats["outcomes_resolved_via_lifecycle"] += resolved
        return


async def _handle_trade_stream_status(status: dict) -> None:
    state["trade_stream_status"] = {
        "enabled": _streaming_trade_tape_enabled(),
        "connected": bool(status.get("connected")),
        "error": status.get("error"),
        "ws_url": status.get("ws_url") or trade_stream.status.get("ws_url"),
        "mode": "stream" if _streaming_trade_tape_enabled() else "poll",
    }
    if status.get("error"):
        state["error"] = status["error"]
    asyncio.create_task(ws_manager.broadcast({
        "type": "trade_stream_status",
        "status": state["trade_stream_status"],
    }))
    bump_generation()


async def _fetch_trades_for_ticker(client: KalshiPublicGateway, ticker: str, min_ts: int | None) -> list[dict]:
    """Pages through every real trade on this one ticker since min_ts,
    newest-first (Kalshi's real ordering, confirmed directly) - stops only
    when the API's own cursor comes back empty (its documented "no more
    pages" signal), not after some fixed count. A single ticker producing
    more than one page's worth of trades within one poll interval is a
    genuine edge case, but this must hold even then per direct instruction
    ("never capped").

    min_ts=None (no watermark yet - the very first tick after a cold
    start/restart) is deliberately NOT paginated - a real bug caught live
    while shipping this fix: with no min_ts floor, Kalshi has no natural
    stopping point short of a ticker's entire trade history, so every
    watched ticker would page up to _TRADE_TAPE_MAX_PAGES_PER_TICKER pages
    each on that first tick, all concurrently - confirmed live as the
    direct cause of a real request timeout right after a restart. Once
    min_ts is set (every tick after the first), the query is naturally
    bounded to "since last successful fetch," which is what actually makes
    unbounded pagination safe."""
    if min_ts is None:
        resp = await client.get_trades(ticker=ticker, limit=_TRADE_TAPE_FETCH_LIMIT, min_ts=None)
        return resp.get("trades") or []
    trades = []
    cursor = None
    for _ in range(_TRADE_TAPE_MAX_PAGES_PER_TICKER):
        resp = await client.get_trades(ticker=ticker, limit=_TRADE_TAPE_FETCH_LIMIT, min_ts=min_ts, cursor=cursor)
        page = resp.get("trades") or []
        trades.extend(page)
        cursor = resp.get("cursor") or None
        if not cursor or not page:
            break
    return trades


async def _fetch_trade_tape(
    client: KalshiPublicGateway, markets: list[dict], since_ts: float | None = None,
) -> list[dict]:
    """Full-exchange trade tape (ROADMAP.md Phase 0.5), scoped to the current
    watchlist rather than the whole exchange - get_trades with no ticker
    filter returns trades across every Kalshi market, most of which aren't
    on anyone's watchlist here and would just be noise next to the
    whale-signal concept this ties into. One fully-paginated fetch per
    watched market, concurrently (same pattern _fetch_markets already uses
    for its explicit-watchlist branch), merged and sorted newest-first.
    Genuinely unbounded - no cap anywhere in this function - per direct
    instruction (2026-08-11): "i want trade tape to be unlimited, never
    capped, for whale-watch-worthy markets." Any display-size limiting
    (e.g. the Trade Tape UI panel) happens at the call site, not here.

    since_ts (real, SDK-confirmed min_ts param): when given, fetches every
    real trade on each ticker since that watermark instead of just "the
    most recent page" - a small overlap margin is subtracted so a trade
    landing right at the boundary can't fall through a gap between two
    polls; the whale-watcher provider already dedupes by trade_id
    (services/whalewatchers/kalshi_trade_tape.py's _seen_trade_ids), so a
    little re-fetched overlap is harmless. None on the very first call
    (no watermark yet) falls back to "just show recent activity," same as
    before this fix."""
    tickers = [m["ticker"] for m in markets if m.get("ticker")]
    if not tickers:
        return []
    min_ts = int(since_ts) - 10 if since_ts is not None else None
    results = await asyncio.gather(
        *(_fetch_trades_for_ticker(client, t, min_ts) for t in tickers), return_exceptions=True
    )
    trades = []
    for result in results:
        if isinstance(result, list):
            trades.extend(result)
    trades.sort(key=lambda t: t.get("created_time") or "", reverse=True)
    return trades
