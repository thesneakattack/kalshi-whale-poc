"""
Rule-based config-tuning advisory engine (see docs/advisory-engine-plan.md).
Direct request (2026-08-08): "I want a recommendation engine/advisory
system... keep it disabled until that data threshold has been reached."

Builds on services/trade_analytics.py (row shape, confidence_label,
compute_summary) and services/config_performance.py (per-trade config
fingerprint). Emits a concrete suggested value pulled directly out of the
observed data (never a fitted/trained model - see the plan doc's
"rule-based, not ML" decision), not just hedged prose.

Unified with what used to be a separate, purely-descriptive "Config Tuning
Hints" panel (trade_analytics.compute_insights(), removed 2026-08-10) -
direct report: that panel named config fields the Config tab's own labels
didn't visually match, and separately, this engine's per-field suggestions
were scoped to trades placed under the *exact* current config
(config_fingerprint == current_fp), so changing any one strategy field
invalidated every prior trade's eligibility for suggesting a value for any
*other* field. Per-field suggestions below (_within_variant_recommendations - the naming
is legacy, kept for continuity with _cross_variant_recommendations, it no
longer filters by variant) now read the FULL trade history, matching
compute_insights()'s old broad scope, while gaining this module's concrete-
suggestion/apply shape it never had. Only _cross_variant_recommendations
still needs real per-variant scoping - "did a fully different past config
perform better as a whole" is a different question that can't mean
anything without it.

Fed by services/paper_broker.py's trade log (strategy.* fields -
entry_threshold, longshot bonus, exit_* fields). Used to also take a
second `market_rows` list for the now-removed Market-Native strategy's own
broker (2026-08-22 removal) - every suggestion function here reasons about
the one remaining broker only.

Deterministic and explainable throughout. Still never writes config on its
own initiative - generate_recommendations() only ever returns data;
whatever calls it (a manual "Apply" click, or later an opt-in auto-apply
path) is responsible for actually calling config_store.update().

Safety gating: per-field suggestions are hedged the same way
compute_insights() always was - each one has its own minimum sample size
(n>=3 for a close-type group, 2+ populated confidence/price buckets for the
entry-threshold/longshot checks) baked into the function itself, not a
blanket per-variant floor. advisory.min_resolved_trades_per_variant still
gates _cross_variant_recommendations specifically (a whole-config
comparison is never trustworthy on a thin sample), and advisory.enabled
still gates whether any of this is shown/appliable at all (checked by
callers, e.g. main.py's routes) - but a real per-field suggestion no longer
requires the *current* variant specifically to have cleared any floor.
"""
import hashlib

from services import config_bounds, config_overrides, regime_analytics, signal_log, stats_power, trade_analytics, trade_category

# Real bug found live (2026-08-15, docs/profit-maximization-assessment-
# 2026-08-15.md): every "is this win-rate gap big enough to act on"
# check in this module used to compare against a single flat 15-point
# tolerance, with no reference to sample size at all - the same bar
# whether n was 5 or 5,000. Concretely wrong at least once: comparing
# min_whale_winrate_pct's rejected pool (55.1%, n=49) against accepted
# trades (68.4%) via the real margin-of-error math below shows that gap
# is NOT clearly outside sampling noise at that n - yet the flat
# tolerance let a "comparable to or better than" suggestion through, and
# it was applied. _comparability_margin_pts replaces the flat constant
# with services/stats_power.py's real Wald-interval math, scaled to
# whichever side of a comparison has the smaller (less confident)
# sample - same "a comparison is never more confident than its weaker
# side" principle _cross_variant_recommendations already used for its
# own confidence_label. Tighter (more demanding) with more data, wider
# (more lenient) with less, instead of one arbitrary number either way.
def _comparability_margin_pts(n_a: int, wr_a: float, n_b: int, wr_b: float) -> float:
    n, wr = (n_a, wr_a) if n_a <= n_b else (n_b, wr_b)
    return stats_power.margin_of_error_pts(n, observed_pct=wr)


def rec_id(config_path: str, suggested_value, n: int) -> str:
    """Stable id for one suggestion. Public (not module-private) since
    services/market_analyst_agent.py's per-series analysis mode (Item 3B)
    reuses the exact same recipe when converting the agent's raw output
    into this module's unified suggestion shape - one id scheme for every
    suggestion source, rule-based or agent-driven. Used by POST /api/
    advisory/recommendations/apply to re-identify a specific rule-based
    recommendation against a freshly recomputed list rather than trusting
    client-supplied current/suggested values directly (the route recomputes
    generate_recommendations() itself); the series-analyst apply path
    instead looks the id up in what was actually persisted at analysis
    time, since an LLM-derived suggestion isn't deterministically
    recomputable the way a rule-based one is - either way, only a value
    this app itself already derived can ever be applied, never whatever a
    request body claims a value should be."""
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


