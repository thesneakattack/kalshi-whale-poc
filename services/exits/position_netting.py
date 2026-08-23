"""
Position netting / hedge-mode active management. Direct correction
(2026-08-15, after the earlier "hedge mode" report): the ME-complement
entry gate (services/mutual_exclusivity.py, shipped earlier the same
session) only blocks a *new* entry once one side of a confirmed pair is
already open - it does nothing for positions already open, doesn't cover
partial/lopsided hedges, and doesn't cover N-way concentration (a real live
example found this session: 51 separate NO positions across one PGA
tournament event, 43.4% of the whale-follow bankroll). This module treats
already-open positions on one confirmed mutually-exclusive event as one
book, not N independent bets - the standard options-portfolio tool for
"I hold several related positions, what's my real risk": compute the
payout profile (total P&L under every possible resolution outcome), not a
heuristic "both near 50c" pattern match.

Grounded directly in this session's own research
(docs/prediction-markets-research-reference.md):
- Sec 1.3: Kalshi's CLOB does NOT mechanically enforce sum-to-100% across a
  mutually-exclusive event's sibling markets - only arbitraged (a 2026
  arXiv preprint documents real violations). Two independent whale signals
  landing on opposite sides of one real event is this strategy
  unknowingly taking BOTH sides of exactly that kind of mispriced book,
  instead of being the arbitrageur that would normally close the gap.
- Sec 2.4: the real, fill-verified fee curve is parabolic, maximal exactly
  at 50c. A "both sides near 50c" position isn't just low-edge, it sits in
  the single most fee-expensive zone on the entire pricing curve - a
  second, independent drag on top of any sum-to-101%-style mispricing.
- kalshi_fees.taker_fee returns exactly 0.0 at price 0 or 1 (its own guard
  clause) - settlement itself is fee-free. This is why a "locked" position
  (profit or loss fixed under every outcome) is provably best left to
  settle rather than unwound early: unwinding can only add fee drag for a
  payout that can no longer change.

Only acts on events Kalshi itself confirms mutually_exclusive == True (not
services/mutual_exclusivity.py's price-sum fallback, which is a reasonable
basis for that module's cheap entry-side block but too noisy a basis for
an automated close/trim action here).
"""
import time

from services import kalshi_fees, market_history

_OUTSIDE = "__outside__"


def find_groups(
    positions: dict, market_titles: dict, event_titles: dict,
    now: float | None = None, min_dwell_sec: float = 0,
) -> list[dict]:
    """positions: PaperBroker.positions (ticker -> Position). Groups
    currently-open positions by event_ticker (same market_titles lookup
    services/mutual_exclusivity.py already uses), keeping only groups where
    Kalshi's own event.mutually_exclusive flag is confirmed True and at
    least 2 members are currently open.

    min_dwell_sec excludes any member opened too recently - mirrors
    check_exits' own opened_since idiom (a position's quote/price hasn't
    caught up to a brand-new entry yet) so a same-tick double-entry (which
    the ME-gate should mostly prevent anyway) isn't force-evaluated on a
    stale first read.

    include_outside is decided ONCE here, from the group's ORIGINAL
    membership, not re-derived later from whatever subset a candidate
    action is evaluating: exactly 2 currently-open siblings on a confirmed
    mutually-exclusive event is a genuine complete head-to-head (no third
    real-world outcome exists - same assumption find_me_pairs() already
    makes), so "someone else wins" is truly impossible and stays impossible
    even for a 1-member subset of it. 3+ members means this app is only
    holding a SUBSET of a larger N-way field (the PGA case: 51-of-100+
    golfers) - "someone else wins" is real for the group and for every
    subset of it, so it's carried through unchanged rather than vanishing
    once a trim candidate leaves only 2 members."""
    now = now if now is not None else time.time()
    by_event: dict[str, list[tuple[str, object]]] = {}
    for ticker, pos in positions.items():
        if now - pos.opened_at < min_dwell_sec:
            continue
        info = market_titles.get(ticker) or {}
        et = info.get("event_ticker")
        if not et:
            continue
        by_event.setdefault(et, []).append((ticker, pos))

    groups = []
    for et, members in by_event.items():
        if len(members) < 2:
            continue
        if (event_titles.get(et) or {}).get("mutually_exclusive") is not True:
            continue
        groups.append({
            "event_ticker": et,
            "members": members,
            "include_outside": len(members) != 2,
        })
    return groups


