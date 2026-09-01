"""
Rule-based calibration for composite_confidence_breakdown's factor weights
(services/confidence_scoring.py). Direct request (2026-08-08): "I'd also like
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

Scope boundary, UPDATED (2026-08-10, direct follow-up request: "i want the
option to enable auto whale-signal calibration... have them auto-enable
and start getting put into play with my whole system once there *is*
enough data"): this module's weight-suggestion half (_suggested_weights
below) was originally strictly read-only/report-only, on the reasoning
that blending a suggestion into the 8-factor formula (what to do with a
factor that has no data yet, whether to floor a negative-discrimination
factor to zero) was a real judgment call a human should make explicitly.
That default-safe judgment call still holds - blended_weights_for_auto_apply()
below applies the exact same blend a human already did by hand earlier
this same session (redistribute only the factors with real discrimination
data, leave data-less factors completely untouched, renormalize to sum to
1.0) - but it's now available as an opt-in automatic path
(confidence_calibration.auto_apply_enabled, default false, gated behind a
typed confirmation phrase same as every other consequential automation in
this app - see main.py's POST /api/confidence-calibration/auto-apply/
enable) rather than exclusively a human copying numbers by hand. Composite_
confidence_breakdown's weights are config-editable (config/settings.yaml's
whale_confidence_weights, threaded through services/whalewatchers/
kalshi_trade_tape.py) - this module still never writes that config itself,
main.py's trading loop does, the same way every other auto-apply path in
this app keeps the actual config_store.update() call at the call site, not
buried in a service module.
"""
from services import stats_power
from services.history import trade_analytics
from services.confidence_scoring import DEFAULT_WEIGHTS

_BUCKET_COUNT = 3
# Derived from DEFAULT_WEIGHTS' own keys, not a second hand-maintained list -
# real reuse gap found and fixed 2026-08-15 (Angle I code review, same
# session): a hardcoded copy here meant a new factor (block_trade_factor,
# added the same session) would silently never get bucket-analyzed/
# discrimination-scored unless someone remembered this second, unrelated
# list. tuple() preserves DEFAULT_WEIGHTS' own insertion order.
_FACTOR_NAMES = tuple(DEFAULT_WEIGHTS)
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


def _tied_run_size(sorted_vals: list[float], cut_idx: int) -> int:
    """Size of the contiguous run of equal, rounded values straddling the
    cut at cut_idx (the boundary between sorted_vals[cut_idx-1] and
    sorted_vals[cut_idx]). 0 if the two neighbors differ - no tie at this
    cut. Rounded comparison matches the existing 2026-08-14 float-jitter
    dedup fix a few lines up (distinct_values)."""
    n = len(sorted_vals)
    if cut_idx <= 0 or cut_idx >= n:
        return 0
    boundary_val = sorted_vals[cut_idx - 1]
    if sorted_vals[cut_idx] != boundary_val:
        return 0
    lo = cut_idx - 1
    while lo > 0 and sorted_vals[lo - 1] == boundary_val:
        lo -= 1
    hi = cut_idx
    while hi < n and sorted_vals[hi] == boundary_val:
        hi += 1
    return hi - lo


