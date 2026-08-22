"""
Shared runtime state and long-lived singletons for the app.

Extracted from main.py 2026-08-17 (direct request: "main.py is now almost
6k lines long, it would be worth it to make it more modular/less monolithic
for better logic targeting and also session efficiency"). This is the
enabling move for that split rather than a cosmetic one: essentially every
route and stream handler in main.py reads or writes the `state` dict and
these singletons, so while they lived in main.py nothing could be lifted
out of it without a circular import. With them here, a router module can
`from services.app_state import state, broker, risk` and be moved out
freely.

Deliberately imports nothing from main.py, and nothing here imports it -
that acyclic direction is the whole point.

`state` is a single mutable dict shared by the trading loop, the websocket
handlers and every route. That is unchanged from before this extraction -
moving it did not make it more or less global, only importable. Its keys
are documented inline below.

NOTE for tests: constructing PaperBroker/RiskManager at import time must
never reach the live data/*.db files, so the established convention
(tests/test_trading_gate.py and friends) of monkeypatching each module's
DB_PATH *before* importing main still applies - main imports this module,
so the same ordering protects both.
"""
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
from services.advisory import advisory_engine
from services import auth as auth_service
from services.backtest import backtest
from services.whale_calibration import calibration_history
from services import candidate_log
from services import cross_strategy
from services.diagnostics import diagnostics
from services import regime_analytics
from services import stats_power
from services.whale_calibration import confidence_calibration
from services.market_events import event_lifecycle
from services.market_events import event_schedule
from services import market_strategy_calibration
from services import config_performance
from services import market_analyst_agent
from services.market_catalog import market_catalog
from services import market_history
from services import ml_feed
from services import mutual_exclusivity
from services.exits import position_netting
from services import reset_log
from services import series_cache
from services import series_evaluator
from services import series_watcher
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

cfg = config_store.get()
broker = PaperBroker(starting_bankroll=cfg["risk"]["starting_bankroll"])
risk = RiskManager(
    starting_bankroll=cfg["risk"]["starting_bankroll"],
    max_daily_loss_pct=cfg["risk"]["max_daily_loss_pct"],
    kill_switch_enabled=cfg["risk"]["kill_switch_enabled"],
)
strategy = FollowTheWhaleStrategy(broker, risk)
shadow = ShadowTrader(default_bankroll=cfg["risk"]["starting_bankroll"])

