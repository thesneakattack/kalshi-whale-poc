"""
An LLM-based "doctorate-level prediction market trader" agent - direct
request (2026-08-09), following the research and design laid out in
docs/prediction-market-strategy-alignment-plan.md Part 3. Distinct in kind
from every other strategy in this app: services/confidence_scoring.py scores
a candidate with fixed arithmetic formulas; this one reads a market's
actual title/rules/category and this
app's own accumulated real track record, and asks an LLM to form an
independent probability estimate the way a human analyst would - not
trained on this app's historical data (there isn't enough of it yet, see
services/whale_calibration/confidence_calibration.py's and services/advisory/advisory_engine.py's own
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

Split 2026-08-23 (per-module audit, third of three approved passes -
whale_simulator -> confidence_scoring, index_feed -> package, this one) from
one flat services/market_analyst_agent.py into cohesion-based siblings, one
per independent analysis mode, the same shape as the market_watch/ and
index_feed/ splits before it:

- _db.py - the one genuinely shared piece: DB_PATH, _connect() (creates all
  three tables), clear_all() (wipes all three).
- per_market.py - the original single-ticker mode (build_prompt,
  analyze_market, analyst_lean, record_analysis, last_analyzed_at,
  resolve_from_market_results, stats, recent, total_count).
- per_series.py - judges a whole series' whale-signal/closed-trade quality,
  can suggest strategy.excluded_series changes (build_series_prompt,
  analyze_series, record_series_analysis, get_series_analysis,
  last_series_analyzed_at, recent_series_analyses).
- full_spectrum.py - "Feed the Analyst," a platform-wide review that can
  suggest a change to any existing config field (build_full_spectrum_prompt,
  analyze_full_spectrum, record_full_spectrum_analysis,
  get_full_spectrum_analysis, last_full_spectrum_analyzed_at,
  recent_full_spectrum_analyses).

Scope note: services/analytics/README.md previously deferred splitting
this file, bundled with market_analyst_orchestrator.py - but that deferral
was about the *orchestrator's* entanglement with advisory_engine/
series_evaluator (it imports advisory_engine.generate_recommendations
directly, and exposes _series_evaluator_overview_with_crosscheck, which
main.py's trading_loop also imports directly), not about this file's own
internal cohesion. market_analyst_agent.py itself has zero import
relationship with either module - confirmed by full read before this split,
not assumed. This split covers only this file; market_analyst_orchestrator.py
remains untouched, for the still-valid reason.

Every name external callers need is re-exported here, so
`from services import market_analyst_agent` /
`from services.market_analyst_agent import X` keep working exactly as
before the split - no call site outside this package needed to change.

REAL GOTCHA, worth reading before touching this package's own tests
(confirmed directly against this package, not assumed - the same shape this
session's tests/conftest.py fix and the index_feed/ split both already hit):
the re-exports below are name *bindings*, not live references back to each
submodule's own namespace. A function defined in per_market.py/per_series.py/
full_spectrum.py always resolves its own global lookups (DB_PATH, inside
_connect()) against _db.py's module dict - regardless of which import path
was used to *call* it - because DB_PATH itself is only ever read inside
_connect(), and _connect() is defined in _db.py, not in the mode files. So
`monkeypatch.setattr(market_analyst_agent, "DB_PATH", tmp_path)` (patching
this package's own copied binding) does NOT change what _connect() actually
reads/writes; only `monkeypatch.setattr(market_analyst_agent._db, "DB_PATH",
tmp_path)` does. tests/test_trading_gate.py, tests/test_whalewatchers_
kalshi_trade_tape.py, tests/test_strategy_engine.py, and tests/conftest.py
all used to assign/monkeypatch the former; fixed to target `_db` directly as
part of this split.

`logger` is deliberately NOT re-exported here: each mode file defines its
own module-scope logger (the per-file idiom used throughout this codebase),
and re-exporting all three under one name would have the last import
silently shadow the other two. Nothing external reads
`market_analyst_agent.logger` (confirmed by grep) - only `caplog`-based
tests, which capture by record content regardless of which named logger
emitted it.
"""
from services.market_analyst_agent import _db, per_market, per_series, full_spectrum  # noqa: F401
from services.market_analyst_agent._db import DB_PATH, _connect, clear_all  # noqa: F401
from services.market_analyst_agent.per_market import (  # noqa: F401
    _TOOL_NAME, _TOOL_SCHEMA, analyst_lean, analyze_market, build_prompt, last_analyzed_at,
    record_analysis, recent, resolve_from_market_results, stats, total_count,
)
from services.market_analyst_agent.per_series import (  # noqa: F401
    _SERIES_TOOL_NAME, _SERIES_TOOL_SCHEMA, analyze_series, build_series_prompt,
    get_series_analysis, last_series_analyzed_at, record_series_analysis, recent_series_analyses,
)
from services.market_analyst_agent.full_spectrum import (  # noqa: F401
    _FULL_SPECTRUM_TOOL_NAME, _FULL_SPECTRUM_TOOL_SCHEMA, analyze_full_spectrum,
    build_full_spectrum_prompt, get_full_spectrum_analysis, last_full_spectrum_analyzed_at,
    record_full_spectrum_analysis, recent_full_spectrum_analyses,
)
