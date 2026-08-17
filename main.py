import asyncio
import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()  # reads .env if present; every var is optional, see .env.example

from services import accounts_store
from services import advisory_engine
from services import auth as auth_service
from services import backtest
from services import calibration_history
from services import candidate_log
from services import cross_strategy
from services import diagnostics
from services import regime_analytics
from services import stats_power
from services import confidence_calibration
from services import event_lifecycle
from services import event_schedule
from services import market_strategy_calibration
from services import config_performance
from services import market_analyst_agent
from services import market_catalog
from services import market_history
from services import ml_feed
from services import mutual_exclusivity
from services import position_netting
from services import reset_log
from services import series_cache
from services import series_evaluator
from services import series_watcher
from services import game_state
from services import index_feed
from services import settlement_edge
from services import trade_archive
from services import signal_log
from services import suggestion_decisions
from services import title_cache
from services import trade_analytics
from services import trade_category
from services.config_store import config_store
from services.http_client import close_client, get_and_reset_rate_limit_hits
from services.kalshi_client import KalshiClient
from services.kalshi_account_client import KalshiAccountClient
from services.kalshi_trade_ws import KalshiTradeWebSocketClient
from services.whale_simulator import WhaleSimulator
from services.whalewatchers import PROVIDERS, get_active_provider
from services.market_strategy import MarketNativeStrategy
from services.paper_broker import PaperBroker
from services.risk_manager import RiskManager
from services.shadow_mode import ShadowTrader
from services.strategy_engine import FollowTheWhaleStrategy

# ---- shared runtime state -------------------------------------------------
#
# Moved to services/app_state.py 2026-08-17 so route groups and stream
# handlers can be lifted out of this file without a circular import - see
# that module's docstring. Imported by name here so every existing
# reference in this file (`state[...]`, `broker.`, `risk.`, ...) keeps
# working unchanged.
from routers import diagnostics_routes  # noqa: E402
from services.app_state import (  # noqa: E402
    account, account_base_url, broker, cfg, index_stream, market_broker, market_risk,
    market_strategy, risk, shadow, state, strategy, trade_stream, whale_provider, whale_sim,
)


class WebSocketManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self.lock:
            self.active_connections.append(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self.lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)

    async def broadcast(self, message: dict) -> None:
        text = json.dumps(message)
        async with self.lock:
            connections = list(self.active_connections)
        for connection in connections:
            try:
                await connection.send_text(text)
            except Exception:
                await self.disconnect(connection)


ws_manager = WebSocketManager()


def _streaming_trade_tape_enabled() -> bool:
    # Kalshi's websocket market-data stream is authenticated, so this can only
    # replace the polled trade tape when the real trade-tape provider is active
    # AND websocket credentials loaded successfully. Fallback stays on the
    # existing REST polling path otherwise.
    return whale_provider.name == "kalshi_trade_tape" and trade_stream.enabled

def _bump_generation():
    state["generation"] += 1


def _close_time_by_ticker() -> dict:
    # ticker -> close_time, for check_exits' runway-exhausted gate
    # (strategy.exit_min_seconds_to_close, ROADMAP #1). Same
    # already-in-memory, zero-new-API-calls construction as
    # _category_by_ticker below, but sourced from state["markets"] rather
    # than market_titles: close_time is mutable upstream
    # (docs/kalshi/market_lifecycle.md's close_date_updated event), so this
    # deliberately reads the freshest per-tick markets list every call
    # instead of anything cached at entry time.
    return {
        m["ticker"]: m.get("close_time")
        for m in (state.get("markets") or [])
        if m.get("ticker") and m.get("close_time")
    }


def _category_by_ticker() -> dict:
    # Per-series/category config overrides (services/config_overrides.py,
    # 2026-08-15 direct request) - built from the already-in-memory
    # state["market_titles"]/state["event_titles"] (pure dict comprehension,
    # zero new API calls), covering every KNOWN ticker rather than just
    # this tick's markets list, so an open position that's rotated off the
    # watchlist still resolves a category for check_exits.
    return {
        ticker: (state["event_titles"].get(info.get("event_ticker")) or {}).get("category")
        for ticker, info in state["market_titles"].items()
        if info.get("event_ticker")
    }


def _sport_for_event(event_info: dict) -> str | None:
    # SPORT ("Baseball"), not the finer per-competition string
    # ("Pro Baseball") - reverse-mapped through category_metadata's
    # sport_by_competition (see _fetch_category_metadata's own comment:
    # get-filters-for-sports.md documents competitions as nested WITHIN a
    # sport, e.g. filters_by_sports["Baseball"]["competitions"] contains
    # "Pro Baseball"/"Japan NPB"/"Korea KBO"/"Mexico LMB" - several
    # competitions, one sport). Falls back to the raw competition string
    # only if the reverse map hasn't been built yet (category_metadata's
    # first fetch hasn't completed) or doesn't recognize it - a real
    # subcategory late is better than none, even if slightly coarser than
    # intended for one refresh cycle.
    competition = event_info.get("competition")
    if not competition:
        return None
    sport_by_competition = state["category_metadata"].get("sport_by_competition") or {}
    return sport_by_competition.get(competition, competition)


def _subcategory_by_ticker() -> dict:
    # Mirrors _category_by_ticker exactly, one field over - 2026-08-16
    # direct request for a series -> subcategory -> category fallback
    # chain in whale-confidence win-rate segmentation (see
    # services/trade_category.py's own subcategory docstring for why this
    # isn't category_tags - that field is the same full facet-filter
    # vocabulary on every event in a category, not per-event data).
    return {
        ticker: _sport_for_event(state["event_titles"].get(info.get("event_ticker")) or {})
        for ticker, info in state["market_titles"].items()
        if info.get("event_ticker")
    }


async def _broadcast_signal_decision(signal_payload: dict | None, decision_payload: dict) -> None:
    await ws_manager.broadcast({
        "type": "signal_decision",
        "signal": signal_payload,
        "decision": decision_payload,
    })


async def _handle_signal(signal, cfg: dict, market_results: dict, config_fp: str, tick_now: float) -> dict:
    state["signal_feed"].insert(0, signal.to_dict())
    state["signal_feed"] = state["signal_feed"][:50]
    state["stats"]["signals_seen"] += 1
    # excluded= (2026-08-17): while an experiment window is open
    # (services/data_quarantine.start), signals are still logged in full and
    # still trade - only their status as *evidence* changes, so a deliberate
    # test never silently corrupts the 30-day stats the way the 28-minute
    # $1-threshold latency test did on 08-16. Read from state, not a fresh
    # DB hit per signal: this is the hot path (20k signals in 28 minutes at
    # peak), and state["experiment_active"] is refreshed once per tick.
    signal_log.log_signal(
        signal.ticker, signal.side, signal.size, signal.confidence,
        state["whale_source"], signal.timestamp, factors=signal.factors,
        raw_context=signal.raw_context, price=signal.price,
        excluded=bool(state.get("experiment_active")),
    )

    market_info = state["market_titles"].get(signal.ticker) or {}
    event_ticker = market_info.get("event_ticker")
    is_live = state["live_status"].get(event_ticker) == "live" if event_ticker else False
    # Structural/schedule-based mid-series signal (services/event_lifecycle.py,
    # 2026-08-15) - a second, independent path to the same "should scheduled
    # close-time protections be bypassed" question that the milestone-based
    # is_live above already answers for team sports. Real live incident:
    # multi-day tournament/field "outright winner" markets (e.g. a golf
    # major) often have NO Kalshi milestone tracking at all
    # (_fetch_live_status's own docstring already confirms "most real
    # candidates get no milestone at all"), so is_live alone stayed False
    # for the tournament's own day 3 of 4 - not because the event wasn't
    # actually happening, but because nothing here had a way to know that
    # from schedule data. Only consulted when the milestone-based signal
    # didn't already say live, and never overrides a real "not live" from
    # Kalshi's own data - purely additive.
    if not is_live and event_ticker:
        is_live = state["event_phase"].get(event_ticker) == event_lifecycle.MID_SERIES
    event_info = state["event_titles"].get(event_ticker) or {}
    category = event_info.get("category")
    subcategory = _sport_for_event(event_info)
    me_complement = (state.get("me_pairs") or {}).get(signal.ticker)

    decision = strategy.evaluate(
        signal, cfg, is_live=is_live, market_results=market_results, config_fingerprint=config_fp,
        latest_prices=state["latest_prices"], category=category, me_complement=me_complement,
    )
    state["decision_feed"].insert(0, decision)
    state["decision_feed"] = state["decision_feed"][:50]
    # limit_order_placed (2026-08-15, strategy.use_limit_orders) is neither
    # a completed trade nor a skip - it's still pending, resolved later by
    # PaperBroker.check_pending_fills (see _handle_fill_decision, which
    # records the eventual fill's own trades_placed/category the same way
    # a market-order trade already does here).
    if decision["action"] == "trade":
        state["stats"]["trades_placed"] += 1
    elif decision["action"] == "limit_order_placed":
        state["stats"]["limit_orders_placed"] += 1
    else:
        state["stats"]["skipped"] += 1
    asyncio.create_task(_broadcast_signal_decision(signal.to_dict(), decision))
    if decision["action"] == "trade":
        trade_category.record_category(signal.ticker, category, tick_now, subcategory=subcategory)

    if cfg.get("mode") in ("shadow", "live"):
        shadow_bankroll, shadow_bankroll_source = _shadow_reference_bankroll(state.get("account") or {}, cfg)
        shadow.evaluate(
            signal, cfg, shadow_bankroll, shadow_bankroll_source,
            is_live=is_live, market_results=market_results, config_fingerprint=config_fp,
        )
    return decision


async def _handle_close_decision(close_decision: dict) -> None:
    state["decision_feed"].insert(0, close_decision)
    state["decision_feed"] = state["decision_feed"][:50]
    state["stats"]["trades_placed"] += 1
    asyncio.create_task(_broadcast_signal_decision(None, close_decision))


async def _handle_fill_decision(fill_decision: dict, tick_now: float) -> None:
    # Maker/limit-order path (2026-08-15) - a resting order that just
    # filled is an ENTRY event (mirrors _handle_signal's own tail: decision
    # feed, trades_placed stat, category capture), not a close, even though
    # it's discovered via PaperBroker.check_pending_fills rather than
    # strategy.evaluate(). category_by_ticker() is already cheap/cached
    # per-tick (see its own docstring) - fine to call again here.
    state["decision_feed"].insert(0, fill_decision)
    state["decision_feed"] = state["decision_feed"][:50]
    state["stats"]["trades_placed"] += 1
    asyncio.create_task(_broadcast_signal_decision(None, fill_decision))
    ticker = fill_decision["trade"]["ticker"]
    category = _category_by_ticker().get(ticker)
    subcategory = _subcategory_by_ticker().get(ticker)
    trade_category.record_category(ticker, category, tick_now, subcategory=subcategory)


_stream_client_cache: dict[str, KalshiClient] = {}


def _stream_market_client(cfg: dict) -> KalshiClient:
    """One reused KalshiClient for the websocket trade path.

    Everywhere else in this file constructs a KalshiClient per request
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
        cached = KalshiClient(base_url, cfg["kalshi"]["request_timeout_sec"])
        _stream_client_cache.clear()
        _stream_client_cache[base_url] = cached
    return cached


async def _process_stream_trade(trade: dict) -> None:
    if not trade.get("trade_id"):
        return
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
        _bump_generation()
        return

    cfg_now = config_store.get()
    config_fp = config_performance.fingerprint(cfg_now)
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
    if not signals:
        _bump_generation()
        return

    now = time.time()
    for signal in signals:
        await _handle_signal(signal, cfg_now, state.get("market_results") or {}, config_fp, now)
    for close_decision in strategy.check_exits(
        state["latest_prices"], state["signal_feed"], cfg_now, state.get("market_results") or {}, opened_since=now,
        category_by_ticker=_category_by_ticker(), close_times=_close_time_by_ticker(),
    ):
        await _handle_close_decision(close_decision)
    _bump_generation()


async def _process_stream_ticker(ticker_msg: dict) -> None:
    ticker = ticker_msg.get("market_ticker")
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
    for market in state["markets"]:
        if market.get("ticker") == ticker:
            market["yes_ask_dollars"] = ticker_msg.get("yes_ask_dollars")
            break
    if state["running"] and state.get("signal_feed"):
        cfg_now = config_store.get()
        for close_decision in strategy.check_exits(
            state["latest_prices"], state["signal_feed"], cfg_now, state.get("market_results") or {},
            opened_since=now, category_by_ticker=_category_by_ticker(), close_times=_close_time_by_ticker(),
        ):
            await _handle_close_decision(close_decision)
    _bump_generation()


async def _noop_stream_trade(_trade: dict) -> None:
    """index_stream subscribes to no trade/ticker channels at all (its
    index_ids are the only thing it asks for), so these can never fire -
    they exist because run()'s signature takes them positionally."""
    return


async def _noop_stream_ticker(_ticker_msg: dict) -> None:
    return


async def _handle_index_stream_status(status: dict) -> None:
    state["index_stream_status"] = {**status, "updated_at": time.time()}


async def _process_stream_index(msg_type: str, msg: dict) -> None:
    """CF Benchmarks / Pyth index ticks (services/index_feed.py).

    For the crypto series this is the settlement quantity itself, not a
    proxy for it - KXBTC15M settles on "the simple average of the sixty
    seconds of CF Benchmarks' BRTI before <close>", and
    cfbenchmarks_value's last_60s_windowed_average_15min IS that average,
    accumulating one observation per second. ~1 message/sec/index, buffered
    the same way trade capture is, so this never writes on the event loop.
    Deliberately does NOT trigger check_exits or any trading action yet -
    capture and projection first, acting on it is a separate, deliberate
    step."""
    if msg_type == "cfbenchmarks_value":
        index_feed.record_cfbenchmarks(msg)
        await _record_settlement_observations(msg.get("index_id"))
    elif msg_type == "pyth_value":
        index_feed.record_pyth(msg)


# ticker -> settlement spec (services/index_feed.settlement_spec). A market's
# rules/strike/close are immutable once listed, so this is cached rather than
# re-fetched: the 15-minute series rotates its ticker every quarter hour, so
# this is a handful of fetches an hour, not one per index tick.
_settlement_spec_cache: dict[str, dict] = {}


async def _spec_for(ticker: str) -> dict:
    spec = _settlement_spec_cache.get(ticker)
    if spec is None:
        cfg = config_store.get()
        client = KalshiClient(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])
        try:
            spec = index_feed.settlement_spec(await client.get_market(ticker))
        except Exception:
            # Don't cache a transport failure as "unsupported" - that would
            # permanently blind this market on one bad request.
            return {"supported": False, "reason": "market fetch failed"}
        _settlement_spec_cache[ticker] = spec
        if len(_settlement_spec_cache) > 500:
            _settlement_spec_cache.clear()
    return spec


_last_capture_prune_at = 0.0


def _maybe_prune_capture_stores(cfg: dict, now: float) -> None:
    """Hourly retention sweep across the sampled capture stores. Bounded
    and idempotent; each store's own prune() is non-raising and logs to
    fault_log on failure."""
    global _last_capture_prune_at
    if now - _last_capture_prune_at < 3600:
        return
    _last_capture_prune_at = now
    hours = float((cfg.get("series_watcher") or {}).get("retention_hours", 168))
    series_watcher.prune(retention_hours=hours, now=now)
    index_feed.prune(retention_hours=hours, now=now)
    game_state.prune(retention_hours=hours, now=now)


async def _resolve_settlement_windows(client: KalshiClient) -> None:
    """Fill in outcomes for observed settlement windows, driven by
    settlement_edge's own pending list rather than the discovery watchlist.

    Confirmed necessary, not assumed: a KXBTC15M market was found
    `finalized` with `result='yes'` six minutes after close while its 59
    observations sat unresolved, because the 15-minute series rotates its
    ticker every quarter hour and the market had already left the watchlist
    by the time `result` populated. The loop's existing outcome pass is
    kept as well - it costs nothing and catches the markets that are still
    watched - but it cannot be the only path.

    One batched call (get_markets_by_tickers, 50/request) against a list
    that is normally empty and at most a handful long."""
    tickers = settlement_edge.unresolved_tickers()
    if not tickers:
        return
    try:
        markets = await client.get_markets_by_tickers(tickers)
    except Exception:
        return  # transient - the same rows are still pending next tick
    for ticker, market in markets.items():
        result = (market.get("result") or "").strip().lower()
        if result in ("yes", "no"):
            settlement_edge.resolve_window(ticker, result == "yes")


async def _record_settlement_observations(index_id: str | None) -> None:
    """While a settlement window is open, record the projection and the
    market's own price side by side for every watched market settling on
    this index (services/settlement_edge.py).

    This is the measurement that decides whether the index feed is an edge
    or merely interesting: both forecasts of the same binary event, captured
    at the same instant, scored against the realised outcome later. Nothing
    here trades - see settlement_edge's own docstring."""
    if not index_id:
        return
    entry = index_feed.latest(index_id)
    if not entry or not entry.get("q15_window_size"):
        return  # not in a settlement window; nothing to compare
    for market in (state.get("markets") or []):
        ticker = market.get("ticker")
        if not ticker:
            continue
        spec = await _spec_for(ticker)
        if not spec.get("supported") or spec.get("index_id") != index_id:
            continue
        # Same index is NOT enough: the q15 window opens before every
        # quarter-hour, so an average accumulating toward 06:00 would
        # otherwise be recorded against a market settling at 17:00. See
        # index_feed.window_matches_close - this was a real bug, found by
        # inspecting the captured rows rather than trusting the wiring.
        if not index_feed.window_matches_close(entry, settlement_edge.close_ts(spec)):
            continue
        settlement_edge.record_observation(
            ticker, spec,
            index_feed.settlement_projection(index_id, spec["strike"]),
            state["latest_prices"].get(ticker),
        )


async def _process_stream_fill(fill_msg: dict) -> None:
    """2026-08-15 direct request: "the open positions should feed from the
    websocket stream and analysis trigger api calls for position
    management." Best-effort parsing, deliberately defensive throughout
    (dict.get() via the existing _slim_fill/_FILL_FIELDS, never assumes a
    field exists) - see services/kalshi_trade_ws.py's own comment on why
    this can't be verified against a real message yet (fill events need a
    real order fill; kalshi_account.trading_enabled is off, the standing
    P0 safety gate). If the real shape turns out to use different field
    names, _slim_fill just returns Nones and the fill_id check below skips
    it - a safe no-op, not a crash or corrupted state, while the raw shape
    (logged once by kalshi_trade_ws.py) stays available to fix the field
    mapping once verified.

    Prepends to the existing state["account"]["fills"] list (same shape/
    cap the REST path already produces, so nothing downstream needs to
    know which source a given fill came from) - deduped by fill_id since
    _fetch_account_snapshot's own periodic REST poll (still running, now
    on a 20s cache - see that function's own comment) will naturally
    reconcile/overwrite this with verified data regardless, so a
    WS-sourced fill only ever needs to survive until the next reconcile."""
    if not state["account"].get("connected"):
        return
    fill = _slim_fill(fill_msg)
    if not fill.get("fill_id"):
        return  # doesn't look like a real fill message - never guess into real account state
    fills = (state["account"].get("fills") or {}).get("fills") or []
    if any(f.get("fill_id") == fill["fill_id"] for f in fills):
        return  # already have it - the REST reconciliation poll likely beat this message here
    state["account"]["fills"] = {"fills": ([fill] + fills)[:50]}
    _bump_generation()


async def _process_stream_position(position_msg: dict) -> None:
    """Same best-effort/defensive shape as _process_stream_fill above -
    same "safe no-op if the real shape doesn't match, never corrupt real
    account state on a guess" reasoning."""
    if not state["account"].get("connected"):
        return
    position = _slim_position(position_msg)
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
    _bump_generation()


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
    _bump_generation()


_MILESTONE_REPOLL_SEC = 60  # Repoll-cached (2026-08-15 tick_duration fix) -
# this used to call get_milestones_for_event() for every unique event on the
# watchlist, every tick, forever, unconditionally - confirmed live as one of
# two per-event REST loops (see _fetch_event_live_data just below) with zero
# caching, together accounting for the bulk of a ~27s tick_duration plateau
# that survived the earlier same-day rate-limit incident's own "no stone
# unturned" audit (that audit fixed discovery/catalog-scan/signal-resolution/
# account-snapshot, but these two live in a later, separate part of the tick
# it didn't touch). Most events (crypto, politics, ...) never have a
# milestone at all, so this was 13+ wasted calls a tick for nothing. Once a
# winner is found for an event, it's cached permanently - a real-world
# outcome doesn't change, so there's never a reason to poll it again.


async def propagate_milestone_winners(client: KalshiClient, markets: list[dict]) -> dict:
    """Best-effort: fetch first milestone per event, inspect its live-data
    for a declared `details.winner`, map that winner to a related market
    ticker when possible and set market_results for the related tickers
    (yes/no). Also records outcomes via market_history.record_outcome()
    so check_exits can close positions this tick. Returns the market_results
    mapping (ticker -> result) built from the provided markets plus any
    propagated winners. This is kept separate so it can be unit-tested.

    Repoll-cached in state["milestone_cache"] - see _MILESTONE_REPOLL_SEC
    above. A cache hit (event not due for repoll, or already resolved) costs
    zero API calls but still reapplies any already-known winner into this
    tick's market_results below, so callers see identical per-tick
    completeness to the pre-caching behavior - only the network cost was cut.
    """
    market_results = {m["ticker"]: m.get("result") for m in markets if m.get("ticker")}
    try:
        event_tickers = list(dict.fromkeys(m.get("event_ticker") for m in markets if m.get("event_ticker")))
        cache = state["milestone_cache"]
        now = time.time()
        to_poll = [
            et for et in event_tickers
            if et not in cache or (
                not cache[et]["winner_found"] and (now - cache[et]["checked_at"]) >= _MILESTONE_REPOLL_SEC
            )
        ]
        if to_poll:
            milestone_tasks = await asyncio.gather(
                *(client.get_milestones_for_event(et) for et in to_poll), return_exceptions=True
            )
            # First pass: default every polled event to "no winner found
            # this tick" (recorded before any further fetch so a transient
            # failure below still throttles the retry to the next repoll
            # window), then collect the events that actually have a real
            # milestone id/type to check live-data for.
            milestone_by_event = {}
            for et, ms_result in zip(to_poll, milestone_tasks):
                cache[et] = {"checked_at": now, "winner_found": False, "related": None, "mapped_winner_ticker": None}
                if isinstance(ms_result, list) and ms_result:
                    ms = ms_result[0]
                    if ms.get("id") and ms.get("type"):
                        milestone_by_event[et] = ms

            # Batched (2026-08-16 API-doc audit finding B3.2, docs/kalshi/
            # get-live-data.md) - was N individual get_live_data() calls,
            # one per event with a milestone, each inside this same loop.
            # One get_live_datas call now covers every event polled this
            # tick regardless of how many need it.
            live_datas = {}
            if milestone_by_event:
                live_datas = await client.get_live_datas([ms["id"] for ms in milestone_by_event.values()])

            # Pass 1: figure out which events actually have a declared
            # winner + related tickers to map (pure, no I/O), collecting
            # every related ticker across every such event into one flat,
            # deduped list.
            events_with_winner = []  # (et, ms, winner, related)
            all_related: list[str] = []
            seen_related = set()
            for et, ms in milestone_by_event.items():
                ld = live_datas.get(ms["id"])
                if not ld:
                    continue
                details = ld.get("details") or {}
                winner = details.get("winner")
                related = ms.get("related_event_tickers") or details.get("related_event_tickers") or []
                if not winner or not related:
                    continue
                events_with_winner.append((et, ms, winner, related))
                for t in related:
                    if t not in seen_related:
                        seen_related.add(t)
                        all_related.append(t)

            # Batched (2026-08-16, direct efficiency note: "a lot of
            # efficiency could be gained by using batch calls to the API vs
            # individual calls for specific markets") - was one gather of
            # individual get_market() calls PER event with a winner, inside
            # this same loop. One get_markets_by_tickers call now covers
            # every related ticker across every such event this tick,
            # regardless of how many events have a winner to map.
            related_market_by_ticker = await client.get_markets_by_tickers(all_related) if all_related else {}

            for et, ms, winner, related in events_with_winner:
                related_markets = [related_market_by_ticker[t] for t in related if t in related_market_by_ticker]
                mapped_winner_ticker = None
                for rm in related_markets:
                    if not isinstance(rm, dict):
                        continue
                    cs = rm.get("custom_strike") or {}
                    try:
                        if isinstance(cs, dict) and any(str(winner).lower() in str(v).lower() for v in cs.values()):
                            mapped_winner_ticker = rm.get("ticker")
                            break
                    except Exception:
                        pass
                    yst = (rm.get("yes_sub_title") or "")
                    nst = (rm.get("no_sub_title") or "")
                    if isinstance(winner, str) and winner:
                        wlow = winner.lower()
                        if yst and wlow in yst.lower():
                            mapped_winner_ticker = rm.get("ticker")
                            break
                        if nst and wlow in nst.lower():
                            mapped_winner_ticker = rm.get("ticker")
                            break
                        title = (rm.get("title") or "")
                        if title and wlow in title.lower():
                            mapped_winner_ticker = rm.get("ticker")
                            break
                if mapped_winner_ticker:
                    cache[et] = {
                        "checked_at": now, "winner_found": True,
                        "related": related, "mapped_winner_ticker": mapped_winner_ticker,
                    }
                    now_ts = time.time()
                    for rt in related:
                        res = "yes" if rt == mapped_winner_ticker else "no"
                        market_results[rt] = res
                        market_history.record_outcome(rt, res, resolved_at=now_ts)
                    _bump_generation()
        # Reapply already-known winners from cache (no new API calls) so a
        # tick that skipped re-polling a resolved event still sees a
        # complete market_results, matching pre-caching per-tick behavior.
        for et in event_tickers:
            entry = cache.get(et)
            if entry and entry["winner_found"] and et not in to_poll:
                for rt in entry["related"]:
                    market_results[rt] = "yes" if rt == entry["mapped_winner_ticker"] else "no"
    except Exception:
        pass
    return market_results


# Kalshi's full market object carries 40+ fields (rules text, combo-leg
# lists, ...); trimming to what's actually used cuts the /api/state payload
# for 8 markets from ~34KB to well under 1KB. event_ticker/close_time/
# strike_type added for Phase 0.5's event/outcome grouping - previously
# dropped here entirely, so the dashboard had no way to know two markets
# were siblings under one event even though Kalshi sends that relationship
# on every market object already. yes_ask_dollars added for the screener
# table's Spread column (ROADMAP.md) - already present on every market
# object _fetch_markets gets back, so exposing it costs nothing extra.
_MARKET_FIELDS = (
    "ticker", "volume_24h_fp", "event_ticker", "close_time", "strike_type",
    "occurrence_datetime", "status", "yes_ask_dollars", "can_close_early",
)


def _slim_market(m: dict) -> dict:
    return {k: m.get(k) for k in _MARKET_FIELDS}


def _enrich_recent_trades(paper_broker_instance: PaperBroker) -> list[dict]:
    """PaperBroker.state()'s recent_trades are raw Trade rows - a close
    trade among them carries no realized_pnl/close_type/won at all, the
    same "displayed value doesn't match its label" bug class CLAUDE.md
    already documents once (the Portfolio header's old Unrealized P&L).
    Real bug found live (2026-08-10, direct user report: "not seeing the
    results of the positions in the trade log") - every row in the
    Portfolio Trade Log rendered identically whether it was a still-open
    entry or an already-settled close, showing "cost to enter"/"payout if
    right" even for a trade that had already won or lost.

    Re-derives via trade_analytics.build_trade_history() over the FULL
    trade_log (not just the tail-25 slice state() itself returns) so an
    entry outside the recent window still pairs correctly with a close
    inside it, then merges the derived fields back onto just the recent-25
    raw rows by (ticker, timestamp) - a close trade's own timestamp is
    exactly build_trade_history()'s exit_timestamp for that row, a stable,
    unambiguous join key requiring no new IDs."""
    full_history = trade_analytics.build_trade_history([t.to_dict() for t in paper_broker_instance.trade_log])
    by_key = {(r["ticker"], r["exit_timestamp"]): r for r in full_history}
    recent = [t.to_dict() for t in paper_broker_instance.trade_log[-25:][::-1]]
    for t in recent:
        derived = by_key.get((t["ticker"], t["timestamp"]))
        if derived is not None:
            t["close_type"] = derived["close_type"]
            t["realized_pnl"] = derived["realized_pnl"]
            t["won"] = derived["won"]
            t["entry_price"] = derived["entry_price"]
            t["hold_sec"] = derived["hold_sec"]
    return recent


