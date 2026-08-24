# Project Capability Router

This repository has reusable Claude workflows under `.claude/skills/`.
They are capabilities, not a checklist: **do not run every skill every
session**. Before non-trivial work, decide whether one or more are relevant
to the current task and load/use the relevant skill before acting.

## Available quality/reliability capabilities

- **quality-plan-task** — implementing or resuming a numbered task from the
  Quality Control Plane plan; re-ground against current HEAD, work one task
  at a time, TDD, verify, commit, stop.
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
- **integration-audit** — after several cross-cutting changes, after
  modularization, or before a checkpoint/release when wiring across routes,
  background tasks, persistence, frontend, config, and CI should be checked.
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
