import asyncio
import os
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()  # reads .env if present; every var is optional, see .env.example

from services import accounts_store
from services import advisory_engine
from services import auth as auth_service
from services import confidence_calibration
from services import config_performance
from services import market_analyst_agent
from services import market_catalog
from services import market_history
from services import ml_feed
from services import signal_log
from services import title_cache
from services import trade_analytics
from services.config_store import config_store
from services.http_client import close_client
from services.kalshi_client import KalshiClient
from services.kalshi_account_client import KalshiAccountClient
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

state = {
    "running": True,
    "markets": [],
    "latest_prices": {},
    # Seeded from data/title_cache.db (see services/title_cache.py) rather
    # than {} - these two accumulate over the app's whole lifetime, not just
    # since the last uvicorn --reload restart, so a ticker/event learned
    # once keeps its title even after it rotates off the top-volume
    # watchlist or the dev server reloads.
    "market_titles": title_cache.load_market_titles(),
    "event_titles": title_cache.load_event_titles(),  # event_ticker -> {"title", "sub_title", "category", "mutually_exclusive"}, see _fetch_event_titles
    "trade_tape": [],  # real trades across the current watchlist, newest first, see _fetch_trade_tape
    "live_status": {},  # event_ticker -> "live" | "finished" | "none" | None, see _fetch_live_status
    # Survives across ticks (unlike live_status above, still replaced wholesale
    # every tick for the current-tick view) - event_ticker -> {"status",
    # "checked_at"}, the memory that lets _fetch_live_status poll lightly
    # instead of re-deriving every event's status from 2 fresh API calls
    # every single tick. See _fetch_live_status.
    "live_status_cache": {},
    "series_cache": {"fetched_at": 0.0, "series": []},  # see _get_top_series
    "series_track_record": {},
    "signal_feed": [],   # most recent first
    "decision_feed": [],
    "stats": {"signals_seen": 0, "trades_placed": 0, "skipped": 0},
    "equity_history": [],  # [{"t": unix_ts, "equity": float}, ...], capped, for the Portfolio view's chart
    "real_balance_history": [],  # same shape, for the real-account toggle — only grows if a real account is connected
    "exchange_status": None,  # {"exchange_active": bool, "trading_active": bool, ...} — see _fetch_exchange_status
    "last_poll": None,
    "error": None,
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


def _bump_generation():
    state["generation"] += 1


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
    "occurrence_datetime", "status", "yes_ask_dollars",
)


def _slim_market(m: dict) -> dict:
    return {k: m.get(k) for k in _MARKET_FIELDS}


_SERIES_CACHE_TTL_SEC = 3600  # series (a recurring-event template - "Pro Basketball Game") don't
# change often enough to justify get_series_list's ~1s cost (12,500+ entries) every 15s poll tick


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
    return cache["series"]


async def _get_top_series(client: KalshiClient, top_n: int = 40) -> list[str]:
    series = await _get_series_cache(client)
    return [s["ticker"] for s in series[:top_n]]


# Series scanned per tick to build services/market_catalog.py's near-term
# catalog - similar order of magnitude to what discovery's own top-series
# fetch already does per tick (_get_top_series' default top_n), a bounded,
# deliberate increase in per-tick API calls, not unbounded.
_CATALOG_SCAN_BATCH_SIZE = 40