def _enrich_positions_with_signal_activity(positions: list[dict]) -> list[dict]:
    """Adds signals_since_entry_count/whale_lean_since_entry to each open
    position - 2026-08-16 direct report: a position's card showed only the
    single whale print that opened it, nothing about whale activity since,
    making the ongoing sentiment-driven exit reasoning (strategy_engine.
    check_exits' _whale_lean/_exit_confidence, which really is running
    every tick - see that module) invisible even though it's real.
    state["signal_feed"] can't answer this itself: it's one 50-slot window
    shared across every ticker in the app, so a busy ticker crowds out a
    quiet one within seconds - signal_log.for_ticker queries the full
    persisted history instead, scoped to exactly this ticker since this
    position's own opened_at. One extra indexed query per open position
    per state build - cheap at the position counts this app actually
    carries (single digits to low tens), not per signal."""
    for p in positions:
        # count is the real total (can exceed for_ticker's own row cap
        # under stress-test load); matches (bounded) is only for the lean
        # weighting below, which doesn't need every row to be representative.
        p["signals_since_entry_count"] = signal_log.count_for_ticker(p["ticker"], since_ts=p["opened_at"])
        matches = signal_log.for_ticker(p["ticker"], since_ts=p["opened_at"])
        if matches:
            yes_weight = sum(s["size"] * s["confidence"] for s in matches if s["side"] == "yes")
            no_weight = sum(s["size"] * s["confidence"] for s in matches if s["side"] == "no")
            total = yes_weight + no_weight
            p["whale_lean_since_entry"] = {
                "count": len(matches),
                "yes_pct": (yes_weight / total * 100) if total else 50.0,
            }
        else:
            p["whale_lean_since_entry"] = None
    return positions


def _enriched_broker_state(paper_broker_instance: PaperBroker, latest_prices: dict[str, float]) -> dict:
    """broker.state() plus the two enrichments _build_state_body's raw
    positions/recent_trades otherwise lack (see each enrichment function's
    own docstring for the real reports behind them) - one call to state()
    rather than the caller spreading it twice, which would recompute
    equity()/cost_basis() for every position a second time for nothing."""
    base = paper_broker_instance.state(latest_prices)
    return {
        **base,
        "positions": _enrich_positions_with_signal_activity(base["positions"]),
        "recent_trades": _enrich_recent_trades(paper_broker_instance),
    }


_SERIES_CACHE_TTL_SEC = 3600  # series (a recurring-event template - "Pro Basketball Game") don't
# change often enough to justify get_series_list's ~1s cost (12,500+ entries) every 15s poll tick

_PINNED_MARKET_REFRESH_SEC = 300  # structural fields (title, close_time, status, ...) for a
# manually-pinned ticker change rarely - price freshness comes from the WS ticker stream instead
# (see _fetch_markets' live-price overlay), not from re-fetching the whole market object every tick.

_DISCOVERY_REFRESH_SEC = 300  # 2026-08-15, second incident on the same code path: "you made the
# market watch list and whale watching grind to a halt and markets aren't even appearing anymore."
# The first fix (this constant alone, at 90s, still AWAITED inline every time it was due) was
# necessary but not sufficient - once services/http_client.py's rate limiter was corrected from a
# concurrency cap to the real token-bucket throughput limit Kalshi actually enforces (see that
# module's own comment), the SAME 12-120-series get_candidate_markets fetch this constant gates
# takes proportionally longer under a correctly-conservative rate (tens of seconds to minutes, not
# the old, rate-limit-violating handful of seconds) - and every tick where a refresh was due
# BLOCKED on it, stalling signal handling/trade execution/whale-tape processing behind a fetch for
# data (which series/markets exist right now) that changes on the order of minutes, not seconds.
# Fixed at the root below (_maybe_refresh_discovery_cache runs this as a genuinely independent
# background task, never awaited by the tick loop - a tick always reads whatever's already cached,
# even if stale) rather than by only tuning this number again; 300s (was 90s) on top of that is a
# real reduction in total REST volume for non-trading data unlikely to change tick-to-tick, not a
# response to a timing problem the background-task fix above already solves independently. Between
# refreshes (now always, not just "between" in the blocking sense), the same selected market list
# is reused with live prices overlaid from state["latest_prices"]/state["latest_asks"] (kept fresh
# every tick by the WS ticker-channel stream - see _process_stream_ticker and this function's own
# set_market_tickers call), so price freshness is fully decoupled from how often the underlying
# selection itself gets re-run.


async def _get_series_cache(client: KalshiClient) -> list[dict]:
    """All series with nonzero lifetime volume (~9,400 of Kalshi's ~12,500
    total, as of 2026-08-08), sorted by volume_fp descending, cached in
    state["series_cache"] and refreshed at most once per
    _SERIES_CACHE_TTL_SEC. See get_series_list's docstring for why this
    (series-level volume, then query real series directly) replaced
    browsing individual markets - a flat browse can be 100% dead combo
    markets even across tens of thousands of entries, confirmed directly,
    repeatedly. Shared by both the automatic watchlist (_get_top_series,
    just the top N) and market search (search_markets, which also needs
    the long tail to text-match against)."""
    cache = state["series_cache"]
    if time.time() - cache["fetched_at"] > _SERIES_CACHE_TTL_SEC or not cache["series"]:
        series = await client.get_series_list()
        series = [s for s in series if float(s.get("volume_fp") or 0) > 0]
        series.sort(key=lambda s: float(s.get("volume_fp") or 0), reverse=True)
        cache["series"] = series
        cache["fetched_at"] = time.time()
        series_cache.save(cache["fetched_at"], cache["series"])
    return cache["series"]


async def _get_top_series(client: KalshiClient, categories: list[str] | None = None, top_n_per_category: int = 12) -> list[str]:
    """Per-category discovery (2026-08-15 direct fix, real live report:
    "i see absolutely no signal or trade activity related to any markets
    other than sports or crypto... mentions... politics"). A flat global
    top-N by lifetime volume (the old behavior) systematically starves any
    category whose lifetime volume is small relative to Sports/Crypto's -
    confirmed live: Sports alone held 32 of the old top 40 series by
    lifetime volume, with Mentions/most of Politics/Entertainment/Climate
    holding zero. No amount of re-ranking *within* that narrow top-40 (see
    event_lifecycle.phase_ranked) can fix a category that was never even
    considered in the first place.

    categories: the same kalshi.categories list this app's own category
    filter already scopes to - top_n_per_category series from EACH one,
    guaranteeing every enabled category gets real, bounded coverage
    instead of zero. Total series considered is bounded by
    len(categories) * top_n_per_category (default config: 6 * 12 = 72), a
    moderate, predictable increase from the old flat 40 - deliberately NOT
    "query all ~9,757 volume-positive series every tick," which would mean
    ~9,757 concurrent API calls per 5s tick and risks recreating the exact
    shape of a real, already-documented incident (status.html phase 97 - a
    removed cap on trade-tape processing froze the app for several
    minutes). None/empty categories falls back to the old flat top-N
    behavior (top_n_per_category * 6, matching the default category
    count) rather than returning nothing."""
    series = await _get_series_cache(client)
    if not categories:
        return [s["ticker"] for s in series[:top_n_per_category * 6]]
    by_category: dict[str, list[str]] = {}
    for s in series:
        cat = s.get("category")
        if cat in categories:
            by_category.setdefault(cat, []).append(s["ticker"])
    result: list[str] = []
    for cat in categories:
        result.extend(by_category.get(cat, [])[:top_n_per_category])
    return result


# Series scanned per background batch to build services/market_catalog.py's
# near-term catalog. Cut from 40 to 10 (2026-08-15 tick_duration
# investigation) - confirmed live as the real remaining root cause after
# fixing three other uncached/uncapped call sites (propagate_
# milestone_winners, _fetch_event_live_data, _fetch_live_status's own
# per-tick cap) didn't meaningfully move tick_duration: this batch runs as
# an independent background task (_maybe_scan_catalog_batch), so it never
# blocks the tick's own await chain, but it draws from the exact same
# shared Kalshi rate limiter (services/http_client.py) the tick's own
# latency-sensitive reads need - backgrounding via asyncio.create_task
# decouples CONTROL FLOW, not RESOURCE CONTENTION. At 40 series/batch, with
# _CATALOG_SCAN_MIN_INTERVAL_SEC's overlap guard meaning a new batch starts
# again the moment the previous one finishes draining, this ran back-to-
# back continuously, at times consuming close to the entire 3.0 tokens/sec
# read budget by itself - starving _fetch_account_snapshot (measured
# stalling to 19-33s on affected ticks despite its own working 20s cache)
# and everything else sharing the bucket. Direct priority ordering already
# established this session ("maximum efficiency and maximum speed for
# position management and whale watching") argues for catalog-scan - a
# bulk, non-urgent backlog-clearing task, same category as signal-
# resolution's own already-throttled limit=10 batch - yielding budget to
# the tick-critical path, not competing with it head-on. Slower to fully
# re-cycle through every configured-category series as a result, but the
# catalog was already substantially warm before this change (thousands of
# series/markets from an earlier scanning period).
_CATALOG_SCAN_BATCH_SIZE = 10


async def _scan_catalog_batch(client: KalshiClient, cfg: dict):
    """Incrementally builds market_catalog's near-term market catalog, a
    bounded batch (least-recently-scanned series first, see market_catalog.
    next_series_to_scan) per call - see market_catalog.py's own module
    docstring for the full "why": volume-ranking the top 40 series
    systematically misses markets that are live right now but sit in a
    lower-volume series, confirmed directly against real Kalshi data (found
    ~0 of the real live markets the user could see on Kalshi's own site).

    No longer gated behind kalshi.live_markets_only (2026-08-15, direct
    "no stone unturned" API audit) - this catalog is now the primary
    source for the DEFAULT (non-live-only) discovery path too (see
    market_catalog.open_candidates/_refresh_discovery_cache), not just the
    live-only one, so it needs to stay warm regardless of that flag.
    Scoped to cfg["kalshi"]["categories"] rather than every one of
    Kalshi's ~9,400 series - this app only ever trades within its
    configured categories, so scanning outside them would be pure waste,
    exactly what this pass exists to eliminate. No categories configured
    falls back to scanning everything (matches _get_top_series' own
    no-categories fallback)."""
    all_series = await _get_series_cache(client)
    categories = cfg["kalshi"].get("categories")
    if categories:
        all_series = [s for s in all_series if s.get("category") in categories]
    batch = market_catalog.next_series_to_scan(all_series, _CATALOG_SCAN_BATCH_SIZE)
    if not batch:
        return
    results = await asyncio.gather(
        *(client.get_markets(limit=100, status="open", series_ticker=s["ticker"]) for s in batch),
        return_exceptions=True,
    )
    now = time.time()
    # Data-robustness audit finding (2026-08-10): mark_scanned() used to be
    # called for the WHOLE batch unconditionally, regardless of whether each
    # series' fetch actually succeeded - return_exceptions=True above
    # swallows a failure with no logging at all, so a persistently-failing
    # series (rate limit, malformed/renamed ticker, transient API error)
    # would mark itself "freshly scanned" every ~59-minute rotation forever,
    # looking identical to a series that's simply quiet, while never
    # actually writing a row. Only the series that genuinely succeeded this
    # batch get marked scanned; a failing one stays at the front of the
    # least-recently-scanned queue and gets retried next tick instead of
    # silently going stale for good.
    succeeded = []
    for s, result in zip(batch, results):
        if isinstance(result, list):
            market_catalog.upsert_markets(s["ticker"], s.get("category"), result, updated_at=now)
            succeeded.append(s["ticker"])
        else:
            # No logging framework exists anywhere in this app yet (audit
            # finding) - stdout is captured by `ddev logs -s fastapi` per
            # this project's own documented workflow, so a failing series is
            # at least visible there instead of vanishing with zero trace.
            print(f"[market_catalog] scan failed for {s['ticker']!r}, will retry next tick: {result!r}")
    if succeeded:
        market_catalog.mark_scanned(succeeded, scanned_at=now)


_CATALOG_SCAN_MIN_INTERVAL_SEC = 15  # how often a new background batch may be KICKED OFF - the
# shared token-bucket rate limiter (services/http_client.py), not this interval, is what actually
# keeps the real aggregate call rate safe; this just avoids spawning pointless overlapping tasks.


def _maybe_scan_catalog_batch(cfg: dict) -> None:
    """Triggers _scan_catalog_batch as an independent background task on
    its own steady interval, decoupled from the main tick entirely - same
    pattern (and same 2026-08-15 "no stone unturned" API audit) as
    discovery's own _maybe_refresh_discovery_cache. Synchronous/non-
    blocking on purpose, exactly like that sibling function."""
    catalog_state = state["catalog_scan"]
    now_ts = time.time()
    due = now_ts - catalog_state["last_started_at"] > _CATALOG_SCAN_MIN_INTERVAL_SEC
    if due and not catalog_state["scanning"]:
        catalog_state["scanning"] = True
        catalog_state["last_started_at"] = now_ts
        catalog_state["task"] = asyncio.create_task(_scan_catalog_batch_background(cfg))


async def _scan_catalog_batch_background(cfg: dict) -> None:
    """Owns its own KalshiClient (not the calling tick's, which closes at
    the end of that same tick - see _refresh_discovery_cache's identical
    reasoning) and delegates the real work to _scan_catalog_batch
    unchanged, so its existing behavior/tests keep working when called
    directly with an explicit client."""
    catalog_state = state["catalog_scan"]
    client = KalshiClient(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])
    try:
        await _scan_catalog_batch(client, cfg)
    except Exception as exc:
        print(f"[market_catalog] background scan batch failed entirely, will retry next cycle: {exc!r}")
    finally:
        catalog_state["scanning"] = False
        await client.close()


def _series_meta_map(series_tickers: set[str]) -> dict:
    """series_of()'s ticker prefix (see services/signal_log.py) already
    equals a real series ticker in practice - what's been missing is a real
    name for it. This is the "better Kalshi series metadata" ROADMAP.md's
    series/category grouping item was waiting on: get_series_list (already
    fetched and cached hourly for the watchlist/search, see
    _get_series_cache) carries a real title and a real, clean 18-category
    taxonomy (via `category`) plus finer tags (e.g. "Tennis", "Soccer") per
    series - zero extra API cost to expose, just data that was already
    being fetched and then discarded. Lets the dashboard show "ITF Women's
    Match" / "Sports · Tennis" instead of a raw ticker prefix like
    "KXITFWMATCH" wherever a whale-accuracy series is surfaced.

    Scoped to series_tickers (the series actually relevant right now, from
    state["series_track_record"]) rather than the full cache - confirmed
    directly, not assumed, that dumping the whole thing was a real mistake:
    the full series_cache is ~9,400 entries and ballooned /api/state from
    ~30KB to over 1MB, undoing the entire earlier efficiency pass in one
    line. A handful of entries (however many distinct series are on the
    current watchlist) costs nothing by comparison."""
    if not series_tickers:
        return {}
    return {
        s["ticker"]: {"title": s.get("title"), "category": s.get("category"), "tags": s.get("tags") or []}
        for s in state["series_cache"]["series"]
        if s["ticker"] in series_tickers
    }


def _real_account_position_tickers(account: dict) -> set[str]:
    """Tickers of the *real* connected Kalshi account's currently open
    positions only - deliberately excludes fills (see trading_loop's own
    extra_tickers comment for why folding fills into anything that drives
    the live watchlist fetch is wrong: fills are historical trade records
    that can span days/weeks, unlike a position, which naturally drops out
    the tick it closes). Shared by trading_loop (feeds _fetch_markets'
    extra_tickers) and _relevant_tickers below (feeds /api/state's title
    scoping) so both stay defined identically rather than drifting."""
    return {
        p.get("ticker") for p in ((account.get("positions") or {}).get("market_positions") or []) if p.get("ticker")
    }


def _relevant_tickers() -> set[str]:
    """Every ticker actually shown on this tick's /api/state response -
    current watchlist, open positions, the Trade Log's own last-25 closed
    trades (broker.state()'s "recent_trades", exactly what
    static/index.html's renderTrades() actually displays - added 2026-08-09,
    a real, confirmed-live gap: a position's ticker dropped out of this set
    the instant it closed and aged out of the watchlist/signal/decision
    feeds, even though the Trade Log kept showing that trade, so it fell
    back to its raw ticker until something else - visiting History, whose
    own endpoint separately backfills the shared client-side title cache -
    happened to pull the title back in), whatever's still in the capped
    signal/decision feeds, and the *real* connected Kalshi account's own
    open positions/recent fills (renderRealPositions/renderRealFills call
    the same marketLabel() as the paper panels - they were only ever
    missing an entry to look up, same bug class, added alongside the
    trade_log fix above once it turned out real-account tickers had no
    title-resolution path at all, not even a lagging one). state["market_titles"]/
    state["event_titles"] themselves accumulate unbounded for the app's
    whole lifetime now (see services/title_cache.py) so history/clusters can
    still resolve an old ticker's title on their own separately-scoped
    requests, but /api/state itself must stay scoped to this same small set
    - same reasoning, same ~1MB regression risk, as _series_meta_map above.

    Real bug found live (2026-08-10, direct report - a Market-Native trade
    log row showing a raw ticker like "KXMLBGAME-26AUG101940BALMIN-BAL"
    instead of its resolved title): this only ever included the
    whale-follow `broker`'s own positions/trade_log, the exact same gap
    already found and fixed once for `broker` itself (see this docstring's
    own history above) - just never extended to `market_broker` when the
    Market-Native tab was built, since GET /api/market-strategy/state
    reuses this same function to scope its own market_titles. A market-
    native trade that ages out of the current watchlist had no title-
    resolution path at all, not even a lagging one - it rendered fine only
    as long as an EARLIER poll had already cached the title client-side;
    a fresh page load (or a long-since-closed market-native position)
    never got the chance."""
    tickers = {m["ticker"] for m in state["markets"] if m.get("ticker")}
    tickers |= set(broker.positions.keys())
    tickers |= {t.ticker for t in broker.trade_log[-25:]}
    tickers |= set(market_broker.positions.keys())
    tickers |= {t.ticker for t in market_broker.trade_log[-25:]}
    tickers |= {s["ticker"] for s in state["signal_feed"] if s.get("ticker")}
    for d in state["decision_feed"]:
        t = d.get("ticker") or (d.get("signal") or {}).get("ticker")
        if t:
            tickers.add(t)
    for d in state["market_decision_feed"]:
        t = d.get("ticker")
        if t:
            tickers.add(t)
    account = state.get("account") or {}
    tickers |= _real_account_position_tickers(account)
    tickers |= {
        f.get("ticker") or f.get("market_ticker")
        for f in ((account.get("fills") or {}).get("fills") or [])
        if f.get("ticker") or f.get("market_ticker")
    }
    return tickers


def _scoped_market_titles(tickers: set[str]) -> dict:
    return {t: state["market_titles"][t] for t in tickers if t in state["market_titles"]}


def _scoped_event_titles(market_titles: dict) -> dict:
    event_tickers = {v["event_ticker"] for v in market_titles.values() if v.get("event_ticker")}
    return {et: state["event_titles"][et] for et in event_tickers if et in state["event_titles"]}


def _scoped_event_live_data(market_titles: dict) -> dict:
    event_tickers = {v["event_ticker"] for v in market_titles.values() if v.get("event_ticker")}
    return {et: state["event_live_data"][et] for et in event_tickers if et in state["event_live_data"]}


def _scoped_live_game_state(market_titles: dict) -> dict:
    event_tickers = {v["event_ticker"] for v in market_titles.values() if v.get("event_ticker")}
    return {et: state["live_game_state"][et] for et in event_tickers if et in state["live_game_state"]}


async def _fetch_category_metadata(client: KalshiClient, ttl_sec: int = 3600) -> dict:
    cache = state["category_metadata"]
    now = time.time()
    if cache.get("fetched_at") and (now - cache["fetched_at"]) < ttl_sec:
        return cache
    try:
        tags_resp, sports_resp = await asyncio.gather(
            client.get_tags_for_series_categories(),
            client.get_filters_for_sports(),
        )
        filters_by_sports = (sports_resp or {}).get("filters_by_sports") or {}
        cache.update({
            "fetched_at": now,
            "tags_by_categories": (tags_resp or {}).get("tags_by_categories") or {},
            "filters_by_sports": filters_by_sports,
            "sport_ordering": (sports_resp or {}).get("sport_ordering") or [],
            # competition -> sport reverse lookup (2026-08-16 direct
            # standing instruction: check docs/kalshi/ for already-
            # available fields before deriving/guessing - get-filters-for-
            # sports.md documents filters_by_sports as {sport: {scopes,
            # competitions: {competition: {scopes}}}}, confirmed live:
            # filters_by_sports["Baseball"]["competitions"] includes "Pro
            # Baseball", "Japan NPB", "Korea KBO", "Mexico LMB" - all one
            # sport, several competitions. The whale-confidence subcategory
            # tier (services/trade_category.py) wants SPORT ("Baseball",
            # matching the user's own "baseball, football" examples), not
            # the finer per-competition string a bare event.competition
            # read would give ("Pro Baseball") - built once per hourly
            # refresh here, not per-trade in _subcategory_by_ticker.
            "sport_by_competition": {
                competition: sport
                for sport, details in filters_by_sports.items()
                for competition in (details.get("competitions") or {})
            },
        })
    except Exception:
        pass
    return cache


async def _cached_market_fetch(client: KalshiClient, tickers: list[str]) -> list[dict]:
    """Shared by _fetch_markets' pinned-watchlist and extra_tickers
    handling (2026-08-15, "websocket stream everything you can... leave
    the api calls for things that are absolutely necessary") - both used
    to re-fetch every one of their tickers via individual REST get_market()
    calls on every single tick, unconditionally. A ticker's structural
    fields (title, event_ticker, occurrence_datetime, close_time, status)
    change rarely; price comes from the WS ticker-channel stream instead
    (_fetch_markets' own live-price overlay right before it returns), not
    from re-fetching the whole market object. state["market_object_cache"]
    (one shared cache, not two - same ticker->market shape and refresh
    semantics either way) only bounds how stale the *structural* fields
    can get, via _PINNED_MARKET_REFRESH_SEC - never price."""
    cache = state["market_object_cache"]
    now_ts = time.time()
    stale_or_missing = [
        t for t in tickers
        if t not in cache or (now_ts - cache[t]["_cached_at"]) > _PINNED_MARKET_REFRESH_SEC
    ]
    if stale_or_missing:
        # Batched (2026-08-16, direct efficiency note: "a lot of efficiency
        # could be gained by using batch calls to the API vs individual
        # calls for specific markets") - was N individual get_market() calls
        # gathered concurrently; one get_markets_by_tickers call now covers
        # every stale/missing ticker regardless of how many need it.
        fetched = await client.get_markets_by_tickers(stale_or_missing)
        for t in stale_or_missing:
            r = fetched.get(t)
            if r is not None:
                r["_cached_at"] = now_ts
                cache[t] = r
    return [
        {k: v for k, v in cache[t].items() if k != "_cached_at"}
        for t in tickers if t in cache
    ]


def _maybe_refresh_discovery_cache(cfg: dict) -> None:
    """Kicks off _refresh_discovery_cache_background as an independent
    background task if the cache is stale and no refresh is already
    running - never awaited by the calling tick (see _fetch_markets' own
    comment, and _DISCOVERY_REFRESH_SEC's, for the incident this fixes).
    Synchronous on purpose: this only ever schedules work, it never does
    any I/O of its own, so there's nothing to await here even though the
    work it schedules is async. Stores the created Task on discovery_cache
    itself so it isn't garbage-collected mid-flight - a live asyncio
    footgun, a Task object with no reference anywhere can be collected
    before it completes even though it's still "running" on the event
    loop.

    Takes no client - _refresh_discovery_cache_background creates its own
    (2026-08-16 fix, real live incident: this used to hand the calling
    tick's own KalshiClient straight into the background task, but that
    same tick's own `finally: await client.close()` closes it at the end of
    that tick, well before an independent background task reliably
    finishes - a real client-lifecycle race, confirmed live via repeated
    "[discovery] background refresh failed" log lines cycling through
    RuntimeError('Session is closed')/ClientConnectionError('Connector is
    closed.')/AssertionError()). See _refresh_discovery_cache_background's
    own docstring for the fix."""
    disc_cache = state["discovery_cache"]
    now_ts = time.time()
    stale = now_ts - disc_cache["fetched_at"] > _DISCOVERY_REFRESH_SEC
    if stale and not disc_cache["refreshing"]:
        disc_cache["refreshing"] = True
        disc_cache["task"] = asyncio.create_task(_refresh_discovery_cache_background(cfg))


# Real, live-confirmed finding (2026-08-16, "close_time mutability"
# investigation - see ROADMAP.md's now-closed "Active investigation" entry):
# Kalshi's own market_lifecycle docs (docs/kalshi/market_lifecycle.md)
# confirm close_time can be revised earlier via a close_date_updated event
# "when a market is closed ahead of its scheduled close time, including
# before determination" - and a catalog row scanned before that revision
# fires keeps showing the OLD close_time/status indefinitely until its next
# scan (confirmed live: a real finalized MLB market's catalog row still
# showed status=active, close_time 2.5 days out, 24 minutes after Kalshi's
# own API had already moved it to status=finalized with the true, earlier
# close_time). candidates_in_window/open_candidates' close_ts>now filter
# can't catch this - it's trusting the same stale column that's wrong. Any
# status past "active" in the real lifecycle (closed/determined/disputed/
# amended/finalized - see market_lifecycle.md's own table) means the
# catalog's belief about this market is no longer trustworthy regardless of
# what close_ts says.
_DISCOVERY_TERMINAL_STATUSES = {"closed", "determined", "disputed", "amended", "finalized"}


async def _refresh_discovery_cache(cfg: dict, client: KalshiClient) -> None:
    """Discovery's selection pipeline, now sourced entirely from
    market_catalog's already-persisted, independently-scanned data
    (services/market_catalog.py's open_candidates) instead of a fresh
    get_candidate_markets REST fetch per series - 2026-08-15 direct "no
    stone unturned" API audit, the definitive fix for the same incident
    _DISCOVERY_REFRESH_SEC's own comment describes: "no more excessive api
    calls to get non trading data that is unlikely to change." Only one
    small network call of its own now (see the real-time confirmation step
    below, 2026-08-16) - the catalog itself still stays warm via the fully
    separate _scan_catalog_batch/_maybe_scan_catalog_batch background task,
    paced independently by the same shared rate limiter. Still run as a
    background task (async def, still triggered by
    _maybe_refresh_discovery_cache below) rather than inlined
    synchronously - defensive: a SQLite query + phase_ranked/
    round_robin_select over a large, still-growing catalog should stay
    fast, but "still fast" isn't a promise worth betting the tick loop's
    responsiveness on now that it doesn't have to.

    Selects the top top_series_per_category DISTINCT SERIES per category
    by walking open_candidates' already volume-sorted rows (mirrors the
    old _get_top_series' per-category cap exactly, just computed from
    current per-market catalog volume instead of a series' lifetime
    total - arguably the more relevant signal for "worth watching right
    now," not a weaker substitute for it), then keeps only markets
    belonging to a selected series before phase-ranking/final selection -
    unchanged from before this fix.

    Pure worker, takes client as a param (2026-08-16 client-lifecycle fix -
    see _refresh_discovery_cache_background's own docstring for why this
    doesn't own/close the client itself) - same split
    _check_signal_resolutions/_check_signal_resolutions_background already
    established for this exact same shape of problem. Lets exceptions
    propagate rather than swallowing them - the background wrapper is what
    catches/logs/retries; tests call this directly with a fake client and
    should see real failures, not a silently-eaten one."""
    disc_cache = state["discovery_cache"]
    min_volume = cfg["kalshi"].get("min_volume_24h", 0)
    categories = cfg["kalshi"].get("categories")
    top_n_per_category = cfg["kalshi"].get("top_series_per_category", 12)
    catalog_rows = market_catalog.open_candidates(
        categories=categories, min_volume=min_volume,
        min_volume_by_series=cfg["kalshi"].get("min_volume_24h_by_series"),
    )
    selected_series_by_category: dict[str, list[str]] = {}
    for row in catalog_rows:
        cat, series = row.get("category"), row.get("series_ticker")
        if not cat or not series:
            continue
        bucket = selected_series_by_category.setdefault(cat, [])
        if series not in bucket and len(bucket) < top_n_per_category:
            bucket.append(series)
    selected_series = {s for bucket in selected_series_by_category.values() for s in bucket}
    if cfg.get("series_evaluator", {}).get("enabled"):
        ineligible = series_evaluator.ineligible_series(time.time())
        selected_series -= set(ineligible)
    candidates = [r for r in catalog_rows if r.get("series_ticker") in selected_series]
    el_cfg = cfg.get("event_lifecycle") or {}
    candidates = event_lifecycle.phase_ranked(
        candidates, state["event_titles"], now=time.time(),
        tournament_min_siblings=el_cfg.get("tournament_min_siblings", 4),
        tournament_pretail_days=el_cfg.get("tournament_pretail_days", 5.0),
        pre_tail_volume_weight=el_cfg.get("pre_tail_volume_weight", 0.4),
        post_tail_volume_weight=el_cfg.get("post_tail_volume_weight", 0.2),
    )
    markets = KalshiClient.round_robin_select(
        candidates, cfg["kalshi"]["watchlist_size"],
        max_children_per_parent=cfg["kalshi"].get("max_children_per_parent"),
    )
    # Real-time confirmation pass (2026-08-16, close_time-mutability fix
    # - see _DISCOVERY_TERMINAL_STATUSES above). Bounded to exactly the
    # final, already-narrowed selection (watchlist_size, not the whole
    # candidate pool), so this stays one cheap batched call per refresh
    # cycle (_DISCOVERY_REFRESH_SEC = 300s), not a return to the
    # per-refresh REST-fetch pattern the 2026-08-15 incident removed.
    # Drops anything Kalshi now reports as past "active" outright - a
    # stale catalog row must never reach the real watchlist just
    # because it hasn't been rescanned yet. A ticker Kalshi didn't
    # return (a genuine fetch miss) keeps its catalog row rather than
    # being dropped - same "degrade honestly, never guess" idiom
    # _fetch_markets' own live_markets_only hydration already uses.
    selected_tickers = [m["ticker"] for m in markets if m.get("ticker")]
    if selected_tickers:
        confirmed = await client.get_markets_by_tickers(selected_tickers)
        live_markets = []
        for m in markets:
            real = confirmed.get(m.get("ticker"))
            if real is None:
                live_markets.append(m)
                continue
            if (real.get("status") or "").strip().lower() in _DISCOVERY_TERMINAL_STATUSES:
                continue  # confirmed no longer tradeable - drop before it ever reaches the watchlist
            live_markets.append(real)  # real current price/status, not the catalog's possibly-stale copy
        markets = live_markets
    disc_cache["markets"] = list(markets)
    disc_cache["fetched_at"] = time.time()


