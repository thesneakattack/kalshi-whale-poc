# Realtime Kalshi Data-Plane Investigation Execution Plan

> **Agentic execution:** Use `.claude/skills/realtime-data-plane-investigation/SKILL.md`
> as the authoritative orchestrator. Execute exactly one numbered task, verify, commit,
> report, and stop. Superpowers skills are supporting disciplines, not a replacement
> progress/orchestration system.

**Goal:** Causally explain persistent Kalshi latency, rate-limit pressure, WebSocket
backlog/loss, and missed whale opportunities, then select a best-engineered solution
through comparative research and experiments.

**Spec:** `docs/superpowers/specs/2026-08-25-realtime-data-plane-investigation-design.md`

## Global constraints

- Current HEAD is implementation truth.
- Real trading stays disabled.
- No tests against live `data/*.db`.
- Exact mirrored Kalshi docs are mandatory for Kalshi assumptions.
- Current Python/`websockets` docs matching installed versions are mandatory for library
  behavior assumptions.
- Exchange-wide trade coverage remains a product requirement unless the user changes it.
- No queue/rate/worker/connection tuning before evidence.
- No structural production redesign before solution selection.
- Experiments must not corrupt historical effectiveness/calibration evidence.
- Network experiments are manual/safe, not push/PR CI.
- One task = one independently reviewable commit.

---

## I0 — Re-ground and establish the causal investigation map

**Create**
- `docs/superpowers/research/2026-08-25-realtime-data-plane-baseline.md`

**Read**
- `CLAUDE.md`
- canonical investigation files
- `docs/kalshi/CHEATSHEET.md`
- exact local WebSocket trade/quick-start/Get Trades/rate-limit/Get Markets docs
- `services/kalshi/websocket.py`
- `services/http_client.py`
- `services/kalshi/public.py`
- `services/whale_stream/**`
- `services/whalewatchers/kalshi_trade_tape.py`
- `services/market_watch/**`
- `services/account_positions.py`
- `services/observability/**`
- relevant tests/CI/recent commits

- [ ] Record branch, HEAD, worktree, runtime mode, and relevant config.
- [ ] Draw the exact current WS message path including all queues, callbacks, thread hops,
  DB calls, REST calls, and mutable-state stages.
- [ ] Draw the exact current REST demand graph, classifying each call as:
  `whale-critical`, `position/risk-critical`, `interactive`, `background`.
- [ ] Inventory existing metrics and missing metrics.
- [ ] Record the known hypotheses from the findings file without upgrading any to
  "confirmed."
- [ ] If a safe paper-mode instance is running, collect a 10-minute no-change baseline.
- [ ] Record what evidence would confirm/falsify each hypothesis.
- [ ] Commit:
  `docs: baseline realtime Kalshi investigation`

**Acceptance**
A reviewer can understand the end-to-end data plane without reading historical chat.

---

## I1 — Measure the WebSocket queues and message-age problem

**Modify**
- `services/kalshi/websocket.py`
- `services/observability/observability.py`
- existing observability tests
- `services/observability/CHEATSHEET.md`

**Required low-overhead metrics**
- received by type;
- processed by type;
- current app queue depth;
- queue high-water;
- oldest-message age;
- queue wait avg/max and bounded percentile mechanism if the existing observability
  architecture supports it cheaply;
- local drops total/recent and by type;
- Kalshi error-25 count/recent;
- reconnect count/reason;
- handler time by message class.

- [ ] Write failing deterministic queue tests.
- [ ] Prove local `QueueFull` and server error 25 are separate metrics.
- [ ] Use monotonic enqueue timestamps.
- [ ] Measure instrumentation overhead with synthetic trade messages.
- [ ] Integrate into existing observability; do not create a second metrics subsystem.
- [ ] Run targeted tests, static quality audit and hot-path benchmark.
- [ ] Commit:
  `obs: measure Kalshi websocket queue health`

**Acceptance**
The repo can tell whether a message is received promptly but processed stale.

---

## I2 — Measure the whale-trade pipeline stage by stage

**Modify**
- `services/whale_stream/whale_stream_handlers.py`
- `services/whalewatchers/kalshi_trade_tape.py`
- existing performance/observability code
- targeted tests

**Measure**
- receive → canonical parse;
- receive → `_prescan_count`;
- below-threshold exit;
- candidate enqueue/start;
- thread-hop wait;
- sync analysis time;
- DB time where measurable;
- context enrichment wait;
- strategy evaluation;
- receive → final decision.

