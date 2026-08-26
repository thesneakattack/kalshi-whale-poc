# Kalshi Autotrader — Personal Production Execution Program

**Repository:** `thesneakattack/kalshi-whale-poc`  
**Baseline audited:** `main` @ `34f26334cc094a2b611fd995de589bdf81a4b957`  
**Baseline date:** 2026-08-26  
**Remote work state at audit:** only `main`; no open PRs  
**Target:** personal, single-user, production-grade Kalshi autotrader for one trusted operator, eventually capable of autonomous real-money trading.

---

## 1. Purpose

This is the canonical program-level sequencing plan for moving the repository from a sophisticated paper/research system toward a personal production autotrader.

It is deliberately **not** another broad repo audit, a replacement for completed investigations, a duplicate realtime remediation plan, a new frontend framework decision, an Autonomous Quality Coordination reinvestigation, permission to enable live trading, permission to run all production work in parallel, or an enterprise/SaaS scaling roadmap.

It answers:

1. What gains are already real and merged?
2. What research/planning leverage exists without runtime implementation?
3. Which existing execution plans are safe to run, need refresh, or need correction?
4. Which missing investigations genuinely precede implementation?
5. In what dependency order should the production program execute?

The governing dependency is:

> **information integrity → economic decision validity → canonical decision semantics → real execution → operator/control surface → production operations → capital qualification**

---

# 2. Status taxonomy

Every capability must be described with one of these states.

| State | Meaning |
|---|---|
| **MERGED + OPERATIONAL** | Code/tooling is on `main` and affects current development/runtime behavior. |
| **MERGED RESEARCH LEVERAGE** | Investigation/spec/measurements are on `main` and can improve future work, but the resulting runtime feature is not implemented. |
| **IMPLEMENTATION PLAN READY** | A detailed execution plan exists, but implementation tasks have not run. |
| **PLAN REQUIRES REFRESH** | Direction remains valid, but current `main` or later evidence requires a limited refresh before execution. |
| **INVESTIGATION REQUIRED** | The problem is real, but no sufficiently rigorous investigation/spec/plan exists yet. |
| **QUALIFICATION REQUIRED** | Implementation exists but still needs runtime/shadow/real-exchange evidence. |
| **PRODUCTION QUALIFIED** | Evidence supports allowing the capability to carry its intended production responsibility. |

> **Research completion does not mean implementation completion. Implementation completion does not mean production qualification.**

---

# 3. Current repository baseline

At this baseline:

- PR #12 — realtime data-plane investigation — merged.
- PR #13 — docs/research CI fast path — merged.
- PR #14 — personal-production target declaration — merged.
- PR #15 — Autonomous Quality Coordination investigation — merged.
- PR #16 — modular/generated status-page workflow — merged.
- PR #17 — factual production-readiness doctrine corrections — merged.
- PR #18 — AQC investigation checklist closeout — merged.
- No open PRs.
- Remote branch inventory contains only `main`.

This is a good point to begin execution because the previous parallel investigations are no longer sitting in competing branches.

---

# 4. Gains already real before executing the remaining plans

This section is intentionally strict. These are gains already on `main` and usable now.

## 4.1 Quality Control Plane — MERGED + OPERATIONAL

Persistent project guidance exposes a real self-observation layer, including:

- `GET /api/quality/summary`
- `GET /api/health/pipeline`
- `GET /api/health/faults`
- `GET /api/observability/summary`
- `GET /api/health/storage`
- `POST /api/health/storage/scan`
- `python -m tools.quality_audit`

### Gain already realized

Future implementation sessions do not need to begin by inventing ad-hoc scripts for basic wiring, failure, growth, and health questions.

The QCP already supplies composite health, scheduler state, fault history, observability trends, storage inventory/integrity, static architecture/contract findings, and baseline-ratcheted finding semantics.

### Execution leverage

Before creating a one-off diagnostic:

1. query existing QCP/runtime health;
2. run the existing static quality audit;
3. check whether the measurement already exists;
4. add a permanent guard only for a genuinely uncovered bug class.

---

## 4.2 Investigation-to-guard discipline — MERGED + OPERATIONAL

A real bug class found by an investigation must be dispositioned into runtime diagnostics, CI guard, shared validation, already-covered protection, or explicitly one-off measurement.

### Gain already realized

