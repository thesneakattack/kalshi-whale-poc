# Backup module — cheat sheet

Owns: `backup.py` (snapshot every `data/*.db` file via SQLite's own online
`Connection.backup()` API, prune old snapshots, record each run in
`data/backup_log.db`) + `routes.py` (status/history/manual-trigger HTTP
surface). New 2026-08-23, direct instruction after a live-traffic
investigation ("crucial infrastructure items") surfaced two real bugs
(the `config_store` torn-read race, the `index_stream_handlers` client
leak — see `static/status.html` phase 126) and, separately, confirmed
ROADMAP.md's "Path to production" backup/retention gap was still real and
unaddressed.

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

## Snapshot layout and retention

One directory per run under `data/backups/<UTC timestamp>/`, holding a
copy of every `data/*.db` file as of that moment — `backup_log.db` itself
included (glob only matches direct children of `data/`, so this can never
recurse into its own prior output). Retention (`backup.retention_count`,
default 14, ~3.5 days at the default interval) prunes whole snapshot
*directories*, oldest first, not individual files within one — a snapshot
is a single point-in-time restore target, so partial pruning within one
would defeat that. Kept deliberately more conservative than this project's
usual retention defaults: `series_watcher.py`'s `raw_trades` is never
pruned by design (a "first-class asset," per `CLAUDE.md`) and was measured
at 6.9GB on 2026-08-23, so every snapshot's real size only grows over
time — see the interval/retention constants' own comments in `backup.py`
and `config/settings.yaml`.

`data/backups/` needed its own `.gitignore` line — the project's existing
`data/*.db` pattern doesn't cross the extra directory level, so backup
output wouldn't have been ignored automatically.

## Two ways to run it, deliberately

1. **Wired into the trading loop** (`_maybe_run_backup`, called from
   `main.py`'s tick loop next to `_maybe_scan_catalog_batch`/
   `_maybe_refresh_discovery_cache` — same fire-and-forget, config-gated,
   interval-checked `_maybe_*` idiom). Zero setup under `ddev` — ticks
   along with everything else the app already does.
2. **Standalone CLI** (`python -m services.backup.backup`, no server or
   event loop needed) — for a real deployment's own cron/systemd timer,
   since ROADMAP.md's "Path to production" section is explicit this app
   has no real host/process supervisor yet, and backup cadence shouldn't
   be hostage to the app process's own uptime once one exists.

`run_backup_cycle` itself is synchronous (`sqlite3`'s `backup()` has no
async form) — `_run_backup_background` is what keeps it off the event
loop via `asyncio.to_thread`, so a multi-MB backup pass never stalls the
trading loop's own tick timing. The CLI path doesn't need this at all,
since there's no event loop to protect.

## Config

`config/settings.yaml`'s `backup` section: `enabled` (default `true`),
`interval_sec` (default 21600 = 6h), `retention_count` (default 14 =
~3.5 days at the default cadence). All three read fresh from config each tick
via `_maybe_run_backup(cfg)` — a live edit takes effect on the next check,
no restart, same as every other live-reloadable knob in this app.

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
  `GET /api/backup/status`/`GET /api/backup/history`, and nothing else
  yet — no dashboard panel wired up as of this writing (`static/`'s
  Config tab is the natural home if that's ever wanted).
- Restoring from a snapshot is a manual, deliberate operation (copy the
  relevant `data/backups/<timestamp>/<name>.db` back over the live file
  while the app is stopped) — no automated restore path exists or is
  planned; this module's whole job is making sure the data to restore
  from actually exists, not automating disaster recovery end to end.
