# Kanban Sync — Milestones and Sub-Issues for Plan-Tracked Issues — Design

**Repository:** `thesneakattack/kalshi-whale-poc`
**Date:** 2026-08-27
**Status:** Brainstormed and approved in chat. This document is the spec;
implementation follows via a separate plan, not this document itself.

---

## 1. Purpose

`tools/kanban_sync`'s "plan" source (`sources_plan.py`) tracks one
GitHub issue per numbered plan doc (`docs/superpowers/plans/*.md`),
covering the whole plan as a single unit — deliberately: the original
design (`docs/superpowers/specs/2026-08-26-kanban-board-sync-design.md`
§5) proved a plan doc's own checkboxes are not a reliable per-task
signal in this repo, so per-task completion was never attempted.

Two real gaps close here, both scoped to plan-tracked issues only (never
worktree/track/roadmap):

1. **No progress visibility inside one plan's own execution.** A
   multi-task plan (e.g. the 12-task kanban-sync board-lifecycle work
   this session, or the 4-task backend-services-modularization work)
   shows as one opaque issue with no sense of how far along it is until
   the whole thing finishes.
2. **No stable grouping above the single-issue level.** Nothing today
   answers "which concrete initiative is this work part of" the way a
   milestone's own due-date/progress-bar view does.

## 2. Non-goals

- Does not touch worktree, track, or roadmap sources at all.
- Does not attempt to auto-detect a task's completion from git log
  pattern-matching. Investigated and rejected — see §4.3.
- Does not attempt to parse every historical task-heading convention this
  repo's plan docs use. Investigated and found three distinct conventions
  in real use (§3) — only the canonical one is supported; others get a
  milestone but no sub-issue decomposition, not a best-effort guess.
- Does not change `sources_plan.py`'s own judgment-classification model
  (done/in-progress/not-started) or its existing close-on-done behavior
  (`docs/superpowers/plans/... - fix: close a plan/roadmap-tracking issue
  when the source it tracks finishes`, already shipped).
- Does not attempt real-time (every-sync-run) reconciliation of
  milestones/sub-issues the way labels/Status are reconciled every run.
  This is a one-time creation action per plan — see §4.2 for why.
- Does not build anything for tracks despite tracks also being real,
  multi-step, sometimes-long-running investigations — a track isn't
  decomposed into a task list with stable headings the way a numbered
  plan is, so there's no equivalent parse target.

## 3. Prior art and constraints this design must respect

- `docs/superpowers/specs/2026-08-26-kanban-board-sync-design.md` §5's
  finding that plan-doc checkboxes are unreliable — this design doesn't
  reopen that question, it sidesteps it entirely by not trying to detect
  task completion from the plan doc's own text at all (§4.3).
- **Three distinct task-heading conventions found in real use, confirmed
  by grepping actual plan docs, not assumed:**
  - `### Task N: <title>` (`docs/superpowers/plans/2026-08-27-backend-services-modularization.md`)
    — `writing-plans`' own official template (`### Task N: [Component Name]`).
    This is the canonical, going-forward convention.
  - `## Task N: <title>` (`docs/superpowers/plans/2026-08-26-autonomous-quality-coordination.md`)
    — one heading level shallower, an older/manually-written variant.
  - `## T1a — <title>` (`docs/superpowers/plans/2026-08-25-frontend-modularization.md`)
    — a completely different PR-group/lettered-subtask scheme for a large
    multi-PR initiative, no "Task N" text at all.

  Only the first (canonical `### Task N:`) is parsed. This is a
  deliberate scope decision (§2), not an oversight — see §4.1.
