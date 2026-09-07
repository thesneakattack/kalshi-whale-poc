# Kanban Sync Improvements — Design

**Date:** 2026-08-28  
**Motivation:** Three failures observed in the live sync run of 2026-08-28: `decompose-plan` created sub-issues under a closed parent issue; a plan was misclassified because existing-issue state wasn't checked before git log; and the mismatch comment gave no hint what classification triggered it.

## Scope

Three independent targeted fixes. No new subsystems. All flows already exist in `tools/kanban_sync/`.

Files touched: `tools/kanban_sync/github_client.py`, `tools/kanban_sync/__main__.py`, `tools/kanban_sync/models.py`, `tools/kanban_sync/sources_plan.py`, `tools/kanban_sync/sync.py`, `.claude/skills/kanban-board-sync/SKILL.md`, `tests/test_kanban_sync_decompose.py` (or new test file).

## Fix 1 — `decompose-plan` refuses a closed parent issue

**Root cause:** `_cmd_decompose_plan` in `__main__.py` resolves a `parent_number` then calls `decompose_plan` immediately, never checking whether the parent issue is open or closed. Result: sub-issues are created under a closed issue (#96 → sub-issues #169–172 created and had to be manually closed).

**Design:**

- `github_client.py`: add `get_issue(number: int) -> IssueState | None` — a single `gh issue view <N> --json number,state,labels` call, same `IssueState` shape `find_by_marker` already returns.
- `__main__.py` `_cmd_decompose_plan`: after resolving `parent_number` in both branches (marker-lookup and `--parent-issue`), check `parent_issue.open`; if closed, print a clear actionable error and `sys.exit(1)`:

```
error: parent issue #N is closed.
If the plan is done, reclassify it as 'done' in your classification JSON
and re-run 'sync --sources plan' — that is the correct resolution path,
not decompose.
```

- `--dry-run` also exits 1 on a closed parent (dry run tests the action's preconditions).
- Tests: closed parent → exit 1 (both with and without `--dry-run`); open parent proceeds as before.

## Fix 2 — Classification step checks existing issue state first

**Root cause:** The skill's step 4 guidance reads `git log -- docs/superpowers/plans/<file>` then cross-references CLAUDE.md/ROADMAP.md. This misses the case where a plan's implementation commits never touched the plan file, but the tracking issue was closed manually with an explicit "done, merged in PR #N" comment — the strongest available signal.

**Design:**

Add a new first substep to `SKILL.md` step 4, before the git-log check:

> **Check the existing issue state first.** Run `gh issue list --search 'autotrade-sync: plan:<filename>' --state all --json number,state,title,body`. If the issue exists and is CLOSED: read its most recent comment. A closing comment that says "Done — merged in PR #N" or equivalent is `done`, regardless of checkbox count or git-log shape on the plan file. Only proceed to git log if the issue is open or absent.

No code change — this is guidance only.

## Fix 3 — Mismatch comment names the classification that triggered it

**Root cause:** `_mismatch_comment` in `sync.py` emits a generic "source is still open" message for all kinds. For plan items, "source is still open" is misleading — what actually happened is that the plan was classified `in-progress` or `not-started`, producing a non-done SyncItem, which triggered the mismatch. The reader has no way to know what to change.

**Design:**

- `models.py` `SyncItem`: add `classification: str = ""` field (empty string for non-plan kinds, no breaking change).
- `sources_plan.py` `build_plan_items`: set `classification=info["status"]` when constructing each `SyncItem`.
- `sync.py` `_mismatch_comment`: when `item.kind == labels.SYNC_MARKER_KIND_PLAN` and `item.classification`, emit a plan-specific message:

```
This issue is closed on GitHub, but this sync run classified its plan as
`<classification>` (not `done`). To resolve: reclassify the plan as `done`
in your classification JSON and re-run `sync --sources plan` — the sync
will then leave this issue closed correctly. Not reopening automatically.
```

Non-plan kinds keep the existing generic message unchanged.

- Tests: plan-kind SyncItem with `classification="in-progress"` → plan-specific comment; non-plan kind → existing generic comment.

## Testing strategy

Each fix is independently testable. Existing tests must not regress. New tests use the injected-runner/injected-client pattern already established in this test suite — no real `gh` calls, no network.

## Non-goals

- No change to the mismatch detection logic itself (what constitutes a mismatch is unchanged).
- No change to the classification algorithm (it remains a human judgment call; Fix 2 improves the guidance, not the automation).
- No auto-cleanup of orphaned sub-issues (Fix 1 prevents the problem; cleanup of the 2026-08-28 incident was done manually).
