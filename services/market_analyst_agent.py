"""
An LLM-based "doctorate-level prediction market trader" agent - direct
request (2026-08-09), following the research and design laid out in
docs/prediction-market-strategy-alignment-plan.md Part 3. Distinct in kind
from every other strategy in this app: services/whale_simulator.py and
services/market_strategy.py both score a candidate with fixed arithmetic
formulas; this one reads a market's actual title/rules/category and this
app's own accumulated real track record, and asks an LLM to form an
independent probability estimate the way a human analyst would - not
trained on this app's historical data (there isn't enough of it yet, see
services/confidence_calibration.py's and services/advisory_engine.py's own
"rule-based, not ML" reasoning, which applies with even more force to
actually training a model), reasoning from first principles and context per
market instead.

Deliberately advisory-only, matching the "prove it, then promote it"
pattern already established twice in this codebase (advisory_engine's
manual-apply-with-audit-trail design, confidence_calibration's read-only-v1
scope): this module never calls create_order, never opens a paper position,
and nothing here is wired into strategy_engine.evaluate()'s actual trade
decision. Its output is a new, visible, loggable opinion sitting alongside
the existing whale-flow and momentum signals - promoting it into an actual
decision input is a distinct, explicit, later step once there's a real
track record, not this one.

Gated behind two independent switches, both required: market_analyst.enabled
(config/settings.yaml, default false) AND a real ANTHROPIC_API_KEY in .env -
same "every credential is optional, everything degrades honestly" pattern
this app already uses for WHALE_WATCHER_*/KALSHI_*. With either missing,
analyze_market() returns None rather than raising - a caller can always
attempt a call and get a clean "not available" signal back.
"""
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "market_analyst.db"

