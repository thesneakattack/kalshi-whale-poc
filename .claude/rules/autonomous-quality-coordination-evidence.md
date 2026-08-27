# Autonomous Quality Coordination — Evidence and Non-Interference Rule

Applies to investigation, design, and later implementation of automated quality reporting, issue escalation, remediation PRs, and any proposed merge automation.

## Core principle

**Automation may observe broadly, but it earns authority to act narrowly.**

Do not optimize for maximum autonomy. Optimize for durable quality enforcement with near-zero contention against active human/Claude work and minimal credential exposure.

## Branch truth and active-work rule

- `main` is the authoritative integrated branch.
- A finding that exists only on a feature/initiative branch is owned by that branch. It may annotate/fail that branch according to existing CI policy, but it must not create a global repository issue or independent remediation branch.
- Open PRs and active remote branches are evidence that work may already be in flight. They may **suppress or delay escalation**, never prove resolution.
- A claimed finding is still resolved only when a fresh audit of integrated `main` no longer reports it.
- Remote CI cannot know about unpushed local edits. Do not design as if it can. Investigate whether local Claude session/worktree metadata provides useful advisory coordination, but do not create a distributed lease system without measured need.

## No guessed quarantine period

Do not hardcode 24 hours, 48 hours, or any other persistence window because it sounds conservative.

Measure repository cadence first:

- time from branch creation/first commit to PR/merge;
- overlapping initiatives;
- frequency of short-lived transitional findings;
- stale branch behavior;
- repeated audit cadence.

Then compare persistence policies using actual repo data and synthetic scenarios.

## Finding identity is an investigation question

Do not assume `QualityFinding.finding_id` is automatically a durable escalation key. Existing scanners use mixed identity schemes, including line-sensitive IDs.

Before issue deduplication or persistence state exists, prove identity behavior under:

- inserted lines;
- harmless file movement/rename where applicable;
- multiple findings of the same rule in one scope;
- symbol rename;
- resolution and recurrence.

Prefer a separate automation/escalation identity if changing existing baseline IDs would destabilize the Quality Control Plane.

## Evidence classes

Every recommendation must state which evidence supports it:

1. current source/test/CI behavior;
2. current git/PR/branch history;
3. deterministic experiment or fault injection;
4. live runtime evidence when required;
5. authoritative upstream documentation matching installed versions;
6. inference/hypothesis.

Inference is allowed during investigation but must remain labeled as such.

## Specialized tool routing

Superpowers owns process. Specialized tools supply evidence.

- **GitNexus:** use for blast-radius/dependency questions after index sanity checks; confirm consequential results against source.
- **Context7:** use for installed-version library docs; fail open to official docs. Never override canonical Kalshi docs.
- **Chrome DevTools MCP:** use only for browser/UI/API/network/WebSocket evidence that requires a browser.
- **dimensional-analysis:** use only when the investigation/implementation touches probability, pricing, contracts, bankroll, exposure, P&L, EV, fees, calibration, weights, thresholds, or normalization.
- **42Crunch / second-opinion:** follow `.claude/rules/tooling-plugins.md`; if still BLOCKED_EXTERNAL, skip silently.
- **parallel agents:** use only for independent research tracks. Main Claude reconciles all conclusions against current measurements and repo policy.

## Subagent policy

For policy-sensitive conclusions, prefer the main agent or a custom/general-purpose subagent that loads repository instructions.

Claude Code's built-in Explore and Plan agents skip `CLAUDE.md` and the parent session's git status for speed. Their research can be useful, but it is non-authoritative unless the prompt explicitly provides the required branch/safety/initiative constraints and the main agent revalidates the result.

Mutating experiments must run in an explicitly isolated worktree created from the intended baseline. Do not let a subagent silently mutate the primary checkout.

## Credential and event rule

No privileged write path may be enabled during this investigation.

When comparing production architectures:

- PR/pull-request audit lanes remain secretless;
- Woodpecker `branch: main` alone is not sufficient for privileged steps because a PR targeting `main` also matches that branch condition;
- a Woodpecker privileged lane, if selected, must require an explicit trusted event such as `event: push` with `branch: main`, or a deliberately controlled `cron`/`manual` event;
- compare GitHub App installation tokens against GitHub Actions' job-scoped `GITHUB_TOKEN` and report-only alternatives rather than assuming one winner;
- grant minimum permissions and avoid bypass/admin authority.

## Remediation authority rule

The investigation may recommend autonomous remediation only for transformations that are deterministic, path-contained, idempotent, independently verifiable, and outside protected economic/control-plane domains.

Initial protected domains include:

- real trading and order execution;
- risk limits and kill switches;
- sizing/bankroll/exposure;
- whale/advisory/confidence/calibration logic;
- strategy/EV/fee/P&L semantics;
- settlement/lifecycle semantics;
- security/authentication/authorization policy;
- CI/branch-protection/credential policy;
- the autonomous quality coordinator's own policy/guard implementation.

An autonomous actor must never "fix" a failed guard by weakening, deleting, baselining, or bypassing that guard.

**Generalized scope note (2026-08-27):** this rule's authority-earning criteria
(deterministic, path-contained, idempotent, independently verifiable, outside protected
domains) apply unchanged to local git/filesystem mutation authority, not only GitHub write
authority — see `tools/quality_coordination.py`'s three cleanup actions
(`docs/superpowers/specs/2026-08-27-autonomous-quality-coordination-workflow-design.md`
§8), which this rule now also governs. The credential/event topology sections above remain
GitHub-specific (there is no GitHub credential in this tool at all — see that spec's §2 and
§11), but the core principle ("Automation may observe broadly, but it earns authority to
act narrowly") and the Remediation authority rule's protected-domain list apply identically
to this tool's own three actions.

## No auto-merge assumption

Auto-merge is not part of the initial production rollout. If evidence later supports it for an exact mechanical allowlist, enabling it is a separate explicit decision with its own review and fault-injection proof.

## Investigation completion rule

The investigation is complete only when:

- current concurrency and audit behavior are measured;
- finding identity stability is characterized;
- coordination/suppression strategies are compared with false-positive/false-negative scenarios;
- control-plane credential/event topologies are threat-modeled and benchmarked for operational complexity;
- reporting surfaces are compared;
- deterministic remediation candidates are empirically classified;
- a no-write prototype survives the scenario/fault matrix;
- an adversarial review attacks the preferred architecture;
- rejected alternatives have evidence-backed reasons;
- a separate production implementation plan exists.
