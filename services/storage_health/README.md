# Storage health module — reference

Owns: `storage_health.py` (inventory/growth/integrity logic, no I/O beyond
sqlite reads and one `os.stat`-only sampler) + `routes.py`
(`GET /api/health/storage`, `POST /api/health/storage/scan`,
`POST /api/health/storage/integrity-check`). Quality Control Plane Task 11
(`docs/superpowers/plans/2026-08-24-quality-control-plane.md`).

Reference failure this module exists to make visible before a human
notices disk usage by hand: `game_state.db` grew to 5.7GB from repeated
full crypto payload persistence (`git log`, `static/status.html` phase
126), unnoticed until a direct 2026-08-23 investigation found it. As of
this module shipping, `game_state.db` is back down to ~73MB (that bug was
already fixed separately) — `series_watcher.db` is now the largest file at
~9.5GB, which is *expected*, not a bug: `raw_trades` is deliberately never
pruned per CLAUDE.md's "accumulated history is a first-class asset" rule.
This module reports that size honestly; it does not judge it.

## Three cost tiers — this is the whole design

| Tier | What it does | Where | Cost |
|---|---|---|---|
| Fast inventory | file stat + `PRAGMA page_count`/`page_size`/`freelist_count` + table name listing | `inventory_data_dir()` / `database_health(include_table_counts=False)` | O(1) per file — one read-only connection open + a few pragma reads, no row scans |
| Deep scan | fast inventory + `SELECT COUNT(*)` per table | `database_health(include_table_counts=True)`, only ever called from `routes._deep_scan_background` | O(rows) — live-measured at ~35s across 30 real db files including two multi-hundred-million-row tables (`series_watcher.raw_trades` at 13M rows, `market_history.snapshots` at ~7M) |
| Integrity check | `PRAGMA quick_check` | `quick_check()`, only ever called from `POST /api/health/storage/integrity-check` | reads the whole file — bounded but real; never automatic, never batched across every db in one call |

`GET /api/health/storage` and `GET /api/quality/summary` both only ever
call the fast tier. The deep scan is fire-and-forget through
`task_supervisor`, overlap-guarded via `state["storage_health"]["scanning"]`
(same shape as `market_catalog.catalog_scan`/`backup`'s own background
scans) and run off the event loop via `asyncio.to_thread` — its result is
cached in `state["storage_health"]["last_scan"]` (in-memory only, lost on
restart, same tradeoff as `discovery_cache`) and surfaced back through
`GET /api/health/storage`'s own response, not a separate route.

## Why every connection here is read-only

`_ro_connect` always opens `file:{path}?mode=ro`. This isn't just
discipline matching the plan's "must never vacuum, prune, delete, migrate,
or repair a DB automatically" constraint — it makes that constraint
mechanically true. A read-only connection cannot write even if a future
edit tried to.

## Growth detection — the one place this module makes a judgment call

The plan explicitly forbids a fixed "too big" threshold "from intuition."
`storage_growth_finding` instead compares a db's *current* size against
its own prior observed size (via `observability.history("db.<name>.
size_bytes", ...)`) and only fires when the file has at least DOUBLED
within a 24h lookback window, with a minimum 1h span between the baseline
sample and now (so a size sample taken 60 seconds ago can't produce a
statistically meaningless "rate"). This is conservative by design — normal
linear accretion never trips it, only genuinely runaway growth does
(exactly the `game_state.db` incident's shape).

**This only works if samples actually exist.** Nothing before this task
recorded `db.*.size_bytes` into `services/observability/observability.py`'s
shared store — Task 9's own `capture_from_runtime` doesn't touch storage
size at all (confirmed by reading that module in full before starting this
one). `maybe_capture_sizes` (wired into `main.py`'s trading loop right next
to `_maybe_capture_observability`) is what populates that history in
production; without it, `storage_growth_finding` would only ever return
`None` outside of tests that insert synthetic samples directly.

### Why `maybe_capture_sizes` skips the cold-start reseed backup.py/observability.py both needed

Both of those modules' own restart-safe seeding (`last_started_at`/
`last_sample_at` reseeded from the most recently *persisted* record on
first check in a process, not trusted at its in-memory `0.0` default) exists
because the guarded operation is genuinely expensive if it fires
spuriously — a full multi-file disk copy for backup, an extra DB write for
observability. `maybe_capture_sizes`'s guarded operation is `os.stat()`
across ~30 files — sub-millisecond, no sqlite connection at all. Firing one
extra time after every `uvicorn --reload` cycle costs nothing worth
protecting against, so this uses a plain in-memory interval gate
(`state["storage_health"]["last_sampled_at"]`, resets to 0.0 on every
restart) rather than copying that seeding complexity. Revisit only if this
sampler's own cost profile changes (e.g. it starts doing real I/O beyond
`stat()`).