def change_effect(fingerprint_before: str, fingerprint_after: str, summaries: dict[str, dict]) -> dict | None:
    """Before/after win-rate + realized-P&L for one logged config change
    (Item 3D, 2026-08-10) - reuses the same per-variant summaries()
    cross-variant comparisons already read, real trade-attribution rather
    than a fabricated number. Only ever meaningful for a change that
    actually altered the fingerprinted strategy.* subset
    (config_performance.fingerprint/strategy_subset) - a risk.*/advisory.*/
    etc. change always logs fingerprint_before == fingerprint_
    after (see main.py's log_applied_change call sites), which this
    correctly reports as "nothing to compare" rather than pretending a
    delta exists. Also None before either side has any resolved trades yet
    - matches this app's own "don't show a number you can't honestly back"
    practice (shadow mode, market analyst's hedged track record)."""
    if fingerprint_before == fingerprint_after:
        return None
    before = summaries.get(fingerprint_before)
    after = summaries.get(fingerprint_after)
    if not before or not after or not before["total_closed"] or not after["total_closed"]:
        return None
    return {
        "before_win_rate_pct": before["win_rate_pct"],
        "before_n": before["total_closed"],
        "before_realized_pnl": before["total_realized_pnl"],
        "after_win_rate_pct": after["win_rate_pct"],
        "after_n": after["total_closed"],
        "after_realized_pnl": after["total_realized_pnl"],
    }


def change_effect_windowed(config_path: str, applied_at: float, rows: list[dict]) -> dict | None:
    """Gap 3 of docs/config-tuning-data-gaps-2026-08-10.md - change_effect()
    above requires an *exact* fingerprint match on both sides, which starves
    for real data fast: config_performance.fingerprint() hashes the entire
    strategy.* dict, so almost any single-field tweak (this one included)
    mints a brand-new fingerprint with zero trades, and stays that way for a
    long time even though the vast majority of *other* fields didn't change.
    Confirmed directly against real data (2026-08-10): 16 distinct
    strategy.* fingerprints exist, most with 0 resolved trades - the
    fragmentation this function exists to route around.

    This is a looser, complementary measurement, not a replacement: every
    trade whose entry_timestamp falls before/after applied_at counts,
    regardless of fingerprint. Real, disclosed tradeoff - broader data,
    weaker causal attribution (other fields may also have changed inside
    the same window) - meant to be read alongside change_effect(), which
    stays the stricter of the two. Unlike change_effect(), this needs no
    fingerprint transition at all, so it's the only effect measurement a
    risk.*/advisory.*/etc. change can ever get - change_effect()'s own
    fingerprinting only ever covers strategy.*.

    None (not a zeroed-out dict) when either side of the window has no
    resolved trades yet - same "don't show a number you can't honestly
    back" practice as change_effect() and every other hedged number in
    this app."""
    before = [r for r in rows if r.get("entry_timestamp") is not None and r["entry_timestamp"] < applied_at]
    after = [r for r in rows if r.get("entry_timestamp") is not None and r["entry_timestamp"] >= applied_at]
    if not before or not after:
        return None
    before_summary = trade_analytics.compute_summary(before)
    after_summary = trade_analytics.compute_summary(after)
    return {
        "before_win_rate_pct": before_summary["win_rate_pct"],
        "before_n": before_summary["total_closed"],
        "before_realized_pnl": before_summary["total_realized_pnl"],
        "after_win_rate_pct": after_summary["win_rate_pct"],
        "after_n": after_summary["total_closed"],
        "after_realized_pnl": after_summary["total_realized_pnl"],
    }


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
    n_best, n_worst = len(populated[best_key]), len(populated[worst_key])
    margin = _comparability_margin_pts(n_best, win_rates[best_key], n_worst, win_rates[worst_key])
    if win_rates[best_key] - win_rates[worst_key] < margin:
        return None
    n = sum(len(v) for v in populated.values())
    suggested = max(current_value, bucket_floor[best_key])
    if suggested <= current_value:
        return None  # nothing to actually suggest - already at/above the well-performing bucket's floor
    suggested = round(suggested, 3)
    return {
        "id": rec_id("strategy.entry_threshold", suggested, n),
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
        "significance_z": stats_power.two_proportion_z_score(n_best, win_rates[best_key], n_worst, win_rates[worst_key]),
    }


def _longshot_bonus_recommendation(rows: list[dict], strat_cfg: dict) -> dict | None:
    """Favorite-longshot bias, confirmed on real Kalshi data (Bürgi, Deng &
    Whelan 2025 - see docs/prediction-markets-research-reference.md Part
    1.2, worse for takers specifically) and already the reason
    strategy_engine.py requires extra confidence for longshot-zone entries
    (strategy.longshot_entry_threshold_bonus). Direct request (2026-08-09):
    "apply the same methodologies... to all the heuristics" - the entry
    threshold already reacts to FLB, but nothing checked whether the
    current bonus is actually *enough*. Same bucket-comparison shape as
    _entry_threshold_recommendation, splitting on price zone instead of
    confidence: if longshot-zone entries under this exact config still win
    meaningfully less often than non-longshot ones even with today's bonus
    already applied, the bonus itself should go up, not just exist."""
    longshot_zone = strat_cfg.get("longshot_price_threshold", 0.15)
    current_bonus = strat_cfg.get("longshot_entry_threshold_bonus", 0.15)
    priced_rows = [r for r in rows if r.get("entry_price") is not None]
    longshot_rows = [r for r in priced_rows if r["entry_price"] <= longshot_zone or r["entry_price"] >= (1 - longshot_zone)]
    non_longshot_rows = [r for r in priced_rows if r not in longshot_rows]
    if len(longshot_rows) < 3 or len(non_longshot_rows) < 3:
        return None
    longshot_wr = sum(1 for r in longshot_rows if r["won"]) / len(longshot_rows) * 100
    non_longshot_wr = sum(1 for r in non_longshot_rows if r["won"]) / len(non_longshot_rows) * 100
    n_longshot, n_non_longshot = len(longshot_rows), len(non_longshot_rows)
    margin = _comparability_margin_pts(n_non_longshot, non_longshot_wr, n_longshot, longshot_wr)
    if non_longshot_wr - longshot_wr < margin:
        return None  # longshot entries aren't meaningfully underperforming under this config's current bonus
    n = n_longshot + n_non_longshot
    suggested = round(min(0.5, current_bonus + 0.1), 3)
    if suggested <= current_bonus:
        return None
    return {
        "id": rec_id("strategy.longshot_entry_threshold_bonus", suggested, n),
        "config_path": "strategy.longshot_entry_threshold_bonus",
        "current_value": current_bonus,
        "suggested_value": suggested,
        "rationale": (
            f"Under this config, longshot-zone entries (price <= {longshot_zone:.0%} or >= "
            f"{1 - longshot_zone:.0%}) won {longshot_wr:.0f}% of the time (n={n_longshot}) vs "
            f"{non_longshot_wr:.0f}% for non-longshot entries (n={n_non_longshot}) - even with "
            f"the current +{current_bonus:.2f} confidence bonus already applied, favorite-longshot bias "
            f"(confirmed worse for takers on real Kalshi data) still shows through. Raising the bonus to "
            f"{suggested:.2f} targets that gap."
        ),
        "n": n,
        "confidence_label": trade_analytics.confidence_label(n),
        "significance_z": stats_power.two_proportion_z_score(n_non_longshot, non_longshot_wr, n_longshot, longshot_wr),
    }


