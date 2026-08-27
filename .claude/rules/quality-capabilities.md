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
  A persisted, read-only observation series was fully specified and planned
  in `docs/superpowers/specs/2026-08-26-autonomous-quality-coordination-
  design.md` / `docs/superpowers/plans/2026-08-26-autonomous-quality-
  coordination.md` (originally 9 TDD tasks, executed via
  `superpowers:executing-plans`/`superpowers:subagent-driven-development` —
  no bespoke orchestrator needed, unlike the investigation itself) and **is
  now implemented** — see the **quality-coordination-observation** bullet
  immediately below and `tools/quality_coordination.py` itself. No
  GitHub write authority exists anywhere in that plan either — see
  `.claude/rules/autonomous-quality-coordination-evidence.md` for the
  governing constraints any future write-lane decision must still satisfy.
  **Corrected same day (plan Task 15):** the module was originally built
  wired into the trading application (a `main.py` scheduler, a
  `config/settings.yaml` entry, app-owned API routes) — a real
  misunderstanding of this feature's own purpose (workflow/tooling quality
  control, not application behavior). Fully decoupled: see `CLAUDE.md`'s
  "Workflow/tooling and application code must never overlap" standing rule.
  **Corrected again, more fundamentally, 2026-08-27:** even the corrected,
  decoupled module still audited the wrong *subject* — the trading
  application's own static code findings, not "the automated workflow
  itself." Direct user correction: this was always meant to be an
  automated project-manager/janitor over this repo's own engineering
  workflow (branches/PRs/CI, `superpowers` plans/ledgers, standing-rule
  compliance), informed by (not auditing) the app's own diagnostics. The
  existing module is kept, renamed to `tools/quality_ratchet.py` (see the
  next bullet) — "AQC" now names only the tool specified in
  `docs/superpowers/specs/2026-08-27-autonomous-quality-coordination-
  workflow-design.md`, not yet implemented.
- **quality-ratchet** (formerly `quality-coordination-observation`,
  renamed 2026-08-27) — `tools/quality_ratchet.py`, a standalone workflow
  tool (not application code — see the standing rule in `CLAUDE.md`), a
  read-only persisted observation series over `tools.quality_audit`'s
  static findings (identity/persistence/suppression policy from the
  autonomous-quality-coordination investigation, I8/I11 — that investigation's
  *mechanics* are reused here even though its *target* is now understood
  to have been the wrong one, see above). No GitHub write authority
  exists, and no application coupling of any kind — invoke directly
  (`python -m tools.quality_ratchet`) or inspect
  `tools/quality_ratchet_data/quality_ratchet.db` directly; there is no
  API route and no dashboard view.
- **economic-strategy-effectiveness-investigation** — **substantially complete**
  (E1-E7, E11-E12 done with real evidence; E8-E10 explicitly scoped-not-
  executed or closed-infeasible —
  `docs/superpowers/plans/2026-08-26-economic-strategy-effectiveness-
  investigation.md`'s status table). Investigated where economic edge is
  created or destroyed between a raw Kalshi whale print and an actually
  executable trade — gate marginal contribution, adverse-selection root
  cause, advisory/calibration objective alignment, execution realism,
  capture-health-tagged replay gaps. Finding: the originally-reported
  88.8%/394-signal vs. 58.3%/12-trade adverse-selection gap
  (`ROADMAP.md`, measured 2026-08-17) does not currently reproduce — that
  exact trade sample is unrecoverable (`paper_broker.db` was reset twice
  since) and the gate configuration that produced it no longer exists;
  under the live configuration, `selection_delta_pts` is currently
  *positive* —
  `docs/superpowers/research/2026-08-26-economic-strategy-effectiveness-
  status-report.md`. A banded, cost-aware gate-EV design and four other
  remediation candidates are specified but **not implemented**:
  `docs/superpowers/specs/2026-08-26-economic-strategy-remediation-
  design.md` / `docs/superpowers/plans/2026-08-26-economic-strategy-
  remediation.md` — one of that plan's two preconditions (Program 1
  merged) is now satisfied (`main`@`22d1a79`, 2026-08-26); the other
  (explicit human review/approval of the design doc) is not, and E1-E7's
  underlying queries still need re-running against then-current data
  before implementation starts, per that plan's own "when this plan is
  picked up for real" instruction.
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
`/close-roadmap-item`. Prefer extending/combining existing capabilities
rather than duplicating them.

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
