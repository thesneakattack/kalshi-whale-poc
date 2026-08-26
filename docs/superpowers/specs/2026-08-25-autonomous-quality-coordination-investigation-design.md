# Autonomous Quality Coordination Investigation — Design Specification

**Repository:** `thesneakattack/kalshi-whale-poc`

**Design date:** 2026-08-25

**Baseline reviewed while drafting:** `main` at `a0c156973acf41fba23d8bd46e1d7bb126584a3f`

**Status:** Investigation design. It intentionally does not select the production control-plane architecture in advance.

## 1. Purpose

Investigate how the existing Quality Control Plane can evolve from deterministic detection into low-noise reporting, durable escalation, and narrowly-scoped mechanical remediation **without competing with active Claude sessions, manual work, or other initiative branches**.

The deliverable is evidence-backed architecture plus a production implementation plan. No privileged automation is activated during the investigation.

## 2. Non-goals

The investigation will not:

- replace `tools/quality_audit` with a second audit framework;
- enable autonomous trading/config/risk/calibration/strategy changes;
- create GitHub issues automatically;
- create autonomous remediation PRs;
- enable auto-merge;
- grant a bot branch-protection bypass;
- modify user-global Claude settings;
- force every installed plugin into the workflow;
- treat an open branch/PR as proof that a finding is fixed;
- claim visibility into unpushed local work from remote CI.

## 3. Success criteria

The investigation succeeds when a reviewer can answer, with measured evidence:

1. **Identity:** how a QCP finding is recognized as the same durable problem across ordinary source movement.
2. **Branch semantics:** which observations remain branch-local and which can become repository-level work.
3. **Contention avoidance:** how open PRs/branches/claims/persistence affect escalation without hiding unresolved defects.
4. **Local/remote boundary:** whether local Claude session/worktree awareness adds value beyond remote Git state.
5. **Reporting:** which finding classes belong in CI logs/statuses, SARIF, PR annotations, or GitHub issues.
6. **Credentials/events:** which control-plane topology gives the minimum authority required, with no PR secret exposure.
7. **Remediation:** the exact initial deterministic fixer allowlist, if any, and its protected paths.
8. **Rollout:** a staged dry-run → reporting → issue → draft-PR progression with explicit rollback gates.
9. **Verification:** fault-injection scenarios demonstrate the coordination rules before any privileged production activation.

## 4. Architectural candidates to compare

### Candidate A — Woodpecker detection + Woodpecker write lane

Woodpecker runs QCP and, only on trusted events, mints/uses a scoped GitHub App installation token for reporting/escalation/remediation.

Research questions:

- key storage and token minting burden;
- exact event filters and secret isolation;
- separation between untrusted PR jobs and trusted main/cron/manual jobs;
- operational recovery when GitHub write calls fail;
- whether one CI platform owning both detection and action is simpler or creates excessive privilege concentration.

### Candidate B — Woodpecker detection + GitHub-native write controller

Woodpecker stays authoritative for exhaustive CI. A GitHub Actions workflow or equivalent GitHub-native controller consumes a deterministic artifact/result and performs approved write actions using job-scoped GitHub credentials.

Research questions:

- trustworthy handoff of detector output;
- duplication/latency between systems;
- narrower credential management versus added workflow complexity;
- whether GitHub-native SARIF/issue/PR actions materially simplify the write plane.

### Candidate C — GitHub-native coordination/control plane + Woodpecker verifier

GitHub-native workflow owns coordination state/reporting/escalation; Woodpecker remains required verification for code changes.

Research questions:

- whether this duplicates QCP execution;
- how branch protection/status requirements interact;
- whether scheduled/default-branch coordination is simpler to reason about than Woodpecker secrets.

### Candidate D — Report-only control

No autonomous GitHub issues/remediation. QCP emits richer branch/main reports and durable state; humans/Claude decide what becomes work.

This is the control candidate. More autonomy must demonstrate enough value to beat it.

## 5. Finding lifecycle model to test

The investigation will prototype, not assume, a lifecycle similar to:

```text
branch observation ──► branch report/gate only

main observation
      │
      ▼
   OBSERVED
      │ persistence evidence
      ▼
  PERSISTENT
      │ active work? ──yes──► SUPPRESSED_PENDING_WORK
      │                         │ merged/closed/stale
      ▼                         └────► fresh main evaluation
  ESCALATION_ELIGIBLE
      │
      ├── report/SARIF
      ├── issue if actionable policy says so
      └── remediation candidate only if deterministic allowlist says so
```

No state transition other than a fresh integrated audit may declare a finding resolved.

## 6. Identity model to test

The investigation must compare:

- existing `finding_id`;
- a normalized semantic `automation_key` derived from check + scope + subject evidence;
- GitHub/SARIF location-based correlation where applicable.

Mutation tests must include line shifts and multiple same-rule findings. The design should avoid changing existing baseline identity unless doing so is independently valuable.

