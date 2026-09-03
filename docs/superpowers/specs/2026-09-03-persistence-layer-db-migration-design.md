# Persistence Layer db.py Migration — Design Spec

**Stage:** design/spec, per CLAUDE.md's "nothing advances on one pass" pipeline
(research → design/spec → implementation plan). Input: the research doc
(`docs/superpowers/research/2026-09-03-persistence-layer-db-migration.md`, PR #504,
merged, PR-stage review GO) plus two assigned feeder documents not yet merged —
`docs/persistence-layer-baseline-2026-09-03.md` (`feat/persistence-layer-baseline-measurement`,
commit `4130eb6`) and `docs/db-foundation-audit-2026-09-03.md` (`feat/db-foundation-audit`,
commit `d783877`). Output of this stage: a design this document's own review cycle clears GO,
which a later, separate implementation-plan stage then turns into ordered, testable tasks.

**Goal:** decide HOW the remaining ~25 `_connect()`-owning modules migrate onto a shared,
closing SQLite helper — resolving a real API-shape conflict between two independently-designed
`db.py` implementations that now both exist in this repo, fixing the concrete gaps a direct
audit already found in the more complete of the two, and defining migration gates precise
enough that an implementation plan can execute against them without re-deriving this analysis.

## The central problem this spec exists to resolve

Two different `services/db.py` designs now exist in this repo, produced independently,
same day, by different sessions, neither aware of the other until now:

1. **PR #484's Task 1** (merged as a *plan*, not yet implemented as code):
   `db.connect(db_path, *, tables: tuple[str, ...] = (), busy_timeout_ms: int = 5000)` +
   `db.register_ddl(table: str, ddl: str) -> None` — DDL registered as a **plain SQL string**,
   keyed globally by **table name alone** (`_DDL_REGISTRY: dict[str, str]`). Extra
   non-table-DDL setup (indexes, `add_column_if_missing` calls) has to run separately, on the
   yielded connection, after `db.connect()` returns — the registry can't express it, so every
   migrated module's `_connect()` wrapper ends up doing `with db.connect(...) as conn: conn.execute("CREATE INDEX ..."); ...; yield conn` around the registry call. Verified directly in PR
   #484's own Tasks 3/5/6.
2. **The prototype** (`feat/persistence-layer-unified-connect`, commit `17b2e8f`, real code,
   8 passing tests, not merged, not on `main`): `db.connect(db_path)` (no `tables` kwarg) +
   `db.register_schema(db_path: Path, table_name: str, init_fn: Callable) -> None` — schema
   registered as a **callback function**, keyed by **`(db_path, table_name)`**. A callback can
   run `CREATE TABLE` + `CREATE INDEX` + `add_column_if_missing` together in one registration,
   with no separate post-`connect()` step needed.

**Neither is implemented as running code on `main` yet** — PR #484 is a plan document (Task 1's
`services/db.py` has not been written), and the prototype sits unmerged in
`.claude/worktrees/persistence-layer-impl`. So this is a real design choice to make now, not a
question of picking between two things already shipped.

### Comparison

