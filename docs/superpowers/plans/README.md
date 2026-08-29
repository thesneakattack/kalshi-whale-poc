# Plans index

19 plans, ~21,700 lines. This file is the map. Read it before opening any plan,
and before citing a task number anywhere — a commit message, a PR body, an issue,
a session note.

## Two gotchas that have already caused wrong conclusions

**1. Task numbers are not unique. Every plan numbers its tasks from 1.**
Eight plans define `### Task N:` headings, so "Task 18" names a different piece of
work in each. On 2026-08-29 a session concluded the realtime plan's Phase P4 had
shipped because `git log | grep 'task 18'` returned commits — they were
`(QCP Task 18)`, from the Quality Control Plane. Phase P4 had never been written.

> **Cite a task as `<plan-slug> Task N`**, never as `Task N` alone.
> `realtime-data-plane-remediation Task 18`, not `Task 18`.
> Two plans already carry their own tag in commit subjects and those stay as they
> are: the realtime plan uses `(P<phase> Task <n>)`, the quality control plane uses
> `(QCP Task <n>)`. Everything else uses the slug form.

**2. The checkboxes lie. Do not read completion out of them.**
Across these 19 files there are **1,210 unchecked boxes and 140 checked** — while
most of the plans' branches have merged. The realtime plan has 4 checked boxes
against 26 tasks that demonstrably shipped.

This is not cosmetic: `tools/quality_coordination.py:305` treats an unchecked
`- [ ]` as the plan's own statement that a task is unfinished, which is why AQC
reports plans as perpetually in-flight. **The commit history and the source are
authoritative for what shipped; these files are not.**

## The plans

"Merged branch" is a mechanical fact — a merge commit naming that branch exists. It
is evidence of delivery, not proof that every task in the plan shipped; only the
realtime row below has been verified task-by-task.

| Plan | What it is | `### Task` | Merged branch |
|---|---|---|---|
| [realtime-data-plane-remediation](2026-08-25-realtime-data-plane-remediation.md) | Realtime Kalshi data-plane remediation, phases P0–P8 | 40 | `feat/realtime-data-plane-remediation` |
| [realtime-data-plane-investigation](2026-08-25-realtime-data-plane-investigation.md) | The investigation that produced the above | — | `chore/realtime-data-plane-investigation` |
| [quality-control-plane](2026-08-24-quality-control-plane.md) | Quality control plane (tagged `QCP Task N`) | — | — |
| [kalshi-integration-dual-phase](2026-08-24-kalshi-integration-dual-phase.md) | Kalshi integration boundary, program overview | — | — |
| [kalshi-integration-phase-a](2026-08-24-kalshi-integration-phase-a.md) | Contain and document the Kalshi boundary | — | — |
| [kalshi-integration-phase-c](2026-08-24-kalshi-integration-phase-c.md) | Consolidate and harden the Kalshi boundary | — | `refactor/kalshi-integration-phase-c` |
| [economic-strategy-effectiveness-investigation](2026-08-26-economic-strategy-effectiveness-investigation.md) | Economic strategy effectiveness + execution realism | — | — |
| [economic-strategy-remediation](2026-08-26-economic-strategy-remediation.md) | Candidate remediation for the above (Program 2) | — | — |
| [backend-services-modularization](2026-08-27-backend-services-modularization.md) | Backend `services/` modularization | 4 | `refactor/backend-services-modularization` |
| [frontend-modularization](2026-08-25-frontend-modularization.md) | Dashboard modularization | — | `docs/frontend-modularization-design` |
| [autonomous-quality-coordination-investigation](2026-08-25-autonomous-quality-coordination-investigation.md) | AQC investigation | — | `chore/autonomous-quality-coordination-investigation` |
| [autonomous-quality-coordination](2026-08-26-autonomous-quality-coordination.md) | AQC, report-only | 4 | `feat/autonomous-quality-coordination-workflow` |
| [autonomous-quality-coordination-workflow](2026-08-27-autonomous-quality-coordination-workflow.md) | AQC workflow | 11 | `feat/autonomous-quality-coordination-workflow` |
| [autonomous-engineering-mode](2026-08-26-autonomous-engineering-mode.md) | Autonomous engineering mode | — | `docs/autonomous-engineering-mode-spec` |
| [kanban-board-sync](2026-08-26-kanban-board-sync.md) | Kanban board sync | 13 | — |
| [kanban-sync-milestones-and-subissues](2026-08-27-kanban-sync-milestones-and-subissues.md) | Milestones + sub-issues for the board | 13 | — |
| [kanban-sync-improvements](2026-08-28-kanban-sync-improvements.md) | Closed-parent guard, classification guidance | 3 | `chore/kanban-sync-improvements` |
| [workflow-remediation](2026-08-27-workflow-remediation.md) | 2026-08-27 workflow audit remediation | 10 | `chore/workflow-remediation` |
| [active-tracks-board](2026-08-26-active-tracks-board.md) | Cross-session initiative board | — | `docs/active-tracks-board` |