async def _refresh_discovery_cache_background(cfg: dict) -> None:
    """Background-task wrapper around _refresh_discovery_cache - owns its own
    KalshiClient (2026-08-16 client-lifecycle fix, real live incident:
    _maybe_refresh_discovery_cache used to hand this the calling tick's own
    client, but that same tick's own `finally: await client.close()` closes
    it at the end of that tick regardless of whether this independent
    background task has finished with it - confirmed live via repeated
    "[discovery] background refresh failed" log lines cycling through
    RuntimeError('Session is closed')/ClientConnectionError('Connector is
    closed.')/AssertionError()). Same split as
    _check_signal_resolutions_background/_check_signal_resolutions - that
    function's own docstring already described this exact pattern as if
    _refresh_discovery_cache followed it too, which is what surfaced this
    gap on review."""
    disc_cache = state["discovery_cache"]
    client = KalshiClient(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])
    try:
        await _refresh_discovery_cache(cfg, client)
    except Exception as exc:
        # No logging framework exists anywhere in this app yet (same gap
        # _scan_catalog_batch's own per-series failure print already
        # documented) - stdout is captured by `ddev logs -s fastapi`, so a
        # failed background refresh is at least visible there instead of
        # vanishing with zero trace. The stale cache stays in place and
        # _maybe_refresh_discovery_cache will try again next time it's due.
        print(f"[discovery] background refresh failed, will retry next cycle: {exc!r}")
    finally:
        disc_cache["refreshing"] = False
        await client.close()


async def _fetch_markets(client: KalshiClient, cfg: dict, extra_tickers: list[str] | None = None) -> list[dict]:
    # Real live report (2026-08-15): kalshi.markets_watchlist used to be a
    # strict either/or with discovery below - a non-empty pinned list
    # replaced round-robin discovery entirely rather than adding to it, so
    # pinning a handful of tickers (e.g. one political market's own
    # candidates) silently zeroed out every other category's whale-signal
    # coverage for as long as the pin stayed set. Direct instruction:
    # "merge: keep KXPRESNOMD pinned + add real discovery." Now always
    # fetched, always merged with whatever discovery below finds - pinned
    # tickers don't count against watchlist_size's cap, same "always
    # included, exempt from the cap" treatment extra_tickers already gets
    # a few lines down.
    #
    # Series-level pins (2026-08-16 direct request: "the market watch list
    # should act as that override, that's what the pinned list is for" -
    # KXBTC15M can never pass live_markets_only's milestone-based live-
    # status check by design, no matter what volume overrides exist). Each
    # watchlist entry is tried against market_catalog.open_markets_for_series
    # first - a literal exact ticker never matches any row's series_ticker
    # column, so it naturally falls through to the existing exact-ticker
    # path below. A series pin resolves to whatever instance(s) are
    # currently open, every refresh - so a rolling 15-minute series stays
    # pinned across rollovers instead of going stale the way a literal
    # ticker pin would.
    watchlist = cfg["kalshi"]["markets_watchlist"]
    series_pinned_markets: list[dict] = []
    literal_pins: list[str] = []
    for entry in watchlist:
        series_markets = market_catalog.open_markets_for_series(entry)
        if series_markets:
            series_pinned_markets.extend(series_markets)
        else:
            literal_pins.append(entry)
    pinned_markets = series_pinned_markets + (
        await _cached_market_fetch(client, literal_pins) if literal_pins else []
    )

    min_volume = cfg["kalshi"].get("min_volume_24h", 0)
    if cfg["kalshi"].get("live_markets_only"):
        # Direct request: discovery itself, not just whether an already-
        # selected market's signal gets acted on, should be able to only
        # ever pick currently-live markets. Round-robin's usual top-n cut
        # happens *after* filtering here, not before - checking live
        # status only on an already-narrowed watchlist would mean "only
        # live" really meant "only live among whichever 50 happened to
        # win on volume," which could easily be zero of them.
        #
        # Candidates come from market_catalog (see _scan_catalog_batch),
        # not a fresh top-40-series fetch - confirmed directly against
        # real Kalshi data that volume-ranking the candidate pool misses
        # almost everything actually live right now (a series can be
        # high-volume overall with nothing airing this exact hour, and
        # vice versa). The catalog is scanned incrementally in the
        # background and may be sparse/empty right after this feature is
        # first turned on - that's an honest, self-correcting transient
        # state (see market_catalog.py), not backfilled with anything
        # fabricated.
        now = time.time()
        candidates = market_catalog.candidates_in_window(
            now, lookahead_sec=_LIVE_STATUS_LOOKAHEAD_SEC, lookback_sec=_LIVE_STATUS_LOOKBACK_SEC,
            min_volume=min_volume,
        )
        candidate_live_status = await _fetch_live_status(client, candidates)
        live_candidates = [
            m for m in candidates
            if candidate_live_status.get(m.get("event_ticker")) == "live"
        ]
        # series_evaluator's BEFORE-check (direct request): the watchlist
        # is fully recomputed from scratch every tick with zero memory,
        # so a series flapping near this filter's own boundary would
        # otherwise be re-added/re-evaluated/re-removed indefinitely.
        # Cheap, one batch query, gated behind series_evaluator.enabled
        # (default off) so this never changes discovery behavior for
        # anyone who hasn't opted in. Only applies to automatic
        # discovery, same carve-out kalshi.min_volume_24h already has -
        # pinned markets (the `if watchlist:` branch above) bypass this
        # entirely, same as every other automatic-discovery-only filter.
        if cfg.get("series_evaluator", {}).get("enabled"):
            ineligible = series_evaluator.ineligible_series(now)
            live_candidates = [
                m for m in live_candidates
                if signal_log.series_of(m.get("ticker")) not in ineligible
            ]
        # Never backfilled with non-live markets to hit watchlist_size -
        # direct choice: the watchlist shrinks (down to zero, if nothing
        # real is live right now) rather than quietly padding it with
        # markets that don't meet the filter someone deliberately turned on.
        markets = KalshiClient.round_robin_select(
            live_candidates, cfg["kalshi"]["watchlist_size"],
            max_children_per_parent=cfg["kalshi"].get("max_children_per_parent"),
        )
        # Real, confirmed-live bug: market_catalog rows only ever carry
        # schedule/title/volume metadata for discovery purposes (see
        # market_catalog.upsert_markets - no yes_bid_dollars/
        # yes_ask_dollars column exists), so every catalog-sourced
        # market silently fell through to state["latest_prices"]'s 0.5
        # fallback below - every card showed 50c/50c YES/NO and never
        # moved, for as long as live_markets_only has been on, direct
        # report: "showing 50c in green and red for all sets of yes/no
        # values all across the app. its not updating either." Final
        # selection is already bounded (watchlist_size parent series,
        # whatever max_children_per_parent allows), so re-fetching by
        # *series* here (one real get_markets(series_ticker=...) call
        # per distinct selected series, full priced market objects) is
        # the same per-series cost the non-live-only branch below
        # already pays - just deferred until after selection instead of
        # spent on the whole broad candidate pool.
        selected_tickers = {m["ticker"] for m in markets if m.get("ticker")}
        selected_series = sorted({signal_log.series_of(t) for t in selected_tickers})
        hydration_results = await asyncio.gather(
            *(client.get_markets(limit=100, status="open", series_ticker=s) for s in selected_series),
            return_exceptions=True,
        )
        hydrated_by_ticker = {}
        for r in hydration_results:
            if isinstance(r, list):
                for hm in r:
                    if hm.get("ticker") in selected_tickers:
                        hydrated_by_ticker[hm["ticker"]] = hm
        # A ticker that settled between the catalog scan and now won't
        # come back from the status="open" batch fetch above (confirmed
        # live: a handful of already-finalized markets were still
        # falling back to the 0.5 placeholder for exactly this reason) -
        # one batched fetch (no status filter, whatever its real current
        # state is - 2026-08-16 batching pass) for just what's still
        # missing, same "always the real current price, never a
        # placeholder" goal, cheap since this is normally a small
        # residual set.
        still_missing = [t for t in selected_tickers if t not in hydrated_by_ticker]
        if still_missing:
            fallback_results = await client.get_markets_by_tickers(still_missing)
            hydrated_by_ticker.update(fallback_results)
        # Still falls back to the original catalog row (schedule/title
        # info, just no live price) rather than dropping a ticker
        # outright if even the per-ticker fetch failed (a real API
        # error) - same "degrade honestly, never silently drop" pattern
        # as the rest of this app.
        markets = [hydrated_by_ticker.get(m["ticker"], m) for m in markets]
    else:
        # Discovery caching (2026-08-15, direct incident: "you made the
        # market watch list and whale watching grind to a halt and markets
        # aren't even appearing anymore") - a tick NEVER awaits the REST
        # discovery pipeline itself anymore, only reads whatever's already
        # in state["discovery_cache"] (possibly empty on a cold start,
        # possibly stale by up to _DISCOVERY_REFRESH_SEC - never blocking).
        # The actual fetch runs as an independent background task (see
        # _maybe_refresh_discovery_cache/_refresh_discovery_cache below) -
        # price freshness still comes from the WS ticker-stream overlay
        # right before this function returns, completely decoupled from
        # how often the underlying series/market *selection* gets re-run.
        _maybe_refresh_discovery_cache(cfg)
        markets = list(state["discovery_cache"]["markets"])

    # Merge in the pinned watchlist fetched at the top of this function -
    # always included, never counted against watchlist_size (same "always
    # included, exempt from the cap" treatment as extra_tickers just below).
    # Pinned first in list order (an explicit, deliberate pin reads as more
    # authoritative than whatever discovery happened to rank), discovery
    # results after, deduped by ticker.
    pinned_tickers = {m["ticker"] for m in pinned_markets if m.get("ticker")}
    markets = pinned_markets + [m for m in markets if m.get("ticker") not in pinned_tickers]

    # A currently-open paper position must keep getting a fresh price/title
    # every tick even if its market has rotated out of the top-volume
    # watchlist selection above - otherwise state["latest_prices"] silently
    # stops updating for it, which freezes mark_to_market and breaks
    # check_exits' take-profit/stop-loss/auto-exit triggers for a position
    # nobody's actively watching anymore even though real money (paper or
    # not) is still on the line.
    have = {m["ticker"] for m in markets if m.get("ticker")}
    missing = [t for t in (extra_tickers or []) if t not in have]
    if missing:
        # Cached, not re-fetched via REST every tick (2026-08-15, "websocket
        # stream everything you can") - same _cached_market_fetch as the
        # pinned watchlist above; price comes from the WS ticker-channel
        # overlay below regardless of when this last hit the real API.
        markets.extend(await _cached_market_fetch(client, missing))

    # Direct report (2026-08-11): "watchlist groupings is broken... likely a
    # result of the active removal of watchlist items. reorganization should
    # occur at the same time the watchlist updates." Confirmed: an open
    # position kept alive above after rotating out of round_robin_select's
    # own selection lands at the *end* of markets regardless of series - if
    # that position's series still has other members earlier in the list
    # (only this one ticker dropped, not the whole series), the frontend
    # (renderMarketCards' seriesRuns) - which assumes same-series markets are
    # always consecutive, since round_robin_select's own output guarantees
    # that - splits one series into two separate on-screen sections instead
    # of merging them. Re-groups by series here, preserving each series'
    # first-occurrence order (not an alphabetical sort, which would destroy
    # round_robin_select's volume-priority ordering) so any appended
    # straggler rejoins its series' existing run. Cheap - one pass, no extra
    # fetches - and also covers the manually-pinned kalshi.markets_watchlist
    # branch above, whose ticker order is whatever the user typed, not
    # necessarily grouped at all.
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for m in markets:
        key = signal_log.series_of(m.get("ticker") or "")
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(m)
    markets = [m for key in order for m in groups[key]]

    # Live-price overlay (2026-08-15, "websocket stream everything you can") -
    # applies regardless of source (pinned, freshly-discovered, or reused
    # from discovery_cache above): state["latest_prices"]/state["latest_asks"]
    # are kept continuously fresh by the WS ticker-channel stream
    # (_process_stream_ticker), independent of how often this function's own
    # REST discovery re-runs. A shallow copy, not an in-place mutation - the
    # entries in discovery_cache["markets"] must stay untouched by a single
    # tick's price overlay, or the cache would silently accumulate per-tick
    # state instead of remaining a clean "what was selected" snapshot.
    # Falls back to whatever price the market object already carried (its
    # own REST-fetched value) when no WS data has arrived for that ticker
    # yet - never guessed, same "missing isn't zero" idiom as the rest of
    # this app.
    latest_prices = state.get("latest_prices") or {}
    latest_asks = state.get("latest_asks") or {}
    overlaid = []
    for m in markets:
        ticker = m.get("ticker")
        if ticker and (ticker in latest_prices or ticker in latest_asks):
            m = dict(m)
            if ticker in latest_prices:
                m["yes_bid_dollars"] = latest_prices[ticker]
            if ticker in latest_asks:
                m["yes_ask_dollars"] = latest_asks[ticker]
        overlaid.append(m)
    return overlaid


_TRADE_TAPE_UI_CAP = 100  # display-only cap for state["trade_tape"] (the Trade
# Tape panel) - a human never needs to scroll more than this. Used to be the
# SAME cap whale-signal detection's input was truncated to as well (direct
# report, 2026-08-11: "i feel like... its not the only reason whale
# positions were undercounted" - correct: confirmed live, this cap was
# filling up within ~2 minutes under real load, meaning every trade past the
# 100 most-recent *platform-wide, across every watched market combined* was
# silently dropped before whale-filtering ever saw it, real size/threshold
# irrelevant). Detection input is not sliced to this at all anymore - see
# _fetch_trade_tape below, which is genuinely unbounded per direct
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


async def _fetch_trades_for_ticker(client: KalshiClient, ticker: str, min_ts: int | None) -> list[dict]:
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
    client: KalshiClient, markets: list[dict], since_ts: float | None = None,
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


_LIVE_STATUS_LOOKBACK_SEC = 8 * 3600  # keep tracking an event up to 8h after its scheduled start
# Widened 1h -> 12h on 2026-08-17. Measured live: 30 Sports events were on
# the watchlist while live_status held ONE entry and live_game_state held
# zero, because a game scheduled for 13:35 is ~7.5h away at 06:00 and fell
# outside a 1-hour lookahead. Nothing was tracked, so no score/period/clock
# was ever captured for any of them.
#
# Widening is bounded, not open-ended: _LIVE_STATUS_REPOLL_SEC (5 min)
# caches each event's status and _LIVE_STATUS_MAX_POLL_PER_TICK (10) caps
# how many are refreshed in any one tick, so a larger candidate pool
# lengthens the rotation rather than multiplying per-tick API calls. Those
# two bounds are what make this safe, and they must stay if this is widened
# further - see their own comments for the 2026-08-15 incident that put
# them there.
_LIVE_STATUS_LOOKAHEAD_SEC = 12 * 3600
# Direct request: once markets/whale data have populated the system, "no
# need to check if a market is live... every tick... they should have
# scheduled open and close times for you to do some light polling to track
# status but otherwise use the schedule and its previous live status to
# operate." Once an event has been checked at all, don't check it again for
# at least this long - a ~20x reduction in the 2-API-call milestone/live-
# data check's frequency versus doing it fresh every 15s tick regardless of
# whether anything could plausibly have changed.
_LIVE_STATUS_REPOLL_SEC = 5 * 60
_LIVE_STATUS_TERMINAL = {"finished", "closed"}  # once genuinely confirmed, never poll this event again
_LIVE_STATUS_MAX_POLL_PER_TICK = 10  # Bounded per-tick batch (2026-08-15
# tick_duration investigation), same shape as _check_signal_resolutions' own
# limit=10 batching - to_poll had no cap at all, so whenever a large
# fraction of the in-window candidate pool (up to ~2,500 rows / 100+
# distinct events - see market_catalog.candidates_in_window, called from
# _fetch_markets' live_markets_only branch) became simultaneously due for
# their 5-minute repoll, this fired dozens-to-hundreds of concurrent REST
# calls in a single tick, saturating the shared, deliberately-conservative
# Kalshi rate limiter (services/http_client.py). Confirmed live: this
# stalled every OTHER read call sharing that same limiter too
# (_fetch_account_snapshot alone measured at 32-33s on the same ticks,
# despite its own independent 20s interval cache doing exactly what it was
# supposed to) - asyncio.gather's concurrency doesn't bypass a shared token
# bucket. Oldest-checked-first (never-checked treated as most urgent, see
# the sort key below) so a large backlog drains gradually across ticks
# instead of spiking once; events excluded from a given tick's batch keep
# whatever cached status they already had (documented fallback behavior
# this function already relies on for "not yet due" - unchanged here).