The project is less likely to accumulate throwaway research scripts that disappear after one session.

Realtime investigation already demonstrates this pattern with replay harnesses, ingest metrics, whale timing, REST/limiter attribution, capture diagnostics, and pinned regression cases.

---

## 4.3 Realtime measurement/replay tooling — MERGED + OPERATIONAL

The **realtime remediation itself is not implemented**, but its investigation merged durable evidence infrastructure, including:

- `tools/realtime_pipeline_replay.py`
- `tools/rest_scheduler_replay.py`
- WebSocket ingest metrics
- whale pipeline stage timing
- REST latency/limiter attribution
- rate-limit probing
- trade-capture diagnostics
- candidate-lifecycle tests
- replay review fixes
- fault-oriented measurement cases

### Gain already realized

The remediation can be measured against deterministic reproductions instead of “feels faster.”

### Not yet realized

Still planned, not implemented:

- reader-side filtering;
- critical/market WS consumer separation;
- off-loop synchronous tick work;
- batched capture writer;
- durable candidate ledger gating `evaluate`;
- critical-first REST scheduling;
- global 429 brake;
- reconnect reconciliation.

Do not credit these as current runtime gains until the remediation executes and is measured.

---

## 4.4 Docs/research CI fast path — MERGED + OPERATIONAL

PR #13 reduces heavy-suite cost for branches whose **cumulative diff** remains docs/Claude-context-only while preserving required status contexts.

### Gain already realized

Research threads can iterate substantially faster without weakening code-change CI.

### Use it for

- Economic Strategy Effectiveness research;
- Canonical Decision/Execution research;
- Production Operations research;
- adversarial/self-review documentation passes.

Do not use it to justify reduced CI on real code changes.

---

## 4.5 Generated status-page workflow — MERGED + OPERATIONAL

PR #16 changed `static/status.html` into generated output assembled from `docs/status-src/`.

Already operational:

- `tools/build_status_page.py`
- source fragments under `docs/status-src/`
- CI drift check
- updated `/sync-status-docs`
- `.claude/worktrees/` exclusion from project-manifest scans

### Gain already realized

Large execution programs can update history with lower context/merge cost, and parallel worktrees no longer inflate project-manifest counts.

### Requirement

Plans written before PR #16 must be refreshed if they instruct agents to edit `static/status.html` directly.

---

## 4.6 Autonomous Quality Coordination research — MERGED RESEARCH LEVERAGE

PR #15/#18 completed the AQC investigation.

The investigation produced evidence around:

- finding identity stability;
- location-free automation identity;
- active-work suppression precedence;
- persistence floors;
- branch/PR overlap behavior;
- reporting economics;
- credential/write-lane threat analysis;
- fault injection;
- event filtering;
- write-gate feasibility;
- deterministic remediation inventory;
- adversarial architecture review.

Selected architecture:

> **report-only / observation-first; no GitHub write lane now.**

### Gain already realized

Future work can immediately apply the AQC coordination doctrine manually:

- do not act on findings overlapping active work;
- distinguish transient branch-local findings from durable integrated-main debt;
- do not grant GitHub write authority without new evidence;
- do not duplicate work already owned by another initiative;
- prefer stable automation identity over unstable raw location-sensitive IDs;
- require stale-SHA/idempotency/write gates for any future write-capable automation;
- remeasure repository cadence before increasing automation authority.

### Not yet realized

The following runtime capability is **not implemented**:

- `services/quality_coordination.py`
- `data/quality_coordination.db`
- persisted coordination observation history
- coordination API routes
- periodic observation scheduling
- coordinator health/staleness surfacing

Therefore AQC has improved **execution discipline**, not delivered runtime autonomous coordination yet.

---

# 5. Existing implementation plans — safety assessment

## 5.1 Realtime Data-Plane Remediation

**Plan:** `docs/superpowers/plans/2026-08-25-realtime-data-plane-remediation.md`  
**State:** **IMPLEMENTATION PLAN READY**, subject to short HEAD preflight.

### Why ready

Its investigation measured real busy-hour behavior, reproduced it deterministically, separated handler cost from loop-stall cost, falsified misleading fixes, corrected its own replay artifacts, identified correctness defects, and selected a feature-flagged architecture with rollback boundaries.

### Current measured evidence

