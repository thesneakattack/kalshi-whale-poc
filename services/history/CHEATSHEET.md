# History module — cheat sheet

Owns: `routes.py` — browsable signal history, flow clustering, the Trading
History tab (win/loss + cumulative P&L curve), market_history's
tracked-ticker/hypothetical-trade summaries. `services/trade_analytics.py`
(the derivation layer over `PaperBroker.trade_log`) and
`services/market_history.py` are already clean and stay flat for now.

## No Kalshi API surface here

Everything here is derived from this app's own persisted logs
(`signal_log.db`, `market_history.db`, `PaperBroker.trade_log`), not a
direct Kalshi call. The one thing worth knowing: `market_history`'s
`spread`/`volume_24h`/`time_to_close_sec` columns are written every tick
but read by nothing today (a real, still-open gap — see the docs-mining
findings this session).

## Handoff — who calls this module, who it calls

- **Upstream (produces what this module reads):** whale-stream's decision
  bridge writes `signal_log`; position (`paper_broker.py`) writes
  `trade_log` on every open/close; `trading_loop`'s `resolve_and_record`
  phase writes `market_history` snapshots/outcomes every tick.
- **Downstream (reads this module's derived output):** analytics
  (`advisory_engine`, `confidence_calibration`, `market_analyst_orchestrator`)
  all call `trade_analytics.build_trade_history()` themselves directly
  rather than through this module's routes — the routes exist for the
  dashboard, the derivation function itself is the real shared interface.
  Config doesn't read history directly; it's on the receiving end of
  whatever analytics decides based on it.
