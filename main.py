import asyncio
import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path as _Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()  # reads .env if present; every var is optional, see .env.example

from services import logging_config
logging_config.configure()

from services import accounts_store
from services import execution
from services.advisory import advisory_engine
from services import auth as auth_service
from services.whale_calibration import calibration_history
from services import candidate_log
from services import candidate_retry
from services import settlement_resolver
from services import capture_writer
from services.diagnostics import diagnostics
from services.history import regime_analytics
from services.history import suggestion_decisions
from services.whale_calibration import confidence_calibration
from services.market_events import event_lifecycle
from services.market_events import event_schedule
from services.config import config_performance
from services import kalshi_fees
from services import market_analyst_agent
from services.market_catalog import market_catalog
from services import market_history
from services import mutual_exclusivity
from services.exits import position_netting
from services.reset import reset_log
from services import series_cache
from services import series_evaluator
from services import series_watcher
from services import fault_log
from services import loop_watchdog
from services import tick_executor
from services import task_supervisor
from services import game_state
from services import index_feed
from services.index_feed import backfill as index_feed_backfill
from services import settlement_edge
from services.reset import trade_archive
from services import signal_log
from services import title_cache
from services.history import trade_analytics
from services import trade_category
from services.config.config_store import config_store
from services.http_client import classify, close_client, get_and_reset_rate_limit_hits, http_metrics_snapshot
from services.kalshi.public import KalshiPublicGateway
from services.kalshi.account_client import KalshiAccountClient
from services.kalshi.websocket import KalshiStreamGateway
from services.kalshi.contracts.trade import parse_fixed_point_dollars
from services.confidence_scoring import WhaleSignal
from services.whale_simulator import WhaleSimulator
from services.whalewatchers import PROVIDERS  # re-instantiation goes through app_state.reload_whale_provider (#565)
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
from services.diagnostics import routes as diagnostics_routes  # noqa: E402
from services.config import routes as config_routes  # noqa: E402
from services.position import routes as position_routes  # noqa: E402
from services.exits import routes as exits_routes  # noqa: E402
from services.whale_calibration import routes as whale_calibration_routes  # noqa: E402
from services.advisory import routes as advisory_routes  # noqa: E402
from services.backtest import routes as backtest_routes  # noqa: E402
from services.market_catalog import routes as market_catalog_routes  # noqa: E402
from services.history import routes as history_routes  # noqa: E402
from services.analytics import routes as analytics_routes  # noqa: E402
from services.reset import routes as reset_routes  # noqa: E402
from services.analytics.market_analyst_orchestrator import (  # noqa: E402
    _analyzing_series, _analyzing_tickers, _build_full_spectrum_context, _build_series_context,
    _CONFIDENCE_RANK, _full_spectrum_suggestions_from_raw, _run_full_spectrum_analysis,
    _run_market_analyst_for_ticker, _run_series_analysis, _series_evaluator_rows_for_advisory,
    _series_suggestions_from_raw,
)
from services.config.config_paths import _config_value_at_path, _types_compatible  # noqa: E402
from services.whale_stream import decision_bridge, index_stream_handlers, whale_stream_handlers  # noqa: E402
from services.whale_stream.decision_bridge import (  # noqa: E402
    _broadcast_signal_decision, _handle_close_decision, _handle_fill_decision, _handle_signal,
    _shadow_reference_bankroll,
)
from services.whale_stream.whale_stream_handlers import (  # noqa: E402
    _fetch_trade_tape, _fetch_trades_for_ticker, _process_stream_fill, _process_stream_lifecycle,
    _process_stream_position, _process_stream_ticker, _process_stream_trade, _stream_market_client,
    _streaming_trade_tape_enabled, _TRADE_TAPE_UI_CAP, build_fill_validator,
)
from services.whale_stream.index_stream_handlers import (  # noqa: E402
    _noop_stream_trade, _noop_stream_ticker, _process_stream_index, _record_settlement_observations,
    _resolve_settlement_windows, _spec_for,
)
from services.market_watch import (  # noqa: E402
    _EVENT_LIVE_DATA_REPOLL_SEC, _fetch_category_metadata, _fetch_event_live_data,
    _fetch_event_titles, _fetch_exchange_status, _fetch_live_status, _fetch_markets,
    _get_series_cache, _get_top_series, _LIVE_STATUS_LOOKAHEAD_SEC, _LIVE_STATUS_LOOKBACK_SEC,
    _LIVE_STATUS_MAX_POLL_PER_TICK, _LIVE_STATUS_REPOLL_SEC, _maybe_scan_catalog_batch,
    _maybe_scan_milestone_batch, _maybe_scan_mve_batch, _MILESTONE_REPOLL_SEC, propagate_milestone_winners,
    _refresh_discovery_cache, _refresh_discovery_cache_background, _scan_catalog_batch, _slim_market,
)
from services.backup import _maybe_run_backup, _maybe_run_large_backup  # noqa: E402
from services.backup import routes as backup_routes  # noqa: E402
from services.alerting import check_and_alert  # noqa: E402
from services.alerting import routes as alerting_routes  # noqa: E402
from services.observability import maybe_capture as _maybe_capture_observability  # noqa: E402
from services.observability import observability  # noqa: E402
from services.observability import routes as observability_routes  # noqa: E402
from services.quality import evidence_provenance  # noqa: E402
from services.quality import routes as quality_routes  # noqa: E402
from services.research import _maybe_run_research  # noqa: E402
from services.research import routes as research_routes  # noqa: E402
from services.storage_health import storage_health  # noqa: E402
from services.storage_health import routes as storage_health_routes  # noqa: E402
from services.app_state import (  # noqa: E402
    account, account_base_url, broker, bump_generation, cfg, get_whale_provider,
    index_stream, reload_whale_provider, risk, shadow, state, strategy,
    trade_stream, whale_sim,
)
from services.position.account_positions import (  # noqa: E402
    _fetch_account_snapshot, _join_real_position_prices, _real_account_position_tickers,
    _slim_fill, _slim_order, _slim_position,
)
from services.market_lookup import (  # noqa: E402
    _category_by_ticker, _close_time_by_ticker, _sport_for_event, _subcategory_by_ticker,
)
from services.state_view import (  # noqa: E402
    _enrich_recent_trades, _enriched_broker_state, _relevant_tickers, _scoped_event_live_data,
    _scoped_event_titles, _scoped_live_game_state, _scoped_market_titles, _series_meta_map,
)
from services.ws_manager import ws_manager  # noqa: E402
from services import history_push  # noqa: E402


_last_capture_prune_at = 0.0
_last_markout_capture_at = 0.0
_MARKOUT_CAPTURE_INTERVAL_SEC = 300  # matches edge_gate_markout_offsets_sec's
# finest configured offset (300s/5min) - see strategy-edge-gate-
# implementation.md Task 4. Runs unconditionally (design SS5/SS8's stated
# exception to the opt-in pattern - markout data has to exist before
# there's anything to decide whether to turn edge_gate_enabled on with).
_last_edge_gate_delta_recompute_at = 0.0  # Task 7 of docs/superpowers/plans/2026-09-03-strategy-edge-gate-implementation.md

# In-memory cache of series tags: {series_ticker: list[str]} — built from
# state["series_cache"]["series"] each tick, invalidated when series_cache
# refreshes (fetched_at changes). Eliminates DB roundtrips and ensures all
# events get tagged, not just newly-discovered ones (task-4 fix 2026-08-31).
_SERIES_TAGS_CACHE: dict[str, list[str]] = {}
_SERIES_TAGS_CACHE_FETCHED_AT: float = 0.0


def _build_series_tags_cache() -> dict[str, list[str]]:
    """Build in-memory {series_ticker: tags} index from
    state["series_cache"]["series"], invalidating and rebuilding if
    series_cache has been refreshed (fetched_at changed). Zero DB cost;
    all tags already loaded in memory from Kalshi's get-series-list response."""
    global _SERIES_TAGS_CACHE, _SERIES_TAGS_CACHE_FETCHED_AT
    cache = state["series_cache"]
    if cache["fetched_at"] != _SERIES_TAGS_CACHE_FETCHED_AT:
        # Cache is stale; rebuild from the fresh series_cache data
        _SERIES_TAGS_CACHE_FETCHED_AT = cache["fetched_at"]
        _SERIES_TAGS_CACHE.clear()
        for series in cache.get("series") or []:
            ticker = series.get("ticker")
            tags = series.get("tags") or []
            if ticker:
                _SERIES_TAGS_CACHE[ticker] = tags
    return _SERIES_TAGS_CACHE


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
    obs_hours = float((cfg.get("observability") or {}).get("retention_hours", 336))
    observability.prune(retention_hours=obs_hours, now=now)
    mh_hours = float((cfg.get("market_history") or {}).get("retention_hours", 168))
    market_history.prune(retention_hours=mh_hours, now=now)
    fl_hours = float((cfg.get("fault_log") or {}).get("retention_hours", 336))
    fault_log.prune(retention_hours=fl_hours, now=now)


