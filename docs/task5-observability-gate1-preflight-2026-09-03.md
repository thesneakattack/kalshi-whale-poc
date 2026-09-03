# Task 5 Gate 1 pre-flight: `services/observability/observability.py`

2026-09-03. Written analysis only, no code, no branch — per coordinator autotrade-1d, groundwork
for whenever reviewer capacity opens up; not to be implemented yet.

## 1. `_connect()` body in full — everything the migrated callback must preserve

`services/observability/observability.py:41-59`:

```python
def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")                                  # :44
    conn.execute("""CREATE TABLE IF NOT EXISTS metric_samples (...)""")      # :45-54
    conn.execute("CREATE INDEX IF NOT EXISTS idx_metric_samples_metric_time # :55-58
                  ON metric_samples(metric, observed_at)")
    return conn
```

One table (`metric_samples`, 4 columns: `observed_at`, `metric`, `value`, `labels_json`), one
index. No `add_column_if_missing`, no `busy_timeout` set (confirmed: no hits in the file) —
matches D3. This is the simplest schema of any module migrated or pre-flighted so far in this
effort — genuinely mechanical on the schema side. 6 call sites (`record_sample` `:66`,
`record_samples_bulk` `:80`, `history` `:89`, `summary` `:104`, `prune` `:124`,
`_latest_sample_time` `:130`), all `with _connect() as conn:`.

## 2. Event-loop exposure — a real, new finding, not previously flagged anywhere

**The general-bucket pre-audit's original "Event loop: NO" verdict for this module was correct
for what it checked (`maybe_capture`'s scheduled path, which does run in the synchronous
trading-loop tick) but incomplete** — it never checked `history()`/`summary()`, which have a
separate, direct HTTP exposure the earlier pre-audit's scope didn't reach. Same shape as Task
4's `reset/routes.py` miss: a different function of the same module has different exposure than
the one originally checked.

`services/observability/routes.py` — **both `history()` and `summary()` are called directly
from `async def` route handlers with zero dispatch**:
- `GET /api/observability/history` (`routes.py:31-38`, `async def get_observability_history`)
  calls `observability.history(...)` at `:38`, synchronously, no `await`/`tick_executor`.
- `GET /api/observability/summary` (`routes.py:41-44`, `async def get_observability_summary`)
  calls `observability.summary(...)` at `:44`, same pattern.

**Measured directly against the live app, not assumed**: `GET
/api/observability/summary?hours=720` — **7.66 seconds**. Not candidate_log-scale
(`count_range()`'s 34s/22.6M-row measurement, issue #510), but a real, significant,
currently-live event-loop freeze on a real, reachable endpoint — same severity class as several
of the `/api/reset` findings already tracked under #510. `summary()`'s `GROUP BY metric` over a
`hours` window scanning thousands of rows per metric (per-metric counts in the 6,000-6,800 range
observed at `hours=720`) is the likely mechanism, though this pre-flight didn't instrument the
query itself to confirm which part of it dominates.

**This should be filed as its own addition to issue #510** (or a new issue cross-referencing
it) before or alongside Task 5's eventual implementation — it's a real, live, currently-unfixed
gap independent of whether/when this migration happens, exactly the same framing the general-
bucket pre-audit used for the `/api/reset` findings.

`record_sample`/`record_samples_bulk` (the write path): called from
`services/storage_health/storage_health.py:174` and (via `capture_from_runtime`, buffer-only,
not `_connect()`-touching) from the trading loop — not confirmed to run on the event loop in
this pass; `storage_health.py`'s own callers weren't traced (out of scope for this pre-flight,
worth a note for whoever picks this up rather than a silent gap).

## 3. Cross-repo call-site census

`grep -rn "observability\.\(record_sample\|record_samples_bulk\|history\|summary\|prune\|_latest_sample_time\)"`
across the whole repo: real callers in `main.py:201` (`prune`, scheduled), `services/observability/routes.py`
(`history`, `summary` — the event-loop finding above), `services/storage_health/storage_health.py`
(`record_samples_bulk`, `history`), plus `tools/kalshi_rate_limit_probe.py` (`summary`, a
standalone CLI tool, not app-code). No `_CountingConn`-style spy pattern found in
`tests/test_observability.py` or `tests/test_storage_health.py` — all test call sites use the
module's public functions directly, no monkeypatching of `_connect` itself.

## 4. What would make this not purely mechanical

- **Nothing on the schema/migration side** — single table, single index, no shared-DDL/D2
  concern (this table isn't `capture_writer`-owned), no `add_column_if_missing`. This is the
  most mechanical module pre-flighted so far.
- **The event-loop finding above is the real substance of this pre-flight** — not a migration
  blocker (per this migration's own Global Constraints, fixing event-loop exposure is out of
  scope, tracked separately), but worth stating loudly in the eventual task text the same way
  Task 3's corrected framing did for `candidate_log.py`'s `count_range()`, so a reader doesn't
  come away thinking this module has no live exposure.