async def _scan_catalog_batch(client: KalshiClient, cfg: dict):
    """Incrementally builds market_catalog's near-term market catalog, a
    bounded batch (least-recently-scanned series first, see market_catalog.
    next_series_to_scan) per tick - see market_catalog.py's own module
    docstring for the full "why": volume-ranking the top 40 series
    systematically misses markets that are live right now but sit in a
    lower-volume series, confirmed directly against real Kalshi data (found
    ~0 of the real live markets the user could see on Kalshi's own site).
    Only runs when kalshi.live_markets_only is on - zero extra API cost for
    anyone who hasn't opted into that feature, same "free for everyone
    else" precedent as every other opt-in feature in this app."""
    if not cfg["kalshi"].get("live_markets_only"):
        return
    all_series = await _get_series_cache(client)
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
    - same reasoning, same ~1MB regression risk, as _series_meta_map above."""
    tickers = {m["ticker"] for m in state["markets"] if m.get("ticker")}
    tickers |= set(broker.positions.keys())
    tickers |= {t.ticker for t in broker.trade_log[-25:]}
    tickers |= {s["ticker"] for s in state["signal_feed"] if s.get("ticker")}
    for d in state["decision_feed"]:
        t = d.get("ticker") or (d.get("signal") or {}).get("ticker")
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


async def _fetch_markets(client: KalshiClient, cfg: dict, extra_tickers: list[str] | None = None) -> list[dict]:
    watchlist = cfg["kalshi"]["markets_watchlist"]
    if watchlist:
        # One ticker at a time, sequentially, meant 8 round trips paid back-to-back —
        # they don't depend on each other, so fetch them concurrently instead.
        results = await asyncio.gather(
            *(client.get_market(ticker) for ticker in watchlist), return_exceptions=True
        )
        markets = [m for m in results if isinstance(m, dict)]
    else:
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
            # one direct per-ticker fetch (no status filter, whatever its
            # real current state is) for just what's still missing, same
            # "always the real current price, never a placeholder" goal,
            # cheap since this is normally a small residual set.
            still_missing = [t for t in selected_tickers if t not in hydrated_by_ticker]
            if still_missing:
                fallback_results = await asyncio.gather(
                    *(client.get_market(t) for t in still_missing), return_exceptions=True,
                )
                for hm in fallback_results:
                    if isinstance(hm, dict) and hm.get("ticker"):
                        hydrated_by_ticker[hm["ticker"]] = hm
            # Still falls back to the original catalog row (schedule/title
            # info, just no live price) rather than dropping a ticker
            # outright if even the per-ticker fetch failed (a real API
            # error) - same "degrade honestly, never silently drop" pattern
            # as the rest of this app.
            markets = [hydrated_by_ticker.get(m["ticker"], m) for m in markets]
        else:
            top_series = await _get_top_series(client)
            markets = await client.get_top_volume_markets(
                cfg["kalshi"]["watchlist_size"], min_volume=min_volume, series_tickers=top_series,
                max_children_per_parent=cfg["kalshi"].get("max_children_per_parent"),
            )

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
        results = await asyncio.gather(*(client.get_market(t) for t in missing), return_exceptions=True)
        markets.extend(m for m in results if isinstance(m, dict))
    return markets


_TRADE_TAPE_TOTAL_CAP = 100  # scales with kalshi.watchlist_size - this is also
# services/whalewatchers/kalshi_trade_tape.py's entire input now, not just the
# UI panel's; too tight a cap here silently shrinks real whale-detection
# coverage back down even if the watchlist itself is wide.


async def _fetch_trade_tape(client: KalshiClient, markets: list[dict]) -> list[dict]:
    """Full-exchange trade tape (ROADMAP.md Phase 0.5), scoped to the current
    watchlist rather than the whole exchange - get_trades with no ticker
    filter returns trades across every Kalshi market, most of which aren't
    on anyone's watchlist here and would just be noise next to the
    whale-signal concept this ties into. One small get_trades() per watched
    market, concurrently (same pattern _fetch_markets already uses for its
    explicit-watchlist branch), merged and sorted newest-first."""
    tickers = [m["ticker"] for m in markets if m.get("ticker")]
    if not tickers:
        return []
    results = await asyncio.gather(
        *(client.get_trades(ticker=t, limit=10) for t in tickers), return_exceptions=True
    )
    trades = []
    for result in results:
        if isinstance(result, dict):
            trades.extend(result.get("trades") or [])
    trades.sort(key=lambda t: t.get("created_time") or "", reverse=True)
    return trades[:_TRADE_TAPE_TOTAL_CAP]


_LIVE_STATUS_LOOKBACK_SEC = 6 * 3600  # keep tracking an event up to 6h after its scheduled start
_LIVE_STATUS_LOOKAHEAD_SEC = 3600  # start tracking an event up to 1h before its scheduled start
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

    milestone_results = await asyncio.gather(
        *(client.get_milestones_for_event(et) for et in to_poll), return_exceptions=True
    )
    live_data_tasks, task_events = [], []
    has_milestone = set()
    for et, ms_result in zip(to_poll, milestone_results):
        if isinstance(ms_result, list) and ms_result:
            ms = ms_result[0]
            if ms.get("id") and ms.get("type"):
                has_milestone.add(et)
                live_data_tasks.append(client.get_live_data(ms["type"], ms["id"]))
                task_events.append(et)

    confirmed = {}
    if live_data_tasks:
        live_results = await asyncio.gather(*live_data_tasks, return_exceptions=True)
        for et, ld_result in zip(task_events, live_results):
            if isinstance(ld_result, dict):
                details = (ld_result.get("live_data") or {}).get("details") or {}
                status = details.get("widget_status")
                if status:
                    confirmed[et] = status

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


async def _fetch_account_snapshot(cfg: dict) -> dict:
    account.trading_enabled = cfg["kalshi_account"]["trading_enabled"]
    if not account.enabled:
        return {
            "connected": False, "balance": None, "positions": None, "fills": None,
            "error": account.status["error"], "trading_enabled": account.trading_enabled,
        }
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
        return {
            "connected": True, "balance": balance, "positions": positions, "fills": fills,
            "error": None, "trading_enabled": account.trading_enabled,
        }
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
    to_fetch = [
        m["event_ticker"] for m in markets
        if m.get("event_ticker") and (
            m["event_ticker"] not in state["event_titles"]
            or state["event_titles"][m["event_ticker"]].get("mutually_exclusive") is None
        )
    ]
    to_fetch = list(dict.fromkeys(to_fetch))  # de-dupe, preserve order
    if not to_fetch:
        return {}
    results = await asyncio.gather(*(client.get_event(et) for et in to_fetch), return_exceptions=True)
    fetched = {}
    for et, result in zip(to_fetch, results):
        if isinstance(result, dict):
            event = result.get("event") or {}
            fetched[et] = {
                "title": event.get("title") or et,
                "sub_title": event.get("sub_title"),
                "category": event.get("category"),
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
            }
    return fetched


async def _check_signal_resolutions(client: KalshiClient):
    """Pick a small batch of old-enough unresolved logged signals and see if
    their markets have settled yet. Fetched concurrently (bumped from 3 to
    10 per tick to keep pace with a larger watchlist generating more
    signals) rather than one-at-a-time, so a bigger batch doesn't stack up
    sequential round-trip latency within a single poll tick."""
    items = signal_log.unresolved_batch(limit=10, older_than_sec=600)
    if not items:
        return
    results = await asyncio.gather(
        *(client.get_market(item["ticker"]) for item in items), return_exceptions=True
    )
    for item, market in zip(items, results):
        if not isinstance(market, dict):
            continue  # market may be gone/renamed — leave unresolved, retry next time
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
        recommendations = advisory_engine.generate_recommendations(
            all_rows, cfg, current_fp, variants, adv_cfg["min_resolved_trades_per_variant"],
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


async def trading_loop():
    while True:
        cfg = config_store.get()
        if not state["running"]:
            await asyncio.sleep(1)
            continue
        client = None
        try:
            # Config-variant fingerprint (docs/advisory-engine-plan.md) -
            # computed once per tick, same cfg snapshot every trade decision
            # below is made against. record_variant is a cheap idempotent
            # upsert, safe to call every tick even when nothing changed.
            config_fp = config_performance.fingerprint(cfg)
            config_performance.record_variant(config_fp, cfg)

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
            markets, account_snapshot, exchange_status, _, _ = await asyncio.gather(
                _fetch_markets(client, cfg, extra_tickers=open_position_tickers), _fetch_account_snapshot(cfg),
                _fetch_exchange_status(client), _check_signal_resolutions(client),
                _scan_catalog_batch(client, cfg),
            )
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
            market_results = {m["ticker"]: m.get("result") for m in markets if m.get("ticker")}
            state["markets"] = [_slim_market(m) for m in markets]
            # Cheap, pure-DB check (no new API calls - see the docstring on
            # resolve_from_market_results) - runs every tick regardless of
            # market_analyst.enabled, so analyses made while it was on still
            # get graded after it's turned back off.
            market_analyst_agent.resolve_from_market_results(market_results)

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

            # MarketNativeStrategy (services/market_strategy.py) - runs every
            # tick alongside the whale-follow strategy below, entirely off
            # its own real-market-data heuristic. No-op (returns []) when
            # market_strategy.enabled is false, same disabled-by-default
            # precedent as the rest of this app's opt-in automation.
            # Intentionally NOT appended to state["decision_feed"] - that
            # feed is the whale-follow strategy's own record; blending the
            # two would defeat the point of each strategy's performance
            # being cleanly, independently measurable (see the plan doc).
            market_strategy.evaluate_all(markets, tick_now, cfg, market_results)
            markets_by_ticker = {m["ticker"]: m for m in markets if m.get("ticker")}
            market_strategy.check_exits(markets_by_ticker, tick_now, cfg, market_results)
            # All three depend on this tick's markets list but not on each
            # other - fetch concurrently rather than one after the other.
            event_titles, trade_tape, live_status = await asyncio.gather(
                _fetch_event_titles(client, markets), _fetch_trade_tape(client, markets),
                _fetch_live_status(client, markets),
            )
            state["event_titles"].update(event_titles)
            title_cache.save_event_titles(event_titles)  # event_titles here is already just this tick's new entries, see _fetch_event_titles
            state["trade_tape"] = trade_tape
            state["live_status"] = live_status  # replaced wholesale, not accumulated - a stale "live" would be wrong, not just incomplete
            # yes_bid_dollars is Kalshi's real field (already a 0-1 probability) —
            # "yes_bid" (cents) doesn't exist on the live API and silently
            # defaulted every price to 0.5.
            state["latest_prices"] = {
                m["ticker"]: float(m.get("yes_bid_dollars") or 0.5) for m in markets if m.get("ticker")
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
            if real_balance_value is not None:
                try:
                    state["real_balance_history"].append({"t": state["last_poll"], "balance": float(real_balance_value) / 100.0})
                    state["real_balance_history"] = state["real_balance_history"][-200:]
                except (TypeError, ValueError):
                    pass

            new_signals = []
            if whale_provider.enabled:
                try:
                    new_signals = await whale_provider.fetch_signals(
                        market_context={"markets": markets, "trade_tape": trade_tape, "cfg": cfg},
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

            shadow_active = cfg.get("mode") in ("shadow", "live")
            if shadow_active:
                shadow_bankroll, shadow_bankroll_source = _shadow_reference_bankroll(account_snapshot, cfg)

            for signal in new_signals:
                state["signal_feed"].insert(0, signal.to_dict())
                state["signal_feed"] = state["signal_feed"][:50]
                state["stats"]["signals_seen"] += 1
                signal_log.log_signal(
                    signal.ticker, signal.side, signal.size, signal.confidence,
                    state["whale_source"], signal.timestamp, factors=signal.factors,
                )

                # Same live-status lookup the LIVE badge uses (state["live_status"],
                # keyed by event_ticker, see _fetch_live_status) - reused here so
                # "live markets only" (config: strategy.live_markets_only) means
                # the exact same thing the dashboard's LIVE badge already shows,
                # not a second, possibly-inconsistent definition of "live."
                market_info = state["market_titles"].get(signal.ticker) or {}
                event_ticker = market_info.get("event_ticker")
                is_live = state["live_status"].get(event_ticker) == "live" if event_ticker else False

                decision = strategy.evaluate(
                    signal, cfg, is_live=is_live, market_results=market_results, config_fingerprint=config_fp,
                    latest_prices=state["latest_prices"],
                )
                state["decision_feed"].insert(0, decision)
                state["decision_feed"] = state["decision_feed"][:50]
                state["stats"]["trades_placed" if decision["action"] == "trade" else "skipped"] += 1

                # Independent of the paper decision above - shadow mode asks
                # the same question against real-account-sized bankroll,
                # and only ever logs, never executes. See services/shadow_mode.py.
                if shadow_active:
                    shadow.evaluate(signal, cfg, shadow_bankroll, shadow_bankroll_source, is_live=is_live, market_results=market_results)

            # Active position management - runs every tick regardless of
            # whether any new signal came in this tick, since a position can
            # need closing (take-profit/stop-loss/sentiment-reversal) purely
            # because the market moved or whale flow shifted, not because a
            # fresh signal arrived. See FollowTheWhaleStrategy.check_exits.
            for close_decision in strategy.check_exits(state["latest_prices"], state["signal_feed"], cfg, market_results):
                state["decision_feed"].insert(0, close_decision)
                state["decision_feed"] = state["decision_feed"][:50]
                state["stats"]["trades_placed"] += 1

        except Exception as e:
            state["error"] = str(e)
        finally:
            # A fresh client every tick means base_url changes (rare, but
            # live-reloadable) take effect immediately - but the SDK client
            # wraps its own aiohttp session, so it needs closing after use
            # or sessions leak across a long-running process.
            if client is not None:
                await client.close()

        _bump_generation()
        await asyncio.sleep(cfg["kalshi"]["poll_interval_sec"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(trading_loop())
    yield
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
        "insights": trade_analytics.compute_insights(all_rows),
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
    variants = {v["fingerprint"]: v for v in config_performance.all_variants()}
    result = advisory_engine.generate_recommendations(
        all_rows, cfg, current_fp, variants, adv_cfg["min_resolved_trades_per_variant"],
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
    variants = {v["fingerprint"]: v for v in config_performance.all_variants()}
    result = advisory_engine.generate_recommendations(
        all_rows, cfg, current_fp, variants, adv_cfg["min_resolved_trades_per_variant"],
    )
    match = next((r for r in result["recommendations"] if r["id"] == body.id), None)
    if match is None:
        raise HTTPException(
            status_code=404,
            detail="recommendation not found - it may be stale (config or trade history changed since it was fetched)",
        )

    field = match["config_path"].removeprefix("strategy.")
    config_store.update({"strategy": {field: match["suggested_value"]}})
    new_fp = config_performance.fingerprint(config_store.get())
    config_performance.log_applied_change(
        config_path=match["config_path"], old_value=match["current_value"], new_value=match["suggested_value"],
        rationale=match["rationale"], trade_count=match["n"],
        fingerprint_before=current_fp, fingerprint_after=new_fp, auto_applied=False,
    )
    _bump_generation()
    return {"applied": match, "new_config": config_store.get()["strategy"]}


@app.get("/api/advisory/applied-changes")
async def get_advisory_applied_changes(limit: int = 50, offset: int = 0):
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    return {
        "changes": config_performance.recent_applied_changes(limit=limit, offset=offset),
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
    return confidence_calibration.generate_calibration_report(rows, cc_cfg["min_resolved_signals"])


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


@app.get("/api/market-strategy/state")
async def get_market_strategy_state():
    # Backend-only for now (docs/advisory-engine-plan.md §9-adjacent,
    # direct request 2026-08-08) - no dedicated dashboard panel yet, but a
    # real, inspectable endpoint so this data pipeline can't silently
    # drift unnoticed while nothing in the UI reads it. Reuses
    # trade_analytics as-is (strategy-agnostic - it only ever reads Trade
    # dicts) rather than reimplementing summary stats for a second broker.
    market_cfg = config_store.get()["market_strategy"]
    all_rows = trade_analytics.build_trade_history([t.to_dict() for t in market_broker.trade_log])
    return {
        "enabled": market_cfg["enabled"],
        "broker": market_broker.state(state["latest_prices"]),
        "summary": trade_analytics.compute_summary(all_rows),
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
    body = {
        "running": state["running"],
        "markets": state["markets"],
        "market_titles": scoped_market_titles,
        "event_titles": _scoped_event_titles(scoped_market_titles),
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
        "error": state["error"],
        "whale_source": state["whale_source"],
        "risk": {"halted": risk.halted, "halt_reason": risk.halt_reason},
        "broker": broker.state(state["latest_prices"]),
        "account": state["account"],
        "exchange_status": state["exchange_status"],
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
    new_cfg = config_store.update(body.patch)
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
    config_store.update({"kalshi_account": {"trading_enabled": True}})
    account.trading_enabled = True  # take effect immediately, not on the next poll tick
    _bump_generation()
    return {"trading_enabled": True}


@app.post("/api/trading/disable")
async def disable_trading():
    # Always allowed, no confirmation needed — turning real trading back off
    # is never the dangerous direction.
    config_store.update({"kalshi_account": {"trading_enabled": False}})
    account.trading_enabled = False
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


class ResetBody(BaseModel):
    # Each flag wipes an independently-persisted domain — see the Danger Zone
    # panel in the Config tab. Paper defaults on (matches the button's
    # original, sole behavior); shadow/signal_log default off since they're
    # long-run track records that normally survive a paper reset on purpose.
    paper: bool = True
    shadow: bool = False
    signal_log: bool = False
    market_analyst: bool = False
    # market_catalog/market_history had no wired reset path at all despite
    # being the two largest data/*.db files on disk (data-robustness audit
    # finding, 2026-08-10) - market_catalog.clear_all() already existed,
    # written for exactly this, just never called from here.
    market_catalog: bool = False
    market_history: bool = False


@app.post("/api/reset")
async def reset_broker(body: ResetBody = ResetBody()):
    cfg = config_store.get()
    cleared = []
    if body.paper:
        # In-place reset (not reassigning `broker`) so this also wipes the
        # persisted account in data/paper_broker.db — see PaperBroker.reset().
        broker.reset(cfg["risk"]["starting_bankroll"])
        risk.reset_day(cfg["risk"]["starting_bankroll"])
        state["signal_feed"] = []
        state["decision_feed"] = []
        state["stats"] = {"signals_seen": 0, "trades_placed": 0, "skipped": 0}
        state["equity_history"] = []
        cleared.append("paper")
    if body.shadow:
        shadow.clear(cfg["risk"]["starting_bankroll"])
        cleared.append("shadow")
    if body.signal_log:
        signal_log.clear_all()
        cleared.append("signal_log")
    if body.market_analyst:
        market_analyst_agent.clear_all()
        cleared.append("market_analyst")
    if body.market_catalog:
        market_catalog.clear_all()
        cleared.append("market_catalog")
    if body.market_history:
        market_history.clear_all()
        cleared.append("market_history")
    _bump_generation()
    return {"ok": True, "cleared": cleared}


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
