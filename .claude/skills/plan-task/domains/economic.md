# Domain: economic (strategy edge, entry gates, advisory/calibration)

Canonical: `docs/kalshi-personal-production-execution-program-2026-08-26.md` §6.1;
`docs/superpowers/specs/2026-08-26-economic-strategy-{effectiveness-investigation,remediation}-design.md`;
the matching `plans/`; `docs/superpowers/research/2026-08-26-economic-*.md` (the status report is
current truth). The remediation plan is NOT approved for execution until its own status line says so.

- Reuse existing reviewed functions (`series_watcher.reconcile()/funnel()/book_context_at_entry()`, `candidate_log.population_gate_summary()`) before any new analysis; `dimensional-analysis` on every EV / unit-cost / P&L formula.
- `data/*.db` read-only (`mode=ro` URI for the multi-GB files); never `GET /api/diagnostics/trade-capture` while realtime work shares the REST limiter.
- Try to falsify before recording a finding; record negative and insufficient-sample results in the status report; surface tuning suggestions, never auto-apply them, even in paper mode.
