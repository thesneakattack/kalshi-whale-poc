# Backtest module — cheat sheet

Owns: `backtest.py` (`entry_threshold_sweep`, `min_whale_winrate_pct_sweep`
- stateless replay over `services/signal_log.py`'s already-logged resolved
signals, no new persistence, no replay of the trading loop itself) +
`routes.py` (`/api/backtest/entry-threshold`, `/api/backtest/min-whale-
winrate`). Split out of `services/analytics/` 2026-08-22 (modularization
Phase 4/9). Leaf module — checked directly before moving: zero imports
besides stdlib, only `services/app_state.py` + `routes.py` + one test
depended on it, the second-cleanest split of this whole pass after
`services/whale_calibration/`.

## No direct Kalshi API surface

Pure functions over `services/signal_log.py`'s already-captured rows
(`resolved_signals_with_factors`/`all_series_stats`/
`resolved_signals_with_series`) — never calls Kalshi's API.

## Real, disclosed scope boundary (carried forward from the module's own
docstring, not re-derived) — what this can and can't replay

`signal_log`'s `signals` table stores `confidence`/`side`/`series`/
`correct` but never `price`, `spread`, `volume_24h`, or raw notional at
signal time. A stateless replay can only ever cover gates whose comparison
is a pure function of what's actually stored — that covers
`strategy.entry_threshold` and `strategy.min_whale_winrate_pct` cleanly.
It does **not** cover `strategy.longshot_price_threshold`/
`longshot_entry_threshold_bonus` (needs price), any of
`market_strategy.py`'s price-band/spread/volume/momentum gates (needs
`market_history` data joined at the exact signal timestamp, not retained),
or `whale_watcher_kalshi.min_notional_usd` (needs raw notional, only
captured going forward via `candidate_log`, doesn't help replay signals
that predate it). **Before extending this module to "backtest" any other
gate, check whether the gate's own comparison is actually reconstructable
from what `signal_log` stores today** — if not, this needs either richer
historical logging or a full trading-loop replay harness, not a quick
addition here.

## Handoff — who calls this module, who it calls

- **Upstream:** `routes.py` only — unlike advisory/whale_calibration,
  nothing in `main.py`'s `trading_loop` calls this module directly. It's
  purely an on-demand analysis tool (`GET` routes, no auto-apply, no
  write-back loop).
- **Downstream:** nothing writes config from this module's output — a
  human reads the sweep and decides whether to change
  `strategy.entry_threshold`/`min_whale_winrate_pct` by hand via
  `POST /api/config`. No `config_store.update()` call anywhere in this
  package.
