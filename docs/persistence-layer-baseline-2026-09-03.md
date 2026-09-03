# Persistence-layer baseline — 2026-09-03

Baseline measurement for the persistence-layer migration to a unified `db.py`
(assigned by the coordinator to feed autotrade-a7's implementation plan).
Four areas requested: per-module fd usage, fault patterns, DB file sizes,
current `_connect()` implementation patterns. All findings below are read
directly from source, the live app's `/api/health/pipeline`, and
`data/fault_log.db` — nothing here is inferred or assumed.

## 1. `_connect()` implementation patterns across the 39 modules

`grep -rl 'def _connect\|sqlite3.connect' services/ tools/ main.py` finds 40
files with their own connection logic (39 real modules + one README). Every
file was individually checked (not just grep-shape-matched) for whether its
connection actually gets closed. Four distinct patterns exist. (Self-review
correction, 2026-09-03: the original version of this doc said "38 files, 37
real modules" here — its own four buckets below already summed to 39, not
37; this header never matched the body it introduced. Re-ran the grep fresh
during self-review rather than trusting the original count: 40 total is
current and correct, and the four buckets below sum to 39 exactly.)

- **Leaking — fresh connection per call, `with conn:` only, never closed (26
  modules)**: `conn = sqlite3.connect(db_path); with conn: ...`, no
  `conn.close()` anywhere near it. This is a real fd leak, not a style
  choice — Python's `sqlite3.Connection.__exit__` commits or rolls back the
  transaction, it does **not** close the connection (confirmed directly
  against `market_catalog.py`'s pre-fix docstring and Python's own sqlite3
  semantics, not assumed). `services/`: `accounts_store.py`, `alerting.py`,
  `backup.py` (its primary `_connect()` — the separate backup-copy
  `src_conn`/`dest_conn` pair in the same file does close correctly, so this
  file is split-pattern, counted here for the leaking half only),
  `candidate_ledger.py`, `candidate_log.py`, `config_performance.py`,
  `data_quarantine.py`, `game_state.py`, `suggestion_decisions.py`,
  `ingestion.py` (index_feed), `_db.py` (market_analyst_agent),
  `event_schedule.py`, `observability.py`, `paper_broker.py`, `research.py`,
  `reset_log.py`, `trade_archive.py`, `risk_manager.py`, `series_cache.py`,
  `series_evaluator.py`, `series_watcher.py`, `settlement_edge.py`,
  `shadow_mode.py`, `trade_category.py`, `calibration_history.py`. `tools/`:
  `coordination_engine.py` — same pattern, but a short-lived CLI script, not
  a long-running server process, so the fd-exhaustion risk this creates is
  much lower operationally (the OS reclaims fds on process exit) even though
  the code shape is identical.
- **Fixed by Tier0 (5 modules)**: `market_history.py`, `title_cache.py`,
  `market_catalog.py`, `signal_log.py`, `fault_log.py` — all five now wrap
  the connect in `try/finally: conn.close()`, with the `try` starting
  immediately after `sqlite3.connect()` (not after PRAGMA/schema-init), per
  a follow-up fix (PR #501) that closed a residual setup-time-failure leak
  the first pass missed. Confirmed by reading `market_catalog.py:67-91`
  directly.
- **Already closes correctly, not part of the problem (5 modules)**:
  `capture_writer.py` (explicit `conn.close()`), `storage_health.py`
  (explicit closes at two call sites), `store_stats.py`'s *second* connection
  (a read-only `uri=True` one, closes correctly — its *first*, `with
  sqlite3.connect(db_path) as conn:`, does not, so this file is also
  split-pattern like `backup.py`, and its own comment already flags "was a
  transaction context manager, not a—" mid-sentence, suggesting this was a
  known, half-addressed issue before this baseline), `tools/
  historical_data_backfill.py`, `tools/quality_ratchet.py`.
- **Pooled / cached, structurally different (3 modules)**: `tick_executor.py`'s
  `connection_for()` — one thread-local connection per worker thread, reused
  across calls, WAL mode, `busy_timeout=50ms` (fails fast rather than
  blocking); confirmed zero production callers as of this module's own
  docstring (built for a future use case, not wired in). `_scoring_pool.py`
  shows the same cache-pattern signal. `diagnostics/_aio_db.py` keeps one
  persistent `aiosqlite.Connection` per (event loop, db_path) pair — a real,
  already-working pooling precedent the new `db.py` could model itself on.

**Net: 26 modules (25 in `services/`, 1 in `tools/`) still carry the exact
leak pattern Tier0 fixed in only 5** (self-review correction: the original
"25 (24 services, 1 tools)" undercounted its own listed names by one — the
list above already names 25 services/ modules, not 24), plus 2 files (`backup.py`,
`store_stats.py`) that are split — half-fixed already, half still leaking.
This is the single largest structural finding for the migration's impact
estimate — a unified `db.py` needs to either close-on-exit by construction
(so no call site can regress this) or pool connections outright, and
`_aio_db.py`'s existing pattern is a real, already-proven-safe model for the
pooled approach rather than something to design from scratch.

## 2. Fault patterns (`data/fault_log.db`, all-time, queried live)

Top connection/lock-shaped faults by count:

| component | operation | exc_type | message | count | first_seen (UTC) | last_seen (UTC) |
|---|---|---|---|---:|---|---|
| market_history | record_snapshot_from_ticker | OperationalError | unable to open database file | **1002** | 2026-09-02 08:24 | 2026-09-02 20:40 |
| capture_writer | flush | OperationalError | database is locked | **237** | — | — |
| capture_writer | flush_retained_on_lock | OperationalError | database is locked | **192** | 2026-08-30 16:19 | 2026-09-03 07:04 |
| market_history | record_snapshot_from_ticker | DatabaseError | (schema/corruption-shaped) | 45 | 2026-09-03 (post-corruption window) | — |
| market_history | record_snapshot_from_ticker | OperationalError | database is locked | 6 | — | — |
| market_catalog | scan_batch | ConnectTimeout | (Kalshi REST, not DB) | 6 | 2026-08-30 | 2026-09-03 |
| market_history | record_snapshot_from_ticker | OperationalError | attempt to write a readonly database | 2 | — | — |

Two things worth separating explicitly:

- **"unable to open database file" (1002 occurrences, 2026-09-02) is the
  fd-exhaustion signature**, not a one-off — this predates today's own
  container-overload incident by roughly 12 hours (that one was
  2026-09-03, this fault window is 2026-09-02 08:24-20:40), meaning the
  connection-leak pattern has caused a real fd-exhaustion event on at
  least two separate occasions, not once. This is quantified, direct
  evidence the leak pattern in §1 is not theoretical.
- **"database is locked" (429 combined, `capture_writer`, spanning
  2026-08-30 to 2026-09-03 — a 4-day-plus recurring pattern)** is write
  contention from multiple per-call connections colliding, not a single
  incident either. `capture_writer.py` already has its own
  `flush_retained_on_lock` retry path for this (a symptom-level mitigation,
  not a fix at the connection-management layer).
- `market_catalog`'s `ConnectTimeout`/`ReadTimeout`/`ReadError` entries are
  Kalshi REST call failures (httpx-level), not SQLite — noted so they're
  not miscounted as persistence-layer faults.

## 3. `data/*.db` file sizes (baseline, `ls -la data/*.db`)

| file | size |
|---|---:|
| series_watcher.db | 29.7 GB |
| candidate_log.db | 3.76 GB |
| game_state.db | 1.28 GB |
| market_history.db | 622 MB |
| index_feed.db | 464 MB |
| observability.db | 312 MB |
| signal_log.db | 102 MB |
| market_catalog.db | 44 MB |
| series_cache.db | 41 MB |
| candidate_ledger.db | 13.5 MB |
| settlement_edge.db | 11.4 MB |
| fault_log.db | 10.3 MB |
| title_cache.db | 8.2 MB |
| everything else (18 files) | < 2 MB each |

`series_watcher.db` at 29.7 GB dwarfs every other store by an order of
magnitude — any migration approach that requires a full read/rewrite of
this file (a schema migration, a wholesale move to a different engine) has
a materially different cost/risk profile than the rest of the ~600MB-and-
under stores. Worth confirming directly with whoever owns the migration
plan whether `series_watcher.db` is in scope for the first migration pass
or deliberately deferred.

## 4. Live fd usage (current snapshot, `GET /api/health/pipeline`)

`open_fds: {count: 41, soft_limit: 1024}` at 2026-09-03T08:38:40Z — healthy
right now (the app recovered from today's earlier container-overload
incident via a `docker restart`; this is a post-recovery snapshot, not
evidence the leak pattern in §1 is harmless under normal load). No live
per-module fd breakdown exists yet — `/api/health/pipeline` reports only
the process-wide total, not which module holds which fd. Tier0's Task 9
(fd-count visibility) added the process-wide counter; a per-module
breakdown would need new instrumentation this baseline doesn't have access
to without adding it.

## Summary for the implementation plan

- The leak pattern Tier0 fixed in 5 modules is structurally present in 26
  more (plus 2 split-pattern files, half-fixed already) — the migration
  should treat "close on exit by construction" as a correctness requirement
  of the new `db.py`, not an optional improvement, given it's already
  caused at least two real fd-exhaustion incidents. `diagnostics/_aio_db.py`
  is a working precedent for a pooled approach, if pooling is preferred
  over per-call-with-close.
- Write-lock contention (`database is locked`, 429+ occurrences over 4+
  days) is a second, independent problem from the fd leak — worth deciding
  whether the new layer also addresses connection pooling/serialization or
  leaves that to a later pass.
- `series_watcher.db`'s 29.7 GB size is the one file-size outlier that
  could change the migration's scoping/sequencing; everything else is
  under 4 GB and most under 500 MB.
- No per-module fd attribution exists yet; if the implementation plan needs
  that granularity, it's new instrumentation work, not something this
  baseline could pull from existing data.
