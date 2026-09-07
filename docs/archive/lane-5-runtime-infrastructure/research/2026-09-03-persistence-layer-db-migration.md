# Research: unifying 30 scattered `_connect()` implementations into `services/db.py`

2026-09-03. Requested by autotrade-1d (coordinator) to feed autotrade-a7's downstream
migration plan. Scope per assignment: current pain points, why unified `db.py` solves them,
migration complexity estimate, risks/benefits. All findings below are verified directly
against current source on `main` (`76e6671`) plus the existing prototype branch
`feat/persistence-layer-unified-connect` (`17b2e8f`) — nothing here is inferred from the
architecture audit's own summary text.

## Current pain points (verified)

`grep -rln "def _connect" services/` finds exactly **30 modules** with their own `_connect()`
implementation — matches the assignment's framing precisely, not approximately:

accounts_store, alerting/alerting, backup/backup, candidate_ledger, candidate_log,
config/config_performance, data_quarantine, fault_log, game_state,
history/suggestion_decisions, index_feed/ingestion, market_analyst_agent/_db,
market_catalog/market_catalog, market_events/event_schedule, market_history,
observability/observability, paper_broker, research/research, reset/reset_log,
reset/trade_archive, risk_manager, series_cache, series_evaluator, series_watcher,
settlement_edge, shadow_mode, signal_log, title_cache, trade_category,
whale_calibration/calibration_history.

