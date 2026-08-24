# Analytics module — cheat sheet

Owns: `routes.py` (`/api/suggestions/*` — shared decline/undecline/declined
infrastructure, not advisory-owned; `/api/market-strategy-calibration/*` —
the separate Market-Native strategy's own tuning; `/api/candidate-log/*`,
`/api/cross-strategy/*`, `/api/regime/*` — segmentation/comparison reads;
`/api/market-analyst/*`, `/api/series-evaluator/*`) +
`market_analyst_orchestrator.py` (the one part of this concern that's real
logic, not routing — LLM-based market/series/full-spectrum analysis, with
its own in-flight guard sets). This is what's left after advisory
(`services/advisory/`) and whale calibration (`services/whale_calibration/`)
were split out 2026-08-22, per the ROADMAP.md item queued the same day —
see those modules' own `CHEATSHEET.md` for what moved and why.
`services/diagnostics.py`/`series_watcher.py`/`settlement_edge.py`/
`config_performance.py` are already clean and stay flat for now.

**Update, 2026-08-23**: `market_analyst_agent.py` alone has since shipped
as its own `services/market_analyst_agent/` package (per-market/per-series/
full-spectrum modes split into cohesion-based siblings, shared DB layer in
`_db.py` — see that package's own `__init__.py` docstring). This was
narrower than the combined split originally deferred below: checked
directly before doing it, `market_analyst_agent.py` itself has zero import
relationship with `advisory_engine`/`series_evaluator` — the entanglement
that motivated the deferral lives entirely in `market_analyst_orchestrator.py`,
which is untouched by this split and remains deferred for the reason
below.

**Still deferred**: `market_analyst_orchestrator.py` (431 lines) is a real
split candidate too, but checked directly before deciding not to split it:
it imports `advisory_engine.generate_recommendations` directly (to build
LLM context) and exposes `_series_evaluator_overview_with_crosscheck`,
which `main.py`'s `trading_loop` also imports directly — genuine
entanglement with advisory/series-evaluator territory that would touch
both concerns in one pass.

## No direct Kalshi API surface — except one real cost center

Analytics reads this app's own derived data (trade history, signal log,
config audit trail), not Kalshi directly. The one exception:
`market_analyst_orchestrator._analyze_market_uncached` calls
`client.get_market`/`client.get_event` (real Kalshi REST) before handing
context to the LLM — see `services/position/CHEATSHEET.md` for the
account-side Kalshi docs; this is the market-data side
(`docs/kalshi/get-market.md`, `get-event.md`).

**This is the only route in the read-only analytics surface that spends
real money per call** (Anthropic API, gated on `ANTHROPIC_API_KEY` +
`market_analyst.enabled` + a per-ticker/series/platform in-flight guard +
`reanalyze_cooldown_sec`). Treat any change here with the same care as a
real-money code path, not a plain analytics read.

## Handoff — who calls this module, who it calls

- **Upstream:** reads `broker`/`market_broker` trade logs (via
  `trade_analytics`), `signal_log`, `config_performance`'s audit trail —
  all already-produced data, nothing this module generates itself except
  LLM analysis records.
- **`market_analyst_orchestrator.py` → `services/advisory/advisory_engine.py`**
  is a real, deliberate cross-module import (LLM context-building needs the
  same recommendation set the dashboard shows) — same shared-dependency
  pattern as `services/advisory/`'s own import of `regime_analytics`. Don't
  read this as leftover coupling from before the split; it's the mirror
  image of it.
- `_series_evaluator_overview_with_crosscheck` (defined in
  `market_analyst_orchestrator.py`) is called directly from `main.py`'s
  `trading_loop` for the `calibration_advisory` phase's auto-apply logic —
  a real, deliberate `main.py`→this module dependency, not leftover
  coupling. Also called from `services/advisory/routes.py` for the same
  reason (advisory recommendations fold in series-evaluator's verdict).
- **Downstream:** `market_strategy_calibration`'s own auto-apply loop (if
  any) and every read-only route here return data only — no
  `config_store.update()` call anywhere in this residual module. The real
  write-back loops (`advisory`/`whale_calibration` auto-apply) moved with
  those modules; see their own `CHEATSHEET.md`s. ROADMAP.md's P4 "move
  analytics out of the live tick loop" item is about `trading_loop`'s
  `calibration_advisory` phase as a whole, spanning this module and its
  two siblings — read that entry before adding more work to that phase.
