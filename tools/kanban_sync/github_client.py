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
_LABEL_NOT_FOUND_RE = re.compile(r"'([^']+)' not found")


@dataclass(frozen=True)
class IssueState:
    number: int
    open: bool
    labels: frozenset[str]


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

    def create_issue(self, title: str, body: str, labels: Sequence[str]) -> IssueState:
        args = ["issue", "create", "--title", title, "--body", body]
        for label in labels:
            args += ["--label", label]
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

    def find_pr_state(self, branch: str) -> str | None:
        stdout = self._run([
            "pr", "list", "--head", branch, "--state", "all",
            "--json", "state", "--limit", "1",
        ])
        results = json.loads(stdout)
        if not results:
            return None
        return results[0]["state"]
