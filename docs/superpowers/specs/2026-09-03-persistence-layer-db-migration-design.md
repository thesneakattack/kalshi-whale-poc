# Persistence Layer db.py Migration — Design Spec

**Stage:** design/spec, per CLAUDE.md's "nothing advances on one pass" pipeline
(research → design/spec → implementation plan). Input: the research doc
(`docs/superpowers/research/2026-09-03-persistence-layer-db-migration.md`, PR #504,
merged, PR-stage review GO) plus two assigned feeder documents — originally cited as unmerged
at drafting time, **now also merged** (PR-stage correction): `docs/persistence-layer-baseline-2026-09-03.md`
(PR #509, merge commit `bbebbbe`) and `docs/db-foundation-audit-2026-09-03.md` (PR #507, merge
commit `6635815`) — both now on `main` alongside their own review cycles. Output of this stage:
a design this document's own review cycle clears GO, which a later, separate implementation-plan
stage then turns into ordered, testable tasks.

**PR-stage correction — a fourth input this document should have cited from the start, found by
this PR's own required adversarial review reading the whole document end to end**: `main`
already has a separate, merged, GO'd design-stage document,
`docs/superpowers/specs/2026-09-03-persistence-layer-redesign-design.md`, whose §1.4 is the
actual origin of the `register_ddl(table, ddl)`/`_DDL_REGISTRY: dict[str, str]` API this
document attributes only to "PR #484's Task 1" throughout — PR #484's Task 1 transcribes that
design's code verbatim (its own text says so), rather than inventing it. This document's central
recommendation therefore does not merely supersede a plan-stage task; it revises an
already-independently-reviewed, GO'd **design-stage decision**, made without either session
being aware of the other — the same "two sessions, same day, unaware of each other" failure
this document's own opening section describes, recurring one stage later, about this document
itself. Named explicitly here, and restated in "Open question for explicit sign-off" below, so
the reader deciding whether to sign off knows the true weight of what they're being asked to
override.

**Revision note (this document's own required PR/artifact-stage adversarial review found 2
Critical + 5 Important + 7 Minor findings against the first draft — all applied below, not a
silent rewrite):** the first draft's headline scope finding (`services/store_stats.py`) was a
grep artifact — the reviewer opened the actual file and found it already correctly closes its
one connection; the match was a docstring quoting old, already-deleted code. And the first
draft's recommended API design (path-keyed schema registry) was independently demonstrated,
by actually running it, to break this repo's universal `monkeypatch.setattr(mod, "DB_PATH",
tmp_path/...)` test convention used by 64 test files. Both are fixed below; see "Revision log"
at the end of this document for the full, itemized disposition of every finding.

**Goal:** decide HOW the remaining 26 `_connect()`-owning modules migrate onto a shared,
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
   #484's own Tasks 3/5/6 (all three needed the escape hatch — 3 of 3, confirmed by this
   document's own adversarial review reading the merged plan text directly, not assumed).
2. **The prototype** (`feat/persistence-layer-unified-connect`, commit `17b2e8f`, real code,
   8 passing tests — independently re-executed twice now, once by PR #504's PR-stage review and
   again by this document's own adversarial review, both times `8 passed`): `db.connect(db_path)`
   (no `tables` kwarg, no `busy_timeout_ms` override — hardcoded to 5000) +
   `db.register_schema(db_path: Path, table_name: str, init_fn: Callable) -> None` — schema
   registered as a **callback function**. **Correction from this document's first draft**: this
   is not keyed by `(db_path, table_name)` as a composite dict key — `services/db.py:27` at
   `17b2e8f` is `_SCHEMAS: dict[Path, list[tuple[str, Callable]]]`, a `db_path`-keyed dict of
   ordered lists, checked with a linear scan. The practical collision scope is the same
   (a genuine conflict is still only possible between two registrations for the same table in
   the same file), but registration order — not stated anywhere in the module — determines
   schema-init replay order, which matters if one table's `init_fn` ever depends on another's
   existing.

**Neither is implemented as running code on `main` yet** — PR #484 is a plan document (Task 1's
`services/db.py` has not been written), and the prototype sits unmerged in
`.claude/worktrees/persistence-layer-impl`. So this is a real design choice to make now, not a
question of picking between two things already shipped.

### Comparison

| | String-DDL / table-name-keyed (PR #484) | Callback / `db_path`-keyed (prototype, `17b2e8f`) |
|---|---|---|
| Expresses `CREATE TABLE` alone | Yes | Yes |
| Expresses `CREATE TABLE` + indexes + `add_column_if_missing` in one registration | **No** — needs a second, manual step per migrated module (verified: every one of PR #484's Tasks 3/5/6 needed this, 3 of 3) | **Yes** — the callback does whatever the module needs |
| `busy_timeout_ms` override per call | **Yes** — `connect(db_path, *, busy_timeout_ms=5000)` | **No** — hardcoded `_DEFAULT_BUSY_TIMEOUT_MS = 5000` at `db.py:31`, no override parameter at all |
| `tables=()` opt-out for read-only/diagnostic callers (run no DDL) | **Yes**, explicit default | **No** — `connect()` replays every registered `init_fn` for that `db_path` on every call, no way to skip |
| Behavior under this repo's `monkeypatch.setattr(mod, "DB_PATH", tmp_path/...)` test convention (used in 64 test files, per direct grep) | **Works** — `tables=` is passed explicitly at each `connect()` call, so a monkeypatched path never depends on what was registered against the real path | **Broken, demonstrated by actually running it**: registering against the real `DB_PATH` at import time, then calling `db.connect()` against a `monkeypatch`-substituted path, silently finds an empty schema list for that path and fails downstream with `no such table` — no error at registration or connect time |
| Collision surface | Global table-name key | `db_path` + table-name (narrower in principle, but see "under test convention" row above — this is the axis that actually matters for a 26-module migration, and it inverts the ranking) |
| Collision behavior, either design | `_DDL_REGISTRY[table] = ddl` — last-write-wins, silent | `if not any(name == table_name ...): append(...)` — **first**-write-wins, silent (confirmed at `db.py:39-41`) |
| Compatibility with `capture_writer.py`'s existing `RAW_TRADES_DDL_SQL` etc. constants | Direct — `db.register_ddl(table, capture_writer.X_DDL_SQL)` is exactly what PR #484's own PR-stage review fixed Tasks 3/5 to do | Also direct — a callback can do `conn.execute(capture_writer.X_DDL_SQL)` equally easily |
| Already incorporates the PR #501 setup-time-failure lesson (`try:` starts right after `connect()`, before pragma/schema-init) | Yes, by design | Yes — confirmed by direct source read: `db.py:51` `conn = sqlite3.connect(...)`, `:53` `try:` on the very next statement, PRAGMAs and schema-init both inside it |

**Decision, revised from this document's first draft: adopt neither design as-is.** Take the
callback (`init_fn`) from the prototype — it is the prototype's genuine, load-bearing strength,
confirmed necessary by all three of PR #484's own migrated modules needing a workaround without
it. Take PR #484's **addressing model** (explicit `tables=` selection passed at each `connect()`
call, plus a `busy_timeout_ms` override) instead of the prototype's `db_path`-keyed registry —
because the prototype's registry, run for real against this repo's own standard test pattern,
produces a silent `no such table` failure with no error at either registration or connect time.
This is not a hypothetical edge case: `monkeypatch.setattr(mod, "DB_PATH", ...)` is how every one
of this repo's 64 relevant test files isolates its database, and it would hit on first contact
with the migration's own test suite, not in some rare production scenario.

**Reference shape** (illustrative, not a full implementation — the downstream implementation
plan's own Task 1 writes the real code and its own tests):

```python
_SCHEMAS: dict[str, Callable[[sqlite3.Connection], None]] = {}

