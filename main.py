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
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
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
from services.diagnostics import diagnostics
from services import regime_analytics
from services.whale_calibration import confidence_calibration
from services.market_events import event_lifecycle
from services.market_events import event_schedule
from services import config_performance
from services import market_analyst_agent
from services.market_catalog import market_catalog
from services import market_history
from services import mutual_exclusivity
from services.exits import position_netting
from services import reset_log
from services import series_cache
from services import series_evaluator
from services import series_watcher
from services import fault_log
from services import loop_watchdog
from services import tick_executor
from services import task_supervisor
from services import game_state
from services import index_feed
from services import settlement_edge
from services import trade_archive
from services import signal_log
from services import title_cache
from services import trade_analytics
from services import trade_category
from services.config_store import config_store
from services.http_client import classify, close_client, get_and_reset_rate_limit_hits, http_metrics_snapshot
from services.kalshi.public import KalshiPublicGateway
from services.kalshi.account_client import KalshiAccountClient
from services.kalshi.websocket import KalshiStreamGateway
from services.confidence_scoring import WhaleSignal
from services.whale_simulator import WhaleSimulator
from services.whalewatchers import PROVIDERS, get_active_provider
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
    _streaming_trade_tape_enabled, _TRADE_TAPE_UI_CAP,
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
    _MILESTONE_REPOLL_SEC, propagate_milestone_winners, _refresh_discovery_cache,
    _refresh_discovery_cache_background, _scan_catalog_batch, _slim_market,
)
from services.backup import _maybe_run_backup  # noqa: E402
from services.backup import routes as backup_routes  # noqa: E402
from services.alerting import check_and_alert  # noqa: E402
from services.alerting import routes as alerting_routes  # noqa: E402
from services.observability import maybe_capture as _maybe_capture_observability  # noqa: E402
from services.observability import observability  # noqa: E402
from services.observability import routes as observability_routes  # noqa: E402
from services.quality import routes as quality_routes  # noqa: E402
from services.research import _maybe_run_research  # noqa: E402
from services.research import routes as research_routes  # noqa: E402
from services.storage_health import storage_health  # noqa: E402
from services.storage_health import routes as storage_health_routes  # noqa: E402
from services.app_state import (  # noqa: E402
    account, account_base_url, broker, bump_generation, cfg, index_stream,
    risk, shadow, state, strategy, trade_stream, whale_provider,
    whale_sim,
)
from services.account_positions import (  # noqa: E402
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
    obs_hours = float((cfg.get("observability") or {}).get("retention_hours", 336))
    observability.prune(retention_hours=obs_hours, now=now)


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
    fix, services/whale_calibration/CHEATSHEET.md's own audit finding -
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
    """Synchronous batched write into series_watcher's raw_trades table -
    root-cause report C1's specifically measured 0.65-1.6s executemany on
    essentially every tick. A plain sync function so it is directly
    unit-testable and directly callable from tick_executor's worker thread
    (realtime data-plane remediation plan, P1 Task 7)."""
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
            _maybe_check_signal_resolutions(cfg)
            _maybe_run_backup(cfg)
            _maybe_run_research(cfg)
            event_schedule._maybe_resolve_event_schedules(cfg)
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
            _maybe_scan_catalog_batch(cfg)
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
                        bump_generation()
            phase_timings["calibration_advisory"] = round(time.time() - _phase_t, 3)
            _phase_t = time.time()

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
            if _streaming_trade_tape_enabled():
                # P2 Task 13: retries H4-unmarked candidates (services/
                # candidate_retry.py) once per tick, stream mode only -
                # mirrors the trade-tape branch above since a retry's own
                # market lookup is only meaningful when the exchange-wide
                # stream is what feeds whale candidates in the first
                # place. Normally a near-instant no-op (nothing due).
                # whale_provider + _handle_signal passed through (code-review
                # fix, finding #1) so a recovered candidate is actually
                # scored and evaluated through the same pipeline a first-try
                # trade uses, not just claimed and dropped.
                await candidate_retry.run_pending(
                    client, whale_provider, _handle_signal, cfg, market_results, config_fp, tick_now,
                )
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
            # The record loop + batched flush (see series_watcher.flush) is
            # root-cause report C1's specifically measured 0.65-1.6s
            # synchronous executemany into the 16.9M-row raw_trades table on
            # essentially every tick - routed through tick_executor (P1
            # Task 7) so it runs off this loop instead of starving the WS
            # consumer for that whole stretch.
            await _flush_trade_capture_async(trade_tape, cfg)
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
            # the same gates evaluate() applied at placement time (the
            # "four-entry gate bypass" fix - see strategy_engine.py's
            # validate_pending_fill/_validate_entry_price docstrings). Same
            # is_live/category/seconds_to_close derivation as
            # _handle_signal's own (decision_bridge.py), just computed
            # fresh at fill time instead of signal time.
            def _validate_fill(ticker: str, side: str, price: float, confidence: float | None) -> tuple[bool, str | None]:
                market_info = state["market_titles"].get(ticker) or {}
                event_ticker = market_info.get("event_ticker")
                is_live = state["live_status"].get(event_ticker) == "live" if event_ticker else False
                if not is_live and event_ticker:
                    is_live = state["event_phase"].get(event_ticker) == event_lifecycle.MID_SERIES
                seconds_to_close = market_history.seconds_to_close(_close_time_by_ticker().get(ticker), tick_now)
                return strategy.validate_pending_fill(
                    ticker, side, price, confidence, cfg,
                    category=_category_by_ticker().get(ticker), is_live=is_live, seconds_to_close=seconds_to_close,
                )

            for fill_decision in broker.check_pending_fills(
                state["latest_prices"], state["latest_asks"], validate_fn=_validate_fill,
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
            for close_decision in strategy.check_exits(
                state["latest_prices"], state["signal_feed"], cfg, market_results, opened_since=tick_now,
                category_by_ticker=_category_by_ticker(), close_times=_close_time_by_ticker(),
            ):
                await _handle_close_decision(close_decision)

            # Position netting (2026-08-15 direct correction: the ME-gate
            # above only blocks a NEW entry into a confirmed complement -
            # it does nothing for positions already open, partial hedges,
            # or N-way concentration). Runs after check_exits, on whatever
            # survived per-position rules - see services/exits/position_netting.py
            # for the payout-profile math. Entirely opt-in
            # (position_netting.enabled, default False) and a no-op until
            # deliberately turned on.
            for close_decision in position_netting.review(
                broker, state["market_titles"], state["event_titles"], state["latest_prices"], cfg,
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
        await asyncio.sleep(cfg["kalshi"]["poll_interval_sec"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    # restart=True on these three: they're the long-running loops the app
    # depends on for its entire purpose (ticking, whale trades, index
    # data) - if one dies from an unhandled exception it must come back,
    # not just vanish. See task_supervisor.py's own docstring for the
    # incidents (6973974, a31ae51, 12323cc) this is meant to catch.
    task = task_supervisor.supervise(trading_loop, component="trading_loop", operation="run", restart=True)
    loop_watchdog_task = task_supervisor.supervise(
        lambda: loop_watchdog.start_forever(), component="loop_watchdog", operation="run", restart=True,
    )
    trade_stream_task = None
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
    index_stream_task = None
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
    yield
    if trade_stream_task is not None:
        await trade_stream.close()
        trade_stream_task.cancel()
    if index_stream_task is not None:
        await index_stream.close()
        index_stream_task.cancel()
    task.cancel()
    loop_watchdog_task.cancel()
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
    paper_closed = broker.close_all_positions(state["latest_prices"], "manual flatten-all")
    real_result = None
    if account.trading_enabled:
        real_result = await execution.flatten_all_real_positions(account)
    bump_generation()
    return {
        "paper_closed": [t.to_dict() for t in paper_closed],
        "real_result": real_result,
    }


@app.post("/api/toggle")
async def toggle_running():
    state["running"] = not state["running"]
    bump_generation()
    return {"running": state["running"]}


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
    # Opt-in (2026-08-27 direct request): a paper reset otherwise archives
    # open positions as-is, unrealized P&L never credited - abandoned, not
    # closed. When true (and the reset is unscoped - a ranged reset never
    # touches positions/bankroll to begin with, see range_start/range_end
    # below), every open position is flattened at its latest known price
    # via the same broker.close_all_positions() POST /api/trading/
    # flatten-all already uses, BEFORE the archive snapshot is taken - so
    # the permanent archive records real realized closes instead of
    # orphaned archived_positions rows.
    close_positions_first: bool = False
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
        # Only meaningful when it will actually run - see reset_broker's own
        # "not ranged" guard for why a ranged reset never closes positions.
        if body.close_positions_first and not (body.range_start or body.range_end):
            counts["close_positions_first"] = len(broker.positions)
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
    if body.trade_category:
        counts["trade_category"] = trade_category.count_range(body.range_end, body.range_start)
    return counts


@app.get("/api/reset/preview")
async def reset_preview(
    paper: bool = False, shadow: bool = False, signal_log: bool = False, market_analyst: bool = False,
    market_catalog: bool = False, market_history: bool = False, series_evaluator: bool = False,
    candidate_log: bool = False, calibration_history: bool = False,
    trade_category: bool = False, close_positions_first: bool = False,
    range_start: float | None = None, range_end: float | None = None,
):
    # Dry-run counterpart to POST /api/reset - same domain/range selection,
    # deletes nothing. Powers the Danger Zone's "here's what you're about
    # to lose" step (2026-08-16 direct request) before the real request
    # fires. Query params, not a body, since this is a GET (no side effects).
    body = ResetBody(
        paper=paper, shadow=shadow, signal_log=signal_log, market_analyst=market_analyst,
        market_catalog=market_catalog, market_history=market_history, series_evaluator=series_evaluator,
        candidate_log=candidate_log, calibration_history=calibration_history,
        trade_category=trade_category, close_positions_first=close_positions_first,
        range_start=range_start, range_end=range_end,
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
        # Close open positions BEFORE the archive snapshot (2026-08-27 direct
        # request), so archive_epoch below records real, realized closes
        # instead of orphaned archived_positions rows with unrealized P&L
        # never credited. Skipped for a ranged reset - positions/bankroll are
        # current live state, never touched by range scoping (see
        # ResetBody.range_start's own docstring), so closing them here would
        # surprise a caller who only asked to purge a date range of history.
        if body.close_positions_first and not ranged:
            closed = broker.close_all_positions(
                state["latest_prices"], f"reset: closed before {scope} reset",
            )
            cleared.append({"domain": "close_positions_first", "closed": len(closed)})
        # Archive BEFORE anything else is destroyed (2026-08-17 direct
        # request: "a safe reset of the paper trading mechanic while
        # maintaining a log of important data"). The motivating incident is
        # concrete: a prior reset left paper_broker.db reaching back only to
        # 08/16 19:28, so every trade-level question about anything earlier -
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
    if body.trade_category:
        deleted = trade_category.clear_range(body.range_end, body.range_start)
        _log("trade_category", deleted)
        cleared.append("trade_category")
    bump_generation()
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


