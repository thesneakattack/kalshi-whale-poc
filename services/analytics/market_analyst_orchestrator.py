"""
Market-analyst + series-analysis orchestration - the one part of
"analytics" that's real logic, not routing. Extracted 2026-08-22 as a unit
(module-level in-flight guard sets included) so they stay singletons of
this module rather than accidentally duplicated. This is the only route
group in the read-only analytics surface that spends real money (LLM
calls via ANTHROPIC_API_KEY) and has race guards protecting against
double-fire - moved with extra care for that reason.
"""
import os
import time

from services import (
    candidate_log, config_performance, market_analyst_agent, ml_feed,
    regime_analytics, series_evaluator, signal_log, stats_power, suggestion_decisions, trade_analytics,
)
from services.advisory import advisory_engine
from services.app_state import broker, bump_generation, state
from services.config.config_paths import _types_compatible
from services.kalshi_client import KalshiClient

_analyzing_tickers: set[str] = set()


async def _run_market_analyst_for_ticker(client: KalshiClient, cfg: dict, ticker: str) -> dict:
    """On-demand, single-ticker orchestration for services/market_analyst_agent/
    - direct request (2026-08-09): switched from an automatic per-tick
    background scan to a button-triggered "analyze this one market right
    now" flow, since this is the first thing in this app that spends real
    money per call (every other strategy is zero-marginal-cost arithmetic),
    so it should be a deliberate human action, not ambient background spend
    - same "prove it, then promote it" philosophy already used for
    advisory_engine's manual-apply-with-audit-trail and
    confidence_calibration's read-only-until-enough-data design.

    Returns {"ok": True, **result} on a fresh analysis, or {"ok": False,
    "reason": <str>} if gated (disabled, no key, already in flight, still in
    cooldown, or the LLM call itself failed/declined) - always a clean dict,
    never raises, so the route can turn this straight into a JSON response."""
    ma_cfg = cfg.get("market_analyst") or {}
    if not ma_cfg.get("enabled"):
        return {"ok": False, "reason": "Market Analyst is disabled — enable it in Config → Market Analyst (AI)."}
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"ok": False, "reason": "No ANTHROPIC_API_KEY configured in .env."}

    if ticker in _analyzing_tickers:
        return {"ok": False, "reason": "Already analyzing this market — try again in a moment."}

    cooldown = ma_cfg.get("reanalyze_cooldown_sec", 1800)
    now = time.time()
    last = market_analyst_agent.last_analyzed_at(ticker)
    if last is not None and (now - last) < cooldown:
        wait_sec = int(cooldown - (now - last))
        return {"ok": False, "reason": f"Already analyzed recently — try again in {wait_sec}s."}

    _analyzing_tickers.add(ticker)
    try:
        return await _analyze_market_uncached(client, ma_cfg, cfg, ticker, now, api_key)
    finally:
        _analyzing_tickers.discard(ticker)


