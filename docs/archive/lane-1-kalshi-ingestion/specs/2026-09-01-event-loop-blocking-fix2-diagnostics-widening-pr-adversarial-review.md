# PR Adversarial Review — Event-Loop-Blocking Fix 2 (Diagnostics Widening)

Branch: `fix/aiosqlite-diagnostics-widening`, merge-base `4493a67` (origin/main), HEAD `1f24571`.
13 commits, 18 files, +2532/-312. Reviewed 2026-09-01.

Independent pass per CLAUDE.md's "nothing advances on one pass" HARD RULE. No memory of how
this branch was built; every load-bearing claim below was re-derived from the diff, the
current source in this worktree, the installed `aiosqlite`/`uvicorn`/CPython sources in the
`fastapi` container, live measurements against the real `data/*.db`, and executed test runs.
The self-review was read only after forming an independent view.

---

## Summary verdict: **NO-GO**

The async conversion itself is mechanically sound — I read three converted functions end to
end and found no change to any SQL text, computed value, return shape, or control flow, and
every one of the 15 newly-async functions has every call site in the repo correctly
`await`ed. But the new shared connection cache introduces a **resource-lifetime defect that
already breaks this repo's own test tooling today**: `aiosqlite` runs one **non-daemon** OS
thread per connection, `_aio_db` never closes the connections it caches, and nothing in
`main.py`'s lifespan or in `tests/test_diagnostics_routes.py` cleans them up — so a plain
interpreter exit hangs forever. `python -m pytest tests/test_diagnostics_routes.py` on this
branch reports "7 passed in 0.30s" and then **never exits** (reproduced, killed at 120s).
The full suite only exits today by accident of alphabetical collection order, which CI's
`-n 4` xdist scheduling and testmon test selection do not preserve. That alone is a blocker.
Beyond it, the cache silently drops the reconnect-on-failure behaviour the design spec
explicitly required and that the sibling module it claims parity with actually has; it leaks
a connection *and* a non-daemon thread on any `schema_init` failure; its central design
justification is factually wrong for the pinned `aiosqlite` version; and the change moves
~280ms+ of measured pure-Python aggregation from a worker thread **onto** the event loop
without measuring it, on a route that currently takes 20.4s wall and is polled every ~5s.

---

## Findings

### Critical

#### C1 — Leaked non-daemon `aiosqlite` worker threads hang interpreter exit; `tests/test_diagnostics_routes.py` already hangs forever

**Files:** `services/diagnostics/_aio_db.py:77-89` (caches, never closes), `main.py:1374-1396`
(lifespan shutdown has no `_aio_db` cleanup), `tests/test_diagnostics_routes.py` (new tests,
no `_reset_aio_db_cache` fixture).

**Mechanism, verified from primary sources:**

1. `aiosqlite/core.py` (0.22.1, read in-container):
   `self._thread = Thread(target=_connection_worker_thread, args=(self._tx,))` — **no
   `daemon=True`**. The thread loops on `tx.get()` until it receives `_STOP_RUNNING_SENTINEL`,
   which only `Connection.stop()`/`close()` ever sends.
2. `_aio_db.connection_for()` stores every connection in the module-global `_connections`
   and nothing in production code ever closes them (`close_for_current_loop()` has exactly
   one caller, `services/research/research.py:203`, on its own throwaway loop).
3. `/usr/local/lib/python3.13/multiprocessing/process.py:329` and CPython's normal
   finalisation both call `threading._shutdown()`, which **joins every non-daemon thread**.

**Deterministic reproduction (the only difference between the two runs is the `close` call):**

```
# leaves connections cached (what the app and tests do today)
$ python /tmp/probe_exit.py
THREADS after opening 3 cached conns (name, daemon): [('MainThread', False),
 ('Thread-1 (_connection_worker_thread)', False), ('Thread-2 (...)', False), ('Thread-3 (...)', False)]
about to exit WITHOUT closing
EXITCODE=124 ELAPSED_MS=30005          <-- killed by `timeout 30`; never exited

# identical, plus `await _aio_db.close_for_current_loop()`
$ python /tmp/probe_exit_control.py
THREADS after close: [('MainThread', False)]
EXITCODE=0 ELAPSED_MS=175
```

**This already bites the test suite** (all runs in the `fastapi` container, worktree
checkout, `aiosqlite` on `PYTHONPATH`):

```
tests/test_diagnostics_routes.py  rc=124 elapsed_ms=124370  7 passed in 0.30s   <-- HUNG
tests/test_quality_routes.py      rc=0   elapsed_ms=3656    6 passed
tests/test_research.py            rc=0   elapsed_ms=3550   17 passed
```