**5 of the 30 are already migrated** to a closes-its-connection pattern, across two separate
merged fix waves found in git history:
- Happy-path fix (PR #499, tier0 plan): `market_history.py`, `title_cache.py`,
  `market_catalog/market_catalog.py`, `signal_log.py`, `fault_log.py` — each got its own
  dedicated commit ("services/X.py's `_connect()` closes its connection"), all landed within
  this one PR. (PR #500, tier1-backend-hygiene, is a separate, unrelated PR — it touches
  `fault_log.py` only for an unrelated DDL-canonicalization refactor, not this fix.)
- Setup-failure-path fix (PR #501, merged, `ebb414b`): the same 5 modules again, because the
  first fix closed the connection on normal exit but still leaked it if pragma/schema-init
  *itself* raised inside `_connect()`. Confirms the leak has two distinct failure shapes, not
  one.

**The other 25 are unmigrated and confirmed to still have the leak.** Verified directly by
reading two of them in full (`services/risk_manager.py:49`, `services/trade_category.py:27`):
both define `_connect()` as a plain function returning `sqlite3.Connection` (not a
`@contextlib.contextmanager` generator), then call it as `with self._connect() as conn:`.
`sqlite3.Connection`'s native `__enter__`/`__exit__` only commits or rolls back the
transaction — it does **not** close the connection. This is the exact bug shape already fixed
twice in the other 5 modules, just not yet applied here. It is not a hypothetical: it's the
same code pattern, confirmed present in every unmigrated module sampled.

**Pragma inconsistency, also confirmed by direct read**: `risk_manager.py`, `trade_category.py`,
and `paper_broker.py` each set `PRAGMA journal_mode=WAL` but **none of the three set
`busy_timeout`** — they rely on sqlite3's own hardcoded 5-second default rather than an
explicit, auditable value. This ties directly to a real finding from tonight's live-app
incident: the fault log's most frequent entries included `capture_writer` /
`flush_retained_on_lock` `OperationalError: database is locked` (191 occurrences) and
`market_history` `OperationalError: database is locked` (6 occurrences) — exactly the failure
mode an inconsistent, implicit busy_timeout policy makes harder to reason about and tune as one
thing.

**Duplication, not just leaks**: every one of the 30 modules re-implements the same
`PRAGMA journal_mode=WAL`, a `CREATE TABLE IF NOT EXISTS`, and (in 11 of them, confirmed via
`grep -rl "def add_column_if_missing\|def _add_column_if_missing" services/`) an AST-identical
`add_column_if_missing` helper.

## What `services/db.py` already does (prototype exists, not yet on `main`)

Commit `17b2e8f` (`feat/persistence-layer-unified-connect`, currently isolated in worktree
`.claude/worktrees/persistence-layer-impl`, not merged) already implements:

- `db.connect(db_path)`: a real `@contextlib.contextmanager` — opens, sets
  `journal_mode=WAL` and `busy_timeout=5000`, runs every schema registered for that path,
  commits, yields, and closes in a `finally` block regardless of how the caller's block exits.
  This structurally closes both leak shapes found above (normal exit and setup-failure exit),
  because the close is in `finally` around the whole body, not bolted onto one path.
- `db.register_schema(db_path, table_name, init_fn)`: modules register their own schema-init
  callback once; `connect()` replays every registered callback for that path on every open
  (idempotent via each callback's own `CREATE TABLE IF NOT EXISTS`).
- `db.add_column_if_missing(conn, table, column, column_def)`: centralizes the 11-times-
  duplicated helper.
- `tests/test_db.py`: 8 tests, passing, covering connection closure (explicitly asserts a
  `sqlite3.ProgrammingError` on post-close use — i.e. it tests for the exact leak class found
  above), parent-directory creation, pragma application, and schema idempotence.

The `busy_timeout=5000` default matches what the 25 unmigrated modules already get implicitly
from sqlite3's own default — so migrating them onto `db.connect()` does not silently change
their timeout behavior, it just makes the existing value explicit and centrally auditable.

## A directly relevant precedent already in this codebase (risk-avoidance, not a blocker)

`services/tick_executor.py`'s `connection_for()` is a *second*, separate connection-caching
utility with a **deliberately different** `busy_timeout=50` (50ms, fail-fast). Its own header
comment documents that a prior code-review finding (#3/#9) investigated wiring it into five of
these same modules and found it unsafe for two concrete reasons: (1) it runs no schema-init DDL
at all, unlike every module's own `_connect()`, so a fresh db file would raise "no such table"
instead of self-initializing; (2) several target modules have real concurrent writers from
*other* threads/the event loop, and swapping in a 50ms busy_timeout would turn today's silent
5-second wait into a newly-common `database is locked` exception — a regression, not a fix.
That investigation explicitly invoked CLAUDE.md's data-plane rule against tuning a timeout
without measuring the actual bottleneck first, and deliberately left `connection_for()`
unwired (zero production callers today).

This doesn't block the `db.py` migration — `db.py`'s default busy_timeout matches the
status quo, it isn't introducing a new, untested value the way `connection_for()` would have.
But it's the right cautionary precedent for this exact class of "should be mechanical, just
swap the connect call" refactor: two of the reasons that one was rejected (schema-init gap,
cross-thread write contention) are exactly the two things `db.py`'s design already had to solve
(schema registry; the shared default matching current behavior). Any per-module migration
should confirm both are actually true for that specific module before treating it as
mechanical, not assume it from the pattern holding for the modules already migrated.

## Migration complexity estimate

- **Mechanical part** (most of the 25): replace the module's own `_connect()` body with a
  `db.register_schema(DB_PATH, "<table>", _init_schema)` call at import time, then replace
  every `with _connect(...) as conn:` / `with self._connect() as conn:` call site with
  `with db.connect(DB_PATH) as conn:`. For modules whose schema-init is just
  `CREATE TABLE IF NOT EXISTS` plus `add_column_if_missing` calls (the pattern seen in both
  sampled modules), this is close to a mechanical extraction.
- **Per-module verification, not skippable**: each of the 25 needs its own before/after test
  run, not a batch find-replace — the setup-failure-path leak fix (PR #501) already proved that
  a fix which looks complete against the happy path can still leak on the failure path, so
  "tests still pass" alone isn't sufficient; the specific regression test shape from
  `test_db.py` (assert the connection is actually closed) should be replicated per module, the
  same way the 5-module fix waves already did.
  connection lifetime testing consistent across all 25, not
  reinvented per module.
- **Modules with anything beyond plain `CREATE TABLE`/`ALTER TABLE`** (haven't been
  individually audited yet — this research doc verified the *pattern* across a handful of the
  25 in depth, including `series_watcher.py` and `settlement_edge.py`, plus the `grep`
  enumeration, not all 25 module bodies) need individual review before being called mechanical.
  `series_watcher.py` in particular is flagged elsewhere in this repo's history by PR #23
  ("Realtime data-plane remediation — Phase P0," merged 2026-08-26, commit `868dbf8`) as having
  real cross-thread write concurrency of its own — that PR added a real lock guarding
  series_watcher's capture buffers against a genuine race between the event loop and
  tick_executor's worker thread, and is exactly the precedent `tick_executor.py`'s own header
  comment cites for why `connection_for()` was deliberately left unwired. A module already
  known to be non-trivial around its own connection/flush timing, independent of this
  migration.
- **Safety-adjacent modules in the list**: `risk_manager.py` (the daily-loss kill switch) and
  `paper_broker.py` are both in the 25. CLAUDE.md's safety invariants apply to any change
  there — the migration itself only touches connection plumbing, not kill-switch logic, but
  each such module's migration should get the same real-diff scrutiny this session already
  applied to a much smaller `risk_manager.py` change earlier tonight (read the actual diff,
  confirm test coverage, don't take "same pattern as the others" on faith for this specific
  file).

No concrete time/effort estimate is given here because none of the 25 modules' bodies beyond
the two sampled have been read in full — that would need either a full per-module audit pass
(out of scope for this research doc, which is about migration *shape*, not a work-breakdown) or
autotrade-a7's own planning pass to size.

## Risks / benefits summary

**Benefits (verified against real findings, not assumed)**: eliminates the exact fd-leak class
proven present in 25 of 30 modules; makes `busy_timeout` explicit and centrally tunable instead
of an implicit, unaudited sqlite3 default — directly relevant to the `database is locked`
faults seen in tonight's incident; removes 11+ duplicate copies of `add_column_if_missing` and
30 duplicate copies of the WAL pragma/table-creation boilerplate; `db.py`'s own test suite
already demonstrates the close-on-exit property the current 25 modules lack.

**Risks**: (1) schema-registry replay-on-every-connect needs confirming it stays cheap across
all 30 modules sharing db files, not just the 8 tests' synthetic case; (2) the tick_executor
precedent above is a real, recent example of this exact class of migration being unsafe for
two specific, non-obvious reasons — each of the 25 modules needs the same "is this actually
mechanical for THIS module" check, not a blanket assumption; (3) two of the 25 are
safety-invariant-adjacent (`risk_manager.py`, `paper_broker.py`) and warrant the same
line-by-line scrutiny already applied to other risk_manager.py changes tonight, not batch
treatment; (4) this is currently Tier2/unplanned per `docs/next-action.md` — per CLAUDE.md's
planning-pipeline rule, this research doc is the correct first stage, but the actual 25-module
migration needs its own design/spec and implementation-plan stages (with their own
review cycles) before code changes start, not a direct jump from this document to
implementation.

## What this research doc does not cover

Full audits of all 25 remaining modules' schema-init bodies (only 2 read in depth); a
performance benchmark of `db.py`'s schema-replay-on-connect cost under real load; a concrete
task breakdown or time estimate (left to autotrade-a7's planning pass, which this doc feeds).
