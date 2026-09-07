"""I9: a minimal, documentation-faithful evaluator of Woodpecker's `when:` condition semantics.

Source: https://woodpecker-ci.org/docs/usage/workflow-syntax (fetched 2026-08-25). Quoted directly
in docs/archive/lane-9-tooling-ci-process-governance/research/2026-08-25-quality-event-fault-injection.md: a `when:` block is a
list of condition entries; a pipeline step runs if ANY entry matches (OR across list entries); an
entry matches if ALL of its keys match (AND within one entry). The docs' own example states this
exact hazard: "The step now triggers on main branch, but also if the target branch of a pull
request is main." -- i.e. `branch: main` alone matches a `pull_request` event whose *target*
branch is main, not only a `push` to main.

Before being trusted for a hypothetical/candidate filter that has never been deployed in this
repo, this evaluator was validated against real pipeline records fetched live from
https://ci.webfoundry.dev/api/repos/1/pipelines/<n> (anonymous public API) -- see
tests/test_quality_coordination_when_filter.py's REAL_* specimens and the research doc for the raw
API output each was read from.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PipelineEvent:
    """One real or hypothetical Woodpecker trigger. `branch` already reflects Woodpecker's own
    resolution -- the PR's target/base branch for `pull_request` events, the pushed ref's branch
    for `push` events -- exactly as Woodpecker's own API reports it (confirmed live)."""

    event: str
    branch: str


@dataclass(frozen=True)
class WhenEntry:
    """One entry in a `when:` list. `None` on a key means that key imposes no constraint (absent
    from the YAML entry); a non-None tuple is the set of values that satisfy that key (Woodpecker
    also accepts a bare scalar in real YAML, which is equivalent to a one-element list)."""

    event: tuple[str, ...] | None = None
    branch: tuple[str, ...] | None = None


def matches(when: tuple[WhenEntry, ...], ev: PipelineEvent) -> bool:
    """OR across entries."""
    return any(_entry_matches(entry, ev) for entry in when)


def _entry_matches(entry: WhenEntry, ev: PipelineEvent) -> bool:
    """AND within one entry."""
    if entry.event is not None and ev.event not in entry.event:
        return False
    if entry.branch is not None and ev.branch not in entry.branch:
        return False
    return True
