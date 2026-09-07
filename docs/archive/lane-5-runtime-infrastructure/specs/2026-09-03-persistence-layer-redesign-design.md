# Persistence Layer Redesign — Design

## Status

Draft design (2026-09-03), stage 2 of a research → design → plan pipeline. Research basis:
`docs/superpowers/research/2026-09-02-architecture-audit-and-rewrite-considerations.md`
(§5, §9) and its authoritative correction/extension,
`docs/superpowers/research/2026-09-02-architecture-audit-second-pass.md` (§4.1, §6.1, §8
Tier 2 items 15/16/23/24/28, §9). This document covers only those five items — the shared
persistence module, finishing the `aiosqlite` migration, the `raw_trades`/`index_ticks`
engine decision, `candidate_log.db` lock contention, and small-file consolidation. It does
not cover Tier 0 (the live fd-exhaustion incident — separate, in-flight, tracked as GitHub
milestone/issue #448 with sub-issues #449–458, plan doc
`docs/archive/lane-6-observability-quality-safety/plans/2026-09-03-tier0-live-incident-remediation.md`
(moved there 2026-09-06, planning-lanes migration), merged as PR #441)
or any other Tier 1/2/3 item.

**Verified against live source at HEAD `fffe972` (branch
`worktree-agent-ae61311892d35be18`, 2026-09-03).** Every number below is either a fresh
live measurement taken for this document (timestamped, read-only, cited below) or a direct
file:line citation; nothing is carried over from either audit without being re-checked
against current source or re-measured. This document's own self-review is appended as
`## Design self-review`; per this project's "nothing advances on one pass" HARD RULE, an
independent adversarial review (a separate Agent call, no memory of this session) and a
consolidation step still need to run before this design can move to the plan stage — not
done here.

## Goal

Decide, with real benchmarks rather than the industry-typical estimates both audits
explicitly warned against trusting, how this app's persistence layer should look after:
(a) one shared connection module replaces 30 independent `_connect()` copies, (b) the
`aiosqlite` migration either continues or is bounded, (c) `raw_trades`/`index_ticks` either
move off SQLite or stay, (d) `candidate_log.db`'s live lock contention is root-caused, and
(e) small-file consolidation is either recommended or explicitly declined.

## Non-goals

- Not an implementation plan. No code, config, or `data/*.db` file changes ship from this
  document; a future `superpowers:writing-plans` pass turns whichever decisions survive
  review into tasks.
- Not Tier 0's scope: this document does not touch, and its migration path does not assume
  anything about the timing of, `services/market_history.py`, `services/title_cache.py`,
  `services/market_catalog/market_catalog.py`, `services/signal_log.py`, or
  `services/fault_log.py`'s own `_connect()` bodies — Tier 0 (PR #441, plan merged
  2026-09-03T03:10:44Z) already scopes those five. **As of this document's authoring,
  Tier 0's code has not landed** — `docs/next-action.md`, read 2026-09-03: "This is still a
  plan document only — none of the 10 tasks' code has been written yet." All 30 modules
  therefore still exhibit the leak today; this document's migration path (§1.3) is written
  so the other 25 can proceed independently of when Tier 0's five land, and so those five
  can adopt this document's shared module as a later, optional follow-up once both exist,
  rather than staying permanently on Tier 0's minimal per-module wrapper.
- Not a decision to migrate the `raw_trades` **write** path off SQLite. §3 recommends a
  read-side, periodically-refreshed analytical copy; `capture_writer.py`'s write path is
  unchanged by anything in this document.
