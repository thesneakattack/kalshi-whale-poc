# Persistence Layer db.py Migration — Implementation Plan

**Stage:** implementation plan, per CLAUDE.md's "nothing advances on one pass" pipeline
(research → design/spec → implementation plan). Input: the merged design spec
(`docs/superpowers/specs/2026-09-03-persistence-layer-db-migration-design.md`, PR #505,
GO, round-2-reviewed) plus its own inputs — the research doc (PR #504), the db-foundation
audit (PR #507), the baseline measurement (PR #509), and the two Gate-1 pre-audits
(`docs/db-migration-gate1-preaudit-2026-09-03.md`, PR #506, 5 named modules;
`docs/db-migration-gate1-preaudit-general-bucket-2026-09-03.md`, PR #508, 21 general-bucket
modules) — all merged to `main`. This document turns that spec's Gate 0/1/2 criteria and
module classification into ordered, testable tasks. Every schema/preserve-list claim below
was re-verified directly against current `main` source (not carried from any prior document's
summary) while drafting this plan — see each task's own citation.

**Assigned adversarial reviewer for this plan's own required review cycle: autotrade-a3**
(a peer session, not a subagent dispatched by this session — per coordinator autotrade-1d's
explicit direction, since CLAUDE.md's HARD RULE requires the reviewer be "a genuinely separate
pass" and this plan's author should not also review it).

## Correction this plan makes to the design spec's own bucket count

The design spec's "Module classification" section computes "17 general bucket" as
25 services modules in scope minus the 8 it individually names
(`risk_manager.py`, `paper_broker.py`, `candidate_ledger.py`, `series_evaluator.py`,
`trade_category.py`, `series_watcher.py`, `settlement_edge.py`, `backup/backup.py`). That
arithmetic does not separately subtract `candidate_log.py` and `observability/observability.py`
— but the same spec, earlier, gives this plan an explicit, unambiguous instruction that
requires doing exactly that: "This spec's downstream implementation plan supersedes PR #484's
Task 1 with the reference shape above; Tasks 3/5/6's actual migrations ... remain valid and
are not re-litigated ... A future implementation plan should explicitly re-target Tasks 3/5/6
(or their equivalent) at `db.register_schema`/`db.connect(...)`." PR #484's Tasks 3/5/6 are
exactly `candidate_log.py`, `series_watcher.py`, `observability/observability.py` — already
planned in full detail (preserve-lists, test names, before/after diffs), never implemented as
code. Re-deriving that detail from scratch inside "general bucket, tracking issue only" would
both contradict this explicit instruction and throw away real, already-reviewed planning work.

**Ruling:** `candidate_log.py` and `observability/observability.py` get their own dedicated
tasks below (Tasks 3 and 5), retargeting PR #484's Task 3/6 content onto
`register_schema`/`connect`. `series_watcher.py` was already one of the design's 8 named
modules (individually-audited-complex tier), so it's unaffected by this correction. The
general bucket is therefore **15 modules**, not 17 — see Task 14 for the corrected list and
arithmetic, shown explicitly so a reviewer can check it in one line rather than trust it.

## Decisions this plan makes without re-litigating (already settled at the design stage)

- **API shape**: `db.register_schema(table_name: str, init_fn: Callable[[sqlite3.Connection],
  None])` (raises on a genuine conflict) + `db.connect(db_path: Path, *, tables:
  tuple[str, ...] = (), busy_timeout_ms: int = 5000)` (`@contextlib.contextmanager`, WAL +
  busy_timeout pragmas, runs each named table's `init_fn`, closes in `finally`). Design spec
  §"Reference shape", already GO'd by both this repo's design-stage review cycle and the
  coordinator's independent sign-off.
- **Gate 0/1/2** exactly as the design spec states them — restated per-task below where a
  specific task must satisfy one, not re-derived.
- **Pooling declined** for this migration's scope (design spec, "Considered and declined:
  pooling"). No task below adopts `_aio_db.py`'s cached-connection pattern.
- **`aiosqlite` migration, `raw_trades` DuckDB/Parquet export**: both explicitly out of scope
  (design spec Non-goals). No task below touches either.
- **Module classification tiers** (safety-adjacent / lighter-caution / individually-audited /
  split-pattern / general-bucket) as the design spec states them, corrected per the ruling
  above for `candidate_log.py`/`observability.py`.

## Decisions this plan makes now (deferred to plan-stage by the design spec)

- **D1 — series_watcher.py's async schema-init path.** The design spec's Non-goals explicitly
  defer "Deciding series_watcher.py's migration wave/sequencing," and the 5-module pre-audit
  names the async/sync gap as "the single largest open question for series_watcher.py's
  migration." **Ruling** (Task 4 below): this migration touches only the **sync** `_connect()`
  (the one that actually leaks — `_ensure_schema_aio` uses `_aio_db.connection_for()`, a
  cached, non-leaking, already-correct pattern the design spec separately declined to extend
  this migration to). `_ensure_schema_aio` is left completely unchanged. Both paths already
  share one source of truth for the DDL text — `capture_writer.RAW_TRADES_DDL_SQL`, confirmed
  directly at `series_watcher.py:190-198`'s own docstring ("Shares capture_writer.RAW_TRADES_DDL_SQL
  with _connect() ... rather than a hand-kept-in-sync second copy") — so migrating the sync side
  onto `register_schema` while leaving the async side as-is does not create a new duplicate; it
  changes how one of the two existing call sites reaches the same shared string, not the string
  itself. No `db.py` async counterpart is built. This is not a new design decision — it is
  applying the design spec's own already-declined-pooling reasoning to the one module where the
  question was still open.
- **D2 — shared-DDL identity-check risk (3 tables).** `register_schema` raises if a table name
  is registered with a *different* callable than one already registered for it. Three tables in
  this migration's scope have their DDL owned by `capture_writer.py` but executed by a
  *different* module: `raw_trades` (owned by `capture_writer.py`, executed by
  `series_watcher.py`), `rejected_candidates` and `rejection_events` (owned by
  `capture_writer.py`, executed by `candidate_log.py`) — confirmed at
  `services/capture_writer.py:132-201` and cross-checked against both pre-audits. Today, each
  table has exactly one registering module, so there is no live collision — but if each
  registering module wrapped the shared DDL string in its *own* locally-defined closure, two
  textually-identical-but-object-different callables would compare unequal under `is`, and a
  second registration attempt (a re-import under test, a future second consumer) would raise
  spuriously. **Ruling** (Task 2 below): define one stable `init_fn` per table, once, in
  `capture_writer.py` itself (next to its owning DDL constant), and have `candidate_log.py`/
  `series_watcher.py` import and register that exact function object — never redefine a local
  closure around the same string.
- **D3 — busy_timeout per module.** The 5-module pre-audit's cross-module finding: "None of the
  5 sets `busy_timeout` explicitly ... worth a single explicit decision (not 5 separate ones)."
  Independently confirmed for the general-bucket modules too — no module's write-up in either
  pre-audit names an explicit `busy_timeout` PRAGMA anywhere in this migration's 26-module scope
  (the only modules in this repo that differentiate busy timeouts today —
  `capture_writer.py`'s `_DAEMON_BUSY_TIMEOUT_MS`/`_CALLER_BUSY_TIMEOUT_MS`,
  `tick_executor.py`'s 50ms — are not in this migration's scope). **Ruling**: every task below
  keeps `db.connect()`'s `busy_timeout_ms=5000` default, unstated at the call site (matching
  `db.register_ddl`-era Task 3's own already-reviewed reasoning: Python's
  `sqlite3.connect(db_path)` already defaults to a 5.0-second busy wait today, so this is
  "make the existing implicit value explicit and policy-driven," not a numeric change — zero
  new tuning risk, per the data-plane HARD RULE's "never change ... retry budget ... because it
  'should help'"). This is a single, explicit, cross-module decision, stated once here, not
  silently inherited 26 separate times.

## Architecture — mapping the design's gates onto tasks

| Task | Module(s) | Gate | Tier | PR grouping |
|---|---|---|---|---|
| 1 | `services/db.py` (new) | Gate 0 | foundation | PR A |
| 2 | `services/capture_writer.py` (add 3 `init_fn` helpers, no behavior change) | Gate 0 (D2) | foundation | PR A |
| 3 | `services/candidate_log.py` | Gate 1 | retargeted PR #484 Task 3 | PR B |
| 4 | `services/series_watcher.py` (sync `_connect()` only, D1) | Gate 1 | individually-audited | PR B |
| 5 | `services/observability/observability.py` | Gate 1 | retargeted PR #484 Task 6 | PR B |
| 6 | `services/risk_manager.py` | Gate 1 | safety-adjacent | PR C (dedicated) |
| 7 | `services/paper_broker.py` | Gate 1 | safety-adjacent | PR D (dedicated) |
| 8 | `services/candidate_ledger.py` | Gate 1 | safety-adjacent | PR E (dedicated) |
| 9 | `services/series_evaluator.py` | Gate 1 | lighter-caution | PR F |
| 10 | `services/trade_category.py` | Gate 1 | lighter-caution | PR F |
| 11 | `services/settlement_edge.py` | Gate 1 | individually-audited | PR G |
| 12 | `services/backup/backup.py` (primary `_connect()` only) | Gate 1 | split-pattern | PR H |
| 13 | `tools/coordination_engine.py` + 6-file call-site-shape update | Gate 1 | lowest priority | PR I |
| 14 | 15-module general bucket | n/a (tracking, not code) | opportunistic | PR A (docs-only, zero risk) |
| 15 | Full regression + live validation | Gate 2 | closeout | after all of A–I merge |

## Global Constraints

- **No change to any trading, risk, sizing, calibration, strategy, settlement, or auth
  *logic*.** Every task below touches connection plumbing only — the `_connect()` function (or
  its module-level equivalent) and, where the module's schema needs it, the one-time DDL/index/
  column-add statements already present in that function today. No task adds, removes, or
  changes a decision, a threshold, a kill-switch check, or an order-placement call.
- **Safety-adjacent modules (`risk_manager.py`, `paper_broker.py`, `candidate_ledger.py`) each
  ship as their own individually reviewed PR, never bundled with each other or with any other
  task in this plan** — repeating PR #484's own established convention for this exact category
  (Global Constraints, "several safety-adjacent modules"), carried forward unchanged by the
  design spec's module classification. Tasks 6/7/8 are written out in full in this one plan
  document for completeness and cross-task consistency (shared reasoning, shared test
  patterns) — but SDD/executing-plans execution must still open PR C, PR D, PR E separately,
  never as one commit range merged together.
- **Every busy-timeout number this plan sets is the existing implicit default (5000ms), made
  explicit** — D3 above. No task invents a new number for any of the 26 modules.
- **`contextlib.closing` is not used** — `_connect()`/`db.connect()` itself is the closing
  context manager, so no call site's syntax changes. Same reasoning Tier0 and PR #484 both
  already established.
- **`series_watcher.py`'s async schema-init path (`_ensure_schema_aio`,
  `services/diagnostics/_aio_db.py`'s `connection_for(schema_init=...)`) is untouched by this
  plan** — D1 above. No task adds an async counterpart to `db.py`.
- **No `data/*.db` file is deleted, moved, or truncated by any task.** Every migration preserves
  every existing table's schema and every existing caller's read/write contract, verified per
  Gate 1's own "confirm every non-`CREATE TABLE` statement is preserved" requirement, task by
  task, not assumed.
- **Event-loop-blocking exposure found in Gate-1 pre-audit is documented, not fixed, by this
  plan** — `services/reset/routes.py`'s `async def reset_broker(...)` calls at least 7 of this
  migration's modules synchronously with no dispatch (accounts_store.py, candidate_log.py's
  `clear_range()` and `count_range()`, market_analyst_agent/_db.py, reset_log.py, trade_archive.py,
  series_evaluator.py, trade_category.py, shadow_mode.py, calibration_history.py — per both
  pre-audits' PR-stage adversarial-review findings). **`candidate_log.py`'s `count_range()` is
  this list's most severe instance by far** — measured at 34,129.54ms against 22.6M rows (issue
  #510's research, PR #512); see Task 3's own event-loop note for the full citation. This is a
  real, pre-existing, separately tracked gap (issue #510) — this migration's job is closing
  each connection on exit, not routing callers through `tick_executor.run()`. A migrated
  `_connect()` still blocks the event loop for at least as long as before (plus `close()`'s own
  cost) when called this way; that is a **pre-existing** property this plan must not make worse,
  and does not fix. Each affected task below states this explicitly rather than silently
  passing over it.
- **This repo has no `pytest-asyncio`.** Not directly relevant (no task adds a new `async def`),
  noted only because Task 4 touches a module that has async code elsewhere in the same file.

---

### Task 1: Build `services/db.py` — the shared closing-connection module (Gate 0)

**Files:**
- Create: `services/db.py`
- Test: `tests/test_db.py` (new)

**Interfaces:**
- Produces: `db.register_schema(table_name: str, init_fn: Callable[[sqlite3.Connection], None])
  -> None` — raises `ValueError` if a *different* callable is already registered for
  `table_name`; is a no-op (not an error) if the identical callable re-registers (idempotent
  under module re-import). `db.connect(db_path: Path, *, tables: tuple[str, ...] = (),
  busy_timeout_ms: int = 5000)` — `@contextlib.contextmanager`; creates `db_path.parent` with
  `parents=True, exist_ok=True`; opens; sets `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout
  = <busy_timeout_ms>`; runs each named table's registered `init_fn` in the order given; yields
  inside its own `with conn:` (commit/rollback) block; **always** closes in `finally`.
  `db.add_column_if_missing(conn, table, column, coltype)` — unchanged from PR #484's original
  (parameter-only, no shared state, safe regardless of migration status).
- Consumes: nothing. This task lands with zero callers — Task 2 is the first (defining `init_fn`
  helpers, not calling `connect()`); Tasks 3+ are the first real callers.

**Why this is safe to land on its own:** identical reasoning to PR #484's own Task 1 — no
existing module imports `services/db.py` yet, so this task changes zero runtime behavior.
`db.py` defines no `DB_PATH` of its own, so it stays invisible to
`tools/quality_audit/persistence.py`'s `PERSISTENCE_MODULE_PATHS` scanner (same reasoning PR
#484's Task 1 already established, still correct — this module still takes `db_path` as a
parameter).

**Disposition of `fix/db-foundation-must-fix-tests` (`e74096a`) — prior art, not silently
orphaned.** That branch is a follow-on off the prototype (`17b2e8f`) with its own test suite for
`services/db.py`. This task's own test list below converges independently on three of its
tests — `test_connect_closes_on_setup_failure_before_yield`,
`test_register_schema_raises_on_genuine_conflict` +
`test_register_schema_same_callable_twice_is_not_a_conflict`, and
`test_connect_on_corrupted_db_file_still_closes` correspond to `e74096a`'s
`test_connect_closes_its_connection_even_when_an_exception_is_raised_inside`,
`test_schema_registration_conflict_raises_instead_of_silently_dropping`, and
`test_connect_against_corrupted_db_file_raises_database_error` — two authors reaching the same
test set independently is evidence the set is right, not a coincidence to ignore. **Explicit
disposition, found missing by this plan's own PR-stage review (coordinator autotrade-1d's
sign-off-condition-3 check) and now stated rather than left implicit:**
- **Adopted, verbatim in spirit:** `e74096a`'s
  `test_lock_contention_raises_operational_error_matching_capture_writer_pattern` — a genuine
  `EXCLUSIVE` lock taken from a second raw connection, confirming `db.connect(busy_timeout_ms=50)`
  raises a catchable `sqlite3.OperationalError` with `"locked"` in the message (matching
  `capture_writer.py`'s existing catch-and-retain pattern), and that a normal `connect()` succeeds
  once the lock releases (nothing left stuck). The design spec classified this **should-fix, not
  blocking** ("worth adding before `db.py` is trusted at scale, not before the first migration") —
  but it already exists, passes, and costs nothing to include, so this task adopts it now rather
  than deferring it. Included in this task's own test list below.
- **Not adopted:** `e74096a`'s underlying `_SCHEMAS` registry shape (`db_path`-keyed, per-path
  linear-scan list) — this is exactly what the design spec's C2 finding rejected (breaks this
  repo's `monkeypatch.setattr(mod, "DB_PATH", ...)` convention). Only `e74096a`'s **tests** carry
  over; its **implementation** does not. `e74096a`'s `busy_timeout_ms` parameter is consistent
  with (not a source for) this task's own — the design spec's reference shape already specifies
  `busy_timeout_ms` independently.
- **Branch fate:** `fix/db-foundation-must-fix-tests` is superseded by this task once it lands;
  no further code from it is pulled in beyond the one adopted test's assertions, transcribed
  fresh against this task's own table-name-keyed API rather than merged/rebased.

- [ ] **Step 1: Write the failing tests first**

Add to `tests/test_db.py`:

```python
import sqlite3
import threading
import time

import pytest

from services import db


def _fresh_registry(monkeypatch):
    """Each test gets its own schema registry - db.py's module-level
    _SCHEMAS is otherwise shared mutable state across tests."""
    monkeypatch.setattr(db, "_SCHEMAS", {})


def _widgets(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS widgets (id INTEGER PRIMARY KEY)")


class _RecordingConnection:
    """Wraps a real sqlite3.Connection to track close() calls without
    mutating the connection object itself.

    **Correction applied 2026-09-03, after this plan's own Task 1 was
    implemented (PR #518)**: this document's first draft monkeypatched
    `conn.close` directly on a real `sqlite3.Connection` instance
    (`conn.close = lambda: ...`) - that raises `AttributeError:
    'sqlite3.Connection' object attribute 'close' is read-only` on this
    container's Python (3.13, confirmed empirically via TDD's own RED step
    while implementing this exact task, not assumed). This repo already has
    a working, already-merged pattern for this shape
    (`tests/test_signal_log.py`'s `test_connect_closes_its_connection`,
    Tier 0's own Task 5): wrap the real connection instead of mutating it,
    delegate everything else via `__getattr__`, and also delegate
    `__enter__`/`__exit__` since `db.connect()`'s body does `with conn:` for
    its own commit/rollback semantics (the fd-closing `finally: conn.
    close()` is a separate, outer step). Every "closes its connection" test
    in this plan (Tasks 1, 3-13) uses this same wrapper - defined once per
    test file, since each task targets a different test module."""

    def __init__(self, inner, closed: list):
        self._inner = inner
        self._closed = closed

    def close(self):
        self._closed.append(True)
        self._inner.close()

    def __enter__(self):
        self._inner.__enter__()
        return self

    def __exit__(self, *exc_info):
        return self._inner.__exit__(*exc_info)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _closing_sqlite_connect(closed: list, *, real_connect=None):
    """real_connect defaults to sqlite3.connect - but by test time that name
    already refers to tests/support/runtime_isolation.py's own
    _guarded_connect (installed once, repo-wide, at collection time), not
    the true unwrapped function. That's fine for every test in this task
    except the corrupted-file one below: _guarded_connect does its own
    conn.execute("PRAGMA synchronous=OFF") immediately inside itself, so a
    corrupted file's DatabaseError fires there - before this wrapper ever
    gets a connection to attach close-tracking to, and before db.py's own
    conn = sqlite3.connect(db_path) line (deliberately outside its try:)
    even returns. Real, unwrapped sqlite3.connect() is lazy (verified
    directly during this task's own implementation, PR #518: it does not
    touch the file's contents at all, only the first real execute() does) -
    production is unaffected, only this specific test's simulation of
    "connect against a corrupted file" needs the true original to
    accurately reproduce that laziness."""
    real_connect = real_connect or sqlite3.connect

    def _tracking_connect(*args, **kwargs):
        return _RecordingConnection(real_connect(*args, **kwargs), closed)

    return _tracking_connect


def test_connect_closes_on_normal_exit(tmp_path, monkeypatch):
    _fresh_registry(monkeypatch)
    closed = []
    monkeypatch.setattr(db.sqlite3, "connect", _closing_sqlite_connect(closed))
    with db.connect(tmp_path / "t.db") as conn:
        conn.execute("SELECT 1")
    assert closed == [True]


def test_connect_closes_even_on_exception(tmp_path, monkeypatch):
    _fresh_registry(monkeypatch)
    closed = []
    monkeypatch.setattr(db.sqlite3, "connect", _closing_sqlite_connect(closed))
    with pytest.raises(ValueError):
        with db.connect(tmp_path / "t.db") as conn:
            raise ValueError("caller-side failure")
    assert closed == [True]


def test_connect_closes_on_setup_failure_before_yield(tmp_path, monkeypatch):
    """PR #501's own lesson, generalized: a failure between connect() and
    yield (here, a schema init_fn that raises) must not leak the connection."""
    _fresh_registry(monkeypatch)
    closed = []
    monkeypatch.setattr(db.sqlite3, "connect", _closing_sqlite_connect(closed))

    def _boom(conn: sqlite3.Connection) -> None:
        raise RuntimeError("schema init failed")

    db.register_schema("boom", _boom)
    with pytest.raises(RuntimeError):
        with db.connect(tmp_path / "t.db", tables=("boom",)):
            pass
    assert closed == [True]


def test_register_schema_creates_table_and_is_idempotent(tmp_path, monkeypatch):
    _fresh_registry(monkeypatch)
    db.register_schema("widgets", _widgets)
    db_path = tmp_path / "t.db"
    with db.connect(db_path, tables=("widgets",)) as conn:
        conn.execute("INSERT INTO widgets DEFAULT VALUES")
    # Second connect() with the same table re-runs init_fn - must not error
    # or wipe the row.
    with db.connect(db_path, tables=("widgets",)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM widgets").fetchone()[0] == 1


def test_no_tables_arg_runs_no_schema(tmp_path, monkeypatch):
    _fresh_registry(monkeypatch)
    db.register_schema("widgets", _widgets)
    db_path = tmp_path / "t.db"
    with db.connect(db_path) as conn:  # tables=() default - no init_fn runs
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("SELECT * FROM widgets")


def test_wal_and_busy_timeout_pragmas_applied(tmp_path, monkeypatch):
    _fresh_registry(monkeypatch)
    with db.connect(tmp_path / "t.db", busy_timeout_ms=1234) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 1234


def test_add_column_if_missing_adds_once(tmp_path, monkeypatch):
    _fresh_registry(monkeypatch)
    db.register_schema("t", lambda conn: conn.execute(
        "CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY)"
    ))
    with db.connect(tmp_path / "t.db", tables=("t",)) as conn:
        db.add_column_if_missing(conn, "t", "extra", "REAL")
        db.add_column_if_missing(conn, "t", "extra", "REAL")  # no error second time
        cols = {row[1] for row in conn.execute("PRAGMA table_info(t)")}
        assert "extra" in cols


def test_connect_creates_nested_parent_directory(tmp_path, monkeypatch):
    """The prototype's mkdir(parents=True) - a tmp_path-based test can hand
    connect() a not-yet-existing nested directory; exist_ok=True alone
    raises FileNotFoundError in that case."""
    _fresh_registry(monkeypatch)
    nested = tmp_path / "a" / "b" / "c.db"
    with db.connect(nested) as conn:
        conn.execute("SELECT 1")
    assert nested.exists()


# --- Gate 0 must-fix tests (design spec, "Required fixes to the prototype") ---


def test_register_schema_raises_on_genuine_conflict(monkeypatch):
    _fresh_registry(monkeypatch)

    def _init_a(conn):
        conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY)")

    def _init_b(conn):
        conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, x TEXT)")

    db.register_schema("t", _init_a)
    with pytest.raises(ValueError):
        db.register_schema("t", _init_b)


def test_register_schema_same_callable_twice_is_not_a_conflict(monkeypatch):
    """Re-importing a module that calls register_schema at its own top
    level must not raise - only a genuinely different callable does."""
    _fresh_registry(monkeypatch)
    db.register_schema("widgets", _widgets)
    db.register_schema("widgets", _widgets)  # no error


def test_connect_on_corrupted_db_file_still_closes(tmp_path, monkeypatch):
    """Real incident this app already had: data/fault_log.db,
    market_history/record_snapshot_from_ticker, DatabaseError 'database disk
    image is malformed,' 2026-09-02 18:21-20:44 UTC. The exception must
    propagate through finally: conn.close() the same as any other exception."""
    from tests.support.runtime_isolation import _original_connect

    _fresh_registry(monkeypatch)
    bad = tmp_path / "corrupt.db"
    bad.write_bytes(b"not a sqlite file" * 100)
    closed = []
    monkeypatch.setattr(
        db.sqlite3, "connect", _closing_sqlite_connect(closed, real_connect=_original_connect)
    )
    with pytest.raises(sqlite3.DatabaseError):
        with db.connect(bad) as conn:
            conn.execute("SELECT * FROM sqlite_master")
    assert closed == [True]


def test_connect_before_registering_module_imported_raises_keyerror(tmp_path, monkeypatch):
    """table-name-only keying trades C2's silent 'no such table' for a loud
    KeyError - better, but must be a named, tested behavior, not an
    accident. A caller passing tables=("unregistered",) before the owning
    module's register_schema call has run gets this, not a silent no-op."""
    _fresh_registry(monkeypatch)
    with pytest.raises(KeyError):
        with db.connect(tmp_path / "t.db", tables=("never_registered",)):
            pass


def test_connect_and_register_schema_are_thread_safe_under_concurrent_registration(monkeypatch):
    """Two modules genuinely racing to register different init_fns for the
    same table name must raise deterministically, not depend on scheduling
    (the reason _SCHEMAS needs a lock, not just correctness under a single
    thread)."""
    _fresh_registry(monkeypatch)
    errors = []

    def _register(suffix):
        def _init(conn):
            conn.execute(f"CREATE TABLE IF NOT EXISTS t{suffix} (id INTEGER PRIMARY KEY)")
        try:
            db.register_schema("race_table", _init)
        except ValueError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_register, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # Exactly one registration wins; every other thread's distinct closure
    # is a genuine conflict and must raise - never a silent last-write-wins,
    # never a crash/hang from unsynchronized dict access.
    assert len(errors) == 19


def test_monkeypatched_db_path_finds_registered_schema(tmp_path, monkeypatch):
    """The C2 regression test: this repo's universal
    monkeypatch.setattr(mod, "DB_PATH", tmp_path/...) convention (64 test
    files) must keep working - table-name-only keying (not the prototype's
    db_path-keyed registry) is why this works."""
    _fresh_registry(monkeypatch)
    db.register_schema("widgets", _widgets)
    real_path = tmp_path / "real" / "app.db"
    monkeypatched_path = tmp_path / "test" / "isolated.db"
    with db.connect(monkeypatched_path, tables=("widgets",)) as conn:
        conn.execute("INSERT INTO widgets DEFAULT VALUES")
        assert conn.execute("SELECT COUNT(*) FROM widgets").fetchone()[0] == 1
    assert not real_path.exists()


def test_table_name_uniqueness_across_full_migration_scope():
    """Gate 0's static check: enumerate every table name this migration's
    own plan intends to register across all in-scope modules and assert
    they're pairwise distinct - catches a planned collision before any
    module's migration task is even written, not discovered incrementally.
    This fixture is the authoritative table-name list for this plan; keep it
    current as tasks land (see this plan's own per-task 'Tables' line)."""
    table_names = [
        # Task 2 (capture_writer.py-owned, shared across two registering modules)
        "raw_trades", "rejected_candidates", "rejection_events",
        # Task 3: candidate_log.py registers rejected_candidates/rejection_events
        # (already listed above - same table, same shared init_fn, not a
        # second distinct entry)
        # Task 4: series_watcher.py registers raw_trades (ditto) plus:
        "book_snapshots",
        # Task 5
        "metric_samples",
        # Task 6
        "risk_meta",
        # Task 7 (verified directly, `grep -n "CREATE TABLE" services/paper_broker.py`
        # — 4 tables, not the 2 this plan's first draft assumed before that check)
        "broker_meta", "positions", "trades", "pending_orders",
        # Task 8
        "candidates",
        # Task 9
        "series_status",
        # Task 10
        "trade_category",
        # Task 11 (module is named settlement_edge.py; its table is not -
        # verified directly, `grep -n "CREATE TABLE" services/settlement_edge.py`)
        "window_observations",
        # Task 12
        "backup_runs",
        # Task 13
        "signal_state", "coordination_runs", "cleanup_actions",
    ]
    assert len(table_names) == len(set(table_names)), (
        "duplicate table name across this migration's own planned registrations"
    )


def test_lock_contention_raises_operational_error_matching_capture_writer_pattern(monkeypatch):
    """Adopted from fix/db-foundation-must-fix-tests (e74096a) - see this task's own
    'Disposition' note above. capture_writer.py's _flush_store already handles exactly
    this shape live (real "database is locked" faults in data/fault_log.db): catch
    sqlite3.OperationalError, retain the batch for the next flush cycle rather than
    blocking or crashing. Verifies db.connect() raises a real, catchable
    OperationalError under genuine lock contention (not something else, not a hang)
    and that busy_timeout_ms is actually overridable per call - capture_writer's own
    _CALLER_BUSY_TIMEOUT_MS (50ms) is 100x shorter than db.py's 5000ms default by
    design (fail-fast, not block-then-retry)."""
    _fresh_registry(monkeypatch)
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "t.db"
        with db.connect(db_path):
            pass  # create the file first

        locker = sqlite3.connect(str(db_path), timeout=0)
        locker.execute("BEGIN EXCLUSIVE")
        try:
            retained = []
            try:
                with db.connect(db_path, busy_timeout_ms=50) as conn:
                    conn.execute("CREATE TABLE never_reached (id INTEGER)")
            except sqlite3.OperationalError as exc:
                retained.append(exc)
            assert retained, "expected db.connect() to raise OperationalError under a held exclusive lock"
            assert "locked" in str(retained[0]).lower()
        finally:
            locker.rollback()
            locker.close()

        # Lock released - a normal connect() now succeeds, confirming the
        # failed attempt above didn't leave anything stuck.
        with db.connect(db_path) as conn:
            conn.execute("SELECT 1")
```

(Needs `import tempfile` and `from pathlib import Path` added to `tests/test_db.py`'s import
block alongside the existing `import sqlite3`/`import contextlib`/`import threading`.)

Run: `ddev exec -s fastapi python -m pytest tests/test_db.py -q`
Expected: **collection error** (`services.db` does not exist yet).

- [ ] **Step 2: Implement `services/db.py`**

```python
"""Shared, closing SQLite connection helper - the single fix for the
30-module "opens a connection, never calls .close()" leak shape that
produced a real 6.8-hour fd-exhaustion incident (2026-09-02). Tier 0
(PR #501) already fixed 5 modules with their own per-module
@contextlib.contextmanager; this module is the shared version the
remaining 26 migrate onto. Design:
docs/superpowers/specs/2026-09-03-persistence-layer-db-migration-design.md.

Schema is registered as a callback (register_schema), not a DDL string -
a callback can express CREATE TABLE, CREATE INDEX, and add_column_if_missing
calls together, in one registration, matching what every real module in
this migration actually needs (confirmed: PR #484's every migrated module
needed a post-connect() escape hatch under the string-DDL design this
module supersedes). Keyed by table name alone, not (db_path, table) - this
repo's universal `monkeypatch.setattr(mod, "DB_PATH", tmp_path/...)` test
convention (64 test files) means a caller's db_path is routinely swapped at
test time; a path-keyed registry silently finds nothing for a monkeypatched
path (demonstrated by running the alternative design) - table-name keying
means the registered schema is found regardless of which literal path is
passed to connect().

Every existing `with _connect() as conn:` call site keeps working unchanged
once its owning module's _connect() is rewritten to wrap db.connect(...) -
this yields the same conn as before, but now closes it on exit. See each
migrated module's own migration task for the exact before/after diff."""
import contextlib
import sqlite3
import threading
from pathlib import Path
from typing import Callable

_SCHEMAS: dict[str, Callable[[sqlite3.Connection], None]] = {}
_SCHEMAS_LOCK = threading.Lock()


def register_schema(table_name: str, init_fn: Callable[[sqlite3.Connection], None]) -> None:
    """Registered once, at import time, by each table's owning (or, for the
    three capture_writer.py-owned tables this migration's D2 ruling covers,
    registering) module - keyed by table name alone, not db_path, so a
    caller's later db.connect(monkeypatched_path, tables=("t",)) finds the
    same registered init_fn regardless of which literal path is passed at
    connect time. Raises on a genuine conflict (a different init_fn already
    registered for this table name) rather than silently keeping the first
    one - the db-foundation-audit's own must-fix #1. Re-registering the
    identical callable (e.g. a module re-imported under test) is a no-op,
    not an error."""
    with _SCHEMAS_LOCK:
        existing = _SCHEMAS.get(table_name)
        if existing is not None and existing is not init_fn:
            raise ValueError(f"conflicting schema registration for table {table_name!r}")
        _SCHEMAS[table_name] = init_fn


@contextlib.contextmanager
def connect(db_path: Path, *, tables: tuple[str, ...] = (), busy_timeout_ms: int = 5000):
    """Every existing `with _connect() as conn:` call site's replacement.
    Opens, sets WAL + the given busy_timeout as one policy, runs each named
    table's registered init_fn (in the order given - significant only if
    one table's init_fn depends on another already existing in the same
    file), yields, and ALWAYS closes - this is the entire fix. tables=()
    (the default) runs no init_fn, for read-only/diagnostic callers whose
    owning module has already guaranteed the schema exists. A table name
    passed here with no registered init_fn (the owning module hasn't been
    imported, or the name is simply wrong) raises a bare KeyError - louder
    than the alternative design's silent 'no such table,' but still worth
    naming: each migrated module's own import graph must guarantee its
    register_schema call runs before its first connect() call."""
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


def add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, coltype: str) -> None:
    """Shared replacement for the AST-identical per-module copies the
    architecture audit's §9.2 found - takes conn as a parameter, shares no
    state, so it doesn't need its caller to route through connect() above."""
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
```

- [ ] **Step 3: Confirm all tests pass**

Run: `ddev exec -s fastapi python -m pytest tests/test_db.py -q`
Expected: all 16 tests pass.

- [ ] **Step 4: `import main` sanity check**

Run: `ddev exec -s fastapi python -c 'import services.db' && echo IMPORT_OK`
Expected: `IMPORT_OK` — imports cleanly in isolation; nothing else imports it yet.

---

### Task 2: `capture_writer.py` — shared `init_fn` helpers for the three cross-module tables (D2, Gate 0)

**Files:**
- Modify: `services/capture_writer.py` (add three functions near the existing DDL constants,
  `:132-201`; zero change to any existing function)
- Test: `tests/test_capture_writer.py` (extend)

**Depends on:** Task 1.

**Why this task exists, separately from Tasks 3/4:** `capture_writer.py` owns
`RAW_TRADES_DDL_SQL` (`:132-151`), `REJECTION_EVENTS_DDL_SQL` (`:161-...`), and
`REJECTED_CANDIDATES_DDL_SQL` (`:182-...`), but does not itself call `_connect()` for any of
them today (`_STORE_DDL`, `:199-201`, is its own internal string-lookup dict for its
buffer/flush machinery, unrelated to `db.py`). `series_watcher.py` executes
`RAW_TRADES_DDL_SQL` directly (`_connect():159`, `_ensure_schema_aio():201`);
`candidate_log.py` executes `REJECTED_CANDIDATES_DDL_SQL`/`REJECTION_EVENTS_DDL_SQL` directly
(`_connect():86,91`). If Task 3 and Task 4 each independently wrapped these strings in their own
locally-defined closure, the two closures would be different callables (`is` returns `False`)
even though they execute byte-identical SQL — `register_schema`'s raise-on-conflict check exists
precisely to catch a *genuine* mismatch, but would also fire on this *spurious* one if each
module authored its own wrapper. Defining the wrapper once, here, and having both consuming
modules import the same function object avoids ever exercising that ambiguity.

**Interfaces:** three new module-level functions on `capture_writer.py`, no signature or
behavior change to anything existing:

```python
def init_raw_trades(conn: sqlite3.Connection) -> None:
    conn.execute(RAW_TRADES_DDL_SQL)


def init_rejected_candidates(conn: sqlite3.Connection) -> None:
    conn.execute(REJECTED_CANDIDATES_DDL_SQL)


def init_rejection_events(conn: sqlite3.Connection) -> None:
    conn.execute(REJECTION_EVENTS_DDL_SQL)
```

- [ ] **Step 1: Write the failing test first**

Add to `tests/test_capture_writer.py`:

```python
def test_init_fn_helpers_create_their_tables(tmp_path):
    import sqlite3
    conn = sqlite3.connect(tmp_path / "t.db")
    try:
        capture_writer.init_raw_trades(conn)
        capture_writer.init_rejected_candidates(conn)
        capture_writer.init_rejection_events(conn)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert {"raw_trades", "rejected_candidates", "rejection_events"} <= tables
    finally:
        conn.close()


def test_init_fn_helpers_are_stable_function_objects():
    """D2's whole point: importing this module twice (module caching aside,
    this pins the property register_schema's identity check depends on)
    yields the same function object, not a fresh closure per import."""
    from services import capture_writer as cw2
    assert capture_writer.init_raw_trades is cw2.init_raw_trades
    assert capture_writer.init_rejected_candidates is cw2.init_rejected_candidates
    assert capture_writer.init_rejection_events is cw2.init_rejection_events
```

Run: `ddev exec -s fastapi python -m pytest tests/test_capture_writer.py -k init_fn -q`
Expected: `AttributeError` — the functions don't exist yet.

- [ ] **Step 2: Add the three functions to `services/capture_writer.py`**

Insert directly after `REJECTED_CANDIDATES_DDL_SQL`'s closing `"""` (after current `:198`,
before the existing `_STORE_DDL = {...}` at `:199`) — read `services/capture_writer.py:130-202`
first to confirm exact current line numbers before inserting, per Gate 1's read-before-editing
convention (applies here even though this task predates the modules that consume it).

- [ ] **Step 3: Confirm tests pass**

Run: `ddev exec -s fastapi python -m pytest tests/test_capture_writer.py -q`
Expected: full file passes, including the two new tests. This is a pure addition — every
existing test in the file must be unaffected.

**Why this is safe:** additive only; no existing function's body, signature, or call sites
change. Zero callers of the three new functions until Tasks 3/4 wire them in.

---

### Task 3: `services/candidate_log.py` migrates to `services/db.py` (retargets PR #484's Task 3)

**Files:**
- Modify: `services/candidate_log.py:58-113` (imports + `_connect`)
- Test: `tests/test_candidate_log.py` (extend)

**Depends on:** Task 1, Task 2.

**Current state, read directly (`services/candidate_log.py:58-113`, confirmed unchanged from
PR #484's own PR-stage-corrected line numbers):**

```python
def _add_column_if_missing(conn, table, column, coltype):  # :67-73, to be removed
    ...

def _connect() -> sqlite3.Connection:  # :76-113
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(capture_writer.REJECTED_CANDIDATES_DDL_SQL)   # :86
    ...
    conn.execute(capture_writer.REJECTION_EVENTS_DDL_SQL)      # :91
    ...
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rejection_events_gate ...")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rejection_events_unresolved ... WHERE resolved = 0")
    _add_column_if_missing(conn, "rejected_candidates", "unit_cost", "REAL")  # :111
    _add_column_if_missing(conn, "rejection_events", "unit_cost", "REAL")    # :112
    return conn
```

Six call sites (`:207, 255, 355, 407, 429, 449`), all `with _connect() as conn:` — verified via
`grep -n "_connect(" services/candidate_log.py`.

**Event-loop note (Gate 1 requirement) — corrected during this plan's PR-stage adversarial
review (autotrade-a3), which found the first draft's framing misleading about severity:**
`resolve_from_market_results()` (the main write path) runs off-loop via `tick_executor.run()`
from `settlement_resolver.py:270-271` — unaffected by this migration. `clear_range()` (`:435`)
is called directly, synchronously, from `services/reset/routes.py`'s `async def
reset_broker(...)` — pre-existing, tracked (issue #510); this task does not fix it, and the
added `close()` cost for that specific call is negligible (a single-row `DELETE`, not a
schema-init-heavy path). **But `count_range()` (`:412`, one of this task's own six migrated call
sites — the `with _connect() as conn:` at `:429` cited above is inside its body) is the single
most severe finding in the whole `/api/reset` event-loop investigation, not a minor one:**
issue #510's research (PR #512, merged) measured it directly at **34,129.54ms** — over 34
seconds of full app-wide event-loop freeze — against `rejection_events`'s 22,596,141 rows,
reachable from both `GET /api/reset/preview?candidate_log=true` and `POST /api/reset` with
`candidate_log:true` (`services/reset/routes.py:79`'s `_reset_domain_counts` calls
`candidate_log.count_range()` directly, verified at `routes.py:110`). This migration still does
not fix it — the fix (routing through `tick_executor.run()` or equivalent) is #510's job, not
this task's, per Global Constraints — but a reader of this task alone must not come away
believing this module's event-loop exposure is minor: it is the worst-measured instance of the
issue #510 pattern anywhere in this migration's scope, and it ships unchanged by this task.

- [ ] **Step 1: Write the failing tests first**

Add to `tests/test_candidate_log.py` (reuse the existing `_redirect_db` fixture):

```python
def test_connect_closes_its_connection(tmp_path, monkeypatch, _redirect_db):
    """_RecordingConnection wraps the real connection instead of mutating
    conn.close directly - that raises AttributeError on this container's
    Python (sqlite3.Connection.close is read-only), the same defect Task
    1's own implementation (PR #518) found and fixed against
    tests/test_signal_log.py's proven pattern (Tier 0's own Task 5),
    applied here for this module's test file."""
    import sqlite3

    closed = []
    real_connect = sqlite3.connect

    class _RecordingConnection:
        def __init__(self, inner):
            self._inner = inner

        def close(self):
            closed.append(True)
            self._inner.close()

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, *exc_info):
            return self._inner.__exit__(*exc_info)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def _tracking_connect(*args, **kwargs):
        return _RecordingConnection(real_connect(*args, **kwargs))

    monkeypatch.setattr(cl.db.sqlite3, "connect", _tracking_connect)
    with cl._connect() as conn:
        conn.execute("SELECT 1")
    assert closed == [True]


def test_connect_still_creates_both_tables_indexes_and_unit_cost_columns(tmp_path, monkeypatch, _redirect_db):
    with cl._connect() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert {"rejected_candidates", "rejection_events"} <= tables
        indexes = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )}
        assert "idx_rejection_events_gate" in indexes
        assert "idx_rejection_events_unresolved" in indexes
        rc_cols = {r[1] for r in conn.execute("PRAGMA table_info(rejected_candidates)")}
        re_cols = {r[1] for r in conn.execute("PRAGMA table_info(rejection_events)")}
        assert "unit_cost" in rc_cols
        assert "unit_cost" in re_cols