async def _fetch_live_status(client: KalshiClient, markets: list[dict]) -> dict:
    """The real live/scheduled/finished status per event, via Kalshi's
    actual milestone/live-data system - confirmed directly against a real
    AFL match at its actual start time (status "scheduled"->"inprogress"->
    "closed", widget_status "none"->"live"->"finished"), not inferred from
    timestamps alone. Reads/writes state["live_status_cache"] directly
    (same module-global pattern _get_series_cache already uses for its own
    cache) rather than taking it as a parameter.

    Schedule-gated in two layers now, not one:
    1. Only markets whose occurrence_datetime falls in a plausible window
       (up to 1h before the scheduled start through 6h after it) are
       considered at all - unchanged in spirit from before, but the bounds
       were actually inverted from what this comment always claimed (a
       real bug found while touching this: the old condition let events
       starting hours in the future in but dropped anything that had been
       running for more than an hour, exactly backwards from "started up
       to 6h ago, or starting within the next hour"). Fixed here.
    2. Within that window, an event is only actually re-polled (the 2 real
       API calls) if it has no cached status yet, or its cached status is
       older than _LIVE_STATUS_REPOLL_SEC - otherwise the cached value is
       reused as-is. A market past its own close_time is treated as
       finished from the schedule alone, no poll needed: trading has
       already stopped there regardless of what the live-data API would
       say. Once a status is confirmed terminal (finished/closed), it's
       never polled again for the rest of this process's life.

    3. When a real poll *is* attempted but Kalshi's milestone/live-data
       system has nothing for this event (confirmed live in practice: most
       real candidates get no milestone at all), falls back to inferring
       from the schedule alone rather than leaving it unknown - started
       per occurrence_datetime and not yet past close_time (already
       screened above) means presumed live. See the schedule-fallback
       block below for the source="schedule" vs "milestone" cache tag."""
    now = time.time()
    cache = state["live_status_cache"]
    event_occ_ts: dict[str, float] = {}
    for m in markets:
        occ, et = m.get("occurrence_datetime"), m.get("event_ticker")
        if not occ or not et or et in event_occ_ts:
            continue
        try:
            occ_ts = datetime.fromisoformat(occ.replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        if -_LIVE_STATUS_LOOKAHEAD_SEC <= (now - occ_ts) <= _LIVE_STATUS_LOOKBACK_SEC:
            event_occ_ts[et] = occ_ts
    if not event_occ_ts:
        return {}

    close_ts_by_ticker = {}
    for m in markets:
        et, close_time = m.get("event_ticker"), m.get("close_time")
        if et in event_occ_ts and close_time and et not in close_ts_by_ticker:
            try:
                close_ts_by_ticker[et] = datetime.fromisoformat(close_time.replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass

    result = {}
    to_poll = []
    for et in event_occ_ts:
        cached = cache.get(et)
        if cached and cached["status"] in _LIVE_STATUS_TERMINAL:
            result[et] = cached["status"]
            continue
        close_ts = close_ts_by_ticker.get(et)
        if close_ts and now > close_ts:
            cache[et] = {"status": "finished", "checked_at": now}
            result[et] = "finished"
            continue
        if cached:
            result[et] = cached["status"]
            if (now - cached["checked_at"]) < _LIVE_STATUS_REPOLL_SEC:
                continue  # recently confirmed, not due for a re-check yet
        to_poll.append(et)

    if not to_poll:
        return result

    to_poll.sort(key=lambda et: (cache.get(et) or {}).get("checked_at", 0.0))
    to_poll = to_poll[:_LIVE_STATUS_MAX_POLL_PER_TICK]

    milestone_results = await asyncio.gather(
        *(client.get_milestones_for_event(et) for et in to_poll), return_exceptions=True
    )
    # Batched (2026-08-16 API-doc audit finding B3.2, docs/kalshi/
    # get-live-data.md) - was N individual get_live_data() calls via
    # asyncio.gather, one per event with a milestone. Live-verified: 3
    # individual = 0.99s wall, 1 batched get_live_datas call = 0.02s wall.
    milestone_by_event = {}
    has_milestone = set()
    for et, ms_result in zip(to_poll, milestone_results):
        if isinstance(ms_result, list) and ms_result:
            ms = ms_result[0]
            if ms.get("id") and ms.get("type"):
                has_milestone.add(et)
                milestone_by_event[et] = ms["id"]

    confirmed = {}
    if milestone_by_event:
        live_datas = await client.get_live_datas(list(milestone_by_event.values()))
        for et, ms_id in milestone_by_event.items():
            ld = live_datas.get(ms_id)
            if not ld:
                continue
            details = ld.get("details") or {}
            status = details.get("widget_status")
            if status:
                confirmed[et] = status
            # Real score/quarter/clock/down-distance/last_play (2026-08-16
            # audit finding B2, docs/kalshi/get-live-data.md) - the exact
            # same get_live_datas call above already fetches this full
            # payload; previously only widget_status was ever read out of
            # it. Pure value-add at zero extra API cost: surfaced here so
            # the dashboard can show real in-game state alongside a
            # watchlisted market, not just a live/finished label.
            if details:
                state["live_game_state"][et] = {"details": details, "updated_at": now}
                # Persist it too (2026-08-17). The line above has been
                # parsing this correctly since 08-16, but into an in-memory
                # dict that dies with the process - so score/period/clock
                # history has been arriving and evaporating every restart.
                # Storing it costs zero extra API calls (this payload is
                # already fetched for widget_status) and is what makes
                # questions like "did this whale print land right after a
                # scoring play" answerable later. Deduplicated on real state
                # change, so a finished game polled for hours writes once.
                game_state.record(et, details, sport=_sport_for_event(
                    state["event_titles"].get(et) or {}))

    # Schedule fallback, direct request: "otherwise use the schedule and
    # its previous live status to operate" - but a direct correction right
    # after: "just because a market is open doesn't mean it's live like
    # sports or mentions or award shows - be careful about how you infer
    # when you can't find live status." A market having no registered
    # milestone at all isn't the same situation as one that has a milestone
    # but whose live-data confirmation didn't come back this tick - the
    # first case likely means this market isn't the kind of discrete,
    # clocked, real-world event this system can meaningfully call "live" in
    # the first place (a mention/award-show market can stay open around its
    # occurrence_datetime with no real in-progress state the way a game
    # clock has), and guessing "live" there would be fabricating a status
    # Kalshi never actually confirmed. So the fallback only applies to
    # events Kalshi *has* confirmed are milestone-tracked - real games this
    # system already knows have a genuine live/in-progress state - and
    # simply couldn't get a fresh widget_status for on this particular
    # tick. Anything with no milestone at all is left out of the result
    # entirely (not cached, not "none", not "live" - genuinely unknown)
    # rather than guessed at either way.
    for et in to_poll:
        if et in confirmed:
            status, source = confirmed[et], "milestone"
        elif et in has_milestone:
            status = "none" if now < event_occ_ts[et] else "live"
            source = "schedule"
        else:
            continue  # not a milestone-tracked event type - no basis to infer anything
        cache[et] = {"status": status, "checked_at": now, "source": source}
        result[et] = status
    return result


_POSITION_FIELDS = (
    "ticker", "position_fp", "market_exposure_dollars", "realized_pnl_dollars",
    # fees_paid_dollars/total_traded_dollars/last_updated_ts added after a
    # direct data-usage review found them: real fields (confirmed against a
    # real connected account), fetched-for-free on every get_positions()
    # call, previously trimmed here and never reaching the frontend at all —
    # a real position's fees directly eat into its P&L, so showing exposure
    # without what it cost to get there was an incomplete picture on a
    # panel that's specifically about real money.
    "fees_paid_dollars", "total_traded_dollars", "last_updated_ts",
)
# Kalshi's own EventPosition has no price field either (same as
# MarketPosition - confirmed against the SDK's models), so this doesn't need
# a price join the way market_positions does below - it's purely the
# real parent-event grouping/exposure rollup, previously fetched every tick
# and then dropped entirely before /api/state (direct report: real
# positions on child markets of the same event rendered as unrelated flat
# rows with no grouping at all).
_EVENT_POSITION_FIELDS = (
    "event_ticker", "total_cost_dollars", "total_cost_shares_fp",
    "event_exposure_dollars", "realized_pnl_dollars", "fees_paid_dollars",
)
_FILL_FIELDS = (
    "ticker", "market_ticker", "side", "action", "count_fp", "yes_price_dollars", "no_price_dollars",
    # created_time/fee_cost/is_taker/fill_id/order_id added for the same
    # reason as _POSITION_FIELDS above — most notably created_time: the real
    # Trade Log had no timestamp at all before this, so real fills couldn't
    # be read in time order or checked for recency.
    "created_time", "fee_cost", "is_taker", "fill_id", "order_id",
)
# Same trim idea as _slim_market: keep only what renderRealPositions/
# renderRealFills actually read (field names confirmed against a real
# connected account, not guessed — see the comment above renderRealPositions
# for why that mattered). event_positions/cursor/... are real fields, just
# not currently rendered anywhere. Keeps fills well under the ~13.4KB a full
# 25-fill page would otherwise cost, the single largest piece of /api/state's
# payload, while still keeping every field the UI actually shows.

# Real order history - GetOrders' full real fields confirmed against a live
# connected account (docstring in kalshi_account_client.py has the full
# example). Unlike positions/fills, order history isn't part of the main
# poll loop at all (see GET /api/account/orders below) - it's on-demand,
# same "paginated, fetched only when that panel is actually open" pattern
# as GET /api/signals/history and GET /api/trading-history, not something
# every 15s tick needs to pull.
_ORDER_FIELDS = (
    "order_id", "ticker", "side", "action", "type", "status",
    "yes_price_dollars", "no_price_dollars", "fill_count_fp", "remaining_count_fp", "initial_count_fp",
    "taker_fees_dollars", "maker_fees_dollars", "created_time", "last_update_time", "client_order_id",
)


def _slim_order(o: dict) -> dict:
    return {k: o.get(k) for k in _ORDER_FIELDS}



def _slim_position(p: dict) -> dict:
    return {k: p.get(k) for k in _POSITION_FIELDS}


def _slim_event_position(p: dict) -> dict:
    return {k: p.get(k) for k in _EVENT_POSITION_FIELDS}


def _join_real_position_prices(account_snapshot: dict, latest_prices: dict) -> None:
    """Kalshi's real MarketPosition has no price field at all (confirmed
    against the SDK's models) - this app has never joined a real position
    against its market's current price, for any real position, anywhere
    (direct report). Attached backend-side, from the same latest_prices
    every other price display already reads, rather than re-derived
    client-side at the render call site - CLAUDE.md's own documented bug
    pattern for displayed financial figures. Real position tickers are
    already force-fetched into `markets` every tick
    (_real_account_position_tickers, phase 60/61), so this should always
    resolve; None (not a fabricated default) if a ticker genuinely isn't
    there yet. Mutates each position dict in place."""
    real_positions = ((account_snapshot.get("positions") or {}).get("market_positions")) or []
    for p in real_positions:
        p["current_yes_price_dollars"] = latest_prices.get(p.get("ticker"))


def _slim_fill(f: dict) -> dict:
    return {k: f.get(k) for k in _FILL_FIELDS}


_ACCOUNT_SNAPSHOT_REFRESH_SEC = 20  # 2026-08-15 "no stone unturned" API audit - this used to fetch
# balance/positions/fills via 3 uncached REST calls on literally every tick, unconditionally, the
# one real REST-call site this whole pass hadn't touched yet. A real Kalshi WS channel exists for
# this (market_positions/fill, confirmed in docs/kalshi/websocket-connection.md's channel list) but
# fill events specifically cannot be observed or verified against real data right now - real trading
# is off (kalshi_account.trading_enabled: false, same P0 safety gate as always), so no order can ever
# fill, so there is no live message to confirm this app's parsing of that channel's real shape
# against. Shipping unverified parsing for real-account financial data is exactly the class of risk
# CLAUDE.md's safety posture warns against - a wrong field name would silently misreport real
# positions, not just crash loudly. A real interval cache is the safe, immediately-effective
# version of the same fix instead: same three REST calls, same data, just not re-fetched more often
# than something could plausibly have changed. See docs/next-steps-2026-08-15-pt2.md for the
# WS-channel design, deferred pending either a real fill to verify parsing against or explicit
# sign-off to ship best-effort parsing with a REST reconciliation safety net.
_account_snapshot_cache: dict = {"fetched_at": 0.0, "snapshot": None}


async def _fetch_account_snapshot(cfg: dict) -> dict:
    account.trading_enabled = cfg["kalshi_account"]["trading_enabled"]
    if not account.enabled:
        return {
            "connected": False, "balance": None, "positions": None, "fills": None,
            "error": account.status["error"], "trading_enabled": account.trading_enabled,
        }
    now_ts = time.time()
    cached = _account_snapshot_cache["snapshot"]
    if cached is not None and (now_ts - _account_snapshot_cache["fetched_at"]) < _ACCOUNT_SNAPSHOT_REFRESH_SEC:
        return {**cached, "trading_enabled": account.trading_enabled}
    try:
        # balance, positions, and fills are independent reads — fetch all three
        # at once instead of one after another.
        balance, positions, fills = await asyncio.gather(
            account.get_balance(), account.get_positions(), account.get_fills(limit=50)
        )
        positions = {
            "market_positions": [_slim_position(p) for p in (positions.get("market_positions") or [])],
            "event_positions": [_slim_event_position(p) for p in (positions.get("event_positions") or [])],
        }
        fills = {"fills": [_slim_fill(f) for f in (fills.get("fills") or [])]}
        snapshot = {
            "connected": True, "balance": balance, "positions": positions, "fills": fills,
            "error": None, "trading_enabled": account.trading_enabled,
        }
        _account_snapshot_cache["snapshot"] = snapshot
        _account_snapshot_cache["fetched_at"] = now_ts
        return snapshot
    except Exception as e:
        return {
            "connected": True, "balance": None, "positions": None, "fills": None,
            "error": str(e), "trading_enabled": account.trading_enabled,
        }


async def _fetch_exchange_status(client: KalshiClient) -> dict | None:
    # A transient hiccup here shouldn't take down the whole poll tick the way
    # a markets/account failure would (nothing downstream depends on it) —
    # swallow and keep the last known status rather than clearing it.
    try:
        return await client.get_exchange_status()
    except Exception:
        return None


async def _fetch_event_titles(client: KalshiClient, markets: list[dict]) -> dict:
    """Fetches every not-yet-cached event's own title/sub_title/category -
    not just events with sibling markets (an earlier, narrower version of
    this only fetched for multi-outcome groups; broadened because this data
    is also what answers "what sport, who vs who" for a *single* market's
    display, not just grouping). Cached in state["event_titles"]
    (accumulates, capped like market_titles) so an event only needs
    fetching once even as the watchlist rotates - a typical watchlist
    (8-20 markets) means at most that many new lookups on a given tick, and
    usually zero once the cache is warm.

    event.get("subtitle") looked plausible but was wrong - the real field
    is sub_title (confirmed directly against a live event: "SD vs AZ (Aug
    6)" only came back under that key), so this was silently returning None
    for every event until caught.

    Also re-fetches an already-cached event if its cached entry has no
    mutually_exclusive value yet (None) - real Kalshi events always return
    a real True/False for this field, so a cached None uniquely means "this
    entry predates that field being extracted here," not a genuine value.
    Without this, every event cached before mutually_exclusive was added
    would stay permanently None forever (this function only ever fetches
    what's "not yet cached" - confirmed live: every entry already in
    data/title_cache.db showed null for it after the field was added,
    since none of them had ever been "not yet cached" again). This
    self-heals over the next few ticks as each event naturally reappears in
    the watchlist, no one-time backfill script or DB wipe needed."""
    required_event_fields = {
        "mutually_exclusive", "series_ticker", "available_on_brokers",
        "product_metadata", "settlement_sources", "strike_date",
        "strike_period", "fee_type_override", "fee_multiplier_override",
        "last_updated_ts",
    }
    to_fetch = [
        m["event_ticker"] for m in markets
        if m.get("event_ticker") and (
            m["event_ticker"] not in state["event_titles"]
            or state["event_titles"][m["event_ticker"]].get("mutually_exclusive") is None
            or any(
                field not in state["event_titles"][m["event_ticker"]]
                for field in required_event_fields
            )
        )
    ]
    to_fetch = list(dict.fromkeys(to_fetch))  # de-dupe, preserve order
    if not to_fetch:
        return {}
    # Batched (2026-08-16 API-doc audit finding B3.1, docs/kalshi/
    # get-events.md) - was N individual get_event() calls via
    # asyncio.gather, one per not-yet-cached event ticker every tick. Live-
    # verified: 3 individual = 0.36s wall, 1 batched call = 0.02s wall, same
    # events returned, no misses. A ticker Kalshi doesn't return (renamed,
    # removed) just doesn't appear in `by_ticker` below and is silently
    # skipped this tick, same as a failed get_event() used to be.
    try:
        events = await client.get_events(to_fetch)
    except Exception:
        events = []
    by_ticker = {e["event_ticker"]: e for e in events if e.get("event_ticker")}
    fetched = {}
    for et in to_fetch:
        event = by_ticker.get(et)
        if event is not None:
            fetched[et] = {
                "title": event.get("title") or et,
                "sub_title": event.get("sub_title"),
                "category": event.get("category"),
                "series_ticker": event.get("series_ticker"),
                "available_on_brokers": event.get("available_on_brokers"),
                "collateral_return_type": event.get("collateral_return_type"),
                # Kalshi's own real field for "exactly one of this event's
                # sibling markets resolves YES" - already present in every
                # get_event() response above, previously discarded. Lets
                # the dashboard tell a genuine 2-outcome inversion pair
                # ("Toronto vs Philadelphia Winner" - the two sibling
                # markets are the same information mirrored, confirmed
                # live: their yes_bid prices sum to ~1.0) apart from
                # sibling markets that are independent props sharing an
                # event but NOT mutually exclusive (e.g. "Max Scherzer 15+
                # outs" and "Aaron Nola 18+ outs") or a genuine multi-way
                # market (e.g. "Wyndham Championship Winner", 60+ golfers,
                # also mutually_exclusive but with no simple pairwise
                # complement) - see eventGroupCardHTML in static/index.html.
                "mutually_exclusive": event.get("mutually_exclusive"),
                # product_metadata.competition/competition_scope - real
                # fields, same "already fetched here, previously discarded"
                # finding. Direct display value only (e.g. "Wyndham
                # Championship" shown on a golf pairing's event card) - NOT
                # used for grouping, since it's tournament-specific for golf
                # but sport-generic for esports ("Dota 2", shared by
                # unrelated matches, confirmed live) and so can't safely
                # replace series_of/round_robin_select's own grouping logic
                # (see ROADMAP.md's parent/child grouping work). Legitimately
                # absent for most non-competitor markets (politics,
                # economics) - unlike mutually_exclusive, a missing value
                # here is a real "this event has no competition," not a
                # backfill signal, so it isn't part of the re-fetch check
                # above.
                "competition": (event.get("product_metadata") or {}).get("competition"),
                "competition_scope": (event.get("product_metadata") or {}).get("competition_scope"),
                "product_metadata": event.get("product_metadata") or {},
                "settlement_sources": event.get("settlement_sources") or [],
                "strike_date": event.get("strike_date"),
                "strike_period": event.get("strike_period"),
                "fee_type_override": event.get("fee_type_override"),
                "fee_multiplier_override": event.get("fee_multiplier_override"),
                "last_updated_ts": event.get("last_updated_ts"),
            }
    return fetched


_EVENT_LIVE_DATA_REPOLL_SEC = 60  # Repoll-cached (2026-08-15 tick_duration
# fix) - same fix, same root cause as _MILESTONE_REPOLL_SEC above: this
# called get_event_live_data() for every unique event on the watchlist,
# every tick, forever, unconditionally. Real live game-state data can
# change fast during an actual live event, so this stays much shorter than
# _LIVE_STATUS_REPOLL_SEC's 5 minutes, but per-tick (~every 15s) was never
# the right cadence either.

_EVENT_LIVE_DATA_EXCLUDED_CATEGORIES = {"Sports"}  # 2026-08-16 API-doc audit
# finding B2 (docs/kalshi/get-event-live-data.md, docs/next-steps-2026-08-15-
# pt3.md): this endpoint is event-ticker-keyed and documented/confirmed to
# serve "crypto price charts, commodity price timeseries, weather
# observations" - live-verified against 3 real crypto tickers (KXBTC15M-*),
# which returned real BTC candlestick data. Sports is the one category
# confirmed NOT served here: every real sports ticker on the watchlist
# 404s from this endpoint 100% of the time - not a bug, structurally the
# wrong data source (the real source for sports live state is the
# milestone-keyed get_live_data(s), which _fetch_live_status and
# propagate_milestone_winners already call - see state["live_game_state"]).
# Corrects an earlier, less careful same-day comment on this constant that
# guessed crypto didn't work here either - it does; only Sports is excluded,
# and only because it's actually confirmed wasteful, not guessed at. Any
# other category without live confirmation either way is deliberately left
# in rather than excluded on a guess, same "don't fabricate" idiom
# _fetch_live_status's own schedule-fallback already follows.


async def _fetch_event_live_data(client: KalshiClient, markets: list[dict]) -> dict:
    event_tickers = list(dict.fromkeys(
        m["event_ticker"] for m in markets if m.get("event_ticker")
    ))
    if not event_tickers:
        return {}
    cache = state["event_live_data_cache"]
    now = time.time()
    # A brand-new event's category isn't known yet on the very first tick it
    # appears (_fetch_event_titles runs concurrently with this function, not
    # before it - state["event_titles"] only reflects prior ticks' fetches
    # during this call). Category-unknown events are polled anyway rather
    # than guess-excluded; the exclusion self-corrects from the next tick
    # once event_titles has caught up, same self-healing shape
    # _fetch_event_titles's own mutually_exclusive backfill already uses.
    to_poll = [
        et for et in event_tickers
        if (et not in cache or (now - cache[et]["checked_at"]) >= _EVENT_LIVE_DATA_REPOLL_SEC)
        and (state["event_titles"].get(et) or {}).get("category") not in _EVENT_LIVE_DATA_EXCLUDED_CATEGORIES
    ]
    if to_poll:
        results = await asyncio.gather(
            *(client.get_event_live_data(et) for et in to_poll), return_exceptions=True
        )
        for et, result in zip(to_poll, results):
            live_data = None
            if isinstance(result, dict):
                ld = result.get("live_data") or {}
                if ld:
                    # **ld first (2026-08-17): this used to keep five named
                    # keys and drop the rest of the live-data response.
                    # Same instruction, same reason as
                    # KalshiTradeWebSocketClient.normalize_trade - a field
                    # Kalshi adds should arrive intact rather than be
                    # discarded before anything can notice it exists. The
                    # explicit keys still win, so `details` is still
                    # guaranteed to be a dict and `range_options` a list for
                    # every existing consumer.
                    live_data = {
                        **ld,
                        "type": ld.get("type"),
                        "details": ld.get("details") or {},
                        "is_historical": ld.get("is_historical"),
                        "default_range": ld.get("default_range"),
                        "range_options": ld.get("range_options") or [],
                    }
            cache[et] = {"data": live_data, "checked_at": now}
            # Persist it (2026-08-17). This is the live-data path that is
            # actually populated in practice - the milestone-driven one in
            # _fetch_live_status only fires for events Kalshi tracks a
            # milestone for, and was measured empty while THIS held six live
            # entries. For crypto events the payload carries OHLC
            # candlesticks and an underlying price timeseries; for games it
            # carries score/period/clock. Both are fetched every tick
            # already and both were living only in memory. Rate-limited and
            # deduplicated inside game_state.record.
            if live_data and (live_data.get("details") or {}):
                game_state.record(
                    et, live_data["details"],
                    sport=_sport_for_event(state["event_titles"].get(et) or {}),
                    event_type=live_data.get("type"),
                )
    return {et: cache[et]["data"] for et in event_tickers if cache.get(et, {}).get("data") is not None}


_SIGNAL_RESOLUTION_CHECK_INTERVAL_SEC = 30  # see _maybe_check_signal_resolutions' own docstring


def _maybe_check_signal_resolutions(cfg: dict) -> None:
    """Triggers _check_signal_resolutions as an independent background
    task on its own interval, decoupled from the main tick entirely - same
    pattern as discovery/catalog-scan's own _maybe_* triggers (2026-08-15
    "no stone unturned" API audit, direct instruction: "maximum efficiency
    and maximum speed for position management and whale watching").
    Previously this ran inline inside the main tick's gather with only its
    OWN interval gate deciding whether to do real work - a no-op on most
    ticks, but on the roughly-1-in-5 tick where it WAS due, that tick
    still blocked on however long 10 concurrent get_market() calls took
    under the real rate limit (services/signal_log.py's own last_checked_at
    fix made this function do real, sustained work for the first time -
    the original trigger for this whole incident response). Fully
    backgrounded now for the same reason discovery was: a bulk, non-urgent
    backlog-clearing task should never be able to slow down the fast,
    time-sensitive path even occasionally."""
    check_state = state["signal_resolution_check"]
    now_ts = time.time()
    due = now_ts - check_state["last_checked_at"] > _SIGNAL_RESOLUTION_CHECK_INTERVAL_SEC
    if due and not check_state["checking"]:
        check_state["checking"] = True
        check_state["last_checked_at"] = now_ts
        check_state["task"] = asyncio.create_task(_check_signal_resolutions_background(cfg))


async def _check_signal_resolutions_background(cfg: dict) -> None:
    """Owns its own KalshiClient - see _refresh_discovery_cache's
    identical reasoning (the calling tick's own client closes at the end
    of that same tick, well before an independent background task would
    finish)."""
    check_state = state["signal_resolution_check"]
    client = KalshiClient(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])
    try:
        await _check_signal_resolutions(client)
    except Exception as exc:
        print(f"[signal_resolution] background check failed, will retry next cycle: {exc!r}")
    finally:
        check_state["checking"] = False
        await client.close()


_SIGNAL_RESOLUTION_BATCH_SIZE = 200  # 2026-08-16 API-doc audit finding: real,
# live backlog confirmed via data/signal_log.db - 26,903 of 32,480 logged
# signals unresolved, 26,824 currently due for a check, against a
# limit=10-per-30s-check pace (the batch size this constant replaces) that
# would take ~22h just for one pass even if every single one resolved on
# first check. Root cause wasn't the interval, it was checking markets
# one-at-a-time: get_markets(tickers=...) (docs/kalshi/get-markets.md,
# confirmed via the installed SDK's own get_markets signature - a real,
# documented batch filter this app never used) turns what used to be N
# individual get_market() calls, each its own local rate-limiter acquire(),
# into ceil(N/50) batched calls (see KalshiClient.get_markets_by_tickers's
# own _MARKETS_BY_TICKERS_BATCH_SIZE=50, sized to Kalshi's real 600-token
# burst ceiling). 200/check at the existing 30s cadence drains this backlog
# in about an hour instead of a full day, at only 4 local acquire() calls
# per check instead of 200.


async def _check_signal_resolutions(client: KalshiClient):
    """Pick a batch of old-enough unresolved logged signals and see if their
    markets have settled yet. Interval-gating and background-task
    scheduling both live in _maybe_check_signal_resolutions above now -
    this function just does the work when asked. See
    _SIGNAL_RESOLUTION_BATCH_SIZE above for why the batch is this large and
    why one get_markets_by_tickers call replaces what used to be N
    individual get_market() calls."""
    items = signal_log.unresolved_batch(limit=_SIGNAL_RESOLUTION_BATCH_SIZE, older_than_sec=600)
    if not items:
        return
    markets = await client.get_markets_by_tickers([item["ticker"] for item in items])
    for item in items:
        market = markets.get(item["ticker"])
        if not market:
            continue  # market may be gone/renamed, or not yet settled — leave unresolved, retry next time
        result = (market.get("result") or "").strip().lower()
        if result in ("yes", "no"):
            signal_log.mark_resolved(item["id"], correct=(result == item["side"]))


def _shadow_reference_bankroll(account_snapshot: dict, cfg: dict) -> tuple[float, str]:
    """What shadow mode treats as "your real bankroll" for position sizing.
    Prefers the real connected account's balance (Kalshi reports it in
    cents, same field the dashboard's account bar divides by 100 to
    display); falls back to config's starting_bankroll, clearly labeled as
    a fallback, so shadow mode is still meaningfully testable without a
    real Kalshi account connected."""
    if account_snapshot.get("connected"):
        bal = account_snapshot.get("balance") or {}
        cents = bal.get("balance") if isinstance(bal, dict) else None
        if cents is not None:
            try:
                return float(cents) / 100.0, "real_account"
            except (TypeError, ValueError):
                pass
    return float(cfg["risk"]["starting_bankroll"]), "configured_starting_bankroll (no real account connected)"


# Tickers with a request currently in flight through _run_market_analyst_for_
# ticker - closes a real race the cooldown check alone can't: last_analyzed_at
# only gets recorded at the very end (after two real awaits: get_market, then
# the LLM call itself), so two near-simultaneous requests for the same ticker
# - e.g. close-and-reopen the market detail modal, click Analyze again before
# the first click's response has landed - would both read the same pre-commit
# cooldown state, both pass, and both spend a real API call. In-process only
# (this app runs as one uvicorn worker, not a distributed fleet), cleared in
# a finally block so a crash or an early-return can't leak a ticker stuck
# "in flight" forever.
_analyzing_tickers: set[str] = set()


async def _run_market_analyst_for_ticker(client: KalshiClient, cfg: dict, ticker: str) -> dict:
    """On-demand, single-ticker orchestration for services/market_analyst_agent.py
    - direct request (2026-08-09): switched from an automatic per-tick
    background scan to a button-triggered "analyze this one market right
    now" flow, since this is the first thing in this app that spends real
    money per call (every other strategy is zero-marginal-cost arithmetic),
    so it should be a deliberate human action, not ambient background spend
    - same "prove it, then promote it" philosophy already used for
    advisory_engine's manual-apply-with-audit-trail and
    confidence_calibration's read-only-until-enough-data design.

    Returns {"ok": True, **result} on a fresh analysis, or {"ok": False,
    "reason": <str>} if gated (disabled, no key, already in flight, still in
    cooldown, or the LLM call itself failed/declined) - always a clean dict,
    never raises, so the route can turn this straight into a JSON response."""
    ma_cfg = cfg.get("market_analyst") or {}
    if not ma_cfg.get("enabled"):
        return {"ok": False, "reason": "Market Analyst is disabled — enable it in Config → Market Analyst (AI)."}
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"ok": False, "reason": "No ANTHROPIC_API_KEY configured in .env."}

    if ticker in _analyzing_tickers:
        return {"ok": False, "reason": "Already analyzing this market — try again in a moment."}

    cooldown = ma_cfg.get("reanalyze_cooldown_sec", 1800)
    now = time.time()
    last = market_analyst_agent.last_analyzed_at(ticker)
    if last is not None and (now - last) < cooldown:
        wait_sec = int(cooldown - (now - last))
        return {"ok": False, "reason": f"Already analyzed recently — try again in {wait_sec}s."}

    _analyzing_tickers.add(ticker)
    try:
        return await _analyze_market_uncached(client, ma_cfg, cfg, ticker, now, api_key)
    finally:
        _analyzing_tickers.discard(ticker)


async def _analyze_market_uncached(
    client: KalshiClient, ma_cfg: dict, cfg: dict, ticker: str, now: float, api_key: str,
) -> dict:
    """The real fetch-and-analyze body, split out of _run_market_analyst_for_
    ticker so the in-flight guard in that function wraps every exit path
    (including early returns below) via one try/finally, rather than each
    needing its own cleanup."""
    try:
        market_detail = await client.get_market(ticker)
    except Exception as e:
        return {"ok": False, "reason": f"Could not fetch market data: {e}"}
    event_ticker = market_detail.get("event_ticker")
    if event_ticker:
        try:
            ev = await client.get_event(event_ticker)
            market_detail = {**market_detail, "category": (ev.get("event") or {}).get("category")}
        except Exception:
            pass  # category is a bonus for the prompt, not required

    # Same real accumulated-context bundle a human sees on the dashboard
    # (services/ml_feed.py's whole "work WITH, not replace" point) - grounds
    # the model's judgment in this app's own track record, not just general
    # world knowledge.
    adv_cfg = cfg["advisory"]
    all_rows = trade_analytics.build_trade_history([t.to_dict() for t in broker.trade_log])
    recommendations = {"recommendations": [], "gated_reason": "advisory engine is disabled"}
    if adv_cfg["enabled"]:
        current_fp = config_performance.fingerprint(cfg)
        variants = {v["fingerprint"]: v for v in config_performance.all_variants()}
        market_rows = trade_analytics.build_trade_history([t.to_dict() for t in market_broker.trade_log])
        recommendations = advisory_engine.generate_recommendations(
            all_rows, cfg, current_fp, variants, adv_cfg["min_resolved_trades_per_variant"], market_rows=market_rows,
            gate_summaries=candidate_log.gate_summary(),
            last_applied_by_path=config_performance.all_last_applied_by_path(),
            series_evaluator_rows=_series_evaluator_overview_with_crosscheck(cfg),
            category_rows=regime_analytics.by_category(all_rows),
        )
    snapshot = ml_feed.build_context_snapshot(
        cfg=cfg,
        portfolio=broker.state(state["latest_prices"]),
        market_snapshot={"markets": state["markets"], "latest_prices": state["latest_prices"]},
        trade_history_rows=all_rows,
        whale_track_record=signal_log.stats(),
        advisory=recommendations,
    )

    result = await market_analyst_agent.analyze_market(
        market_detail, snapshot, ma_cfg.get("model", "claude-sonnet-5"), api_key,
    )
    if result is None:
        return {"ok": False, "reason": "The model call failed or declined to answer — see server logs."}
    market_price = float(market_detail.get("yes_bid_dollars") or market_detail.get("yes_ask_dollars") or 0.5)
    market_analyst_agent.record_analysis(
        ticker=ticker, series=signal_log.series_of(ticker), market_price=market_price,
        estimated_probability=result["estimated_probability"], llm_confidence=result["confidence"],
        reasoning=result["reasoning"], model=ma_cfg.get("model", "claude-sonnet-5"), analyzed_at=now,
    )
    _bump_generation()
    return {"ok": True, **result, "market_price": market_price}


# Series currently being analyzed - same in-flight-race guard as
# _analyzing_tickers above, kept as its own set rather than sharing one:
# tickers and series are different namespaces (a series is a ticker prefix,
# e.g. "KXPGATOUR" vs. a real ticker like "KXPGATOUR-26AUG10-DEF"), so
# reusing the same set risks a false "already in flight" collision if a
# series name and a real ticker ever happened to be identical strings.
_analyzing_series: set[str] = set()


def _build_series_context(cfg: dict, series: str) -> dict:
    """Assembles everything services/market_analyst_agent.build_series_
    prompt() needs (Item 3B) - real whale-signal stats scoped to this
    series (signal_log.series_stats reused as-is: series_of() on an
    already-bare series string is a no-op, so this works without a
    series-specific variant of that function), this series' own closed-
    trade summary (trade_analytics.compute_summary on rows filtered to
    tickers under this series), its current excluded_series membership +
    per-series notional override, and its series_evaluator verdict if it's
    ever been evaluated."""
    all_rows = trade_analytics.build_trade_history([t.to_dict() for t in broker.trade_log])
    series_rows = [r for r in all_rows if signal_log.series_of(r["ticker"]) == series]
    evaluator_row = next((r for r in series_evaluator.overview() if r["series"] == series), None)
    strat_cfg = cfg.get("strategy") or {}
    whale_cfg = cfg.get("whale_watcher_kalshi") or {}
    return {
        "series": series,
        "whale_stats": signal_log.series_stats(series, days=30),
        "trade_summary": trade_analytics.compute_summary(series_rows),
        "currently_excluded": series in (strat_cfg.get("excluded_series") or []),
        "min_notional_override": (whale_cfg.get("min_notional_usd_by_series") or {}).get(series),
        "evaluator_status": evaluator_row,
    }


def _series_suggestions_from_raw(cfg: dict, series: str, raw_suggestions: list[dict]) -> list[dict]:
    """Converts services/market_analyst_agent.analyze_series()'s raw
    {"action": "exclude"|"include", "rationale"} output into this app's
    unified suggestion shape ({config_path, current_value, suggested_value,
    id, rationale}, same as services/advisory_engine.py's rule-based
    suggestions) - done here, not in market_analyst_agent.py, since it
    needs the live config to compute the actual before/after
    strategy.excluded_series list. A no-op action (e.g. the model suggests
    "exclude" on a series that's already excluded) is silently dropped -
    nothing to actually apply."""
    current_list = list((cfg.get("strategy") or {}).get("excluded_series") or [])
    out = []
    for raw in raw_suggestions:
        action = raw.get("action")
        if action == "exclude" and series not in current_list:
            suggested_list = sorted(current_list + [series])
        elif action == "include" and series in current_list:
            suggested_list = [s for s in current_list if s != series]
        else:
            continue  # already in the suggested state, or an action we don't recognize - nothing to apply
        out.append({
            "id": advisory_engine.rec_id("strategy.excluded_series", suggested_list, 0),
            "config_path": "strategy.excluded_series",
            "current_value": current_list,
            "suggested_value": suggested_list,
            "rationale": raw.get("rationale") or "",
            "series": series,
            "source": "series-analyst",
        })
    return out


async def _run_series_analysis(cfg: dict, series: str) -> dict:
    """On-demand per-series orchestration (Item 3B) - same gating shape as
    _run_market_analyst_for_ticker (disabled/no-key/in-flight/cooldown all
    return a clean {"ok": False, "reason": ...} rather than raising), reuses
    market_analyst.reanalyze_cooldown_sec rather than inventing a second
    knob for a mode that spends the exact same kind of API call."""
    ma_cfg = cfg.get("market_analyst") or {}
    if not ma_cfg.get("enabled"):
        return {"ok": False, "reason": "Market Analyst is disabled — enable it in Config → Market Analyst (AI)."}
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"ok": False, "reason": "No ANTHROPIC_API_KEY configured in .env."}
    if series in _analyzing_series:
        return {"ok": False, "reason": "Already analyzing this series — try again in a moment."}

    cooldown = ma_cfg.get("reanalyze_cooldown_sec", 1800)
    now = time.time()
    last = market_analyst_agent.last_series_analyzed_at(series)
    if last is not None and (now - last) < cooldown:
        wait_sec = int(cooldown - (now - last))
        return {"ok": False, "reason": f"Already analyzed recently — try again in {wait_sec}s."}

    _analyzing_series.add(series)
    try:
        series_ctx = _build_series_context(cfg, series)
        model = ma_cfg.get("model", "claude-sonnet-5")
        result = await market_analyst_agent.analyze_series(series_ctx, model, api_key)
        if result is None:
            return {"ok": False, "reason": "The model call failed or declined to answer — see server logs."}
        suggestions = _series_suggestions_from_raw(cfg, series, result["suggestions"])
        # Same "declining sticks until the evidence genuinely changes" as
        # the rule-based Advisory path - filtered before persisting, not
        # just before display, so a re-analysis with identical output
        # doesn't re-mint the same already-declined id into a fresh row.
        declined = suggestion_decisions.declined_ids()
        suggestions = [s for s in suggestions if s["id"] not in declined]
        analysis_id = market_analyst_agent.record_series_analysis(
            series=series, summary=result["summary"], suggestions=suggestions, model=model, analyzed_at=now,
        )
        _bump_generation()
        return {"ok": True, "analysis_id": analysis_id, "series": series, "summary": result["summary"], "suggestions": suggestions}
    finally:
        _analyzing_series.discard(series)


