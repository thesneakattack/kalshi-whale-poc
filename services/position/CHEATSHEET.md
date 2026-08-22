# Position module — cheat sheet

Owns: `routes.py` (account order history, Market-Native state, risk
halt/resume for all 3 independent risk trackers, position-netting
diagnostics, erroneous-close correction) + `services/account_positions.py`
(real-account snapshot fetch/trimming). `services/paper_broker.py` is the
actual broker — already clean, lives flat, not moved into this folder (see
the modularization plan's "Folder-per-concern restructuring" section).

## Relevant Kalshi API docs (real account, not paper)

Only load-bearing once `kalshi_account.trading_enabled` is flipped — until
then these are read-only mirrors (`account.enabled` gates every call).

- `docs/kalshi/get-orders.md`, `get-positions.md`, `get-fills.md` — REST,
  polled every tick via `_fetch_account_snapshot` (20s cache,
  `_ACCOUNT_SNAPSHOT_REFRESH_SEC`).
- `docs/kalshi/user-orders.md`, `user-fills.md`, `market-positions.md` — the
  WS equivalents. `_process_stream_fill`/`_process_stream_position`
  (still in `main.py`, Phase 6 territory) already consume these; the REST
  poll above stays the reconciliation source of truth (see
  `_fetch_account_snapshot`'s own comment on why — unverified parsing on
  real financial data, CLAUDE.md's safety posture).
- Field trimming (`_POSITION_FIELDS`/`_FILL_FIELDS`/`_ORDER_FIELDS` in
  `account_positions.py`) was built directly against a live connected
  account, not guessed — re-verify against the docs above before adding a
  new field rather than assuming Kalshi's shape.

## Handoff — who calls this module, who it calls

- **Upstream (writes a position):** whale-stream's decision bridge
  (`_handle_signal` → `strategy.evaluate()`) and position management's
  `check_exits`/`position_netting.review` both call straight into
  `services/paper_broker.py` (`open_position`/`close_position`), not
  through this module — this module only mirrors the *real* account and
  serves read routes for both brokers.
- **Downstream (reads this module's output):** history
  (`trade_analytics.build_trade_history` reads `broker.trade_log`
  directly) and analytics (`config_performance`, `diagnostics`) both read
  broker/risk state via `services.app_state`, not via this module's routes
  — the routes exist for the dashboard, not for other backend modules.
- **`_real_account_position_tickers`** (defined here) feeds market watch's
  `_fetch_markets` (`extra_tickers`, so an open real position never rotates
  off the watchlist) and `state_view._relevant_tickers` (title-resolution
  scoping) — both are real cross-module dependencies, not incidental.