- [ ] Build counters for total trades, below-threshold, candidates and off-list candidates.
- [ ] Prove diagnostics do not write one DB row per ordinary exchange-wide trade.
- [ ] Measure `config_store.get()` and any repeated per-message config/fingerprint work.
- [ ] Measure how often `asyncio.to_thread` is entered versus actual candidate frequency.
- [ ] Capture a representative 10-minute paper-mode window when available.
- [ ] Classify H3/H4 as confirmed/falsified/inconclusive.
- [ ] Commit:
  `obs: trace whale processing latency`

**Acceptance**
The most expensive stages are measured rather than inferred from code shape.

---

## I3 — Reproduce candidate-loss behavior under transient enrichment failure

**Create**
- `tests/test_whale_candidate_lifecycle.py`

**Modify only as needed for observability/testability**
- `services/whalewatchers/kalshi_trade_tape.py`

**Experiment**
A whale-sized unknown-market trade:
1. first context lookup raises a retryable error/429-equivalent;
2. same `trade_id` is presented again or retry is attempted;
3. determine whether it can ever reach evaluation.

- [ ] Write the failing/reproduction test around the current behavior before fixing
  anything.
- [ ] Explicitly record when `_mark_seen()` occurs relative to context resolution and final
  evaluation.
- [ ] Distinguish:
  `wire_seen`, `candidate_pending`, `terminal_evaluated`.
- [ ] If current code permanently suppresses the candidate, record H4 as a proven defect.
- [ ] Do not redesign the dedupe model in this task.
- [ ] Commit:
  `test: reproduce whale enrichment loss`

**Acceptance**
A missed candidate due to transient REST failure is either proven or falsified.

---

## I4 — Build REST-vs-WS trade capture reconciliation

**Create**
- `services/diagnostics/trade_capture_reconciliation.py`
- `tests/test_trade_capture_reconciliation.py`

**Modify**
- existing diagnostics routes/CHEATSHEET as appropriate

**Use exact Kalshi docs**
- public trade WS;
- Get Trades;
- pagination;
- rate limits.

- [ ] Implement bounded, read-only time-window reconciliation by `trade_id`.
- [ ] Handle REST pagination and explicit truncation.
- [ ] Compare:
  REST count, WS count, intersection, missing IDs, whale-sized missing IDs.
- [ ] Attach local drop/error25/reconnect evidence for the window when available.
- [ ] Do not run reconciliation every tick.
- [ ] Add a manual diagnostics endpoint or CLI.
- [ ] Run fixture/contract tests.
- [ ] Commit:
  `diag: reconcile Kalshi trades across REST and websocket`

**Acceptance**
The app can quantify actual capture completeness rather than infer it from local counters.

---

## I5 — Decompose REST latency into local and upstream components

**Modify**
- `services/http_client.py`
- `services/observability/observability.py`
- tests
- observability CHEATSHEET

**Required timing**
- limiter wait;
- network call;
- retry/backoff;
- total elapsed;
- attempts;
- 429s.

**Add low-cardinality caller classes**
- `critical_whale`
- `critical_position`
- `interactive`
- `background_discovery`
- `background_catalog`
- `background_live_status`
- `background_resolution`
- `other`

- [ ] Test a fake 100ms limiter wait + 10ms network call and prove they are distinct.
- [ ] Test 429/backoff accounting.
- [ ] Preserve current endpoint normalization.
- [ ] Annotate highest-value call sites first; don't mass-edit irrelevant callers.
- [ ] Record limiter waiter depth/high-water if it can be done safely.
- [ ] Run targeted/full observability tests.
- [ ] Commit:
  `obs: decompose Kalshi REST latency`

**Acceptance**
A multi-second call can be attributed to local queueing, upstream network, or retry sleep.

---

## I6 — Build deterministic realtime replay/load harness

**Create**
- `tools/realtime_pipeline_replay.py`
- `tests/test_realtime_pipeline_replay.py`

**Purpose**
Reproduce measured arrival/service patterns without changing production architecture.

**Workloads**
At least:
- measured normal trade rate;
- measured p95 rate;
- measured burst rate;
- mixed trade+ticker;
- mixed trade+critical fill/position/lifecycle;
- transient slow handler;
- transient REST enrichment stall;
- reconnect/drop marker.

- [ ] Use existing doc-backed fixture shapes.
- [ ] Model current queue/consumer topology first.
- [ ] Replay measured handler distributions from I1/I2.
- [ ] Report:
  throughput, queue growth, oldest age, drops, critical-message wait.
- [ ] Prove the current topology's sustainable rate.
- [ ] Keep the harness implementation-independent enough to test competing topologies
  later.