## 7. Active-work signals to compare

Evaluate each signal separately and in combinations:

1. exact finding claim in PR body/structured metadata;
2. open PR changed-path overlap;
3. active remote branch changed-path overlap;
4. PR draft/ready state;
5. branch age/staleness;
6. persistence/observation count;
7. optional local Claude session/worktree advisory metadata;
8. no suppression beyond persistence (control).

Score false suppression, duplicate-work prevention, implementation complexity, maintenance cost, and explainability.

Exact claims suppress escalation but never mark resolution. Path overlap is weaker evidence than an exact claim.

## 8. Local Claude coordination research

Claude Code currently exposes worktree/session/subagent hooks and custom-agent isolation. The investigation may prototype **local advisory observation only**, for example a temporary registry of active worktrees/session scopes.

Constraints:

- no remote CI dependency on local-only state unless a later explicit publishing mechanism is selected;
- no user-global settings edit;
- no automatic lock preventing legitimate work;
- local stale state must expire or be reconstructable;
- compare against doing nothing; YAGNI wins if remote branch/PR awareness is enough.

## 9. Parallel-agent research design

Use parallel agents only where tracks are independent:

- Track A: QCP finding identity and scanner semantics;
- Track B: git/PR concurrency and historical cadence;
- Track C: GitHub/Woodpecker/Claude Code official platform/security semantics;
- Track D: adversarial review after solution selection.

Main Claude owns the authoritative active-work map and final synthesis.

Policy-sensitive tracks should not rely blindly on built-in Explore/Plan because those agents skip repository instructions and parent git state. Use custom/general-purpose agents or inject the necessary constraints explicitly.

## 10. Tool-routing design

- **Superpowers:** always process owner.
- **GitNexus:** structural evidence for QCP producer/consumer/blast-radius analysis after index sanity check.
- **Context7:** installed-version library research only; fail open to official docs.
- **GitHub/gh:** current branches, PRs, changed paths, history, statuses.
- **Woodpecker:** exhaustive validation and experiment target for workflow semantics; use local linter where possible.
- **Chrome DevTools:** only if a UI/reporting surface actually needs browser behavior research.
- **dimensional-analysis:** only if a candidate fixer touches trading-math code; otherwise explicitly record NOT_NEEDED.
- **42Crunch/second-opinion:** blocked external remains non-gating.

## 11. Credential threat model requirements

Every candidate topology must address:

- malicious/untrusted PR code attempting secret exfiltration;
- PR targeting `main` matching branch filters;
- GitHub App private-key exposure and token scope/expiry;
- job-scoped GitHub Actions token permissions;
- stale/replayed detector results;
- branch-protection bypass;
- self-modifying remediator/control-plane changes;
- issue/PR storm from unstable identity;
- externally controlled text becoming prompt injection if an AI remediation agent is ever introduced;
- failure/retry idempotence.

No production architecture gets write authority merely because it is technically possible.

## 12. Reporting research requirements

Map current finding classes to surfaces using an evidence matrix:

- source-located deterministic static finding → evaluate SARIF/code scanning;
- branch-local hard regression → CI status/check/annotation;
- persistent actionable integrated defect → GitHub issue candidate;
- transient observation/suppression → machine state/log only;
- runtime-only anomaly → existing runtime observability/alerting unless durable engineering work is proven.

Issue creation should be exceptional enough that the issue tracker remains a work queue, not telemetry storage.

## 13. Remediation research requirements

A candidate mechanical fixer must prove:

- deterministic output;
- idempotence;
- explicit allowed input/output paths;
- no control-plane/trading-domain writes;
- clean-tree precondition;
- patch/diff size bounds;
- independent test/CI verification;
- repeated execution does not create churn;
- failure cannot weaken the guard that requested the fix.

Initial rollout, if any, creates draft PRs only. Auto-merge remains a separate future decision.

## 14. Evidence matrix for architecture selection

Score each credible candidate 1–5 with written evidence for:

- contention avoidance;
- false-positive escalation risk;
- false-negative suppression risk;
- credential exposure;
- untrusted-PR isolation;
- implementation complexity;
- operational complexity;
- recovery/idempotence;
- observability/explainability;
- maintenance burden;
- compatibility with current branch/CI policy;
- ability to stage/disable safely.

Do not hide a fatal security/coordination flaw behind a high average score; mark veto conditions separately.

## 15. Investigation outputs

By completion this initiative must produce:

- measured concurrency/cadence report;
- full QCP identity/automation-suitability matrix;
- active-work strategy experiment matrix;
- platform credential/event research record with official citations;
- reporting-surface decision;
- threat model;
- deterministic-remediation candidate inventory;
- no-write coordinator simulator/fault results;
- architecture decision record with adversarial review;
- production design specification;
- production implementation plan written only after the architecture is chosen.
