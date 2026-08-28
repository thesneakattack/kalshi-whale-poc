# Kanban Sync Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three failures from the 2026-08-28 live sync run: closed-parent guard, classification guidance, and mismatch comment quality.

**Architecture:** Three independent targeted fixes — (1) guard in `_cmd_decompose_plan` that refuses a closed parent; (2) guidance update in `kanban-board-sync/SKILL.md`; (3) `classification` field on `SyncItem` used for plan-specific mismatch comments.

**Tech Stack:** Python 3.13, pytest, `gh` CLI (injected via `FakeRunner` / fake-client pattern).

**Spec:** `docs/superpowers/specs/2026-08-28-kanban-sync-improvements-design.md`

## Global Constraints

- Workflow tooling only — never import from `services/`, `main.py`, or `config/`.
- All tests use `FakeRunner` / injected-client pattern; no real `gh` calls.
- `SyncItem` is `frozen=True`; new field must have a default and appear after all non-default fields.
- `_mismatch_comment` behavior for non-plan kinds is unchanged.
- Guard belongs in `__main__._cmd_decompose_plan`, not in `plan_tasks.decompose_plan`.
- `--dry-run` also refuses a closed parent (it tests preconditions).
- Stage specific paths; never `git add -A`. Branch: `chore/kanban-sync-improvements`.

---

### Task 1: Closed-parent guard

**Files:**
- Modify: `tools/kanban_sync/github_client.py` — add `get_issue(number: int) -> IssueState | None`
- Modify: `tools/kanban_sync/__main__.py` — closed-parent check in `_cmd_decompose_plan` after `parent_number` is resolved
- Test: `tests/test_kanban_sync_github_client.py` — 2 tests for `get_issue`
- Test: `tests/test_kanban_sync_main.py` — 2 tests for guard; update `_FakeParentIssueClient`

**Interfaces:**
- Produces: `GithubClient.get_issue(number: int) -> IssueState | None` — `gh issue view <N> --json number,state,labels`; `None` on `GithubCliError`.

- [ ] **Step 1: Write the two `get_issue` tests (expect failure)**

Add to `tests/test_kanban_sync_github_client.py`:
```python
def test_get_issue_returns_issue_state_for_open_issue():
    runner = FakeRunner()
    runner.queue(json.dumps({
        "number": 96, "state": "OPEN",
        "labels": [{"name": "status:claimable"}, {"name": "type:plan-task"}],
    }))
    client = GithubClient(REPO, runner=runner)
    result = client.get_issue(96)
    assert result is not None and result.number == 96 and result.open is True
    assert result.labels == frozenset({"status:claimable", "type:plan-task"})
    call = runner.calls[0]
    assert call[:3] == ["gh", "issue", "view"] and "96" in call
    assert "--json" in call and "number,state,labels" in call

def test_get_issue_returns_none_on_cli_error():
    runner = FakeRunner()
    runner.queue("", returncode=1, stderr="HTTP 404: Not Found")
    client = GithubClient(REPO, runner=runner)
    assert client.get_issue(9999) is None
```

Run `pytest tests/test_kanban_sync_github_client.py::test_get_issue_returns_issue_state_for_open_issue tests/test_kanban_sync_github_client.py::test_get_issue_returns_none_on_cli_error -v`. Expected: FAIL.

- [ ] **Step 2: Implement `get_issue` in `github_client.py`**

Add after `get_sub_issues_summary`:
```python
def get_issue(self, number: int) -> IssueState | None:
    try:
        stdout = self._run(["issue", "view", str(number), "--json", "number,state,labels"])
    except GithubCliError:
        return None
    item = json.loads(stdout)
    return IssueState(
        number=item["number"],
        open=item["state"] == "OPEN",
        labels=frozenset(label["name"] for label in item["labels"]),
    )
```

Run the two tests. Expected: PASS.

- [ ] **Step 3: Write the two closed-parent guard tests (expect failure)**