- **`gh` CLI (2.97.0) mechanics, confirmed live, not assumed:**
  - Milestones: no `gh milestone` subcommand exists, but `gh api -X POST
    repos/<repo>/milestones -f title=<name>` creates one (confirmed
    live: returns a `number`, `title`, `open_issues`/`closed_issues`
    counts). `gh issue create --milestone <name>` and `gh issue edit
    --milestone <name>`/`--remove-milestone` assign/unassign by title,
    not by number — no separate lookup call needed at assignment time.
  - Sub-issues: `gh issue create --parent <number>` and `gh issue edit
    --parent <number>`/`--remove-parent` are native, first-class flags —
    no raw GraphQL needed. Confirmed live: a parent's `gh issue view
    --json subIssuesSummary` returns `{"completed": N, "total": M,
    "percentCompleted": P}`, live-updating (confirmed with a real
    close-then-requery — note a few seconds' propagation delay observed
    once, not always; not something a same-run close-then-check should
    assume is instant). A child's `gh issue view --json parent` returns
    the parent's number/state/title/url directly.
  - **Confirmed live: GitHub does NOT auto-close a parent when all its
    sub-issues close.** `state` stayed `"OPEN"` with
    `subIssuesSummary: {completed: 1, total: 1}}`. This needs new
    `kanban_sync` logic (§4.4), not something the platform gives for
    free.
  - This project's own "Auto-add sub-issues to project" workflow is
    already enabled (confirmed via GraphQL introspection during the
    Status-field design work) — a sub-issue created under a parent
    already on the Project board gets added automatically. No new
    `kanban_sync` code needed for board membership of sub-issues.
- `tools/kanban_sync/sync.py`'s existing `SyncGithubClient` Protocol and
  `sync_pass_one`/`sync_pass_two` structure — new capabilities extend
  this, following the same pattern every prior addition this session used
  (a new `GithubClient` method + a new orchestration point in `sync.py`,
  TDD, `FakeGithubClient` extended in tests).
- `labels.py`'s `SYNC_MARKER_KIND_PLAN` and the marker-identity model
  (`markers.py`) — task sub-issues need their own stable identity distinct
  from the parent's, since a plan's task count/titles are fixed at
  approval time and don't change (§4.1 covers key derivation).

## 4. Architecture

### 4.1 Scope: canonical `### Task N:` plans only

A new parser (in `sources_plan.py` or a small sibling module) matches
`^### Task (\d+):\s*(.+)$` against a plan doc's text. If zero matches,
the plan gets a milestone (§4.2) but no sub-issues — not a fallback
attempt at the other two conventions. This is checked once, not on every
sync run (§4.2).

Each matched task becomes a stable identity `(kind="plan-task",
key=f"{plan_filename}:{task_number}")`, mirroring the existing marker
model. `task_number` is the literal number in the heading text (`Task 1`
→ `1`), not a positional index — stable even if a task's title changes
between plan-doc revisions, since the heading is re-grepped each time
this one-time action runs (though in practice it only ever runs once per
plan, per §4.2).

### 4.2 Trigger: one-time creation, not ongoing reconciliation

