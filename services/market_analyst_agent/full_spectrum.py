"""\"Feed the Analyst\" full-spectrum scan (Item 3C, 2026-08-10) - a review
across all config/history/whale-watching data, suggestion->confirm->apply,
same as the other two modes. See this package's __init__.py for the full
module-level context.

Direct request: a full-spectrum analysis "across all config/history/
whale-watching data," suggestion->confirm->apply, same as the other two
modes. Unlike the single-market/per-series modes, this can suggest a
change to ANY config field, not just one fixed path - a materially wider
blast radius, so main.py's conversion step (unlike 3B's) validates every
raw suggestion against the live config (must be an EXISTING section.field,
never inventing a new path; the two fields already protected from generic
config edits - kalshi_account.trading_enabled/advisory.auto_apply_enabled -
stay protected here too) before anything gets persisted as appliable.
Given no cost/latency numbers exist anywhere for this agent and this
prompt is materially bigger than the other two modes (aggregated
platform-wide data, not one market/series), the frontend gates the
trigger behind a confirm() dialog disclosing what's being sent - matches
the severity of Advisory's existing apply-confirmation, not the typed
phrase reserved specifically for real-money trading.
"""
import json
import logging
import os
import time
import uuid

from services.market_analyst_agent._db import _connect

logger = logging.getLogger(__name__)

_FULL_SPECTRUM_TOOL_NAME = "record_full_spectrum_analysis"
_FULL_SPECTRUM_TOOL_SCHEMA = {
    "name": _FULL_SPECTRUM_TOOL_NAME,
    "description": (
        "Record your full-spectrum analysis of this trading platform's overall performance across "
        "every strategy, config section, and whale-signal source, and any concrete config changes "
        "you'd suggest. An empty suggestions list is a legitimate, useful answer when nothing stands "
        "out as worth changing."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "3-6 sentences of plain-English assessment of overall platform "
                                "performance - what's working, what isn't, and why.",
            },
            "suggestions": {
                "type": "array",
                "description": "Zero or more suggested config changes, each a real, existing "
                                "config field - never invent a new one.",
                "items": {
                    "type": "object",
                    "properties": {
                        "config_path": {
                            "type": "string",
                            "description": "Dotted path to an EXISTING config field, e.g. "
                                            "'strategy.entry_threshold' or 'risk.max_daily_loss_pct'.",
                        },
                        "suggested_value": {
                            "type": ["number", "string", "boolean", "array"],
                            "description": "The new value, matching the field's existing type.",
                        },
                        "rationale": {"type": "string"},
                    },
                    "required": ["config_path", "suggested_value", "rationale"],
                },
            },
        },
        "required": ["summary", "suggestions"],
    },
}


def build_full_spectrum_prompt(context: dict) -> str:
    """context: assembled by main.py's _build_full_spectrum_context() -
    aggregated/summarized rollups (compute_summary()-style), never raw
    per-trade rows, so prompt size stays bounded regardless of how much
    history has accumulated - the same reasoning trade_analytics.
    compute_summary() vs. build_trade_history() already embodies for the
    History tab's own aggregate cards."""
    return f"""You are an experienced prediction-market trading analyst performing a full-spectrum review of this entire paper-trading platform - every strategy, its live config, and its accumulated real track record. This is a deeper, broader review than analyzing one market or one series.

## Live configuration (all sections)

{json.dumps(context.get("config"), default=str)}

## Whale-follow strategy performance (all-time, aggregated)

{json.dumps(context.get("trade_summary"))}

## Performance by config variant (each distinct strategy.* config ever run)

{json.dumps(context.get("variant_summaries"))}

## Whale-signal track record (independent order-flow accuracy, last 30 days)

{json.dumps(context.get("whale_track_record"))}

## Busiest series' whale-signal breakdown (top 10 by trade volume, not exhaustive)

{json.dumps(context.get("per_series_whale_breakdown"))}

## Existing rule-based suggestions (this platform's own deterministic advisory engine)

{json.dumps(context.get("advisory_recommendations")) or "none currently"}

## Rejected-candidate counterfactuals (per entry/discovery gate, what would have happened if a rejected candidate had been let through)

{json.dumps(context["rejected_candidate_gates"]) if context.get("rejected_candidate_gates") else "none logged yet"}

## Performance by category (Politics, Sports, Crypto, etc. - whale-follow strategy)

{json.dumps(context["regime_by_category"]) if context.get("regime_by_category") else "not enough categorized trades yet"}

## Performance by hour of day, UTC (whale-follow strategy)

{json.dumps(context["regime_by_hour"]) if context.get("regime_by_hour") else "not enough trades yet"}

## Recent applied config changes (most recent 20, any source)

{json.dumps(context.get("recent_applied_changes"))}

## Current portfolio state

{json.dumps(context.get("portfolio"))}

## Task

Call {_FULL_SPECTRUM_TOOL_NAME} with a plain-English assessment of overall platform performance and any concrete config changes you'd suggest, weighing this against everything above - the existing rule-based suggestions, cross-variant performance, whale-signal accuracy, and recent changes already tried. Only suggest a change to a config field you can see actually exists in the configuration above. Be honest about uncertainty - if the data doesn't clearly support a change, say so and suggest nothing."""


