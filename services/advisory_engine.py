"""
Rule-based config-tuning advisory engine (see docs/advisory-engine-plan.md).
Direct request (2026-08-08): "I want a recommendation engine/advisory
system... keep it disabled until that data threshold has been reached."

Builds on services/trade_analytics.py (row shape, confidence_label,
compute_summary) and services/config_performance.py (per-trade config
fingerprint). Two upgrades over trade_analytics.compute_insights():

1. Scoped per config-variant instead of blended across every config the
   user has ever run - a trade placed under last week's entry_threshold
   shouldn't quietly influence today's recommendation for a different
   value.
2. Emits a concrete suggested value pulled directly out of the observed
   data (never a fitted/trained model - see the plan doc's "rule-based,
   not ML" decision), not just hedged prose.

Deterministic and explainable throughout. Still never writes config on its
own initiative - generate_recommendations() only ever returns data;
whatever calls it (a manual "Apply" click, or later an opt-in auto-apply
path) is responsible for actually calling config_store.update().

Safety gating (the actual point of "keep it disabled until enough data"):
generate_recommendations() enforces the per-variant minimum-resolved-trades
floor *inside this function*, not in the UI or the route layer - there is
no way to reach a real recommendation for an under-sampled variant through
any caller, regardless of the advisory.enabled flag (that flag only
controls whether callers show what this module says at all; this module
itself never hands back a real recommendation without enough data).
"""
import hashlib

from services import trade_analytics

# Cross-variant comparisons only recommend adopting a value for a field
# where the two variants actually differ - nothing to say about a field
# that's identical between them.
_COMPARABLE_MIN_WIN_RATE_GAP = 15  # pts - matches trade_analytics' own confidence-bucket threshold


def _rec_id(config_path: str, suggested_value, n: int) -> str:
    """Stable id for one recommendation, used by POST /api/advisory/
    recommendations/apply to re-identify a specific recommendation against
    a freshly recomputed list rather than trusting client-supplied
    current/suggested values directly - the route recomputes
    generate_recommendations() itself and only applies a value this module
    just derived, never whatever a request body claims a value should be."""
    raw = f"{config_path}|{suggested_value!r}|{n}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def variant_summaries(rows: list[dict]) -> dict[str, dict]:
    """Groups trade_analytics-shaped rows (each carrying config_fingerprint)
    by variant and runs the existing compute_summary() per group - reused,
    not reimplemented, so this can never silently drift from what the
    History tab's own aggregate numbers say."""
    by_fp: dict[str, list[dict]] = {}
    for r in rows:
        fp = r.get("config_fingerprint")
        if fp is None:
            continue  # trades placed before this feature existed - no variant to attribute them to
        by_fp.setdefault(fp, []).append(r)
    return {fp: trade_analytics.compute_summary(group) for fp, group in by_fp.items()}


def _entry_threshold_recommendation(rows: list[dict], current_value: float) -> dict | None:
    """Same confidence-bucket analysis as trade_analytics.compute_insights,
    but the suggested value is now the actual boundary between the worst-
    and best-performing bucket, not just prose about it."""
    buckets: dict[str, list[dict]] = {"low (<0.5)": [], "medium (0.5-0.75)": [], "high (>0.75)": []}
    bucket_floor = {"low (<0.5)": 0.0, "medium (0.5-0.75)": 0.5, "high (>0.75)": 0.75}
    for r in rows:
        c = r["entry_confidence"]
        if c is None:
            continue
        key = "low (<0.5)" if c < 0.5 else ("medium (0.5-0.75)" if c <= 0.75 else "high (>0.75)")
        buckets[key].append(r)
    populated = {k: v for k, v in buckets.items() if len(v) > 0}
    if len(populated) < 2:
        return None
    win_rates = {k: sum(1 for r in v if r["won"]) / len(v) * 100 for k, v in populated.items()}
    worst_key = min(win_rates, key=win_rates.get)
    best_key = max(win_rates, key=win_rates.get)
    if win_rates[best_key] - win_rates[worst_key] < _COMPARABLE_MIN_WIN_RATE_GAP:
        return None
    n = sum(len(v) for v in populated.values())
    suggested = max(current_value, bucket_floor[best_key])
    if suggested <= current_value:
        return None  # nothing to actually suggest - already at/above the well-performing bucket's floor
    suggested = round(suggested, 3)
    return {
        "id": _rec_id("strategy.entry_threshold", suggested, n),
        "config_path": "strategy.entry_threshold",
        "current_value": current_value,
        "suggested_value": suggested,
        "rationale": (
            f"Under this config, trades entered at {worst_key} confidence won "
            f"{win_rates[worst_key]:.0f}% of the time vs {win_rates[best_key]:.0f}% at {best_key} "
            f"(n={n}) - raising the floor to {suggested:.2f} would have filtered out the weaker bucket."
        ),
        "n": n,
        "confidence_label": trade_analytics.confidence_label(n),
    }