def _maybe_capture_markouts(cfg: dict, now: float) -> None:
    """Markout-capture sweep (Task 4, docs/superpowers/plans/2026-09-03-
    strategy-edge-gate-implementation.md): for every real entry trade,
    records the market price at each configured offset after entry
    (edge_gate_markout_offsets_sec) once that offset comes due, so the
    edge-gate design's SS6 decisive comparison has real markout data to
    train on. Runs on its own _MARKOUT_CAPTURE_INTERVAL_SEC cadence,
    unconditionally - not gated behind edge_gate_enabled, per the design's
    own SS5/SS8 stated exception (markout data has to exist before there's
    anything to decide whether to turn edge_gate_enabled on with).

    Reads only paper_broker.db (trades) and market_catalog.db (close
    times) and writes only the new markouts table in market_history.db -
    never positions, bankroll, or any trading-decision state. fault_log-
    wrapped and non-raising, matching every other _maybe_* sweep here.

    Bracketed directly with time.perf_counter() (not inferred from whole-
    tick before/after noise - Task 4's own runtime-cost-measurement step,
    per the data-plane HARD RULE) since this sweep only fires once per
    _MARKOUT_CAPTURE_INTERVAL_SEC: most individual ticks won't include its
    cost at all, so a whole-tick comparison would be too weak a signal to
    catch a real regression here."""
    global _last_markout_capture_at
    if now - _last_markout_capture_at < _MARKOUT_CAPTURE_INTERVAL_SEC:
        return
    _last_markout_capture_at = now
    _t0 = time.perf_counter()
    try:
        offsets = (cfg.get("strategy") or {}).get(
            "edge_gate_markout_offsets_sec", [300, 3600, None]
        )
        trades = [
            {"id": t.id, "ticker": t.ticker, "side": t.side, "price": t.price, "timestamp": t.timestamp}
            for t in broker.trades_since(after=now - 40 * 86400)  # 40d: covers close_window_sec's 32d default with margin
        ]
        if not trades:
            return
        close_ts_by_ticker = market_catalog.close_ts_for_tickers([t["ticker"] for t in trades])
        targets = market_history.pending_markout_targets(trades, offsets, now, close_ts_by_ticker)
        for target in targets:
            price = market_history.recent_price(
                target["ticker"], max_age_sec=_MARKOUT_CAPTURE_INTERVAL_SEC * 2, as_of=target["target_ts"],
            )
            market_history.record_markout(
                target["trade_id"], target["ticker"], target["entry_side"], target["entry_price"],
                target["entry_ts"], target["offset_label"], target["offset_sec"], target["target_ts"],
                price, now,
            )
    except Exception as exc:
        fault_log.record("market_history", "capture_markouts", exc)
    finally:
        _elapsed_ms = (time.perf_counter() - _t0) * 1000
        if _elapsed_ms > 50:
            # record_fault, not record() - this branch has no exception
            # object to pass. fault_log.record()'s third positional arg is
            # typed `exc: BaseException` and its body dereferences
            # exc.__traceback__; passing a plain string there raises
            # AttributeError inside record()'s own try/except, which
            # swallows it and returns False - the diagnostic would silently
            # never actually log (verified by reading services/fault_log.py
            # directly, not assumed from the plan text: record_fault's own
            # docstring - "Log something worth knowing that isn't an
            # exception" - and every existing non-exception fault_log call
            # site in this codebase, e.g. strategy_engine.py:277 and
            # kalshi/websocket.py:474, already use record_fault for exactly
            # this shape).
            fault_log.record_fault(
                "market_history", "capture_markouts_slow",
                f"{_elapsed_ms:.1f}ms", severity="warn",
            )
def _maybe_recompute_edge_gate_deltas(cfg: dict, now: float) -> None:
    """Hourly Delta_calibrated recompute sweep (Task 7 of docs/superpowers/
    plans/2026-09-03-strategy-edge-gate-implementation.md). Same interval-
    guard idiom as _maybe_prune_capture_stores above; interval from
    strategy.edge_gate_recompute_interval_sec (default 3600s, design §5).
    Deliberately NOT gated behind edge_gate_enabled - recomputing an
    unused cache is cheap and harmless, and this keeps
    confidence_calibration.delta_calibrated_for populated from the moment
    edge_gate_enabled is eventually flipped true, rather than needing a
    cold-start warm-up delay the first time someone turns the gate on."""
    global _last_edge_gate_delta_recompute_at
    interval = (cfg.get("strategy") or {}).get("edge_gate_recompute_interval_sec", 3600)
    if now - _last_edge_gate_delta_recompute_at < interval:
        return
    _last_edge_gate_delta_recompute_at = now
    try:
        confidence_calibration.recompute_deltas(cfg, now)
    except Exception as exc:
        fault_log.record("whale_calibration", "recompute_edge_gate_deltas", exc)


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
        check_state["task"] = task_supervisor.supervise(
            lambda: _check_signal_resolutions_background(cfg),
            component="signal_resolution", operation="background_check",
        )


@classify("background_resolution")
async def _check_signal_resolutions_background(cfg: dict) -> None:
    """Owns its own KalshiPublicGateway - see _refresh_discovery_cache's
    identical reasoning (the calling tick's own client closes at the end
    of that same tick, well before an independent background task would
    finish). Exceptions are caught and recorded by task_supervisor.supervise
    (the caller) - this only needs its own finally to release the
    "checking" flag and close the client regardless of outcome."""
    check_state = state["signal_resolution_check"]
    client = KalshiPublicGateway(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])
    try:
        await _check_signal_resolutions(client)
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
# into ceil(N/50) batched calls (see KalshiPublicGateway.get_markets_by_tickers's
# own _MARKETS_BY_TICKERS_BATCH_SIZE=50, sized to Kalshi's real 600-token
# burst ceiling). 200/check at the existing 30s cadence drains this backlog
# in about an hour instead of a full day, at only 4 local acquire() calls
# per check instead of 200.


async def _check_signal_resolutions(client: KalshiPublicGateway):
    """Pick a batch of old-enough unresolved logged signals and see if their
    markets have settled yet. Interval-gating and background-task
    scheduling both live in _maybe_check_signal_resolutions above now -
    this function just does the work when asked. See
    _SIGNAL_RESOLUTION_BATCH_SIZE above for why the batch is this large and
    why one get_markets_by_tickers call replaces what used to be N
    individual get_market() calls.

    Only trusts a market's `result` once `status` is `finalized` (2026-08-23
    fix, services/whale_calibration/README.md's own audit finding -
    "the single most important thing a future audit of this module's
    numbers should check first"). Same gap, same fix shape as
    catalog_scan.propagate_milestone_winners/whale_stream_handlers'
    _process_stream_lifecycle (phase 134, docs/kalshi/market_lifecycle.md):
    Kalshi sets `result` the instant a market is `determined`, but the
    result "may be disputed" and can flip via determined -> disputed ->
    amended before finally reaching finalized. Before this fix,
    signal_log.mark_resolved was a one-way write straight off `determined`'s
    result - the exact `correct` field confidence_calibration.py's bands and
    every whale-tracking win-rate filter are computed from, with no
    mechanism to notice or repair a later reversal. A ticker whose market
    hasn't reached finalized yet just stays in unresolved_batch's pool and
    gets rechecked on a later pass, same as one Kalshi hasn't returned data
    for at all - this was already the "not yet settled" degrade path, now
    it also covers "settled but not yet final."""
    items = signal_log.unresolved_batch(limit=_SIGNAL_RESOLUTION_BATCH_SIZE, older_than_sec=600)
    if not items:
        return
    markets = await client.get_markets_by_tickers([item["ticker"] for item in items])
    for item in items:
        market = markets.get(item["ticker"])
        if not market or market.get("status") != "finalized":
            continue  # not settled yet, or settled but still inside the dispute window — retry next time
        result = (market.get("result") or "").strip().lower()
        if result in ("yes", "no"):
            signal_log.mark_resolved(item["id"], correct=(result == item["side"]))


def _flush_trade_capture(trade_tape: list, cfg: dict) -> dict:
    """Records this tick's trade tape, then flushes series_watcher's
    book_snapshots buffer. Trades no longer flush here (P3 Task 15 -
    record_trade submits each row to capture_writer's own independently-
    scheduled daemon thread instead), which is what root-cause report C1's
    originally-measured 0.65-1.6s executemany on essentially every tick was
    - the book-side flush this function still does synchronously is a much
    smaller, unmeasured-as-a-problem cost, kept here because P1 Task 7
    already offloaded it via tick_executor regardless. A plain sync
    function so it is directly unit-testable and directly callable from
    tick_executor's worker thread."""
    for tape_trade in trade_tape:
        series_watcher.record_trade(tape_trade, cfg)
    return series_watcher.flush()


async def _flush_trade_capture_async(trade_tape: list, cfg: dict) -> dict:
    """Awaitable wrapper: runs _flush_trade_capture via tick_executor
    instead of the calling event loop (P1 Task 7). Still uses series_
    watcher's own _connect(), not tick_executor.connection_for() - see
    tick_executor.py's own docstring ("connection_for() status") for why
    that swap was investigated and deliberately not made (code-review
    finding #3)."""
    return await tick_executor.run(lambda: _flush_trade_capture(trade_tape, cfg))


