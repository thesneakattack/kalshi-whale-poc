# Backup module — reference

Owns: `backup.py` (snapshot `data/*.db` files, on two independent tiers as
of 2026-08-30 — see "Two-tier split" below — via SQLite's own online
`Connection.backup()` API, prune old snapshots per tier, record each run
in `data/backup_log.db`) + `routes.py` (status/history/manual-trigger HTTP
surface, `?tier=` throughout). New 2026-08-23, direct instruction after a
live-traffic investigation ("crucial infrastructure items") surfaced two
real bugs (the `config_store` torn-read race, the `index_stream_handlers`
client leak — see `static/status.html` phase 126) and, separately,
confirmed ROADMAP.md's "Path to production" backup/retention gap was
still real and unaddressed.

## Why `Connection.backup()`, not `cp`/`shutil.copy`

This project's persistence idiom (`CLAUDE.md`) runs most `data/*.db` files
in WAL mode specifically for read/write concurrency while the live app is
running. A raw file copy taken mid-write can capture a torn, inconsistent
snapshot — the exact failure class `services/config_store.py`'s own
2026-08-23 fix (see its docstring) hit for `config/settings.yaml`, just
for a different file type. `sqlite3.Connection.backup()` is the standard
library's documented-safe way to take a consistent snapshot of a live,
concurrently-written database with zero coordination required from the
writer's side, regardless of journal mode.

## Two-tier split (2026-08-30)

The single-cadence design below grew into a real problem: every snapshot
carried a full copy of `series_watcher.db`/`candidate_log.db`/
`market_history.db` — the three files `CLAUDE.md`'s "accumulated history
is a first-class asset" rule keeps growing forever by design, with no
row-level retention of their own. By 2026-08-30 that meant 292GB across
13 snapshots in `data/backups/`, once `series_watcher.db` alone reached
24GB — the backup mechanism meant to protect the app's data had become
the single largest consumer of disk on the box.

The fix splits backup into two independent tiers, each with its own
cadence, snapshot directory, and config knobs:

- **`regular` tier** — unchanged 6h/14-count cadence
  (`backup.interval_sec`/`backup.retention_count`), snapshots into
  `data/backups/` as before, but now *excludes* the three large files —
  just the small, account-critical ones (`paper_broker.db`,
  `risk_state.db`, `config_store` state, etc.).
- **`large` tier** — the three permanently-growing files only
  (`backup.large_files`, default `series_watcher.db`/`candidate_log.db`/
  `market_history.db`), on a longer, separately-configured 86400s/4-count
  cadence (`backup.large_file_interval_sec`/
  `backup.large_file_retention_count`), snapshotting into
  `data/backups_large/` — a *sibling* of `data/backups/`, not nested
  inside it (nesting would corrupt both tiers' oldest-first directory-name
  pruning and status counts — see `LARGE_BACKUP_DIR`'s own comment in
  `backup.py`).