# "Feed the Analyst" full-spectrum scan (Item 3C, 2026-08-10) - only one
# subject (the whole platform), so a plain bool is enough for the in-flight
# guard, unlike the ticker/series sets above.
_full_spectrum_analyzing = False

# The two config paths already protected from generic manual edits (see
# update_config()'s own guards below) - the full-spectrum agent gets the
# exact same protection, since its suggestions can otherwise touch ANY
# config field, a materially wider blast radius than the fixed single-field
# scope 3B's per-series mode was deliberately limited to.
_PROTECTED_CONFIG_PATHS = {
    "kalshi_account.trading_enabled", "advisory.auto_apply_enabled",
    # Same protection, same reasoning (2026-08-10, direct request to add a
    # calibration auto-apply path) - a typed-confirmation-gated route is
    # the only way to flip this on, not a plain Config-tab checkbox.
    "confidence_calibration.auto_apply_enabled",
}

# trade_analytics.confidence_label()'s three tiers, ranked so
# advisory.auto_apply_min_confidence (a config string) can be compared
# against a real recommendation's own confidence_label with a single >=.
_CONFIDENCE_RANK = {"low": 0, "moderate": 1, "higher": 2}


def _config_value_at_path(cfg: dict, config_path: str):
    """Reads a "section.field" path out of a live config dict - the read
    side of the same section/field split every apply route already does
    for writes (config_store.update({section: {field: value}})). Used to
    catch a stale suggestion: an LLM-derived suggestion (series/full-
    spectrum analyst) is looked up from what was persisted at analysis
    time, not recomputed fresh the way a rule-based Advisory recommendation
    is - if the live config's actual current value has since drifted from
    what the suggestion assumed (a manual edit, an auto-apply, or a second
    analysis elsewhere), blindly applying it would silently overwrite based
    on a stale premise and log a fabricated "before" value that was never
    actually live. Missing section/field reads as None, same as dict.get."""
    section, _, field = config_path.partition(".")
    return (cfg.get(section) or {}).get(field)


def _types_compatible(a, b) -> bool:
    """Loose type-compatibility check for a full-spectrum suggestion's
    value against the field's current one - int/float are interchangeable
    (a human editing the Config tab's number inputs doesn't distinguish
    them either), bool is checked strictly on both sides since Python's
    bool is technically an int subclass and a stray True/False landing in
    a numeric field would be a real, confusing config corruption, not a
    reasonable suggestion."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return True
    return type(a) is type(b)


def _build_full_spectrum_context(cfg: dict) -> dict:
    """Assembles services/market_analyst_agent.build_full_spectrum_prompt()'s
    input (Item 3C) - deliberately every value here is an aggregated
    rollup (compute_summary(), variant_summaries(), stats()), never raw
    per-trade/per-signal rows, so prompt size stays bounded regardless of
    how much history has accumulated (per the plan's own explicit
    "aggregated/summarized data, not raw per-trade rows" requirement)."""
    all_rows = trade_analytics.build_trade_history([t.to_dict() for t in broker.trade_log])
    market_rows = trade_analytics.build_trade_history([t.to_dict() for t in market_broker.trade_log])
    current_fp = config_performance.fingerprint(cfg)
    variants = {v["fingerprint"]: v for v in config_performance.all_variants()}
    adv_cfg = cfg.get("advisory") or {}
    gate_summaries = candidate_log.gate_summary()
    recommendations = advisory_engine.generate_recommendations(
        all_rows, cfg, current_fp, variants, adv_cfg.get("min_resolved_trades_per_variant", 30),
        market_rows=market_rows, gate_summaries=gate_summaries,
        last_applied_by_path=config_performance.all_last_applied_by_path(),
        series_evaluator_rows=_series_evaluator_overview_with_crosscheck(cfg),
        category_rows=regime_analytics.by_category(all_rows),
    )
    # Busiest 10 series by observed trade volume - a real, disclosed bound
    # (not exhaustive) so this section can't grow unbounded as more series
    # accumulate history over the app's life.
    busiest_series = sorted(
        series_evaluator.overview(), key=lambda r: r["trades_observed"], reverse=True,
    )[:10]
    per_series_whale = [
        {**signal_log.series_stats(r["series"], days=30), "evaluator_status": r["status"]}
        for r in busiest_series
    ]
    return {
        "config": cfg,
        "trade_summary": trade_analytics.compute_summary(all_rows),
        "market_strategy_summary": trade_analytics.compute_summary(market_rows),
        "whale_track_record": signal_log.stats(days=30),
        "advisory_recommendations": recommendations["recommendations"],
        "variant_summaries": advisory_engine.variant_summaries(all_rows),
        "recent_applied_changes": config_performance.recent_applied_changes(limit=20),
        "per_series_whale_breakdown": per_series_whale,
        "portfolio": broker.state(state["latest_prices"]),
        # Two real gaps closed here (2026-08-11 hardening pass, "web of
        # expertise" audit) - both datasets already existed and were
        # already aggregated rollups (no raw per-trade rows, consistent
        # with this function's own bound), just never assembled into this
        # context before. gate_summary() is the rejected-candidate
        # counterfactual data (what would have happened to a candidate a
        # gate turned down) - the model previously only ever saw the
        # accepted side of every threshold. by_category/by_hour_of_day are
        # the same segmentation the History tab's Regime panel already
        # shows a human, now available to the model too.
        "rejected_candidate_gates": gate_summaries,
        "regime_by_category": regime_analytics.by_category(all_rows),
        "regime_by_hour": regime_analytics.by_hour_of_day(all_rows),
    }


def _full_spectrum_suggestions_from_raw(cfg: dict, raw_suggestions: list[dict]) -> list[dict]:
    """Validates + converts the model's raw {"config_path",
    "suggested_value", "rationale"} output into this app's unified
    suggestion shape - unlike 3B's fixed-field conversion, this can't trust
    the model's config_path at all (it can name literally any field), so
    every suggestion here is checked against the LIVE config before being
    treated as real: the path must resolve to a section+field that already
    exists (never inventing a new one), must not be one of the two fields
    already protected from generic config edits, must actually differ from
    the current value, and must be a reasonably type-compatible value.
    Anything that fails any check is silently dropped, not surfaced as an
    error - matching how 3B's own no-op filtering works, an invalid
    suggestion just isn't a real suggestion."""
    out = []
    for raw in raw_suggestions:
        config_path = raw.get("config_path") or ""
        if config_path in _PROTECTED_CONFIG_PATHS:
            continue
        section, _, field = config_path.partition(".")
        if not section or not field:
            continue
        section_cfg = cfg.get(section)
        if not isinstance(section_cfg, dict) or field not in section_cfg:
            continue  # never touch a field that doesn't already exist in the live config
        current_value = section_cfg[field]
        suggested_value = raw.get("suggested_value")
        if suggested_value == current_value:
            continue
        if current_value is not None and not _types_compatible(current_value, suggested_value):
            continue
        out.append({
            "id": advisory_engine.rec_id(config_path, suggested_value, 0),
            "config_path": config_path,
            "current_value": current_value,
            "suggested_value": suggested_value,
            "rationale": raw.get("rationale") or "",
            "source": "full-spectrum-analyst",
        })
    return out


async def _run_full_spectrum_analysis(cfg: dict) -> dict:
    """On-demand full-platform orchestration (Item 3C) - same gating shape
    as _run_market_analyst_for_ticker/_run_series_analysis (disabled/no-key/
    in-flight/cooldown all return a clean {"ok": False, "reason": ...}
    rather than raising), reuses market_analyst.reanalyze_cooldown_sec
    rather than a third distinct knob."""
    global _full_spectrum_analyzing
    ma_cfg = cfg.get("market_analyst") or {}
    if not ma_cfg.get("enabled"):
        return {"ok": False, "reason": "Market Analyst is disabled — enable it in Config → Market Analyst (AI)."}
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"ok": False, "reason": "No ANTHROPIC_API_KEY configured in .env."}
    if _full_spectrum_analyzing:
        return {"ok": False, "reason": "A full-spectrum scan is already running — try again in a moment."}

    cooldown = ma_cfg.get("reanalyze_cooldown_sec", 1800)
    now = time.time()
    last = market_analyst_agent.last_full_spectrum_analyzed_at()
    if last is not None and (now - last) < cooldown:
        wait_sec = int(cooldown - (now - last))
        return {"ok": False, "reason": f"Already analyzed recently — try again in {wait_sec}s."}

    _full_spectrum_analyzing = True
    try:
        context = _build_full_spectrum_context(cfg)
        model = ma_cfg.get("model", "claude-sonnet-5")
        result = await market_analyst_agent.analyze_full_spectrum(context, model, api_key)
        if result is None:
            return {"ok": False, "reason": "The model call failed or declined to answer — see server logs."}
        suggestions = _full_spectrum_suggestions_from_raw(cfg, result["suggestions"])
        declined = suggestion_decisions.declined_ids()
        suggestions = [s for s in suggestions if s["id"] not in declined]
        analysis_id = market_analyst_agent.record_full_spectrum_analysis(
            summary=result["summary"], suggestions=suggestions, model=model, analyzed_at=now,
        )
        _bump_generation()
        return {"ok": True, "analysis_id": analysis_id, "summary": result["summary"], "suggestions": suggestions}
    finally:
        _full_spectrum_analyzing = False


async def trading_loop():
    while True:
        cfg = config_store.get()
        if not state["running"]:
            await asyncio.sleep(1)
            continue
        client = None
        # Real incident evidence this exists to catch next time (hardening-
        # and-accuracy-roadmap-2026-08-11.md Part 3 Item 2, closed
        # 2026-08-15): a genuine 502 was hit during the phase-97 cold-start
        # incident, but nothing in state could show a tick running long or
        # rate-limit hits piling up before it actually broke something.
        tick_start_wall = time.time()
        try:
            # Config-variant fingerprint (docs/advisory-engine-plan.md) -
            # computed once per tick, same cfg snapshot every trade decision
            # below is made against. record_variant is a cheap idempotent
            # upsert, safe to call every tick even when nothing changed.
            config_fp = config_performance.fingerprint(cfg)
            config_performance.record_variant(config_fp, cfg)

            # Renders a verdict for any series whose observation window
            # completed since last tick (see services/series_evaluator.py).
            # Gated behind series_evaluator.enabled, but trades are still
            # recorded unconditionally (services/whalewatchers/
            # kalshi_trade_tape.py) - if this gets turned on later, real
            # accumulated history is already there instead of a cold start.
            if cfg.get("series_evaluator", {}).get("enabled"):
                series_evaluator.evaluate_pending(cfg, time.time())

            client = KalshiClient(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])

            # Market data, account data, exchange status, and resolution-checking
            # don't depend on each other — fetch/run all four concurrently.
            # extra_tickers includes both brokers' open positions - a market-
            # native position that rotates out of the top-volume watchlist
            # needs price updates for its own exit checks just as much as a
            # whale-follow one does (see ROADMAP.md - this was a real bug
            # for the whale broker before extra_tickers existed at all).
            # Also folds in the *real* connected Kalshi account's own open
            # positions (from last tick's snapshot - this tick's fresh one is
            # fetched concurrently below, one-tick-old tickers are fine since
            # a real account's holdings rarely change tick to tick) - without
            # this, a real position's market never got fetched at all unless
            # it happened to already be on the watchlist, so
            # state["market_titles"] never had an entry for it and the real
            # Positions panel showed raw ticker IDs with zero title
            # resolution, a gap paper trading never had.
            #
            # Deliberately NOT fills here (real, confirmed-live regression,
            # direct report: "the market watchlist doesn't appear to be
            # updating/repopulating/removing closed markets and those with
            # insufficient volume") - state["markets"] IS the live watchlist
            # (wholesale-replaced every tick from exactly this fetch), not
            # just a title-resolution scratch space. get_fills(limit=50)
            # returns real historical trade records that can span days/weeks
            # since a market only rolls out of the last-50 window once
            # enough *newer* fills replace it - unlike a paper/real
            # *position*, which naturally drops out of this set the tick it
            # closes. Folding fills in here force-fed long-since-finalized,
            # zero-volume markets back into the live watchlist every single
            # tick for as long as their fill stayed in that window - exactly
            # the symptom reported. Fills' tickers still get title
            # resolution for the real Trade Log display via
            # _relevant_tickers()/state["market_titles"]'s own unbounded
            # accumulation - they just don't belong in the *live* watchlist
            # fetch.
            real_position_tickers = _real_account_position_tickers(state.get("account") or {})
            open_position_tickers = list(
                set(broker.positions.keys()) | set(market_broker.positions.keys()) | real_position_tickers
            )
            _maybe_scan_catalog_batch(cfg)
            _maybe_check_signal_resolutions(cfg)
            markets, account_snapshot, exchange_status = await asyncio.gather(
                _fetch_markets(client, cfg, extra_tickers=open_position_tickers), _fetch_account_snapshot(cfg),
                _fetch_exchange_status(client),
            )
            await _fetch_category_metadata(client)
            state["account"] = account_snapshot
            if exchange_status is not None:
                state["exchange_status"] = exchange_status

            # Straight from Kalshi's market.result field ("yes"/"no"/"" -
            # empty until the market settles) - used unconditionally by
            # check_exits to close out any open position on a market that's
            # actually resolved, regardless of exit config. Built from the
            # full (pre-_slim_market) markets list since result isn't one of
            # _MARKET_FIELDS (that trimming is only for the /api/state
            # payload, not internal use).
            market_results = await propagate_milestone_winners(client, markets)
            state["markets"] = [_slim_market(m) for m in markets]
            # Cheap, pure-DB check (no new API calls - see the docstring on
            # resolve_from_market_results) - runs every tick regardless of
            # market_analyst.enabled, so analyses made while it was on still
            # get graded after it's turned back off.
            market_analyst_agent.resolve_from_market_results(market_results)
            # Same zero-extra-API-call resolution shape - grades every
            # rejected candidate (services/candidate_log.py, Gap 1 of
            # docs/config-tuning-data-gaps-2026-08-10.md) against how its
            # market actually resolved.
            candidate_log.resolve_from_market_results(market_results)

            # Real market data logging (docs/advisory-engine-plan.md §9,
            # direct request: "start storing and analyzing market data
            # now") - independent of whale signals, independent of whether
            # either strategy ever trades a given market. Same real fields
            # already fetched above, zero extra API cost.
            tick_now = time.time()
            market_history.record_snapshots(
                [
                    {
                        "ticker": m["ticker"],
                        "yes_price": float(m.get("yes_bid_dollars") or 0.5),
                        "spread": max(
                            float(m.get("yes_ask_dollars") or 0.0) - float(m.get("yes_bid_dollars") or 0.0), 0.0,
                        ) if m.get("yes_ask_dollars") is not None and m.get("yes_bid_dollars") is not None else None,
                        "volume_24h": float(m.get("volume_24h_fp") or 0.0),
                        "time_to_close_sec": market_history.seconds_to_close(m.get("close_time"), tick_now),
                    }
                    for m in markets if m.get("ticker")
                ],
                timestamp=tick_now,
            )
            for m in markets:
                result = (m.get("result") or "").strip().lower()
                if result in ("yes", "no") and m.get("ticker"):
                    market_history.record_outcome(m["ticker"], result, resolved_at=tick_now)
                    # Close the loop on any settlement-window observations
                    # taken for this market (services/settlement_edge.py) -
                    # the realised outcome is written onto the rows that
                    # forecast it, so scoring can never pair an observation
                    # with a different window's result. No-op (0 rows) for
                    # the overwhelming majority of markets, which are not
                    # index-settled and were never observed.
                    settlement_edge.resolve_window(m["ticker"], result == "yes")

            # Calibration-history tracking (Gap 6, docs/config-tuning-data-
            # gaps-2026-08-10.md) - confidence_calibration.py already
            # computes a real report on demand, but only ever as a single
            # point-in-time snapshot, discarded the moment the request
            # ends. due() is a single cheap MAX() query, so this tick's
            # cost stays negligible unless a snapshot is actually due; only
            # then does the expensive full-table-scan report computation
            # run. Costs zero API tokens (pure local computation), unlike
            # the market analyst - automatic background capture is fine
            # here.
            cc_cfg = cfg.get("confidence_calibration") or {}
            if cc_cfg.get("enabled") and calibration_history.due(
                tick_now, cc_cfg.get("snapshot_interval_sec", 21600)
            ):
                cc_rows = signal_log.resolved_signals_with_factors()
                cc_result = confidence_calibration.generate_calibration_report(
                    cc_rows, cc_cfg["min_resolved_signals"], cfg.get("whale_confidence_weights"),
                )
                if cc_result["report"] is not None:
                    calibration_history.record_snapshot(cc_result["report"], tick_now)
                    # Auto-apply (2026-08-10, direct request) - off by
                    # default, only reachable via the typed-confirmation-
                    # gated /api/confidence-calibration/auto-apply/enable.
                    # Same cooldown idiom as the snapshot check itself:
                    # last_applied_at() is one cheap indexed query, so this
                    # only pays for the real work (blend + config write)
                    # once the cooldown has actually elapsed.
                    if cc_cfg.get("auto_apply_enabled"):
                        last_auto = config_performance.last_applied_at("calibration-auto-apply")
                        cooldown = cc_cfg.get("auto_apply_cooldown_sec", 86400)
                        # Direct report (2026-08-11): "auto apply should wait for a
                        # significant dataset... before applying changes." The report
                        # itself only needs min_resolved_signals (default 50) to exist
                        # at all - reasonable for a human reading a read-only panel, too
                        # thin a bar for the system to act on unsupervised. A separate,
                        # stricter floor specifically for the automatic-write path, same
                        # "manual can be more permissive than automatic" split
                        # auto_apply_min_n below applies to advisory.
                        auto_apply_floor = cc_cfg.get("auto_apply_min_resolved_signals", 150)
                        if (
                            last_auto is None or (tick_now - last_auto) >= cooldown
                        ) and cc_result["report"]["resolved_count"] >= auto_apply_floor:
                            current_weights = cfg.get("whale_confidence_weights") or {}
                            blended = confidence_calibration.blended_weights_for_auto_apply(
                                current_weights, cc_result["report"].get("suggested_weights"),
                            )
                            if blended is not None and blended != current_weights:
                                fp_before = config_performance.fingerprint(cfg)
                                config_store.update({"whale_confidence_weights": blended})
                                fp_after = config_performance.fingerprint(config_store.get())
                                # "predict how those changes may improve (or worsen)"
                                # (direct report) - the biggest observed calibration
                                # gap is exactly what suggested_weights was derived to
                                # address (services/confidence_calibration.py's
                                # _suggested_weights renormalizes toward the
                                # best-discriminating factors) - cite it plainly rather
                                # than fabricate a forward win-rate number this app has
                                # no way to honestly back before the new weights have
                                # actually scored any signals yet.
                                ranked = cc_result["report"].get("ranked_by_discrimination") or []
                                top_factor = ranked[0] if ranked else None
                                top_gap = next(
                                    (f["gap_pts"] for f in cc_result["report"]["per_factor"] if f["factor"] == top_factor),
                                    None,
                                ) if top_factor else None
                                predicted = (
                                    f" Largest observed calibration gap was {top_factor} at {top_gap:+.1f}pts - "
                                    f"this reweighting shifts weight toward the factors that discriminate best."
                                    if top_factor and top_gap is not None else ""
                                )
                                config_performance.log_applied_change(
                                    config_path="whale_confidence_weights",
                                    old_value=current_weights, new_value=blended,
                                    rationale=(
                                        f"Auto-applied calibration-suggested weights "
                                        f"(n={cc_result['report']['resolved_count']} resolved signals)."
                                        f"{predicted}"
                                    ),
                                    trade_count=cc_result["report"]["resolved_count"],
                                    fingerprint_before=fp_before, fingerprint_after=fp_after,
                                    auto_applied=True, source="calibration-auto-apply",
                                )
                                _bump_generation()

            # Advisory auto-apply - real bug found live (2026-08-10):
            # advisory.auto_apply_enabled was already protected from
            # generic config edits and had a min_confidence/cooldown_sec
            # config surface, but nothing anywhere actually read those
            # fields or auto-applied anything - the feature was reachable
            # from no path at all (see the two new /api/advisory/auto-
            # apply/* routes' own comment for the full story). Same
            # cheap-cooldown-check-first shape as calibration's own
            # auto-apply above; only applies the single highest-priority
            # (first) recommendation clearing auto_apply_min_confidence
            # per cooldown window, not a burst of every qualifying one at
            # once - same "auto-apply is inherently conservative" posture
            # calibration's own auto-apply follows.
            adv_cfg = cfg.get("advisory") or {}
            if adv_cfg.get("enabled") and adv_cfg.get("auto_apply_enabled"):
                last_adv_auto = config_performance.last_applied_at("unified-advisory-auto")
                adv_cooldown = adv_cfg.get("auto_apply_cooldown_sec", 86400)
                if last_adv_auto is None or (tick_now - last_adv_auto) >= adv_cooldown:
                    adv_current_fp = config_performance.fingerprint(cfg)
                    adv_all_rows = trade_analytics.build_trade_history([t.to_dict() for t in broker.trade_log])
                    adv_market_rows = trade_analytics.build_trade_history([t.to_dict() for t in market_broker.trade_log])
                    adv_variants = {v["fingerprint"]: v for v in config_performance.all_variants()}
                    adv_result = advisory_engine.generate_recommendations(
                        adv_all_rows, cfg, adv_current_fp, adv_variants,
                        adv_cfg["min_resolved_trades_per_variant"], market_rows=adv_market_rows,
                        gate_summaries=candidate_log.gate_summary(),
                        # Staleness filter (2026-08-11, direct bug report) matters most
                        # right here - unlike a manual click, auto-apply has no human
                        # to notice it's repeatedly nudging the same field off the
                        # exact same stale evidence every cooldown window.
                        last_applied_by_path=config_performance.all_last_applied_by_path(),
                        series_evaluator_rows=_series_evaluator_overview_with_crosscheck(cfg),
                        category_rows=regime_analytics.by_category(adv_all_rows),
                    )
                    min_confidence_rank = _CONFIDENCE_RANK.get(adv_cfg.get("auto_apply_min_confidence", "higher"), 2)
                    # Direct report (2026-08-11): "auto apply should wait for a
                    # significant dataset... before applying changes." confidence_
                    # label's "higher" tier already starts at n=15 (trade_analytics.
                    # confidence_label) - a reasonable bar for a human to read a
                    # suggestion, thinner than what should trigger an unsupervised
                    # config write. A dedicated, separately-tunable floor for the
                    # automatic path only - manual Apply (see apply_advisory_
                    # recommendation) is untouched by this, same "manual can be more
                    # permissive than automatic" split as the calibration side above.
                    min_n = adv_cfg.get("auto_apply_min_n", 25)
                    qualifying = [
                        r for r in adv_result.get("recommendations", [])
                        if _CONFIDENCE_RANK.get(r["confidence_label"], 0) >= min_confidence_rank and r["n"] >= min_n
                    ]
                    if qualifying:
                        rec = qualifying[0]
                        section, _, field = rec["config_path"].partition(".")
                        config_store.update({section: {field: rec["suggested_value"]}})
                        adv_new_fp = config_performance.fingerprint(config_store.get())
                        config_performance.log_applied_change(
                            config_path=rec["config_path"], old_value=rec["current_value"],
                            new_value=rec["suggested_value"], rationale=rec["rationale"], trade_count=rec["n"],
                            fingerprint_before=adv_current_fp, fingerprint_after=adv_new_fp,
                            auto_applied=True, source="unified-advisory-auto",
                        )
                        _bump_generation()

            # MarketNativeStrategy (services/market_strategy.py) - runs every
            # tick alongside the whale-follow strategy below, entirely off
            # its own real-market-data heuristic. No-op (returns []) when
            # market_strategy.enabled is false, same disabled-by-default
            # precedent as the rest of this app's opt-in automation.
            # Appended to its own state["market_decision_feed"], NOT
            # state["decision_feed"] - that feed is the whale-follow
            # strategy's own record; blending the two would defeat the
            # point of each strategy's performance being cleanly,
            # independently measurable (see the plan doc). Was computed and
            # discarded every tick until the Market-Native tab (2026-08-10,
            # direct request) needed a real feed to show.
            for decision in market_strategy.evaluate_all(
                markets, tick_now, cfg, market_results, state.get("me_pairs"), category_by_ticker=_category_by_ticker(),
            ):
                state["market_decision_feed"].insert(0, decision)
                if decision["action"] == "trade":
                    # Same category-at-entry-time capture as the whale-follow
                    # side above - one shared table across both strategies,
                    # since Gap 9's segmentation reads either strategy's own
                    # trade history the same way.
                    m_ticker = decision["ticker"]
                    m_event_ticker = (state["market_titles"].get(m_ticker) or {}).get("event_ticker")
                    m_event_info = state["event_titles"].get(m_event_ticker) or {}
                    trade_category.record_category(
                        m_ticker, m_event_info.get("category"), tick_now,
                        subcategory=_sport_for_event(m_event_info),
                    )
            markets_by_ticker = {m["ticker"]: m for m in markets if m.get("ticker")}
            for decision in market_strategy.check_exits(
                markets_by_ticker, tick_now, cfg, market_results, category_by_ticker=_category_by_ticker(),
            ):
                state["market_decision_feed"].insert(0, decision)
            state["market_decision_feed"] = state["market_decision_feed"][:50]
            state["market_results"] = market_results
            if _streaming_trade_tape_enabled():
                await trade_stream.set_market_tickers([m["ticker"] for m in markets if m.get("ticker")])
            # All three depend on this tick's markets list but not on each
            # other - fetch concurrently rather than one after the other.
            trade_tape_since = state.get("trade_tape_last_fetch_ts")
            if _streaming_trade_tape_enabled():
                event_titles, event_live_data, live_status = await asyncio.gather(
                    _fetch_event_titles(client, markets),
                    _fetch_event_live_data(client, markets),
                    _fetch_live_status(client, markets),
                )
                trade_tape = state["trade_tape"]
            else:
                event_titles, event_live_data, trade_tape, live_status = await asyncio.gather(
                    _fetch_event_titles(client, markets),
                    _fetch_event_live_data(client, markets),
                    _fetch_trade_tape(client, markets, since_ts=trade_tape_since),
                    _fetch_live_status(client, markets),
                )
                state["trade_tape_last_fetch_ts"] = tick_now
            state["event_titles"].update(event_titles)
            # Mutually-exclusive pair detection (2026-08-14 direct request,
            # services/mutual_exclusivity.py) - recomputed fresh every tick
            # from this tick's markets/event_titles (cheap, pure, zero new
            # API calls), not persisted, so a sibling set or Kalshi's own
            # flag changing is reflected immediately instead of going
            # stale. Shared by both strategies below rather than each
            # computing its own copy.
            state["me_pairs"] = mutual_exclusivity.find_me_pairs(markets, state["event_titles"])
            # Event-lifecycle phase (2026-08-15, docs/hardening-and-accuracy-
            # roadmap-2026-08-11.md Part 1, direct request: "pre-tail,
            # mid-series, post-tail type analysis") - recomputed fresh every
            # tick from this tick's markets (sibling count per event, cheap,
            # zero new API calls) + state["event_titles"] (mutually_exclusive,
            # best-effort - an event not yet cached there just falls back to
            # event_lifecycle.classify_phase's safe single-game-shaped
            # narrow window, same "don't guess" idiom _fetch_live_status
            # already uses). See services/event_lifecycle.py's own module
            # docstring for the real incident this closes.
            el_cfg = cfg.get("event_lifecycle") or {}
            _sibling_counts: dict[str, int] = {}
            for m in markets:
                et = m.get("event_ticker")
                if et:
                    _sibling_counts[et] = _sibling_counts.get(et, 0) + 1
            event_phase: dict[str, str] = {}
            for m in markets:
                et = m.get("event_ticker")
                if not et or et in event_phase:
                    continue
                event_phase[et] = event_lifecycle.classify_phase(
                    occurrence_datetime=m.get("occurrence_datetime"),
                    now=tick_now,
                    sibling_count=_sibling_counts.get(et, 1),
                    mutually_exclusive=(state["event_titles"].get(et) or {}).get("mutually_exclusive"),
                    close_time=m.get("close_time"),
                    tournament_min_siblings=el_cfg.get("tournament_min_siblings", 4),
                    tournament_pretail_days=el_cfg.get("tournament_pretail_days", 5.0),
                )
            state["event_phase"] = event_phase
            tags_by_categories = state["category_metadata"].get("tags_by_categories") or {}
            for et, event_meta in state["event_titles"].items():
                category = event_meta.get("category")
                event_meta["category_tags"] = tags_by_categories.get(category, []) if category else []
            title_cache.save_event_titles(event_titles)  # event_titles here is already just this tick's new entries, see _fetch_event_titles
            state["event_live_data"].update(event_live_data)
            # trade_tape itself (the incremental, uncapped-beyond-a-sanity-
            # ceiling result) is what whale detection reads below - only the
            # UI-facing copy gets sliced down to a human-scannable size
            # (direct report, 2026-08-11: the two used to share one 100-item
            # cap, which was silently dropping real trades from detection
            # under normal load, not just trimming the display).
            state["trade_tape"] = trade_tape[:_TRADE_TAPE_UI_CAP]
            # Capture the REST-polled tape too, not just the websocket path
            # (_process_stream_trade) - the two are independent sources of
            # the same prints, and a watcher that only sees one of them
            # would under-report exactly when the stream is the thing
            # that's broken. record_trade dedupes on trade_id, so the
            # deliberate overlap between the two paths costs nothing.
            for tape_trade in trade_tape:
                series_watcher.record_trade(tape_trade, cfg)
            # One batched write per tick for everything the websocket path
            # buffered in between (see series_watcher.flush) - the capture
            # layer never writes per message, which is what makes it safe
            # to run against an exchange-wide subscription.
            series_watcher.flush()
            # Same per-tick batched write for index ticks. Without this the
            # buffer only drained when it hit its own _FLUSH_BATCH, which at
            # ~1 tick/sec/index meant minutes of data sitting unwritten -
            # observed live as index_ticks holding 0 rows while the in-memory
            # snapshot showed ticks arriving.
            index_feed.flush()
            settlement_edge.flush()
            game_state.flush()
            # Retention (2026-08-17). Every capture store above is
            # unbounded by construction, and prune() existed but was never
            # called - data/ was already 841MB with series_watcher at 130MB
            # after a few hours and game_state at 32MB within minutes of
            # first writing, because a crypto payload carries a whole
            # candlestick array per row. Runs at most hourly, and never
            # touches raw_trades or settlement-window rows: CLAUDE.md treats
            # accumulated history as a first-class asset, so only the
            # high-churn sampled series are trimmed.
            _maybe_prune_capture_stores(cfg, tick_now)
            await _resolve_settlement_windows(client)
            state["live_status"] = live_status  # replaced wholesale, not accumulated - a stale "live" would be wrong, not just incomplete
            # yes_bid_dollars is Kalshi's real field (already a 0-1 probability) —
            # "yes_bid" (cents) doesn't exist on the live API and silently
            # defaulted every price to 0.5.
            state["latest_prices"] = {
                m["ticker"]: float(m.get("yes_bid_dollars") or 0.5) for m in markets if m.get("ticker")
            }
            # Maker/limit-order path (2026-08-15) - genuinely missing, not
            # defaulted like latest_prices above: check_pending_fills needs
            # to tell "no fresh ask this tick" apart from "a real 0.5 ask,"
            # since guessing an ask would mean guessing whether a resting
            # order should fill - the one thing this mechanism must never do.
            state["latest_asks"] = {
                m["ticker"]: float(m["yes_ask_dollars"]) for m in markets
                if m.get("ticker") and m.get("yes_ask_dollars") not in (None, "")
            }
            _join_real_position_prices(state["account"], state["latest_prices"])
            # Human-readable label for a ticker — whale signals/decisions/positions
            # only carry the raw ticker string, so the dashboard looks this up to
            # show something a person can actually read instead of e.g.
            # "KXMVESPORTS...-FC34E0243A1". yes_sub_title/no_sub_title (not just
            # title) are kept so the dashboard can say what a Yes or No position
            # actually *means* ("betting YES = San Diego wins"), not just show a
            # side tag - previously only a single collapsed title string was kept
            # here, which lost that. Accumulates (doesn't overwrite) so a signal
            # from a market that has since rotated out of the top-volume
            # watchlist still resolves to its title - kept unbounded in memory
            # and write-through persisted to data/title_cache.db (see
            # services/title_cache.py) rather than capped at 500 by insertion
            # order, which used to silently evict exactly the older entries
            # signal history/clusters/positions need most. _build_state_body
            # scopes what's actually sent over /api/state, so this growing
            # unbounded server-side doesn't reintroduce the payload-size
            # regression that scoping was built to fix.
            # mve_selected_legs (real SDK field, Market.mve_selected_legs) is
            # the authoritative "is this a combo/MVE market, and what are its
            # real legs" signal - each entry already has {event_ticker,
            # market_ticker, side}. Kept ephemeral (not persisted to
            # title_cache.db, unlike title/yes_sub_title/no_sub_title) since
            # it's only meaningful "live," the same way latest_prices is
            # recomputed fresh every tick rather than persisted. Each leg's
            # own label/price resolves lazily client-side off the existing
            # marketTitles/latest_prices caches, same eventually-consistent
            # pattern every other off-watchlist ticker reference already uses
            # - no extra API calls needed just to expose the leg list itself.
            new_market_titles = {
                m["ticker"]: {
                    **title_cache.market_title_fields(m), "event_ticker": m.get("event_ticker"),
                    "legs": m.get("mve_selected_legs") or None,
                }
                for m in markets if m.get("ticker")
            }
            state["market_titles"].update(new_market_titles)
            title_cache.save_market_titles(new_market_titles)
            # Computed once per poll tick (not per /api/state request, which is polled
            # more often) since it's the same until the next tick anyway.
            state["series_track_record"] = {
                m["ticker"]: signal_log.series_stats(m["ticker"], days=30) for m in markets if m.get("ticker")
            }
            state["last_poll"] = time.time()
            state["error"] = None
            state["equity_history"].append({"t": state["last_poll"], "equity": broker.equity(state["latest_prices"])})
            state["equity_history"] = state["equity_history"][-500:]

            # "balance" (cash, in cents) verified against a real account 2026-08-07
            # — see ROADMAP.md/status.html. Still guarded rather than assumed,
            # since a disconnected/errored account has no balance dict at all.
            # Divided by 100 here to store dollars, matching every consumer of
            # this history (the equity chart, the header strip) - this used to
            # store the raw cents value directly, a real bug found while
            # wiring the header strip to it: a $1,000.00 real balance would
            # have silently rendered as "$100,000.00".
            real_balance = (account_snapshot.get("balance") or {}) if account_snapshot.get("connected") else {}
            real_balance_value = real_balance.get("balance") if isinstance(real_balance, dict) else None
            # portfolio_value (cash + open positions' value) alongside balance
            # (cash only) - real bug found live (2026-08-15): renderHeaderStrip's
            # "Change (session)" diffed the CURRENT portfolio_value against
            # this history's cash-only "balance" field as if they were the
            # same scope. static/index.html's own comment already documents
            # these as genuinely different, non-interchangeable numbers - the
            # header diff just wasn't following it. An account with $500 cash
            # and one already-open $500 position would show portfolio_value
            # ($1,000) minus a cash-only baseline ($500) = a fabricated +$500
            # "change" the instant a real account with open positions was
            # first polled after a restart, before anything actually moved.
            # Recorded alongside (not instead of) balance so both history
            # series stay available; None when portfolio_value is absent
            # rather than guessed, same "missing isn't zero" idiom as every
            # other optional figure in this app.
            real_portfolio_value = real_balance.get("portfolio_value") if isinstance(real_balance, dict) else None
            if real_balance_value is not None:
                try:
                    entry = {"t": state["last_poll"], "balance": float(real_balance_value) / 100.0}
                    if real_portfolio_value is not None:
                        entry["portfolio_value"] = float(real_portfolio_value) / 100.0
                    state["real_balance_history"].append(entry)
                    state["real_balance_history"] = state["real_balance_history"][-200:]
                except (TypeError, ValueError):
                    pass

            new_signals = []
            if _streaming_trade_tape_enabled():
                state["whale_source"] = f"{whale_provider.name} (websocket)"
            elif whale_provider.enabled:
                try:
                    new_signals = await whale_provider.fetch_signals(
                        market_context={
                            "markets": markets, "trade_tape": trade_tape, "cfg": cfg,
                            "client": client,
                        },
                    )
                    state["whale_source"] = whale_provider.name
                except Exception as e:
                    # Provider hiccuped — fall back to the simulator for this
                    # tick rather than stalling the whole loop.
                    state["error"] = f"whale-watcher fetch failed, using simulator: {e}"
                    whale_sim.size_range = tuple(cfg["whale_signal"]["whale_size_range"])
                    whale_sim.bias = cfg["whale_signal"]["bias"]
                    sig = whale_sim.maybe_generate(
                        markets, cfg["whale_signal"]["signal_frequency_sec"],
                        live_status=state["live_status"], live_only=cfg["whale_signal"].get("live_markets_only", False),
                    )
                    new_signals = [sig] if sig else []
                    state["whale_source"] = f"simulated ({whale_provider.name} fallback)"
            else:
                whale_sim.size_range = tuple(cfg["whale_signal"]["whale_size_range"])
                whale_sim.bias = cfg["whale_signal"]["bias"]
                sig = whale_sim.maybe_generate(
                    markets, cfg["whale_signal"]["signal_frequency_sec"],
                    live_status=state["live_status"], live_only=cfg["whale_signal"].get("live_markets_only", False),
                )
                new_signals = [sig] if sig else []
                state["whale_source"] = "simulated"

            for signal in new_signals:
                await _handle_signal(signal, cfg, market_results, config_fp, tick_now)

            # Maker/limit-order path (2026-08-15) - resolves resting limit
            # orders strategy.evaluate() may have placed above (opt-in,
            # strategy.use_limit_orders) against this tick's real bid/ask.
            # Runs before check_exits below (same reasoning as the
            # opened_since guard immediately below) so a just-filled
            # position isn't exit-checked this same tick against
            # state["latest_prices"], snapshotted before this fill happened.
            for fill_decision in broker.check_pending_fills(state["latest_prices"], state["latest_asks"]):
                await _handle_fill_decision(fill_decision, tick_now)

            # Active position management - runs every tick regardless of
            # whether any new signal came in this tick, since a position can
            # need closing (take-profit/stop-loss/sentiment-reversal) purely
            # because the market moved or whale flow shifted, not because a
            # fresh signal arrived. See FollowTheWhaleStrategy.check_exits.
            # opened_since=tick_now (real live bug, 2026-08-11): without
            # this, a position the signal loop just opened above gets
            # exit-checked in this same pass against state["latest_prices"],
            # which was snapshotted at the top of this tick - before that
            # position's own entry price, if it came from a live trade-tape
            # print newer than the last quote poll. See check_exits's
            # docstring for the live incident this fixes.
            for close_decision in strategy.check_exits(
                state["latest_prices"], state["signal_feed"], cfg, market_results, opened_since=tick_now,
                category_by_ticker=_category_by_ticker(), close_times=_close_time_by_ticker(),
            ):
                await _handle_close_decision(close_decision)

            # Position netting (2026-08-15 direct correction: the ME-gate
            # above only blocks a NEW entry into a confirmed complement -
            # it does nothing for positions already open, partial hedges,
            # or N-way concentration). Runs after check_exits, on whatever
            # survived per-position rules - see services/position_netting.py
            # for the payout-profile math. Entirely opt-in
            # (position_netting.enabled, default False) and a no-op until
            # deliberately turned on.
            for close_decision in position_netting.review(
                broker, state["market_titles"], state["event_titles"], state["latest_prices"], cfg,
            ):
                await _handle_close_decision(close_decision)

        except Exception as e:
            state["error"] = str(e)
        finally:
            # A fresh client every tick means base_url changes (rare, but
            # live-reloadable) take effect immediately - but the SDK client
            # wraps its own aiohttp session, so it needs closing after use
            # or sessions leak across a long-running process.
            if client is not None:
                await client.close()

        state["last_tick_duration_sec"] = round(time.time() - tick_start_wall, 2)
        state["last_tick_rate_limit_hits"] = get_and_reset_rate_limit_hits()
        _bump_generation()
        await asyncio.sleep(cfg["kalshi"]["poll_interval_sec"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(trading_loop())
    trade_stream_task = None
    if _streaming_trade_tape_enabled():
        trade_stream_task = asyncio.create_task(
            trade_stream.run(
                _process_stream_trade, _process_stream_ticker, _handle_trade_stream_status,
                on_fill=_process_stream_fill, on_position=_process_stream_position,
            )
        )
    index_stream_task = None
    if index_stream.enabled and (index_stream.index_ids or index_stream.underlying_tickers):
        # Its own connection and its own task - see index_stream's own
        # comment for why this isn't just another channel on trade_stream.
        index_stream_task = asyncio.create_task(
            index_stream.run(
                _noop_stream_trade, _noop_stream_ticker, _handle_index_stream_status,
                on_index=_process_stream_index,
            )
        )
    yield
    if trade_stream_task is not None:
        await trade_stream.close()
        trade_stream_task.cancel()
    if index_stream_task is not None:
        await index_stream.close()
        index_stream_task.cancel()
    task.cancel()
    await close_client()
    # `account` is a long-lived singleton (unlike the per-tick market-data
    # client) holding its own SDK-managed aiohttp session — needs its own
    # explicit close, the hand-rolled version never did since it only ever
    # used the shared httpx client via get_client().
    await account.close()


app = FastAPI(title="Kalshi Whale-Signal Paper Trader", lifespan=lifespan)

# allow_origins=["*"] was fine while this only ever ran on localhost/DDEV, but
# doesn't hold once it's reachable from anywhere else — a same-origin browser
# tab never needs CORS at all (the frontend and API are served from this same
# app), so this only matters for cross-origin callers, which by default means
# just the DDEV hostname and common local dev ports. Override with a
# comma-separated ALLOWED_ORIGINS in .env for any other real deployment.
_default_origins = [
    "https://kalshi-whale-poc.ddev.site",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]
_allowed_origins_env = os.environ.get("ALLOWED_ORIGINS", "").strip()
allowed_origins = (
    [o.strip() for o in _allowed_origins_env.split(",") if o.strip()]
    if _allowed_origins_env
    else _default_origins
)
app.add_middleware(CORSMiddleware, allow_origins=allowed_origins, allow_methods=["*"], allow_headers=["*"])

# Extracted route groups (2026-08-17) - see routers/diagnostics_routes.py for
# the pattern. include_router preserves every path exactly as it was when
# these were @app.* in this file, so nothing client-side or test-side moves.
app.include_router(diagnostics_routes.router)

# AuthMiddleware added first (inner) so SessionMiddleware — added second, thus
# outermost — populates request.session before AuthMiddleware ever reads it.
# Both are no-ops end-to-end until GOOGLE_CLIENT_ID/SECRET + APP_SECRET_KEY
# are set in .env; see services/auth.py.
app.add_middleware(auth_service.AuthMiddleware)
if auth_service.auth_configured():
    app.add_middleware(SessionMiddleware, secret_key=auth_service.session_secret_key())


# ---- auth --------------------------------------------------------------

@app.get("/api/session")
async def get_session(request: Request):
    return {
        "auth_configured": auth_service.auth_configured(),
        "user": request.session.get("user") if auth_service.auth_configured() else None,
    }


@app.get("/auth/login")
async def auth_login(request: Request):
    if not auth_service.auth_configured():
        return RedirectResponse(url="/")
    redirect_uri = str(request.url_for("auth_callback"))
    state_token = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state_token
    return RedirectResponse(url=auth_service.build_login_url(redirect_uri, state_token))


@app.get("/auth/callback", name="auth_callback")
async def auth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error:
        return RedirectResponse(url=f"/login?error={error}")
    if not code or state != request.session.get("oauth_state"):
        return RedirectResponse(url="/login?error=state_mismatch")
    redirect_uri = str(request.url_for("auth_callback"))
    try:
        tokens = await auth_service.exchange_code(code, redirect_uri)
        claims = await auth_service.verify_id_token(tokens["id_token"])
    except Exception as e:
        return RedirectResponse(url=f"/login?error={type(e).__name__}")
    request.session["user"] = {
        "email": claims.get("email"),
        "name": claims.get("name"),
        "picture": claims.get("picture"),
    }
    return RedirectResponse(url="/")


@app.post("/auth/logout")
async def auth_logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


# ---- API -------------------------------------------------------------------

class ConfigPatch(BaseModel):
    patch: dict


class ApplyRecommendationBody(BaseModel):
    id: str


# kalshi_account.trading_enabled is the one config value that turns on real
# order placement — it doesn't go through the generic config patch endpoint
# below at all, on purpose. See EnableTradingBody/enable_trading for the only
# path that can flip it on, which requires a real connected account and an
# exact-match typed confirmation phrase, not just a checkbox.
TRADING_CONFIRMATION_PHRASE = "ENABLE REAL TRADING"


class EnableTradingBody(BaseModel):
    confirmation_phrase: str


@app.get("/api/markets/{ticker}/orderbook")
async def get_market_orderbook(ticker: str):
    # Per-market drill-down (ROADMAP.md Phase 0.5) - on-demand, not part of
    # the poll loop, so it gets its own short-lived client rather than
    # waiting for the next tick. Matches trading_loop()'s own construct/use/
    # close pattern (see its `finally: await client.close()`), just fired
    # from a request instead of a timer.
    cfg = config_store.get()
    client = KalshiClient(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])
    try:
        return await client.get_orderbook(ticker)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        await client.close()


@app.get("/api/markets/{ticker}/candlesticks")
async def get_candlesticks(ticker: str, event_ticker: str):
    # Price history for the per-market drill-down (ROADMAP.md Phase 0.5).
    # get_market_candlesticks requires series_ticker, which market objects
    # don't carry directly (only event_ticker) - verified via introspection,
    # not guessed from the ticker string, since a wrong value here is a hard
    # API error rather than a silently-wrong display. event_ticker comes
    # from the caller (the frontend already has it on state.markets) so
    # this can go straight to the one get_event() lookup it needs rather
    # than an extra get_market() call first to discover it.
    #
    # Fixed window: last 7 days, hourly candles - dense enough for a
    # meaningful chart, short enough to stay a single fast request. Not
    # user-configurable yet.
    cfg = config_store.get()
    client = KalshiClient(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])
    try:
        event = await client.get_event(event_ticker)
        series_ticker = (event.get("event") or {}).get("series_ticker")
        if not series_ticker:
            raise HTTPException(status_code=502, detail="Could not resolve series_ticker for this event")
        end_ts = int(time.time())
        start_ts = end_ts - 7 * 24 * 3600
        return await client.get_candlesticks(series_ticker, ticker, start_ts, end_ts, period_interval=60)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        await client.close()


