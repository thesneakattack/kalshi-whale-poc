"""I9: prove write-boundary semantics (stale-decision refusal, idempotent/retry-safe writes)
without any real credential or network call. FakeGitHubTransport is in-memory only.

Prototype status: EXPERIMENTAL, part of the I8 throwaway coordination-sim package. Nothing here
is wired to a real GitHub client or credential -- see docs/superpowers/research/2026-08-25-
quality-event-fault-injection.md.
"""
from __future__ import annotations

from tools.quality_coordination_sim.write_gate import Decision, FakeGitHubTransport, WriteGate


def _gate(sha: str = "abc123") -> tuple[WriteGate, FakeGitHubTransport, list]:
    transport = FakeGitHubTransport()
    current = [sha]  # mutable box so a test can change "current main" after the gate is built
    gate = WriteGate(transport, current_main_sha=lambda: current[0])
    return gate, transport, current


# --- checklist: "test stale main SHA handling: a decision generated for old main must refuse
#     a write" -----------------------------------------------------------------------------


def test_stale_decision_is_refused_before_touching_transport():
    gate, transport, current = _gate(sha="sha-A")
    decision = Decision(automation_key="k1", main_sha="sha-A", action="open_issue", payload="p1")
    current[0] = "sha-B"  # main moved on after the decision was computed
    result = gate.submit(decision)
    assert result is None
    assert gate.refused == ["k1"]
    assert transport.call_log == []  # refused BEFORE the transport was ever called -- not a
    #                                   failed write, a write that never happened


def test_fresh_decision_matching_current_sha_writes():
    gate, transport, _ = _gate(sha="sha-A")
    decision = Decision(automation_key="k1", main_sha="sha-A", action="open_issue", payload="p1")
    result = gate.submit(decision)
    assert result == "p1"
    assert transport.issues == {"k1": "p1"}


# --- checklist: "test duplicate/retry behavior for the chosen reporting/escalation action
#     using a fake GitHub transport" ---------------------------------------------------------


def test_duplicate_submit_is_idempotent_no_second_write():
    gate, transport, _ = _gate(sha="sha-A")
    decision = Decision(automation_key="k1", main_sha="sha-A", action="open_issue", payload="p1")
    gate.submit(decision)
    gate.submit(decision)  # e.g. two coordinator runs raced, or a caller retried unconditionally
    assert transport.issues == {"k1": "p1"}  # still exactly one issue, not two
    assert transport.call_log.count("wrote:k1") == 1
    assert transport.call_log.count("noop-duplicate:k1") == 1


def test_retry_after_transient_failure_succeeds_without_duplicate():
    gate, transport, _ = _gate(sha="sha-A")
    transport.fail_next_n_calls = 1
    decision = Decision(automation_key="k1", main_sha="sha-A", action="open_issue", payload="p1")
    result = gate.submit(decision, retries=1)
    assert result == "p1"
    assert transport.call_log.count("wrote:k1") == 1  # exactly one real write despite the retry
    assert transport.issues == {"k1": "p1"}


def test_retry_exhausted_raises_and_leaves_no_partial_write():
    gate, transport, _ = _gate(sha="sha-A")
    transport.fail_next_n_calls = 5
    decision = Decision(automation_key="k1", main_sha="sha-A", action="open_issue", payload="p1")
    raised = False
    try:
        gate.submit(decision, retries=1)
    except ConnectionError:
        raised = True
    assert raised
    assert "k1" not in transport.issues  # a failed attempt leaves no partial/ghost record


def test_two_different_decisions_for_same_key_update_not_duplicate():
    """A later decision with the same automation_key but different payload (e.g. updated
    occurrence count) updates the same GitHub-side record -- it is still one issue, not a new
    one, matching I1's identity contract (occurrence-count changes are updates, not new items)."""
    gate, transport, current = _gate(sha="sha-A")
    d1 = Decision(automation_key="k1", main_sha="sha-A", action="open_issue", payload="p1")
    gate.submit(d1)
    current[0] = "sha-B"
    d2 = Decision(automation_key="k1", main_sha="sha-B", action="open_issue", payload="p2")
    gate.submit(d2)
    assert transport.issues == {"k1": "p2"}
    assert transport.call_log.count("wrote:k1") == 2  # two real content changes -> two real writes
    assert transport.call_log.count("noop-duplicate:k1") == 0