Both tiers share the same `run_backup_cycle`/`_backup_one_file`/
`_prune_old_snapshots` mechanism below (via `tier`/`exclude`/`only`/
`snapshot_root` parameters) and the same `backup_runs` table, tagged by a
`tier` column (`NULL` on pre-split rows, treated as `"regular"` since
that's the only cadence that existed then). See `backup.py`'s own module
docstring and `_DEFAULT_LARGE_FILES` for the authoritative current
detail.

## Snapshot layout and retention

One directory per run under `data/backups/<UTC timestamp>/` (regular
tier) or `data/backups_large/<UTC timestamp>/` (large tier), holding a
copy of that tier's own file selection as of that moment —
`backup_log.db` itself is a regular-tier file (glob only matches direct
children of `data/`, so this can never recurse into either tier's own
prior output). Retention prunes whole snapshot *directories* within a
tier's own root, oldest first, not individual files within one — a
snapshot is a single point-in-time restore target, so partial pruning
within one would defeat that. The regular tier's 14-count default is kept
deliberately more conservative than this project's usual retention
defaults, but now costs nothing like the pre-split snapshot did — its
own files are all single-digit-MB now that the large tier carries the
three permanently-growing files separately — see the interval/retention
constants' own comments in `backup.py` and `config/settings.yaml`.

`data/backups/` (and, since the split, `data/backups_large/`) each needed
their own `.gitignore` line — the project's existing `data/*.db` pattern
doesn't cross the extra directory level, so backup output wouldn't have
been ignored automatically.

## Two ways to run it, deliberately

1. **Wired into the trading loop** (`_maybe_run_backup` for the regular
   tier and `_maybe_run_large_backup` for the large tier, both called
   from `main.py`'s tick loop next to `_maybe_scan_catalog_batch`/
   `_maybe_refresh_discovery_cache` — same fire-and-forget, config-gated,
   interval-checked `_maybe_*` idiom, each against its own `state["backup"]`/
   `state["backup_large"]`). Zero setup under `ddev` — ticks along with
   everything else the app already does.
2. **Standalone CLI** (`python -m services.backup.backup`, no server or
   event loop needed — runs both tiers back to back, see the module's own
   `__main__` block) — for a real deployment's own cron/systemd timer,
   since ROADMAP.md's "Path to production" section is explicit this app
   has no real host/process supervisor yet, and backup cadence shouldn't
   be hostage to the app process's own uptime once one exists.

`run_backup_cycle` itself is synchronous (`sqlite3`'s `backup()` has no
async form) — `_run_backup_background` is what keeps it off the event
loop via `asyncio.to_thread`, so a multi-MB backup pass never stalls the
trading loop's own tick timing. The CLI path doesn't need this at all,
since there's no event loop to protect.

## Config

`config/settings.yaml`'s `backup` section, regular tier: `enabled`
(default `true`, shared by both tiers), `interval_sec` (default 21600 =
6h), `retention_count` (default 14 = ~3.5 days at the default cadence).
Large tier (2026-08-30): `large_files` (default
`series_watcher.db`/`candidate_log.db`/`market_history.db` —
`backup._DEFAULT_LARGE_FILES`), `large_file_interval_sec` (default 86400
= 24h), `large_file_retention_count` (default 4 = ~4 days at that
cadence). All six read fresh from config each tick via
`_maybe_run_backup(cfg)`/`_maybe_run_large_backup(cfg)` — a live edit
takes effect on the next check, no restart, same as every other
live-reloadable knob in this app.

## Known-fixed bug: `--reload` (or any restart) used to re-trigger an immediate backup

`_maybe_run_backup`'s "due" check compared `now` against
`state["backup"]["last_started_at"]` — pure in-memory state, reset to its
`app_state.py` default (`0.0`) on every process start. That's not just a
real reboot: this dev environment's `uvicorn --reload` restarts the whole
process (and its in-memory `state`) on any `.py` edit, including files
under `tests/`. Every one of those resets made the next tick's "due" check
read "never backed up," firing an immediate full snapshot of every
`data/*.db` file regardless of how recently one had actually completed.

Found and fixed live 2026-08-23, same "module quality" pass as the rest of
this session: 37 backup runs recorded in a 4.4h window against a configured
6h `interval_sec` — median gap ~94s, 28 of 36 gaps under 200s, only 1 over
an hour. Each run's real disk I/O (9.4GB, ~40-50s) measurably contended
with the live trading loop's own SQLite reads/writes on the same disk —
`tick_phase_timings.market_fetch` (which does its own real, if cached, REST
+ SQLite work) was observed at 6-8s against a 6s `poll_interval_sec` during
this window, dropping to ~1-3s immediately after the fix. Fixed by seeding
`last_started_at` from the already-persisted `backup_runs` history
(`backup.latest()`) the first time `_maybe_run_backup` runs in a given
process, instead of trusting in-memory state alone on a cold start — a
restart now means "go check what actually happened," not "assume the
worst and re-backup immediately." `tests/test_backup.py` covers this
directly (recent persisted history → no refire; old/missing history →
still fires promptly).

## Handoff

- **Downstream (reads run history):** `routes.py`'s
  `GET /api/backup/status` (returns both tiers — the large tier under its
  own `large_tier` key), `GET /api/backup/history?tier=` (defaults to
  `regular`), and `POST /api/backup/run?tier=` (`regular` default,
  `large`, or `all` to run both cycles and return a combined
  `{"regular": {...}, "large": {...}}` result — the one to call before a
  risky operation if full coverage of every `data/*.db` file is wanted,
  since the bare default now covers only the regular tier). Nothing else
  reads this yet — no dashboard panel wired up as of this writing
  (`static/`'s Config tab is the natural home if that's ever wanted).
  `backup_overdue_finding` (`services/storage_health/storage_health.py`)
  and `GET /api/quality/summary` check only the regular tier's recency —
  a deliberate scope boundary (see `routes.py`'s own module docstring),
  not an oversight.
- Restoring from a snapshot is a manual, deliberate operation (copy the
  relevant `data/backups/<timestamp>/<name>.db` or
  `data/backups_large/<timestamp>/<name>.db` back over the live file
  while the app is stopped) — no automated restore path exists or is
  planned; this module's whole job is making sure the data to restore
  from actually exists, not automating disaster recovery end to end.
- **Test isolation (QCP Task 18, 2026-08-24):** this module's `DATA_DIR`
  constant (globbed for every `data/*.db` file) is now centrally
  redirected by `tests/support/runtime_isolation.py`'s
  `DATA_DIR_MODULE_PATHS` registry, not just this file's own local
  `tests/test_backup.py` fixture — closes a real gap where the browser-E2E
  harness (`tests/support/e2e_server.py`, no per-file fixtures of its own)
  had no protection and `GET /api/quality/summary` 500'd trying to open a
  real `data/*.db` file read-only. See that registry's own comment for the
  full incident.