- oldest queued message p50 ≈ 119 s;
- whale receive→decision ≈ 94 s average;
- 10.7% app-queue loss in the measured busy hour;
- whale-sized capture 107/309;
- 0/85 during one drop episode;
- 373,113 prints → 772 candidates;
- reader-side pure count filtering + loop hygiene modeled candidate p95 ≈ 20 ms;
- no durable downstream `trade_id` idempotency;
- transient enrichment can permanently lose a candidate;
- critical REST traffic can wait behind background work despite low aggregate budget usage.

### Preflight only

Before Task 1:

- branch from current `main`;
- verify referenced source paths/signatures;
- update status-doc instructions for `docs/status-src/`;
- mark anything independently satisfied later;
- verify no later merge contradicts the chosen architecture;
- do not redesign merely because the plan predates a few docs merges.

### Verdict

> **Execute first. Do not reinvestigate.**

---

## 5.2 Frontend Modularization

**Plan:** `docs/superpowers/plans/2026-08-25-frontend-modularization.md`  
**State:** **PLAN REQUIRES REFRESH**, architecture still sound.

T0 research/spec/plan/orchestrator is complete. The actual migration remains largely unexecuted:

- frontend graph/contract guards;
- Preact/signals/htm/uPlot runtime;
- legacy move;
- pilot System Health panel;
- core polling/WS/view ownership;
- config schema API;
- config validation;
- schema-driven Config panel;
- charts/history;
- remaining panel strangler migration.

### Why defer

The operator console must ultimately represent backend concepts that earlier production work will define or materially change:

- realtime health/freshness;
- execution authority;
- durable trade intent;
- order lifecycle;
- partial fills;
- unknown outcomes;
- reconciliation;
- canonical paper/shadow/live state.

### Refresh before execution

- remove doc tasks already satisfied;
- update generated-status assumptions;
- verify CI/branch-protection assumptions;
- rebaseline import graph/window-export counts;
- add canonical execution/realtime-health presentation requirements.

### Verdict

> **Keep the plan; execute after canonical trading/execution semantics stabilize.**

---

## 5.3 Autonomous Quality Coordination implementation

**Plan:** `docs/superpowers/plans/2026-08-26-autonomous-quality-coordination.md`  
**State:** **PLAN REQUIRES ARCHITECTURAL CORRECTION**.

### Keep

- report-only authority;
- no GitHub credential;
- no issue/PR writes;
- external automation key;
- one owned SQLite store;
- DB test isolation;
- content-fingerprint idempotence;
- active-work suppression;
- persisted observation history;
- read-only API exposure.

### Unsafe as written

Task 6 proposes:

1. `quality_coordination.enabled: true`;
2. `_maybe_run_quality_coordination(cfg)` in the main trading scheduler tick;
3. synchronous `fetch_branch_signals()`;
4. `urllib.request.urlopen(..., timeout=5.0)`;
5. potentially multiple branch compare calls;
6. synchronous `observe_main(...)`;
7. synchronous full `tools.quality_audit.run_audit(...)`.

That conflicts with the later realtime investigation's key invariant: unrelated synchronous work on the shared trading loop can create the exact starvation/backlog failure being remediated.

### Required correction

- remove AQC execution from the trading-critical tick;
- default disabled until runtime-cost proof;
- prefer standalone scheduled CLI/process;
- second choice: isolated worker/thread;
- use `asyncio.to_thread` only if the complete iteration is isolated and a separate process is unjustified;
- add noninterference measurement.

### Verdict

> **Do not execute as written. Preserve the investigation; revise runtime integration only.**

---

# 6. Missing investigation programs

## 6.1 Economic Strategy Effectiveness & Execution Realism

**State:** **INVESTIGATION REQUIRED**

### Core question

> Where is economic edge created or destroyed between the raw exchange event and an actually executable trade?

Trace:

`raw event → capture health → candidate → signal → confidence/features → each gate → selected trade → decision-time market → latency → fillability → fees/slippage → exit/settlement → net result`

### Required evidence

#### Gate marginal contribution

For every meaningful gate:

- sample before/after;
- unit-cost distribution;
- expected EV;
- realized EV where valid;
- win rate as diagnostic only;
- latency added;
- drawdown/risk effect;
- sample-size sufficiency.

#### Adverse-selection root cause

Current evidence:

- 394 KXBTC15M whale signals: 88.8% correct;
- 12 selected trades: 58.3%.

