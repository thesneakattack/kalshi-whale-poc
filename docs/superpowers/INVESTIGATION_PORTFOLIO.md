# Investigation Portfolio

Coordination index for concurrent Superpowers-driven investigations/initiatives
in this repository. **This is a thin index, not a research report.** It does
not replace any investigation's own plan/spec/research artifacts, and it is
not an execution authority — an investigation's own plan remains
authoritative for its internal execution.

Evidence baseline for this document: `main` at `09a6abd` (2026-08-26),
`chore/autonomous-quality-coordination-investigation` at `a6a31e5`,
`git worktree list`, `gh pr list --state all`, and direct inspection of
`docs/superpowers/{plans,specs,research}/**` on both. Reconstructed
read-only; no active branch/worktree was modified to produce it.

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
  signal in this repo.** Verified directly: `docs/superpowers/plans/2026-08-24-quality-control-plane.md`
  (180 items) and both Kalshi Integration phase plans (138 + 87 items) show
  **0** checked `- [x]` boxes despite being fully delivered and merged
  (QCP is cited as a live, in-production capability throughout `CLAUDE.md`;
  Phase A/C merged via PR #3 and PR #9). This repo's numbered-task
  orchestrators track progress through git commits/actual artifacts, not by
  editing the plan file's checkboxes after the fact ("reconstruct progress,
  don't maintain a ledger" — each orchestrator skill's own stated
  convention). **Use git history and research-artifact presence as the
  completion signal, not checkbox counts.**