async def _analyze_market_uncached(
    client: KalshiClient, ma_cfg: dict, cfg: dict, ticker: str, now: float, api_key: str,
) -> dict:
    """The real fetch-and-analyze body, split out of _run_market_analyst_for_
    ticker so the in-flight guard in that function wraps every exit path
    (including early returns below) via one try/finally, rather than each
    needing its own cleanup."""
    try:
        market_detail = await client.get_market(ticker)
    except Exception as e:
        return {"ok": False, "reason": f"Could not fetch market data: {e}"}
    event_ticker = market_detail.get("event_ticker")
    if event_ticker:
        try:
            ev = await client.get_event(event_ticker)
            market_detail = {**market_detail, "category": (ev.get("event") or {}).get("category")}
        except Exception:
            pass  # category is a bonus for the prompt, not required

    # Same real accumulated-context bundle a human sees on the dashboard
    # (services/ml_feed.py's whole "work WITH, not replace" point) - grounds
    # the model's judgment in this app's own track record, not just general
    # world knowledge.
    adv_cfg = cfg["advisory"]
    all_rows = trade_analytics.build_trade_history([t.to_dict() for t in broker.trade_log])
    recommendations = {"recommendations": [], "gated_reason": "advisory engine is disabled"}
    if adv_cfg["enabled"]:
        current_fp = config_performance.fingerprint(cfg)
        variants = {v["fingerprint"]: v for v in config_performance.all_variants()}
        recommendations = advisory_engine.generate_recommendations(
            all_rows, cfg, current_fp, variants, adv_cfg["min_resolved_trades_per_variant"],
            gate_summaries=candidate_log.gate_summary(),
            last_applied_by_path=config_performance.all_last_applied_by_path(),
            series_evaluator_rows=_series_evaluator_overview_with_crosscheck(cfg),
            category_rows=regime_analytics.by_category(all_rows),
        )
    snapshot = ml_feed.build_context_snapshot(
        cfg=cfg,
        portfolio=broker.state(state["latest_prices"]),
        market_snapshot={"markets": state["markets"], "latest_prices": state["latest_prices"]},
        trade_history_rows=all_rows,
        whale_track_record=signal_log.stats(),
        advisory=recommendations,
    )

    result = await market_analyst_agent.analyze_market(
        market_detail, snapshot, ma_cfg.get("model", "claude-sonnet-5"), api_key,
    )
    if result is None:
        return {"ok": False, "reason": "The model call failed or declined to answer — see server logs."}
    market_price = float(market_detail.get("yes_bid_dollars") or market_detail.get("yes_ask_dollars") or 0.5)
    market_analyst_agent.record_analysis(
        ticker=ticker, series=signal_log.series_of(ticker), market_price=market_price,
        estimated_probability=result["estimated_probability"], llm_confidence=result["confidence"],
        reasoning=result["reasoning"], model=ma_cfg.get("model", "claude-sonnet-5"), analyzed_at=now,
    )
    bump_generation()
    return {"ok": True, **result, "market_price": market_price}


# Series currently being analyzed - same in-flight-race guard as
# _analyzing_tickers above, kept as its own set rather than sharing one:
# tickers and series are different namespaces (a series is a ticker prefix,
# e.g. "KXPGATOUR" vs. a real ticker like "KXPGATOUR-26AUG10-DEF"), so
# reusing the same set risks a false "already in flight" collision if a
# series name and a real ticker ever happened to be identical strings.
_analyzing_series: set[str] = set()


def _build_series_context(cfg: dict, series: str) -> dict:
    """Assembles everything services/market_analyst_agent.build_series_
    prompt() needs (Item 3B) - real whale-signal stats scoped to this
    series (signal_log.series_stats reused as-is: series_of() on an
    already-bare series string is a no-op, so this works without a
    series-specific variant of that function), this series' own closed-
    trade summary (trade_analytics.compute_summary on rows filtered to
    tickers under this series), its current excluded_series membership +
    per-series contract-count override, and its series_evaluator verdict if
    it's ever been evaluated."""
    all_rows = trade_analytics.build_trade_history([t.to_dict() for t in broker.trade_log])
    series_rows = [r for r in all_rows if signal_log.series_of(r["ticker"]) == series]
    evaluator_row = next((r for r in series_evaluator.overview() if r["series"] == series), None)
    strat_cfg = cfg.get("strategy") or {}
    whale_cfg = cfg.get("whale_watcher_kalshi") or {}
    return {
        "series": series,
        "whale_stats": signal_log.series_stats(series, days=30),
        "trade_summary": trade_analytics.compute_summary(series_rows),
        "currently_excluded": series in (strat_cfg.get("excluded_series") or []),
        "min_contracts_override": (whale_cfg.get("min_contracts_by_series") or {}).get(series),
        "evaluator_status": evaluator_row,
    }


def _series_suggestions_from_raw(cfg: dict, series: str, raw_suggestions: list[dict]) -> list[dict]:
    """Converts services/market_analyst_agent.analyze_series()'s raw
    {"action": "exclude"|"include", "rationale"} output into this app's
    unified suggestion shape ({config_path, current_value, suggested_value,
    id, rationale}, same as services/advisory/advisory_engine.py's rule-based
    suggestions) - done here, not in market_analyst_agent/, since it
    needs the live config to compute the actual before/after
    strategy.excluded_series list. A no-op action (e.g. the model suggests
    "exclude" on a series that's already excluded) is silently dropped -
    nothing to actually apply."""
    current_list = list((cfg.get("strategy") or {}).get("excluded_series") or [])
    out = []
    for raw in raw_suggestions:
        action = raw.get("action")
        if action == "exclude" and series not in current_list:
            suggested_list = sorted(current_list + [series])
        elif action == "include" and series in current_list:
            suggested_list = [s for s in current_list if s != series]
        else:
            continue  # already in the suggested state, or an action we don't recognize - nothing to apply
        out.append({
            "id": advisory_engine.rec_id("strategy.excluded_series", suggested_list, 0),
            "config_path": "strategy.excluded_series",
            "current_value": current_list,
            "suggested_value": suggested_list,
            "rationale": raw.get("rationale") or "",
            "series": series,
            "source": "series-analyst",
        })
    return out