def test_connect_sets_explicit_busy_timeout_pragma(tmp_path, monkeypatch, _redirect_db):
    with cl._connect() as conn:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
```

Run: `ddev exec -s fastapi python -m pytest tests/test_candidate_log.py -k 'closes_its_connection or unit_cost_columns or busy_timeout_pragma' -q`
Expected: fails against current `main` (`cl.db` doesn't exist as an attribute yet).

- [ ] **Step 2: Read the exact current function before editing**

Run: `sed -n '58,113p' services/candidate_log.py`. Confirm it still matches the transcription
above before editing — this plan's own citation is from today's read, but the file may have
moved again since.

- [ ] **Step 3: Apply the fix**

Add `db` to the existing `from services import capture_writer` import (`:62`). Register the two
tables once at module scope, using Task 2's shared `init_fn` objects (D2):

```python
from services import capture_writer, db

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "candidate_log.db"

db.register_schema("rejected_candidates", capture_writer.init_rejected_candidates)
db.register_schema("rejection_events", capture_writer.init_rejection_events)
```

Rewrite `_connect()`:

```python
@contextlib.contextmanager
def _connect():
    """Every existing `with _connect() as conn:` call site keeps working
    unchanged - now backed by services/db.py's closing connect(). The two
    CREATE INDEX statements and two add_column_if_missing calls aren't
    expressible in a single table's registered init_fn (they span both
    tables / aren't CREATE TABLE at all), so this wrapper still runs them
    itself on the yielded connection."""
    with db.connect(DB_PATH, tables=("rejected_candidates", "rejection_events")) as conn:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rejection_events_gate ON rejection_events (strategy, gate_name)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rejection_events_unresolved ON rejection_events (ticker) "
            "WHERE resolved = 0"
        )
        db.add_column_if_missing(conn, "rejected_candidates", "unit_cost", "REAL")
        db.add_column_if_missing(conn, "rejection_events", "unit_cost", "REAL")
        yield conn
