---
name: realtime-data-plane-investigation
description: Use to investigate persistent Kalshi WebSocket backlog/drops, REST rate-limit pressure, latency, and missed whale candidates. Reconstruct current state, measure before changing behavior, research best-practice solution families, benchmark competing designs, select an evidence-backed architecture, then write a separate implementation plan.
---

# Realtime Kalshi Data-Plane Investigation

Canonical files:

- `docs/superpowers/research/2026-08-25-realtime-data-plane-known-findings.md`
- `docs/superpowers/specs/2026-08-25-realtime-data-plane-investigation-design.md`
- `docs/superpowers/plans/2026-08-25-realtime-data-plane-investigation.md`
- `.claude/rules/realtime-data-plane-evidence.md`

If any are missing, stop before editing.

## Role

This skill is the top-level investigation orchestrator.

The goal is not to implement the assistant's favored architecture. The known-findings file
contains hypotheses only.

Your job is to discover the causal bottlenecks and choose the best repo-specific solution
through evidence.

## Relevant capability selection

Before each task, inspect available repository skills/plugins and use only those that
materially help.

Normally relevant:

- `root-cause-debugging`
- `observability-performance`
- `kalshi-contract-review`
- `ci-cd-guardrails`
- `integration-audit`
- `verification-before-completion`
- `test-driven-development`
- `systematic-debugging`

Use `brainstorming` during solution-family exploration.

Use `dispatching-parallel-agents` only for independent research tracks where parallelism
actually improves coverage. Synthesize their conclusions yourself; never accept a
subagent recommendation without reconciling it against current measurements.

Do not let a generic execution plugin replace this initiative's numbered task workflow.

## Per-task workflow

1. Read `CLAUDE.md`, canonical investigation files, relevant module reference docs
   (`README.md`/`CHEATSHEET.md`), exact mirrored Kalshi docs, current tests, current CI, and
   relevant recent git history.
2. Re-ground:
   - branch;
   - HEAD;
   - working tree;
   - current runtime topology;
   - current config values relevant to the task.
3. Determine whether current HEAD already implements/supersedes any plan step.
4. State the question being tested and what evidence would confirm/falsify it.
5. Prefer an experiment/test that distinguishes competing hypotheses.
6. Add instrumentation only when existing diagnostics cannot answer the question.
7. Keep instrumentation bounded and measure its own hot-path cost.
8. For Kalshi semantics, use exact mirrored official docs rather than memory.
9. For Python/`websockets` behavior, research current docs/source matching installed
   versions when necessary.
10. Use paper/shadow mode only for live experiments; quarantine experimental evidence when
    required by repo policy.
11. Record negative results. A falsified hypothesis is useful progress.
12. Do not make a structural production redesign before the solution-comparison tasks.
13. Run targeted verification, then broader checks proportional to blast radius.
14. Apply permanent runtime/CI ownership to measurements that expose recurring failure
    classes.
15. Before commit:
    - `git diff`
    - `git diff --check`
    - `git status --short`
    - no live DB, credentials, `.env`, browser state, large captures, or temp benchmark
      output staged.
16. Commit one logical investigation task.
17. Report:
    - task/question;
    - HEAD before;
    - docs/research consulted;
    - experiment;
    - result;
    - hypothesis status;
    - instrumentation overhead;
    - permanent guard impact;
    - commit SHA;
    - next task.
18. Stop.

## Solution-selection workflow

When the plan reaches solution exploration:

1. Enumerate at least three plausible families for each major confirmed bottleneck unless
   fewer are technically credible; explain why excluded families are not credible.
2. Research authoritative current practices.
3. Prototype outside the production path.
4. Benchmark all candidates against the same representative workload.
5. Fault-inject:
   - burst traffic;
   - transient REST 429/timeout;
   - reconnect;
   - queue pressure;
   - slow handler;
   - malformed message where relevant.
6. Score all candidates using the design spec's matrix.
7. Prefer the simplest candidate that meets correctness, capture, latency and operational
   requirements.
8. Write an architecture decision with rejected alternatives.
9. Only then invoke `writing-plans` to create the production implementation plan.

## No premature conclusion

Do not conclude:

- "use multiple queues";
- "use Redis/Kafka";
- "increase workers";
- "split connections";
- "increase the limiter";
- "use REST reconciliation";

until experiments show the mechanism and tradeoffs.

A sophisticated-sounding guess is still a guess.
