# Claude Kickoff — Realtime Kalshi Data-Plane Investigation

Start a fresh Claude session after committing this bundle and paste:

```text
Begin the Realtime Kalshi Data-Plane Investigation from the current repository state.

This is an investigation and architecture-selection program, not a directive to implement
a preselected queue or rate-limit solution.

Treat current HEAD, code, tests, CI, runtime diagnostics, and exact mirrored official
documentation as truth. Do not rely on prior chat memory.

Use `.claude/skills/realtime-data-plane-investigation/SKILL.md` as the top-level
orchestrator.

Read first:

- CLAUDE.md
- .claude/rules/quality-capabilities.md
- .claude/rules/kalshi-integration-authority.md
- .claude/rules/realtime-data-plane-evidence.md
- .claude/skills/realtime-data-plane-investigation/SKILL.md
- .claude/skills/root-cause-debugging/SKILL.md
- .claude/skills/observability-performance/SKILL.md
- .claude/skills/kalshi-contract-review/SKILL.md
- docs/archive/lane-1-kalshi-ingestion/research/2026-08-25-realtime-data-plane-known-findings.md
- docs/archive/lane-1-kalshi-ingestion/specs/2026-08-25-realtime-data-plane-investigation-design.md
- docs/archive/lane-1-kalshi-ingestion/plans/2026-08-25-realtime-data-plane-investigation.md
- docs/kalshi/CHEATSHEET.md
- relevant service CHEATSHEET.md files
- current relevant CI definitions
- recent git history around WebSocket, rate-limit, whale-stream and market-watch changes

Recognize all installed repository skills and Superpowers/plugins. Use the relevant ones
automatically. In particular, use systematic-debugging, TDD,
verification-before-completion, brainstorming, code review, and parallel research agents
when they materially improve the investigation.

Do not mechanically invoke every plugin. Do not let a generic Superpowers execution model
replace the repo-specific investigation workflow.

The findings document contains hypotheses, not conclusions.

Your job is to:
1. measure the actual realtime workload and bottlenecks;
2. reproduce failure mechanisms;
3. quantify REST-vs-WS capture completeness;
4. trace whale candidates by trade_id to terminal outcomes;
5. separate local rate-limiter wait from upstream network latency;
6. research current best-practice solution families using authoritative Kalshi, Python,
   websockets, and reputable realtime-system sources;
7. prototype multiple credible architectures outside the production path;
8. benchmark and fault-inject them against the same measured workload;
9. adversarially review the leading design;
10. select the simplest architecture that actually meets correctness, capture, latency and
    operational requirements;
11. only then write a separate remediation design and production implementation plan.

Do not increase queue sizes, workers, rate limits, connection counts or change
subscription scope merely because those changes sound plausible.

Real trading must remain disabled. Preserve test DB isolation and the established
document-backed Kalshi boundary.

Before editing, reconstruct branch/HEAD/working tree and determine the first genuinely
incomplete numbered investigation task.

Execute exactly ONE task, verify it, commit it, report evidence and hypothesis status, and
STOP before the next task.
```

Subsequent session:

```text
Continue the Realtime Kalshi Data-Plane Investigation from current HEAD using
`.claude/skills/realtime-data-plane-investigation/SKILL.md`.

Reconstruct progress from the repository, execute exactly the next genuinely incomplete
numbered task, use evidence rather than assumptions, verify and commit it, then stop.
```