Sub-issues and the milestone are created **once**, the first time a
plan's classification (from the existing `kanban-board-sync` skill
judgment step) is `not-started` or `in-progress` **and** no sub-issues
already exist under that plan's parent issue (checked via `gh issue view
--json subIssuesSummary` — `total == 0` means never decomposed).
Subsequent sync runs skip a plan that already has sub-issues, entirely.

This is deliberately not folded into `sync_pass_one`'s per-item loop
(unlike labels/Status, which reconcile every run): a plan's task list is
fixed once the plan is approved — there's no ongoing drift to reconcile
against the way a `status_label` or Project Status column can drift.
Treating it as a one-time action also avoids a real risk a
reconcile-every-run design would have: re-running task decomposition
against a plan doc that's been edited after approval (e.g. this design
doc's own plan, once written, shouldn't spawn new sub-issues just because
someone tweaks task wording later) would be surprising and isn't what
"the plan was approved for these tasks" is supposed to mean.

Concretely, this is a new function callable from `__main__.py` (a new
subcommand, e.g. `python -m tools.kanban_sync decompose-plan --plan
<filename>`) or a step folded into the existing `kanban-board-sync`
skill's own classification flow (§5's Step 4/5 already does judgment work
for each plan candidate) — implementation plan decides the exact
call-site, not this spec.

### 4.3 Sub-issue closing: explicit, not auto-detected

**Rejected: parsing git log for a task-completion signal.** Investigated
and rejected for two concrete reasons, not a vague preference: (1) this
repo's own commit-message convention for referencing "which task" isn't
uniform — some commits say "Task N of
`docs/superpowers/plans/<file>`" explicitly (the backend-services-
modularization work), others (today's own kanban-sync board-lifecycle
work, executed via native Plan Mode rather than `writing-plans`)
reference a Plan-Mode-local file instead, not the repo's own
`docs/superpowers/plans/` path at all; (2) even where the convention is
followed, a task can span more than one commit, or a commit can bundle
unrelated cleanup, making "did this task's commit land" a fuzzy match at
best. Building a heuristic parser for an already-inconsistent signal
would just move the reliability problem, not solve it — the same
reasoning that ruled out branch-name-to-plan correlation for
`phase:implementing`/`verification` (worktree-only) applies here.

**Instead: closing a task sub-issue is an explicit action**, the same
natural checkpoint already exercised 12 times this session — right after
a task's own commit lands, whoever executed it (human or Claude) closes
that task's sub-issue, the same way GitHub issues are ordinarily used
(closed by the person who did the work, not inferred by a bot watching
history). `kanban_sync` does not attempt to do this itself.

### 4.4 Parent auto-close: new mechanical logic

Confirmed live (§3): GitHub never auto-closes a parent when its
sub-issues all close. New logic, folded into the existing plan-source
reconciliation path in `sync_pass_one` (or a small adjacent check,
implementation plan decides): for a plan-tracked issue that has
sub-issues (`subIssuesSummary.total > 0`), if `completed == total` and
the issue is still open, close it — reusing the exact close-and-report
shape every other close path in `sync.py` already uses. This is the one
piece of this design that *does* belong in ongoing reconciliation (unlike
creation, §4.2), since "did the last sub-issue just close" is exactly the
kind of drift-over-time signal the rest of `sync_pass_one` already
handles.

### 4.5 Milestone and depends-on chaining

One milestone per canonical plan, title = the plan filename (matching the
existing plan-tracked issue's own `key`). Created alongside the
sub-issues in the same one-time action (§4.2): `gh api -X POST
repos/<repo>/milestones -f title=<filename>`, then the parent issue and
every task sub-issue get `--milestone <filename>` (create-time flag,
avoiding a separate edit call per issue).

Consecutive task sub-issues get `depends-on:#N` labels chaining
`Task 2 → depends-on Task 1`, `Task 3 → depends-on Task 2`, etc. —
reusing the exact mechanism tracks already use (`sync_pass_two`), not a
new dependency system. This directly encodes the sequential assumption
every numbered plan in this repo already makes ("Task 2" presumes
"Task 1" landed) as a real, visible constraint on the board rather than
leaving it implicit in the doc's own prose.

## 5. Verification approach

- Live smoke test (read-only + one real, disposable test issue pair,
  cleaned up immediately) already run during this design's own research
  — confirmed: `--parent` creation, `subIssuesSummary` live-updating,
  parent-does-not-auto-close, milestone creation/assignment by title, all
  via plain `gh` CLI flags with no raw GraphQL needed.
- Unit tests (TDD, mirroring every prior addition this session): the
  `### Task N:` parser against real fixture text drawn from the three
  known conventions (asserts extraction from the canonical one, asserts
  zero matches — not a crash — against the other two); the one-time
  "already decomposed, skip" check; the parent auto-close condition
  (`completed == total > 0` on an open issue); dry-run makes zero
  mutating calls for all of the above, matching every other capability in
  this codebase.
