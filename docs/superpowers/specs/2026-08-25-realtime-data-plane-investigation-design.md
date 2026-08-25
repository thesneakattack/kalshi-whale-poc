# Realtime Kalshi Data-Plane Investigation Design

## Status

Approved investigation design. This is intentionally **not** an implementation design for
a predetermined queue topology.

## Goal

Determine the actual causal chain behind persistent Kalshi latency, rate-limit pressure,
WebSocket backlog/loss, and missed whale opportunities, then select and document the
best-engineered remediation using measured evidence, authoritative documentation, and
comparative experiments.

## Investigation philosophy

The investigation must operate like engineering research rather than patch selection.

The sequence is:

```text
OBSERVE
  ↓
MEASURE
  ↓
REPRODUCE
  ↓
EXPLAIN CAUSALLY
  ↓
RESEARCH SOLUTION SPACE
  ↓
PROTOTYPE COMPETING OPTIONS
  ↓
BENCHMARK / FAULT-INJECT
  ↓
COMPARE
  ↓
SELECT
  ↓
WRITE IMPLEMENTATION DESIGN + PLAN
```

Do not invert that sequence.

## Claude reasoning/tooling model

Use the repository as the primary context and use all **relevant** installed capabilities
rather than running skills mechanically.

Top-level orchestration for this initiative:

- `.claude/skills/realtime-data-plane-investigation/SKILL.md`

Required/supporting project capabilities when applicable:

- `root-cause-debugging`
- `observability-performance`
- `kalshi-contract-review`
- `integration-audit`
- `ci-cd-guardrails`
- `persistence-safety`
- `final-verification`
- `checkpoint`
- `session-handoff`

Relevant installed Superpowers disciplines may be used:

- `brainstorming`
- `systematic-debugging`
- `test-driven-development`
- `verification-before-completion`
- `requesting-code-review`
- `receiving-code-review`
- `dispatching-parallel-agents` for **independent research questions only**
- `writing-plans` only after the investigation has selected an implementation design

Do not let generic Superpowers worktree/subagent/ledger orchestration replace the
repo-specific initiative workflow unless the user explicitly requests that.

Parallel agents are appropriate for independent research such as:

- Kalshi WebSocket/rate-limit semantics;
- Python asyncio/WebSocket queueing/backpressure best practice;
- realtime scheduling/priority architecture patterns;
- SQLite/thread/event-loop hot-path analysis.

Their results must be reconciled against the same repo measurements before adoption.

## Sources of authority

### Kalshi behavior

1. current local mirrored official Kalshi docs;
2. safe read-only observed behavior when needed;
3. official SDK implementation details;
4. repo contract fixtures/CHEATSHEET;
5. model memory only as hypothesis generation, never final authority.

### Python/WebSocket behavior

Use current authoritative upstream sources where research is needed:

- Python `asyncio` documentation;
- `websockets` library documentation matching the installed major version;
- library source/changelog where behavior such as receive buffering/backpressure depends on
  implementation details.

### Architecture research

Research recognized realtime patterns, but require repo-fit evidence before adoption.

Examples worth examining, not blindly selecting:

- staged pipelines;
- bounded queues and explicit backpressure;
- load shedding;
- coalescing state updates;
- work partitioning by key;
- actor/event-loop ownership;
- priority scheduling;
- weighted fair queues;
- reserved capacity;
- connection isolation;
- reconciliation/eventual consistency;
- single-writer state mutation;
- batch/async persistence.

External architecture literature is supporting evidence, not a substitute for benchmarks.

## Non-negotiable product constraints

- Exchange-wide whale discovery remains a requirement unless the user explicitly changes
  it.
- Real trading remains disabled during investigation.
- No safety/risk/auth/CORS/kill-switch weakening.
- Tests cannot touch live `data/*.db`.
- Raw research/history data must not be destroyed.
- Experiments must be quarantined from effectiveness/calibration evidence when appropriate.
- High-frequency diagnostics must be bounded and sampled where necessary.
- Do not add expensive runtime validation to the hot path.
- Do not claim success based on average latency alone.

