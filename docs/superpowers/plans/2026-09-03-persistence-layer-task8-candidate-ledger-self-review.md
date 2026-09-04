# Task 8 self-review — `services/candidate_ledger.py` migrates to `services/db.py`

Plan: `docs/superpowers/plans/2026-09-03-persistence-layer-db-migration-implementation.md`
lines 1597-1715 (Task 8). Safety-adjacent tier, dedicated PR, human go-ahead given for
Tasks 6/7/8 (relayed by coordinator; plan content independently verified against the
current file before starting — see below). Stage 1 of the review cycle requested for this
task: self-review, then a fresh independent adversarial review, then consolidation, then
GO before merge.

## Verified before writing any code

- Read the plan's Task 8 section directly (`sed`, not summary): "Files: Modify
  `services/candidate_ledger.py:20-38`... Depends on Task 1... Ships as its own
  individually reviewed PR — never bundled with any other task." Matches what was relayed.
- Read `services/candidate_ledger.py` as it exists on `origin/main` before editing (plan's
  own Step 2 requirement): parameterless `_connect()`, `PRAGMA journal_mode=WAL` at `:33`,
  single unconditional `CREATE TABLE IF NOT EXISTS candidates` at `:35-37`. Matches the
  plan's "Current state" transcription exactly — no drift since the plan was written.
- Read `services/db.py` (Task 1 / Gate 0) directly: `register_schema`, `connect(db_path,
  *, tables=(), busy_timeout_ms=5000)`, `add_column_if_missing` all present and match the
  plan's described API. Confirmed via `grep -n "^def "`, not assumed from the plan's
  description of it.
- Read `services/candidate_log.py` (Task 3, already merged, `3e3f17a`) as the closest real
  precedent for the exact `register_schema` + `@contextlib.contextmanager _connect()`
  shape, and PR #540's body (Task 5, observability.py) for the review-rigor bar an
  already-merged migration task was held to.

## What changed

Same shape as Tasks 3 and 5, the two already-merged precedents: `_connect()` becomes a
`@contextlib.contextmanager` wrapping `db.connect(DB_PATH, tables=("candidates",))`. The
`PRAGMA journal_mode=WAL` line is deleted from the module — `db.connect()` already sets it
(and `busy_timeout=5000`) as part of its own connect policy, so keeping a duplicate call
here would be redundant, not protective. Table creation moves into a registered
`_init_candidates(conn)` callback, verbatim DDL, no changes. No call site's syntax changes
(`claim`, `record_decision`, `decision_for`, `stats` all already used
`with _connect() as conn:`).

This is, as the plan says, the simplest of the three safety-adjacent modules: single
table, no `add_column_if_missing`, no index, no schema evolution ever.

## Call-site census (no truncation — the standing correction after two prior undercounts)

`grep -rln "candidate_ledger" --include="*.py" .` across the whole repo, 13 files:

- **Production, calls only the public API** (never `_connect()` or `DB_PATH` directly):
  `services/candidate_retry.py`, `services/whale_stream/decision_bridge.py`. Two more
  files reference `candidate_ledger` only in comments, no import:
  `services/tick_executor.py`, `services/whalewatchers/_scoring_pool.py`,
  `services/whalewatchers/generic_rest.py`.
- **`decision_bridge.py`** is the safety-relevant one: `claim()` and `record_decision()`
  are called from inside `tick_executor.run(lambda: ...)`, the trading-critical decision
  path the plan's safety framing names. Read the two call sites directly (`:66`, `:129`)
  — both call the public function, neither touches `_connect()`'s return shape.
  **`decision_bridge.py` itself is not modified by this task.**