@app.get("/api/markets/{ticker}/trades")
async def get_market_trades(ticker: str):
    # Recent trades for one market, in the drill-down (ROADMAP.md Phase
    # 0.5) - distinct from the full-exchange trade tape (a separate,
    # not-yet-built Terminal/Whale-Watch-level feed across every watched
    # market). No series_ticker complication here, unlike candlesticks -
    # get_trades takes a plain ticker filter.
    cfg = config_store.get()
    client = KalshiClient(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])
    try:
        return await client.get_trades(ticker=ticker, limit=15)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        await client.close()


def _dollars(v) -> float | None:
    return float(v) if v not in (None, "") else None


def _slim_detail_market(m: dict) -> dict:
    return {
        "ticker": m.get("ticker"),
        "title": m.get("title"),
        "subtitle": m.get("subtitle"),
        "yes_sub_title": m.get("yes_sub_title"),
        "no_sub_title": m.get("no_sub_title"),
        "status": m.get("status"),
        "yes_bid": _dollars(m.get("yes_bid_dollars")),
        "yes_ask": _dollars(m.get("yes_ask_dollars")),
        "no_bid": _dollars(m.get("no_bid_dollars")),
        "no_ask": _dollars(m.get("no_ask_dollars")),
        "last_price": _dollars(m.get("last_price_dollars")),
        "previous_price": _dollars(m.get("previous_price_dollars")),
        "volume": _dollars(m.get("volume_fp")),
        "volume_24h": _dollars(m.get("volume_24h_fp")),
        "open_interest": _dollars(m.get("open_interest_fp")),
        "liquidity": _dollars(m.get("liquidity_dollars")),
        "close_time": m.get("close_time"),
        "open_time": m.get("open_time"),
    }


@app.get("/api/markets/{ticker}/detail")
async def get_market_detail(ticker: str):
    # Everything one whole-market "landing page" view needs in one call
    # (ROADMAP.md, Open Positions -> full market detail): the market's own
    # full object plus, when it belongs to a multi-outcome event, every
    # sibling market in that event (get_event's own `markets` list already
    # includes them with live prices - no per-sibling get_market() round
    # trip needed) so the modal can show the same kind of outcome table
    # Kalshi's own market page shows, not just this one ticker in isolation.
    cfg = config_store.get()
    client = KalshiClient(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])
    try:
        try:
            market = await client.get_market(ticker)
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))

        event_ticker = market.get("event_ticker")
        event_info, siblings = None, []
        if event_ticker:
            try:
                ev = await client.get_event(event_ticker)
                event_info = ev.get("event")
                siblings = [m for m in (ev.get("markets") or []) if m.get("ticker") != ticker]
            except Exception:
                pass  # event context is a bonus, not core to the ticker's own detail
    finally:
        await client.close()

    detail = _slim_detail_market(market)
    detail["rules_primary"] = market.get("rules_primary")
    detail["rules_secondary"] = market.get("rules_secondary")
    detail["event_ticker"] = event_ticker
    detail["event"] = {
        "title": event_info.get("title"),
        "sub_title": event_info.get("sub_title"),
        "category": event_info.get("category"),
    } if event_info else None
    detail["siblings"] = sorted(
        (_slim_detail_market(m) for m in siblings),
        key=lambda m: m["volume_24h"] or 0, reverse=True,
    )
    return detail


@app.get("/api/signals/history")
async def get_signal_history(limit: int = 50, offset: int = 0, resolved_only: bool = False):
    # Browsable signal history (ROADMAP.md Phase 0.5) - individual signals,
    # not just the aggregate win-rate stat cards. Pure on-demand read
    # against signal_log.db, not part of /api/state's poll cycle - a
    # separate paginated fetch, same pattern as /api/markets/search.
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    signals = signal_log.recent(limit=limit, offset=offset, resolved_only=resolved_only)
    return {
        "signals": signals,
        "total": signal_log.total_count(resolved_only=resolved_only),
        # Resolved/older signals routinely reference tickers that have long
        # since rotated off the live watchlist and won't be in /api/state's
        # scoped market_titles - state["market_titles"] itself is unbounded
        # for the app's lifetime (see services/title_cache.py), so this page
        # of signals can still resolve its own titles independently.
        "market_titles": _scoped_market_titles({s["ticker"] for s in signals if s.get("ticker")}),
    }


@app.get("/api/signals/clusters")
async def get_signal_clusters(hours: int = 24):
    # Persistent flow clustering (ROADMAP.md P2 stretch item) - probable-
    # same-actor accumulation groups, inferred from timing/size similarity
    # on the persisted signal log, not a live/poll-cycle concern.
    hours = min(max(hours, 1), 24 * 30)
    clusters = signal_log.find_clusters(hours=hours)
    return {
        "clusters": clusters,
        "market_titles": _scoped_market_titles({c["ticker"] for c in clusters if c.get("ticker")}),
    }


@app.get("/api/trading-history")
async def get_trading_history(limit: int = 50, offset: int = 0):
    # The History tab (direct request): win/loss record, what closed each
    # position (take-profit/stop-loss/sentiment-reversal/auto-exit/settled),
    # and sample-size-hedged hints about which config knob a pattern might
    # argue for adjusting. All derived from the trade log's existing reason
    # strings (see services/trade_analytics.py) - no new persistence.
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)

    # broker.trade_log is already chronological ascending (append-only at
    # runtime, ORDER BY timestamp ASC on load) - exactly what
    # build_trade_history expects and what a cumulative P&L curve needs.
    all_rows = trade_analytics.build_trade_history([t.to_dict() for t in broker.trade_log])

    cumulative_pnl_curve = []
    running = 0.0
    for r in all_rows:
        if r["realized_pnl"] is not None:
            running += r["realized_pnl"]
        cumulative_pnl_curve.append({"t": r["exit_timestamp"], "cumulative_pnl": round(running, 2)})

    newest_first = list(reversed(all_rows))
    page = newest_first[offset:offset + limit]
    return {
        "trades": page,
        "total": len(all_rows),
        "summary": trade_analytics.compute_summary(all_rows),
        "exit_management_split": trade_analytics.exit_management_split(all_rows),
        "cumulative_pnl_curve": cumulative_pnl_curve,
        "market_titles": _scoped_market_titles({r["ticker"] for r in page}),
    }


@app.get("/api/account/orders")
async def get_account_orders(limit: int = 25, cursor: str | None = None, status: str | None = None):
    # Real order history - direct data-usage review finding: get_orders()
    # was fully implemented in kalshi_account_client.py and returns real
    # order objects (confirmed against the live connected account), but
    # nothing ever called it - no route, no state key, no panel. Any order
    # on that account (placed by this app once trading is enabled, or
    # manually on Kalshi's own site) was completely invisible in this
    # dashboard. Kept out of the main /api/state poll loop on purpose -
    # order history isn't bounded the way "current positions" is, so it's
    # an on-demand paginated fetch instead (Kalshi's own cursor, passed
    # through opaquely, not the limit/offset pagination this app's own
    # endpoints use elsewhere - real order history is Kalshi's data, not
    # ours to re-paginate).
    limit = min(max(limit, 1), 100)
    if not account.enabled:
        return {"connected": False, "orders": [], "cursor": None, "error": account.status["error"]}
    try:
        result = await account.get_orders(limit=limit, cursor=cursor, status=status)
        return {
            "connected": True,
            "orders": [_slim_order(o) for o in (result.get("orders") or [])],
            "cursor": result.get("cursor"),
            "error": None,
        }
    except Exception as e:
        return {"connected": True, "orders": [], "cursor": None, "error": str(e)}


@app.get("/api/advisory/status")
async def get_advisory_status():
    # Honest progress reporting even while gated (docs/advisory-engine-plan.md
    # §3, layer 2) - this never leaks a real recommendation early, but it's
    # useful to show "18/30 resolved trades" while waiting, same real-data-
    # or-honest-fallback idiom as the rest of this app.
    adv_cfg = config_store.get()["advisory"]
    cfg = config_store.get()
    current_fp = config_performance.fingerprint(cfg)
    all_rows = trade_analytics.build_trade_history([t.to_dict() for t in broker.trade_log])
    summaries = advisory_engine.variant_summaries(all_rows)
    known_variants = config_performance.all_variants()
    min_resolved = adv_cfg["min_resolved_trades_per_variant"]
    variants_out = []
    for v in known_variants:
        fp = v["fingerprint"]
        resolved = summaries.get(fp, {}).get("total_closed", 0)
        variants_out.append({
            "fingerprint": fp,
            "first_seen_at": v["first_seen_at"],
            "resolved_count": resolved,
            "ready": resolved >= min_resolved,
            "is_current": fp == current_fp,
        })
    return {
        "enabled": adv_cfg["enabled"],
        "min_resolved_trades_per_variant": min_resolved,
        "auto_apply_enabled": adv_cfg["auto_apply_enabled"],
        "current_fingerprint": current_fp,
        "variants": variants_out,
    }


# Real bug found live (2026-08-10): advisory.auto_apply_enabled was already
# protected from generic /api/config edits, with an error message pointing
# at these exact two routes - but they never actually existed, and nothing
# anywhere read auto_apply_min_confidence/auto_apply_cooldown_sec either.
# The feature was reachable from no path at all. Fixed alongside adding the
# equivalent for confidence_calibration (direct request: "i want the option
# to enable auto whale-signal calibration... have them auto-enable and
# start getting put into play with my whole system once there *is* enough
# data") - same typed-confirmation-phrase gate as real trading, since
# auto-applying a config change with no human in the loop is a genuinely
# consequential action, not a plain checkbox.
ADVISORY_AUTO_APPLY_CONFIRMATION_PHRASE = "ENABLE ADVISORY AUTO APPLY"
CALIBRATION_AUTO_APPLY_CONFIRMATION_PHRASE = "ENABLE CALIBRATION AUTO APPLY"


class EnableAutoApplyBody(BaseModel):
    confirmation_phrase: str


@app.post("/api/advisory/auto-apply/enable")
async def enable_advisory_auto_apply(body: EnableAutoApplyBody):
    if body.confirmation_phrase != ADVISORY_AUTO_APPLY_CONFIRMATION_PHRASE:
        raise HTTPException(
            status_code=400,
            detail=f'Confirmation phrase did not match. Type exactly: "{ADVISORY_AUTO_APPLY_CONFIRMATION_PHRASE}"',
        )
    old_value = config_store.get()["advisory"]["auto_apply_enabled"]
    fp = config_performance.fingerprint(config_store.get())
    config_store.update({"advisory": {"auto_apply_enabled": True}})
    config_performance.log_applied_change(
        config_path="advisory.auto_apply_enabled", old_value=old_value, new_value=True,
        rationale="Enabled via the typed advisory-auto-apply confirmation phrase.", trade_count=0,
        fingerprint_before=fp, fingerprint_after=fp, auto_applied=False, source="manual",
    )
    _bump_generation()
    return {"auto_apply_enabled": True}


