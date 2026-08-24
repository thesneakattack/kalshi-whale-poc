# Quality module — cheat sheet

Owns: `models.py` (`QualityFinding`/`QualityReport`, the shared contract
runtime diagnostics and CI audit tooling both produce — Task 1) +
`routes.py` (`GET /api/quality/summary`, Task 2026-08-24-quality-control-
plane.md's Task 10). This package is a *composition* layer, not a new
source of truth — it owns no persistence of its own and performs no I/O
beyond calling into modules that already exist.

## What `/api/quality/summary` actually does

One HTTP call, four existing read-only sources, zero new instrumentation:

| Field | Source | Notes |
|---|---|---|
| `findings` / `counts` / `status` | `services/observability/observability.py`'s `runtime_findings(cfg, state, trade_stream, index_stream)` | rolled into a `QualityReport` for `overall_status()`/`counts()` |
| `diagnostics` | `services/diagnostics/diagnostics.py`'s `run_offline(cfg)` | deliberately excludes `check_coverage` (the one diagnostic that makes a real Kalshi call) — see that function's own docstring |
| `alerts` | `services/alerting/alerting.py`'s `active_alerts()` | currently-unresolved alerts only, not full history |
| `faults` | `services/fault_log.py`'s `summary()` | counts by component/severity + top offenders |

**Deliberately not included yet** — Task 11/16 territory, expected to be
folded in here once they land rather than duplicated: storage health
(`services/storage_health/`, when it exists), latest research-sweep
metadata (`services/research/`, when it exists).

## Why this route is safe to call on every tick / poll cheaply

Every one of the four composed sources reads only local SQLite state —
none constructs a `KalshiClient` or otherwise touches the network.
`diagnostics.run_offline()` is explicit about this in its own docstring
(`check_coverage` is the one real-API diagnostic, and it's the one thing
`run_offline` leaves out). `tests/test_quality_routes.py` proves the
composed route holds this invariant too, not just `run_offline` in
isolation — it monkeypatches `KalshiClient.__init__` to raise and confirms
`GET /api/quality/summary` still returns 200.

## `runtime_findings`'s rules live in `services/observability/observability.py`, not here

Deliberate: `services/quality/` composes and formats; the actual anomaly
*rules* (tick duration vs. poll interval, non-zero dropped WS messages,
enabled-but-disconnected streams, repeated recent rate-limit hits) live
next to the metrics they read, in `services/observability/observability.py`
— see that module's own CHEATSHEET.md for the full rule list, thresholds,
and the "omit a finding rather than fabricate a verdict" discipline each
one follows when evidence is insufficient (no sample yet, a feature never
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
