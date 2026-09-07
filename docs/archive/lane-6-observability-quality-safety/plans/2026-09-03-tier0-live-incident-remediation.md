# Tier 0 Live-Incident Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `GET /api/health/pipeline` from hanging indefinitely, close the
SQLite connection-lifetime leak that produced today's 6.8-hour
file-descriptor-exhaustion incident, and get a definitive, evidence-based
read on `market_history.db`'s corruption fault — the three items
`docs/next-action.md` currently lists as Tier 0, ahead of everything else in
either architecture audit.

**Research basis:**
`docs/superpowers/research/2026-09-02-architecture-audit-second-pass.md`
§4.1 (fd exhaustion + `market_history.db` corruption), §4.7
(`markets_watched: 0` / stuck tick), §8 Tier 0 items 0-2. No separate design
spec exists for this plan — see "Why no spec doc" under Global Constraints
for why that's a deliberate scoping call, not an omission.

**Live re-verification done immediately before drafting this plan
(2026-09-03, 02:09-02:15 UTC), not assumed from the audit's own hours-old
numbers:**

- `GET /api/health/pipeline` still does not return — two probes (90s and
  60s budgets) both got zero response.
- **New fact the audit did not have:** the live worker had already
  restarted since the audit (pid 8, ~26 min uptime, only 216 of 1,024 file
  descriptors in use) — i.e., **the fd leak cannot be what's hanging this
  specific request right now**, because there aren't enough open
  descriptors for exhaustion to be the cause this time. Yet dozens of other
  routes (`/api/state`, `/api/advisory/*`, `/api/regime/*`,
  `/api/backtest/*`, `/api/series-evaluator/status`, ...) are all returning
  `200 OK` in the same window (`docker logs ddev-kalshi-whale-poc-fastapi
  --since 30m`). Only `/api/health/pipeline` (and, per the PR-stage review
  of the architecture-audit PR, `/api/health/faults`) is stuck.