Specs and designs live in `../specs/`, one per plan, same slug.

## realtime-data-plane-remediation — verified task state (2026-08-29)

The only plan whose task state has been checked one by one, against commit phase
tags and against the source itself.

- **Shipped:** Tasks 1–13 (P0–P2, untagged, predating the tag convention; confirmed
  in source by their plan-named interfaces `candidate_ledger`, `whale_gate`,
  `tick_executor`, `capture_writer`, `loop_watchdog`, `candidate_retry`), 14–17
  (P3), 17a/17b/17c (P3.5), 29, 30, 33 (P7), 34–39 (P8).
- **Task 20** (`check_exits` per-tick memoization) also shipped, out of phase and
  with no phase tag on its commit — `check_exits` takes `tick_cache` at
  `services/exits/exit_engine.py:111`. The plan had deliberately relocated it ahead
  of Tasks 18/19 after a live crash report.
- **Shipped 2026-08-29 (PR #198):** Tasks **18**, **19** and **24** — the
  critical/market queue split (behind `realtime_data_plane.two_consumer_mode`),
  ticker coalescing, and the batched deferred settlement resolver
  (`services/settlement_resolver.py`) — after the settlement-cascade drop
  root cause was confirmed. Boundary-verified live the same day.
- **Never implemented:** Tasks **21–23**, **25–28** (the rest of P4/P5/P6)
  plus **31**, **32**, **40**.

Checked individually, by each task's own named deliverable, not by assuming a range:
`_critical_queue`/`_consume_market` (18), `services/settlement_resolver.py` (19, 24),
`_connection_generation` (21), limiter priority queues (22), `trip_brake` (23),
`services/milestone_cache.py` (25), the `trade_tape_poll` caller class (26),
`on_loss_event` (27), `services/position/ws_state_verify.py` (28) — all absent from
`services/` and `main.py`. A first pass here generalized ‘18–28 unshipped’ from three
spot checks and got Task 20 wrong; the phase-tag sweep alone is not sufficient,
because tags were applied inconsistently.

Each remaining absence is independently decisive:

- Task 22 (critical-first waiter queues) is absent — `_TokenBucketRateLimiter` in
  `services/http_client.py` has a flat `waiters` counter and no priority queues.
- Task 31 is absent — `_fetch_account_snapshot`'s periodic REST poll still runs
  unconditionally inside the tick gather (`main.py`).

P4/P5 are where the competing solution families for the message-drop bottleneck are
already written: A isolate the consumer (18), B evict REST from it (19, 24), C fix
limiter scheduling (22, 23), D cut REST demand (25, 26, 27). Raising queue capacity
is the anti-move `CLAUDE.md`'s hard rule names. One family is already falsified in
place: `services/http_client.py:246` records that a global `asyncio.Semaphore`
bounding REST concurrency was tried for this exact bottleneck and did not work, down
to `Semaphore(5)`.

Those phases stalled because their empirical input never arrived: Task 17b's live
stress test failed all three attempts, root-caused in the plan itself to concurrent
Claude Code worktree sessions triggering `uvicorn --reload` mid-experiment.

## Constraints on editing these files

- **`### Task N: <title>` headings are parsed** by
  `tools/kanban_sync/plan_tasks.py:19` (`^### Task (\d+):[ \t]*(.+)$`). Renaming
  them to a namespaced ID breaks issue decomposition. Add the plan slug around the
  reference instead of inside the heading.
- **`- [ ]` / `- [x]` are parsed** by `tools/quality_coordination.py:305`. Checking a
  box off is a claim that the task shipped, so check one only against commit or
  source evidence — the same standard `CLAUDE.md`'s never-guess rule applies
  everywhere else.