def _flush_secondary_capture_stores(cfg: dict, now: float) -> dict:
    """index_feed/settlement_edge/game_state's own flush() plus the hourly
    _maybe_prune_capture_stores sweep - the remaining synchronous capture-
    store I/O that P1 Task 7/8 (see _flush_trade_capture_async/
    _resolve_and_record_settlements_async above) never moved off the event
    loop. capture_writer.py's own module docstring already names the
    mechanism (issue #211): series_watcher.db has more than one writer, and
    SQLite's write lock is per-file, not per-table - series_watcher.prune()
    (a full-scan DELETE, called from _maybe_prune_capture_stores below) is
    one of the writers that contends for that lock. capture_writer's daemon
    thread only pays for a collision with a bounded retry; running these
    calls directly on the event loop meant a collision froze the WHOLE
    event loop instead - every WS ticker/trade message and every HTTP
    request, not just this one tick's own progress. Confirmed live
    (2026-09-01): tick.phase.capture_flush_and_titles_sec measured a 882.6s
    max against a 7.3s average, directly correlated with open-position
    ticker staleness (oldest observed 3.8h) and exit_engine's
    stale_price_uncorroborated fault. Moved onto tick_executor the same way
    P1 Task 7/8 already did, not given its own new thread pool - see
    tick_executor.py's own docstring for why its 2-worker pool wasn't
    blindly widened without measuring that specific bottleneck first."""
    index_result = index_feed.flush()
    settlement_result = settlement_edge.flush()
    game_state_result = game_state.flush()
    _maybe_prune_capture_stores(cfg, now)
    _maybe_capture_markouts(cfg, now)
    _maybe_recompute_edge_gate_deltas(cfg, now)
    return {"index_feed": index_result, "settlement_edge": settlement_result, "game_state": game_state_result}


async def _flush_secondary_capture_stores_async(cfg: dict, now: float) -> dict:
    """Awaitable wrapper: runs _flush_secondary_capture_stores via
    tick_executor instead of the calling event loop."""
    return await tick_executor.run(lambda: _flush_secondary_capture_stores(cfg, now))


