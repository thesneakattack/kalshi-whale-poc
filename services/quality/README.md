# Quality module — reference

Owns: `models.py` (`QualityFinding`/`QualityReport`, the shared contract
runtime diagnostics and CI audit tooling both produce — Task 1) +
`routes.py` (`GET /api/quality/summary`, Tasks 10-11 of
`docs/superpowers/plans/2026-08-24-quality-control-plane.md`). This package
is a *composition* layer, not a new source of truth — it owns no
persistence of its own and performs no I/O beyond calling into modules
that already exist.

## What `/api/quality/summary` actually does

One HTTP call, six existing read-only sources, zero new instrumentation:

| Field | Source | Notes |
|---|---|---|
| `findings` / `counts` / `status` | `services/observability/observability.py`'s `runtime_findings(...)`, `services/storage_health/storage_health.py`'s `storage_findings(...)`, (since 2026-08-30, #71) `services/alerting/alerting.py`'s `alert_findings(active_alerts())`, **and** (since 2026-08-30, #214) `services/quality/evidence_provenance.py`'s `findings()` | all four lists concatenated, then rolled into one `QualityReport` for `overall_status()`/`counts()` — an active `critical` alert (kill switch, crash) is an `error` finding, any other active alert a `warning`, and an evidence-completeness defect is a `warning` |
| `diagnostics` | `services/diagnostics/diagnostics.py`'s `run_offline(cfg)` | deliberately excludes `check_coverage` (the one diagnostic that makes a real Kalshi call) — see that function's own docstring |
| `alerts` | `services/alerting/alerting.py`'s `active_alerts()` | currently-unresolved alerts only, not full history |
| `faults` | `services/fault_log.py`'s `summary()` | counts by component/severity + top offenders |
| `storage` | `services/storage_health/storage_health.py`'s `inventory_data_dir(DATA_DIR)` | the raw fast-tier inventory (sizes, page counts, table names — no `COUNT(*)`), alongside the findings derived from it |

This route also fetches `services/backup/backup.py`'s `latest()` and the
configured `backup.interval_sec` directly, to pass into
`storage_findings()` for its `backup-overdue` rule — see
`services/storage_health/README.md`'s own note on why
`storage_health.py` itself never imports `services.backup`.

**Deliberately not included yet** — Task 16 territory, expected to be
folded in here once it lands rather than duplicated: latest research-sweep
metadata (`services/research/`, when it exists).

## Evidence-completeness signal (2026-08-30, #214)

`evidence_provenance.py` composes three already-existing, already-public
defect counters into `current_completeness_state()`: `settlement_resolver.
snapshot()["dropped_after_max_attempts"]`, `index_feed.ingestion.
snapshot()["dropped_rows"]`, and `capture_writer.dropped_count()`/
`overflow_dropped_count()` (per store). Its `findings()` wrapper feeds
`GET /api/quality/summary` the same way `alerting.alert_findings()` does.
Deliberately NOT `settlement_resolver.dropped_total` - that field is a
conservation sum (also incremented by the expected non-binary-result skip
branch), not a defect count; its own source comment says so.
`services/advisory/routes.py` and `services/whale_calibration/routes.py`
also call `current_completeness_state()` directly, and `main.py`'s
`_maybe_run_auto_apply` uses it to refuse an automatic config write while
a defect is open - see `docs/archive/lane-4-analytics-advisory-research/specs/
2026-08-30-self-feeding-loop-provenance-design.md` (moved there 2026-09-06,
planning-lanes migration).

## Why this route is safe to call on every tick / poll cheaply

Every one of the six composed sources reads only local state — none
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
README.md for the full rule list, thresholds, and the "omit a finding
rather than fabricate a verdict" discipline every rule in both sets
follows when evidence is insufficient (no sample yet, a feature never
enabled, a single isolated blip).

## Hot-path impact

**Corrected 2026-09-04 (issue #530) — the "None" claim below was stale and
inaccurate.** `GET /api/quality/summary` is never called *from*
`main.py`'s `trading_loop`, but until this fix seven of its own composed
calls (`observability.runtime_findings`, `storage_health.
inventory_data_dir`, `backup.latest`, `storage_health.storage_findings`,
`alerting.active_alerts`, `research.latest`, `fault_log.summary`) ran
synchronously on the same asyncio event loop the WS trading callbacks and
every other route share — so while one of those calls was in flight,
*nothing else* on the process could run, trading loop included. This is
exactly what PR #424's own prior finding already showed live ("5
concurrent `GET /api/quality/summary` requests stalling an unrelated
`GET /api/state` for minutes") — this README's "zero effect on tick
timing" framing was never true for that failure mode, only for the
unrelated (true) claim that this route isn't *called from* the trading
loop. Fixed by wrapping each of the seven in `asyncio.to_thread` (see
`routes.py`'s own module docstring for the full rationale, live
measurements, and why `diagnostics.run_offline`/`alerting.alert_findings`/
`evidence_provenance.findings` were deliberately left as-is). One
important corrected expectation: direct live measurement (docker exec
against real production `data/*.db` files, 2026-09-04, re-measured after
adversarial review found the DB files had grown between passes) found the
seven newly-dispatched calls sum to roughly 100-300ms today — cheap, but
climbing as `fault_log.db`/`observability.db` grow, not a fixed "under
100ms" number. The route's own multi-second-to-30+s total latency
(docs/event-loop-blocking-routes-census-2026-09-03.md) is still
dominated by `diagnostics.run_offline()` (measured ~9.1s against the same
live data), which yields control genuinely and frequently (~1,100 real
awaited aiosqlite yields per call) — but per `_aio_db.py`'s own docstring
also has its own pre-existing, undisclosed, out-of-scope on-loop CPU
chunks between those yields (>=280ms measured 2026-09-01, likely more
now), not addressed by this fix. This fix stops the route from stalling
*other* requests while it runs; it does not make the route itself fast,
does not close `run_offline()`'s own separate on-loop-CPU gap, and never
claimed to do either.

Original (still-true) sub-claims, updated for the above: its own cost is
bounded by what it composes: `observability.runtime_findings` (pure,
reads already-computed `state`, plus one small indexed query via
`_repeated_rate_limit_hits_finding`), `storage_health.inventory_data_dir`
(the cheap file-stat tier, never the deep scan), `alerting.
active_alerts()`/`fault_log.summary()` (small indexed queries),
`diagnostics.run_offline` (explicitly excludes the one live-network
check, `check_coverage`) — no source here does any network I/O, proven
directly by `tests/test_quality_routes.py::
test_quality_summary_makes_no_kalshi_network_calls`.

## Failure behavior

A source this route composes raising would surface as a 500 for the whole
summary rather than a partial response — there is currently no
per-section try/except isolating one composed source's failure from the
rest. Acceptable today because every current source is a pure/read-only
local computation with no external dependency to fail on; revisit if a
future source here ever depends on something that can genuinely fail
independently (e.g. a network call).

## What is deliberately not automated

No automatic remediation of anything this route surfaces — it is strictly
read-only reporting, by design (the design spec's own framing: "the first
read for a future coding agent beginning an investigation," not an
actuator). No scheduled polling of this route exists anywhere in this
app; it's pulled on-demand by a human or an investigating session, and
(since QCP Task 18) by `frontend/src/js/system-health.js` while the
Terminal tab is open.

## Handoff

- No dashboard panel consumes this yet — Task 18's "minimal System Health
  UI" is the intended first caller, same as `services/observability/`'s
  own routes.
- `QualityReport.overall_status()` is coarse by design (`ok`/`warning`/
  `error`, worst-of across `findings` only). Since 2026-08-30 (#71) active
  alerts are part of `findings` (`check: active-alert`, one per active row,
  `finding_id` `alerting:active-alert:<category>:<id>`), so a tripped kill
  switch or a crash does reach `status`; `alerts.active` is still exposed
  raw alongside. It still does not fold in `diagnostics`' own separate
  `overall` (`ok`/`warn`/`fail`/`unknown`) or `faults`; a caller that wants
  "is *anything* wrong" has to look at those too. Revisit if/when a real
  UI consumer needs one true verdict.