Add to `tests/test_kanban_sync_main.py`:
```python
class _FakeClosedParentClient:
    def find_by_marker(self, marker):
        from tools.kanban_sync.github_client import IssueState
        return IssueState(number=96, open=True, labels=frozenset())
    def get_issue(self, number):
        from tools.kanban_sync.github_client import IssueState
        return IssueState(number=number, open=False, labels=frozenset())

def test_decompose_plan_subcommand_errors_when_parent_is_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_check_project_scope", lambda: None)
    monkeypatch.setattr(cli, "GithubClient", lambda repo: _FakeClosedParentClient())
    monkeypatch.setattr(cli, "PLANS_DIR", tmp_path)
    (tmp_path / "x.md").write_text("# X\n")
    with pytest.raises(SystemExit) as exc:
        cli.main(["decompose-plan", "--plan", "x.md"])
    assert exc.value.code == 1

def test_decompose_plan_subcommand_errors_when_parent_is_closed_dry_run(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_check_project_scope", lambda: None)
    monkeypatch.setattr(cli, "GithubClient", lambda repo: _FakeClosedParentClient())
    monkeypatch.setattr(cli, "PLANS_DIR", tmp_path)
    (tmp_path / "x.md").write_text("# X\n")
    with pytest.raises(SystemExit) as exc:
        cli.main(["decompose-plan", "--plan", "x.md", "--dry-run"])
    assert exc.value.code == 1
```

Also add `get_issue` to the existing `_FakeParentIssueClient` (returns open IssueState — the `--parent-issue` bypass path still works):
```python
def get_issue(self, number):
    from tools.kanban_sync.github_client import IssueState
    return IssueState(number=number, open=True, labels=frozenset())
```

Run the two new tests. Expected: FAIL.

- [ ] **Step 4: Add the guard in `_cmd_decompose_plan`**

After the `if args.parent_issue is not None / else` block that sets `parent_number` (before `plan_path = PLANS_DIR / args.plan`), insert:
```python
parent_issue_state = client.get_issue(parent_number)
if parent_issue_state is not None and not parent_issue_state.open:
    print(
        f"error: parent issue #{parent_number} is closed.\n"
        f"If the plan is done, reclassify it as 'done' in your classification JSON\n"
        f"and re-run 'sync --sources plan' — that is the correct resolution path,\n"
        f"not decompose.",
        file=sys.stderr,
    )
    sys.exit(1)
```

Run `pytest tests/test_kanban_sync_github_client.py tests/test_kanban_sync_main.py tests/test_kanban_sync_plan_tasks.py -v`. Expected: all PASS.

- [ ] **Step 5: Commit and push; confirm CI**

```bash
git add tools/kanban_sync/github_client.py tools/kanban_sync/__main__.py \
  tests/test_kanban_sync_github_client.py tests/test_kanban_sync_main.py \
  docs/superpowers/plans/2026-08-28-kanban-sync-improvements.md
git commit -m "fix: decompose-plan refuses a closed parent issue (#96 → sub-issues #169-172 incident)"
git push
gh api repos/thesneakattack/kalshi-whale-poc/commits/$(git rev-parse HEAD)/status \
  --jq '.statuses[] | "\(.context): \(.state)"'
```

---

### Task 2: Classification guidance — SKILL.md step 4

**Files:**
- Modify: `.claude/skills/kanban-board-sync/SKILL.md` — step 4 gets a new first substep: check existing issue state before git-log

**Interfaces:** None (guidance only; no tests).

- [ ] **Step 1: Update step 4 in SKILL.md**

Replace the step 4 paragraph beginning "**Classify each candidate.**" with:

```markdown
4. **Classify each candidate.** For each filename this is a judgment call,
   not a checkbox scan (spec §5 measured why plan-doc `- [ ]` state is
   unreliable in this repo).

   **Check the existing issue state first:**
   ```bash
   gh issue list --search 'autotrade-sync: plan:<filename>' --state all \
     --json number,state,title --limit 1
   ```
   If the issue exists and is **CLOSED**: read its most recent comment
   (`gh issue view <N> --comments`). A "done, merged in PR #N" comment
   means classify `done` immediately — strongest signal, overrides git-log.

   Only when the issue is open or absent: read
   `git log --oneline -- docs/superpowers/plans/<file>`, cross-reference
   `CLAUDE.md` and `ROADMAP.md`, and classify `done`, `in-progress`, or
   `not-started`. Write to `/tmp/kanban-plan-classifications.json`.
```

- [ ] **Step 2: Commit and push; confirm CI**

```bash
git add .claude/skills/kanban-board-sync/SKILL.md
git commit -m "fix: kanban-board-sync skill checks existing issue state before git-log classification"
git push
gh api repos/thesneakattack/kalshi-whale-poc/commits/$(git rev-parse HEAD)/status \
  --jq '.statuses[] | "\(.context): \(.state)"'
```

---

### Task 3: Classification-aware mismatch comment