def _bucket_win_rates(rows: list[dict], factor_name: str) -> tuple[dict, str]:
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
    enough variance to say") is the honest outcome, not a fabricated split.

    Rows missing this factor entirely are excluded before bucketing, not
    treated as a KeyError - real finding (2026-08-10, consulting live
    data while setting sensible config defaults): cluster_factor/
    trend_factor/analyst_factor were all added to composite_confidence_
    breakdown after this app had already logged its first ~9000 real
    signals, so every one of those older rows' factors_json genuinely
    lacks those three keys. Without this filter, enabling confidence_
    calibration against real production history crashes this function
    outright the first time it's called - same "leave it out of the
    average entirely when absent" idiom the rest of this app already uses
    for an optional factor, applied here per-row instead of per-signal.

    Returns (buckets, data_status) - see design §3. data_status
    distinguishes a permanently sparse factor (analyst_factor/
    block_trade_factor-shaped: "insufficient_variance", benign forever)
    from a transient tie-contaminated cut ("contaminated", the condition
    this predicate exists to catch) - only the second should ever gate
    anything downstream (§9)."""
    applicable_rows = [r for r in rows if factor_name in r["factors"]]
    sorted_rows = sorted(applicable_rows, key=lambda r: r["factors"][factor_name])
    n = len(sorted_rows)
    # Rounded before dedup (2026-08-14 fix): exact float equality here would
    # let binary floating-point jitter around one real value (e.g. a
    # constant factor computed via slightly different arithmetic paths
    # across rows - 0.3 vs 0.30000000000000004) count as "real variance",
    # defeating the whole point of this near-constant-factor guard and
    # letting noise-level differences feed a spurious gap_pts/discriminates
    # verdict into auto-apply.
    rounded_vals = [round(r["factors"][factor_name], 6) for r in sorted_rows]
    distinct_values = set(rounded_vals)
    if n < _BUCKET_COUNT or len(distinct_values) < _BUCKET_COUNT:
        return {}, "insufficient_variance"

    third = n // _BUCKET_COUNT
    materiality_floor = max(30, 0.005 * n)
    for cut_idx in (third, n - third):
        if _tied_run_size(rounded_vals, cut_idx) >= materiality_floor:
            return {}, "contaminated"

    buckets = {
        "low": sorted_rows[:third],
        "mid": sorted_rows[third:n - third],
        "high": sorted_rows[n - third:],
    }
    return {
        key: {"n": len(group), "win_rate": round(sum(1 for r in group if r["correct"]) / len(group) * 100, 1)}
        for key, group in buckets.items() if group
    }, "ok"


def _factor_report(rows: list[dict], factor_name: str) -> dict:
    buckets, data_status = _bucket_win_rates(rows, factor_name)
    if "low" not in buckets or "high" not in buckets:
        return {"factor": factor_name, "buckets": buckets, "gap_pts": None,
                "discriminates": None, "data_status": data_status}
    gap = round(buckets["high"]["win_rate"] - buckets["low"]["win_rate"], 1)
    return {
        "factor": factor_name, "buckets": buckets, "gap_pts": gap,
        "discriminates": gap >= _MIN_DISCRIMINATION_GAP, "data_status": data_status,
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


def blended_weights_for_auto_apply(current_weights: dict, suggested_weights: dict | None) -> dict | None:
    """The auto-apply path (2026-08-10, direct request) - automates the
    exact blend a human did by hand earlier this same session, not a new
    algorithm: suggested_weights only ever covers factors WITH real
    discrimination data (see _suggested_weights above), renormalized among
    just that subset - naively overwriting whale_confidence_weights with
    it wholesale would silently zero out every factor with no data yet
    (cluster_factor/trend_factor/analyst_factor as of this writing). This
    keeps every factor missing from suggested_weights completely
    unchanged at its current value, then renormalizes the WHOLE set back
    to sum to 1.0 so the blend is a real, valid weight distribution, not
    just the untouched factors' old values plus a subset that no longer
    sums with them correctly.

    Returns None when there's nothing to apply - no discriminating factor
    yet (suggested_weights is None/empty) or a degenerate all-zero blend -
    "keep current weights" stays a legitimate outcome, not an error."""
    if not suggested_weights:
        return None
    blended = dict(current_weights)
    blended.update(suggested_weights)
    total = sum(blended.values())
    if total <= 0:
        return None
    return {k: round(v / total, 4) for k, v in blended.items()}


# Fixed-width bands, not tertiles - unlike _bucket_win_rates above (which
# splits by rank specifically to stay robust against a near-constant
# factor), the *overall* composite_confidence score is a continuous blend
# unlikely to be near-constant, and the question this answers is genuinely
# different: not "does a higher score correlate with winning more" (that's
# discrimination - per_factor above already covers it) but "does a
# 60-70%-confidence signal actually win ~60-70% of the time" - calibration.
# This is the concrete metric Kalshi itself argued was the right lens
# (docs/prediction-markets-research-reference.md Part 1.4, the Clinton &
# Huang vs. Kalshi dispute over hit-rate vs. calibration as the correct way
# to measure a prediction market's - or here, a signal's - real accuracy),
# and this app had no way to check it before this, only the flat overall
# win_rate services/signal_log.py's stats() already reports.
_CONFIDENCE_BANDS = [
    (0.0, 0.5, "<50%"),
    (0.5, 0.6, "50-60%"),
    (0.6, 0.7, "60-70%"),
    (0.7, 0.8, "70-80%"),
    (0.8, 0.9, "80-90%"),
    (0.9, 1.01, "90-100%"),  # 1.01 so a confidence of exactly 1.0 lands in this band
]
# Same minimum-sample-size idiom as trade_analytics.compute_insights - a
# band with only 1-2 signals in it isn't worth reporting as a finding.
_MIN_BAND_SIZE = 3


def _confidence_calibration_bands(rows: list[dict]) -> list[dict]:
    """Buckets resolved real signals by their final composite_confidence
    score into fixed confidence bands and compares each band's *predicted*
    probability (the band's own midpoint) against its *observed* win rate.
    Bands with too few signals are dropped rather than reported on a
    misleadingly small sample - same honesty-over-fabrication idiom
    _bucket_win_rates above already uses."""
    bands = []
    for lo, hi, label in _CONFIDENCE_BANDS:
        group = [r for r in rows if lo <= r["confidence"] < hi]
        if len(group) < _MIN_BAND_SIZE:
            continue
        observed = round(sum(1 for r in group if r["correct"]) / len(group) * 100, 1)
        predicted_mid = round((lo + min(hi, 1.0)) / 2 * 100, 1)
        bands.append({
            "band": label,
            "n": len(group),
            "predicted_pct": predicted_mid,
            "observed_win_rate_pct": observed,
            "gap_pts": round(observed - predicted_mid, 1),
        })
    return bands


def _series_win_rates(rows: list[dict]) -> list[dict]:
    """Categorical group-by-series breakdown (#60 - `series` is a real,
    indexed, stored column that signal_log.resolved_signals_with_factors()
    never selected, so this module never saw it). Plain group-by, not
    _bucket_win_rates' numeric tertile split - `series` is a category
    label, not a sortable factor value, and it does not live inside
    factors_json so it is not one of _FACTOR_NAMES either. Drops any
    series under _MIN_BAND_SIZE, reusing the same floor
    _confidence_calibration_bands already uses rather than adding a second
    "enough data" constant to this file."""
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        buckets.setdefault(r["series"], []).append(r)
    out = [
        {
            "series": series, "n": len(group),
            "win_rate_pct": round(sum(1 for r in group if r["correct"]) / len(group) * 100, 1),
        }
        for series, group in buckets.items() if len(group) >= _MIN_BAND_SIZE
    ]
    out.sort(key=lambda b: -b["n"])
    return out


def generate_calibration_report(rows: list[dict], min_resolved_signals: int, current_weights: dict | None = None) -> dict:
    """rows: services.signal_log.resolved_signals_with_factors()'s output -
    already scoped to real (not simulated) signals that carry a factor
    breakdown, nothing more to filter here. Gated entrypoint - see module
    docstring for why the threshold lives inside this function, not a
    caller.

    current_weights: the caller's live config["whale_confidence_weights"],
    so the report's own "current_weights" field reflects whatever's
    actually scoring real signals right now, not a stale hardcoded mirror -
    defaults to confidence_scoring.DEFAULT_WEIGHTS (this formula's original
    weights) when the caller has no config override to pass, same fallback
    composite_confidence_breakdown itself uses."""
    current_weights = {**DEFAULT_WEIGHTS, **(current_weights or {})}
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
            # 2026-08-15, real gap closed: the auto-apply gate
            # (auto_apply_min_resolved_signals) was a bare sample-size
            # floor with no confidence-interval framing, despite
            # services/stats_power.py's real margin-of-error math already
            # existing and already being used for exactly this question
            # elsewhere (main.py's series-evaluator win-rate cross-check).
            # Same formula, reused here rather than re-derived.
            "overall_win_rate_margin_pts": round(
                stats_power.margin_of_error_pts(resolved_count, overall_win_rate), 1,
            ),
            "confidence_label": trade_analytics.confidence_label(resolved_count),
            "current_weights": current_weights,
            "per_factor": per_factor,
            "ranked_by_discrimination": [f["factor"] for f in ranked],
            "suggested_weights": _suggested_weights(per_factor),
            "confidence_calibration": _confidence_calibration_bands(rows),
            "by_series": _series_win_rates(rows),
        },
        "gated_reason": None,
        "resolved_count": resolved_count,
    }