## Primary success metrics

The investigation must establish baselines and candidate-solution results for:

### Whale capture

- REST-vs-WS trade capture completeness;
- whale-sized REST-vs-WS completeness;
- candidate lifecycle terminal-completion rate;
- number of candidates lost due to retryable enrichment failure;
- duplicate-decision rate.

### Latency

For whale candidates:

- wire/receive → prescan;
- receive → candidate;
- candidate → context ready;
- context ready → analytics complete;
- analytics → strategy decision;
- receive → final decision p50/p95/p99/max.

For critical non-trade messages:

- ticker age for open positions;
- fill processing delay;
- position processing delay;
- lifecycle processing delay.

### WebSocket capacity

- receive rate by type;
- service rate by type;
- queue depth/high-water;
- oldest queue age;
- queue wait p50/p95/p99/max;
- local drops by type;
- Kalshi error-25 count;
- reconnects;
- CPU/event-loop saturation indicators.

### REST budget

- limiter wait;
- network time;
- backoff time;
- total elapsed;
- 429 rate;
- demand by caller class;
- endpoint cost where known;
- background vs critical share of read budget.

## Solution quality criteria

Every serious solution candidate must be scored against the same matrix:

| Dimension | Requirement |
|---|---|
| Correctness | No new duplicate/missed trade decisions; ordering semantics explicit |
| Capture | Improves or preserves verified trade completeness |
| Whale latency | p95/p99 materially better under representative load |
| Critical events | Fill/position/lifecycle cannot be starved by ordinary trade traffic |
| Rate budget | Critical requests remain responsive under background load |
| Burst behavior | Bounded and observable; no silent infinite backlog |
| Recovery | Reconnect/drop/transient REST failure behavior explicit |
| Performance | CPU/memory/event-loop cost measured |
| Complexity | Minimum architecture needed for the measured problem |
| Maintainability | Clear ownership/interfaces, testable components |
| Observability | Failure mode remains diagnosable after deployment |
| Compatibility | Preserves current documented Kalshi boundary/safety model |

No candidate wins merely because it is "more scalable" in the abstract.

## Candidate solution exploration requirement

After root cause is established, Claude must generate at least **three meaningfully
different solution families** for each confirmed major bottleneck.

Examples:

### WebSocket data plane

Potential families:

1. optimize the current single-event-loop design with a much cheaper first-stage classifier;
2. staged queues by semantic traffic class;
3. split physical WebSocket connections plus per-class processing;
4. keyed worker/actor partitioning;
5. process isolation for CPU/DB-heavy analysis.

Only include a family if current measurements make it plausible.

### REST scheduling

Potential families:

1. optimize/remove unnecessary calls;
2. endpoint-cost-aware batching/coalescing;
3. priority-aware local scheduler;
4. weighted fair queue / reserved capacity for critical calls;
5. split background work onto explicit low-priority budgets.

### Loss/recovery

Potential families:

1. retryable candidate-state machine;
2. bounded REST reconciliation after known loss;
3. periodic sampled completeness auditing;
4. persistent candidate journal for at-least-once evaluation where necessary.

## Prototype rule

Do not implement competing architectures directly in production.

Use:

- deterministic replay;
- fixture-driven queues;
- fake rate limiters;
- captured/anonymized message-shape samples where safe;
- temporary benchmark harnesses under `tools/`;
- paper-mode controlled experiments.

A candidate only proceeds to final design after it outperforms the baseline on the
relevant metrics **without violating correctness/safety**.

## Required final artifacts

The investigation is complete only when it produces:

1. `docs/superpowers/research/...-root-cause-report.md`
2. `docs/superpowers/research/...-solution-comparison.md`
3. a written architecture decision explaining rejected alternatives
4. an approved implementation design spec
5. a separate implementation plan
6. permanent runtime/CI guards for the measurement classes that exposed the problem

The implementation plan must not be written until the solution comparison is complete.