**Files:**
- Modify: `tools/kanban_sync/models.py` — add `classification: str = ""` to `SyncItem` (last field)
- Modify: `tools/kanban_sync/sources_plan.py` — set `classification=info["status"]` in `build_plan_items`
- Modify: `tools/kanban_sync/sync.py` — plan-specific branch in `_mismatch_comment`
- Test: `tests/test_kanban_sync_sources_plan.py` — 1 test
- Test: `tests/test_kanban_sync_sync.py` — 2 tests

**Interfaces:**
- Produces: `SyncItem.classification: str = ""` — set by `sources_plan`, empty for all other kinds.

- [ ] **Step 1: Write the three new tests (expect failure)**

In `tests/test_kanban_sync_sources_plan.py`:
```python
def test_build_plan_items_sets_classification_from_status():
    items = build_plan_items({
        "x.md": {"status": "in-progress", "note": "partial"},
        "y.md": {"status": "done", "note": ""},
    })
    x = next(i for i in items if i.key == "x.md")
    y = next(i for i in items if i.key == "y.md")
    assert x.classification == "in-progress"
    assert y.classification == "done"
```

In `tests/test_kanban_sync_sync.py` (import `_mismatch_comment` from `tools.kanban_sync.sync`):
```python
def test_mismatch_comment_for_plan_kind_names_the_classification():
    from tools.kanban_sync.models import SyncItem
    plan_item = SyncItem(
        kind=labels.SYNC_MARKER_KIND_PLAN, key="2026-08-27-x.md",
        title="Plan: 2026-08-27-x.md", status_label=labels.STATUS_CLAIMABLE,
        type_label=labels.TYPE_PLAN_TASK, context_body="",
        acceptance_criteria=(), classification="in-progress",
    )
    comment = _mismatch_comment(plan_item)
    assert "in-progress" in comment and "reclassify" in comment
    assert "sync --sources plan" in comment

def test_mismatch_comment_for_non_plan_kind_uses_generic_message():
    from tools.kanban_sync.models import SyncItem
    track_item = SyncItem(
        kind=labels.SYNC_MARKER_KIND_WORKTREE, key="feat/x",
        title="feat/x", status_label=labels.STATUS_CLAIMABLE,
        type_label=labels.TYPE_TRACKING, context_body="",
        acceptance_criteria=(),
    )
    comment = _mismatch_comment(track_item)
    assert "still open" in comment and "reclassify" not in comment
```

Run all three tests. Expected: FAIL.

- [ ] **Step 2: Implement the three changes**

`models.py` — add as the last field of `SyncItem`:
```python
classification: str = ""
```

`sources_plan.py` — add `classification=info["status"]` to the `SyncItem(...)` call.

`sync.py` — replace `_mismatch_comment`:
```python
def _mismatch_comment(item: SyncItem) -> str:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if item.kind == labels.SYNC_MARKER_KIND_PLAN and item.classification:
        return (
            f"<!-- event: sync-mismatch | agent: kanban-board-sync | ts: {ts} -->\n"
            f"This issue is closed on GitHub, but this sync run classified its plan as "
            f"`{item.classification}` (not `done`). To resolve: reclassify the plan as "
            f"`done` in your classification JSON and re-run `sync --sources plan` — the "
            f"sync will then leave this issue closed correctly. Not reopening automatically."
        )
    return (
        f"<!-- event: sync-mismatch | agent: kanban-board-sync | ts: {ts} -->\n"
        f"This issue is closed on GitHub, but its source (`{item.kind}:{item.key}`) "
        f"is still open. Not reopening automatically - please reconcile manually."
    )
```

Run `pytest tests/test_kanban_sync_models.py tests/test_kanban_sync_sources_plan.py tests/test_kanban_sync_sync.py -v`. Expected: all PASS.

- [ ] **Step 3: Commit, push, confirm CI, open PR**

```bash
git add tools/kanban_sync/models.py tools/kanban_sync/sources_plan.py \
  tools/kanban_sync/sync.py tests/test_kanban_sync_sources_plan.py \
  tests/test_kanban_sync_sync.py
git commit -m "fix: mismatch comment names the classification that triggered it (plan kind)"
git push
gh api repos/thesneakattack/kalshi-whale-poc/commits/$(git rev-parse HEAD)/status \
  --jq '.statuses[] | "\(.context): \(.state)"'
gh pr create --title "fix: kanban sync improvements (closed-parent guard, classification guidance, mismatch comment)" \
  --body "Three targeted fixes from the 2026-08-28 live sync run failures. Spec: docs/superpowers/specs/2026-08-28-kanban-sync-improvements-design.md"
```
