# Autonomous Quality Coordination Investigation Execution Plan

> **Agentic execution:** Use `.claude/skills/autonomous-quality-coordination-investigation/SKILL.md` as the authoritative orchestrator. Execute exactly one numbered investigation task, verify, commit, report, and stop. Superpowers skills are supporting disciplines and process gates, not a replacement progress ledger.

**Goal:** Determine, through current-repo evidence, historical analysis, authoritative platform research, simulation, and fault injection, how the existing Quality Control Plane can report/escalate/remediate durable findings without stepping on concurrent work or exposing excessive GitHub authority.

**Architecture:** Investigation-first. The existing QCP remains the detector; this plan measures identity, concurrency, reporting, coordination, event/credential, and remediation behavior before selecting a production controller. All prototypes are no-write toward GitHub and isolated from the primary checkout.

**Tech Stack:** Python 3.13, existing `services.quality`/`tools.quality_audit`, git/GitHub CLI, Woodpecker CI, Claude Code/Superpowers, GitNexus where justified, Context7/official docs, optional Chrome DevTools only if a browser-facing question emerges.

**Spec:** `docs/superpowers/specs/2026-08-25-autonomous-quality-coordination-investigation-design.md`

## Global Constraints

- Current HEAD/code/tests/git history are implementation truth.
- Re-ground active local worktrees, remote branches, open PRs, and CI before every task.
- Do not edit a file owned by active parallel work without explicit reconciliation.
- `main` is integrated truth; normal investigation commits use a short-lived initiative branch/worktree.
- Branch-only findings never create repository-level issues/remediation during this investigation.
- No GitHub issue/PR creation by automation, no privileged CI secret, no GitHub App private key, and no auto-merge during investigation.
- No tests read/write live `data/*.db`; no real trading; no weakening safety gates.
- Do not modify user-global Claude settings.
- Do not hardcode persistence/grace thresholds before I2 measures repo cadence.
- Do not assume `finding_id` is a durable automation key before I1.
- Superpowers owns process; specialized tools are used only when relevant and capability-verified.
- Built-in Explore/Plan subagents are non-authoritative for policy-sensitive conclusions unless repo constraints are explicitly injected and the main agent revalidates them.
- One task = one independently reviewable investigation commit.

---

## I0 — Re-ground the repository, concurrency map, and toolchain

**Create**
- `docs/superpowers/research/2026-08-25-autonomous-quality-coordination-baseline.md`

**Read/inspect**
- `CLAUDE.md`
- all `.claude/rules/*.md`
- this initiative's known findings/spec/plan/orchestrator
- `.claude/settings.json`, `.claude/hooks/**`, relevant `.claude/skills/**`
- `services/quality/**`
- `tools/quality_audit/**`
- `tests/test_quality_audit.py` and related tests
- `.woodpecker/**`, `.github/workflows/**`, `docs/woodpecker-ci.md`
- recent git history, current worktrees, local/remote branches, open PRs, PR changed files, current commit statuses

**Capabilities**
- Superpowers process skills
- `dispatching-parallel-agents` only for independent read-only inventory tracks
- GitNexus only after index sanity check
- GitHub/`gh` for authoritative remote-work state

- [x] Record current branch, HEAD, dirty state, `git worktree list`, origin/main divergence, open PRs, remote initiative branches, and changed-path ownership. (Baseline doc §1.)
- [x] Identify any files this initiative must not touch because another branch currently changes them. (Baseline doc §2 — contention map.)
- [x] Record current Woodpecker/GitHub Actions topology and required status contexts from repo policy/current remote state where accessible. (Baseline doc §3.)
- [x] Inventory current Claude hooks/skills/plugins by **capability state**, not installation state; record ACTIVE/LIMITED/BLOCKED/NOT_NEEDED for this investigation. (Baseline doc §5.)
- [x] Sanity-check GitNexus; if the known corrupted-index smell occurs, repair according to `tooling-plugins.md` before any structural query. (Baseline doc §5/§6.)
- [x] Use parallel read-only tracks for (a) QCP map, (b) active-work map, and (c) external platform research only if doing so reduces context load without sharing mutable state. (Baseline doc §6.)
- [x] Main Claude synthesizes one baseline containing authoritative paths, active contention, current detector/CI flow, and explicit unknowns. (Baseline doc §7/§8.)
- [x] If `.claude/rules/quality-capabilities.md` is no longer contested, add only a small routing entry for this orchestrator in a separate focused commit; otherwise record the deferral and leave it untouched. (Router entry landed as its own commit, `e55b1e8`.)
- [x] Commit the baseline (and router entry only if conflict-free): `docs: baseline autonomous quality coordination investigation`. (`0853c41`, router entry `e55b1e8`.)

