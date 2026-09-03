# Next action

**Single next action: execute
`docs/superpowers/plans/2026-09-03-persistence-layer-db-migration-implementation.md`**
(merged, PR #516 — 15 tasks, full review cycle at both artifact and PR stage).
Its planning pipeline is complete: research, design/spec, and implementation
plan are all merged, and the API-shape decision is signed off.

**Do not read a task list out of this file.** The plan is the task list, and
`gh pr list` plus `git log origin/main` are the only current record of what has
shipped. This section names the stage; it does not track progress inside it,
because a status snapshot in a file every session reads goes stale within the
hour and then actively misleads — that has now happened twice in one day.

**Gates that hold regardless of where execution has got to:**

- **Task 1 (`services/db.py`) blocks every other task.** It is Gate 0: the
  module plus its 16 tests must land before any module migrates onto it.
- **Tasks 6, 7 and 8 — `risk_manager.py`, `paper_broker.py`,
  `candidate_ledger.py` — are safety-adjacent and need a human go-ahead before
  starting**, not just at review. They touch the daily-loss kill switch and the
  broker. Each gets its own dedicated PR and a full diff review; "the pattern
  was mechanical for the last five modules" is exactly the reasoning that walks
  something past scrutiny, and it is not sufficient here.
- **Author and reviewer stay separate**, for code as for documents: whoever
  implements a task does not review it, and the adversarial pass is a fresh
  agent with no memory of writing it.
- `autotrade-73`'s `fix/db-foundation-must-fix-tests` (`e74096a`) is **input to
  Task 1, not Task 1** — Task 1 adopts its tests (including the lock-contention
  one) but not its path-keyed registry, which the design rejected.

## Reference state — 2026-09-03 ~12:15 UTC (facts, not progress)

**Merged today, on `main`:** Tier0 live-incident remediation (PR #501, fd-leak
fixes in five modules, verified live), Tier1 backend-hygiene/de-polling
(PR #500), the strategy-edge gate (PR #502, 10 tasks), and the four
persistence-layer research inputs — #504 (research), #507 (db foundation
audit), #509 (baseline measurement), #506 + #508 (Gate 1 pre-audits covering
all 26 in-scope modules).

**The API-shape decision is made and signed off** (coordinator review, PR #505
comments): a hybrid — callback schema registration keyed by **table name**
(`register_schema(table_name, init_fn)`, raising on a genuine conflict), plus
explicit `tables=` selection and a `busy_timeout_ms` override at
`connect()`. Neither pre-existing design was adopted wholesale: the prototype's
`db_path`-keyed registry was demonstrated (by running it) to break the
`monkeypatch.setattr(mod, "DB_PATH", tmp_path/...)` convention 64 test files
use, and PR #484's string-DDL registry needed a manual escape hatch in 3 of its
own first 3 migrated modules. Three conditions ride with the sign-off, for the
plan to carry: a Gate 0 global-table-name-uniqueness test; a Gate 1 call-site
shape check spanning **every importing module plus `tests/`**; and the
e74096a-is-input-not-Task-1 note above.

**Scope: 26 modules.** Verified counts, with falsifiers: 42 `CREATE TABLE`
names across those modules, 0 cross-file duplicates (41 by one reviewer's count
— `series_watcher` declares `book_snapshots` twice, sync and async, not a real
duplicate; 0 duplicates holds either way). All 26 `_connect()`s are bare-return
`-> sqlite3.Connection`, and all 112 production call sites are
`with _connect() as conn:` — sqlite3's `with conn:` is a *transaction* context
manager, never a closing one, which is the leak in one sentence and makes the
migration uniform. `services/diagnostics/store_stats.py` is **not** in scope
(already fixed, issue #210); migrating it would be a regression.
`tools/coordination_engine.py` is in scope at lowest priority, and is the one
module whose callers are bare-assignment: 24 of them, 2 production
(`tools/quality_coordination.py:584,:657`, zero `.close()` in that file) and 22
across four test files.

## Open, filed, not yet worked

- **Issue #510** — `services/reset/routes.py` contains no `await`, no
  `run_in_executor`, no `tick_executor` anywhere, so `POST /api/reset` and
  `GET /api/reset/preview` run nine in-scope modules' DB calls synchronously on
  the event loop, including `count_range` against `candidate_log.db` (3.6 GB)
  from a plain GET. Pre-existing, operator-triggered, independent of this
  migration, and explicitly **not** the cause of the standing stall pattern
  below. `autotrade-73` is writing its research doc; fix shape is PR #414's
  `tick_executor.run(...)` pattern.
- **Standing live condition, unexplained, being tracked not diagnosed:**
  sustained high worker CPU (139–187%) with `loop_watchdog` stalls accumulating
  at ~37–53/min, present continuously for hours. A worker replacement at
  11:56 UTC dropped container CPU to its session low (76%), which points at
  **process-state accumulation rather than steady load** — `autotrade-84` is
  tracking the new worker's CPU/stall growth curve from t=0 to test that.
- **`market_history.db` watch:** it was genuinely corrupt on 2026-09-02
  (recovered via SQLite `.recover`; original quarantined). A **new, single,
  unexplained** `disk I/O error` hit `market_history.py:234` at 12:07 UTC.
  Checked: `PRAGMA quick_check` returns `ok`, ownership is clean
  (`davidf:davidf`, PR #387's container-user fix holding), disk 23% used. The
  older `readonly database` / `unable to open` faults on this file are
  pre-PR-#387 history, not current. **Escalation threshold: a second
  `disk I/O error`, or any `malformed`, escalates immediately.**

## Process note for whoever resumes

Peer sessions work in `.claude/worktrees/`; the primary checkout is the
coordinator's and is parked on `main`. **A branch switch in the primary reloads
the live app** — one on 2026-09-03 at 11:56 UTC rewrote every `.py` that `main`
had gained since the switched-from branch forked, firing uvicorn `--reload`
twice and replacing the worker. Diagnose any unexplained reload with
`git reflog --date=iso` in the primary first.

`docs/SESSION_CRASH_RECOVERY.md` holds the per-role onboarding procedure if a
session is lost.

---

Superseded status snapshots are deliberately not kept in this file, because
`orient.sh` prints the whole thing into every session banner. Previous versions
are in git (`git log -p --follow docs/next-action.md`); parked decisions live in
`docs/open-decisions.md`, which the same banner prints separately.
