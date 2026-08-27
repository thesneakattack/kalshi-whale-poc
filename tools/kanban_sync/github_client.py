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

from tools.kanban_sync import project_status

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]

_URL_NUMBER_RE = re.compile(r"/issues/(\d+)\s*$")
_LABEL_NOT_FOUND_RE = re.compile(r"'([^']+)' not found")


@dataclass(frozen=True)
class IssueState:
    number: int
    open: bool
    labels: frozenset[str]
    body: str = ""


class GithubCliError(RuntimeError):
    pass


def _default_runner(args: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(args, capture_output=True, text=True)


class GithubClient:
    def __init__(self, repo: str, runner: Runner = _default_runner) -> None:
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
        # GitHub's issue search does not match the literal `<!--`/`-->` HTML-comment
        # delimiters (verified live 2026-08-27 against the real API - a search for the
        # full wrapped marker always returns zero results even for an issue whose body
        # genuinely contains that exact text). Search on the marker's inner content only.
        search_term = marker.strip()
        if search_term.startswith("<!--") and search_term.endswith("-->"):
            search_term = search_term[4:-3].strip()
        stdout = self._run([
            "issue", "list", "--search", search_term, "--state", "all",
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

    def list_open_by_label(self, label: str) -> list[IssueState]:
        """Bulk-lists every currently-OPEN issue carrying `label`, with body
        included so a caller can markers.parse_marker() it to recover
        (kind, key) - unlike find_by_marker, not scoped to one marker. The
        only mechanism that can notice a source item that has vanished
        entirely from a run's scan (see sync.close_stale_worktree_issues)."""
        stdout = self._run([
            "issue", "list", "--label", label, "--state", "open",
            "--json", "number,state,labels,body", "--limit", "1000",
        ])
        results = json.loads(stdout)
        return [
            IssueState(
                number=item["number"],
                open=item["state"] == "OPEN",
                labels=frozenset(l["name"] for l in item["labels"]),
                body=item["body"],
            )
            for item in results
        ]

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

    def set_labels(self, number: int, add: Sequence[str], remove: Sequence[str]) -> None:
        if not add and not remove:
            return
        args = ["issue", "edit", str(number)]
        for label in add:
            args += ["--add-label", label]
        for label in remove:
            args += ["--remove-label", label]
        # depends-on:#N labels are created per-dependency, on demand - unlike the fixed
        # status:*/type:* set (tools/kanban_sync/labels.py), gh never has them
        # pre-created. `gh issue edit --add-label` does not auto-create a missing
        # label, so create it and retry rather than propagate the error (found live
        # 2026-08-27, the first real sync's first depends-on edge). Bounded to one
        # retry per label in `add` so a genuinely unrelated failure still raises.
        for _ in range(len(add) + 1):
            try:
                self._run(args)
                return
            except GithubCliError as exc:
                match = _LABEL_NOT_FOUND_RE.search(str(exc))
                if not match:
                    raise
                self._run(["label", "create", match.group(1), "--color", "ededed"])
        raise GithubCliError(f"gh issue edit {number} still failing after creating missing labels")

    def close_issue(self, number: int) -> None:
        self._run(["issue", "close", str(number)])

    def post_comment(self, number: int, body: str) -> None:
        self._run(["issue", "comment", str(number), "--body", body])

    def graphql_rate_limit(self) -> tuple[int, int]:
        """Returns (remaining, reset_epoch_seconds) for the GraphQL quota. `gh project`
        and `gh issue edit` are both GraphQL-backed under the hood despite looking like
        plain CLI commands (found live 2026-08-27 - a bulk board-population run
        exhausted the 5000/5000 quota partway through with no advance warning, and the
        failure surfaced as a misleading 'unknown owner type' error rather than
        anything rate-limit-shaped). Not repo-scoped - rate_limit is a global endpoint -
        so this bypasses _run's automatic --repo flag rather than reusing it."""
        result = self._runner(["gh", "api", "rate_limit", "--jq", ".resources.graphql"])
        if result.returncode != 0:
            raise GithubCliError(
                f"gh api rate_limit failed: {result.stderr or result.stdout}"
            )
        data = json.loads(result.stdout)
        return data["remaining"], data["reset"]

    def find_pr_state(self, branch: str) -> str | None:
        stdout = self._run([
            "pr", "list", "--head", branch, "--state", "all",
            "--json", "state", "--limit", "1",
        ])
        results = json.loads(stdout)
        if not results:
            return None
        return results[0]["state"]

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

    def ensure_on_project(self, issue_number: int) -> str:
        """Idempotently adds the issue to the board's project if not already
        present; returns the project item's own node ID (PVTI_..., distinct
        from the issue's node ID) needed by set_project_status's --id.
        Not repo-scoped (owner/project-scoped) - bypasses _run's automatic
        --repo flag like graphql_rate_limit already does."""
        issue_url = f"https://github.com/{self._repo}/issues/{issue_number}"
        result = self._runner([
            "gh", "project", "item-add", str(project_status.PROJECT_NUMBER),
            "--owner", project_status.PROJECT_OWNER,
            "--url", issue_url,
            "--format", "json",
        ])
        if result.returncode != 0:
            raise GithubCliError(f"gh project item-add failed: {result.stderr or result.stdout}")
        return json.loads(result.stdout)["id"]

    def set_project_status(self, item_id: str, status: str) -> None:
        """Sets the project's Status field by GraphQL node ID, not by
        --field/--value name, so a UI rename of the option's display text
        doesn't silently break this call."""
        try:
            option_id = project_status.STATUS_OPTION_IDS[status]
        except KeyError:
            raise GithubCliError(f"unknown project Status option: {status!r}") from None
        result = self._runner([
            "gh", "project", "item-edit",
            "--id", item_id,
            "--field-id", project_status.STATUS_FIELD_ID,
            "--project-id", project_status.PROJECT_ID,
            "--single-select-option-id", option_id,
        ])
        if result.returncode != 0:
            raise GithubCliError(f"gh project item-edit failed: {result.stderr or result.stdout}")

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
        appending it as a query string). per_page=100 raises the safe ceiling
        from GitHub's default page size of 30."""
        result = self._runner([
            "gh", "api", "-X", "GET", f"repos/{self._repo}/milestones",
            "-f", "state=all", "-f", "per_page=100",
        ])
        if result.returncode != 0:
            raise GithubCliError(f"gh api milestones list failed: {result.stderr or result.stdout}")
        for milestone in json.loads(result.stdout):
            if milestone["title"] == title:
                return milestone["number"]
        return None
