# Kanban Board Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `tools/kanban_sync`, a deterministic, independently-testable
reconciler that reflects worktrees, `ROADMAP.md`, `active-tracks-board.md`'s
tracks, and open numbered plan docs onto real GitHub Issues — built to the
`github-issues-kanban` skill's actual claimable-work contract — plus the two
call sites that trigger it (a new on-demand skill, and a step folded into
the existing `/checkpoint` skill).

**Architecture:** A pure-Python core under `tools/kanban_sync/` (source
parsers → `SyncItem` list → a two-pass reconciler against an injectable
`gh`-CLI wrapper) that never performs a real subprocess/network call in its
own test suite. The one non-deterministic step — classifying which numbered
plan docs are still open, since their own checkboxes are proven unreliable
(spec §5) — is deliberately left to the new skill's judgment, not the CLI.

**Tech Stack:** Python 3.13 (stdlib only — `argparse`, `dataclasses`, `json`,
`re`, `subprocess`, `pathlib`, `datetime`, `typing`), `pytest`, the `gh` CLI
(already authenticated, `project` scope confirmed present), the installed
`github-issues-kanban` Claude Code skill's label/event conventions. No new
dependency.

**Spec:** `docs/superpowers/specs/2026-08-26-kanban-board-sync-design.md`
(read in full before starting — this plan implements it section by section;
the Global Constraints below quote its load-bearing decisions verbatim, but
the spec has the reasoning).

## Global Constraints

1. **Never write to a repo file.** `ROADMAP.md`, every `docs/superpowers/
   plans/*.md`, and `active-tracks-board.md` are read-only sources. Nothing
   in this plan opens one of them for writing. (Spec §2, §9.)
2. **Never reopen a manually-closed issue.** If a source item is still open
   but its GitHub issue is closed, post a `sync-mismatch` event comment and
   leave it closed — do not call `gh issue reopen` anywhere in this plan.
   (Spec §9.)
3. **Use the kanban skill's real label constants, not invented ones.**
   `status:*` values come verbatim from the installed skill's
   `assets/label-scheme.json`
   (`status:claimable`/`claimed`/`in-progress`/`ready-for-review`/`blocked`/
   `done`). The only repo-local additions are `type:*` (already defined by
   `docs/superpowers/specs/2026-08-26-autonomous-engineering-mode-design.md`
   Task 15 as a repo-local extension) and a new `type:tracking` this plan
   introduces for worktree issues. (Spec §7.)
4. **`## Acceptance criteria` is always populated; `## Scope` only when
   confident.** The kanban skill refuses to dispatch an issue with no
   acceptance criteria — every `SyncItem` this plan produces carries at
   least one. `## Scope` is emitted only for items with a knowably-narrow
   target; when omitted, `autonomous-engineering-mode`'s existing
   fail-closed behavior (no declared scope → not claimable) is what keeps
   that safe, not anything built here. (Spec §7.)
5. **`active-tracks-board.md` syncs at track granularity — one issue per
   track (3 today: A, B, C), never per sub-step.** Track C's issue carries
   `depends-on:#<Track A issue>` and `depends-on:#<Track B issue>` because
   its own heading says "hard-gated." This resolves an ambiguity in the
   spec's §8 prose in favor of §5's explicit granularity table. (Spec §5,
   §8; this plan's own resolution documented here per writing-plans'
   no-silent-deviation rule.)
6. **Plan docs sync at plan granularity, and only when not done.** One
   issue per `docs/superpowers/plans/*.md` file not already referenced by
   `active-tracks-board.md`, and only for files classified `in-progress` or
   `not-started`. A `done` classification means no issue is created at all
   (never create-then-close). Classification is a judgment call made by the
   `kanban-board-sync` skill (Task 11), not inferred from the plan's own
   `- [ ]` checkboxes — measured unreliable in spec §5. (Spec §5.)
7. **No Projects V2 board-attachment code in this plan.** This plan only
   creates/updates/closes Issues with the right labels. Attaching them to a
   Projects V2 board view is the existing `github-issues-kanban` skill's own
   Generate/Triage capability — an explicit non-goal of the spec (§2), not
   rebuilt here.
8. **No CI/cron trigger.** The only two entry points are the on-demand
   skill (Task 11) and the `/checkpoint` step (Task 12) — no Woodpecker job,
   no git hook, no privileged GitHub-write credential added to CI. (Spec
   §10.)
9. **Every `gh`/`git` subprocess call goes through an injectable runner.**
   No test in this plan may perform a real `gh`/`git`/network call — tests
   use a fake runner (`tests/support/fake_gh_runner.py`, Task 1) or a fake
   in-memory `GithubClient` double. (Spec §13.)
10. **Repository:** `thesneakattack/kalshi-whale-poc`, matching every other
    cross-referenced doc in this repo.
11. Run tests via `ddev exec -s fastapi python3 -m pytest -q <path>` per
    this project's existing dev workflow (`CLAUDE.md`'s "Dev workflow"
    section) — not a bare host `pytest`.

---

### Task 1: Package scaffold, label constants, shared data model, fake runner

**Files:**
- Create: `tools/kanban_sync/__init__.py`
- Create: `tools/kanban_sync/labels.py`
- Create: `tools/kanban_sync/models.py`
- Create: `tests/support/fake_gh_runner.py`
- Test: `tests/test_kanban_sync_labels.py`
- Test: `tests/test_kanban_sync_models.py`

**Interfaces:**
- Produces: `labels.STATUS_CLAIMABLE/CLAIMED/IN_PROGRESS/READY_FOR_REVIEW/BLOCKED/DONE: str`,
  `labels.ALL_STATUS_LABELS: frozenset[str]`,
  `labels.TYPE_BUG/INVESTIGATION/PLAN_TASK/FEATURE/DESIGN/TRACKING: str`,
  `labels.SYNC_MARKER_KIND_WORKTREE/ROADMAP/TRACK/PLAN: str` — every later
  task imports from here instead of writing label strings inline.
- Produces: `models.SyncItem` (frozen dataclass: `kind: str, key: str,
  title: str, status_label: str, type_label: str, context_body: str,
  acceptance_criteria: tuple[str, ...], scope_paths: tuple[str, ...] = (),
  depends_on_keys: tuple[tuple[str, str], ...] = (), done: bool = False`)
  and `models.SyncReport` (dataclass: `created: list[str], updated:
  list[str], closed: list[str], flagged_mismatches: list[str], dry_run:
  bool = False`, all defaulting to empty lists via `field(default_factory=list)`).
- Produces: `tests.support.fake_gh_runner.FakeRunner` — a generic fake for
  any `Callable[[Sequence[str]], subprocess.CompletedProcess[str]]`
  (used for both `git` and `gh` invocations in later tasks).

- [ ] **Step 1: Write the failing tests for labels.py**

```python
# tests/test_kanban_sync_labels.py
from tools.kanban_sync import labels


def test_status_labels_match_kanban_skill_scheme():
    assert labels.STATUS_CLAIMABLE == "status:claimable"
    assert labels.STATUS_CLAIMED == "status:claimed"
    assert labels.STATUS_IN_PROGRESS == "status:in-progress"
    assert labels.STATUS_READY_FOR_REVIEW == "status:ready-for-review"
    assert labels.STATUS_BLOCKED == "status:blocked"
    assert labels.STATUS_DONE == "status:done"


def test_all_status_labels_contains_exactly_six_values():
    assert labels.ALL_STATUS_LABELS == {
        "status:claimable", "status:claimed", "status:in-progress",
        "status:ready-for-review", "status:blocked", "status:done",
    }


def test_type_labels_include_repo_local_tracking_extension():
    assert labels.TYPE_TRACKING == "type:tracking"
    assert labels.TYPE_PLAN_TASK == "type:plan-task"


def test_sync_marker_kinds_are_lowercase_single_words():
    for kind in (
        labels.SYNC_MARKER_KIND_WORKTREE, labels.SYNC_MARKER_KIND_ROADMAP,
        labels.SYNC_MARKER_KIND_TRACK, labels.SYNC_MARKER_KIND_PLAN,
    ):
        assert kind == kind.lower()
        assert " " not in kind
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_kanban_sync_labels.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.kanban_sync'`

- [ ] **Step 3: Implement labels.py and the package `__init__.py`**

```python
# tools/kanban_sync/__init__.py
"""Kanban board sync (docs/superpowers/specs/2026-08-26-kanban-board-sync-
design.md): reflects worktrees, ROADMAP.md, active-tracks-board.md's
tracks, and open numbered plan docs onto real GitHub Issues, built to the
installed github-issues-kanban skill's claimable-work contract. Entry
point: `python -m tools.kanban_sync`.
"""
```

```python
# tools/kanban_sync/labels.py
"""Canonical label constants (docs/superpowers/specs/2026-08-26-kanban-
board-sync-design.md §7). status:* values are copied verbatim from the
installed github-issues-kanban skill's assets/label-scheme.json - do not
invent new status values. type:* is this repo's own extension, already
established by docs/superpowers/specs/2026-08-26-autonomous-engineering-
mode-design.md (Task 15's note); type:tracking is this plan's own addition
to that same repo-local family, for issues that track a worktree rather
than represent claimable work.
"""
from __future__ import annotations

STATUS_CLAIMABLE = "status:claimable"
STATUS_CLAIMED = "status:claimed"
STATUS_IN_PROGRESS = "status:in-progress"
STATUS_READY_FOR_REVIEW = "status:ready-for-review"
STATUS_BLOCKED = "status:blocked"
STATUS_DONE = "status:done"

ALL_STATUS_LABELS = frozenset({
    STATUS_CLAIMABLE, STATUS_CLAIMED, STATUS_IN_PROGRESS,
    STATUS_READY_FOR_REVIEW, STATUS_BLOCKED, STATUS_DONE,
})

TYPE_BUG = "type:bug"
TYPE_INVESTIGATION = "type:investigation"
TYPE_PLAN_TASK = "type:plan-task"
TYPE_FEATURE = "type:feature"
TYPE_DESIGN = "type:design"
TYPE_TRACKING = "type:tracking"

SYNC_MARKER_KIND_WORKTREE = "worktree"
SYNC_MARKER_KIND_ROADMAP = "roadmap"
SYNC_MARKER_KIND_TRACK = "track"
SYNC_MARKER_KIND_PLAN = "plan"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_kanban_sync_labels.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Write the failing tests for models.py**

```python
# tests/test_kanban_sync_models.py
from tools.kanban_sync import labels
from tools.kanban_sync.models import SyncItem, SyncReport