_TOOL_NAME = "record_market_analysis"
_TOOL_SCHEMA = {
    "name": _TOOL_NAME,
    "description": (
        "Record your independent analysis of this prediction market. "
        "estimated_probability is YOUR OWN estimate of the true probability "
        "the market resolves YES, informed by the market's title/rules/"
        "category and the account context provided - not a restatement of "
        "the current market price."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "estimated_probability": {
                "type": "number", "minimum": 0.0, "maximum": 1.0,
                "description": "Your own probability estimate that this market resolves YES, 0.0-1.0.",
            },
            "confidence": {
                "type": "number", "minimum": 0.0, "maximum": 1.0,
                "description": "How confident you are in that estimate itself, 0.0-1.0 - low if the "
                                "market's rules are ambiguous, the outcome is genuinely uncertain even "
                                "with good information, or you have little real basis to differ from "
                                "the current price.",
            },
            "reasoning": {
                "type": "string",
                "description": "2-4 sentences of plain-English reasoning a human could evaluate - name "
                                "the specific factors that moved your estimate away from (or confirmed) "
                                "the current market price.",
            },
        },
        "required": ["estimated_probability", "confidence", "reasoning"],
    },
}


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS analyses (
            id TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            series TEXT NOT NULL,
            market_price REAL NOT NULL,
            estimated_probability REAL NOT NULL,
            llm_confidence REAL NOT NULL,
            reasoning TEXT NOT NULL,
            model TEXT NOT NULL,
            analyzed_at REAL NOT NULL,
            resolved INTEGER NOT NULL DEFAULT 0,
            correct INTEGER,
            resolved_at REAL
        )
        """
    )
    # Per-series analysis mode (Item 3B, 2026-08-10) - a separate table
    # rather than shoehorning series rows into `analyses` above: that
    # table's schema is tightly single-market-probability-shaped
    # (estimated_probability/llm_confidence/etc. are all NOT NULL, and
    # SQLite can't relax a NOT NULL constraint via an additive ALTER TABLE
    # anyway), whereas a series analysis has no probability estimate at
    # all - just a summary and zero or more config-change suggestions. A
    # new table is itself an additive schema change (CLAUDE.md), not a
    # drop-and-recreate of the existing one.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS series_analyses (
            id TEXT PRIMARY KEY,
            series TEXT NOT NULL,
            summary TEXT NOT NULL,
            suggestions_json TEXT NOT NULL,
            model TEXT NOT NULL,
            analyzed_at REAL NOT NULL
        )
        """
    )
    # "Feed the Analyst" full-spectrum scan (Item 3C, 2026-08-10) - same
    # shape as series_analyses (a summary + a list of already-validated,
    # unified-shape suggestions), just with no series/ticker subject at
    # all, so it gets its own table rather than a magic sentinel value in
    # series_analyses' NOT NULL `series` column.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS full_spectrum_analyses (
            id TEXT PRIMARY KEY,
            summary TEXT NOT NULL,
            suggestions_json TEXT NOT NULL,
            model TEXT NOT NULL,
            analyzed_at REAL NOT NULL
        )
        """
    )
    return conn


def build_prompt(market_detail: dict, context_snapshot: dict, own_track_record: dict | None = None) -> str:
    """market_detail: a real, full get_market()/get_event()-shaped dict
    (title, rules_primary, rules_secondary, category, yes_bid_dollars,
    yes_ask_dollars, close_time, volume_24h_fp, open_interest_fp,
    liquidity_dollars, last_price_dollars - the same fields the dashboard's
    own market-detail modal reads via main.py's _slim_detail_market(), read
    here in their raw (unslimmed) form since that's what
    client.get_market() actually returns. Audit finding (2026-08-09): the
    last three used to be fetched into market_detail and then silently
    dropped before reaching this prompt - the analyst was reasoning with
    strictly less context than a human sees on the same market's own detail
    modal, at zero extra API cost to fix. context_snapshot:
    services/ml_feed.py's build_context_snapshot() output - this app's own
    accumulated track record, so the model's judgment is grounded in real
    history here, not just general world knowledge (the whole "work WITH,
    not replace" point of ml_feed.py's design). own_track_record: this
    agent's own stats() output (hit rate + Brier score on its own past
    calls), passed in explicitly rather than read from the DB in here so
    this function stays pure/testable - the DB read happens once in
    analyze_market() below. Direct request: let the agent self-calibrate
    off its own accumulated accuracy, not just other subsystems' data,
    without spending tokens replaying its own past reasoning verbatim."""
    whale_track = context_snapshot.get("whale_track_record") or {}
    advisory = context_snapshot.get("advisory") or {}
    own_track_record = own_track_record or {}
    if own_track_record.get("resolved"):
        own_track_line = (
            f"{own_track_record['resolved']} of your own past estimates have resolved "
            f"(last {own_track_record.get('window_days', 30)} days): {own_track_record.get('hit_rate_pct')}% "
            f"hit rate, Brier score {own_track_record.get('brier_score')} (0 = perfect, 0.25 = "
            f"coin-flip, lower is better). If your Brier score is well above 0.25, you have been "
            f"overconfident on average - weight that into your confidence score below, not just this "
            f"market's own uncertainty."
        )
    else:
        own_track_line = "No resolved estimates yet - no self-calibration signal available."

    return f"""You are an experienced prediction-market analyst evaluating one real, currently-open Kalshi market. Form your own independent estimate of the true probability this market resolves YES - do not simply restate the current market price back as your estimate.

## Market

Title: {market_detail.get("title") or "(no title available)"}
Category: {market_detail.get("category") or "unknown"}
Rules: {market_detail.get("rules_primary") or "(none provided)"}
{market_detail.get("rules_secondary") or ""}

Current market price: {float(market_detail.get("yes_bid_dollars") or market_detail.get("yes_ask_dollars") or 0.5):.2f} (implies the market currently thinks this is that likely)
Last trade price: {market_detail.get("last_price_dollars") or "unknown"}
24h volume: {market_detail.get("volume_24h_fp") or "unknown"}
Open interest: {market_detail.get("open_interest_fp") or "unknown"}
Liquidity: {market_detail.get("liquidity_dollars") or "unknown"}
Closes: {market_detail.get("close_time") or "unknown"}

## This platform's own accumulated context (use this to calibrate, not to substitute for your own judgment)

Whale-signal track record (independent size-based order-flow signals on this platform, not your input): {json.dumps(whale_track) if whale_track else "no data yet"}
Existing rule-based config-tuning recommendations: {json.dumps(advisory.get("recommendations")) if advisory.get("recommendations") else "none yet / not enough data"}

## Your own track record

{own_track_line}

## Task

Call {_TOOL_NAME} with your own probability estimate, your confidence in that estimate, and brief reasoning naming the specific factors that moved your estimate away from (or confirmed) the current market price. Be honest about uncertainty - a low confidence score is a legitimate, useful answer when the rules are ambiguous or you have no real edge over the market's own price."""


async def analyze_market(
    market_detail: dict, context_snapshot: dict, model: str, api_key: str | None = None,
) -> dict | None:
    """Returns {"estimated_probability", "confidence", "reasoning"} or None
    if no API key is configured or the call fails for any reason - never
    raises, matching this app's "degrade honestly, don't crash the trading
    loop over an optional feature" pattern used throughout (e.g. real
    account fetch failures, live-status lookups). Forces a single tool call
    (_TOOL_SCHEMA) rather than parsing free-text JSON out of a response -
    the reliable way to get structured output from this model, not a
    string-matching guess. Reads this agent's own stats() (a single indexed
    query against its own small table, not another LLM call) and threads it
    into build_prompt() so each new call can self-calibrate off its own
    accumulated accuracy - direct request, so the agent "leverages its own
    history" instead of reasoning from a blank slate every time."""
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic  # optional dependency - only imported when actually needed
    except ImportError:
        return None

    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        own_track_record = stats(days=30)
        response = await client.messages.create(
            model=model,
            max_tokens=1024,
            tools=[_TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[{"role": "user", "content": build_prompt(market_detail, context_snapshot, own_track_record)}],
        )
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == _TOOL_NAME:
                result = dict(block.input)
                result["estimated_probability"] = min(max(float(result["estimated_probability"]), 0.0), 1.0)
                result["confidence"] = min(max(float(result["confidence"]), 0.0), 1.0)
                return result
        return None  # model didn't use the tool - treat as unavailable, don't guess
    except Exception as e:
        # Data-robustness audit finding (2026-08-10): this used to be a bare
        # `except Exception: return None` - the real error was discarded
        # entirely, and main.py's caller returned a message saying "see
        # server logs" even though no logging framework exists anywhere in
        # this app (confirmed by repo-wide grep). stdout is captured by
        # `ddev logs -s fastapi` per this project's own documented
        # workflow, so that message is now actually true instead of
        # pointing at logs that don't exist.
        print(f"[market_analyst_agent] analyze_market failed for this call: {e!r}")
        return None


# --- Per-series analysis mode (Item 3B, 2026-08-10) ---------------------------
# Direct request: the agent should be able to "analyze a whole series," not
# just one market - natural home is a button on the series-evaluator log
# panel (Item 1), since it already lists every series with its own status.
# A series analysis judges whale-signal quality and closed-trade performance
# for an entire series, not one market's own probability - a different kind
# of question, so it gets its own tool schema rather than overloading
# _TOOL_SCHEMA above. The only thing this mode can suggest changing is
# strategy.excluded_series membership: this app's per-field suggestion
# apply mechanism (services/advisory_engine.py's generalized
# `section.field` split) only ever handles a single flat scalar per
# config_path, and min_notional_usd_by_series is a nested dict keyed by
# series - applying a change to one series' entry there without clobbering
# every other series' override would need its own bespoke merge logic.
# Scoped out of this pass deliberately, not silently: min_notional_usd_by_
# series is still given to the model as read-only context (so it can reason
# about whether the current threshold looks right), it just can't act on it
# yet - a natural, disclosed follow-on once a real need for it shows up.
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
    min_notional_usd_by_series override (if any), and its
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
Per-series whale-notional override: {series_ctx.get("min_notional_override") if series_ctx.get("min_notional_override") is not None else "none (uses the account-wide default)"}
Series-worthiness gate (services/series_evaluator.py) verdict: {evaluator_line}

## Whale-signal track record for this series (last 30 days)

{json.dumps(whale) if whale.get("total_signals") else "no whale signals logged yet for this series"}

## Closed trades on this series (paper account, all-time)

{json.dumps(trades) if trades.get("total_closed") else "no closed trades yet on this series"}

## Task

Call {_SERIES_TOOL_NAME} with a plain-English summary of whether this series looks worth continuing to watch, and any suggested change to its exclusion status. Be honest about thin evidence - if there isn't enough real data yet to judge this series one way or the other, say so in the summary and suggest nothing."""


async def analyze_series(series_ctx: dict, model: str, api_key: str | None = None) -> dict | None:
    """Same gating/error-handling shape as analyze_market() above (no key
    -> None, model declines the tool -> None, any exception caught and
    logged, never raised). Returns {"summary": str, "suggestions":
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
        print(f"[market_analyst_agent] analyze_series failed for this call: {e!r}")
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
    """Mirrors last_analyzed_at() above - the per-series equivalent used
    for this mode's own reanalyze cooldown."""
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


# --- "Feed the Analyst" full-spectrum scan (Item 3C, 2026-08-10) -------------
# Direct request: a full-spectrum analysis "across all config/history/
# whale-watching data," suggestion->confirm->apply, same as the other two
# modes. Unlike the single-market/per-series modes, this can suggest a
# change to ANY config field, not just one fixed path - a materially wider
# blast radius, so main.py's conversion step (unlike 3B's) validates every
# raw suggestion against the live config (must be an EXISTING section.field,
# never inventing a new path; the two fields already protected from generic
# config edits - kalshi_account.trading_enabled/advisory.auto_apply_enabled -
# stay protected here too) before anything gets persisted as appliable.
# Given no cost/latency numbers exist anywhere for this agent and this
# prompt is materially bigger than the other two modes (aggregated
# platform-wide data, not one market/series), the frontend gates the
# trigger behind a confirm() dialog disclosing what's being sent - matches
# the severity of Advisory's existing apply-confirmation, not the typed
# phrase reserved specifically for real-money trading.
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

## Market-native strategy performance (all-time, aggregated)

{json.dumps(context.get("market_strategy_summary"))}

## Performance by config variant (each distinct strategy.* config ever run)

{json.dumps(context.get("variant_summaries"))}

## Whale-signal track record (independent order-flow accuracy, last 30 days)

{json.dumps(context.get("whale_track_record"))}

## Busiest series' whale-signal breakdown (top 10 by trade volume, not exhaustive)

{json.dumps(context.get("per_series_whale_breakdown"))}

## Existing rule-based suggestions (this platform's own deterministic advisory engine)

{json.dumps(context.get("advisory_recommendations")) or "none currently"}

## Recent applied config changes (most recent 20, any source)

{json.dumps(context.get("recent_applied_changes"))}

## Current portfolio state

{json.dumps(context.get("portfolio"))}

## Task

Call {_FULL_SPECTRUM_TOOL_NAME} with a plain-English assessment of overall platform performance and any concrete config changes you'd suggest, weighing this against everything above - the existing rule-based suggestions, cross-variant performance, whale-signal accuracy, and recent changes already tried. Only suggest a change to a config field you can see actually exists in the configuration above. Be honest about uncertainty - if the data doesn't clearly support a change, say so and suggest nothing."""


async def analyze_full_spectrum(context: dict, model: str, api_key: str | None = None) -> dict | None:
    """Same gating/error-handling shape as analyze_market()/analyze_series()
    above. Returns {"summary": str, "suggestions": [{"config_path",
    "suggested_value", "rationale"}, ...]} - main.py validates/converts
    each raw suggestion into this app's unified suggestion shape (needs the
    live config to check the path actually exists and compute current_value),
    not done here so this function stays a pure LLM-call wrapper."""
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
        print(f"[market_analyst_agent] analyze_full_spectrum failed for this call: {e!r}")
        return None


def record_full_spectrum_analysis(
    summary: str, suggestions: list[dict], model: str, analyzed_at: float | None = None,
) -> str:
    """suggestions are already fully materialized/validated (main.py's
    unified {config_path, current_value, suggested_value, id, rationale}
    shape) by the time this is called - same reasoning as
    record_series_analysis above."""
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
    """Mirrors last_analyzed_at()/last_series_analyzed_at() above - there's
    only ever one "subject" for this mode (the whole platform), so no key
    to scope by."""
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


def analyst_lean(ticker: str, max_age_sec: float = 86400) -> float | None:
    """Most recent estimated_probability for this ticker if one exists and
    is fresh enough, else None - a single indexed SQLite read, no LLM call.
    Direct request (2026-08-09): "inform the heuristics engines... without
    consuming AI tokens" - this is that wiring. services/whale_simulator.py's
    composite_confidence_breakdown() turns this into its own analyst_factor
    (0.5/neutral when None, same idiom as agreement_factor/trend_factor),
    so a market someone has manually analyzed keeps nudging the cheap,
    automatic whale-confidence score afterward, at zero extra API cost -
    resolved or not, since the estimate itself doesn't stop being the
    model's honest read just because the market hasn't settled yet. 24h
    default freshness window: the event being estimated is far more stable
    than the market's own price, so this doesn't need to be as tight as
    reanalyze_cooldown_sec, just not stale enough to be estimating a
    different market state entirely (e.g. post-news, near close)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT estimated_probability, analyzed_at FROM analyses WHERE ticker = ? "
            "ORDER BY analyzed_at DESC LIMIT 1",
            (ticker,),
        ).fetchone()
    if row is None:
        return None
    estimated_probability, analyzed_at = row
    if (time.time() - analyzed_at) > max_age_sec:
        return None
    return estimated_probability