Do not infer a culprit from 12 trades. Decompose gate combinations, cost bands, cooldowns, market context, and interactions.

#### Advisory/calibration objective audit

Classify every recommendation/tuning mechanism as:

- economically aligned;
- partially economic;
- accuracy/calibration only;
- unknown.

#### Execution realism

Use retained order-book/trade data where supported:

- spread;
- depth;
- fill probability;
- IOC no-fill;
- partial fill;
- fees;
- latency-induced movement;
- alpha decay.

Do not fabricate precision absent historical state.

#### Replay gap analysis

Determine what is reconstructable now, what gates cannot be replayed honestly, and what additional contemporaneous state must be captured.

### Capture-health rule

Historical intervals must be tagged:

- healthy;
- measurably degraded;
- completeness unknown;
- known saturated/lossy.

Do not silently mix loss-correlated periods with healthy samples.

### Deliverables

- research report;
- adversarial analysis;
- design/spec;
- consolidated implementation plan;
- permanent measurement/guard disposition;
- explicit insufficient-sample list.

---

## 6.2 Canonical Decision + Shadow + Live Execution

**State:** **INVESTIGATION REQUIRED**

Current `services/execution.py` explicitly discloses that normal strategy code does not call real `create_order`; real execution orchestration today is emergency flattening.

### Core design

Define one canonical strategy output:

`TradeIntent` / `ExecutionIntent`

Then:

`market/signal state → strategy → canonical intent → paper | shadow | live`

### Map all entry paths

At minimum:

- follow-the-whale;
- settlement edge;
- pending limit-fill behavior;
- any other path capable of opening a position.

### Required live executor semantics

- durable intent identity;
- stable client order ID;
- submit;
- acknowledgement;
- outcome unknown;
- partial fill;
- filled;
- cancel;
- close;
- reconciliation;
- restart recovery;
- stale order;
- settlement.

### Authority

Real Kalshi account/order/position state wins disagreements with local derived state.

### Deliverables

- current-path map;
- canonical intent schema;
- paper/shadow/live ownership model;
- live-order state machine;
- restart/fault semantics;
- design/spec;
- adversarial review;
- implementation plan.

---

## 6.3 Personal Production Operations

**State:** **INVESTIGATION REQUIRED LATER**

Constraint: one user, one Kalshi account, one primary production host.

Scope:

- deployment topology;
- supervision;
- restart/reboot;
- TLS;
- secrets;
- single-user auth;
- session policy;
- alert destination;
- off-host backup;
- restore test;
- DB corruption/recovery;
- exchange/network outage;
- stale market/account state;
- health-driven entry interlocks;
- production capital/risk policy.

Explicit exclusions unless evidence changes:

- Kubernetes;
- microservices;
- Kafka;
- multi-region;
- distributed DB;
- multi-tenant auth;
- enterprise HA.

---

# 7. Dependency-backed production sequence

## Program 0 — Doctrine truth correction

### Objective

Narrowly fix the remaining project-level implication that “all P0 code-level gates are done / remaining work is operational.”

### Why

Every Claude execution session reads `CLAUDE.md`. That wording can underweight realtime code work, autonomous execution, strategy validation, reconciliation, replay, and execution realism.

### Correct classification

> Historical real-money **safety primitives** are substantially shipped. Personal production readiness is not. Production-critical realtime, strategy/economic, execution, validation, and operational work remains.

Update analogous ROADMAP framing.

### Do not

- restart the production audit;
- write another giant doctrine;
- change runtime/config;
- rewrite historical archives.

### Exit gate

A fresh Claude session can no longer infer that production is primarily an ops/deployment task.

---

## Program 1 — Realtime Foundation

### Owner

Existing realtime remediation plan.

### Entry

- Program 0 merged;
- clean current main;
- mechanical plan preflight complete;
- no conflicting runtime initiative.

### Execution cycle per phase

1. implement;
2. targeted tests;
3. deterministic replay;
4. fault test;
5. compare to baseline;
6. adversarially search for a new bottleneck/correctness regression;
7. correct;
8. only then merge/continue.

### Preserve

- live trading disabled;
- risk invariants;
- additive DB changes;
- Kalshi vendor boundary;
- money stores write-through unless separately justified.

### Exit

Use existing plan/spec gates; record achieved p50/p95/p99 and loss/completeness behavior.

