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
- **RESOLVED — the standing CPU/stall condition was diagnosed and fixed.**
  Root cause (research: PR #519): `_process_stream_ticker` in
  `services/whale_stream/whale_stream_handlers.py` called `strategy.check_exits`,
  `broker.check_pending_fills` and `position_netting.review` **synchronously per
  WS ticker message** across 700+ markets, with no `await` between the
  running-check and `bump_generation()` — no cooperative yield point, so the
  backlog starved every other coroutine including the tick loop's own
  continuation. Fixed in PR #526 by a global min-interval throttle
  (`kalshi.ticker_exit_check_min_interval_sec`, default 2.0) around that block;
  **at least an order-of-magnitude reduction in aggregate blocking cost**,
  confirmed by two independent benchmarks (500-call synthetic loop) that agree
  exactly on the call-count mechanism — 500/500 invocations reach the block
  before the fix, 1/500 after, in both runs — but whose after-fix wall-clock
  times diverge 8.3× (1.25 s vs 0.150 s for the same loop), attributed to
  container-load variance between runs rather than a methodology difference.
  Don't quote a specific multiplier (24×/230×) as fact. Worst-case exit
  latency is **unchanged** — `main.py`'s `safety_net_interval_sec` (30 s) already
  bounded it and shares no state with the throttle. Historical peak symptoms, for
  recognising a recurrence: a **1,251 s tick**, positions stale 23 min, ingest
  `queue_depth` 19,656 against a 20,000 cap, `queue_wait` averaging 912 s.
- **Issue #530 — at least 63 of 88 async route handlers block the event loop**
  (corrected from an earlier 58/89: the adversarial review found undercounts in
  3 of 7 spot-checked files; "at least" because the detection method is pattern
  matching and cannot see indirect calls — `services/quality/routes.py:40` reaches
  a blocking read through `observability.runtime_findings()`, one level removed
  from what the grep matched). A census of all 17 `routes.py` files plus `main.py`
  found blocking is the *dominant* pattern in the route layer, not an exception.
  Worst confirmed: **`/api/quality/summary` — 358 calls (18.3/hr) at 11.59–33.35 s
  across 3 independent measurements, no caching found, ~1.2–3.3 event-loop-blocked
  hours (6–17 % of the 19.6 h observation window) — a range built on variable
  per-call cost, not a fixed constant** (undispatched `alerting.active_alerts()`,
  `fault_log.summary()`, `research.latest()`, and the indirect `runtime_findings()`
  path above; note `diagnostics.run_offline()` is **not** implicated — PR #424
  already made it async). **Severity is not uniform and the count is a poor
  guide:** `/api/state` is the most-polled endpoint yet costs 0.06–0.52 s.
  Prioritise by cost × call-frequency, using the nginx access log for frequency,
  not intuition. **Fixing all 63 is explicitly not the recommendation** — see
  `tick_executor.connection_for()`'s deliberate non-wiring and PR #424's
  built-then-reverted pool. Full census: PR #537 (open).
- **Issue #532 — `rejection_events` (22.6 M rows) is one table outgrowing three
  access patterns**, not three independent findings: the 34 s `count_range`
  (#510/#512), a `population_gate_summary()` scan that went ~4.8 s → 24–31 s as
  the table grew 6.2 M → 22.6 M since 2026-08-26 (#410 — **already dispatched and
  cached, so this is capacity, not a missing `tick_executor.run`**), and the
  3.6 GB store that held Task 3's leaked handles. No fix proposed: retention
  discards accumulated history, which is a first-class asset and a human
  decision.
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

**Merging is not deploying.** `gh pr merge` is remote-side; the live app runs
from the primary's working tree, which only changes when someone pulls — and
that pull *is* the deploy, since it rewrites the bind mount and fires the
reload. A merged PR can sit un-deployed indefinitely. **This already happened**:
after PR #522 merged, a coordinator-directed fd-leak check on `candidate_log.db`
was run and read against a worker process that had already exited — the check's
own PID-liveness method (inode ctime, which drifts and can misreport a running
process's age) misidentified which process was current. The mistake was caught
by cross-checking `/proc/<pid>/stat` field 22 (real start time) against
`/proc/stat`'s `btime`, which identified the actual deployed worker; the check
was then redone against that PID and passed cleanly. **This exchange lived only
in cross-session chat, never a PR comment or doc**; an adversarial reviewer of
a later doc citing this could not corroborate it from the repo alone, which is
itself evidence for why it needs saying here with this much specificity.
Verify a deploy with **both**
`git merge-base --is-ancestor <merge sha> HEAD` **and** a `WatchFiles detected
changes in … <file>` line in `ddev logs -s fastapi` — only the log line proves
the running process picked it up. Wait for the merge commit's own CI (it is a
new, untested combination, not the already-green PR head), and never measure a
worker in its first few minutes: `last_tick_duration_sec` reads `None` and
`open_fds` is artificially low during warm-up. When identifying *which* process
is the current worker, use `/proc/<pid>/stat` field 22 + `/proc/stat`'s `btime`
for real start time — not inode ctime, and not a `docker top`-reported PID
without confirming it resolves inside the container's own PID namespace.

`docs/SESSION_CRASH_RECOVERY.md` holds the per-role onboarding procedure if a
session is lost.

---

Superseded status snapshots are deliberately not kept in this file, because
`orient.sh` prints the whole thing into every session banner. Previous versions
are in git (`git log -p --follow docs/next-action.md`); parked decisions live in
`docs/open-decisions.md`, which the same banner prints separately.