- Not a security/auth review, not a backup/disaster-recovery audit (`services/backup/backup.py`
  is out of scope, per both audits' own open item), not a decision on the two-process split
  (Tier 3).

## Decisions record

| decision | choice | basis |
|---|---|---|
| persistence module | **hand-rolled**, not SQLAlchemy Core | §1: SQLAlchemy Core is faster per-call (measured) but the absolute costs are already small next to this app's real bottlenecks, and hand-rolling avoids a new runtime dependency and keeps the "correctly hand-rolled" precedent this app already has for exactly this class of problem |
| migration path | **incremental, all new/touched code first, existing modules opportunistically** | §1.3: no flag-day rewrite of 25 modules; each module changes its import line only |
| aiosqlite migration (item 16) | **sequenced after the persistence module, not blocked by it; trading-critical call sites need their own separate measurement before moving** | §2 |
| `raw_trades` engine | **stay on SQLite as system of record; add a periodically-refreshed DuckDB/Parquet read-side copy for analytics** | §3: real benchmark shows a 58–644× read speedup once data is in Parquet, but reading the live SQLite file *through* DuckDB is measured *slower* than native SQLite |
| `index_ticks` engine | **stay on SQLite, unchanged** | §3.4: already small (464 MB, ~897K live rows), already retained, no measured problem |
| `candidate_log.db` contention | **root-caused (§4); fix is a budget/ordering change, not an engine change** | §4 |
| small-file consolidation (item 28) | **declined for now** | §5: the benchmarked evidence supports "a pooled connection is cheap," which is necessary but not sufficient evidence for "a shared write lock across today's independent concerns is cheap" — that specific question was not benchmarked |

---

## 1. The shared persistence module (item 15)

### 1.1 Current state, freshly verified

`grep -rn "^def _connect" services/ main.py` (2026-09-03): **30 files** define a
module-level `_connect()`. Full list: `services/data_quarantine.py`,
`services/market_history.py`, `services/index_feed/ingestion.py`,
`services/history/suggestion_decisions.py`, `services/shadow_mode.py`,
`services/risk_manager.py`, `services/trade_category.py`, `services/title_cache.py`,
`services/series_cache.py`, `services/fault_log.py`, `services/series_evaluator.py`,
`services/observability/observability.py`, `services/alerting/alerting.py`,
`services/game_state.py`, `services/candidate_log.py`, `services/accounts_store.py`,
`services/market_catalog/market_catalog.py`, `services/whale_calibration/calibration_history.py`,
`services/series_watcher.py`, `services/settlement_edge.py`, `services/candidate_ledger.py`,
`services/backup/backup.py`, `services/signal_log.py`, `services/market_events/event_schedule.py`,
`services/reset/reset_log.py`, `services/paper_broker.py`, `services/reset/trade_archive.py`,
`services/market_analyst_agent/_db.py`, `services/config/config_performance.py`,
`services/research/research.py`.

Of the **25 not already scoped by Tier 0** (i.e. excluding `market_history.py`,
`title_cache.py`, `market_catalog.py`, `signal_log.py`, `fault_log.py`), I checked every
one directly (2026-09-03) for a `.close()` call anywhere in the file. Four files have *some*
`.close()` call — `game_state.py:375`, `backup/backup.py:158,160`,
`market_events/event_schedule.py:478`, `research/research.py:194` — but reading each in
context, every one belongs to a *different*, separately-opened connection (a one-off VACUUM
connection in `game_state.py`'s `purge_crypto`-shaped function, the source/dest connections
inside `backup.py`'s own `sqlite3.Connection.backup()` call, an `httpx` client close in
`event_schedule.py`, and a comment, not code, in `research.py`), never the connection
`_connect()` itself returns. **All 25 modules' `_connect()`-returned connection is closed by
nothing but CPython's reference counting**, confirming the second-pass audit's "26 of 30
never close()" finding still holds for the un-scoped 25 (the audit's original 26 count spans
all 30; Tier 0 will fix 5 of those 26 once its code lands, leaving 21 of the 26 — plus the 4
"never had a close() anywhere" modules that were among the audit's other 4 — for a total of
25 in this document's scope, matching the fresh count above).

### 1.2 Benchmark: hand-rolled closing `connect()` vs. SQLAlchemy Core pool

Both audits explicitly declined to trust an estimate here and asked for a real number. Run
2026-09-03 inside `ddev-kalshi-whale-poc-fastapi` (SQLAlchemy 2.0.52, installed to a scratch
`PYTHONPATH` target for this benchmark only — not added to `requirements.txt`), against a
throwaway scratch SQLite file (`/tmp/bench_scratch.db`, never `data/*.db`), N=2000 calls per
variant, each call = open (or pool-checkout) → one `SELECT` → close (or pool-checkin).
**Every variant applies `conn.execute("PRAGMA journal_mode=WAL")` per connect/checkout**,
matching every real `services/*.py` `_connect()` body and this document's own §1.4 "Before"
example — this detail is load-bearing for the fd-leak result (without it, variant C shows no
measurable fd delta at all) and is stated here explicitly, corrected after an earlier version
of this section omitted it from the variant description despite the Appendix claiming full
reproducibility. Script: `bench_persistence.py` (not committed; reproducible from this
document — see Appendix).

| Variant | median | mean | p90 | p99 | max | fd delta over 2000 calls |
|---|---|---|---|---|---|---|
| **A** — hand-rolled `@contextlib.contextmanager` that closes (Tier 0's minimal-form pattern) | 471.3 µs | 524.3 µs | 721.0 µs | 1,430.8 µs | 3,032.6 µs | **0** |
| **B** — SQLAlchemy Core, `create_engine("sqlite:///...")`, default `QueuePool` (`pool_size=5, max_overflow=5`) | **82.5 µs** | 100.6 µs | 127.4 µs | 421.6 µs | 4,363.9 µs | **+3** (bounded, matches pool steady-state) |
| **C** — current app idiom, `with sqlite3.connect(...) as conn:` (commits, never closes) | 153.7 µs | 220.3 µs | 244.5 µs | 646.1 µs | 14,423.6 µs | **+314** |

Reading this honestly:

- **B is ~5.7× faster than A at the median** (82.5 µs vs. 471.3 µs) because it reuses a live
  connection instead of paying `sqlite3.connect()`'s real cost every call — consistent in
  order of magnitude with the second-pass audit's own previously-measured 548 µs/connect
  figure (my A variant's 471.3 µs is the same cost, re-measured fresh, not merely repeated
  from the audit).
- **C is faster than A** (153.7 µs vs. 471.3 µs) precisely *because* it skips the `close()`
  syscall — this is the same tradeoff the leak is built on: the fastest-looking variant is
  the one silently leaking.
- **C's fd delta (+314 of 2000, ~15.7%) is the leak mechanism itself, reproduced on demand**,
  not inferred: CPython's reference counting does not release most of these connections
  before the loop ends, matching the second-pass audit's own hypothesis ("the shape of
  connections that pile up until something — the cyclic garbage collector, on its own
  schedule — releases them," §4.1). This is direct, reproducible evidence for *why* a
  closing contract matters regardless of which implementation wins on speed.
- **B's fd delta (+3, then flat) is the pool's steady-state connection count**, not a leak —
  it stops growing once the pool is warm, unlike C.

**Correctness/thread-safety, verified from source, not memory** (`sqlalchemy/dialects/sqlite/pysqlite.py`
in the installed 2.0.52 package): for a **file-based** SQLite URL, SQLAlchemy's own pysqlite
dialect automatically selects `QueuePool` and sets `check_same_thread=False` unless
overridden (`"check_same_thread", not self._is_url_file_db(url)` — line ~698; changelog note
in the same file: "SQLite file database engines now use `QueuePool` by default. Previously,
`NullPool` were used"). This directly answers the question "is it safe to share a
SQLAlchemy-pooled SQLite connection across this app's many OS threads (tick_executor's pool,
`_scoring_pool`'s pool, aiosqlite's worker threads, the main event loop)?" — yes, by design,
for exactly this app's file-based-SQLite-from-multiple-threads shape, without needing
`connect_args={"check_same_thread": False}` to be supplied by hand (though supplying it
explicitly, matching the audit's own "never rely on a silent default" line, costs nothing
and documents the intent at the call site).

**Failure behavior — named, not just benchmarked:**

| | A (hand-rolled closing) | B (SQLAlchemy Core pool) |
|---|---|---|
| Connect failure | Raised immediately, no partial state cached | Same, plus: a pool checkout can also raise `TimeoutError` if every pooled connection (`pool_size + max_overflow`) is already checked out and `pool_timeout` (default 30s) elapses — **a new failure mode this app's current per-call-connect pattern does not have** |
| Lock contention (`SQLITE_BUSY`/`SQLITE_LOCKED`) | Identical either way — both variants hand the DBAPI-level `sqlite3.OperationalError` straight through; neither changes `_is_lock_error`-style detection, which stays at the call-site/module level regardless of which connect layer is under it |
| Sizing | N/A — no shared capacity to exhaust | `pool_size`/`max_overflow` are new knobs *per engine* that need sizing per file, based on real concurrent-caller counts — get this wrong and pool exhaustion becomes a new, self-inflicted contention point the current model doesn't have |
| Auditability | ~10-line function, trivial to read end-to-end | A well-known, heavily-used library; correctness is externally validated, but the pooling/checkout machinery itself is not visible at the call site the way A's `with connect() as conn:` is |

### 1.3 Decision: hand-rolled, not SQLAlchemy Core — and why the faster option loses

**Recommendation: build the shared module by hand, as a closing `@contextlib.contextmanager`
(Variant A's shape), not on SQLAlchemy Core.** This is not "the benchmark didn't matter" —
it did, and B measurably wins on latency. The decision rests on what actually needs fixing
versus what SQLAlchemy Core would additionally cost:

1. **The measured defect is the leak (fd exhaustion), not the 471 µs.** Both audits'
   headline live incident (§4.1 of the second-pass audit: a 6.8-hour data-loss outage at
   `RLIMIT_NOFILE`=1024) was caused by connections that never close, not by connect being
   slow. Variant A closes 100% of the time (fd delta 0, measured); that is the whole fix
   this module exists to deliver. B's extra 388.8 µs/call of savings (471.3 − 82.5) at this
   app's real call volumes is real but secondary — none of the app's own measured slow
   routes (§3's 8.8s/3.6s raw_trades queries, the second-pass audit's 48.6s
   `/api/quality/summary`) are bottlenecked on per-call connect overhead; they are
   bottlenecked on scan cost and thread-pool contention, neither of which a connection pool
   fixes.
2. **SQLAlchemy Core is a new runtime dependency in a project that names its dependency
   minimalism as deliberate** (`requirements.txt`'s per-pin comments; the second-pass
   audit's C7 correction that the app already carries 14 pins, not 7 — a real, but
   different, point: *some* dependencies are already justified by name; SQLAlchemy Core
   would need the same bar, and "makes a 471 µs operation take 82 µs, on a path that isn't
   the app's measured bottleneck" does not clear it). The Toolchain rule's "handspun tooling
   defaults to disabled until its own run history proves value" cuts the other way here too:
   Variant A **is** exactly the pattern Tier 0 already shipped as its minimal, already-proven
   fix for 5 modules — adopting the same shape for the other 25 has a real, current run
   history (once Tier 0 lands) to point to; SQLAlchemy Core would be introduced with zero
   run history in this codebase.
3. **New failure modes need to earn their keep.** §1.2's pool-exhaustion `TimeoutError` is a
   failure shape this app does not have today. Introducing it app-wide, for a latency win
   that doesn't address the app's measured bottlenecks, trades a well-understood risk
   (per-call connect cost, already priced into every route's timing) for a less-understood
   one (pool sizing, tuned per file, wrong by default until measured) — exactly the kind of
   trade the data-plane HARD RULE says must be explicit, not incidental to a library swap.
4. **What SQLAlchemy Core would have been good for — reuse under high call volume — is a
   real, separate, already-tracked need**, and this app already has three purpose-built
   answers to it for the specific hot paths that need it: `_aio_db.py` (read-heavy
   diagnostics), `_scoring_pool.py` (whale-scoring hot path), and Tier 0's per-module
   closing wrapper. A general-purpose pool duplicates what those already do for the paths
   that matter, while adding pool-sizing risk to the ~25 paths that don't need it (human- or
   per-tick cadence, not per-request).

**This benchmark is not wasted if item 15 is revisited later**: if a future measurement
shows a *specific* file/table genuinely bottlenecked on connect-churn (not leak, not scan
cost — a real throughput ceiling from opening fresh connections under real concurrent load),
Variant B's numbers above are the load-bearing evidence to reach for, and the module's API
(§1.4) is designed so swapping its internals from "closing contextmanager" to "SQLAlchemy
Core pool" is an internal change, not a call-site rewrite.

### 1.4 API shape

```python
# services/db.py (name illustrative)
import contextlib
import sqlite3
from pathlib import Path
from typing import Callable

# One row per table this module owns DDL for. Each entry replaces N
# module-local CREATE TABLE IF NOT EXISTS copies with one canonical
# definition (closes the second-pass audit's §9.1/§6.2 DDL-duplication
# finding — raw_trades x3, rejection_events x2, rejected_candidates x2 —
# on its own engineering merits, independent of any removed CLAUDE.md rule).
_DDL_REGISTRY: dict[str, str] = {}


def register_ddl(table: str, ddl: str) -> None:
    """Called once, at import time, by each table's owning module."""
    _DDL_REGISTRY[table] = ddl


@contextlib.contextmanager
def connect(db_path: Path, *, tables: tuple[str, ...] = (), busy_timeout_ms: int = 5000):
    """Every existing `with _connect() as conn:` / `with _connect(DB_PATH) as conn:`
    call site's replacement. Opens, sets WAL + the given busy_timeout as one
    policy (not 30 divergent copies), runs each named table's registered DDL
    idempotently, yields, and ALWAYS closes — this is the entire fix.
    `tables=()` (the default) runs no DDL, for read-only/diagnostic callers
    whose owning module has already guaranteed the schema exists."""
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
    """Replaces the 11 AST-identical copies the first audit's §9.2 #4 found
    (services/db_helpers.py in that audit's naming; same function, this
    module's home) — takes conn as a parameter, shares no state, so it
    doesn't need every caller to route through connect() above."""
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
```

**Before (a representative existing module, e.g. `services/candidate_log.py`):**

```python
def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS rejected_candidates (...)")
    conn.execute("CREATE TABLE IF NOT EXISTS rejection_events (...)")
    return conn

# call site, unchanged shape today:
with _connect() as conn:
    ...
```

**After:**

```python
from services import db

db.register_ddl("rejected_candidates", "CREATE TABLE IF NOT EXISTS rejected_candidates (...)")
db.register_ddl("rejection_events", "CREATE TABLE IF NOT EXISTS rejection_events (...)")

def _connect():
    return db.connect(DB_PATH, tables=("rejected_candidates", "rejection_events"))

# call site: ZERO changes — `with _connect() as conn:` still works, because
# _connect() still returns something usable in a `with` block; it now also
# closes on exit.
```

Every module keeps its own `DB_PATH` and its own thin `_connect()` wrapper (a one-line
function, not a full rewrite) — this is deliberate: `tools/quality_audit/persistence.py`'s
`PERSISTENCE_MODULE_PATHS` test-isolation scanner (§6.1 of the second-pass audit already
confirmed this stays needed regardless of engine choice) keys off a module-level `DB_PATH`
assignment; keeping it exactly where it is today means that scanner needs zero changes.

### 1.5 Migration path: incremental, not a flag day

**Not all-at-once.** 25 modules, several safety-adjacent (`risk_manager.py`,
`paper_broker.py`, `candidate_ledger.py`), is real surface for one PR to touch at once, and
"nothing advances on one pass" already requires its own review cycle per meaningfully-scoped
change — one PR per module (or a small batch of clearly-related, low-risk modules) keeps
each diff reviewable and each rollback trivial (a single `git revert`).

**Not "only new code" either** — the leak is a property of *existing* modules, and new code
alone never fixes it. The concrete sequence:

1. **Land `services/db.py`** (§1.4) with its own unit tests (closing behavior, DDL-registry
   idempotency, WAL/busy-timeout policy) — no call site changes yet.
2. **Migrate by risk/traffic, highest first**: the modules the fd census (second-pass audit
   §3.5, §4.1) actually named with large live handle counts —
   `series_watcher.py`/`candidate_log.py`/`observability/observability.py` — go first, since
   they're the ones with a demonstrated, measured leak contribution. Each migration is a
   mechanical "replace the function body, keep the call sites" diff per §1.4's before/after,
   independently revertable.
3. **The remaining ~20 modules migrate opportunistically** — whenever a PR already touches
   one of them for an unrelated reason, swap its `_connect()` to call `db.connect()` in the
   same PR (small, low-risk, and amortizes the migration cost into work already happening),
   rather than a dedicated sweep PR per module. A tracking issue (not a hard deadline) keeps
   the remaining count visible.
4. **Tier 0's five modules are explicitly out of this sequence** (§ Non-goals) — once Tier
   0's own minimal `@contextlib.contextmanager` wrapper lands for them, they become
   candidates for step 3's opportunistic swap to the shared module too, since the module's
   API (§1.4) is designed to be a drop-in replacement for exactly that per-module wrapper
   shape.
5. **`tick_executor.connection_for()` and `_scoring_pool.cached_read_connection()` are not
   migrated by this sequence** — they solve a different problem (a long-lived, thread-local
   *cache* of one connection, not a closing wrapper around a fresh one) and are addressed by
   §2 instead.

---

## 2. Finishing the `aiosqlite` migration (item 16) — sequenced after §1, not blocked by it

### 2.1 What "finish the migration" actually requires — narrower than the audit's framing

Reading `services/tick_executor.py` and `services/whalewatchers/_scoring_pool.py` directly
(not inferring from the audit's summary):

- **`tick_executor.connection_for()` has zero production callers**, by the module's own
  docstring, for two documented reasons: (1) it runs no DDL, so a schema-less first touch
  would raise `no such table` where every module's own `_connect()` idempotently creates it;
  (2) every one of its five originally-intended target modules
  (`series_watcher`/`market_analyst_agent`/`candidate_log`/`market_history`/`settlement_edge`)
  is also written from *other* contexts (the event loop directly, other routes), so wiring
  in a 50ms-busy-timeout dedicated connection would very plausibly convert today's silent
  5s-default wait into a new, common `database is locked` exception on writes that don't
  originate from the tick executor at all.
- **`tick_executor.run()`** (the actual mechanism in production use) is a **work-offload**
  primitive (`loop.run_in_executor(_executor, fn)`), not a connection manager. Its callers —
  `grep -rn "tick_executor.run(" services/ main.py`, 2026-09-03 — number well over a dozen,
  spanning `services/whale_calibration/routes.py` (2, diagnostic report routes),
  `services/analytics/routes.py` (1, diagnostic report), `services/settlement_resolver.py`,
  `services/index_feed/backfill.py`, `services/whale_stream/index_stream_handlers.py` (3),
  `services/index_feed/ingestion.py`, `services/whale_stream/whale_stream_handlers.py`,
  `services/market_watch/event_metadata.py`, `services/market_watch/live_status.py`, and
  `main.py` (several: trade-capture flush, secondary capture stores, signal-log series
  stats). **Critically, `services/whale_stream/decision_bridge.py:66,129` routes
  `candidate_ledger.claim()`/`record_decision()` through `tick_executor.run()` — this is
  trading-critical: `services/whalewatchers/_scoring_pool.py`'s own docstring says these
  calls "gate every whale signal" (corrected attribution — an earlier version of this line
  attributed the quote to `candidate_ledger.py` itself, which does not contain this exact
  phrase; the underlying claim that these calls gate every whale signal is independently true
  from `decision_bridge.py`'s own code regardless of which module's docstring states it).**
  Each of these callers' actual SQLite work still goes through its *own* module's leaking
  `_connect()` — `tick_executor.run()` only moves the blocking call off the event loop; it
  does not touch how that call opens its connection.
- **`_aio_db.py` is explicitly scoped to read-only, low-frequency callers** by its own
  docstring ("not the per-trade whale-scoring hot path... a single connection per file is
  enough headroom **here**"). It is not today designed to absorb `tick_executor.run()`'s
  write-shaped callers (capture-store flushes, `candidate_ledger` claims) without its own
  additional design work: aiosqlite serializes every operation on one connection's single
  worker thread per (loop, file), so routing a *write-heavy* caller through it is a real,
  different concurrency question than the read-only diagnostics case it was built for.

**Conclusion: "finish the aiosqlite migration" is not a connection-plumbing swap — it is a
per-call-site rewrite from synchronous `sqlite3` calls to `aiosqlite`'s async API** (a
different exception surface too: `_aio_db.py`'s own docstring documents that a dead
connection raises `ValueError`, not `sqlite3.ProgrammingError`, under aiosqlite 0.22.1 —
verified from the installed library, not assumed identical to sync `sqlite3`). That is
real, non-mechanical work, one call site at a time, not a single PR.

### 2.2 Sequencing answer

**Independent of §1 structurally, but §1 should land first in practice, and item 16 proceeds
incrementally afterward — not blocked on §1's completion.**

- They are independent because an `aiosqlite.Connection` is not a `sqlite3.Connection`; a
  call site migrated to `_aio_db.connection_for()` no longer touches `services/db.py` at
  all for that operation. §1's module does not gate whether a given call site *can* migrate
  to aiosqlite.
- §1 should still go first because: (a) it is the cheaper, lower-risk, already-proven-shape
  fix (Tier 0's five modules are the proof), and lands across the other 25 modules on a
  matter of PRs, not the multi-call-site rewrite item 16 requires; (b) every
  `tick_executor.run()` caller that has *not yet* migrated to aiosqlite keeps using its own
  module's `_connect()` underneath — §1 closes the leak for those regardless of item 16's
  progress, so doing §1 first shrinks the leak-exposed population immediately rather than
  waiting on item 16's slower, per-site conversion; (c) §1's DDL registry (§1.4) is a
  direct prerequisite for aiosqlite's own `schema_init` parameter on any *newly*-migrated
  call site — `_aio_db.connection_for()`'s `schema_init` callback needs a canonical DDL
  source to run once per key, which is exactly what the registry provides, instead of each
  migrated call site hand-writing its own copy (reintroducing the exact duplication problem
  §1 exists to close).
- **Trading-critical call sites need their own separate measurement before moving**, per the
  data-plane HARD RULE's "identify the bottleneck and mechanism first, never tune based on
  'should help.'" `decision_bridge.py`'s `candidate_ledger.claim()`/`record_decision()` calls
  gate every whale signal; routing them onto `_aio_db`'s one-connection-per-(loop,file) cache
  would centralize *every* candidate-ledger operation app-wide onto one serialized worker
  thread. Whether that is faster or a new bottleneck depends on real candidate volume and
  contention this document does not have live numbers for — **this document does not
  recommend migrating that call site**, and any future plan that does must carry its own
  before/after measurement, not assume aiosqlite is strictly better because it worked for
  the read-only diagnostics routes it was designed for.
- The non-trading-critical, genuinely read-only or low-frequency `tick_executor.run()`
  callers (the diagnostic report routes already on `_aio_db` per the audit, plus candidates
  like `settlement_resolver.py`'s and `index_feed`'s flush-and-read helpers) are reasonable
  incremental targets, each migrated and measured on its own, the same "per-caller with
  measurement" discipline the second-pass audit's §8.1 already prescribed and this document
  is not relitigating.

---

## 3. `raw_trades`/`index_ticks` engine decision (item 23)

### 3.1 Live measurement: current size and shape

`docker exec ddev-kalshi-whale-poc-fastapi` against `/app/data/series_watcher.db` via a
`mode=ro` URI (read-only; never copied, never written), 2026-09-03T05:0x UTC:

- File size: **29,573,849,088 bytes (29.57 GB)** — grown from the second-pass audit's
  2026-09-02 figure of ~27.2 GB/~27–29 GB.
- `raw_trades` row count: **≈40,542,063** (`SELECT MAX(rowid)`, 0.6 ms — the approximation
  `services/diagnostics/store_stats.py` itself uses and documents as exact for a genuinely
  append-only table; `raw_trades` is append-only by design, unlike `index_ticks` below).
  Grown from the audit's 39.2M.
- Series distribution (`SELECT series, COUNT(*) ... GROUP BY series`, a real full-table
  scan, run once): `KXBTC15M` dominates at **26,009,459 rows** (~64% of the table);
  `KXBTCD` 4.69M, `KXETH15M` 3.50M, `KXMLBGAME` 2.64M, `KXATPMATCH` 2.57M, `KXNFLGAME`
  750,744, `KXETHD` 385,442, `KXNBAGAME` 1,275. `KXBTC15M` is the app's own standing stability
  test series (`CLAUDE.local.md`'s memory index) and the realistic worst case for any
  per-series query.

### 3.2 The three real queries that read `raw_trades` today

`grep -rn raw_trades services/ tools/` (2026-09-03), narrowed to actual `SELECT`s against
live production code (excluding DDL, comments, and `tools/historical_data_backfill.py`,
which is an offline one-off tool, not a live query path):

1. `services/series_watcher.py:498` (`capture_stats()`) — `SELECT COUNT(*), MIN(observed_at),
   MAX(observed_at) FROM raw_trades WHERE series = ?`, all-time, no date bound.
2. `services/series_watcher.py:609,614` (`funnel()`) — `SELECT COUNT(*), SUM(CASE WHEN
   count_fp >= ? THEN 1 ELSE 0 END), MIN(observed_at) FROM raw_trades WHERE series = ? AND
   observed_at > ? AND excluded = 0`, plus a second `SELECT COUNT(*) ... WHERE series = ? AND
   observed_at > ? AND resolved_side IS NULL` — both time-windowed (`hours` parameter,
   default 24).
3. `services/diagnostics/store_stats.py` — the already-shipped (issue #210, 2026-08-30)
   approximate-above-2M-rows probe; its own docstring records the *pre-fix* cost this
   section re-confirms below (a full `SELECT COUNT(*)` took 70.5s on a 30.79M-row table).

Both real per-series queries (1, 2) already use `idx_raw_trades_series (series, observed_at)`
— confirmed via `EXPLAIN QUERY PLAN` (not assumed): `SEARCH raw_trades USING COVERING INDEX
idx_raw_trades_series (series=?)` for query 1, `SEARCH raw_trades USING INDEX
idx_raw_trades_series (series=? AND observed_at>?)` for query 2's second half. This matters:
these are not unindexed scans; they are the cost of an indexed scan over a genuinely large
per-series row range.

### 3.3 Benchmark: native SQLite vs. DuckDB-via-sqlite_scanner vs. DuckDB+Parquet

All three variants run the *same* two representative queries (query 1 and query 2's first
half from §3.2, `series='KXBTC15M'`, the largest/worst case), read-only, against the live
file (or a derived export), 2026-09-03. DuckDB 1.5.5 installed to a scratch `PYTHONPATH`
target inside the container for this benchmark only.

| Variant | capture_stats-style (all-time COUNT/MIN/MAX) | funnel-style (24h window COUNT+SUM) |
|---|---|---|
| **Native SQLite**, `mode=ro`, using `idx_raw_trades_series` | 8,799.26 ms | 3,629.71 ms |
| **DuckDB + `sqlite_scanner`**, `ATTACH ... (TYPE SQLITE, READ_ONLY)`, same live file, no copy | **26,588.11 ms** (3.0× *slower*) | **14,248.72 ms** (3.9× *slower*) |
| **DuckDB + Parquet** (one-time export of `KXBTC15M` only, ZSTD) | **13.65 ms** (644× *faster* than SQLite) | **20.16 ms** (180× *faster* than SQLite) |

**The middle row is the load-bearing negative result neither audit had:** reading a live
SQLite file *through* DuckDB's `sqlite_scanner` extension is measurably **worse** than just
using SQLite directly — the scanner does not push `WHERE series = ?` down into SQLite's own
B-tree index the way SQLite's own query planner does; it pays a full-table read through the
extension's row-by-row C API regardless of the filter. **Any DuckDB adoption must go through
a real columnar copy (Parquet or DuckDB-native), never treat DuckDB's SQLite scanner as a
zero-copy fast path.**

**The one-time export cost, measured, not estimated:** `COPY (SELECT * FROM sw.raw_trades
WHERE series = 'KXBTC15M') TO '/tmp/....parquet' (FORMAT PARQUET, COMPRESSION ZSTD)` — the
one series that is 64% of the table — took **181.56 s (≈3.0 min)** via the same
`sqlite_scanner` mechanism (the slow read above is also the cost of producing the export;
there is no faster path available without touching the live writer). **Extrapolation
mechanism, corrected after independent adversarial re-verification:** a naive
proportional-to-output-rows scaling (40.5M/26.0M × 181.6s ≈ 283s ≈ 4.7 min) is *not* the
right model — the §3.3 `EXPLAIN` evidence above shows the scanner pays a large,
roughly filter-independent cost to read the *entire* ~40.6M-row table via `SQLITE_SCAN`
before any `series` filter is applied, plus a smaller marginal cost per row actually
selected. A two-point fit against that mechanism (a 750,744-row/2.9%-of-`KXBTC15M` export
measured independently at 75.03s — 41% of `KXBTC15M`'s own export time despite 34× fewer
output rows, giving a fixed cost of ≈72s and a marginal cost of ≈4.2µs/row) applied to a
single unfiltered whole-table export (40,542,063 rows, no per-series filter needed at all)
gives **≈243s (≈4.1 min)** — close to the naive estimate's 283s/4.7min, but for the right
reason: the fixed full-table-scan cost dominates regardless of how much of the table any
one export actually keeps, so a *full-table* export is not meaningfully more expensive per
scan than a single-series one. Either way, the one-time bulk-conversion cost stays in the
same few-minutes order of magnitude for the *whole* table, not just the biggest series.

**Compression, measured on both sides:** the `KXBTC15M`-only Parquet file is
**1,526,472,100 bytes (1.53 GB)** for 26,012,845 rows (measured at write time, a few rows
more than the §3.1 snapshot — the table is live and being written throughout this
benchmark). The SQLite-side comparison uses SQLite's own `dbstat` virtual table (read-only,
`SELECT name, SUM(pgsize) FROM dbstat WHERE name IN (...)`, run once against the live file —
slow, ~6 minutes wall-clock on this 40M-row table, but real, not estimated from file size):

| Object | Bytes | |
|---|---|---|
| `raw_trades` (table data only) | 23,799,341,056 | 23.80 GB |
| `idx_raw_trades_series` | 1,199,456,256 | 1.20 GB |
| `idx_raw_trades_ticker` | 1,988,489,216 | 1.99 GB |
| `sqlite_autoindex_raw_trades_1` (the `trade_id` PRIMARY KEY index) | 2,267,115,520 | 2.27 GB |
| **Total, table + all 3 indexes** | 29,254,402,048 | **29.25 GB** |

This total (29.25 GB) accounts for essentially the entire 29.57 GB file — confirming
`raw_trades` and its indexes dominate `series_watcher.db`; `book_snapshots` and its indexes
are comparatively negligible (retained/pruned, per §3.4's `index_ticks` finding of the same
shape). `KXBTC15M` is 26,012,845 of ~40,542,063 rows (64.16% by row count). Applying that share:

- Table-only basis: 23.80 GB × 0.6416 ≈ **15.27 GB** SQLite vs. 1.53 GB Parquet → **≈10.0×**
  compression.
- Table + all indexes basis (the fairer comparison, since the indexes exist specifically to
  serve the same queries Parquet's column pruning replaces): 29.25 GB × 0.6416 ≈ **18.77 GB**
  SQLite vs. 1.53 GB Parquet → **≈12.3×** compression.

**The uniform-per-row-bytes-across-series assumption behind that row-count-share
calculation is measurably false, corrected after independent adversarial re-verification —
not just theoretically unverified as an earlier version of this section stated.** A
2,000-row `LIMIT` sample of `LENGTH(raw_json)` across seven series shows `KXBTC15M`
averaging **359.7 bytes/row**, noticeably the *smallest* of the seven sampled
(`KXNFLGAME` 410.5, `KXATPMATCH` 414.5, `KXNBAGAME` 411.8, `KXMLBGAME` 420.9, `KXETH15M`
394.3, `KXBTCD` 406.0 — i.e. 14–17% larger than `KXBTC15M`), and the Parquet side shows the
same pattern (`KXNFLGAME`'s own export averages 70.6 bytes/row vs. `KXBTC15M`'s 58.7,
~20% higher). Since both compression figures above are derived entirely from `KXBTC15M`,
and `KXBTC15M` has below-average per-row payload size, **the 10.0×/12.3× figures should be
read as an upper bound specific to `KXBTC15M`, not a representative whole-table blended
compression ratio** — the true blended ratio across all series is more likely somewhat
lower. This does not change the decision (§3.5): the **query-speed** figures above
(58–644×) remain the load-bearing number regardless, since they required no per-series
proportionality assumption at all — same file, same rows, same query, timed directly both
ways — and are unaffected by this correction.

### 3.4 `index_ticks` — a different, much smaller file, benchmarked separately

`index_feed.db`: **463,687,680 bytes (463.7 MB)**. `index_ticks` row count: **896,902**
(exact `COUNT(*)`, 1,436.99 ms — `MAX(rowid)` here reads 2,090,212 and is **not** a reliable
approximation, because unlike `raw_trades`, `index_ticks` is pruned in place
(`services/index_feed/ingestion.py:354`'s `DELETE FROM index_ticks WHERE observed_at < ?`),
so rowid gaps from deleted rows make the approximation overstate the real count — a real,
useful finding in its own right: `store_stats.py`'s "exact for an append-only store" caveat
is correct and `index_ticks` is the concrete case where it matters). The representative
per-`index_id` query (`services/index_feed/settlement_algebra.py:312`'s shape, `WHERE
index_id = ? AND observed_at >= ?`, 24h window) took **1.94 ms** against the live SQLite
file — already fast, no index-pushdown problem, nothing to fix.

**Verdict: `index_ticks` stays on SQLite, unchanged.** It is small (well under 1 GB),
already retained, and its real query shape is already cheap. Nothing in this benchmark
supports moving it; the second-pass audit's own §5.1 correction (936,932 rows measured
2026-09-02 → 896,902 net-of-prune / 2,090,212 raw rowid high-water-mark now) already flagged
it as much smaller than `raw_trades`, and this document confirms that holds under direct
query timing, not just row count.

### 3.5 Go/no-go

**Go, for `raw_trades`, as a read-side derived copy — not a write-path migration.**

- **What moves:** a periodically-refreshed Parquet (or DuckDB-native table) export of
  `raw_trades`, read by the analytics functions in §3.2 (`capture_stats()`, `funnel()`,
  `store_stats.py`'s exact-count fallback, and any future backtest/replay tooling per the
  audit's own OLAP-shape argument) — **not** `capture_writer.py`'s write path, which stays
  exactly as it is today.
- **Why not move the write path too:** this benchmark did not test DuckDB's write/concurrency
  behavior at all, and `capture_writer.py`'s retain-on-lock semantics (issue #211, already
  fixed and load-bearing — §4 below shows a *related* contention class still live) are
  specifically built around SQLite's WAL + `busy_timeout` model. Migrating writes is a
  materially bigger, riskier commitment this document explicitly declines to recommend
  without its own dedicated write-side benchmark — consistent with the "compare competing
  solution families on mechanism, benchmark, correctness, failure behavior, complexity"
  requirement, which this section applies to the *read* side because that is what was
  benchmarked.
- **Open question this design does not resolve, flagged for the plan stage**: `raw_trades`'
  `excluded` column can apparently be updated in place (the schema allows it; whether any
  live code path actually does was not traced here) — if so, a naive periodic *append-only*
  Parquet refresh would miss in-place updates. The plan stage must trace every writer of
  `excluded`/`resolved_side` before choosing between (a) a full periodic re-export (simple,
  costs ~4.7 min estimated per §3.3, acceptable off-hours) or (b) an incremental
  append-plus-small-updates-file design (cheaper per refresh, more complex). This document
  does not have enough evidence to choose between them and does not guess.
- **Backup interaction, still unaudited** (both audits' own open item, not resolved here):
  whether a periodic export job's read of `series_watcher.db` contends with
  `services/backup/backup.py`'s own `sqlite3.Connection.backup()` read of the same file.
  WAL mode's concurrent-reader guarantee suggests no functional conflict, but this is stated
  as inference from SQLite's documented WAL semantics, not measured here, and needs its own
  check before a refresh job's cadence is chosen.

---

## 4. `candidate_log.db` lock contention (item 24)

### 4.1 Live evidence

Queried `fault_log.db` read-only, 2026-09-03T05:06:57Z:

| component/operation | severity | count | first_seen | last_seen |
|---|---|---|---|---|
| `capture_writer` / `flush_retained_on_lock` (`rejected_candidates`) | warn | **181** | 2026-08-30T16:19:54Z | **2026-09-03T05:05:29Z** |
| `capture_writer` / `flush` (`rejected_candidates`, "unable to open") | error | 2 | 2026-09-02T13:01:37Z | 2026-09-02T13:06:58Z |
| `capture_writer` / `flush` (`raw_trades`, "database is locked", drops rows) | error | 237 | 2026-08-27T20:22:15Z | 2026-08-30T16:08:55Z |

The last row is the second-pass audit's already-confirmed-historical C1 finding (fixed by
commit `13680e5`, merged as `6d1a5e3`/PR #244 2026-08-30T13:26:15Z, zero recurrence since —
re-verified here directly, not re-asserted from the audit). Note: the row's own `last_seen`
(2026-08-30T16:08:55Z) is about 2h43m *after* the merge timestamp, consistent with ordinary
deploy lag between the merge landing on `main` and the running instance picking it up, not a
sign the fix was incomplete — flagged here only as a footnote for anyone later
reconstructing this incident's exact timeline. The middle row is the fd-exhaustion incident window (second-pass audit §4.1),
likewise historical. **The top row is live and current**: its `last_seen` timestamp is 88
seconds before this query ran, and it has grown from the audit's cited 163 (2026-09-02) to
**181** as of this probe. Elapsed time first-to-last: ≈84.76 hours (2026-08-30T16:19:54Z →
2026-09-03T05:05:29Z), giving a rate of **≈2.14/hour, or about one every 28 minutes** — a
low-frequency, structural race, not saturation, and every occurrence is the **retain**
path (warn severity — the batch is kept and retried, not dropped; no data loss, only a
deferred write), a materially different and less severe finding than the historical
`raw_trades` drop-path row above.

### 4.2 Mechanism, from source

`candidate_log.db` has **two independent writers** on the same file:

1. **`capture_writer`'s daemon thread** (`services/capture_writer.py:429` `_run()`), on its
   own ~1-second cadence (`_FLUSH_INTERVAL_SEC = 1.0`), flushing `rejected_candidates` and
   `rejection_events` with `_DAEMON_BUSY_TIMEOUT_MS = 1000` (a full second of patience if it
   finds the file locked).
2. **`candidate_log.py`'s `resolve_from_market_results()`** (`services/candidate_log.py:195`),
   called once per trading tick. **Tick cadence, corrected: 30 seconds, not 6.**
   `config/settings.yaml`'s `poll_interval_sec: 6` is real, but it is not what actually
   drives `trading_loop()`'s sleep — `main.py`'s `_tick_interval_sec(cfg)` only returns
   `poll_interval_sec` when the app is *not* running in streaming mode; when
   `_streaming_trade_tape_enabled()` is true (`whale_provider.name == "kalshi_trade_tape" and
   trade_stream.enabled`) it returns `safety_net_interval_sec` instead —
   `config/settings.yaml`'s value for that key is **30**. A live `GET /api/state` check
   confirms the currently-running app's `trade_stream_status` is `{"enabled": true,
   "connected": true, "mode": "stream", ...}` — i.e. streaming mode is active right now, so
   the real driving cadence is 30s. (An earlier version of this section stated 6s as "verified
   live... not assumed" — that check read the static config key without tracing the
   conditional function that actually consumes it, exactly the gap the never-guess HARD RULE
   exists to catch; corrected here after independent adversarial re-verification against both
   source and the live app.) This function does two things against the same file, in order:
   a. Calls `capture_writer.flush_now("rejected_candidates")` and
      `flush_now("rejection_events")` **synchronously**
      (`services/candidate_log.py:239-240`) — these route through the *same*
      `_flush_store()` as the daemon thread, but with the caller budget,
      `_CALLER_BUSY_TIMEOUT_MS = 50` — **20× shorter** than the daemon's own 1000ms.
   b. Opens its *own* `_connect()`-based write transaction (a `SELECT` plus two batched
      `executemany()` `UPDATE`s, already fixed in a prior PR — comment at
      `candidate_log.py:222-232` — to keep this window short) against the same file, with
      `_connect()`'s implicit default `sqlite3.connect()` `timeout` of 5.0s (no explicit
      `busy_timeout` pragma set in `candidate_log.py`'s own `_connect()` — confirmed by
      reading it directly, matching the first audit's §5.1 note that `busy_timeout` is not
      uniform across modules).

**The mechanism: whenever the daemon thread's own periodic flush and the tick-driven
`flush_now()` calls land close together in time, the 50ms caller-side budget is
structurally the more likely loser** — it is a small fraction of the daemon's own 1000ms
patience, and a small fraction of the write-transaction durations this same file's writers
have already been measured at elsewhere (`capture_writer.py:19-20`'s own docstring cites a
190K-row book-snapshot prune at ~90ms warm on a comparable mount — not this table
specifically, but the same order of magnitude a 50ms budget cannot absorb). At a 30-second
tick cadence against a 1-second daemon cadence, a collision is not rare in principle (the
two timers' phases drift relative to each other over time) but is not the dominant case
either — the observed ~1-per-28-minute rate is consistent with an occasional near-simultaneous
overlap between two independently-scheduled timers with a short window, not a saturated
resource. (The tick being 30s rather than 6s, if anything, makes the caller side's case for
widening its own budget *stronger*, not weaker — a 30-second-cadence caller has more slack to
spare than a 6-second one; §4.3's fix direction is unaffected by this correction, only the
number quoted for the collision-likelihood framing.)

**What is established vs. inferred, stated explicitly per the never-guess HARD RULE:**
established from source — the two writers exist, their budgets are 1000ms vs. 50ms, and the
tick cadence is 30s (traced through `_tick_interval_sec()`'s streaming-mode branch and
cross-checked against the live app, not the static config key alone — see above). **Not
established** — `fault_log`'s row shape (`context`: "N row(s)
retained") does not distinguish which of the two callers (daemon-thread periodic flush, or
tick-driven `flush_now()`) lost any *specific* occurrence, since both call the identical
`_flush_store()` function. The mechanism above is the best explanation the source code's
structure supports, not a captured thread-interleaving trace. **Falsifier, cheap, not run
here** (this document is design-only, no code changes): log which caller (`daemon` vs.
`flush_now`) triggered each `flush_retained_on_lock` occurrence — one extra string in the
existing `fault_log.record(...)` call at `capture_writer.py:404-407` — and the next dozen
occurrences settle the question definitively.

### 4.3 Fix direction (for the plan stage, not implemented here)

Two independent, non-exclusive candidates, both aimed at the actual mechanism rather than a
blind timeout increase (which the data-plane HARD RULE forbids without measurement):

1. **Widen `resolve_from_market_results()`'s `flush_now()` calls' budget**, or route them
   through the daemon's own 1000ms budget instead of the 50ms caller default — the caller
   context here (once per 30-second tick, not a latency-sensitive per-request path) can
   afford to wait longer than the current default was tuned for (a UI/route-facing caller).
   This directly targets the asymmetry named in §4.2 without touching the daemon at all.
2. **Set an explicit `busy_timeout` pragma in `candidate_log.py`'s own `_connect()`**,
   matching the module-by-module documented-divergence pattern the first audit's §5.1 already
   flagged as inconsistent — currently relies on Python's implicit 5.0s default rather than
   stating the value at the call site.

Both are small, targeted changes once the persistence module (§1) exists to hold the pragma
policy in one place instead of one more divergent per-module copy — a natural, low-risk
early adopter of §1's module once it lands, not a reason to delay this fix until §1 is
fully rolled out everywhere.

---

## 5. Consolidating the small SQLite files (item 28) — declined

Per this task's own instruction, item 28 is gated on whether §1's benchmark shows "a shared
connection module makes a shared lock cheap enough." The benchmark (§1.2) shows **half of
that**: a pooled connection's *per-call cost* is cheap (82.5 µs median) and a closing
connection's fd behavior is bounded either way. **What it does not show, because it was not
tested, is whether merging today's 17 independent small files (`config_performance.db`,
`accounts_store.db`, `reset_log.db`, and others under 1 MB — corrected from an earlier "~14"
estimate after independent adversarial re-verification against a fresh listing of current
`data/*.db` file sizes) onto one shared file's write lock introduces real contention between writers that
today never see each other at all.** That is a materially different question from "is one
call to one file cheap" — it is "do N formerly-independent low-frequency writers, now
sharing one file, ever collide," which requires either a real concurrent-write simulation
across the actual small files' real cadences, or live measurement after a trial merge —
neither of which this document has done.

**Declined for now, not because the mechanism is expensive, but because:**

- The absolute payoff is small: 17 files × 3 fds/connection (SQLite's WAL-mode db + `-wal` +
  `-shm`) ≈ 51 fds saved at most — marginal against a 1,024-descriptor budget, and Tier 0's
  fix plus §1's migration already address the *leak* (the thing that actually exhausted the
  budget), not the *file count*.
- Independently-relevant supporting evidence for declining now, not cited above: §4.1 already
  shows that `candidate_log.db` — today, exactly **two** independent writers sharing **one**
  file — produces a measured, live, warn-severity contention fault roughly every 28 minutes.
  If two writers on one file already collide often enough to be a named, root-caused problem
  in this same document, merging several more independent, previously-isolated low-frequency
  writers onto shared files without a real concurrent-write benchmark is a materially riskier
  choice than the payoff above alone suggests.
- The real cost is losing independent backup and crash-isolation boundaries: today, a
  corruption or lock pathology in one small file (as `market_history.db` genuinely
  experienced per the second-pass audit's §4.1 corruption finding) cannot touch a sibling
  file's data. Merging trades that isolation for a small fd count reduction this app does
  not currently need.
- §1's migration (once it lands across the 25 modules) already gives every file a closing,
  policy-consistent connection — the thing item 28 would have bought (a "shared connection
  module" existing at all) is delivered by §1 regardless of whether the files themselves are
  merged.

**Revisit only if a real, measured fd or file-count constraint reappears** after §1 and
Tier 0 both land — at that point, re-run this section's missing half (a real concurrent-write
benchmark across the actual small-file write cadences) before deciding, rather than
inferring safety from §1.2's single-file benchmark alone.

---

## 6. Sequencing across all five items

```
Tier 0 (separate, in-flight, PR #441 plan merged — code not yet landed)
   │  fixes 5 of 30 _connect()-owning modules
   ▼
§1 Persistence module (services/db.py) — lands first, mechanical, low-risk
   │  25 remaining modules migrate incrementally (§1.5); Tier 0's 5 can
   │  adopt it later as an opportunistic follow-up
   ├──► §4 candidate_log.db fix — small, targeted, early adopter of §1's
   │     pragma policy once §1 exists (not blocked on full §1 rollout)
   │
   ├──► §2 aiosqlite migration — independent of §1 structurally, proceeds
   │     incrementally afterward per-call-site; trading-critical sites
   │     (decision_bridge.py) need their own separate measurement first
   │
   └──► §3 raw_trades read-side DuckDB/Parquet copy — independent of §1/§2;
         needs its own separate plan for the excluded-column/incremental-
         refresh open question (§3.5) before implementation

§5 Small-file consolidation — declined; revisit only if a new, measured
   need appears after the above land
```

---

## 7. Risks and rollback

- **§1**: each module's migration is a single function-body swap behind an unchanged call
  site signature — a `git revert` per module, no cross-module coordination needed. Unit
  tests per module (closing behavior, DDL idempotency) gate each migration PR.
- **§2**: per-call-site rewrites carry real behavior-change risk (different exception types,
  different concurrency characteristics) — each migrated call site needs its own before/after
  measurement, not a blanket "aiosqlite is better" assumption. Trading-critical call sites
  are explicitly excluded from this document's recommendation.
- **§3**: the read-side export is additive (a new derived file), never touches
  `capture_writer.py`'s write path or `raw_trades`' own schema — a failed or stale export
  degrades to "analytics query is slow again," not data loss. The excluded-column open
  question (§3.5) must be resolved before implementation, not assumed safe.
  `PRAGMA integrity_check` (read-only) on `series_watcher.db` before any refresh job is
  first scheduled, given `market_history.db`'s own recent corruption finding makes "assume
  the file is healthy" an unverified claim on this specific app's live data right now.
- **§4**: both candidate fixes (§4.3) are configuration/timeout changes on a non-trading-decision
  path (rejected-candidate observability, not order placement) — low blast radius, easily
  reverted, and should ship with the falsifier (caller-attribution logging) from §4.2 so the
  fix's actual effect is measured, not assumed.
- **§5**: no risk — nothing is implemented; this is an explicit decline.

## 8. Testing

- §1: unit tests per migrated module verify (a) `_connect()`'s returned context manager
  actually calls `.close()` on exit (monkeypatch `sqlite3.connect`, assert `close` called —
  the same pattern Tier 0's own plan already uses for its five modules, per
  `docs/archive/lane-6-observability-quality-safety/plans/2026-09-03-tier0-live-incident-remediation.md` (moved there 2026-09-06, planning-lanes migration)'s Task 2 test
  sketch), (b) DDL registration is idempotent across repeated `connect()` calls, (c) existing
  module-level tests (which already `monkeypatch` `DB_PATH` per `CLAUDE.md`'s manual-verification
  convention) keep passing unchanged, proving call-site transparency.
- §2: each migrated call site's existing test suite must pass unchanged, plus a new
  concurrency-shaped test per site given aiosqlite's different serialization behavior.
- §3: the exact-count assertions in `store_stats.py`'s own tests continue to gate the SQLite
  side; a new test suite for the Parquet/DuckDB read path asserts row-count parity against
  the SQLite source at export time (a reconciliation check, not a trust-it-blindly design).
- §4: a regression test reproducing the two-writer collision shape (mock `time.sleep`-driven
  interleaving between a fake daemon flush and a fake tick-driven call) to prove the fix
  changes the outcome, not just the fault-log message — built against the corrected 30-second
  tick cadence (§4.2), not the earlier, incorrect 6-second figure.

## 9. Success criteria (per CLAUDE.md's effectiveness/efficiency/informativeness axes)

- **§1**: fd count for the 25 migrated modules stays flat under sustained load (measurable via
  the same `/proc/<pid>/fd` census both audits used) — effectiveness. Per-call latency is
  reported, not assumed, once real call-site instrumentation exists — efficiency. Every
  connection failure surfaces through the same, already-existing `sqlite3.Error` degradation
  path each module already has — informativeness, unchanged behavior.
- **§2**: each migrated call site reports before/after latency and correctness parity in its
  own PR — no blanket claim of success across the whole item.
- **§3**: the analytics routes that read `raw_trades` today (§3.2) report their new p50/p90
  latency post-migration, compared directly against this document's own §3.3 baseline
  numbers — a concrete, falsifiable claim, not "it should be faster."
- **§4**: `flush_retained_on_lock` occurrence rate on `candidate_log.db`, measured the same
  way §4.1 measured it here, drops from ~2.14/hour toward zero (or the falsifier from §4.2
  attributes the remaining occurrences to a different, still-unaddressed cause).
- **§5**: N/A — declined.

---

## Appendix — benchmark reproduction

All benchmarks in this document were run 2026-09-03 inside `ddev-kalshi-whale-poc-fastapi`
against either the live `data/series_watcher.db`/`data/index_feed.db`/`data/fault_log.db`
files (always via a `mode=ro` URI, read-only, never copied or written) or a throwaway scratch
file (`/tmp/bench_scratch.db`, `/tmp/raw_trades_kxbtc15m.parquet` — both container-local
`/tmp`, never `data/`, deleted at container restart, not committed). DuckDB 1.5.5 and
SQLAlchemy 2.0.52 were installed to scratch `PYTHONPATH` targets (`pip install --target=...`)
for this benchmarking session only — neither is added to `requirements.txt` by this document.
The connect-cost benchmark script (`bench_persistence.py`, referenced in §1.2) is not
committed; its contents are fully specified in §1.2/§1.4 for anyone reproducing the
measurement.

**Live sources read directly**: `services/capture_writer.py`, `services/candidate_log.py`,
`services/tick_executor.py`, `services/whalewatchers/_scoring_pool.py`,
`services/diagnostics/_aio_db.py`, `services/diagnostics/store_stats.py`,
`services/series_watcher.py`, `services/index_feed/ingestion.py`,
`services/index_feed/settlement_algebra.py`, `services/market_analyst_agent/_db.py`,
`tools/quality_audit/persistence.py`, `config/settings.yaml`, `docs/next-action.md`,
`sqlalchemy/dialects/sqlite/pysqlite.py` (installed 2.0.52), all 30 `_connect()`-defining
modules under `services/`. Live queries: `data/series_watcher.db`, `data/index_feed.db`,
`data/fault_log.db` (all `mode=ro`), `gh pr view 441`, `git log`.

---

## Design self-review

Self-review per this project's "nothing advances on one pass" HARD RULE — same author,
checking this artifact's own internal consistency and unaddressed scope before an
independent adversarial review runs. This is not the adversarial review itself (that must
be a separate, memory-less Agent call, not done here) — it is the cheaper first pass meant
to catch sloppiness before spending that independent effort.

**Internal consistency checks:**

- The `_connect()` module count (30) is used consistently across §1.1, §1.5, §6, and the
  Non-goals section — all five recur against the same fresh grep, not a mix of the audit's
  older counts and my own. Checked by re-reading each occurrence against §1.1's source list:
  consistent.
- The Tier 0 "code not yet landed" finding (Non-goals, §1.1, §1.5, §6) is stated identically
  everywhere it appears and is dated to the same `docs/next-action.md` read — I did not find
  a place where the document assumes Tier 0's fix is live while stating elsewhere that it
  isn't. This was a genuine correction mid-investigation (I initially assumed, from the task
  brief's own wording, that Tier 0's fix was already shipped, and only discovered it
  wasn't by directly checking `services/market_history.py`'s current source and
  `docs/next-action.md` — worth flagging explicitly here since it's exactly the kind of thing
  an adversarial review would otherwise have to catch, and I'd rather name it than have it
  found).
- The DuckDB `sqlite_scanner` negative result (§3.3) is stated once, prominently, and the
  Parquet numbers that follow do not accidentally reuse the scanner's slow numbers — verified
  by re-reading the table and prose against my own raw benchmark output (reproduced above in
  my working notes, not re-pasted here) rather than trusting my own summary of it.

**Unaddressed scope / weaknesses I can see in my own artifact:**

1. **§4.2's mechanism claim is source-derived, not a captured trace**, and I labeled it as
   such — but I should be explicit here that this is the single most inference-heavy claim in
   the document. The alternative hypothesis I did not fully rule out: the daemon thread's
   *own* successive flushes could in principle collide with each other under some scheduling
   pathology (not just against `resolve_from_market_results()`), and the fault log's shape
   cannot distinguish that either. §4.2's falsifier (log the caller) resolves this either way,
   but the document's prose leans toward the two-independent-writers framing more confidently
   than the "not established" caveat technically supports. A stricter version would hedge
   §4.2's opening sentence further; I judged the current wording — mechanism named, then
   immediately qualified with what's established vs. not — as honest rather than overclaiming,
   but this is a judgment call an adversarial reviewer should re-check against the raw
   `capture_writer.py`/`candidate_log.py` source, not take from my framing.
2. **The compression-ratio estimate (§3.3) still rests on one unverified proportionality
   assumption** (uniform per-row bytes across series) even after the `dbstat` measurement
   upgrade. I labeled this explicitly, but did not attempt to verify it — e.g., by comparing
   `raw_trades`' fixed-width numeric columns against `raw_json`'s variable-length text column,
   whose size plausibly *does* vary by series (a `KXATPMATCH` raw payload and a `KXBTC15M` one
   are not obviously the same byte count). This is a real gap: the true compression ratio
   could differ meaningfully from 10.0–12.3× if `raw_json` sizes vary by series. Since the
   query-speed numbers (58–644×) don't depend on this assumption at all, and are the more
   load-bearing figure for the go/no-go, I judged this an acceptable, clearly-labeled gap
   rather than a blocking one — but an adversarial reviewer should treat the specific
   10.0–12.3× figures as softer than the query-speed figures, and I should have said so more
   directly in §3.3 itself rather than only in this self-review.
3. **§2's sequencing recommendation is more architectural reasoning than benchmark** — unlike
   §1 and §3, I did not (and could not, in the time available) benchmark aiosqlite's actual
   write-serialization behavior under realistic concurrent load. The recommendation to exclude
   `decision_bridge.py` from any near-term migration is a conservative, safety-motivated call
   grounded in reading the module docstrings and the data-plane HARD RULE, not a measurement —
   I labeled it that way in §2.2, but it's worth flagging here as the one place in this
   document where "benchmark it" (the task's own instruction) was not actually done, because
   doing so safely against trading-critical code was out of scope for a docs-only design pass
   against a live paper-trading system. A future plan for §2 needs its own dedicated
   benchmark, not an extension of this document's reasoning.
4. **I did not verify whether `raw_trades.excluded` is ever actually updated in place** by any
   live code path (§3.5 names this as an open question rather than resolving it) — I searched
   for the column's read sites but did not trace every writer before running out of budget for
   this pass. This is correctly flagged as unresolved rather than guessed, but it is a real
   gap the plan stage cannot skip, since it determines whether §3's refresh design needs to be
   append-only or append-plus-update.
5. **Backup interaction (§3.5) is stated as "inference from SQLite's documented WAL semantics,
   not measured" — I did not actually re-read `services/backup/backup.py` end-to-end**, only
   the `_backup_one_file`/`_connect`/`_data_db_files` functions relevant to §1.1's close()
   audit. A full backup-interaction check was explicitly out of this document's scope (both
   audits already flagged it as their own open item), but I want to be precise that my
   citation of "WAL mode's concurrent-reader guarantee" is my own inference from general
   SQLite WAL semantics (which I have not independently verified against `docs/kalshi/` or any
   SQLite documentation page for this document specifically), not something I confirmed against
   this repo's actual backup code path.
6. **The small-file consolidation decline (§5) does not name which ~14 files it means**,
   deferring to "the first audit's §5.2 inventory" — I did not re-derive that list myself for
   this document. Since I'm declining the recommendation anyway, I judged re-deriving the
   exact file list as low-value effort relative to the rest of the investigation, but an
   adversarial reviewer checking §5 should re-confirm the "~14 files under 1MB" figure against
   current `data/*.db` sizes rather than trust the first audit's now-9-days-old count.
7. **I did not re-run the `flush_retained_on_lock` count query a second time** to confirm the
   rate calculation (§4.1) isn't an artifact of one noisy reading — 181 at one point-in-time
   query. The rate (~2.14/hour) is arithmetic on a single first_seen/last_seen/count triple
   from `fault_log`'s own aggregated row, which is what the schema provides (it does not store
   individual occurrence timestamps), so a second query would only confirm the count grew
   further, not materially change the rate estimate — I judge this a low-risk gap, but note it
   since "run it twice" is generally the kind of cheap check the never-guess rule favors.

**What I did not find wrong with my own artifact** (stated so this isn't only a list of
caveats): the core decisions record (§ Decisions record) is each individually traceable to a
specific measured or sourced finding elsewhere in the document — I checked each of the seven
rows against its cited section and found no decision asserted without a section actually
supporting it. The benchmark numbers throughout are drawn directly from raw command output
captured during this session, not retyped from memory between tool calls. No section
recommends weakening a safety gate, enabling real trading, or writing to `data/*.db` — checked
against the Non-goals section and the Safety Invariants in `CLAUDE.md` directly.
