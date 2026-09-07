# Investigation Portfolio

Coordination index for concurrent Superpowers-driven investigations/initiatives
in this repository. **This is a thin index, not a research report.** It does
not replace any investigation's own plan/spec/research artifacts, and it is
not an execution authority — an investigation's own plan remains
authoritative for its internal execution.

Evidence baseline for this document: `main` at `34f2633` (2026-08-26), plus
direct read-only inspection of `git branch -a`, `git worktree list`,
`gh pr list --state open`, and `docs/superpowers/{plans,specs,research}/**`.
This is a refresh of an earlier draft of this same file (originally committed
as `94aa113` on this same branch, baselined against `main@09a6abd`) — four
PRs (#15, #16, #17, #18) merged since that baseline, and two things happened
in the current session that the prior draft could not have known about (see
§4, §5). Reconstructed read-only; no active branch/worktree was modified to
produce it.

## 1. Purpose and scope

Let anyone (human or a future Claude session) answer, from one document:
what investigations/initiatives exist, which are running, which are done
but unmerged, which are merged, which are queued/blocked, who owns which
branch/worktree, and which surfaces might contend if two run at once.
Out of scope: reconciling contradictions between investigations, resolving
write contention, starting queued work, or restating any investigation's
actual findings — those stay in the investigation's own artifacts.

## 2. Interpretation rules

- **Plan existence ≠ execution authorization.** Several plans below sit on
  `main`, fully written, with **zero** implementation started.
- **A numbered plan's checkbox state is not a reliable completion
  signal in this repo.** Verified directly: `docs/archive/lane-6-observability-quality-safety/plans/2026-08-24-quality-control-plane.md` (moved there 2026-09-06, planning-lanes migration)
  (180 items) and both Kalshi Integration phase plans (138 + 87 items) show
  **0** checked `- [x]` boxes despite being fully delivered and merged
  (QCP is cited as a live, in-production capability throughout `CLAUDE.md`;
  Phase A/C merged via PR #3 and PR #9). Conversely, the Autonomous Quality
  Coordination investigation plan shows **100% checked** (`0` unchecked as
  of the `docs: close out the investigation plan's checklist` commit,
  `160942c`/PR #18) — checkbox state in this repo tracks retroactively
  reconciled paperwork, not live execution progress, in either direction.
  This repo's numbered-task orchestrators track progress through git
  commits/actual artifacts, not by editing the plan file's checkboxes as
  work happens ("reconstruct progress, don't maintain a ledger" — each
  orchestrator skill's own stated convention). **Use git history and
  research-artifact/code presence as the completion signal, not checkbox
  counts.**
- **Branch presence ≠ active.** `chore/realtime-data-plane-investigation`
  exists locally and on `origin`, matches between them, but is a stale
  already-merged branch (merge-base is far behind current `main`, 0 ahead)
  — its content shipped via **a different branch name**
  (`chore/realtime-dp-investigation`, PR #10 then PR #12) and it was never
  deleted post-merge. Treat it as historical, not active. Likewise
  `chore/autonomous-quality-coordination-investigation` (local only) is
  fully merged (PR #15) and stale — safe to delete, not evidence of
  anything still open.
- **Worktree presence is a stronger signal than a bare branch, but "locked"
  in `git worktree list` is the actual live-ownership signal**, not worktree
  presence alone. As of this baseline:
  - `.claude/worktrees/agent-a77b293d25b099924` (branch
    `feat/realtime-data-plane-remediation`) — **locked**, genuinely active
    (this session's own background agent; 2 commits in, phase P0 in
    progress as of this baseline).
  - `.claude/worktrees/docs+production-doctrine-reconciliation` (branch
    `docs/production-doctrine-reconciliation`) — **locked**, but that
    branch's content already merged to `main` via PR #17
    (`3720ce2`). A locked worktree on an already-merged branch is leftover
    cleanup debt, not active work — treat as historical, not a live writer.
  - `.claude/worktrees/aqc-investigation` (branch
    `docs/close-out-investigation-checklist`) — **not locked**, and that
    branch is also fully merged (PR #18, `34f2633`). Historical.
  - `.claude/worktrees/investigation-portfolio` (this document's own
    branch, `chore/investigation-portfolio`) — **not locked**, unmerged,
    single prior commit (`94aa113`) before this refresh.

## 3. Current investigation registry

| Name | Status | Branch / worktree | Canonical plan | Confidence |
|---|---|---|---|---|
| Quality Control Plane (QCP) | COMPLETED — MERGED | (folded into `main`) | `docs/archive/lane-6-observability-quality-safety/plans/2026-08-24-quality-control-plane.md` (moved there 2026-09-06, planning-lanes migration) | High |
| Kalshi Integration Phase A (boundary) | COMPLETED — MERGED (PR #3) | (folded into `main`) | `docs/archive/lane-1-kalshi-ingestion/plans/2026-08-24-kalshi-integration-phase-a.md` | High |
| Kalshi Integration Phase C (facade-free boundary) | COMPLETED — MERGED (PR #9) | (folded into `main`) | `docs/archive/lane-1-kalshi-ingestion/plans/2026-08-24-kalshi-integration-phase-c.md` | High |
| Frontend Modularization — design | COMPLETED — MERGED (PR #11) | (folded into `main`) | `docs/archive/lane-8-frontend-dashboard/specs/2026-08-25-frontend-modularization-design.md` (moved there 2026-09-06, planning-lanes migration) | High |
| Frontend Modularization — implementation | QUEUED — confirmed not started | none | `docs/archive/lane-8-frontend-dashboard/plans/2026-08-25-frontend-modularization.md` (moved there 2026-09-06, planning-lanes migration) via `.claude/skills/frontend-modularization-task/SKILL.md` | High (direct file check: `frontend/src/js/` is still the pre-migration flat layout — `advisory-calibration.js`, `config-panel.js`, etc. — no `core/`/`panels/`/`legacy/` split the plan's T1c calls for) |
| Realtime Kalshi Data-Plane Investigation (research+root cause) | COMPLETED — MERGED (PR #10, PR #12) | (folded into `main`) | `docs/archive/lane-1-kalshi-ingestion/plans/2026-08-25-realtime-data-plane-investigation.md` | High |
| Realtime Kalshi Data-Plane Remediation (implementation) | **ACTIVE — IMPLEMENTATION** | `feat/realtime-data-plane-remediation` / `.claude/worktrees/agent-a77b293d25b099924` (locked) | `docs/archive/lane-1-kalshi-ingestion/plans/2026-08-25-realtime-data-plane-remediation.md` (7 phases, P0–P6) | High — 2 commits observed (`36ca1f9` event-loop stall watchdog, `dcc9816` durable candidate-ledger table, both P0) |
| Autonomous Quality Coordination Investigation ("P1") | **COMPLETED — MERGED** (PR #15, checklist close-out PR #18) | (folded into `main`) | `docs/archive/lane-9-tooling-ci-process-governance/plans/2026-08-25-autonomous-quality-coordination-investigation.md` | High |
| Autonomous Quality Coordination — production implementation | QUEUED — not started; **two autonomous attempts this session, both aborted before any commit** | none (branches `chore/autonomous-quality-coordination-implementation` and a stray `worktree-agent-*` were created and deleted, zero commits on either) | `docs/archive/lane-9-tooling-ci-process-governance/plans/2026-08-26-autonomous-quality-coordination.md` (9 tasks) | High |
| Claude/AI Control-Plane Bloat Investigation ("P2") | QUEUED — not yet created | none | none exists | High (absence re-confirmed by repo-wide grep this baseline) |
| Autonomous Assignment Execution Investigation ("P3") | QUEUED — not yet created | none | none exists | High (absence re-confirmed by repo-wide grep this baseline) |
| CI fast-path for docs-only pushes | COMPLETED — MERGED (PR #13) | (folded into `main`) | n/a (single-PR perf fix, not a numbered investigation) | High |
| Standing production-goal directive | COMPLETED — MERGED (PR #14) | (folded into `main`) | n/a (policy doc, `CLAUDE.md`) | High |
| Production-readiness doctrine reconciliation | COMPLETED — MERGED (PR #17) | (folded into `main`) | n/a — a direct verification/correction pass against `CLAUDE.md`/`ROADMAP.md`, no numbered plan/spec/research doc exists for it | High |
| Status-page modularization | COMPLETED — MERGED (PR #16) | (folded into `main`) | n/a — a refactor (`static/status.html` → `docs/status-src/` fragments + `tools/build_status_page.py`), not a numbered investigation | High |

## 4. P1 → P2 → P3 dependency chain

These three labels are **portfolio ordering labels only** — not names used
inside any plan file itself.

- **P1 = Autonomous Quality Coordination Investigation.** **Now
  `COMPLETED — MERGED`**, a material change from this document's prior
  draft (which recorded it `ACTIVE — DESIGN`, unmerged). Its own numbered
  plan (`I0`–`I13`) audited QCP finding identity, coordination cadence,
  suppression strategies, credential/event topology, reporting surfaces,
  and deterministic-remediation candidates, produced a no-write prototype
  and a fault-injection pass, decided on an architecture (I10: **Candidate
  D, report-only** — no GitHub write authority, no new credentials, a
  persisted observation series only), specified it (I11), and wrote a
  9-task production implementation plan (I12,
  `docs/archive/lane-9-tooling-ci-process-governance/plans/2026-08-26-autonomous-quality-coordination.md`).
  Merged via PR #15 (`79ea790`); its checklist was retroactively closed out
  via PR #18 (`34f2633`).
  - **The production implementation plan is written but has zero
    implementation.** Confirmed directly: no `services/quality_coordination.py`
    exists anywhere in git history. Two autonomous attempts to execute it
    this session were both interrupted/aborted before any commit landed —
    recorded in §3, not evidence of any defect in the plan itself, just
    that execution hasn't actually happened yet.
- **P2 = Claude/AI Control-Plane Bloat Investigation.** **No plan, spec,
  research file, skill, rule, or branch exists anywhere in this repository**
  (re-confirmed by a full-repo `git grep` this baseline). The closest
  existing evidence toward its eventual scope is `.claude/rules/tooling-plugins.md`'s
  "Known unresolved" section (the still-live GitNexus
  `PreToolUse`/`PostToolUse` hooks spawning a node process per
  Grep/Glob/Bash call) — raw material for a future P2, not a P2 artifact
  itself.
  - **Start gate: ambiguous, but moot today.** P1's *investigation* is now
    merged, which satisfies one reasonable reading of "P2 executes only
    against P1's final merged result." But P1's own completion rule (its
    plan's I13 acceptance criterion) treats the investigation as complete
    once "the repository contains... a production implementation plan,"
    which is a lower bar than *that plan being implemented and merged*.
    Whether P2's start gate means "P1's investigation artifacts are merged"
    (satisfied) or "P1's production implementation is also merged" (not
    satisfied — implementation hasn't started) is **UNKNOWN / NEEDS
    CONFIRMATION** — not settled by any artifact found. Moot for now either
    way: **P2 has no artifacts and has not started — no predecessor-order
    violation to record.**
- **P3 = Autonomous Assignment Execution Investigation.** **Zero repository
  evidence of any kind.** Purely a forward-looking label.
  - **Start gate: NOT satisfied** (transitively — depends on P2, which
    depends on P1). **P3 has not started — no violation to record.**

## 5. Other active investigations

- **Realtime Kalshi Data-Plane Remediation (implementation)** is now the
  **only genuinely active implementation initiative** in the repository.
  Branch `feat/realtime-data-plane-remediation`, worktree
  `.claude/worktrees/agent-a77b293d25b099924` (**locked** — live), 2 commits
  observed at this baseline (P0 phase: event-loop stall watchdog, a durable
  but still-unwired candidate-ledger table). This is a change from this
  document's prior draft, which recorded this plan as "queued and ready."
  This portfolio's own non-interference rule means: **do not touch that
  worktree/branch, do not modify
  `docs/archive/lane-1-kalshi-ingestion/plans/2026-08-25-realtime-data-plane-remediation.md`,
  and do not modify any file under `services/kalshi/`,
  `services/whale_stream/`, or `services/http_client.py`** while it is
  live.
- The **Autonomous Quality Coordination production implementation** (§3,
  §4) is QUEUED, not active — two attempts this session did not produce a
  running worktree or any commit; nothing to grandfather or avoid there
  today, but the next attempt is real forward work, not a fresh idea.
- The **Frontend Modularization** implementation plan remains on `main`
  with no active branch — confirmed not started (§3), not merely unknown,
  a change from this document's prior draft.
- `chore/realtime-data-plane-investigation` (stale, see §2),
  `chore/autonomous-quality-coordination-investigation` (stale, merged, see
  §2), and `perf/ci-skip-heavy-for-docs-only` (merged) are not active.

## 6. Completed predecessor investigations

| Investigation | Evidence it's complete | Downstream consumers |
|---|---|---|
| Quality Control Plane | `/api/quality/summary` etc. cited live throughout `CLAUDE.md`; `docs/archive/lane-6-observability-quality-safety/plans/2026-08-24-quality-control-plane.md` (moved there 2026-09-06, planning-lanes migration) | P1/AQC (its entire I0–I10 investigation audited QCP's own finding/scanner surface) |
| Kalshi Integration Phase A + C | PR #3, PR #9 merged; `services/kalshi/CHEATSHEET.md` documents the now-permanent boundary | All Kalshi-touching work (`.claude/rules/kalshi-integration-authority.md`), Realtime Data-Plane investigation and remediation |
| Realtime Kalshi Data-Plane Investigation (research phase) | PR #10, PR #12 merged; 9 research artifacts + root-cause report + design + plan now on `main` | Realtime Kalshi Data-Plane **Remediation** — now **ACTIVE — IMPLEMENTATION** (§3, §5), not merely queued |
| Frontend Modularization (design phase) | PR #11 merged; spec on `main` | Frontend Modularization implementation plan (queued, confirmed not started) |
| Autonomous Quality Coordination Investigation (P1) | PR #15 + PR #18 merged; 10 research artifacts, a design spec, and a 9-task production implementation plan now on `main` | The not-yet-started AQC production implementation (§3, §4); P2 (Claude/AI Control-Plane Bloat), once created |
| Production-readiness doctrine reconciliation | PR #17 merged; `CLAUDE.md`/`ROADMAP.md` now carry verified (not assumed) facts about `shadow_trades` row count, `risk.max_daily_loss_pct`, and `auto_apply_enabled` | Any future production-risk/shadow-readiness investigation — these verified facts are its starting evidence, not something it needs to re-derive |

P1/AQC's own artifacts (`docs/superpowers/research/2026-08-25-autonomous-quality-coordination-*.md`,
`...-quality-finding-identity-audit.md`, `...-quality-coordination-cadence.md`,
`...-quality-coordinator-simulation.md`, `...-autonomous-quality-threat-model.md`,
`...-autonomous-quality-architecture-decision.md`) are themselves a
predecessor-in-waiting for the not-yet-created P2 and P3 — see §4.

## 7. Branch/worktree ownership

| Branch | Worktree | Status | Notes |
|---|---|---|---|
| `main` | primary checkout | integrated truth | clean; no untracked residue (the prior draft's "9 untracked stray files" / bundle-install scaffolding is gone — resolved since that baseline, not investigated further here) |
| `feat/realtime-data-plane-remediation` | `.claude/worktrees/agent-a77b293d25b099924` | **ACTIVE, locked** | 2 commits, P0 phase in progress; do not touch |
| `docs/production-doctrine-reconciliation` | `.claude/worktrees/docs+production-doctrine-reconciliation` | locked but **already merged** (PR #17) | leftover cleanup debt, not live work — safe to remove when convenient, not urgent |
| `docs/close-out-investigation-checklist` | `.claude/worktrees/aqc-investigation` | not locked, **already merged** (PR #18) | historical, safe to remove |
| `chore/investigation-portfolio` | `.claude/worktrees/investigation-portfolio` | this document's own branch, not locked | based on `main`, refreshed this baseline |
| `chore/autonomous-quality-coordination-investigation` | none | **already merged** (PR #15), local-only | stale, safe to delete |
| `chore/realtime-data-plane-investigation` | none | stale, superseded by `chore/realtime-dp-investigation` (already merged/deleted) | do not reuse |
| `perf/ci-skip-heavy-for-docs-only` | none | merged, remote deleted | historical |

## 8. Shared-surface contention matrix

Only investigations that are actually active or immediately queued are
scored; a fully historical/merged initiative can't contend for anything.

| Surface | Realtime remediation (**active**) | AQC production impl. (queued) | Frontend modularization (queued) |
|---|---|---|---|
| `services/kalshi/**`, `services/whale_stream/**`, `services/http_client.py` | **write** (core scope, in progress) | no | no |
| `main.py` (tick loop / scheduler section) | **write** (loop hygiene, ledger gates per its plan) | **write** (scheduler wiring, Task 6) | no |
| `services/quality_coordination.py` (new), `.claude/rules/quality-capabilities.md` (router entry) | no | **write** (core scope, not yet started) | no |
| `frontend/src/js/**`, `static/*.html` | no | no | **write** (core scope) |
| `config/settings.yaml` (new flags) | **write** (remediation phases ship behind flags) | **write** (Task 6's `enabled` flag) | unlikely |
| `static/project-manifest.json` (generated, whole-tree snapshot) | possible | possible | possible |
| `CLAUDE.md`, `ROADMAP.md`, `static/status.html` | possible (narrative updates) | possible | possible |

Classification:
- **`services/kalshi/**` / `whale_stream` / `http_client`** — SAFE PARALLEL
  today: realtime remediation is the only current writer, and no other
  active/queued initiative touches these paths.
- **`main.py`'s scheduler section** — **READ/WRITE DEPENDENCY, not yet a
  live conflict**: both realtime remediation (active now) and the AQC
  production implementation (queued, not started) expect to add scheduler
  wiring there. Since AQC implementation has not started, there is no
  current WRITE/WRITE contention — but whoever picks up AQC implementation
  next should re-diff `main.py`'s scheduler section against whatever
  realtime remediation has merged by then, since that section will have
  moved.
- **`static/project-manifest.json`** — WRITE/WRITE CONTENTION risk if two
  initiatives regenerate it independently on unmerged branches (it's a
  full-tree snapshot, not an additive log) — this repo has already hit
  manifest drift/regeneration issues before (`project-manifest-regen-gotcha`
  precedent). Low probability today since only realtime remediation is
  actually active.
- **`CLAUDE.md` / `ROADMAP.md` / `static/status.html`** — WRITE/WRITE
  CONTENTION in principle, but this repo's own PR history shows these merge
  cleanly in practice because edits are additive/scoped. Treat as
  low-severity.
- Everything else in the matrix with only one active writer today is READ
  or SAFE PARALLEL by construction.

**No other active initiative currently writes to any surface the active
realtime-remediation initiative also writes to** — this baseline's direct
answer to the question the remediation plan's own re-grounding step already
asks itself.

## 9. Investigation start gates

- **P2** — do not begin substantive execution until P1's start-gate
  ambiguity (§4) is resolved one way or the other; at minimum, P1's
  investigation is merged (satisfied), but whether P2 also needs P1's
  production implementation merged first is `UNKNOWN / NEEDS CONFIRMATION`.
- **P3** — do not begin substantive execution until P2 reaches
  `COMPLETED — MERGED`.
- **AQC production implementation** — plan exists (I12), fully specified,
  no predecessor gate beyond the investigation itself (already merged).
  Ready whenever picked up; two attempts this session did not get far
  enough to leave any state to reconcile.
- **Realtime remediation implementation** — no predecessor gate; already
  picked up and **actively running** (§3, §5) on
  `feat/realtime-data-plane-remediation`.
- **Frontend modularization implementation** — design merged, plan exists,
  orchestrator skill exists, no known predecessor gate, confirmed not
  started.

## 10. Completion / merge gates

- An investigation is `COMPLETED — MERGED` only once its branch is
  actually merged to `main` — not once research reads "done," and not
  once a branch shows all planned commits.
- P1/AQC's investigation itself now satisfies this (PR #15/#18). Its
  *production implementation* is a separate, still-unmet gate (§4).
- A stale, already-merged branch left undeleted (`chore/realtime-data-plane-investigation`,
  `chore/autonomous-quality-coordination-investigation`) is not evidence of
  anything still open — verify via `git merge-base`/`git rev-list`, not
  branch presence. A **locked worktree on an already-merged branch**
  (`docs+production-doctrine-reconciliation`, §2) is the same trap in a
  different guise — verify merge state, not lock state, before treating it
  as active.

## 11. Cumulative evidence and reconciliation rules

Later investigations must consume, not silently overwrite, earlier
findings. Use these states when a later investigation revisits an earlier
one's conclusion: `CONFIRMED`, `REFINED`, `PARTIALLY SUPERSEDED`,
`SUPERSEDED`, `CONTRADICTED`, `UNAFFECTED`. Distinguish **what kind** of
earlier output is being touched:

- Raw measurements / reproduced causal findings (e.g. the realtime
  investigation's root-cause report, or the production-doctrine
  reconciliation's verified `shadow_trades`/`max_daily_loss_pct`/
  `auto_apply_enabled` facts) — normally durable; a later investigation
  should `CONFIRM` or `REFINE`, rarely `CONTRADICT`, unless it re-runs the
  same measurement.
- Architecture/orchestration decisions (e.g. AQC's I10 "Candidate D,
  report-only," or the temporary `I<n>`-task STOP convention used to run
  an investigation) — expected to be `SUPERSEDED` more readily, especially
  by a future P2 whose entire purpose is re-examining Claude/AI
  control-plane orchestration.
- The distinction matters concretely for P2: it may supersede *how* P1/AQC
  was investigated (task-numbering convention, STOP semantics, skill
  structure) while leaving P1's *empirical* findings (finding-identity
  audit, cadence measurements, credential-topology threat model) intact,
  unless it specifically re-measures them.

Actual reconciliation writing happens in the downstream investigation's own
research artifacts, not in this index.

## 12. Known conflicts / uncertainties

- **P2's exact start-gate reading** (§4, §9) — whether "P1's final merged
  result" means the investigation (satisfied) or also its production
  implementation (not yet true) is `UNKNOWN / NEEDS CONFIRMATION`. Moot
  today since P2 has no artifacts at all.
- **`main.py` scheduler-section drift** (§8) — realtime remediation is
  actively rewriting the tick loop / scheduler area right now; whoever
  picks up the AQC production implementation next (Task 6, scheduler
  wiring) should re-diff against whatever's landed there by then rather
  than assuming the current shape.
- **Two locked worktrees on already-merged branches**
  (`docs+production-doctrine-reconciliation`) — not urgent, but worth
  cleaning up (`git worktree remove`) since "locked" otherwise reads as
  "active" at a glance (§2), which is exactly the kind of ambiguity this
  document exists to remove.
- **Frontend Modularization implementation** — now confirmed **not
  started** (direct file check), superseding this document's prior
  `UNKNOWN / NEEDS CONFIRMATION` on that point.
- **The prior draft's "untracked bundle install residue" finding** (its
  own §12) no longer reproduces — `git status` on `main` is clean at this
  baseline. Recorded as resolved, not re-investigated (out of scope here;
  if the residue's origin matters later, `git log`/session history around
  2026-08-26 01:xx–02:xx is the place to look).
- **No detected predecessor-gate violations.** Neither P2 nor P3 has any
  repository artifact, so neither could have started before its gate —
  clean state, not a gap in this audit.

## 13. How Claude should use this document

Before starting a **new** investigation: read this file → find the
canonical plan → verify its start/dependency gate (§9) → check current
branch/worktree state directly (`git branch --all`, `git worktree list`,
`gh pr list`) rather than trusting this snapshot blindly → check §8 for a
conflicting active writer → then follow the canonical plan, which remains
the execution authority.

During an investigation: this document is not a to-do list and not a
progress ledger. Do not update it after every task. Update it only at real
lifecycle transitions (queued→ready, active research→design→
implementation→verification, active→completed-unmerged→completed-merged,
blocked→ready) — see §14.

## 14. Portfolio maintenance policy

- Update at lifecycle transitions only, not per-commit or per-task.
- Adding a brand-new investigation earns one new row in §3 (and §4/§5/§6 as
  applicable) — not a restatement of its plan.
- This file intentionally has no generator, hook, or scanner. A future
  Claude/AI Control-Plane Bloat Investigation (P2, §4) is explicitly free to
  conclude this file should be generated, consolidated, relocated, or
  removed — nothing here should be treated as permanent infrastructure.
- Do not let this file grow into a second research report. If an entry
  needs more than a few lines to explain, that content belongs in the
  investigation's own artifacts, linked from here.
