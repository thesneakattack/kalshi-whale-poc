"""
Read-side helpers that build /api/state's (and the position/history route
handlers') view of the shared state - scoping it down to just what's
relevant right now, and enriching PaperBroker's raw state() with fields it
doesn't itself compute. Extracted 2026-08-21 as part of main.py's
modularization pass: these were previously defined directly in main.py and
read by nearly every future module (position, history, and the /api/state
builder itself), so they get their own shared home rather than being owned
by any one of those.
"""
from services import market_history, signal_log
from services.history import trade_analytics
from services.position.account_positions import _real_account_position_tickers
from services.app_state import broker, state
from services.paper_broker import PaperBroker


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
    # The market's own real settled result (services/market_history.py's
    # outcomes table, Kalshi's market.result field), independent of
    # close_type/won above - those describe THIS position's own exit
    # (win/lose at whatever price it closed at), not what the market went
    # on to actually resolve as. For an early-exit close_type (stop_loss,
    # take_profit, sentiment_reversal, ...) the market kept trading after
    # the position closed and can resolve either way; this is what makes an
    # early exit checkable in hindsight against the ticker's real outcome.
    # None for a still-open position or one that hasn't settled yet.
    outcomes = market_history.outcomes_for_tickers([t["ticker"] for t in recent])
    for t in recent:
        t["market_result"] = outcomes.get(t["ticker"])
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
    instead of its resolved title, back when this app still had a second
    Market-Native strategy - removed 2026-08-22): this only ever included
    the whale-follow `broker`'s own positions/trade_log, the exact same gap
    already found and fixed once for `broker` itself (see this docstring's
    own history above)."""
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
    # Canonical key only (A14): REST fills carry `ticker` natively and WS
    # fills get it from services/kalshi/contracts/fill.py at the gateway -
    # no presentation-layer alias fallback needed.
    tickers |= {
        f.get("ticker")
        for f in ((account.get("fills") or {}).get("fills") or [])
        if f.get("ticker")
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
