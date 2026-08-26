# Project Capability Router

This repository has reusable Claude workflows under `.claude/skills/`.
They are capabilities, not a checklist: **do not run every skill every
session**. Before non-trivial work, decide whether one or more are relevant
to the current task and load/use the relevant skill before acting.

## Available quality/reliability capabilities

- **quality-plan-task** — implementing or resuming a numbered task from the
  Quality Control Plane work; re-ground against current HEAD, work one task
  at a time, TDD, verify, commit, stop.
- **frontend-modularization-task** — implementing or resuming a numbered task
  from the frontend modularization plan (Preact + signals + htm strangler-fig
  migration, schema-driven Config tab, charts module); reconstruct progress
  from `frontend/src/js/{panels,legacy}` and the `frontend-*` baseline
  ratchets, one task at a time, TDD, verify, commit, stop.
- **autonomous-quality-coordination-investigation** — **complete** (I0-I13,
  `docs/superpowers/plans/2026-08-25-autonomous-quality-coordination-investigation.md`).
  Investigated how Quality Control Plane findings could be reported,
  escalated, or mechanically remediated without stepping on active
  branches/PRs or adding GitHub write authority. Decision: report-only stays
  correct today (zero durable findings measured in the sample) —
  `docs/superpowers/research/2026-08-25-autonomous-quality-architecture-decision.md`.
  A persisted, read-only observation series is fully specified and planned
  but **not implemented**: `docs/superpowers/specs/2026-08-26-autonomous-
  quality-coordination-design.md` / `docs/superpowers/plans/2026-08-26-
  autonomous-quality-coordination.md` (9 TDD tasks, execute via
  `superpowers:executing-plans` or `superpowers:subagent-driven-development`
  when picked up — no bespoke orchestrator needed, unlike the investigation
  itself). No GitHub write authority exists anywhere in that plan either —
  see `.claude/rules/autonomous-quality-coordination-evidence.md` for the
  governing constraints any future write-lane decision must still satisfy.
- **root-cause-debugging** — unexpected bug, failing test, live incident,
  contradictory metrics, strange behavior, performance anomaly, or anything
  tempting a speculative patch. Prove root cause before changing behavior.
- **persistence-safety** — adding/changing SQLite, `DB_PATH`, state stores,
  migrations, retention, backup behavior, or tests that touch persistence.
- **kalshi-contract-review** — touching Kalshi REST/WS fields, message types,
  orders, fills, positions, lifecycle, rate limits, settlement, or API
  fixtures. Read `docs/kalshi/` before changing code.
- **observability-performance** — latency, CPU, polling, REST volume,
  WebSocket throughput, SQLite efficiency, caching, metrics, or optimization.
  Measure first; reuse existing instrumentation.
- **frontend-verification** — frontend JS, API consumption, generated bundle,
  browser behavior, user-visible error handling, or frontend CI.
- **ci-cd-guardrails** — creating or extending deterministic recurring checks.
  If a failure can be safely and reliably detected in a clean checkout, CI/CD
  is the default permanent owner; manual Claude execution is supplementary.
- **integration-audit** — after several cross-cutting changes, modularization,
  or before a checkpoint/release when wiring across routes, background tasks,
  persistence, frontend, config, runtime diagnostics, and CI should be checked.
- **session-handoff** — ending a substantial working session or preparing for
  `/compact`, `/clear`, or a fresh session. Leave exact HEAD, verification
  state, and next work reconstructable from git.
- **final-verification** — before claiming a large multi-module initiative is
  complete. Prove the guardrails themselves with safe fault injection, then
  run the full verification matrix.

Existing project skills remain authoritative for their own responsibilities,
including `/checkpoint`, `/config-field-edit`, `/run`, and
`/sync-status-docs`. Prefer extending/combining existing capabilities rather
than duplicating them.

## Standing investigation-to-guard rule

Whenever a real bug, logic gap, integration gap, data-integrity problem,
resource leak, API mismatch, performance pathology, test-isolation failure,
or recurring operational issue is discovered, do not stop at the immediate
fix. Before closing the work, classify how the same failure class should be
caught in the future:

1. permanent runtime diagnostic,
2. permanent CI/CD guard,
3. shared logic used by runtime and CI,
4. existing permanent guard already covers it,
5. genuinely one-off investigation, with the reason recorded.

If 1–3 is appropriate and reasonably scoped, ship or explicitly plan the
guard with the fix.

### CI ownership rule

A deterministic guard is **not permanently integrated** merely because Claude
knows how to run it manually.

If a check:
- does not require live application state,
- is deterministic enough for automation,
- can run safely in a clean checkout,
- and protects against a recurring failure class,

the default permanent owner is CI/CD.

When implementing or extending such a checker, wire it into the appropriate
GitHub Actions workflow in the same logical task unless there is a documented
reason not to. The task is not complete at "the checker works locally."

Network-dependent, slow, or upstream-canary checks should normally be
scheduled/manual workflows. Checks that require actual running-system state
belong in runtime diagnostics/observability. Some failure classes warrant
both, preferably sharing pure checking logic.

Claude/manual invocation is supplementary verification, not the permanent
owner of deterministic checks.

## Global boundaries

- Current code/tests/git history are implementation truth; do not duplicate
  functionality because an older plan says to create it.
- Never use tests to read/write live repository `data/*.db` files.
- Never enable real trading or weaken real-money safety gates as part of
  diagnostics, verification, or quality work.
- Heavy diagnostic/research work must not block the trading/WebSocket hot path.
- Unknown/insufficient evidence is preferable to a fabricated healthy result.
- Heuristic static-analysis findings are not hard CI failures until their
  confidence is demonstrated.