async def _run_series_analysis(cfg: dict, series: str) -> dict:
    """On-demand per-series orchestration (Item 3B) - same gating shape as
    _run_market_analyst_for_ticker (disabled/no-key/in-flight/cooldown all
    return a clean {"ok": False, "reason": ...} rather than raising), reuses
    market_analyst.reanalyze_cooldown_sec rather than inventing a second
    knob for a mode that spends the exact same kind of API call."""
    ma_cfg = cfg.get("market_analyst") or {}
    if not ma_cfg.get("enabled"):
        return {"ok": False, "reason": "Market Analyst is disabled — enable it in Config → Market Analyst (AI)."}
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"ok": False, "reason": "No ANTHROPIC_API_KEY configured in .env."}
    if series in _analyzing_series:
        return {"ok": False, "reason": "Already analyzing this series — try again in a moment."}

    cooldown = ma_cfg.get("reanalyze_cooldown_sec", 1800)
    now = time.time()
    last = market_analyst_agent.last_series_analyzed_at(series)
    if last is not None and (now - last) < cooldown:
        wait_sec = int(cooldown - (now - last))
        return {"ok": False, "reason": f"Already analyzed recently — try again in {wait_sec}s."}

    _analyzing_series.add(series)
    try:
        series_ctx = _build_series_context(cfg, series)
        model = ma_cfg.get("model", "claude-sonnet-5")
        result = await market_analyst_agent.analyze_series(series_ctx, model, api_key)
        if result is None:
            return {"ok": False, "reason": "The model call failed or declined to answer — see server logs."}
        suggestions = _series_suggestions_from_raw(cfg, series, result["suggestions"])
        # Same "declining sticks until the evidence genuinely changes" as
        # the rule-based Advisory path - filtered before persisting, not
        # just before display, so a re-analysis with identical output
        # doesn't re-mint the same already-declined id into a fresh row.
        declined = suggestion_decisions.declined_ids()
        suggestions = [s for s in suggestions if s["id"] not in declined]
        analysis_id = market_analyst_agent.record_series_analysis(
            series=series, summary=result["summary"], suggestions=suggestions, model=model, analyzed_at=now,
        )
        bump_generation()
        return {"ok": True, "analysis_id": analysis_id, "series": series, "summary": result["summary"], "suggestions": suggestions}
    finally:
        _analyzing_series.discard(series)


# "Feed the Analyst" full-spectrum scan (Item 3C, 2026-08-10) - only one
# subject (the whole platform), so a plain bool is enough for the in-flight
# guard, unlike the ticker/series sets above.
_full_spectrum_analyzing = False

# The two config paths already protected from generic manual edits (see
# services/config/routes.py's update_config guards) - the full-spectrum
# agent gets the exact same protection, since its suggestions can otherwise
# touch ANY config field, a materially wider blast radius than the fixed
# single-field scope 3B's per-series mode was deliberately limited to.
_PROTECTED_CONFIG_PATHS = {
    "kalshi_account.trading_enabled", "advisory.auto_apply_enabled",
    # Same protection, same reasoning (2026-08-10, direct request to add a
    # calibration auto-apply path) - a typed-confirmation-gated route is
    # the only way to flip this on, not a plain Config-tab checkbox.
    "confidence_calibration.auto_apply_enabled",
}

# trade_analytics.confidence_label()'s three tiers, ranked so
# advisory.auto_apply_min_confidence (a config string) can be compared
# against a real recommendation's own confidence_label with a single >=.
_CONFIDENCE_RANK = {"low": 0, "moderate": 1, "higher": 2}