```

Remove the now-unused module-local `_add_column_if_missing` (`:67-73`) — confirm via `grep -n
"_add_column_if_missing" services/candidate_log.py` that nothing else in the module calls the
local copy before deleting it. Add `import contextlib` to the import block if not already
present.

- [ ] **Step 4: Confirm the test passes and nothing else regressed**

Run: `ddev exec -s fastapi python -m pytest tests/test_candidate_log.py -q`
Expected: every test passes, including the three new ones — ~25 existing tests all route
through `_connect()`; a clean pass is direct evidence the migration is call-site-transparent.

---

### Task 4: `services/series_watcher.py`'s sync `_connect()` migrates to `services/db.py` (D1, individually-audited)

**Files:**
- Modify: `services/series_watcher.py:140-186` (imports + `_connect`; `_ensure_schema_aio`,
  `:189-227`, is explicitly **not** touched — D1)
- Test: `tests/test_series_watcher.py` (extend)

**Depends on:** Task 1, Task 2.

**Current state, read directly (`services/series_watcher.py:151-186`):**

```python
def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(capture_writer.RAW_TRADES_DDL_SQL)                                    # :159
    conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_trades_series ON raw_trades (series, observed_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_trades_ticker ON raw_trades (ticker, observed_at)")
    conn.execute("""CREATE TABLE IF NOT EXISTS book_snapshots (...)""")                # 15 columns
    conn.execute("CREATE INDEX IF NOT EXISTS idx_book_ticker ON book_snapshots (ticker, observed_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_book_series ON book_snapshots (series, observed_at)")
    return conn
```

Two tables (`raw_trades`, `book_snapshots`), four indexes, no `add_column_if_missing`. No
`busy_timeout` set (D3 — adopt `db.connect()`'s 5000ms default explicitly).

**PR #23 lock — confirmed non-interacting (5-module pre-audit, re-verified directly here):**
`_buffer_lock` (`:140`) guards only `_book_buffer` append/swap; the lock is released **before**
`with _connect() as conn:` is reached in `flush()` (`:419`) — the module's own comment (`:410-413`)
states this is deliberate. No lock-ordering change needed; `db.connect()` substitutes directly.

**D1 — async path is untouched.** `_ensure_schema_aio` (`:189-227`) keeps calling
`await conn.execute(capture_writer.RAW_TRADES_DDL_SQL)` directly, exactly as today. This task's
diff must not include any line in `:189-227`'s range — verified as a step below, not assumed.

**Event-loop note:** the sync `_connect()` itself is not called from the FastAPI event loop
directly (all its callers — `record_book`/`flush` etc. — already route through
`tick_executor.run()` or the daemon thread; the async paths use `_aio_db.connection_for()`
separately). No event-loop change from this migration.

- [ ] **Step 1: Write the failing tests first**

Add to `tests/test_series_watcher.py`:

```python
def test_connect_closes_its_connection(tmp_path, monkeypatch):
    """_RecordingConnection wraps the real connection instead of mutating
    conn.close directly - that raises AttributeError on this container's
    Python (sqlite3.Connection.close is read-only), the same defect Task
    1's own implementation (PR #518) found and fixed against
    tests/test_signal_log.py's proven pattern (Tier 0's own Task 5),
    applied here for this module's test file."""
    import sqlite3

    closed = []
    real_connect = sqlite3.connect

    class _RecordingConnection:
        def __init__(self, inner):
            self._inner = inner

        def close(self):
            closed.append(True)
            self._inner.close()

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, *exc_info):
            return self._inner.__exit__(*exc_info)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def _tracking_connect(*args, **kwargs):
        return _RecordingConnection(real_connect(*args, **kwargs))

    monkeypatch.setattr(sw.db.sqlite3, "connect", _tracking_connect)
    with sw._connect() as conn:
        conn.execute("SELECT 1")
    assert closed == [True]


