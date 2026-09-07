# Adversarial Review — Fix 2 (Diagnostics Widening) Implementation Plan

Reviewing: `docs/superpowers/plans/2026-09-01-event-loop-blocking-fix2-diagnostics-widening.md`
(found in worktree `.claude/worktrees/aiosqlite-diagnostics-whale-scoring`, branch
`fix/aiosqlite-diagnostics-widening`; this review was conducted from a separate,
independent worktree — `.claude/worktrees/agent-a2896d7a6e06ba9c6` — whose `services/`,
`tests/`, and `requirements.txt` were verified byte-identical to the plan's own worktree
via `diff`, confirming both are unmodified checkouts of the same base commit `4493a67`.
No memory of how the plan was written was carried into this review; every claim below was
re-derived from source.)

## Verdict: **NO-GO**

The plan's core claims about function signatures, DB_PATH values, caller lists, the
aiosqlite API, the exception-re-export guarantee, and the version pin all check out —
this is careful, well-researched work. But independent verification found **two concrete,
reproducible test failures** the plan does not anticipate, **one systemic test-breakage
affecting the majority of an entire test file**, and **two real (if narrow-probability)
concurrency-safety gaps in the plan's own new `_aio_db.py` infrastructure** — the exact
module this whole fix depends on to be safe. None of these are style nitpicks; all are
either "the plan's own Step N `Expected: PASS` is wrong" or "the new cache module doesn't
provide the safety guarantee its own docstring claims." These should be fixed before
execution, not discovered mid-implementation.

---

## Item 1 — Function signatures, DB_PATH values, "before" code shape

**Checked:** Read `services/diagnostics/diagnostics.py` (778 lines) and
`services/series_watcher.py` (984 lines) in full, current `main` state (confirmed
identical across both worktrees).

**Result: CONFIRMS**, with one caveat surfaced separately as Finding A below.

- `_close_ts_for_tickers` (diagnostics.py:74), `_fetch_path_changes` (:100),
  `check_threshold_integrity` (:147), `check_price_band_adherence` (:226) — all do raw
  `with closing(sqlite3.connect(...))` exactly where and how the plan's Task 2 code blocks
  claim. Line range `74-305` the plan cites for Task 2 exactly spans these four.
- `check_runway_at_entry` (:310), `config_epochs` (:389), `performance_by_epoch` (:423) —
  match Task 3's claimed range `310-487` exactly.
- `selectivity_curve` (:616), `check_confidence_input_coverage` (:702) — match Task 4's
  claimed range `616-736` (function ends at 736; `check_confidence_input_coverage` itself
  starts at 702, confirmed via direct grep).
- `run_offline` (:739) — matches Task 6's claimed range `739-778`.
- `check_config_bounds` (:592) — confirmed no DB access (see Item 7).
- series_watcher.py: `_signals_for_series` (:475), `_trades_for_series` (:493),
  `funnel` (:524), `reconcile` (:660), `capture_stats` (:430), `book_context_at_entry`
  (:847), `check_series_funnel` (:916) — all present, all do the exact SQL/shape the plan's
  Task 5 code blocks show as "before."
- `services/diagnostics/routes.py`: `get_diagnostics` (line 48, `async def`, body at line
  53 `return diagnostics.run_offline(...)` — no `await`) and `get_series_watcher` (line
  192, `async def`, body at lines 204-207, 4 unwaited calls) — exact match to the plan's
  claimed lines 53 and 203-207.
- `services/quality/routes.py`: line 27 import, line 86
  `"diagnostics": await _diagnostics_pool.run(lambda: diagnostics.run_offline(cfg)),` —
  exact match.
- `services/diagnostics/_diagnostics_pool.py`: exactly the described 2-worker
  `ThreadPoolExecutor` + `run()` wrapper.

No line, comment, or variable-name drift found anywhere the plan's diffs assume an exact
match.

## Item 2 — Completeness of the caller list

