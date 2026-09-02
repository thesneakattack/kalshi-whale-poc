# PR Self-Review — Event-Loop-Blocking Fix 2 (Diagnostics Widening)

Branch: `fix/aiosqlite-diagnostics-widening`, merge-base `4493a67` (origin/main), HEAD `1f24571`.
13 commits, 18 files changed, +2532/-312.

Same-author self-review per CLAUDE.md's "nothing advances on one pass" HARD RULE, before
the independent adversarial review. Written from the controller session that dispatched
and reviewed every task (1-6) individually and then merged three parallel lanes together.

## Scope recap

Converts every DB-touching function in `services/diagnostics/diagnostics.py` and
`services/series_watcher.py` from synchronous `sqlite3` to `aiosqlite`, via a shared
loop-scoped connection cache (`services/diagnostics/_aio_db.py`), then wires `run_offline()`
and its three real call sites through end to end and deletes the now-unnecessary
`_diagnostics_pool.py`. Goal: the FastAPI event loop is never blocked by this code's disk I/O.

## What was actually verified, and how (not just claimed)

- **Task 1** (foundation): loop-scoped connection cache, keyed by loop *object* (not `id()`,
  after a real fix-round caught an address-reuse collision risk). 7/7 tests, including a
  dedicated cross-loop isolation test rewritten twice until it validated the real contract
  (black-box "closed connection raises" + white-box "cache evicts only current loop's
  entries"), not just cache-size proxies.
- **Task 2** (`check_threshold_integrity`/`check_price_band_adherence` + 2 shared helpers):
  clean on first review, 8/8 tests, zero fix rounds.
- **Task 3** (`check_runway_at_entry`/`config_epochs`/`performance_by_epoch`, built in a
  separate worktree/lane after fast-forwarding in Task 2's commit): 1 fix round (a dead,
  redundant `__init__.py` import the reviewer traced to an already-existing equivalent
  import elsewhere and confirmed no consumer needed the package-attribute form). 4/4 tests.
- **Task 4** (`selectivity_curve`/`check_confidence_input_coverage`, separate lane): reviewed
  clean, including an explicitly-verified required docstring correction (stale
  "tick_executor pool" reference, checked against the real current module name). 3/3 tests
  for the new coverage plus the 2 pre-existing tests.
- **Task 5** (`series_watcher.py`'s 7 read-only functions, separate lane): 1 fix round for a
  real cross-file regression a task-scoped test run didn't catch (`capture_stats` going
  async broke a synchronous call in `tests/test_main_tick_executor_wiring.py`) — found by
  the reviewer actually executing the test, not inferring it. Also root-caused (not
  papered over) a genuine leaked-non-daemon-thread test hang via a `_reset_aio_db_cache`
  fixture matching an established sibling-file pattern. 32/32, then 42/42 after the fix.
- **Task 6** (wire `run_offline()` + 3 real call sites + delete `_diagnostics_pool.py`):
  reviewed clean. Reviewer independently re-ran the "any 4th unawaited call site" grep and
  the "_diagnostics_pool fully gone" grep rather than trusting the report. 48/48.
- **Lane integration**: merging the 3 lanes (base + lane B + lane C) back together produced
  zero merge conflicts (git auto-merge, including correctly deduplicating an identical
  `_aio_db` import both lane B and the base branch had added independently). Verified by
  running the combined test surface immediately after each merge, not assumed from a clean
  `git merge` exit code alone.
- **Full targeted test surface** (Task 7 Step 1): `tests/test_aio_db.py
  tests/test_diagnostics.py tests/test_series_watcher.py tests/test_quality_routes.py
  tests/test_diagnostics_routes.py tests/test_research.py
  tests/test_main_tick_executor_wiring.py` — 97 passed, 1 pre-existing unrelated
  `StarletteDeprecationWarning`, 0 failures.

Every task above was reviewed by a fresh subagent Agent call carrying no memory of the
implementation, reading the diff and re-deriving claims against it — this self-review is
the layer above that, checking internal consistency and unaddressed scope across all of them
together, not re-doing per-task review.

## Internal consistency check across tasks

- **Schema-init placement is consistent everywhere it's needed.** `_ensure_schema_aio`
  (Task 5) and the equivalent pattern in `diagnostics.py`'s own connection calls are passed
  `schema_init=` only where a function touches its *own* file's freshly-creatable tables
  (`funnel`, `capture_stats`, `book_context_at_entry` in `series_watcher.py`); functions
  reading other modules' schema-guaranteed DBs (`_signals_for_series`, `_trades_for_series`,
  `selectivity_curve`, etc.) correctly omit it. Verified per-task by each task's reviewer,
  cross-checked here that no task contradicts another's convention.