| | String-DDL / table-name-keyed (PR #484) | Callback / path+table-keyed (prototype) |
|---|---|---|
| Expresses `CREATE TABLE` alone | Yes | Yes |
| Expresses `CREATE TABLE` + indexes + `add_column_if_missing` in one registration | **No** — needs a second, manual step per migrated module (verified: every one of PR #484's Tasks 3/5/6 needed this) | **Yes** — the callback does whatever the module needs |
| Collision surface | Global table-name key — **two unrelated modules with same-named tables in different DB files would collide**, worse than the prototype's scope (not yet observed in practice, since table names have stayed distinct so far, but structurally wider) | `(db_path, table_name)` key — collision only possible between two registrations for the *same* table in the *same* file |
| Collision behavior, either design | Not applicable to PR #484's registry as designed (last-write-wins on a plain dict `_DDL_REGISTRY[table] = ddl`, silent) | **Confirmed real defect** (db-foundation-audit finding 3): first registration silently wins on a genuine `(db_path, table_name)` conflict, no error, no log line |
| Compatibility with `capture_writer.py`'s existing `RAW_TRADES_DDL_SQL` etc. constants | Direct — `db.register_ddl(table, capture_writer.X_DDL_SQL)` is exactly what PR #484's own PR-stage review fixed Tasks 3/5 to do | Also direct — a callback can do `conn.execute(capture_writer.X_DDL_SQL)` equally easily |
| Real, passing test suite already | No (Task 1 unimplemented) | Yes — 8 tests, independently re-executed by PR #504's PR-stage review (not merely read), confirmed `8 passed in 0.18s` |
| Already incorporates the PR #501 setup-time-failure lesson (`try:` starts right after `connect()`, before pragma/schema-init) | Yes, by design (PR #484's own Task 1 code block does this) | Yes, independently confirmed by the db-foundation-audit's direct source read |

**Decision: adopt the prototype's shape — callback-based `register_schema(db_path, table_name,
init_fn)` — as the design this spec's downstream implementation plan builds `services/db.py`
around, not PR #484's `register_ddl(table, ddl_string)`.**

Reasoning, weighted by mechanism and failure behavior per this repo's own standard for
comparing competing solution families:

- The callback shape structurally eliminates the "run extra DDL on the yielded connection after
  `connect()` returns" workaround every one of PR #484's three migrated modules needed — that
  workaround is not a hypothetical future problem, it already happened three times in the first
  three modules migrated under the string-DDL design, and the research/baseline docs both
  describe several of the remaining 25 modules (`series_watcher.py` sampled directly:
  `raw_trades` + `book_snapshots`, four indexes) as having the identical need. A design that
  needs a manual escape hatch in 3 of its first 3 real uses is a signal the primitive is
  under-scoped, not that the escape hatch is fine.
- The `(db_path, table_name)` collision surface is strictly narrower than PR #484's
  table-name-only surface, and — this is the more load-bearing point — **the prototype's
  collision bug is fixable in isolation** (raise or warn on a genuine conflict instead of
  silently keeping the first registration; db-foundation-audit's own suggested mitigation),
  whereas PR #484's design's *wider* collision surface (global table name, no path
  qualification at all) is a structural property of the chosen key, not a bug to patch.
- The prototype already has a real, independently-re-executed test suite; PR #484's Task 1 has
  none yet, since it was never implemented.

**This does not undo or contradict PR #484's own merged plan.** PR #484's Tasks 3/5/6 were
themselves already gated on Task 1 landing first ("Depends on: Task 1"), and Task 1 — build
`services/db.py` — has not been implemented. This spec's downstream implementation plan
supersedes PR #484's Task 1 with the callback-based design instead; Tasks 3/5/6's *actual*
migrations (which three specific modules move first, preserving every index/column-add call)
remain valid and are not re-litigated — only the underlying primitive they'd call changes shape.
A future implementation plan should explicitly re-target Tasks 3/5/6 (or their equivalent) at
`db.register_schema`/`db.connect(db_path)` instead of `db.register_ddl`/
`db.connect(db_path, tables=...)`.

## Required fixes to the prototype before any module migrates onto it

The db-foundation-audit (direct source read + test execution, not assumed) found five gaps.
Three are must-fix before the first real module migration; two are should-fix, tracked but not
blocking:

1. **Must-fix — silent schema-registration conflict** (audit finding 3, independently confirmed
   by autotrade-73's separate audit pass reaching the same conclusion). Change
   `register_schema` to raise (preferred) or at minimum log a warning when a second
   registration for the same `(db_path, table_name)` arrives with a **different** `init_fn`
   than the first. With ~25 modules migrating, several potentially in parallel sessions per
   this repo's own working pattern today, a silent first-wins collision would mask a real bug
   with no error and no log line — exactly the failure shape this repo's own incidents this
   session have repeatedly been about (uncoordinated parallel work silently overwriting or
   contradicting other work). Cheap to add; blocks nothing else.
2. **Must-fix — corrupted-DB-file handling has no test**, despite this being a real incident
   this app already had (`market_history.db`, `sqlite3.DatabaseError: database disk image is
   malformed`, 2026-09-03, `docs/next-action.md`). The code path should already handle it
   correctly by construction (the exception propagates through `finally: conn.close()` the same
   as any other exception, per PR #501's own already-incorporated lesson) — but "should" is not
   "verified," and this app has already needed to trust that exact code path once for real. Add
   a test: open `db.connect()` against a file with garbage bytes written to it, assert `close()`
   still ran regardless of when in the open/pragma/schema-init sequence the corruption is
   detected.
3. **Must-fix, documentation not code — capture_writer.py's retry wrapper must NOT be dropped
   during its migration.** `db.py` has no retry logic of its own beyond the single
   `busy_timeout` wait; `capture_writer.py`'s existing `flush_retained_on_lock` retry path
   (429+ real "database is locked" faults over 4+ days, per the baseline measurement) has to
   keep wrapping calls into `db.connect()`, not be silently assumed redundant because
   `db.connect()` "handles locking now." This is a real, concrete risk specific to this one
   module given its measured contention rate — the implementation plan's task for
   `capture_writer.py`'s own eventual migration (if and when it's picked up; it is not one of
   PR #484's three, and is not explicitly in the research doc's 30-module list either — see the
   scope-count correction below) must state this explicitly as an acceptance criterion, not
   leave it implicit.
4. **Should-fix, not blocking — no test for real lock contention.** `busy_timeout=5000` is set
   but nothing drives two genuine connections into contention to confirm the PRAGMA is doing
   what's intended at the SQLite-engine level, versus being an unverified value. Worth adding
   before `db.py` is trusted at scale, not before the first migration.
5. **Should-fix, not blocking, tradeoff to document — schema re-runs every `connect()` call**,
   not once per process. Each `CREATE TABLE IF NOT EXISTS` is itself a real disk check; for a
   `db_path` with several registered tables (e.g. `signal_log.py`'s multiple tables), this
   partially works against `db.py`'s own stated goal (per its docstring) of eliminating
   per-call connect overhead. Not a correctness bug. The implementation plan should name this
   as an accepted tradeoff (simplicity over micro-optimizing an already-cheap idempotent check)
   rather than silently assume it's free — matching the data-plane HARD RULE's "never change …
   because it 'should help' … identify the measured bottleneck first": there is no measurement
   yet showing this matters at this repo's actual table counts and connect frequency, so no
   change is proposed here, only the tradeoff is named for whoever later measures it.

Item 5 (`db.connect()` doesn't prevent event-loop-blocking misuse) from the audit is a real,
correctly-identified property but is **not a `db.py` defect to fix** — it's a per-call-site
discipline question identical to the one this repo's Tier0/P1 work already solved for five
other modules (route through `tick_executor.run()` from any FastAPI-event-loop caller). This
spec folds it into the migration gates below (every per-module migration task must state
explicitly whether that module's `_connect()` is ever called from the event loop directly, and
if so, confirm the migrated call site still routes through `tick_executor.run()` or equivalent)
rather than proposing a `db.py`-level enforcement mechanism the research/audit never asked for
and this spec has no measured justification to invent.

## Scope correction: 30 modules named, 38 files actually carry connection logic

The research doc's `grep -rln "def _connect" services/` (exactly 30 hits) is accurate for what
it searched, but the baseline measurement's broader
`grep -rl 'def _connect\|sqlite3.connect' services/ tools/ main.py` (38 hits) found the true
footprint is wider — the extra 8 files use `sqlite3.connect()` directly without a function
literally named `_connect()`, or live outside `services/`:

- **`services/store_stats.py`** — a genuine, previously-unlisted gap, not just a naming
  variance. It is **split-pattern**: one connection (`uri=True`, read-only) already closes
  correctly, but a second, `with sqlite3.connect(db_path) as conn:`, does not. Its own existing
  comment ("was a transaction context manager, not a—", cut off mid-sentence, per the baseline
  doc) suggests this was already a known, half-fixed issue before today. **Add to the migration
  scope** — it was missing from both PR #504's 30-module list and PR #484's Task 7 tracking
  list.
- **`services/backup/backup.py`** — also split-pattern (its primary `_connect()` leaks; a
  separate `src_conn`/`dest_conn` pair in the same file, used for the backup-copy operation,
  already closes correctly). **Already in PR #484's Task 7 tracking list** (as `backup/backup.py`)
  — no scope gap here, just worth noting the file needs a partial, not full, migration.
- **`tools/coordination_engine.py`** — same leaking shape, but a short-lived CLI script rather
  than a long-running server process; the OS reclaims its fds on process exit, so the
  fd-exhaustion risk this creates is real but operationally much lower priority than any
  server-resident module. **Add to migration scope, lowest priority** (opportunistic, same
  tracking-issue treatment as the general 22-module bucket, not urgent).
- **`services/capture_writer.py`, `services/storage_health.py`, `tools/historical_data_backfill.py`,
  `tools/quality_ratchet.py`** — already close correctly (confirmed by the baseline's own
  per-file check, not grep-shape alone). **Not in scope** — no defect to migrate.
- **`services/tick_executor.py`'s `connection_for()`, `services/whalewatchers/_scoring_pool.py`,
  `services/diagnostics/_aio_db.py`** — pooled/cached, structurally different from the
  per-call-open-close pattern this migration addresses. **Not in scope** for this migration
  (see "Considered and declined: pooling" below for why `_aio_db.py`'s pattern specifically is
  not being adopted wholesale here, despite being a real, working precedent).

**Corrected scope: 27 modules to migrate onto the new `services/db.py`** — the research doc's
25 (`services/`-only, `_connect()`-only), plus `store_stats.py` (partial — one of its two
connections) and `tools/coordination_engine.py` (lowest priority). `backup.py` was already
counted in the 25 (partial migration, same as `store_stats.py`).

## Considered and declined: pooling (`_aio_db.py`'s pattern), for this migration

The baseline measurement names `diagnostics/_aio_db.py` — one persistent `aiosqlite.Connection`
per (event loop, db_path) pair, already working — as "a real, already-proven-safe model for the
pooled approach rather than something to design from scratch." This is accurate, and worth
recording rather than silently not considering it: a pooled design would eliminate the
per-call connect/close overhead entirely, not just make it safe.

**Declined for this migration's scope, not declined as a future idea**, for three concrete
reasons:

1. `_aio_db.py`'s pattern is `asyncio`/`aiosqlite`-based. The 27 modules in this migration's
   scope are sync `sqlite3` callers (the research doc's own §2 explicitly defers any
   `aiosqlite` migration to a separate, unstarted plan, citing the design's own admission that
   the necessary before/after benchmark against trading-critical code was never done). Adopting
   pooling here would silently bundle the aiosqlite migration into this one, which is exactly
   the kind of undisclosed scope expansion this repo's HARD RULE process exists to prevent.
2. `tick_executor.py`'s `connection_for()` is this repo's own, real, recent precedent for why a
   confident-looking connection-management refactor across many modules can be unsafe for
   non-obvious reasons specific to *this* app: it was investigated for exactly this kind of
   wiring, found unsafe for two concrete reasons (no schema-init DDL; a 50ms busy_timeout that
   would convert today's silent 5-second wait into a newly-common lock exception under real
   cross-thread contention), and deliberately left unwired. A pooled design changes lock/timeout
   dynamics in ways the close-on-exit-per-call design does not — it needs the same rigor
   `connection_for()` got, not less, and that rigor is out of scope for a spec whose job is to
   fix a leak, not redesign the concurrency model.
3. Per the data-plane HARD RULE, capacity/pooling changes require a measured bottleneck and
   mechanism first, not "should help." Nothing in the research, baseline, or audit documents
   measures per-call connect/close overhead as the actual bottleneck for any of the 27 modules
   (the measured problems are fd exhaustion from never closing, and lock contention from
   uncoordinated concurrent writers — both are fixed by closing-on-exit; neither requires
   pooling to fix).

Recorded here so a future session doesn't have to re-discover `_aio_db.py`'s precedent from
scratch, and so choosing not to pool is a stated decision, not a silent default.

## Module classification for the implementation plan

Per-module classification the implementation plan should use directly, built from the research
doc's sampling plus the baseline's full 38-file pass:

- **Safety-adjacent — individual, dedicated PR required, never bundled** (repeating PR #484's
  own already-established convention for this exact category): `risk_manager.py` (daily-loss
  kill switch), `paper_broker.py`, `candidate_ledger.py`. Each migration touches only connection
  plumbing, not kill-switch/trading logic — but each gets the same real-diff scrutiny CLAUDE.md's
  safety invariants require for any change in these files, not "same pattern as the others"
  taken on faith.
- **Closer to the live trading path than the general bucket, lighter caution** (per PR #484's
  own finding, carried forward unchanged — this spec found nothing to add or remove from this
  pair): `series_evaluator.py`, `trade_category.py`.
- **Individually audited already, real complexity beyond mechanical extraction confirmed**:
  `series_watcher.py` — PR #23 ("Realtime data-plane remediation — Phase P0," merged
  2026-08-26) added a real lock guarding this module's capture buffers against a genuine
  cross-thread race between the event loop and `tick_executor`'s worker thread. Its migration
  needs individual review for interaction with that existing lock, not mechanical extraction.
  `settlement_edge.py` was also sampled in the research doc as non-trivial; both need
  individual audit before being called mechanical, not assumed safe from the pattern holding
  for already-migrated modules.
- **Size/cost outlier, scope decision needed before migrating**: `series_watcher.py`'s own
  `series_watcher.db` is 29.7 GB — an order of magnitude larger than every other store
  (`candidate_log.db` at 3.76 GB is the next largest). This spec does not decide whether
  `series_watcher.py` migrates in the first wave or is deliberately deferred — that is a real
  scope/sequencing call belonging to the implementation plan, informed by whether the migration
  itself requires any operation whose cost scales with file size (schema-replay-on-every-connect
  does not rewrite the file, so a straight migration is likely cheap regardless of size — but
  this is stated as reasoning, not measured, per the data-plane HARD RULE, and the
  implementation plan should confirm it before treating `series_watcher.py` as low-risk purely
  because the connection-close fix itself is mechanical).
- **Split-pattern, partial migration only**: `backup/backup.py`, `services/store_stats.py` (new
  finding, see scope correction above) — only the leaking half of each file's connection logic
  is in scope; the half that already closes correctly is explicitly untouched.
- **General opportunistic bucket** (no individual flag, same "one PR per module or small
  low-risk batch, tracking issue not a hard deadline" treatment PR #484's Task 7 already
  established): the remaining ~20 modules, plus `tools/coordination_engine.py` at lowest
  priority given its short-lived-process risk profile.

## Migration gates

**Gate 0 — before any module migrates (blocks the whole migration, not per-module):**
- The three must-fix items above (silent schema-conflict, corrupted-DB test, capture_writer
  retry-wrapper documentation) land in `services/db.py` and its own test suite.
- The API-shape decision above (`register_schema`/callback, not `register_ddl`/string) is the
  one this spec's downstream implementation plan targets — flagged here as the single
  highest-stakes call this spec makes; see "Open question for explicit sign-off" below.

**Gate 1 — before each individual module's migration is considered mechanical:**
- Read that module's actual `_connect()` (or equivalent) body in full, current-source, not
  from this spec's or any prior document's summary — matching this repo's own
  Task-Step-2-"read before editing" convention already used throughout PR #484 and Tier0.
- Confirm every non-`CREATE TABLE` statement (indexes, `add_column_if_missing` calls) the
  module's current connect function runs is preserved in its `register_schema` callback —
  the exact regression class PR #484's Task 3's own test
  (`test_connect_still_creates_both_tables_indexes_and_unit_cost_columns`) exists to catch,
  generalized to every module now, not just the three PR #484 covered.
- A real "connection is actually closed" regression test per module, not "tests still pass" —
  per the research doc's own point: a fix that looks complete against the happy path already
  proved (PR #501) it can still leak on the setup-failure path.
- Confirm whether the module's connect function is ever called from the FastAPI event loop
  directly (not only from a background thread/`tick_executor.run()`); if so, the migrated call
  site must keep that routing — this migration must not reintroduce the event-loop-blocking
  bug class Tier0/P1 already fixed elsewhere.
- Safety-adjacent modules (`risk_manager.py`, `paper_broker.py`, `candidate_ledger.py`) get
  their own dedicated PR, full diff review, never bundled with an unrelated change or with each
  other.
- `series_evaluator.py`/`trade_category.py` get the lighter "don't bundle silently as routine"
  caution PR #484 already established.
- `series_watcher.py`/`settlement_edge.py` get individual audit for the specific
  non-mechanical concern named above before being scheduled, not batched with the general
  bucket.

**Gate 2 — after each module (or small batch) migrates:**
- Full local suite passes; `import main` sanity check.
- Live fd-count check post-deploy (Tier0's Task 9 already added the process-wide `open_fds`
  counter this migration can reuse — no new instrumentation needed for a coarse signal, though
  no per-module fd attribution exists yet if that granularity is ever wanted).
- For any module appearing in the fault-log contention data (`capture_writer.py`'s eventual
  migration specifically, if picked up — 429+ "database is locked" faults over 4+ days),
  re-check that rate post-migration the same way Tier0's own Task 4/Task 10 pattern already
  established for `candidate_log.db`'s contention fix.

## Non-goals (explicitly deferred, not silently in scope)

- Any `aiosqlite` migration for these 27 modules (the research doc's item 16 territory —
  separately deferred, unstarted).
- A DuckDB/Parquet export or any change to `raw_trades`'s system-of-record status (unrelated
  question, already resolved by PR #484's own Task 9 for the 3 modules it covered; not
  reopened here).
- Adopting `_aio_db.py`'s pooling pattern for these 27 modules (see "Considered and declined"
  above).
- A schema-replay-performance benchmark at real table counts (named as a should-fix tradeoff
  above, not measured here — no task in the downstream implementation plan should assume this
  spec cleared it).
- Deciding `series_watcher.py`'s migration wave/sequencing (flagged as an implementation-plan
  decision above, informed but not made here).
- Full individual audits of all 27 modules' connect-function bodies beyond the ones already
  sampled directly (`risk_manager.py`, `trade_category.py`, `series_watcher.py`,
  `settlement_edge.py`, `store_stats.py`, `backup.py`) — Gate 1 above is how the remaining
  bodies get read, at migration time, not pre-audited here.

## Open question for explicit sign-off

**The API-shape decision (callback/`register_schema` over string/`register_ddl`) is this
spec's single highest-stakes call** — it effectively supersedes what PR #484's own,
already-merged Task 1 planned to build, based on a prototype that PR #484's own authoring
session did not know existed. This is exactly a "genuine design/architecture decision" this
repo's own take-the-wheel carve-out reserves for a human call rather than an AI-executed
default — flagged explicitly for this spec's own adversarial review to scrutinize the
reasoning above on its merits, and for the coordinator/user to confirm before an implementation
plan is drafted against it, rather than this spec silently deciding it standalone the way `git
commit`-level judgment calls are normally fine to make autonomously.
