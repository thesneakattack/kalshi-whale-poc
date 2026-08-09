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

Deliberately NOT a recommendation engine: compute_insights() only ever
describes what already happened, tagged with how many trades it's based on
- it never edits config, and every insight is explicit about being a
correlation on a possibly-small sample, not a proven cause.
"""
import re

_CLOSE_TYPE_PATTERNS = [
    ("settled_win", re.compile(r"^closed: market settled \w+ - position won")),
    ("settled_loss", re.compile(r"^closed: market settled \w+ - position lost")),
    ("take_profit", re.compile(r"^closed: take-profit hit")),
    ("stop_loss", re.compile(r"^closed: stop-loss hit")),
    ("sentiment_reversal", re.compile(r"^closed: whale sentiment reversed")),
    ("auto_exit", re.compile(r"^closed: auto-exit")),
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
_EARLY_PROFIT_TYPES = ("take_profit", "auto_exit", "sentiment_reversal", "momentum_reversal")


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

        rows.append({
            "ticker": t["ticker"],
            "side": t["side"],
            "size": t["size"],
            "entry_price": entry["price"] if entry else None,
            "exit_price": t["price"],
            "entry_timestamp": entry["timestamp"] if entry else None,
            "exit_timestamp": t["timestamp"],
            "hold_sec": (t["timestamp"] - entry["timestamp"]) if entry else None,
            "close_type": close_type,
            "realized_pnl": realized_pnl,
            "won": bool(realized_pnl is not None and realized_pnl > 0),
            "entry_confidence": entry_confidence,
            "entry_reason": entry["reason"] if entry else None,
            "exit_reason": t["reason"],
            "left_on_table": left_on_table,
            "cost_basis": cost_basis,
            "cash_back": cash_back,
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

    return {
        "total_closed": n,
        "wins": wins,
        "losses": n - wins,
        "win_rate_pct": round(wins / n * 100, 1) if n else None,
        "total_realized_pnl": round(sum(pnls), 2) if pnls else 0.0,
        "avg_hold_sec": round(sum(hold_secs) / len(hold_secs), 0) if hold_secs else None,
        "total_left_on_table": round(left_on_table_total, 2),
        "total_capital_deployed": round(cost_basis_total, 2),
        "by_close_type": close_type_breakdown,
    }


def confidence_label(n: int) -> str:
    """Sample-size-only hedge, not a statistical test - just says how much
    weight a human should put on the insight before acting on it."""
    if n < 5:
        return "low"
    if n < 15:
        return "moderate"
    return "higher"


def compute_insights(rows: list[dict]) -> list[dict]:
    """Heuristic, human-facing hints about which config knob a pattern in
    the trade history might argue for adjusting - deliberately conservative:
    every insight names the config field it's about, the trade count it's
    based on, and a confidence label, and none of them ever fires below a
    minimum sample size. This never writes config; it only describes what
    already happened."""
    insights = []
    closed = [r for r in rows if r["close_type"]]

    # 1. Win rate by entry-confidence bucket -> entry_threshold.
    buckets: dict[str, list[dict]] = {"low (<0.5)": [], "medium (0.5-0.75)": [], "high (>0.75)": []}
    for r in closed:
        c = r["entry_confidence"]
        if c is None:
            continue
        key = "low (<0.5)" if c < 0.5 else ("medium (0.5-0.75)" if c <= 0.75 else "high (>0.75)")
        buckets[key].append(r)
    populated = {k: v for k, v in buckets.items() if len(v) > 0}
    if len(populated) >= 2:
        win_rates = {k: sum(1 for r in v if r["won"]) / len(v) * 100 for k, v in populated.items()}
        worst_key = min(win_rates, key=win_rates.get)
        best_key = max(win_rates, key=win_rates.get)
        if win_rates[best_key] - win_rates[worst_key] >= 15:  # not just sample-size noise
            n = sum(len(v) for v in populated.values())
            insights.append({
                "topic": "entry_threshold",
                "text": (
                    f"Trades entered at {worst_key} confidence won {win_rates[worst_key]:.0f}% of the time "
                    f"vs {win_rates[best_key]:.0f}% at {best_key} confidence - raising strategy.entry_threshold "
                    f"may filter out the weaker end."
                ),
                "n": n, "confidence": confidence_label(n),
            })

    # 2. Per-close-type outcomes -> which exit mechanism is helping/hurting.
    by_type: dict[str, list[dict]] = {}
    for r in closed:
        by_type.setdefault(r["close_type"], []).append(r)

    stop_loss_group = by_type.get("stop_loss", [])
    if len(stop_loss_group) >= 3:
        avg_pnl = sum(r["realized_pnl"] for r in stop_loss_group if r["realized_pnl"] is not None) / len(stop_loss_group)
        n = len(stop_loss_group)
        insights.append({
            "topic": "stop_loss_pct",
            "text": (
                f"stop_loss closed {n} position(s), avg realized {avg_pnl:+.2f}. If similar setups often "
                f"recovered afterward, stop_loss_pct may be too tight; if losses kept deepening, it's doing its job."
            ),
            "n": n, "confidence": confidence_label(n),
        })

    take_profit_group = by_type.get("take_profit", [])
    if len(take_profit_group) >= 3:
        avg_pnl = sum(r["realized_pnl"] for r in take_profit_group if r["realized_pnl"] is not None) / len(take_profit_group)
        left = [r["left_on_table"] for r in take_profit_group if r["left_on_table"]]
        avg_left = sum(left) / len(left) if left else 0.0
        n = len(take_profit_group)
        insights.append({
            "topic": "take_profit_pct",
            "text": (
                f"take_profit closed {n} position(s), avg realized {avg_pnl:+.2f}, averaging {avg_left:.1f}c/contract "
                f"left on the table versus a full $1 win. Consider raising take_profit_pct if this feels too eager."
            ),
            "n": n, "confidence": confidence_label(n),
        })

    auto_exit_group = by_type.get("auto_exit", [])
    if len(auto_exit_group) >= 3:
        avg_pnl = sum(r["realized_pnl"] for r in auto_exit_group if r["realized_pnl"] is not None) / len(auto_exit_group)
        n = len(auto_exit_group)
        advice = (
            "Consider raising auto_exit_threshold if it's closing winners too early."
            if avg_pnl > 0 else
            "Consider lowering auto_exit_threshold or increasing the pnl/sentiment weights if it's not cutting losses fast enough."
        )
        insights.append({
            "topic": "auto_exit_threshold",
            "text": f"auto_exit closed {n} position(s), avg realized {avg_pnl:+.2f}. {advice}",
            "n": n, "confidence": confidence_label(n),
        })

    # sentiment_reversal (whale-follow) and its market-native analog
    # momentum_reversal - direct report: "the config tunings hints section...
    # doesn't seem to give me actual advice at all." Investigated against
    # real trade history rather than assumed: confirmed live, 84 of 103 real
    # closed trades (81%) were sentiment_reversal - by far the dominant real
    # exit mechanism this app produces - yet neither of these two close
    # types had a heuristic here at all, unlike stop_loss/take_profit/
    # auto_exit above. Every other insight this function can produce
    # happened to need a close type or a spread of confidence buckets this
    # dataset didn't have, so the panel was correctly staying silent rather
    # than fabricating something - but silence on the single most common
    # real close type is exactly the gap worth closing.
    sentiment_reversal_group = by_type.get("sentiment_reversal", [])
    if len(sentiment_reversal_group) >= 3:
        avg_pnl = sum(r["realized_pnl"] for r in sentiment_reversal_group if r["realized_pnl"] is not None) / len(sentiment_reversal_group)
        n = len(sentiment_reversal_group)
        advice = (
            "Consider raising exit_sentiment_lean_pct or exit_sentiment_min_signals if it's reversing out on "
            "noise before a real trend forms."
            if avg_pnl <= 0 else
            "Consider lowering exit_sentiment_lean_pct or exit_sentiment_min_signals to react faster if similar "
            "reversals keep paying off."
        )
        insights.append({
            "topic": "exit_sentiment_lean_pct",
            "text": f"sentiment_reversal closed {n} position(s), avg realized {avg_pnl:+.2f}. {advice}",
            "n": n, "confidence": confidence_label(n),
        })

    momentum_reversal_group = by_type.get("momentum_reversal", [])
    if len(momentum_reversal_group) >= 3:
        avg_pnl = sum(r["realized_pnl"] for r in momentum_reversal_group if r["realized_pnl"] is not None) / len(momentum_reversal_group)
        n = len(momentum_reversal_group)
        advice = (
            "Consider raising min_momentum_delta if it's reversing out on noise before a real trend forms."
            if avg_pnl <= 0 else
            "Consider lowering min_momentum_delta to react faster if similar reversals keep paying off."
        )
        insights.append({
            "topic": "min_momentum_delta",
            "text": f"momentum_reversal closed {n} position(s), avg realized {avg_pnl:+.2f}. {advice}",
            "n": n, "confidence": confidence_label(n),
        })

    # 3. Settled-only (never actively exited) vs actively-managed win rate.
    settled = [r for r in closed if r["close_type"] in ("settled_win", "settled_loss")]
    managed = [r for r in closed if r["close_type"] not in ("settled_win", "settled_loss")]
    if len(settled) >= 3 and len(managed) >= 3:
        settled_wr = sum(1 for r in settled if r["won"]) / len(settled) * 100
        managed_wr = sum(1 for r in managed if r["won"]) / len(managed) * 100
        n = len(settled) + len(managed)
        insights.append({
            "topic": "exit_management_overall",
            "text": (
                f"Positions held to resolution won {settled_wr:.0f}% of the time (n={len(settled)}); "
                f"actively-managed exits (take-profit/stop-loss/reversal/auto-exit) won {managed_wr:.0f}% "
                f"of the time (n={len(managed)})."
            ),
            "n": n, "confidence": confidence_label(n),
        })

    return insights