- **`except sqlite3.Error` blocks are untouched everywhere**, across all 7 converted
  functions in `series_watcher.py` and all 6 in `diagnostics.py` — `aiosqlite` re-exports
  the same exception classes (confirmed directly in-container: `aiosqlite.Error is
  sqlite3.Error` → `True`), so no task needed to change exception types, and none did.
- **No task introduced concurrency the design didn't call for.** `run_offline()` awaits its
  6 checks and the per-series `check_series_funnel` calls sequentially, matching today's
  exact ordering — Task 6's reviewer specifically checked this wasn't silently "improved"
  to `asyncio.gather`.
- **The one known cross-task dependency (Task 3 → Task 2's `_close_ts_for_tickers`) was
  handled correctly**, not glossed over: lane C's branch was fast-forwarded onto Task 2's
  commit before Task 3 started, specifically because Task 3 consumes that now-async helper.
- **Out-of-scope modules stayed out of scope.** `trade_category.categories_for_tickers()`
  and `signal_log.resolved_signals_with_factors()` are wrapped in `asyncio.to_thread()` at
  their call sites, never converted themselves — every task that touches either confirmed
  this via the Global Constraints' explicit caller list, and no task's diff changes either
  function's signature.

## Known, deliberately-not-fixed gaps (surfaced, not hidden)

1. **`tests/test_diagnostics.py:12`** — `from pathlib import Path` is now a dead import
   (Task 6's own test rewrite removed its last use). Cosmetic, no CI lint catches it.
2. **`check_series_funnel` computes `reconcile()` and `funnel()` separately** (Task 5's own
   Step 6 note), doing its core DB work twice per call. A real, measured compute-redundancy
   cost, explicitly out of this plan's scope (event-loop-blocking, not compute cost) —
   flagged in each relevant task's ledger, needs a `docs/open-decisions.md` line (Task 7
   Step 6, not yet done as of this self-review).
3. **The container's real Python image does not have `aiosqlite` installed** and cannot,
   until this PR merges to `main` — the fastapi container is built from the *primary*
   checkout's `requirements.txt`, and primary stays on `main` throughout this branch's
   entire life. Every task's test verification in this branch ran under an ephemeral
   `HOME=/tmp pip install --user aiosqlite==0.22.1` workaround, confirmed to touch no repo
   file. This means the plan's own Task 7 Step 2/3 (restart to bake in the dependency, then
   live-smoke-test) cannot execute in the order the plan text describes — verified directly
   this session: a real `ddev restart` was performed and `aiosqlite` was still not
   importable in the container afterward, for the structural reason above, not a fluke.
   **Ruling:** defer the live smoke test to after this PR merges and primary pulls `main`,
   matching this repo's own established precedent (PR #414's live-validation step ran the
   same way, confirmed from `docs/next-action.md`'s history) — not a new process invented
   for this PR.

## What this self-review did NOT re-verify

Per this stage's purpose (cheapest layer, catch sloppiness before independent effort), this
review trusted each task's own dispatched-reviewer verdicts and did not re-read every line
of the diff itself — that re-derivation from source is the adversarial review's job, next.

## Self-review verdict

No new issues found beyond the three already-tracked gaps above (1 cosmetic, 2 already
logged for `docs/open-decisions.md`/post-merge sequencing). Ready for adversarial review.
