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


def two_proportion_z_score(n_a: int, pct_a: float, n_b: int, pct_b: float) -> float | None:
    """Two-sample pooled-proportion z-score for "is this win-rate gap
    between group A and group B real, or within sampling noise" - the
    textbook test behind exactly the comparisons this app's advisory
    engine already makes informally via margin_of_error_pts (this session's
    own direct request, 2026-08-15: "give me a statistical significance
    score in addition to the semantics" - a number to show alongside the
    existing plain-English rationale, not a replacement for it).
    Deliberately the simpler pooled two-proportion z-test, not a full
    contingency-table chi-square or Fisher's exact - same "good enough for
    comparing an observed win rate against another observed win rate"
    scope this module's own docstring already sets for the single-sample
    case above.

    Returns None (not NaN/inf) when the comparison is undefined: either
    sample empty, or the pooled proportion is exactly 0 or 1 (every trade
    on both sides won, or every trade on both sides lost - zero variance,
    a z-score can't be computed, and there is by construction no gap left
    to explain anyway)."""
    if n_a <= 0 or n_b <= 0:
        return None
    p_a, p_b = max(0.0, min(1.0, pct_a / 100.0)), max(0.0, min(1.0, pct_b / 100.0))
    p_pool = (p_a * n_a + p_b * n_b) / (n_a + n_b)
    if p_pool <= 0.0 or p_pool >= 1.0:
        return None
    se = math.sqrt(p_pool * (1 - p_pool) * (1.0 / n_a + 1.0 / n_b))
    if se == 0:
        return None
    return (p_a - p_b) / se


def one_sample_t_score(values: list[float], reference: float = 0.0) -> float | None:
    """One-sample t-statistic: is this sample's mean significantly
    different from `reference` (usually 0 - "is this average realized P&L
    actually different from break-even, or just noise")? The other half of
    this session's "give me a statistical significance score" request -
    two_proportion_z_score above covers win-RATE comparisons (two
    proportions); several advisory_engine.py recommendations instead
    compare a mean dollar figure (avg realized P&L, avg left-on-table,
    avg stop-loss overshoot) against zero, which is a one-sample-mean
    question, not a two-proportion one - using a z-test there would be the
    wrong tool for the data shape, not just a less precise one.

    Uses the sample standard deviation (Bessel-corrected, n-1 denominator)
    - standard for a t-test where the population variance isn't known,
    which it never is here. Returns None (not NaN/inf) when there are
    fewer than 2 values (no variance to estimate at all) or the sample has
    zero variance (every value identical - a real but degenerate case,
    e.g. every closed trade left exactly the same dollar amount on the
    table)."""
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    if variance == 0:
        return None
    se = math.sqrt(variance / n)
    return (mean - reference) / se