def register_schema(table_name: str, init_fn: Callable[[sqlite3.Connection], None]) -> None:
    """Registered once, at import time, keyed by table name alone (not db_path) - so a
    caller's later db.connect(monkeypatched_path, tables=("t",)) finds the same registered
    init_fn regardless of which literal path is passed at connect time. Raises on a genuine
    conflict (a different init_fn already registered for this table name) rather than
    silently keeping the first one - the db-foundation-audit's own must-fix #1, applied here
    at the table-name granularity this design actually uses."""
    existing = _SCHEMAS.get(table_name)
    if existing is not None and existing is not init_fn:
        raise ValueError(f"conflicting schema registration for table {table_name!r}")
    _SCHEMAS[table_name] = init_fn


@contextlib.contextmanager
def connect(db_path: Path, *, tables: tuple[str, ...] = (), busy_timeout_ms: int = 5000):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
        for table in tables:
            _SCHEMAS[table](conn)
        with conn:
            yield conn
    finally:
        conn.close()
```

This combines: the callback's expressiveness (no post-`connect()` escape hatch needed — a
callback runs `CREATE TABLE` + indexes + `add_column_if_missing` together, exactly like the
prototype); PR #484's explicit `tables=()` default (no DDL runs for read-only/diagnostic
callers) and `busy_timeout_ms` override (this repo already differentiates busy timeouts by call
context today — `capture_writer.py`'s `_DAEMON_BUSY_TIMEOUT_MS = 1000` vs.
`_CALLER_BUSY_TIMEOUT_MS = 50`, `tick_executor.py`'s `busy_timeout = 50` — a fixed 5000ms with no
override cannot express what this repo already does); and a raise-on-conflict registration,
closing the silent-collision gap at the granularity this design actually uses (table name).

**Two implementation details the reference shape above is illustrative, not final, about**
(the downstream implementation plan's own Task 1 writes the real code and must not silently
regress either): the prototype's `mkdir` call uses `parents=True` (`db.py:50`) — carried into
the reference shape above rather than the earlier draft's `exist_ok=True`-only version, since a
`db_path` under a not-yet-existing nested directory (exactly what `tmp_path`-based tests can
produce) would otherwise raise. The prototype also guards `_SCHEMAS` with a lock
(`_SCHEMAS_LOCK`, `db.py:28`) for concurrent registration/connect from multiple threads — not
shown in the illustrative shape above for brevity, but Task 1 must include it; the raise-on-
conflict check this spec adds makes concurrent-registration safety more, not less, important
than it was in the prototype, since two modules genuinely racing to register different
`init_fn`s for the same table name should raise deterministically, not depend on scheduling.

**A real, already-shipped precedent for the callback shape, missed in this document's first
draft**: `services/diagnostics/_aio_db.py:187-191` already has a `schema_init: Callable[...] |
None` parameter that "runs exactly once - only on this key's first-ever open, never on a cache
hit" (`:191`, quoted verbatim). This is direct,
in-production evidence the callback approach is not a new, unproven idea for this codebase —
it's already shipped and operating. Whether the new `services/db.py` should adopt this same
"run once, not every connect" semantic (closing the schema-replay-per-connect cost named below)
is left to the implementation plan to decide and test, rather than specified here as part of
this spec's own reference shape — doing so correctly requires its own cache-invalidation
reasoning (what happens on a fresh `tmp_path` per test run, whether a process-lifetime cache is
even desirable for a module that legitimately reconnects to different paths) that this spec has
not verified and should not assert as safe without that verification.

**This does not undo or contradict PR #484's own merged plan.** PR #484's Tasks 3/5/6 were
themselves already gated on Task 1 landing first ("Depends on: Task 1"), and Task 1 — build
`services/db.py` — has not been implemented. This spec's downstream implementation plan
supersedes PR #484's Task 1 with the reference shape above; Tasks 3/5/6's *actual* migrations
(which three specific modules move first, preserving every index/column-add call) remain valid
and are not re-litigated — only the underlying primitive they'd call changes. A future
implementation plan should explicitly re-target Tasks 3/5/6 (or their equivalent) at
`db.register_schema`/`db.connect(db_path, tables=..., busy_timeout_ms=...)` instead of
`db.register_ddl`.

## Required fixes to the prototype before any module migrates onto it

The db-foundation-audit and this document's own adversarial review, both reading `services/db.py`
at `17b2e8f` directly (not assumed), found the following. Since the reference shape above is not
a straight adoption of the prototype, each item is restated against the reference shape, not the
prototype verbatim.

**Pointer, deliberately not expanded here**: a follow-on branch off the same prototype,
`fix/db-foundation-must-fix-tests` (commit `e74096a`, confirmed one commit ahead of `17b2e8f`),
already attempts fixes for some of the items below — but it is still built on the prototype's
original path-keyed registry (still carries the C2 defect this document's own revision exists to
avoid) and pre-dates this document's reference-shape decision. It is **input to the
implementation plan's Task 1, not itself Task 1** — which parts of its work are reusable (its
test *scenarios*, not its registry implementation) is a plan-stage judgment call, made there with
the full context of what it does and doesn't carry over, not decided in this design document.

1. **Must-fix — silent schema-registration conflict.** Confirmed present in the prototype
   (`db.py:39-41`, first-write-wins, silent, no error/log line) and addressed directly in the
   reference shape above (`register_schema` raises on a genuine conflict). Real risk given

1. **Must-fix — silent schema-registration conflict.** Confirmed present in the prototype
   (`db.py:39-41`, first-write-wins, silent, no error/log line) and addressed directly in the
   reference shape above (`register_schema` raises on a genuine conflict). Real risk given
   ~26 modules migrating, potentially across parallel sessions per this repo's own working
   pattern today — a silent collision would mask a real bug with no signal at all.
2. **Must-fix — corrupted-DB-file handling has no test**, despite this being a real incident
   this app already had. **Citation correction from this document's first draft**: the
   authoritative record is `data/fault_log.db`, not `docs/next-action.md` (which contains no
   mention of this incident) — `market_history/record_snapshot_from_ticker`, `DatabaseError`,
   "database disk image is malformed," count 45, **2026-09-02 18:21 → 20:44 UTC** (not
   2026-09-03). The code path should already handle this correctly by construction (the
   exception propagates through `finally: conn.close()` the same as any other exception, per
   PR #501's own already-incorporated lesson, confirmed present in the reference shape above
   too) — but "should" is not "verified," and this app has already needed to trust that exact
   code path once for real. Add a test: open `connect()` against a file with garbage bytes
   written to it, assert `close()` still ran regardless of when in the sequence the corruption
   is detected.
3. **Must-fix, documentation not code — `capture_writer.py`'s existing retry mechanism must NOT
   be dropped during its own eventual migration.** **Correction from this document's first
   draft**: the fault name is `flush_retained_on_lock`; the retry mechanism itself is
   `capture_writer.py`'s `_retain()` function (defined `:329`, invoked from the lock-error
   handler at `:403-414` where the fault is actually recorded — `:412`'s
   `fault_log.record("capture_writer", "flush_retained_on_lock", ...)` confirms the string is a
   label, not the code path), not a function literally named `flush_retained_on_lock`.
   `services/db.py` (either design) has no retry logic of its own beyond the single
   `busy_timeout` wait, so `capture_writer.py`'s existing retain-and-retry-next-cycle behavior
   has to keep wrapping calls into `connect()`, not be assumed redundant. **Correction to the
   contention figure**: the commonly-cited "429 over 4+ days" combines two sequential code eras
   across an 11-minute boundary where the fault was renamed (`capture_writer/flush`, 237
   occurrences, ends 2026-08-30 16:08 UTC; `capture_writer/flush_retained_on_lock`, 192
   occurrences, begins 2026-08-30 16:19 UTC — the issue #211 retain-on-lock fix landing; a small
   third row, `capture_writer/flush`, count 2, 2026-09-02 13:01-13:06 UTC, exists after the
   boundary too — the rename wasn't perfectly complete, but 2 occurrences doesn't change the
   rate below). The *current-regime* rate is 192 (growing live; ~53/day) over roughly 3.6 days
   since that fix, not 429 over 4 — still real, still supports this must-fix, but an
   implementation plan sizing a
   post-migration re-check window off the combined figure would size off the wrong number. This
   module is not one of PR #484's three migrated modules and is not in the research doc's
   30-module list (it already closes its connections correctly — see the scope section below);
   this item applies only if/when `capture_writer.py` is itself migrated onto `db.py` for some
   other reason (e.g. consistency), which is not proposed by this spec.
4. **Should-fix, not blocking — no test for real lock contention.** `busy_timeout=5000` (or the
   reference shape's `busy_timeout_ms` default) is set but nothing drives two genuine
   connections into contention to confirm the PRAGMA does what's intended at the SQLite-engine
   level. Worth adding before `db.py` is trusted at scale, not before the first migration.
5. **Should-fix, not blocking, tradeoff to document — schema re-runs every `connect()` call.**
   Each `CREATE TABLE IF NOT EXISTS` (and, per the reference shape, each callback's own
   `PRAGMA table_info` scans for `add_column_if_missing` calls) is a real disk check, replayed
   on every connect. **Correction from this document's first draft, illustrated with**
   `services/signal_log.py` **(note: one of Tier0's five already-migrated modules, not one of
   this spec's own 26 — used here only as an illustration of the general tradeoff's magnitude,
   not as an in-scope migration example)**: it has exactly **one** table (`signals`), not
   "multiple," and **7** `_add_column_if_missing` calls (its real, underscore-prefixed name in
   this module — re-counted directly against source, the document's own prior "8" figure
   counted that function's own `def` line as an eighth call), not 8 — 4 indexes + 7
   `_add_column_if_missing` calls = 11 statements (7
   `PRAGMA table_info` scans among them), all replayed every connect. `_aio_db.py:191`'s "runs
   exactly once" precedent (named above) is the existing, proven answer to this tradeoff, if and
   when the implementation plan decides to adopt it — not adopted in this spec's own reference
   shape, per the reasoning given above.

Event-loop-blocking misuse (the audit's own item 5) is a real, correctly-identified property but
is **not a `db.py` defect to fix** — it's a per-call-site discipline question identical to the
one this repo's Tier0/P1 work already solved for five other modules (route through
`tick_executor.run()` from any FastAPI-event-loop caller). This spec folds it into the migration
gates below rather than proposing a `db.py`-level enforcement mechanism no input document asked
for.

## Scope correction: 30 modules named, 26 to migrate — not 27

**Correction from this document's first draft, which incorrectly added a 27th module.** The
research doc's `grep -rln "def _connect" services/` (exactly 30 hits, independently re-confirmed)
undercounts the true footprint slightly, but not the way the first draft claimed:

- **`services/store_stats.py` does not exist. The real file is `services/diagnostics/store_stats.py`,
  and it already correctly closes its one connection — this is not a migration candidate at
  all.** This document's first draft claimed a "split-pattern leak" here, sourced from the
  baseline measurement's broader grep matching a line *inside the module's own docstring*
  (`store_stats.py:8`), which quotes the module's **old, already-deleted** code as part of
  documenting a **completed** fix (issue #210, 2026-08-30): *"The old `with
  sqlite3.connect(...)` was a transaction context manager, not a closing one... Connections are
  opened read-only (`mode=ro`) and closed"* (`:38-41`). The file's actual, current connection
  (`:105`, `uri=True`, read-only) closes correctly in a `finally:` block (`:134-136`). **Removing
  this module from scope entirely** — migrating it as the first draft proposed would have been
  an active regression: `connect()`'s reference shape above opens a read-write handle and would
  run schema DDL against a live capture DB on a diagnostic read path, exactly the bug class
  `store_stats.py`'s own docstring documents as already fixed. It also carries hard-won
  performance findings (a measured 70.5s `COUNT(*)` against `series_watcher.db`) a mechanical
  migration would risk putting back on a blocking path.
- **`tools/coordination_engine.py`** — genuinely leaking (confirmed: `sqlite3.connect()` with no
  matching `.close()` anywhere in the file), outside `services/` so outside the research doc's
  30-module count. A short-lived CLI script, not a long-running server process — the OS
  reclaims its fds on exit, so the fd-exhaustion risk here is real but operationally much lower
  priority than any server-resident module. **PR-stage correction, a real and distinct risk this
  module carries that no other module in scope does**: `_connect()` (`:29`) has zero in-file
  production references, but a peer session's independent read (not self-caught) found it **does**
  have real production callers outside its own file: `tools/quality_coordination.py:584`
  (`run_detect_cycle()`) and `:657` (`main()`'s `--clean` handler), both `conn = ce._connect()`,
  confirmed zero `.close()` anywhere in that file either. **This document's immediately-prior
  PR-stage correction claimed the callers are "exclusively in `tests/`" — that was wrong.** The
  accurate picture: 22 test call sites across 4 test files plus these 2 production sites in
  `tools/quality_coordination.py`, all 24 sharing the identical
  **bare assignment** shape (`conn = ce._connect()`, not `with ce._connect() as conn:`). Migrating
  it to a `@contextlib.contextmanager`-returning shape (this migration's whole approach) would
  make every one of those 24 call sites receive a generator-context-manager object instead
  of a `sqlite3.Connection` — `conn.execute(...)` would fail immediately, not leak silently.
  `tools/quality_coordination.py` is itself confirmed a standalone CLI tool too (`python -m
  tools.quality_coordination`, zero references from `main.py` or `services/` — never imported
  into the long-running FastAPI process), so the priority reasoning is unaffected, but the
  migration task's own scope spans six files (`coordination_engine.py`,
  `tools/quality_coordination.py`, and the four test files named in the Gate 1 addition below),
  not one module plus a single test file. This is the one module in scope whose call-site
  *shape* (not just its schema/DDL) must
  be part of its own migration task — Gate 1
  below is updated to say so explicitly rather than assuming "leaking" implies the same
  with-statement shape every other module uses. **Add to migration scope, lowest priority**
  (opportunistic, same tracking treatment as the general bucket, not urgent) — the priority
  ranking is unchanged (this is still not a production fd-exhaustion risk to the long-running
  app), only the migration task's own scope (touch `tools/quality_coordination.py` and all four
  test files named below, not just the module itself and one test file) is corrected.
- **`services/backup/backup.py`** — split-pattern (its primary `_connect()` leaks; a separate
  `src_conn`/`dest_conn` pair used for the backup-copy operation already closes correctly).
  **Already in PR #484's Task 7 tracking list** — confirmed by reading that list directly
  (`docs/superpowers/plans/2026-09-03-persistence-layer-implementation.md`, Task 7) — no scope
  gap, just a partial (not full-file) migration when picked up.
- **`services/capture_writer.py`, `services/storage_health/storage_health.py`,
  `tools/historical_data_backfill.py`, `tools/quality_ratchet.py`** — already close correctly.
  **Not in scope.**
- **`services/tick_executor.py`'s `connection_for()`, `services/whalewatchers/_scoring_pool.py`,
  `services/diagnostics/_aio_db.py`** — pooled/cached, structurally different. **Not in scope**
  for this migration (see "Considered and declined: pooling" below).

**Corrected scope: 26 modules** — the research doc's 25 (`_connect()`-named, `services/`-only,
Tier0's 5 already excluded) plus `tools/coordination_engine.py`, lowest priority.
`backup/backup.py` was already counted inside the 25 (partial migration).

## Considered and declined: pooling (`_aio_db.py`'s pattern), for this migration

The baseline measurement names `diagnostics/_aio_db.py` — one persistent `aiosqlite.Connection`
per (event loop, db_path) pair, already working — as "a real, already-proven-safe model for the
pooled approach rather than something to design from scratch." This is accurate, and worth
recording rather than silently not considering it: a pooled design would eliminate the
per-call connect/close overhead entirely, not just make it safe.

**Declined for this migration's scope, not declined as a future idea**, for three concrete
reasons:

1. `_aio_db.py`'s pattern is `asyncio`/`aiosqlite`-based. The 26 modules in this migration's
   scope are sync `sqlite3` callers (the research doc's own §2 explicitly defers any
   `aiosqlite` migration to a separate, unstarted plan, citing the design's own admission that
   the necessary before/after benchmark against trading-critical code was never done). Adopting
   pooling here would silently bundle the aiosqlite migration into this one, which is exactly
   the kind of undisclosed scope expansion this repo's HARD RULE process exists to prevent.
2. `tick_executor.py`'s `connection_for()` is this repo's own, real, recent precedent for why a
   confident-looking connection-management refactor across many modules can be unsafe for
   non-obvious reasons specific to *this* app — independently re-confirmed by this document's
   own adversarial review reading `tick_executor.py`'s header comment directly: it was
   investigated for exactly this kind of wiring, found unsafe for two concrete reasons (no
   schema-init DDL; a 50ms busy_timeout that would convert today's silent 5-second wait into a
   newly-common lock exception under real cross-thread contention), and deliberately left
   unwired, citing a specific prior code-review finding (#3/#9). A pooled design changes
   lock/timeout dynamics in ways the close-on-exit-per-call design does not — it needs the same
   rigor `connection_for()` got, not less.
3. Per the data-plane HARD RULE, capacity/pooling changes require a measured bottleneck and
   mechanism first, not "should help." Nothing in the research, baseline, or audit documents
   measures per-call connect/close overhead as the actual bottleneck for any of the 26 modules
   (the measured problems are fd exhaustion from never closing, and lock contention from
   uncoordinated concurrent writers — both are fixed by closing-on-exit; neither requires
   pooling to fix). **Independently measured for this revision**: the concern that per-call
   open/close on a WAL database might trigger an expensive checkpoint on every close is real in
   principle but small in practice here — `data/series_watcher.db-wal` (the largest store) is
   4.7 MB against a 29.7 GB main file, confirming the reference shape's cost genuinely does not
   scale with file size (`connect()`'s body touches only pragmas, schema DDL, and the checkpoint
   on close — never existing rows).

Recorded here so a future session doesn't have to re-discover `_aio_db.py`'s precedent from
scratch, and so choosing not to pool is a stated decision, not a silent default.

## Module classification for the implementation plan

- **Safety-adjacent — individual, dedicated PR required, never bundled** (repeating PR #484's
  own already-established convention for this exact category, confirmed unaltered against the
  merged plan text): `risk_manager.py` (daily-loss kill switch), `paper_broker.py`,
  `candidate_ledger.py`. Each migration touches only connection plumbing, not
  kill-switch/trading logic — but each gets the same real-diff scrutiny CLAUDE.md's safety
  invariants require, not "same pattern as the others" taken on faith.
- **Closer to the live trading path than the general bucket, lighter caution** (per PR #484's
  own finding, carried forward unchanged): `series_evaluator.py`, `trade_category.py`.
- **Individually audited already, real complexity beyond mechanical extraction confirmed**:
  `series_watcher.py` — PR #23 ("Realtime data-plane remediation — Phase P0," merged
  2026-08-26) added a real lock guarding this module's capture buffers against a genuine
  cross-thread race between the event loop and `tick_executor`'s worker thread. Its migration
  needs individual review for interaction with that existing lock. `settlement_edge.py` was
  also sampled as non-trivial; both need individual audit before being called mechanical.
- **Size outlier, migration cost independently confirmed not to scale with it**:
  `series_watcher.py`'s own `series_watcher.db` is 29.7 GB — confirmed exact
  (29,738,631,168 bytes), an order of magnitude larger than the next-largest store
  (`candidate_log.db`, 3.76 GB). The reference shape's `connect()` touches only pragmas, schema
  DDL, and (on close, for a WAL database) a checkpoint proportional to the WAL file specifically
  (measured at 4.7 MB for this store), never the main file's existing rows — so a straight
  migration's *mechanical* cost does not scale with the 29.7 GB figure. This does not by itself
  resolve whether `series_watcher.py` migrates in the first wave or is deliberately sequenced
  later — that remains an implementation-plan sequencing call, informed by the non-mechanical
  concern above (the PR #23 lock interaction), not by file size.
- **Not in scope** (corrected from the first draft): `services/diagnostics/store_stats.py` —
  already fixed, not a migration candidate. See scope-correction section above.
- **Split-pattern, partial migration only**: `backup/backup.py` — only the leaking half
  (`_connect()`) is in scope; `src_conn`/`dest_conn` are already correct and untouched.
- **General opportunistic bucket** (no individual flag, same "one PR per module or small
  low-risk batch, tracking issue not a hard deadline" treatment PR #484's Task 7 already
  established): the remaining **17** modules (25 `services/` modules in scope, minus the 8
  individually named above: `risk_manager.py`, `paper_broker.py`, `candidate_ledger.py`,
  `series_evaluator.py`, `trade_category.py`, `series_watcher.py`, `settlement_edge.py`,
  `backup/backup.py`), plus `tools/coordination_engine.py` at lowest priority given its
  short-lived-process risk profile — 18 total in this bucket.

## Migration gates

**Gate 0 — before any module migrates (blocks the whole migration, not per-module):**
- The three must-fix items above (raise-on-conflict registration, corrupted-DB test,
  `capture_writer.py`'s retry-mechanism documentation for if/when it's ever migrated) land in
  `services/db.py` and its own test suite.
- The reference API shape above (callback registration keyed by table name, explicit `tables=`
  selection and `busy_timeout_ms` at `connect()`) is what the implementation plan's Task 1
  builds — flagged as the single highest-stakes call this spec makes; see "Open question for
  explicit sign-off" below.
- A test proving the reference shape works correctly under `monkeypatch.setattr(mod, "DB_PATH",
  tmp_path/...)` — the exact failure mode this revision exists to prevent — before any module's
  own migration task is written, not discovered by the first module that tries it.
- **PR-stage addition**: table-name-only keying (the C2 fix) trades a silent failure mode for a
  loud one that still needs covering — `connect()`'s `_SCHEMAS[table](conn)` is an unguarded
  dict lookup, so calling it for a table whose owning module hasn't been imported yet (and
  therefore hasn't run its `register_schema` call) raises a bare `KeyError`. Better than C2's
  silent `no such table`, but still unnamed anywhere in this design until now — Task 1's own
  tests should cover the "connect before the registering module is imported" case explicitly,
  and the implementation plan should confirm each migrated module's own import graph guarantees
  registration happens before its first `connect()` call (typically true for a module
  registering its own schema at its own top level, but worth stating as a requirement rather
  than assuming).
- **PR-stage addition, explicit per coordinator sign-off discussion**: a global table-name-
  uniqueness test, distinct from the raise-on-conflict registration check above. The
  raise-on-conflict check is a *runtime* guard — it only fires if two different `init_fn`s are
  actually registered for the same table name during a real process's import graph. This test
  is a *static* one: enumerate the full set of table names the migration's own plan intends to
  register across all 26 modules (a fixture the implementation plan's Task 1 builds and keeps
  current as modules are added) and assert they're pairwise distinct, so a planned collision is
  caught before any module's migration task is even written, not discovered incrementally as
  each module lands. This belongs in `services/db.py`'s own test suite (`db.py`-level, same tier
  as the monkeypatch test above), not deferred to the implementation plan the way per-module
  scheduling decisions are.

**Gate 1 — before each individual module's migration is considered mechanical:**
- Read that module's actual `_connect()` (or equivalent) body in full, current-source, not
  from this spec's or any prior document's summary — matching this repo's own
  Task-Step-2-"read before editing" convention, and matching exactly the lesson this document's
  own adversarial review re-taught by opening `store_stats.py` when the first draft hadn't.
- Confirm every non-`CREATE TABLE` statement (indexes, `add_column_if_missing` calls) the
  module's current connect function runs is preserved in its `register_schema` callback — the
  exact regression class PR #484's Task 3's own test
  (`test_connect_still_creates_both_tables_indexes_and_unit_cost_columns`) exists to catch,
  generalized to every module now.
- A real "connection is actually closed" regression test per module, not "tests still pass" —
  per PR #501's own lesson: a fix that looks complete against the happy path can still leak on
  the setup-failure path.
- A test confirming the migrated module's own tests still pass under whatever `DB_PATH`
  monkeypatching convention that module's existing test file already uses — the specific
  failure this revision fixed at the `db.py` level, re-verified at the call-site level too.
- **PR-stage addition — call-site *shape*, not just DDL preservation, covering every caller
  file, not only the module's own file or its tests.** Grep every caller of the module's
  `_connect()` repo-wide (production and test, any file, not just the owning module's own body
  and its matching test file) and confirm each uses `with _connect() as conn:` (or the module's
  own equivalent) before assuming the migration is call-site-transparent. The sign-off census
  behind this design (111/111 with-shape call sites) covered production call sites within the
  26 in-scope modules' own files only — it does not cover a case like
  `tools/coordination_engine.py`'s, whose 24 real callers, **verified by repo-wide grep, not
  assumed** (`grep -rn '_connect()' tests/ tools/ --include='*.py' | grep coordination | grep -v
  'def _connect'`), span **six** files, not one module plus one test file: 2 production sites in
  `tools/quality_coordination.py` (`:584`, `:657`) and 22 test sites spread across **four**
  separate test files — `tests/test_coordination_engine.py` (15), `tests/test_quality_coordination_branch_domain.py`
  (4), `tests/test_quality_coordination_cli.py` (2), `tests/test_quality_coordination_cleanup_actions.py`
  (1) — all 24 using a **bare assignment** (`conn = ce._connect()`), not a `with` block. Migrating
  that module to a context-manager-returning `_connect()` without updating all 24 call sites
  across all six files would break every one of them (`conn.execute(...)` on a
  generator-context-manager object, not a connection) — the one module in scope where this
  gate's own default assumption doesn't hold, and where a same-directory-only or single-test-file
  grep would have missed real callers (confirmed the hard way, twice: two successive revisions
  of this very document each corrected the count while still under-scoping which files it
  actually touched, before independent peer sessions' direct reads caught both).
- Confirm whether the module's connect function is ever called from the FastAPI event loop
  directly; if so, the migrated call site must keep routing through `tick_executor.run()` or
  equivalent — this migration must not reintroduce the event-loop-blocking bug class Tier0/P1
  already fixed elsewhere.
- Safety-adjacent modules get their own dedicated PR, full diff review, never bundled.
- `series_evaluator.py`/`trade_category.py` get the lighter "don't bundle silently as routine"
  caution PR #484 already established.
- `series_watcher.py`/`settlement_edge.py` get individual audit for the specific non-mechanical
  concern named above before being scheduled.
- **Correction from this document's first revision**: the reference shape's registry is a plain
  `dict[str, Callable]` (`_SCHEMAS`, table-name-keyed, no ordered list) — registration order
  itself carries no meaning, unlike the prototype's `db_path`-keyed list. What *is* still
  significant is **`tables=` ordering at each `connect()` call site**: `connect()`'s `for table
  in tables: _SCHEMAS[table](conn)` runs callbacks in the order the caller lists them, so if any
  module's schema-init depends on another table already existing in the same file (e.g. a
  foreign-key-shaped dependency), that module's own migration must pass `tables=(...)` in the
  correct order — Gate 1 should confirm this isn't silently assumed for any given module.

**Gate 2 — after each module (or small batch) migrates:**
- Full local suite passes; `import main` sanity check.
- Live fd-count check post-deploy (Tier0's Task 9 already added the process-wide `open_fds`
  counter — confirmed present at `services/diagnostics/routes.py:457` — this migration can
  reuse it; no per-module fd attribution exists yet if that granularity is ever wanted).
- For `capture_writer.py`'s eventual migration specifically, if and when it's picked up: re-check
  the `flush_retained_on_lock` occurrence rate post-migration against the corrected current-regime
  baseline above (~53/day, not the combined 429-over-4-days figure), the same way Tier0's own
  Task 4/Task 10 pattern already established for `candidate_log.db`'s contention fix.

## Non-goals (explicitly deferred, not silently in scope)

- Any `aiosqlite` migration for these 26 modules.
- A DuckDB/Parquet export or any change to `raw_trades`'s system-of-record status.
- Adopting `_aio_db.py`'s pooling pattern for these 26 modules, or its "run schema init exactly
  once" semantic — named as a viable future refinement, not adopted in this spec's own reference
  shape (see reasoning above).
- A schema-replay-performance benchmark at real table counts.
- Deciding `series_watcher.py`'s migration wave/sequencing.
- Full individual audits of all 26 modules' connect-function bodies beyond the ones already
  sampled directly (`risk_manager.py`, `trade_category.py`, `series_watcher.py`,
  `settlement_edge.py`, `backup.py`, and — this revision's own correction —
  `store_stats.py`, now removed from scope entirely rather than pending audit).

## Open question for explicit sign-off

**The API-shape decision is this spec's single highest-stakes call.** It does not merely
supersede a plan-stage task — **it revises an API shape that was independently designed,
reviewed, and cleared GO at the design stage already**, in
`docs/superpowers/specs/2026-09-03-persistence-layer-redesign-design.md` §1.4 (see the
provenance correction at the top of this document), which PR #484's Task 1 then transcribed
verbatim. This document's own revision above diverges from *all three* — that GO'd design, PR
#484's transcription of it, and the unmerged prototype — rather than adopting any of them
wholesale. This is exactly a "genuine design/architecture decision" this repo's own
take-the-wheel carve-out reserves for a human call rather than an AI-executed default — flagged
explicitly for the coordinator/user to confirm, with the full weight of what's being revised
stated plainly, before an implementation plan is drafted against it.

## Revision log (first draft → this revision)

Every finding from this document's own required adversarial review, and its disposition:

| # | Severity | Finding | Disposition |
|---|---|---|---|
| C1 | Critical | `services/store_stats.py` "split-pattern leak" is a grep artifact — real file is `services/diagnostics/store_stats.py`, already fixed | **Fixed** — removed from scope entirely; scope corrected 27→26; migration would have been a regression, documented as such |
| C2 | Critical | Recommended path-keyed registry breaks `monkeypatch.setattr(mod, "DB_PATH", ...)`, demonstrated by running it | **Fixed** — reference shape now uses table-name-keyed registration (PR #484's addressing model) with the prototype's callback, plus a raise-on-conflict check and a `monkeypatch` test named in Gate 0 |
| I1 | Important | Comparison table applied an inconsistent standard to the two designs' equally-silent collision behavior | **Fixed** — both now stated as silent-collision in the comparison table; the practical ranking is based on the test-convention finding (C2), not the collision-surface framing alone |
| I2 | Important | Must-fix #2 cited `docs/next-action.md` for a claim that document doesn't contain; correct date is 2026-09-02, not 09-03 | **Fixed** — re-cited to `data/fault_log.db` directly, with corrected date and full occurrence window |
| I3 | Important | The "429 over 4+ days" figure conflates two sequential fault-name eras across an 11-minute boundary | **Fixed** — current-regime figure (192 over ~3.6 days, ~53/day) now used for Gate 2's own re-check baseline; combined figure kept only for total-historical context |
| I4 | Important | Must-fix #1's "independently confirmed by autotrade-73's separate audit" claim is unsupported — the baseline doc contains no schema-conflict finding | **Fixed** — claim removed; the schema-conflict finding is sourced to the db-foundation-audit alone, which is the real source |
| I5 | Important | `_aio_db.py`'s `schema_init` callback (a real, shipped precedent for the callback shape and for should-fix #5) was never examined | **Fixed** — cited directly (`_aio_db.py:187-191`), used to support the callback-shape decision and named as a viable future refinement for the replay-cost tradeoff |
| M1 | Minor | Wrong paths throughout (`services/store_stats.py`, `services/storage_health.py`) | **Fixed** — corrected to `services/diagnostics/store_stats.py` (now removed from scope anyway) and `services/storage_health/storage_health.py` |
| M2 | Minor | `signal_log.py` does not have "multiple tables" — it has one table, 4 indexes, 8 `add_column_if_missing` calls | **Fixed in round 1, count still wrong; corrected in round 2** — round 1's "8" was itself a grep artifact (counted the function's own `def add_column_if_missing` line as an eighth call); the scoped re-review caught this. Real count: 7. Also clarified `signal_log.py` is a Tier0 module, not one of this spec's 26, used only as an illustration |
| M3 | Minor | "the extra 8 files" arithmetic doesn't match grep B − grep A (10, 9 code files); `backup.py` was double-counted as "extra" despite matching both greps | **Fixed** — scope-correction section rewritten around the actually-new file (`tools/coordination_engine.py`) and the actually-refuted file (`store_stats.py`), not an arithmetic-derived "8" |
| M4 | Minor (confirmed, not a defect) | WAL-checkpoint-on-close cost vs. `series_watcher.db`'s size — unconsidered but small in practice (4.7 MB WAL vs 29.7 GB main file) | **Incorporated** — cited as independent measurement supporting the "cost doesn't scale with file size" claim in both the pooling and size-outlier sections |
| M5 | Minor | Registration order is load-bearing (schema-init replay order) and was undocumented | **Fixed in round 1, left inconsistent with round 1's own C2 fix; corrected in round 2** — round 1's Gate 1 text said registration order was "carried into the reference shape," but the reference shape's registry has no order (plain `dict[str, Callable]`); what's actually significant is `tables=` ordering at each `connect()` call site, now corrected |
| M6 | Minor (confirmed, no defect) | Lane discipline — decides an API shape and gates, doesn't write implementation tasks | No change needed |
| M7 | Minor (confirmed, no defect) | Safety classification carried forward accurately from PR #484 | No change needed |

### Round 2 — scoped re-review of round 1's fixes (required before consolidation, not assumed clean)

Per this project's "a revision is checked against the fix list item by item, never accepted on
its own completion claim" rule, round 1's fixes were independently re-verified (fresh Agent,
reading primary sources directly — live `data/fault_log.db` queries, `services/signal_log.py`,
`services/diagnostics/_aio_db.py`, the prototype source at `17b2e8f` — not trusting round 1's own
claims). Both Criticals (C1, C2) and all five Importants (I1-I5) were independently confirmed
genuinely fixed. Two Minors (M2, M5, above) were found still wrong after round 1's own attempt,
and three new issues were found in round 1's fix pass itself, not present in the original review:

| # | Finding | Disposition |
|---|---|---|
| N1 | Reference shape's `mkdir(exist_ok=True)` dropped the prototype's `parents=True` (`db.py:50`) — a silent functional downgrade that would raise `FileNotFoundError` under exactly the nested-`tmp_path` scenario Gate 0's own mandated monkeypatch test could hit | **Fixed** — `parents=True` restored; also noted the prototype's `_SCHEMAS_LOCK` (concurrent-registration guard) is not shown in the illustrative shape and must not be dropped by Task 1 |
| N2 | General-bucket module count (`~19`) didn't match the corrected scope arithmetic (25 services modules − 8 individually-named = 17, not 19) — round 1 edited this exact line and left it wrong | **Fixed** — corrected to 17 (18 including `tools/coordination_engine.py`), with the 8 named modules listed explicitly so the arithmetic is checkable |
| N3 | A third `capture_writer/flush` fault-log row (2 occurrences, 2026-09-02, after the era boundary) exists but isn't mentioned — doesn't change the ~53/day rate, purely a completeness gap | **Fixed** — noted inline; explicitly stated it doesn't affect the rate the gate depends on |

No new Critical or Important finding in round 2. Consolidation below proceeds on round 2's
state of the document.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