- [ ] Commit:
  `test: add realtime Kalshi pipeline replay`

**Acceptance**
Queue/worker ideas can be tested without experimenting blindly on production code.

---

## I7 — Run a safe live baseline and correlate failure modes

**Create**
- `docs/superpowers/research/2026-08-25-realtime-live-baseline.md`

**Procedure**
Use paper/shadow mode and experiment quarantine.

Observe at least 30 minutes if practical, including a busy period if available.

- [ ] Collect WS receive/service rates, queue age, drop deltas, error25, reconnects.
- [ ] Collect whale pipeline stage latency/candidate counts.
- [ ] Collect REST limiter/network/backoff timing by caller class.
- [ ] Run bounded REST-vs-WS reconciliation windows.
- [ ] Correlate candidate misses with queue/drop/reconnect/enrichment events.
- [ ] Do not call correlation causation.
- [ ] Update hypothesis table.
- [ ] Commit research results:
  `research: capture realtime Kalshi baseline`

**Acceptance**
The deterministic harness and real workload have a shared empirical baseline.

---

## I8 — Verify endpoint costs, batching and background REST demand

**Create**
- `tools/kalshi_rate_limit_probe.py`
- `tests/test_kalshi_rate_limit_probe.py`
- `docs/superpowers/research/2026-08-25-rest-demand-study.md`

**Use exact current mirrored docs**
Discover account limits/endpoint-cost pages from `llms.txt` rather than guessing names.

- [ ] Query safe read-only account limit/cost endpoints where credentials permit.
- [ ] Record actual cost for key endpoints.
- [ ] Revalidate `get_markets_by_tickers` chunk-size assumptions with bounded 50/100/200
  tests when current docs make those sizes safe.
- [ ] Measure completeness, total latency and 429 behavior.
- [ ] Measure caller-class share of REST demand in a normal runtime window.
- [ ] Measure milestone/live-data duplicate demand/caching overlap.
- [ ] Quantify how much catalog/discovery/live-status/resolution work competes with
  whale/position-critical calls.
- [ ] Commit:
  `research: measure Kalshi REST demand and batching`

**Acceptance**
The REST optimization problem is expressed in actual endpoint cost and workload numbers.

---

## I9 — Research best-in-class solution families

**Create**
- `docs/superpowers/research/2026-08-25-realtime-solution-research.md`

**This task is research only.**

Use:
- current Kalshi official docs;
- current Python asyncio docs;
- current `websockets` docs/source matching installed version;
- reputable realtime architecture references;
- repo evidence.

Use parallel research agents when useful, with separate scopes.

**Required research tracks**
1. high-frequency WebSocket ingestion/backpressure;
2. ordering-safe concurrent processing / keyed partitioning;
3. coalescing state updates vs event streams;
4. critical-vs-background request scheduling;
5. reconciliation / at-least-once candidate evaluation;
6. SQLite/event-loop/thread interaction under the observed workload;
7. whether a broker/process boundary would add real value at current scale.

- [ ] For every proposed pattern, record:
  - why it exists;
  - when it is appropriate;
  - failure semantics;
  - ordering implications;
  - Python-specific cost;
  - operational burden;
  - repo fit.
- [ ] Explicitly include reasons **not** to use sophisticated infrastructure when a simpler
  in-process design meets the measured requirement.
- [ ] Generate at least three plausible solution families for each confirmed major
  bottleneck.
- [ ] Do not select a winner yet.
- [ ] Commit:
  `research: survey realtime data-plane architectures`

**Acceptance**
The candidate set reflects current engineering practice, not just the assistant's initial
ideas.

---

## I10 — Prototype and benchmark competing WebSocket/data-plane designs

**Extend**
- `tools/realtime_pipeline_replay.py`
- replay tests

**Create**
- `docs/superpowers/research/2026-08-25-ws-solution-comparison.md`

Only prototype candidates justified by I7/I9. Candidate examples may include:

- optimized single queue/consumer with earlier prescan;
- staged trade ingress → candidate queue;
- traffic-class queues;
- keyed worker partitioning;
- split physical WS connections;
- process-isolated heavy analytics;
- combinations.

- [ ] Define correctness invariants before benchmarking:
  - no duplicate whale decisions;
  - per-ticker ordering where required;
  - account/lifecycle loss policy;
  - bounded memory.
- [ ] Run all candidates against the same measured workload distributions.
- [ ] Measure:
  - sustained throughput;
  - p50/p95/p99 whale decision latency;
  - critical-event latency;
  - drops;
  - queue age;
  - CPU;
  - memory;
  - ordering violations;
  - recovery after stalls.
