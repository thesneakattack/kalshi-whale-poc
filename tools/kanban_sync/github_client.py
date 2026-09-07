"""Thin, testable wrapper around the `gh` CLI (spec §13: every method
takes an injectable runner so no test performs a real gh/network call).
Implements exactly the primitives sync.py and sources_worktree.py need -
find an issue by its sync marker, create one, adjust labels, close it,
post a plain comment, and look up a branch's PR state. Does not implement
claim/dispatch/event-bus semantics - that's the installed github-issues-
kanban skill's job once these issues exist (spec §2 non-goals).

Every gh invocation passes through GithubClient._invoke, which retries a
transient upstream failure (an HTTP 5xx, or a transport timeout) a bounded
number of times with backoff and leaves every other failure to the caller
exactly as before (issue #240).
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Sequence

from tools.kanban_sync import project_status

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]

log = logging.getLogger(__name__)

_URL_NUMBER_RE = re.compile(r"/issues/(\d+)\s*$")
_LABEL_NOT_FOUND_RE = re.compile(r"'([^']+)' not found")
# gh reports an API failure as `HTTP <status>: <reason> (<url>)`. A transport
# failure carries no status at all - gh is a Go binary, so it surfaces as
# Go's net/http text ("Client.Timeout exceeded", "i/o timeout", "TLS
# handshake timeout"), which the word "timeout" is the common thread of.
_HTTP_STATUS_RE = re.compile(r"\bHTTP (\d{3})\b")
_TIMEOUT_RE = re.compile(r"\btimeout\b", re.IGNORECASE)
# Three retries at 2s/4s/8s (issue #240): long enough to outlast the 504
# that aborted the 2026-08-30 sync half-applied, short enough that a real
# outage still fails the run inside 15s instead of hanging it.
_RETRY_BACKOFF_S: tuple[float, ...] = (2.0, 4.0, 8.0)


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


def _is_transient(result: "subprocess.CompletedProcess[str]") -> bool:
    """A failure GitHub, not the request, is responsible for: a 5xx, or a
    transport timeout that never got a status at all. When gh reports a
    status, that status is the API's real answer about the request - a 401
    is a broken token, a 404 a missing issue, a 422 a bad payload, and the
    label-not-found text create_issue/set_labels recover from is a 422 too -
    so any non-5xx status is never retried, whatever else the text says."""
    text = result.stderr or result.stdout or ""
    status = _HTTP_STATUS_RE.search(text)
    if status:
        return status.group(1).startswith("5")
    return bool(_TIMEOUT_RE.search(text))


class GithubClient:
    def __init__(self, repo: str, runner: Runner = _default_runner,
                 sleep_fn: Callable[[float], None] = time.sleep) -> None:
        self._repo = repo
        self._runner = runner
        # Injectable so the retry tests assert the backoff schedule instead
        # of waiting 14s for it; the production default is the real clock.
        self._sleep = sleep_fn

    def _invoke(self, args: Sequence[str]) -> "subprocess.CompletedProcess[str]":
        """The one place every gh call goes through - _run's --repo-scoped
        calls and the project/milestone/rate-limit calls that omit --repo
        alike - so the transient-retry policy exists exactly once (issue
        #240: `gh pr list` died on `HTTP 504: 504 Gateway Timeout` and the
        2026-08-30 sync aborted half-applied; items before it reconciled,
        items after it not). Safe because the sync is idempotent - find-or-
        create by marker - so a retried create cannot double-create.

        Returns the final result rather than raising: each caller keeps
        deciding what a non-zero exit means, exactly as before, so the error
        surfaced once the budget is spent is gh's own, never a wrapper's.
        Each retry is logged at WARNING so a flaky network is visible, not
        silent (the CLI configures no logging; Python's last-resort handler
        prints WARNING and above to stderr)."""
        for attempt, delay in enumerate(_RETRY_BACKOFF_S, start=1):
            result = self._runner(args)
            if result.returncode == 0 or not _is_transient(result):
                return result
            log.warning(
                "gh %s failed transiently (attempt %d/%d), retry in %.0fs: %s",
                " ".join(args[1:]), attempt, len(_RETRY_BACKOFF_S) + 1, delay,
                (result.stderr or result.stdout or "").strip(),
            )
            self._sleep(delay)
        return self._runner(args)

    def _run(self, args: Sequence[str]) -> str:
        result = self._invoke(["gh", *args, "--repo", self._repo])
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
            "--json", "number,state,labels,body", "--limit", "5",
        ])
        results = json.loads(stdout)
        # GitHub's issue search is fuzzy full-text, not a literal match - the
        # top hit can be an unrelated issue that merely shares tokens with the
        # marker (found live 2026-08-30, issue #90: a search for one plan's
        # marker matched an unrelated roadmap-tracked issue and, trusted
        # blindly, caused a real wrongful `close_issue` call). Only trust a
        # result whose body actually contains the literal marker text.
        for item in results:
            if marker in (item.get("body") or ""):
                return IssueState(
                    number=item["number"],
                    open=item["state"] == "OPEN",
                    labels=frozenset(label["name"] for label in item["labels"]),
                )
        return None

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

    def list_closed_issues(self) -> list[IssueState]:
        """Bulk-lists every CLOSED issue on the repo, unscoped by label -
        the backfill_closed_status one-time pass (root cause A: 3 of 4 close
        paths in sync.py never wrote the Project's Status field) needs every
        closed issue regardless of its type:* label, unlike
        list_open_by_label's per-family scoping."""
        stdout = self._run([
            "issue", "list", "--state", "closed",
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
        result = self._invoke(["gh", "api", "rate_limit", "--jq", ".resources.graphql"])
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

    # --- read-only PR readers for `review-tier` (the AI-assisted engineering
    # principles design, §6.2). None of these writes anything.

    def get_pr_files(self, number: int) -> list[str]:
        """Every changed path in the PR.

        REST plus --paginate, not `gh pr view --json files`: that field caps at
        100 entries and PR #660 changed 124 (verified live 2026-09-07). A
        truncated file list would silently classify a large PR as Tier B, which
        is the one failure this reader must not have. `gh api` takes no --repo
        flag, so this goes through _invoke rather than _run, exactly as
        graphql_rate_limit does - the transient-retry policy still applies.
        """
        result = self._invoke([
            "gh", "api", f"repos/{self._repo}/pulls/{number}/files",
            "--paginate", "--jq", ".[].filename",
        ])
        if result.returncode != 0:
            raise GithubCliError(
                f"gh api pulls/{number}/files failed: {result.stderr or result.stdout}"
            )
        return [line for line in result.stdout.splitlines() if line.strip()]

    def get_pr_diff(self, number: int) -> str:
        """The raw unified diff, for the data-model rule's line pattern."""
        return self._run(["pr", "diff", str(number)])

    def get_pr_labels(self, number: int) -> frozenset[str]:
        """The PR's own labels plus those of every issue it closes: a code PR
        often carries no label while the issue it closes carries the concern."""
        stdout = self._run([
            "pr", "view", str(number), "--json", "labels,closingIssuesReferences",
        ])
        data = json.loads(stdout)
        names = {label["name"] for label in data.get("labels") or []}
        for issue in data.get("closingIssuesReferences") or []:
            state = self.get_issue(issue["number"])
            if state is not None:
                names |= set(state.labels)
        return frozenset(names)

    def list_pr_comments(self, number: int) -> list[str]:
        """Comment bodies in posted order. Issue-style comments only - the
        review artifacts this repo posts are all of that kind."""
        stdout = self._run(["pr", "view", str(number), "--json", "comments"])
        return [comment["body"] for comment in json.loads(stdout)["comments"]]

    def get_sub_issues_summary(self, issue_number: int) -> tuple[int, int]:
        """Returns (completed, total) sub-issue counts. Repo-scoped (unlike
        the project-object methods above), so uses _run's automatic --repo
        flag like find_by_marker/create_issue already do."""
        stdout = self._run(["issue", "view", str(issue_number), "--json", "subIssuesSummary"])
        data = json.loads(stdout)["subIssuesSummary"]
        return data["completed"], data["total"]

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
        result = self._invoke([
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
        result = self._invoke([
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
        result = self._invoke([
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
        result = self._invoke([
            "gh", "api", "-X", "GET", f"repos/{self._repo}/milestones",
            "-f", "state=all", "-f", "per_page=100",
        ])
        if result.returncode != 0:
            raise GithubCliError(f"gh api milestones list failed: {result.stderr or result.stdout}")
        for milestone in json.loads(result.stdout):
            if milestone["title"] == title:
                return milestone["number"]
        return None