def _exit_pct_recommendation(rows: list[dict], close_type: str, config_path: str, current_value: float | None,
                             strat_cfg: dict | None = None) -> dict | None:
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
        # left_on_table is a whole-position dollar total (size * (1-price) or
        # size * price - see trade_analytics.build_trade_history), not a
        # cents/contract figure - averaging it raw let one large position
        # dominate the suggestion and fed a mislabeled unit straight into a
        # cost-basis-fraction config field with no bound (real bug, 2026-08-14
        # review: could suggest an absurd take_profit_pct off one big trade).
        # Normalize by cost_basis first, same convention as the stop_loss
        # branch just below, so this stays a fraction-of-cost-basis average.
        left_fractions = [
            r["left_on_table"] / r["cost_basis"]
            for r in group if r["left_on_table"] and r.get("cost_basis")
        ]
        if not left_fractions:
            return None
        avg_left_pct = sum(left_fractions) / len(left_fractions)
        suggested = round(current_value + avg_left_pct, 3)
        rationale = (
            f"take_profit closes under this config left an average {avg_left_pct:.0%} of cost basis on "
            f"the table vs a full $1 win (n={n}) - raising take_profit_pct to {suggested:.2f} targets that gap."
        )
        significance_t = stats_power.one_sample_t_score(left_fractions)
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
        significance_t = stats_power.one_sample_t_score(overshoots)
    # Bound the suggestion to what a position can physically reach
    # (services/config_bounds.py, 2026-08-17 direct report: "the avisory is
    # askings me to change my max loss when it comes to auto closing is
    # 1.75 the cost of opening my position"). The take-profit branch above
    # is `current_value + avg_left_pct` with no ceiling, so a run of trades
    # that each left a lot on the table could push it past 1.0 - a
    # take_profit_pct that no position can ever hit doesn't fail loudly, it
    # silently disables take-profit while stop-loss keeps firing.
    suggested, clamp_note = config_bounds.clamp(config_path, suggested, strat_cfg or {})
    if clamp_note:
        rationale = f"{rationale} ({clamp_note})"
    full_path = f"strategy.{config_path}"
    return {
        "id": rec_id(full_path, suggested, n),
        "config_path": full_path,
        "current_value": current_value,
        "suggested_value": suggested,
        "rationale": rationale,
        "n": n,
        "confidence_label": trade_analytics.confidence_label(n),
        "significance_t": significance_t,
    }


def _auto_exit_threshold_recommendation(rows: list[dict], strat_cfg: dict) -> dict | None:
    """Ported from trade_analytics.compute_insights (the old Config Tuning
    Hints panel) - now a concrete suggested value instead of hedged prose,
    same shape as every other suggestion here."""
    group = [r for r in rows if r["close_type"] == "auto_exit"]
    if len(group) < 3:
        return None
    n = len(group)
    pnls = [r["realized_pnl"] for r in group if r["realized_pnl"] is not None]
    avg_pnl = sum(pnls) / n
    current_value = strat_cfg.get("auto_exit_threshold", 0.6)
    if avg_pnl > 0:
        suggested = round(min(0.95, current_value + 0.05), 3)
        tail = "closing winners too early on average - raising the threshold asks for more conviction before auto-exiting."
    else:
        suggested = round(max(0.05, current_value - 0.05), 3)
        tail = "not cutting losses fast enough on average - lowering the threshold reacts sooner."
    if suggested == current_value:
        return None
    return {
        "id": rec_id("strategy.auto_exit_threshold", suggested, n),
        "config_path": "strategy.auto_exit_threshold",
        "current_value": current_value,
        "suggested_value": suggested,
        "rationale": f"auto_exit closed {n} position(s), averaging {avg_pnl:+.2f} realized - {tail}",
        "n": n,
        "confidence_label": trade_analytics.confidence_label(n),
        "significance_t": stats_power.one_sample_t_score(pnls),
    }