- **Tests**, 6 files, all monkeypatch `candidate_ledger.DB_PATH` (never `_connect()`
  itself except `tests/test_candidate_ledger.py`'s own new tests, all `with`-wrapped):
  `tests/test_candidate_ledger.py`, `tests/test_candidate_retry.py`,
  `tests/test_candidate_retry_integration.py`, `tests/test_generic_rest.py` (comment
  only), `tests/test_whale_candidate_lifecycle.py`, `tests/test_whale_stream_decision_bridge.py`.
- **`tests/support/runtime_isolation.py`**: lists `services.candidate_ledger` in
  `PERSISTENCE_MODULE_PATHS` (its own AST cross-check keeps this complete). `DB_PATH` the
  attribute is unchanged, so this entry needs no update.

No caller anywhere calls `_connect()` without `with`, so the change from
`def _connect() -> sqlite3.Connection` (plain function) to a
`@contextlib.contextmanager`-decorated generator is transparent to every one of them.

## TDD, reported honestly (same standard PR #540 held itself to)

Ran the plan's own three new tests together before editing the module:

```
test_connect_closes_its_connection            FAILED (AttributeError: module 'services.candidate_ledger' has no attribute 'db')
test_connect_still_creates_candidates_table    passed
test_connect_sets_explicit_busy_timeout_pragma passed
```

Only the first was genuinely red. The other two pass before and after by construction, not
by accident of a weak assertion: `_connect()` already creates `candidates` pre-migration
(so that test is a preservation pin, not new coverage), and this container's `sqlite3`
module defaults `busy_timeout` to 5000 already (verified directly:
`sqlite3.connect(tmp).execute("PRAGMA busy_timeout").fetchone()` → `5000` on a bare
connection, no PRAGMA set) — the same characteristic Task 5's PR body called out for its
own identical test. Calling these two "failing tests" would be false; they're named here
so the record doesn't imply otherwise.

After the fix: full file green (9/9, `.claude/worktrees/.../tests/test_candidate_ledger.py`).

## One deviation from the plan's literal test code

The plan's Step 1 snippet re-does `monkeypatch.setattr(candidate_ledger, "DB_PATH",
tmp_path / "cl.db")` inside each of the three new test functions. The file already has an
autouse `_isolated_db` fixture doing exactly this (to `tmp_path / "candidate_ledger_test.db"`,
functionally identical — a distinct path under the same per-test `tmp_path`). Dropped the
redundant per-test monkeypatch and relied on the existing fixture instead, matching this
file's own established convention and PR #540's identical judgment call on
`test_observability.py`. Recorded here, not silently done.

## Verification run

```
tests/test_candidate_ledger.py                                                  9 passed
+ test_candidate_retry.py, test_candidate_retry_integration.py, test_generic_rest.py,
  test_whale_candidate_lifecycle.py, test_whale_stream_decision_bridge.py,
  test_runtime_isolation.py, test_db.py                                        76 passed (0 failed)
```

Run twice: once before a container-wide ddev outage/restart interrupted the first attempt
(unrelated to this change — root-caused separately as a concurrent `ddev restart` race
between two other sessions, confirmed via `docker ps`/`ddev describe` before and after),
once clean after the container was confirmed stable again. Both runs, where completed,
showed the same result.

Full-suite confidence is Woodpecker's per CLAUDE.md, not re-derived locally; the PR will
cite the pushed commit's CI status rather than a local full-suite run.

## What this task does not do

- Does not touch `decision_bridge.py`, `tick_executor.py`, or any trading/risk/sizing/
  calibration/strategy/settlement/auth code. Stated explicitly per the coordinator's
  instruction not to let "same pattern as the others" stand in for saying this plainly.
- Does not change `candidate_ledger`'s public API, return types, or any caller's syntax.
- Does not touch `data/*.db` files, delete history, or resolve anything the plan didn't
  ask this task to resolve.
- Does not check off Task 8's plan-doc checkboxes, following the same established practice
  Tasks 1-5's PRs used (avoiding churn on a doc other in-flight task PRs also touch).

## Scope check

`git diff --stat`: `services/candidate_ledger.py` (40 lines changed),
`tests/test_candidate_ledger.py` (58 lines added). Nothing else.