and it is **collection-order dependent**, which is the part that makes it dangerous:

```
pytest tests/test_diagnostics_routes.py tests/test_series_watcher.py  -> rc=0   ms=8210   39 passed
pytest tests/test_series_watcher.py tests/test_diagnostics_routes.py  -> rc=124 ms=124690 39 passed  <-- HUNG
```

`tests/test_diagnostics.py`, `tests/test_series_watcher.py` and
`tests/test_main_tick_executor_wiring.py` each carry an autouse `_reset_aio_db_cache`
fixture; `tests/test_diagnostics_routes.py` — the file **this branch added**, and the only
one that drives the routes end to end — does not. The full suite passes today
(`3052 passed, 16 skipped in 119.57s` at `-n 4`) purely because some later file's `reset()`
happens to clean up after it.

**Why that is not safe to rely on:** `.woodpecker/tests-pytest.yml` runs `-n 4`
(pytest-xdist, `--dist load` — dynamic, non-deterministic assignment of tests to worker
processes) **and** pytest-testmon (runs only the tests a change affects). A worker process
that ends its batch on `test_diagnostics_routes.py`'s tests never exits; a testmon-selected
subset triggered by editing `services/diagnostics/routes.py` selects that file and little
else. The per-edit `.claude/hooks/run_tests.py` hook running that one file is a guaranteed
hang.

**Production scope — I suspected an app-shutdown outage and falsified it.** `main.py`'s
lifespan shutdown (`main.py:1374-1396`) closes streams, cancels tasks, stops
`capture_writer`, closes both HTTP clients, and never touches `_aio_db`. But
`uvicorn/server.py:313-330`'s `capture_signals()` restores the original SIGTERM handler and
then `signal.raise_signal(SIGTERM)`, killing the process outright before finalisation:

```
$ python /tmp/probe_sigterm.py     # replays uvicorn's exact sequence with 3 leaked conns
captured: [15] live threads: ['MainThread', 'Thread-1 (_connection_worker_thread)', ...]
Terminated
EXITCODE=143 ms=330
```

So `ddev restart`, `docker stop`, Ctrl-C and `--reload`'s `process.terminate()` are **not**
affected. The exposure is plain interpreter exit — i.e. pytest, and any future non-signal
exit path. That does not reduce C1 below Critical: an order-dependent CI hang and a
guaranteed hang for the repo's own targeted-test workflow is a blocker on its own.

**Notable:** the self-review's Task 5 bullet (lines 38-40) says this branch already
"root-caused (not papered over) a genuine leaked-non-daemon-thread test hang via a
`_reset_aio_db_cache` fixture." The mechanism was therefore known, diagnosed as a *test*
problem, fixed in three test files — and not applied to the one test file this same branch
added, nor to `_aio_db`'s own lifecycle.

**Suggested fixes (any one closes the test hang; the first two also close the general case):**
give `_aio_db` an `atexit`/`threading._register_atexit` shutdown hook the way
`concurrent.futures.thread` already does for `_diagnostics_pool`'s executor; or set the
worker threads daemon; or, minimally, add the `_reset_aio_db_cache` fixture to
`tests/test_diagnostics_routes.py` and hoist it into `tests/conftest.py` so no future test
file can reintroduce it.

---

### Important

#### I2 — `schema_init` failure leaks both a connection and a non-daemon thread, once per call

**File:** `services/diagnostics/_aio_db.py:81-89`

```python
async with _lock_for_current_loop():
    conn = _connections.get(key)
    if conn is None:
        conn = await aiosqlite.connect(db_path)
        conn.row_factory = aiosqlite.Row
        if schema_init is not None:
            await schema_init(conn)      # <-- if this raises...
        _connections[key] = conn         # <-- ...we never get here
    return conn
```

