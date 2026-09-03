# `services/db.py` foundation audit — 2026-09-03

Read `services/db.py` (commit 17b2e8f, `.claude/worktrees/persistence-layer-impl`)
and all 8 tests in `tests/test_db.py` directly. Confirmed zero production
callers exist yet (`grep -rn "from services import db\b\|db\.connect(\|db\.register_schema("`
outside the module and its own tests returns nothing) — this is inert
foundation code, not yet load-bearing anywhere. Findings below are about
what happens once the 30-module migration actually wires it in.

## What's solid

- **The fd-leak fix is correct and matches the lesson from PR #501**: `try:`
  starts immediately after `sqlite3.connect()` succeeds, before PRAGMA/schema
  setup — a setup-time failure still reaches `finally: conn.close()`. This
  was a real, separately-discovered gap in the first Tier0 pass; `db.py`
  incorporates it from the start rather than needing its own follow-up fix.
- **`_SCHEMAS` dict access is correctly lock-protected** on both the write
  path (`register_schema`) and the read path (`connect`) — no obvious race
  there, and the one concurrency test (10 threads registering distinct
  tables + connecting) passes.
- Schema registration is genuinely idempotent for the common case (same
  table, same `init_fn`, called from multiple `import`-time registrations).

## Real risks / gaps, not yet covered by tests

1. **No test for the exact failure mode this app hit for real today**:
   opening `db.connect()` against a genuinely corrupted SQLite file
   (`sqlite3.DatabaseError: database disk image is malformed` —
   `market_history.db`'s real 2026-09-03 incident). The code path should
   handle it correctly by construction (the exception propagates through
   `finally: conn.close()` same as any other), but nothing verifies that,
   and this is exactly the scenario this app has already hit in production
   once. Cheap to add — open `db.connect()` against a file with garbage
   bytes written to it and assert `close()` still ran.

2. **No test for actual lock contention.** `busy_timeout` is set to 5000ms,
   but no test opens two real connections and drives one into an actual
   "database is locked" wait/timeout to confirm the busy_timeout is doing
   what's intended rather than just being a PRAGMA value nobody's checked
   at the SQLite-engine level. This matters concretely: `capture_writer.py`
   alone has 429 "database is locked" faults in the current fault log
   (`data/fault_log.db`, spanning 2026-08-30 to today) — if that module
   migrates to `db.connect()`, its current `flush_retained_on_lock` retry
   wrapper will still be needed *around* `db.connect()`, since `db.py`
   itself has no retry logic beyond the single busy_timeout wait. Worth
   making explicit in the implementation plan so migrating `capture_writer.py`
   doesn't silently drop that retry behavior.

3. **Silent schema-registration conflict, not tested or documented as
   intentional.** `register_schema` drops a second registration for the
   same `(db_path, table_name)` pair if the table name already has *any*
   registration — first-registered wins, silently, regardless of whether
   the second `init_fn`'s body actually matches the first. The one test
   covering this (`test_schema_registration_idempotent`) only proves the
   *same* `init_fn` re-registered twice is harmless; it doesn't cover two
   *different* `init_fn`s for the same table name colliding. With 30
   modules being migrated (potentially across several sessions, per
   today's own multi-session work), an accidental table-name collision
   between two unrelated modules sharing a DB file would silently apply
   only the first one's schema, with no error and no log line. Cheap
   mitigation: raise (or at minimum warn) on a genuine conflict instead of
   silently keeping the first registration.

4. **Schema init re-runs every `connect()` call, not once per process.**
   Every `db.connect()` call re-executes every registered `init_fn` for
   that `db_path` (each typically a `CREATE TABLE IF NOT EXISTS`, itself a
   real disk check). The module's own docstring cites eliminating "548 µs
   per-call connect overhead" as a goal — re-running N schema checks on
   every single connect for a DB file with several tables (e.g.
   `signal_log.py`'s multiple tables) partially works against that same
   goal. Not a correctness bug, a performance tradeoff worth being explicit
   about in the implementation plan rather than assuming it's free.

5. **`db.connect()` is a synchronous context manager and does nothing to
   prevent event-loop-blocking misuse.** Nothing in `db.py` enforces or
   even flags that a caller on the FastAPI event loop must route through
   `tick_executor.run()` (or similar) rather than calling `with
   db.connect(...)` directly — the exact bug class this app's own Tier0/P1
   work spent real effort eliminating from five modules already. This
   isn't new risk introduced by `db.py` (today's 25+ leaking modules have
   the identical exposure), but centralizing the connection logic doesn't
   by itself prevent a future call site from reintroducing an event-loop
   stall; that discipline still has to be enforced per call site during
   migration, not by this module.

## Confidence level

**Medium-high for the core mechanism (open/pragma/schema-init/close), low
for "production-ready without changes."** The fd-leak fix itself — the
whole reason this migration exists — is correctly implemented and the one
thing most load-bearing for the impact estimate. But three of the five
gaps above (corrupted-DB handling, real lock-contention behavior, and the
silent schema-conflict case) are exactly the failure modes this app has
already hit for real in the last 24 hours (`market_history.db` corruption,
429 lock faults) — I'd want at least the corrupted-DB and schema-conflict
cases covered by a real test before the first module migrates, since both
are cheap to add and both map directly to incidents this repo has already
had, not hypothetical edge cases.

Not itself a blocker for continuing planning work (autotrade-a7's
implementation plan, the baseline measurement) — `db.py` is inert and
nothing depends on it yet — but worth resolving before the first real
module (e.g. one of the 25 still-leaking ones) is migrated onto it.