def _sentiment_exit_recommendations(rows: list[dict], strat_cfg: dict) -> list[dict]:
    """Ported from trade_analytics.compute_insights, fixing a real bug found
    there: the old insight's `topic` only ever named exit_sentiment_lean_pct
    even though its own text recommended tuning exit_sentiment_min_signals
    too. Emits both as separate, independently-appliable suggestions
    instead of one field silently standing in for two."""
    group = [r for r in rows if r["close_type"] == "sentiment_reversal"]
    if len(group) < 3:
        return []
    n = len(group)
    pnls = [r["realized_pnl"] for r in group if r["realized_pnl"] is not None]
    avg_pnl = sum(pnls) / n
    significance_t = stats_power.one_sample_t_score(pnls)
    current_lean = strat_cfg.get("exit_sentiment_lean_pct", 65)
    current_min_signals = strat_cfg.get("exit_sentiment_min_signals", 3)
    if avg_pnl <= 0:
        lean_suggested = min(95, current_lean + 5)
        signals_suggested = current_min_signals + 2
        tail = (
            "reversed out on noise before a real trend formed, on average losing money - requiring a "
            "stronger/more-confirmed signal before triggering asks for more confirmation."
        )
    else:
        lean_suggested = max(50, current_lean - 5)
        signals_suggested = max(1, current_min_signals - 2)
        tail = "paid off on average - reacting faster (less lean required, fewer signals) may capture more of these."
    rationale = f"sentiment_reversal closed {n} position(s), averaging {avg_pnl:+.2f} realized - {tail}"
    out = []
    if lean_suggested != current_lean:
        out.append({
            "id": rec_id("strategy.exit_sentiment_lean_pct", lean_suggested, n),
            "config_path": "strategy.exit_sentiment_lean_pct",
            "current_value": current_lean,
            "suggested_value": lean_suggested,
            "rationale": rationale,
            "n": n,
            "confidence_label": trade_analytics.confidence_label(n),
            "significance_t": significance_t,
        })
    if signals_suggested != current_min_signals:
        out.append({
            "id": rec_id("strategy.exit_sentiment_min_signals", signals_suggested, n),
            "config_path": "strategy.exit_sentiment_min_signals",
            "current_value": current_min_signals,
            "suggested_value": signals_suggested,
            "rationale": rationale,
            "n": n,
            "confidence_label": trade_analytics.confidence_label(n),
            "significance_t": significance_t,
        })
    return out


def _within_variant_recommendations(rows: list[dict], strat_cfg: dict) -> list[dict]:
    """Despite the name (kept for continuity with _cross_variant_
    recommendations below), this no longer filters to one config variant -
    see the module docstring. `rows` is the full whale-follow trade
    history."""
    out = []
    rec = _entry_threshold_recommendation(rows, strat_cfg["entry_threshold"])
    if rec:
        out.append(rec)
    rec = _longshot_bonus_recommendation(rows, strat_cfg)
    if rec:
        out.append(rec)
    rec = _exit_pct_recommendation(rows, "take_profit", "take_profit_pct", strat_cfg.get("take_profit_pct"), strat_cfg)
    if rec:
        out.append(rec)
    rec = _exit_pct_recommendation(rows, "stop_loss", "stop_loss_pct", strat_cfg.get("stop_loss_pct"), strat_cfg)
    if rec:
        out.append(rec)
    rec = _auto_exit_threshold_recommendation(rows, strat_cfg)
    if rec:
        out.append(rec)
    out += _sentiment_exit_recommendations(rows, strat_cfg)
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
        if other_wr is None:
            continue
        margin = _comparability_margin_pts(summary["total_closed"], other_wr, current_summary["total_closed"], current_wr)
        if other_wr - current_wr < margin:
            continue  # only surface variants that clearly outperformed the current one
        other_variant = variants.get(fp)
        if other_variant is None:
            continue
        n = min(current_summary["total_closed"], summary["total_closed"])
        significance_z = stats_power.two_proportion_z_score(
            summary["total_closed"], other_wr, current_summary["total_closed"], current_wr,
        )
        diffs = {
            k: v for k, v in other_variant["config"].items()
            if current_variant["config"].get(k) != v
        }
        for field, other_value in diffs.items():
            full_path = f"strategy.{field}"
            out.append({
                "id": rec_id(full_path, other_value, n),
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
                "significance_z": significance_z,
            })
    return out


# "Web of expertise" audit (2026-08-11, direct instruction: "all of the
# analyzers, engines, heuristics, should have the potential to inform each
# other") - gap #1: services/candidate_log.py's rejected-candidate
# counterfactual data (gate_summary()'s hypothetical_win_rate - what would
# have happened to a candidate a gate turned down) never reached this
# engine, even though every other suggestion function here only ever sees
# the *accepted* side of a threshold. Maps each loggable (strategy,
# gate_name) pair to the config field it gates and which direction a
# "rejected candidates did fine" finding argues for moving it - "min" gates
# reject values BELOW the threshold (comparable/better rejected performance
# argues for LOWERING it, admitting more candidates), "max" gates reject
# values ABOVE it (argues for RAISING it). Deliberately only the gates
# listed here are eligible - an unmapped gate is skipped, never guessed at,
# since a wrong-direction suggestion would be worse than none at all.
_GATE_CONFIG_PATH_AND_DIRECTION = {
    ("whale_follow", "entry_threshold"): ("strategy.entry_threshold", "min"),
    ("whale_follow", "min_whale_winrate_pct"): ("strategy.min_whale_winrate_pct", "min"),
    # Real gap found 2026-08-14: these two whale_follow gates
    # (strategy_engine.py) have logged rejections via candidate_log since
    # they shipped but were never added here, so that counterfactual data
    # was captured and then never surfaced as a suggestion. close_window
    # rejects when seconds_to_close is *outside* the window (usually too
    # far out) so loosening means raising the ceiling ("max"); special_
    # market_gate rejects when seconds_to_close is *below* its grace
    # period ("min"). whale_watcher_kalshi's own min_contracts gate
    # (kalshi_trade_tape.py) also logs rejections under a third strategy
    # key, "whale_watcher" - deliberately NOT added here yet, since
    # _rejected_candidate_recommendations' current_value lookup below reads
    # straight off strat_cfg and whale_watcher_kalshi is a different config
    # section entirely - needs its own comparison-baseline + cfg-section
    # wiring, not just a map entry (see ROADMAP.md).
    ("whale_follow", "close_window"): ("strategy.close_window_sec", "max"),
    ("whale_follow", "special_market_gate"): ("strategy.special_market_min_seconds_to_close", "min"),
}
_REJECTED_CANDIDATE_MIN_N = 5  # matches trade_analytics.confidence_label's own low/moderate boundary
_REJECTED_CANDIDATE_NUDGE_PCT = 0.10  # a 10% step toward "admit more"/"restrict more", same
# fixed-nudge idiom _auto_exit_threshold_recommendation/_sentiment_exit_recommendations already use


