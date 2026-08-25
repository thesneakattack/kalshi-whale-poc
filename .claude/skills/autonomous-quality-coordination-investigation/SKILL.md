---
name: autonomous-quality-coordination-investigation
description: Use to investigate and design contention-aware autonomous quality reporting/escalation/remediation for this repository. Reconstruct concurrent work, audit QualityFinding identity and automation suitability, compare coordination and credential topologies, prototype without GitHub writes, adversarially review the result, then write a separate production implementation plan.
---

# Autonomous Quality Coordination Investigation

Canonical files:

- `docs/superpowers/research/2026-08-25-autonomous-quality-coordination-known-findings.md`
- `docs/superpowers/specs/2026-08-25-autonomous-quality-coordination-investigation-design.md`
- `docs/superpowers/plans/2026-08-25-autonomous-quality-coordination-investigation.md`
- `.claude/rules/autonomous-quality-coordination-evidence.md`

If any are missing, stop before editing.

## Role

This skill orchestrates an investigation, not an autonomous bot implementation.

Known findings are starting evidence and hypotheses. Current HEAD, open work, tests, CI, and experiments can supersede them.

The investigation must answer three questions before production code is planned:

1. How can recurring quality findings be surfaced without stepping on active work?
2. Which findings, if any, are safe for deterministic autonomous remediation?
3. Which event/credential/reporting architecture provides the least privilege and least operational complexity for the needed authority?

## Re-ground before every numbered task

Read/inspect as relevant:

- `CLAUDE.md`;
- `.claude/rules/*.md`;
- this initiative's canonical files;
- current branch, HEAD, dirty state, and local worktrees;
- `git log --oneline -20` and task-relevant history;
- open PRs and remote initiative branches;
- `tools/quality_audit/**`, `services/quality/**`, tests, baseline, current CI workflows;
- current Woodpecker commit statuses;
- any active branch diff touching files the task might change.

Determine whether another branch/session already owns the surface before editing it.

## Capability selection

Superpowers is the primary process. Use the repository's existing specialized capabilities as evidence providers, not as a checklist.

Normally relevant:

- `root-cause-debugging`
- `ci-cd-guardrails`
- `integration-audit`
- `session-handoff`
- `final-verification`
- `verification-before-completion`
- `requesting-code-review`
- `brainstorming` during architecture comparison
- `writing-plans` only after the investigation chooses an architecture

Use `dispatching-parallel-agents` for independent read-only research tracks where parallelism materially improves coverage. The main agent owns synthesis and must reconcile disagreements against current evidence.

### Subagent caution

Built-in Explore/Plan agents are fast but do not load `CLAUDE.md` or the parent git status. For policy-sensitive conclusions, prefer main-agent analysis or custom/general-purpose agents that load repo instructions. If Explore/Plan is used, inject the necessary constraints explicitly and treat the result as research input only.

### GitNexus

Use GitNexus deliberately for structural questions such as `QualityFinding` producers/consumers and QCP blast radius. Before trusting it:

1. run `npx gitnexus@latest status`;
2. smell-test two unrelated symbols or compare a consequential answer with `grep`/source;
3. if the known corrupted-index pattern appears, rebuild using the commands in `.claude/rules/tooling-plugins.md`;
4. never accept `epistemic: exact` as proof by itself.

### Context7 and official docs

Use Context7 only where installed-version library semantics matter. If unavailable/rate-limited, use official upstream docs immediately. GitHub/Woodpecker/Claude Code architecture decisions should cite their current official documentation.

### Chrome DevTools and dimensional analysis

Chrome DevTools is NOT_NEEDED for backend-only coordination tasks; use it only if a reporting/UI question actually becomes browser-facing.

Dimensional analysis is NOT_NEEDED for coordinator logic unless a proposed fixer or test touches trading math. If it does, it becomes mandatory before completion for that unit.

Blocked external plugins remain skipped per `.claude/rules/tooling-plugins.md`.

## Per-task workflow

1. Re-ground current HEAD and active work.
2. State the exact question and what would confirm/falsify each candidate answer.
3. Separate independent research tracks; dispatch in parallel only when safe/useful.
4. Collect current repo evidence before outside recommendations.
5. Use authoritative external docs for platform semantics.
6. Prefer a deterministic experiment/scenario over opinion.
7. Record negative results and rejected assumptions.
8. Do not enable GitHub writes, privileged CI secrets, remediation PR creation, or auto-merge during the investigation.
9. For any mutating prototype, use an explicitly isolated worktree from the intended baseline; never experiment in the primary checkout.
10. Run targeted verification appropriate to the task; use Woodpecker for exhaustive validation after a push.
11. Before commit: `git diff`, `git diff --check`, `git status --short`; no secrets, live DBs, captures, or temp outputs staged.
12. Commit one coherent investigation task.
13. Check actual Woodpecker status after push when the task is pushed.
14. Report: task/question, HEAD before, active-work map, tools/docs used, experiments, results, confidence, rejected hypotheses, files changed, commit SHA, CI state, next task.
15. Stop.

## Parallel research pattern

Good parallel tracks include:

- QCP scanner/finding-identity audit;
- repository branch/PR cadence analysis;
- GitHub/Woodpecker/Claude Code platform-security research;
- adversarial review of a proposed architecture.

Bad parallelization includes two agents editing the same coordinator prototype, two agents independently changing the baseline, or an agent modifying a file currently changed by another active branch.

## Solution-selection workflow

At I10:

1. require at least three credible control-plane architectures unless evidence eliminates a family earlier;
2. compare them on contention avoidance, false-positive rate, false-negative risk, credential exposure, branch safety, operational complexity, failure recovery, observability, and maintenance burden;
3. include a report-only/no-autonomous-write control;
4. dispatch an independent adversarial review after the main agent has a provisional winner;
5. reconcile every critique against measured repo evidence and current official docs;
6. prefer the simplest design meeting requirements;
7. write the architecture decision with rejected alternatives;
8. only then use `writing-plans` to create the production implementation plan.

## Stop conditions

Stop and ask for human intervention only when an external account/credential decision is genuinely required to continue the investigation. Do not halt because optional plugins are blocked.

Do not modify user-global Claude settings. The known GitNexus hook cleanup remains a user action documented elsewhere and is outside this initiative.
