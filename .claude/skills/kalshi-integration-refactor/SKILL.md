---
name: kalshi-integration-refactor
description: Use when implementing, resuming, auditing, or reviewing the dual-phase Kalshi integration boundary initiative. Reconstruct progress from current HEAD, execute exactly one genuinely incomplete A/C task, derive Kalshi semantics from mirrored official docs rather than memory, verify permanent CI/runtime ownership, commit, and stop.
---

# Kalshi Integration Refactor Task Execution

Canonical initiative files:

- `docs/superpowers/research/2026-08-24-kalshi-integration-audit.md`
- `docs/superpowers/specs/2026-08-24-kalshi-integration-boundary-design.md`
- `docs/superpowers/plans/2026-08-24-kalshi-integration-dual-phase.md`
- `docs/superpowers/plans/2026-08-24-kalshi-integration-phase-a.md`
- `docs/superpowers/plans/2026-08-24-kalshi-integration-phase-c.md`

If any canonical file is missing, stop before editing.

## Execution model

This skill is the initiative's top-level orchestrator.

Do not replace it with `superpowers:subagent-driven-development`,
`superpowers:executing-plans`, isolated-worktree orchestration, per-task agent ledgers, or
another progress system unless the user explicitly requests that model.

Installed Superpowers skills remain supporting disciplines. Use them selectively when
relevant, especially:

- `test-driven-development`
- `systematic-debugging`
- `verification-before-completion`
- `requesting-code-review`
- `receiving-code-review`
- `brainstorming`

Use repository project skills automatically when relevant:

- `kalshi-contract-review` for every Kalshi contract/field/endpoint/channel change;
- `ci-cd-guardrails` for deterministic recurring checks;
- `observability-performance` for hot-path/REST/WS/performance work;
- `persistence-safety` when DB/storage behavior is touched;
- `integration-audit` after cross-cutting migration groups;
- `checkpoint`, `session-handoff`, and existing repo workflows as appropriate.

## Phase rule

Phase A must pass the design spec's Phase A completion gate before any Phase C task
begins.

If Phase A is not fully proven, do not "start C while finishing A."

## Per-task procedure

1. Read `CLAUDE.md`, the canonical design/program docs, the exact current task, relevant
   module `CHEATSHEET.md`, exact mirrored Kalshi docs, current tests, and current CI
   definitions.
2. Re-ground:
   - `git status --short`
   - `git branch --show-current`
   - `git rev-parse HEAD`
   - relevant recent commits
   - current implementations of every file the plan names
3. Determine whether current HEAD already implements or supersedes any planned step.
   Current code/tests/git are implementation truth. Implement only the missing delta.
4. State the smallest remaining delta before editing.
5. For Kalshi semantics, identify the exact local official docs used. Never substitute
   memory for an available source.
6. Use red/green TDD where behavior can meaningfully be tested.
7. Preserve compatibility incrementally. Never perform an incidental big-bang rewrite.
8. Preserve raw payload archival while centralizing semantic interpretation.
9. Preserve all existing trading/risk/auth/CORS/data-safety gates.
10. Measure before changing hot-path performance characteristics.
11. For every real bug/failure class discovered, apply the repo's investigation-to-guard
    rule and assign runtime/CI/shared/existing/one-off ownership.
12. Any deterministic boundary/contract checker is incomplete until its CI owner invokes
    it and deliberate isolated failure proves the check.
13. Run targeted tests and the task's named verification. Run broader regression checks
    proportional to the blast radius.
14. Before commit:
    - `git diff`
    - `git diff --check`
    - `git status --short`
    - confirm no live `data/*.db`, `.env`, credentials, browser profiles, temp outputs,
      or unrelated artifacts are staged.
15. Commit exactly one independently testable logical task.
16. Report:
    - phase/task;
    - HEAD before;
    - exact local Kalshi docs consulted;
    - what already existed;
    - implementation delta;
    - files changed;
    - tests/verification;
    - CI/runtime guard changes;
    - compatibility-caller count change when applicable;
    - performance evidence when applicable;
    - new findings/disposition;
    - commit SHA;
    - next genuinely incomplete task.
17. Stop before beginning the next task.

## Progress state

Do not maintain a separate progress ledger.

Reconstruct progress from current code/tests, git history, numbered plans and acceptance
criteria, CI state, generated census/contract reports, and the repository's existing
checkpoint/session-handoff artifacts.
