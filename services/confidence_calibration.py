"""
Rule-based calibration for composite_confidence_breakdown's factor weights
(services/whale_simulator.py). Direct request (2026-08-08): "I'd also like
an agent to study whale prints and their actual outcomes so as to create a
more accurate formula." Same "rule-based, not ML" decision as
docs/advisory-engine-plan.md's §1 applied again here, for the same reasons -
and with even more force this time: when this was built, signal_log had 9
resolved real signals total, nowhere near enough to fit anything
trustworthy. Deterministic bucket analysis over services/signal_log.py's
per-signal factor breakdowns (factors_json), same "pull the finding directly
out of observed data, don't fit a model" idiom advisory_engine.py already
established for strategy.entry_threshold.

Gating (the actual point, matching advisory_engine's own pattern):
generate_calibration_report() enforces the minimum-resolved-signal floor
*inside this function*, not the route or UI - no caller can reach a report
built on too little data regardless of config.

Scope boundary, deliberate: this ships strictly read-only/report-only for
v1, unlike advisory_engine's manual-apply button. composite_confidence_
breakdown's weights aren't config-editable today - they're hardcoded
constants in services/whale_simulator.py - so there's no live value to PATCH
yet. A human reads the report and edits the weights in code if/when the
finding is worth acting on. Building an apply path is a real follow-up, not
done here, once a report has actually shipped and proven useful.
"""
from services import trade_analytics

_BUCKET_COUNT = 3
_FACTOR_NAMES = ("depth_factor", "unusualness_factor", "proximity_factor", "context_factor", "agreement_factor")
# How much a factor's high-bucket win rate must beat its low-bucket win rate
# to count as "this factor actually discriminates outcomes" - a smaller bar
# than advisory_engine's 15pt (trade-level comparisons carry more real-world
# confounds than a single scored factor does).
_MIN_DISCRIMINATION_GAP = 10
# No factor's suggested weight ever goes all the way to zero off one report -
# a factor showing no discrimination yet might just be under-sampled this
# round, not genuinely useless; never fully strip its future chance to prove
# otherwise as more data comes in.
_MIN_SUGGESTED_WEIGHT = 0.05

# The weights actually hardcoded in composite_confidence_breakdown today -
# kept here only as the "current" reference point a rationale string can
# compare against, not read by the scoring function itself.
CURRENT_WEIGHTS = {
    "depth_factor": 0.35, "unusualness_factor": 0.20, "proximity_factor": 0.15,
    "context_factor": 0.15, "agreement_factor": 0.15,
}


def _bucket_win_rates(rows: list[dict], factor_name: str) -> dict:
    """Splits resolved signals into low/mid/high thirds by this factor's
    logged value (index-based tertiles on the sorted rows, not a value
    comparison - avoids tie/duplicate-value edge cases entirely) and
    returns each third's win rate. Same confidence-bucket idiom advisory_
    engine._entry_threshold_recommendation already uses for strategy.
    entry_threshold, applied here per-factor instead of per-trade.

    Requires at least _BUCKET_COUNT *distinct* values, not just enough rows -
    a real bug caught by this module's own tests: a near-constant factor
    (proximity_factor is often exactly 0.0, agreement_factor often exactly
    0.5) sorts stably, so index-based tertiles on a tied value would just
    reflect whatever order the rows happened to arrive in - not anything
    the factor itself explains. Returning {} (gap_pts stays None, "not
    enough variance to say") is the honest outcome, not a fabricated split."""
    sorted_rows = sorted(rows, key=lambda r: r["factors"][factor_name])
    n = len(sorted_rows)
    distinct_values = {r["factors"][factor_name] for r in sorted_rows}
    if n < _BUCKET_COUNT or len(distinct_values) < _BUCKET_COUNT:
        return {}
    third = n // _BUCKET_COUNT
    buckets = {
        "low": sorted_rows[:third],
        "mid": sorted_rows[third:n - third],
        "high": sorted_rows[n - third:],
    }
    return {
        key: {"n": len(group), "win_rate": round(sum(1 for r in group if r["correct"]) / len(group) * 100, 1)}
        for key, group in buckets.items() if group
    }


def _factor_report(rows: list[dict], factor_name: str) -> dict:
    buckets = _bucket_win_rates(rows, factor_name)
    if "low" not in buckets or "high" not in buckets:
        return {"factor": factor_name, "buckets": buckets, "gap_pts": None, "discriminates": None}
    gap = round(buckets["high"]["win_rate"] - buckets["low"]["win_rate"], 1)
    return {
        "factor": factor_name,
        "buckets": buckets,
        "gap_pts": gap,
        "discriminates": gap >= _MIN_DISCRIMINATION_GAP,
    }


def _suggested_weights(per_factor: list[dict]) -> dict | None:
    """Concrete suggested weights, not just descriptive stats - matching
    advisory_engine's "emit an actual value, not just hedged prose" ethos.
    Deliberately simple and explainable, not a fitted model: each factor's
    suggested share is proportional to its own discrimination gap (bigger
    gap = more of the outcome it actually explains = more weight), floored
    so nothing goes to zero off one report, then renormalized to sum to 1.0.
    Returns None if no factor showed any real discrimination yet - "keep
    current weights" is itself a legitimate finding, not a report failure."""
    gaps = {f["factor"]: f["gap_pts"] for f in per_factor if f["gap_pts"] is not None}
    if not gaps or max(gaps.values()) <= 0:
        return None
    clamped = {k: max(v, 0.0) for k, v in gaps.items()}
    total = sum(clamped.values())
    if total <= 0:
        return None
    floored = {k: max(v / total, _MIN_SUGGESTED_WEIGHT) for k, v in clamped.items()}
    floor_total = sum(floored.values())
    return {k: round(v / floor_total, 2) for k, v in floored.items()}


def generate_calibration_report(rows: list[dict], min_resolved_signals: int) -> dict:
    """rows: services.signal_log.resolved_signals_with_factors()'s output -
    already scoped to real (not simulated) signals that carry a factor
    breakdown, nothing more to filter here. Gated entrypoint - see module
    docstring for why the threshold lives inside this function, not a
    caller."""
    resolved_count = len(rows)
    if resolved_count < min_resolved_signals:
        return {
            "report": None,
            "gated_reason": (
                f"{resolved_count}/{min_resolved_signals} resolved real signals with a factor "
                "breakdown - calibration activates once that's reached"
            ),
            "resolved_count": resolved_count,
        }

    per_factor = [_factor_report(rows, name) for name in _FACTOR_NAMES]
    overall_win_rate = round(sum(1 for r in rows if r["correct"]) / resolved_count * 100, 1)
    ranked = sorted(
        (f for f in per_factor if f["gap_pts"] is not None),
        key=lambda f: f["gap_pts"], reverse=True,
    )

    return {
        "report": {
            "resolved_count": resolved_count,
            "overall_win_rate": overall_win_rate,
            "confidence_label": trade_analytics.confidence_label(resolved_count),
            "current_weights": CURRENT_WEIGHTS,
            "per_factor": per_factor,
            "ranked_by_discrimination": [f["factor"] for f in ranked],
            "suggested_weights": _suggested_weights(per_factor),
        },
        "gated_reason": None,
        "resolved_count": resolved_count,
    }