def _resolve_and_record_settlements(markets: list, market_results: dict, tick_now: float) -> list:
    """The tick's synchronous market-result resolution + market-history
    recording - root-cause report C1's ~1-5s 'resolve_and_record' phase
    (realtime data-plane remediation plan, P1 Task 8). Single-connect
    calls (market_analyst_agent/candidate_log resolution, the batched
    market_history.record_snapshots executemany) plus a per-finalized-
    market outcome/settlement-window write - a smaller N+1 shape than
    series_stats' (Task 8's other target) since it only touches markets
    that actually finalized this tick, not every watched market, but the
    same _connect()-per-call cost either way. A plain sync function,
    directly unit-testable and directly callable from tick_executor's
    worker thread. Returns the slimmed markets list state["markets"]
    should be set to (unchanged from what the inline block used to
    compute)."""
    slimmed = [_slim_market(m) for m in markets]
    # Cheap, pure-DB checks (no new API calls) - run every tick regardless
    # of market_analyst.enabled/whether a market ever traded, so analyses
    # made while a feature was on still get graded after it's turned off.
    market_analyst_agent.resolve_from_market_results(market_results)
    candidate_log.resolve_from_market_results(market_results)
    # Real market data logging (docs/advisory-engine-plan.md §9) -
    # independent of whale signals, independent of whether either strategy
    # ever trades a given market. Same real fields already fetched by the
    # caller, zero extra API cost.
    # yes_price: None (no real bid this tick) is filtered out by
    # record_snapshots itself, never written as a fabricated 0.5 - issue
    # #577's root fix (2026-09-05). Was `float(m.get("yes_bid_dollars") or
    # 0.5)`, the same falsy-not-missing bug as trading_loop's latest_prices
    # (Task 2): fabricated on a genuinely absent bid AND on a real 0.0 one.
    market_history.record_snapshots(
        [
            {
                "ticker": m["ticker"],
                "yes_price": parse_fixed_point_dollars(m.get("yes_bid_dollars")),
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
        # status=="finalized" gate: 2026-08-23 fix, same reasoning as
        # propagate_milestone_winners' own docstring - result is set at
        # "determined" but can still flip (disputed -> amended) before
        # "finalized" is truly terminal.
        if result in ("yes", "no") and m.get("ticker") and m.get("status") == "finalized":
            market_history.record_outcome(m["ticker"], result, resolved_at=tick_now)
            # Close the loop on any settlement-window observations taken
            # for this market (services/settlement_edge.py) - the realised
            # outcome is written onto the rows that forecast it, so scoring
            # can never pair an observation with a different window's
            # result. No-op (0 rows) for the overwhelming majority of
            # markets, which are not index-settled and were never observed.
            settlement_edge.resolve_window(m["ticker"], result == "yes")
    return slimmed


async def _resolve_and_record_settlements_async(markets: list, market_results: dict, tick_now: float) -> list:
    """Awaitable wrapper: runs _resolve_and_record_settlements via
    tick_executor instead of the calling event loop (P1 Task 8). The
    market_analyst_agent/candidate_log/market_history/settlement_edge
    calls inside still use their own modules' _connect(), not
    tick_executor.connection_for() - see tick_executor.py's own docstring
    ("connection_for() status") for why (code-review finding #3)."""
    return await tick_executor.run(
        lambda: _resolve_and_record_settlements(markets, market_results, tick_now)
    )


async def _build_series_track_record_async(tickers: list, days: int = 30) -> dict:
    """Awaitable wrapper: runs signal_log.series_stats_bulk via
    tick_executor instead of the calling event loop - root-cause report
    C1's specifically named series_stats N+1 at main.py:736, one
    _connect() per watched market before this (P1 Task 8). Still uses
    signal_log's own _connect(), not tick_executor.connection_for() - see
    tick_executor.py's own docstring ("connection_for() status") for why
    (code-review finding #3)."""
    return await tick_executor.run(lambda: signal_log.series_stats_bulk(tickers, days=days))


_SCHEDULER_TRIGGER_INTERVAL_SEC = 5.0


async def _scheduler_loop(trigger, name: str) -> None:
    """One supervised loop per background trigger check (P8 Task 36). The
    five _maybe_* functions and _maybe_run_auto_apply used to be invoked from
    inside trading_loop's body, which made every one of them tick-cadenced by
    accident of where the call lived, not by design - each already carries
    its own due()/overlap guard and spawns its real work as an independent
    task. Only the caller moved. Gated on state["running"] so nothing fires
    while the app is paused, exactly as trading_loop's own gate behaved. The
    interval sits well under every trigger's own due() interval (the tightest
    is catalog_scan's 15s), so due()-precision is preserved; a not-due call
    is one dict comparison.

    Issue #585: `trigger` is called generically for all nine registered
    triggers, eight of which are plain `def ... -> None` (they stay fast by
    spawning their own real work as an independent task_supervisor.supervise
    background task and returning immediately - unaffected by this check,
    since calling them still returns None). `_maybe_run_auto_apply` is the
    one exception: it does its (rare, hours-scale) calibration-snapshot work
    inline rather than backgrounding it, and that work includes a call this
    loop must be able to await (services.signal_log.
    resolved_signals_with_factors_async(), routed off the event loop per
    services.signal_log's own async/sync split for issue #410/#581) - so it
    is now `async def` and calling it returns a coroutine instead of running
    synchronously. iscoroutine() tells the two shapes apart: a plain sync
    trigger's `None` return is left alone (identical to before), an async
    trigger's coroutine is awaited right here - serialized with this loop's
    own sleep cycle exactly as the old fully-synchronous call was, so no new
    overlap-guard is needed for auto_apply's due()/cooldown checks."""
    while True:
        await asyncio.sleep(_SCHEDULER_TRIGGER_INTERVAL_SEC)
        if not state["running"]:
            continue
        result = trigger(config_store.get())
        if asyncio.iscoroutine(result):
            await result


async def _maybe_run_auto_apply(cfg: dict) -> None:
    """Calibration-history snapshot + calibration auto-apply, and unified
    advisory auto-apply - moved verbatim out of trading_loop (P8 Task 36).
    Both are hours-scale (snapshot_interval_sec 21600, auto_apply_cooldown_sec
    86400) and were the one place the tick still did real inline work when
    due instead of the _maybe_* trigger shape everything else uses. Still
    inline-when-due here (same blocking profile as before, once every several
    hours) EXCEPT for the one line issue #585 measured at ~13.8s of
    GIL-holding event-loop time: cc_rows below now awaits
    signal_log.resolved_signals_with_factors_async() (built for #410/#581,
    "same query, same output, same contract" per its own docstring) instead
    of calling the sync resolved_signals_with_factors() inline. That forced
    this function itself to become `async def` - its only caller,
    _scheduler_loop, was verified (not assumed) to already support awaiting
    a trigger that returns a coroutine, see that function's own docstring.
    Offloading the REST of this due-time work (the report computation, the
    advisory block below) via tick_executor is still a separate follow-up,
    not part of this fix."""
    tick_now = time.time()

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
        cc_rows = await signal_log.resolved_signals_with_factors_async()
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
                    (last_auto is None or (tick_now - last_auto) >= cooldown)
                    and cc_result["report"]["resolved_count"] >= auto_apply_floor
                    and not evidence_provenance.current_completeness_state()["degraded"]
                ):
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
                        # address (services/whale_calibration/confidence_calibration.py's
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
                        bump_generation()

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
            adv_variants = {v["fingerprint"]: v for v in config_performance.all_variants()}
            adv_result = advisory_engine.generate_recommendations(
                adv_all_rows, cfg, adv_current_fp, adv_variants,
                adv_cfg["min_resolved_trades_per_variant"],
                gate_summaries=candidate_log.gate_summary(),
                # Staleness filter (2026-08-11, direct bug report) matters most
                # right here - unlike a manual click, auto-apply has no human
                # to notice it's repeatedly nudging the same field off the
                # exact same stale evidence every cooldown window.
                last_applied_by_path=config_performance.all_last_applied_by_path(),
                series_evaluator_rows=_series_evaluator_rows_for_advisory(cfg),
                category_rows=regime_analytics.by_category(adv_all_rows),
                # 2026-09-03, Task 3a of docs/superpowers/plans/2026-09-03-
                # tier1-backend-hygiene.md: this is the one UNSUPERVISED
                # call site (no human between a suggestion and applying it)
                # - the one that most needs to honor a decline, and
                # previously didn't (advisory_engine.py's declined_ids
                # docstring, :926-929).
                declined_ids=suggestion_decisions.declined_ids(),
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
            if qualifying and not evidence_provenance.current_completeness_state()["degraded"]:
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
                bump_generation()


_SCHEDULER_TRIGGERS = (
    ("signal_resolution", _maybe_check_signal_resolutions),
    ("backup", _maybe_run_backup),
    ("backup_large", _maybe_run_large_backup),
    ("research", _maybe_run_research),
    ("event_schedule", event_schedule._maybe_resolve_event_schedules),
    ("catalog_scan", _maybe_scan_catalog_batch),
    # Multivariate (combo) event discovery (issue #268) - deliberately its
    # own trigger, not folded into catalog_scan above: MVE discovery is
    # independent of kalshi.categories scope (see
    # services/market_watch/mve_scan.py's own module docstring for why a
    # regular per-series scan structurally can't reach these markets at
    # all - every MVE series reports volume_fp=0.00 on its own /series
    # entry, which catalog_scan._get_series_cache already filters out
    # before any category logic even runs).
    ("mve_scan", _maybe_scan_mve_batch),
    # Broad milestone discovery (entry-gate-me-pairing-and-netting-
    # remediation Part 3) - independent of `markets`/watchlist scope, see
    # services/market_watch/milestone_scan.py's own module docstring.
    ("milestone_scan", _maybe_scan_milestone_batch),
    ("auto_apply", _maybe_run_auto_apply),
)


def _sparse_price_dict(markets: list[dict], field: str) -> dict[str, float]:
    """ticker -> price for every market with a real, parseable value at
    `field`; a market with no real value there is simply absent from the
    result, never defaulted. Shared by trading_loop's latest_prices
    (yes_bid_dollars) and latest_asks (yes_ask_dollars) rebuild so the two
    can never drift on how "no real price" gets represented - before issue
    #577's fix, latest_prices alone defaulted a missing/falsy bid to a
    fabricated 0.5 (indistinguishable from a real 0.5, and wrongly firing
    on a real 0.0 bid too, since `or` is a falsy check not a missing
    check) while latest_asks next to it already did this correctly.
    parse_fixed_point_dollars preserves a genuine 0.0 and returns None for
    absent/malformed - this function's whole job is "keep only the real
    ones," pulled out once so both dicts are built by the same one
    tested rule instead of two independently-maintained comprehensions."""
    result: dict[str, float] = {}
    for m in markets:
        ticker = m.get("ticker")
        if not ticker:
            continue
        price = parse_fixed_point_dollars(m.get(field))
        if price is not None:
            result[ticker] = price
    return result


def _tick_interval_sec(cfg: dict) -> float:
    """P8 Task 39: trading_loop's own REST tick is a safety net in streaming
    mode, not the primary data path, once check_exits/check_pending_fills/
    position_netting.review all also run from the WS ticker path (Task 38)
    and the five _maybe_* schedulers + candidate_retry run from their own
    independent loops (Tasks 36-37). The tick's remaining jobs (market/
    account/exchange-status fetch, settlement resolution, event-lifecycle
    classification, capture flush, retention) are either genuinely REST-only
    or already covered faster by the WS path - see the config field's own
    comment in config/settings.yaml.

    Non-streaming mode is unaffected: REST trade-tape polling is still the
    primary path there (_streaming_trade_tape_enabled() false), so the tick
    keeps its original poll_interval_sec cadence exactly as before this
    task."""
    kalshi_cfg = cfg["kalshi"]
    if _streaming_trade_tape_enabled():
        return kalshi_cfg["safety_net_interval_sec"]
    return kalshi_cfg["poll_interval_sec"]


async def _candidate_retry_loop() -> None:
    """candidate_retry.run_pending's one and only caller (P8 Task 37) - it
    used to be invoked once per tick from trading_loop's body (P2 Task 13).
    Its documented single-mutator contract (services/candidate_retry.py:
    "call from exactly one place") is preserved by relocation, not
    duplication: trading_loop's call is gone, this loop is the single
    caller. Owns its own KalshiPublicGateway per run - the tick's own client
    closes at the end of each tick, the same reason every other background
    task here owns one (see _check_signal_resolutions_background) - and
    constructs it only when something is actually pending, so the idle path
    is one snapshot() read, no client churn. Same stream-mode gate the tick
    applied: a retry's own market lookup is only meaningful when the
    exchange-wide stream is what feeds whale candidates in the first place.
    whale_provider + _handle_signal are still threaded through so a
    recovered candidate is scored and evaluated through the same pipeline a
    first-try trade uses, not just claimed and dropped."""
    loop_state = state["candidate_retry_loop"]
    while True:
        await asyncio.sleep(_SCHEDULER_TRIGGER_INTERVAL_SEC)
        if not state["running"] or not _streaming_trade_tape_enabled():
            continue
        if candidate_retry.snapshot().get("pending", 0) <= 0:
            continue
        cfg = config_store.get()
        loop_state["running"] = True
        loop_state["last_started_at"] = time.time()
        client = KalshiPublicGateway(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])
        try:
            await candidate_retry.run_pending(
                client, get_whale_provider(), _handle_signal, cfg, state.get("market_results") or {},
                config_performance.fingerprint(cfg), time.time(),
            )
        finally:
            loop_state["running"] = False
            await client.close()


async def _settlement_resolver_loop() -> None:
    """settlement_resolver.run_pending's one and only caller (P4 Tasks
    19+24) - the settled lifecycle handler enqueues, this loop drains in
    one GET /markets?tickers= batch per run instead of the former one
    get_market() per settlement inline on the WS consumer. Same shape as
    _candidate_retry_loop: own client per run, idle path is one snapshot()
    read. Deliberately NO streaming-mode gate: pending only fills from the
    WS settled handler, but tickers already enqueued must still drain if
    streaming is toggled off before they resolve - the REST read is the
    resolution, not the stream. resolved_rows feeds the same
    outcomes_resolved_via_lifecycle stat the inline branch incremented, so
    that counter keeps its meaning (store rows) across the relocation."""
    loop_state = state["settlement_resolver_loop"]
    while True:
        await asyncio.sleep(_SCHEDULER_TRIGGER_INTERVAL_SEC)
        if not state["running"]:
            continue
        if settlement_resolver.snapshot().get("pending", 0) <= 0:
            continue
        cfg = config_store.get()
        loop_state["running"] = True
        loop_state["last_started_at"] = time.time()
        client = KalshiPublicGateway(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])
        try:
            result = await settlement_resolver.run_pending(client)
            state["lifecycle_stream_stats"]["outcomes_resolved_via_lifecycle"] += result.get("resolved_rows", 0)
        finally:
            loop_state["running"] = False
            await client.close()


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
        # Per-phase breakdown of the same tick (2026-08-17, docs/next-session-
        # pickup-2026-08-17.md "URGENT, not yet actioned" item) -
        # last_tick_duration_sec alone couldn't say *which* phase of a 19.63s
        # tick against a 6s poll_interval_sec was slow; guessing which
        # asyncio.gather() block dominated would have been exactly the kind
        # of unverified claim this project has been burned by before.
        # Declared outside try/except so a mid-tick exception still leaves
        # whatever phases completed before the failure visible, rather than
        # losing the whole breakdown.
        phase_timings: dict[str, float] = {}
        _phase_t = tick_start_wall
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

            client = KalshiPublicGateway(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])

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
            open_position_tickers = list(set(broker.positions.keys()) | real_position_tickers)
            # P8 Task 34 - the one set the WS ticker handler and the
            # observability sampler both key per-position cadence off, so
            # "which tickers count as open" has exactly one definition.
            state["open_position_tickers"] = set(open_position_tickers)
            # signal-resolution / backup / research / event-schedule trigger checks
            # run from their own supervised loops now (P8 Task 36, _scheduler_loop).
            await check_and_alert(cfg)
            markets, account_snapshot, exchange_status = await asyncio.gather(
                _fetch_markets(client, cfg, extra_tickers=open_position_tickers), _fetch_account_snapshot(cfg),
                _fetch_exchange_status(client),
            )
            # Root-cause report C3/R1 (realtime data-plane remediation plan,
            # P1 Task 9): this used to fire BEFORE the critical gather above,
            # so the background catalog-scan task it spawns (up to
            # PACE_LIMIT concurrent get_markets calls even after Task 9's
            # own pacing fix) could start competing for the same REST
            # token bucket the position/account/exchange-status fetch was
            # about to need. Triggering it only after that gather returns
            # means the critical fetch's own calls are already dispatched
            # first.
            # _maybe_scan_catalog_batch runs from its own supervised loop now (P8
            # Task 36). The launch-order concern in the comment above is moot:
            # it no longer shares this coroutine at all.
            await _fetch_category_metadata(client)
            phase_timings["market_fetch"] = round(time.time() - _phase_t, 3)
            _phase_t = time.time()
            state["account"] = account_snapshot
            if exchange_status is not None:
                state["exchange_status"] = exchange_status

            # Straight from Kalshi's market.result field ("yes"/"no"/"" -
            # empty until the market settles), gated to status=="finalized"
            # (2026-08-23 fix - see propagate_milestone_winners' own
            # docstring) - used unconditionally by check_exits to close out
            # any open position on a market that's actually resolved,
            # regardless of exit config. Built from the full
            # (pre-_slim_market) markets list since result isn't one of
            # _MARKET_FIELDS (that trimming is only for the /api/state
            # payload, not internal use).
            market_results = await propagate_milestone_winners(client, markets)
            # _resolve_and_record_settlements bundles everything that used
            # to run inline here (root-cause report C1's ~1-5s
            # "resolve_and_record" phase) - state["markets"] slimming, the
            # market_analyst_agent/candidate_log resolution, the batched
            # market_history.record_snapshots write, and the per-finalized-
            # market outcome/settlement-window write - onto one
            # tick_executor worker thread instead of this loop (P1 Task 8).
            # propagate_milestone_winners above stays on the loop: it's an
            # awaited network call, not sync work tick_executor can run.
            tick_now = time.time()
            state["markets"] = await _resolve_and_record_settlements_async(markets, market_results, tick_now)
            phase_timings["resolve_and_record"] = round(time.time() - _phase_t, 3)
            _phase_t = time.time()

            # Calibration-history snapshot / calibration auto-apply / unified
            # advisory auto-apply moved to their own supervised loop (P8 Task 36,
            # _maybe_run_auto_apply) - the tick no longer does hours-scale work
            # inline when it happens to be due.

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
            # candidate_retry.run_pending (P2 Task 13) runs from its own
            # supervised loop now - _candidate_retry_loop, P8 Task 37 - which
            # keeps its single-mutator contract (still exactly one caller).
            phase_timings["event_and_tradetape_fetch"] = round(time.time() - _phase_t, 3)
            _phase_t = time.time()
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
            # already uses). See services/market_events/event_lifecycle.py's own module
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
            # Stamp real per-series tags (from Task 1's series_metadata/series_tags)
            # onto EVERY event in the cache each tick, not just this tick's fetch
            # delta (2026-08-31 Task 4 fix: ensures backlog doesn't lose tags after
            # restart, and eliminates DB roundtrips by using in-memory index).
            # Build in-memory cache from state["series_cache"]["series"] if
            # series_cache refreshed (fetched_at changed); otherwise reuse.
            tags_by_series_ticker = _build_series_tags_cache()
            for et, event_meta in state["event_titles"].items():
                series_ticker = event_meta.get("series_ticker")
                event_meta["category_tags"] = tags_by_series_ticker.get(series_ticker, []) if series_ticker else []
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
            # The record loop below (record_trade, now submitting to
            # capture_writer's own daemon thread - P3 Task 15) is what
            # root-cause report C1 originally measured as a 0.65-1.6s
            # synchronous executemany into the 16.9M-row raw_trades table on
            # essentially every tick; that cost is now off this loop
            # entirely, on capture_writer's own schedule. This call still
            # runs via tick_executor (P1 Task 7) for the book_snapshots
            # flush series_watcher.flush() still does synchronously here.
            await _flush_trade_capture_async(trade_tape, cfg)
            # Same per-tick batched write for index ticks (index_feed), plus
            # settlement_edge/game_state's own flush and the hourly
            # retention sweep (_maybe_prune_capture_stores - series_watcher/
            # index_feed/game_state/observability/market_history/fault_log
            # prune(), 2026-08-17: every capture store above is unbounded by
            # construction, data/ was already 841MB with series_watcher at
            # 130MB after a few hours and game_state at 32MB within minutes
            # of first writing, because a crypto payload carries a whole
            # candlestick array per row - runs at most hourly, never touches
            # raw_trades or settlement-window rows since CLAUDE.md treats
            # accumulated history as a first-class asset). Routed through
            # tick_executor (see _flush_secondary_capture_stores' own
            # docstring) rather than run directly here - a lock collision on
            # any of these, series_watcher.prune()'s full-scan DELETE
            # (issue #211) included, used to freeze the whole event loop,
            # not just this tick.
            await _flush_secondary_capture_stores_async(cfg, tick_now)
            await _resolve_settlement_windows(client)
            state["live_status"] = live_status  # replaced wholesale, not accumulated - a stale "live" would be wrong, not just incomplete
            # yes_bid_dollars is Kalshi's real field (already a 0-1
            # probability) — "yes_bid" (cents) doesn't exist on the live
            # API. Issue #577 (2026-09-05): this used to default every
            # missing bid to a fabricated 0.5, indistinguishable from a
            # real 0.5 bid and, worse, ALSO fabricated on a real 0.0 bid
            # (`or` is a falsy check, not a missing check). Sparse now via
            # _sparse_price_dict, exactly like latest_asks: a ticker with
            # no real bid is simply absent from the dict, never invented.
            state["latest_prices"] = _sparse_price_dict(markets, "yes_bid_dollars")
            # Maker/limit-order path (2026-08-15) - genuinely missing, not
            # defaulted like latest_prices above: check_pending_fills needs
            # to tell "no fresh ask this tick" apart from "a real 0.5 ask,"
            # since guessing an ask would mean guessing whether a resting
            # order should fill - the one thing this mechanism must never do.
            state["latest_asks"] = _sparse_price_dict(markets, "yes_ask_dollars")
            # P7 Task 29 (redesigned): the rebuilds above bound both dicts to
            # this tick's fetched markets (open positions always included via
            # extra_tickers); keep their per-ticker write stamps bounded the
            # same way so neither grows with every ticker ever seen.
            for key in ("latest_prices", "latest_asks"):
                stamps = state[f"{key}_updated_at"]
                for stale_ticker in [t for t in stamps if t not in state[key]]:
                    del stamps[stale_ticker]
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
            # series_stats_bulk (P1 Task 8) replaces what used to be one
            # signal_log.series_stats() call - and one _connect() - per
            # market: root-cause report C1's specifically named series_stats
            # N+1. One connection, one query pair per unique series, run
            # off the loop via tick_executor.
            _tracked_tickers = [m["ticker"] for m in markets if m.get("ticker")]
            state["series_track_record"] = await _build_series_track_record_async(_tracked_tickers, days=30)
            phase_timings["capture_flush_and_titles"] = round(time.time() - _phase_t, 3)
            _phase_t = time.time()
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
            # Resolved once per tick so this whole block reads one instance
            # even if a reconnect swaps the provider mid-tick (#565).
            provider = get_whale_provider()
            if _streaming_trade_tape_enabled():
                state["whale_source"] = f"{provider.name} (websocket)"
            elif provider.enabled:
                try:
                    new_signals = await provider.fetch_signals(
                        market_context={
                            "markets": markets, "trade_tape": trade_tape, "cfg": cfg,
                            "client": client,
                        },
                    )
                    state["whale_source"] = provider.name
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
                    state["whale_source"] = f"simulated ({provider.name} fallback)"
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
            phase_timings["signal_and_entry"] = round(time.time() - _phase_t, 3)
            _phase_t = time.time()

            # Maker/limit-order path (2026-08-15) - resolves resting limit
            # orders strategy.evaluate() may have placed above (opt-in,
            # strategy.use_limit_orders) against this tick's real bid/ask.
            # Runs before check_exits below (same reasoning as the
            # opened_since guard immediately below) so a just-filled
            # position isn't exit-checked this same tick against
            # state["latest_prices"], snapshotted before this fill happened.
            #
            # validate_fn re-checks the fill-time price/confidence against
            # the same gates evaluate() applied at placement time - see
            # build_fill_validator's own docstring (services/whale_stream/
            # whale_stream_handlers.py; extracted from this exact closure,
            # P8 Task 38, so _process_stream_ticker's own check_pending_
            # fills call shares one implementation instead of a second,
            # drifting copy).
            for fill_decision in broker.check_pending_fills(
                state["latest_prices"], state["latest_asks"], validate_fn=build_fill_validator(cfg, tick_now),
            ):
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
            #
            # tick_cache (I13 P4 Task 20, 2026-08-27): shared across every
            # position this call processes, so positions on the same ticker
            # share one recent_price/volatility/analyst_lean/series_stats
            # read instead of one each - see exit_engine.check_exits's own
            # tick_cache docstring for the full mechanism and its known
            # scope gap (distinct-ticker positions still cost one read
            # each; this only dedupes same-ticker repeats).
            tick_cache: dict = {}
            for close_decision in strategy.check_exits(
                state["latest_prices"], state["signal_feed"], cfg, market_results, opened_since=tick_now,
                category_by_ticker=_category_by_ticker(), close_times=_close_time_by_ticker(),
                tick_cache=tick_cache, latest_prices_updated_at=state["latest_prices_updated_at"],
                latest_asks=state["latest_asks"],
            ):
                await _handle_close_decision(close_decision)

            # Position netting (2026-08-15 direct correction: the ME-gate
            # above only blocks a NEW entry into a confirmed complement -
            # it does nothing for positions already open, partial hedges,
            # or N-way concentration). Runs after check_exits, on whatever
            # survived per-position rules - see services/exits/position_netting.py
            # for the payout-profile math. Entirely opt-in
            # (position_netting.enabled, default False) and a no-op until
            # deliberately turned on - which it HAS been: config/settings.yaml
            # carries enabled: true, so this path is live, not dormant.
            for close_decision in position_netting.review(
                broker, state["market_titles"], state["event_titles"], state["latest_prices"], cfg,
                latest_asks=state["latest_asks"],
            ):
                await _handle_close_decision(close_decision)
            phase_timings["exit_management"] = round(time.time() - _phase_t, 3)

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
        # Per-tick since-last-tick snapshot (QCP Task 15), same reset-and-
        # stash pattern as get_and_reset_rate_limit_hits() right above - the
        # reset happens exactly once per tick, here, so
        # observability.capture_from_runtime can stay a pure read of an
        # already-computed state value rather than ever calling
        # http_metrics_snapshot(reset=True) itself (which would make an
        # incidental GET /api/observability/current request silently zero
        # out the counters this tick's own sample was about to read).
        state["last_tick_http_metrics"] = http_metrics_snapshot(reset=True)
        state["tick_phase_timings"] = phase_timings
        _maybe_capture_observability(cfg, state, trade_stream, index_stream)
        storage_health.maybe_capture_sizes(state, storage_health.DATA_DIR)
        bump_generation()
        await asyncio.sleep(_tick_interval_sec(cfg))


