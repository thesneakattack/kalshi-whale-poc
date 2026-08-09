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
    return conn


def build_prompt(market_detail: dict, context_snapshot: dict) -> str:
    """market_detail: a real, full get_market()/get_event()-shaped dict
    (title, rules_primary, rules_secondary, category, yes_bid_dollars,
    yes_ask_dollars, close_time, volume_24h_fp - the same fields the
    dashboard's own market-detail modal reads, see main.py's
    GET /api/markets/{ticker}/detail). context_snapshot:
    services/ml_feed.py's build_context_snapshot() output - this app's own
    accumulated track record, so the model's judgment is grounded in real
    history here, not just general world knowledge (the whole "work WITH,
    not replace" point of ml_feed.py's design)."""
    whale_track = context_snapshot.get("whale_track_record") or {}
    advisory = context_snapshot.get("advisory") or {}

    return f"""You are an experienced prediction-market analyst evaluating one real, currently-open Kalshi market. Form your own independent estimate of the true probability this market resolves YES - do not simply restate the current market price back as your estimate.

## Market

Title: {market_detail.get("title") or "(no title available)"}
Category: {market_detail.get("category") or "unknown"}
Rules: {market_detail.get("rules_primary") or "(none provided)"}
{market_detail.get("rules_secondary") or ""}

Current market price: {float(market_detail.get("yes_bid_dollars") or market_detail.get("yes_ask_dollars") or 0.5):.2f} (implies the market currently thinks this is that likely)
24h volume: {market_detail.get("volume_24h_fp") or "unknown"}
Closes: {market_detail.get("close_time") or "unknown"}

## This platform's own accumulated context (use this to calibrate, not to substitute for your own judgment)

Whale-signal track record (independent size-based order-flow signals on this platform, not your input): {json.dumps(whale_track) if whale_track else "no data yet"}
Existing rule-based config-tuning recommendations: {json.dumps(advisory.get("recommendations")) if advisory.get("recommendations") else "none yet / not enough data"}

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
    string-matching guess."""
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic  # optional dependency - only imported when actually needed
    except ImportError:
        return None

    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model=model,
            max_tokens=1024,
            tools=[_TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[{"role": "user", "content": build_prompt(market_detail, context_snapshot)}],
        )
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == _TOOL_NAME:
                result = dict(block.input)
                result["estimated_probability"] = min(max(float(result["estimated_probability"]), 0.0), 1.0)
                result["confidence"] = min(max(float(result["confidence"]), 0.0), 1.0)
                return result
        return None  # model didn't use the tool - treat as unavailable, don't guess
    except Exception:
        return None


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
