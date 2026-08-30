from tools.kanban_sync.plan_tasks import parse_canonical_tasks, decompose_plan
from tools.kanban_sync import labels

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


def test_parse_canonical_tasks_does_not_capture_subsequent_paragraphs_as_titles():
    r"""Regression test: a title-less canonical heading (e.g. '### Task 3:' with
    nothing after it) must NOT capture text from the following paragraph as the
    title. The old regex used \s* which matches newlines, causing cross-line
    capture. The fix uses [ \t]* (horizontal whitespace only), which prevents
    capturing into subsequent lines."""
    text = """# Plan

### Task 3:

Actual body paragraph that should never be treated as a title.

### Task 4: real title

More body text.
"""
    result = parse_canonical_tasks(text)
    # Task 3 has no inline title, so it should NOT appear in results.
    # Task 4 has a real title and should be correctly extracted.
    assert result == [(4, "real title")]


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


_THREE_TASK_PLAN = """# Some Plan

### Task 1: First thing

body

### Task 2: Second thing

more body

### Task 3: Third thing

even more body
"""


def test_decompose_plan_start_from_task_skips_earlier_tasks():
    """Retroactively decomposing an in-flight plan (spec: an already-
    partially-executed plan like a track's canonical doc) must not create
    misleadingly-open sub-issues for tasks already known to be done."""
    client = _FakeDecomposeClient()

    result = decompose_plan(
        "x.md", _THREE_TASK_PLAN, 42, client, dry_run=False, start_from_task=2,
    )

    assert result["tasks_found"] == 2
    assert len(client.created_issues) == 2
    assert client.created_issues[0]["title"] == "Task 2: Second thing"
    assert client.created_issues[1]["title"] == "Task 3: Third thing"


def test_decompose_plan_start_from_task_chains_from_the_first_included_task():
    """The first included task must not carry a dangling depends-on
    reference to a skipped, never-created earlier task's issue."""
    client = _FakeDecomposeClient()

    result = decompose_plan(
        "x.md", _THREE_TASK_PLAN, 42, client, dry_run=False, start_from_task=2,
    )

    first_number = result["sub_issues_created"][0]
    second_number = result["sub_issues_created"][1]
    assert client.label_calls == [(second_number, [f"depends-on:#{first_number}"], [])]


def test_decompose_plan_start_from_task_defaults_to_one_backward_compatible():
    client = _FakeDecomposeClient()

    result = decompose_plan("x.md", _CANONICAL_PLAN, 42, client, dry_run=False)

    assert result["tasks_found"] == 2


_FENCED_BACKTICK_HEREDOC = """# Some Plan

### Task 1: Real task

Live smoke test - create a throwaway plan doc first:

```bash
cat > /tmp/test-plan.md << 'HEREDOC'
# Test Plan

### Task 2: Fenced example, not a task
HEREDOC
```

### Task 3: Another real task

body
"""

_FENCED_TILDE = """# Some Plan

### Task 1: Real task

~~~markdown
### Task 2: Fenced example, not a task
~~~

body
"""

_FENCED_NESTED_LONGER_OUTER = """# Some Plan

### Task 1: Real task

````markdown
```python
### Task 2: Fenced example, not a task
```
### Task 3: Still inside the outer four-backtick fence
````

### Task 4: Real task after the outer fence closes

body
"""

_FENCED_UNCLOSED = """# Some Plan

### Task 1: Real task

```python
### Task 2: Inside a fence that never closes
"""


def test_parse_canonical_tasks_ignores_task_heading_inside_a_backtick_fence():
    """Regression for issue #226: the milestones/sub-issues plan documents
    the `### Task N:` convention inside fenced examples (a bash heredoc,
    python test-fixture strings) and decompose_plan turned six of those
    example lines into real sub-issues (#174, #175, #179, #180, #184,
    #185). Fenced lines are not headings and must never parse as tasks;
    a heading after the fence closes still must."""
    assert parse_canonical_tasks(_FENCED_BACKTICK_HEREDOC) == [
        (1, "Real task"),
        (3, "Another real task"),
    ]


def test_parse_canonical_tasks_ignores_task_heading_inside_a_tilde_fence():
    assert parse_canonical_tasks(_FENCED_TILDE) == [(1, "Real task")]


def test_parse_canonical_tasks_only_closes_a_fence_on_a_fence_at_least_as_long():
    """CommonMark: a fence closes only on the same character with at least
    as many of them, so a four-backtick fence wrapping a three-backtick
    example (how a plan shows a fenced example *of* a fenced block) is one
    block - the inner ``` lines are content, not a premature close."""
    assert parse_canonical_tasks(_FENCED_NESTED_LONGER_OUTER) == [
        (1, "Real task"),
        (4, "Real task after the outer fence closes"),
    ]


def test_parse_canonical_tasks_treats_an_unclosed_fence_as_running_to_end_of_document():
    """CommonMark: no closing fence means the block runs to the end of the
    document - same reading the awk fence-toggle used to verify #226 gives."""
    assert parse_canonical_tasks(_FENCED_UNCLOSED) == [(1, "Real task")]