# MarketNativeStrategy (docs/advisory-engine-plan.md §9-adjacent, direct
# request 2026-08-08: "start storing and analyzing market data now") - a
# second, independent automated paper strategy with its own capital pool
# and its own data/*.db files, so its performance is cleanly measurable on
# its own and never contaminates the whale-follow broker/risk state above.
# Off by default (market_strategy.enabled) - see services/market_strategy.py.
# db_path is derived from broker.db_path/risk.db_path (not a fresh
# Path(__file__) lookup) specifically so tests that redirect those two
# instances' DB_PATH before importing main (see tests/test_trading_gate.py)
# transparently redirect these two as well - constructing a real
# PaperBroker/RiskManager at import time must never be able to reach the
# live data/*.db files no matter what a test does.
market_broker = PaperBroker(
    starting_bankroll=cfg["market_strategy"]["starting_bankroll"],
    db_path=broker.db_path.parent / "market_broker.db",
)
market_risk = RiskManager(
    starting_bankroll=cfg["market_strategy"]["starting_bankroll"],
    max_daily_loss_pct=cfg["market_strategy"]["max_daily_loss_pct"],
    kill_switch_enabled=cfg["market_strategy"]["kill_switch_enabled"],
    db_path=risk.db_path.parent / "market_risk_state.db",
)
market_strategy = MarketNativeStrategy(market_broker, market_risk)
whale_sim = WhaleSimulator(
    size_range=tuple(cfg["whale_signal"]["whale_size_range"]),
    bias=cfg["whale_signal"]["bias"],
)
whale_provider = get_active_provider()  # only active if its own env vars are set — see services/whalewatchers/
# Kalshi's demo and production environments use separate credentials and
# separate hosts (KALSHI_API_KEY_ID/KALSHI_PRIVATE_KEY_PATH for one won't
# authenticate against the other) — KALSHI_ACCOUNT_BASE_URL lets the account
# client point at either independently of kalshi.base_url above, which stays
# on production for public market data regardless.
account_base_url = os.environ.get("KALSHI_ACCOUNT_BASE_URL", "").strip() or cfg["kalshi"]["base_url"]
account = KalshiAccountClient(
    account_base_url,
    cfg["kalshi"]["request_timeout_sec"],
    cfg["kalshi_account"]["trading_enabled"],
)  # real account — only active if KALSHI_API_KEY_ID + KALSHI_PRIVATE_KEY_PATH are set in .env
# exchange_wide_trades (2026-08-17): subscribe the trade channel with no
# market_tickers so every print on the exchange arrives, not just those on
# the rotating watchlist - the single largest measured gap in this system
# (425 markets trading in a 30s window against 15 watched; 5 of 5 whale
# prints >=$2,500 invisible). Read at import time, like every other
# constructor arg here; flipping it needs a real restart, not a live config
# reload, because it changes what this connection subscribed to at handshake.
trade_stream = KalshiTradeWebSocketClient(
    account_base_url,
    exchange_wide_trades=bool(cfg["kalshi"].get("trade_stream_exchange_wide", False)),
    # The indices the crypto series settle against - see
    # services/index_feed.py. Empty list disables the subscription.
    # market_lifecycle_v2 (2026-08-17, docs/next-session-pickup-2026-08-17.md
    # item #2) - unconditionally exchange-wide push notifications for
    # market open/close/settlement, replacing part of the 6-second REST
    # poll's own job. Lives on this connection (already built for
    # exchange-wide volume via the reader/worker queue split), never on
    # index_stream below - see KalshiTradeWebSocketClient.__init__'s own
    # comment for why that isolation matters. Same config-gated,
    # read-at-import-time rollout shape as trade_stream_exchange_wide above.
    subscribe_lifecycle=bool(cfg["kalshi"].get("market_lifecycle_stream_enabled", True)),
)
# Index feeds get their OWN connection (2026-08-17), not a channel on the
# trade socket. Measured: subscribed alone, cfbenchmarks_value delivers
# exactly 1 message/second as documented; multiplexed behind an
# exchange-wide trade subscription it delivered ~20 and then appeared
# frozen for minutes, because a single reader loop drains the socket only
# as fast as its slowest handler. run()'s reader/worker split fixes the
# general case, but the index feed is the one stream whose value is
# entirely in its timeliness - it IS the settlement quantity - so it gets
# physical isolation rather than a fair share of a contended queue.
index_stream = KalshiTradeWebSocketClient(
    account_base_url,
    index_ids=list(cfg.get("index_feed", {}).get("index_ids") or []),
    underlying_tickers=list(cfg.get("index_feed", {}).get("underlying_tickers") or []),
)