If `schema_init` raises, the connection is neither cached nor closed, so its worker thread
(non-daemon, per C1) lives for the rest of the process. `aiosqlite` handles its *own*
connect failure (`_connect()`'s `except BaseException: self.stop()`), but this path is
outside that.

**Failure scenario:** `series_watcher.db` becomes unreadable (disk-image malformed, permission
change, a `data/` mount problem). `_ensure_schema_aio` raises `sqlite3.DatabaseError`,
`funnel()`/`capture_stats()`/`book_context_at_entry()` degrade honestly to their
`except sqlite3.Error` branch — and each leaks one thread. `funnel()` runs once per watched
series (8 today, from the live `/api/quality/summary` response) per `run_offline()`, on a
route the dashboard polls every ~5s. That is on the order of 8 leaked OS threads and
connections every 5 seconds, indefinitely, with no error surfaced beyond a normal "watcher
store unreadable" string.

**Fix:** wrap `schema_init` in `try/except` and `await conn.close()` before re-raising.

#### I1 — No liveness check or reconnect; the docstring's parity claim with `_scoring_pool` is false, and the design spec's reconnect requirement is unimplemented

**Files:** `services/diagnostics/_aio_db.py:65-89` vs `services/whalewatchers/_scoring_pool.py:50-57`

`connection_for`'s docstring says its contract is "same contract as
`services/whalewatchers/_scoring_pool.py`'s `cached_read_connection()`". That function is:

```python
conn = cache.get(db_path)
if conn is not None:
    try:
        conn.execute("SELECT 1")
        return conn
    except sqlite3.ProgrammingError:
        del cache[db_path]  # evict a connection closed out from under us elsewhere, fall through to reopen
```

`_aio_db.connection_for` is `conn = _connections.get(key); if conn is not None: return conn`
— no liveness probe, no eviction, no reopen. The contracts differ in exactly the
self-healing dimension.

The design spec this PR implements names this requirement explicitly
(`2026-09-01-event-loop-blocking-elimination-design.md:229-235`): *"a broken/closed
`aiosqlite.Connection` needs its own reconnect-on-failure test, not a copy-paste of the old
thread-local recovery logic."* There is no such test and no such logic.

**Two concrete consequences:**

1. **Lost self-healing.** Before this branch every call did its own
   `sqlite3.connect()`, so any transient breakage healed on the next call. Now a broken
   cached connection is returned forever, and because every caller wraps its query in
   `except sqlite3.Error`, the diagnostics subsystem would report `unknown` /
   "signal_log unreadable" **permanently and quietly** rather than failing loudly — the
   exact silent-degradation shape `diagnostics.py`'s own module docstring says it exists to
   avoid ("this module exists to be trusted when other numbers are in doubt"), and a
   data-plane HARD RULE informativeness regression.
2. **A closed connection escapes the degradation path entirely.** `aiosqlite`'s `_execute`
   raises `ValueError("Connection closed")`, which is **not** a `sqlite3.Error`, so it
   bypasses every `except sqlite3.Error` block and surfaces as a 500 from
   `/api/quality/summary`, `/api/diagnostics` and `/api/diagnostics/series/{s}`.

I could not construct a high-probability production trigger for a *cached* connection going
bad (nothing in `services/`, `main.py` or `tools/` deletes, renames, moves or copies a
`data/*.db` at runtime — grepped for `.unlink(`/`os.remove`/`shutil.move`/`rename(`, zero
non-test hits — and `POST /api/reset` uses `DELETE FROM`, not file replacement, so
post-reset staleness is impossible). Labelled accordingly: the defect is a **stated
contract that is false and a spec requirement that is unmet**, not a demonstrated live bug.

#### I3 — `_aio_db`'s central design justification is factually wrong for `aiosqlite==0.22.1`

**File:** `services/diagnostics/_aio_db.py:16-29` (and the same claim in the plan's
Architecture section and Task 1)

The docstring states: *"An `aiosqlite.Connection` is bound to the event loop that created it;
handing a connection opened under the main app's long-lived loop to code running under a
different, temporary loop (or vice versa) is a real correctness hazard, not a hypothetical -
aiosqlite's internal read/write queue is loop-bound."* The plan (line 68 and Task 1) says
this was "confirmed against the library's own source via Context7, not recalled from memory."

Read against the installed `aiosqlite/core.py` 0.22.1, all three parts are false:

```python
# Connection.__init__
if loop is not None:
    warn("aiosqlite.Connection no longer uses the `loop` parameter", DeprecationWarning)

# Connection._execute  -- the future is created on whatever loop is CALLING
future = asyncio.get_event_loop().create_future()
self._tx.put_nowait((future, function))

# _connection_worker_thread  -- results are delivered to THAT future's loop
future.get_loop().call_soon_threadsafe(set_result, future, result)
```

The transport is a plain `SimpleQueue` on a plain `Thread`; the version explicitly removed
loop binding. Loop-scoping the cache is still the right call — for **lifetime** reasons (a
connection keyed only by path would be closed out from under the main loop by research.py's
`close_for_current_loop()`, and a dead loop's entries could never be cleaned up) — but the
mechanism given is not the real one, and CLAUDE.md's never-guess HARD RULE is explicit that
"a gap filled with a guess is read as fact by the next session." Correct the docstring, the
plan, and the design spec to the lifetime rationale.

#### I4 — The conversion moves measured pure-Python work from a worker thread *onto* the event loop, unmeasured

`run_offline()` previously ran in its entirety on `_diagnostics_pool`'s worker thread
(`await _diagnostics_pool.run(lambda: diagnostics.run_offline(cfg))`). It now runs on the
event loop, with only the SQL (via aiosqlite) and two explicitly wrapped calls
(`trade_category.categories_for_tickers`, `signal_log.resolved_signals_with_factors`) off it.
Every aggregation loop between a fetch and its `Check` has **no `await` point**, so it holds
the loop for its full duration.

Measured against the real `data/*.db` (read-only, `mode=ro` URIs), after first reading
`GET /api/quality/summary` per CLAUDE.md's investigation order:

| check | rows | I/O (off-loop) | pure-Python (now **on-loop**) |
|---|---|---|---|
| `check_threshold_integrity` | 23,957 | 64ms | **78ms** |
| `selectivity_curve` | 50,711 | 112ms | **86ms** |
| `check_confidence_input_coverage` | 124,859 | 1,043ms | **89ms** |
| `trade_analytics.build_trade_history` | 621 (one series) | — | **1.5ms × 16 calls ≈ 24ms** |

That is ≥ ~280ms of measured, contiguous, un-awaited on-loop CPU per call, with
`check_price_band_adherence`, `check_runway_at_entry`, `performance_by_epoch` and both
`funnel()`/`reconcile()` aggregations unmeasured on top. The live route currently takes
**20.4s wall** (`curl` against the running app, 2026-09-01) and the dashboard polls it every
~5s.

This is a genuine improvement for `GET /api/diagnostics` and `GET /api/diagnostics/series/{s}`,
which previously ran all 20s on the loop. It is a **regression** for `GET /api/quality/summary`,
which previously contributed 0ms of on-loop time. Neither direction was measured before
shipping, which is what CLAUDE.md's data-plane HARD RULE forbids ("Any diagnostic or
abstraction on the exchange-wide hot path is measured for runtime cost before it ships";
"never change ... because it 'should help'"). Not necessarily a blocker on the merits —
~90ms blocks are two orders of magnitude better than the 13-minute stall the spec was
fixing — but it must be measured and stated as an explicit tradeoff, not left implicit.

#### I5 — One connection per DB file halves `run_offline`'s concurrency, against the design spec's own stated reasoning

**File:** `services/diagnostics/_aio_db.py:8-14`

`aiosqlite` serialises every operation on a connection onto that connection's single worker
thread. `_aio_db` keeps exactly one connection per `(loop, db_path)`. The design spec is
explicit that this is the property to watch, and chose otherwise for the sibling pool
(`...elimination-design.md:215-224`): *"a single connection serializes all operations against
that file onto one thread, so matching or exceeding today's throughput still means opening
more than one connection per file (hence 2, not 1, for the scoring pool above)."*

`_diagnostics_pool` gave `run_offline()` 2 concurrent workers. With `/api/quality/summary`
measured at 20.4s against a ~5s poll, roughly four requests are in flight at all times;
they now interleave on one thread per file rather than queueing on two workers, so
per-request latency degrades under overlap even though aggregate throughput is roughly
unchanged. The module docstring reasons about this honestly and names the 2-per-file escape
hatch, but the "a single connection per file is enough headroom here" conclusion is asserted,
not measured — again the specific thing the data-plane HARD RULE names ("never change ...
connection/thread counts ... because it 'should help': identify the measured bottleneck and
its mechanism first").

#### I6 — Merging this takes the running app down until the container image is rebuilt; no ordering is recorded anywhere

- `Dockerfile:17-25` — `COPY requirements.txt requirements-dev.txt ./` then
  `RUN pip install --no-cache-dir -r requirements-dev.txt`. The dependency is **baked at
  image build time**, and `.ddev/docker-compose.fastapi.yaml:18-20` builds from the
  **primary** checkout (`context: ..`).
- `main.py:31` — `from services.diagnostics import diagnostics` at module import →
  `diagnostics.py:47` `from services.diagnostics import _aio_db` → `_aio_db.py:43`
  `import aiosqlite`, all at import time.
- Confirmed in-container: `python -c "import aiosqlite"` → `ModuleNotFoundError`, with or
  without `HOME=/tmp`; `pip show aiosqlite` → "Package(s) not found"; a filesystem-wide
  `find` for an `aiosqlite` package directory returns nothing.
- uvicorn runs with `--reload` watching the `../:/app` bind mount.

So the instant the primary checkout pulls `main` after this merges, watchfiles fires a
reload, the child re-imports `main`, and the app is hard-down with `ModuleNotFoundError`
until someone rebuilds the image.

The self-review's gap 3 correctly identifies that the dependency isn't in the container, but
frames the consequence purely as "the live smoke test must be deferred" and rules to defer
it. It never states that the merge itself is an outage window, and no rebuild-before-or-with-
merge instruction exists in the plan, the branch, or `docs/next-action.md`.
`.claude/rules/branching-and-ci.md` is explicit that an unchecked post-merge item must
either be done before merging or have its executor and timing named before merging.

---

### Minor

- **M1 — Two dead imports in production code.** `services/diagnostics/diagnostics.py:37` and
  `services/series_watcher.py:64` both still carry `from contextlib import closing`; `grep -n
  "closing("` returns zero hits in either file. The self-review names only a dead `Path`
  import in a *test* file and misses both of these. (`Path` in `series_watcher.py:66` is
  still live — used at line 78 for `DB_PATH`. `Path` in `_aio_db.py:40` is live in the type
  annotations.)
- **M2 — Stale rationale in `services/research/research.py:196-200`.** The comment justifies
  the `try/finally` with *"CPython can later reuse this dead loop's freed `id()` for an
  unrelated new loop."* Commit `c0c22a4` changed the cache key from `id(loop)` to the loop
  **object**, which (as `_aio_db.py:25-29` correctly says) holds a strong reference and makes
  id-reuse structurally impossible. The `try/finally` is still correct and worth keeping —
  without it a failed cleanup pins the dead loop object, its connection and its non-daemon
  thread forever — but the stated reason is wrong.
- **M3 — Unsynchronised dict iteration across threads (theoretical).**
  `_aio_db.close_for_current_loop():101` builds `[key for key in _connections if key[0] is loop]`
  while another thread's loop can be executing `_connections[key] = conn` at line 88, which
  can raise `RuntimeError: dictionary changed size during iteration` inside research.py's
  `finally` (masking the report and skipping cleanup). **I could not reproduce this** in
  ~1,000 forced attempts across two probes, including with `sys.setswitchinterval(1e-6)` and
  a churner thread inserting a unique key per iteration — 0 errors both times. It is also
  self-limiting in production: after startup the main loop's five paths are all cached and it
  stops inserting. Reported as an unproven thread-safety gap, explicitly labelled as such,
  not as a live bug. A `threading.Lock` around the two dict mutations, or snapshotting with
  `list(_connections)`, would close it for free.
- **M4 — Order-dependent test.** `tests/test_aio_db.py`'s
  `test_locks_are_scoped_per_loop_not_shared_across_loops` asserts
  `len(_aio_db._locks) == 2` — an absolute count on a module global. It passes today only
  because `test_aio_db.py` sorts first; any test file that touches `_aio_db` without calling
  `reset()` and runs before it (under xdist, `-k`, or a future rename) breaks it.
- **M5 — One near-vacuous test.**
  `test_connection_for_without_schema_init_does_not_require_a_preexisting_table` asserts only
  `conn is not None`. It would pass against almost any implementation, including a badly
  broken one. (The other six in the file are genuinely non-vacuous — see below.)
- **M6 — Undocumented behavioural change.** `_connect()` does
  `DB_PATH.parent.mkdir(exist_ok=True)`; `_ensure_schema_aio` and `_aio_db.connection_for`
  do not. With `data/` missing, `capture_stats()` now returns
  `{"error": "unable to open database file"}` where it previously self-healed and returned
  zeros. Arguably the more honest behaviour, but it is a change and it is unnoted.
- **M7 — Plan committed with every checkbox unchecked, and one follow-up genuinely undone.**
  The merged plan doc has 49 `- [ ]` and 0 `- [x]`, despite Tasks 1-6 being implemented, so
  it misrepresents its own state. Task 7 Step 6 (add the `check_series_funnel` double-compute
  entry to `docs/open-decisions.md`) is genuinely not done:
  `git diff 4493a67..HEAD --stat -- docs/open-decisions.md` is empty and
  `grep -n "check_series_funnel\|aiosqlite" docs/open-decisions.md` returns nothing.
- **M8 — Process note.** This PR-stage review cycle is running against an unpushed branch:
  `git ls-remote --heads origin fix/aiosqlite-diagnostics-widening` is empty and
  `gh pr list --head ...` returns `[]`. CLAUDE.md's HARD RULE defines the PR cycle as running
  "after it's pushed and opened ... against the PR as submitted." Re-confirm CI status
  against the real pushed SHA before merging, per `.claude/rules/branching-and-ci.md`.

---

## Self-review assessment

**Gap 1 — "`tests/test_diagnostics.py:12` — `from pathlib import Path` is now a dead import.
Cosmetic, no CI lint catches it."**
**Accurate but incomplete.** `Path` at `tests/test_diagnostics.py:12` is indeed the only
occurrence in the file (`grep -n "Path" tests/test_diagnostics.py` → one hit), and the "no CI
lint" claim checks out: no ruff/flake8/pylint/pyflakes step exists in `.woodpecker/*.yml` or
`requirements-dev.txt`. What it misses is that the same branch left **two dead imports in
production code** — `from contextlib import closing` in `services/diagnostics/diagnostics.py:37`
and `services/series_watcher.py:64`, both orphaned by this exact conversion (see M1). Naming
the cosmetic test-file one while missing the two in `services/` inverts the priority.

**Gap 2 — "`check_series_funnel` computes `reconcile()` and `funnel()` separately, doing its
core DB work twice per call. A real, measured compute-redundancy cost, explicitly out of this
plan's scope ... needs a `docs/open-decisions.md` line (Task 7 Step 6, not yet done)."**
**Substantively correct, with two corrections.** The redundancy is real and I confirmed it in
source: `check_series_funnel` (`services/series_watcher.py:986,1045`) awaits `reconcile()`
and then `funnel()`, and each independently calls `_signals_for_series()` **and**
`_trades_for_series()` — so both queries run twice per series, ×8 watched series per
`run_offline()`. Two corrections: (a) it is **pre-existing**, not introduced here — the
pre-branch code had the identical `evidence=funnel(...)["stages"]` shape, which the self-review
does not say; (b) calling it "measured" overstates it — I found no measurement of it anywhere
in the branch. It is also now *more* relevant than the self-review allows, because all of that
doubled work is serialised onto one shared connection per file (I5) rather than spread across
per-call connections. And the follow-up it names is still outstanding: nothing on this branch
touches `docs/open-decisions.md` (see M7).

**Gap 3 — "The container's real Python image does not have `aiosqlite` installed and cannot,
until this PR merges to `main` ... Ruling: defer the live smoke test to after this PR merges."**
**Accurate on the facts, materially undersells the consequence.** I independently confirmed
the dependency is absent (`import aiosqlite` → `ModuleNotFoundError`, `pip show` → not found,
filesystem-wide `find` → nothing; note the "`HOME=/tmp pip install --user`" workaround the
self-review describes has left no trace in the container, so it did not survive — I had to
install into a throwaway `--target` directory to run anything). The structural reason given
is right: `Dockerfile` bakes `requirements.txt`, built from the primary checkout, which stays
on `main`. What the ruling omits is that **merging causes an app outage**, not merely a
deferred test: `main.py:31` pulls `aiosqlite` in at import time and uvicorn `--reload` watches
the primary bind mount, so the app dies the moment the primary pulls `main` and stays down
until an image rebuild (see I6). Deferring the smoke test is reasonable; deferring it without
naming the rebuild-vs-merge ordering is not, and `.claude/rules/branching-and-ci.md`
specifically requires that an unchecked post-merge item have its executor and timing stated
before merging.

**One thing the self-review says that this review actively contradicts:** its Task 5 bullet
credits the branch with having "root-caused (not papered over) a genuine leaked-non-daemon-
thread test hang." The root cause was correctly identified but the fix was applied only to
three test files; the same branch's own new test file (`tests/test_diagnostics_routes.py`)
was left without it and **hangs today** (C1). Classifying this as a test-fixture concern
rather than an `_aio_db` lifecycle concern is what let it through.

---

## What I verified, and how

**Read in full:** the 4,380-line review diff (code portions line by line), the Fix 2 section of
`docs/superpowers/specs/2026-09-01-event-loop-blocking-elimination-design.md`, the 1,234-line
plan, and the current worktree source of `services/diagnostics/_aio_db.py`,
`services/diagnostics/diagnostics.py`, `services/diagnostics/routes.py`,
`services/quality/routes.py`, `services/research/research.py`, `services/series_watcher.py`,
`services/whalewatchers/_scoring_pool.py`, `main.py`'s lifespan, `Dockerfile`,
`.ddev/docker-compose.fastapi.yaml`, `.woodpecker/tests-pytest.yml`, and the new/modified
test files.

**Confirmed correct (no finding):**

1. **Behaviour equivalence.** Read `check_threshold_integrity`, `check_runway_at_entry` and
   `funnel` end to end against their pre-branch forms. Every SQL string is byte-identical,
   every parameter tuple is identical, every aggregation loop, threshold, status ladder and
   `Check(...)` construction is untouched. The only deltas are `def`→`async def`,
   `with closing(sqlite3.connect(X))` → `await _aio_db.connection_for(X)`,
   `.execute(...).fetchall()` → `await conn.execute_fetchall(...)`,
   `.execute(...).fetchone()` → `cursor = await conn.execute(...); await cursor.fetchone()`,
   and `await` on newly-async callees. Row-shape is preserved: `_aio_db` sets
   `row_factory = aiosqlite.Row` on every connection, which matches the four call sites that
   set `sqlite3.Row` themselves and is upward-compatible with the three that relied on plain
   tuples (`_close_ts_for_tickers`'s `{t: ts for t, ts in rows}`, `funnel`'s and
   `capture_stats`'s tuple unpacking all work unchanged against `Row`).
2. **Zero missed `await`s, repo-wide, against current state.** Grepped both bare
   (`(^|[^A-Za-z_.])name\s*\(`) and attribute (`\.name\s*\(`) call forms for all 15 newly-async
   functions across the whole repo excluding worktrees. Every executable call site is awaited:
   `services/diagnostics/routes.py:53,204,205,206,207`, `services/quality/routes.py:92`,
   `services/research/research.py:202`, `services/diagnostics/diagnostics.py:172,247,342,435,754-760,767`,
   `services/series_watcher.py:642,646,757,758,924,986,1045`, and every test via `asyncio.run(...)`.
   Remaining textual hits are comments/docstrings only. `tests/test_kanban_sync_sync.py`'s
   `reconcile(...)` and `tools/kanban_sync/__main__.py:147`'s are a different, unrelated symbol.
3. **`_diagnostics_pool.py` deletion is clean.** `grep -rn "_diagnostics_pool"` across the
   whole repo (all file types, not just `.py`) returns only documentation and two explanatory
   code *comments* (`services/diagnostics/diagnostics.py:709`, `services/quality/routes.py:79`).
   No import, no dynamic import, no string-based module loading, no config reference. Checked
   `importlib`/`getattr`/`__import__` usage repo-wide — none touches this module.
   `tests/test_diagnostics_pool.py` was deleted with it.
4. **Schema-init placement.** `schema_init=_ensure_schema_aio` is passed exactly where a
   function reads `series_watcher.py`'s own tables (`capture_stats`, `funnel`,
   `book_context_at_entry`) and omitted for the four DBs owned by other write-path modules
   (`signal_log.db`, `paper_broker.db`, `market_catalog.db`, `config_performance.db`) — each
   of which has its own `_connect()` with `CREATE TABLE IF NOT EXISTS` on a live write path,
   traced individually. `_ensure_schema_aio`'s DDL is byte-identical to `_connect()`'s (both
   read side by side) and fully idempotent (`IF NOT EXISTS` on both tables and all four
   indexes; `PRAGMA journal_mode=WAL` is a no-op on an already-WAL file).
5. **DDL persists without an explicit `commit()`** — I did not assume Python's legacy
   transaction rules, I probed them: a `schema_init` that runs `CREATE TABLE` with no commit,
   followed by connection close and a fresh `sqlite3.connect`, shows
   `PROBE2 tables persisted after close, no commit: ['probe_t']`.
6. **`connection_for`'s double-checked locking is correct for same-loop concurrency.** The
   fast-path `get` and the in-lock re-`get` have no `await` between them within each block, so
   two coroutines on one loop cannot both create; cross-loop callers use distinct keys.
   `_lock_for_current_loop`'s check-then-set is likewise await-free.
7. **`research.py`'s `try/finally` ordering is correct.** The `finally` is inside the coroutine
   passed to `asyncio.run(...)`, so it runs to completion while the loop is still alive and
   before `asyncio.run` tears it down. `close_for_current_loop()` closes only that loop's
   entries (`key[0] is loop`), leaving the main app's untouched — and there is no concurrency
   on that throwaway loop, so the mid-`await` eviction interleaving I looked for cannot occur
   there.
8. **Exception types unchanged.** `aiosqlite` re-exports `sqlite3`'s exception classes, so
   every pre-existing `except sqlite3.Error` still catches (the one gap is `ValueError` on a
   closed connection, reported under I1).
9. **No stale-data risk from the long-lived connections.** `POST /api/reset` →
   `PaperBroker.reset()` uses `DELETE FROM`, never file deletion or replacement
   (`services/paper_broker.py:749-766`, `services/reset/routes.py:150-215`), and no runtime
   code anywhere unlinks/renames/moves a `data/*.db`. SELECTs on a Python `sqlite3` connection
   in legacy isolation mode do not open a transaction, so each read sees the latest committed
   state. A cached connection therefore cannot serve a stale snapshot.
10. **Test quality — four checked in depth, all non-vacuous:**
    `test_close_for_current_loop_only_closes_this_loops_entries` genuinely spins a second event
    loop on a worker thread, proves the closed connection actually raises on reuse, and then
    positively proves loop A's own connections still work — not a dict-size proxy;
    `test_schema_init_runs_once_on_first_open_only` uses a real callback and counts real
    invocations; `test_run_offline_reuses_cached_connections_across_calls` asserts both
    `after_first > 0` and `after_second == after_first` against the real `_connections` dict, so
    it fails both if caching breaks and if it grows;
    `test_selectivity_curve_computes_accuracy_curve_from_real_signals` seeds 60 real rows into a
    real SQLite file and asserts real curve structure. All tests use real tmp_path SQLite files
    and real `asyncio.run` execution — nothing mocks away the code under test. Two weaker ones
    are flagged as M4/M5.

**Executed (all in `ddev-kalshi-whale-poc-fastapi`, `aiosqlite==0.22.1` installed into a
throwaway `--target` directory — no repo file and no container package state modified):**

- `pytest tests/test_aio_db.py tests/test_diagnostics.py tests/test_series_watcher.py
  tests/test_diagnostics_routes.py tests/test_research.py tests/test_quality_routes.py
  tests/test_main_tick_executor_wiring.py -q` → **97 passed** in 10.70s.
- `pytest -n 4 -m 'not slow' -q` (full suite, as CI runs it) → **3052 passed, 16 skipped** in
  119.57s, exit 0.
- Per-file exit timing → the C1 hang table above.
- Two ordering permutations of `test_series_watcher.py` / `test_diagnostics_routes.py` → the
  C1 order-dependence result above.
- Three purpose-built probes: interpreter-exit hang + its close-the-connections control
  (C1), uvicorn's `capture_signals()` SIGTERM re-raise sequence (C1's production scope,
  falsified), and DDL-without-commit persistence (verification #5).
- Two forced-interleave probes for the M3 dict race (~1,000 iterations, 0 errors).
- On-loop CPU measurement against the live `data/*.db` via read-only `mode=ro` URIs (I4),
  and `curl` timing of the live `GET /api/quality/summary` (20.4s, 8 watched series, 23,960 /
  50,711 / 124,859 rows) — run after reading the app's own diagnostics first, per CLAUDE.md's
  "Start investigations here" order.
- Library/runtime source reads: `aiosqlite/core.py` (thread creation, `_execute`, worker loop,
  `stop`/`close`), `uvicorn/server.py:313-345` (`capture_signals`, `handle_exit`),
  `uvicorn/supervisors/basereload.py:87-105` (`restart`/`shutdown`, unbounded `join()`),
  `uvicorn/_subprocess.py`, `multiprocessing/process.py:329` (`threading._shutdown()`).

**Not verified:** I did not start a second live instance of the app under uvicorn to observe
a real reload cycle end to end (it would open WS connections and a trading loop against a
fresh store); C1's production scope and I6's outage claim rest on source-state proof plus the
signal-sequence probe instead. I also did not measure `check_price_band_adherence`,
`check_runway_at_entry`, `performance_by_epoch`, `funnel()` or `reconcile()`'s on-loop CPU —
the four measurements in I4 are a lower bound, not the total.

---

## What would have to change for GO

1. **C1** — close the leaked worker threads: an `atexit`/`threading._register_atexit` hook in
   `_aio_db` (the mechanism `concurrent.futures` already gives the pool this replaces), or
   daemon threads, or at minimum a `tests/conftest.py`-level `_reset_aio_db_cache` so no test
   file can reintroduce the hang. Re-run `pytest tests/test_diagnostics_routes.py` alone and
   confirm it *exits*, not just that it passes.
2. **I2** — `try/except` around `schema_init` with `await conn.close()` before re-raise, plus
   a test.
3. **I1** — either implement the liveness-probe/evict-and-reopen the docstring already claims
   and the design spec already required (with its test), or amend both to say plainly that it
   was dropped and why.
4. **I3** — correct the loop-binding rationale in `_aio_db.py`, the plan, and the design spec
   to the real (lifetime) reason.
5. **I6** — write the merge/rebuild ordering into the PR body before merging, per
   `.claude/rules/branching-and-ci.md`.
6. **I4/I5** — state the on-loop-CPU and single-connection tradeoffs explicitly with the
   numbers above, or measure further and adjust.
7. **M7** — add the `docs/open-decisions.md` entry Task 7 Step 6 calls for, and reconcile the
   plan doc's checkboxes with what actually shipped.

M1/M2/M6 are cheap enough to fold into the same pass. M3/M4/M5/M8 are recorded for the
consolidation step to accept or defer on the merits.