def _rejected_candidate_recommendations(
    gate_summaries: list[dict], strat_cfg: dict, whale_summary: dict | None,
) -> list[dict]:
    """One suggestion per mapped gate where candidate_log has enough
    resolved rejections to judge - compares what accepted trades actually
    did against what rejected candidates would have done. Only suggests a
    change when rejected candidates did comparably or better (real margin
    of error, not a flat tolerance - see _comparability_margin_pts) - a
    gate correctly filtering out worse candidates needs no comment."""
    out = []
    for (strategy_key, gate_name), (config_path, direction) in _GATE_CONFIG_PATH_AND_DIRECTION.items():
        row = next(
            (g for g in gate_summaries if g["strategy"] == strategy_key and g["gate_name"] == gate_name), None,
        )
        if row is None or row["hypothetical_win_rate"] is None:
            continue
        rejected_n = row["hypothetical_win_rate_n"]
        if rejected_n < _REJECTED_CANDIDATE_MIN_N:
            continue
        if not whale_summary:
            continue
        accepted_wr = whale_summary.get("win_rate_pct")
        accepted_n = whale_summary.get("total_closed", 0)
        if accepted_wr is None or accepted_n < _REJECTED_CANDIDATE_MIN_N:
            continue
        rejected_wr = row["hypothetical_win_rate"]
        margin = _comparability_margin_pts(rejected_n, rejected_wr, accepted_n, accepted_wr)
        if rejected_wr < accepted_wr - margin:
            continue  # rejected candidates did meaningfully worse - the gate is working, nothing to suggest
        _, _, field = config_path.partition(".")
        current_value = strat_cfg.get(field)
        if current_value is None or isinstance(current_value, bool):
            continue  # field not present in this config section, or not a plain number - nothing safe to nudge
        step = abs(current_value) * _REJECTED_CANDIDATE_NUDGE_PCT if current_value else _REJECTED_CANDIDATE_NUDGE_PCT
        suggested = round(current_value - step if direction == "min" else current_value + step, 4)
        if suggested == current_value:
            continue
        n = min(rejected_n, accepted_n)
        significance_z = stats_power.two_proportion_z_score(rejected_n, rejected_wr, accepted_n, accepted_wr)
        out.append({
            "id": rec_id(config_path, suggested, n),
            "config_path": config_path,
            "current_value": current_value,
            "suggested_value": suggested,
            "rationale": (
                f"Candidates rejected by the {gate_name} gate would have won {rejected_wr:.0f}% of the time "
                f"(n={rejected_n} resolved), within real sampling margin ({margin:.1f}pts) of accepted trades' "
                f"actual {accepted_wr:.0f}% (n={accepted_n}) - the gate may be filtering out perfectly good "
                f"candidates. Based on rejected-candidate counterfactual data (services/candidate_log.py), "
                f"not the accepted-trade history every other suggestion here uses."
            ),
            "n": n,
            "confidence_label": trade_analytics.confidence_label(n),
            "source": "rejected-candidate-counterfactual",
            "significance_z": significance_z,
        })
    return out


_SERIES_EVALUATOR_MIN_N = 5  # same floor as every other sample-size-hedged suggestion here


def _series_evaluator_recommendations(series_rows: list[dict], strat_cfg: dict) -> list[dict]:
    """"Web of expertise" audit (2026-08-11) gap #4: services/series_
    evaluator.py already renders a real whale-worthiness verdict
    (qualifying rate) per series, and GET /api/series-evaluator/status
    already cross-checks it against realized win rate
    (below_winrate_floor, phase 82/86) - but that disagreement was purely
    read-only, surfaced to a human on the History tab and nowhere else.
    A series either rejected by series_evaluator's own verdict or flagged
    below_winrate_floor, and not already in strategy.excluded_series, is a
    concrete, actionable candidate to add - one suggestion per series
    rather than one big batch, so each can be reviewed/applied
    independently like every other suggestion here."""
    excluded = set(strat_cfg.get("excluded_series") or [])
    out = []
    for row in series_rows:
        series = row.get("series")
        if not series or series in excluded:
            continue
        reasons = []
        if row.get("status") == "rejected":
            reasons.append(
                f"series_evaluator rejected it on qualifying rate (strike {row.get('strike_count', 0)})"
            )
        if row.get("below_winrate_floor"):
            wr = row.get("whale_win_rate")
            reasons.append(
                f"realized whale win rate is {wr:.0f}%, below strategy.min_whale_winrate_pct" if wr is not None
                else "realized whale win rate is below strategy.min_whale_winrate_pct"
            )
        if not reasons:
            continue
        n = row.get("whale_resolved", 0)
        if n < _SERIES_EVALUATOR_MIN_N:
            continue
        new_excluded = sorted(excluded | {series})
        out.append({
            "id": rec_id("strategy.excluded_series", new_excluded, n),
            "config_path": "strategy.excluded_series",
            "current_value": sorted(excluded),
            "suggested_value": new_excluded,
            "rationale": (
                f"{series}: " + "; ".join(reasons) + f" (n={n} resolved whale signals, last 30 days). "
                f"Cross-referencing services/series_evaluator.py's own verdict with realized signal_log "
                f"win-rate data, not just accepted-trade history."
            ),
            "n": n,
            "confidence_label": trade_analytics.confidence_label(n),
            "source": "series-evaluator-crosscheck",
        })
    return out


