# Quality module — cheat sheet

Owns: `models.py` (`QualityFinding`/`QualityReport`, the shared contract
runtime diagnostics and CI audit tooling both produce — Task 1) +
`routes.py` (`GET /api/quality/summary`, Tasks 10-11 of
`docs/superpowers/plans/2026-08-24-quality-control-plane.md`). This package
is a *composition* layer, not a new source of truth — it owns no
persistence of its own and performs no I/O beyond calling into modules
that already exist.

## What `/api/quality/summary` actually does

One HTTP call, five existing read-only sources, zero new instrumentation:

| Field | Source | Notes |
|---|---|---|
| `findings` / `counts` / `status` | `services/observability/observability.py`'s `runtime_findings(...)` **and** `services/storage_health/storage_health.py`'s `storage_findings(...)` | both lists concatenated, then rolled into one `QualityReport` for `overall_status()`/`counts()` |
| `diagnostics` | `services/diagnostics/diagnostics.py`'s `run_offline(cfg)` | deliberately excludes `check_coverage` (the one diagnostic that makes a real Kalshi call) — see that function's own docstring |
| `alerts` | `services/alerting/alerting.py`'s `active_alerts()` | currently-unresolved alerts only, not full history |
| `faults` | `services/fault_log.py`'s `summary()` | counts by component/severity + top offenders |
| `storage` | `services/storage_health/storage_health.py`'s `inventory_data_dir(DATA_DIR)` | the raw fast-tier inventory (sizes, page counts, table names — no `COUNT(*)`), alongside the findings derived from it |

This route also fetches `services/backup/backup.py`'s `latest()` and the
configured `backup.interval_sec` directly, to pass into
`storage_findings()` for its `backup-overdue` rule — see
`services/storage_health/CHEATSHEET.md`'s own note on why
`storage_health.py` itself never imports `services.backup`.

**Deliberately not included yet** — Task 16 territory, expected to be
folded in here once it lands rather than duplicated: latest research-sweep
metadata (`services/research/`, when it exists).

## Why this route is safe to call on every tick / poll cheaply

Every one of the five composed sources reads only local state — none
constructs a `KalshiClient` or otherwise touches the network.
`diagnostics.run_offline()` is explicit about this in its own docstring
(`check_coverage` is the one real-API diagnostic, and it's the one thing
`run_offline` leaves out); `storage_health.inventory_data_dir()` is
explicit about the same idea for disk cost — it only ever runs the fast
tier here (file stat + lightweight PRAGMAs, no `COUNT(*)`, no
`quick_check`), never the deep scan or integrity check. `tests/
test_quality_routes.py` proves the composed route holds the no-network
invariant, not just `run_offline` in isolation — it monkeypatches
`KalshiClient.__init__` to raise and confirms `GET /api/quality/summary`
still returns 200.

## The anomaly rules themselves live next to their own metrics, not here

Deliberate, same split for both composed rule sets: `services/quality/`
composes and formats; the actual rules live next to the data they judge.
`services/observability/observability.py`'s `runtime_findings` (tick
duration vs. poll interval, non-zero dropped WS messages, enabled-but-
disconnected streams, repeated recent rate-limit hits) and `services/
storage_health/storage_health.py`'s `storage_findings` (storage growth,
storage integrity, backup overdue) each live in their own module's
CHEATSHEET.md for the full rule list, thresholds, and the "omit a finding
rather than fabricate a verdict" discipline every rule in both sets
follows when evidence is insufficient (no sample yet, a feature never
enabled, a single isolated blip).

## Handoff

- No dashboard panel consumes this yet — Task 18's "minimal System Health
  UI" is the intended first caller, same as `services/observability/`'s
  own routes.
- `QualityReport.overall_status()` is coarse by design (`ok`/`warning`/
  `error`, worst-of across `findings` only) — it does not currently fold
  in `diagnostics`' own separate `overall` (`ok`/`warn`/`fail`/`unknown`)
  or the presence of active alerts/faults into one combined verdict; a
  caller that wants "is *anything* wrong" today has to look at more than
  just `status`. Revisit if/when a real UI consumer needs one true verdict.
