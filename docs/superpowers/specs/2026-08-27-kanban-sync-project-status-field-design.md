# Kanban Sync — Project Status Field, Phase Vocabulary, and Stale-Worktree Closure — Design

**Repository:** `thesneakattack/kalshi-whale-poc`
**Date:** 2026-08-27
**Status:** Brainstormed and approved in chat, then in Plan Mode with a
written plan approved. This document is the spec; implementation follows
via the already-approved plan, not this document itself.

---

## 1. Purpose

`tools/kanban_sync` (`docs/superpowers/specs/2026-08-26-kanban-board-sync-design.md`)
reflects worktrees, `ROADMAP.md`, `active-tracks-board.md`, and numbered
plan docs onto real GitHub Issues via `status:*`/`type:*`/`phase:*` labels.
Three related gaps close here:

1. **`phase:*` labels don't match superpowers' real lifecycle vocabulary**
   (brainstorming → spec → plan → implementation → verification). The
   current values (`research-evidence`/`design-spec`/`implementation-plan`/
   `implemented`) were never grounded against that vocabulary when added
   (PR #99).
2. **A stale-issue-closure gap**: `sources_worktree.py` only ever iterates
   *currently-existing* worktrees, so a removed one never produces a
   `done=True` item — its GitHub tracking issue is never closed. Confirmed
   live: issue #98 ("Worktree: feat/kanban-sync-phase-labels") stayed open
   hours after that branch merged and its worktree was removed.
3. **The board's actual visible columns have never been driven by this
   tool at all.** Investigating (1) surfaced a hard platform constraint:
   GitHub Projects V2 cannot group a Board or Table view by the Labels
   field, full stop — confirmed against GitHub's own current documentation
   ("You cannot group by title, labels, reviewers, or linked pull
   requests") and via live GraphQL introspection of this project. Only a
   single-select or iteration *field* on the Project itself can drive
   board columns. The Project's native "Status" field (Inbox/Next/Doing/
   Waiting/Done) is that mechanism — and `tools/kanban_sync` has never
   touched it. The 33 issues currently on the board were added by a
   one-off manual/scripted run, not by any code in this repo or any
   GitHub-native automation (see §3).

## 2. Non-goals

- Does not retrofit `phase:implementing`/`phase:verification` onto
  plan-doc or track items. A worktree already maps 1:1 to one branch; a
  plan/track item does not reliably correlate to a branch by name (see
  §3's empirical evidence) — this granularity stays worktree-only.
- Does not generalize "item vanished from scan → close its issue"
  detection to any source beyond worktree.
- Does not change the depends-on two-pass mechanism (`sync_pass_two`).
- Does not add a 6th Project Status option. `status:blocked` and
  `status:ready-for-review` both map to the existing "Waiting" column
  (see §4.3) rather than growing the board's shape.
- Does not attempt to preview Project Status changes in `--dry-run`. The
  mapping is fully deterministic from `item.status_label`/`item.done`
  alone, so the outcome is knowable by inspection without a live read —
  dry-run makes zero project-related calls, matching this tool's existing
  dry-run invariant.
- Does not change how a worktree's branch gets renamed (a rename still
  reads as delete-old+create-new, matching every source's existing
  marker-identity model — a known, pre-existing limitation, not a new one
  introduced here).

## 3. Prior art and constraints this design must respect

- `docs/superpowers/specs/2026-08-26-kanban-board-sync-design.md` — the
  parent design. §7's `status:*`/`type:*` label scheme, §9's closure
  semantics ("closure follows the same direction... the reverse case — an
  issue closed on GitHub while its repo source still shows open — is not
  auto-reopened"), and §6's marker-identity model are extended, not
  replaced. §9 only ever discusses closing a *still-represented* source's
  issue (a checked-off ROADMAP item, a done track, a closed plan) — it
  never addresses a source item that vanishes from the scan entirely,
  which is exactly what a removed worktree does. This spec extends §9 to
  cover that case explicitly (§4.2 below).
- `tools/kanban_sync/labels.py`'s own module docstring currently states
  "this repo's board already supports grouping its view by Labels
  natively" — **confirmed false** this session, both against GitHub's
  current documentation and by direct GraphQL introspection of this
  project's own view configuration. This premise gets corrected in the
  same edit as the phase-label rename (§4.1).
- **The installed `github-issues-kanban` skill's own
  `assets/template-personal-todo.json` archetype**
  (`~/.claude/plugins/mcpmarket-me/skills/github-issues-kanban/`) — almost
  certainly what this exact board was scaffolded from (its title,
  "Personal Todo Board", matches the template's `title_pattern: "{name}"`)
  — already specifies the board's intended column↔label mapping
  explicitly:
  ```json
  "columns": [
    { "name": "Inbox",   "status_label": null,                 "description": "Unprocessed new items" },
    { "name": "Next",    "status_label": "status:claimable",   "description": "Triaged; ready for an agent or you" },
    { "name": "Doing",   "status_label": "status:in-progress", "description": "Active work" },
    { "name": "Waiting", "status_label": "status:blocked",     "description": "Blocked or waiting on external" },
    { "name": "Done",    "status_label": "status:done",        "description": "Closed" }
  ]
  ```
  and a declarative `"automation"` array describing exactly this
  trigger→column-move behavior. Confirmed via a directory listing that
  this skill has **zero executable files** anywhere (no `.py`/`.sh`/`.js`)
  — it is purely prompt-driven, so that automation was never wired to
  running code. §4.3's mapping follows this archetype directly rather
  than inventing one. The archetype's own notes also state: *"Best for
  personal capture; not designed for teams"* — directly relevant to why
  this repo (a single-developer repository per
  `.claude/rules/branching-and-ci.md`) uses this board shape at all.
- **How the 33 existing board items got there** — investigated, not
  assumed. GraphQL introspection of the project's 6 built-in workflows
  (`ProjectV2Workflow`) shows only "Auto-add sub-issues to project"
  enabled; none of the 6 auto-adds a newly-created top-level issue.
  Timing analysis of all 33 items (`content.createdAt` vs.
  `ProjectV2Item.createdAt`) shows them added in near-uniform ~3-4 second
  intervals, in exact reverse issue-number order — the signature of a
  script loop, not manual web-UI clicks. Conclusion: a one-off manual/
  scripted population, not `tools/kanban_sync` (which has zero
  project-related code today) and not GitHub-native automation. Without
  this design, no future issue this tool creates ever appears on the
  board.
- `gh` CLI (installed version 2.97.0) has a full `gh project` subcommand
  group — confirmed via real `--help` output, not assumed:
  `item-add`, `item-edit`, `item-list`, `field-list`, and others.
  `item-edit` supports setting a single-select field either by name
  (`--field "Status" --value "Doing"`) or by GraphQL node ID
  (`--id --field-id --project-id --single-select-option-id`). Both
  `item-add` and `item-edit` are GraphQL-backed under the hood despite
  their plain-CLI-flag appearance — confirmed via this repo's own test
  docstring (`tests/test_kanban_sync_github_client.py`) describing a real
  2026-08-27 incident where a 33-item bulk `item-add`+`item-edit` run
  silently exhausted the 5000/5000 GraphQL quota partway through.
- `tools/kanban_sync/github_client.py`'s `_run(args)` helper always
  appends `--repo <repo>` — correct for issue/PR operations, wrong for
  `gh project ...` commands, which are owner-scoped via `--owner`. The
  existing `graphql_rate_limit()` method already bypasses `_run` for
  exactly this reason (a global, non-repo-scoped endpoint) — the new
  project methods follow that same established pattern.
- `sources_tracks.py`'s `parse_track_items` always sets
  `status_label=labels.STATUS_CLAIMABLE` **regardless of `done`** (unlike
  `sources_roadmap.py`/`sources_plan.py`, which correctly flip
  `status_label` when `done=True`). This is a real correctness constraint
  on §4.3's implementation, not a style choice — read directly from the
  current file, not assumed.

## 4. Architecture

### 4.1 Part A — Phase vocabulary rename + worktree-only granularity

Final names (`tools/kanban_sync/labels.py`):

| Old | New |
|---|---|
| `PHASE_RESEARCH_EVIDENCE = "phase:research-evidence"` | `PHASE_RESEARCH = "phase:research"` |
| `PHASE_DESIGN_SPEC = "phase:design-spec"` | `PHASE_SPEC = "phase:spec"` |
| `PHASE_IMPLEMENTATION_PLAN = "phase:implementation-plan"` | `PHASE_PLAN = "phase:plan"` |
| `PHASE_IMPLEMENTED = "phase:implemented"` | `PHASE_DONE = "phase:done"` (mirrors `STATUS_DONE`) |
| *(none)* | `PHASE_IMPLEMENTING = "phase:implementing"` — **worktree-only** |
| *(none)* | `PHASE_VERIFICATION = "phase:verification"` — **worktree-only** |

`ALL_PHASE_LABELS` becomes the 6-member set.

**Why implementing/verification are worktree-only, not general** —
empirically grounded, not a preference: branch names do not reliably
correlate to plan/track initiative names in this repo's real history.
`chore/realtime-data-plane-investigation` vs.
`chore/realtime-dp-investigation` are two different branch names for
arguably the same topic; `feat/autonomous-quality-coordination` is a
literal substring of the unrelated, later, different initiative
`feat/autonomous-quality-coordination-workflow`; only 1 of the last 10
merged PR bodies referenced its plan doc's path at all. Slug-matching or
PR-body-text-matching would misfire against real, already-existing cases.
A worktree item has no such problem — it already maps 1:1 to exactly one
branch by construction.

`sources_worktree.py`'s `_status_for_pr_state` extends from a 2-tuple to
`(status_label, done, phase_label)`:

| PR state | `status_label` | `done` | `phase_label` |
|---|---|---|---|
| `None` (no PR yet) | `STATUS_IN_PROGRESS` | `False` | `PHASE_IMPLEMENTING` |
| `"OPEN"` | `STATUS_READY_FOR_REVIEW` | `False` | `PHASE_VERIFICATION` |
| `"MERGED"` | `STATUS_DONE` | `True` | `PHASE_DONE` |
| `"CLOSED"` (not merged) | `STATUS_IN_PROGRESS` | `False` | `PHASE_IMPLEMENTING` |

`build_worktree_items` passes `phase_label` into `SyncItem(...)`
(currently never set, defaults to `None`).

`sources_plan.py`/`sources_tracks.py` get a mechanical rename only — no
behavior change. Both still only ever reach spec/plan/research/done,
never the two worktree-only values.

### 4.2 Part B — Stale worktree-tracking-issue closure

**Root cause**: `build_worktree_items` only ever iterates
*currently-existing* worktrees (`git worktree list --porcelain`). Once a
worktree is removed post-merge, it is simply absent from that output —
there is no `done=True` item ever produced for it. `sync_pass_one`'s only
iteration is `for item in items:` (confirmed by reading the whole file) —
it never independently enumerates real GitHub issues, so an issue whose
item is entirely absent from a run is left completely untouched: not
closed, not queried, not flagged. This extends §9 of the parent spec,
which only ever discusses closing a still-represented source's issue.

New primitive, `github_client.py`:
```python
def list_open_by_label(self, label: str) -> list[IssueState]:
    stdout = self._run([
        "issue", "list", "--label", label, "--state", "open",
        "--json", "number,state,labels,body", "--limit", "1000",
    ])
    ...
```
`IssueState` gains a `body: str = ""` field (default-valued, no existing
call site changes). Deliberately unscoped by search term (unlike
`find_by_marker`) — lists every open `type:tracking` issue; the caller
parses each one's marker client-side via the existing
`markers.parse_marker()`.

New closure logic, `sync.py` (extends its existing "close issues whose
source item is now done... without ever reopening one a human closed by
hand" role, reusing the `_mismatch_comment`-style comment convention):
```python
def close_stale_worktree_issues(live_branches: set[str], client, *, dry_run: bool) -> SyncReport:
    ...
    for issue in client.list_open_by_label(labels.TYPE_TRACKING):
        parsed = parse_marker(issue.body)
        if parsed is None:
            continue
        kind, key = parsed
        if kind != labels.SYNC_MARKER_KIND_WORKTREE or key in live_branches:
            continue
        if not dry_run:
            client.post_comment(issue.number, _stale_worktree_comment(key))
            client.close_issue(issue.number)
        report.closed.append(...)
```
**Safety is structural, not just logical**: `list_open_by_label` only
returns `--state open` issues, so a manually-closed tracking issue for a
dead branch is never even in the candidate set — the parent spec's "never
reopen a manually-closed issue" invariant holds by construction. Any
issue whose body doesn't parse as a marker, or whose parsed kind isn't
`"worktree"`, is skipped outright — the label alone is never trusted.

`__main__.py`: `_collect_items` changes return type from `list[SyncItem]`
to `tuple[list[SyncItem], set[str] | None]` — `None` when `"worktree"`
isn't among `--sources`, otherwise the set of currently-live branch names
(computed from the same `porcelain` output already fetched, via a new
`live_worktree_branches` helper in `sources_worktree.py`). `_cmd_sync`
runs `close_stale_worktree_issues` only when non-`None`.

**Named risk, accepted rather than mitigated**: a human-*reopened* stale
issue gets re-closed on the next sync — consistent with `sync_pass_one`'s
existing behavior for a reopened-but-`done` item (no new inconsistency,
just worth naming since the "never reopen" precedent could be misread as
covering this direction too).

### 4.3 Part C — Project Status field automation (the main deliverable)

`status_label`/`done` → Status-option mapping, following the personal-todo
archetype (§3) directly:

| `status_label` | Status option | Why |
|---|---|---|
| `STATUS_CLAIMABLE` | **Next** | Archetype: "Triaged; ready for an agent or you" |
| `STATUS_CLAIMED` | **Doing** | A held lock is active engagement; claimed work can't stay "ready to pick up" |
| `STATUS_IN_PROGRESS` | **Doing** | Archetype: "Active work" |
| `STATUS_READY_FOR_REVIEW` | **Waiting** | Archetype's own Waiting description, "Blocked or waiting on external", literally describes an open PR awaiting review |
| `STATUS_BLOCKED` | **Waiting** | Archetype: direct match |
| `STATUS_DONE` / `item.done` | **Done** | Archetype: "Closed" |

**Inbox is never set by `kanban_sync`.** Every `SyncItem` this tool
produces already carries a real `status:*` value (`STATUS_CLAIMABLE` at
minimum) — Inbox is reserved for whatever a human adds to the project by
hand, outside this tool's model entirely, matching the archetype's own
`"status_label": null` entry for that column.

New module, `tools/kanban_sync/project_status.py` — holds the Project's
real GraphQL node IDs (confirmed live this session via both raw GraphQL
and `gh project field-list`) and the mapping table above. New
`github_client.py` methods (bypassing `_run`'s automatic `--repo` flag,
per §3):
```python
def ensure_on_project(self, issue_number: int) -> str:
    """Idempotently adds the issue to the project; returns the project
    item's own node ID (PVTI_..., distinct from the issue's node ID)."""
    ...

def set_project_status(self, item_id: str, status: str) -> None:
    """Sets the Status field by GraphQL node ID, not --field/--value
    name, so a human renaming a column's cosmetic label doesn't silently
    break this call."""
    ...
```
**ID-based, not name-based** — deliberate: an option's node ID is stable
across a pure display-text rename; only deleting/recreating an option
changes it, which already fails loud via `gh`'s own error.

New helper + 3 call sites in `sync.py`:
```python
def _sync_project_status(item, number, client, *, dry_run):
    if dry_run:
        return
    status = (project_status.STATUS_DONE if item.done
              else project_status.STATUS_LABEL_TO_PROJECT_STATUS[item.status_label])
    item_id = client.ensure_on_project(number)
    client.set_project_status(item_id, status)
```
`item.done` is checked *first*, not `item.status_label` — a necessary
correctness fix, not a style choice: `sources_tracks.py` always sets
`status_label=STATUS_CLAIMABLE` regardless of `done` (§3), so mapping via
`status_label` alone would land a just-finished track on "Next" instead
of "Done". Called from: the just-created branch, the
transition-to-done-and-close branch, and unconditionally after the
matched/updated branch (not gated by whether labels changed this run).
Deliberately excluded: the `flagged_mismatches` branch and the
already-closed-and-done no-op path, matching this file's existing
"closed issues are hands-off" philosophy.

**Unconditional every run, not diff-gated against the label** — decided
deliberately. Gating on "did the status label change this run" would mean
this feature never fires for any of the 33 pre-existing items on its
first run (they already carry the label value it would compute today).
Detecting drift against the actual current Project Status value would
need an extra read per item, the same cost order as just writing, for no
correctness benefit. At this repo's real scale (~30-40 items), the added
cost (+2 calls/item) is trivial against the 5000/hour GraphQL quota — the
2026-08-27 incident that exhausted it was caused by not checking
remaining budget before starting (already fixed via
`_check_rate_limit_budget`), not by per-item call count.

**Named consequence, stated explicitly rather than left as a silent side
effect**: the first real run after this ships overwrites any manually-set
Status value on all 33 pre-existing items — and any human drag-and-drop
thereafter, on every subsequent run — to match what `kanban_sync`
computes. The board becomes source-of-truth-driven and self-correcting.
This should be surfaced to the user before merging, since it changes how
the board behaves for manual edits going forward.

Worktree items need no special-casing: their PR-state-driven
`status_label` (already computed by §4.1's `_status_for_pr_state`) flows
through this same table naturally.

Rate-limit constant (`__main__.py`):
```python
_ESTIMATED_CALLS_PER_ITEM_WRITE = 6   # was 4 — +2 for ensure_on_project/set_project_status
_ESTIMATED_CALLS_PER_ITEM_DRY_RUN = 1  # unchanged — dry-run makes zero project calls
```

**Failure handling: hard failure, fail loud, stop the run.** No
try/except added — `GithubCliError` propagates and aborts the whole sync,
exactly like every other unhandled error in this codebase today (`sync.py`
has zero try/except anywhere; the rate-limit guard's own comment states
the philosophy directly: "refusing to start an under-budget run is safer
than a partial one"). A renamed/deleted Status field or option is
systemic, not per-item — it fails identically for every remaining item,
so one loud stop beats N silent misses.

## 5. Verification approach

Fault-injection scenarios per safety claim (mirrors the parent spec's
§13):

- Dry-run makes zero mutating calls, in both Part B (no
  `post_comment`/`close_issue`) and Part C (no `ensure_on_project`/
  `set_project_status`).
- A manually-closed tracking issue for a dead branch is never touched by
  Part B's closure pass (structural — `list_open_by_label` excludes it by
  construction, not by a runtime check).
- A `type:tracking` issue whose body doesn't parse as a marker, or whose
  parsed kind isn't `"worktree"`, is never closed by Part B.
- Part C reapplies the computed Status every run even when labels are
  unchanged (proves the deliberate unconditional/self-healing choice) —
  and correctly overwrites a pre-seeded "manually set" Status value to
  match what's computed (proves the named first-run consequence with a
  real test, not just prose).
- Part C never touches the project for a manually-closed issue whose
  source is still open (the `flagged_mismatches` branch).
- Live, read-only smoke test for Part B: `python -m tools.kanban_sync sync
  --sources worktree --dry-run` — confirm issue #98 is reported as a
  stale-closure candidate.
- Live smoke test for Part C (necessarily non-dry-run, since dry-run never
  touches the project): confirm one or two items' actual Project Status
  field changes as expected via `gh project item-list 3 --owner
  thesneakattack --format json`, before trusting a full run against all
  ~30-40 items.

## 6. Explicitly deferred

- A 6th Project Status option to distinguish blocked-on-dependency from
  waiting-on-review — the archetype's own "Waiting" column already
  conflates both by design; revisit only if that conflation becomes a
  real, felt problem.
- Generalizing the vanished-from-scan closure detection (Part B) to
  sources other than worktree.
- Verifying `gh project item-add`'s exact `--format json` shape and its
  claimed re-add idempotency beyond what `--help` documents — flagged for
  a one-time empirical check (`GH_DEBUG=api`, the same method this repo's
  own prior GraphQL-quota incident used) during implementation, not
  blocking this design.
- Tightening `_ESTIMATED_CALLS_PER_ITEM_WRITE` beyond the current
  conservative `6` if `gh` turns out to make more than 1 internal GraphQL
  call per `item-add`/`item-edit` invocation.

## 7. Self-review

- **Placeholder scan**: no TBD/TODO; every open question from the
  brainstorming/planning phase resolved to an explicit decision above
  (Inbox mapping, blocked-vs-ready-for-review, ID-based vs name-based
  field edits, diff-gated vs unconditional, fail-loud vs fail-soft).
- **Internal consistency**: §4.1's phase rename, §4.2's closure logic, and
  §4.3's Status-field sync all reference the same `SyncItem`/marker/label
  primitives the parent spec already established, without renaming a
  concept between sections.
- **Scope check**: three parts, but one initiative — all three were
  discovered in one continuous investigation and share heavy file overlap
  (`sync.py`, `__main__.py`, `sources_worktree.py`, `github_client.py`).
  Splitting them into separate specs would fragment shared context for no
  real isolation benefit, since none is independently useful without the
  others being at least designed alongside it.
- **Ambiguity check**: the one place two readings were possible (should
  Part C's project sync run diff-gated or unconditional) is resolved
  explicitly in §4.3 with reasoning, not left to the implementer's
  judgment.
