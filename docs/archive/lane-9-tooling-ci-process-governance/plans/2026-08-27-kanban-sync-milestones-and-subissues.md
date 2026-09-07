# Kanban Sync Milestones and Sub-Issues Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give plan-tracked `kanban_sync` issues a milestone (one per plan)
and, for plans using the canonical `### Task N:` heading convention, one
GitHub-native sub-issue per task with sequential `depends-on` chaining —
plus new mechanical logic to close a plan's parent issue once all its
sub-issues are done.

**Architecture:** A new `tools/kanban_sync/plan_tasks.py` module owns
task-heading parsing and the one-time decomposition orchestration.
`github_client.py` gains milestone (`create_milestone`,
`find_milestone_by_title`, `set_milestone`) and sub-issue
(`create_issue`'s `parent`/`milestone` kwargs, `get_sub_issues_summary`)
primitives, all via plain `gh` CLI flags — no raw GraphQL. `sync.py`
gains `close_completed_plan_parents`, mirroring the existing
`close_stale_worktree_issues` shape. A new `decompose-plan` CLI
subcommand in `__main__.py` is the one-time trigger (deliberately not
folded into the ordinary `sync` reconciliation loop).

**Tech Stack:** Python 3.13, `gh` CLI 2.97.0 (`--parent`/`--milestone`
flags on `issue create`/`issue edit`, `gh api` for milestone CRUD), the
existing `FakeRunner`/`FakeGithubClient` test doubles.

**Spec:** `docs/archive/lane-9-tooling-ci-process-governance/specs/2026-08-27-kanban-sync-milestones-and-subissues-design.md`

## Global Constraints

- Only the canonical `### Task N: <title>` heading convention is parsed
  (spec §3, §4.1) — the other two conventions found in real plan docs
  (`## Task N:` and `## T1a —`) are out of scope; zero matches is a valid,
  non-error outcome, not a fallback-parsing attempt.
- Milestone/sub-issue creation is a **one-time** action per plan, gated on
  `subIssuesSummary.total == 0` on the plan's existing parent issue — never
  folded into the ordinary `sync --sources plan` reconciliation loop
  (spec §4.2).
- Sub-issue closing is explicit (a human/Claude action when a task's
  commit lands), never auto-detected from git log (spec §4.3, rejected
  with reasoning — do not reopen this question mid-implementation).
- Parent auto-close (when `completed == total > 0`) *is* new ongoing
  reconciliation logic — the one piece of this design that runs every
  sync (spec §4.4).
- Every new `GithubClient` method takes an injectable runner and is tested
  via `FakeRunner`/`FakeGithubClient` — no test ever performs a real `gh`
  call (matching every existing test in this codebase).
- TDD throughout: write the failing test, confirm it fails for the right
  reason, then implement.

---

### Task 1: `plan_tasks.py` — canonical Task-heading parser

**Files:**
- Create: `tools/kanban_sync/plan_tasks.py`
- Test: `tests/test_kanban_sync_plan_tasks.py`

**Interfaces:**
- Produces: `parse_canonical_tasks(text: str) -> list[tuple[int, str]]` —
  returns `(task_number, task_title)` pairs in document order. Empty list
  when the canonical pattern doesn't appear at all.

- [ ] **Step 1: Write the failing tests**

```python
from tools.kanban_sync.plan_tasks import parse_canonical_tasks

_CANONICAL = """# Some Plan

### Task 1: `labels.py`: rename + 2 new constants

body text here

### Task 2: `sources_plan.py` rename

more body
"""

_SHALLOWER_LEVEL = """# Some Plan

## Task 1: Schema module and connection helper

body
"""

_LETTERED_SUBTASKS = """# Some Plan

## T1a — Guards first, stale docs

body
"""


def test_parse_canonical_tasks_extracts_number_and_title_in_order():
    result = parse_canonical_tasks(_CANONICAL)

    assert result == [
        (1, "`labels.py`: rename + 2 new constants"),
        (2, "`sources_plan.py` rename"),
    ]


def test_parse_canonical_tasks_returns_empty_for_shallower_heading_level():
    """## Task N: (one level shallower than the canonical ### Task N:) is
    a real, different convention found in this repo's own plan docs -
    must not match, not fall back to a looser pattern."""
    assert parse_canonical_tasks(_SHALLOWER_LEVEL) == []


def test_parse_canonical_tasks_returns_empty_for_lettered_subtask_scheme():
    """## T1a - <title> is a third real convention (frontend-modularization's
    own plan) - no "Task N:" text at all, must not match."""
    assert parse_canonical_tasks(_LETTERED_SUBTASKS) == []


def test_parse_canonical_tasks_returns_empty_for_plain_text():
    assert parse_canonical_tasks("Just some prose, no headings at all.") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ddev exec -s fastapi python -m pytest tests/test_kanban_sync_plan_tasks.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.kanban_sync.plan_tasks'`

- [ ] **Step 3: Write the implementation**

```python
"""Canonical `### Task N: <title>` heading parser for numbered plan docs,
and the one-time milestone/sub-issue decomposition action for
plan-tracked kanban_sync issues (spec: docs/archive/lane-9-tooling-ci-process-governance/specs/2026-08-27-kanban-sync-milestones-and-subissues-design.md).

Only the canonical writing-plans template heading
(`### Task N: <title>`) is parsed - this repo's own plan docs are
confirmed (via direct grep, not assumption) to also use two other
conventions (`## Task N:`, one level shallower; `## T1a -`, a lettered
PR-group scheme with no "Task N" text at all). Both are deliberately out
of scope (spec §2, §4.1) - zero matches is a valid, non-error outcome,
not a signal to try a looser pattern.
"""
from __future__ import annotations

import re

_TASK_HEADING_RE = re.compile(r"^### Task (\d+):\s*(.+)$", re.MULTILINE)


def parse_canonical_tasks(text: str) -> list[tuple[int, str]]:
    return [(int(m.group(1)), m.group(2).strip()) for m in _TASK_HEADING_RE.finditer(text)]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ddev exec -s fastapi python -m pytest tests/test_kanban_sync_plan_tasks.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/kanban_sync/plan_tasks.py tests/test_kanban_sync_plan_tasks.py
git commit -m "feat: add canonical Task-heading parser for plan docs"
```

---

### Task 2: `github_client.py` — milestone create/find

**Files:**
- Modify: `tools/kanban_sync/github_client.py`
- Test: `tests/test_kanban_sync_github_client.py`

**Interfaces:**
- Consumes: `GithubClient._run` (existing, repo-scoped `gh` wrapper).
- Produces: `create_milestone(title: str) -> int` (returns the milestone's
  `number`); `find_milestone_by_title(title: str) -> int | None`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_kanban_sync_github_client.py` (near the other
milestone/project-adjacent tests, e.g. after the `set_project_status`
tests):

```python
def test_create_milestone_posts_to_the_milestones_endpoint():
    runner = FakeRunner()
    runner.queue(json.dumps({"number": 7, "title": "2026-08-27-some-plan.md"}))
    client = GithubClient(REPO, runner=runner)

    result = client.create_milestone("2026-08-27-some-plan.md")

    assert result == 7
    call = runner.calls[0]
    assert call[:3] == ["gh", "api", "-X"]
    assert "POST" in call
    assert f"repos/{REPO}/milestones" in call
    assert "-f" in call and "title=2026-08-27-some-plan.md" in call


def test_create_milestone_propagates_a_real_error():
    runner = FakeRunner()
    runner.queue("", returncode=1, stderr="HTTP 500: Internal Server Error")
    client = GithubClient(REPO, runner=runner)

    try:
        client.create_milestone("x")
        assert False, "expected GithubCliError"
    except GithubCliError as exc:
        assert "500" in str(exc)


def test_find_milestone_by_title_returns_matching_number():
    runner = FakeRunner()
    runner.queue(json.dumps([
        {"number": 3, "title": "other-plan.md"},
        {"number": 7, "title": "2026-08-27-some-plan.md"},
    ]))
    client = GithubClient(REPO, runner=runner)

    result = client.find_milestone_by_title("2026-08-27-some-plan.md")

    assert result == 7
    call = runner.calls[0]
    assert call[:2] == ["gh", "api"]
    assert f"repos/{REPO}/milestones" in call
    assert "-X" in call and "GET" in call
    assert "state=all" in call


def test_find_milestone_by_title_returns_none_when_no_match():
    runner = FakeRunner()
    runner.queue(json.dumps([{"number": 3, "title": "other-plan.md"}]))
    client = GithubClient(REPO, runner=runner)

    assert client.find_milestone_by_title("2026-08-27-some-plan.md") is None


def test_find_milestone_by_title_propagates_a_real_error():
    runner = FakeRunner()
    runner.queue("", returncode=1, stderr="HTTP 500: Internal Server Error")
    client = GithubClient(REPO, runner=runner)

    try:
        client.find_milestone_by_title("x")
        assert False, "expected GithubCliError"
    except GithubCliError as exc:
        assert "500" in str(exc)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ddev exec -s fastapi python -m pytest tests/test_kanban_sync_github_client.py -k milestone -v`
Expected: FAIL with `AttributeError: 'GithubClient' object has no attribute 'create_milestone'`

- [ ] **Step 3: Write the implementation**

Add to `tools/kanban_sync/github_client.py`, after `set_project_status`
(both are owner/repo-object-scoped `gh api`-backed calls, kept together):

```python
    def create_milestone(self, title: str) -> int:
        """Creates a new milestone, returning its repo-scoped number
        (distinct from a project item's node ID or an issue's number)."""
        result = self._runner([
            "gh", "api", "-X", "POST", f"repos/{self._repo}/milestones",
            "-f", f"title={title}",
        ])
        if result.returncode != 0:
            raise GithubCliError(f"gh api milestones create failed: {result.stderr or result.stdout}")
        return json.loads(result.stdout)["number"]

    def find_milestone_by_title(self, title: str) -> int | None:
        """state=all (not just open) so a milestone someone closed by hand
        is still found - avoids creating a duplicate-titled milestone.
        -X GET is required alongside -f: gh api defaults to POST whenever
        any -f/-F field is present unless -X explicitly overrides it -
        confirmed live (a bare -f state=all here 422s, since it POSTs
        {"state": "all"} as a body to a GET-only endpoint instead of
        appending it as a query string)."""
        result = self._runner([
            "gh", "api", "-X", "GET", f"repos/{self._repo}/milestones", "-f", "state=all",
        ])
        if result.returncode != 0:
            raise GithubCliError(f"gh api milestones list failed: {result.stderr or result.stdout}")
        for milestone in json.loads(result.stdout):
            if milestone["title"] == title:
                return milestone["number"]
        return None
```

Note: both bypass `_run`'s automatic `--repo` flag (like
`graphql_rate_limit`/`ensure_on_project`/`set_project_status` already
do) since the repo is embedded directly in the API path
(`repos/{owner}/{repo}/milestones`), not passed as a separate `--repo`
flag the way `gh issue`/`gh pr` subcommands take it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `ddev exec -s fastapi python -m pytest tests/test_kanban_sync_github_client.py -v`
Expected: PASS (all tests, including the 5 new ones)

- [ ] **Step 5: Commit**

```bash
git add tools/kanban_sync/github_client.py tests/test_kanban_sync_github_client.py
git commit -m "feat: add github_client.create_milestone/find_milestone_by_title"
```

---

### Task 3: `github_client.py` — sub-issue support

**Files:**
- Modify: `tools/kanban_sync/github_client.py`
- Test: `tests/test_kanban_sync_github_client.py`

**Interfaces:**
- Consumes: existing `create_issue`, extended (backward-compatible).
- Produces: `create_issue(..., *, parent: int | None = None, milestone:
  str | None = None)`; `get_sub_issues_summary(issue_number: int) ->
  tuple[int, int]` (completed, total); `set_milestone(issue_number: int,
  title: str | None) -> None` (`None` removes the milestone).

- [ ] **Step 1: Write the failing tests**

```python
def test_create_issue_with_parent_and_milestone_adds_both_flags():
    runner = FakeRunner()
    runner.queue("https://github.com/thesneakattack/kalshi-whale-poc/issues/50\n")
    client = GithubClient(REPO, runner=runner)

    result = client.create_issue(
        "Task 1: Title", "Body", ["status:claimable"],
        parent=42, milestone="2026-08-27-some-plan.md",
    )

    assert result.number == 50
    call = runner.calls[0]
    assert "--parent" in call and "42" in call
    assert "--milestone" in call and "2026-08-27-some-plan.md" in call


def test_create_issue_without_parent_or_milestone_omits_both_flags():
    """Backward-compatibility guard: every existing call site (worktree/
    roadmap/track/plan item creation) never passes these - must produce
    the exact same args as before this change."""
    runner = FakeRunner()
    runner.queue("https://github.com/thesneakattack/kalshi-whale-poc/issues/50\n")
    client = GithubClient(REPO, runner=runner)

    client.create_issue("Title", "Body", ["status:claimable"])

    call = runner.calls[0]
    assert "--parent" not in call
    assert "--milestone" not in call


def test_get_sub_issues_summary_returns_completed_and_total():
    runner = FakeRunner()
    runner.queue(json.dumps({"subIssuesSummary": {"completed": 2, "total": 5, "percentCompleted": 40}}))
    client = GithubClient(REPO, runner=runner)

    result = client.get_sub_issues_summary(42)

    assert result == (2, 5)
    call = runner.calls[0]
    assert call[:3] == ["gh", "issue", "view"]
    assert "42" in call
    assert "--repo" in call and REPO in call
    assert "--json" in call and "subIssuesSummary" in call


def test_get_sub_issues_summary_propagates_a_real_error():
    runner = FakeRunner()
    runner.queue("", returncode=1, stderr="HTTP 404: Not Found")
    client = GithubClient(REPO, runner=runner)

    try:
        client.get_sub_issues_summary(999)
        assert False, "expected GithubCliError"
    except GithubCliError as exc:
        assert "404" in str(exc)


def test_set_milestone_adds_the_milestone_flag():
    runner = FakeRunner()
    runner.queue("")
    client = GithubClient(REPO, runner=runner)

    client.set_milestone(42, "2026-08-27-some-plan.md")

    call = runner.calls[0]
    assert call[:3] == ["gh", "issue", "edit"]
    assert "--milestone" in call and "2026-08-27-some-plan.md" in call


def test_set_milestone_none_removes_the_milestone():
    runner = FakeRunner()
    runner.queue("")
    client = GithubClient(REPO, runner=runner)

    client.set_milestone(42, None)

    call = runner.calls[0]
    assert "--remove-milestone" in call
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ddev exec -s fastapi python -m pytest tests/test_kanban_sync_github_client.py -k "parent or sub_issues or set_milestone" -v`
Expected: FAIL — `create_issue()` raises `TypeError: create_issue() got an
unexpected keyword argument 'parent'`; `get_sub_issues_summary`/
`set_milestone` raise `AttributeError`.

- [ ] **Step 3: Write the implementation**

Replace the existing `create_issue` method:

```python
    def create_issue(
        self, title: str, body: str, labels: Sequence[str],
        *, parent: int | None = None, milestone: str | None = None,
    ) -> IssueState:
        args = ["issue", "create", "--title", title, "--body", body]
        for label in labels:
            args += ["--label", label]
        if parent is not None:
            args += ["--parent", str(parent)]
        if milestone is not None:
            args += ["--milestone", milestone]
        # A brand-new label family's first-ever item (e.g. phase:* on its
        # first ever creation, 2026-08-27) hits this path before any issue
        # exists to retrofit via set_labels' own already-proven retry below -
        # same "gh doesn't auto-create a missing label" risk, same fix.
        for _ in range(len(labels) + 1):
            try:
                stdout = self._run(args)
                match = _URL_NUMBER_RE.search(stdout.strip())
                if not match:
                    raise GithubCliError(f"could not parse issue number from: {stdout!r}")
                return IssueState(number=int(match.group(1)), open=True, labels=frozenset(labels))
            except GithubCliError as exc:
                match = _LABEL_NOT_FOUND_RE.search(str(exc))
                if not match:
                    raise
                self._run(["label", "create", match.group(1), "--color", "ededed"])
        raise GithubCliError("gh issue create still failing after creating missing labels")
```

Add after `find_pr_state` (or near the other issue-level methods):

```python
    def get_sub_issues_summary(self, issue_number: int) -> tuple[int, int]:
        """Returns (completed, total) sub-issue counts. Repo-scoped (unlike
        the project-object methods above), so uses _run's automatic --repo
        flag like find_by_marker/create_issue already do."""
        stdout = self._run(["issue", "view", str(issue_number), "--json", "subIssuesSummary"])
        data = json.loads(stdout)["subIssuesSummary"]
        return data["completed"], data["total"]

    def set_milestone(self, issue_number: int, title: str | None) -> None:
        if title is None:
            self._run(["issue", "edit", str(issue_number), "--remove-milestone"])
        else:
            self._run(["issue", "edit", str(issue_number), "--milestone", title])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ddev exec -s fastapi python -m pytest tests/test_kanban_sync_github_client.py -v`
Expected: PASS (all tests — confirms the `create_issue` extension didn't
break any existing call-site assertion)

- [ ] **Step 5: Commit**

```bash
git add tools/kanban_sync/github_client.py tests/test_kanban_sync_github_client.py
git commit -m "feat: add sub-issue support (create_issue parent/milestone, get_sub_issues_summary, set_milestone)"
```

---

### Task 4: `plan_tasks.py` — one-time decomposition orchestration

**Files:**
- Modify: `tools/kanban_sync/plan_tasks.py`
- Test: `tests/test_kanban_sync_plan_tasks.py`

**Interfaces:**
- Consumes: `client.get_sub_issues_summary`, `client.find_milestone_by_title`,
  `client.create_milestone`, `client.set_milestone`, `client.create_issue`,
  `client.set_labels` (all from Task 2/3, plus existing `set_labels`).
- Produces: `decompose_plan(plan_filename: str, plan_text: str,
  parent_number: int, client, *, dry_run: bool) -> dict` — returns
  `{"skipped": "already decomposed", "existing_sub_issues": int}` if
  already decomposed, else `{"milestone": str, "tasks_found": int,
  "sub_issues_created": list[int]}`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_kanban_sync_plan_tasks.py`:

```python
from tools.kanban_sync import labels
from tools.kanban_sync.plan_tasks import decompose_plan


class _FakeDecomposeClient:
    def __init__(self, *, existing_summary=(0, 0), existing_milestone=None):
        self._summary = existing_summary
        self._milestone_number = existing_milestone
        self.created_milestones: list[str] = []
        self.milestone_assignments: dict[int, str] = {}
        self.created_issues: list[dict] = []
        self.label_calls: list[tuple[int, list[str], list[str]]] = []
        self._next_number = 100

    def get_sub_issues_summary(self, issue_number):
        return self._summary

    def find_milestone_by_title(self, title):
        return self._milestone_number

    def create_milestone(self, title):
        self.created_milestones.append(title)
        self._milestone_number = 1
        return 1

    def set_milestone(self, issue_number, title):
        self.milestone_assignments[issue_number] = title

    def create_issue(self, title, body, labels_, *, parent=None, milestone=None):
        from tools.kanban_sync.github_client import IssueState
        number = self._next_number
        self._next_number += 1
        self.created_issues.append({
            "number": number, "title": title, "parent": parent, "milestone": milestone,
        })
        return IssueState(number=number, open=True, labels=frozenset(labels_))

    def set_labels(self, number, add, remove):
        self.label_calls.append((number, add, remove))


_CANONICAL_PLAN = """# Some Plan

### Task 1: First thing

body

### Task 2: Second thing

more body
"""


def test_decompose_plan_skips_when_already_decomposed():
    client = _FakeDecomposeClient(existing_summary=(1, 3))

    result = decompose_plan("x.md", _CANONICAL_PLAN, 42, client, dry_run=False)

    assert result == {"skipped": "already decomposed", "existing_sub_issues": 3}
    assert client.created_issues == []


def test_decompose_plan_creates_milestone_and_assigns_to_parent():
    client = _FakeDecomposeClient()

    result = decompose_plan("2026-08-27-x.md", _CANONICAL_PLAN, 42, client, dry_run=False)

    assert result["milestone"] == "2026-08-27-x.md"
    assert client.created_milestones == ["2026-08-27-x.md"]
    assert client.milestone_assignments[42] == "2026-08-27-x.md"


def test_decompose_plan_reuses_an_existing_milestone_instead_of_creating_a_duplicate():
    client = _FakeDecomposeClient(existing_milestone=9)

    decompose_plan("2026-08-27-x.md", _CANONICAL_PLAN, 42, client, dry_run=False)

    assert client.created_milestones == []


def test_decompose_plan_creates_one_sub_issue_per_canonical_task():
    client = _FakeDecomposeClient()

    result = decompose_plan("x.md", _CANONICAL_PLAN, 42, client, dry_run=False)

    assert result["tasks_found"] == 2
    assert len(client.created_issues) == 2
    assert client.created_issues[0]["title"] == "Task 1: First thing"
    assert client.created_issues[0]["parent"] == 42
    assert client.created_issues[0]["milestone"] == "x.md"
    assert client.created_issues[1]["title"] == "Task 2: Second thing"


def test_decompose_plan_chains_depends_on_between_consecutive_tasks():
    client = _FakeDecomposeClient()

    result = decompose_plan("x.md", _CANONICAL_PLAN, 42, client, dry_run=False)

    first_number = result["sub_issues_created"][0]
    second_number = result["sub_issues_created"][1]
    assert client.label_calls == [(second_number, [f"depends-on:#{first_number}"], [])]


def test_decompose_plan_creates_no_sub_issues_for_non_canonical_plan():
    client = _FakeDecomposeClient()

    result = decompose_plan("x.md", "## T1a - not canonical\n", 42, client, dry_run=False)

    assert result["tasks_found"] == 0
    assert client.created_issues == []
    # Milestone still gets created/assigned regardless of heading convention.
    assert client.created_milestones == ["x.md"]


def test_decompose_plan_dry_run_makes_no_mutating_calls():
    client = _FakeDecomposeClient()

    result = decompose_plan("x.md", _CANONICAL_PLAN, 42, client, dry_run=True)

    assert result["tasks_found"] == 2
    assert client.created_milestones == []
    assert client.milestone_assignments == {}
    assert client.created_issues == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ddev exec -s fastapi python -m pytest tests/test_kanban_sync_plan_tasks.py -k decompose -v`
Expected: FAIL with `ImportError: cannot import name 'decompose_plan'`

- [ ] **Step 3: Write the implementation**

Add to `tools/kanban_sync/plan_tasks.py`:

```python
from tools.kanban_sync import labels


def decompose_plan(
    plan_filename: str,
    plan_text: str,
    parent_number: int,
    client,
    *,
    dry_run: bool,
) -> dict:
    """One-time action (spec §4.2): assigns a milestone (create-or-reuse,
    titled after the plan filename) to the plan's parent issue, and - only
    if the plan uses the canonical "### Task N:" heading convention -
    creates one sub-issue per task with depends-on chaining between
    consecutive tasks. Safe to call repeatedly: skips entirely if the
    parent already has sub-issues (subIssuesSummary.total > 0), since a
    plan's task list is fixed once approved (spec §4.2's own reasoning for
    why this is one-time, not ongoing reconciliation)."""
    completed, total = client.get_sub_issues_summary(parent_number)
    if total > 0:
        return {"skipped": "already decomposed", "existing_sub_issues": total}

    milestone_title = plan_filename
    milestone_number = client.find_milestone_by_title(milestone_title)
    if milestone_number is None and not dry_run:
        client.create_milestone(milestone_title)

    if not dry_run:
        client.set_milestone(parent_number, milestone_title)

    tasks = parse_canonical_tasks(plan_text)
    created_sub_issues: list[int] = []
    if tasks and not dry_run:
        previous_number: int | None = None
        for task_number, task_title in tasks:
            issue = client.create_issue(
                f"Task {task_number}: {task_title}",
                f"## Context\nPart of `docs/superpowers/plans/{plan_filename}`.",
                [labels.STATUS_CLAIMABLE, labels.TYPE_PLAN_TASK],
                parent=parent_number,
                milestone=milestone_title,
            )
            created_sub_issues.append(issue.number)
            if previous_number is not None:
                client.set_labels(issue.number, [f"depends-on:#{previous_number}"], [])
            previous_number = issue.number

    return {
        "milestone": milestone_title,
        "tasks_found": len(tasks),
        "sub_issues_created": created_sub_issues,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ddev exec -s fastapi python -m pytest tests/test_kanban_sync_plan_tasks.py -v`
Expected: PASS (all 11 tests — 4 from Task 1, 7 new)

- [ ] **Step 5: Commit**

```bash
git add tools/kanban_sync/plan_tasks.py tests/test_kanban_sync_plan_tasks.py
git commit -m "feat: add decompose_plan - one-time milestone + sub-issue creation"
```

---

### Task 5: `sync.py` — close a plan parent once all sub-issues are done

**Files:**
- Modify: `tools/kanban_sync/sync.py`
- Test: `tests/test_kanban_sync_sync.py`

**Interfaces:**
- Consumes: `client.list_open_by_label` (existing, Task 5 of the prior
  plan), `client.get_sub_issues_summary` (Task 3 above), `client.close_issue`
  (existing).
- Produces: `close_completed_plan_parents(client, *, dry_run: bool) ->
  SyncReport`.

- [ ] **Step 1: Write the failing tests**

Extend `FakeGithubClient` in `tests/test_kanban_sync_sync.py`:

```python
    def get_sub_issues_summary(self, issue_number):
        return self.sub_issues_summary.get(issue_number, (0, 0))
```

Add `self.sub_issues_summary: dict[int, tuple[int, int]] = {}` to
`FakeGithubClient.__init__` (alongside the other dict fields).

Update the import line:

```python
from tools.kanban_sync.sync import close_completed_plan_parents, close_stale_worktree_issues, reconcile, sync_pass_one
```

**Pre-dispatch fix (verified against current `tests/test_kanban_sync_sync.py`
before this task was dispatched):** the existing `_item()` helper hardcodes
`type_label=labels.TYPE_TRACKING` regardless of its `kind` argument — passing
`kind="plan"` alone does NOT produce a `TYPE_PLAN_TASK`-labeled issue, since
`type_label` is a plain dataclass field with no derivation from `kind`
(`tools/kanban_sync/models.py`'s `SyncItem` has no such logic either). Without
this fix, `_plan_item()`'s created issue would never carry `TYPE_PLAN_TASK`,
so `client.list_open_by_label(labels.TYPE_PLAN_TASK)` would never return it
and every test below would fail on `assert report.closed`. Fix: `_item()`
gets a new `type_label` keyword parameter defaulting to the existing
`labels.TYPE_TRACKING` (every current call site is unaffected), and
`_plan_item()` passes `type_label=labels.TYPE_PLAN_TASK` explicitly. Add this
one-line change to `_item()`'s signature as parts of this task's Step 1
(the function itself already exists in the file, only its signature changes):

```python
def _item(kind="track", key="A", *, title="Track A", status=labels.STATUS_CLAIMABLE,
          done=False, depends_on=(), phase=None, type_label=labels.TYPE_TRACKING):
    return SyncItem(
        kind=kind, key=key, title=title, status_label=status,
        type_label=type_label, context_body="## Context\nx",
        acceptance_criteria=("done when x happens",), done=done,
        depends_on_keys=depends_on, phase_label=phase,
    )
```

Add tests (near the `close_stale_worktree_issues` tests):

```python
def _plan_item(key="x.md", *, title="Plan: x.md"):
    return _item(kind="plan", key=key, title=title, status=labels.STATUS_CLAIMABLE,
                 type_label=labels.TYPE_PLAN_TASK)


def test_close_completed_plan_parents_closes_issue_whose_sub_issues_are_all_done():
    client = FakeGithubClient()
    sync_pass_one([_plan_item()], client, dry_run=False)
    (number,) = client.issues.keys()
    client.sub_issues_summary[number] = (3, 3)

    report = close_completed_plan_parents(client, dry_run=False)

    assert client.issues[number]["open"] is False
    assert report.closed


def test_close_completed_plan_parents_leaves_issue_open_when_sub_issues_incomplete():
    client = FakeGithubClient()
    sync_pass_one([_plan_item()], client, dry_run=False)
    (number,) = client.issues.keys()
    client.sub_issues_summary[number] = (2, 3)

    report = close_completed_plan_parents(client, dry_run=False)

    assert client.issues[number]["open"] is True
    assert report.closed == []


def test_close_completed_plan_parents_ignores_issue_with_no_sub_issues():
    client = FakeGithubClient()
    sync_pass_one([_plan_item()], client, dry_run=False)
    (number,) = client.issues.keys()
    client.sub_issues_summary[number] = (0, 0)

    report = close_completed_plan_parents(client, dry_run=False)

    assert client.issues[number]["open"] is True
    assert report.closed == []


def test_close_completed_plan_parents_dry_run_makes_no_mutating_calls():
    client = FakeGithubClient()
    sync_pass_one([_plan_item()], client, dry_run=False)
    (number,) = client.issues.keys()
    client.sub_issues_summary[number] = (3, 3)

    report = close_completed_plan_parents(client, dry_run=True)

    assert client.issues[number]["open"] is True
    assert report.closed  # still reported, matching every other dry-run in this file
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ddev exec -s fastapi python -m pytest tests/test_kanban_sync_sync.py -k close_completed_plan -v`
Expected: FAIL with `ImportError: cannot import name 'close_completed_plan_parents'`

- [ ] **Step 3: Write the implementation**

Extend the `SyncGithubClient` Protocol:

```python
    def get_sub_issues_summary(self, issue_number: int) -> tuple[int, int]: ...
```

Add the function (near `close_stale_worktree_issues`):

```python
def close_completed_plan_parents(client: SyncGithubClient, *, dry_run: bool) -> SyncReport:
    """Closes an open type:plan-task issue whose sub-issues (created by
    plan_tasks.decompose_plan) are all complete. GitHub never auto-closes
    a parent when its sub-issues all close (confirmed live 2026-08-27) -
    this is the mechanical check that does it. Unlike decompose_plan
    (a one-time action, spec §4.2), this runs every sync - "did the last
    sub-issue just close" is exactly the kind of drift-over-time signal
    the rest of this module already reconciles (spec §4.4)."""
    report = SyncReport(dry_run=dry_run)
    for issue in client.list_open_by_label(labels.TYPE_PLAN_TASK):
        completed, total = client.get_sub_issues_summary(issue.number)
        if total == 0 or completed < total:
            continue
        if not dry_run:
            client.close_issue(issue.number)
        report.closed.append(f"#{issue.number} all {total} sub-issues complete")
    return report
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ddev exec -s fastapi python -m pytest tests/test_kanban_sync_sync.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add tools/kanban_sync/sync.py tests/test_kanban_sync_sync.py
git commit -m "feat: add close_completed_plan_parents"
```

---

### Task 6: `__main__.py` — `decompose-plan` subcommand + wire the auto-close pass

**Files:**
- Modify: `tools/kanban_sync/__main__.py`
- Test: `tests/test_kanban_sync_main.py`

**Interfaces:**
- Consumes: `plan_tasks.decompose_plan` (Task 4), `sync.close_completed_plan_parents`
  (Task 5), `markers.build_marker`, `labels.SYNC_MARKER_KIND_PLAN`.
- Produces: `python -m tools.kanban_sync decompose-plan --plan <filename>
  [--dry-run]` CLI subcommand; `_cmd_sync` now also runs
  `close_completed_plan_parents` whenever `"plan"` is among `--sources`.

- [ ] **Step 1: Write the failing tests**

```python
def test_decompose_plan_subcommand_errors_when_plan_issue_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "GithubClient", lambda repo: _FakeNoIssueClient())
    monkeypatch.setattr(cli, "PLANS_DIR", tmp_path)
    (tmp_path / "x.md").write_text("# X\n")

    with pytest.raises(SystemExit) as exc:
        cli.main(["decompose-plan", "--plan", "x.md"])

    assert exc.value.code == 1


class _FakeNoIssueClient:
    def find_by_marker(self, marker):
        return None


def test_cmd_sync_runs_close_completed_plan_parents_when_plan_in_sources(monkeypatch):
    calls = []
    _patch_sync_pipeline(monkeypatch, items=[], live_branches=None)
    monkeypatch.setattr(
        cli, "close_completed_plan_parents",
        lambda client, dry_run: calls.append(True) or _FakeReport(),
    )

    cli._cmd_sync(argparse.Namespace(sources="plan", dry_run=False, plan_classifications=None))

    assert calls == [True]


def test_cmd_sync_skips_close_completed_plan_parents_when_plan_not_in_sources(monkeypatch):
    calls = []
    _patch_sync_pipeline(monkeypatch, items=[], live_branches=None)
    monkeypatch.setattr(
        cli, "close_completed_plan_parents",
        lambda client, dry_run: calls.append(True) or _FakeReport(),
    )

    cli._cmd_sync(argparse.Namespace(sources="roadmap", dry_run=False, plan_classifications=None))

    assert calls == []
```

Note: `_patch_sync_pipeline`'s `_collect_items` fake takes `(sources,
plan_classifications)` and returns `(items, live_branches)` — its
signature and `_FakeReport`/`_patch_sync_pipeline` already exist from the
prior plan's Task 7. Reuse them, don't redefine.

- [ ] **Step 2: Run tests to verify they fail**

Run: `ddev exec -s fastapi python -m pytest tests/test_kanban_sync_main.py -k "decompose_plan_subcommand or close_completed_plan_parents" -v`
Expected: FAIL — `decompose-plan` subcommand not registered (`argparse`
error / `AttributeError` on `close_completed_plan_parents`).

- [ ] **Step 3: Write the implementation**

Update imports:

```python
from tools.kanban_sync import labels
from tools.kanban_sync.github_client import GithubClient
from tools.kanban_sync.markers import build_marker
from tools.kanban_sync.models import SyncItem
from tools.kanban_sync.plan_tasks import decompose_plan
from tools.kanban_sync.sources_plan import build_plan_items, list_plan_candidates
from tools.kanban_sync.sources_roadmap import parse_roadmap_items
from tools.kanban_sync.sources_tracks import parse_track_items
from tools.kanban_sync.sources_worktree import (
    collect_worktree_items, live_worktree_branches, parse_worktree_list,
)
from tools.kanban_sync.sync import (
    close_completed_plan_parents, close_stale_worktree_issues, reconcile,
)
```

Add the new subcommand handler (near `_cmd_plan_candidates`):

```python
def _cmd_decompose_plan(args: argparse.Namespace) -> None:
    _check_project_scope()
    client = GithubClient(REPO)
    marker = build_marker(labels.SYNC_MARKER_KIND_PLAN, args.plan)
    existing = client.find_by_marker(marker)
    if existing is None:
        print(
            f"error: no tracked issue found for plan {args.plan!r} - "
            f"run `sync --sources plan` first",
            file=sys.stderr,
        )
        sys.exit(1)
    plan_path = PLANS_DIR / args.plan
    if not plan_path.exists():
        print(f"error: plan doc not found: {plan_path}", file=sys.stderr)
        sys.exit(1)
    result = decompose_plan(
        args.plan, plan_path.read_text(), existing.number, client, dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))
```

Extend `_cmd_sync` (after the existing `live_branches is not None` block):

```python
    if "plan" in sources:
        plan_close_report = close_completed_plan_parents(client, dry_run=args.dry_run)
        report.closed += plan_close_report.closed
```

Register the subcommand in `main`:

```python
    decompose_parser = sub.add_parser("decompose-plan", help="create milestone + task sub-issues for one plan")
    decompose_parser.add_argument("--plan", required=True, help="plan doc filename, e.g. 2026-08-27-x.md")
    decompose_parser.add_argument("--dry-run", action="store_true")
    decompose_parser.set_defaults(func=_cmd_decompose_plan)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ddev exec -s fastapi python -m pytest tests/test_kanban_sync_main.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add tools/kanban_sync/__main__.py tests/test_kanban_sync_main.py
git commit -m "feat: add decompose-plan subcommand, wire close_completed_plan_parents into sync"
```

---

### Task 7: Full verification + live smoke test

**Files:** none (verification only)

- [ ] **Step 1: Run the complete kanban_sync test suite**

Run: `ddev exec -s fastapi python -m pytest tests/test_kanban_sync*.py -v`
Expected: PASS (all tests across all 9 test files)

- [ ] **Step 2: `tools.quality_audit`**

Run: `python3 -m tools.quality_audit` (host, not ddev — matches this
session's established convention for this specific tool)
Expected: exit 0, any new findings are `severity: info` (api-usage
inventory confirming the new methods are called), same shape as the
prior plan's own Task 12 result.

- [ ] **Step 3: Live smoke test against a real, disposable plan doc**

Do NOT run this against any of this repo's own real numbered plans yet.
Create a throwaway plan doc and a throwaway tracked issue for it first:

```bash
cat > /tmp/test-plan.md << 'EOF'
# Test Plan

### Task 1: First throwaway task

### Task 2: Second throwaway task
EOF
gh issue create --title "Plan: test-plan-DELETE-ME.md" --body "<!-- autotrade-sync: plan:test-plan-DELETE-ME.md -->" --label "status:claimable" --label "type:plan-task"
```

Note the returned issue number, then run (from the repo root, on host —
`gh` isn't in the ddev container):

```bash
mkdir -p /tmp/fake-plans-dir
cp /tmp/test-plan.md /tmp/fake-plans-dir/test-plan-DELETE-ME.md
python3 -c "
from tools.kanban_sync.__main__ import PLANS_DIR
"
```

Since `PLANS_DIR` is a module-level constant pointing at
`docs/superpowers/plans`, the simplest real end-to-end path is to
temporarily place the throwaway doc there instead:

```bash
cp /tmp/test-plan.md docs/superpowers/plans/test-plan-DELETE-ME.md
python3 -m tools.kanban_sync decompose-plan --plan test-plan-DELETE-ME.md
```

Expected output: JSON showing `"milestone": "test-plan-DELETE-ME.md"`,
`"tasks_found": 2`, `"sub_issues_created": [<two numbers>]`.

Verify live:

```bash
gh issue view <parent-number> --json subIssuesSummary,milestone
```

Expected: `subIssuesSummary.total == 2`, `milestone.title ==
"test-plan-DELETE-ME.md"`.

Close both sub-issues, then confirm the parent auto-closes on the next
sync:

```bash
gh issue close <sub-issue-1-number>
gh issue close <sub-issue-2-number>
python3 -m tools.kanban_sync sync --sources plan --plan-classifications /tmp/empty-classifications.json
```

(`/tmp/empty-classifications.json` can be `{}` — the plan source itself
finds nothing new to classify; `close_completed_plan_parents` runs
regardless since `"plan"` is in `--sources`.)

```bash
gh issue view <parent-number> --json state
```

Expected: `"state": "CLOSED"`.

- [ ] **Step 4: Clean up every throwaway artifact**

```bash
gh issue delete <parent-number> --yes
gh issue delete <sub-issue-1-number> --yes
gh issue delete <sub-issue-2-number> --yes
gh api -X DELETE repos/thesneakattack/kalshi-whale-poc/milestones/<milestone-number>
rm docs/superpowers/plans/test-plan-DELETE-ME.md
rm -f /tmp/test-plan.md /tmp/empty-classifications.json
rm -rf /tmp/fake-plans-dir
```

Confirm `git status` is clean of the throwaway plan doc before the final
commit/push (it must never be committed).

- [ ] **Step 5: Push, open a PR, confirm Woodpecker green, merge**

Per `.claude/rules/branching-and-ci.md`: push the branch, `gh pr create`,
wait for the 5 required `ci/woodpecker/pr/*` checks, then
`gh pr merge --merge --delete-branch`.