- **Branch presence ≠ active.** `chore/realtime-data-plane-investigation`
  exists locally and on `origin`, matches between them, but is a stale
  already-merged branch (merge-base is 30 commits behind current `main`,
  0 ahead) — its content shipped via **a different branch name**
  (`chore/realtime-dp-investigation`, PR #10 then PR #12) and it was never
  deleted post-merge. Treat it as historical, not active.
- **Worktree presence = active**, with higher confidence than a bare
  branch: `.claude/worktrees/aqc-investigation` (branch
  `chore/autonomous-quality-coordination-investigation`) is the one
  currently-live worktree besides the primary checkout.

## 3. Current investigation registry

| Name | Status | Branch / worktree | Canonical plan | Confidence |
|---|---|---|---|---|
| Quality Control Plane (QCP) | COMPLETED — MERGED | (folded into `main`) | `docs/superpowers/plans/2026-08-24-quality-control-plane.md` | High |
| Kalshi Integration Phase A (boundary) | COMPLETED — MERGED (PR #3) | (folded into `main`) | `docs/superpowers/plans/2026-08-24-kalshi-integration-phase-a.md` | High |
| Kalshi Integration Phase C (facade-free boundary) | COMPLETED — MERGED (PR #9) | (folded into `main`) | `docs/superpowers/plans/2026-08-24-kalshi-integration-phase-c.md` | High |
| Frontend Modularization — design | COMPLETED — MERGED (PR #11) | (folded into `main`) | `docs/superpowers/specs/2026-08-25-frontend-modularization-design.md` | High |
| Frontend Modularization — implementation | QUEUED / UNKNOWN progress | none found | `docs/superpowers/plans/2026-08-25-frontend-modularization.md` via `.claude/skills/frontend-modularization-task/SKILL.md` | **Low — needs confirmation** (see §12) |
| Realtime Kalshi Data-Plane Investigation (research+root cause) | COMPLETED — MERGED (PR #10, PR #12) | (folded into `main`) | `docs/superpowers/plans/2026-08-25-realtime-data-plane-investigation.md` | High |
| Realtime Kalshi Data-Plane Remediation (implementation) | QUEUED / READY, not started | none — plan specifies a fresh `feat/realtime-data-plane-remediation` | `docs/superpowers/plans/2026-08-25-realtime-data-plane-remediation.md` | High |
| Autonomous Quality Coordination Investigation ("P1") | ACTIVE — DESIGN | `chore/autonomous-quality-coordination-investigation` / `.claude/worktrees/aqc-investigation` | `docs/superpowers/plans/2026-08-25-autonomous-quality-coordination-investigation.md` | High |
| Claude/AI Control-Plane Bloat Investigation ("P2") | QUEUED — not yet created | none | none exists | High (absence confirmed by repo-wide grep) |
| Autonomous Assignment Execution Investigation ("P3") | QUEUED — not yet created | none | none exists | High (absence confirmed by repo-wide grep) |
| CI fast-path for docs-only pushes | COMPLETED — MERGED (PR #13) | (folded into `main`) | n/a (single-PR perf fix, not a numbered investigation) | High |
| Standing production-goal directive | COMPLETED — MERGED (PR #14) | (folded into `main`) | n/a (policy doc, `CLAUDE.md`) | High |

## 4. P1 → P2 → P3 dependency chain

These three labels are **portfolio ordering labels only** (per the
coordination task that produced this document) — not names used inside any
plan file itself.

- **P1 = Autonomous Quality Coordination Investigation.** Real, active,
  substantially advanced. Branch/worktree above. Its own numbered plan
  (`I0`–`I10`) audits QCP finding identity, coordination cadence,
  suppression strategies, credential/event topology, reporting surfaces,
  and deterministic-remediation candidates, then a no-write prototype, a
  fault-injection pass, and an architecture decision.
  - `6526eee` — "docs: decide autonomous quality coordination architecture"
    (I10). Provisional winner: **Candidate D, report-only** — no GitHub
    write authority, no new credentials, a persisted observation series
    only. An **independent, isolated adversarial-review subagent** was
    already dispatched against the decision and produced 9 objections, 7
    incorporated.
  - **This branch is under active concurrent development right now** — it
    advanced past `a6a31e5` to `f38a3c5` ("docs: specify autonomous quality
    coordination", I11) while this portfolio was being written, confirmed
    as a clean fast-forward (`a6a31e5` is an ancestor of `f38a3c5`), not a
    rewrite. I11 added a **production design spec**
    (`docs/superpowers/specs/2026-08-26-autonomous-quality-coordination-design.md`)
    for I10's chosen architecture (`services/quality_coordination.py`: a
    3-table SQLite schema, `automation_key` computed externally per I1,
    I3's suppression precedence reused). This is direct, live evidence of
    the "grandfathered active investigation" case this document exists to
    coordinate around — untouched by this coordination task.
  - Outstanding per its own evidence rule's completion checklist
    (`.claude/rules/autonomous-quality-coordination-evidence.md`,
    "Investigation completion rule"): a design spec now exists (I11), but
    **a separate numbered production *implementation plan* (the
    `docs/superpowers/plans/...` counterpart, analogous to how the
    realtime investigation split into investigation-plan →
    remediation-plan) does not yet exist as of `f38a3c5`.** Re-verify at
    pickup time — this branch is moving.
  - Branch is not merged, and is far enough ahead of `origin` that pushing
    is this initiative's own next step, not this document's concern.
- **P2 = Claude/AI Control-Plane Bloat Investigation.** **No plan, spec,
  research file, skill, rule, or branch exists anywhere in this repository**
  (confirmed by a full-repo `git grep` across `main`, the
  `chore/autonomous-quality-coordination-investigation` branch, and
  untracked files). The closest existing evidence toward its eventual scope
  is `.claude/rules/tooling-plugins.md`'s "Known unresolved" section (the
  still-live GitNexus `PreToolUse`/`PostToolUse` hooks spawning a node
  process per Grep/Glob/Bash call) — raw material for a future P2, not a
  P2 artifact itself.
  - **Start gate: NOT satisfied.** Per the intended dependency semantics,
    P2 should perform substantive execution only against P1's *final
    merged* result. P1 is still `ACTIVE — DESIGN`, unmerged. **P2 has not
    started — no predecessor-order violation to record.**
- **P3 = Autonomous Assignment Execution Investigation.** **Zero repository
  evidence of any kind.** Purely a forward-looking label from the
  coordination-task prompt that produced this document.
  - **Start gate: NOT satisfied** (transitively — depends on P2, which
    depends on P1). **P3 has not started — no violation to record.**

## 5. Other active investigations

Only one other initiative currently shows a live worktree or genuinely
in-flight state:

- None found beyond P1 above. `chore/realtime-data-plane-investigation`
  (stale, see §2) and `perf/ci-skip-heavy-for-docs-only` (merged, local
  branch shows `[gone]` against origin) are not active.
- The **Realtime Kalshi Data-Plane Remediation** implementation plan
  (§3) is fully written and sitting on `main`, unstarted, with no branch
  yet — "queued and ready," not "active." (Note: the user has this exact
  file open in their editor as of this writing — worth surfacing as likely
  next work, but starting it is out of scope for this coordination task.)
- The **Frontend Modularization** implementation plan is likewise on
  `main` with no active branch; its true task-completion state could not
  be established from checkbox counts alone (§2) and is recorded as
  `UNKNOWN / NEEDS CONFIRMATION` in §12 rather than guessed.

## 6. Completed predecessor investigations

Evidence these are real completed prerequisites, with their downstream
relationship:

| Investigation | Evidence it's complete | Downstream consumers |
|---|---|---|
| Quality Control Plane | `/api/quality/summary` etc. cited live throughout `CLAUDE.md`; `docs/superpowers/plans/2026-08-24-quality-control-plane.md` | P1 (its entire I0–I10 investigation audits QCP's own finding/scanner surface) |
| Kalshi Integration Phase A + C | PR #3, PR #9 merged; `services/kalshi/CHEATSHEET.md` documents the now-permanent boundary | All Kalshi-touching work (`.claude/rules/kalshi-integration-authority.md`), Realtime Data-Plane investigation and remediation |
| Realtime Kalshi Data-Plane Investigation (research phase) | PR #10, PR #12 merged; 9 research artifacts + root-cause report + design + plan now on `main` | Realtime Kalshi Data-Plane **Remediation** (its implementation plan, queued, §3) |
| Frontend Modularization (design phase) | PR #11 merged; spec on `main` | Frontend Modularization implementation plan (queued, unclear progress) |

P1's own artifacts (`docs/superpowers/research/2026-08-25-autonomous-quality-coordination-*.md`,
`...-quality-finding-identity-audit.md`, `...-quality-coordination-cadence.md`,
`...-quality-coordinator-simulation.md`, `...-autonomous-quality-threat-model.md`,
`...-autonomous-quality-architecture-decision.md`) are themselves a
predecessor-in-waiting for the not-yet-created P2 and P3 — see §4.

## 7. Branch/worktree ownership

| Branch | Worktree | Status | Notes |
|---|---|---|---|
| `main` | primary checkout | integrated truth | 9 untracked stray files present — see §12, none tracked/committed |
| `chore/autonomous-quality-coordination-investigation` | `.claude/worktrees/aqc-investigation` | ACTIVE (P1) | clean working tree; 1 unpushed trivial merge commit |
| `chore/realtime-data-plane-investigation` | none | STALE / already superseded | do not reuse; content shipped under a different branch name (see §2) |
| `perf/ci-skip-heavy-for-docs-only` | none | MERGED, remote deleted | historical |
| This coordination task | `.claude/worktrees/investigation-portfolio` | new, isolated | branch `chore/investigation-portfolio`, based on `origin/main` at `09a6abd` |

No other worktrees were found (`git worktree list` shows exactly the
primary checkout, `aqc-investigation`, and this new one).

## 8. Shared-surface contention matrix

Only investigations that are actually active or immediately queued are
scored; a fully historical/merged initiative can't contend for anything.

| Surface | P1 (aqc, active) | Realtime remediation (queued) | Frontend modularization (queued) |
|---|---|---|---|
| `services/kalshi/**`, `services/whale_stream/**`, `services/http_client.py` | no | **write** (core scope) | no |
| `services/quality/**`, `tools/quality_audit/**` | **write** (identity/reporting design) | no | no |
| `.claude/rules/quality-capabilities.md` | **conditional write** — P1's own plan gates this edit on "if not contested" (see its I0 task list) | no | no |
| `frontend/src/js/**`, `static/*.html` | no | no | **write** (core scope) |
| `config/settings.yaml` (new flags) | no | **write** (every remediation phase ships behind a flag) | unlikely |
| `static/project-manifest.json` (generated, whole-tree snapshot) | possible (if new services/tests land) | possible | possible |
| `CLAUDE.md`, `ROADMAP.md`, `static/status.html` | possible (narrative updates) | possible | possible |

Classification:
- **`services/kalshi/**` / `whale_stream` / `http_client`** — SAFE PARALLEL
  against P1 today (P1's scope is quality-coordination infrastructure, not
  the data plane); would become WRITE/WRITE CONTENTION only if a future P2
  also starts touching runtime code, which it has not.
- **`static/project-manifest.json`** — WRITE/WRITE CONTENTION risk if two
  initiatives regenerate it independently on unmerged branches (it's a
  full-tree snapshot, not an additive log) — this repo has already hit
  manifest drift/regeneration issues before (see `project-manifest-regen-gotcha`
  precedent). Low probability today since only P1 is actually active.
- **`CLAUDE.md` / `ROADMAP.md` / `static/status.html`** — WRITE/WRITE
  CONTENTION in principle, but this repo's own PR history shows these merge
  cleanly in practice because edits are additive/scoped (new bullet, new
  checked box) rather than restructuring. Treat as low-severity.
- Everything else in the matrix with only one active writer today is READ
  or SAFE PARALLEL by construction — no second active writer exists yet to
  contend with.

This matrix directly answers a question P1's own plan already asks itself
(I0: "Identify any files this initiative must not touch because another
branch currently changes them") — as of this baseline, **no other active
initiative currently writes to any surface P1 also writes to.**

## 9. Investigation start gates

- **P2** — do not begin substantive execution until P1 reaches
  `COMPLETED — MERGED` (a production implementation plan exists, per P1's
  own completion rule, and the branch is merged to `main`).
- **P3** — do not begin substantive execution until P2 reaches
  `COMPLETED — MERGED`.
- **Realtime remediation implementation** — plan already exists and is
  unblocked; its own header says land on `chore/realtime-dp-investigation`
  if still open (it is not — merged and deleted) or a fresh
  `feat/realtime-data-plane-remediation` branch. No predecessor
  investigation blocks it; it is ready whenever picked up.
- **Frontend modularization implementation** — design merged, plan exists,
  orchestrator skill exists. No known predecessor gate; actual current
  task progress is `UNKNOWN` (§12) and should be re-derived from git
  history at pickup time, not assumed from the plan file's checkboxes.

## 10. Completion / merge gates

- An investigation is `COMPLETED — MERGED` only once its branch is
  actually merged to `main` — not once research reads "done," and not
  once a branch shows all planned commits.
- P1 specifically is not `COMPLETED` until a **separate production
  implementation plan** exists (its own stated completion rule) — the
  architecture decision (I10) is necessary but not sufficient.
- A stale, already-merged branch left undeleted (like
  `chore/realtime-data-plane-investigation`) is not evidence of anything
  still open — verify via `git merge-base`/`git rev-list`, not branch
  presence.

## 11. Cumulative evidence and reconciliation rules

Later investigations must consume, not silently overwrite, earlier
findings. Use these states when a later investigation revisits an earlier
one's conclusion: `CONFIRMED`, `REFINED`, `PARTIALLY SUPERSEDED`,
`SUPERSEDED`, `CONTRADICTED`, `UNAFFECTED`. Distinguish **what kind** of
earlier output is being touched:

- Raw measurements / reproduced causal findings (e.g. the realtime
  investigation's root-cause report) — normally durable; a later
  investigation should `CONFIRM` or `REFINE`, rarely `CONTRADICT`, unless
  it re-runs the same measurement.
- Architecture/orchestration decisions (e.g. P1's I10 "Candidate D,
  report-only," or the temporary `I<n>`-task STOP convention used to run
  an investigation) — expected to be `SUPERSEDED` more readily, especially
  by a future P2 whose entire purpose is re-examining Claude/AI
  control-plane orchestration.
- The distinction matters concretely for P2: it may supersede *how* P1
  was investigated (task-numbering convention, STOP semantics, skill
  structure) while leaving P1's *empirical* findings (finding-identity
  audit, cadence measurements, credential-topology threat model) intact,
  unless it specifically re-measures them.

Actual reconciliation writing happens in the downstream investigation's own
research artifacts, not in this index.

## 12. Known conflicts / uncertainties

- **Untracked "bundle install" residue in the primary `main` checkout**
  (visible in `git status`): `.claude/rules/autonomous-quality-coordination-evidence.md`,
  `.claude/skills/autonomous-quality-coordination-investigation/`,
  `BUNDLE_README.md`, `INSTALL_AUTONOMOUS_QUALITY_COORDINATION.md`,
  `PACKAGE_MANIFEST.json`, `START_AUTONOMOUS_QUALITY_COORDINATION.md`, and
  untracked copies of the P1 plan/spec/research docs. Diffed directly
  against the committed version on `chore/autonomous-quality-coordination-investigation`:
  content-identical except the untracked copy is an **earlier, unfinished**
  snapshot (several task checkboxes still `[ ]` where the committed branch
  version now shows `[x]`). This is leftover installation scaffolding from
  bootstrapping P1 that was never committed and never cleaned up — **not**
  part of any active initiative's tracked state. Left untouched by this
  task per the non-interference mandate; whoever next works in the primary
  checkout should decide whether to discard it (it's fully superseded) or
  investigate further.
  - Separately, `BUNDLE_README.md`'s actual prose describes the
    **realtime** data-plane investigation bundle, not the autonomous-quality
    one, while sitting alongside the autonomous-quality bundle's other
    files and manifest (`PACKAGE_MANIFEST.json`'s `package` field says
    `kalshi-autonomous-quality-coordination-investigation`). Two different
    bundle installs appear to have left overlapping untracked residue in
    the same checkout. Recorded as `UNKNOWN / NEEDS CONFIRMATION` — not
    reconciled here.
- **Frontend Modularization implementation task progress** — the plan file
  shows 5 of 54 boxes checked, but §2 already established checkbox counts
  are not reliable in this repo, and no branch/worktree exists to check git
  history against. Recorded as `UNKNOWN / NEEDS CONFIRMATION` rather than
  guessed as either "5 tasks done" or "not started."
- **P1's exact remaining scope** — the completion rule requires "a separate
  production implementation plan"; whether that's meant as one more
  numbered task inside the existing plan (an `I11`) or a wholly new
  plan/spec pair analogous to the realtime investigation's
  investigation-plan → remediation-plan split could not be determined from
  current artifacts alone. `UNKNOWN / NEEDS CONFIRMATION`.
- **No detected predecessor-gate violations.** Neither P2 nor P3 has any
  repository artifact, so neither could have started before its gate —
  this is a clean state, not a gap in this audit.

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