_CATEGORY_MIN_N = 5


def _category_conditional_recommendations(
    category_rows: list[dict], strat_cfg: dict, overall_win_rate: float | None,
    strategy_overrides: dict | None = None, overall_n: int = 0,
) -> list[dict]:
    """"Web of expertise" audit (2026-08-11) gap #2: no suggestion here has
    ever been category-conditional, even though services/regime_analytics.py's
    by_category() already computes exactly the per-category win rate needed
    to judge one - trade_category.py's category-at-entry-time capture and
    the History tab's Regime Segmentation panel both already exist, this
    was purely a missing connection. category_rows: regime_analytics.
    by_category()'s own output. Compares each category's win rate against
    the OVERALL win rate (not confidence-bucket boundaries like
    _entry_threshold_recommendation - a category-level suggestion is a
    coarser question, "should this whole category get a different bar,"
    not "where exactly is the crossover point"). Writes to
    strategy_overrides.by_category.<category>.entry_threshold via
    services/config_overrides.py's generic resolver (2026-08-15 migration -
    this used to be the bespoke, single-field strategy.
    entry_threshold_by_category; the resolver now covers any strategy.*
    field, entry_threshold included, so this is just its first real
    producer, not a special case), one category at a time so each can be
    reviewed/applied independently. config_path is always exactly
    "<section>.<field>" (main.py's apply route requires this) - so this
    targets "strategy_overrides.by_category" as a whole, with
    suggested_value being the COMPLETE by_category dict (every other
    category's entry preserved via config_overrides.merge_override, not
    just this one) so config_store.update()'s one-level-deep merge can't
    clobber sibling categories."""
    if overall_win_rate is None:
        return []
    by_category = dict((strategy_overrides or {}).get("by_category") or {})
    base_threshold = strat_cfg.get("entry_threshold", 0.5)
    out = []
    for row in category_rows:
        category = row.get("category")
        n = row.get("total_closed", 0)
        wr = row.get("win_rate_pct")
        if not category or wr is None or n < _CATEGORY_MIN_N:
            continue
        gap = wr - overall_win_rate
        # overall_win_rate is the whole book - always a much larger sample
        # than any one category, so the category's own n is the real
        # limiting factor here (see _comparability_margin_pts above for
        # the two-sample version of this same fix).
        if abs(gap) < stats_power.margin_of_error_pts(n, observed_pct=wr):
            continue
        current = by_category.get(category, {}).get("entry_threshold", base_threshold)
        step = 0.05
        suggested = round(min(0.95, current + step) if gap < 0 else max(0.05, current - step), 3)
        if suggested == current:
            continue
        significance_z = stats_power.two_proportion_z_score(n, wr, overall_n, overall_win_rate) if overall_n else None
        new_by_category = config_overrides.merge_override(
            {"by_category": by_category}, "by_category", category, "entry_threshold", suggested,
        )["by_category"]
        tail = (
            f"performs {abs(gap):.0f}pts worse than the overall {overall_win_rate:.0f}% win rate - a higher "
            f"category-specific threshold asks for more conviction here specifically."
            if gap < 0 else
            f"performs {gap:.0f}pts better than the overall {overall_win_rate:.0f}% win rate - a lower "
            f"category-specific threshold could capture more of these."
        )
        out.append({
            "id": rec_id("strategy_overrides.by_category", new_by_category, n),
            "config_path": "strategy_overrides.by_category",
            "current_value": by_category,
            "suggested_value": new_by_category,
            "rationale": f"{category} (n={n} resolved, {wr:.0f}% win rate) {tail}",
            "n": n,
            "confidence_label": trade_analytics.confidence_label(n),
            "source": "category-conditional",
            "significance_z": significance_z,
        })
    return out