def test_connect_still_creates_both_tables_and_all_four_indexes(tmp_path, monkeypatch):
    monkeypatch.setattr(sw, "DB_PATH", tmp_path / "sw.db")
    with sw._connect() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert {"raw_trades", "book_snapshots"} <= tables
        indexes = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )}
        assert {
            "idx_raw_trades_series", "idx_raw_trades_ticker",
            "idx_book_ticker", "idx_book_series",
        } <= indexes


def test_connect_sets_explicit_busy_timeout_pragma(tmp_path, monkeypatch):
    monkeypatch.setattr(sw, "DB_PATH", tmp_path / "sw.db")
    with sw._connect() as conn:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_connect_uses_the_shared_raw_trades_ddl(tmp_path, monkeypatch):
    """Pre-existing test (services/series_watcher.py's own test file) -
    confirm it still passes unmodified; not new to this task, restated here
    so the migration diff is checked against it explicitly."""
    monkeypatch.setattr(sw, "DB_PATH", tmp_path / "sw.db")
    with sw._connect() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(raw_trades)")}
        assert cols  # non-empty - table exists with the shared DDL's columns
```

Run: `ddev exec -s fastapi python -m pytest tests/test_series_watcher.py -k 'closes_its_connection or four_indexes or busy_timeout_pragma' -q`
Expected: fails against current `main`.

- [ ] **Step 2: Read the exact current function before editing**

Run: `sed -n '140,227p' services/series_watcher.py`. Confirm `_connect()` (`:151-186`) matches
the transcription above and `_ensure_schema_aio()` (`:189-227`) is what this task must leave
untouched.

- [ ] **Step 3: Apply the fix**

Add `db` to the existing import block. Register `raw_trades` using Task 2's shared `init_fn`
(D2); register `book_snapshots` with a local `init_fn` (owned by this module, no cross-module
identity concern):

```python
from services import capture_writer, db

