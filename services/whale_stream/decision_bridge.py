"""
The decision bridge - turns a whale signal (or a close/fill event) into a
recorded decision + dashboard broadcast. Extracted 2026-08-22 as part of
main.py's modularization pass into its own module (not folded into
whale_stream_handlers.py) since trading_loop itself - which stays in
main.py per project convention - calls these same four functions directly,
not just the stream callbacks; a shared, symmetric home avoids main.py
importing from the whale-stream module just for its own loop body.
"""
import asyncio

from services import signal_log, trade_category
from services.market_events import event_lifecycle
from services.app_state import shadow, state, strategy
from services.market_lookup import _category_by_ticker, _sport_for_event, _subcategory_by_ticker
from services.ws_manager import ws_manager


async def _broadcast_signal_decision(signal_payload: dict | None, decision_payload: dict) -> None:
    await ws_manager.broadcast({
        "type": "signal_decision",
        "signal": signal_payload,
        "decision": decision_payload,
    })


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
    # Structural/schedule-based mid-series signal (services/market_events/event_lifecycle.py,
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
        market_titles=state["market_titles"], event_titles=state["event_titles"], markets=state["markets"],
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
    asyncio.create_task(_broadcast_signal_decision(None, fill_decision))
    # "four-entry gate bypass" fix (2026-08-22, main.py's check_pending_fills
    # validate_fn) - a fill that failed re-validation at its fill-time price
    # never became a trade; there's no ["trade"] key to read here.
    if fill_decision["action"] != "trade":
        state["stats"]["skipped"] += 1
        return
    state["stats"]["trades_placed"] += 1
    ticker = fill_decision["trade"]["ticker"]
    category = _category_by_ticker().get(ticker)
    subcategory = _subcategory_by_ticker().get(ticker)
    trade_category.record_category(ticker, category, tick_now, subcategory=subcategory)