def _build_full_spectrum_context(cfg: dict) -> dict:
    """Assembles services/market_analyst_agent.build_full_spectrum_prompt()'s
    input (Item 3C) - deliberately every value here is an aggregated
    rollup (compute_summary(), variant_summaries(), stats()), never raw
    per-trade/per-signal rows, so prompt size stays bounded regardless of
    how much history has accumulated (per the plan's own explicit
    "aggregated/summarized data, not raw per-trade rows" requirement)."""
    all_rows = trade_analytics.build_trade_history([t.to_dict() for t in broker.trade_log])
    current_fp = config_performance.fingerprint(cfg)
    variants = {v["fingerprint"]: v for v in config_performance.all_variants()}
    adv_cfg = cfg.get("advisory") or {}
    gate_summaries = candidate_log.gate_summary()
    # Computed once, reused below for the return dict's own "regime_by_
    # category" - previously called twice on the identical all_rows input
    # (2026-08-23, ROADMAP.md's "per-module data-consumption audit" gap-check).
    category_rows = regime_analytics.by_category(all_rows)
    recommendations = advisory_engine.generate_recommendations(
        all_rows, cfg, current_fp, variants, adv_cfg.get("min_resolved_trades_per_variant", 30),
        gate_summaries=gate_summaries,
        last_applied_by_path=config_performance.all_last_applied_by_path(),
        series_evaluator_rows=_series_evaluator_overview_with_crosscheck(cfg),
        category_rows=category_rows,
    )
    # Busiest 10 series by observed trade volume - a real, disclosed bound
    # (not exhaustive) so this section can't grow unbounded as more series
    # accumulate history over the app's life.
    busiest_series = sorted(
        series_evaluator.overview(), key=lambda r: r["trades_observed"], reverse=True,
    )[:10]
    per_series_whale = [
        {**signal_log.series_stats(r["series"], days=30), "evaluator_status": r["status"]}
        for r in busiest_series
    ]
    return {
        "config": cfg,
        "trade_summary": trade_analytics.compute_summary(all_rows),
        "whale_track_record": signal_log.stats(days=30),
        "advisory_recommendations": recommendations["recommendations"],
        "variant_summaries": advisory_engine.variant_summaries(all_rows),
        "recent_applied_changes": config_performance.recent_applied_changes(limit=20),
        "per_series_whale_breakdown": per_series_whale,
        "portfolio": broker.state(state["latest_prices"]),
        # Two real gaps closed here (2026-08-11 hardening pass, "web of
        # expertise" audit) - both datasets already existed and were
        # already aggregated rollups (no raw per-trade rows, consistent
        # with this function's own bound), just never assembled into this
        # context before. gate_summary() is the rejected-candidate
        # counterfactual data (what would have happened to a candidate a
        # gate turned down) - the model previously only ever saw the
        # accepted side of every threshold. by_category/by_hour_of_day are
        # the same segmentation the History tab's Regime panel already
        # shows a human, now available to the model too.
        "rejected_candidate_gates": gate_summaries,
        "regime_by_category": category_rows,
        "regime_by_hour": regime_analytics.by_hour_of_day(all_rows),
    }


def _full_spectrum_suggestions_from_raw(cfg: dict, raw_suggestions: list[dict]) -> list[dict]:
    """Validates + converts the model's raw {"config_path",
    "suggested_value", "rationale"} output into this app's unified
    suggestion shape - unlike 3B's fixed-field conversion, this can't trust
    the model's config_path at all (it can name literally any field), so
    every suggestion here is checked against the LIVE config before being
    treated as real: the path must resolve to a section+field that already
    exists (never inventing a new one), must not be one of the two fields
    already protected from generic config edits, must actually differ from
    the current value, and must be a reasonably type-compatible value.
    Anything that fails any check is silently dropped, not surfaced as an
    error - matching how 3B's own no-op filtering works, an invalid
    suggestion just isn't a real suggestion."""
    out = []
    for raw in raw_suggestions:
        config_path = raw.get("config_path") or ""
        if config_path in _PROTECTED_CONFIG_PATHS:
            continue
        section, _, field = config_path.partition(".")
        if not section or not field:
            continue
        section_cfg = cfg.get(section)
        if not isinstance(section_cfg, dict) or field not in section_cfg:
            continue  # never touch a field that doesn't already exist in the live config
        current_value = section_cfg[field]
        suggested_value = raw.get("suggested_value")
        if suggested_value == current_value:
            continue
        if current_value is not None and not _types_compatible(current_value, suggested_value):
            continue
        out.append({
            "id": advisory_engine.rec_id(config_path, suggested_value, 0),
            "config_path": config_path,
            "current_value": current_value,
            "suggested_value": suggested_value,
            "rationale": raw.get("rationale") or "",
            "source": "full-spectrum-analyst",
        })
    return out


