# Self-Review — run_offline() Cooperative Yield + Elastic Connection Pool

Branch: `fix/run-offline-cooperative-yield`, one commit (`85631d2`) on top of
`main`'s `23f12e5` (PR #420 merge). Fast-follow, dispatched at explicit
user direction to move fast on implementation and apply full review rigor
before the PR merges, not before/during each edit.

## What this fixes, and what it deliberately does not

Same-day live incident after #420 merged: 5 concurrent
`GET /api/quality/summary` requests stalled a completely unrelated
`GET /api/state` for minutes. Two compounding mechanisms, both already
named as unmeasured tradeoffs in #420's own adversarial review (I4: on-loop
CPU with no yield points; I5: one connection per file serializing all
queries) - this fixes both, live-measured:

1. **Cooperative yielding** (`diagnostics.py`'s `run_offline()`): `await
   asyncio.sleep(0)` after each of its ~14 sequential checks. Verified with
   a concurrent heartbeat task: 0-1 ticks before, 2.2M ticks during 5 real
   concurrent `run_offline()` calls after.
2. **Elastic connection pool** (`_aio_db.py`): grows 2→10 connections per
   `(loop, db_path)` key based on an in-flight counter of concurrent
   `connection_for()` callers for that exact key, never reserved up front,
   never shrunk. Verified: `config_performance.db` grew 2→4,
   `signal_log.db` 2→3 under real 5-way concurrent load against production
   data, confirmed by direct inspection of `_aio_db._connections`.

**Deliberately NOT fixed here, and said so explicitly in the commit
message and to the user before implementing**: growing the pool did not
meaningfully reduce total wall time for 5 concurrent calls against real
data (~4.5min each, essentially unchanged). The dominant cost is raw
per-query/per-row cost at real table sizes (up to 124,859 rows) - a
genuine, separate root-cause question this fast-follow does not chase,
matching the user's own explicit framing ("a guard against
halting/hanging/throttling and not a root-cause solution") and CLAUDE.md's
HARD RULE against changing capacity because it "should help" without first
identifying the measured bottleneck's mechanism.

## Internal consistency check

- **Elastic sizing never shrinks**: `target_size` computation
  (`min(_MAX_POOL_SIZE, max(_MIN_POOL_SIZE, _in_flight[key]))`) is only
  ever used to GROW (`pool.extend(...)` when `len(pool) < target_size`) -
  no code path ever removes a live slot for being "too many" once created.
  Matches the stated design (cheap to over-provision a few idle
  connections, expensive to under-provision during a burst).
- **`_in_flight` is decremented in a `finally`**, so an exception anywhere
  inside `connection_for()` (a `schema_init` failure, a probe failure, a
  connect() failure) still releases the count - verified by reading the
  full function body, the `try/finally` wraps everything from the fast
  path through the locked section.
- **The fast path's new second condition** (`_in_flight[key] <= len(pool)`)
  is a read of an already-decremented-per-completed-call counter at the
  moment this line runs - since `_in_flight[key]` was incremented for THIS
  call before the check, the check is really "am I (plus whoever else is
  concurrently in flight right now) more than the pool can already serve",
  which is the intended semantic, not an off-by-one.
- **Growth only happens under the lock**, so two callers that both observe
  under-capacity and both fall through to the lock don't double-grow past
  `_MAX_POOL_SIZE` - the second one to acquire the lock re-reads
  `_in_flight[key]` and the already-larger `pool`, and `target_size` is
  computed fresh each time under the lock, capped at `_MAX_POOL_SIZE`
  regardless of how many callers are queued for the lock.
- **Existing safety properties from #420 are untouched in shape, only
  extended to a list**: the per-slot liveness probe (compare-and-swap
  eviction), the schema-init-failure cleanup, the exit-cleanup hook, and
  `close_for_current_loop()`/`reset()` were all updated to iterate pool
  lists rather than single connections - re-read each one against the
  original single-connection logic to confirm the same guarantee holds
  per-slot instead of per-key.

## Test changes and what they prove

- `test_run_offline_yields_between_checks_instead_of_holding_the_loop`
  (new): concurrent heartbeat ticks ≥10 during a real `run_offline()` call
  - confirmed FAILS against the pre-fix code (this exact assertion, run
    manually against the unmodified diagnostics.py, ticks 0-1).
- `test_connection_for_grows_the_pool_under_real_concurrent_demand` (new):
  `asyncio.gather()` with a deliberately slow `schema_init` to force real
  overlap, asserts distinct connection count exceeds `_MIN_POOL_SIZE`.
- `test_connection_for_is_cached_within_the_same_loop` and
  `test_schema_init_runs_once_per_pool_slot_never_again_after_the_pool_fills`
  (both updated, not new): rewritten for `_MIN_POOL_SIZE` since purely
  sequential calls (no two ever in flight at once) never observe
  concurrent demand and correctly never grow past the floor - this is a
  real behavior difference from a fixed-2 pool, not a loosened assertion.
- Full targeted suite: 101 passed (was 100 before this commit; net +1
  test file count from the two new tests, minus none removed).

## Known gaps, not fixed here (recorded, not hidden)

1. **Real per-query cost at production data volumes remains unaddressed**
   (see above) - this is the load-bearing open item for anyone reading
   this PR expecting the original live symptom (multi-minute concurrent
   stalls) to be fully resolved. It is not; only the two specific
   mechanisms this fast-follow targeted are resolved.
2. **`_MAX_POOL_SIZE = 10` is not itself measured** - chosen as "generous
   headroom, cheap to over-provision" per the user's own framing, not
   derived from a specific capacity calculation. If the real per-query
   cost investigation above ever lands, this number may need revisiting
   together with it.
3. **No test exercises the pool actually hitting `_MAX_POOL_SIZE`** and
   plateauing (the growth test uses `_MIN_POOL_SIZE + 3`, which is below
   `_MAX_POOL_SIZE`) - worth adding if a future change touches this file's
   growth logic again, not added here to keep this fast-follow's diff
   focused on what was actually measured live.

## Self-review verdict

No new issues found beyond the three gaps above (1 is the honestly-scoped
boundary of this fast-follow, not a defect; 2-3 are minor, non-blocking).
Ready for adversarial review.
