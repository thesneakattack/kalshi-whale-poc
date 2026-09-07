# Autonomous Quality Coordination — Known Findings and Research Starting Point

**Repository baseline reviewed:** `main` at `a0c156973acf41fba23d8bd46e1d7bb126584a3f` on 2026-08-25.

**Purpose:** Preserve the evidence that motivated this investigation without prematurely selecting the implementation. Everything below must be re-grounded when I0 runs.

## 1. The repository already has a Quality Control Plane

Do not build a second generalized audit framework.

Current `tools/quality_audit/__main__.py` aggregates multiple scanners into the shared `QualityFinding`/`QualityReport` contract, compares findings against a reviewed baseline, emits JSON, and gates new high-confidence errors. Current scanners include router wiring, background wiring, persistence isolation, resource lifecycle, config usage, API usage, frontend contracts, Kalshi contract documentation, and Kalshi integration-boundary enforcement.

The correct design surface is therefore **reporting/coordination/escalation around the existing QCP**, with changes to QCP contracts only when evidence shows they are necessary.

## 2. Contention is already real, not hypothetical

At research time the repository had concurrent initiatives:

- open PR #11, `docs/frontend-modularization-design`, explicitly created in an isolated worktree because the primary checkout was in use by a parallel session;
- remote `chore/realtime-dp-investigation`, four commits ahead of `main`, modifying realtime/observability code and `tools/quality_audit/baseline.json`;
- `main` already contains the separate realtime-data-plane investigation plan/orchestrator.

This means an automation system that reacts to every `main` or branch finding without active-work awareness can produce duplicate issues, conflicting fixes, or noisy judgments about transitional code.

## 3. Installation must not touch contested shared files

This package intentionally does not edit:

- `.claude/rules/quality-capabilities.md` (currently changed by PR #11 at research time);
- `tools/quality_audit/baseline.json` (currently changed by the realtime investigation branch at research time);
- `.woodpecker/**`, `.github/workflows/**`, QCP implementation, or app code.

I0 must re-check those facts. If the active work has merged, later investigation tasks may edit the relevant router/plan surfaces in a focused commit.

## 4. Existing finding IDs are not uniformly suitable as durable issue keys

Current QCP scanners use mixed identity schemes.

Examples observed:

- resource-lifecycle findings use semantic structure such as module/function/local-variable identity;
- Kalshi boundary findings currently include `file:line` in multiple IDs.

A harmless line insertion can therefore make an unchanged violation look like a new finding if `finding_id` is reused as the long-lived external issue key.

Investigation implication: I1 must mutation-test every scanner's identity behavior before the automation layer chooses whether to reuse `finding_id`, derive a separate `automation_key`, or adopt another stable fingerprint. Avoid changing baseline IDs merely to satisfy automation unless the migration cost is justified.

## 5. Branch-only findings should remain branch-local

Provisional rule to test:

- PR/feature branches may receive CI failures, annotations, or reports;
- they do not create global repository issues or separate remediation branches for findings not present on integrated `main`;
- only persistent default-branch findings become candidates for escalation.

This follows the repository's standing branch policy: `main` is integrated truth, while implementation belongs on short-lived initiative branches.

## 6. Active work may suppress escalation but never prove resolution

Candidate coordination hierarchy to test:

1. exact finding claim in PR metadata or a structured marker;
2. changed-path/scope overlap with an open PR;
3. active remote branch overlap/staleness;
4. persistence/grace policy;
5. optional local Claude session/worktree advisory state.

An active-work signal should move a finding to a state like `suppressed_pending_work`, not `resolved`. After the relevant work merges, a new `main` audit decides whether the finding actually disappeared.

## 7. Remote CI cannot see unpushed local work

No design can make Woodpecker/GitHub omniscient about an unpushed local checkout.

Claude Code now exposes hooks such as WorktreeCreate, WorktreeRemove, SubagentStart/Stop, and SessionEnd, so a local advisory layer may be technically possible. The investigation must determine whether it adds enough value to justify coordination state. A distributed lease registry is not assumed necessary.

## 8. Fixed 24/48-hour delays are not evidence-backed

Earlier proposals used example delays. The current repo moves quickly and already shows overlapping sessions/branches within hours.

I2 must measure actual branch/PR/commit cadence and simulate candidate policies before choosing observation-count or time thresholds. The simplest policy that prevents duplicate work without hiding durable defects should win.

## 9. Credential/event topology is still an open decision

Credible architecture families include:

- **Woodpecker-only write lane:** detection and privileged GitHub writes from Woodpecker using a least-privilege GitHub App;
- **Hybrid:** Woodpecker remains detector/verifier while a GitHub-native workflow owns issue/SARIF/PR writes with an ephemeral GitHub Actions token;
- **GitHub-native control lane:** GitHub Actions owns reporting/escalation/remediation control; Woodpecker remains exhaustive build/test verifier;
- **Report-only control:** QCP/CI surfaces findings but no autonomous external writes.

Do not select a winner until I4/I5/I6 compare event semantics, credential exposure, operational complexity, permissions, and failure recovery.

Woodpecker's current documentation is particularly important: a `branch: main` condition also matches a PR whose target is `main`; privileged steps therefore require an explicit trusted event filter such as `event: push` plus `branch: main` if Woodpecker is selected.

## 10. Reporting surface should match the finding type

Candidates to compare:

- existing Woodpecker status/log output;
- SARIF/code-scanning alerts for source-located static findings;
- PR annotations/checks for branch-local regressions;
- GitHub issues only for durable actionable repository work;
- no notification for intermediate observation/suppression state.

Goal: avoid turning the issue tracker into a telemetry stream.

## 11. Deterministic remediation is a much smaller surface than detection

Potential candidates worth proving rather than assuming:

- committed project-manifest regeneration;
- generated frontend bundle synchronization;
- formatter/import transformations if the repo has a deterministic, accepted tool and exact path scope.

Non-candidates for autonomous remediation include trading/economic semantics, risk, sizing, calibration, strategy, execution, security policy, CI policy, and the coordinator's own guardrails.

No auto-merge is planned for initial rollout.

## 12. Claude's newer agent/tooling capabilities change how the investigation should run

Current official Claude Code docs establish that:

- subagents have separate context and can reduce main-session context pressure;
- built-in Explore and Plan agents skip `CLAUDE.md` and parent git status for speed;
- custom/general-purpose agents load repository instructions;
- custom agents can scope tools, models, skills, hooks, memory, MCP servers, and worktree isolation;
- `isolation: worktree` is available, but worktree baselines must be understood before using it for modifying experiments;
- hooks expose worktree/session/subagent lifecycle events.

Investigation implication: parallel agents are useful for independent evidence gathering, but the main agent must own synthesis and current branch/policy truth. Mutating experiments use explicit worktree isolation.

## 13. Existing project tooling is already rich enough

The current repo routing policy marks these as usable/relevant when triggered:

- Superpowers — primary development process;
- GitNexus — structural evidence, with mandatory index sanity checks because this repo has already observed corrupt but apparently "exact" results;
- dimensional-analysis — trading-math correctness only;
- Chrome DevTools MCP — browser evidence only;
- Context7 — version-specific library docs, fail-open;
- 42Crunch and second-opinion — blocked external, skip rather than gating work.

The investigation should exploit this routing, not "use every plugin" mechanically.

## 14. Research questions that remain genuinely open

1. What fraction of QCP findings have durable enough identity for external state?
2. How often would active-work overlap have suppressed a valid escalation in recent history?
3. Are exact finding claims worth their manual/agent metadata overhead?
4. Is local session/worktree advisory state useful enough to build, or does remote PR/branch awareness plus persistence cover the problem?
5. Which control-plane topology minimizes long-lived secrets and operational burden in this single-developer public repo?
6. Which finding types gain value from SARIF versus existing CI output?
7. Which deterministic generators are truly idempotent and path-contained?
8. What staged activation proves value before granting write authority?