@app.post("/api/advisory/auto-apply/disable")
async def disable_advisory_auto_apply():
    # Disabling never needs the confirmation phrase - same asymmetric
    # safety convention as real trading (enabling something consequential
    # needs friction, turning it back off shouldn't).
    old_value = config_store.get()["advisory"]["auto_apply_enabled"]
    fp = config_performance.fingerprint(config_store.get())
    config_store.update({"advisory": {"auto_apply_enabled": False}})
    if old_value:
        config_performance.log_applied_change(
            config_path="advisory.auto_apply_enabled", old_value=True, new_value=False,
            rationale="Disabled via the dashboard.", trade_count=0,
            fingerprint_before=fp, fingerprint_after=fp, auto_applied=False, source="manual",
        )
    _bump_generation()
    return {"auto_apply_enabled": False}


@app.post("/api/confidence-calibration/auto-apply/enable")
async def enable_calibration_auto_apply(body: EnableAutoApplyBody):
    if body.confirmation_phrase != CALIBRATION_AUTO_APPLY_CONFIRMATION_PHRASE:
        raise HTTPException(
            status_code=400,
            detail=f'Confirmation phrase did not match. Type exactly: "{CALIBRATION_AUTO_APPLY_CONFIRMATION_PHRASE}"',
        )
    old_value = config_store.get()["confidence_calibration"]["auto_apply_enabled"]
    fp = config_performance.fingerprint(config_store.get())
    config_store.update({"confidence_calibration": {"auto_apply_enabled": True}})
    config_performance.log_applied_change(
        config_path="confidence_calibration.auto_apply_enabled", old_value=old_value, new_value=True,
        rationale="Enabled via the typed calibration-auto-apply confirmation phrase.", trade_count=0,
        fingerprint_before=fp, fingerprint_after=fp, auto_applied=False, source="manual",
    )
    _bump_generation()
    return {"auto_apply_enabled": True}


@app.post("/api/confidence-calibration/auto-apply/disable")
async def disable_calibration_auto_apply():
    old_value = config_store.get()["confidence_calibration"]["auto_apply_enabled"]
    fp = config_performance.fingerprint(config_store.get())
    config_store.update({"confidence_calibration": {"auto_apply_enabled": False}})
    if old_value:
        config_performance.log_applied_change(
            config_path="confidence_calibration.auto_apply_enabled", old_value=True, new_value=False,
            rationale="Disabled via the dashboard.", trade_count=0,
            fingerprint_before=fp, fingerprint_after=fp, auto_applied=False, source="manual",
        )
    _bump_generation()
    return {"auto_apply_enabled": False}


@app.get("/api/advisory/recommendations")
async def get_advisory_recommendations():
    # Always safe to call regardless of advisory.enabled - the per-variant
    # data-threshold gate lives inside advisory_engine.generate_recommendations
    # itself (docs/advisory-engine-plan.md §3, layer 2), not here, so there's
    # no route-level check that could accidentally be the only thing standing
    # between an under-sampled variant and a real recommendation.
    adv_cfg = config_store.get()["advisory"]
    if not adv_cfg["enabled"]:
        return {"recommendations": [], "gated_reason": "advisory engine is disabled", "resolved_count": None}

    cfg = config_store.get()
    current_fp = config_performance.fingerprint(cfg)
    all_rows = trade_analytics.build_trade_history([t.to_dict() for t in broker.trade_log])
    market_rows = trade_analytics.build_trade_history([t.to_dict() for t in market_broker.trade_log])
    variants = {v["fingerprint"]: v for v in config_performance.all_variants()}
    result = advisory_engine.generate_recommendations(
        all_rows, cfg, current_fp, variants, adv_cfg["min_resolved_trades_per_variant"], market_rows=market_rows,
        gate_summaries=candidate_log.gate_summary(),
        last_applied_by_path=config_performance.all_last_applied_by_path(),
        series_evaluator_rows=_series_evaluator_overview_with_crosscheck(cfg),
        category_rows=regime_analytics.by_category(all_rows),
        declined_ids=suggestion_decisions.declined_ids(),
    )
    return result


@app.post("/api/advisory/recommendations/apply")
async def apply_advisory_recommendation(body: ApplyRecommendationBody):
    # Manual apply path (docs/advisory-engine-plan.md §4) - always available
    # regardless of auto_apply_enabled, always a human-initiated click.
    # Recommendations are recomputed fresh here rather than trusting
    # whatever the request body claims a value should be - only a
    # recommendation this call just derived itself can ever be applied.
    adv_cfg = config_store.get()["advisory"]
    if not adv_cfg["enabled"]:
        raise HTTPException(status_code=400, detail="advisory engine is disabled")

    cfg = config_store.get()
    current_fp = config_performance.fingerprint(cfg)
    all_rows = trade_analytics.build_trade_history([t.to_dict() for t in broker.trade_log])
    market_rows = trade_analytics.build_trade_history([t.to_dict() for t in market_broker.trade_log])
    variants = {v["fingerprint"]: v for v in config_performance.all_variants()}
    result = advisory_engine.generate_recommendations(
        all_rows, cfg, current_fp, variants, adv_cfg["min_resolved_trades_per_variant"], market_rows=market_rows,
        gate_summaries=candidate_log.gate_summary(),
        last_applied_by_path=config_performance.all_last_applied_by_path(),
        series_evaluator_rows=_series_evaluator_overview_with_crosscheck(cfg),
        category_rows=regime_analytics.by_category(all_rows),
        declined_ids=suggestion_decisions.declined_ids(),
    )
    match = next((r for r in result["recommendations"] if r["id"] == body.id), None)
    if match is None:
        raise HTTPException(
            status_code=404,
            detail="recommendation not found - it may be stale (config or trade history changed since it was fetched)",
        )

    # config_path is always exactly "<top-level section>.<field>" (see every
    # suggestion function in advisory_engine.py) - strategy.* and, since
    # 2026-08-10's unified engine, market_strategy.* too, so this can no
    # longer assume "strategy." is the only prefix a recommendation carries.
    section, _, field = match["config_path"].partition(".")
    config_store.update({section: {field: match["suggested_value"]}})
    new_fp = config_performance.fingerprint(config_store.get())
    config_performance.log_applied_change(
        config_path=match["config_path"], old_value=match["current_value"], new_value=match["suggested_value"],
        rationale=match["rationale"], trade_count=match["n"],
        fingerprint_before=current_fp, fingerprint_after=new_fp, auto_applied=False, source="unified-advisory",
    )
    _bump_generation()
    return {"applied": match, "new_config": config_store.get()["strategy"]}


class DeclineSuggestionBody(BaseModel):
    id: str
    config_path: str
    rationale: str | None = None


@app.post("/api/suggestions/decline")
async def decline_suggestion(body: DeclineSuggestionBody):
    # History tab redesign (2026-08-14/15 direct request) - "choose to hold
    # back" needs to actually stick, not just hide a card until the next
    # poll re-fetches the exact same suggestion. No staleness check needed
    # here unlike the apply route above - declining an id that's already
    # gone from the live recommendation set (or was never real) is still a
    # perfectly valid "no thanks," it just has nothing left to suppress.
    suggestion_decisions.decline(body.id, body.config_path, body.rationale)
    _bump_generation()
    return {"declined": True, "id": body.id}


class UndeclineSuggestionBody(BaseModel):
    id: str


@app.post("/api/suggestions/undecline")
async def undecline_suggestion(body: UndeclineSuggestionBody):
    # "You can revisit this anytime" - the Advanced-section "previously
    # declined" list's per-row undo action.
    existed = suggestion_decisions.undecline(body.id)
    _bump_generation()
    return {"undeclined": existed, "id": body.id}


@app.get("/api/suggestions/declined")
async def get_declined_suggestions(limit: int = 50):
    return {"declined": suggestion_decisions.list_declined(limit)}


@app.get("/api/advisory/applied-changes")
async def get_advisory_applied_changes(limit: int = 50, offset: int = 0):
    # Effect tracking (Item 3D, 2026-08-10): a strategy.* change already
    # gets a real fingerprint transition logged (fingerprint_before/after) -
    # attach the same before/after win-rate + realized-P&L a cross-variant
    # recommendation already computes, via variant_summaries() over the
    # *current* full trade history (not a snapshot from when the change was
    # applied), so this reflects everything resolved since, not just what
    # existed the moment it was logged. Scoped to config_path.startswith
    # ("strategy.") specifically, not just "fingerprint changed" - a manual
    # patch can touch a strategy.* field and a non-fingerprinted field (e.g.
    # risk.*) in the same save, and only the strategy.* row's own change is
    # what actually caused that transition.
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    changes = config_performance.recent_applied_changes(limit=limit, offset=offset)
    all_rows = trade_analytics.build_trade_history([t.to_dict() for t in broker.trade_log])
    market_rows = trade_analytics.build_trade_history([t.to_dict() for t in market_broker.trade_log])
    summaries = advisory_engine.variant_summaries(all_rows)
    for c in changes:
        c["effect"] = (
            advisory_engine.change_effect(c["fingerprint_before"], c["fingerprint_after"], summaries)
            if c["config_path"].startswith("strategy.") else None
        )
        # Gap 3 of docs/config-tuning-data-gaps-2026-08-10.md - a looser,
        # complementary measurement alongside the strict one above: works
        # for any config_path (not just strategy.*, and with no fingerprint-
        # transition requirement), and isn't starved by fingerprint
        # fragmentation since it counts every trade before/after applied_at
        # regardless of which exact config variant produced it. Picks
        # market_broker's own trade history for market_strategy.* changes -
        # that strategy's trades never appear in the whale-follow broker's
        # own log at all.
        rows_for_path = market_rows if c["config_path"].startswith("market_strategy.") else all_rows
        c["effect_windowed"] = advisory_engine.change_effect_windowed(c["config_path"], c["applied_at"], rows_for_path)
    return {
        "changes": changes,
        "total": config_performance.applied_changes_count(),
    }


@app.get("/api/confidence-calibration/status")
async def get_confidence_calibration_status():
    # Same "honest progress even while gated" idiom as /api/advisory/status -
    # never leaks a real report early, but useful to show real progress
    # toward the threshold while waiting.
    cc_cfg = config_store.get()["confidence_calibration"]
    resolved_count = len(signal_log.resolved_signals_with_factors())
    return {
        "enabled": cc_cfg["enabled"],
        "min_resolved_signals": cc_cfg["min_resolved_signals"],
        "resolved_count": resolved_count,
        "ready": resolved_count >= cc_cfg["min_resolved_signals"],
        "auto_apply_enabled": cc_cfg.get("auto_apply_enabled", False),
    }


@app.get("/api/confidence-calibration/report")
async def get_confidence_calibration_report():
    # Always safe to call regardless of confidence_calibration.enabled - the
    # data-threshold gate lives inside generate_calibration_report() itself
    # (services/confidence_calibration.py), not here, matching advisory's
    # own route-level pattern.
    cc_cfg = config_store.get()["confidence_calibration"]
    if not cc_cfg["enabled"]:
        return {"report": None, "gated_reason": "confidence calibration is disabled", "resolved_count": None}

    rows = signal_log.resolved_signals_with_factors()
    current_weights = config_store.get().get("whale_confidence_weights")
    return confidence_calibration.generate_calibration_report(rows, cc_cfg["min_resolved_signals"], current_weights)


@app.get("/api/confidence-calibration/history")
async def get_confidence_calibration_history(limit: int = 100):
    # services/calibration_history.py - Gap 6 of docs/config-tuning-data-
    # gaps-2026-08-10.md. Always safe to call - the trend line these
    # snapshots build up, distinct from the live report above.
    limit = min(max(limit, 1), 500)
    return {"snapshots": calibration_history.history(limit=limit)}


@app.get("/api/market-strategy-calibration/status")
async def get_market_strategy_calibration_status():
    # "Web of expertise" audit (2026-08-11) gap #5 - same status-route shape
    # as the whale-side /api/confidence-calibration/status above.
    msc_cfg = config_store.get()["market_strategy_calibration"]
    resolved_count = len(trade_analytics.build_trade_history([t.to_dict() for t in market_broker.trade_log]))
    return {
        "enabled": msc_cfg["enabled"],
        "min_resolved_trades": msc_cfg["min_resolved_trades"],
        "resolved_count": resolved_count,
        "ready": resolved_count >= msc_cfg["min_resolved_trades"],
    }


@app.get("/api/market-strategy-calibration/report")
async def get_market_strategy_calibration_report():
    msc_cfg = config_store.get()["market_strategy_calibration"]
    if not msc_cfg["enabled"]:
        return {"report": None, "gated_reason": "market-native calibration is disabled", "resolved_count": None}
    rows = trade_analytics.build_trade_history([t.to_dict() for t in market_broker.trade_log])
    return market_strategy_calibration.generate_calibration_report(rows, msc_cfg["min_resolved_trades"])


@app.get("/api/candidate-log/summary")
async def get_candidate_log_summary():
    # services/candidate_log.py - Gap 1 of docs/config-tuning-data-gaps-
    # 2026-08-10.md. Always safe to call, no enable flag: this data
    # collects passively from every gate check regardless of any config
    # toggle, same as signal_log itself.
    return {"gates": candidate_log.gate_summary()}


@app.get("/api/position-netting/groups")
async def get_position_netting_groups():
    # services/position_netting.py - read-only, safe to call anytime
    # regardless of position_netting.enabled (same "observe before you
    # choose to act" principle as the rest of this app's history/advisory
    # surfaces). Lets the user see exactly how any currently-open
    # mutually-exclusive-event group (a real hedge/concentration pattern
    # or not) is classified before ever turning automated action on.
    return {"groups": position_netting.describe_groups(
        broker, state["market_titles"], state["event_titles"], state["latest_prices"], config_store.get(),
    )}


@app.get("/api/cross-strategy/comparison")
async def get_cross_strategy_comparison():
    # services/cross_strategy.py - Gap 7 of docs/config-tuning-data-gaps-
    # 2026-08-10.md, and the user's own direct question this session.
    # Always safe to call, no enable flag - a pure read over trades both
    # strategies have already placed.
    whale_rows = trade_analytics.build_trade_history([t.to_dict() for t in broker.trade_log])
    market_rows = trade_analytics.build_trade_history([t.to_dict() for t in market_broker.trade_log])
    return {
        "aggregate": cross_strategy.aggregate_comparison(whale_rows, market_rows),
        "ticker_overlap": cross_strategy.ticker_overlap(whale_rows, market_rows),
    }


@app.get("/api/regime/by-hour")
async def get_regime_by_hour(strategy: str = "whale_follow"):
    # services/regime_analytics.py - Gap 9 of docs/config-tuning-data-gaps-
    # 2026-08-10.md. Always safe to call, no enable flag.
    trade_log = market_broker.trade_log if strategy == "market_native" else broker.trade_log
    rows = trade_analytics.build_trade_history([t.to_dict() for t in trade_log])
    return {"strategy": strategy, "buckets": regime_analytics.by_hour_of_day(rows)}


@app.get("/api/regime/by-day-of-week")
async def get_regime_by_day_of_week(strategy: str = "whale_follow"):
    trade_log = market_broker.trade_log if strategy == "market_native" else broker.trade_log
    rows = trade_analytics.build_trade_history([t.to_dict() for t in trade_log])
    return {"strategy": strategy, "buckets": regime_analytics.by_day_of_week(rows)}


@app.get("/api/regime/by-category")
async def get_regime_by_category(strategy: str = "whale_follow"):
    # services/trade_category.py - the deferred category half of Gap 9,
    # docs/config-tuning-data-gaps-2026-08-10.md. Always safe to call -
    # naturally empty until enough trades placed after this shipped have a
    # recorded category, same "auto-enables once there's real data"
    # pattern every other gate in this app already uses.
    trade_log = market_broker.trade_log if strategy == "market_native" else broker.trade_log
    rows = trade_analytics.build_trade_history([t.to_dict() for t in trade_log])
    return {"strategy": strategy, "buckets": regime_analytics.by_category(rows)}


@app.get("/api/regime/by-series")
async def get_regime_by_series(strategy: str = "whale_follow"):
    # services/regime_analytics.py's by_series() - 2026-08-16 direct
    # request, the finest of the three segmentation tiers. Always safe to
    # call, no enable flag, no trade_category.py dependency (series is a
    # pure function of the ticker).
    trade_log = market_broker.trade_log if strategy == "market_native" else broker.trade_log
    rows = trade_analytics.build_trade_history([t.to_dict() for t in trade_log])
    return {"strategy": strategy, "buckets": regime_analytics.by_series(rows)}


@app.get("/api/regime/by-subcategory")
async def get_regime_by_subcategory(strategy: str = "whale_follow"):
    # services/regime_analytics.py's by_subcategory() - 2026-08-16 direct
    # follow-up, the middle tier between by_series and by_category. Same
    # "auto-enables once there's real data" pattern as by_category - empty
    # until trades placed after this shipped have a recorded subcategory
    # (sports events only; see services/trade_category.py).
    trade_log = market_broker.trade_log if strategy == "market_native" else broker.trade_log
    rows = trade_analytics.build_trade_history([t.to_dict() for t in trade_log])
    return {"strategy": strategy, "buckets": regime_analytics.by_subcategory(rows)}


@app.get("/api/backtest/entry-threshold")
async def get_backtest_entry_threshold():
    # services/backtest.py - Gap 2 of docs/config-tuning-data-gaps-2026-08-
    # 10.md, stateless replay against every already-logged resolved signal.
    # Always safe to call - pure read, no enable flag.
    rows = signal_log.resolved_signals_with_factors()
    current_threshold = config_store.get()["strategy"]["entry_threshold"]
    return {"current_threshold": current_threshold, "sweep": backtest.entry_threshold_sweep(rows)}


@app.get("/api/backtest/min-whale-winrate")
async def get_backtest_min_whale_winrate():
    strat_cfg = config_store.get()["strategy"]
    series_stats = signal_log.all_series_stats(days=30)
    signal_rows = signal_log.resolved_signals_with_series(days=30)
    sweep = backtest.min_whale_winrate_pct_sweep(
        series_stats, signal_rows, min_resolved_for_filter=strat_cfg.get("min_resolved_for_whale_filter", 10),
    )
    return {"current_floor": strat_cfg.get("min_whale_winrate_pct", 40), "sweep": sweep}


@app.get("/api/market-analyst/status")
async def get_market_analyst_status():
    # Same "honest progress even while gated/disconnected" idiom as
    # advisory/confidence-calibration's own status routes. api_key_configured
    # is reported separately from enabled - both gate the feature (see
    # _run_market_analyst_for_ticker), and a user turning the checkbox on
    # with no key set should see *why* the Analyze button won't do anything.
    ma_cfg = config_store.get()["market_analyst"]
    return {
        "enabled": ma_cfg["enabled"],
        "api_key_configured": bool(os.environ.get("ANTHROPIC_API_KEY")),
        **market_analyst_agent.stats(days=30),
    }


class MarketAnalystAnalyzeBody(BaseModel):
    ticker: str


@app.post("/api/market-analyst/analyze")
async def post_market_analyst_analyze(body: MarketAnalystAnalyzeBody):
    # On-demand trigger, direct request (2026-08-09) - replaces the earlier
    # automatic per-tick background scan (see _run_market_analyst_for_ticker's
    # own docstring for why: this is the only thing in this app that spends
    # real money per call). A human clicks "Analyze" on one specific market
    # they're actually looking at; nothing runs on a schedule anymore.
    cfg = config_store.get()
    client = KalshiClient(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])
    try:
        return await _run_market_analyst_for_ticker(client, cfg, body.ticker)
    finally:
        await client.close()


@app.get("/api/market-analyst/analyses")
async def get_market_analyst_analyses(limit: int = 25, offset: int = 0, resolved_only: bool = False):
    # Always safe to call regardless of market_analyst.enabled - past
    # analyses stay visible/inspectable even after the feature's turned off,
    # same as every other history panel in this app.
    return {
        "rows": market_analyst_agent.recent(limit=limit, offset=offset, resolved_only=resolved_only),
        "total": market_analyst_agent.total_count(resolved_only=resolved_only),
    }


class MarketAnalystSeriesAnalyzeBody(BaseModel):
    series: str


@app.post("/api/market-analyst/series/analyze")
async def post_market_analyst_series_analyze(body: MarketAnalystSeriesAnalyzeBody):
    # Per-series analysis mode (Item 3B, 2026-08-10, direct request) -
    # button lives on the series-evaluator log panel (Item 1), analyzing
    # one whole series' whale-signal + closed-trade performance rather than
    # a single market's own probability. Same "deliberate human click, not
    # background spend" reasoning as the single-market Analyze button.
    cfg = config_store.get()
    return await _run_series_analysis(cfg, body.series)


class MarketAnalystSeriesApplyBody(BaseModel):
    analysis_id: str
    suggestion_id: str


@app.post("/api/market-analyst/series/apply")
async def post_market_analyst_series_apply(body: MarketAnalystSeriesApplyBody):
    # Applies one suggestion from a *persisted* series analysis - looked up
    # by (analysis_id, suggestion_id) rather than trusting whatever
    # config_path/suggested_value the request body might claim, same
    # never-trust-the-client principle as the rule-based Advisory apply
    # route. Unlike that route, this can't recompute the suggestion fresh
    # (an LLM's raw output isn't deterministically reproducible the way a
    # rule-based one is) - the persisted, server-computed value at analysis
    # time is the trusted source of truth instead.
    analysis = market_analyst_agent.get_series_analysis(body.analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="Series analysis not found.")
    match = next((s for s in analysis["suggestions"] if s["id"] == body.suggestion_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail="Suggestion not found on this analysis.")

    cfg_before = config_store.get()
    # Staleness check (direct report 2026-08-11: "make sure the suggested
    # values arent stale") - this suggestion's current_value was captured
    # when the analysis ran, not recomputed just now (see the docstring
    # above on why this route can't do what the rule-based Advisory apply
    # route does). If the live config has moved since - a manual edit, an
    # auto-apply, or a second analysis touching the same field - applying
    # this suggestion would silently overwrite based on a premise that's no
    # longer true, and the audit trail would log a "before" value that was
    # never actually live at apply time.
    live_value = _config_value_at_path(cfg_before, match["config_path"])
    if live_value != match["current_value"]:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Stale suggestion - {match['config_path']} is now {live_value!r}, not the "
                f"{match['current_value']!r} this suggestion was based on. Re-run the analysis and try again."
            ),
        )
    fp_before = config_performance.fingerprint(cfg_before)
    section, _, field = match["config_path"].partition(".")
    config_store.update({section: {field: match["suggested_value"]}})
    fp_after = config_performance.fingerprint(config_store.get())
    config_performance.log_applied_change(
        config_path=match["config_path"], old_value=match["current_value"], new_value=match["suggested_value"],
        rationale=match["rationale"], trade_count=0,
        fingerprint_before=fp_before, fingerprint_after=fp_after, auto_applied=False, source="series-analyst",
    )
    _bump_generation()
    return {"applied": match, "new_config": config_store.get()["strategy"]}


@app.post("/api/market-analyst/full-spectrum/analyze")
async def post_market_analyst_full_spectrum_analyze():
    # "Feed the Analyst" (Item 3C, 2026-08-10, direct request) - one button
    # (History tab), confirm-gated client-side given this prompt is
    # materially bigger/costlier than the single-market/per-series modes
    # and no cost/latency numbers exist anywhere for this agent yet.
    cfg = config_store.get()
    return await _run_full_spectrum_analysis(cfg)


class MarketAnalystFullSpectrumApplyBody(BaseModel):
    analysis_id: str
    suggestion_id: str


@app.post("/api/market-analyst/full-spectrum/apply")
async def post_market_analyst_full_spectrum_apply(body: MarketAnalystFullSpectrumApplyBody):
    # Same persisted-lookup trust model as the per-series apply route above -
    # looked up by (analysis_id, suggestion_id) against what was actually
    # validated and persisted at analysis time (main.py's
    # _full_spectrum_suggestions_from_raw already confirmed the config_path
    # exists and isn't one of the two protected fields), never whatever the
    # request body itself claims.
    analysis = market_analyst_agent.get_full_spectrum_analysis(body.analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="Full-spectrum analysis not found.")
    match = next((s for s in analysis["suggestions"] if s["id"] == body.suggestion_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail="Suggestion not found on this analysis.")

    cfg_before = config_store.get()
    # Staleness check - see the identical guard on the series-apply route
    # above for the full reasoning.
    live_value = _config_value_at_path(cfg_before, match["config_path"])
    if live_value != match["current_value"]:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Stale suggestion - {match['config_path']} is now {live_value!r}, not the "
                f"{match['current_value']!r} this suggestion was based on. Re-run the analysis and try again."
            ),
        )
    fp_before = config_performance.fingerprint(cfg_before)
    section, _, field = match["config_path"].partition(".")
    config_store.update({section: {field: match["suggested_value"]}})
    fp_after = config_performance.fingerprint(config_store.get())
    config_performance.log_applied_change(
        config_path=match["config_path"], old_value=match["current_value"], new_value=match["suggested_value"],
        rationale=match["rationale"], trade_count=0,
        fingerprint_before=fp_before, fingerprint_after=fp_after, auto_applied=False, source="full-spectrum-analyst",
    )
    _bump_generation()
    return {"applied": match, "new_config": config_store.get()}


def _series_evaluator_overview_with_crosscheck(cfg: dict) -> list[dict]:
    """series_evaluator.overview() enriched with the real win-rate
    cross-check (Gap 4/10 of docs/config-tuning-data-gaps-2026-08-10.md) -
    factored out of GET /api/series-evaluator/status (phase 82) so
    advisory_engine's own series-evaluator suggestions (2026-08-11, "web
    of expertise" audit) can reuse the exact same enrichment instead of
    duplicating it. series_evaluator judges a series by *qualifying rate*
    (real trades observed vs. how many cleared the notional threshold),
    strategy_engine.py's own min_whale_winrate_pct gate judges it by
    *realized win rate* - two genuinely independent mechanisms this
    attaches to each other."""
    strat_cfg = cfg["strategy"]
    win_rate_floor = strat_cfg.get("min_whale_winrate_pct", 40)
    min_resolved_for_filter = strat_cfg.get("min_resolved_for_whale_filter", 10)
    win_stats = signal_log.all_series_stats(days=30)
    series_rows = series_evaluator.overview()
    for row in series_rows:
        stat = win_stats.get(row["series"]) or {"resolved": 0, "win_rate": None}
        row["whale_resolved"] = stat["resolved"]
        row["whale_win_rate"] = stat["win_rate"]
        row["below_winrate_floor"] = (
            stat["resolved"] >= min_resolved_for_filter
            and stat["win_rate"] is not None and stat["win_rate"] < win_rate_floor
        )
        # Gap 10 (docs/config-tuning-data-gaps-2026-08-10.md) - the real
        # margin of error around this series' observed win rate, so
        # "below_winrate_floor" reads as more than a bare true/false: a
        # series barely under the floor with a wide margin (thin n) is a
        # different situation than one clearly under it with a tight one.
        row["whale_win_rate_margin_pts"] = (
            stats_power.margin_of_error_pts(stat["resolved"], stat["win_rate"])
            if stat["win_rate"] is not None else None
        )
    return series_rows


@app.get("/api/series-evaluator/status")
async def get_series_evaluator_status():
    # Every series ever evaluated, independent of what's on the *current*
    # watchlist - direct request: the log/history the user wanted, doubling
    # as the persisted series_status table itself (see services/
    # series_evaluator.py). Always safe to call regardless of enabled -
    # same "history stays visible after a feature's turned off" idiom as
    # market_analyst's own status route above.
    se_cfg = config_store.get().get("series_evaluator") or {}
    series_rows = _series_evaluator_overview_with_crosscheck(config_store.get())
    return {"enabled": bool(se_cfg.get("enabled")), "series": series_rows}


class SeriesEvaluatorResetBody(BaseModel):
    series: str


@app.post("/api/series-evaluator/reset")
async def post_series_evaluator_reset(body: SeriesEvaluatorResetBody):
    # The manual "Re-evaluate" action - a deliberate fresh start (clears
    # strike_count too, see series_evaluator.reset's own docstring), not a
    # continuation of prior escalation. Doesn't force-pin the series back
    # onto the watchlist - normal volume/live-status ranking still decides
    # whether it actually reappears.
    ok = series_evaluator.reset(body.series)
    if not ok:
        raise HTTPException(status_code=404, detail=f"No series_evaluator record for {body.series!r}")
    return {"ok": True, "series": body.series}