def test_sync_item_defaults():
    item = SyncItem(
        kind="track", key="A", title="Track A",
        status_label=labels.STATUS_CLAIMABLE, type_label=labels.TYPE_TRACKING,
        context_body="## Context\nx", acceptance_criteria=("x",),
    )
    assert item.scope_paths == ()
    assert item.depends_on_keys == ()
    assert item.done is False


def test_sync_item_is_frozen():
    item = SyncItem(
        kind="track", key="A", title="Track A",
        status_label=labels.STATUS_CLAIMABLE, type_label=labels.TYPE_TRACKING,
        context_body="x", acceptance_criteria=("x",),
    )
    try:
        item.title = "changed"
        assert False, "expected FrozenInstanceError"
    except Exception as exc:
        assert type(exc).__name__ == "FrozenInstanceError"


def test_sync_report_defaults_to_empty_lists_independently():
    a = SyncReport()
    b = SyncReport()
    a.created.append("x")
    assert b.created == []
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_kanban_sync_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.kanban_sync.models'`

- [ ] **Step 7: Implement models.py**

```python
# tools/kanban_sync/models.py
"""Shared data model for one syncable unit of work (spec §5-§9). Every
source parser produces a list[SyncItem]; sync.py consumes it uniformly
regardless of which source produced it.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SyncItem:
    kind: str  # one of labels.SYNC_MARKER_KIND_*
    key: str  # stable within kind; must survive line-number/edit churn
    title: str
    status_label: str  # labels.STATUS_*
    type_label: str  # labels.TYPE_*
    context_body: str
    acceptance_criteria: tuple[str, ...]
    scope_paths: tuple[str, ...] = ()
    depends_on_keys: tuple[tuple[str, str], ...] = ()  # (kind, key) pairs
    done: bool = False


@dataclass
class SyncReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    closed: list[str] = field(default_factory=list)
    flagged_mismatches: list[str] = field(default_factory=list)
    dry_run: bool = False
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_kanban_sync_models.py -v`
Expected: PASS (3 tests)