At minimum demonstrate:

- no silent candidate duplication;
- durable candidate identity;
- bounded queue age under defined tested envelopes;
- critical-class isolation;
- no unacceptable app-queue loss under tested envelope;
- materially reduced receive→candidate→decision latency;
- REST critical traffic not trapped behind background FIFO work;
- reconnect behavior not “recovering” by silently discarding queued work;
- transient enrichment failures accounted/retried/abandoned explicitly rather than silently lost.

---

## Program 2R — Economic research lane, concurrent with Program 1

Allowed concurrency only if:

- read-only research/data analysis;
- docs/spec/plan changes only;
- no edits to realtime implementation surfaces;
- no live config changes.

### Exit

Produce all §6.1 deliverables and survive adversarial review.

---

## Program 2 — Economic Strategy/Analytics Remediation

### Entry

- Program 1 merged and runtime-measured;
- Program 2R plan approved;
- research accounts for pre-remediation capture bias.

### Implementation families may include

Only if evidence supports them:

- economic-value objective corrections;
- gate marginal-effect analytics;
- candidate/rejection reporting;
- fee authority;
- execution model;
- replay/state capture;
- cold-analytics/hot-path separation not already resolved by Program 1.

### Exit

The system can explain with evidence:

- why an entry passes;
- each gate’s economic contribution;
- which recommendations optimize EV versus accuracy only;
- which execution assumptions drive simulated P&L;
- what historical replay is trustworthy;
- which unknowns remain sample-limited.

---

## Program 3R — Canonical Decision + Live Execution investigation

### Entry

Program 1 stable and Program 2 has established the economic data/semantics a real decision needs.

### Why not earlier

A live executor should execute the system’s canonical economically meaningful intent, not freeze an interim strategy interface.

### Exit

Approved design/spec and execution plan covering §6.2.

---

## Program 3 — Canonical Decision + Real Execution implementation

Target:

`market/signal → canonical strategy → TradeIntent → paper | shadow | live`

### Requirements

- paper/shadow/live share decision intent;
- durable live order lifecycle;
- explicit unknown outcomes;
- idempotent retries;
- first-class partial fills;
- startup reconciliation before new exposure;
- real exchange state authoritative;
- risk-reducing actions remain available during appropriate halts.

### Exit maturity

Implementation should reach integration/runtime verification, not automatically “production qualified.”

---

## Program 4 — Canonical Shadow Qualification

### Entry

Canonical intent semantics exist.

### Do not

Run the old duplicate shadow path for weeks just to produce a sample count.

### Collect

- intent count;
- reasons/gates;
- decision-time market;
- hypothetical executable order;
- hypothetical fill;
- divergence from paper;
- data-health state;
- reconnect/restart behavior;
- economic outcome.

### Exit

Representative sample with no unexplained semantic divergence from the intended live decision path.

Choose sample thresholds from opportunity frequency/statistical power; do not guess them here.

---

## Program 5 — Frontend Operator Console

### Owner

Refreshed frontend modularization plan.

### Entry

- canonical execution model stable;
- realtime health semantics stable;
- plan rebased.

### Add to existing plan

Expose:

- signal freshness;
- realtime degradation;
- REST/API degradation;
- execution authority;
- TradeIntent/order lifecycle;
- partial fills;
- unknown outcomes;
- reconciliation;
- risk-interlock reason;
- paper/shadow/live distinction;
- backup/alert/auth/production status where relevant.

### Exit

Frontend migration complete without increasing backend load or hiding stale/degraded state.

---

## Program 6 — Personal Production Operations

### Entry

Core trading interfaces stable.

### Exit evidence

- supervised restart;
- safe startup reconciliation;
- secure auth;
- secrets out of Git;
- TLS;
- real alert delivery;
- off-host backup;
- restore procedure;
- reboot recovery;
- outage/degraded behavior;
- explicit production capital/risk policy;
- health interlocks that block new risk without blocking necessary risk reduction.

---

## Program 7 — Revised AQC implementation

### Priority

Useful but not trading-critical.

May move earlier only if fully isolated from trading and noncontending with critical branches.

### Mandatory change

Do not use current Task 6 tick integration.

Preferred topology:

`scheduled standalone CLI/process → static audit + branch reads → quality_coordination.db → read-only API/UI`

### Preserve