def payout_profile(members: list[tuple[str, object]], include_outside: bool) -> dict[str, float]:
    """Total group P&L under every possible resolution outcome - one entry
    per member ticker winning, plus (when include_outside) one synthetic
    "__outside__" bucket for any real-world outcome the group doesn't
    cover. cost_basis/entry_fee mirror PaperBroker.cost_basis/
    open_position's own unit_cost math exactly (never re-derived
    differently) - this module's own single source of truth stays that
    broker, not a second formula."""
    total_cost = sum(
        pos.size * (pos.entry_price if pos.side == "yes" else (1 - pos.entry_price))
        for _, pos in members
    )
    total_fee = sum(pos.entry_fee for _, pos in members)

    def _group_payout(winner: str | None) -> float:
        payout = 0.0
        for ticker, pos in members:
            wins = (ticker == winner) == (pos.side == "yes")
            if wins:
                payout += pos.size
        return payout - total_cost - total_fee

    profile = {ticker: _group_payout(ticker) for ticker, _ in members}
    if include_outside:
        profile[_OUTSIDE] = _group_payout(None)
    return profile


def classify(profile: dict[str, float]) -> str:
    """locked_profit: positive under every outcome - provably optimal to
    hold to settlement (see module docstring on fee-free settlement).
    locked_loss: negative under every outcome - the loss magnitude is
    already fixed regardless of timing. variable: still genuinely
    outcome-dependent - the real case, handled by _best_variable_action."""
    values = list(profile.values())
    if not values:
        return "variable"
    if min(values) > 0:
        return "locked_profit"
    if max(values) < 0:
        return "locked_loss"
    return "variable"


def _scenario_probabilities(members: list[tuple[str, object]], latest_prices: dict, profile: dict) -> dict[str, float]:
    """Each scenario's live-market-implied likelihood, read straight off
    each member's own current price (latest_prices - same source
    check_exits already uses) - "ticker T wins" has probability
    current_price(T) regardless of which side this app holds on it. The
    market's own real-time read, not a fitted model - same "trust the
    market's live read" precedent as market_analyst_agent.analyst_lean().
    Deliberately NOT
    renormalized to sum to exactly 1: per this module's own docstring,
    Kalshi's CLOB doesn't guarantee that in the first place, and forcing
    it to sum to 1 would erase exactly the mispricing signal this module
    exists to use - all candidate actions are compared under the same
    unnormalized weights, so the comparison stays valid either way."""
    probs = {}
    yes_prob_sum = 0.0
    for ticker, pos in members:
        price = latest_prices.get(ticker, pos.entry_price)
        probs[ticker] = price
        yes_prob_sum += price
    if _OUTSIDE in profile:
        probs[_OUTSIDE] = max(0.0, 1 - yes_prob_sum)
    return probs


def expected_value(profile: dict, members: list[tuple[str, object]], latest_prices: dict) -> float:
    probs = _scenario_probabilities(members, latest_prices, profile)
    return sum(probs.get(scenario, 0.0) * pnl for scenario, pnl in profile.items())


def _unwind_now_value(members: list[tuple[str, object]], latest_prices: dict) -> float:
    """Certain P&L if every position in `members` were closed right now at
    live prices - proceeds minus cost basis minus entry fee minus the real
    exit fee each leg would incur (services/kalshi_fees.py, the same
    formula PaperBroker.close_position itself uses). Unlike
    expected_value(), this needs no probability weighting - closing locks
    in the outcome immediately, nothing left to be uncertain about."""
    total = 0.0
    for ticker, pos in members:
        price = latest_prices.get(ticker, pos.entry_price)
        proceeds = pos.size * price if pos.side == "yes" else pos.size * (1 - price)
        exit_fee = kalshi_fees.taker_fee(pos.size, price, ticker=ticker)
        cost_basis = pos.size * (pos.entry_price if pos.side == "yes" else (1 - pos.entry_price))
        total += proceeds - exit_fee - cost_basis - pos.entry_fee
    return total