**Checked:** Independent repo-wide grep (not the plan's own tables) for `run_offline`,
`series_watcher.funnel(`, `.reconcile(`, `.check_series_funnel(`, `.capture_stats(`,
`.book_context_at_entry(`, `_diagnostics_pool`, across `services/`, `main.py`, `tools/`.

**Result: CONFIRMS.**

- `run_offline` callers: exactly 3 — `services/quality/routes.py:86`,
  `services/research/research.py:183`, `services/diagnostics/routes.py:53`. No 4th caller
  anywhere in `services/`, `main.py`, or `tools/`.
- `series_watcher.funnel/.reconcile/.book_context_at_entry/.capture_stats` direct callers:
  only `services/diagnostics/routes.py:204-207`. `check_series_funnel` is called only from
  `diagnostics.py:765` inside `run_offline`.
- `services/app_state.py`, `services/kalshi/websocket.py`, `main.py`, and
  `services/whale_stream/whale_stream_handlers.py` all reference `series_watcher` but only
  ever call the write-path functions (`record_trade`, `record_book`, `flush`, `prune`) —
  correctly out of this plan's scope — never the read functions this plan converts.
- No direct external caller of the individual `Check` functions
  (`check_threshold_integrity`, etc.) exists outside `diagnostics.py` itself.

## Item 3 — The loop-scoped connection-cache design (Task 1, `_aio_db.py`)

**Checked:** Read `services/research/research.py` in full (274 lines). Verified
aiosqlite's actual implementation via Context7 (`/omnilib/aiosqlite`) and a raw GitHub
fetch of `aiosqlite/core.py`.

**Result: MIXED — the loop-scoping premise CONFIRMS, but two real gaps found in the
design meant to realize it.**

Confirmed pieces:
- `_run_research_background` (research.py:212) does `await asyncio.to_thread(run_and_store, cfg)`
  (line 224) — exact match.
- `run_and_store` (line 199) and `build_report` (line 139) are both plain synchronous
  `def`s with zero `async`/`await` of their own today — confirmed by reading both in full.
- `build_report()` genuinely runs with no event loop of its own (it's a plain function
  invoked via `run_in_executor` under the hood of `asyncio.to_thread`) until the plan's new
  `asyncio.run(_diagnostics_and_cleanup())` call creates one. The loop-scoped-cache
  rationale is real, not invented.
- aiosqlite's own architecture (confirmed via Context7 docs and a raw source fetch,
  not recalled from memory): a `Connection`'s internal request queue and futures are bound
  to the event loop that created them (`asyncio.get_running_loop()` at future-creation
  time inside `_execute`/`_connect`); sharing one across two different loops is a
  documented hazard, consistent with the general "asyncio primitives are not
  thread/loop-safe" rule. The plan's claim here is correct, and its citation (Context7, not
  memory) is honest.

**Finding C (new, moderate):** the design's own `_lock = asyncio.Lock()` (plan line ~190,
`_aio_db.py` module scope) is a **single shared instance used by every loop**, unlike the
`_connections` cache it protects, which is correctly loop-scoped by `_key()`. Since
`build_report()` really does run on a separate OS thread with its own independent
`asyncio.run()` loop (confirmed above), and asyncio's synchronization primitives
(`Lock`/`Event`/`Semaphore`/`Condition`) are documented as unsafe across different
event loops/threads for exactly the reason cited for `Connection` objects — a waiter
`Future` created by `acquire()` under one loop, released by a different loop's `release()`
call, uses a non-threadsafe `call_soon()` into the other loop's internals — this
undermines the exact cross-loop safety property Task 1 exists to provide. The practical
window is narrow (once a `(loop_id, db_path)` entry is cached, later calls return before
ever touching the lock — see the fast path in `connection_for()`), so this won't fire on
every run, but it is a real gap: the module-level `_lock` should be scoped per-loop the
same way `_connections` is (e.g. a `dict[int, asyncio.Lock]` keyed the same as
`_connections`), not left as a bare cross-loop-shared object.