- report-only;
- no GitHub writes;
- no new credential;
- suppression;
- stable identity;
- own DB;
- persisted observation history.

### Exit

Coordinator failure, GitHub timeout, or full audit runtime cannot materially delay ingestion, decisions, reconciliation, or risk-reducing actions.

---

## Program 8 — Capital Qualification

### Stage A — Production-hosted paper

Validate real host/process without order authority.

### Stage B — Canonical shadow

Run actual live-intent semantics without submitting orders.

### Stage C — Tiny human-controlled real execution

Validate executor separately from autonomous strategy profitability:

- submit;
- acknowledgement;
- no fill;
- partial fill where observable;
- cancel;
- restart;
- reconcile;
- close/flatten;
- settlement.

### Stage D — Microscopic autonomous canary

Use deliberately tiny hard caps and compare expected versus actual:

- decision;
- order;
- fill;
- fees;
- latency;
- reconciliation.

### Stage E — Restricted personal production

Increase limits only after evidence gates.

### Stage F — Normal personal production

Still bounded; production never means unlimited capital authority.

---

# 8. Safe concurrency matrix

| A | B | Concurrent? | Rule |
|---|---|---:|---|
| Realtime implementation | Economic research | **Yes** | Research/docs/data only |
| Realtime implementation | Frontend core/polling rewrite | **No** | Shared runtime/state assumptions |
| Realtime implementation | AQC scheduler | **No** | Current AQC plan touches main scheduling |
| Realtime implementation | Strategy gate rewrites | **No** | Signal population changes while baseline moves |
| Economic remediation | Canonical execution research | **Limited** | Begin late; intent must consume final economics |
| Canonical execution implementation | Independent shadow rewrite | **No** | Shadow must derive from canonical intent |
| Canonical execution implementation | Execution-state frontend | **No** | UI waits on stable semantics |
| Frontend migration | Revised standalone AQC | **Potentially** | Only if path ownership does not overlap |
| Production-ops research | Frontend migration | **Potentially** | Research/docs only |

---

# 9. Mandatory between-step self-review gate

After every major program step and independently mergeable phase:

## A. State the result

Separate verified fact, inference, target design, unresolved question.

## B. Search for contradiction

Ask whether:

- source disagrees with the plan;
- a later merge invalidated an interface;
- a primitive is being mistaken for integration;
- historical measurement is being mistaken for current behavior;
- a plan is being mistaken for implementation.

## C. Attack the conclusion

Ask:

> “What obvious adjacent problem am I missing, and what evidence would overturn this conclusion?”

Do not change merely for the sake of changing.

## D. Scope check

Stop drift into enterprise infrastructure, unrelated refactors, new framework debates, duplicate plans, beautification, or automatic live enablement.

## E. Active-work/ownership check

Apply AQC research discipline now, even before its runtime service exists:

- inspect branches/worktrees/PRs;
- identify changed-path overlap;
- avoid duplicate remediation;
- do not create a competing plan for work already owned.

## F. Regression-guard disposition

For each real bug class decide:

- runtime metric;
- CI guard;
- shared validation;
- already covered;
- one-off.

## G. Correct before continuing

Do not carry a known invalid assumption into the next phase.

---

# 10. Plans/research not to rerun

Do not rerun as fresh investigations:

- `2026-08-24-kalshi-integration-dual-phase.md`
- `2026-08-24-kalshi-integration-phase-a.md`
- `2026-08-24-kalshi-integration-phase-c.md`
- `2026-08-24-quality-control-plane.md`
- `2026-08-25-realtime-data-plane-investigation.md`
- `2026-08-25-autonomous-quality-coordination-investigation.md`
- frontend framework-selection research

For realtime, run **remediation**, not investigation.

For AQC, retain completed research and revise implementation mechanics.

For frontend, refresh/execute the existing plan rather than reopening framework selection.

---

# 11. Exact immediate queue

## Immediate 1 — tiny doctrine branch

Branch:

`docs/production-doctrine-final-truth`

Change only:

- `CLAUDE.md` top-level “all P0 / rest operational” implication;
- corresponding ROADMAP classification.

Use docs-only CI fast path.

Merge.

## Immediate 2 — realtime execution branch

Branch:

`feat/realtime-data-plane-remediation`

from updated `main`.

Run the existing plan preflight. If source does not contradict the design, begin Task 1.