db.register_schema("raw_trades", capture_writer.init_raw_trades)


def _init_book_snapshots(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS book_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            series TEXT NOT NULL,
            observed_at REAL NOT NULL,
            exchange_ts REAL,
            price_dollars REAL,
            yes_bid_dollars REAL,
            yes_ask_dollars REAL,
            yes_bid_size_fp REAL,
            yes_ask_size_fp REAL,
            volume_fp REAL,
            open_interest_fp REAL,
            dollar_volume REAL,
            dollar_open_interest REAL,
            last_trade_size_fp REAL,
            raw_json TEXT NOT NULL
        )
        """
    )


db.register_schema("book_snapshots", _init_book_snapshots)


@contextlib.contextmanager
def _connect():
    """Every existing `with _connect() as conn:` call site keeps working
    unchanged - now backed by services/db.py's closing connect(). The four
    CREATE INDEX statements aren't expressible in a table's registered
    init_fn (index creation isn't table schema), so this wrapper still runs
    them itself. _ensure_schema_aio() (async, _aio_db.connection_for()'s
    cached path) is untouched by this migration - see this task's own D1
    note; it keeps calling capture_writer.RAW_TRADES_DDL_SQL directly."""
    with db.connect(DB_PATH, tables=("raw_trades", "book_snapshots")) as conn:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_trades_series ON raw_trades (series, observed_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_trades_ticker ON raw_trades (ticker, observed_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_book_ticker ON book_snapshots (ticker, observed_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_book_series ON book_snapshots (series, observed_at)")
        yield conn
```

Add `import contextlib` if not already present.

- [ ] **Step 4: Confirm `_ensure_schema_aio` is untouched**

Run: `git diff main -- services/series_watcher.py | grep -n '^[+-]' | grep -A2 -B2
'_ensure_schema_aio'` (against this task's own branch) — expected: empty output, or output
confined to context lines only, never a `+`/`-` line inside that function's body.

- [ ] **Step 5: Confirm tests pass**

Run: `ddev exec -s fastapi python -m pytest tests/test_series_watcher.py -q`
Expected: full file passes — this file has 10+ tests that bypass `_connect()` via raw
`sqlite3.connect(sw.DB_PATH)`, unaffected by this migration; a clean pass is evidence the
schema those tests depend on is unchanged.

---

### Task 5: `services/observability/observability.py` migrates to `services/db.py` (retargets PR #484's Task 6)

**Files:**
- Modify: `services/observability/observability.py:22-59` (imports + `_connect`)
- Test: `tests/test_observability.py` (extend)

**Depends on:** Task 1.

**Current state, read directly (`services/observability/observability.py:41-59`):**

```python
def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS metric_samples (...)""")   # 4 columns
    conn.execute("CREATE INDEX IF NOT EXISTS idx_metric_samples_metric_time ON metric_samples(metric, observed_at)")
    return conn
```

Single table, single index, no `add_column_if_missing` — simplest of the three retargeted
modules. Six call sites (`:66, 80, 89, 104, 124, 130`), all `with _connect() as conn:`.
No `busy_timeout` set (D3). Event loop: `maybe_capture` runs in the synchronous trading-loop
tick — no change.

- [ ] **Step 1: Write the failing tests first**

Add to `tests/test_observability.py`:

```python
def test_connect_closes_its_connection(tmp_path, monkeypatch):
    """_RecordingConnection wraps the real connection instead of mutating
    conn.close directly - that raises AttributeError on this container's
    Python (sqlite3.Connection.close is read-only), the same defect Task
    1's own implementation (PR #518) found and fixed against
    tests/test_signal_log.py's proven pattern (Tier 0's own Task 5),
    applied here for this module's test file."""
    import sqlite3

    closed = []
    real_connect = sqlite3.connect

    class _RecordingConnection:
        def __init__(self, inner):
            self._inner = inner

        def close(self):
            closed.append(True)
            self._inner.close()

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, *exc_info):
            return self._inner.__exit__(*exc_info)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def _tracking_connect(*args, **kwargs):
        return _RecordingConnection(real_connect(*args, **kwargs))

    monkeypatch.setattr(obs.db.sqlite3, "connect", _tracking_connect)
    with obs._connect() as conn:
        conn.execute("SELECT 1")
    assert closed == [True]


def test_connect_still_creates_table_and_index(tmp_path, monkeypatch):
    monkeypatch.setattr(obs, "DB_PATH", tmp_path / "obs.db")
    with obs._connect() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "metric_samples" in tables
        indexes = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )}
        assert "idx_metric_samples_metric_time" in indexes


def test_connect_sets_explicit_busy_timeout_pragma(tmp_path, monkeypatch):
    monkeypatch.setattr(obs, "DB_PATH", tmp_path / "obs.db")
    with obs._connect() as conn:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
```

Run: `ddev exec -s fastapi python -m pytest tests/test_observability.py -k 'closes_its_connection or table_and_index or busy_timeout_pragma' -q`
Expected: fails against current `main`.

- [ ] **Step 2: Read the exact current function before editing**

Run: `sed -n '22,59p' services/observability/observability.py`. Confirm it matches the
transcription above.

- [ ] **Step 3: Apply the fix**

```python
from services import db

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "observability.db"


def _init_metric_samples(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS metric_samples (
            observed_at REAL NOT NULL,
            metric TEXT NOT NULL,
            value REAL,
            labels_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )


db.register_schema("metric_samples", _init_metric_samples)


@contextlib.contextmanager
def _connect():
    """Every existing `with _connect() as conn:` call site keeps working
    unchanged - now backed by services/db.py's closing connect()."""
    with db.connect(DB_PATH, tables=("metric_samples",)) as conn:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_metric_samples_metric_time "
            "ON metric_samples(metric, observed_at)"
        )
        yield conn
```

Add `import contextlib` if not already present.

- [ ] **Step 4: Confirm tests pass**

Run: `ddev exec -s fastapi python -m pytest tests/test_observability.py -q`
Expected: full file passes.

---

### Task 6: `services/risk_manager.py` migrates to `services/db.py` (safety-adjacent — dedicated PR)

**Files:**
- Modify: `services/risk_manager.py:49-125`
- Test: `tests/test_risk_manager.py` (extend)

**Depends on:** Task 1. **Ships as its own individually reviewed PR — never bundled with any
other task in this plan** (Global Constraints).

**Current state (`services/risk_manager.py:49-75`, 5-module pre-audit, re-cited here):**
`_connect(db_path: Path)` — parameterized, not module-level `DB_PATH`-only. `PRAGMA
journal_mode=WAL` (`:58`); `_add_column_if_missing(conn, "risk_meta", "day_start_date", "TEXT")`
(`:74`); no indexes, no `busy_timeout`. Instance-method wrapper `def _connect(self): return
_connect(self.db_path)` (`:124-125`). Real call sites `:102, 128`, both `with self._connect() as
conn:`. `RiskManager.__init__` resolves `self.db_path = db_path or DB_PATH` at *construction*
time (`:89`, documented at `:83-88` specifically so `monkeypatch.setattr(rm, "DB_PATH", ...)`
still works, and so a caller can pass an explicit `db_path` for "a second, independent risk
tracker"). No test calls `_connect()` directly — `tests/test_risk_manager.py` interacts purely
through `RiskManager`'s public API.

**Safety framing:** this task touches only `_connect`/`_connect(self)` — the kill-switch logic
(`check_daily_loss`, `day_start_bankroll` comparison, etc.) is in different functions, untouched.
Per CLAUDE.md's safety invariants, this still gets the same real-diff scrutiny as any other
change to this file, not "same pattern as the others" taken on faith — this is why it's its own
dedicated PR with its own full review, not bundled into Task 3–5's PR.

- [ ] **Step 1: Write the failing tests first**

Add to `tests/test_risk_manager.py`:

```python
def test_connect_closes_its_connection(tmp_path, monkeypatch):
    """_RecordingConnection wraps the real connection instead of mutating
    conn.close directly - that raises AttributeError on this container's
    Python (sqlite3.Connection.close is read-only), the same defect Task
    1's own implementation (PR #518) found and fixed against
    tests/test_signal_log.py's proven pattern (Tier 0's own Task 5),
    applied here for this module's test file."""
    import sqlite3

    closed = []
    real_connect = sqlite3.connect

    class _RecordingConnection:
        def __init__(self, inner):
            self._inner = inner

        def close(self):
            closed.append(True)
            self._inner.close()

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, *exc_info):
            return self._inner.__exit__(*exc_info)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def _tracking_connect(*args, **kwargs):
        return _RecordingConnection(real_connect(*args, **kwargs))

    monkeypatch.setattr(rm.db.sqlite3, "connect", _tracking_connect)
    tracker = rm.RiskManager(db_path=tmp_path / "rm.db")
    with tracker._connect() as conn:
        conn.execute("SELECT 1")
    assert closed == [True]


