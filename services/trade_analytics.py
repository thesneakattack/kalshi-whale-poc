"""
Turns the paper broker's flat trade log into the History tab: which config
knob closed each position, whether it won, what a full $1 win would have
paid versus what an early exit actually banked, and a handful of sample-
size-hedged heuristic hints about which knob might be worth adjusting.

No new persistence and no schema change - everything here is derived by
parsing the reason-string conventions services/paper_broker.py and
services/strategy_engine.py already write (see close_position()'s
"closed: {reason} (realized ...)" prefix and evaluate()'s
"whale print ... (conf ...)" entry reason), the same "distinction lives in
the string, not a new column" idiom used to add close trades to the
existing trades table in the first place.

The per-field "which config knob might be worth adjusting" heuristics used
to live here too (compute_insights()), separate from services/
advisory_engine.py's own concrete-suggestion machinery - two engines
answering the same underlying question in two different shapes, one of them
(this module's) unable to ever suggest a real value or be applied. Merged
into advisory_engine.generate_recommendations() (2026-08-10, direct
report: hints here named config fields with nothing on the Config page to
match them against) - see that module for the unified suggestion pool.
What's left here is exit_management_split(): a real observation
(held-to-resolution vs. actively-managed win rate) that doesn't map onto
any single tunable field, so it never fit the suggestion shape either
engine uses - kept as its own descriptive function rather than forced into
one.
"""
import re

from services import stats_power

_CLOSE_TYPE_PATTERNS = [
    ("settled_win", re.compile(r"^closed: market settled \w+ - position won")),
    ("settled_loss", re.compile(r"^closed: market settled \w+ - position lost")),
    ("take_profit", re.compile(r"^closed: take-profit hit")),
    ("stop_loss", re.compile(r"^closed: stop-loss hit")),
    ("sentiment_reversal", re.compile(r"^closed: whale sentiment reversed")),
    ("auto_exit", re.compile(r"^closed: auto-exit")),
    # ROADMAP #1's runway gate (strategy_engine.check_exits'
    # exit_min_seconds_to_close, shipped this session) - real gap, direct
    # report ("certain trades being closed by 'unknown'"): this pattern
    # never existed, so every runway-exhausted close fell through
    # classify_close_type to None and rendered as "unknown" in the UI.
    ("runway_exhausted", re.compile(r"^closed: runway exhausted")),
    # services/exits/position_netting.py's own close reason - a second real gap
    # found while auditing every close_position() call site for this same
    # bug shape after the runway_exhausted one above turned out to be real.
    ("position_netting", re.compile(r"^closed: position netting")),
    # market_strategy.py's whale-independent analog to sentiment_reversal -
    # same "close if the signal this position was entered on has flipped
    # against the held side" idea, using real price momentum instead of
    # whale prints.
    ("momentum_reversal", re.compile(r"^closed: momentum reversed")),
]

_ENTRY_CONF_RE = re.compile(r"\(conf ([\d.]+)\)")
_REALIZED_RE = re.compile(r"\(realized ([+-][\d.]+)\)")

# Close types that represent a deliberate profit-taking exit ahead of
# settlement - the only ones "left on the table" is a meaningful, honest
# number for (see build_trade_history).
_EARLY_PROFIT_TYPES = ("take_profit", "auto_exit", "sentiment_reversal", "momentum_reversal",
                       "runway_exhausted")


def classify_close_type(reason: str) -> str | None:
    for name, pattern in _CLOSE_TYPE_PATTERNS:
        if pattern.match(reason):
            return name
    return None