Do not open another realtime investigation.

## Immediate 3 — parallel research worktree

Branch/worktree:

`research/economic-strategy-effectiveness`

Research only.

Do not modify:

- `main.py`;
- realtime scheduler;
- WS consumer;
- Kalshi limiter implementation;
- strategy config;
- live data stores.

Produce research/spec/plan.

## After realtime merges

Rebase/finalize economic research as needed, incorporate post-remediation measurement implications, merge the research plan, then execute its remediation.

---

# 12. Production-readiness maturity table

| Capability | Current state | Next owner |
|---|---|---|
| QCP/static health tooling | MERGED + OPERATIONAL | Maintain/extend only when gaps found |
| Docs/research CI fast path | MERGED + OPERATIONAL | Existing CI |
| Generated status workflow | MERGED + OPERATIONAL | `/sync-status-docs` |
| Realtime measurement/replay | MERGED + OPERATIONAL | Program 1 |
| Realtime architecture fix | IMPLEMENTATION PLAN READY | Program 1 |
| AQC research/suppression/write policy | MERGED RESEARCH LEVERAGE | Apply manually now |
| AQC persisted coordinator | PLAN REQUIRES REFRESH | Program 7 |
| Frontend research/spec | MERGED RESEARCH LEVERAGE | Program 5 |
| Frontend Preact migration | PLAN REQUIRES REFRESH | Program 5 |
| Economic strategy effectiveness | INVESTIGATION REQUIRED | Program 2R |
| Realistic execution simulation | INVESTIGATION REQUIRED | Program 2R/2 |
| Full strategy replay | INVESTIGATION REQUIRED | Program 2R/2 |
| Canonical TradeIntent | INVESTIGATION REQUIRED | Program 3R |
| Autonomous strategy→real order | INVESTIGATION REQUIRED | Program 3R/3 |
| Durable live order state machine | INVESTIGATION REQUIRED | Program 3R/3 |
| Canonical shadow qualification | QUALIFICATION REQUIRED after Program 3 | Program 4 |
| Production operator console | PLAN REQUIRES REFRESH | Program 5 |
| Production host/ops | INVESTIGATION REQUIRED | Program 6 |
| Real capital policy | INVESTIGATION REQUIRED + HUMAN DECISION | Program 6 |
| Live execution canary | QUALIFICATION REQUIRED | Program 8 |
| Personal production | NOT YET QUALIFIED | Program 8 |

---

# 13. How prior automation/QCP work improves the upcoming program **before its own plans are executed**

The previous investigations are not wasted because their runtime plans are unexecuted.

They already lower future execution cost through:

### Reduced duplicate investigation

QCP endpoints/static audit answer many “is this broken?” questions before a new script is written.

### Safer parallel work

AQC already characterized active work, changed-path overlap, suppression precedence, finding persistence, and branch cadence. Claude can apply those policies manually immediately.

### Lower CI cost for research

Docs/research investigations use the merged fast path instead of repeatedly paying the full application E2E suite.

### Better worktree accounting

Project-manifest scans no longer multi-count `.claude/worktrees/`.

### Lower documentation/context cost

Status history is fragmented/generated rather than one huge file.

### Better falsification tooling

Realtime replay and QCP observability allow proposed fixes to be tested against known failure modes.

### Stronger autonomy boundaries

AQC established that “more automation” is not automatically progress; report-only remains the evidence-backed default.

> These gains make future work faster and safer. They do **not** mean realtime remediation, AQC persistence, frontend migration, economic remediation, canonical execution, or production deployment are implemented.

---

# 14. Final operating model

Stop producing broad overlapping audits.

Use two controlled lanes.

## Implementation lane

1. doctrine truth correction;
2. realtime remediation;
3. economic remediation;
4. canonical decision/live execution;
5. canonical shadow qualification;
6. frontend operator console;
7. production operations;
8. capital qualification.

## Research lane

Keep only one major research thread ahead of implementation:

1. Economic Strategy Effectiveness while realtime executes.
2. Canonical Decision/Live Execution after realtime stabilizes and economic semantics are understood.
3. Production Operations once core application semantics stabilize.

AQC implementation stays off critical path until its scheduling design is corrected.

---

# 15. One-sentence rule

> **Do not build the system’s ability to act faster than its ability to observe correctly, decide economically, reconcile reality, and prove what happened.**