state = {
    "running": True,
    "last_tick_duration_sec": None,  # wall-clock time of the most recently completed tick, see trading_loop
    "last_tick_rate_limit_hits": 0,  # 429s hit during that same tick - services/http_client.py's rolling counter
    "tick_phase_timings": {},  # wall-clock seconds per named phase of the most recent tick, see trading_loop
    "markets": [],
    "latest_prices": {},
    "latest_asks": {},  # maker/limit-order path (2026-08-15) - see check_pending_fills wiring below
    "event_phase": {},  # event_ticker -> pre_tail/mid_series/post_tail/no_occurrence, see services/market_events/event_lifecycle.py
    # Seeded from data/title_cache.db (see services/title_cache.py) rather
    # than {} - these two accumulate over the app's whole lifetime, not just
    # since the last uvicorn --reload restart, so a ticker/event learned
    # once keeps its title even after it rotates off the top-volume
    # watchlist or the dev server reloads.
    "market_titles": title_cache.load_market_titles(),
    "event_titles": title_cache.load_event_titles(),  # event_ticker -> {"title", "sub_title", "category", "mutually_exclusive"}, see _fetch_event_titles
    "me_pairs": {},  # ticker -> complement_ticker, confirmed 2-outcome mutually-exclusive pairs, see services/mutual_exclusivity.py
    "trade_tape": [],  # real trades across the current watchlist, newest first, see _fetch_trade_tape
    "trade_tape_last_fetch_ts": None,  # watermark for _fetch_trade_tape's incremental min_ts fetch - None until the first successful tick
    "market_results": {},
    "live_status": {},  # event_ticker -> "live" | "finished" | "none" | None, see _fetch_live_status
    "event_live_data": {},  # event_ticker -> live_data payload from /live_data/events/{event_ticker}
    # event_ticker -> {"details": {away_points, home_points, clock, quarter,
    # status, winner, last_play, situation, ...}, "updated_at": ts} - the
    # real in-game state from the same milestone get_live_data(s) call
    # _fetch_live_status already makes every poll to derive widget_status,
    # previously discarded past that one field (2026-08-16 API-doc audit
    # finding B2, docs/next-steps-2026-08-15-pt3.md). Accumulates like
    # live_status_cache below, not replaced wholesale - a market that
    # rotates off the watchlist mid-game just stops getting fresh updates
    # rather than losing its last-known state.
    "live_game_state": {},
    "category_metadata": {"fetched_at": 0.0, "tags_by_categories": {}, "filters_by_sports": {}, "sport_ordering": []},
    # Survives across ticks (unlike live_status above, still replaced wholesale
    # every tick for the current-tick view) - event_ticker -> {"status",
    # "checked_at"}, the memory that lets _fetch_live_status poll lightly
    # instead of re-deriving every event's status from 2 fresh API calls
    # every single tick. See _fetch_live_status.
    "live_status_cache": {},
    # Same repoll-cache shape as live_status_cache above, for the two other
    # per-event REST loops that used to run unconditionally on every single
    # tick (2026-08-15 tick_duration investigation - confirmed live: every
    # one of these calls 404s or comes back empty for the entire current
    # watchlist, every tick, forever, since neither had ANY caching at all -
    # see _fetch_event_live_data and propagate_milestone_winners).
    # event_ticker -> {"data": live_data|None, "checked_at": ts}.
    "event_live_data_cache": {},
    # event_ticker -> {"checked_at": ts, "winner_found": bool, "related":
    # [...]|None, "mapped_winner_ticker": str|None}.
    "milestone_cache": {},
    # Decouples _check_signal_resolutions from the main poll_interval_sec
    # trading-tick cadence (2026-08-15 direct instruction) - see that
    # function's own docstring for why. Memory-only, not persisted: worst
    # case after a restart is one resolution-check running a few seconds
    # earlier than its interval would otherwise allow, not a correctness
    # issue worth a whole persistence layer over.
    "signal_resolution_check": {"last_checked_at": 0.0, "checking": False, "task": None},
    # Seeded from data/series_cache.db (services/series_cache.py, 2026-08-15)
    # rather than the empty shape - same "a restart shouldn't force an
    # immediate re-fetch of data that's still fresh per its own TTL" fix as
    # market_titles/event_titles above, just for the ~9,400-series catalog
    # instead. _get_series_cache's own _SERIES_CACHE_TTL_SEC (1h) check is
    # unchanged; only what it measures against now survives a reload.
    "series_cache": series_cache.load(),  # see _get_top_series
    "market_object_cache": {},  # ticker -> {market dict, "_cached_at"} - see _cached_market_fetch
    # Deliberately memory-only, NOT persisted like series_cache above -
    # direct instruction (2026-08-15): active-monitoring/near-real-time
    # data belongs in memory, not on disk. _DISCOVERY_REFRESH_SEC is 90s,
    # short enough that a persisted copy would already be stale-per-its-
    # own-policy the moment a restart finished loading it back - there's no
    # "still fresh" window worth protecting the way series_cache's 1h one
    # is.
    # "refreshing" (2026-08-15) - guards _maybe_refresh_discovery_cache
    # against launching a second overlapping background refresh while one
    # is still in flight; "task" holds the asyncio.Task itself so it isn't
    # garbage-collected mid-flight (a real asyncio footgun - a task with no
    # live reference anywhere can be collected before it completes).
    "discovery_cache": {"fetched_at": 0.0, "markets": [], "refreshing": False, "task": None},  # see _DISCOVERY_REFRESH_SEC
    # Same background-task decoupling as discovery_cache above, for
    # market_catalog's incremental scan (see _maybe_scan_catalog_batch).
    "catalog_scan": {"scanning": False, "last_started_at": 0.0, "task": None},
    # Seeded from data/event_schedule.db (services/market_events/event_schedule.py,
    # 2026-08-15) - event_ticker -> {"start_ts", "end_ts", "source",
    # "resolved_at"} | None. See _resolve_event_schedules and _handle_signal's
    # is_live computation.
    "event_schedules": event_schedule.load_all(),
    "series_track_record": {},
    "signal_feed": [],   # most recent first
    "decision_feed": [],
    # market_strategy.py's own decision feed - deliberately separate from
    # decision_feed above (not merged), same "each strategy's performance
    # cleanly, independently measurable" principle already documented at
    # the trading-loop call site. Existed as a real gap until the
    # Market-Native tab (2026-08-10, direct request) needed somewhere to
    # show it - evaluate_all()/check_exits()'s return values were
    # previously computed then discarded every tick.
    "market_decision_feed": [],
    "stats": {"signals_seen": 0, "trades_placed": 0, "skipped": 0, "limit_orders_placed": 0},
    "equity_history": [],  # [{"t": unix_ts, "equity": float}, ...], capped, for the Portfolio view's chart
    "real_balance_history": [],  # same shape, for the real-account toggle — only grows if a real account is connected
    "exchange_status": None,  # {"exchange_active": bool, "trading_active": bool, ...} — see _fetch_exchange_status
    "last_poll": None,
    "error": None,
    "trade_stream_status": {
        "enabled": whale_provider.name == "kalshi_trade_tape" and trade_stream.enabled,
        "connected": False,
        "error": None,
        "ws_url": trade_stream.status.get("ws_url"),
        "mode": "stream" if whale_provider.name == "kalshi_trade_tape" and trade_stream.enabled else "poll",
    },
    # market_lifecycle_v2 observability (2026-08-17) - a genuinely new,
    # never-before-observed-live channel (see main.py._process_stream_
    # lifecycle), exposed on /api/state so its real event volume/shape can
    # be verified without grepping logs, same reasoning as
    # kalshi_trade_tape.py's own self.stats for exchange-wide trade.
    "lifecycle_stream_stats": {"events_by_type": {}, "close_time_updates_applied": 0, "last_event_at": None},
    "whale_source": whale_provider.name if whale_provider.enabled else "simulated",
    "account": {
        "connected": account.enabled, "balance": None, "positions": None, "fills": None,
        "error": None, "trading_enabled": account.trading_enabled,
    },
    # Bumped on every real change to anything /api/state reports - a poll
    # tick completing, or one of the handful of control endpoints that
    # mutate state outside the loop (toggle/halt/resume/reset/config/
    # trading-enable-disable). GET /api/state uses this as an ETag so a
    # poll that lands between real changes costs a conditional-GET's worth
    # of headers, not the full ~40KB body re-fetched and re-parsed for
    # nothing - see get_state()/_build_state_body().
    "generation": 0,
}


def bump_generation() -> None:
    """Marks a real change to anything /api/state reports - see
    state["generation"]'s own comment above. Lives here (not in main.py)
    so every router/module that mutates `state` can call it without
    reaching back into main.py - main.py's modularization pass (2026-08-21)
    moved this alongside `state` itself since it's called from nearly every
    bucket main.py is being split into."""
    state["generation"] += 1