async def _capture_writer_liveness_loop() -> None:
    """capture_writer's daemon thread isn't itself an asyncio task
    task_supervisor can restart directly - this coroutine is what's
    actually supervised (restart=True), and it just polls
    capture_writer.ensure_alive() every 5s, matching P3 Task 14's own
    liveness contract (observability.py's dead-writer finding covers the
    "visible in /api/quality/summary" half of that same contract)."""
    while True:
        await asyncio.sleep(5)
        capture_writer.ensure_alive()


async def _stream_consumer_liveness_loop(gateway, *, interval_sec: float = 10.0) -> None:
    """Backstop for a stuck stream consumer (issue #145) - polls
    gateway.ensure_consumer_progressing() every interval_sec. One instance
    per active KalshiStreamGateway (trade_stream, index_stream - same
    class, each its own connection/queue/consumer). See that method's own
    docstring for the detection signal and services/kalshi/websocket.py's
    _HANDLER_TIMEOUT_SEC for the complementary per-message bound this is a
    backstop for, not a replacement of."""
    while True:
        await asyncio.sleep(interval_sec)
        await gateway.ensure_consumer_progressing()


async def _ticker_flush_loop(gateway, *, interval_sec: float = 0.25) -> None:
    """Independent scheduled drain of gateway's ticker-coalescing map
    (issue #576) - calls gateway.flush_pending_tickers() every
    interval_sec, decoupled entirely from market_queue's own state so it
    keeps draining even while services/kalshi/websocket.py's
    _consume_market_from is fully suspended awaiting the trade-dispatch
    semaphore - the plausible starvation trigger #576 root-caused (a pure
    message-count fairness counter inside that consumer loop cannot help,
    because the loop isn't running while suspended there). See that
    module's flush_pending_tickers()/_TICKER_FLUSH_BATCH_MAX for the
    mechanism and batch-cap justification.

    interval_sec=0.25 (not the also-benchmarked 1.0s): reported delivery
    numbers from a separate benchmarking pass (350/1800 at 1.0s vs.
    1475/1800 at 0.25s, idle-tick overhead 0.58-4.3ms) show a monotonic
    improvement with a shorter interval at negligible extra cost - relayed
    figures, not reproduced by this module's own tests, and not yet backed
    by a committed benchmark artifact in this repo (see PR #597's body for
    the caveat and a search for one). The *direction* (shorter interval
    wins, cheaply) is independently corroborated by a separate peer status
    commit describing the same benchmarking effort with different exact
    figures (git commit 1ce4811, docs/next-action.md as of that commit);
    the qualitative case for biasing toward the tighter interval holds
    regardless of exactly which digits are right.

    Wired only for trade_stream (see lifespan()): index_stream never calls
    set_market_tickers, so its own _ticker_by_market map is provably
    always empty - the same per-gateway-applicability reasoning
    _index_feed_backfill_loop's own CF-Benchmarks-only scoping already
    follows a few lines below."""
    while True:
        await asyncio.sleep(interval_sec)
        await gateway.flush_pending_tickers()