async def _run_full_spectrum_analysis(cfg: dict) -> dict:
    """On-demand full-platform orchestration (Item 3C) - same gating shape
    as _run_market_analyst_for_ticker/_run_series_analysis (disabled/no-key/
    in-flight/cooldown all return a clean {"ok": False, "reason": ...}
    rather than raising), reuses market_analyst.reanalyze_cooldown_sec
    rather than a third distinct knob."""
    global _full_spectrum_analyzing
    ma_cfg = cfg.get("market_analyst") or {}
    if not ma_cfg.get("enabled"):
        return {"ok": False, "reason": "Market Analyst is disabled — enable it in Config → Market Analyst (AI)."}
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"ok": False, "reason": "No ANTHROPIC_API_KEY configured in .env."}
    if _full_spectrum_analyzing:
        return {"ok": False, "reason": "A full-spectrum scan is already running — try again in a moment."}

    cooldown = ma_cfg.get("reanalyze_cooldown_sec", 1800)
    now = time.time()
    last = market_analyst_agent.last_full_spectrum_analyzed_at()
    if last is not None and (now - last) < cooldown:
        wait_sec = int(cooldown - (now - last))
        return {"ok": False, "reason": f"Already analyzed recently — try again in {wait_sec}s."}

    _full_spectrum_analyzing = True
    try:
        context = _build_full_spectrum_context(cfg)
        model = ma_cfg.get("model", "claude-sonnet-5")
        result = await market_analyst_agent.analyze_full_spectrum(context, model, api_key)
        if result is None:
            return {"ok": False, "reason": "The model call failed or declined to answer — see server logs."}
        suggestions = _full_spectrum_suggestions_from_raw(cfg, result["suggestions"])
        declined = suggestion_decisions.declined_ids()
        suggestions = [s for s in suggestions if s["id"] not in declined]
        analysis_id = market_analyst_agent.record_full_spectrum_analysis(
            summary=result["summary"], suggestions=suggestions, model=model, analyzed_at=now,
        )
        bump_generation()
        return {"ok": True, "analysis_id": analysis_id, "summary": result["summary"], "suggestions": suggestions}
    finally:
        _full_spectrum_analyzing = False


def _series_evaluator_overview_with_crosscheck(cfg: dict) -> list[dict]:
    """series_evaluator.overview() enriched with the real win-rate
    cross-check (Gap 4/10 of docs/config-tuning-data-gaps-2026-08-10.md) -
    factored out of GET /api/series-evaluator/status (phase 82) so
    advisory_engine's own series-evaluator suggestions (2026-08-11, "web
    of expertise" audit) can reuse the exact same enrichment instead of
    duplicating it. series_evaluator judges a series by *qualifying rate*
    (real trades observed vs. how many cleared the notional threshold),
    strategy_engine.py's own min_whale_winrate_pct gate judges it by
    *realized win rate* - two genuinely independent mechanisms this
    attaches to each other."""
    strat_cfg = cfg["strategy"]
    win_rate_floor = strat_cfg.get("min_whale_winrate_pct", 40)
    min_resolved_for_filter = strat_cfg.get("min_resolved_for_whale_filter", 10)
    win_stats = signal_log.all_series_stats(days=30)
    series_rows = series_evaluator.overview()
    for row in series_rows:
        stat = win_stats.get(row["series"]) or {"resolved": 0, "win_rate": None}
        row["whale_resolved"] = stat["resolved"]
        row["whale_win_rate"] = stat["win_rate"]
        row["below_winrate_floor"] = (
            stat["resolved"] >= min_resolved_for_filter
            and stat["win_rate"] is not None and stat["win_rate"] < win_rate_floor
        )
        # Gap 10 (docs/config-tuning-data-gaps-2026-08-10.md) - the real
        # margin of error around this series' observed win rate, so
        # "below_winrate_floor" reads as more than a bare true/false: a
        # series barely under the floor with a wide margin (thin n) is a
        # different situation than one clearly under it with a tight one.
        row["whale_win_rate_margin_pts"] = (
            stats_power.margin_of_error_pts(stat["resolved"], stat["win_rate"])
            if stat["win_rate"] is not None else None
        )
    return series_rows