def _best_variable_action(
    profile: dict, members: list[tuple[str, object]], latest_prices: dict, include_outside: bool,
) -> tuple[str | None, list[str], float | None]:
    """Evaluates trim_worst_leg and close_all against the hold baseline for
    a still-genuinely-outcome-dependent group. Returns (action, [tickers to
    close now], expected-value improvement over holding), or
    (None, [], None) if nothing beats holding. Deliberately NOT an
    open-ended search: whether to "reverse" (trim toward the better-priced
    leg) or something closer to "double down" is decided by which move
    the computed expected value the most, not a heuristic guess - see this
    module's own top-of-file docstring. Adding capital to strengthen a
    leg (the genuine "double down" case) is deliberately not a candidate
    here - see docs/comprehensive-development-plan-2026-08-15.md-style
    reasoning: this is the highest-risk direction (automatically
    *increasing* exposure) and ships once trim/close_all are validated
    against real data, matching this app's established "safe direction
    first" pattern (auto_exit_enabled before kelly_fraction_of_cap)."""
    ev_hold = expected_value(profile, members, latest_prices)

    best_trim_ticker, best_trim_improvement = None, float("-inf")
    for ticker, pos in members:
        remaining = [(t, p) for t, p in members if t != ticker]
        remaining_profile = payout_profile(remaining, include_outside) if remaining else {}
        remaining_ev = expected_value(remaining_profile, remaining, latest_prices) if remaining else 0.0
        leg_value = _unwind_now_value([(ticker, pos)], latest_prices)
        improvement = (remaining_ev + leg_value) - ev_hold
        if improvement > best_trim_improvement:
            best_trim_ticker, best_trim_improvement = ticker, improvement

    close_all_improvement = _unwind_now_value(members, latest_prices) - ev_hold

    candidates = [
        ("trim_worst_leg", [best_trim_ticker] if best_trim_ticker else [], best_trim_improvement),
        ("close_all", [t for t, _ in members], close_all_improvement),
    ]
    action, tickers, improvement = max(candidates, key=lambda c: c[2])
    if not tickers or improvement <= 0:
        return None, [], None
    return action, tickers, improvement


def _materiality_bar(
    members: list[tuple[str, object]], min_edge_usd: float, normal_vol: float | None,
    vol_lookback: float, now: float,
) -> float:
    """The noise filter (direct request: "it needs to filter out noise...
    account for volatility"): small, noisy expected-value differences
    don't trigger churn. Scaled by the group's own current volatility
    using the exact same pattern exit_engine._exit_confidence already
    established (market_history.volatility() vs. a configured "normal"
    baseline, vol_ratio clamped to [0.25, 4.0]) rather than inventing a
    second volatility-normalization idiom - a noisier read on the group's
    own tickers means the live prices behind expected_value() are less
    trustworthy, so a bigger edge is required before acting. The group's
    MOST volatile member sets the bar (conservative - one noisy leg is
    enough to make the whole group's live read less trustworthy)."""
    if not normal_vol:
        return min_edge_usd
    vols = [market_history.volatility(ticker, vol_lookback, as_of=now) for ticker, _ in members]
    vols = [v for v in vols if v is not None]
    if not vols:
        return min_edge_usd
    vol_ratio = max(0.25, min(4.0, max(vols) / normal_vol))
    return min_edge_usd * vol_ratio