## Integrity findings are free, not a proxy for `quick_check`

`storage_integrity_finding` reads `database_health()`'s own `error` field —
populated when even the *lightweight* pragma open fails (a genuinely
corrupt or non-SQLite file). This is incidental evidence that costs
nothing extra on the fast path, not a substitute for the real
`PRAGMA quick_check`. A file that opens fine for `database_health()` can
still fail a full `quick_check` — this module does not claim otherwise,
and `confidence="medium"` on this finding (vs. `"high"` elsewhere) is
deliberate, reflecting that weaker evidence honestly.

There is currently no mechanism to cache the *result* of an explicit
`POST /api/health/storage/integrity-check` run and surface a stale-but-
still-true failure in `/api/quality/summary` later — a manual check that
finds real corruption is visible in that one response, not remembered
afterward. Known limitation, not a bug: adding that would mean caching
per-db state and deciding a staleness policy for it, out of scope for this
task's "at minimum" requirements.

## `backup_overdue_finding` — why it takes plain arguments instead of importing `services.backup`

`storage_health.py` deliberately imports only
`services.observability.observability` (already side-effect-free at import
time) and `services.quality.models` — never `services.app_state` or
`services.backup`, unlike almost every other routes-adjacent module in this
codebase. `services.backup.backup` imports `services.app_state` at its own
top level (for `state["backup"]`), which transitively constructs
`PaperBroker`/`RiskManager`/`KalshiClient` — importing it here would make
even `storage_health.py`'s pure unit tests (fast, isolated, no `import
main`) drag that whole chain in. `backup_overdue_finding(last_run,
interval_sec, ...)` takes `backup.latest()`'s result and the configured
interval as plain arguments instead; both `services/storage_health/
routes.py` and `services/quality/routes.py` fetch those themselves and pass
them in — the same "composition layer does the I/O, the pure module just
judges the data" split `services/observability/observability.py`
established for Task 9's `capture_from_runtime`/`maybe_capture`.

## `resolve_db_path` — the path-traversal guard

`POST /api/health/storage/integrity-check` takes a caller-supplied `name`
over HTTP and turns it into a filesystem path. `resolve_db_path` rejects
anything with a path separator, anything not ending in `.db`, and (via
`.resolve()` + a parent-directory equality check) anything that would
resolve outside `data_dir` even through a `..` component — tested directly
in `tests/test_storage_health.py`, not just indirectly through the route.

## Hot-path impact

`maybe_capture_sizes(state, storage_health.DATA_DIR)` runs synchronously
in `main.py`'s `trading_loop`, once per tick, but gated to fire at most
once per `_SIZE_SAMPLE_INTERVAL_SEC` (15 min) - a non-firing tick costs
one time comparison. On a firing tick, cost is one `os.stat()` per
`data/*.db` file (cheap, no file content read) — the genuinely expensive
tier (`PRAGMA quick_check`, `SELECT COUNT(*)` table row counts) is never
reached from the tick loop at all; it only runs from
`POST /api/health/storage/scan`, itself offloaded via
`task_supervisor.supervise` + `asyncio.to_thread` so a deep scan of
several GB of `data/*.db` files can't stall the trading loop.

## Failure behavior

Every connection here is opened read-only (`_ro_connect`, see "Why every
connection here is read-only" below) — a failure to open or read one
`.db` file (locked, corrupt, mid-write) is caught per-file in
`database_health`/`inventory_data_dir` and recorded as that one entry's
own `error` field, never raised up to abort the whole inventory. The deep
scan's own overlap guard (`state["storage_health"]["scanning"]`) prevents
two scans running concurrently against the same files.

## What is deliberately not automated

No automatic remediation — a growth or integrity finding is reported, not
acted on (no automatic prune/vacuum/backup-trigger from this module
itself; `backup.py` is the separate, already-scheduled mechanism for
actually protecting the data). The deep integrity scan is on-demand only,
never scheduled — same reasoning `docs/kalshi/CHEATSHEET.md`'s canary
entries use for staying manual/scheduled rather than push/PR: real disk
I/O across potentially several GB is not something to pay for on every
tick or every push.

## Handoff

- No dashboard panel consumes any of the three routes yet — Task 18's
  "minimal System Health UI" is the intended first caller, same as
  `services/observability/`'s and `services/quality/`'s own routes.
- `services/quality/routes.py` folds `storage_findings()` into
  `GET /api/quality/summary` and also exposes the raw fast inventory under
  a top-level `"storage"` key, matching how `"diagnostics"`/`"alerts"`/
  `"faults"` already expose raw data alongside derived findings.