def build_trade_history(trade_log: list[dict]) -> list[dict]:
    """trade_log must be chronological ascending (oldest first) - the same
    order PaperBroker.trade_log/the trades table already stores it in.
    Returns one row per CLOSE trade (a still-open position has no close yet
    and isn't included here - the Positions panel already covers those),
    each enriched with its paired entry trade.

    Pairing rule: the most recent non-close trade seen for that ticker
    before this close. Positions are one-per-ticker with no overlap
    (paper_broker.py's `positions` dict is keyed by ticker, and a new
    open_position() can't happen until the prior one is fully closed), so
    this is unambiguous even across a ticker being opened and closed
    multiple times over the app's life."""
    last_entry: dict[str, dict] = {}
    rows = []
    for t in trade_log:
        # excluded (2026-08-17): a CLOSE trade flagged by
        # PaperBroker.correct_erroneous_close after a confirmed-fabricated
        # exit_price - real incident, a stop-loss fired on a price
        # market_history's own independent data said was wrong, corrupting
        # win rate/P&L for every consumer of this function. Skipped
        # entirely rather than zeroed, so it neither counts as a loss nor
        # as a phantom win - it simply never happened, as far as any stat
        # built from this function is concerned. Never set on an entry row
        # (correct_erroneous_close only ever touches `closed:` rows), so
        # this can't orphan a later close's pairing.
        if t.get("excluded"):
            continue
        if not t["reason"].startswith("closed:"):
            last_entry[t["ticker"]] = t
            continue

        entry = last_entry.get(t["ticker"])
        close_type = classify_close_type(t["reason"])
        realized_match = _REALIZED_RE.search(t["reason"])
        realized_pnl = float(realized_match.group(1)) if realized_match else None

        entry_confidence = None
        if entry is not None:
            conf_match = _ENTRY_CONF_RE.search(entry["reason"])
            entry_confidence = float(conf_match.group(1)) if conf_match else None

        # Kalshi pays exactly $1/contract on a full win regardless of side -
        # this is an explicit hypothetical ("if this had gone on to fully
        # resolve your way"), not a claim about what would actually have
        # happened had the position stayed open; only computed for exit
        # types that were a deliberate early profit-take, and only when the
        # exit was in fact profitable (a stop-loss or a losing reversal has
        # nothing meaningful to compare against a "full win").
        left_on_table = None
        if close_type in _EARLY_PROFIT_TYPES and (realized_pnl or 0) > 0:
            left_on_table = round(
                t["size"] * (1 - t["price"]) if t["side"] == "yes" else t["size"] * t["price"], 2,
            )

        # Actual dollar amounts, not just prices - same side-aware convention
        # as PaperBroker.cost_basis/open_position's unit_cost (a "no" position's
        # real per-contract cost is 1-price, not price). cost_basis is what was
        # actually paid at entry; cash_back is what actually came back at close.
        cost_basis = None
        if entry is not None:
            cost_basis = round(
                entry["size"] * (entry["price"] if entry["side"] == "yes" else (1 - entry["price"])), 2,
            )
        cash_back = round(t["size"] * (t["price"] if t["side"] == "yes" else (1 - t["price"])), 2)

        # Real Kalshi taker fees (services/kalshi_fees.py) on both legs -
        # kept as its own explicit field rather than silently folded into
        # cash_back/cost_basis, matching this app's own convention of
        # showing the mechanism, not just the fee-adjusted result (the
        # reason string's "(realized ...)" figure IS fee-inclusive - see
        # PaperBroker.close_position - this is that same fee cost broken
        # back out for display). 0.0 for trades logged before fee modeling
        # existed, not None, since "no fee data" and "zero fee" look
        # identical for those old rows and there's no honest way to tell
        # them apart in a display context.
        fees_paid = round((entry.get("fee") or 0.0 if entry else 0.0) + (t.get("fee") or 0.0), 2)

        rows.append({
            "ticker": t["ticker"],
            "side": t["side"],
            "size": t["size"],
            "entry_price": entry["price"] if entry else None,
            "exit_price": t["price"],
            "entry_timestamp": entry["timestamp"] if entry else None,
            "exit_timestamp": t["timestamp"],
            "hold_sec": (t["timestamp"] - entry["timestamp"]) if entry else None,
            # How long between the whale print being seen (Trade.signal_seen_at,
            # the originating WhaleSignal's own .timestamp) and the resulting
            # position actually opening - 2026-08-16 direct report, previously
            # only answerable by hand-joining signal_log/paper_broker/
            # config_performance across separate DB files. None for entries
            # opened before this field existed, or for a close row (there is
            # no "time to close" signal - see exit_reason instead).
            "time_to_open_sec": (
                (entry["timestamp"] - entry["signal_seen_at"])
                if entry and entry.get("signal_seen_at") is not None else None
            ),
            "close_type": close_type,
            "realized_pnl": realized_pnl,
            "won": bool(realized_pnl is not None and realized_pnl > 0),
            "entry_confidence": entry_confidence,
            "entry_reason": entry["reason"] if entry else None,
            "exit_reason": t["reason"],
            "left_on_table": left_on_table,
            "cost_basis": cost_basis,
            "cash_back": cash_back,
            "fees_paid": fees_paid,
            # Which strategy config was active when this position was opened
            # (see services/config_performance.py) - sourced from the entry
            # trade specifically, though the close trade carries the same
            # value by construction (PaperBroker.close_position inherits it
            # from the position being closed).
            "config_fingerprint": entry.get("config_fingerprint") if entry else None,
        })
    return rows