**Acceptance**
A cold reviewer can see exactly what is active, what is contested, which tools are actually usable, and which repository files/services constitute the current QCP/CI control plane.

---

## I1 — Audit every QCP finding for durable automation identity

**Create**
- `docs/superpowers/research/2026-08-25-quality-finding-identity-audit.md`

**Potential temporary test/prototype work**
- use a temp directory or explicit investigation worktree; do not change production identity yet.

**Capabilities**
- GitNexus for `QualityFinding`, `run_audit`, scanner producer/consumer graph after sanity check
- `root-cause-debugging` if observed identity behavior is surprising
- parallel agents may independently inventory disjoint scanners

- [x] Enumerate every registered scanner and every `QualityFinding` shape it can emit: `check`, severity, confidence, source, scope, evidence fields, remediation, and exact `finding_id` construction. (Doc §3 — 16 CI-scanner shapes + 7 runtime-producer shapes.)
- [x] Classify each ID as semantic-stable, location-sensitive, content-sensitive, aggregate/inventory, or unsuitable for external state. (Doc §5.)
- [x] Write synthetic mutation experiments that insert harmless lines before a finding and record whether the ID changes. (Doc §4, Appendix A harness.)
- [x] Test multiple same-rule findings in one file/scope so a proposed normalized identity cannot collapse distinct defects. (Doc §4/§5.2.)
- [x] Test recurrence after resolution and a plausible file/symbol rename where the scanner supports it. (Doc §5.3/§5.4.)
- [x] Compare three identity strategies: raw `finding_id`; separate normalized `automation_key`; reporting-surface-native correlation such as SARIF rule+location. (Doc §7.)
- [x] Quantify baseline-migration cost if existing `finding_id`s were changed. (Doc §8.)
- [x] Recommend an identity contract **without implementing it**, including collision and recurrence semantics. (Doc §9 — specification only, later implemented externally at I11 §3 rather than by modifying scanners.)
- [x] Commit: `research: characterize quality finding identity stability`. (`07398bd`.)

**Acceptance**
The investigation can state exactly which existing IDs are safe for durable state and whether a separate automation identity is required, backed by mutation tests rather than intuition.

---

## I2 — Measure repository concurrency and derive persistence candidates

**Create**
- `docs/superpowers/research/2026-08-25-quality-coordination-cadence.md`

**Capabilities**
- GitHub/`gh` and git history
- parallel read-only agent for historical extraction if useful
- no GitNexus needed unless a code-path question unexpectedly arises

- [x] Define a reproducible sampling window from available git/PR history (prefer enough recent initiatives to include current multi-session behavior). (Doc §2, 41h window.)
- [x] Measure time from first initiative commit to PR creation and merge/close where data exists. (Doc §3.)
- [x] Measure overlap: number of simultaneously active initiative branches/PRs and their changed-path intersections. (Doc §4.)
- [x] Identify short-lived branch findings/manifest/baseline transitions that would have generated noisy escalation if acted on immediately. (Doc §6, the 3 replayed episodes.)
- [x] Measure stale branch tail separately from normal active-work duration. (Doc §8.)
- [x] Simulate candidate persistence policies based on observation count, elapsed time, audit cadence, and active-work suppression. (Doc §9, P0-P-k2+T6h policies.)
- [x] Compare false early escalation and excessive delay; do not optimize only one side. (Doc §9 table.)
- [x] Produce candidate threshold ranges and explain confidence/data limitations; do not encode them in production code. (Doc §9 "Candidate ranges," §11 bounded unknowns.)
- [x] Commit: `research: measure quality coordination cadence`. (`39f0655`.)

**Acceptance**
Any recommended persistence/grace policy is derived from this repo's observed cadence and simulated trade-offs, not a generic 24/48-hour guess.

---

## I3 — Compare active-work detection and suppression strategies

**Create**
- `docs/superpowers/research/2026-08-25-active-work-suppression-matrix.md`