def _series_conditional_recommendations(
    rows: list[dict], strat_cfg: dict, overall_win_rate: float | None,
    strategy_overrides: dict | None = None, overall_n: int = 0,
) -> list[dict]:
    """2026-08-16 direct request: "it makes more sense to do it by series
    (ex. KXBTC15M, or KXMLB), and fallback to category, seeing as how the
    markets are generally unique individual events" - then refined: "maybe
    before falling back to category winrate from series winrate, theres a
    middle step... by subcategory (e.g., baseball, football)". Same shape
    as _category_conditional_recommendations above (compare a segment's
    win rate against the whole book's, suggest nudging entry_threshold if
    the gap clears both the min-N floor and the margin-of-error check),
    but per SERIES (signal_log.series_of - a market ticker like
    KXBTC15M-26AUG161645-45 never recurs, the series is the real
    repeating unit), falling through progressively coarser tiers of
    evidence - series' own win rate, then its subcategory's
    (services/trade_category.py's Kalshi `competition` capture, e.g. "Pro
    Baseball"), then its category's (existing regime_analytics.by_category)
    - the first one with enough resolved trades to trust. Mirrors
    config_overrides.resolve()'s own "most specific wins, falls through to
    next coarser layer" philosophy, just applied to which win-rate FIGURE
    justifies a suggestion rather than which config VALUE applies - the
    suggestion itself always targets strategy_overrides.by_series.<series>,
    since that's the tier that actually governs this series' real trades
    regardless of which tier's data justified the number.

    A series with genuinely too little data at every tier (fewer than
    _CATEGORY_MIN_N resolved trades in its own history, its subcategory's,
    AND its category's) gets no suggestion at all - same "don't guess"
    principle as every other gated recommendation here, just checked three
    times instead of once."""
    if overall_win_rate is None:
        return []
    series_by_name = {r["series"]: r for r in regime_analytics.by_series(rows)}
    subcategory_by_name = {r["subcategory"]: r for r in regime_analytics.by_subcategory(rows)}
    category_by_name = {r["category"]: r for r in regime_analytics.by_category(rows)}

    # series -> subcategory/category, derived once from the same per-ticker
    # lookups the by_subcategory/by_category tiers above already queried -
    # first non-empty value wins per series (every ticker in one series
    # shares the same category/subcategory in practice, so which specific
    # ticker supplies it doesn't matter).
    tickers = [r["ticker"] for r in rows]
    categories = trade_category.categories_for_tickers(tickers)
    subcategories = trade_category.subcategories_for_tickers(tickers)
    series_to_category: dict[str, str] = {}
    series_to_subcategory: dict[str, str] = {}
    for r in rows:
        series = signal_log.series_of(r["ticker"])
        if series not in series_to_category and categories.get(r["ticker"]):
            series_to_category[series] = categories[r["ticker"]]
        if series not in series_to_subcategory and subcategories.get(r["ticker"]):
            series_to_subcategory[series] = subcategories[r["ticker"]]

    by_series_override = dict((strategy_overrides or {}).get("by_series") or {})
    base_threshold = strat_cfg.get("entry_threshold", 0.5)
    out = []
    for series, series_row in series_by_name.items():
        n = series_row.get("total_closed", 0)
        wr = series_row.get("win_rate_pct")
        evidence = "its own"

        if wr is None or n < _CATEGORY_MIN_N:
            sub = series_to_subcategory.get(series)
            sub_row = subcategory_by_name.get(sub) if sub else None
            if sub_row and sub_row.get("win_rate_pct") is not None and sub_row.get("total_closed", 0) >= _CATEGORY_MIN_N:
                n, wr, evidence = sub_row["total_closed"], sub_row["win_rate_pct"], f'"{sub}" subcategory'
            else:
                cat = series_to_category.get(series)
                cat_row = category_by_name.get(cat) if cat else None
                if cat_row and cat_row.get("win_rate_pct") is not None and cat_row.get("total_closed", 0) >= _CATEGORY_MIN_N:
                    n, wr, evidence = cat_row["total_closed"], cat_row["win_rate_pct"], f'"{cat}" category'
                else:
                    continue  # not enough data at any of the three tiers - no guess

        gap = wr - overall_win_rate
        if abs(gap) < stats_power.margin_of_error_pts(n, observed_pct=wr):
            continue
        current = by_series_override.get(series, {}).get("entry_threshold", base_threshold)
        step = 0.05
        suggested = round(min(0.95, current + step) if gap < 0 else max(0.05, current - step), 3)
        if suggested == current:
            continue
        significance_z = stats_power.two_proportion_z_score(n, wr, overall_n, overall_win_rate) if overall_n else None
        new_by_series = config_overrides.merge_override(
            {"by_series": by_series_override}, "by_series", series, "entry_threshold", suggested,
        )["by_series"]
        tail = (
            f"performs {abs(gap):.0f}pts worse than the overall {overall_win_rate:.0f}% win rate - a higher "
            f"series-specific threshold asks for more conviction here specifically."
            if gap < 0 else
            f"performs {gap:.0f}pts better than the overall {overall_win_rate:.0f}% win rate - a lower "
            f"series-specific threshold could capture more of these."
        )
        out.append({
            "id": rec_id("strategy_overrides.by_series", new_by_series, n),
            "config_path": "strategy_overrides.by_series",
            "current_value": by_series_override,
            "suggested_value": new_by_series,
            "rationale": f"{series} (using {evidence} data: n={n} resolved, {wr:.0f}% win rate) {tail}",
            "n": n,
            "confidence_label": trade_analytics.confidence_label(n),
            "source": "series-conditional",
            "significance_z": significance_z,
        })
    return out