**Finding D (new, moderate):** Task 6 Step 6's `_diagnostics_and_cleanup()` has no
`try/finally`:

```python
async def _diagnostics_and_cleanup() -> dict:
    result = await diagnostics.run_offline(cfg, now=now)
    await _aio_db.close_for_current_loop()
    return result
```

If anything in `run_offline()`'s call graph raises an exception not internally caught by
one of its `except sqlite3.Error` guards, `close_for_current_loop()` never runs, and that
throwaway loop's connections leak in `_connections` forever, keyed by a now-dead loop's
`id()`. Because CPython can reuse a freed object's memory address, a later unrelated event
loop could in principle be allocated at that same address, and `connection_for()` would
then hand out a connection bound to a different, dead loop — precisely the hazard the
loop-scoped design exists to prevent. Fix is a straightforward `try/finally`.

## Item 4 — `asyncio.to_thread` wrapping decisions (trade_category / signal_log)

**Checked:** Independent grep for every caller of `categories_for_tickers` and
`resolved_signals_with_factors` across the whole repo (not the plan's own list).

**Result: CONFIRMS the conclusion, CONTRADICTS the plan's stated evidence (list is
incomplete).**

- `categories_for_tickers` other callers: `services/history/regime_analytics.py:130`,
  `services/advisory/advisory_engine.py:788` — both confirmed real, both would break if the
  signature became async. Matches the plan.
- `resolved_signals_with_factors` other callers: the plan names only
  `services/whale_calibration/routes.py`. Independent grep found **three more** real
  callers the plan doesn't mention: `main.py:490`, `services/backtest/routes.py:21`, and
  `services/whale_calibration/routes.py` itself has *two* call sites (lines 112, 147), not
  one. The plan's conclusion (leave it sync, wrap in `asyncio.to_thread`) is still correct
  — actually more strongly justified than stated — but the enumerated evidence is
  incomplete. Minor, but "verify, don't guess" cuts both ways: an incomplete caller list
  presented as complete is itself a small instance of the failure mode this review exists
  to catch.

## Item 5 — The aiosqlite API calls the plan's code uses

**Checked:** Context7 (`/omnilib/aiosqlite`) live documentation query, cross-checked
against a raw fetch of `aiosqlite/__init__.py` and `aiosqlite/core.py` from
`github.com/omnilib/aiosqlite`.

**Result: CONFIRMS, fully.**

- `conn = await aiosqlite.connect(db_path)` — correct; `aiosqlite.connect()` returns an
  awaitable `Connection` proxy, and `await`-ing it is the library's own documented idiom
  (shown directly in its exception-handling example: `db2 = await aiosqlite.connect(...)`).
- `conn.row_factory = aiosqlite.Row` — correct; `row_factory` is a settable property on
  `Connection`, `aiosqlite.Row` is the documented dict-style-access factory.
- `await conn.execute_fetchall(sql, params)` — correct; returns the fetched rows directly
  when awaited (not a cursor, not requiring `async with`), matching the plan's usage
  exactly.
- `cursor = await conn.execute(sql, params)` then `row = await cursor.fetchone()` — correct;
  `execute()` returns a `Cursor` proxy, which has `fetchone`/`fetchmany`/`fetchall` as
  documented coroutine methods.
- Exception re-export: **verified at the source level**. `aiosqlite/__init__.py` imports
  `DatabaseError, Error, IntegrityError, NotSupportedError, OperationalError,
  ProgrammingError, Warning` **directly from the stdlib `sqlite3` module** (not
  redefined) — so `aiosqlite.OperationalError is sqlite3.OperationalError` literally, and
  the plan's `except sqlite3.Error:` blocks work unmodified against exceptions raised by
  aiosqlite calls. This is the plan's most load-bearing "no code needs to change type"
  claim in the Global Constraints section, and it holds.

## Item 6 — Test file existence and shape

