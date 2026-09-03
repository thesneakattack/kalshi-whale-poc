# Persistence Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the approved persistence-layer redesign into executable, ordered,
testable work: build the shared `services/db.py` closing-connection module and
migrate the three highest-value modules onto it, root-cause-fix
`candidate_log.db`'s live lock contention (currently firing every ~28 minutes),
and record the still-open plan-stage decisions (item 16's aiosqlite call-site
list, item 23's `excluded`/`resolved_side` writer trace, item 15's remaining
22-module tracking) that the design deliberately left unresolved rather than
guessed. Zero changes to trading, risk, sizing, calibration, strategy,
settlement, or auth code.

**Research/design basis (all GO, per this project's "nothing advances on one
pass" HARD RULE):**

- `docs/superpowers/research/2026-09-02-architecture-audit-and-rewrite-considerations.md`
  and `docs/superpowers/research/2026-09-02-architecture-audit-second-pass.md`
  (Tier 2 items 15/16/23/24/28)
- `docs/superpowers/specs/2026-09-03-persistence-layer-redesign-design.md`
  (commit `bb64a7c`, then fixed post-review at `1b44cb0`) — the design this
  plan implements
- `docs/superpowers/specs/2026-09-03-persistence-layer-redesign-design-review.md`
  (commit `12ae40c`) — independent adversarial review, verdict GO-AFTER-FIXES
- `docs/superpowers/specs/2026-09-03-persistence-layer-redesign-design-consolidation.md`
  — verdict **GO** for the design stage; explicit that "implementation plan"
  is the next, separate, still-gated stage (this document)

**Verified fresh at plan-drafting time (2026-09-03), not assumed from the
design's own now-hours-old numbers:** `docs/next-action.md` confirms Tier 0's
own plan (`docs/superpowers/plans/2026-09-03-tier0-live-incident-remediation.md`,
PR #441, merged) is **still code-not-landed** — `services/market_history.py`'s
`_connect()` has no `@contextlib.contextmanager` yet. This plan's scope (the
25 `_connect()`-owning modules *not* in Tier 0's five, plus `candidate_log.db`'s
contention fix) is file-disjoint from Tier 0's five modules
(`market_history.py`, `title_cache.py`, `market_catalog/market_catalog.py`,
`signal_log.py`, `fault_log.py`), so this plan does not block on Tier 0
landing and makes no assumption about when it will.

## Design decisions this plan implements without re-litigating

Per the design's own Decisions record (all GO, all independently reviewed):

1. **Hand-rolled `services/db.py`**, a closing `@contextlib.contextmanager` —
   not SQLAlchemy Core. The measured defect is the fd leak, not per-call
   latency; Variant A (hand-rolled) closes 100% of the time (fd delta 0,
   measured) and is the same shape Tier 0 already shipped for its five
   modules. This plan does not introduce SQLAlchemy Core.
2. **aiosqlite migration (item 16) is sequenced after §1 in practice, not
   blocked by it structurally** — and is *not* a mechanical plumbing swap:
   each call site is a real async rewrite needing its own before/after
   measurement. Trading-critical call sites
   (`decision_bridge.py:66,129`'s `candidate_ledger.claim()`/
   `record_decision()`, routed through `tick_executor.run()`) are explicitly
   **not recommended for migration** by the design, and this plan does not
   migrate any call site to aiosqlite — see Task 8.
3. **`raw_trades`/`index_ticks` stay on SQLite as the system of record.**
   `index_ticks` needs nothing further (§3.4: already small, already fast).
   `raw_trades` gets a read-side, periodically-refreshed DuckDB/Parquet
   export for analytics only — never `sqlite_scanner` as a live read path
   (measured 3.0–3.9× *slower* than native SQLite, confirmed independently
   twice). The design explicitly defers the export's own implementation to
   "its own separate plan" (§6) pending resolution of the `excluded`/
   `resolved_side` open question — this plan resolves that question
   (Task 9) but does not build the export.
4. **`candidate_log.db` contention**: two non-exclusive fixes — widen the
   tick-driven caller's busy-timeout budget (or route it through the
   daemon's own budget), and set an explicit `busy_timeout` pragma via the
   shared module. Both implemented here (Tasks 3–4), preceded by the
   design's own named falsifier (Task 2).
5. **Small-file consolidation (item 28) declined for now** — no task in this
   plan implements it. Revisit only if a real, measured fd/file-count
   constraint reappears after this plan and Tier 0 both land.

## Architecture — mapping the design's five items onto tasks

```
Task 1  services/db.py + its own unit tests            (§1.2-§1.4)
   │
   ├─► Task 2  candidate_log.db falsifier: per-caller attribution on
   │           flush_retained_on_lock faults              (§4.2's named falsifier)
   ├─► Task 3  candidate_log.py -> services/db.py          (§4.3 candidate 2,
   │           delivered as a side effect of migrating)     delivered via §1
   ├─► Task 4  widen resolve_from_market_results()'s        (§4.3 candidate 1)
   │           flush_now() busy-timeout budget
   │
   ├─► Task 5  series_watcher.py -> services/db.py          (§1.5 step 2,
   ├─► Task 6  observability/observability.py -> services/db.py  fd-census
   │           (candidate_log.py above is the third)         priority 3)
   │
   ├─► Task 7  tracking issue: remaining 22 modules'         (§1.5 step 3,
   │           opportunistic migration                       "not a dedicated
   │           (Tier 0's 5 explicitly excluded, see above)    sweep PR")
   │
   ├─► Task 8  §2 aiosqlite: record sequencing decision +    (§2.2, explicitly
   │           candidate call sites, no code                  not benchmarked
   │           (decision_bridge.py explicitly excluded)       by the design)
   │
   └─► Task 9  §3 raw_trades: trace every writer of           (§3.5's open
               excluded/resolved_side + PRAGMA                question, named
               integrity_check precondition, no export code   "for the plan
                                                                stage")

Task 10  Full regression suite + live validation (fd census for Tasks 1/5/6,
         candidate_log.db occurrence rate + falsifier attribution for
         Tasks 2-4, §9's success criteria)

§5 (small-file consolidation): no task — declined per the design, not
   re-opened here.
```

This mirrors the design's own §6 sequencing diagram directly: §1 (Task 1)
lands first as the mechanical, low-risk prerequisite; §4's fix (Tasks 2–4) is
named there as "a natural early adopter of §1's module once it lands, not a
reason to delay this fix until §1 is fully rolled out everywhere" — so it
follows Task 1 immediately, ahead of the other two priority §1 migrations,
because it addresses a fault that is live and currently firing (§4.1: 181
occurrences and counting, ≈2.14/hour) while Tasks 5–6 address a *measured but
not currently alarming* fd-accumulation contribution. §2 and §3 are each
marked independent of §1/§2 structurally in the design and are placed last
because neither has any code to land in this plan — each produces a decision
record, not a diff, so ordering them relative to the code tasks carries no
real dependency; they are placed after the code work only so a reader
executing this plan top-to-bottom sees the mechanical wins land first.

**Tech Stack:** Python 3 stdlib only (`contextlib`, `sqlite3`) for Tasks
1–7,10; no new runtime dependency, matching the design's own §1.3 reasoning
(SQLAlchemy Core was benchmarked and explicitly declined). Tasks 8–9 are
investigation/decision-recording tasks — `grep`/`git log`/direct source
reads plus `gh issue create` (already this repo's standing convention, see
`CLAUDE.md`'s "Branching, CI, sessions" and `tools/kanban_sync`), no new
tooling.

## Global Constraints

- **No change to any trading, risk, sizing, calibration, strategy,
  settlement, or auth code.** This plan's blast radius, stated explicitly
  per module:
  - `services/db.py` (new) — a generic connect-and-close helper with no
    caller-specific behavior; nothing calls it until Tasks 3/5/6 wire a
    caller in.
  - `services/candidate_log.py` — Tasks 3–4 touch `_connect()` (lines
    76–145) and `resolve_from_market_results()`'s two `flush_now()` calls
    (lines 236–237) only. `candidate_log.py` is read by
    `services/whale_stream/decision_bridge.py` (trading-critical, per the
    design's §2.1) but **decision_bridge.py itself is never touched by this
    plan** — it calls `candidate_ledger.claim()`/`record_decision()`
    (`services/candidate_ledger.py`, a *different* module with its own,
    separate `_connect()` that this plan does not migrate — see the note
    below), not anything in `candidate_log.py`. Verified directly:
    `grep -rn "candidate_log" services/whale_stream/decision_bridge.py` —
    zero hits. `candidate_log.py`'s own callers, named precisely after
    independent adversarial re-verification (an earlier version of this
    line cited a non-matching glob, `services/whale_stream/*_handlers.py`):
    its *rejection recording* path is called from `services/strategy_engine.py`,
    `services/kalshi/websocket.py`, `services/whalewatchers/kalshi_trade_tape.py`,
    and `services/whale_stream/whale_stream_handlers.py` (a candidate that
    did **not** trade); its `resolve_from_market_results()` is called from
    both `main.py`'s tick loop **and** `services/settlement_resolver.py`'s
    separately-supervised settlement-polling loop (see Task 4's own note) —
    none of these is the order-placement path.
  - `services/capture_writer.py` — Tasks 2 and 4 touch `_flush_store()`
    (adds a `caller` parameter, Task 2) and `flush_now()` (adds a
    `busy_timeout_ms` override parameter, Task 4). `capture_writer.submit()`
    (the hot-path enqueue function every whale-signal-adjacent caller uses)
    is untouched by both tasks — verified: neither task's diff includes
    `submit()`'s line range.
  - `services/series_watcher.py`, `services/observability/observability.py`
    — Tasks 5–6 touch each module's `_connect()` only, call-site-transparent
    (every caller already uses `with _connect() as conn:` — verified via
    `grep -n "_connect(" <file>` against current source for both files
    before drafting each task, zero bare/non-`with` usages found).
  - **`services/candidate_ledger.py` and `services/tick_executor.py` are
    never touched by this plan.** `candidate_ledger.py` is one of the 25
    modules in this plan's nominal §1 scope (it has its own `_connect()`,
    per the design's §1.1 list) but is deliberately left for Task 7's
    opportunistic-migration tracking, not migrated here — the design's own
    §1.5 step 1 names it, alongside `risk_manager.py`/`paper_broker.py`, as
    "several safety-adjacent" modules that get one-PR-per-module treatment,
    never bundled into a batch; this plan does not open that PR. Migrating
    it is real, in-scope future work under Task 7's tracking issue, done in
    its own dedicated, reviewed PR when picked up — not silently deferred
    without a record.
- **No write to any `data/*.db` file's write path beyond the two `candidate_log.db`
  contention fixes (Tasks 2–4) and the closing-connection migrations
  (Tasks 3, 5–6), all of which preserve every existing table's schema and
  every existing caller's read/write contract unchanged.** No task in this
  plan deletes, moves, or truncates a live `data/*.db` file. Task 9's
  `PRAGMA integrity_check` is read-only by construction.
- **No SQLAlchemy Core, no DuckDB, no aiosqlite call-site migration ships in
  this plan.** Tasks 8–9 produce decision records and open-question
  resolutions only, per the design's own explicit deferral of both to "their
  own separate plan[s]" (§6). Implementing either is a **future plan's**
  scope, gated on Task 8/9's findings, not assumed to follow automatically.
- **Every busy-timeout number this plan sets is either the existing default
  (Task 3: `services/db.py`'s `busy_timeout_ms=5000`, i.e. no behavior
  change, only making today's implicit Python `sqlite3.connect(...,
  timeout=5.0)` default explicit and policy-driven) or an existing,
  already-proven value reused rather than a new invented number** (Task 4:
  `capture_writer._DAEMON_BUSY_TIMEOUT_MS`'s 1000ms, already running in
  production on the daemon thread's own flushes, reused for the tick-driven
  caller rather than picking a new figure) — per the data-plane HARD RULE's
  "never change ... retry budget ... because it 'should help': identify the
  measured bottleneck and mechanism first." Task 10's live validation is
  where both get checked against real behavior, not assumed correct on
  landing.
- **`contextlib.closing` is not used**, for the same reason Tier 0's plan
  declined it: `_connect()` itself becomes the closing context manager, so
  no call site's syntax changes. Consistent with this plan's own migrated
  modules and with Tier 0's precedent
  (`docs/superpowers/plans/2026-09-03-tier0-live-incident-remediation.md`'s
  Global Constraints).
- **This repo has no `pytest-asyncio`.** Not directly relevant to this
  plan's tasks (none add new `async def` functions), noted only because
  Task 8's tracking record for item 16 must carry this forward to whichever
  future plan actually migrates a call site.
- **Tier 0 dependency, stated precisely:** this plan's modules are
  file-disjoint from Tier 0's five and do not require Tier 0's code to have
  landed. If Tier 0 lands first, its five modules become candidates for
  Task 7's opportunistic-migration tracking (per the design's §1.5 step 4);
  if this plan lands first, nothing here needs to change when Tier 0
  eventually lands, since neither touches the other's files.
- **Open item this plan does not resolve, flagged rather than silently
  decided:** the design's §7 names a `PRAGMA integrity_check` on
  `series_watcher.db` as a precondition "before any refresh job is first
  scheduled," given `market_history.db`'s own recent, confirmed corruption
  (`docs/next-action.md`, fixed via `.recover`, root cause still
  unconfirmed). Task 9 runs this check as part of resolving the `excluded`/
  `resolved_side` open question, but a *positive* corruption finding on
  `series_watcher.db` — a live, 29.6 GB, actively-written file `raw_trades`
  and `book_snapshots` both live in — is explicitly **out of this plan's
  authority to remediate**: per `CLAUDE.md`'s safety invariants, a repair
  decision on live data is a human call (`docs/open-decisions.md`), not
  something Task 9 or Task 10 does automatically, mirroring exactly how
  Tier 0's own Task 8 handled `market_history.db`'s corruption finding.

---

### Task 1: Build `services/db.py` — the shared closing-connection module

**Files:**
- Create: `services/db.py`
- Test: `tests/test_db.py` (new)

**Interfaces:**
- Produces: `db.connect(db_path: Path, *, tables: tuple[str, ...] = (),
  busy_timeout_ms: int = 5000)` — a `@contextlib.contextmanager` that opens,
  sets `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout = <busy_timeout_ms>`,
  runs each named table's registered DDL idempotently, yields the connection
  inside its own `with conn:` (commit/rollback) block, and **always** closes
  in a `finally`. `db.register_ddl(table: str, ddl: str) -> None` — called
  once at import time by each table-owning module. `db.add_column_if_missing(
  conn, table, column, coltype)` — the shared replacement for the 11
  AST-identical copies the audit's §9.2 found (takes `conn` as a parameter,
  no shared state, safe to call from any module regardless of whether that
  module has migrated to `db.connect()` yet).
- Consumes: nothing — this task lands with zero callers. Tasks 3/5/6 are the
  first callers.

**Why this is safe to land on its own:** no existing module imports
`services/db.py` yet (it doesn't exist until this task), so this task changes
zero runtime behavior for the running app. `services/db.py` defines no
`DB_PATH` of its own — confirmed by design (`tools/quality_audit/persistence.py`'s
`PERSISTENCE_MODULE_PATHS` scanner keys off a module-level `DB_PATH =`
assignment; `db.connect()` takes `db_path` as a parameter, so this file is
invisible to that scanner and does not need registering there).

- [ ] **Step 1: Write the failing tests first**

Add to `tests/test_db.py`:

```python
import sqlite3
import contextlib

import pytest

from services import db


def _fresh_registry(monkeypatch):
    """Each test gets its own DDL registry - db.py's module-level
    _DDL_REGISTRY is otherwise shared mutable state across tests."""
    monkeypatch.setattr(db, "_DDL_REGISTRY", {})


def test_connect_closes_on_normal_exit(tmp_path, monkeypatch):
    _fresh_registry(monkeypatch)
    closed = []
    real_connect = sqlite3.connect

    def _tracking_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        real_close = conn.close
        conn.close = lambda: (closed.append(True), real_close())[-1]
        return conn

    monkeypatch.setattr(db.sqlite3, "connect", _tracking_connect)
    with db.connect(tmp_path / "t.db") as conn:
        conn.execute("SELECT 1")
    assert closed == [True]


def test_connect_closes_even_on_exception(tmp_path, monkeypatch):
    _fresh_registry(monkeypatch)
    closed = []
    real_connect = sqlite3.connect

    def _tracking_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        real_close = conn.close
        conn.close = lambda: (closed.append(True), real_close())[-1]
        return conn

    monkeypatch.setattr(db.sqlite3, "connect", _tracking_connect)
    with pytest.raises(ValueError):
        with db.connect(tmp_path / "t.db") as conn:
            raise ValueError("caller-side failure")
    assert closed == [True]


def test_ddl_registered_table_created_and_idempotent(tmp_path, monkeypatch):
    _fresh_registry(monkeypatch)
    db.register_ddl("widgets", "CREATE TABLE IF NOT EXISTS widgets (id INTEGER PRIMARY KEY)")
    db_path = tmp_path / "t.db"
    with db.connect(db_path, tables=("widgets",)) as conn:
        conn.execute("INSERT INTO widgets DEFAULT VALUES")
    # Second connect() with the same table re-runs the DDL - must not error
    # or wipe the row (CREATE TABLE IF NOT EXISTS is naturally idempotent;
    # this test pins that db.connect() doesn't do anything to break that).
    with db.connect(db_path, tables=("widgets",)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM widgets").fetchone()[0] == 1


def test_no_tables_arg_runs_no_ddl(tmp_path, monkeypatch):
    _fresh_registry(monkeypatch)
    db.register_ddl("widgets", "CREATE TABLE IF NOT EXISTS widgets (id INTEGER PRIMARY KEY)")
    db_path = tmp_path / "t.db"
    with db.connect(db_path) as conn:  # tables=() default - no DDL run
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("SELECT * FROM widgets")


def test_wal_and_busy_timeout_pragmas_applied(tmp_path, monkeypatch):
    _fresh_registry(monkeypatch)
    with db.connect(tmp_path / "t.db", busy_timeout_ms=1234) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 1234


def test_add_column_if_missing_adds_once(tmp_path, monkeypatch):
    _fresh_registry(monkeypatch)
    db.register_ddl("t", "CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY)")
    with db.connect(tmp_path / "t.db", tables=("t",)) as conn:
        db.add_column_if_missing(conn, "t", "extra", "REAL")
        db.add_column_if_missing(conn, "t", "extra", "REAL")  # no error second time
        cols = {row[1] for row in conn.execute("PRAGMA table_info(t)")}
        assert "extra" in cols
```

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/agent-ae61311892d35be18 && python -m pytest tests/test_db.py -q"`
Expected: **collection error** (`services.db` does not exist yet) — this is
the pre-implementation failure this task closes.

- [ ] **Step 2: Implement `services/db.py`**

Exactly the design's §1.4 API shape (already reviewed and GO'd — transcribed
here verbatim, not re-derived):

```python
"""Shared, closing SQLite connection helper - the single fix for the
30-module (25 in this plan's scope; Tier 0 already covers the other 5)
"opens a connection, never calls .close()" leak shape that produced a real
6.8-hour fd-exhaustion incident (2026-09-02). Design:
docs/superpowers/specs/2026-09-03-persistence-layer-redesign-design.md §1.

Every existing `with _connect() as conn:` call site keeps working unchanged
once its owning module's _connect() is rewritten to return db.connect(...)
- this yields the same conn as before, but now closes it on exit. See that
module's own migration task for the exact before/after diff."""
import contextlib
import sqlite3
from pathlib import Path

_DDL_REGISTRY: dict[str, str] = {}


def register_ddl(table: str, ddl: str) -> None:
    """Called once, at import time, by each table's owning module."""
    _DDL_REGISTRY[table] = ddl


@contextlib.contextmanager
def connect(db_path: Path, *, tables: tuple[str, ...] = (), busy_timeout_ms: int = 5000):
    """Every existing `with _connect() as conn:` / `with _connect(DB_PATH) as
    conn:` call site's replacement. Opens, sets WAL + the given busy_timeout
    as one policy, runs each named table's registered DDL idempotently,
    yields, and ALWAYS closes - this is the entire fix. tables=() (the
    default) runs no DDL, for read-only/diagnostic callers whose owning
    module has already guaranteed the schema exists."""
    db_path.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=busy_timeout_ms / 1000)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
        for table in tables:
            conn.execute(_DDL_REGISTRY[table])
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

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/agent-ae61311892d35be18 && python -m pytest tests/test_db.py -q"`
Expected: all 6 tests pass.

- [ ] **Step 4: `import main` sanity check**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/agent-ae61311892d35be18 && python -c 'import services.db' && echo IMPORT_OK"`
Expected: `IMPORT_OK` — the new module imports cleanly in isolation (nothing
imports it into `main.py`'s graph yet).

---

### Task 2: `candidate_log.db` contention falsifier — per-caller attribution on `flush_retained_on_lock` faults

**Files:**
- Modify: `services/capture_writer.py:348` (`_flush_store` signature),
  `:404-407` (the `fault_log.record` call inside the lock-error branch),
  `:416-426` (`flush_now`), `:429-449` (`_run`)
- Test: `tests/test_capture_writer.py` (extend existing file)

**Why this task exists, and why it must land before Tasks 3–4, not after:**
the design's own §4.2 names this exact falsifier as "cheap, not run here...
the next dozen occurrences settle the question definitively" — whether
`flush_retained_on_lock` occurrences come from the daemon thread's own
periodic flush colliding with itself, or from the tick-driven
`resolve_from_market_results()` caller losing the race against the daemon
(the design's leading hypothesis, explicitly labeled "established from
source... not a captured thread-interleaving trace"). Landing this first
means Tasks 3–4's fix gets measured against a real per-caller baseline
instead of an aggregate count that can't distinguish "the fix worked" from
"the mechanism was never what we thought."

**A correction to the design's own suggested implementation, found by reading
`services/fault_log.py:125-144` directly before writing this task (never-guess
HARD RULE):** the design's text says "one extra string in the existing
`fault_log.record(...)` call" — literally adding the caller name to the
`context=` argument. **This would not work.** `fault_log._write()`'s
`INSERT ... ON CONFLICT (component, operation, exc_type, message) DO UPDATE
SET count = count + 1, last_seen = excluded.last_seen` — `context` is *not*
part of the `UNIQUE` key and is *not* touched by the `DO UPDATE SET` clause.
Once the first `flush_retained_on_lock` row for a given `(component,
operation, exc_type, message)` combination exists, every subsequent
occurrence — from either caller — only bumps `count`/`last_seen`; the row's
`context` stays frozen at whatever the *first* occurrence happened to record.
Putting the caller name in `context` would silently fail to distinguish
"the next dozen occurrences" at all, defeating the falsifier's entire
purpose while looking like it worked (the field would be populated, just not
usefully). **Fix: fold the caller identity into `operation` instead**, which
*is* part of the `UNIQUE` key — `flush_retained_on_lock_daemon` vs.
`flush_retained_on_lock_flush_now` become two genuinely separate aggregated
rows, each with its own accurate `count`/`first_seen`/`last_seen`. Still "one
extra string," just in the field that actually participates in
deduplication.

**Interfaces:**
- `_flush_store(store: str, busy_timeout_ms: int = _CALLER_BUSY_TIMEOUT_MS,
  *, caller: str = "daemon")` — new keyword-only `caller` parameter,
  defaulting to `"daemon"` (today's only *implicit* caller identity, from
  `_run()`'s periodic loop) so every call site that doesn't pass it
  explicitly keeps today's exact behavior.
- `flush_now(store: str, *, busy_timeout_ms: int | None = None) -> dict` —
  passes `caller="flush_now"` to `_flush_store` internally. The
  `busy_timeout_ms` override parameter (default `None` = today's
  `_CALLER_BUSY_TIMEOUT_MS`) is added here rather than in Task 4, since both
  tasks touch this same function signature — adding it once, now, avoids a
  second signature change in Task 4. Every existing call site calls
  `flush_now(store)` positionally with no keyword arg, so this is purely
  additive — verified via `grep -rn "flush_now(" services/ main.py tests/`
  before drafting this task (**34** call sites total — corrected after
  independent adversarial re-count found the originally-cited "28" wrong;
  the substantive safety claim, zero call sites pass a second positional
  argument, was independently re-verified and holds regardless of the exact
  count: `services/candidate_log.py` 10, `tests/test_candidate_log.py` 2,
  `tests/test_capture_writer.py` 19, `tests/test_main_tick_executor_wiring.py`
  1, `tests/test_series_watcher.py` 1, `tests/test_whale_candidate_lifecycle.py`
  1).
- `fault_log.record(...)`'s own signature is untouched — only the string
  passed as `operation` changes, from a caller inside `_flush_store`'s except
  block.

- [ ] **Step 1: Write the failing test first**

Add to `tests/test_capture_writer.py` (check its existing fixture first —
`grep -n "^def _\|_STORE_PATHS\|_is_lock_error\|_hold_write_lock" tests/test_capture_writer.py`
— this file has **no module-level `cw`/`capture_writer` alias**; every
existing test does its own local `from services import capture_writer`
inside the function body, so all three new tests below follow that same
convention rather than introducing a new one):

```python
def test_flush_retained_on_lock_records_caller_in_operation_name(monkeypatch, tmp_path):
    """Pins the fix for the design's named falsifier
    (docs/superpowers/specs/2026-09-03-persistence-layer-redesign-design.md
    §4.2): flush_retained_on_lock's fault_log row must distinguish which
    caller (daemon vs. flush_now) hit the lock, via `operation`, not
    `context` - fault_log._write()'s ON CONFLICT clause never updates
    `context` on a repeat occurrence, only `operation` is part of the
    UNIQUE key that actually creates separate rows. Uses this file's own
    _hold_write_lock()/_release() pattern (matching
    test_lock_collision_retains_the_batch_instead_of_dropping_it and its
    three neighbors below it in this file) rather than a manually-raised
    sqlite3.OperationalError: a manually-constructed OperationalError has
    no sqlite_errorcode attribute (only the real sqlite3 C extension sets
    it when it raises the error itself), and _is_lock_error() gates
    exclusively on that attribute - a mock that doesn't set it would
    silently take the generic-exception branch instead of the
    flush_retained_on_lock branch this test exists to exercise
    (adversarial review Finding F6)."""
    from services import capture_writer

    db_path = tmp_path / "candidate_log.db"
    monkeypatch.setattr(capture_writer, "_STORE_PATHS", {"rejected_candidates": db_path})
    monkeypatch.setattr(capture_writer, "_buffers", {"rejected_candidates": [("t", "s", "g")]})
    _fresh_counters(monkeypatch, capture_writer, ["rejected_candidates"])
    capture_writer.flush_now("rejected_candidates")  # creates the file and the table
    monkeypatch.setattr(capture_writer, "_buffers", {"rejected_candidates": [("t", "s", "g")]})

    recorded = []
    monkeypatch.setattr(
        capture_writer.fault_log, "record",
        lambda component, operation, exc, **kw: recorded.append(operation),
    )

    holder = _hold_write_lock(db_path)
    try:
        capture_writer._flush_store("rejected_candidates", caller="flush_now")
        # a failed flush retains (does not clear) the buffer - confirmed by
        # test_lock_collision_retains_the_batch_instead_of_dropping_it above -
        # so the same buffered rows are still there for the second call.
        capture_writer._flush_store("rejected_candidates", caller="daemon")
    finally:
        _release(holder)

    assert recorded == [
        "flush_retained_on_lock_flush_now",
        "flush_retained_on_lock_daemon",
    ]


def test_flush_now_passes_flush_now_as_caller(monkeypatch, tmp_path):
    from services import capture_writer

    seen = {}
    monkeypatch.setattr(
        capture_writer, "_flush_store",
        lambda store, busy_timeout_ms=capture_writer._CALLER_BUSY_TIMEOUT_MS, *, caller="daemon": seen.update(
            store=store, busy_timeout_ms=busy_timeout_ms, caller=caller,
        ),
    )
    monkeypatch.setattr(capture_writer, "_buffers", {"rejected_candidates": []})
    capture_writer.flush_now("rejected_candidates")
    assert seen["caller"] == "flush_now"
    assert seen["busy_timeout_ms"] == capture_writer._CALLER_BUSY_TIMEOUT_MS  # unchanged default


def test_flush_now_accepts_busy_timeout_override(monkeypatch, tmp_path):
    from services import capture_writer

    seen = {}
    monkeypatch.setattr(
        capture_writer, "_flush_store",
        lambda store, busy_timeout_ms=capture_writer._CALLER_BUSY_TIMEOUT_MS, *, caller="daemon": seen.update(
            busy_timeout_ms=busy_timeout_ms,
        ),
    )
    monkeypatch.setattr(capture_writer, "_buffers", {"rejected_candidates": []})
    capture_writer.flush_now("rejected_candidates", busy_timeout_ms=999)
    assert seen["busy_timeout_ms"] == 999
```

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/agent-ae61311892d35be18 && python -m pytest tests/test_capture_writer.py -k 'caller or busy_timeout_override' -q"`
Expected: **fails** (`_flush_store()` has no `caller` parameter, `flush_now()`
has no `busy_timeout_ms` parameter) against current `main`.

- [ ] **Step 2: Read the exact current code before editing**

Run: `sed -n '348,459p' services/capture_writer.py`. Confirm it still matches
this task's own "before" description above before editing — if not, stop and
re-derive the diff from current source.

- [ ] **Step 3: Apply the fix**

In `_flush_store`, change the signature and the lock-error branch:

```python
def _flush_store(store: str, busy_timeout_ms: int = _CALLER_BUSY_TIMEOUT_MS, *, caller: str = "daemon") -> None:
```

and inside the `except Exception as exc:` block's `if _is_lock_error(exc):`
branch, change:

```python
            fault_log.record(
                "capture_writer", "flush_retained_on_lock", exc, severity="warn",
                context=f"{store}: {len(rows)} row(s) retained, {overflow} overflow-dropped",
            )
```

to:

```python
            fault_log.record(
                "capture_writer", f"flush_retained_on_lock_{caller}", exc, severity="warn",
                context=f"{store}: {len(rows)} row(s) retained, {overflow} overflow-dropped",
            )
```

In `flush_now`:

```python
def flush_now(store: str, *, busy_timeout_ms: int | None = None) -> dict:
    n = len(_buffers.get(store, []))
    _flush_store(
        store,
        busy_timeout_ms=busy_timeout_ms if busy_timeout_ms is not None else _CALLER_BUSY_TIMEOUT_MS,
        caller="flush_now",
    )
    return {"flushed": n}
```

In `_run`, both `_flush_store(...)` calls already pass `caller`'s intended
default (`"daemon"`) implicitly — add it explicitly rather than relying on
the default, so a future reader doesn't have to trace the default value to
know this is the daemon path:

```python
                budget = _CALLER_BUSY_TIMEOUT_MS if _stop_event.is_set() else _DAEMON_BUSY_TIMEOUT_MS
                _flush_store(store, busy_timeout_ms=budget, caller="daemon")
    ...
    for store in _buffers:
        _flush_store(store, busy_timeout_ms=_CALLER_BUSY_TIMEOUT_MS, caller="daemon")
```

- [ ] **Step 4: Confirm the test passes and nothing else regressed**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/agent-ae61311892d35be18 && python -m pytest tests/test_capture_writer.py tests/test_candidate_log.py -q"`
Expected: every test passes, including the three new ones. `test_candidate_log.py`
is included because it exercises `flush_now` indirectly through
`resolve_from_market_results()` and the other 4 call sites — confirms the
additive signature change doesn't break any existing caller.

---

### Task 3: `candidate_log.py`'s `_connect()` migrates to `services/db.py` (delivers §4.3 fix candidate 2)

**Files:**
- Modify: `services/candidate_log.py:76-145` (`_connect`, plus the module's
  import block)
- Test: `tests/test_candidate_log.py` (extend existing file)

**Depends on:** Task 1 (`services/db.py` must exist).

**Interfaces:** `_connect()` changes from a plain function returning
`sqlite3.Connection` to a thin wrapper around `db.connect(...)` — every
existing `with _connect() as conn:` call site (lines 239, 287, 387, 439,
461, 481, confirmed via `grep -n "_connect(" services/candidate_log.py`
immediately before drafting this task: all six are `with _connect() as
conn:`, zero bare usages) keeps working unmodified.

**A correction to the design's own §1.4 illustrative "After" example, found
by reading `candidate_log.py:76-145` directly (never-guess HARD RULE):** the
design's simplified example shows `_connect()` becoming a literal one-line
`return db.connect(DB_PATH, tables=(...))`. The real function does more than
two bare `CREATE TABLE` statements — it also creates two indexes
(`idx_rejection_events_gate`, `idx_rejection_events_unresolved`) and calls
`_add_column_if_missing` twice (the `unit_cost` column on both tables). None
of that is expressible inside `db.register_ddl`'s single-DDL-string-per-table
model as specified. **This is not a re-litigation of §1.4's API** (the
registry's shape stays exactly as designed) — it's this task correctly
extending the module's own thin wrapper to do the extra, non-table-DDL setup
work itself, on the connection `db.connect()` yields, before yielding it
onward. This is why `_connect()` becomes a small generator (a handful of
lines), not literally the one-liner the design's illustrative example showed
— consistent with §1.4's actual closing sentence ("a one-line function, not a
full rewrite" — the operative constraint is "not a full rewrite," which this
still is).

**Also delivers §4.3 fix candidate 2** ("set an explicit `busy_timeout`
pragma in `candidate_log.py`'s own `_connect()`") as a side effect of
adopting `db.connect()`'s default `busy_timeout_ms=5000` — **this is not a
numeric change**: Python's `sqlite3.connect(db_path)` (today's implicit call,
with no `timeout=` kwarg) already defaults to a 5.0-second busy wait: this
task makes that value explicit and policy-driven (an actual `PRAGMA
busy_timeout` statement, consistent with every other module that migrates to
`services/db.py`) rather than changing what candidate_log.py's own
write-transaction patience actually is. Zero new numeric-tuning risk.

- [ ] **Step 1: Write the failing test first**

Add to `tests/test_candidate_log.py` (reuse the existing `_redirect_db`
fixture — `grep -n "_redirect_db" tests/test_candidate_log.py` — for
`DB_PATH` redirection):

```python
def test_connect_closes_its_connection(tmp_path, monkeypatch, _redirect_db):
    closed = []
    real_connect = sqlite3.connect

    def _tracking_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        real_close = conn.close
        conn.close = lambda: (closed.append(True), real_close())[-1]
        return conn

    monkeypatch.setattr(cl.db.sqlite3, "connect", _tracking_connect)
    with cl._connect() as conn:
        conn.execute("SELECT 1")
    assert closed == [True]


def test_connect_still_creates_both_tables_indexes_and_unit_cost_columns(tmp_path, monkeypatch, _redirect_db):
    """Guards against the design's own simplified illustrative example
    (a bare two-CREATE-TABLE _connect()) being copied verbatim and silently
    dropping the two CREATE INDEX statements and the two
    add_column_if_missing calls the real module needs."""
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

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/agent-ae61311892d35be18 && python -m pytest tests/test_candidate_log.py -k 'closes_its_connection or unit_cost_columns or busy_timeout_pragma' -q"`
Expected: **fails** (`closed == []`; `cl.db` doesn't exist yet as an
attribute of the `candidate_log` module) against current `main`.

- [ ] **Step 2: Read the exact current function before editing**

Run: `sed -n '60,146p' services/candidate_log.py`. Confirm it still matches
this task's own "before" quotation above (already transcribed in full
earlier in this plan's own investigation) before editing.

- [ ] **Step 3: Apply the fix**

Add `from services import db` to the import block (`services/candidate_log.py:62`,
alongside the existing `from services import capture_writer`). Register the
DDL once, at module scope, and rewrite `_connect()`:

```python
from services import capture_writer, db

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "candidate_log.db"

db.register_ddl(
    "rejected_candidates",
    """
    CREATE TABLE IF NOT EXISTS rejected_candidates (
        ticker TEXT NOT NULL,
        strategy TEXT NOT NULL,
        gate_name TEXT NOT NULL,
        observed_value REAL,
        threshold_value REAL,
        side TEXT,
        rejected_at REAL NOT NULL,
        resolved INTEGER NOT NULL DEFAULT 0,
        result TEXT,
        resolved_at REAL,
        PRIMARY KEY (ticker, strategy, gate_name)
    )
    """,
)
db.register_ddl(
    "rejection_events",
    """
    CREATE TABLE IF NOT EXISTS rejection_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT NOT NULL,
        strategy TEXT NOT NULL,
        gate_name TEXT NOT NULL,
        observed_value REAL,
        threshold_value REAL,
        side TEXT,
        rejected_at REAL NOT NULL,
        resolved INTEGER NOT NULL DEFAULT 0,
        result TEXT,
        resolved_at REAL
    )
    """,
)


@contextlib.contextmanager
def _connect():
    """Every existing `with _connect() as conn:` call site keeps working
    unchanged - now backed by services/db.py's closing connect() (2026-09-03,
    Task 3 of docs/superpowers/plans/2026-09-03-persistence-layer-implementation.md).
    Also sets an explicit busy_timeout pragma (db.connect()'s 5000ms default -
    unchanged from Python's own prior implicit default, now stated rather than
    silent), the fix this plan's design named as §4.3 candidate 2. The two
    CREATE INDEX statements and two add_column_if_missing calls below are not
    expressible in db.register_ddl's single-statement-per-table model, so this
    wrapper still runs them itself on the yielded connection - see this task's
    own note on why this isn't a literal one-line wrapper."""
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

Remove the now-unused `_add_column_if_missing` module-local function
(`candidate_log.py:67-73`) — replaced by `db.add_column_if_missing`; confirm
via `grep -n "_add_column_if_missing" services/candidate_log.py` that nothing
else in the module still calls the local copy before deleting it. Add
`import contextlib` to the import block if not already present (`grep -n
"^import contextlib" services/candidate_log.py` first).

- [ ] **Step 4: Confirm the test passes and nothing else regressed**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/agent-ae61311892d35be18 && python -m pytest tests/test_candidate_log.py -q"`
Expected: every test in the file passes, including the three new ones —
`test_candidate_log.py` already has ~25 existing tests exercising
`record_rejection`, `resolve_from_market_results`, `gate_summary`, etc.,
all of which route through `_connect()`; a clean pass here is direct
evidence the migration is call-site-transparent.

---

### Task 4: Widen `resolve_from_market_results()`'s `flush_now()` busy-timeout budget (§4.3 fix candidate 1)

**Files:**
- Modify: `services/candidate_log.py:236-237` only (the two `flush_now()`
  calls inside `resolve_from_market_results()`)
- Test: `tests/test_candidate_log.py` (extend existing file)

**Depends on:** Task 2 (`flush_now()`'s `busy_timeout_ms` override parameter
must exist first).

**Blast radius, stated precisely:** `candidate_log.py` has **six** call sites
of `capture_writer.flush_now()` (lines 236-237, 286, 386, 437-438, 458-459,
478-479 — confirmed via `grep -n "flush_now(" services/candidate_log.py`).
Only the two inside `resolve_from_market_results()` (lines 236-237) are
touched by this task. The other **five** functions (`gate_summary`,
`population_gate_summary`, `clear_all`, `count_range`, `clear_range`) are
route-facing/admin callers, not the tick-driven caller the design's §4.2
mechanism analysis is about — their `flush_now()` calls keep the current
50ms (`_CALLER_BUSY_TIMEOUT_MS`) budget the design says that default was
"tuned for" (a UI/route-facing caller), unchanged by this task.

**Interfaces:** No signature change in this task (Task 2 already added
`flush_now`'s `busy_timeout_ms` parameter) — only the two call sites' actual
arguments change.

**Why this number, not a new invented one:** `_TICK_RESOLVE_FLUSH_BUSY_TIMEOUT_MS
= 1000` reuses `capture_writer._DAEMON_BUSY_TIMEOUT_MS`'s existing value
directly, rather than introducing a new magic number — the daemon thread's
own periodic flush already waits up to 1000ms for this exact file under real
production load today; `resolve_from_market_results()` runs from **two
independent callers**, corrected here after independent adversarial
re-verification found this task's original framing incomplete: `main.py`'s
main tick loop (once per ~30-second tick, `_tick_interval_sec()`'s
streaming-mode branch) **and** `services/settlement_resolver.py`'s
separately-supervised `_settlement_resolver_loop()`, polling every
`_SCHEDULER_TRIGGER_INTERVAL_SEC` = 5.0 seconds whenever a settlement is
pending — both dispatched through the same `tick_executor` 2-worker thread
pool, so they can genuinely run concurrently on different threads, not just
at staggered wall-clock moments. `settlement_resolver.py`'s own docstring
notes settlements "cascade at boundary times," so the 5-second-cadence
caller is likely bursty rather than steady. Neither caller is a
request-latency-sensitive path, so 1000ms — the same patience the daemon's
own periodic flush already proves safe under real load — is generous
against either cadence; this correction changes the justification's
precision, not the chosen number. **Known limitation, stated explicitly:**
Task 2's `daemon`/`flush_now` operation-name taxonomy cannot distinguish a
main-tick-driven collision from a settlement-resolver-driven one — both
route through `capture_writer.flush_now()` identically and collapse into
`caller="flush_now"`. If Task 10's re-measurement still shows a non-trivial
`flush_retained_on_lock_flush_now` rate after this task lands, that
ambiguity — not a failure of this fix — is the reason further
instrumentation (attributing by call-site, not just by `flush_now` vs.
`daemon`) would be needed to see which of the two callers is still
colliding. Defined as a local constant in `candidate_log.py` rather than
importing `capture_writer`'s private `_DAEMON_BUSY_TIMEOUT_MS` across the
module boundary, to avoid a cross-module private-name dependency.

- [ ] **Step 1: Write the failing test first**

Add to `tests/test_candidate_log.py`:

```python
def test_resolve_from_market_results_flushes_with_widened_budget(monkeypatch, _redirect_db):
    seen = []
    monkeypatch.setattr(
        cl.capture_writer, "flush_now",
        lambda store, **kw: seen.append((store, kw.get("busy_timeout_ms"))),
    )
    cl.resolve_from_market_results({})
    assert seen == [
        ("rejected_candidates", cl._TICK_RESOLVE_FLUSH_BUSY_TIMEOUT_MS),
        ("rejection_events", cl._TICK_RESOLVE_FLUSH_BUSY_TIMEOUT_MS),
    ]
    assert cl._TICK_RESOLVE_FLUSH_BUSY_TIMEOUT_MS == cl.capture_writer._DAEMON_BUSY_TIMEOUT_MS


def test_other_flush_now_callers_keep_the_default_budget(monkeypatch, _redirect_db):
    """gate_summary and friends must NOT pick up the widened budget - only
    resolve_from_market_results's tick-driven calls do."""
    seen = []
    monkeypatch.setattr(
        cl.capture_writer, "flush_now",
        lambda store, **kw: seen.append(kw.get("busy_timeout_ms")),
    )
    cl.gate_summary()
    assert seen == [None]  # no override passed - flush_now's own default applies
```

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/agent-ae61311892d35be18 && python -m pytest tests/test_candidate_log.py -k 'widened_budget or keep_the_default' -q"`
Expected: **fails** (`cl._TICK_RESOLVE_FLUSH_BUSY_TIMEOUT_MS` doesn't exist;
current calls pass no `busy_timeout_ms` kwarg at all) against current `main`.

- [ ] **Step 2: Read the exact current call sites before editing**

Run: `sed -n '234,238p' services/candidate_log.py`. Confirm it still reads
`capture_writer.flush_now("rejected_candidates")` /
`capture_writer.flush_now("rejection_events")` with no keyword argument
before editing.

- [ ] **Step 3: Apply the fix**

Add near the top of `services/candidate_log.py`, after `DB_PATH`:

```python
# Reuses capture_writer's own daemon-flush patience (1000ms) rather than a
# new arbitrary number: resolve_from_market_results() runs from two
# independent callers - main.py's ~30s tick loop and
# settlement_resolver.py's separately-supervised 5s-poll-while-pending loop,
# both via tick_executor's thread pool - neither is a request-latency-
# sensitive path, so either can afford to wait as long as the daemon
# thread's own periodic flush already does, instead of racing it on the far
# shorter 50ms budget tuned for UI/route callers
# (capture_writer._CALLER_BUSY_TIMEOUT_MS). Design decision:
# docs/superpowers/specs/2026-09-03-persistence-layer-redesign-design.md §4.3
# candidate 1, implemented docs/superpowers/plans/2026-09-03-persistence-
# layer-implementation.md Task 4.
_TICK_RESOLVE_FLUSH_BUSY_TIMEOUT_MS = capture_writer._DAEMON_BUSY_TIMEOUT_MS
```

and in `resolve_from_market_results()`:

```python
    capture_writer.flush_now("rejected_candidates", busy_timeout_ms=_TICK_RESOLVE_FLUSH_BUSY_TIMEOUT_MS)
    capture_writer.flush_now("rejection_events", busy_timeout_ms=_TICK_RESOLVE_FLUSH_BUSY_TIMEOUT_MS)
```

(Deriving the constant from `capture_writer._DAEMON_BUSY_TIMEOUT_MS` at
import time, rather than hardcoding `1000`, means the two stay
mechanically in sync if `capture_writer`'s own daemon budget is ever
re-tuned — this is a read of the value, not a re-litigation of it, and
`capture_writer` is already imported by `candidate_log.py`.)

- [ ] **Step 4: Confirm the test passes and nothing else regressed**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/agent-ae61311892d35be18 && python -m pytest tests/test_candidate_log.py tests/test_capture_writer.py -q"`
Expected: every test passes, including the two new ones.

---

### Task 5: `series_watcher.py`'s `_connect()` migrates to `services/db.py`

**Files:**
- Modify: `services/series_watcher.py:151-207` (`_connect`, plus imports)
- Test: `tests/test_series_watcher.py` (extend existing file)

**Depends on:** Task 1.

**Why this module, now:** one of the three modules the second-pass audit's
fd census (§3.5, §4.1) named with a large measured live handle count — the
design's §1.5 step 2 names `series_watcher.py`/`candidate_log.py`/
`observability/observability.py` explicitly as the priority-first batch,
"since they're the ones with a demonstrated, measured leak contribution."
`candidate_log.py` is Task 3 above; this is the second of the three.

**Interfaces:** Identical shape to Task 3 — `_connect()` becomes a thin
`@contextlib.contextmanager` wrapper around `db.connect(DB_PATH, tables=
("raw_trades", "book_snapshots"))`, plus the four `CREATE INDEX` statements
(`idx_raw_trades_series`, `idx_raw_trades_ticker`, `idx_book_ticker`,
`idx_book_series`) run on the yielded connection, same reasoning as Task 3's
correction to the design's simplified example. Every existing `with
_connect() as conn:` call site (lines 458, 484 — confirmed via `grep -n
"_connect(" services/series_watcher.py`, both `with`-block usages, zero
bare) keeps working unmodified.

**Explicitly out of scope for this task:** `_ensure_schema_aio` (a *separate*,
already-existing async function a few lines below `_connect()`, used by
`_aio_db.connection_for()`'s `schema_init` hook for the read-only diagnostics
path) keeps its own independent copy of the `raw_trades`/`book_snapshots`
DDL, unmigrated. The design's §1.4 names deduplicating this exact kind of
DDL-triplication as one of the registry's engineering benefits, and having
`_ensure_schema_aio` read from `db._DDL_REGISTRY["raw_trades"]` instead of
its own copy is a real, reasonable future cleanup — but it touches the
`_aio_db`/aiosqlite path this plan's §2 (Task 8) explicitly declines to
change without its own dedicated review, so this task leaves it alone rather
than quietly expanding scope. Named here so it isn't lost.

- [ ] **Step 1: Write the failing test first**

Add to `tests/test_series_watcher.py` (check its existing DB-redirect
fixture first — `grep -n "^def _\|monkeypatch.setattr(sw" tests/test_series_watcher.py`):

```python
def test_connect_closes_its_connection(tmp_path, monkeypatch):
    monkeypatch.setattr(sw, "DB_PATH", tmp_path / "series_watcher.db")
    closed = []
    real_connect = sqlite3.connect

    def _tracking_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        real_close = conn.close
        conn.close = lambda: (closed.append(True), real_close())[-1]
        return conn

    monkeypatch.setattr(sw.db.sqlite3, "connect", _tracking_connect)
    with sw._connect() as conn:
        conn.execute("SELECT 1")
    assert closed == [True]


def test_connect_still_creates_all_tables_and_indexes(tmp_path, monkeypatch):
    monkeypatch.setattr(sw, "DB_PATH", tmp_path / "series_watcher.db")
    with sw._connect() as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"raw_trades", "book_snapshots"} <= tables
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert {
            "idx_raw_trades_series", "idx_raw_trades_ticker",
            "idx_book_ticker", "idx_book_series",
        } <= indexes
```

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/agent-ae61311892d35be18 && python -m pytest tests/test_series_watcher.py -k 'closes_its_connection or all_tables_and_indexes' -q"`
Expected: **fails** (`sw.db` doesn't exist as an attribute yet) against
current `main`.

- [ ] **Step 2: Read the exact current function before editing**

Run: `sed -n '145,207p' services/series_watcher.py`. Confirm it still matches
this plan's own earlier transcription (§ "Files"/"Interfaces" above, and this
plan's own investigation) before editing — if not, stop and re-derive.

- [ ] **Step 3: Apply the fix**

Add `from services import db` to the import block. Register both tables' DDL
at module scope (the exact `CREATE TABLE` bodies already transcribed above
in this plan's investigation — copy verbatim from current source, do not
retype from memory), then:

```python
@contextlib.contextmanager
def _connect():
    """Every existing `with _connect() as conn:` call site keeps working
    unchanged - now backed by services/db.py's closing connect() (2026-09-03,
    Task 5 of docs/superpowers/plans/2026-09-03-persistence-layer-implementation.md).
    This was one of the three modules the architecture audit's fd census
    named with a demonstrated, measured leak contribution.
    _ensure_schema_aio (below) deliberately keeps its own separate DDL copy -
    see this task's own scope note on why."""
    with db.connect(DB_PATH, tables=("raw_trades", "book_snapshots")) as conn:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_trades_series ON raw_trades (series, observed_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_trades_ticker ON raw_trades (ticker, observed_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_book_ticker ON book_snapshots (ticker, observed_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_book_series ON book_snapshots (series, observed_at)")
        yield conn
```

- [ ] **Step 4: Confirm the test passes and nothing else regressed**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/agent-ae61311892d35be18 && python -m pytest tests/test_series_watcher.py -q"`
Expected: every test passes, including the two new ones. Given
`series_watcher.db` is the live 29.6 GB file (§3.1), also run a targeted
smoke check against the real file structure (read-only): `docker exec
ddev-kalshi-whale-poc-fastapi sh -c "cd /app && python -c \"import sqlite3;
c = sqlite3.connect('file:data/series_watcher.db?mode=ro', uri=True);
print(c.execute('PRAGMA integrity_check(1)').fetchone())\""` — expected
`('ok',)`, confirming this task's own reasoning about the live file's schema
matches reality before Task 10's live validation.

---

### Task 6: `services/observability/observability.py`'s `_connect()` migrates to `services/db.py`

**Files:**
- Modify: `services/observability/observability.py:41-59` (`_connect`, plus
  imports)
- Test: `tests/test_observability.py` (extend existing file)

**Depends on:** Task 1.

**Why this module, now:** the third of the fd-census-named priority modules
(§1.5 step 2), and the smallest/simplest of the three — one table
(`metric_samples`), one index, no `add_column_if_missing` calls. Confirmed
via direct read (`services/observability/observability.py:41-59`) before
drafting this task.

**Interfaces:** Same shape as Tasks 3/5. Every existing `with _connect() as
conn:` call site (lines 66, 80, 89, 104, 124, 130 — confirmed via `grep -n
"_connect(" services/observability/observability.py`, all six `with`-block
usages) keeps working unmodified.

- [ ] **Step 1: Write the failing test first**

Add to `tests/test_observability.py` (this file already imports the module
under its full name at module level — `from services.observability import
observability`, line 33 — with **no `obs` alias anywhere in the file**;
both new tests below use `observability.` directly, matching that existing
import rather than introducing an alias no other test in this file uses):

```python
def test_connect_closes_its_connection(tmp_path, monkeypatch):
    monkeypatch.setattr(observability, "DB_PATH", tmp_path / "observability.db")
    closed = []
    real_connect = sqlite3.connect

    def _tracking_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        real_close = conn.close
        conn.close = lambda: (closed.append(True), real_close())[-1]
        return conn

    monkeypatch.setattr(observability.db.sqlite3, "connect", _tracking_connect)
    with observability._connect() as conn:
        conn.execute("SELECT 1")
    assert closed == [True]


def test_connect_still_creates_table_and_index(tmp_path, monkeypatch):
    monkeypatch.setattr(observability, "DB_PATH", tmp_path / "observability.db")
    with observability._connect() as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "metric_samples" in tables
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert "idx_metric_samples_metric_time" in indexes
```

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/agent-ae61311892d35be18 && python -m pytest tests/test_observability.py -k 'closes_its_connection or table_and_index' -q"`
Expected: **fails** against current `main`.

- [ ] **Step 2: Read the exact current function before editing**

Run: `sed -n '20,60p' services/observability/observability.py`. Confirm it
still matches this plan's own earlier transcription before editing.

- [ ] **Step 3: Apply the fix**

Add `db` to the existing `from services import (...)` block (already imports
`candidate_retry, capture_writer, fault_log, http_client, loop_watchdog,
strategy_engine, whale_pipeline_perf` — alphabetically, `db` goes before
`fault_log`). Register the DDL, then:

```python
db.register_ddl(
    "metric_samples",
    """
    CREATE TABLE IF NOT EXISTS metric_samples (
        observed_at REAL NOT NULL,
        metric TEXT NOT NULL,
        value REAL,
        labels_json TEXT NOT NULL DEFAULT '{}'
    )
    """,
)


@contextlib.contextmanager
def _connect():
    """Every existing `with _connect() as conn:` call site keeps working
    unchanged - now backed by services/db.py's closing connect() (2026-09-03,
    Task 6 of docs/superpowers/plans/2026-09-03-persistence-layer-implementation.md).
    Third of the three fd-census-priority modules (see series_watcher.py's
    and candidate_log.py's own migration tasks for the other two)."""
    with db.connect(DB_PATH, tables=("metric_samples",)) as conn:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_metric_samples_metric_time ON metric_samples(metric, observed_at)"
        )
        yield conn
```

Add `import contextlib` to the import block if not already present.

- [ ] **Step 4: Confirm the test passes and nothing else regressed**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/agent-ae61311892d35be18 && python -m pytest tests/test_observability.py -q"`
Expected: every test passes, including the two new ones.

---

### Task 7: Tracking issue for the remaining 22 modules' opportunistic §1 migration

**Not a code task** — per the design's own §1.5 step 3: the ~20 (22, per
this plan's exact recount below) remaining modules migrate "whenever a PR
already touches one of them for an unrelated reason... rather than a
dedicated sweep PR per module," with "a tracking issue (not a hard deadline)"
keeping the remaining count visible. This task creates that record, not the
migrations themselves.

**The exact list, re-derived from the design's §1.1 30-module list minus the
5 Tier 0 modules and the 3 this plan migrates directly (Tasks 3/5/6):**
`data_quarantine.py`, `index_feed/ingestion.py`,
`history/suggestion_decisions.py`, `shadow_mode.py`, `risk_manager.py`,
`trade_category.py`, `series_cache.py`, `series_evaluator.py`,
`alerting/alerting.py`, `game_state.py`, `accounts_store.py`,
`whale_calibration/calibration_history.py`, `settlement_edge.py`,
`candidate_ledger.py`, `backup/backup.py`, `market_events/event_schedule.py`,
`reset/reset_log.py`, `paper_broker.py`, `reset/trade_archive.py`,
`market_analyst_agent/_db.py`, `config/config_performance.py`,
`research/research.py` — **22 modules**, matching this plan's Global
Constraints section above (25 total in scope minus the 3 migrated directly).

**Why grouped as one tracking record rather than 22 individual tasks in this
plan:** the design is explicit that individual sweep PRs are the wrong
shape for these — no measured leak contribution singles any of them out the
way the fd census did for the priority 3, and forcing 22 near-identical
tasks into this document would not make any of them safer, only longer.
`risk_manager.py`, `paper_broker.py`, and `candidate_ledger.py` are called
out explicitly (repeating the design's own §1.5 step 1 language) as
safety-adjacent modules that must each get their own dedicated, individually
reviewed PR when picked up — never bundled with each other or with a
routine unrelated change, unlike the other 19. **Two more modules added to
this same caution list after independent adversarial review** (not as
central as the three above — neither gates nor executes a trade — but
measurably closer to the live trading path than the remaining ~17 in the
general opportunistic bucket): `series_evaluator.py` (governs whether a
series is re-admitted to or removed from the automatic watchlist that
whale-signal generation draws candidates from — upstream of, and feeding
into, live signal generation) and `trade_category.py` (writes once per
position **open**, participating in the live position-opening sequence
even though it only records metadata rather than gating the decision).

- [ ] **Step 1: Create the tracking issue**

`gh issue create --repo thesneakattack/kalshi-whale-poc --title "Persistence
module (services/db.py): migrate the remaining 22 _connect() modules
opportunistically" --body "..."` — body content: the 22-module list above,
the design citation
(`docs/superpowers/specs/2026-09-03-persistence-layer-redesign-design.md`
§1.5), this plan's citation, an explicit note that
`risk_manager.py`/`paper_broker.py`/`candidate_ledger.py` each need their own
dedicated PR, never bundled with an unrelated change or with each other, and
a second, lighter caution for `series_evaluator.py`/`trade_category.py` —
not requiring a fully dedicated PR, but the same "don't bundle silently as
routine" care given their proximity to live signal generation and
position-opening respectively.
Apply label `phase:implementing` per `tools/kanban_sync/labels.py`'s
vocabulary (an ongoing, no-deadline tracking item, not research/spec/plan
stage work).

- [ ] **Step 2: Record the issue number**

Add one line to `docs/open-decisions.md` in the standing "item · next action
· who decides · since" format: `services/db.py migration for the remaining
22 _connect() modules · pick up opportunistically per issue #<N>, one PR per
module or small low-risk batch, risk_manager.py/paper_broker.py/
candidate_ledger.py each get their own dedicated PR · whoever's touching one
of these files next · 2026-09-03`.

**Why this is safe:** creates a GitHub issue and a documentation line only —
zero code, zero risk of any kind beyond bookkeeping accuracy.

---

### Task 8: Item 16 (aiosqlite) — record the sequencing decision and candidate call sites; no code migrated

**Not a code task.** The design's own §2 self-review (point 3) states
plainly: "'benchmark it' ... was not actually done, because doing so safely
against trading-critical code was out of scope for a docs-only design pass...
A future plan for §2 needs its own dedicated benchmark." This plan does not
invent that benchmark or guess which call site to migrate first — doing so
would violate the never-guess HARD RULE exactly as the design's own
self-review flagged. This task instead makes the design's own conclusion
concrete and trackable.

- [ ] **Step 1: Record the exclusion explicitly**

Add to `docs/open-decisions.md`: `aiosqlite migration (item 16) for
tick_executor.run() callers · decision_bridge.py's candidate_ledger.claim()/
record_decision() calls are explicitly NOT to be migrated without their own
dedicated before/after measurement (design §2.2: centralizing every
candidate-ledger operation onto _aio_db's one-serialized-worker-thread cache
is an unmeasured risk against a trading-critical path) · you (design
approval before any such measurement work starts) · 2026-09-03`.

- [ ] **Step 2: Record the candidate non-trading-critical call sites**

Add a second line: `aiosqlite migration (item 16), non-trading-critical
candidates · settlement_resolver.py's and index_feed's flush-and-read
helpers (design §2.2's own naming) are reasonable incremental targets, each
needing its own before/after measurement per CLAUDE.md's data-plane HARD
RULE before migrating - not started by this plan · whoever picks this up ·
2026-09-03`.

- [ ] **Step 3: Confirm no code in this plan touches an aiosqlite call site**

Run: `git diff main --stat -- services/diagnostics/_aio_db.py
services/tick_executor.py services/whale_stream/decision_bridge.py` (against
this plan's own branch once Tasks 1-7 are applied) — expected: empty output,
confirming this plan's own Global Constraints held.

**Why this is safe:** documentation-only; explicitly protects the
trading-critical path by recording what NOT to do without measurement,
rather than silently proceeding on an assumption.

---

### Task 9: Item 23 (`raw_trades`) — trace `excluded`/`resolved_side` writers; `PRAGMA integrity_check` precondition

**Not a code task** — resolves the design's own §3.5 "open question this
design does not resolve, flagged for the plan stage," at the pipeline stage
the design itself named for it. Does not build the DuckDB/Parquet export
(the design's §6 explicitly defers that to "its own separate plan").

- [ ] **Step 1: Trace every writer of `raw_trades.excluded` and `.resolved_side`**

Run: `grep -rn "excluded\s*=" services/ main.py | grep -i raw_trades` and,
separately, `grep -rn "UPDATE raw_trades" services/ main.py` and `grep -rn
"resolved_side" services/ main.py` — read every match's surrounding function
to determine whether it's an `INSERT`-time value (append-only, safe for a
periodic re-export) or a later in-place `UPDATE` (would be missed by a naive
append-only Parquet refresh, per the design's own framing of this exact
risk). Record the answer, with file:line citations, not a summary.

- [ ] **Step 2: Read-only integrity check on `series_watcher.db`**

Run (read-only, `mode=ro` URI, matching every benchmark in the design
itself): `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app && python
-c \"import sqlite3; c = sqlite3.connect('file:data/series_watcher.db?mode=ro',
uri=True); print(c.execute('PRAGMA integrity_check(5)').fetchone())\""` —
`integrity_check(5)` (bounded to 5 errors) rather than the unbounded form,
since this file is 29.6 GB and live; a full unbounded check on a file this
size is exactly the kind of exchange-adjacent-hot-path-adjacent cost the
data-plane HARD RULE says must be measured, not assumed cheap — bounding it
is the safer default for a first pass. If it reports anything other than
`ok`, escalate immediately per `docs/open-decisions.md` (a human decision,
per this plan's own Global Constraints) rather than proceeding to Step 3.

- [ ] **Step 3: Record the findings and the resulting design choice**

Add to `docs/open-decisions.md`: one line recording Step 1's finding (whether
`excluded`/`resolved_side` are ever updated in place, with the file:line
citations) and, contingent on that finding, which of the design's §3.5
options — (a) full periodic re-export (simple, ~4.1 min estimated per the
design's §3.3) or (b) incremental append-plus-small-updates-file — the
DuckDB/Parquet export's own future plan should build against; plus Step 2's
integrity-check result. Format: `raw_trades DuckDB/Parquet export design
input · [finding] → recommend option (a)/(b) · you (design approval for the
export plan itself) · 2026-09-03`.

**Why this is safe:** Step 1 is read-only source inspection. Step 2 is a
read-only SQLite pragma against a `mode=ro` URI — no write, no lock
contention risk beyond an ordinary concurrent read (WAL mode permits this).
Step 3 is documentation only.

---

### Task 10: Full regression suite + live validation

**Not a code task** — the empirical confirmation Tasks 1-9's changes work as
intended against the live app, per this repo's own standing practice
(matching Tier 0's own final task).

- [ ] **Step 1: Run the full local test suite**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/agent-ae61311892d35be18 && python -m pytest tests/ -q -m 'not slow'"`

Expected: same pass count as the pre-existing baseline (confirm fresh —
`git log -1 --format=%h` on `main` — rather than trusting a number from
earlier in this plan-writing session) plus this plan's net new tests (6 in
Task 1, 3 in Task 2, 3 in Task 3, 2 in Task 4, 2 in Task 5, 2 in Task 6 = 18
net new), all passing. Any other failure is a real regression — investigate
via `superpowers:systematic-debugging` before proceeding.

- [ ] **Step 2: `import main` sanity check**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/agent-ae61311892d35be18 && python -c 'import main' && echo IMPORT_OK"`
Expected: `IMPORT_OK` — no import errors from the new `services/db.py`
module or its three new importers.

- [ ] **Step 3: Deploy and watch fd count for the three migrated modules**

After this branch merges and the primary checkout's running app reloads it,
watch `/proc/<worker-pid>/fd` (the same census mechanism Tier 0's own Task 9
uses) across at least a few hours of live uptime — per this repo's own
"Derive soak windows from logged occurrence gaps" convention, size the
window from `candidate_log.db`'s real flush cadence (daemon: ~1s;
`series_watcher`'s trade-tape hot-path writes; `observability`'s ~60s
sampling) rather than defaulting to an arbitrary 2h/24h split. Expected: no
unbounded climb attributable to these three modules specifically — some
ordinary churn is fine.

- [ ] **Step 4: Re-measure `candidate_log.db`'s `flush_retained_on_lock`
      occurrence rate, split by the falsifier's per-caller attribution**

Query `fault_log.db` (read-only) for `component='capture_writer'` and
`operation LIKE 'flush_retained_on_lock_%'`, grouped by `operation`. Compare
against §4.1's pre-fix baseline (181 occurrences, ≈2.14/hour, all
undifferentiated). Per the design's own §9 success criteria: the combined
rate should drop from ~2.14/hour toward zero. If it does not, the
per-`operation` split (now available for the first time via Task 2's fix)
tells you which caller — daemon or `flush_now` — is still colliding, rather
than leaving the question open the way the pre-fix aggregate count did.
Record the result in `docs/next-action.md`.

- [ ] **Step 5: Confirm `services/db.py`'s callers didn't change any
      externally-visible behavior**

Hit each migrated module's own read routes with the live app up
(`candidate_log.py`'s `gate_summary`/`population_gate_summary` via whichever
route exposes them, `observability`'s metrics route, `series_watcher`'s
`capture_stats`/`funnel`) and confirm the response shapes are unchanged from
before this plan — a call-site-transparent migration should produce
byte-identical JSON shapes, only (per Step 3/4) different fd/contention
behavior underneath.

- [ ] **Step 6: Record the result**

Write the outcome into `docs/next-action.md`, replacing this plan's current
entry, and update `docs/open-decisions.md` for anything Steps 3-5 leave open
(fd count still climbing from an unexpected source, occurrence rate not
dropping as expected, any response-shape difference) — each gets its own
line rather than being silently left unrecorded, per this plan's own Global
Constraints and CLAUDE.md's investigation-to-guard discipline.

---

## Plan self-review

Self-review per this project's "nothing advances on one pass" HARD RULE —
same author, checking this artifact's own internal consistency and
unaddressed scope before an independent adversarial review runs (that must
be a separate, memory-less Agent call, not done here).

**Design coverage:** all five of the design's items (15/16/23/24/28) are
addressed. Item 15 (Task 1 + Tasks 3/5/6 + Task 7's tracking), item 16
(Task 8, decision record only — the design itself declined to specify a
first call site, and this plan does not guess one), item 23 (Task 9, the
open question resolved at the plan stage, export implementation correctly
deferred to a future plan per the design's own §6), item 24 (Tasks 2-4,
falsifier landing before the fix, both non-exclusive candidates
implemented), item 28 (no task — declined, matching the design exactly).

**Sequencing fidelity check:** re-read against the design's own §6 diagram
before finalizing this plan — Task 1 first (mechanical prerequisite); §4's
fix (Tasks 2-4) placed immediately after, matching §6's explicit "not
blocked on full §1 rollout... early adopter" framing and this plan's own
stated reasoning (a currently-firing live fault outranks a
measured-but-quiet fd contribution); the other two priority §1 migrations
(Tasks 5-6) follow; the remaining-22-module tracking (Task 7) and the two
no-code decision records (Tasks 8-9) come last since none of them has a
real dependency on task order, only on Task 1 existing (Task 7) or nothing
at all (Tasks 8-9, pure documentation). No task was placed out of the
design's own logic without a stated reason.

**A genuine correction found while drafting, not glossed over:** the
design's own suggested falsifier implementation ("one extra string in the
existing `fault_log.record(...)` call," implying `context=`) would not have
worked — `fault_log._write()`'s `ON CONFLICT` clause never updates `context`
on a repeat occurrence, only `count`/`last_seen`. Task 2 instead folds the
caller identity into `operation` (which *is* part of the `UNIQUE` key). This
was caught by reading `services/fault_log.py:125-144` directly before
writing Task 2, per the never-guess HARD RULE, rather than transcribing the
design's own prose verbatim — the kind of thing this plan's own adversarial
review should double-check independently rather than trust this note.

**A second correction, also found while drafting:** the design's §1.4
illustrative "After" example for `services/candidate_log.py` (a bare
two-line `_connect()`) does not account for that module's real `_connect()`
body, which also creates two indexes and calls `_add_column_if_missing`
twice — nor does it generalize to `series_watcher.py`'s four indexes. Tasks
3, 5, and 6 each extend the pattern to run the extra DDL on the yielded
connection rather than silently dropping it, and each task's own test suite
includes an explicit "still creates all tables/indexes/columns" regression
test specifically because a verbatim copy of the design's simplified example
would have passed the "closes its connection" test while silently breaking
query performance (missing indexes) or, for `candidate_log.py`, breaking the
`unit_cost` cost-blindness fix entirely (missing column). This is exactly
the shape of gap this plan's own tests are built to catch, not merely
narrate.

**Unaddressed scope / weaknesses I can see in my own artifact:**

1. **Task 4's chosen number (1000ms, matching the daemon's existing budget)
   is a genuine engineering decision this plan makes, not one the design
   made for it** — the design named two non-exclusive candidate directions
   ("widen the budget, or route through the daemon's own budget") but never
   committed to a number. I chose to reuse `_DAEMON_BUSY_TIMEOUT_MS` exactly
   rather than pick a new value, on the reasoning that a number already
   proven safe in production for the same file beats inventing one — but
   this is still new load-bearing decision I introduced, and Task 10 Step 4
   is where it actually gets checked against real behavior. Flagging this
   explicitly for the adversarial review to scrutinize on its own merits,
   not accept because I judged it low-risk.
2. **Task 9's `PRAGMA integrity_check(5)` (bounded to 5 errors, not
   unbounded) is my own conservative choice, not something the design
   specified** — the design's §7 only says "PRAGMA integrity_check
   (read-only)" without naming the bound. I chose a bounded check given the
   file's live 29.6 GB size and the data-plane HARD RULE's "any diagnostic
   on the exchange-wide hot path is measured for runtime cost before it
   ships" — but I have not measured how long even the bounded form takes
   against the real file, and an adversarial review should treat this as an
   assumption, not a verified-safe choice, and check whether it needs its
   own timing measurement before Task 9 Step 2 runs live.
3. **Task 7's "22 modules" recount is my own re-derivation** (30 total minus
   Tier 0's 5 minus this plan's 3 direct migrations), not copied from the
   design's own "~20" figure — I did the subtraction explicitly and got 22,
   which I believe is more precise than the design's own rounded figure, but
   an adversarial review should re-verify the arithmetic against the
   design's §1.1 list independently rather than trust my count.
4. **This plan does not include a task auditing whether any test elsewhere
   in the suite directly imports `candidate_log._add_column_if_missing`**
   (removed by Task 3) **or otherwise depends on the exact internal shape of
   any of the three migrated modules' `_connect()` beyond what each task's
   own test file already covers** — Task 10 Step 1's full-suite run is the
   actual backstop for this, but I have not grepped every test file in the
   repo for a reference to these specific internals before writing this
   plan, only the three modules' own test files. A real gap if one exists
   elsewhere, caught by Step 1 regardless, but not pre-verified here.
5. **Task 8 and Task 9 produce `docs/open-decisions.md` lines whose "who
   decides"/next-action framing I wrote without checking with the user** —
   consistent with this repo's own convention for that file (many existing
   lines are session-authored), but worth naming explicitly since these are
   real, if small, process decisions (issue labels, line wording) I made
   without a design-stage sign-off on the exact wording, only on the
   substance (that a decision record is the right artifact, which the
   design itself already established).

**What I did not find wrong with my own artifact:** every task states
files, an "Interfaces"/blast-radius section, a TDD test-first step, and an
explicit "why this is safe" statement, per this task's own structural
requirements. Every code snippet for Tasks 1-6 is either a verbatim
transcription of currently-read source (confirmed via direct `Read`/`grep`
during this plan's own drafting, not recalled) or a concrete new
implementation matching the design's own reviewed API shape — no
placeholder/sketch code. No task enables real trading, weakens a safety
gate, deletes or moves a `data/*.db` file, or touches
`candidate_ledger.py`/`tick_executor.py`/`decision_bridge.py` — checked
against this plan's own Global Constraints section and CLAUDE.md's safety
invariants directly. The falsifier (Task 2) lands before the fix it
measures (Tasks 3-4), matching the design's own reasoning for why that
ordering produces a meaningful before/after rather than an unfalsifiable
"it seems better now."