def test_connect_still_creates_table_with_day_start_date_column(tmp_path, monkeypatch):
    tracker = rm.RiskManager(db_path=tmp_path / "rm.db")
    with tracker._connect() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "risk_meta" in tables
        cols = {r[1] for r in conn.execute("PRAGMA table_info(risk_meta)")}
        assert "day_start_date" in cols


def test_connect_sets_explicit_busy_timeout_pragma(tmp_path, monkeypatch):
    tracker = rm.RiskManager(db_path=tmp_path / "rm.db")
    with tracker._connect() as conn:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_monkeypatched_db_path_still_works(tmp_path, monkeypatch):
    """This module's own DB_PATH-resolved-at-construction-time mechanism
    (:83-88's own comment) - confirm it still works after migration."""
    monkeypatch.setattr(rm, "DB_PATH", tmp_path / "monkeypatched_rm.db")
    tracker = rm.RiskManager()
    with tracker._connect() as conn:
        conn.execute("SELECT 1")
    assert (tmp_path / "monkeypatched_rm.db").exists()
```

Run: `ddev exec -s fastapi python -m pytest tests/test_risk_manager.py -k 'closes_its_connection or day_start_date_column or busy_timeout_pragma or monkeypatched_db_path' -q`
Expected: fails against current `main`.

- [ ] **Step 2: Read the exact current function before editing**

Run: `sed -n '49,125p' services/risk_manager.py`. Confirm the transcription above still matches.

- [ ] **Step 3: Apply the fix**

```python
from services import db


def _init_risk_meta(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS risk_meta (...)""")  # exact existing DDL, unchanged


db.register_schema("risk_meta", _init_risk_meta)


@contextlib.contextmanager
def _connect(db_path: Path):
    with db.connect(db_path, tables=("risk_meta",)) as conn:
        db.add_column_if_missing(conn, "risk_meta", "day_start_date", "TEXT")
        yield conn
```

The instance method (`:124-125`) is unaffected — `def _connect(self): return _connect(self.db_path)`
already delegates to the module-level function and needs no change beyond the module-level
function itself now being a generator wrapped by `db.connect()`.

- [ ] **Step 4: Confirm tests pass**

Run: `ddev exec -s fastapi python -m pytest tests/test_risk_manager.py -q`
Expected: full file passes, including kill-switch behavior tests — unaffected by this migration
since none of them touch connection plumbing.

---

### Task 7: `services/paper_broker.py` migrates to `services/db.py` (safety-adjacent — dedicated PR)

**Files:**
- Modify: `services/paper_broker.py:163-338`
- Test: `tests/test_paper_broker.py` (extend)

**Depends on:** Task 1. **Ships as its own individually reviewed PR — never bundled with any
other task in this plan.**

**Current state — read directly in full during this plan's own self-review
(`services/paper_broker.py:163-267`), correcting the 5-module pre-audit's own summary, which
undercounted the table count:** `_connect(db_path: Path)` — same parameterized shape as
`risk_manager.py`. `PRAGMA journal_mode=WAL` (`:172`). **Four tables, not the pre-audit's
implied one or two**: `broker_meta` (`:175-180`, `bankroll`/`starting_bankroll`, no schema
evolution ever), `positions` (`:184-190`), `trades` (`:195-203`), `pending_orders`
(`:253-263`). **13 `_add_column_if_missing` calls, distributed across three of the four tables,
in this exact order:**

| # | Line | Table | Column | Type |
|---|---|---|---|---|
| 1 | `:209` | `positions` | `config_fingerprint` | `TEXT` |
| 2 | `:210` | `trades` | `config_fingerprint` | `TEXT` |
| 3 | `:215` | `positions` | `entry_fee` | `REAL` |
| 4 | `:216` | `trades` | `fee` | `REAL` |
| 5 | `:222` | `positions` | `hold_to_settlement` | `INTEGER` |
| 6 | `:226` | `trades` | `signal_seen_at` | `REAL` |
| 7 | `:236` | `trades` | `excluded` | `INTEGER NOT NULL DEFAULT 0` |
| 8 | `:242` | `trades` | `netting_improvement_usd` | `REAL` |
| 9 | `:243` | `trades` | `netting_bar_usd` | `REAL` |
| 10 | `:244` | `trades` | `netting_vol_ratio` | `REAL` |
| 11 | `:245` | `trades` | `netting_exit_fee_usd` | `REAL` |
| 12 | `:265` | `pending_orders` | `signal_seen_at` | `REAL` |
| 13 | `:266` | `pending_orders` | `confidence` | `REAL` |

`broker_meta` gets none. One index: `CREATE INDEX IF NOT EXISTS idx_trades_excluded ON trades
(excluded)` (`:237`) — **ordering matters here**: it runs immediately after call #7 adds the
`excluded` column it indexes, not after all 13 calls — an `init_fn` that reordered "add all
columns, then create all indexes" would still work today (SQLite's `CREATE INDEX` doesn't care
when the column was added, only that it exists first), but the migrated callback preserves the
original ordering anyway, since nothing requires changing it and preserving it removes any
question of whether reordering is truly inert. No `busy_timeout`. Instance-method wrapper
(`:337-338`), 10 real call sites (`:294, 414, 465, 477, 637, 709, 759, 785, 799, 808`), all
`with self._connect() as conn:`. `self.db_path = db_path or DB_PATH` resolved at construction
(`:278`), same pattern and same reason as `risk_manager.py`.

**Safety framing:** this is the paper-trading execution path's own persistence — the module
with the most schema-evolution history in scope. This task touches connection plumbing only
(`_connect`); order-execution, bankroll, and P&L logic are in different functions, untouched.
Dedicated PR, full diff review, per CLAUDE.md's safety invariants.

- [ ] **Step 1: Write the failing tests first**

Add to `tests/test_paper_broker.py`:

```python
def test_connect_closes_its_connection(tmp_path, monkeypatch):
    """_RecordingConnection wraps the real connection instead of mutating
    conn.close directly - that raises AttributeError on this container's
    Python (sqlite3.Connection.close is read-only), the same defect Task
    1's own implementation (PR #518) found and fixed against
    tests/test_signal_log.py's proven pattern (Tier 0's own Task 5),
    applied here for this module's test file."""
    import sqlite3

    closed = []
    real_connect = sqlite3.connect

    class _RecordingConnection:
        def __init__(self, inner):
            self._inner = inner

        def close(self):
            closed.append(True)
            self._inner.close()

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, *exc_info):
            return self._inner.__exit__(*exc_info)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def _tracking_connect(*args, **kwargs):
        return _RecordingConnection(real_connect(*args, **kwargs))

    monkeypatch.setattr(pb.db.sqlite3, "connect", _tracking_connect)
    broker = pb.PaperBroker(db_path=tmp_path / "pb.db")
    with broker._connect() as conn:
        conn.execute("SELECT 1")
    assert closed == [True]


def test_connect_still_creates_all_four_tables_thirteen_columns_and_the_index(tmp_path, monkeypatch):
    broker = pb.PaperBroker(db_path=tmp_path / "pb.db")
    with broker._connect() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert {"broker_meta", "positions", "trades", "pending_orders"} <= tables
        positions_cols = {r[1] for r in conn.execute("PRAGMA table_info(positions)")}
        for expected in ("config_fingerprint", "entry_fee", "hold_to_settlement"):
            assert expected in positions_cols, expected
        trades_cols = {r[1] for r in conn.execute("PRAGMA table_info(trades)")}
        for expected in (
            "config_fingerprint", "fee", "signal_seen_at", "excluded",
            "netting_improvement_usd", "netting_bar_usd", "netting_vol_ratio",
            "netting_exit_fee_usd",
        ):
            assert expected in trades_cols, expected
        pending_cols = {r[1] for r in conn.execute("PRAGMA table_info(pending_orders)")}
        for expected in ("signal_seen_at", "confidence"):
            assert expected in pending_cols, expected
        indexes = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )}
        assert "idx_trades_excluded" in indexes


def test_connect_sets_explicit_busy_timeout_pragma(tmp_path, monkeypatch):
    broker = pb.PaperBroker(db_path=tmp_path / "pb.db")
    with broker._connect() as conn:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
```

Run: `ddev exec -s fastapi python -m pytest tests/test_paper_broker.py -k 'closes_its_connection or four_tables_thirteen_columns or busy_timeout_pragma' -q`
Expected: fails against current `main`.

- [ ] **Step 2: Read the exact current function before editing**

Run: `sed -n '163,267p' services/paper_broker.py`. Confirm the four-table, 13-column-add
transcription in this task's own "Current state" section above still matches — it was read
directly during this plan's own drafting/self-review (not from the pre-audit's summary alone,
which undercounted the table set), but a fresh read before editing is still required per Gate 1.

- [ ] **Step 3: Apply the fix**

Register all four tables (`broker_meta` needs no extra statements — no column adds, no index —
so its `init_fn` is a bare `CREATE TABLE IF NOT EXISTS`, same shape as Task 8's `candidates`).
Inside the migrated `_connect()`'s body, after `db.connect(db_path, tables=("broker_meta",
"positions", "trades", "pending_orders"))`, run the 13 `add_column_if_missing` calls and the one
index in the exact order shown in the table above (column #7 on `trades`, `excluded`, before the
index that references it).

- [ ] **Step 4: Confirm tests pass**

Run: `ddev exec -s fastapi python -m pytest tests/test_paper_broker.py -q`
Expected: full file passes — this is the module with the most existing test coverage among the
5 named modules; a clean pass is strong evidence of call-site transparency.

---

### Task 8: `services/candidate_ledger.py` migrates to `services/db.py` (safety-adjacent — dedicated PR)

**Files:**
- Modify: `services/candidate_ledger.py:20-38`
- Test: `tests/test_candidate_ledger.py` (extend)

**Depends on:** Task 1. **Ships as its own individually reviewed PR — never bundled with any
other task in this plan.**

**Current state (`services/candidate_ledger.py:20-38`, 5-module pre-audit, re-cited) — simplest
of the three safety-adjacent modules by a wide margin:** parameterless `_connect()`, reads
module-level `DB_PATH` directly. `PRAGMA journal_mode=WAL` (`:33`, added later per its own
code-review-fix comment — "every sibling persistence module already has this; this one was
missed"). **Nothing else** — no `add_column_if_missing`, no index, no `busy_timeout`. Single
table `candidates`, single unconditional `CREATE TABLE IF NOT EXISTS` (`:35-37`), no schema
evolution ever. No instance wrapper — pure-function module; all four public functions (`claim`,
`record_decision`, `decision_for`, `stats`) call `_connect()` directly, all `with`-wrapped
(`:48, 60, 68, 74`). One test calls `_connect()` directly, already correctly `with`-wrapped
(`tests/test_candidate_ledger.py:56`).

**Safety framing:** `candidate_ledger.claim()`/`record_decision()` are called from
`services/whale_stream/decision_bridge.py` — the trading-critical decision path (per the design
spec's citation of PR #484's own already-verified analysis). This task touches connection
plumbing only; `decision_bridge.py` itself is not touched by this task at all.

- [ ] **Step 1: Write the failing tests first**

Add to `tests/test_candidate_ledger.py`:

```python
def test_connect_closes_its_connection(tmp_path, monkeypatch):
    """_RecordingConnection wraps the real connection instead of mutating
    conn.close directly - that raises AttributeError on this container's
    Python (sqlite3.Connection.close is read-only), the same defect Task
    1's own implementation (PR #518) found and fixed against
    tests/test_signal_log.py's proven pattern (Tier 0's own Task 5),
    applied here for this module's test file."""
    import sqlite3

    closed = []
    real_connect = sqlite3.connect

    class _RecordingConnection:
        def __init__(self, inner):
            self._inner = inner

        def close(self):
            closed.append(True)
            self._inner.close()

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, *exc_info):
            return self._inner.__exit__(*exc_info)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def _tracking_connect(*args, **kwargs):
        return _RecordingConnection(real_connect(*args, **kwargs))

    monkeypatch.setattr(candidate_ledger.db.sqlite3, "connect", _tracking_connect)
    with candidate_ledger._connect() as conn:
        conn.execute("SELECT 1")
    assert closed == [True]


