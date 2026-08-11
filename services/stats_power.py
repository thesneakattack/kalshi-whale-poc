"""
Sample-size / statistical-power convention - Gap 10 of docs/config-tuning-
data-gaps-2026-08-10.md. Every ad hoc n-threshold already scattered through
this codebase (trade_analytics.confidence_label's 5/15,
advisory.min_resolved_trades_per_variant's 10, confidence_calibration.
min_resolved_signals's 50, series_evaluator.min_trades_observed's 1000)
is a reasonable-looking round number, not something derived from an actual
power calculation. This module doesn't replace any of them (each was
chosen for its own local reason, and rewriting five independent gates in
one pass would be a much bigger, riskier change than "add a documented
convention") - it's the real, textbook math behind "is n big enough to
trust this," available as one shared, tested utility instead of getting
re-derived ad hoc (this session's own "eyeballing sqrt(p(1-p)/n) by hand"
for the per-series win-rate comparisons is exactly the gap this closes).

Standard normal-approximation (Wald) confidence interval for a single
proportion: margin_of_error = z * sqrt(p(1-p)/n). Good enough for this
app's purposes (comparing a real observed win rate against a fixed floor)
without pulling in a stats library - deliberately not the more exact
Wilson/Clopper-Pearson intervals, which matter more at small n or extreme
p than anything this app's gates operate at.
"""
import math

# Two-sided z-multipliers for the confidence levels this app is ever likely
# to want - not a general lookup table, just the handful worth naming.
_Z_FOR_CONFIDENCE = {0.90: 1.645, 0.95: 1.96, 0.99: 2.576}


def margin_of_error_pts(n: int, observed_pct: float = 50.0, confidence_level: float = 0.95) -> float:
    """Half-width, in percentage points, of the confidence interval around
    observed_pct at this sample size - "the real win rate is probably
    within +/- this many points of what we observed." observed_pct
    defaults to 50 (maximum variance, so the most conservative/widest
    margin) when the caller doesn't have a specific rate in mind yet, e.g.
    when sizing n before any data exists. Returns inf for n <= 0 - no data
    means no basis for any margin, not a fabricated 0."""
    if n <= 0:
        return float("inf")
    p = max(0.0, min(1.0, observed_pct / 100.0))
    z = _Z_FOR_CONFIDENCE.get(round(confidence_level, 2), 1.96)
    return z * math.sqrt(p * (1 - p) / n) * 100


def min_n_for_margin(margin_pts: float, observed_pct: float = 50.0, confidence_level: float = 0.95) -> int:
    """Inverse of margin_of_error_pts - how large a sample is needed to pin
    a rate down to within +/- margin_pts at this confidence level. E.g.
    "how many resolved signals would we need to be confident a series'
    real win rate is within 5 points of what we're observing.\""""
    if margin_pts <= 0:
        return float("inf")
    p = max(0.0, min(1.0, observed_pct / 100.0))
    z = _Z_FOR_CONFIDENCE.get(round(confidence_level, 2), 1.96)
    n = (z ** 2) * p * (1 - p) / (margin_pts / 100.0) ** 2
    return math.ceil(n)