def record_analysis(
    ticker: str, series: str, market_price: float, estimated_probability: float,
    llm_confidence: float, reasoning: str, model: str, analyzed_at: float | None = None,
) -> str:
    analyzed_at = analyzed_at if analyzed_at is not None else time.time()
    analysis_id = str(uuid.uuid4())[:8]
    with _connect() as conn:
        conn.execute(
            "INSERT INTO analyses (id, ticker, series, market_price, estimated_probability, "
            "llm_confidence, reasoning, model, analyzed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (analysis_id, ticker, series, market_price, estimated_probability,
             llm_confidence, reasoning, model, analyzed_at),
        )
    return analysis_id


def last_analyzed_at(ticker: str) -> float | None:
    """For the caller's own re-analysis cooldown - most recent analyzed_at
    for this ticker, across resolved or not, or None if never analyzed."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT MAX(analyzed_at) FROM analyses WHERE ticker = ?", (ticker,),
        ).fetchone()
    return row[0] if row and row[0] is not None else None


def resolve_from_market_results(market_results: dict) -> int:
    """market_results: the same {ticker: "yes"/"no"/""/None} mapping
    strategy_engine.check_exits already receives every tick from main.py's
    already-fetched markets - zero new API calls needed to resolve past
    analyses, unlike signal_log's own fresh-fetch resolution check (an
    older pattern that predates this convenience dict's existence, see
    main.py's _check_signal_resolutions). "correct" is whether the side the
    estimate leaned toward (>0.5 = yes, <=0.5 = no) matched the real
    outcome - the analyst's calibration (Brier score, in stats() below) is
    the more informative number for its own accuracy specifically. Returns
    how many analyses were resolved this call."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, ticker, estimated_probability FROM analyses WHERE resolved = 0",
        ).fetchall()
        resolved_count = 0
        now = time.time()
        for analysis_id, ticker, estimated_probability in rows:
            result = (market_results.get(ticker) or "").strip().lower()
            if result not in ("yes", "no"):
                continue
            predicted_side = "yes" if estimated_probability > 0.5 else "no"
            conn.execute(
                "UPDATE analyses SET resolved = 1, correct = ?, resolved_at = ? WHERE id = ?",
                (1 if predicted_side == result else 0, now, analysis_id),
            )
            resolved_count += 1
    return resolved_count


