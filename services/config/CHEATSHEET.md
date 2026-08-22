# Config module — cheat sheet

Owns: `routes.py` (GET/POST `/api/config`) + `config_paths.py` (generic
`_config_value_at_path`/`_types_compatible`). `services/config_store.py`
(load/persist `settings.yaml`), `config_bounds.py` (validation ranges),
`config_overrides.py` (per-category/series resolver), and
`config_performance.py` (fingerprinting + audit trail) are already clean
and stay flat for now — see the modularization plan's "Folder-per-concern
restructuring" section.

## No Kalshi API surface here

Config is purely internal — `settings.yaml` is this app's own tuning
surface, not a Kalshi object. The one place this module touches anything
Kalshi-shaped is indirectly: `config_bounds.py`'s validated ranges should
stay consistent with real Kalshi constraints (e.g. price bounds, fee
structure) if those ever change — check `docs/kalshi/` before loosening a
bound, not this module's own history.

## Handoff — config is a hub, not a pipeline stage

Every other module reads `cfg`/`config_store.get()` (via
`services.app_state.cfg` at import time, or a fresh `config_store.get()`
call for live values) — there's no single "next module," all six other
concerns are consumers.

- **Writers back into config** (the actual "flow" worth knowing): analytics
  (`advisory_engine`, `confidence_calibration`) auto-applies tuning
  suggestions through `config_store.update()` directly, bypassing this
  module's `POST /api/config` route entirely (see `trading_loop`'s
  `calibration_advisory` phase in `main.py`) — this module's own
  `update_config` is specifically the *manual*, dashboard-driven write
  path, with the three guarded fields (`kalshi_account.trading_enabled`,
  `advisory.auto_apply_enabled`, `confidence_calibration.auto_apply_enabled`)
  requiring their own dedicated confirmation-gated routes instead.
- **Every write, from either path, gets logged** to
  `config_performance.log_applied_change` — `source` distinguishes
  `"manual"` (this module) from `"unified-advisory"`/`"calibration-auto-apply"`
  (analytics). See CLAUDE.md's HARD COMMANDMENT section for why this
  audit trail matters: it's what let a 3-day live-tuning drift that
  violated the project's own safety target get diagnosed and reverted
  (2026-08-21) instead of staying an "unexplained" diff.