def describe_groups(
    broker, market_titles: dict, event_titles: dict, latest_prices: dict, cfg: dict, now: float | None = None,
) -> list[dict]:
    """Read-only view of every currently-open netting-eligible group, its
    payout profile, classification, and recommended action - the entire
    implementation behind GET /api/position-netting/groups. Same math as
    review() below but never closes anything, so it's safe to call anytime
    regardless of position_netting.enabled - lets the user see what WOULD
    happen before ever turning the automated action on (same "observe,
    then choose to act" principle as this app's History tab)."""
    net_cfg = cfg.get("position_netting") or {}
    now = now if now is not None else time.time()
    min_dwell = net_cfg.get("min_dwell_sec", 300)
    min_edge = net_cfg.get("min_edge_improvement_usd", 1.0)
    normal_vol = net_cfg.get("normal_volatility", 0.02)
    vol_lookback = net_cfg.get("volatility_lookback_sec", 1800)

    out = []
    for group in find_groups(broker.positions, market_titles, event_titles, now=now, min_dwell_sec=min_dwell):
        members = group["members"]
        include_outside = group["include_outside"]
        profile = payout_profile(members, include_outside)
        status = classify(profile)
        entry = {
            "event_ticker": group["event_ticker"],
            "members": [
                {
                    "ticker": t, "side": p.side, "size": p.size, "entry_price": p.entry_price,
                    "current_price": latest_prices.get(t, p.entry_price), "opened_at": p.opened_at,
                }
                for t, p in members
            ],
            "payout_profile": profile,
            "status": status,
        }
        if status == "locked_profit":
            entry["recommendation"] = {
                "action": "hold",
                "reason": "payout is positive under every possible outcome - settlement is fee-free, closing early can only add cost",
            }
        elif status == "locked_loss":
            entry["recommendation"] = {
                "action": "close_all",
                "tickers": [t for t, _ in members],
                "reason": "payout is negative under every possible outcome - the loss is already fixed regardless of timing; closing now frees up bankroll/position headroom instead of leaving it dead until settlement",
            }
        else:
            action, tickers, improvement = _best_variable_action(profile, members, latest_prices, include_outside)
            bar = _materiality_bar(members, min_edge, normal_vol, vol_lookback, now)
            if action and improvement is not None and improvement >= bar:
                entry["recommendation"] = {
                    "action": action,
                    "tickers": tickers,
                    "expected_value_improvement_usd": round(improvement, 2),
                    "materiality_bar_usd": round(bar, 2),
                    "reason": f"estimated ${improvement:.2f} expected-value improvement over holding (bar ${bar:.2f})",
                }
            else:
                entry["recommendation"] = {
                    "action": "hold",
                    "materiality_bar_usd": round(bar, 2),
                    "reason": "still outcome-dependent, but no candidate action clears the materiality bar",
                }
        out.append(entry)
    return out


def review(
    broker, market_titles: dict, event_titles: dict, latest_prices: dict, cfg: dict, now: float | None = None,
) -> list[dict]:
    """Runs once per tick, after strategy.check_exits - group-level netting
    analysis on whatever survived per-position exit rules first (clean
    layering: simple per-position rules run first, this more sophisticated
    group-level layer only evaluates what's left). Entirely opt-in
    (position_netting.enabled, default False) - real automated
    position-closing action needs the same "ships fully built, off by
    default" treatment as auto_exit_enabled/kelly_fraction_of_cap
    elsewhere in this app."""
    net_cfg = cfg.get("position_netting") or {}
    if not net_cfg.get("enabled", False):
        return []
    now = now if now is not None else time.time()
    decisions = []
    for group in describe_groups(broker, market_titles, event_titles, latest_prices, cfg, now=now):
        rec = group["recommendation"]
        if rec["action"] == "hold":
            continue
        for ticker in rec["tickers"]:
            pos = broker.positions.get(ticker)
            if pos is None:
                continue
            price = latest_prices.get(ticker, pos.entry_price)
            reason = f"position netting ({group['status']}, event {group['event_ticker']}): {rec['reason']}"
            trade = broker.close_position(ticker, price, reason)
            if trade is None:
                continue
            decisions.append({
                "action": "close", "ticker": ticker, "trade": trade.to_dict(), "reason": reason,
                "source": "position_netting",
            })
    return decisions