**Capabilities**
- GitHub/`gh`
- Claude Code official docs for session/worktree hooks
- Superpowers brainstorming for alternatives
- parallel agents may model independent strategy families

- [x] Define synthetic scenarios: exact finding claimed in PR; same-path unrelated PR; directory overlap; remote branch without PR; draft PR; stale branch; branch closed unmerged; merge fixes finding; merge does not fix finding; local unpushed work; two active PRs overlap one finding. (Doc §4, scenario matrix.)
- [x] Evaluate exact finding claims in PR body/structured marker. (Doc §6, signal A.)
- [x] Evaluate open-PR changed-path/scope overlap. (Doc §7, signal B.)
- [x] Evaluate active remote-branch overlap and staleness. (Doc §8, signal C.)
- [x] Evaluate delay/observation-only suppression as the minimal control. (Doc §10, signal E.)
- [x] Research Claude WorktreeCreate/WorktreeRemove/SessionEnd/Subagent lifecycle hooks and prototype on paper whether local advisory state could represent unpushed work without pretending remote CI can read it. (Doc §9, signal F.)
- [x] Compare doing **no local registry** against a local advisory registry; include stale-state cleanup and operational burden. (Doc §9 — rejected, ≤2.3-min real coverage window.)
- [x] Define precedence: exact claim > strong semantic/path evidence > weak overlap > persistence only, if evidence supports that order. (Doc §11.)
- [x] Assert that suppression never becomes resolution; a fresh `main` audit is the only resolver. (Doc §11 invariant, later proven executably in I8.)
- [x] Commit: `research: compare active work suppression strategies`. (`4fb265e`.)

**Acceptance**
The selected coordination signals have known false-positive/false-negative behavior and no claim of omniscience over local unpushed work.

---

## I4 — Compare event and credential control-plane topologies

**Create**
- `docs/superpowers/research/2026-08-25-quality-control-plane-topologies.md`

**Capabilities**
- current official GitHub App, GitHub Actions, SARIF/code-scanning, and Woodpecker documentation
- Context7 only if a library/SDK implementation detail becomes relevant; official platform docs remain primary
- `ci-cd-guardrails`

**Required candidates**
- A: Woodpecker detector + Woodpecker GitHub-App write lane
- B: Woodpecker detector + GitHub-native write controller
- C: GitHub-native coordination controller + Woodpecker verifier
- D: report-only/no autonomous write

- [x] For each candidate draw event flow, trust boundaries, credentials, token lifetime, minimum permissions, retry/idempotence point, and failure/recovery path. (Doc §3, Candidates A-D.)
- [x] Verify from current Woodpecker docs that `branch: main` also matches PRs targeting main; require explicit event filtering in any privileged Woodpecker design. (Doc §2.1; re-verified live against real pipeline data at I9.)
- [x] Verify current Woodpecker secret behavior for pull-request events and record why PR lanes stay secretless. (Doc §2.1; re-verified live at I9.)
- [x] Research GitHub App installation-token scope/expiry and minimum permission model. (Doc §2.2.)
- [x] Research GitHub Actions job-scoped token as a competing write-side credential, including permission configuration relevant to candidate actions. (Doc §2.3.)
- [x] Compare key-management burden, secret exposure, platform coupling, and auditability. (Doc §4, comparison matrix.)
- [x] Keep any live credential/API proof read-only or use existing user auth; do not create/store a bot private key in this task. (No credential created anywhere in this investigation, confirmed again at I13.)
- [x] Commit: `research: compare quality control plane credential topologies`. (`a4521e7`.)

**Acceptance**
There is no unexamined assumption that Woodpecker+App, GitHub Actions, or autonomous writing is inherently best.

---

## I5 — Compare reporting surfaces and noise economics

**Create**
- `docs/superpowers/research/2026-08-25-quality-reporting-surfaces.md`

**Capabilities**
- GitHub code-scanning/SARIF official docs
- existing QCP JSON output and Woodpecker status/log behavior
- Chrome DevTools only if an actual browser-facing presentation question is chosen; otherwise record NOT_NEEDED