async def analyze_full_spectrum(context: dict, model: str, api_key: str | None = None) -> dict | None:
    """Same gating/error-handling shape as per_market.analyze_market()/
    per_series.analyze_series() above. Returns {"summary": str,
    "suggestions": [{"config_path", "suggested_value", "rationale"}, ...]} -
    main.py validates/converts each raw suggestion into this app's unified
    suggestion shape (needs the live config to check the path actually
    exists and compute current_value), not done here so this function stays
    a pure LLM-call wrapper."""
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic
    except ImportError:
        return None

    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model=model,
            max_tokens=2048,
            tools=[_FULL_SPECTRUM_TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": _FULL_SPECTRUM_TOOL_NAME},
            messages=[{"role": "user", "content": build_full_spectrum_prompt(context)}],
        )
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == _FULL_SPECTRUM_TOOL_NAME:
                result = dict(block.input)
                result["suggestions"] = result.get("suggestions") or []
                return result
        return None
    except Exception as e:
        logger.exception("analyze_full_spectrum failed for this call")
        return None


def record_full_spectrum_analysis(
    summary: str, suggestions: list[dict], model: str, analyzed_at: float | None = None,
) -> str:
    """suggestions are already fully materialized/validated (main.py's
    unified {config_path, current_value, suggested_value, id, rationale}
    shape) by the time this is called - same reasoning as
    per_series.record_series_analysis above."""
    analyzed_at = analyzed_at if analyzed_at is not None else time.time()
    analysis_id = str(uuid.uuid4())[:8]
    with _connect() as conn:
        conn.execute(
            "INSERT INTO full_spectrum_analyses (id, summary, suggestions_json, model, analyzed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (analysis_id, summary, json.dumps(suggestions), model, analyzed_at),
        )
    return analysis_id


def get_full_spectrum_analysis(analysis_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, summary, suggestions_json, model, analyzed_at FROM full_spectrum_analyses WHERE id = ?",
            (analysis_id,),
        ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "summary": row[1], "suggestions": json.loads(row[2]), "model": row[3], "analyzed_at": row[4]}


def last_full_spectrum_analyzed_at() -> float | None:
    """Mirrors per_market.last_analyzed_at()/per_series.
    last_series_analyzed_at() - there's only ever one "subject" for this
    mode (the whole platform), so no key to scope by."""
    with _connect() as conn:
        row = conn.execute("SELECT MAX(analyzed_at) FROM full_spectrum_analyses").fetchone()
    return row[0] if row and row[0] is not None else None


def recent_full_spectrum_analyses(limit: int = 10, offset: int = 0) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, summary, suggestions_json, model, analyzed_at FROM full_spectrum_analyses "
            "ORDER BY analyzed_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [
        {"id": r[0], "summary": r[1], "suggestions": json.loads(r[2]), "model": r[3], "analyzed_at": r[4]}
        for r in rows
    ]