async def _index_feed_backfill_loop(gateway, *, interval_sec: float = 10.0) -> None:
    """Reconnect-gap backfill for services/index_feed/ (issue #260): every
    interval_sec, checks gateway.ingest_metrics()['connection'] for a
    reconnect this session hasn't backfilled yet and, if one just
    happened, fetches the missed CF Benchmarks window via the REST
    passthrough (services/index_feed/backfill.py) - the WS channel itself
    has no resume/replay capability (that module's own docstring), so a
    lost window is otherwise gone for good, degrading settlement_algebra's
    settlement-edge math for every crypto market that settles against it.

    Same interval as _stream_consumer_liveness_loop (10s), the existing
    periodic hook this piggybacks the same gating on: frequent enough that
    a gap is backfilled within one polling cycle of reconnecting, and an
    idle check (the overwhelmingly common case - no new reconnect) costs
    one dict comparison, no REST call, no signing. Skips entirely when
    credentials aren't loaded (gateway.signing_credentials() is None) -
    lifespan() only starts this loop when index_stream.enabled is already
    true, so that should never actually happen at runtime; the check is
    defense in depth, not the real gate."""
    while True:
        await asyncio.sleep(interval_sec)
        credentials = gateway.signing_credentials()
        if credentials is None:
            continue
        key_id, private_key = credentials

        async def _fetch(index_id, start_ts, end_ts, _key_id=key_id, _private_key=private_key):
            return await index_feed_backfill.fetch_cfbenchmarks_history(
                index_id, start_ts, end_ts,
                key_id=_key_id, private_key=_private_key, base_url=gateway.base_url,
            )

        await index_feed_backfill.check_and_backfill(
            gateway.ingest_metrics()["connection"], _fetch, gateway.index_ids,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Captured once, before anything that could call mark_history_changed()
    # off this thread starts running (docs/superpowers/specs/2026-09-03-
    # history-event-driven-design.md §4.3) - candidate_ledger.claim()/
    # record_decision() run on a tick_executor worker thread and need this
    # reference to dispatch their history_updated broadcast via
    # asyncio.run_coroutine_threadsafe rather than asyncio.create_task,
    # which would raise RuntimeError from that thread.
    history_push.set_main_loop(asyncio.get_running_loop())
    # restart=True on these three: they're the long-running loops the app
    # depends on for its entire purpose (ticking, whale trades, index
    # data) - if one dies from an unhandled exception it must come back,
    # not just vanish. See task_supervisor.py's own docstring for the
    # incidents (6973974, a31ae51, 12323cc) this is meant to catch.
    task = task_supervisor.supervise(trading_loop, component="trading_loop", operation="run", restart=True)
    loop_watchdog_task = task_supervisor.supervise(
        lambda: loop_watchdog.start_forever(), component="loop_watchdog", operation="run", restart=True,
    )
    # Unwired today (P3 Task 14): nothing calls capture_writer.submit() yet
    # (Task 15 routes series_watcher.record_trade through it) - starting it
    # now is safe since an idle writer with empty buffers never opens a DB
    # connection (capture_writer._flush_store's own early return).
    capture_writer.start()
    capture_writer_liveness_task = task_supervisor.supervise(
        _capture_writer_liveness_loop, component="capture_writer", operation="liveness", restart=True,
    )
    # P8 Task 36: the background trigger checks run from their own supervised
    # loops, not from trading_loop's body - see _scheduler_loop.
    scheduler_tasks = [
        task_supervisor.supervise(
            lambda t=trigger, n=name: _scheduler_loop(t, n),
            component="scheduler", operation=name, restart=True,
        )
        for name, trigger in _SCHEDULER_TRIGGERS
    ]
    scheduler_tasks.append(task_supervisor.supervise(
        _candidate_retry_loop, component="scheduler", operation="candidate_retry", restart=True,
    ))
    scheduler_tasks.append(task_supervisor.supervise(
        _settlement_resolver_loop, component="scheduler", operation="settlement_resolver", restart=True,
    ))
    trade_stream_task = None
    trade_stream_liveness_task = None
    trade_stream_ticker_flush_task = None
    if _streaming_trade_tape_enabled():
        trade_stream_task = task_supervisor.supervise(
            lambda: trade_stream.run(
                whale_stream_handlers._process_stream_trade, whale_stream_handlers._process_stream_ticker,
                whale_stream_handlers._handle_trade_stream_status,
                on_fill=whale_stream_handlers._process_stream_fill,
                on_position=whale_stream_handlers._process_stream_position,
                on_lifecycle=whale_stream_handlers._process_stream_lifecycle,
            ),
            component="trade_stream", operation="run", restart=True,
        )
        trade_stream_liveness_task = task_supervisor.supervise(
            lambda: _stream_consumer_liveness_loop(trade_stream), component="trade_stream", operation="liveness", restart=True,
        )
        # Issue #576: independent ticker-map flush, trade_stream only (see
        # the flush loop function's own docstring for why index_stream is
        # excluded).
        trade_stream_ticker_flush_task = task_supervisor.supervise(
            lambda: _ticker_flush_loop(trade_stream), component="trade_stream", operation="ticker_flush", restart=True,
        )
    index_stream_task = None
    index_stream_liveness_task = None
    if index_stream.enabled and (index_stream.index_ids or index_stream.underlying_tickers):
        # Its own connection and its own task - see index_stream's own
        # comment for why this isn't just another channel on trade_stream.
        index_stream_task = task_supervisor.supervise(
            lambda: index_stream.run(
                index_stream_handlers._noop_stream_trade, index_stream_handlers._noop_stream_ticker,
                index_stream_handlers._handle_index_stream_status,
                on_index=index_stream_handlers._process_stream_index,
            ),
            component="index_stream", operation="run", restart=True,
        )
        index_stream_liveness_task = task_supervisor.supervise(
            lambda: _stream_consumer_liveness_loop(index_stream), component="index_stream", operation="liveness", restart=True,
        )
    index_feed_backfill_task = None
    if index_stream_task is not None and index_stream.index_ids:
        # Scoped to index_ids (CF Benchmarks) specifically, not
        # underlying_tickers (Pyth) - docs/kalshi/rest-passthrough.md's
        # historical-values backfill is CF Benchmarks-only; there is no
        # equivalent documented REST passthrough for Pyth to backfill from.
        index_feed_backfill_task = task_supervisor.supervise(
            lambda: _index_feed_backfill_loop(index_stream), component="index_stream", operation="backfill", restart=True,
        )
    yield
    if trade_stream_task is not None:
        await trade_stream.close()
        trade_stream_task.cancel()
        trade_stream_liveness_task.cancel()
        trade_stream_ticker_flush_task.cancel()
    if index_stream_task is not None:
        await index_stream.close()
        index_stream_task.cancel()
        index_stream_liveness_task.cancel()
        if index_feed_backfill_task is not None:
            index_feed_backfill_task.cancel()
    task.cancel()
    loop_watchdog_task.cancel()
    capture_writer_liveness_task.cancel()
    for scheduler_task in scheduler_tasks:
        scheduler_task.cancel()
    capture_writer.stop()
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

# Extracted route groups (2026-08-17) - see services/diagnostics/routes.py for
# the pattern. include_router preserves every path exactly as it was when
# these were @app.* in this file, so nothing client-side or test-side moves.
app.include_router(diagnostics_routes.router)
app.include_router(config_routes.router)
app.include_router(position_routes.router)
app.include_router(exits_routes.router)
app.include_router(whale_calibration_routes.router)
app.include_router(advisory_routes.router)
app.include_router(backtest_routes.router)
app.include_router(market_catalog_routes.router)
app.include_router(history_routes.router)
app.include_router(analytics_routes.router)
app.include_router(reset_routes.router)
app.include_router(backup_routes.router)
app.include_router(alerting_routes.router)
app.include_router(observability_routes.router)
app.include_router(storage_health_routes.router)
app.include_router(quality_routes.router)
app.include_router(research_routes.router)

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

# kalshi_account.trading_enabled is the one config value that turns on real
# order placement — it doesn't go through the generic config patch endpoint
# below at all, on purpose. See EnableTradingBody/enable_trading for the only
# path that can flip it on, which requires a real connected account and an
# exact-match typed confirmation phrase, not just a checkbox.
TRADING_CONFIRMATION_PHRASE = "ENABLE REAL TRADING"


class EnableTradingBody(BaseModel):
    confirmation_phrase: str


# Emergency flatten (2026-08-23 gap-check finding: no "get flat
# immediately" path existed at all for either broker) - its own
# confirmation phrase, same typed-confirmation pattern as real trading
# itself, since this is a real, irreversible action against both the
# paper account and (if connected/enabled) real capital.
FLATTEN_CONFIRMATION_PHRASE = "FLATTEN ALL POSITIONS"


class FlattenAllBody(BaseModel):
    confirmation_phrase: str


_state_body_cache = {"generation": None, "body": None}  # see get_state()

_last_event_live_data_sent_at = 0.0  # 2026-09-03, Task 7 of docs/
# superpowers/plans/2026-09-03-tier1-backend-hygiene.md: event_live_data
# is 87.3% of /api/state's payload (live-measured) despite already being
# event-scoped; the underlying data only refreshes once every
# _EVENT_LIVE_DATA_REPOLL_SEC (60s, services/market_watch/event_metadata.py),
# so resending it every poll resends unchanged data most of the time.
# Reuses that existing constant directly rather than picking an
# independent number.


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
    global _last_event_live_data_sent_at
    scoped_market_titles = _scoped_market_titles(_relevant_tickers())
    # Augment market_titles with any event-level flags (so the UI can read
    # `mutually_exclusive` / `collateral_return_type` without an extra lookup)
    event_info = _scoped_event_titles(scoped_market_titles)
    now_for_eld = time.time()
    if now_for_eld - _last_event_live_data_sent_at >= _EVENT_LIVE_DATA_REPOLL_SEC:
        event_live_data = _scoped_event_live_data(scoped_market_titles)
        _last_event_live_data_sent_at = now_for_eld
    else:
        event_live_data = {}  # client already Object.assign-merges rather
        # than replaces (polling-and-websocket.js:87) - an empty dict here
        # is a safe no-op, not missing data.
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
        # Streaming-path liveness/measurement surface (C3 finding,
        # 2026-08-25): both were populated in-process (observability has
        # been sampling trade_stream_perf all along) but never served -
        # the A17 soak had to reconstruct stream throughput from
        # data/observability.db history because the live snapshot omitted
        # the purpose-built per-second perf window. A dashboard/diagnostic
        # read of /api/state can now tell "stream quiet" from "handler
        # stalled" directly.
        "trade_tape_last_fetch_ts": state.get("trade_tape_last_fetch_ts"),
        "trade_stream_perf": state.get("trade_stream_perf"),
        "live_status": state["live_status"],
        # Shallow-copied, not passed through by reference (adversarial
        # review of PR #647/issue #634, 2026-09-06): now that get_state's
        # jsonable_encoder call runs on a worker thread instead of
        # monopolizing the event loop, these four fields' live containers
        # can genuinely be mutated in place by the trading loop
        # concurrently with a worker thread still walking this exact dict -
        # a race that was structurally impossible before that fix.
        # services/whale_stream/whale_stream_handlers.py inserts a new
        # ticker key into state["latest_prices"] in place (reproduced live:
        # a same-instant dict resize during iteration on another thread
        # raises RuntimeError: dictionary changed size during iteration -
        # exactly jsonable_encoder's dict-recursion branch);
        # services/whale_stream/decision_bridge.py mutates
        # state["signal_feed"]/state["decision_feed"] in place
        # (`.insert(0, ...)` on the existing list object, one line before
        # rebinding to a new sliced object - lists have no iterator version
        # check, so this doesn't raise, it silently duplicates or drops an
        # entry in the served JSON instead) and increments
        # state["stats"][key] in place. A shallow copy here decouples the
        # dict this function returns from whatever those call sites do
        # next - cheap, since _build_state_body() itself is already
        # memoized by generation (this only runs once per generation bump,
        # not once per request). See
        # test_state_body_mutable_feed_fields_are_snapshotted_not_live_references.
        "latest_prices": dict(state["latest_prices"]),
        "signal_feed": list(state["signal_feed"]),
        "decision_feed": list(state["decision_feed"]),
        "stats": dict(state["stats"]),
        "equity_history": state["equity_history"],
        "real_balance_history": state["real_balance_history"],
        "whale_track_record": signal_log.stats(days=30),
        "series_track_record": state["series_track_record"],
        "series_meta": _series_meta_map({r["series"] for r in state["series_track_record"].values()}),
        "last_poll": state["last_poll"],
        "last_tick_duration_sec": state["last_tick_duration_sec"],
        "last_tick_rate_limit_hits": state["last_tick_rate_limit_hits"],
        "tick_phase_timings": state["tick_phase_timings"],
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
        "lifecycle_stream_stats": state["lifecycle_stream_stats"],
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
async def get_state(request: Request):
    # ETag is just the generation counter - cheap to compute, and exact
    # (bumped only on a real change, see _bump_generation/state["generation"]).
    # A poll that lands between real changes (the common case at a 5s
    # frontend interval against a 15s backend poll_interval_sec) costs a
    # conditional request's worth of headers instead of the full ~40KB body,
    # re-fetched and re-parsed for data the dashboard already has. This 304
    # path is untouched by issue #634's fix below - it returns before ever
    # reaching _build_state_body()/jsonable_encoder.
    etag = f'"{state["generation"]}"'
    if _if_none_match_hits(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
    # Issue #634: _build_state_body()'s returned dict is memoized by
    # state["generation"] (see that function's own docstring) - but turning
    # that dict into JSON bytes was NOT memoized, and ran synchronously, on
    # the event loop, on every single 200 response. FastAPI's default
    # handling for a returned plain dict calls jsonable_encoder(...) (a
    # pure-Python recursive walk - every single value, at every nesting
    # level, checks isinstance(obj, BaseModel) and dataclasses.is_dataclass(obj)
    # before falling through to the primitive/dict/list cases; confirmed by
    # reading fastapi/encoders.py's jsonable_encoder at the pinned
    # fastapi==0.134.0, not assumed - so a live loop_watchdog capture
    # showing "is_dataclass" deep in the recursion does NOT by itself mean a
    # real dataclass instance was present anywhere in this payload; that
    # frame appears on literally every value jsonable_encoder ever touches)
    # directly inline via fastapi/routing.py's serialize_response, with zero
    # yield points - real, measured cost at this route's actual observed
    # sizes (nginx access log: 33-35KB most polls, 747-782KB on the ~60s
    # event_live_data repoll cycle - see _EVENT_LIVE_DATA_REPOLL_SEC): a
    # synthetic payload built to the same shape/size measured
    # jsonable_encoder alone at ~3.2ms (33KB) / ~38ms (780KB) / ~164ms (3.45MB,
    # the #634 issue body's own worst observed figure) - real, recurring,
    # fully event-loop-blocking cost on this app's one shared trading loop,
    # but NOT, on its own, large enough to explain the separately-tracked
    # 9.3-9.6s stall magnitude issue #605 also recorded around the same
    # time (that magnitude is issue #150's leaked-ThreadPoolExecutor-worker
    # mechanism, a different bug - see #605's own comment thread). Offloading
    # jsonable_encoder to a thread (matching #552/#629/#630/#636's precedent
    # for this exact class of problem) removes it from the loop regardless
    # of which field would have triggered the slow path; json.dumps on the
    # now-already-jsonable result still runs inline inside JSONResponse
    # (cheap - ~0.5-25ms across the same size range, since it's a single
    # C-accelerated pass over already-primitive data, not a per-value
    # Python recursion). asyncio.to_thread's own dispatch overhead measured
    # negligible (~+0.1ms, within run-to-run noise) against the ~3.2ms small
    # common case, so this does not tax the cheap path to fix the rare one.
    #
    # Headers must be built explicitly here rather than left on the
    # `response: Response` FastAPI would otherwise inject: verified by
    # reading fastapi/routing.py's get_request_handler at 0.134.0 that its
    # `response.headers.raw.extend(...)` merge onto the final response ONLY
    # runs in the branch where the endpoint returns a plain (non-Response)
    # value - when an endpoint returns a Response instance directly (as
    # this one now does), FastAPI uses it completely as-is and never merges
    # anything set on an injected `response` parameter. Relying on that
    # parameter here would have silently dropped ETag/Cache-Control on
    # every 200 response - covered by
    # test_state_endpoint_200_preserves_etag_and_cache_control_headers.
    body = _build_state_body()
    encoded = await asyncio.to_thread(jsonable_encoder, body)
    return JSONResponse(content=encoded, headers={"ETag": etag, "Cache-Control": "no-cache"})


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
    bump_generation()
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
    bump_generation()
    return {"trading_enabled": False}


@app.post("/api/trading/flatten-all")
async def flatten_all_positions(body: FlattenAllBody):
    # Emergency "get flat immediately" (2026-08-23 gap-check finding) - a
    # real, irreversible action, so it gets the same typed-confirmation
    # gate as real trading itself rather than a plain checkbox. Always
    # flattens the paper account (harmless, fully reversible via the
    # Danger Zone); also flattens the real account whenever
    # kalshi_account.trading_enabled is true - see
    # services/execution.py's flatten_all_real_positions docstring for the
    # order construction and its disclosed lack of a real-fill
    # verification yet (moved above the vendor adapter at Task A9).
    if body.confirmation_phrase != FLATTEN_CONFIRMATION_PHRASE:
        raise HTTPException(
            status_code=400,
            detail=f'Confirmation phrase did not match. Type exactly: "{FLATTEN_CONFIRMATION_PHRASE}"',
        )
    paper_closed = broker.close_all_positions(
        state["latest_prices"], "manual flatten-all", latest_asks=state["latest_asks"],
    )
    real_result = None
    if account.trading_enabled:
        real_result = await execution.flatten_all_real_positions(account)
    bump_generation()
    return {
        "paper_closed": [t.to_dict() for t in paper_closed],
        "real_result": real_result,
    }


CLOSE_POSITIONS_CONFIRMATION_PHRASE = "CLOSE SELECTED POSITIONS"


class ClosePositionsBody(BaseModel):
    tickers: list[str]
    confirmation_phrase: str
    reason: str = "manual close"


@app.post("/api/trading/close-positions")
async def close_positions(body: ClosePositionsBody):
    # Selective sibling of flatten-all above (2026-09-03, off-watchlist
    # entry bleed remediation): flatten-all is all-or-nothing, and there
    # was no path to close only a SUBSET of open positions - e.g. the ones
    # a bug opened outside the user's configured watchlist while leaving
    # legitimate ones open - short of a full flatten. Same typed-
    # confirmation gate as flatten-all: a real, irreversible action, paper
    # account only (a real-account equivalent isn't needed - real trading
    # has never been enabled, see services/position/README.md).
    if body.confirmation_phrase != CLOSE_POSITIONS_CONFIRMATION_PHRASE:
        raise HTTPException(
            status_code=400,
            detail=f'Confirmation phrase did not match. Type exactly: "{CLOSE_POSITIONS_CONFIRMATION_PHRASE}"',
        )
    if not body.tickers:
        raise HTTPException(status_code=400, detail="tickers must be a non-empty list")
    closed = []
    missing = []
    for ticker in body.tickers:
        if ticker not in broker.positions:
            missing.append(ticker)
            continue
        pos = broker.positions[ticker]
        # Sold into the side of the book this position actually exits on -
        # yes_bid for YES, yes_ask for NO (the NO bid is 1 - yes_ask). This
        # endpoint priced both sides off latest_prices (yes_bid) until
        # 2026-09-04, so a NO close on an empty yes book paid $1.00/contract
        # as if the market had settled NO. forced_exit_quote, not
        # sellable_quote: a manual close must not refuse, so an unsellable
        # book resolves to zero proceeds rather than a fabricated payout.
        price = kalshi_fees.forced_exit_quote(
            pos.side,
            state["latest_prices"].get(ticker, pos.entry_price),
            state["latest_asks"].get(ticker),
            unknown_fallback=pos.entry_price,
        )
        trade = broker.close_position(ticker, price, body.reason)
        if trade is not None:
            closed.append(trade)
    bump_generation()
    return {
        "closed": [t.to_dict() for t in closed],
        "missing": missing,
    }


@app.post("/api/toggle")
async def toggle_running():
    state["running"] = not state["running"]
    bump_generation()
    return {"running": state["running"]}


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
        "active_provider": get_whale_provider().name,
    }


@app.post("/api/accounts/connect")
async def connect_account(body: ConnectAccountBody):
    try:
        accounts_store.save(body.provider, body.credentials)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if body.provider == get_whale_provider().name:
        # Re-instantiate so it picks up the new creds now. Goes through
        # app_state so every path sees the swap - a `global whale_provider`
        # here only ever rebound main's own name, leaving the WS-trade path
        # and diagnostics on the dead instance with a separate #546 dedupe
        # ledger (issue #565).
        reload_whale_provider()
    return {"ok": True}


@app.post("/api/accounts/{provider}/disconnect")
async def disconnect_account(provider: str):
    accounts_store.delete(provider)
    if provider == get_whale_provider().name:
        reload_whale_provider()  # see connect_account - one rebind, every path (#565)
    return {"ok": True}


# Dashboard/status/login/accounts pages used to be served here via
# FileResponse/StaticFiles. Moved to ddev's "web" (nginx) container serving
# static/ directly, with /api/ and /auth/ reverse-proxied back to this
# service (see .ddev/nginx/kalshi-proxy.conf) — main.py is API-only now, so
# a separate frontend can be built against it without this process also
# owning page-serving. nginx replicates the same no-cache intent that used
# to live in NO_CACHE_HEADERS here (see that config's comment for why).


