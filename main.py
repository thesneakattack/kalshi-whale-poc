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
from services import config_performance
from services import market_history
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
    "event_titles": title_cache.load_event_titles(),  # event_ticker -> {"title", "sub_title", "category"}, see _fetch_event_titles
    "trade_tape": [],  # real trades across the current watchlist, newest first, see _fetch_trade_tape
    "live_status": {},  # event_ticker -> "live" | "finished" | "none" | None, see _fetch_live_status
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


async def _get_top_series(client: KalshiClient, top_n: int = 30) -> list[str]:
    series = await _get_series_cache(client)
    return [s["ticker"] for s in series[:top_n]]


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


def _relevant_tickers() -> set[str]:
    """Every ticker actually shown on this tick's /api/state response -
    current watchlist, open positions, and whatever's still in the capped
    signal/decision feeds. state["market_titles"]/state["event_titles"]
    themselves accumulate unbounded for the app's whole lifetime now (see
    services/title_cache.py) so history/clusters can still resolve an old
    ticker's title on their own separately-scoped requests, but /api/state
    itself must stay scoped to this same small set - same reasoning, same
    ~1MB regression risk, as _series_meta_map above."""
    tickers = {m["ticker"] for m in state["markets"] if m.get("ticker")}
    tickers |= set(broker.positions.keys())
    tickers |= {s["ticker"] for s in state["signal_feed"] if s.get("ticker")}
    for d in state["decision_feed"]:
        t = d.get("ticker") or (d.get("signal") or {}).get("ticker")
        if t:
            tickers.add(t)
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
        top_series = await _get_top_series(client)
        markets = await client.get_top_volume_markets(
            cfg["kalshi"]["watchlist_size"], min_volume=cfg["kalshi"].get("min_volume_24h", 0), series_tickers=top_series,
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
    return trades[:50]


_LIVE_STATUS_WINDOW_SEC = 6 * 3600  # started up to 6h ago, or starting within the next hour


async def _fetch_live_status(client: KalshiClient, markets: list[dict]) -> dict:
    """The real live/scheduled/finished status per event, via Kalshi's
    actual milestone/live-data system - confirmed directly against a real
    AFL match at its actual start time (status "scheduled"->"inprogress"->
    "closed", widget_status "none"->"live"->"finished"), not inferred from
    timestamps alone. Only checked for markets whose occurrence_datetime is
    within a plausible live window - most watchlist markets aren't starting
    imminently, and this is two extra API calls per event (milestone
    lookup, then live-data lookup), not something to run unconditionally
    for every market on every poll tick."""
    now = time.time()
    candidates = []
    for m in markets:
        occ, et = m.get("occurrence_datetime"), m.get("event_ticker")
        if not occ or not et:
            continue
        try:
            occ_ts = datetime.fromisoformat(occ.replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        if -_LIVE_STATUS_WINDOW_SEC <= (now - occ_ts) <= 3600:
            candidates.append(et)
    candidates = list(dict.fromkeys(candidates))  # de-dupe, preserve order
    if not candidates:
        return {}

    milestone_results = await asyncio.gather(
        *(client.get_milestones_for_event(et) for et in candidates), return_exceptions=True
    )
    live_data_tasks, task_events = [], []
    for et, result in zip(candidates, milestone_results):
        if isinstance(result, list) and result:
            ms = result[0]
            if ms.get("id") and ms.get("type"):
                live_data_tasks.append(client.get_live_data(ms["type"], ms["id"]))
                task_events.append(et)
    if not live_data_tasks:
        return {}

    live_results = await asyncio.gather(*live_data_tasks, return_exceptions=True)
    status = {}
    for et, result in zip(task_events, live_results):
        if isinstance(result, dict):
            details = (result.get("live_data") or {}).get("details") or {}
            status[et] = details.get("widget_status")
    return status


_POSITION_FIELDS = ("ticker", "position_fp", "market_exposure_dollars", "realized_pnl_dollars")
_FILL_FIELDS = ("ticker", "market_ticker", "side", "action", "count_fp", "yes_price_dollars", "no_price_dollars")
# Same trim as _slim_market: keep only what renderRealPositions/renderRealFills
# actually read (field names confirmed against a real connected account, not
# guessed — see the comment above renderRealPositions for why that mattered).
# event_positions/cursor/fill_id/order_id/... are real fields, just not
# currently rendered anywhere. Cut fills from ~13.4KB to well under 2KB for a
# 25-fill page, the single largest piece of /api/state's payload.


def _slim_position(p: dict) -> dict:
    return {k: p.get(k) for k in _POSITION_FIELDS}


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
        positions = {"market_positions": [_slim_position(p) for p in (positions.get("market_positions") or [])]}
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
    for every event until caught."""
    to_fetch = [
        m["event_ticker"] for m in markets
        if m.get("event_ticker") and m["event_ticker"] not in state["event_titles"]
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
            open_position_tickers = list(set(broker.positions.keys()) | set(market_broker.positions.keys()))
            markets, account_snapshot, exchange_status, _ = await asyncio.gather(
                _fetch_markets(client, cfg, extra_tickers=open_position_tickers), _fetch_account_snapshot(cfg),
                _fetch_exchange_status(client), _check_signal_resolutions(client),
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
            new_market_titles = {
                m["ticker"]: {
                    "title": m.get("title") or m.get("yes_sub_title") or m["ticker"],
                    "yes_sub_title": m.get("yes_sub_title"),
                    "no_sub_title": m.get("no_sub_title"),
                    "event_ticker": m.get("event_ticker"),
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
                    state["whale_source"], signal.timestamp,
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


@app.get("/api/markets/search")
async def search_markets(q: str = "", min_volume: float = 0, category: str = "", limit: int = 50):
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
        markets = await client.get_top_volume_markets(limit, min_volume=min_volume, series_tickers=candidate_tickers)
        results = markets
        # Opportunistically cache titles/events for whatever this search
        # touched, same shape _fetch_markets already populates - so a result
        # added to the watchlist afterward already has a label, no gap.
        searched_titles = {
            m["ticker"]: {
                "title": m.get("title") or m.get("yes_sub_title") or m["ticker"],
                "yes_sub_title": m.get("yes_sub_title"),
                "no_sub_title": m.get("no_sub_title"),
                "event_ticker": m.get("event_ticker"),
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