- [x] Inventory current QCP/CI output visible on branch pushes and PRs. (Doc §2.)
- [x] Build a finding-class matrix: source-located deterministic error, aggregate architecture error, heuristic warning, inventory/info, runtime anomaly, persistent actionable defect. (Doc §3.)
- [x] Prototype QCP→SARIF conversion **offline only** for representative source-located findings; do not upload. (Doc §4, Appendix A converter — never uploaded, confirmed again at I10/I11.)
- [x] Verify how SARIF identity/location semantics interact with I1's stable automation identity requirements. (Doc §4.)
- [x] Compare CI log/status, SARIF, PR annotation/comment/check, and GitHub issue for each finding class. (Doc §5.)
- [x] Simulate a week of repeated observations using I2 cadence and estimate issue/comment churn under each policy. (Doc §6, Appendix B simulation.)
- [x] Define a "silent intermediate state" rule so observations/suppression do not generate repetitive comments. (Doc §7; implemented executably at I8, reused unchanged at I11 §6.)
- [x] Commit: `research: select quality finding reporting surfaces`. (`dcaf475`.)

**Acceptance**
GitHub issues, if retained, represent durable actionable work rather than becoming another telemetry stream.

---

## I6 — Threat-model the proposed authority boundary

**Create**
- `docs/superpowers/research/2026-08-25-autonomous-quality-threat-model.md`

**Capabilities**
- `ci-cd-guardrails`
- Superpowers code/security review discipline
- official GitHub/Woodpecker docs
- second-opinion/42Crunch remain skipped if still BLOCKED_EXTERNAL