def test_connect_still_creates_candidates_table(tmp_path, monkeypatch):
    monkeypatch.setattr(candidate_ledger, "DB_PATH", tmp_path / "cl.db")
    with candidate_ledger._connect() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "candidates" in tables


def test_connect_sets_explicit_busy_timeout_pragma(tmp_path, monkeypatch):
    monkeypatch.setattr(candidate_ledger, "DB_PATH", tmp_path / "cl.db")
    with candidate_ledger._connect() as conn:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
```

Run: `ddev exec -s fastapi python -m pytest tests/test_candidate_ledger.py -k 'closes_its_connection or candidates_table or busy_timeout_pragma' -q`
Expected: fails against current `main`.

- [ ] **Step 2: Read the exact current function before editing**

Run: `sed -n '20,38p' services/candidate_ledger.py`. Confirm the transcription above matches.

- [ ] **Step 3: Apply the fix**

```python
from services import db


def _init_candidates(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS candidates (...)""")  # exact existing DDL, unchanged


db.register_schema("candidates", _init_candidates)


@contextlib.contextmanager
def _connect():
    with db.connect(DB_PATH, tables=("candidates",)) as conn:
        yield conn
```

The simplest migration in this plan — no extra statements needed inside the `with` block beyond
the registered table itself.

- [ ] **Step 4: Confirm tests pass**

Run: `ddev exec -s fastapi python -m pytest tests/test_candidate_ledger.py -q`
Expected: full file passes.

---

### Task 9: `services/series_evaluator.py` migrates to `services/db.py` (lighter-caution)

**Files:**
- Modify: `services/series_evaluator.py:50-73`
- Test: `tests/test_series_evaluator.py` (extend)

**Depends on:** Task 1. **May bundle with Task 10 in one PR (PR F)** — lighter-caution tier, not
"never bundled," but the PR description must call out both modules' proximity to live signal
generation / position-opening explicitly, not treat this as a routine batch.

**Current state (general-bucket pre-audit, `services/series_evaluator.py:50-73`):** `PRAGMA
journal_mode=WAL`; single `CREATE TABLE IF NOT EXISTS series_status (...)`. No index, no
`add_column_if_missing`, no `busy_timeout`. 7 call sites, all `with _connect() as conn:`. Tests
(`tests/test_series_evaluator.py`) also monkeypatch `signal_log.DB_PATH` alongside their own —
cross-module test coupling to account for, not a reason this module's own migration is unsafe.

**Why lighter-caution, not general-bucket:** `series_evaluator.py` governs watchlist
admission/removal — upstream of, and feeding into, live whale-signal generation (design spec's
module classification, carried from PR #484's own independent adversarial-review finding).

**Event-loop note:** `clear_all()` is called directly from `reset_broker`'s async handler — same
pre-existing, tracked (#510) exposure named in Global Constraints; not fixed by this task.

- [ ] **Step 1: Write the failing tests first** (same 3-test shape as Task 5 — closes-on-exit,
  still-creates-table, explicit-busy-timeout-pragma — against `series_status`).

Run: `ddev exec -s fastapi python -m pytest tests/test_series_evaluator.py -k 'closes_its_connection or table_created or busy_timeout_pragma' -q`
Expected: fails against current `main`.

- [ ] **Step 2: Read the exact current function before editing**

Run: `sed -n '50,73p' services/series_evaluator.py`. Confirm the single-table, no-extras shape
this task assumes.

- [ ] **Step 3: Apply the fix** — same shape as Task 8 (single table, no extras).

- [ ] **Step 4: Confirm tests pass**

Run: `ddev exec -s fastapi python -m pytest tests/test_series_evaluator.py -q`
Expected: full file passes, including the `signal_log.DB_PATH` cross-coupled tests.

---

### Task 10: `services/trade_category.py` migrates to `services/db.py` (lighter-caution)

**Files:**
- Modify: `services/trade_category.py:27-78`
- Test: `tests/test_trade_category.py` (extend)

**Depends on:** Task 1. **May bundle with Task 9 in one PR (PR F)** — same caution framing.

**Current state (general-bucket pre-audit, `services/trade_category.py:27-78`):** `PRAGMA
journal_mode=WAL`; `CREATE TABLE IF NOT EXISTS trade_category (...)`; a PRAGMA-`table_info`-
guarded `ALTER TABLE ... ADD COLUMN subcategory TEXT`, **inlined rather than calling the shared
`_add_column_if_missing` helper** — same idempotent-check shape, different code path. This
migration consolidates it onto `db.add_column_if_missing` (removing the inlined duplicate),
matching the architecture audit's §9.2 finding this whole migration exists partly to close. 6
call sites, all `with _connect() as conn:`. No `busy_timeout`.

**Why lighter-caution:** writes once per position **open**, participating in the live
position-opening sequence even though it only records metadata (design spec's module
classification).

**Event-loop note:** `clear_range()` is called directly from `reset_broker`'s async handler
(#510, not fixed here). `clear_all()` is not called from that route — its own exposure is not
independently confirmed by either pre-audit; this task does not need to resolve that to migrate
safely (the migration doesn't change when or how `clear_all()` is called, only how `_connect()`
manages its own connection).

- [ ] **Step 1: Write the failing tests first** (closes-on-exit, still-creates-table-with-
  subcategory-column, explicit-busy-timeout-pragma).

Run: `ddev exec -s fastapi python -m pytest tests/test_trade_category.py -k 'closes_its_connection or subcategory or busy_timeout_pragma' -q`
Expected: fails against current `main`.

- [ ] **Step 2: Read the exact current function before editing**

Run: `sed -n '27,78p' services/trade_category.py`. Confirm the inlined `ALTER TABLE` guard's
exact form before replacing it with `db.add_column_if_missing`.

- [ ] **Step 3: Apply the fix** — register `trade_category`'s `CREATE TABLE`; inside the migrated
`_connect()`, replace the inlined `PRAGMA table_info`-guard with
`db.add_column_if_missing(conn, "trade_category", "subcategory", "TEXT")`.

- [ ] **Step 4: Confirm tests pass**

Run: `ddev exec -s fastapi python -m pytest tests/test_trade_category.py -q`
Expected: full file passes.

---

### Task 11: `services/settlement_edge.py` migrates to `services/db.py` (individually-audited)

**Files:**
- Modify: `services/settlement_edge.py:49-83`
- Test: `tests/test_settlement_edge.py` (extend)

**Depends on:** Task 1.

**Current state (5-module pre-audit, `services/settlement_edge.py:49-83`):** parameterless
`_connect()`. `PRAGMA journal_mode=WAL` (`:52`). Two indexes: `idx_se_window` (`:76-78`, plain
composite) and **`idx_se_unresolved`** (`:79-82`) — a **partial index**, `... WHERE settled_yes
IS NULL`. **This is the only partial index across the 5 named modules; the migrated callback
must preserve the `WHERE` clause exactly**, not just the column list — dropping it silently
stops the index from matching SQLite's query planner the way it does today. No
`add_column_if_missing` — single-shot `CREATE TABLE IF NOT EXISTS` (`:53-75`), no schema
evolution has ever been needed. `conn.row_factory = sqlite3.Row` is set at the **call site**
(`edge_report()`, `:275-276`), not inside `_connect()` — needs no `db.py`-level handling, carries
over unchanged at that one call site. 7 call sites (`:193, 215, 275, 365, 392, 404, 414`), all
`with _connect() as conn:`.

- [ ] **Step 1: Write the failing tests first**

Add, in addition to the standard closes-on-exit / busy-timeout-pragma pair:

```python
def test_connect_still_creates_both_indexes_with_partial_where_clause(tmp_path, monkeypatch):
    monkeypatch.setattr(se, "DB_PATH", tmp_path / "se.db")
    with se._connect() as conn:
        rows = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='index' AND name IN "
            "('idx_se_window', 'idx_se_unresolved')"
        ).fetchall()
        by_name = {name: sql for name, sql in rows}
        assert "idx_se_window" in by_name
        assert "idx_se_unresolved" in by_name
        assert "WHERE settled_yes IS NULL" in by_name["idx_se_unresolved"]
```

Run: `ddev exec -s fastapi python -m pytest tests/test_settlement_edge.py -k 'closes_its_connection or partial_where_clause or busy_timeout_pragma' -q`
Expected: fails against current `main`.

- [ ] **Step 2: Read the exact current function before editing**

Run: `sed -n '49,83p' services/settlement_edge.py`. Confirm the partial index's exact `WHERE`
clause text before transcribing it into the migrated `_connect()`.

- [ ] **Step 3: Apply the fix** — register the module's table (`window_observations` — verified
directly via `grep -n "CREATE TABLE" services/settlement_edge.py`, not the module's own file
name, which this plan's first draft wrongly assumed the table shared); the two `CREATE INDEX`
statements (including the exact partial-index `WHERE` clause, transcribed verbatim from Step 2's
read, not from this plan's own citation) run inside the migrated `_connect()`'s body, same
pattern as Task 3's two indexes.

- [ ] **Step 4: Confirm tests pass**

Run: `ddev exec -s fastapi python -m pytest tests/test_settlement_edge.py -q`
Expected: full file passes, including `edge_report()`'s own tests (row_factory unaffected).

---

### Task 12: `services/backup/backup.py` — primary `_connect()` only (split-pattern, partial migration)

**Files:**
- Modify: `services/backup/backup.py:96-122` (primary `_connect()` only — `_backup_one_file`'s
  `src_conn`/`dest_conn` pair, `:152-160`, is explicitly **not** touched, already correct)
- Test: `tests/test_backup.py` (extend)

**Depends on:** Task 1.

**Current state (general-bucket pre-audit, `services/backup/backup.py:96-122`):** `PRAGMA
journal_mode=WAL`; `CREATE TABLE IF NOT EXISTS backup_runs (...)`; `CREATE INDEX IF NOT EXISTS
idx_backup_runs_started_at`; one `_add_column_if_missing(conn, "backup_runs", "tier", "TEXT")`.
Two call sites (`:227, 252`), both `with _connect() as conn:` — leaking, per the source
material's own correction (verified directly: `with X as conn:` only proves closure if `X` is a
real context manager; a plain `sqlite3.Connection`'s native `__enter__`/`__exit__` only commits/
rolls back). **Separate, already-correct pattern**: `_backup_one_file` (`:152-160`) opens
`src_conn`/`dest_conn` directly via `sqlite3.connect()` with manual `try/finally: close()` — not
part of this task's scope.

**Event loop:** NO — dispatched via `task_supervisor.supervise()` wrapping `asyncio.to_thread`
(`:386-389, 426-432`). No change from this migration.

- [ ] **Step 1: Write the failing tests first** (closes-on-exit, still-creates-table-index-and-
  tier-column, explicit-busy-timeout-pragma — same 3-test shape as Task 3).

Run: `ddev exec -s fastapi python -m pytest tests/test_backup.py -k 'closes_its_connection or tier_column or busy_timeout_pragma' -q`
Expected: fails against current `main`.

- [ ] **Step 2: Read the exact current function before editing**

Run: `sed -n '96,160p' services/backup/backup.py`. Confirm the primary `_connect()`'s bounds
(`:96-122`) and `_backup_one_file`'s bounds (`:152-160`) are distinct and don't overlap before
editing only the former.

- [ ] **Step 3: Apply the fix** — same shape as Task 3 (one table, one index, one
`add_column_if_missing`, inside the migrated primary `_connect()`'s body). `_backup_one_file`'s
`src_conn`/`dest_conn` pattern is untouched — confirm via `git diff main -- services/backup/backup.py`
that no line inside `:152-160`'s range changes.

- [ ] **Step 4: Confirm tests pass**

Run: `ddev exec -s fastapi python -m pytest tests/test_backup.py -q`
Expected: full file passes, including the file-copy backup tests (untouched pattern).

---

### Task 13: `tools/coordination_engine.py` migrates to `services/db.py` (lowest priority — 6-file call-site-shape update)

**Files:**
- Modify: `tools/coordination_engine.py:29-63` (`_connect()`)
- Modify: `tools/quality_coordination.py:584, 657` (the two production call sites — bare
  assignment `conn = ce._connect()` must become `with ce._connect() as conn:`)
- Modify: `tests/test_coordination_engine.py` (15 bare-assignment call sites → `with`)
- Modify: `tests/test_quality_coordination_branch_domain.py` (4: `:80, 106, 126, 149`)
- Modify: `tests/test_quality_coordination_cli.py` (2: `:72, 88`)
- Modify: `tests/test_quality_coordination_cleanup_actions.py` (1: `:95`)

**Depends on:** Task 1.

**Why this task's scope is six files, not one module plus its own test file — the one module in
this migration where Gate 1's default assumption doesn't hold:** every one of this module's 24
real callers (2 production, 22 test) uses `conn = ce._connect()` — a **bare assignment**, never
`with ce._connect() as conn:` — confirmed by repo-wide grep, not assumed:
`grep -rn '_connect()' tests/ tools/ --include='*.py' | grep coordination | grep -v 'def
_connect'`. Migrating `_connect()` to return a `@contextlib.contextmanager` generator (this
migration's whole approach) makes every one of those 24 call sites receive a
generator-context-manager object instead of a `sqlite3.Connection` — `conn.execute(...)` would
fail immediately (a loud `AttributeError`, not a silent leak) if only the module itself were
migrated. This task's own diff must update all 24 call sites, or it must not ship.

**Current state (general-bucket pre-audit, `tools/coordination_engine.py:29-63`):** sets
`conn.row_factory = sqlite3.Row` — the only module in this migration's scope that does; must be
preserved. `PRAGMA journal_mode=WAL`. Three `CREATE TABLE` statements (`signal_state`,
`coordination_runs`, `cleanup_actions`). No indexes, no `add_column_if_missing`, no
`busy_timeout`.

**Priority framing:** a short-lived CLI process (`python -m tools.quality_coordination`), not
server-resident — confirmed via the module's own docstring, "never imported by main.py or any
part of the live trading app." The OS reclaims its fds on exit, so this is real but low-urgency
relative to every other module in this plan. Scheduled last for exactly that reason.

- [ ] **Step 1: Write the failing tests first**

Add to `tests/test_coordination_engine.py`:

```python
def test_connect_closes_its_connection(tmp_path, monkeypatch):
    """_RecordingConnection wraps the real connection instead of mutating
    conn.close directly - that raises AttributeError on this container's
    Python (sqlite3.Connection.close is read-only), the same defect Task
    1's own implementation (PR #518) found and fixed against
    tests/test_signal_log.py's proven pattern (Tier 0's own Task 5),
    applied here for this module's test file."""
    import sqlite3

    closed = []
    real_connect = sqlite3.connect

    class _RecordingConnection:
        def __init__(self, inner):
            self._inner = inner

        def close(self):
            closed.append(True)
            self._inner.close()

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, *exc_info):
            return self._inner.__exit__(*exc_info)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def _tracking_connect(*args, **kwargs):
        return _RecordingConnection(real_connect(*args, **kwargs))

    monkeypatch.setattr(ce.db.sqlite3, "connect", _tracking_connect)
    with ce._connect() as conn:
        conn.execute("SELECT 1")
    assert closed == [True]


