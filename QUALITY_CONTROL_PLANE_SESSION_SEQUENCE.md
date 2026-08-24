# Quality Control Plane — Claude Session Sequence

This is the intended operating sequence for the official Claude VS Code extension.

## Phase 1 — Fresh initiative session

Paste `claude-prompts/01-session-kickoff.txt`.

Expected outcome:
- Claude reads repository authority and initiative docs.
- Claude reads current git state/history.
- Claude compares current HEAD against the implementation plan.
- Claude reports what is complete/partial/unimplemented.
- Claude does not modify code.

## Phase 2 — Baseline

Paste `claude-prompts/02-baseline-only.txt`.

Expected outcome:
- Task 0 only.
- Full test collection.
- Full backend suite.
- Frontend install/lint/build.
- Generated bundle sync verification.
- CI workflow inventory.
- No Task 1 implementation yet.

If baseline exposes a real pre-existing failure, resolve that deliberately before proceeding.

## Phase 3 — Normal task execution

Paste `claude-prompts/03-begin-next-task.txt`.

Claude should:
1. identify the first genuinely incomplete numbered task;
2. re-read it;
3. inspect current files and recent relevant history;
4. state the implementation delta;
5. TDD the change;
6. run targeted and relevant broader verification;
7. inspect diff;
8. commit;
9. report exact evidence;
10. stop before the next numbered task unless your session instructions explicitly allow continuation.

Repeat this prompt for subsequent tasks.

## Phase 4 — Integration checkpoint

After 3–4 substantive tasks, paste `claude-prompts/04-integration-checkpoint.txt`.

Expected:
- full pytest;
- architecture audit;
- frontend lint/build if relevant;
- browser/API checks if relevant;
- git diff/status review;
- review of new persistent stores/routes/background services;
- confirmation that next task still makes sense.

Then resume using prompt 03.

## Phase 5 — Just-in-time guard prompts

### New bug / surprising behavior
Use `05-debug-surprise.txt`.

### New SQLite-backed service
Use `06-persistence-task.txt` *before* wiring it into broadly imported runtime code.

### Kalshi API-shaped change
Use `07-kalshi-api-task.txt` *before* coding or writing fixtures.

### Observability/performance
Use `08-observability-task.txt` before adding instrumentation or optimization.

### Frontend/browser/API integration
Use `09-frontend-task.txt` before implementation.

## Phase 6 — End of current coding session

Paste `10-session-end.txt`.

Expected:
- current task completed/verified/committed;
- exact HEAD;
- first incomplete task;
- test/build/audit state;
- no ambiguous half-completed work;
- status/roadmap/CHEATSHEET synchronization state;
- clean handoff.

## Phase 7 — New VS Code Claude session

Paste `11-resume-session.txt`.

Claude reconstructs progress from:
- CLAUDE.md;
- initiative docs;
- git history;
- current HEAD;
- latest status/pickup docs;
- relevant CHEATSHEETs.

It must not assume prior chat context.

Then use prompt 03.

## Phase 8 — Final verification

When all implementation tasks appear done, paste `12-final-verification.txt`.

Completion requires evidence, not a statement:
- full tests;
- frontend lint/build;
- quality audit;
- browser E2E;
- deliberate injected failures;
- live-data isolation check;
- no accidental data/secret changes;
- final review of baseline debt;
- final code review.