- Reading `services/diagnostics/routes.py:342-380`'s `get_pipeline_health`
  and `services/diagnostics/store_stats.py` directly: the route's own
  `asyncio.gather()` over `_blocking_extras` + one `store_stats.store_stats`
  probe per entry in `_store_specs()` (`raw_trades`/`book_snapshots` on
  `series_watcher.db`, `signals` on `signal_log.db`, `rejections` on
  `candidate_log.db`, `index_ticks`, `settlement_observations`,
  `game_states`) has **no timeout on any individual probe**, and
  `asyncio.gather()` without one waits for every awaitable before returning
  anything. `store_stats.store_stats()` itself has a bounded worst case
  (Python's sqlite3 default 5.0s busy-timeout, plus query time) for
  ordinary lock contention, so a probe hanging *past* that bound — as this
  route has been doing for many minutes across repeated calls — means
  something other than an ordinary `SQLITE_BUSY` wait: unresolved by this
  plan (see Task 1's own honest limitation note), but newly narrowed from
  "the whole app is stuck" to "one specific store's blocking probe never
  returns, and every poll of this one route consumes another
  `asyncio.to_thread` worker-pool slot that this specific mechanism never
  frees."
- `market_history.db` and `market_catalog.db` (both named in the audit's
  corruption/unable-to-open findings) are **not** in `_store_specs()` at
  all — this route's hang is not explained by reading either of those
  files, which narrows the live suspects to `series_watcher.db`,
  `signal_log.db`, `candidate_log.db`, `index_feed.db`,
  `settlement_edge.db`, `game_state.db`, or `fault_log.db`/scheduler state
  inside `_blocking_extras`.

**Architecture — four independent fixes, sequenced by how directly each
unblocks live investigation:**

1. **Bound every individual probe inside `/api/health/pipeline`'s
   `asyncio.gather()` with `asyncio.wait_for()`, converting a timeout into
   the same `{"error": ...}` shape `store_stats.store_stats()` already
   returns on any other failure.** This is simultaneously the fix (the
   route becomes reachable again, degraded-but-honest, instead of hanging
   forever) and the diagnostic (the next real request will show exactly
   which store's `error` field says "timed out," which the current
   incident gives no way to see without this). **Explicit limitation,
   not glossed over:** `asyncio.wait_for` cancels the *coroutine* awaiting
   a stuck `asyncio.to_thread` call, which unblocks the HTTP response —
   it cannot forcibly stop the underlying OS thread if the blocking
   `sqlite3` call inside it is genuinely wedged (as opposed to merely
   slow), since Python cannot interrupt a thread blocked in a C-level
   syscall. If the same store times out on every subsequent poll with no
   change, that is evidence of a **permanently stuck** call (an OS-level
   I/O stall, not ordinary SQLite lock contention) leaking one
   `ThreadPoolExecutor` worker per poll indefinitely — Task 1's own Step
   4 is the live check for exactly this, and if confirmed, it becomes a
   new, separately-scoped investigation (this plan does not attempt to
   fix an OS-level I/O stall sight unseen).
2. **Give five confirmed-leaking `_connect()` functions a real
   `close()`**, by turning each into a `@contextlib.contextmanager`
   generator that wraps the existing transaction semantics (`with conn:`)
   inside a `try/finally: conn.close()`. This is a **behavior-preserving,
   call-site-transparent** change: every existing `with _connect(...) as
   conn:` call site keeps working unmodified, because `_connect(...)`
   already returned something usable in a `with` statement — it now
   returns a context manager that also closes on exit, instead of a raw
   `sqlite3.Connection` whose own `__exit__` never closes. Verified before
   writing this plan (`grep -rn '_connect(' <each file>` minus the `def`/
   `with` lines): no caller stores the return value outside a `with`
   block, and no caller does `isinstance()`/type-checks it — the five
   modules are `services/market_history.py`, `services/title_cache.py`,
   `services/market_catalog/market_catalog.py`, `services/signal_log.py`,
   and `services/fault_log.py` (this last one added by this plan's own
   adversarial review — see the "Revision round" note at the end of this
   document — since it shares the identical shape and directly backs one
   of the two routes this plan's own diagnosis names as stuck). This is
   the "minimal form" of the fix the architecture audit's §6.1 describes
   (the persistence module is the "full form," a separate, larger Tier 2
   item, not this plan's scope — this plan does not touch the other 21 of
   the 26 modules the audit found with the same shape, since those
   weren't the ones confirmed leaking live or directly implicated in a
   currently-stuck route).
3. **Read-only integrity check on `market_history.db` and
   `market_catalog.db`, with a decision gate before any write.** `PRAGMA
   integrity_check` never modifies the file; its result (`ok` vs. a list
   of corruption descriptions, or — confirmed live, see Task 8 — an
   exception raised directly) decides whether `docs/next-action.md`'s open
   question ("restore from backup") needs to happen at all — this plan
   does not restore anything automatically, per the safety invariant
   against automation touching live data.
4. **`GET /api/health/faults` stops blocking the event loop** (added by
   this plan's own adversarial review — same "Revision round" note).
   Unlike `/api/health/pipeline`'s probes (already off-loop via
   `asyncio.to_thread`, just unbounded), this route calls its two SQLite
   reads **directly, synchronously, on the event loop, with no thread hop
   at all** — the same #210 bug class already fixed once for this
   route's sibling. Wrapping both calls in `asyncio.to_thread` stops this
   route's own slowness (live-measured at 45.3s, unexplained and out of
   this task's scope) from blocking every other request the process is
   serving while it runs — the same distinction item 1 draws for
   `/api/health/pipeline`'s store probes.

**Tech Stack:** Python 3 stdlib only (`asyncio`, `contextlib`, `sqlite3`) —
no new dependencies, consistent with `docs/superpowers/research/2026-09-02-
architecture-audit-second-pass.md`'s §6.3 finding that this repo's
dependency minimalism is a real strength, not a gap to fix here.

## Global Constraints

- **No fix for the fd-exhaustion root *cause*** (which thread/reference
  actually retains the leaked connections) beyond Tasks 2-6's five
  confirmed modules — the audit names 21 more modules with the identical
  no-`close()` shape and explicitly defers consolidating all of them into
  one persistence module to Tier 2, a separate, larger initiative this
  plan does not start.
- **No write to any `data/*.db` file anywhere in this plan.** Task 8's
  integrity check is read-only by construction (`PRAGMA integrity_check`
  performs no writes); if it finds real corruption, this plan's Task 8
  Step 3 records the finding and stops — restoring from a backup is a
  human decision (`docs/open-decisions.md`), never automated here, per
  CLAUDE.md's "Automation ... never edits ... and never weakens, baselines,
  or bypasses a guard."
- **No change to any trading, risk, sizing, calibration, strategy,
  settlement, or auth code.** Every file this plan touches is
  diagnostics/persistence-adjacent (`services/diagnostics/`,
  `services/market_history.py`, `services/title_cache.py`,
  `services/market_catalog/`, `services/signal_log.py`,
  `services/fault_log.py`), never the paths CLAUDE.md's safety invariants
  name.
- **No new timeout, retry, or capacity number is asserted as correct
  without being labeled an estimate to verify live**, per the data-plane
  HARD RULE. Task 1's `STORE_PROBE_TIMEOUT_SEC` is derived from Python's
  documented sqlite3 default busy-timeout (5.0s) plus headroom for query
  cost, stated as an estimate in the code comment, and Task 10's live
  validation is where it gets checked against real behavior, not assumed
  correct on landing.
- **`contextlib.closing` is not used** for Tasks 2-6's fix (the audit's own
  §5.2 named it as the natural fix and it would work, but it changes every
  call site's syntax from `with _connect(...) as conn:` to `with
  contextlib.closing(_connect(...)) as conn:` — a larger, noisier diff
  across dozens of call sites for no behavioral difference from turning
  `_connect` itself into the closing context manager, which needs zero
  call-site changes). Named here because it's the "obvious" fix a reviewer
  might expect and this is a deliberate, stated alternative-comparison, not
  an oversight.
- **Why no separate spec doc:** the "nothing advances on one pass" HARD
  RULE's pipeline is research → design/spec → implementation plan for
  work carrying a design decision. This plan's four fixes are each a
  narrow, mechanical application of an existing, already-proven pattern in
  this exact codebase (`asyncio.wait_for` bounding a gather; a
  `contextlib.contextmanager`-wrapped connection function; a read-only
  `PRAGMA` check before any write decision; `asyncio.to_thread` moving a
  blocking call off the event loop) — not a novel design requiring its own
  comparison-of-alternatives document the way the frontend migration or
  the Kalshi integration boundary did. This plan's Architecture section
  above states every design choice and its rationale directly, serving
  the same function a short spec would for work this narrow. Flagged
  explicitly for this plan's own adversarial review to check whether that
  judgment call is justified, rather than silently assumed.
- This repo has no `pytest-asyncio`. Test async functions with
  `asyncio.run(module.async_func(...))` inside a plain `def test_...`,
  never `@pytest.mark.asyncio`/`async def test_...` (verified convention:
  `tests/test_index_stream_handlers.py`'s existing tests, confirmed still
  current by `tests/test_pipeline_health_cost.py`'s own `TestClient(main.
  app).get(...)` pattern, which drives the async route through FastAPI's
  sync test client rather than `asyncio.run` directly).

---

### Task 1: Bound `/api/health/pipeline`'s per-item probes so one stuck store can't hang the whole route

**Files:**
- Modify: `services/diagnostics/routes.py:293-465` (`_blocking_extras`,
  `get_pipeline_health`)
- Test: `tests/test_pipeline_health_cost.py` (extend existing file)

**Interfaces:**
- Produces: `GET /api/health/pipeline` always returns within
  `STORE_PROBE_TIMEOUT_SEC` of the slowest individual probe, never
  indefinitely. Any store (or `_blocking_extras`) that doesn't finish in
  time reports `{"error": "timed out after <N>s"}` in its existing slot —
  same shape `store_stats.store_stats()` already uses for every other
  failure (`{"error": str(exc)}`), so no downstream consumer of this
  route's response schema needs to change.
- Consumes: nothing new — `store_stats.store_stats` and `_blocking_extras`
  keep their exact current signatures; this task wraps their invocation,
  not their bodies.

- [ ] **Step 1: Confirm the current failure mode with a live-shaped test**

Add to `tests/test_pipeline_health_cost.py`:

```python
def test_pipeline_health_currently_hangs_on_one_slow_store(monkeypatch):
    """Pins the bug this task fixes: today, one probe that never returns
    means the whole route never returns, because asyncio.gather() has no
    timeout. This test is expected to hang (and be skipped) before Task 1's
    fix lands - it exists so Step 5's version of it can assert the fix
    actually bounds it, not just that the code changed."""
    pytest.skip("documents pre-fix behavior; see the bounded version after Task 1's fix")
```

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier0-live-incident-plan && python -m pytest tests/test_pipeline_health_cost.py -q"`
Expected: existing tests still pass, new test skips (not a real regression
test yet — Step 5 replaces it with the real assertion once the timeout
exists to test against).

- [ ] **Step 2: Read the exact current code before changing it**

Run: `sed -n '314,331p' services/diagnostics/routes.py` (the `_store_specs`/
`_blocking_extras` functions) and `sed -n '342,382p' services/diagnostics/
routes.py` (the route itself, through the `results = await asyncio.gather(...)`
line and the dict keys that read `extras[...]`). Confirm nothing has
changed since this plan was drafted (2026-09-03) before editing — if it
has, stop and re-derive this task's diff from the current source rather
than applying a stale patch.

- [ ] **Step 3: Add the bounded wrapper and apply it**

In `services/diagnostics/routes.py`, near the top of the file, add:

```python
# Task 1 of docs/superpowers/plans/2026-09-03-tier0-live-incident-
# remediation.md: no individual probe below had a timeout, so one store
# whose blocking sqlite3 call never returns hung the whole route forever
# (live incident, 2026-09-03 - GET /api/health/pipeline stopped responding
# while every other route kept serving). 10.0s is an estimate: Python's
# sqlite3 default busy-timeout is 5.0s, so an ordinary SQLITE_BUSY wait
# resolves (success or OperationalError) well inside 10s; a probe that
# still hasn't returned past that is not ordinary lock contention and
# this task's own live-validation step (Task 1 Step 4) is where that
# number gets checked against real behavior, not assumed correct.
STORE_PROBE_TIMEOUT_SEC = 10.0


async def _bounded(coro, *, timeout: float | None = None) -> dict:
    """Runs one probe coroutine with a hard wall-clock bound, converting a
    timeout into the same {"error": ...} shape store_stats.store_stats()
    already returns for every other failure - callers of this route never
    see a schema difference between "the store errored" and "the store
    never answered in time." Cancelling the wait_for() here unblocks this
    HTTP response; it cannot forcibly stop the underlying OS thread if the
    wrapped asyncio.to_thread() call is genuinely stuck (not just slow) -
    see this plan's Architecture section.

    `timeout` reads STORE_PROBE_TIMEOUT_SEC live, inside the function body,
    rather than capturing it as a default-parameter expression - a default
    argument's value is frozen once, at `async def` (module-import) time,
    so a test that reassigns the module attribute afterward (e.g.
    `monkeypatch.setattr(routes, "STORE_PROBE_TIMEOUT_SEC", 0.2)`) would
    silently have no effect on an already-bound default (caught by this
    plan's own adversarial review, deterministically reproduced - a bare
    `timeout: float = STORE_PROBE_TIMEOUT_SEC` default looks identical at
    every production call site but breaks exactly this kind of test)."""
    if timeout is None:
        timeout = STORE_PROBE_TIMEOUT_SEC
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        return {"error": f"timed out after {timeout:.0f}s"}
```

Then change `get_pipeline_health`'s probe block from:

```python
    probe_started = time.perf_counter()
    results = await asyncio.gather(
        asyncio.to_thread(_blocking_extras, now),
        *(
            asyncio.to_thread(
                store_stats.store_stats, db_path, table, col, now, exact=exact_rows
            )
            for db_path, table, col in specs.values()
        ),
    )
    extras, store_results = results[0], results[1:]
```

to:

```python
    probe_started = time.perf_counter()
    results = await asyncio.gather(
        _bounded(asyncio.to_thread(_blocking_extras, now)),
        *(
            _bounded(asyncio.to_thread(
                store_stats.store_stats, db_path, table, col, now, exact=exact_rows
            ))
            for db_path, table, col in specs.values()
        ),
    )
    extras, store_results = results[0], results[1:]
```

- [ ] **Step 4: Make every `extras[...]` read below survive a timed-out `extras`**

`extras` can now be `{"error": "timed out after 10s"}` instead of the four
keys `_blocking_extras` normally returns. Every place the route reads from
`extras` (`extras["schedulers"]`, `extras["faults_last_24h"]`,
`settlement_edge_buffered`/`game_state_buffered` inside
`buffered_unwritten`) must use `.get(...)` with an explicit fallback
instead of `[...]`, or a timed-out `extras` turns into a `KeyError` 500
instead of the degraded-but-answering response this task exists to
produce. Change:

```python
        "schedulers": extras["schedulers"],
```

to:

```python
        "schedulers": extras.get("schedulers"),
```

and the same `.get(...)` pattern for `extras["faults_last_24h"]` and the
two `buffered_unwritten` reads. Add one more field so a timeout on
`_blocking_extras` itself is visible rather than silently turning into a
row of `null`s: `"extras_error": extras.get("error")` alongside the
existing top-level keys.

- [ ] **Step 5: Replace Step 1's placeholder test with the real assertion**

Replace the `pytest.skip(...)` test from Step 1 with:

```python
def test_pipeline_health_bounds_a_hung_store_probe(monkeypatch):
    """The live incident this task fixes: one store probe that never
    returns must not hang the whole route. A monkeypatched store_stats
    that blocks forever must still let the route respond, with that one
    store's slot showing a timeout error instead of a value."""
    import main
    from fastapi.testclient import TestClient
    from services.diagnostics import routes

    real_probe = routes.store_stats.store_stats

    def _hangs_for_rejections(db_path, table, col, now, **kwargs):
        if table == "rejected_candidates":
            time.sleep(routes.STORE_PROBE_TIMEOUT_SEC + 5)  # longer than the bound
            raise AssertionError("should have been cancelled/timed out before returning")
        return real_probe(db_path, table, col, now, **kwargs)

    monkeypatch.setattr(routes.store_stats, "store_stats", _hangs_for_rejections)
    monkeypatch.setattr(routes, "STORE_PROBE_TIMEOUT_SEC", 0.2)  # bound the test's own wall time

    started = time.perf_counter()
    body = TestClient(main.app).get("/api/health/pipeline").json()
    elapsed = time.perf_counter() - started

    assert elapsed < 5.0, f"route should return near the 0.2s bound, took {elapsed:.1f}s"
    assert "error" in body["stores"]["rejections"]
    assert "timed out" in body["stores"]["rejections"]["error"]
    # every other store still answered normally, proving one hung probe
    # doesn't take the others down with it
    assert "error" not in body["stores"]["signals"]
```

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier0-live-incident-plan && python -m pytest tests/test_pipeline_health_cost.py -q"`
Expected: all tests pass, including the new one, in well under 5 seconds
(the whole point — the pre-fix version of this test, run against
unmodified `main`, would hang for the full `time.sleep` duration or longer,
which is the regression this test permanently guards against).

**Note on the leftover monkeypatched worker thread:** Step 5's test
patches `time.sleep`, a genuinely interruptible-at-the-Python-level call
(unlike a real blocked C-level `sqlite3` call), so the background thread
in this specific test *does* eventually finish and get garbage collected -
this test validates the route-level timeout behavior, not the
"permanently stuck OS thread" scenario named as an open risk in this
plan's Architecture section, which cannot be safely reproduced in a fast
unit test.


### Task 2: `services/market_history.py`'s `_connect()` closes its connection

**Files:**
- Modify: `services/market_history.py:24-28` (imports), `:86-99` (`_connect`)
- Test: `tests/test_market_history.py` (extend existing file)

**Interfaces:** `_connect(db_path: Path)` changes from a plain function
returning `sqlite3.Connection` to a `@contextlib.contextmanager`-decorated
generator — every existing `with _connect(DB_PATH) as conn:` call site
(lines 153, 217, 234, 291, 329, 341, 348, 353, 380, 398, 414, 418, confirmed
by `grep -n 'with _connect(' services/market_history.py` immediately before
editing) keeps working with zero changes, because `_connect(...)` still
returns something valid to open a `with` block on — it's a context manager
object instead of a raw connection, and what it yields (`conn`) is
identical to before. `_scoring_read_connection` (a separate function,
`:100`) is unrelated and untouched — it's a deliberately long-lived cached
connection, not a per-call leak.

- [ ] **Step 1: Write the failing test first**

Add to `tests/test_market_history.py` (check its existing fixture helper
first: `grep -n '^def _' tests/test_market_history.py`, match its name/shape):

```python
def test_connect_closes_its_connection(tmp_path, monkeypatch):
    """with sqlite3.connect(...) commits a transaction, it does not close
    the connection - 26 of 30 _connect()-owning modules in this app had
    this shape, and it produced a real 6.8-hour file-descriptor-exhaustion
    incident (2026-09-02) once enough of them piled up. This module was
    one of the four confirmed leaking live."""
    import sqlite3
    from services import market_history as mh

    monkeypatch.setattr(mh, "DB_PATH", tmp_path / "market_history.db")
    closed = []
    real_connect = sqlite3.connect

    def _tracking_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        real_close = conn.close

        def _close():
            closed.append(True)
            real_close()

        conn.close = _close
        return conn

    monkeypatch.setattr(mh.sqlite3, "connect", _tracking_connect)

    with mh._connect(mh.DB_PATH) as conn:
        conn.execute("SELECT 1")

    assert closed == [True]
```

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier0-live-incident-plan && python -m pytest tests/test_market_history.py -k test_connect_closes_its_connection -q"`
Expected: **fails** (`closed == []`) against current `main` — this is the
regression the fix closes.

- [ ] **Step 2: Read the exact current function before editing**

Run: `sed -n '86,99p' services/market_history.py`. Confirm it still matches
this task's own Step 3 "before" block, quoted below, before editing — if not,
stop and re-derive the diff from current source.

- [ ] **Step 3: Apply the fix**

Add `import contextlib` to the import block (`services/market_history.py:25`,
alphabetically before `import sqlite3`). Change:

```python
def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(db_path)
    # WAL mode (2026-08-11, real live incident): rollback-journal mode
    # serializes ALL writers and readers against each other for the whole
    # transaction; WAL lets readers proceed concurrently with a writer and
    # is the standard hardening step for exactly the bursty-write scenario
    # that took the app down (trade-tape volume overwhelming a per-call
    # sqlite3.connect()). idempotent - safe to run on every connect.
    conn.execute("PRAGMA journal_mode=WAL")
    _init_schema(conn)
    return conn
```

to:

```python
@contextlib.contextmanager
def _connect(db_path: Path):
    """Every existing `with _connect(DB_PATH) as conn:` call site keeps
    working unchanged - this yields the same conn as before, but now
    closes it on exit (2026-09-03, Task 2 of docs/superpowers/plans/
    2026-09-03-tier0-live-incident-remediation.md): `with conn:` alone
    commits/rolls back a transaction, it never closes the connection, and
    this module was one of four confirmed leaking descriptors in the
    2026-09-02 fd-exhaustion incident."""
    db_path.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(db_path)
    # WAL mode (2026-08-11, real live incident): rollback-journal mode
    # serializes ALL writers and readers against each other for the whole
    # transaction; WAL lets readers proceed concurrently with a writer and
    # is the standard hardening step for exactly the bursty-write scenario
    # that took the app down (trade-tape volume overwhelming a per-call
    # sqlite3.connect()). idempotent - safe to run on every connect.
    conn.execute("PRAGMA journal_mode=WAL")
    _init_schema(conn)
    try:
        with conn:
            yield conn
    finally:
        conn.close()
```

- [ ] **Step 4: Confirm the test passes and nothing else regressed**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier0-live-incident-plan && python -m pytest tests/test_market_history.py -q"`
Expected: every test passes, including the new one.

---

### Task 3: `services/title_cache.py`'s `_connect()` closes its connection

**Files:**
- Modify: `services/title_cache.py:37-39` (imports), `:56-?` (`_connect`,
  read the current end-of-function line before editing — its body is
  longer than market_history.py's, with two inline `CREATE TABLE IF NOT
  EXISTS` statements before `return conn`)
- Test: `tests/test_title_cache.py` (extend existing file)

**Interfaces:** Same shape as Task 2 — `_connect()` (no-arg, uses the
module-level `DB_PATH`) becomes a context manager; all five call sites
(`:139, 157, 182, 220, 282`, confirmed by `grep -n 'with _connect(' services/
title_cache.py` before editing — this module has five, not the one this
plan's research section initially miscounted, per the architecture audit's
own PR-stage-review correction) keep working unchanged.

- [ ] **Step 1: Write the failing test first**

Add to `tests/test_title_cache.py`, next to the existing `_tc(tmp_path,
monkeypatch)` fixture helper:

```python
def test_connect_closes_its_connection(tmp_path, monkeypatch):
    """Same fd-leak class as market_history.py's Task 2 - five call sites
    in this module share one non-closing _connect(), and the live fd
    census (2026-09-02) measured this file's handle count growing fastest
    of any store (5 -> 148+ in under an hour)."""
    import sqlite3
    tc = _tc(tmp_path, monkeypatch)
    closed = []
    real_connect = sqlite3.connect

    def _tracking_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        real_close = conn.close

        def _close():
            closed.append(True)
            real_close()

        conn.close = _close
        return conn

    monkeypatch.setattr(tc.sqlite3, "connect", _tracking_connect)

    with tc._connect() as conn:
        conn.execute("SELECT 1")

    assert closed == [True]
```

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier0-live-incident-plan && python -m pytest tests/test_title_cache.py -k test_connect_closes_its_connection -q"`
Expected: **fails** against current `main`.

- [ ] **Step 2: Read the exact current function before editing**

Run: `awk '/^def _connect/,/^def /' services/title_cache.py | head -40` to
see the full body including both inline `CREATE TABLE` statements and the
`return conn` line, since this plan's own research pass did not
transcribe the full body. Confirm it still matches before editing.

- [ ] **Step 3: Apply the fix**

Add `import contextlib` (`services/title_cache.py:37`, alphabetically
before `import json`). Change the function signature from `def _connect()
-> sqlite3.Connection:` to `@contextlib.contextmanager\ndef _connect():`,
keep every line of the body **exactly as read in Step 2** (both `CREATE
TABLE IF NOT EXISTS` statements, unchanged), and replace `return conn` at
the end with:

```python
    try:
        with conn:
            yield conn
    finally:
        conn.close()
```

Add a docstring matching Task 2's, adjusted for this module's five call
sites and its measured fd-growth number, directly under the
`@contextlib.contextmanager` line.

- [ ] **Step 4: Confirm the test passes and nothing else regressed**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier0-live-incident-plan && python -m pytest tests/test_title_cache.py -q"`
Expected: every test passes, including the new one.

---

### Task 4: `services/market_catalog/market_catalog.py`'s `_connect()` closes its connection

**Files:**
- Modify: `services/market_catalog/market_catalog.py:33-38` (imports),
  `:65-?` (`_connect`, read its actual current end before editing — like
  title_cache.py, it has an inline `CREATE TABLE IF NOT EXISTS` before
  `return conn`)
- Test: `tests/test_market_catalog.py` (extend existing file)

**Interfaces:** Same shape as Tasks 2-3. `_connect(db_path: Path)` becomes
a context manager; all eleven call sites (`:136, 162, 208, 218, 233, 362,
403, 504, 566, 605, 616`, confirmed by `grep -n 'with _connect(' services/
market_catalog/market_catalog.py` before editing) keep working unchanged.
This is the module that reads/writes `market_catalog.db` — the store the
live `markets_watched: 0` incident's own uneliminated hypothesis (§4.7 of
the second-pass audit) named as a possible discovery-path blocker; fixing
its connection lifetime does not itself explain or fix that incident (this
task is scoped to the leak only), but a corrupt or contended
`market_catalog.db` is exactly the kind of file this bug class makes more
likely, so this task's live validation (Task 10) checks `markets_watched`
again as one of its signals, not as a claim that this task alone resolves
it.

- [ ] **Step 1: Write the failing test first**

Add to `tests/test_market_catalog.py` (check its existing fixture helper
first: `grep -n '^def _' tests/test_market_catalog.py`):

```python
def test_connect_closes_its_connection(tmp_path, monkeypatch):
    """Same fd-leak class as Tasks 2-3 - eleven call sites in this module
    share one non-closing _connect(), on the file the live markets_watched:
    0 incident's own open hypothesis names as a possible cause."""
    import sqlite3
    from services.market_catalog import market_catalog as mc

    monkeypatch.setattr(mc, "DB_PATH", tmp_path / "market_catalog.db")
    closed = []
    real_connect = sqlite3.connect

    def _tracking_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        real_close = conn.close

        def _close():
            closed.append(True)
            real_close()

        conn.close = _close
        return conn

    monkeypatch.setattr(mc.sqlite3, "connect", _tracking_connect)

    with mc._connect(mc.DB_PATH) as conn:
        conn.execute("SELECT 1")

    assert closed == [True]
```

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier0-live-incident-plan && python -m pytest tests/test_market_catalog.py -k test_connect_closes_its_connection -q"`
Expected: **fails** against current `main`.

- [ ] **Step 2: Read the exact current function before editing**

Run: `awk '/^def _connect/,/^def /' services/market_catalog/market_catalog.py | head -40`.
Confirm it matches before editing.

- [ ] **Step 3: Apply the fix**

Add `import contextlib` (`services/market_catalog/market_catalog.py:33`,
alphabetically before `import sqlite3`). Same transformation as Tasks 2-3:
`@contextlib.contextmanager` decorator, keep the body (including the
inline `CREATE TABLE`) exactly as read in Step 2, replace `return conn`
with the `try: with conn: yield conn; finally: conn.close()` block, add a
matching docstring naming this module's eleven call sites.

- [ ] **Step 4: Confirm the test passes and nothing else regressed**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier0-live-incident-plan && python -m pytest tests/test_market_catalog.py -q"`
Expected: every test passes, including the new one.

---

### Task 5: `services/signal_log.py`'s `_connect()` closes its connection

**Files:**
- Modify: `services/signal_log.py:24-30` (imports), `:150-161` (`_connect`)
- Test: `tests/test_signal_log.py` (extend existing file)

**Interfaces:** Same shape as Tasks 2-4. `_connect()` (no-arg, uses the
module-level `DB_PATH`) becomes a context manager; every call site
(21 of them per `grep -c 'with _connect(' services/signal_log.py`,
confirmed before editing) keeps working unchanged. `_scoring_read_connection`
(a separate function, `:164`) is unrelated and untouched, same reasoning
as Task 2's `_scoring_read_connection` note.

- [ ] **Step 1: Write the failing test first**

Add to `tests/test_signal_log.py` (check its existing fixture helper
first: `grep -n '^def _' tests/test_signal_log.py`):

```python
def test_connect_closes_its_connection(tmp_path, monkeypatch):
    """Same fd-leak class as Tasks 2-4 - 21 call sites in this module
    share one non-closing _connect()."""
    import sqlite3
    from services import signal_log as sl

    monkeypatch.setattr(sl, "DB_PATH", tmp_path / "signal_log.db")
    closed = []
    real_connect = sqlite3.connect

    def _tracking_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        real_close = conn.close

        def _close():
            closed.append(True)
            real_close()

        conn.close = _close
        return conn

    monkeypatch.setattr(sl.sqlite3, "connect", _tracking_connect)

    with sl._connect() as conn:
        conn.execute("SELECT 1")

    assert closed == [True]
```

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier0-live-incident-plan && python -m pytest tests/test_signal_log.py -k test_connect_closes_its_connection -q"`
Expected: **fails** against current `main`.

- [ ] **Step 2: Read the exact current function before editing**

Run: `sed -n '150,161p' services/signal_log.py`. Confirm it still matches
this task's own Step 3 "before" block, quoted below, before editing.

- [ ] **Step 3: Apply the fix**

Add `import contextlib` (`services/signal_log.py:24`, alphabetically
before `import json`). Same transformation as Task 2 exactly (this
function's body is byte-identical in shape to `market_history.py`'s,
including the `_init_schema(conn)` call rather than an inline `CREATE
TABLE`):

```python
@contextlib.contextmanager
def _connect():
    """Every existing `with _connect() as conn:` call site (21 of them)
    keeps working unchanged - this yields the same conn as before, but now
    closes it on exit (2026-09-03, Task 5 of docs/superpowers/plans/
    2026-09-03-tier0-live-incident-remediation.md), same fix and same
    reasoning as market_history.py's Task 2."""
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    # WAL mode (2026-08-11, real live incident): rollback-journal mode
    # serializes ALL writers and readers against each other for the whole
    # transaction; WAL lets readers proceed concurrently with a writer and
    # is the standard hardening step for exactly the bursty-write scenario
    # that took the app down (trade-tape volume overwhelming a per-call
    # sqlite3.connect()). idempotent - safe to run on every connect.
    conn.execute("PRAGMA journal_mode=WAL")
    _init_schema(conn)
    try:
        with conn:
            yield conn
    finally:
        conn.close()
```

- [ ] **Step 4: Confirm the test passes and nothing else regressed**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier0-live-incident-plan && python -m pytest tests/test_signal_log.py -q"`
Expected: every test passes, including the new one.

---

### Task 6: `services/fault_log.py`'s `_connect()` closes its connection

**Added by this plan's own adversarial review (finding F4):** the plan's
own Live re-verification section already named `GET /api/health/faults` as
one of only two currently-stuck routes, alongside `/api/health/pipeline` —
but the first draft of this plan fixed only the second. `fault_log.py` has
the identical non-closing `_connect()` shape as Tasks 2-5's four modules
and was not counted among them. Fixing it here, in the same mechanical
form, closes that gap rather than leaving it as a silent, undisclosed
exclusion (the plan explicitly disclosed excluding the 21 genuinely-
deferred modules with this shape, Tier 2's scope — this one is directly
implicated by this plan's own diagnosis, so it does not belong in that
"deferred" set).

**Files:**
- Modify: `services/fault_log.py` (imports, `:58-82` `_connect`)
- Test: `tests/test_fault_log.py` (already exists — confirmed by direct
  `ls` before drafting this task; it has an `@pytest.fixture(autouse=True)
  def _isolated(monkeypatch, tmp_path)` that already redirects `DB_PATH`
  to a tmp path for every test in the file, and imports the module as
  `fl`, not `fault_log` — the new test below matches both conventions
  rather than redirecting `DB_PATH` itself, which would be redundant with
  the autouse fixture)

**Interfaces:** Same shape as Tasks 2-5 exactly. `_connect()` (no-arg,
module-level `DB_PATH`) becomes a context manager; call sites confirmed by
`grep -n 'with _connect(' services/fault_log.py` immediately before
editing (this plan's own adversarial review found 4: lines 129, 153, 191,
203 — re-confirm before trusting that count, since this task's own draft
did not independently re-derive it a third time) keep working unchanged.

- [ ] **Step 1: Write the failing test first**

Add to `tests/test_fault_log.py`:

```python
def test_connect_closes_its_connection(monkeypatch):
    """Same fd-leak class as Tasks 2-5 - fault_log.py's own _connect() had
    the identical non-closing shape, and this module is the one CLAUDE.md
    tells every session to read first (services/fault_log.py's own callers
    include GET /api/health/faults, one of only two routes this plan's own
    live re-verification found stuck). DB_PATH is already redirected by
    this file's autouse _isolated fixture - no need to set it here."""
    import sqlite3

    closed = []
    real_connect = sqlite3.connect

    def _tracking_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        real_close = conn.close

        def _close():
            closed.append(True)
            real_close()

        conn.close = _close
        return conn

    monkeypatch.setattr(fl.sqlite3, "connect", _tracking_connect)

    with fl._connect() as conn:
        conn.execute("SELECT 1")

    assert closed == [True]
```

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier0-live-incident-plan && python -m pytest tests/test_fault_log.py -k test_connect_closes_its_connection -q"`
Expected: **fails** against current `main`.

- [ ] **Step 2: Read the exact current function before editing**

Run: `sed -n '58,82p' services/fault_log.py`. Confirm it still matches the
Files section's description (the inline `CREATE TABLE IF NOT EXISTS
faults` + two `CREATE INDEX IF NOT EXISTS` statements, `return conn` at
the end) before editing.

- [ ] **Step 3: Apply the fix**

Add `import contextlib` to `services/fault_log.py`'s import block
(currently `import sqlite3` / `import time` / `import traceback` / `from
pathlib import Path`, confirmed by direct read — `contextlib` sorts first
alphabetically, before `sqlite3`). Same
transformation as Tasks 2-5: `@contextlib.contextmanager` decorator, keep
the body (the `CREATE TABLE`/`CREATE INDEX` statements) exactly as read in
Step 2, replace `return conn` with the `try: with conn: yield conn;
finally: conn.close()` block, add a docstring matching Tasks 2-5's,
naming this module's confirmed call-site count and its role as one of
Tier 0's two stuck routes.

- [ ] **Step 4: Confirm the test passes and nothing else regressed**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier0-live-incident-plan && python -m pytest tests/test_fault_log.py -q"`
Expected: every test passes, including the new one. Also run the broader
suite for this module's actual callers, since `fault_log.record()` is
called from dozens of files: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier0-live-incident-plan && python -m pytest tests/ -k fault_log -q"`
Expected: no regressions — this change alters only how `_connect()`
closes, not any query, schema, or return value any caller depends on.

---

### Task 7: `GET /api/health/faults` stops blocking the event loop

**Added by this plan's own adversarial review (finding F4).** Live
evidence gathered during that review (`/api/health/faults?component=market_history&limit=15`,
2026-09-03 02:32:00-02:32:46Z): **45.3 seconds**, slower than
`/api/health/pipeline`'s own measured 24-28s in the same window. Reading
`services/diagnostics/routes.py:489-497` directly: `get_faults()` is
`async def` and calls `fl.summary(...)`/`fl.recent(...)` **directly,
synchronously, with no `asyncio.to_thread` and no timeout of any kind** —
the exact "blocking sqlite3 inside an `async def`, no thread hop" defect
class `tests/test_pipeline_health_cost.py`'s own docstring describes as
already fixed once for this sibling route (issue #210). While either
call runs, it blocks every other request this process is serving, not
just this one.

**This task does not investigate *why* `fl.summary()`/`fl.recent()` take
45 seconds** (query cost, table size, missing index — unmeasured, and per
the data-plane HARD RULE not to be guessed at) — it only stops that
unknown cost from blocking the whole app while it's paid, the same
distinction Task 1 draws for `/api/health/pipeline`'s store probes. If
this route stays slow after this task (bounded now, not blocking), the
query-cost question is a new, separately-scoped investigation.

**Files:**
- Modify: `services/diagnostics/routes.py:489-497` (`get_faults`)
- Test: `tests/test_diagnostics_routes.py` (extend existing file — confirm
  its existing `TestClient`/monkeypatch conventions first, per this file's
  own established pattern used throughout this plan)

**Interfaces:** `get_faults(limit, component, hours)`'s response shape is
unchanged for the success path; `fl.summary`/`fl.recent` keep their exact
current signatures — this task wraps their invocation in
`asyncio.to_thread`, matching the pattern `services/diagnostics/store_stats.py`'s
own module docstring already documents as the fix for the identical
class of bug on the sibling route ("Blocking sqlite3: call it through
`asyncio.to_thread`, never from a coroutine directly. That is half of what
#210 was.").

- [ ] **Step 1: Confirm the current implementation before editing**

Run: `sed -n '489,497p' services/diagnostics/routes.py`. Confirm it still
reads:

```python
@router.get("/api/health/faults")
async def get_faults(limit: int = 50, component: str | None = None, hours: float = 24.0):
    """Every swallowed exception and edge case, deduplicated with a count
    (services/fault_log.py). Start a session here: a large `count` or a
    recent `last_seen` means something is failing right now, silently."""
    from services import fault_log as fl

    return {"summary": fl.summary(since_ts=time.time() - hours * 3600),
            "faults": fl.recent(limit=limit, component=component)}
```

before editing — if not, stop and re-derive the diff from current source.

- [ ] **Step 2: Write the failing test first**

This file's own established convention (its Step-5-added tests' comment,
confirmed by direct read: "Exercised through the real route functions,
same `asyncio.run(...)` convention as every other test in this file — no
`TestClient` here, that's `test_quality_routes.py`'s convention, not this
file's") is to call the route function directly via
`asyncio.run(diagnostics_routes.get_X())`, imported at this file's own top
as `from services.diagnostics import routes as diagnostics_routes`. Match
that, not `TestClient`. Add to `tests/test_diagnostics_routes.py`:

```python
def test_get_faults_runs_off_the_event_loop(monkeypatch):
    """Same #210 bug class as /api/health/pipeline's store probes
    (tests/test_pipeline_health_cost.py) - fl.summary()/fl.recent() must
    not run synchronously inside this async def route, or a slow fault_log
    query blocks every other request this process is serving."""
    from services import fault_log

    on_loop = []

    def _summary(*args, **kwargs):
        try:
            asyncio.get_running_loop()
            on_loop.append("summary")
        except RuntimeError:
            pass
        return {"distinct_faults": 0, "total_occurrences": 0, "by_component": {},
                "by_severity": {}, "most_frequent": []}

    def _recent(*args, **kwargs):
        try:
            asyncio.get_running_loop()
            on_loop.append("recent")
        except RuntimeError:
            pass
        return []

    monkeypatch.setattr(fault_log, "summary", _summary)
    monkeypatch.setattr(fault_log, "recent", _recent)

    body = asyncio.run(diagnostics_routes.get_faults())

    assert on_loop == []
    assert body["summary"]["distinct_faults"] == 0
    assert body["faults"] == []
```

(`asyncio` is already imported at this file's top, per `grep -n '^import asyncio' tests/test_diagnostics_routes.py:14` — no new import needed.)

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier0-live-incident-plan && python -m pytest tests/test_diagnostics_routes.py -k test_get_faults_runs_off_the_event_loop -q"`
Expected: **fails** (`on_loop` non-empty) against current `main`.

- [ ] **Step 3: Apply the fix**

Change:

```python
    from services import fault_log as fl

    return {"summary": fl.summary(since_ts=time.time() - hours * 3600),
            "faults": fl.recent(limit=limit, component=component)}
```

to:

```python
    from services import fault_log as fl

    summary, faults = await asyncio.gather(
        asyncio.to_thread(fl.summary, since_ts=time.time() - hours * 3600),
        asyncio.to_thread(fl.recent, limit=limit, component=component),
    )
    return {"summary": summary, "faults": faults}
```

(`asyncio` is already imported at module scope, confirmed in Task 1's own
research — no new import needed. This task deliberately does **not** wrap
these two calls in Task 1's `_bounded()` helper: `fl.summary`/`fl.recent`
already catch their own exceptions internally and degrade to `{"error":
...}`/`[]` rather than raising or hanging past SQLite's own busy-timeout,
so this task's own live evidence — 45s, a real return, not a hang — does
not show the same "never returns" mechanism Task 1 exists to bound. If a
future session measures this route hanging indefinitely rather than
merely slowly, that's new evidence for adding the same `_bounded()`
treatment here too, not assumed necessary now without that evidence.)

- [ ] **Step 4: Confirm the test passes and nothing else regressed**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier0-live-incident-plan && python -m pytest tests/test_diagnostics_routes.py -q"`
Expected: every test passes, including the new one.

---

### Task 8: Read-only integrity check on `market_history.db` and `market_catalog.db`, decision recorded, no auto-restore

**Not a code task** — a one-time diagnostic with a documented outcome,
same shape as the precedent plan's own "Task 5: live validation" (not a
code change either). Read-only against live, actively-written data files,
so this runs from the primary checkout against the running app's actual
`data/market_history.db` and `data/market_catalog.db`, not worktree
copies. Both files, not just `market_history.db`, per `docs/next-action.md`'s
own Tier-0 wording, which names both — the plan's first draft checked only
one; added back per this plan's own adversarial review (finding F7).

**Already run once, for real, during this plan's own adversarial review**
(2026-09-03, 02:33:51Z and 02:35:03Z, Task 8 Step 2's exact command, twice,
~90 seconds apart) — the result below is real evidence, not a prediction,
and Step 2's own command needs correcting to match what actually happens
(finding F6):

- `market_history.db`: `PRAGMA integrity_check` **raised
  `sqlite3.DatabaseError: database disk image is malformed` while
  fetching, both times** — it did not return a row saying so, it raised.
  Reproducing identically twice, ~90 seconds apart, on a live, currently
  mounted, actively-written file is evidence of **real, persistent,
  structural damage**, not a one-off race — a transient read-vs-write race
  would not fail the identical way twice in a row with no write in
  between. The cross-check against the live fault count (Step 1) also
  argues against "transient": the `malformed` fault's `count` had not
  grown since the architecture audit's original reading (45 occurrences,
  last seen 2026-09-02T20:44:11Z) — a transient race that only happened
  once during the fd-exhaustion window, hours ago, would not still fail a
  fresh read-only check now.
- `market_catalog.db`: **`ok`** — clean. This is useful, independent
  evidence bearing on this plan's own open question in Task 4 (whether a
  corrupt/contended `market_catalog.db` could explain `markets_watched: 0`)
  — structural corruption on that file is now ruled out, though contention
  (not corruption) remains untested.

Step 1-3 below are still written as instructions for whoever executes this
plan (the result may have changed again by execution time — a corrupt file
could theoretically self-heal only via an explicit repair/restore, which
this plan does not perform, so re-running is a confirmation, not a
redundant check), but the finding above is real, already-obtained
evidence, not a hypothetical.

- [ ] **Step 1: Confirm the fault is still live before checking, for both files**

Run: `curl -sk -m 90 "https://kalshi-whale-poc.ddev.site:8443/api/health/faults?component=market_history&limit=15"`
(retry once at 90s if the route is still affected by the Task 1/7 bugs
this plan hasn't deployed yet — if both attempts fail, fall back to
reading `fault_log.db` directly, read-only: `docker exec ddev-kalshi-whale-poc-fastapi
python3 -c "import sqlite3; c=sqlite3.connect('file:/app/data/fault_log.db?mode=ro', uri=True); [print(r) for r in c.execute(\"SELECT operation,severity,exc_type,message,count,last_seen FROM faults WHERE component='market_history' AND message LIKE '%malformed%'\")]"`).
Record whatever `count`/`last_seen` this read returns before proceeding —
Task 10's live validation compares against this baseline. As of this
plan's own review (02:32Z), that baseline is `count=45,
last_seen=2026-09-02T20:44:11.57Z` (unchanged from the architecture
audit's original reading) — confirm it's still the same or note the
delta, don't assume it's still current by the time this task actually
runs.

- [ ] **Step 2: Run the read-only integrity check on both files**

Run (from the primary checkout, both files are the live, actively-written
originals — do not copy them first, `PRAGMA integrity_check` is read-only
by SQLite's own documented contract). The command below wraps the query in
`try/except`, unlike this plan's first draft, because `PRAGMA
integrity_check` was confirmed live to **raise** `sqlite3.DatabaseError`
directly on a corrupt file rather than return a corruption-description row
— a bare `.fetchall()` with no exception handling would crash this
diagnostic script itself on exactly the file it exists to check:

```
docker exec ddev-kalshi-whale-poc-fastapi python3 -c "
import sqlite3
for name in ('market_history.db', 'market_catalog.db'):
    print(f'--- {name} ---')
    conn = sqlite3.connect(f'file:/app/data/{name}?mode=ro', uri=True)
    try:
        for r in conn.execute('PRAGMA integrity_check').fetchall():
            print(r[0])
    except sqlite3.DatabaseError as exc:
        print(f'RAISED: {exc}')
    finally:
        conn.close()
"
```

Expected output, per file: exactly one line reading `ok` if the file is
structurally sound; a list of specific corruption descriptions (page
numbers, table names) if `PRAGMA integrity_check` completes but finds
damage; or a line starting `RAISED:` with the exception text if the check
itself cannot complete (this is what `market_history.db` actually did
when this command ran during this plan's own review — see above). Capture
the full output verbatim for both files either way.

- [ ] **Step 3: Record the result, decide, do not act automatically**

For `market_catalog.db`: if `ok` (confirmed clean as of this plan's own
review), record that in `docs/open-decisions.md`'s `markets_watched: 0`
line as evidence against the structural-corruption hypothesis for that
file (contention, not corruption, remains a separate, untested
possibility). If it now reports anything else, treat it with the same
"stop, do not restore" logic as `market_history.db` below — do not assume
it stays clean just because this plan's review found it clean once.

For `market_history.db`: given the evidence already gathered (raised
`DatabaseError` reproduced twice, count unchanged from the original
audit reading hours earlier), the honest default reading is **confirmed,
persistent corruption**, not "possibly transient" — but this task still
does not restore, delete, or modify the file automatically. Record the
full verbatim output (or, if re-running finds it unexpectedly clean this
time, record that too — an inconsistent result across runs would itself
be a new, notable finding, not assumed to mean the same thing as a
one-time clean read) in `docs/open-decisions.md` as an explicit line:
`services/backup/backup.py`'s `recent()`/`latest()` name the available
pre-incident snapshots, and whether/which one to restore from is a human
decision per this repo's `data/*.db` safety invariant — this plan does
not make that call or execute a restore.

---

### Task 9: File-descriptor visibility in `/api/health/pipeline` + an early-warning fault

**Files:**
- Modify: `services/diagnostics/routes.py` (imports, `get_pipeline_health`)
- Test: `tests/test_pipeline_health_cost.py` (extend existing file)

**Interfaces:** Adds one new top-level field to `/api/health/pipeline`'s
response, `"open_fds"`, and one new `fault_log` component
(`"fd_budget"`/`"approaching_limit"`) written once per breach of an 80%
threshold — chosen because it gives real lead time before the 1,024-fd
ceiling this repo's own live incident hit (2026-09-02), without being so
low it fires on ordinary variation; stated as an estimate for Task 10 to
confirm against real behavior, per the data-plane HARD RULE, same as
Task 1's timeout constant. Linux-specific (`/proc/self/fd`,
`resource.RLIMIT_NOFILE`) — acceptable because this app only ever runs in
the Linux ddev container, confirmed by `grep -rn 'RLIMIT\|proc/self'
services/` returning zero prior hits (this is a new pattern, not
inconsistent with an existing portable one).

- [ ] **Step 1: Write the failing test first**

Add to `tests/test_pipeline_health_cost.py`:

```python
def test_pipeline_health_reports_open_fd_count():
    """The 2026-09-02 fd-exhaustion incident had no visibility anywhere in
    this route until the container was already at its 1,024-descriptor
    ceiling. This field is the lead-time signal that incident had none of."""
    import main
    from fastapi.testclient import TestClient

    body = TestClient(main.app).get("/api/health/pipeline").json()

    assert isinstance(body["open_fds"], dict)
    assert isinstance(body["open_fds"]["count"], int)
    assert body["open_fds"]["count"] > 0
    assert isinstance(body["open_fds"]["soft_limit"], int)


def test_fd_budget_fault_fires_past_80_percent(monkeypatch):
    """Permanent recurrence detection for the incident's own root symptom -
    a fault_log row should exist before the ceiling is hit, not only after,
    unlike 2026-09-02's real incident which had zero warning. fault_log.record's
    real signature (services/fault_log.py:85-87, confirmed against current
    source before writing this test) is
    record(component, operation, exc: BaseException, context=None,
    severity="error", now=None) -> bool - there is no record_message."""
    import main
    from fastapi.testclient import TestClient
    from services import fault_log
    from services.diagnostics import routes

    monkeypatch.setattr(routes, "_current_fd_count", lambda: 900)
    monkeypatch.setattr(routes, "_fd_soft_limit", lambda: 1024)  # 900/1024 = 87.9%, over the 80% threshold

    recorded = []
    monkeypatch.setattr(fault_log, "record", lambda *a, **kw: recorded.append((a, kw)) or True)

    TestClient(main.app).get("/api/health/pipeline")

    assert recorded, "expected a fault_log.record() call once open fds crossed 80% of the soft limit"
    (component, operation, exc), kwargs = recorded[0]
    assert (component, operation) == ("fd_budget", "approaching_limit")
    assert kwargs.get("severity") == "warn"
```

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier0-live-incident-plan && python -m pytest tests/test_pipeline_health_cost.py -k 'open_fd_count or fd_budget' -q"`
Expected: **fails** — `open_fds` doesn't exist yet, and `get_pipeline_health`
doesn't call `fault_log.record` for this new component yet.

**Why the test patches the module-level `services.fault_log.record`, not
`routes.fault_log.record`:** `routes.py` does not import `fault_log` at
module scope anywhere (confirmed: `grep -n 'from services import fault_log'
services/diagnostics/routes.py` returns only local, inside-function imports
at lines 161, 173, 332, and 494 — the fourth, inside `get_faults()` as
`from services import fault_log as fl`, is the same pattern Task 7 relies
on) — Step 2's implementation follows that file's own existing convention
and imports `fault_log` locally inside `get_pipeline_health`, so there is
no `routes.fault_log` module attribute to monkeypatch; patching
`services.fault_log.record` itself is the correct
seam, matching how every other `fault_log.record` call in this file
already works.

- [ ] **Step 2: Implement**

Add near `STORE_PROBE_TIMEOUT_SEC` (Task 1):

```python
# Task 9 of docs/superpowers/plans/2026-09-03-tier0-live-incident-
# remediation.md: the 2026-09-02 fd-exhaustion incident had zero
# visibility anywhere until the container was already at its ceiling.
# 80% is an estimate - enough lead time to notice before the 1,024-fd
# limit this incident actually hit, without firing on ordinary variation;
# Task 10's live validation is where that gets checked against real
# behavior, not assumed correct on landing.
FD_BUDGET_WARN_FRACTION = 0.8


def _current_fd_count() -> int:
    return len(os.listdir("/proc/self/fd"))


def _fd_soft_limit() -> int:
    return resource.getrlimit(resource.RLIMIT_NOFILE)[0]
```

Add `import os` and `import resource` to the top-level imports (both
stdlib, alphabetically ordered with the existing `import asyncio` /
`import time`, before the `from fastapi import ...` line). Inside
`get_pipeline_health`, before the `return {...}`, add:

```python
    from services import fault_log

    fd_count = _current_fd_count()
    fd_limit = _fd_soft_limit()
    if fd_limit and fd_count / fd_limit >= FD_BUDGET_WARN_FRACTION:
        fault_log.record(
            "fd_budget", "approaching_limit",
            RuntimeError(f"{fd_count}/{fd_limit} file descriptors in use"),
            severity="warn",
        )
```

and add `"open_fds": {"count": fd_count, "soft_limit": fd_limit},` to the
returned dict.

- [ ] **Step 3: Confirm the tests pass**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier0-live-incident-plan && python -m pytest tests/test_pipeline_health_cost.py -q"`
Expected: every test in the file passes, including both new ones.

---

### Task 10: Full regression suite + required live validation

**Not a code task** — the empirical confirmation this plan's fixes actually
resolve the live symptoms, per this repo's own standing practice (matching
every precedent plan's own final task, e.g.
`docs/superpowers/plans/2026-09-01-event-loop-blocking-fix1.md`'s Task 5).

**Note on this plan's own trigger evidence going stale between review
passes:** by the time this plan's PR-stage review ran (2026-09-03,
02:59-03:00Z, roughly 25-50 minutes after the numbers cited throughout
this plan and its own artifact-stage review were gathered), both
`GET /api/health/pipeline` (0.19s) and `GET /api/health/faults` (0.036s)
were responding quickly, not hanging. This does not mean the underlying
defects aren't real — the missing per-item timeout in `asyncio.gather()`
(Task 1) and the fully-synchronous, no-thread-hop SQLite calls (Task 7)
are confirmed present in current source regardless of whether they are
manifesting as a hang at any given moment — but it does mean the
"currently stuck" / "live incident in progress" framing used throughout
this plan (drafted from a specific incident window) is a historical
trigger, not a guaranteed live symptom whoever executes this plan will
still be able to reproduce on demand. Steps 1-3 below already require
re-measuring live before drawing any conclusion; do not skip that
re-measurement on the assumption the numbers cited earlier in this
document are still current.

- [ ] **Step 1: Run the full local test suite**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier0-live-incident-plan && python -m pytest tests/ -q -m 'not slow'"`

Expected: same pass count as the pre-existing baseline (confirm the current
baseline fresh — `git log -1 --format=%h` on `main` — rather than trusting
a number from an earlier session, since this repo's suite size changes
PR to PR) plus this plan's 9 net new tests (Task 1 nets 1 — Step 1's
placeholder is replaced by Step 5's real assertion, not kept alongside it;
4 in Tasks 2-5, one per module; 1 in Task 6; 1 in Task 7; 2 in Task 9), all
passing. Any other failure is a real regression — investigate before
proceeding, per `superpowers:systematic-debugging`.

- [ ] **Step 2: `import main` sanity check**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier0-live-incident-plan && python -c 'import main' && echo IMPORT_OK"`
Expected: `IMPORT_OK` — no import errors from `contextlib` (new in the
five modules Tasks 2-6 touch: `market_history.py`, `title_cache.py`,
`market_catalog.py`, `signal_log.py`, `fault_log.py`) or `os`/`resource`
(new in `services/diagnostics/routes.py`, Task 9).

- [ ] **Step 3: Deploy and re-measure `/api/health/pipeline` and `/api/health/faults`**

After this branch merges and the primary checkout's running app reloads it
(`git merge origin/main` on primary, confirm via `docker logs --timestamps
ddev-kalshi-whale-poc-fastapi` that WatchFiles picked up the changed files
and the server restarted), immediately probe both routes this plan exists
to fix:

```
curl -sk -m 90 -w '\nhttp=%{http_code} total=%{time_total}s\n' "https://kalshi-whale-poc.ddev.site:8443/api/health/pipeline"
curl -sk -m 90 -w '\nhttp=%{http_code} total=%{time_total}s\n' "https://kalshi-whale-poc.ddev.site:8443/api/health/faults?component=market_history&limit=15"
```

**Pass/fail criterion, stated precisely — do not conflate two different
claims** (this plan's own adversarial review found live evidence, taken
immediately before this task was finalized, that only ~5 of the route's
~24-28 second total response time is inside the specific block Task 1
bounds — `stores_probe_ms` read 4653.0ms and 5004.79ms on two probes,
while total server-side time, isolated from network/TLS via `curl`'s own
`time_starttransfer`, was 23.9-28.1s):

1. **Task 1's own claim, checkable precisely**: the response body's
   `stores_probe_ms` field stays under `STORE_PROBE_TIMEOUT_SEC × 1000`
   (10,000ms) plus reasonable overhead, and no store's slot shows a
   permanent, repeating `"error": "timed out after 10s"` across multiple
   probes (a one-time timeout during unusual load is not itself a
   failure; the same store timing out on every subsequent poll is the
   "permanently stuck" case Task 1's own Architecture section already
   flags as a new, separately-scoped investigation).
2. **Total route latency is a separate, still-open question this plan
   does not claim to close.** If `stores_probe_ms` is bounded but total
   response time is still tens of seconds, that time is being spent
   *outside* the block Task 1 wraps (candidates named live during this
   plan's own review: `strategy_engine.me_gate_stats()`,
   `mutual_exclusivity.me_pairing_stats()`, `http_client.rest_latency_snapshot()`,
   `capture_writer.loss_snapshot()`, or scheduling delay before the
   handler starts) — record the actual number in `docs/next-action.md`
   as a new, explicitly separate finding, not a failure of this task.
   Do not read "the route is still slow" as evidence Task 1 didn't work;
   check `stores_probe_ms` specifically before drawing that conclusion.
3. **For `/api/health/faults` (Task 7's fix)**: total response time should
   drop from the ~45s this plan's own review measured, though the same
   caveat applies — Task 7 stops the route from blocking the *whole
   process* while `fl.summary()`/`fl.recent()` run, it does not make
   those two queries themselves faster (their own cost is unmeasured and
   out of this task's scope, same distinction as Task 1). A `curl`
   against `/api/state` (a route neither task touches) run *concurrently*
   with the `/api/health/faults` probe, both returning promptly, is the
   real signal Task 7 worked — the process didn't stall for everyone
   else while this one route was slow.

- [ ] **Step 4: Watch the fd count for at least 3 hours, not a spot check**

`docker exec` the fd census command used throughout this plan's own
research (`ls /proc/<worker-pid>/fd | wc -l`, worker pid found via `for q
in $(ls /proc | grep -E '^[0-9]+$'); do c=$(tr '\0' ' ' < /proc/$q/cmdline
2>/dev/null); case "$c" in *spawn_main*) echo $q;; esac; done`) at least
three times across 3+ hours of the live worker's uptime (the second-pass
audit's own recurrence evidence was ambiguous at 1-2 samples; do not repeat
that mistake here). Expected: the count stays roughly flat over time
instead of climbing without bound — some growth from ordinary connection
churn is fine, unbounded climb toward the 1,024 soft limit is not. If
`open_fds` (Task 9) shows the count still climbing steadily hours after
this deploy, Tasks 2-6's five modules were not the only real contributors
and the remaining 21 modules the audit found with the same shape (Tier 2,
out of this plan's scope) need to move up in priority — record that
finding in `docs/open-decisions.md`, don't silently re-scope this plan to
cover them.

- [ ] **Step 5: Re-check `markets_watched` and the `market_history.db`
      fault count against Task 8's baseline**

Run: `curl -sk -m 30 "https://kalshi-whale-poc.ddev.site:8443/api/health/pipeline"`
and read `markets_watched` and `price_staleness`. Expected (not
guaranteed by this plan, since none of its tasks directly targets the
`markets_watched: 0` mechanism — see the Architecture section's honest
scoping note on Task 4/market_catalog.py): if `markets_watched` recovered
on its own once the route stopped hanging, record that as evidence for
§4.7's "genuinely nothing open right now, masked by the hung route"
hypothesis; if it's still 0 with the route now responding normally, that
rules out the hung-route explanation. Task 8 already ruled out structural
corruption on `market_catalog.db` (confirmed `ok`) as of this plan's own
review — if `markets_watched` is still 0 despite that, the remaining
hypothesis is contention or something this plan's own investigation did
not reach, and it needs its own follow-up, not assumed fixed by this
plan. Separately, using Task 8 Step 1's recorded baseline `count`/
`last_seen` for the `malformed` fault: if the count has grown since, the
corruption mechanism is still actively producing new faults regardless of
what Task 8's integrity check found, and that too is a new finding for
`docs/open-decisions.md`, not something this plan closes by itself.

- [ ] **Step 6: Record the result**

Write the outcome into `docs/next-action.md` (replacing this plan's
current Tier-0 entry) and `docs/open-decisions.md` for anything that
remains open per Steps 3-5 above — a hung store still unidentified, the
route's total-latency gap (Step 3) still unexplained, fd count still
climbing, `markets_watched` still 0, or new `malformed` faults since Task
8's baseline are all real, separate findings this plan's own scope does
not close, and each needs its own next-step line rather than being
silently left unrecorded.

---

## Plan self-review

**Research coverage:** The second-pass audit's §4.1 (fd exhaustion,
`market_history.db` corruption) and §4.7 (`markets_watched: 0`) map onto
Tasks 1-8 directly. This plan also incorporates one thing the audit itself
did not have: a fresh live re-verification (done immediately before
drafting, 2026-09-03 02:09-02:15 UTC) that found the fd leak *cannot* be
what's currently hanging `/api/health/pipeline` (the live worker had only
216 of 1,024 descriptors in use at the time), which the audit's own
hours-old numbers would not have caught — Task 1 is scoped around that
corrected understanding, not the audit's original fd-exhaustion framing
alone.

**Placeholder scan:** Every code block in Tasks 1-9 is either a verbatim
transcription of currently-read source or a concrete new implementation,
not a sketch — the one exception is Tasks 3-4 (`title_cache.py`,
`market_catalog.py`), whose `_connect()` bodies are longer than what this
plan's own research pass transcribed in full (both have inline
multi-statement `CREATE TABLE` DDL beyond what's quoted in this plan's
Architecture section) — both tasks' own Step 2 explicitly instructs
reading the real, current full body before editing rather than assuming
this plan's partial excerpt is complete. This is the same
disclosed-scoping-decision shape the precedent plan's self-review used for
its own two under-verified functions, not a silently-hidden gap.

**Type/interface consistency:** All five of Tasks 2-6's `_connect()`
functions change identically — `def _connect(...) -> sqlite3.Connection:`
returning a raw connection becomes `@contextlib.contextmanager\ndef
_connect(...):` yielding one, with `try: with conn: yield conn; finally:
conn.close()` as the closing body — and every task's own Step 1 test
follows the identical structure (a `_tracking_connect` wrapper recording
`close()` calls, matching `tests/test_pipeline_health_cost.py`'s own
existing `_record(monkeypatch)`/`_RecordingConnection` pattern for
`store_stats.py`, reused rather than reinvented). `fault_log.record`'s real
signature (confirmed against `services/fault_log.py:85-87` before writing
Task 9) is used identically in both Task 9's implementation and its test.

**Scope boundary, stated plainly:** this plan fixes the fd leak in five
confirmed modules, bounds two routes that were each blocking the process
in a different way (Task 1: a hung probe inside a timeout-free `gather()`;
Task 7: fully synchronous blocking SQLite with no thread hop at all), adds
fd visibility, and gets a definitive read on two files' corruption status.
It does not fix the remaining 21 modules with the same connection-leak
shape (Tier 2), does not identify which specific store is causing
`/api/health/pipeline`'s remaining latency (Task 1 bounds and diagnoses
the store-probe portion specifically; Task 10 Step 3 is explicit that the
rest of the route's latency is a separately-scoped, still-open question,
not something Task 1 was ever positioned to close), and does not resolve
`markets_watched: 0` (Task 10 Step 5 checks whether it self-resolves once
the route responds again, but no task in this plan directly targets that
mechanism). Each of these is named explicitly in this plan rather than
implied as solved.

### Revision round after this plan's own adversarial review (2026-09-03)

This plan went through the independent adversarial review the "nothing
advances on one pass" HARD RULE requires
(`docs/superpowers/plans/2026-09-03-tier0-live-incident-remediation-plan-review.md`,
verdict GO-AFTER-FIXES) before being considered ready. Every item on that
review's must-fix and should-fix list was applied — most visibly, Task 1's
`_bounded()` timeout-resolution bug fixed, Tasks 6-7 added
(`fault_log.py`'s own connection leak and `GET /api/health/faults`'s
event-loop-blocking fix, since the plan's own diagnosis already named that
route as stuck), Task 8 rewritten with the real, already-executed
integrity-check finding (`market_history.db`: confirmed corrupt,
reproduced twice; `market_catalog.db`: confirmed clean), and Task 10's
pass/fail criterion split into two separately-stated claims rather than
one that conflated them. The full fix list, what was applied against each
item, and why this revision did not trigger a third independent review
cycle are recorded in the companion document,
`docs/superpowers/plans/2026-09-03-tier0-live-incident-remediation-consolidation.md`.