- A real, disposable end-to-end test against a genuinely new plan doc
  (not this repo's own real plan docs) once implemented, before trusting
  it against a real numbered plan.

## 6. Explicitly deferred

- Auto-decomposing the two non-canonical task-heading conventions.
- Any reconciliation of milestone/sub-issue *structure* after initial
  creation (e.g. a plan doc edited post-approval to add a 13th task) —
  out of scope; the one-time trigger (§4.2) means this would need a
  deliberate re-run, not automatic detection.
- Extending this to tracks, despite tracks also being multi-step —
  tracks have no equivalent fixed, headed task list to parse (§2).
- Any attempt to detect task completion from git log or commit message
  conventions (§4.3) — rejected, not deferred as "maybe later"; the
  reasoning (inconsistent conventions, fuzzy task-to-commit mapping)
  wouldn't be resolved by more engineering effort, only by this repo
  adopting one single, disciplined commit-message convention for task
  references, which is a process change, not a `kanban_sync` feature.
- **Sub-issue marker identity.** The implementation (`plan_tasks.py`,
  Task 4) gives each created sub-issue a plain `## Context\nPart of
  \`docs/superpowers/plans/<file>\`.` body with no `autotrade-sync:`
  marker, rather than the stable `(kind="plan-task",
  key=f"{plan_filename}:{task_number}")` identity this section originally
  specified. Found in the final whole-branch review (2026-08-27), after
  implementation: since sub-issue closing is explicit (§4.3, never
  reconciled after creation) and `markers.py`'s `_MARKER_RE` doesn't
  accept a hyphenated kind like `plan-task` without a regex change, a
  marker would have added parsing surface with no reconciliation ever
  consuming it. Accepted as a deliberate simplification, not fixed -
  cheapest available correctness for `close_completed_plan_parents`
  (§4.4) already comes from filtering on the *parent's* `plan` marker
  kind instead (fixed the same review round - see git history around
  2026-08-27 for the exact commit), which sub-issues, having no marker at
  all, are naturally excluded from.
- **Partial-decomposition recovery.** `decompose_plan`'s one-time gate is
  `subIssuesSummary.total > 0`, not `total >= len(tasks)`. If
  `create_issue` fails partway through a multi-task decomposition (rate
  limit, transient 5xx), the plan is left with some-but-not-all sub-issues
  and no CLI path to finish the rest - re-running `decompose-plan` sees
  `total > 0` and reports `"skipped": "already decomposed"` even though
  it isn't. Found in the final whole-branch review (2026-08-27); not
  fixed - no observed partial failure yet, and completing it correctly
  needs either a `--force`/resume mode or `total >= len(tasks)` gating,
  either of which is a deliberate follow-up, not a one-line fix. If this
  is ever hit for real, treat it as a signal to implement resume support
  rather than a bug to patch around.
- **Auto-close vs. plan classification staleness.** `close_completed_plan_parents`
  (§4.4) can close a plan's parent issue before the plan doc has been
  reclassified `done` in the next `sync --sources plan --plan-classifications
  ...` run's classification JSON (produced by the kanban-board-sync skill's
  judgment step, §5's own gap this design never touches). When that
  happens, `sync_pass_one`'s existing mismatch-comment path (unrelated to
  this design, pre-existing) sees a closed issue with a still-open
  source and posts a comment every run until the classification catches
  up - noisy, not incorrect (the issue stays closed, never reopened).
  Found in the final whole-branch review (2026-08-27); not fixed, since
  the real fix (deduplicating that pre-existing comment path) is outside
  this design's scope. Operational mitigation: reclassify a plan to
  `done` in the classification JSON as soon as its parent auto-closes,
  per the kanban-board-sync skill.

## 7. Self-review

- **Placeholder scan**: no TBD/TODO; every open question surfaced during
  brainstorming (auto-detect vs. explicit close, reconcile-every-run vs.
  one-time, which heading conventions to support) resolved to an explicit
  decision with reasoning, not left open.
- **Internal consistency**: §4.1-§4.5 all reference the same marker/
  identity model and `SyncGithubClient` extension pattern the rest of
  `tools/kanban_sync` already uses; no new persistence mechanism, no new
  CLI framework.
- **Scope check**: deliberately narrow — plan-tracked issues only, one
  heading convention only, one-time creation only. Matches this session's
  own established pattern of shipping a well-bounded piece rather than
  trying to solve every adjacent case in the same pass.
- **Ambiguity check**: the two places a reader could reasonably ask "why
  not just detect X automatically" (task completion, §4.3; ongoing
  milestone/sub-issue reconciliation, §4.2) both have an explicit
  rejected-and-why, not a silent gap.