**Checked:** Read `tests/test_diagnostics.py` in full (361 lines) and
`tests/test_series_watcher.py` in full (650 lines); grepped route-level test coverage
across all of `tests/`.

**Result: MOSTLY CONFIRMS, with two concrete new test-breakage findings (A, B below) and
one coverage gap (G).**

- All 17 test functions the plan names by exact name in Tasks 2, 3, 4, and 6
  (`test_threshold_integrity_flags_signals_below_the_configured_floor` through
  `test_confidence_input_coverage_unknown_below_the_resolved_floor`) exist verbatim in
  `tests/test_diagnostics.py`, calling the functions the plan assumes.
- The `dbs` fixture (test_diagnostics.py:25) exists exactly as assumed — monkeypatches
  `DB_PATH` on `signal_log`, `paper_broker`, `config_performance`, `market_catalog`,
  `trade_category`, and `series_watcher` modules via `monkeypatch.setattr`.
- `tests/test_series_watcher.py` is confirmed as the exact filename (resolving the plan's
  own hedge "confirm exact filename at execution time") — GitNexus's index independently
  confirmed this too.
- `tests/test_diagnostics_pool.py`, `tests/test_diagnostics_routes.py`,
  `tests/test_quality_routes.py`, `tests/test_research.py` all exist (resolving the plan's
  stated uncertainty about several of these).
- `tests/test_diagnostics_pool.py` contains exactly 2 tests, both testing
  `_diagnostics_pool.run` directly with synthetic work functions and nothing else — the
  plan's Task 6 Step 7 guidance ("if it only tests this deleted module directly, delete it
  too") is correct and unambiguous to apply here.
