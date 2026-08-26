"""I9: proves write-boundary semantics without any real credential or network call.

Two properties proven here, both required before any real write credential could ever be
justified for the eventual coordinator (I8's `Coordinator` hands off ESCALATION_ELIGIBLE
decisions; this module is what would stand between that decision and an actual GitHub write):

1. Stale-SHA refusal: a `Decision` computed against an older `main` SHA must refuse to write if
   the real current `main` SHA has since moved on -- the finding may have been fixed, superseded,
   or the whole repo state may no longer match what produced the decision. Directly implements the
   evidence rule's "Remote CI cannot see unpushed local edits ... do not design as if it can" and
   "A claimed finding is still resolved only when a fresh audit of integrated main no longer
   reports it" by refusing to act on a decision that predates the freshest audit.
2. Idempotent, retry-safe writes: the same logical decision (same automation_key + same payload)
   submitted twice -- whether from an at-least-once retry after a timeout, or two coordinator runs
   racing -- must produce exactly one GitHub-side effect, never a duplicate issue.

`FakeGitHubTransport` is in-memory only. No network, no credential, ever -- there is no code path
in this module capable of making an HTTP call.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class Decision:
    automation_key: str
    main_sha: str
    action: str
    payload: str


@dataclass
class FakeGitHubTransport:
    """In-memory stand-in for a real GitHub write client. Records every call attempt, including
    duplicates it suppressed, so a test can assert on call counts, not just end state."""

    issues: dict[str, str] = field(default_factory=dict)
    call_log: list[str] = field(default_factory=list)
    fail_next_n_calls: int = 0

    def open_or_update_issue(self, key: str, payload: str) -> str:
        self.call_log.append(f"attempt:{key}")
        if self.fail_next_n_calls > 0:
            self.fail_next_n_calls -= 1
            raise ConnectionError("simulated transient failure")
        if key in self.issues and self.issues[key] == payload:
            self.call_log.append(f"noop-duplicate:{key}")
            return self.issues[key]
        self.issues[key] = payload
        self.call_log.append(f"wrote:{key}")
        return payload


class WriteGate:
    """The only thing allowed to call a transport. Refuses a stale decision before ever touching
    the transport -- staleness is checked first, unconditionally, so a refusal is never confused
    with a failed write attempt (test_stale_decision_is_refused_before_touching_transport asserts
    the transport's call_log is empty on refusal, not just that no issue was created)."""

    def __init__(self, transport: FakeGitHubTransport, current_main_sha: Callable[[], str]):
        self.transport = transport
        self._current_main_sha = current_main_sha
        self.refused: list[str] = []

    def submit(self, decision: Decision, retries: int = 0) -> str | None:
        if decision.main_sha != self._current_main_sha():
            self.refused.append(decision.automation_key)
            return None
        attempt = 0
        while True:
            try:
                return self.transport.open_or_update_issue(decision.automation_key, decision.payload)
            except ConnectionError:
                attempt += 1
                if attempt > retries:
                    raise
