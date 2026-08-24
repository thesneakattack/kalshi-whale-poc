"""Per-series analysis mode (Item 3B, 2026-08-10) - judges a whole market
series' whale-signal quality and closed-trade performance, rather than one
market's own probability. See this package's __init__.py for the full
module-level context.

Direct request: the agent should be able to "analyze a whole series," not
just one market - natural home is a button on the series-evaluator log
panel (Item 1), since it already lists every series with its own status.
A series analysis judges whale-signal quality and closed-trade performance
for an entire series, not one market's own probability - a different kind
of question, so it gets its own tool schema rather than overloading
per_market's _TOOL_SCHEMA. The only thing this mode can suggest changing is
strategy.excluded_series membership: this app's per-field suggestion
apply mechanism (services/advisory/advisory_engine.py's generalized
`section.field` split) only ever handles a single flat scalar per
config_path, and min_contracts_by_series is a nested dict keyed by
series - applying a change to one series' entry there without clobbering
every other series' override would need its own bespoke merge logic.
Scoped out of this pass deliberately, not silently: min_contracts_by_
series is still given to the model as read-only context (so it can reason
about whether the current threshold looks right), it just can't act on it
yet - a natural, disclosed follow-on once a real need for it shows up.
"""
import json
import logging
import os
import time
import uuid

from services.market_analyst_agent._db import _connect

logger = logging.getLogger(__name__)

_SERIES_TOOL_NAME = "record_series_analysis"
_SERIES_TOOL_SCHEMA = {
    "name": _SERIES_TOOL_NAME,
    "description": (
        "Record your analysis of this market series' whale-signal quality and closed-trade "
        "performance. Suggest changing its exclusion status only if the evidence genuinely "
        "warrants it - an empty suggestions list is a legitimate, useful answer when the series "
        "looks fine as-is."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "2-4 sentences of plain-English reasoning about whether this "
                                "series' whale signals and closed trades look worth continuing "
                                "to watch.",
            },
            "suggestions": {
                "type": "array",
                "description": "Zero or more suggested changes to this series' membership in "
                                "strategy.excluded_series.",
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string", "enum": ["exclude", "include"],
                            "description": "'exclude' to add this series to strategy."
                                            "excluded_series (stop trading it), 'include' to "
                                            "remove it (resume trading it).",
                        },
                        "rationale": {"type": "string"},
                    },
                    "required": ["action", "rationale"],
                },
            },
        },
        "required": ["summary", "suggestions"],
    },
}


def build_series_prompt(series: str, series_ctx: dict) -> str:
    """series_ctx: assembled by main.py's _build_series_context() - real
    whale-signal stats (services/signal_log.series_stats), this series'
    own closed-trade summary (services/trade_analytics.compute_summary,
    scoped to trades on tickers under this series), its current
    strategy.excluded_series membership and whale_watcher_kalshi.
    min_contracts_by_series override (if any), and its
    services/series_evaluator.py verdict (if it's ever been evaluated)."""
    whale = series_ctx.get("whale_stats") or {}
    trades = series_ctx.get("trade_summary") or {}
    evaluator = series_ctx.get("evaluator_status")
    evaluator_line = (
        f"status={evaluator['status']}, trades_observed={evaluator['trades_observed']}, "
        f"strike_count={evaluator['strike_count']}"
        if evaluator else "never evaluated by the series-worthiness gate"
    )
    return f"""You are an experienced prediction-market analyst evaluating one entire market series (a recurring family of related markets, e.g. all matches in a tournament or all games in a league) on a paper-trading platform that follows real-money whale order flow.

## Series: {series}

Currently excluded from trading: {series_ctx.get("currently_excluded")}
Per-series whale contract-count override: {series_ctx.get("min_contracts_override") if series_ctx.get("min_contracts_override") is not None else "none (uses the account-wide default)"}
Series-worthiness gate (services/series_evaluator.py) verdict: {evaluator_line}

## Whale-signal track record for this series (last 30 days)

{json.dumps(whale) if whale.get("total_signals") else "no whale signals logged yet for this series"}

## Closed trades on this series (paper account, all-time)

{json.dumps(trades) if trades.get("total_closed") else "no closed trades yet on this series"}

## Task

Call {_SERIES_TOOL_NAME} with a plain-English summary of whether this series looks worth continuing to watch, and any suggested change to its exclusion status. Be honest about thin evidence - if there isn't enough real data yet to judge this series one way or the other, say so in the summary and suggest nothing."""


async def analyze_series(series_ctx: dict, model: str, api_key: str | None = None) -> dict | None:
    """Same gating/error-handling shape as per_market.analyze_market()
    above (no key -> None, model declines the tool -> None, any exception
    caught and logged, never raised). Returns {"summary": str, "suggestions":
    [{"action", "rationale"}, ...]} - main.py converts each raw suggestion
    into this app's unified {config_path, current_value, suggested_value,
    id, rationale} shape (needs the live config to compute the actual
    before/after excluded_series list), not done here so this function
    stays a pure LLM-call wrapper."""
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
            max_tokens=1024,
            tools=[_SERIES_TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": _SERIES_TOOL_NAME},
            messages=[{"role": "user", "content": build_series_prompt(series_ctx["series"], series_ctx)}],
        )
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == _SERIES_TOOL_NAME:
                result = dict(block.input)
                result["suggestions"] = result.get("suggestions") or []
                return result
        return None
    except Exception as e:
        logger.exception("analyze_series failed for this call")
        return None


def record_series_analysis(
    series: str, summary: str, suggestions: list[dict], model: str, analyzed_at: float | None = None,
) -> str:
    """suggestions are already fully materialized (main.py's unified
    {config_path, current_value, suggested_value, id, rationale} shape,
    not the model's raw {"action", "rationale"}) by the time this is
    called - persisted as-is so the apply route can look one up by its
    own id later without needing to re-derive it from a raw model output
    that isn't deterministically recomputable the way a rule-based
    suggestion is."""
    analyzed_at = analyzed_at if analyzed_at is not None else time.time()
    analysis_id = str(uuid.uuid4())[:8]
    with _connect() as conn:
        conn.execute(
            "INSERT INTO series_analyses (id, series, summary, suggestions_json, model, analyzed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (analysis_id, series, summary, json.dumps(suggestions), model, analyzed_at),
        )
    return analysis_id


def get_series_analysis(analysis_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, series, summary, suggestions_json, model, analyzed_at FROM series_analyses WHERE id = ?",
            (analysis_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row[0], "series": row[1], "summary": row[2],
        "suggestions": json.loads(row[3]), "model": row[4], "analyzed_at": row[5],
    }


def last_series_analyzed_at(series: str) -> float | None:
    """Mirrors per_market.last_analyzed_at() - the per-series equivalent
    used for this mode's own reanalyze cooldown."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT MAX(analyzed_at) FROM series_analyses WHERE series = ?", (series,),
        ).fetchone()
    return row[0] if row and row[0] is not None else None


def recent_series_analyses(limit: int = 25, offset: int = 0) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, series, summary, suggestions_json, model, analyzed_at FROM series_analyses "
            "ORDER BY analyzed_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [
        {"id": r[0], "series": r[1], "summary": r[2], "suggestions": json.loads(r[3]), "model": r[4], "analyzed_at": r[5]}
        for r in rows
    ]