- **Gap:** no test anywhere covers `selectivity_curve()` directly (the plan hedges "plus
  any selectivity_curve test present" — correctly, since none exists). Not a new
  regression, but means this function's aiosqlite conversion is verified only indirectly
  via `test_run_offline_reports_worst_status_across_checks`, or by the manual live smoke
  test in Task 7.
- `test_read_paths_close_their_sqlite_connections` (test_diagnostics.py:294): the plan
  describes this as asserting "something about connection lifecycle" and suggests adapting
  it to a cache-growth assertion. Read in full, this test is actually a **static
  source-regex check** (`re.findall(r"with sqlite3\.connect\(", source)` over the two
  target files' own source text), not a runtime lifecycle assertion. After conversion,
  literally zero `with sqlite3.connect(` patterns remain in either file's read paths, so
  the test would trivially, vacuously PASS without encoding any real guard at all unless
  it is rewritten from scratch (not merely "adapted"). The plan's own suggested
  replacement (assert `len(_aio_db._connections)` doesn't grow on a second same-loop call)
  is a reasonable new test, but it only covers the same-loop-reuse case — it does **not**
  cover the different failure mode Finding D above describes (throwaway-loop leak on an
  exception path), which has no test coverage anywhere in this plan.

## Item 7 — `check_config_bounds(cfg)` has zero DB access

**Checked:** Read `services/config/config_bounds.py` in full (277 lines).

**Result: CONFIRMS.** The entire module is pure arithmetic over a passed-in `cfg` dict —
no `import sqlite3`, no `DB_PATH`, no I/O of any kind. `check_all(cfg)` (called by
`check_config_bounds`) only calls `config_overrides.resolve(...)`, itself pure. Correctly
left synchronous.

## Item 8 — Scope discipline

**Checked:** Full read of every code block in the plan's Tasks 1-6; grep for write-path
function names against the plan's diffs.

**Result: CONFIRMS**, with one framing imprecision (H below).

- No write-path function (`record_trade`, `record_book`, `flush`, `prune`,
  `capture_writer.py`'s `submit`/`_flush_store`) appears in any task's file list or code
  block. Confirmed by reading `series_watcher.py` in full: none of these functions are
  touched by anything in the plan.
- No arithmetic, threshold, dollar, or probability constant changes anywhere in the plan's
  code blocks — every diff preserves the exact same SQL text, same formulas, same
  literals; only the connection-acquisition mechanism changes (`sqlite3.connect` /
  `_connect()` → `_aio_db.connection_for`), consistent with the plan's own Global
  Constraints claim that the dimensional-analysis HARD RULE doesn't add new obligations
  here.
- (Aside, not a plan defect: `services/series_watcher.py`'s `record_book` in the current
  checked-out source already returns `(accepted, should_flush)` rather than flushing
  inline — Fix 1 has evidently already merged to `main` ahead of this Fix 2 plan, which is
  consistent with and doesn't affect this review.)

## Item 9 — `aiosqlite==0.22.1` version pin

**Checked:** `requirements.txt` grep; PyPI JSON API fetch; Context7 library resolution.

**Result: CONFIRMS, fully.**

- `aiosqlite` is genuinely absent from `requirements.txt` today (zero hits).
- `0.22.1` is a real, current PyPI release — released 2025-12-23, marked
  Production/Stable, the newest version ahead of `0.22.0` (2025-12-13) and `0.21.0`
  (2025-02-03). Not a fabricated version number.
- The insertion point the plan names ("after the `websockets` line, before the
  `anthropic` comment block") corresponds to real, adjacent lines in `requirements.txt`
  (`websockets==17.0.1` at line 52, `anthropic==0.121.0` at line 58).

---

## New findings not explicitly asked for, surfaced during verification

**Finding A (critical — concrete, reproducible test failures):** `_aio_db.connection_for()`
(Task 1) does not replicate what `series_watcher._connect()` (lines 151-207) currently
does on every call: `DB_PATH.parent.mkdir(exist_ok=True)`, `PRAGMA journal_mode=WAL`, and
`CREATE TABLE IF NOT EXISTS` for both `raw_trades` and `book_snapshots` plus their
indexes. Task 5 converts `funnel()`, `capture_stats()`, and `book_context_at_entry()` from
`with _connect() as conn:` to `conn = await _aio_db.connection_for(DB_PATH)`, silently
dropping this schema-ensuring guarantee — a direct departure from CLAUDE.md's own
documented "Persistence idiom" (`DB_PATH.parent.mkdir(exist_ok=True)` +
`CREATE TABLE IF NOT EXISTS` on every connect). This is **not** a pre-existing
characteristic being reproduced: `diagnostics.py`'s own bare `sqlite3.connect(...)` calls
already relied on schema pre-existing (created elsewhere), so the aiosqlite conversion of
`diagnostics.py`'s functions doesn't change anything there — this is specific and new to
`series_watcher.py`'s three read functions, which currently DO self-heal via `_connect()`.

Independently verified as breaking two real, currently-passing tests:
- `tests/test_series_watcher.py::test_funnel_flags_that_capture_was_off_rather_than_claiming_no_whales`
  (line 471) never calls a `series_watcher` write function before calling `sw.funnel(...)`
  — it relies entirely on `_connect()`'s auto-schema-creation. Asserts
  `stages["prints_observed"] == 0`. After conversion, the never-created `series_watcher.db`
  has no `raw_trades` table; `aiosqlite.connect()` silently creates an empty file with no
  tables; the subsequent query raises `OperationalError: no such table: raw_trades`,
  caught by `except sqlite3.Error as exc:`, setting `observed = None` — the assertion
  `== 0` fails against `None`.
- `tests/test_series_watcher.py::test_book_context_says_unknown_rather_than_inventing_a_spread`
  (line 621) similarly never writes to `sw.DB_PATH` before calling
  `sw.book_context_at_entry(...)`. Asserts `"not reconstructable" in out["reason"]`. After
  conversion, the same no-such-table failure is caught by the function's own
  `except sqlite3.Error as exc: return {..., "reason": f"watcher store unreadable: {exc}"}`
  — a *different* reason string that does not contain "not reconstructable," so the
  assertion fails.

Both would be caught immediately by the plan's own Task 5 Step 9 ("Expected: PASS"), so
this isn't a silent landmine — but the plan gives the implementer no guidance for it, and
the tempting fast fix (just change the two assertions to match the new "unreadable"
behavior) would silently regress a real production behavior: the diagnostics module's
whole stated design philosophy (diagnostics.py's own module docstring: "degrades honestly
— a check that cannot be computed reports status 'unknown' with the reason, never a
fabricated number") depends on distinguishing "no data yet" from "store unreadable," and
this conversion, as currently specified, collapses that distinction for a genuinely fresh
`series_watcher.db`.

**Finding B (significant — systemic test breakage):** `tests/test_research.py`'s shared
helper `_patch_every_analyzer()` (line 102) monkeypatches
`research.diagnostics.run_offline` as a **plain synchronous lambda**:
`lambda cfg, now=None: {"overall": "ok"}`. This helper is called by 11 of the file's
~17 test functions (confirmed by grep), including the three that directly exercise
`build_report()` (`test_build_report_includes_every_documented_section`,
`test_build_report_skips_advisory_generation_when_disabled`,
`test_build_report_never_touches_config_store_or_logs_an_applied_change`) and several that
exercise it indirectly via `run_and_store`/`_maybe_run_research`. After Task 6 Step 6
converts `build_report()`'s call site to
`result = await diagnostics.run_offline(cfg, now=now)` (inside the new
`_diagnostics_and_cleanup()` wrapped in `asyncio.run(...)`), every one of those 11 tests
will raise `TypeError: object dict can't be used in 'await' expression`, since awaiting
the monkeypatched lambda's plain dict return value is not awaitable. The plan's Task 6
File Structure section only lists "`tests/test_research.py` if it exists" with no specific
guidance — contrast this with Task 4 Step 1's careful, explicit attention to a
structurally similar (but far smaller-blast-radius) monkeypatch-shape risk in
`test_diagnostics.py`. This needs an explicit step: convert
`_patch_every_analyzer`'s `run_offline` patch to an `async def` (or `AsyncMock`).

**Finding E (minor — undocumented spec correction):** The plan's introduction claims to
correct/extend the background spec "in three places" (routes.py, `book_context_at_entry`/
`capture_stats`, research.py). It omits a fourth, real divergence: the spec's parenthetical
says `config_epochs` "isn't called by `run_offline()` at all and stays out of scope" —
readable as "stays synchronous." Task 3 Step 4 nonetheless converts `config_epochs` to
`async def`, correctly, since (a) it does its own raw `sqlite3.connect(config_performance.DB_PATH)`
call (diagnostics.py:397-404) the spec's phrasing overlooks, and (b) `performance_by_epoch`
(which the spec does name as in-scope) calls it and, once async, must `await` it. The
technical decision is right; it just isn't flagged as a spec correction the way the other
three are.

**Finding G (minor — coverage gap):** Neither `GET /api/diagnostics` nor
`GET /api/diagnostics/series/{series}` (the two routes Task 6 Step 5 modifies) has any test
coverage anywhere in `tests/` today (confirmed: zero hits for the literal route strings
across the whole test suite, including `tests/test_diagnostics_routes.py`, whose actual
tests cover `get_index_settlement` and `get_account_diagnostics` — different routes in the
same file). The plan's File Structure section hedges this as "a new
`tests/test_diagnostics_routes.py` assertion if none currently covers the two routes" but
never turns it into a committed step under Task 6 — easy to skip, leaving these two
converted routes verified only by the manual curl-based smoke test in Task 7, not by
durable CI coverage.

**Finding H (minor — imprecise justification, correct conclusion):** The rationale for
deleting `_diagnostics_pool.py` ("run_offline()'s target DB files... don't overlap with
tick_executor's other trading-critical writers") is true as stated (verified: `signal_log.db`,
`paper_broker.db`, `market_catalog.db`, `config_performance.db`, `series_watcher.db` are
all distinct filenames from `candidate_log.db`/`candidate_ledger.db`) but is a bit of a
non-sequitur: `_diagnostics_pool.py` was never on `tick_executor`'s pool to begin with — it
is explicitly its *own* dedicated pool (confirmed from its own docstring: "not
services.tick_executor's shared one"). The real reason deletion is safe is that
`run_offline()` stops running on *any* thread pool at all post-conversion (native async
instead), not DB-file non-overlap with tick_executor specifically. Also worth being
explicit about: `paper_broker.db` — arguably the single most trading-critical file in the
app — *is* one of `run_offline()`'s most heavily-read targets; the "no overlap with
trading-critical writers" phrasing glosses over this even though it doesn't change the
conclusion.

---

## Fixes needed before this plan is safe to execute

1. **`docs/superpowers/plans/2026-09-01-event-loop-blocking-fix2-diagnostics-widening.md`,
   Task 1 Step 4 (`_aio_db.py`'s `connection_for()`, plan lines ~197-208) and Task 5 Steps
   4/7/8** — decide and document explicitly whether `_aio_db.connection_for()` should
   replicate `_connect()`'s mkdir/PRAGMA/`CREATE TABLE IF NOT EXISTS` schema-ensuring
   behavior for `series_watcher.py`'s three converted read functions, or whether the
   resulting "unreadable" vs. "no data yet" behavior change on a schema-less DB is an
   accepted, intentional change. Either way, update
   `tests/test_series_watcher.py::test_funnel_flags_that_capture_was_off_rather_than_claiming_no_whales`
   (line 471) and `::test_book_context_says_unknown_rather_than_inventing_a_spread`
   (line 621) explicitly in the plan rather than leaving an implementer to discover and
   improvise around the failure.

2. **Task 6 File Structure section / Step 1** (plan's `tests/test_research.py` mention) —
   add an explicit step: `tests/test_research.py`'s `_patch_every_analyzer()` (line 102)
   must have its `research.diagnostics.run_offline` monkeypatch converted from a plain
   `lambda cfg, now=None: {"overall": "ok"}` to an `async def` equivalent (or
   `unittest.mock.AsyncMock`), or all 11 dependent tests will fail with
   `TypeError: object dict can't be used in 'await' expression`.

3. **Task 1 Step 4 (`_aio_db.py`, plan line ~190, `_lock = asyncio.Lock()`)** — scope the
   lock per event loop the same way `_connections` is scoped (e.g. store per-loop locks
   alongside the connections dict), or otherwise eliminate the cross-loop-shared
   `asyncio.Lock` — as written it undermines the exact cross-loop safety guarantee the
   loop-scoped cache design exists to provide.

4. **Task 6 Step 6 (research.py's `_diagnostics_and_cleanup()`, plan lines ~962-976)** —
   wrap the `await diagnostics.run_offline(...)` / `await _aio_db.close_for_current_loop()`
   pair in `try/finally` so a connection cleanup still happens (and the leaked-connection /
   stale-id-reuse hazard is avoided) even if `run_offline()` raises.

5. **Plan intro (the "3 places" list, plan lines 11-14)** — add `config_epochs` as a
   fourth, explicit correction to the background spec, with the reason (it does its own DB
   call and is transitively in `run_offline()`'s call graph via `performance_by_epoch`).

6. **Task 6 File Structure / Step 5** — commit explicitly (not just hedge) to adding route
   coverage for `GET /api/diagnostics` and `GET /api/diagnostics/series/{series}` in
   `tests/test_diagnostics_routes.py`, since neither currently has any test coverage
   anywhere in the suite.

7. **Task 6 "deletion is safe" rationale** — restate the reasoning to lead with "no
   remaining thread-pool involvement at all" rather than "DB file non-overlap with
   tick_executor," and acknowledge `paper_broker.db`'s own trading-criticality rather than
   implying `run_offline()`'s targets are all low-stakes stores.
