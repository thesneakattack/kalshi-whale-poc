# Claude start directive — Autonomous Quality Coordination Investigation

Use the repository skill `.claude/skills/autonomous-quality-coordination-investigation/SKILL.md` as the authoritative orchestrator and execute **I0 only** from `docs/superpowers/plans/2026-08-25-autonomous-quality-coordination-investigation.md`.

Before doing anything substantive, re-ground against the current repository: read `CLAUDE.md`, all `.claude/rules/*.md`, this initiative's research/spec/plan, relevant existing Quality Control Plane code/tests, recent git history, current local worktrees, remote branches, open PRs, and Woodpecker status. Treat current code/tests/git as implementation truth and treat the known-findings document as hypotheses/evidence, not conclusions.

Use Superpowers as the development process. Use specialized tooling only when its current capability state and task routing policy say it materially helps. In particular:

- use `dispatching-parallel-agents` only for genuinely independent, read-only research tracks and synthesize centrally;
- do not treat built-in Explore/Plan subagents as policy-authoritative unless you explicitly inject the relevant repo rules, because they do not load `CLAUDE.md`/parent git state;
- sanity-check GitNexus before trusting it and confirm consequential graph claims against source;
- use Context7 only for version-specific external-library evidence and fail open to official docs;
- use Chrome DevTools only for browser-facing evidence;
- use dimensional-analysis only if a task actually reaches trading/math semantics;
- skip BLOCKED_EXTERNAL tools silently per `.claude/rules/tooling-plugins.md`;
- do not change user-global Claude settings or re-litigate the known GitNexus hook issue.

Do not implement the autonomous quality system. Do not create GitHub issues or remediation PRs. Do not add write credentials. Do not modify files currently owned by another active branch without first reconciling that branch. Complete I0, verify it, commit that one investigation unit on the proper short-lived initiative branch/worktree, report the exact evidence and next task, and stop.