def _exit_pct_recommendation(rows: list[dict], close_type: str, config_path: str, current_value: float | None) -> dict | None:
    """take_profit_pct / stop_loss_pct: suggest the current value adjusted
    by what was actually observed on that variant's own closes of this
    type - average left_on_table for take-profit (banked early, "how much
    more was on the table"), average overshoot-past-limit for stop-loss."""
    if current_value is None:
        return None
    group = [r for r in rows if r["close_type"] == close_type]
    if len(group) < 3:
        return None
    n = len(group)
    if close_type == "take_profit":
        left = [r["left_on_table"] for r in group if r["left_on_table"]]
        if not left:
            return None
        avg_left_pct = (sum(left) / len(left)) / 100  # left_on_table is in cents/contract, pct is a fraction
        suggested = round(current_value + avg_left_pct, 3)
        rationale = (
            f"take_profit closes under this config averaged {sum(left) / len(left):.1f}c/contract left on "
            f"the table vs a full $1 win (n={n}) - raising take_profit_pct to {suggested:.2f} targets that gap."
        )
    else:  # stop_loss
        overshoots = [
            (-r["realized_pnl"] / r["cost_basis"] - current_value)
            for r in group if r["realized_pnl"] is not None and r["cost_basis"]
        ]
        overshoots = [o for o in overshoots if o > 0]
        if not overshoots:
            return None
        avg_overshoot = sum(overshoots) / len(overshoots)
        suggested = round(max(0.01, current_value - avg_overshoot), 3)
        rationale = (
            f"stop_loss closes under this config realized an average loss "
            f"{avg_overshoot:.0%} past the configured {current_value:.0%} limit (n={n}, likely from price "
            f"gaps between poll ticks) - tightening stop_loss_pct to {suggested:.2f} targets that gap."
        )
    full_path = f"strategy.{config_path}"
    return {
        "id": _rec_id(full_path, suggested, n),
        "config_path": full_path,
        "current_value": current_value,
        "suggested_value": suggested,
        "rationale": rationale,
        "n": n,
        "confidence_label": trade_analytics.confidence_label(n),
    }


def _within_variant_recommendations(rows: list[dict], strat_cfg: dict) -> list[dict]:
    out = []
    rec = _entry_threshold_recommendation(rows, strat_cfg["entry_threshold"])
    if rec:
        out.append(rec)
    rec = _exit_pct_recommendation(rows, "take_profit", "take_profit_pct", strat_cfg.get("take_profit_pct"))
    if rec:
        out.append(rec)
    rec = _exit_pct_recommendation(rows, "stop_loss", "stop_loss_pct", strat_cfg.get("stop_loss_pct"))
    if rec:
        out.append(rec)
    return out


def _cross_variant_recommendations(
    current_fp: str, summaries: dict[str, dict], variants: dict[str, dict], min_resolved: int,
) -> list[dict]:
    """Only compares variants that individually clear min_resolved - a
    comparison is never more confident than its weaker side, so its
    confidence_label uses the smaller of the two Ns."""
    out = []
    current_summary = summaries.get(current_fp)
    current_variant = variants.get(current_fp)
    if current_summary is None or current_variant is None:
        return out
    if current_summary["total_closed"] < min_resolved:
        return out
    current_wr = current_summary["win_rate_pct"]
    if current_wr is None:
        return out

    for fp, summary in summaries.items():
        if fp == current_fp or summary["total_closed"] < min_resolved:
            continue
        other_wr = summary["win_rate_pct"]
        if other_wr is None or other_wr - current_wr < _COMPARABLE_MIN_WIN_RATE_GAP:
            continue  # only surface variants that clearly outperformed the current one
        other_variant = variants.get(fp)
        if other_variant is None:
            continue
        n = min(current_summary["total_closed"], summary["total_closed"])
        diffs = {
            k: v for k, v in other_variant["config"].items()
            if current_variant["config"].get(k) != v
        }
        for field, other_value in diffs.items():
            full_path = f"strategy.{field}"
            out.append({
                "id": _rec_id(full_path, other_value, n),
                "config_path": full_path,
                "current_value": current_variant["config"].get(field),
                "suggested_value": other_value,
                "rationale": (
                    f"A previously-run config (variant {fp[:8]}) won {other_wr:.0f}% of {summary['total_closed']} "
                    f"resolved trades vs the current config's {current_wr:.0f}% of {current_summary['total_closed']} "
                    f"- that variant's strategy.{field} was {other_value!r} instead of the current "
                    f"{current_variant['config'].get(field)!r}."
                ),
                "n": n,
                "confidence_label": trade_analytics.confidence_label(n),
                "compared_fingerprint": fp,
            })
    return out


def generate_recommendations(
    rows: list[dict], cfg: dict, current_fp: str, variants: dict[str, dict], min_resolved_trades: int,
) -> dict:
    """The gated entrypoint. Returns {"recommendations": [...], "gated_reason": str|None}.
    gated_reason is set (and recommendations is always []) whenever the
    *current* variant hasn't cleared min_resolved_trades yet - this is the
    literal mechanism behind "disabled until the data threshold is
    reached," independent of whatever advisory.enabled says."""
    summaries = variant_summaries(rows)
    current_summary = summaries.get(current_fp)
    resolved_count = current_summary["total_closed"] if current_summary else 0

    if resolved_count < min_resolved_trades:
        return {
            "recommendations": [],
            "gated_reason": (
                f"current config has {resolved_count}/{min_resolved_trades} resolved trades - "
                "recommendations activate once that's reached"
            ),
            "resolved_count": resolved_count,
        }

    current_rows = [r for r in rows if r.get("config_fingerprint") == current_fp]
    recs = _within_variant_recommendations(current_rows, cfg["strategy"])
    recs += _cross_variant_recommendations(current_fp, summaries, variants, min_resolved_trades)
    return {"recommendations": recs, "gated_reason": None, "resolved_count": resolved_count}