def compute_summary(rows: list[dict]) -> dict:
    """Pure descriptive aggregate stats - overall and broken down by what
    closed each position. No hedging/confidence needed here, unlike
    compute_insights below, since this doesn't imply any config change."""
    n = len(rows)
    wins = sum(1 for r in rows if r["won"])
    pnls = [r["realized_pnl"] for r in rows if r["realized_pnl"] is not None]
    hold_secs = [r["hold_sec"] for r in rows if r["hold_sec"] is not None]
    left_on_table_total = sum(r["left_on_table"] for r in rows if r["left_on_table"])
    cost_basis_total = sum(r["cost_basis"] for r in rows if r["cost_basis"])
    fees_paid_total = sum(r.get("fees_paid") or 0.0 for r in rows)

    by_type = {}
    for r in rows:
        key = r["close_type"] or "unknown"
        by_type.setdefault(key, []).append(r)
    close_type_breakdown = {}
    for key, group in by_type.items():
        group_pnls = [r["realized_pnl"] for r in group if r["realized_pnl"] is not None]
        close_type_breakdown[key] = {
            "count": len(group),
            "wins": sum(1 for r in group if r["won"]),
            "total_pnl": round(sum(group_pnls), 2) if group_pnls else None,
            "avg_pnl": round(sum(group_pnls) / len(group_pnls), 2) if group_pnls else None,
        }

    win_rate_pct = round(wins / n * 100, 1) if n else None
    total_realized_pnl = round(sum(pnls), 2) if pnls else 0.0

    return {
        "total_closed": n,
        "wins": wins,
        "losses": n - wins,
        "win_rate_pct": win_rate_pct,
        "total_realized_pnl": total_realized_pnl,
        # Direct request (2026-08-16, regime-segmentation follow-up: "not
        # just winrate either, but whatever else would be statistically
        # useful") - win rate alone can't distinguish a real edge from the
        # "high win rate, thin edge" trap CLAUDE.md already documents once
        # (unit_cost 0.8-1.0: 78-94% win rates that still net negative,
        # because a win pays a few cents while a loss costs nearly a
        # dollar). avg_realized_pnl surfaces the actual per-trade dollar
        # outcome alongside win rate so that trap is visible at the
        # segment level too, not just in one hard-coded historical finding.
        "avg_realized_pnl": round(total_realized_pnl / n, 2) if n else None,
        # Sample-size honesty, made numeric rather than left to
        # confidence_label's coarse low/moderate/higher bucketing alone -
        # stats_power.margin_of_error_pts is the same real math
        # services/advisory_engine.py already gates recommendations on,
        # now exposed for direct display ("the real win rate is probably
        # within +/- this many points of what we observed").
        "win_rate_margin_pts": (
            round(stats_power.margin_of_error_pts(n, observed_pct=win_rate_pct), 1)
            if n and win_rate_pct is not None else None
        ),
        "confidence_label": confidence_label(n),
        "avg_hold_sec": round(sum(hold_secs) / len(hold_secs), 0) if hold_secs else None,
        "total_left_on_table": round(left_on_table_total, 2),
        "total_capital_deployed": round(cost_basis_total, 2),
        "total_fees_paid": round(fees_paid_total, 2),
        "by_close_type": close_type_breakdown,
    }


def confidence_label(n: int) -> str:
    """Sample-size-only hedge, not a statistical test - just says how much
    weight a human should put on the insight before acting on it. The 5/15
    cutoffs are a reasonable-looking round-number convention, not derived
    from a power calculation (Gap 10, docs/config-tuning-data-gaps-2026-
    08-10.md) - for reference, services/stats_power.py's real margin-of-
    error math puts n=5 at roughly +/-44 points and n=15 at roughly +/-25
    points around a 50% observed rate (95% CI) - both genuinely wide, which
    is exactly why "low"/"moderate" undersell rather than oversell what
    this size of sample can support."""
    if n < 5:
        return "low"
    if n < 15:
        return "moderate"
    return "higher"


def exit_management_split(rows: list[dict]) -> dict | None:
    """Held-to-resolution vs actively-managed (take-profit/stop-loss/
    sentiment- or momentum-reversal/auto-exit) win rate. Purely descriptive,
    like compute_summary - not folded into advisory_engine's unified
    suggestion pool because it doesn't map onto any single tunable
    config field the way every other migrated insight did. Returns None
    (not zeros) until both groups clear the same n>=3 hedge every other
    insight in this app uses, rather than showing a rate computed off 1-2
    trades."""
    closed = [r for r in rows if r["close_type"]]
    settled = [r for r in closed if r["close_type"] in ("settled_win", "settled_loss")]
    managed = [r for r in closed if r["close_type"] not in ("settled_win", "settled_loss")]
    if len(settled) < 3 or len(managed) < 3:
        return None
    return {
        "settled_win_rate_pct": round(sum(1 for r in settled if r["won"]) / len(settled) * 100, 1),
        "managed_win_rate_pct": round(sum(1 for r in managed if r["won"]) / len(managed) * 100, 1),
        "n_settled": len(settled),
        "n_managed": len(managed),
    }

    return insights