- [ ] Fault-inject bursts, slow handlers and reconnects.
- [ ] Reject candidates that merely move backlog elsewhere.
- [ ] Record the comparison matrix.
- [ ] Commit:
  `research: benchmark websocket architecture candidates`

**Acceptance**
The final WS design can be selected from comparative evidence.

---

## I11 — Prototype and benchmark competing REST scheduling/recovery designs

**Extend**
- fake limiter/replay tooling

**Create**
- `docs/superpowers/research/2026-08-25-rest-solution-comparison.md`

Candidate families may include:

- eliminate/coalesce unnecessary calls only;
- priority scheduler;
- weighted fair queue;
- reserved critical capacity;
- endpoint-cost-aware scheduling;
- background budget;
- improved batching;
- shared milestone cache;
- retryable candidate enrichment;
- REST trade reconciliation.

- [ ] Build synthetic caller loads from I5/I8 measured distributions.
- [ ] Compare critical request wait under normal/burst background load.
- [ ] Verify total external budget still respects Kalshi limits.
- [ ] Fault-inject 429s/timeouts.
- [ ] Measure starvation/fairness.
- [ ] Test candidate-retry semantics against duplicate-decision safety.
- [ ] Compare reconciliation cost to actual observed WS loss rate.
- [ ] Commit:
  `research: benchmark REST scheduling and recovery candidates`

**Acceptance**
Priority/reconciliation changes are justified by measured benefit and explicit failure
semantics.

---

## I12 — Conduct architecture adversarial review

**Create**
- `docs/superpowers/research/2026-08-25-realtime-architecture-review.md`

Use:
- `brainstorming`;
- `requesting-code-review`;
- `receiving-code-review`;
- independent parallel reviewers where useful.

For the leading candidate(s), require devil's-advocate review on:

- hidden ordering races;
- dedupe semantics;
- queue starvation;
- memory blow-up;
- process/thread safety;
- SQLite write contention;
- reconnect state;
- account-event correctness;
- rate-limit starvation;
- observability blind spots;
- operational complexity;
- failure recovery;
- deployment/upgrade complexity.

- [ ] Attempt to falsify the apparent winner.
- [ ] Re-run targeted benchmarks for any credible criticism.
- [ ] Update solution matrices rather than arguing from preference.
- [ ] Commit:
  `research: adversarially review realtime architecture`

**Acceptance**
The selected design survives critique with evidence rather than because it was first.

---

## I13 — Select the architecture and write the root-cause report

**Create**
- `docs/superpowers/research/2026-08-25-realtime-root-cause-report.md`
- `docs/superpowers/specs/2026-08-25-realtime-data-plane-remediation-design.md`

**Root-cause report must include**
- confirmed causal chains;
- falsified hypotheses;
- capture completeness;
- candidate-loss mechanism if any;
- WS capacity limits;
- REST contention breakdown;
- solution comparison matrices;
- rejected alternatives and reasons;
- expected performance/correctness target after remediation.

**Design spec must include**
- exact chosen topology;
- message classes;
- ordering semantics;
- backpressure/drop/coalescing policy;
- retry/dedupe state machine;
- REST scheduler semantics;
- reconciliation policy;
- persistence/thread/process ownership;
- observability;
- migration strategy;
- rollback;
- acceptance metrics.

- [ ] Use current measurements, not historical comments.
- [ ] Ensure the design chooses the simplest solution that satisfies the measured target.
- [ ] Run an internal contradiction/placeholder review.
- [ ] Do not implement production changes yet.
- [ ] Commit:
  `docs: design evidence-backed realtime remediation`

**Acceptance**
There is one explicit, defensible architecture ready for implementation planning.

---

## I14 — Produce the production implementation plan

Use `superpowers:writing-plans` only now.

**Create**
- `docs/superpowers/plans/2026-08-25-realtime-data-plane-remediation.md`

The plan must:

- implement the I13 design incrementally;
- preserve current Kalshi integration contracts;
- include TDD;
- include deterministic replay gates;
- include safe paper-mode soak gates;
- include CI/runtime permanent diagnostics;
- include migration/rollback checkpoints;
- include before/after acceptance targets;
- make no unsupported "nice to have" refactors.

- [ ] Self-review the plan against every I13 requirement.
- [ ] Confirm no placeholder steps.
- [ ] Commit:
  `docs: plan realtime Kalshi remediation`
- [ ] STOP. Do not start implementation until the user reviews/approves the resulting
  remediation design and plan.

**Acceptance**
Claude has transformed an open investigation into a fully researched, evidence-backed
implementation program without guessing the final architecture in advance.
