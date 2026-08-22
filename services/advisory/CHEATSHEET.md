# Advisory module — cheat sheet

Owns: `advisory_engine.py` (rule-based config-tuning suggestion engine -
per-field recommendations pulled directly out of observed trade history,
never a fitted model, see `docs/advisory-engine-plan.md`) + `routes.py`
(`/api/advisory/*`, 6 routes). Split out of `services/analytics/`
2026-08-22 (modularization Phase 3/9), per the already-queued ROADMAP.md
item. `/api/suggestions/decline|undecline|declined` stay in
`services/analytics/routes.py` - shared infrastructure
(`services/suggestion_decisions.py`) used by both this module's
recommendations and market-analyst's own suggestions, not advisory-owned.

## No direct Kalshi API surface

This module never calls Kalshi's API - it works entirely off this app's
own derived data: `services/trade_analytics.py`'s row shape (built from
`broker.trade_log`) and `services/config_performance.py`'s per-trade
config fingerprint. Its one real cross-import among the modules that used
to sit together under `services/analytics/`: `services/regime_analytics.py`
(used by `_category_conditional_recommendations`/
`_series_conditional_recommendations`). Kept as a plain
`from services import regime_analytics`, not dragged into this package -
a legitimate shared low-level dependency, the same way this module already
depends on `trade_analytics`/`signal_log` without those moving either.

## Audit finding: suggestion functions compare win rate, never win rate
paired with cost — the exact gap CLAUDE.md's HARD COMMANDMENT exists to
prevent

Checked directly against the actual suggestion-generating functions while
writing this cheat sheet, not assumed. `_entry_threshold_recommendation`
(the function that decides whether to suggest raising
`strategy.entry_threshold`) buckets trades by confidence and compares
**win rate alone** between buckets — no `cost_basis`, `entry_price`, or
`realized_pnl` term anywhere in it. `_cross_variant_recommendations`
(config-A-vs-config-B) compares `win_rate_pct` alone.
`_category_conditional_recommendations`/`_series_conditional_recommendations`
compare a segment's `win_rate_pct` against the book's overall win rate,
again alone. `grep`-confirmed: every reference to `cost_basis`/
`realized_pnl` in this file lives either inside `_exit_pct_recommendation`
(take-profit/stop-loss sizing, a different question) or inside
`change_effect`/`change_effect_windowed` — the **retrospective** "what
happened after a change was already applied" reporting behind
`GET /api/advisory/applied-changes`, never inside the functions that
decide what to *suggest* in the first place.

**Why this matters, concretely**: CLAUDE.md's HARD COMMANDMENT table shows
win rate and cost move together in this app's real data — the ≥0.95
unit-cost bucket wins 96.3% of the time and *loses* 2.4% per dollar
risked, forever. A confidence-bucket comparison has no way to distinguish
"this bucket wins more because the signal is genuinely better" from "this
bucket wins more because it's mechanically priced into the near-certainty
band" — both look identical to `_entry_threshold_recommendation`, which
would suggest raising the threshold either way once the win-rate gap
clears its significance margin. **Not fixed here** (a real algorithm
change — deciding how to weigh win rate against cost inside every
suggestion function is a judgment call, not a refactor), but this is the
single most important thing a future audit of this module's suggestions
should check: pull a real `entry_threshold` recommendation this module
actually made, and check whether the bucket it favored also had a higher
mean unit cost. If so, the suggestion may be chasing the HARD
COMMANDMENT's explicitly-named trap ("chasing win rate upward past ~85%")
by construction, not by a training-data coincidence.

## Handoff — who calls this module, who it calls

- **Upstream:** `main.py`'s `trading_loop` (`calibration_advisory` phase)
  calls `advisory_engine.generate_recommendations(...)` directly (not just
  through `routes.py`) every tick, gated by `advisory.enabled`.
  `services/analytics/market_analyst_orchestrator.py`'s
  `_analyze_market_uncached` also calls `generate_recommendations(...)`
  directly, to fold the same recommendation set into the market-analyst
  LLM's context snapshot — a real, deliberate cross-module call, not
  incidental coupling.
- **Downstream (real write-back loop):** when `advisory.auto_apply_enabled`
  is on (config-gated, typed-confirmation-phrase protected — `POST
  /api/advisory/auto-apply/enable`), `trading_loop` applies a
  recommendation straight into `config_store` — this module only ever
  returns data, `main.py` is the only place that calls
  `config_store.update()`, matching the module docstring's own "never
  writes config on its own initiative" rule. Ties to the still-open P4
  "move analytics/advisory computation out of the live tick loop" item —
  not re-solved here, just recorded.
- `rec_id(...)` is public (not module-private) specifically because
  `services/market_analyst_agent.py`'s per-series/full-spectrum analysis
  modes reuse the exact same id recipe when converting the LLM's raw
  output into this module's unified suggestion shape.
