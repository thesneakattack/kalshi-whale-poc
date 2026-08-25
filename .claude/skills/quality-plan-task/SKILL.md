---
name: quality-plan-task
description: Use when implementing, resuming, or reviewing a Quality Control Plane task, or when the user says to continue that work. Re-ground against current HEAD, execute one genuinely incomplete task using TDD, permanently integrate deterministic recurring checks into CI/CD when appropriate, verify, commit, and stop before the next task.
---

# Quality Control Plane Task Execution

The canonical initiative documents are:

- `docs/superpowers/specs/2026-08-24-quality-control-plane-design.md`
- `docs/superpowers/plans/2026-08-24-quality-control-plane.md`

These files are required project context for Quality Control Plane work.
If either is missing, stop and report the missing file rather than inventing
or reconstructing the plan from memory/chat context.

## Execution method override

The technical tasks, architecture, acceptance criteria, safety constraints,
task ordering, and verification requirements in the Quality Control Plane plan
remain authoritative.

The original recommendation to execute this initiative using
`superpowers:subagent-driven-development`,
`superpowers:executing-plans`, isolated worktrees, per-task implementer or
reviewer subagents, a separate progress ledger, or the associated multi-round
whole-branch orchestration process is superseded by the repository's current
Claude workflow.

This skill, `.claude/skills/quality-plan-task/SKILL.md`, is the execution
orchestrator for the Quality Control Plane initiative.

Superpowers skills remain installed and available. Use them selectively as
supporting engineering capabilities when relevant, especially:

- `test-driven-development`
- `systematic-debugging`
- `verification-before-completion`
- `requesting-code-review`
- `receiving-code-review`
- `brainstorming`

Do not switch the initiative to the Superpowers worktree/subagent/ledger
orchestration model unless the user explicitly requests that execution model.

Do not create or maintain a separate Superpowers progress ledger for this
initiative. Reconstruct progress from:

- current code,
- current tests,
- current git history,
- current CI state,
- this implementation plan,
- relevant `CHEATSHEET.md` files,
- the repository's existing checkpoint/session-handoff mechanisms.

Where the implementation plan literally recommends
`subagent-driven-development`, `executing-plans`, isolated worktrees,
per-task implementation subagents, reviewer subagents, or the older heavy
execution process, treat only that execution-method recommendation as
superseded. Preserve the plan's technical requirements, task ordering,
acceptance criteria, safety rules, and verification requirements.

## Task execution

1. Read `CLAUDE.md`, the design spec above, the implementation plan above,
   and the exact current numbered task.

2. Re-ground the task against current HEAD:
   - `git status --short`
   - `git branch --show-current`
   - `git rev-parse HEAD`
   - relevant recent commits
   - every existing file the task proposes touching
   - relevant `CHEATSHEET.md`
   - current `.github/workflows/`
   Per `.claude/rules/branching-and-ci.md`: already on this initiative's
   branch → continue using it. On `main` → sync it and create one before
   implementing; the initiative stays on one branch across its numbered
   tasks, not one branch per task.

3. Determine what already exists.
   - The plan describes intent.
   - Current code and tests are implementation truth.
   - Never create a duplicate service because stale plan text says to.
   - If current HEAD already satisfies part or all of a task, verify the
     acceptance criteria and implement only the missing delta.

4. State the smallest remaining implementation delta before editing.

5. Identify which project-specific capabilities apply to the task.
   Load the corresponding repo skills when relevant, including:
   - `persistence-safety`
   - `kalshi-contract-review`
   - `observability-performance`
   - `frontend-verification`
   - `root-cause-debugging`
   - `ci-cd-guardrails`
   - `integration-audit`

6. Use Superpowers selectively as supporting process skills when relevant.
   In particular:
   - use TDD for behavior that can meaningfully be test-driven;
   - use systematic debugging for unexpected failures instead of speculative
     patching;
   - use verification-before-completion before declaring a task complete;
   - use code-review skills when the scope/risk warrants review;
   - use brainstorming only when genuine design uncertainty remains.

7. Use TDD where behavior can meaningfully be tested:
   - write or update the failing targeted test first;
   - run it and confirm the expected failure;
   - implement the smallest complete solution;
   - run the targeted test;
   - run the relevant module/regression tests;
   - run the relevant static quality/build/contract checks.

8. Explicitly verify wiring and integration where applicable:
   - router mounted;
   - scheduler/background path actually called;
   - state initialized;
   - persistence registered for test isolation;
   - frontend module imported;
   - generated bundle rebuilt and synchronized;
   - config value actually consumed;
   - checker actually invoked by CI when CI owns it.

9. Apply the repository's standing investigation-to-guard rule to every real
   new finding. Classify future detection as:
   - permanent runtime diagnostic/observability;
   - permanent CI/CD guard;
   - shared runtime + CI checking logic;
   - existing permanent guard already covers it;
   - genuinely one-off, with documented reason.

10. A deterministic recurring check is not considered permanently integrated
    merely because Claude can run it manually.

    If a check:
    - does not require live application state;
    - is deterministic enough for automation;
    - can run safely in a clean checkout;
    - protects against a recurring failure class;

    then CI/CD is the default permanent owner.

11. For every deterministic recurring guard created or extended:
    - implement the checker as testable code where reusable logic is
      appropriate;
    - add tests for the checker;
    - prove it catches an isolated deliberate failure;
    - prove valid repository state passes;
    - wire it into the appropriate GitHub Actions workflow in the same logical
      task unless there is a documented reason not to;
    - use a separately named CI job/check when that improves failure meaning;
    - verify the workflow actually invokes the checker and does not silently
      skip.

    A task is not complete merely because the checker works locally.

12. Runtime-only conditions must instead be integrated into application
    diagnostics/observability.

    Network-dependent, upstream, slow, or expensive checks normally belong in
    scheduled/manual CI.

    Some failure classes warrant both runtime and CI coverage, preferably
    sharing pure checking logic.

13. Preserve safety boundaries:
    - never let tests read or write live repository `data/*.db`;
    - never delete, reset, move, vacuum, or destructively migrate historical
      DBs for testing or verification;
    - never enable real trading for verification;
    - never weaken typed trading confirmation, kill switches, CORS, execution
      risk gates, or account safety controls;
    - read relevant `docs/kalshi/` documentation before making Kalshi-shaped
      assumptions;
    - keep heavy diagnostics/research work off trading and WebSocket hot paths.

14. Before committing:
    - run targeted tests;
    - run relevant broader regression tests;
    - run relevant frontend lint/build/contract/browser checks if applicable;
    - run the applicable quality checker;
    - inspect `git diff`;
    - run `git diff --check`;
    - run `git status --short`;
    - verify no live DBs, `.env`, credentials, browser profiles, temporary
      files, logs, or unrelated generated output are staged.

15. Commit one independently testable logical task with a descriptive commit
    message.

16. Report:
    - task number and name;
    - HEAD before the task;
    - what current HEAD already contained;
    - exact implementation delta;
    - files changed;
    - tests added or modified;
    - targeted and broader verification results;
    - CI/CD changes;
    - runtime diagnostic/observability changes;
    - new findings and permanent guard disposition;
    - commit SHA;
    - first genuinely incomplete next task.

17. Stop. Do not silently begin the next numbered task. Push the commit on
    the initiative branch (never `main` directly); leave the PR unopened/
    unmerged between tasks unless this task was the initiative's final
    task — only then open/merge per `.claude/rules/branching-and-ci.md`'s
    "Integration lifecycle."