def _drop_stale_recommendations(
    recs: list[dict], rows: list[dict], last_applied_by_path: dict[str, float],
) -> list[dict]:
    """Direct, confirmed-live bug report (2026-08-11): "if i click apply it
    just gives me the same evaluation and same potential increase value
    for that factor... suggesting a massive bug." Confirmed: every
    per-field suggestion function above recomputes its verdict from the
    full trade history every call and reads current_value fresh from cfg
    (so it does reflect a just-applied change) - but the underlying
    evidence (e.g. _auto_exit_threshold_recommendation's avg_pnl over
    trades with close_type == "auto_exit") is the exact same stale group
    of past trades until a genuinely new one closes. Clicking Apply
    repeatedly against that same stale evidence just walks the value
    further in the same direction each time, off information that never
    actually validated whether the previous nudge helped. Drops any
    recommendation whose config_path was changed more recently than the
    newest trade *entered* under the relevant row set - entry_timestamp
    vs. applied_at is the same before/after convention change_effect()
    above already uses, not a new comparison invented for this. A
    config_path never applied before (not in last_applied_by_path) is
    never stale by definition.

    fresh_samples_since_change (2026-08-15 direct request: "the advisory
    should also take into consideration how many samples have been logged
    after the change before giving me any updated advice... it should be
    aware that ive made a change, it should be reflected wherever its
    displayed") - upgrades the has_fresh_evidence boolean above from a
    pure drop/keep gate into a real count attached to every surviving
    recommendation, so a card backed by 2 post-change trades reads
    differently from one backed by 200 even though both technically
    passed the same "at least one" gate. None (not 0) specifically means
    "this field has never been changed" - a genuinely different situation
    from "changed, but nothing fresh yet" (which is 0, and already
    filtered out below), so the frontend can tell them apart rather than
    both showing as a bare "0"."""
    if not last_applied_by_path:
        for rec in recs:
            rec["fresh_samples_since_change"] = None
        return recs
    out = []
    for rec in recs:
        last_applied = last_applied_by_path.get(rec["config_path"])
        if last_applied is None:
            rec["fresh_samples_since_change"] = None
            out.append(rec)
            continue
        fresh_count = sum(
            1 for r in rows
            if r.get("entry_timestamp") is not None and r["entry_timestamp"] > last_applied
        )
        if fresh_count > 0:
            rec["fresh_samples_since_change"] = fresh_count
            out.append(rec)
    return out


def generate_recommendations(
    rows: list[dict], cfg: dict, current_fp: str, variants: dict[str, dict], min_resolved_trades: int,
    gate_summaries: list[dict] | None = None,
    last_applied_by_path: dict[str, float] | None = None, series_evaluator_rows: list[dict] | None = None,
    category_rows: list[dict] | None = None, declined_ids: set[str] | None = None,
) -> dict:
    """The unified entrypoint - see the module docstring for what changed
    2026-08-10. Returns {"recommendations": [...], "resolved_count": int,
    "min_resolved_trades_per_variant": int}. No blanket gate on the whole
    return value anymore: per-field suggestions (_within_variant_
    recommendations) read the full history and hedge on their own
    per-field sample size, same as compute_insights() always did.
    resolved_count/min_resolved_trades_per_
    variant are still reported so a caller can show progress toward
    unlocking _cross_variant_recommendations specifically, the one thing
    here that still needs the current variant to clear a floor.

    declined_ids (History tab redesign, 2026-08-14/15 direct request):
    services/suggestion_decisions.declined_ids() - a suggestion a human
    already clicked "no thanks" on doesn't come back with the exact same
    evidence behind it. Filtered last, after staleness - a recommendation
    dropped by staleness never had a decline recorded against its id
    anyway, so order between the two checks doesn't matter, but staleness
    running first keeps the more common case cheap."""
    summaries = variant_summaries(rows)
    current_summary = summaries.get(current_fp)
    resolved_count = current_summary["total_closed"] if current_summary else 0

    # Computed once, not once per caller-gated block below (2026-08-23,
    # ROADMAP.md's "per-module data-consumption audit" gap-check) - a real
    # O(n) pass over the full trade history (compute_summary), and every
    # real caller (routes.py, market_analyst_orchestrator's context
    # builders) passes both gate_summaries and category_rows together, so
    # the two blocks below were computing the identical result twice on
    # every call in the normal case, not just a rare overlap.
    overall_summary = trade_analytics.compute_summary(rows)

    recs = _within_variant_recommendations(rows, cfg["strategy"])
    recs += _cross_variant_recommendations(current_fp, summaries, variants, min_resolved_trades)
    if gate_summaries:
        recs += _rejected_candidate_recommendations(
            gate_summaries, cfg["strategy"], overall_summary,
        )
    if series_evaluator_rows:
        recs += _series_evaluator_recommendations(series_evaluator_rows, cfg["strategy"])
    if category_rows:
        recs += _category_conditional_recommendations(
            category_rows, cfg["strategy"], overall_summary.get("win_rate_pct"),
            cfg.get("strategy_overrides"), overall_n=overall_summary.get("total_closed", 0),
        )
        # series -> subcategory -> category fallback chain (2026-08-16
        # direct request, see _series_conditional_recommendations' own
        # docstring) - gated behind the same `if category_rows:` as the
        # category-level suggestion above since it needs category_rows'
        # caller-supplied evidence that regime segmentation has real data
        # to work with at all; series/subcategory tiers are computed
        # internally from `rows` itself, no separate caller-supplied arg
        # needed for those two.
        recs += _series_conditional_recommendations(
            rows, cfg["strategy"], overall_summary.get("win_rate_pct"),
            cfg.get("strategy_overrides"), overall_n=overall_summary.get("total_closed", 0),
        )
    recs = _drop_stale_recommendations(recs, rows, last_applied_by_path or {})
    if declined_ids:
        recs = [r for r in recs if r["id"] not in declined_ids]
    return {
        "recommendations": recs,
        "resolved_count": resolved_count,
        "min_resolved_trades_per_variant": min_resolved_trades,
    }
