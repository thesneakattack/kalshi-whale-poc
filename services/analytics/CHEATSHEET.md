# Analytics module — cheat sheet

Owns: `routes.py` (advisory/calibration/regime/backtest/market-analyst/
series-evaluator routes) + `market_analyst_orchestrator.py` (the one part
of this concern that's real logic, not routing — LLM-based market/series/
full-spectrum analysis, with its own in-flight guard sets).
`services/diagnostics.py`/`series_watcher.py`/`settlement_edge.py`/
`config_performance.py`/`advisory_engine.py`/`confidence_calibration.py`
are already clean and stay flat for now.

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
- **Downstream — this is the one place with a real write-back loop:**
  `advisory_engine`/`confidence_calibration` auto-apply write straight into
  `config_store` from `trading_loop`'s `calibration_advisory` phase
  (in `main.py`, not through `services/config/routes.py`'s manual path —
  see that module's own cheat sheet for the distinction). This in-process,
  every-tick computation is exactly what ROADMAP.md's P4 "move analytics
  out of the live loop" item is about — read that entry before adding more
  work to this phase of the tick loop.
- `_series_evaluator_overview_with_crosscheck` (defined here) is also
  called directly from `main.py`'s `trading_loop` for the same
  calibration_advisory auto-apply logic — a real, deliberate main.py→this
  module dependency, not leftover coupling.