def test_connect_still_creates_all_three_tables_and_row_factory(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "DB_PATH", tmp_path / "ce.db")
    with ce._connect() as conn:
        assert conn.row_factory is sqlite3.Row
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert {"signal_state", "coordination_runs", "cleanup_actions"} <= tables
```

Run: `ddev exec -s fastapi python -m pytest tests/test_coordination_engine.py -k 'closes_its_connection or three_tables' -q`
Expected: fails against current `main`.

- [ ] **Step 2: Read the exact current function before editing**

Run: `sed -n '29,63p' tools/coordination_engine.py`. Confirm the three-table, `row_factory`,
no-extras shape.

- [ ] **Step 3: Apply the fix to `tools/coordination_engine.py`** — register the three tables;
migrated `_connect()` sets `conn.row_factory = sqlite3.Row` on the yielded connection before
`yield conn` (this is caller-side state on the connection object, not schema — set inside the
wrapper, same as `settlement_edge.py`'s `row_factory` note in Task 11, but here it's set inside
`_connect()` itself rather than at each call site, so it moves into the wrapper unchanged).

- [ ] **Step 4: Update all 24 call sites to the `with` shape**

Run `grep -rn '_connect()' tests/ tools/ --include='*.py' | grep coordination | grep -v 'def
_connect'` again against the post-Step-3 branch to get the exact current line numbers (they will
have shifted from the pre-audit's citations after Step 3's edit) and change every
`conn = ce._connect()` (or `= coordination_engine._connect()`) to `with ce._connect() as conn:`
(re-indenting the following block), in all six files listed above.

- [ ] **Step 5: Confirm every call site was caught**

Run the same grep once more: `grep -rn '_connect()' tests/ tools/ --include='*.py' | grep
coordination | grep -v 'def _connect' | grep -v 'with '`. Expected: **empty output** — any
remaining match is a bare assignment this task's Step 4 missed.

- [ ] **Step 6: Confirm tests pass**

Run: `ddev exec -s fastapi python -m pytest tests/test_coordination_engine.py
tests/test_quality_coordination_branch_domain.py tests/test_quality_coordination_cli.py
tests/test_quality_coordination_cleanup_actions.py -q`
Expected: full suite across all four files passes.

---

### Task 14: Tracking issue for the 15-module general opportunistic bucket

**Not a code task.** Same treatment PR #484's own Task 7 established: no measured leak
contribution singles any of these 15 out the way the fd census did for the individually-named
modules; a dedicated migration task per module would not make any of them safer, only longer.
Migrates opportunistically, whenever a PR already touches one of them for an unrelated reason.

**The 15-module list, with the arithmetic shown** (design spec's own "17" minus
`candidate_log.py`/`observability.py`, per this plan's correction at the top — both now Tasks 3
and 5 above): `accounts_store.py`, `alerting/alerting.py`, `config/config_performance.py`,
`data_quarantine.py`, `game_state.py`, `history/suggestion_decisions.py`,
`index_feed/ingestion.py`, `market_analyst_agent/_db.py`, `market_events/event_schedule.py`,
`research/research.py`, `reset/reset_log.py`, `reset/trade_archive.py`, `series_cache.py`,
`shadow_mode.py`, `whale_calibration/calibration_history.py` — 15 modules.

**Two items to carry into the tracking issue body, not silently dropped:**
1. **Event-loop exposure via `/api/reset`** — 7 of these 15 (`accounts_store.py`,
   `market_analyst_agent/_db.py`'s `clear_all()`, `reset_log.py`, `trade_archive.py`,
   `shadow_mode.py`, `calibration_history.py`, plus `alerting.py`'s undetermined
   `check_and_alert` path) are already tracked under issue #510
   (autotrade-73's research, PR #512) — cite it, don't re-derive it.
2. **`series_cache.py`'s event-loop exposure is the one remaining unresolved verdict** across
   both pre-audits (general-bucket doc's own "What this document does not cover" section) — flag
   this explicitly in the tracking issue so whoever picks up `series_cache.py`'s migration checks
   it directly first, per Gate 1's own "confirm event-loop exposure" requirement, rather than
   assuming either answer.

- [ ] **Step 1: Create the tracking issue**

`gh issue create --repo thesneakattack/kalshi-whale-poc --title "Persistence module
(services/db.py): migrate the remaining 15 _connect() modules opportunistically" --body "..."`
— body content: the 15-module list above with the corrected arithmetic shown, this plan's own
citation, the two carry-forward items above, and a note that this plan's Tasks 1–13 are
prerequisite (`services/db.py` must exist before any of these 15 migrate). Apply label
`phase:implementing` per `tools/kanban_sync/labels.py`'s vocabulary.

- [ ] **Step 2: Record the issue number**

Add one line to `docs/open-decisions.md`: `services/db.py migration for the remaining 15
_connect() modules · pick up opportunistically per issue #<N>, one PR per module or small
low-risk batch · whoever's touching one of these files next · 2026-09-03`.

**Why this is safe:** documentation only — zero code, zero risk beyond bookkeeping accuracy.
May ship in the same PR as Task 1/2 (PR A) since it has no code dependency on them landing
first, only a logical one (the issue body should say `services/db.py` is a prerequisite).

---

### Task 15: Full regression suite + live validation (Gate 2)

**Depends on:** all of Tasks 1–13 merged (Task 14 is documentation-only and doesn't gate this).

- [ ] **Step 1: Full local suite, once all code PRs have merged to `main`**

Per `.claude/rules/branching-and-ci.md`, read the pushed/merged CI result rather than re-running
the full suite locally as a duplicate check: `gh api
repos/thesneakattack/kalshi-whale-poc/commits/<final-merge-sha>/status`, confirm every required
context green.

- [ ] **Step 2: `import main` sanity check**

Run: `ddev exec -s fastapi python -c 'import main' && echo IMPORT_OK` — confirms every migrated
module's import-time `register_schema` call executes cleanly in the real app's own import graph
(not just in test isolation), the scenario Gate 0's "connect before the registering module is
imported" test guards against in the abstract.

- [ ] **Step 3: Live fd-count check post-deploy**

Read `GET /api/observability/summary` (or the process-wide `open_fds` counter Tier0's own Task 9
added at `services/diagnostics/routes.py:457`) before and after a period of normal live traffic
covering every migrated module's write path. Expected: `open_fds` does not grow monotonically —
direct evidence the migration's connections are actually closing under real load, not only under
test.

- [ ] **Step 4: Table-name-uniqueness fixture, re-run against the real final registration set**

Re-run `test_table_name_uniqueness_across_full_migration_scope` (Task 1) — by this point every
task has landed, so this is the final confirmation the fixture's list matches what actually
registered, not just what this plan intended.

- [ ] **Step 5: `PRAGMA busy_timeout` census**

Run a read-only pragma check against each migrated module's `DB_PATH` in the live app,
confirming `5000` — direct evidence D3's ruling was applied uniformly, not silently dropped by
one task's implementer.

**Why this is safe:** read-only checks and a CI-result read; no code change in this task itself.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