@app.get("/api/market-strategy/state")
async def get_market_strategy_state():
    # Backend-only at first (docs/advisory-engine-plan.md §9-adjacent,
    # direct request 2026-08-08) - now the real data source for the
    # dedicated Market-Native tab (2026-08-10, direct request: "the
    # market-native strategy seems to have stalled, and i think itd be
    # good to have its own tab now"). Reuses trade_analytics as-is
    # (strategy-agnostic - it only ever reads Trade dicts) rather than
    # reimplementing summary stats for a second broker.
    market_cfg = config_store.get()["market_strategy"]
    all_rows = trade_analytics.build_trade_history([t.to_dict() for t in market_broker.trade_log])
    scoped_market_titles = _scoped_market_titles(_relevant_tickers())
    return {
        "enabled": market_cfg["enabled"],
        "broker": {**market_broker.state(state["latest_prices"]), "recent_trades": _enrich_recent_trades(market_broker)},
        "summary": trade_analytics.compute_summary(all_rows),
        "risk": {"halted": market_risk.halted, "halt_reason": market_risk.halt_reason},
        "decision_feed": state["market_decision_feed"],
        "market_titles": scoped_market_titles,
        "latest_prices": state["latest_prices"],
    }


@app.get("/api/market-history/summary")
async def get_market_history_summary():
    return {
        "tracked_tickers": market_history.tracked_ticker_count(),
        "total_snapshots": market_history.snapshot_count(),
        "resolved_outcomes": market_history.outcome_count(),
    }


@app.get("/api/market-history/hypothetical-trades")
async def get_market_history_hypothetical_trades():
    # Retrospective, explicitly hypothetical (see market_history.py's
    # docstring) - never a claim about a real position. Computed on demand
    # from logged snapshots/outcomes, not separately persisted.
    return {"trades": market_history.compute_hypothetical_trades()}


@app.get("/api/market-catalog/status")
async def get_market_catalog_status():
    # Honest progress reporting (same idiom as /api/advisory/status) for
    # market_catalog.py's incremental background scan - lets the Config tab
    # or a curl check say "X series scanned, Y near-term markets known"
    # instead of the catalog being an opaque, silently-filling-in cache.
    cfg = config_store.get()
    progress = market_catalog.scan_progress()
    progress["enabled"] = bool(cfg["kalshi"].get("live_markets_only"))
    return progress


@app.get("/api/markets/search")
async def search_markets(q: str = "", min_volume: float = 0, category: str = "", limit: int = 50, live_only: bool = False):
    # On-demand market search/browse (ROADMAP.md Phase 0.5) - distinct from
    # the automatic watchlist selection (_fetch_markets), which stays
    # volume-filtered by config default (kalshi.min_volume_24h). Defaults to
    # min_volume=0 - full catalog access, dormant markets included - so a
    # market being excluded from the automatic watchlist never means it's
    # unreachable, only that it's not the default view.
    #
    # Series-based, same as the automatic watchlist and for the same
    # reason: an early version of this endpoint browsed individual markets
    # directly (even paginating 5000+ of them for a text query) and that
    # turned out fundamentally unreliable - confirmed directly, repeatedly,
    # with real numbers - Kalshi's combo/MVE markets are generated in such
    # bulk that a flat browse of even tens of thousands of markets can
    # still contain zero real matches. Text-matching against ~9,400
    # series (title/tags/category), a much smaller and cleanly-labeled
    # set, then querying only the matching series directly, is what
    # actually works.
    cfg = config_store.get()
    client = KalshiClient(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])
    try:
        all_series = await _get_series_cache(client)  # already sorted by volume_fp desc
        q_lower = q.strip().lower()
        category_lower = category.strip().lower()
        candidates = all_series
        if q_lower:
            candidates = [
                s for s in candidates
                if q_lower in (s.get("title") or "").lower()
                or q_lower in " ".join(s.get("tags") or []).lower()
                or q_lower in (s.get("category") or "").lower()
                or q_lower in (s.get("ticker") or "").lower()
            ]
        if category_lower:
            candidates = [s for s in candidates if (s.get("category") or "").lower() == category_lower]

        # Caps how many series to fan out to (a network request each), not
        # how many markets come back - candidates is already volume-sorted.
        candidate_tickers = [s["ticker"] for s in candidates[:30]]
        if live_only:
            # Prefer market_catalog (see _scan_catalog_batch) if it already
            # has near-term data for the matched series - same reasoning as
            # _fetch_markets' discovery path (volume-ranking a fresh 30-
            # series fetch misses almost everything actually live right
            # now). Falls back to a fresh live fetch when the catalog has
            # nothing for these specific series yet (e.g. the background
            # scan hasn't reached them, or kalshi.live_markets_only has
            # never been turned on) - search must still work even before
            # the catalog's built up, just less completely.
            now = time.time()
            catalog_candidates = market_catalog.candidates_in_window(
                now, lookahead_sec=_LIVE_STATUS_LOOKAHEAD_SEC, lookback_sec=_LIVE_STATUS_LOOKBACK_SEC,
                min_volume=min_volume,
            )
            if q_lower or category_lower:
                # A real search/category narrowing is active - scope the
                # catalog to the (untruncated) matched series, not just the
                # top 30 by volume that candidate_tickers caps at below,
                # which would otherwise throw away most of the catalog's
                # own breadth advantage for a search that matched more than
                # 30 series.
                matched_series = {s["ticker"] for s in candidates}
                catalog_candidates = [m for m in catalog_candidates if m.get("series_ticker") in matched_series]
            market_candidates = catalog_candidates or await client.get_candidate_markets(
                min_volume=min_volume, series_tickers=candidate_tickers,
            )
            live_status = await _fetch_live_status(client, market_candidates)
            live_candidates = [
                m for m in market_candidates
                if live_status.get(m.get("event_ticker")) == "live"
            ]
            markets = KalshiClient.round_robin_select(
                live_candidates, limit, max_children_per_parent=cfg["kalshi"].get("max_children_per_parent"),
            )
        else:
            markets = await client.get_top_volume_markets(
                limit, min_volume=min_volume, series_tickers=candidate_tickers,
                max_children_per_parent=cfg["kalshi"].get("max_children_per_parent"),
            )
        results = markets
        # Opportunistically cache titles/events for whatever this search
        # touched, same shape _fetch_markets already populates - so a result
        # added to the watchlist afterward already has a label, no gap.
        searched_titles = {
            m["ticker"]: {
                **title_cache.market_title_fields(m), "event_ticker": m.get("event_ticker"),
                "legs": m.get("mve_selected_legs") or None,
            }
            for m in results if m.get("ticker")
        }
        state["market_titles"].update(searched_titles)
        title_cache.save_market_titles(searched_titles)
        _bump_generation()  # market_titles changed - invalidate the cached /api/state body, see _build_state_body
        return {
            "markets": [_slim_market(m) for m in results],
            "market_titles": {m["ticker"]: state["market_titles"][m["ticker"]] for m in results if m.get("ticker")},
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        await client.close()


_state_body_cache = {"generation": None, "body": None}  # see get_state()


def _build_state_body() -> dict:
    # Rebuilding this means running signal_log.stats()/shadow.recent()/
    # shadow.stats() (each a real SQLite query) plus broker.state() - real
    # but small work, and completely pointless to repeat for two requests
    # that land inside the same poll-tick generation (two browser tabs, or a
    # conditional GET that still needs the body because the client had no
    # prior ETag). Memoized on state["generation"] rather than a time-based
    # TTL so it's exact, not approximate: invalidated exactly when something
    # that would change the response actually happened, see _bump_generation.
    if _state_body_cache["generation"] == state["generation"]:
        return _state_body_cache["body"]
    scoped_market_titles = _scoped_market_titles(_relevant_tickers())
    # Augment market_titles with any event-level flags (so the UI can read
    # `mutually_exclusive` / `collateral_return_type` without an extra lookup)
    event_info = _scoped_event_titles(scoped_market_titles)
    event_live_data = _scoped_event_live_data(scoped_market_titles)
    live_game_state = _scoped_live_game_state(scoped_market_titles)
    augmented_market_titles = {}
    for t, mt in scoped_market_titles.items():
        et = mt.get("event_ticker")
        ev = event_info.get(et) or {}
        augmented_market_titles[t] = {**mt, "mutually_exclusive": ev.get("mutually_exclusive"), "collateral_return_type": ev.get("collateral_return_type")}
    body = {
        "running": state["running"],
        "markets": state["markets"],
        "market_titles": augmented_market_titles,
        "event_titles": event_info,
        "event_live_data": event_live_data,
        "live_game_state": live_game_state,
        "category_metadata": {
            "tags_by_categories": state["category_metadata"].get("tags_by_categories") or {},
            "filters_by_sports": state["category_metadata"].get("filters_by_sports") or {},
            "sport_ordering": state["category_metadata"].get("sport_ordering") or [],
        },
        "trade_tape": state["trade_tape"],
        "live_status": state["live_status"],
        "latest_prices": state["latest_prices"],
        "signal_feed": state["signal_feed"],
        "decision_feed": state["decision_feed"],
        "stats": state["stats"],
        "equity_history": state["equity_history"],
        "real_balance_history": state["real_balance_history"],
        "whale_track_record": signal_log.stats(days=30),
        "series_track_record": state["series_track_record"],
        "series_meta": _series_meta_map({r["series"] for r in state["series_track_record"].values()}),
        "last_poll": state["last_poll"],
        "last_tick_duration_sec": state["last_tick_duration_sec"],
        "last_tick_rate_limit_hits": state["last_tick_rate_limit_hits"],
        # Real bug found and fixed 2026-08-15, same session that added
        # me_pairs in the first place: _build_state_body() is a curated
        # whitelist, not a passthrough of the whole state dict, and this key
        # was never added to it - the ME-pair entry gate itself worked
        # correctly (main.py's own internal use of state["me_pairs"] never
        # went through this function), but nothing outside the process could
        # ever see which pairs were currently detected. An earlier "curl and
        # check" verification missed this because it read
        # `(resp.get("me_pairs") or {})` - the `or {}` silently produced the
        # same empty-looking result whether the key was present-but-empty or
        # missing entirely.
        "me_pairs": state["me_pairs"],
        "error": state["error"],
        "whale_source": state["whale_source"],
        "risk": {"halted": risk.halted, "halt_reason": risk.halt_reason},
        "broker": _enriched_broker_state(broker, state["latest_prices"]),
        "account": state["account"],
        "exchange_status": state["exchange_status"],
        "trade_stream_status": state["trade_stream_status"],
        "shadow": _shadow_state(),
    }
    _state_body_cache["generation"] = state["generation"]
    _state_body_cache["body"] = body
    return body


def _if_none_match_hits(header_value: str | None, etag: str) -> bool:
    # nginx's gzip module rewrites a strong ETag to weak (adds a "W/" prefix)
    # on the way out - confirmed directly, not assumed (a real curl round
    # trip through the ddev proxy came back "W/\"2\"" for an origin-set
    # `"2"`). Per RFC 7232's weak-comparison rule, "W/" is ignorable for
    # revalidation purposes, so strip it from whatever the client echoes
    # back rather than requiring an exact byte match that this proxy chain
    # will never actually send.
    if not header_value:
        return False
    incoming = header_value.strip()
    if incoming.startswith("W/"):
        incoming = incoming[2:]
    return incoming == etag


@app.websocket("/api/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket)


@app.get("/api/state")
async def get_state(request: Request, response: Response):
    # ETag is just the generation counter - cheap to compute, and exact
    # (bumped only on a real change, see _bump_generation/state["generation"]).
    # A poll that lands between real changes (the common case at a 5s
    # frontend interval against a 15s backend poll_interval_sec) costs a
    # conditional request's worth of headers instead of the full ~40KB body,
    # re-fetched and re-parsed for data the dashboard already has.
    etag = f'"{state["generation"]}"'
    if _if_none_match_hits(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "no-cache"  # always revalidate via If-None-Match, never assume freshness
    return _build_state_body()


def _shadow_state() -> dict:
    # Reads config_store fresh rather than the module-level cfg (only ever
    # set once, at import time) - mode can change live via /api/config and
    # this should reflect it immediately, not just after the next poll tick.
    mode = config_store.get().get("mode")
    return {
        "active": mode in ("shadow", "live"),
        "mode": mode,
        "recent_trades": shadow.recent(25),
        **shadow.stats(),
    }



@app.get("/api/config")
async def get_config():
    return config_store.get()


@app.post("/api/config")
async def update_config(body: ConfigPatch):
    if "kalshi_account" in body.patch and "trading_enabled" in (body.patch.get("kalshi_account") or {}):
        raise HTTPException(
            status_code=400,
            detail=(
                "kalshi_account.trading_enabled can't be changed through /api/config — "
                "use POST /api/trading/enable (requires a connected account and a typed "
                "confirmation phrase) or POST /api/trading/disable."
            ),
        )
    if "advisory" in body.patch and "auto_apply_enabled" in (body.patch.get("advisory") or {}):
        raise HTTPException(
            status_code=400,
            detail=(
                "advisory.auto_apply_enabled can't be changed through /api/config — "
                "use POST /api/advisory/auto-apply/enable (requires a typed confirmation "
                "phrase) or POST /api/advisory/auto-apply/disable."
            ),
        )
    if "confidence_calibration" in body.patch and "auto_apply_enabled" in (body.patch.get("confidence_calibration") or {}):
        raise HTTPException(
            status_code=400,
            detail=(
                "confidence_calibration.auto_apply_enabled can't be changed through /api/config — "
                "use POST /api/confidence-calibration/auto-apply/enable (requires a typed "
                "confirmation phrase) or POST /api/confidence-calibration/auto-apply/disable."
            ),
        )
    # Change-history logging (Item 3D, 2026-08-10) - this was the one real
    # gap in config_performance.log_applied_change()'s coverage: every plain
    # Config-tab save went completely unlogged before this, even though the
    # Advisory apply route has always had a full audit trail. Logged AFTER
    # config_store.update() so fingerprint_after reflects the config that
    # actually took effect, but the diff itself is computed against the
    # pre-update snapshot (diff_patch reads old_cfg, not the live store).
    old_cfg = config_store.get()
    fp_before = config_performance.fingerprint(old_cfg)
    changes = config_performance.diff_patch(old_cfg, body.patch)
    new_cfg = config_store.update(body.patch)
    fp_after = config_performance.fingerprint(new_cfg)
    for config_path, old_value, new_value in changes:
        config_performance.log_applied_change(
            config_path=config_path, old_value=old_value, new_value=new_value,
            rationale="Manual edit via the Config tab.", trade_count=0,
            fingerprint_before=fp_before, fingerprint_after=fp_after,
            auto_applied=False, source="manual",
        )
    _bump_generation()
    return new_cfg


@app.post("/api/trading/enable")
async def enable_trading(body: EnableTradingBody):
    if not account.enabled:
        raise HTTPException(
            status_code=400,
            detail="No real Kalshi account is connected — set KALSHI_API_KEY_ID and "
                   "KALSHI_PRIVATE_KEY_PATH in .env first.",
        )
    if body.confirmation_phrase != TRADING_CONFIRMATION_PHRASE:
        raise HTTPException(
            status_code=400,
            detail=f'Confirmation phrase did not match. Type exactly: "{TRADING_CONFIRMATION_PHRASE}"',
        )
    fp_before = config_performance.fingerprint(config_store.get())
    config_store.update({"kalshi_account": {"trading_enabled": True}})
    account.trading_enabled = True  # take effect immediately, not on the next poll tick
    # Logged like any other config change (Item 3D) - this is arguably the
    # single most safety-critical field in the app, so it belongs in the
    # same change-history audit trail as everything else, not a blind spot
    # just because it has its own confirmation-gated route.
    config_performance.log_applied_change(
        config_path="kalshi_account.trading_enabled", old_value=False, new_value=True,
        rationale="Enabled via the typed real-trading confirmation phrase.", trade_count=0,
        fingerprint_before=fp_before, fingerprint_after=fp_before, auto_applied=False, source="manual",
    )
    _bump_generation()
    return {"trading_enabled": True}


@app.post("/api/trading/disable")
async def disable_trading():
    # Always allowed, no confirmation needed — turning real trading back off
    # is never the dangerous direction.
    fp_before = config_performance.fingerprint(config_store.get())
    config_store.update({"kalshi_account": {"trading_enabled": False}})
    account.trading_enabled = False
    config_performance.log_applied_change(
        config_path="kalshi_account.trading_enabled", old_value=True, new_value=False,
        rationale="Disabled real trading.", trade_count=0,
        fingerprint_before=fp_before, fingerprint_after=fp_before, auto_applied=False, source="manual",
    )
    _bump_generation()
    return {"trading_enabled": False}


@app.post("/api/toggle")
async def toggle_running():
    state["running"] = not state["running"]
    _bump_generation()
    return {"running": state["running"]}


@app.post("/api/risk/halt")
async def halt_trading():
    risk.manual_halt("Manually halted from dashboard")
    _bump_generation()
    return {"halted": risk.halted, "halt_reason": risk.halt_reason}


@app.post("/api/risk/resume")
async def resume_trading():
    risk.resume()
    _bump_generation()
    return {"halted": risk.halted, "halt_reason": risk.halt_reason}


@app.post("/api/market-risk/halt")
async def halt_market_native():
    # Same manual halt as /api/risk/halt above, for market_strategy.py's
    # own independent risk manager - previously had no route at all (real
    # gap found live 2026-08-10: market-native's kill switch had tripped
    # and had no way to be manually managed, only services/risk_manager.py's
    # automatic daily rollover fix - see reset_day - could ever clear it).
    market_risk.manual_halt("Manually halted from dashboard")
    _bump_generation()
    return {"halted": market_risk.halted, "halt_reason": market_risk.halt_reason}


@app.post("/api/market-risk/resume")
async def resume_market_native():
    market_risk.resume()
    _bump_generation()
    return {"halted": market_risk.halted, "halt_reason": market_risk.halt_reason}


@app.post("/api/shadow-risk/resume")
async def resume_shadow():
    # Same manual un-halt as the two routes above, for shadow_mode.py's own
    # independent risk tracker - previously had no route at all. Confirmed
    # live (2026-08-10) it had been stuck halted (-99.7%) with no recovery
    # path, dormant only because mode was "paper" at the time.
    shadow.resume()
    _bump_generation()
    return {"halted": shadow.halted, "halt_reason": shadow.halt_reason}


class ResetBody(BaseModel):
    # Each flag wipes an independently-persisted domain — see the Danger Zone
    # panel in the Config tab. Paper defaults on (matches the button's
    # original, sole behavior); shadow/signal_log default off since they're
    # long-run track records that normally survive a paper reset on purpose.
    paper: bool = True
    # Naming for the archive snapshot taken before a paper reset (see
    # services/trade_archive.py). Optional - both default to a generated
    # label/reason - but worth setting when the reset marks a deliberate
    # config experiment, since the label is how epochs are told apart in
    # trade_archive.compare().
    archive_label: str | None = None
    archive_reason: str | None = None
    shadow: bool = False
    signal_log: bool = False
    market_analyst: bool = False
    # market_catalog/market_history had no wired reset path at all despite
    # being the two largest data/*.db files on disk (data-robustness audit
    # finding, 2026-08-10) - market_catalog.clear_all() already existed,
    # written for exactly this, just never called from here.
    market_catalog: bool = False
    market_history: bool = False
    # Bulk-wipe, separate from the per-row POST /api/series-evaluator/reset
    # action - that one is a deliberate single-series re-evaluate; this one
    # is "start the whole series-worthiness log over."
    series_evaluator: bool = False
    candidate_log: bool = False
    calibration_history: bool = False
    # market_strategy.py's own capital pool/risk state previously had no
    # reset path at all (real gap found live 2026-08-10 investigating why
    # it "stalled" - its kill switch had tripped with no way to recover
    # short of editing data/market_risk_state.db by hand). Off by default,
    # same convention as everything except paper itself.
    market_native: bool = False
    trade_category: bool = False
    # Range scoping (2026-08-16 direct request, after a real incident this
    # session spent well over an hour reconstructing from git history and
    # config timestamps: a noisy tuning/dev stretch should be purgeable
    # without losing the valid history on either side of it, instead of
    # every Danger Zone action being all-or-nothing). Only affects the four
    # domains with a real clear_range/count_range (signal_log, candidate_log,
    # trade_category, and paper - scoped to the trades table only, never
    # positions/bankroll/pending_orders, see PaperBroker.clear_trade_range's
    # own docstring for why). Every other domain ignores these and does its
    # existing full clear when selected - unchanged behavior for them.
    # Unix timestamps (seconds); None on a side means unbounded that
    # direction, same "None = no limit" convention used everywhere else in
    # this app. Both None (the default) means "everything", identical to
    # today's behavior.
    range_start: float | None = None
    range_end: float | None = None


def _reset_domain_counts(body: ResetBody) -> dict[str, int | None]:
    """Best-effort 'how many rows would this remove' per selected domain,
    for both /api/reset/preview and the audit-log rows_before column.
    None for domains with no cheap count available (market_catalog/
    market_history/series_evaluator/calibration_history/shadow) rather than
    paying for a full-table scan just for the log - a domain-recorded
    but count-less audit row is still a categorical improvement over
    today's zero record of resets ever happening at all."""
    counts: dict[str, int | None] = {}
    if body.paper:
        counts["paper"] = (
            broker.count_trade_range(body.range_end, body.range_start)
            if (body.range_start or body.range_end) else len(broker.trade_log)
        )
    if body.shadow:
        counts["shadow"] = None
    if body.signal_log:
        counts["signal_log"] = signal_log.count_range(body.range_end, body.range_start)
    if body.market_analyst:
        counts["market_analyst"] = market_analyst_agent.total_count()
    if body.market_catalog:
        counts["market_catalog"] = None
    if body.market_history:
        counts["market_history"] = None
    if body.series_evaluator:
        counts["series_evaluator"] = None
    if body.candidate_log:
        counts["candidate_log"] = candidate_log.count_range(body.range_end, body.range_start)
    if body.calibration_history:
        counts["calibration_history"] = None
    if body.market_native:
        counts["market_native"] = (
            market_broker.count_trade_range(body.range_end, body.range_start)
            if (body.range_start or body.range_end) else len(market_broker.trade_log)
        )
    if body.trade_category:
        counts["trade_category"] = trade_category.count_range(body.range_end, body.range_start)
    return counts


@app.get("/api/reset/preview")
async def reset_preview(
    paper: bool = False, shadow: bool = False, signal_log: bool = False, market_analyst: bool = False,
    market_catalog: bool = False, market_history: bool = False, series_evaluator: bool = False,
    candidate_log: bool = False, calibration_history: bool = False, market_native: bool = False,
    trade_category: bool = False, range_start: float | None = None, range_end: float | None = None,
):
    # Dry-run counterpart to POST /api/reset - same domain/range selection,
    # deletes nothing. Powers the Danger Zone's "here's what you're about
    # to lose" step (2026-08-16 direct request) before the real request
    # fires. Query params, not a body, since this is a GET (no side effects).
    body = ResetBody(
        paper=paper, shadow=shadow, signal_log=signal_log, market_analyst=market_analyst,
        market_catalog=market_catalog, market_history=market_history, series_evaluator=series_evaluator,
        candidate_log=candidate_log, calibration_history=calibration_history, market_native=market_native,
        trade_category=trade_category, range_start=range_start, range_end=range_end,
    )
    return {"counts": _reset_domain_counts(body), "scope": "all" if not (range_start or range_end) else "range"}


@app.get("/api/reset/history")
async def get_reset_history(limit: int = 50):
    # The audit trail /api/reset now writes - 2026-08-16 direct request,
    # after a real incident this session spent well over an hour
    # reconstructing (from git history and config_performance.db
    # timestamps, since nothing recorded a reset had even happened) when
    # and why signal_log.db/paper_broker.db had lost days of history.
    return {"events": reset_log.recent(limit=min(max(limit, 1), 200))}


@app.post("/api/reset")
async def reset_broker(body: ResetBody = ResetBody()):
    cfg = config_store.get()
    cleared = []
    ranged = bool(body.range_start or body.range_end)
    scope = "between" if (body.range_start and body.range_end) else (
        "after" if body.range_start else ("before" if body.range_end else "all")
    )
    counts_before = _reset_domain_counts(body)

    def _log(domain: str, deleted: int | None):
        reset_log.record(
            domain=domain, scope=scope, rows_before=counts_before.get(domain), rows_deleted=deleted,
            range_start=body.range_start, range_end=body.range_end,
        )

    if body.paper:
        # Archive BEFORE anything is destroyed (2026-08-17 direct request:
        # "a safe reset of the paper trading mechanic while maintaining a
        # log of important data"). The motivating incident is concrete: a
        # prior reset left paper_broker.db reaching back only to 08/16
        # 19:28, so every trade-level question about anything earlier -
        # realised win rate, mean entry unit cost, exit breakdown - was
        # unanswerable. reset_log recorded that a reset happened; nothing
        # recorded what it removed.
        #
        # Runs for ranged resets too: a scoped delete still destroys closed
        # history, which is exactly the evidence this preserves.
        archive_result = trade_archive.archive_epoch(
            label=body.archive_label or f"pre-reset {time.strftime('%Y-%m-%d %H:%M')}",
            reason=body.archive_reason or f"automatic archive before {scope} paper reset",
            cfg=cfg,
        )
        cleared.append({"domain": "archive", "epoch": archive_result})
        if ranged:
            # Scoped: only the trades table (closed history) - never
            # positions/bankroll/pending_orders, which are current live
            # state, not history to prune. See PaperBroker.clear_trade_range.
            deleted = broker.clear_trade_range(body.range_end, body.range_start)
        else:
            # Unscoped: full account reset, unchanged from before this change.
            broker.reset(cfg["risk"]["starting_bankroll"])
            risk.reset_day(cfg["risk"]["starting_bankroll"])
            state["signal_feed"] = []
            state["decision_feed"] = []
            state["stats"] = {"signals_seen": 0, "trades_placed": 0, "skipped": 0}
            state["equity_history"] = []
            deleted = counts_before.get("paper")
        _log("paper", deleted)
        cleared.append("paper")
    if body.shadow:
        shadow.clear(cfg["risk"]["starting_bankroll"])
        _log("shadow", None)
        cleared.append("shadow")
    if body.signal_log:
        deleted = signal_log.clear_range(body.range_end, body.range_start)
        _log("signal_log", deleted)
        cleared.append("signal_log")
    if body.market_analyst:
        market_analyst_agent.clear_all()
        _log("market_analyst", counts_before.get("market_analyst"))
        cleared.append("market_analyst")
    if body.market_catalog:
        market_catalog.clear_all()
        _log("market_catalog", None)
        cleared.append("market_catalog")
    if body.market_history:
        market_history.clear_all()
        _log("market_history", None)
        cleared.append("market_history")
    if body.series_evaluator:
        series_evaluator.clear_all()
        _log("series_evaluator", None)
        cleared.append("series_evaluator")
    if body.candidate_log:
        deleted = candidate_log.clear_range(body.range_end, body.range_start)
        _log("candidate_log", deleted)
        cleared.append("candidate_log")
    if body.calibration_history:
        calibration_history.clear_all()
        _log("calibration_history", None)
        cleared.append("calibration_history")
    if body.market_native:
        if ranged:
            deleted = market_broker.clear_trade_range(body.range_end, body.range_start)
        else:
            market_broker.reset(cfg["market_strategy"]["starting_bankroll"])
            market_risk.reset_day(cfg["market_strategy"]["starting_bankroll"])
            state["market_decision_feed"] = []
            deleted = counts_before.get("market_native")
        _log("market_native", deleted)
        cleared.append("market_native")
    if body.trade_category:
        deleted = trade_category.clear_range(body.range_end, body.range_start)
        _log("trade_category", deleted)
        cleared.append("trade_category")
    _bump_generation()
    return {"ok": True, "cleared": cleared, "scope": scope}


# ---- connected accounts -----------------------------------------------
# Kalshi's own account (services/kalshi_account_client.py) stays .env/file-path
# based on purpose — an RSA private key shouldn't ever pass through a browser
# form. This is for the whale-watcher provider library instead: connecting a
# provider here beats hand-editing .env, and it's encrypted at rest.

class ConnectAccountBody(BaseModel):
    provider: str
    credentials: dict


@app.get("/api/accounts")
async def list_accounts():
    return {
        "storage_enabled": accounts_store.enabled(),
        "connected": accounts_store.status(),
        "available_providers": list(PROVIDERS.keys()),
        "active_provider": whale_provider.name,
    }


@app.post("/api/accounts/connect")
async def connect_account(body: ConnectAccountBody):
    global whale_provider
    try:
        accounts_store.save(body.provider, body.credentials)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if body.provider == whale_provider.name:
        whale_provider = get_active_provider()  # re-instantiate so it picks up the new creds now
    return {"ok": True}


@app.post("/api/accounts/{provider}/disconnect")
async def disconnect_account(provider: str):
    global whale_provider
    accounts_store.delete(provider)
    if provider == whale_provider.name:
        whale_provider = get_active_provider()
    return {"ok": True}


# Dashboard/status/login/accounts pages used to be served here via
# FileResponse/StaticFiles. Moved to ddev's "web" (nginx) container serving
# static/ directly, with /api/ and /auth/ reverse-proxied back to this
# service (see .ddev/nginx/kalshi-proxy.conf) — main.py is API-only now, so
# a separate frontend can be built against it without this process also
# owning page-serving. nginx replicates the same no-cache intent that used
# to live in NO_CACHE_HEADERS here (see that config's comment for why).


