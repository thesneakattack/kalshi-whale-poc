# Kalshi Integration Refactor — Claude Kickoff

After installing/committing this planning bundle, start a fresh Claude session and use:

```text
Begin the dual-phase Document-Backed Kalshi Integration initiative from the current
repository state.

Treat current code, tests, git history, and CI as implementation truth. Do not rely on
previous chat/session memory.

Use `.claude/skills/kalshi-integration-refactor/SKILL.md` as the authoritative execution
orchestrator.

Read first:

- CLAUDE.md
- .claude/rules/quality-capabilities.md
- .claude/rules/kalshi-integration-authority.md
- .claude/skills/kalshi-integration-refactor/SKILL.md
- .claude/skills/kalshi-contract-review/SKILL.md
- docs/archive/lane-1-kalshi-ingestion/research/2026-08-24-kalshi-integration-audit.md
- docs/archive/lane-1-kalshi-ingestion/specs/2026-08-24-kalshi-integration-boundary-design.md
- docs/archive/lane-1-kalshi-ingestion/plans/2026-08-24-kalshi-integration-dual-phase.md
- docs/archive/lane-1-kalshi-ingestion/plans/2026-08-24-kalshi-integration-phase-a.md
- docs/archive/lane-1-kalshi-ingestion/plans/2026-08-24-kalshi-integration-phase-c.md
- docs/kalshi/CHEATSHEET.md
- current relevant module CHEATSHEET.md files
- current CI definitions
- recent relevant git history

Before editing, verify all canonical files exist, determine branch/HEAD/working-tree state,
and reconstruct whether any part of the initiative has already been implemented or
superseded.

Then execute exactly ONE genuinely incomplete numbered task, beginning with A0 unless
current HEAD already proves A0 complete.

For every Kalshi semantic assumption, use the exact mirrored official documentation rather
than memory. Preserve raw payload archival, real-money safety gates, test DB isolation,
existing rate-limit/telemetry behavior, and hot-path performance.

Follow Kalshi documented suggested practices and workflows where relevant or useful, such as using the websockets python library to handle ping/ponging

Use relevant repo capabilities and supporting Superpowers disciplines automatically.
Deterministic recurring checks must be permanently owned by CI, not merely runnable by
Claude.

Verify the task, commit one logical change, report the exact docs consulted, tests/CI/
runtime/performance evidence, commit SHA, and next incomplete task, then STOP before the
next numbered task.

Do not begin Phase C until the formal Phase A stability gate has completely passed.
```

For subsequent tasks:

```text
Continue the Document-Backed Kalshi Integration initiative from current HEAD using
kalshi-integration-refactor. Reconstruct progress from the repository, execute exactly the
next genuinely incomplete numbered task, verify and commit it, then stop.
```