- [x] Threat-model malicious/untrusted PR code attempting credential exfiltration. (Doc T1.)
- [x] Threat-model PR-target-branch filter mistakes. (Doc T2; re-verified live at I9.)
- [x] Threat-model unstable identity causing issue/PR storms. (Doc T3.)
- [x] Threat-model stale detector result/replay after main moves. (Doc T4; the "stale-SHA" property proven executably at I9's `write_gate.py`.)
- [x] Threat-model self-modification: remediator weakens scanner/baseline/policy/workflow/branch protection to make itself green. (Doc T5.)
- [x] Threat-model protected economic domains and accidental real-money semantic change. (Doc T6.)
- [x] Threat-model prompt injection through issue/PR text if an AI candidate-remediation path is ever introduced. (Doc T7.)
- [x] Define protected paths/capabilities and immutable/external policy boundaries for any later fixer. (Doc §3; reused unchanged at I7/I11 §8.)
- [x] Define fail-open vs fail-closed behavior for detection failure, GitHub API outage, token failure, active-work ambiguity, and identity ambiguity. (Doc §4; the GitHub-outage case implemented at I11 §10.)
- [x] Add veto conditions to the architecture scoring matrix. (Doc §5, V1-V8; applied to Candidates A/C/D at I10 §1.)
- [x] Commit: `research: threat model autonomous quality authority`. (`c8fe088`.)

**Acceptance**
No architecture can win merely by averaging well while retaining a fatal credential/contention/self-modification flaw.

---

## I7 — Inventory and prove deterministic remediation candidates

**Create**
- `docs/superpowers/research/2026-08-25-deterministic-remediation-inventory.md`

**Capabilities**
- GitNexus only for candidate blast radius where useful
- TDD/property-style deterministic tests in an isolated temp/worktree
- dimensional-analysis only if a candidate reaches financial/trading math; expected default is NOT_NEEDED

- [x] Inventory existing repository generators/autofixers/codemods rather than inventing new ones.
- [x] Test `tools.project_manifest --write`/`--check` behavior for idempotence and exact path output in a disposable tree/worktree.
- [x] Test frontend bundle generation only after re-grounding against any merged frontend-modularization work; record its exact input/output path set. (Re-grounded: PR #11 was docs-only design, no implementation shipped. Verdict: no committed target exists — REJECTED as not applicable, not tested further.)
- [x] Inventory formatter/import tools actually configured by the repo and reject any merely hypothetical fixer. (None configured — no pyproject.toml/setup.cfg/.flake8, eslint has no --fix.)
- [x] For each candidate run twice and require an empty second diff. (Literal instruction unsatisfiable for a generator with an embedded timestamp — see doc §3 for the semantic-idempotence refinement actually applied, recorded as a negative result on the plan wording.)
- [x] Test dirty-tree/preexisting-change behavior and define refusal semantics.
- [x] Test path containment and diff-size bounds.
- [x] Mark every candidate AUTO_DRAFT_PR, REPORT_ONLY, or REJECTED with evidence. No candidate gets auto-merge authority.
- [x] Commit: `research: prove deterministic remediation candidates`.

**Acceptance**
Every proposed fixer has executable proof of determinism/idempotence/path scope; no semantic "engineering" fix is mislabeled mechanical.

---

## I8 — Build a no-write coordination simulator

**Create in an explicit isolated investigation worktree**
- `tools/quality_coordination_sim/` or another narrowly named prototype location selected after re-grounding
- `tests/` fixtures for the prototype
- `docs/superpowers/research/2026-08-25-quality-coordinator-simulation.md`

**Important**
This is throwaway/experimental until I10 selects architecture. Do not wire it into production CI or GitHub APIs.

**Capabilities**
- Superpowers `using-git-worktrees`
- `test-driven-development`
- `systematic-debugging` for unexpected behavior
- subagent modification only inside the explicit worktree if parallelism is justified

- [x] Write failing scenario tests for branch-only finding, main observation, repeated main observation, line-shift identity, exact PR claim, path-overlap PR, stale branch, merge resolves, merge does not resolve, recurrence, two concurrent claims, ambiguous local-only work. (Confirmed red first: `ModuleNotFoundError` before `coordinator.py` existed.)
- [x] Implement the smallest pure coordinator state machine needed to exercise I1–I3 policies; no network writes. (~150 lines, stdlib only.)
- [x] Feed recorded/synthetic GitHub state snapshots rather than calling GitHub inside core decision logic. (`Observation` is caller-supplied; no GitHub/Woodpecker client exists in the package.)
- [x] Prove repeated identical input is idempotent.
- [x] Prove a claim/overlap can only suppress/delay, never mark resolved. (Dedicated test, 50-audit replay.)
- [x] Prove branch-only findings cannot become repository escalation candidates.
- [x] Record decision explanations so every suppression/escalation is inspectable.
- [x] Measure whether the policy would have created duplicate work against the I2 historical sample. (Real I2 §6 episode timestamps + live-fetched PR #12 merge time — zero duplicate-work events.)
- [x] Commit prototype and research result separately if the repo's task discipline requires; mark prototype status clearly. (Prototype status marked EXPERIMENTAL/THROWAWAY in package docstring and research doc header.)

**Acceptance**
The coordination policy survives the scenario matrix without GitHub write authority and produces explainable decisions.

---

## I9 — Fault-inject event and workflow semantics without privileged writes

**Create/modify only after current active branches are reconciled**
- temporary/synthetic Woodpecker workflow fixture or dedicated no-secret investigation workflow if justified
- research report `docs/superpowers/research/2026-08-25-quality-event-fault-injection.md`

**Capabilities**
- `ci-cd-guardrails`
- `woodpecker-cli lint` and local execution if available/safe
- Woodpecker remains exhaustive verifier for pushed investigation changes

- [x] Prove a candidate privileged filter is **not** selected on pull_request events targeting main. (Against a real specimen: pipelines 92/94/96, `event=pull_request, branch=main`, PR #3.)
- [x] Prove the same filter is selected on the intended trusted main-push/cron/manual event in a synthetic/local evaluation. (Against real pipeline 163, `event=push, branch=main`.)
- [x] Prove PR lane operates with no write secret requirement. (Woodpecker docs, live-fetched: secrets require explicit per-secret `pull_request` opt-in; default is push-only.)
- [x] Test stale main SHA handling: a decision generated for old main must refuse a write in the eventual design.
- [x] Test duplicate/retry behavior for the chosen reporting/escalation action using a fake GitHub transport.
- [x] Do not install real App credentials or issue/PR writes. (Confirmed: no `.woodpecker/*.yml` change, no PR opened, `FakeGitHubTransport` has no network dependency at all.)
- [x] Commit: `test: fault inject quality control event semantics`.

**Acceptance**
The event/credential design is proven with failure cases before any real write credential exists.

---

## I10 — Architecture decision and adversarial retort

**Create**
- `docs/superpowers/research/2026-08-25-autonomous-quality-architecture-decision.md`

**Capabilities**
- Superpowers `brainstorming`
- `dispatching-parallel-agents` for an independent adversarial reviewer
- `requesting-code-review`
- main Claude remains final synthesizer

- [x] Score all surviving Candidate A–D architectures on the spec matrix using cited evidence from I0–I9. (I4's matrix carried forward, contextualized with I7/I9 findings.)
- [x] Mark veto conditions separately from weighted scores. (I6 §5's V1-V8, applied per-candidate, kept as its own table row.)
- [x] Select a provisional winner and the simplest viable rollout. (D report-only, extended with a persisted observation series; dry-run→reporting stage only, issue/draft-PR stages designed but not activated.)
- [x] Dispatch an independent adversarial reviewer with the decision, evidence table, and repo safety/branch rules explicitly supplied. (Isolated general-purpose subagent, no shared context, explicit rule text supplied inline.)
- [x] Require the reviewer to find failure modes, hidden maintenance cost, security gaps, and reasons the report-only control may actually be better. (Inverted since D=report-only was the provisional winner: asked instead for the direct case for Candidate C, plus failure-mode/maintenance-cost/security-gap angles against D specifically.)
- [x] Reconcile each critique against evidence; change the decision only where the critique is supported. (§6: 7 of 9 objections produced concrete document edits; 2 acknowledged without changing the topology choice.)
- [x] Record concessions, rejected objections, and remaining uncertainty. (§6, explicit subsections for each.)
- [x] Commit: `docs: decide autonomous quality coordination architecture`.

**Acceptance**
The architecture survives a deliberate devil's-advocate pass and wins against a report-only control for documented reasons, or the investigation concludes that report-only is the correct current architecture.

---

## I11 — Write the production design specification

**Create**
- `docs/superpowers/specs/YYYY-MM-DD-autonomous-quality-coordination-design.md` using the actual completion date if it differs from this investigation date.

**Capabilities**
- Superpowers design/spec discipline
- `integration-audit`
- relevant project skills based on selected architecture

- [x] Specify exact modules/interfaces/state schema and which existing QCP types are reused. (`services/quality_coordination.py`, 3-table SQLite schema, `QualityFinding`/`QualityReport` reused unmodified.)
- [x] Specify stable automation identity and migration behavior. (External `derive_automation_key()`, diverging from I1's literal recommendation with reason — no scanner files modified; two named fallbacks for rules 4/14.)
- [x] Specify branch/main observation semantics and active-work suppression precedence. (I3 §11 precedence reused exactly; real anonymous-GitHub-API data sources specified for the first time.)
- [x] Specify local advisory tier only if I3 proved it worthwhile. (I3 rejected it — N/A, reason stated.)
- [x] Specify reporting surfaces per finding class. (One surface: the persisted series; SARIF/issue stay candidates per I10.)
- [x] Specify credential/token/event architecture and minimum permissions if any write lane survived I10. (None survived — N/A, forward-referenced to I4/I9 for a future decision.)
- [x] Specify protected paths/domains and self-modification prevention. (Structural: one write path, no repo-file writes exist in the module at all.)
- [x] Specify deterministic fixer registry/allowlist if any candidate survived I7. (I7's one candidate stays unregistered/unactivated — forward-referenced.)
- [x] Specify state retention, idempotence, retry/recovery, and outage behavior. (Unbounded retention; content-fingerprint idempotence, not commit-SHA — caught and fixed a real container/git-binary gap during self-review; transactional writes; graceful degradation on GitHub outage.)
- [x] Specify staged activation and rollback/kill switch for automation itself. (Single `quality_coordination.enabled` config flag; no further stage activated by this spec.)
- [x] Self-review for ambiguity, contradictions, placeholders, and scope. (§12 — two real errors caught and fixed during self-review, recorded rather than smoothed over.)
- [x] Commit: `docs: specify autonomous quality coordination`.

**Acceptance**
An implementation agent can build the selected architecture without inventing policy decisions.

---

## I12 — Produce the production implementation plan

**Create**
- `docs/superpowers/plans/YYYY-MM-DD-autonomous-quality-coordination.md`

**Required skill**
- Superpowers `writing-plans`

- [x] Re-ground current HEAD/active branches again; implementation may begin days after I10. (Fetched/checked before starting; still synced with `main`, no open PRs.)
- [x] Translate the approved production spec into bite-sized TDD tasks with exact files/interfaces/tests/commands/commit boundaries. (9 tasks, real complete code throughout, no placeholders.)
- [x] Stage rollout in this order unless the selected architecture proves a safer/simpler sequence: local/dry-run state → branch/main reporting → persistent integrated-state observation → external reporting/SARIF → issue escalation → deterministic draft PR creation. (Only the first two stages are built — SARIF/issue/draft-PR are correctly out of scope since I10/I11 never activated them; stated explicitly in the plan's Self-Review rather than silently omitted.)
- [x] Auto-merge is excluded; enabling it requires a later explicit design decision. (No write lane exists anywhere in this plan at all.)
- [x] Every privilege increase has its own task and rollback condition rather than being bundled into initial scaffolding. (N/A beyond Task 6's single `enabled` flag — no privilege increase exists in a report-only plan; the kill switch is that one flag.)
- [x] Include deliberate fault-injection proofs for identity, PR secret isolation, stale SHA, self-modification protection, duplicate retry, suppression, and non-resolution by claims. (Task 8: identity/self-modification/suppression/non-resolution proven directly; PR-secret-isolation/stale-SHA/duplicate-retry marked N/A with reason — no write lane exists to prove those against, I9's write_gate.py prototype already proved them for the design that would need them.)
- [x] Include Woodpecker required-context/branch-protection updates only if the selected architecture requires them and only after current CI state is read. (Read current `.woodpecker/*.yml` state first — confirmed none needed, module runs in-process not in CI.)
- [x] Include docs/rules/orchestrator updates without duplicating existing project policy. (Task 9 — one line each in CLAUDE.md and quality-capabilities.md, pointing at the real routes rather than restating design detail.)
- [x] Self-review the plan against the production spec for full coverage and no placeholders. (Self-Review section — caught and fixed two real bugs during drafting: a dotted-scope-vs-file-path suppression-matching bug, and a bare in-memory scheduler global repeating this repo's own already-fixed cold-start-reload bug class.)
- [x] Commit: `docs: plan autonomous quality coordination implementation`.

**Acceptance**
The production plan is executable by a fresh agent and grants authority incrementally rather than all at once.

---

## I13 — Final investigation verification, review, and handoff

**Capabilities**
- `integration-audit`
- `requesting-code-review`
- `final-verification`
- `verification-before-completion`
- `/checkpoint`
- `session-handoff`

- [x] Re-run the investigation evidence checklist and confirm every open research question has an answer or an explicitly bounded unknown. (All 8 design-spec §14 questions, all 11 §15 outputs, all 10 evidence-rule completion bullets — verified doc §1.)
- [x] Run integration audit over the investigation artifacts/prototype and ensure no production runtime/CI write behavior was accidentally enabled. (Zero touches to `data/`/`.env`/`config/`/`.woodpecker/`/`.github/`/`main.py`/`services/` across the whole branch — verified doc §2.)
- [x] Review active QCP baseline debt and ensure the investigation did not hide a new real error by baseline edits. (`baseline.json` untouched; live re-run shows 0 new/193 existing/0 resolved — verified doc §3.)
- [x] Run final verification proportional to changed code; if the prototype modified runnable code, include safe fault injection and the repo's complete final matrix per `final-verification`. (Every prior task's push already independently CI-verified 5/5 green; I8/I9's mutation-injection proofs already satisfy fault injection for the only runnable prototype code — verified doc §4.)
- [x] Push and inspect actual Woodpecker statuses for the final investigation commit/PR.
- [x] Perform a final fresh-eyes review of credential assumptions, event filters, active-work handling, stable identity, protected paths, outage semantics, and rollout gates. (Cross-checked across I1/I3/I4/I6/I7/I9/I10/I11/I12 — verified doc §5, no drift found.)
- [x] Sync ROADMAP/status/appropriate capability router only where repo conventions require and no parallel branch owns those files. (ROADMAP.md gains one P4 entry; `.claude/rules/quality-capabilities.md`'s router entry updated to complete; `static/status.html` deliberately left untouched, matching the confirmed precedent of the comparable prior investigation — verified doc §6.)
- [x] Merge the **investigation** PR only after green CI and review. This does not activate the future autonomous system. (PR #15, merged `79ea790`; all 5 required PR contexts and all 5 required post-merge push contexts green. No write lane, credential, or autonomous system activated by the merge — confirmed by the integration audit above.)
- [x] Use `session-handoff` to leave the chosen production spec/plan and exact next implementation step reconstructable. (Handoff delivered: next unit is Task 1 of `docs/superpowers/plans/2026-08-26-autonomous-quality-coordination.md`, no bespoke orchestrator required.)

**Acceptance**
The repository contains a validated research record, architecture decision, production spec, and production implementation plan, while autonomous write/remediation authority remains disabled until that separate implementation initiative begins.