- [ ] **Step 9: Add the shared FakeRunner test double (no test file of its own — it's test infrastructure, exercised by later tasks)**

```python
# tests/support/fake_gh_runner.py
"""Generic fake for a `Callable[[Sequence[str]], subprocess.CompletedProcess[str]]`
runner - used to test both git and gh CLI wrappers in tools/kanban_sync
without ever shelling out for real (spec §13: "no live GitHub calls
required for the test suite itself").
"""
from __future__ import annotations

import subprocess
from typing import Sequence


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self._responses: list[subprocess.CompletedProcess] = []

    def queue(self, stdout: str, returncode: int = 0, stderr: str = "") -> None:
        self._responses.append(
            subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)
        )

    def __call__(self, args: Sequence[str]) -> subprocess.CompletedProcess:
        self.calls.append(list(args))
        if not self._responses:
            raise AssertionError(f"FakeRunner called with {list(args)} but no response was queued")
        return self._responses.pop(0)
```

- [ ] **Step 10: Sanity-import the fake runner (proves it's importable from tests/)**

Run: `ddev exec -s fastapi python3 -c "from tests.support.fake_gh_runner import FakeRunner; r = FakeRunner(); r.queue('ok'); print(r(['x']).stdout)"`
Expected: prints `ok`

- [ ] **Step 11: Commit**

```bash
git add tools/kanban_sync/__init__.py tools/kanban_sync/labels.py tools/kanban_sync/models.py \
        tests/support/fake_gh_runner.py tests/test_kanban_sync_labels.py tests/test_kanban_sync_models.py
git commit -m "feat: scaffold tools/kanban_sync with label constants and shared data model"
```

---

### Task 2: Sync marker and title/slug utilities

**Files:**
- Create: `tools/kanban_sync/markers.py`
- Test: `tests/test_kanban_sync_markers.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `markers.build_marker(kind: str, key: str) -> str`,
  `markers.parse_marker(text: str) -> tuple[str, str] | None`,
  `markers.slugify(text: str, max_len: int = 60) -> str`,
  `markers.extract_roadmap_title(bullet_text: str) -> str` — used by
  `sources_roadmap.py` (Task 5), `sources_worktree.py` (Task 4), and
  `sync.py` (Task 7) to embed/find the `<!-- autotrade-sync: kind:key -->`
  marker (spec §6).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_kanban_sync_markers.py
from tools.kanban_sync.markers import (
    build_marker, extract_roadmap_title, parse_marker, slugify,
)


def test_build_marker_format():
    assert build_marker("roadmap", "shadow-mode-review") == (
        "<!-- autotrade-sync: roadmap:shadow-mode-review -->"
    )


def test_parse_marker_round_trips_with_build_marker():
    marker = build_marker("track", "A")
    assert parse_marker(marker) == ("track", "A")


def test_parse_marker_returns_none_when_absent():
    assert parse_marker("just a normal issue body, no marker here") is None


def test_parse_marker_finds_marker_inside_longer_body():
    body = (
        "## Context\nsome text\n\n"
        "<!-- autotrade-sync: plan:2026-08-25-frontend-modularization.md -->"
        "\nmore text"
    )
    assert parse_marker(body) == ("plan", "2026-08-25-frontend-modularization.md")


def test_slugify_lowercases_and_hyphenates():
    assert slugify("Shadow-mode sustained run + review") == "shadow-mode-sustained-run-review"


def test_slugify_truncates_to_max_len():
    assert len(slugify("a" * 100, max_len=60)) == 60


def test_slugify_collapses_repeated_separators():
    assert slugify("a   b---c") == "a-b-c"


def test_extract_roadmap_title_uses_bold_lead_in():
    bullet = "**Shadow-mode sustained run + review** - `services/shadow_mode.py` has never..."
    assert extract_roadmap_title(bullet) == "Shadow-mode sustained run + review"


def test_extract_roadmap_title_falls_back_to_plain_text_when_no_bold_lead_in():
    bullet = "Consider a dedicated charts module for the Advanced view."
    assert extract_roadmap_title(bullet) == bullet
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_kanban_sync_markers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.kanban_sync.markers'`

- [ ] **Step 3: Implement markers.py**

```python
# tools/kanban_sync/markers.py
"""Sync-identity marker and title/slug derivation (spec §6). The marker is
deliberately decoupled from anything that shifts under ordinary editing
(line numbers, surrounding prose) - see spec §6's reference to
.claude/rules/autonomous-quality-coordination-evidence.md's identity-
stability concern, applied here to sync sources instead of scanner
findings.
"""
from __future__ import annotations

import re

_MARKER_RE = re.compile(r"<!--\s*autotrade-sync:\s*([a-z]+):(\S+?)\s*-->")
_BOLD_RE = re.compile(r"^\*\*(.+?)\*\*")
_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")


def build_marker(kind: str, key: str) -> str:
    return f"<!-- autotrade-sync: {kind}:{key} -->"


def parse_marker(text: str) -> tuple[str, str] | None:
    match = _MARKER_RE.search(text)
    if not match:
        return None
    return match.group(1), match.group(2)


def slugify(text: str, max_len: int = 60) -> str:
    lowered = text.strip().lower()
    slug = _NON_SLUG_RE.sub("-", lowered).strip("-")
    return slug[:max_len].rstrip("-")


def extract_roadmap_title(bullet_text: str) -> str:
    """Bold lead-in if present (`**Text** rest...`), else the bullet's own
    text up to a sentence break within the first 80 chars, else the first
    80 chars verbatim."""
    stripped = bullet_text.strip()
    bold_match = _BOLD_RE.match(stripped)
    if bold_match:
        return bold_match.group(1).strip()
    period = stripped.find(". ")
    if 0 < period <= 80:
        return stripped[:period].strip()
    return stripped[:80].strip()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_kanban_sync_markers.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/kanban_sync/markers.py tests/test_kanban_sync_markers.py
git commit -m "feat: add kanban sync marker and title/slug utilities"
```

---

### Task 3: GithubClient — a thin, testable `gh` CLI wrapper

**Files:**
- Create: `tools/kanban_sync/github_client.py`
- Test: `tests/test_kanban_sync_github_client.py`

**Interfaces:**
- Consumes: `tests.support.fake_gh_runner.FakeRunner` (Task 1).
- Produces: `github_client.IssueState` (frozen dataclass: `number: int,
  open: bool, labels: frozenset[str]`), `github_client.GithubCliError`
  (exception), `github_client.GithubClient(repo: str, runner:
  Callable[[Sequence[str]], subprocess.CompletedProcess] = subprocess.run)`
  with methods `find_by_marker(marker: str) -> IssueState | None`,
  `create_issue(title: str, body: str, labels: Sequence[str]) -> IssueState`,
  `set_labels(number: int, add: Sequence[str], remove: Sequence[str]) -> None`,
  `close_issue(number: int) -> None`, `post_comment(number: int, body: str) -> None`,
  `find_pr_state(branch: str) -> str | None`. Used by `sources_worktree.py`
  (Task 4) and `sync.py` (Tasks 7-8) — later tasks depend on this exact
  method set and these exact return types.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_kanban_sync_github_client.py
import json

from tests.support.fake_gh_runner import FakeRunner
from tools.kanban_sync.github_client import GithubCliError, GithubClient

REPO = "thesneakattack/kalshi-whale-poc"


def test_find_by_marker_returns_none_when_no_results():
    runner = FakeRunner()
    runner.queue(json.dumps([]))
    client = GithubClient(REPO, runner=runner)

    result = client.find_by_marker("<!-- autotrade-sync: track:A -->")

    assert result is None
    assert runner.calls[0][:3] == ["gh", "issue", "list"]
    assert "--repo" in runner.calls[0] and REPO in runner.calls[0]


def test_find_by_marker_parses_existing_issue():
    runner = FakeRunner()
    runner.queue(json.dumps([
        {"number": 17, "state": "OPEN", "labels": [{"name": "status:claimable"}, {"name": "type:tracking"}]}
    ]))
    client = GithubClient(REPO, runner=runner)

    result = client.find_by_marker("<!-- autotrade-sync: worktree:feat/x -->")

    assert result.number == 17
    assert result.open is True
    assert result.labels == frozenset({"status:claimable", "type:tracking"})


def test_create_issue_parses_number_from_returned_url():
    runner = FakeRunner()
    runner.queue("https://github.com/thesneakattack/kalshi-whale-poc/issues/42\n")
    client = GithubClient(REPO, runner=runner)

    result = client.create_issue("Title", "Body", ["status:claimable", "type:tracking"])

    assert result.number == 42
    assert result.open is True
    create_call = runner.calls[0]
    assert create_call[:3] == ["gh", "issue", "create"]
    assert create_call.count("--label") == 2


def test_set_labels_is_a_no_op_when_nothing_to_change():
    runner = FakeRunner()
    client = GithubClient(REPO, runner=runner)

    client.set_labels(5, add=[], remove=[])

    assert runner.calls == []


def test_set_labels_sends_add_and_remove_flags():
    runner = FakeRunner()
    runner.queue("")
    client = GithubClient(REPO, runner=runner)

    client.set_labels(5, add=["status:done"], remove=["status:claimable"])

    call = runner.calls[0]
    assert call[:3] == ["gh", "issue", "edit"]
    assert "--add-label" in call and "status:done" in call
    assert "--remove-label" in call and "status:claimable" in call


def test_close_issue_calls_gh_issue_close():
    runner = FakeRunner()
    runner.queue("")
    client = GithubClient(REPO, runner=runner)

    client.close_issue(9)

    assert runner.calls[0][:3] == ["gh", "issue", "close"]
    assert "9" in runner.calls[0]


def test_post_comment_calls_gh_issue_comment_with_body():
    runner = FakeRunner()
    runner.queue("")
    client = GithubClient(REPO, runner=runner)

    client.post_comment(9, "hello world")

    call = runner.calls[0]
    assert call[:3] == ["gh", "issue", "comment"]
    assert "--body" in call and "hello world" in call


def test_find_pr_state_returns_none_when_no_pr():
    runner = FakeRunner()
    runner.queue(json.dumps([]))
    client = GithubClient(REPO, runner=runner)

    assert client.find_pr_state("feat/no-pr-yet") is None


def test_find_pr_state_returns_state_string():
    runner = FakeRunner()
    runner.queue(json.dumps([{"state": "MERGED"}]))
    client = GithubClient(REPO, runner=runner)

    assert client.find_pr_state("feat/done") == "MERGED"


def test_nonzero_returncode_raises_githubcliierror():
    runner = FakeRunner()
    runner.queue("", returncode=1, stderr="HTTP 404: Not Found")
    client = GithubClient(REPO, runner=runner)

    try:
        client.close_issue(999)
        assert False, "expected GithubCliError"
    except GithubCliError as exc:
        assert "404" in str(exc)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_kanban_sync_github_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.kanban_sync.github_client'`

- [ ] **Step 3: Implement github_client.py**

```python
# tools/kanban_sync/github_client.py
"""Thin, testable wrapper around the `gh` CLI (spec §13: every method
takes an injectable runner so no test performs a real gh/network call).
Implements exactly the primitives sync.py and sources_worktree.py need -
find an issue by its sync marker, create one, adjust labels, close it,
post a plain comment, and look up a branch's PR state. Does not implement
claim/dispatch/event-bus semantics - that's the installed github-issues-
kanban skill's job once these issues exist (spec §2 non-goals).
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from typing import Callable, Sequence

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]

_URL_NUMBER_RE = re.compile(r"/issues/(\d+)\s*$")


@dataclass(frozen=True)
class IssueState:
    number: int
    open: bool
    labels: frozenset[str]


class GithubCliError(RuntimeError):
    pass


class GithubClient:
    def __init__(self, repo: str, runner: Runner = subprocess.run) -> None:
        self._repo = repo
        self._runner = runner

    def _run(self, args: Sequence[str]) -> str:
        result = self._runner(["gh", *args, "--repo", self._repo])
        if result.returncode != 0:
            raise GithubCliError(
                f"gh {' '.join(args)} failed: {result.stderr or result.stdout}"
            )
        return result.stdout

    def find_by_marker(self, marker: str) -> IssueState | None:
        stdout = self._run([
            "issue", "list", "--search", marker, "--state", "all",
            "--json", "number,state,labels", "--limit", "1",
        ])
        results = json.loads(stdout)
        if not results:
            return None
        item = results[0]
        return IssueState(
            number=item["number"],
            open=item["state"] == "OPEN",
            labels=frozenset(label["name"] for label in item["labels"]),
        )

    def create_issue(self, title: str, body: str, labels: Sequence[str]) -> IssueState:
        args = ["issue", "create", "--title", title, "--body", body]
        for label in labels:
            args += ["--label", label]
        stdout = self._run(args)
        match = _URL_NUMBER_RE.search(stdout.strip())
        if not match:
            raise GithubCliError(f"could not parse issue number from: {stdout!r}")
        return IssueState(number=int(match.group(1)), open=True, labels=frozenset(labels))

    def set_labels(self, number: int, add: Sequence[str], remove: Sequence[str]) -> None:
        if not add and not remove:
            return
        args = ["issue", "edit", str(number)]
        for label in add:
            args += ["--add-label", label]
        for label in remove:
            args += ["--remove-label", label]
        self._run(args)

    def close_issue(self, number: int) -> None:
        self._run(["issue", "close", str(number)])

    def post_comment(self, number: int, body: str) -> None:
        self._run(["issue", "comment", str(number), "--body", body])

    def find_pr_state(self, branch: str) -> str | None:
        stdout = self._run([
            "pr", "list", "--head", branch, "--state", "all",
            "--json", "state", "--limit", "1",
        ])
        results = json.loads(stdout)
        if not results:
            return None
        return results[0]["state"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_kanban_sync_github_client.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/kanban_sync/github_client.py tests/test_kanban_sync_github_client.py
git commit -m "feat: add testable gh CLI wrapper for kanban sync"
```

---

### Task 4: Worktree source

**Files:**
- Create: `tools/kanban_sync/sources_worktree.py`
- Test: `tests/test_kanban_sync_sources_worktree.py`

**Interfaces:**
- Consumes: `labels.*`, `models.SyncItem` (Task 1); `github_client.GithubClient`,
  `github_client.IssueState` (Task 3, for typing/tests only —
  `collect_worktree_items` takes a client, not raw gh output).
- Produces: `sources_worktree.WorktreeInfo` (frozen dataclass: `path: str,
  branch: str`), `sources_worktree.parse_worktree_list(porcelain_output: str)
  -> list[WorktreeInfo]`, `sources_worktree.build_worktree_items(worktrees:
  Sequence[WorktreeInfo], pr_state_by_branch: dict[str, str | None], *,
  main_branch: str = "main") -> list[SyncItem]`,
  `sources_worktree.collect_worktree_items(porcelain_output: str, client:
  GithubClient, *, main_branch: str = "main") -> list[SyncItem]` — the last
  is what `__main__.py` (Task 9) calls directly.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_kanban_sync_sources_worktree.py
import json

from tests.support.fake_gh_runner import FakeRunner
from tools.kanban_sync import labels
from tools.kanban_sync.github_client import GithubClient
from tools.kanban_sync.sources_worktree import (
    WorktreeInfo, build_worktree_items, collect_worktree_items, parse_worktree_list,
)

PORCELAIN = """worktree /home/davidf/code/portfolio/showcase-projects/autotrade
HEAD 0714509abc
branch refs/heads/main

worktree /home/davidf/code/portfolio/showcase-projects/autotrade/.claude/worktrees/autonomous-quality-coordination
HEAD 3df08d0abc
branch refs/heads/feat/autonomous-quality-coordination
"""


def test_parse_worktree_list_extracts_path_and_branch():
    result = parse_worktree_list(PORCELAIN)

    assert result == [
        WorktreeInfo(path="/home/davidf/code/portfolio/showcase-projects/autotrade", branch="main"),
        WorktreeInfo(
            path="/home/davidf/code/portfolio/showcase-projects/autotrade/.claude/worktrees/autonomous-quality-coordination",
            branch="feat/autonomous-quality-coordination",
        ),
    ]


def test_build_worktree_items_skips_main_branch():
    worktrees = parse_worktree_list(PORCELAIN)

    items = build_worktree_items(worktrees, pr_state_by_branch={})

    assert len(items) == 1
    assert items[0].key == "feat/autonomous-quality-coordination"


def test_build_worktree_items_no_pr_yet_is_in_progress():
    worktrees = [WorktreeInfo(path="/x", branch="feat/x")]

    items = build_worktree_items(worktrees, pr_state_by_branch={"feat/x": None})

    assert items[0].status_label == labels.STATUS_IN_PROGRESS
    assert items[0].done is False
    assert items[0].type_label == labels.TYPE_TRACKING


def test_build_worktree_items_open_pr_is_ready_for_review():
    worktrees = [WorktreeInfo(path="/x", branch="feat/x")]

    items = build_worktree_items(worktrees, pr_state_by_branch={"feat/x": "OPEN"})

    assert items[0].status_label == labels.STATUS_READY_FOR_REVIEW
    assert items[0].done is False


def test_build_worktree_items_merged_pr_is_done():
    worktrees = [WorktreeInfo(path="/x", branch="feat/x")]

    items = build_worktree_items(worktrees, pr_state_by_branch={"feat/x": "MERGED"})

    assert items[0].status_label == labels.STATUS_DONE
    assert items[0].done is True


def test_collect_worktree_items_queries_pr_state_per_branch():
    runner = FakeRunner()
    runner.queue(json.dumps([{"state": "OPEN"}]))  # find_pr_state for feat/autonomous-quality-coordination
    client = GithubClient("thesneakattack/kalshi-whale-poc", runner=runner)

    items = collect_worktree_items(PORCELAIN, client)

    assert len(items) == 1
    assert items[0].status_label == labels.STATUS_READY_FOR_REVIEW
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_kanban_sync_sources_worktree.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.kanban_sync.sources_worktree'`

- [ ] **Step 3: Implement sources_worktree.py**

```python
# tools/kanban_sync/sources_worktree.py
"""Worktree source (spec §5's first row): one SyncItem per active,
non-main git worktree. Status is derived mechanically from real branch/PR
state, never asked for as free text anywhere.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from tools.kanban_sync import labels
from tools.kanban_sync.github_client import GithubClient
from tools.kanban_sync.models import SyncItem

_ENTRY_RE = re.compile(
    r"worktree (?P<path>\S+)\n"
    r"HEAD (?P<sha>\S+)\n"
    r"branch refs/heads/(?P<branch>\S+)",
)


@dataclass(frozen=True)
class WorktreeInfo:
    path: str
    branch: str


def parse_worktree_list(porcelain_output: str) -> list[WorktreeInfo]:
    return [
        WorktreeInfo(path=m.group("path"), branch=m.group("branch"))
        for m in _ENTRY_RE.finditer(porcelain_output)
    ]


def _status_for_pr_state(pr_state: str | None) -> tuple[str, bool]:
    """Returns (status_label, done)."""
    if pr_state is None:
        return labels.STATUS_IN_PROGRESS, False
    if pr_state == "MERGED":
        return labels.STATUS_DONE, True
    if pr_state == "OPEN":
        return labels.STATUS_READY_FOR_REVIEW, False
    return labels.STATUS_IN_PROGRESS, False  # CLOSED-not-merged: branch is still live work


def build_worktree_items(
    worktrees: Sequence[WorktreeInfo],
    pr_state_by_branch: dict[str, str | None],
    *,
    main_branch: str = "main",
) -> list[SyncItem]:
    items: list[SyncItem] = []
    for wt in worktrees:
        if wt.branch == main_branch:
            continue
        status_label, done = _status_for_pr_state(pr_state_by_branch.get(wt.branch))
        items.append(SyncItem(
            kind=labels.SYNC_MARKER_KIND_WORKTREE,
            key=wt.branch,
            title=f"Worktree: {wt.branch}",
            status_label=status_label,
            type_label=labels.TYPE_TRACKING,
            context_body=(
                f"## Context\nTracking issue for the active git worktree at "
                f"`{wt.path}`, branch `{wt.branch}`. Generated and kept in "
                f"sync by `tools/kanban_sync` - labels here are overwritten "
                f"on the next sync run, don't hand-edit them.\n"
            ),
            acceptance_criteria=("Branch is merged to main and the worktree is removed.",),
            done=done,
        ))
    return items


def collect_worktree_items(
    porcelain_output: str,
    client: GithubClient,
    *,
    main_branch: str = "main",
) -> list[SyncItem]:
    worktrees = parse_worktree_list(porcelain_output)
    pr_state_by_branch = {
        wt.branch: client.find_pr_state(wt.branch)
        for wt in worktrees if wt.branch != main_branch
    }
    return build_worktree_items(worktrees, pr_state_by_branch, main_branch=main_branch)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_kanban_sync_sources_worktree.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/kanban_sync/sources_worktree.py tests/test_kanban_sync_sources_worktree.py
git commit -m "feat: add worktree source for kanban sync"
```

---

### Task 5: ROADMAP.md source

**Files:**
- Create: `tools/kanban_sync/sources_roadmap.py`
- Test: `tests/test_kanban_sync_sources_roadmap.py`

**Interfaces:**
- Consumes: `labels.*`, `models.SyncItem` (Task 1); `markers.slugify`,
  `markers.extract_roadmap_title` (Task 2).
- Produces: `sources_roadmap.parse_roadmap_items(text: str) -> list[SyncItem]`
  — called directly by `__main__.py` (Task 9) with `Path("ROADMAP.md").read_text()`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_kanban_sync_sources_roadmap.py
from tools.kanban_sync import labels
from tools.kanban_sync.sources_roadmap import parse_roadmap_items

SAMPLE = """## Path to production

Some prose paragraph that isn't a bullet at all.

- [x] **The event loop stalled for 17-38+ seconds at a stretch, live,
      post-P0-P2.** Found 2026-08-26 gathering Program 1's evidence.
      Fixed via a SQL rewrite. PR #35.
- [ ] `services/shadow_mode.py` logs what the strategy *would* trade
      against real signal data, but hasn't been run for a real
      evaluation stretch and reviewed.
- [ ] Consider a dedicated charts module for the Advanced view.
"""


def test_parse_roadmap_items_skips_checked_items():
    items = parse_roadmap_items(SAMPLE)

    keys = [i.key for i in items]
    assert not any("event-loop-stalled" in k for k in keys)


def test_parse_roadmap_items_returns_one_item_per_open_checkbox():
    items = parse_roadmap_items(SAMPLE)

    assert len(items) == 2


def test_parse_roadmap_items_uses_bold_lead_in_as_title_when_present():
    items = parse_roadmap_items(
        "- [ ] **Shadow-mode sustained run + review** - never happened.\n"
    )

    assert items[0].title == "Shadow-mode sustained run + review"


def test_parse_roadmap_items_falls_back_to_plain_text_title():
    items = parse_roadmap_items(SAMPLE)

    charts_item = [i for i in items if "charts" in i.title.lower()][0]
    assert charts_item.title == "Consider a dedicated charts module for the Advanced view."


def test_parse_roadmap_items_captures_multiline_continuation_in_body():
    items = parse_roadmap_items(SAMPLE)

    shadow_item = [i for i in items if "shadow_mode" in i.context_body][0]
    assert "evaluation stretch and reviewed" in shadow_item.context_body


def test_parse_roadmap_items_marks_status_claimable_and_type_feature():
    items = parse_roadmap_items(SAMPLE)

    assert all(i.status_label == labels.STATUS_CLAIMABLE for i in items)
    assert all(i.type_label == labels.TYPE_FEATURE for i in items)
    assert all(i.done is False for i in items)


def test_parse_roadmap_items_always_has_acceptance_criteria():
    items = parse_roadmap_items(SAMPLE)

    assert all(len(i.acceptance_criteria) >= 1 for i in items)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_kanban_sync_sources_roadmap.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.kanban_sync.sources_roadmap'`

- [ ] **Step 3: Implement sources_roadmap.py**

```python
# tools/kanban_sync/sources_roadmap.py
"""ROADMAP.md source (spec §5's second row): one SyncItem per open
`- [ ]` top-level bullet. ROADMAP.md's checkbox state IS actively
maintained in this repo (unlike numbered plan docs - see sources_plan.py
and spec §5), so a direct 1:1 parse is safe here.
"""
from __future__ import annotations

import re

from tools.kanban_sync import labels
from tools.kanban_sync.markers import extract_roadmap_title, slugify
from tools.kanban_sync.models import SyncItem

_BULLET_RE = re.compile(
    r"^- \[(?P<mark>[ x])\] (?P<first_line>.+)$", re.MULTILINE,
)


def _iter_bullets(text: str) -> list[tuple[bool, str]]:
    """Returns (checked, full_bullet_text) for every top-level `- [ ]`/
    `- [x]` bullet, where full_bullet_text includes indented continuation
    lines up to the next top-level bullet or blank-line-delimited block."""
    lines = text.splitlines()
    bullets: list[tuple[bool, str]] = []
    current_lines: list[str] | None = None
    current_checked = False

    for line in lines:
        match = re.match(r"^- \[([ x])\] (.+)$", line)
        if match:
            if current_lines is not None:
                bullets.append((current_checked, "\n".join(current_lines)))
            current_checked = match.group(1) == "x"
            current_lines = [match.group(2)]
            continue
        if current_lines is not None and line.startswith("      "):
            current_lines.append(line.strip())
            continue
        if current_lines is not None:
            bullets.append((current_checked, "\n".join(current_lines)))
            current_lines = None

    if current_lines is not None:
        bullets.append((current_checked, "\n".join(current_lines)))

    return bullets


def parse_roadmap_items(text: str) -> list[SyncItem]:
    items: list[SyncItem] = []
    for checked, bullet_text in _iter_bullets(text):
        if checked:
            continue
        title = extract_roadmap_title(bullet_text)
        key = slugify(title)
        items.append(SyncItem(
            kind=labels.SYNC_MARKER_KIND_ROADMAP,
            key=key,
            title=title,
            status_label=labels.STATUS_CLAIMABLE,
            type_label=labels.TYPE_FEATURE,
            context_body=f"## Context\n{bullet_text}",
            acceptance_criteria=(
                "This bullet is checked off (`- [x]`) in ROADMAP.md with a "
                "factual note on what shipped, per the close-roadmap-item "
                "skill's convention.",
            ),
            done=False,
        ))
    return items
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_kanban_sync_sources_roadmap.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/kanban_sync/sources_roadmap.py tests/test_kanban_sync_sources_roadmap.py
git commit -m "feat: add ROADMAP.md source for kanban sync"
```

---

### Task 6: `active-tracks-board.md` track source

**Files:**
- Create: `tools/kanban_sync/sources_tracks.py`
- Test: `tests/test_kanban_sync_sources_tracks.py`

**Interfaces:**
- Consumes: `labels.*`, `models.SyncItem` (Task 1).
- Produces: `sources_tracks.parse_track_items(text: str) -> list[SyncItem]`
  — called directly by `__main__.py` (Task 9) with
  `Path("docs/superpowers/plans/2026-08-26-active-tracks-board.md").read_text()`.
  Each returned item's `depends_on_keys` uses `("track", "<letter>")` pairs.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_kanban_sync_sources_tracks.py
from tools.kanban_sync import labels
from tools.kanban_sync.sources_tracks import parse_track_items

SAMPLE = """## Track A — Realtime data plane (lead track)

**Status (2026-08-26):** CH1 is next. Not started.

Some more prose about Track A.

---

## Track B — Economic strategy validity

**Status (2026-08-26):** deferred behind Track A by explicit user decision.

---

## Track C — Downstream production programs (sequential, hard-gated)

Programs 3R -> 3 -> 4. Nothing here should start.

**Program 7 status (2026-08-26): paused after Task 3.**
"""


def test_parse_track_items_returns_one_item_per_track_heading():
    items = parse_track_items(SAMPLE)

    assert [i.key for i in items] == ["A", "B", "C"]


def test_parse_track_items_uses_full_heading_as_title():
    items = parse_track_items(SAMPLE)

    assert items[0].title == "Track A — Realtime data plane (lead track)"


def test_parse_track_items_captures_status_line_in_body():
    items = parse_track_items(SAMPLE)

    assert "CH1 is next" in items[0].context_body


def test_parse_track_items_marks_hard_gated_track_dependent_on_earlier_tracks():
    items = parse_track_items(SAMPLE)

    track_c = [i for i in items if i.key == "C"][0]
    assert set(track_c.depends_on_keys) == {("track", "A"), ("track", "B")}


def test_parse_track_items_ungated_tracks_have_no_dependencies():
    items = parse_track_items(SAMPLE)

    track_a = [i for i in items if i.key == "A"][0]
    track_b = [i for i in items if i.key == "B"][0]
    assert track_a.depends_on_keys == ()
    assert track_b.depends_on_keys == ()


def test_parse_track_items_all_claimable_type_investigation_not_done():
    items = parse_track_items(SAMPLE)

    assert all(i.status_label == labels.STATUS_CLAIMABLE for i in items)
    assert all(i.type_label == labels.TYPE_INVESTIGATION for i in items)
    assert all(i.done is False for i in items)


def test_parse_track_items_done_status_line_marks_item_done():
    text = "## Track D — Finished track\n\n**Status (2026-08-26):** complete, nothing left.\n"

    items = parse_track_items(text)

    assert items[0].done is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_kanban_sync_sources_tracks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.kanban_sync.sources_tracks'`

- [ ] **Step 3: Implement sources_tracks.py**

```python
# tools/kanban_sync/sources_tracks.py
"""active-tracks-board.md source (spec §5's third row, granularity fixed
by this plan's Global Constraint 5): one SyncItem per `## Track <letter>`
heading - never per sub-step (CH1/CH2/...). A track whose section contains
a hard-gate keyword depends on every track heading that appears earlier in
the file; this is a deliberately simple, conservative heuristic (spec §8) -
over-blocking is safer than inventing a wrong edge from free-form prose.
"""
from __future__ import annotations

import re

from tools.kanban_sync import labels
from tools.kanban_sync.models import SyncItem

_TRACK_HEADING_RE = re.compile(r"^## Track (?P<letter>[A-Z]) — (?P<title>.+)$", re.MULTILINE)
_STATUS_LINE_RE = re.compile(r"\*\*Status[^*]*:\*\*[^\n]*")
_GATE_KEYWORDS = ("hard-gated", "hard gate", "gated behind")
_DONE_KEYWORDS = ("complete", "done")


def _split_sections(text: str) -> list[tuple[str, str, str]]:
    """Returns (letter, title, section_body) for every `## Track X` heading,
    where section_body runs to the next `## ` heading or end of file."""
    headings = list(_TRACK_HEADING_RE.finditer(text))
    sections = []
    for idx, match in enumerate(headings):
        start = match.end()
        end = headings[idx + 1].start() if idx + 1 < len(headings) else len(text)
        sections.append((match.group("letter"), match.group("title"), text[start:end]))
    return sections


def parse_track_items(text: str) -> list[SyncItem]:
    sections = _split_sections(text)
    letters_in_order = [letter for letter, _, _ in sections]
    items: list[SyncItem] = []

    for idx, (letter, title, body) in enumerate(sections):
        status_match = _STATUS_LINE_RE.search(body)
        status_text = status_match.group(0) if status_match else ""
        done = any(kw in status_text.lower() for kw in _DONE_KEYWORDS)

        depends_on: tuple[tuple[str, str], ...] = ()
        if any(kw in body.lower() for kw in _GATE_KEYWORDS):
            depends_on = tuple(
                ("track", earlier_letter) for earlier_letter in letters_in_order[:idx]
            )

        heading = f"Track {letter} — {title}"
        items.append(SyncItem(
            kind=labels.SYNC_MARKER_KIND_TRACK,
            key=letter,
            title=heading,
            status_label=labels.STATUS_CLAIMABLE,
            type_label=labels.TYPE_INVESTIGATION,
            context_body=f"## Context\n{heading}\n\n{status_text or body.strip()[:500]}",
            acceptance_criteria=(
                f"`active-tracks-board.md`'s Track {letter} section is "
                f"updated to a completed status.",
            ),
            depends_on_keys=depends_on,
            done=done,
        ))
    return items
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_kanban_sync_sources_tracks.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/kanban_sync/sources_tracks.py tests/test_kanban_sync_sources_tracks.py
git commit -m "feat: add active-tracks-board.md track source for kanban sync"
```

---

### Task 7: Reconciliation engine — pass one (find-or-create, update, close, non-reopen)

**Files:**
- Create: `tools/kanban_sync/sync.py`
- Test: `tests/test_kanban_sync_sync.py`

**Interfaces:**
- Consumes: `labels.*`, `models.SyncItem`, `models.SyncReport` (Task 1);
  `markers.build_marker` (Task 2).
- Produces: `sync.SyncGithubClient` (a `typing.Protocol` describing the
  subset of `GithubClient` this module needs — `find_by_marker`,
  `create_issue`, `set_labels`, `close_issue`, `post_comment`), `sync._render_body(item: SyncItem) -> str`,
  `sync.sync_pass_one(items: Sequence[SyncItem], client: SyncGithubClient,
  *, dry_run: bool) -> tuple[dict[tuple[str, str], int], SyncReport]`. Task
  8 adds `sync_pass_two` and `sync.reconcile` to this same file.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_kanban_sync_sync.py
from tools.kanban_sync import labels
from tools.kanban_sync.github_client import IssueState
from tools.kanban_sync.models import SyncItem
from tools.kanban_sync.sync import sync_pass_one


class FakeGithubClient:
    def __init__(self) -> None:
        self.issues: dict[int, dict] = {}
        self._next_number = 1
        self.comments: list[tuple[int, str]] = []

    def find_by_marker(self, marker):
        for number, issue in self.issues.items():
            if marker in issue["body"]:
                return IssueState(number=number, open=issue["open"], labels=frozenset(issue["labels"]))
        return None

    def create_issue(self, title, body, labels_):
        number = self._next_number
        self._next_number += 1
        self.issues[number] = {"title": title, "body": body, "labels": set(labels_), "open": True}
        return IssueState(number=number, open=True, labels=frozenset(labels_))

    def set_labels(self, number, add, remove):
        issue = self.issues[number]
        issue["labels"] = (issue["labels"] | set(add)) - set(remove)

    def close_issue(self, number):
        self.issues[number]["open"] = False

    def post_comment(self, number, body):
        self.comments.append((number, body))


def _item(kind="track", key="A", *, title="Track A", status=labels.STATUS_CLAIMABLE,
          done=False, depends_on=()):
    return SyncItem(
        kind=kind, key=key, title=title, status_label=status,
        type_label=labels.TYPE_TRACKING, context_body="## Context\nx",
        acceptance_criteria=("done when x happens",), done=done,
        depends_on_keys=depends_on,
    )


def test_sync_pass_one_creates_new_issue_for_new_item():
    client = FakeGithubClient()

    _, report = sync_pass_one([_item()], client, dry_run=False)

    assert len(client.issues) == 1
    assert report.created and "Track A" in report.created[0]


def test_sync_pass_one_is_idempotent_no_duplicate_on_second_run():
    client = FakeGithubClient()
    sync_pass_one([_item()], client, dry_run=False)

    _, report = sync_pass_one([_item()], client, dry_run=False)

    assert len(client.issues) == 1
    assert report.created == []


def test_sync_pass_one_closes_issue_when_item_becomes_done():
    client = FakeGithubClient()
    sync_pass_one([_item(done=False)], client, dry_run=False)

    _, report = sync_pass_one([_item(done=True)], client, dry_run=False)

    (issue,) = client.issues.values()
    assert issue["open"] is False
    assert report.closed


def test_sync_pass_one_does_not_reopen_manually_closed_issue():
    client = FakeGithubClient()
    sync_pass_one([_item(done=False)], client, dry_run=False)
    (number,) = client.issues.keys()
    client.close_issue(number)  # simulate a human closing it by hand

    _, report = sync_pass_one([_item(done=False)], client, dry_run=False)  # source still open

    assert client.issues[number]["open"] is False
    assert report.flagged_mismatches
    assert client.comments and "sync-mismatch" in client.comments[0][1]


def test_sync_pass_one_skips_creating_issue_for_item_already_done():
    client = FakeGithubClient()

    _, report = sync_pass_one([_item(done=True)], client, dry_run=False)

    assert client.issues == {}
    assert report.created == []


def test_sync_pass_one_dry_run_makes_no_mutating_calls():
    client = FakeGithubClient()

    _, report = sync_pass_one([_item()], client, dry_run=True)

    assert client.issues == {}
    assert report.dry_run is True
    assert report.created
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_kanban_sync_sync.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.kanban_sync.sync'`

- [ ] **Step 3: Implement sync.py (pass one only — pass two arrives in Task 8)**

```python
# tools/kanban_sync/sync.py
"""Reconciliation engine (spec §6-9): given a list of SyncItems from any
source, find-or-create the matching GitHub issue by its sync marker,
reconcile labels, and close issues whose source item is now done -
without ever reopening one a human closed by hand. Depends-on resolution
(sync_pass_two, reconcile) is added in Task 8.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol, Sequence

from tools.kanban_sync import labels
from tools.kanban_sync.markers import build_marker
from tools.kanban_sync.models import SyncItem, SyncReport


class SyncGithubClient(Protocol):
    def find_by_marker(self, marker: str): ...
    def create_issue(self, title: str, body: str, labels_: Sequence[str]): ...
    def set_labels(self, number: int, add: Sequence[str], remove: Sequence[str]) -> None: ...
    def close_issue(self, number: int) -> None: ...
    def post_comment(self, number: int, body: str) -> None: ...


def _render_body(item: SyncItem) -> str:
    parts = [item.context_body.rstrip(), "\n## Acceptance criteria"]
    parts += [f"- {c}" for c in item.acceptance_criteria]
    if item.scope_paths:
        parts.append("\n## Scope")
        parts += [f"- {p}" for p in item.scope_paths]
    parts.append(f"\n{build_marker(item.kind, item.key)}")
    return "\n".join(parts)


def _mismatch_comment(item: SyncItem) -> str:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return (
        f"<!-- event: sync-mismatch | agent: kanban-board-sync | ts: {ts} -->\n"
        f"This issue is closed on GitHub, but its source (`{item.kind}:{item.key}`) "
        f"is still open. Not reopening automatically - please reconcile manually."
    )


def sync_pass_one(
    items: Sequence[SyncItem],
    client: SyncGithubClient,
    *,
    dry_run: bool,
) -> tuple[dict[tuple[str, str], int], SyncReport]:
    report = SyncReport(dry_run=dry_run)
    number_by_identity: dict[tuple[str, str], int] = {}

    for item in items:
        marker = build_marker(item.kind, item.key)
        existing = client.find_by_marker(marker)
        identity = (item.kind, item.key)

        if existing is None:
            if item.done:
                continue  # never existed and already done - nothing to create
            if dry_run:
                report.created.append(f"{identity}: {item.title}")
                continue
            desired_labels = sorted({item.status_label, item.type_label})
            issue = client.create_issue(item.title, _render_body(item), desired_labels)
            number_by_identity[identity] = issue.number
            report.created.append(f"#{issue.number} {item.title}")
            continue

        number_by_identity[identity] = existing.number

        if item.done:
            if existing.open:
                if not dry_run:
                    client.close_issue(existing.number)
                report.closed.append(f"#{existing.number} {item.title}")
            continue

        if not existing.open:
            if not dry_run:
                client.post_comment(existing.number, _mismatch_comment(item))
            report.flagged_mismatches.append(f"#{existing.number} {item.title}")
            continue

        existing_status_labels = existing.labels & labels.ALL_STATUS_LABELS
        stale_status = existing_status_labels - {item.status_label}
        add = ({item.status_label, item.type_label} - existing.labels)
        remove = stale_status
        if add or remove:
            if not dry_run:
                client.set_labels(existing.number, sorted(add), sorted(remove))
            report.updated.append(f"#{existing.number} {item.title}")

    return number_by_identity, report
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_kanban_sync_sync.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/kanban_sync/sync.py tests/test_kanban_sync_sync.py
git commit -m "feat: add kanban sync reconciliation pass one (create/update/close/flag)"
```

---

### Task 8: Reconciliation engine — pass two (depends-on) and `reconcile()`

**Files:**
- Modify: `tools/kanban_sync/sync.py` (add `sync_pass_two`, `reconcile`)
- Modify: `tests/test_kanban_sync_sync.py` (add tests)

**Interfaces:**
- Consumes: everything from Task 7's `sync.py`.
- Produces: `sync.sync_pass_two(items: Sequence[SyncItem], number_by_identity:
  dict[tuple[str, str], int], client: SyncGithubClient, *, dry_run: bool) ->
  SyncReport`, `sync.reconcile(items: Sequence[SyncItem], client:
  SyncGithubClient, *, dry_run: bool = False) -> SyncReport` — the single
  entry point `__main__.py` (Task 9) calls.

- [ ] **Step 1: Write the failing tests (append to the existing test file)**

```python
# tests/test_kanban_sync_sync.py (append)
from tools.kanban_sync.sync import reconcile


def test_reconcile_sets_depends_on_label_using_real_issue_number():
    client = FakeGithubClient()
    track_a = _item(kind="track", key="A", title="Track A")
    track_c = _item(kind="track", key="C", title="Track C", depends_on=(("track", "A"),))

    reconcile([track_a, track_c], client)

    c_number = [n for n, i in client.issues.items() if i["title"] == "Track C"][0]
    a_number = [n for n, i in client.issues.items() if i["title"] == "Track A"][0]
    assert f"depends-on:#{a_number}" in client.issues[c_number]["labels"]


def test_reconcile_second_run_does_not_re_add_existing_depends_on_label():
    client = FakeGithubClient()
    track_a = _item(kind="track", key="A", title="Track A")
    track_c = _item(kind="track", key="C", title="Track C", depends_on=(("track", "A"),))
    reconcile([track_a, track_c], client)

    reconcile([track_a, track_c], client)  # second run, same input

    c_number = [n for n, i in client.issues.items() if i["title"] == "Track C"][0]
    depends_labels = [l for l in client.issues[c_number]["labels"] if l.startswith("depends-on:#")]
    assert len(depends_labels) == 1  # not duplicated


def test_reconcile_skips_depends_on_for_dependency_not_yet_created():
    client = FakeGithubClient()
    track_c = _item(kind="track", key="C", title="Track C", depends_on=(("track", "A"),))

    report = reconcile([track_c], client)  # Track A never in this run's item list

    (number,) = client.issues.keys()
    assert not [l for l in client.issues[number]["labels"] if l.startswith("depends-on:#")]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_kanban_sync_sync.py -v -k reconcile`
Expected: FAIL — `ImportError: cannot import name 'reconcile' from 'tools.kanban_sync.sync'`

- [ ] **Step 3: Append sync_pass_two and reconcile to sync.py**

```python
# tools/kanban_sync/sync.py (append)

def sync_pass_two(
    items: Sequence[SyncItem],
    number_by_identity: dict[tuple[str, str], int],
    client: SyncGithubClient,
    *,
    dry_run: bool,
) -> SyncReport:
    report = SyncReport(dry_run=dry_run)
    for item in items:
        identity = (item.kind, item.key)
        number = number_by_identity.get(identity)
        if number is None or item.done:
            continue  # not created this run, or already closed - no deps to set

        desired = {
            f"depends-on:#{number_by_identity[dep]}"
            for dep in item.depends_on_keys
            if dep in number_by_identity
        }
        existing = client.find_by_marker(build_marker(item.kind, item.key))
        current = {
            label for label in (existing.labels if existing else frozenset())
            if label.startswith("depends-on:#")
        }
        add = desired - current
        remove = current - desired
        if add or remove:
            if not dry_run:
                client.set_labels(number, sorted(add), sorted(remove))
            report.updated.append(f"#{number} depends-on updated")
    return report


def reconcile(
    items: Sequence[SyncItem],
    client: SyncGithubClient,
    *,
    dry_run: bool = False,
) -> SyncReport:
    number_by_identity, report = sync_pass_one(items, client, dry_run=dry_run)
    pass_two_report = sync_pass_two(items, number_by_identity, client, dry_run=dry_run)
    report.updated += pass_two_report.updated
    return report
```

- [ ] **Step 4: Run the full sync test file to verify everything passes**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_kanban_sync_sync.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/kanban_sync/sync.py tests/test_kanban_sync_sync.py
git commit -m "feat: add depends-on resolution and reconcile() entry point to kanban sync"
```

**Note on spec §13's dependency-cycle fault-injection scenario:** that
scenario is satisfied by construction, not by new code in `sync.py`. The
only source that ever populates `depends_on_keys` in this plan is Task 6's
track parser, and its heuristic (`letters_in_order[:idx]`) can only ever
point a track at a strictly *earlier* heading in the same file — a track
can never depend on itself or a later track, so no cycle can be produced
by this plan's actual data flow. General cycle rejection for a
hypothetical future depends-on-producing source is deliberately left to
the installed `github-issues-kanban` skill's own existing conductor-side
detection (`references/dependency-chain.md`'s "Cycle detection" section)
rather than duplicated here — `sync.py` only ever writes labels a future
dispatch attempt would still correctly evaluate.

---

### Task 9: Plan-doc candidate listing and classification consumption

**Files:**
- Create: `tools/kanban_sync/sources_plan.py`
- Test: `tests/test_kanban_sync_sources_plan.py`

**Interfaces:**
- Consumes: `labels.*`, `models.SyncItem` (Task 1).
- Produces: `sources_plan.list_plan_candidates(plans_dir: Path,
  active_tracks_board_text: str) -> list[str]`,
  `sources_plan.build_plan_items(classifications: dict[str, dict]) ->
  list[SyncItem]` — both called by `__main__.py` (Task 10) and documented
  for the `kanban-board-sync` skill (Task 11) to drive interactively.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_kanban_sync_sources_plan.py
from tools.kanban_sync.sources_plan import build_plan_items, list_plan_candidates


def test_list_plan_candidates_excludes_board_file_and_referenced_plans(tmp_path):
    (tmp_path / "2026-08-26-active-tracks-board.md").write_text("board")
    (tmp_path / "2026-08-26-subscription-churn-investigation.md").write_text("x")
    (tmp_path / "2026-08-25-frontend-modularization.md").write_text("x")
    board_text = (
        "Investigation plan: `docs/superpowers/plans/"
        "2026-08-26-subscription-churn-investigation.md`"
    )

    result = list_plan_candidates(tmp_path, board_text)

    assert result == ["2026-08-25-frontend-modularization.md"]


def test_list_plan_candidates_returns_sorted_list(tmp_path):
    (tmp_path / "2026-08-26-active-tracks-board.md").write_text("board")
    (tmp_path / "b-plan.md").write_text("x")
    (tmp_path / "a-plan.md").write_text("x")

    result = list_plan_candidates(tmp_path, "")

    assert result == ["a-plan.md", "b-plan.md"]


def test_build_plan_items_skips_done_classification():
    items = build_plan_items({"x.md": {"status": "done", "note": "shipped"}})

    assert items == []


def test_build_plan_items_creates_item_for_in_progress():
    items = build_plan_items({
        "2026-08-25-frontend-modularization.md": {
            "status": "in-progress", "note": "several tasks still open",
        }
    })

    assert len(items) == 1
    assert items[0].key == "2026-08-25-frontend-modularization.md"
    assert items[0].done is False
    assert "several tasks still open" in items[0].context_body


def test_build_plan_items_has_acceptance_criteria():
    items = build_plan_items({"x.md": {"status": "not-started", "note": ""}})

    assert len(items[0].acceptance_criteria) >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_kanban_sync_sources_plan.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.kanban_sync.sources_plan'`

- [ ] **Step 3: Implement sources_plan.py**

```python
# tools/kanban_sync/sources_plan.py
"""Numbered plan docs not already represented by a track (spec §5's last
row). Candidate listing is mechanical (which files exist, and which of
them active-tracks-board.md already references); classifying a candidate
as done/in-progress/not-started is a judgment call the on-demand
kanban-board-sync skill makes by reading git log/CLAUDE.md/ROADMAP.md, not
something this module infers from the plan doc's own checkboxes -
measured unreliable in spec §5.
"""
from __future__ import annotations

import re
from pathlib import Path

from tools.kanban_sync import labels
from tools.kanban_sync.models import SyncItem

_PLAN_DOC_REF_RE = re.compile(r"docs/superpowers/plans/([\w.-]+\.md)")
_EXCLUDED_FILENAMES = frozenset({"2026-08-26-active-tracks-board.md"})


def list_plan_candidates(plans_dir: Path, active_tracks_board_text: str) -> list[str]:
    referenced = set(_PLAN_DOC_REF_RE.findall(active_tracks_board_text))
    all_plans = {p.name for p in plans_dir.glob("*.md")}
    return sorted(all_plans - referenced - _EXCLUDED_FILENAMES)


def build_plan_items(classifications: dict[str, dict]) -> list[SyncItem]:
    """`classifications` maps filename -> {"status": "done"|"in-progress"|
    "not-started", "note": str}, produced by the kanban-board-sync skill's
    judgment-assisted classification step."""
    items: list[SyncItem] = []
    for filename, info in classifications.items():
        if info["status"] == "done":
            continue
        note = info.get("note", "")
        items.append(SyncItem(
            kind=labels.SYNC_MARKER_KIND_PLAN,
            key=filename,
            title=f"Plan: {filename}",
            status_label=labels.STATUS_CLAIMABLE,
            type_label=labels.TYPE_PLAN_TASK,
            context_body=(
                f"## Context\nTracks `docs/superpowers/plans/{filename}` "
                f"as a whole, not per-task (see kanban-board-sync-design.md "
                f"§5 on why plan-doc checkboxes aren't a reliable per-task "
                f"signal in this repo).\n\nClassification: {info['status']}."
                f"\n{note}"
            ),
            acceptance_criteria=(
                f"`docs/superpowers/plans/{filename}` is reclassified "
                f"'done' on a future sync run.",
            ),
            done=False,
        ))
    return items
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_kanban_sync_sources_plan.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/kanban_sync/sources_plan.py tests/test_kanban_sync_sources_plan.py
git commit -m "feat: add plan-doc candidate listing and classification consumption"
```

---

### Task 10: CLI entrypoint (`python -m tools.kanban_sync`)

**Files:**
- Create: `tools/kanban_sync/__main__.py`
- Test: `tests/test_kanban_sync_main.py`

**Interfaces:**
- Consumes: `github_client.GithubClient` (Task 3);
  `sources_worktree.collect_worktree_items` (Task 4);
  `sources_roadmap.parse_roadmap_items` (Task 5);
  `sources_tracks.parse_track_items` (Task 6);
  `sync.reconcile` (Task 8); `sources_plan.list_plan_candidates`,
  `sources_plan.build_plan_items` (Task 9).
- Produces: `python -m tools.kanban_sync sync --sources <csv> [--dry-run]
  [--plan-classifications PATH]` and `python -m tools.kanban_sync
  plan-candidates` as real CLI commands — the two commands Task 11's skill
  and Task 12's checkpoint step invoke.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_kanban_sync_main.py
import subprocess

import pytest

from tools.kanban_sync import __main__ as cli


def test_check_project_scope_exits_when_scope_missing(monkeypatch):
    monkeypatch.setattr(
        cli.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=0, stdout="gist, read:org, repo", stderr=""),
    )

    with pytest.raises(SystemExit) as exc:
        cli._check_project_scope()

    assert exc.value.code == 1


def test_check_project_scope_passes_when_scope_present(monkeypatch):
    monkeypatch.setattr(
        cli.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=0, stdout="gist, project, read:org, repo", stderr=""),
    )

    cli._check_project_scope()  # must not raise


def test_collect_items_errors_when_plan_source_missing_classifications():
    with pytest.raises(SystemExit) as exc:
        cli._collect_items(["plan"], None)

    assert exc.value.code == 1


def test_sync_subcommand_requires_sources_argument():
    with pytest.raises(SystemExit):
        cli.main(["sync"])


def test_plan_candidates_subcommand_is_registered(monkeypatch):
    called = []
    monkeypatch.setattr(cli, "_cmd_plan_candidates", lambda args: called.append(True))

    cli.main(["plan-candidates"])

    assert called == [True]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_kanban_sync_main.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.kanban_sync.__main__'`

- [ ] **Step 3: Implement __main__.py**

```python
# tools/kanban_sync/__main__.py
"""CLI entrypoint for the kanban board sync (docs/superpowers/specs/
2026-08-26-kanban-board-sync-design.md). `python -m tools.kanban_sync sync
--sources worktree,roadmap,track` runs the fully mechanical sources (used
by the /checkpoint skill integration, Task 12); `--sources plan`
additionally needs `--plan-classifications <path-to-json>`, produced by
the on-demand kanban-board-sync skill's judgment-assisted classification
step (Task 11).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from tools.kanban_sync.github_client import GithubClient
from tools.kanban_sync.models import SyncItem
from tools.kanban_sync.sources_plan import build_plan_items, list_plan_candidates
from tools.kanban_sync.sources_roadmap import parse_roadmap_items
from tools.kanban_sync.sources_tracks import parse_track_items
from tools.kanban_sync.sources_worktree import collect_worktree_items
from tools.kanban_sync.sync import reconcile

REPO = "thesneakattack/kalshi-whale-poc"
ROADMAP_PATH = Path("ROADMAP.md")
ACTIVE_TRACKS_BOARD_PATH = Path("docs/superpowers/plans/2026-08-26-active-tracks-board.md")
PLANS_DIR = Path("docs/superpowers/plans")


def _check_project_scope() -> None:
    result = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    combined = f"{result.stdout}\n{result.stderr}"
    if "project" not in combined:
        print(
            "error: gh CLI is missing the 'project' OAuth scope needed for "
            "Projects V2 board access.\nRun: gh auth refresh -s project",
            file=sys.stderr,
        )
        sys.exit(1)


def _collect_items(sources: list[str], plan_classifications: Path | None) -> list[SyncItem]:
    items: list[SyncItem] = []
    client = GithubClient(REPO)

    if "worktree" in sources:
        porcelain = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True, text=True, check=True,
        ).stdout
        items += collect_worktree_items(porcelain, client)
    if "roadmap" in sources:
        items += parse_roadmap_items(ROADMAP_PATH.read_text())
    if "track" in sources:
        items += parse_track_items(ACTIVE_TRACKS_BOARD_PATH.read_text())
    if "plan" in sources:
        if plan_classifications is None:
            print("error: --sources plan requires --plan-classifications <path>", file=sys.stderr)
            sys.exit(1)
        items += build_plan_items(json.loads(plan_classifications.read_text()))

    return items


def _cmd_sync(args: argparse.Namespace) -> None:
    _check_project_scope()
    sources = args.sources.split(",")
    items = _collect_items(sources, args.plan_classifications)
    client = GithubClient(REPO)
    report = reconcile(items, client, dry_run=args.dry_run)

    print(f"created: {len(report.created)}")
    for line in report.created:
        print(f"  + {line}")
    print(f"updated: {len(report.updated)}")
    print(f"closed: {len(report.closed)}")
    print(f"flagged mismatches: {len(report.flagged_mismatches)}")
    for line in report.flagged_mismatches:
        print(f"  ! {line}")


def _cmd_plan_candidates(_args: argparse.Namespace) -> None:
    for path in list_plan_candidates(PLANS_DIR, ACTIVE_TRACKS_BOARD_PATH.read_text()):
        print(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.kanban_sync")
    sub = parser.add_subparsers(dest="command", required=True)

    sync_parser = sub.add_parser("sync", help="reconcile sources onto GitHub Issues")
    sync_parser.add_argument("--sources", required=True, help="comma-separated: worktree,roadmap,track,plan")
    sync_parser.add_argument("--dry-run", action="store_true")
    sync_parser.add_argument("--plan-classifications", type=Path, default=None)
    sync_parser.set_defaults(func=_cmd_sync)

    candidates_parser = sub.add_parser("plan-candidates", help="list plan docs needing classification")
    candidates_parser.set_defaults(func=_cmd_plan_candidates)

    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_kanban_sync_main.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Run the entire kanban_sync test suite together**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_kanban_sync_*.py -v`
Expected: PASS (all tests from Tasks 1-10, ~65 tests total)

- [ ] **Step 6: Commit**

```bash
git add tools/kanban_sync/__main__.py tests/test_kanban_sync_main.py
git commit -m "feat: add CLI entrypoint for kanban board sync"
```

---

### Task 11: On-demand `kanban-board-sync` skill

**Files:**
- Create: `.claude/skills/kanban-board-sync/SKILL.md`

**Interfaces:**
- Consumes: `python -m tools.kanban_sync sync`, `python -m tools.kanban_sync
  plan-candidates` (Task 10) as shell commands — no Python interface, this
  is a markdown skill file.

- [ ] **Step 1: Write the skill file**

```markdown
---
name: kanban-board-sync
description: This skill should be used when the user asks to "sync the kanban board", "sync issues", "update the board", "refresh the kanban board", or wants worktrees/ROADMAP.md/active-tracks-board.md/plan docs reflected onto the real GitHub Issues board. Runs tools/kanban_sync end to end, including the judgment-assisted plan-doc classification step a plain CLI invocation can't do on its own.
---

# Kanban board sync

Reflects this repo's current state onto real GitHub Issues, per
`docs/superpowers/specs/2026-08-26-kanban-board-sync-design.md`. One-way:
repo state is always authoritative, nothing here ever edits a repo file.
Uses `tools/kanban_sync` for every deterministic part; this skill's own
job is the one judgment call that tool can't make on its own (spec §5) -
classifying which numbered plan docs are actually still open.

## Steps

1. **Confirm `gh` has the `project` scope.** `gh auth status` - if
   `project` isn't listed, stop and tell the user to run
   `gh auth refresh -s project` (interactive OAuth device flow, only they
   can do it). Don't attempt to work around a missing scope.

2. **Run the mechanical sources first, as a dry run.**
   ```bash
   python -m tools.kanban_sync sync --sources worktree,roadmap,track --dry-run
   ```
   Read the report. If the created/updated/closed counts look sane (a few
   dozen items, not hundreds - spec §5's measured numbers are a rough
   sanity bound), re-run without `--dry-run` to actually write.

3. **List plan-doc candidates.**
   ```bash
   python -m tools.kanban_sync plan-candidates
   ```
   Prints filenames under `docs/superpowers/plans/` not already referenced
   by `active-tracks-board.md`.

4. **Classify each candidate.** For each filename this is a judgment call,
   not a checkbox scan (spec §5 measured why a plan doc's own `- [ ]`
   state is unreliable in this repo): read
   `git log --oneline -- docs/superpowers/plans/<file>`, cross-reference
   `CLAUDE.md` and `ROADMAP.md` for whether that initiative is described
   as shipped, and classify as `done`, `in-progress`, or `not-started`.
   Write the result to a JSON file, e.g. `/tmp/kanban-plan-classifications.json`:
   ```json
   {
     "2026-08-25-frontend-modularization.md": {
       "status": "in-progress",
       "note": "frontend-modularization-task skill still has open tasks"
     }
   }
   ```
   Include every candidate from step 3, even ones classified `done` — they
   get skipped, not created-then-closed (spec §5, Global Constraint 6).

5. **Sync the plan source.**
   ```bash
   python -m tools.kanban_sync sync --sources plan \
     --plan-classifications /tmp/kanban-plan-classifications.json --dry-run
   ```
   Review, then re-run without `--dry-run`.

6. **Report a short summary** to the user: counts created/updated/closed/
   flagged, and call out any `flagged mismatches` explicitly — those need a
   human to look at the issue and decide whether to close it for real or
   remove the stale dependency (spec §9).
```

- [ ] **Step 2: Verify the skill's commands are exactly what Task 10 implemented**

Run: `grep -n "python -m tools.kanban_sync" .claude/skills/kanban-board-sync/SKILL.md`
Expected: three matches, all using the `sync`/`plan-candidates` subcommands
and flags (`--sources`, `--dry-run`, `--plan-classifications`) exactly as
defined in `tools/kanban_sync/__main__.py`'s `argparse` setup — no typos,
no flags that don't exist.

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/kanban-board-sync/SKILL.md
git commit -m "feat: add on-demand kanban-board-sync skill"
```

---

### Task 12: Fold a mechanical sync step into `/checkpoint`

**Files:**
- Modify: `.claude/skills/checkpoint/SKILL.md`

**Interfaces:**
- Consumes: `python -m tools.kanban_sync sync --sources worktree,roadmap,track`
  (Task 10) as a shell command.

- [ ] **Step 1: Insert a new step 8 after the existing "Roadmap sync check" step, renumbering what follows**

Locate the existing step 7 (`**Roadmap sync check.**`) and the existing
step 8 (`**PR check`) in `.claude/skills/checkpoint/SKILL.md`. Insert a new
step between them, and renumber the two steps after it (old 8 → new 9, old
9 → new 10):

```markdown
8. **Kanban board sync (mechanical sources only).** Run the fast,
   fully-deterministic slice of the board sync — worktrees, `ROADMAP.md`,
   `active-tracks-board.md` — so the GitHub Issues board doesn't drift too
   far behind a normal working session:
   ```bash
   python -m tools.kanban_sync sync --sources worktree,roadmap,track
   ```
   This does not run the judgment-assisted plan-doc classification step —
   that's the standalone `kanban-board-sync` skill, run on demand, since
   it's slower and shouldn't gate every checkpoint. If `gh` is missing the
   `project` scope, this prints an actionable error and exits nonzero —
   don't treat that as a checkpoint failure, just note it and move on;
   getting that scope added is a one-time human action
   (`gh auth refresh -s project`), not something to fix mid-checkpoint.

9. **PR check — only when this checkpoint completes the initiative, not
   every mid-initiative checkpoint.** If the branch has no open PR yet and
   the initiative this branch covers is actually done, `gh pr create`; if
   CI (step 6) is green and the diff has been reviewed, `gh pr merge
   --merge` (preserves individual commits) then delete the branch — see
   `.claude/rules/branching-and-ci.md`'s "Integration lifecycle" for the
   full policy. A checkpoint in the middle of a multi-task initiative just
   leaves the branch pushed and green; it doesn't open or merge a PR yet.

10. **Session-hygiene prompt.** Give a short 2-3 sentence summary of what
    this checkpoint covered (keeps continuity across a later `/compact`).
    Then suggest — don't insist — whichever fits: `/compact` if context is
    getting heavy after a substantial chunk of work, `/clear` if the next
    task is materially unrelated to what was just finished.
```

Use the `Edit` tool for this — replace the existing steps 8 and 9 (and the
blank line between them) with the block above (new steps 8, 9, 10). Leave
every earlier step (1-7) and the "Scope note" section that follows
untouched.

- [ ] **Step 2: Verify the renumbering is internally consistent**

Run: `grep -n "^[0-9]*\. \*\*" .claude/skills/checkpoint/SKILL.md`
Expected: exactly one step numbered 1 through 10, each number appearing
once, in order, with no gap or duplicate.

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/checkpoint/SKILL.md
git commit -m "feat: fold mechanical kanban board sync into /checkpoint"
```

---

### Task 13: Real dry-run verification against the live repo

**Files:** none created or modified — this task verifies Tasks 1-12
against real repository state, per spec §13's dry-run verification
approach.

**Interfaces:** none — this is a verification-only task.

- [ ] **Step 1: Run the full kanban_sync test suite one more time, all together**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_kanban_sync_*.py -v`
Expected: PASS, every test from Tasks 1-10.

- [ ] **Step 2: Confirm `gh` still has the `project` scope in this environment**

Run: `gh auth status 2>&1 | grep -i scope`
Expected: output includes `project` among the listed scopes (confirmed
present earlier this session — re-check here since Task 13 runs later).

- [ ] **Step 3: Real dry-run against worktrees, ROADMAP.md, and active-tracks-board.md**

Run: `python -m tools.kanban_sync sync --sources worktree,roadmap,track --dry-run`

Expected, cross-checked against spec §5's measured numbers: roughly
25-30 items reported as `created` from the ROADMAP source (measured 27
open items at spec-writing time — some may have shipped since), a small
number (1-3) from the worktree source matching `git worktree list`'s real
current output, and exactly 3 from the track source (Track A, B, C). If
the counts are wildly different (e.g. hundreds), stop and investigate
before ever running without `--dry-run` — that would mean a source parser
is over-matching.

- [ ] **Step 4: Real dry-run of plan-candidates listing**

Run: `python -m tools.kanban_sync plan-candidates`

Expected: a list of `.md` filenames under `docs/superpowers/plans/`,
excluding `2026-08-26-active-tracks-board.md` itself and every plan
already referenced by that file's text (Tracks A/B/C's canonical docs —
`2026-08-26-subscription-churn-investigation.md`,
`2026-08-25-realtime-data-plane-remediation.md`,
`2026-08-26-economic-strategy-effectiveness-investigation.md`,
`2026-08-26-economic-strategy-remediation.md`,
`2026-08-26-autonomous-quality-coordination.md`). Cross-check the printed
list against `ls docs/superpowers/plans/*.md` by eye to confirm nothing
real is silently missing.

- [ ] **Step 5: Do not run a real (non-dry-run) sync as part of this task**

This plan's own verification stops at dry-run — actually writing issues to
the live repo for the first time is a deliberate, separate action for the
user to trigger via the `kanban-board-sync` skill (Task 11) when they're
ready, not something this implementation plan does on its own as part of
"finishing the plan."

- [ ] **Step 6: Final commit — none expected**

If steps 1-4 all pass with no code changes needed, there is nothing new to
commit here. If step 3's counts revealed a real parser bug, fix it,
extend the relevant task's test file with a regression test, re-run this
task's steps 1-4, and commit the fix through the normal `superpowers:
test-driven-development` cycle before considering this plan complete.