def stats(days: int = 30) -> dict:
    """total/resolved counts, hit-rate (did the implied side win), and a
    Brier score (mean squared error between estimated_probability and the
    real 0/1 outcome - the standard proper-scoring-rule metric for a
    probability estimate specifically, lower is better, 0 = perfect,
    0.25 = no-better-than-a-coin-flip on average). Deliberately reports
    both, not just hit-rate: the same hit-rate-vs-calibration distinction
    this app's own research surfaced (docs/prediction-markets-research-
    reference.md Part 1.4, the Clinton & Huang vs. Kalshi dispute) applies
    to grading this agent's own track record, not just the platforms it
    was studied on."""
    since = time.time() - days * 86400
    with _connect() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM analyses WHERE analyzed_at >= ?", (since,)
        ).fetchone()[0]
        resolved_rows = conn.execute(
            "SELECT estimated_probability, correct FROM analyses WHERE analyzed_at >= ? AND resolved = 1",
            (since,),
        ).fetchall()
    resolved_count = len(resolved_rows)
    hit_rate = None
    brier_score = None
    if resolved_count:
        hit_rate = round(sum(1 for _, correct in resolved_rows if correct) / resolved_count * 100, 1)
        # outcome is 1.0 if the side the estimate leaned toward actually won
        # (i.e. "correct"), 0.0 otherwise - NOT literally "did yes happen",
        # since Brier score here is grading the estimate's own confidence
        # calibration around whichever side it leaned toward.
        squared_errors = [
            (prob if prob > 0.5 else (1 - prob)) - (1.0 if correct else 0.0)
            for prob, correct in resolved_rows
        ]
        brier_score = round(sum(e ** 2 for e in squared_errors) / resolved_count, 3)
    return {
        "window_days": days,
        "total_analyses": total,
        "resolved": resolved_count,
        "hit_rate_pct": hit_rate,
        "brier_score": brier_score,
    }


def recent(limit: int = 25, offset: int = 0, resolved_only: bool = False) -> list[dict]:
    where = "WHERE resolved = 1" if resolved_only else ""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT id, ticker, series, market_price, estimated_probability, llm_confidence, "
            f"reasoning, model, analyzed_at, resolved, correct, resolved_at FROM analyses {where} "
            f"ORDER BY analyzed_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    cols = ("id", "ticker", "series", "market_price", "estimated_probability", "llm_confidence",
            "reasoning", "model", "analyzed_at", "resolved", "correct", "resolved_at")
    return [dict(zip(cols, r)) for r in rows]


def total_count(resolved_only: bool = False) -> int:
    where = "WHERE resolved = 1" if resolved_only else ""
    with _connect() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM analyses {where}").fetchone()[0]


def clear_all():
    with _connect() as conn:
        conn.execute("DELETE FROM analyses")
        conn.execute("DELETE FROM series_analyses")
        conn.execute("DELETE FROM full_spectrum_analyses")
