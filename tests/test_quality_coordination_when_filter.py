"""I9: prove Woodpecker `when:` event/branch filter semantics using a documentation-faithful
evaluator, validated against real pipeline records fetched live from this repo's own Woodpecker
instance (https://ci.webfoundry.dev/api/repos/1/pipelines/<n>, anonymous public API, 2026-08-25)
-- not synthetic assumptions. See docs/superpowers/research/2026-08-25-quality-event-fault-
injection.md for the full evidence chain and citations.

Prototype status: EXPERIMENTAL, part of the I8 throwaway coordination-sim package. Proves a
*candidate* filter's semantics before any real privileged Woodpecker step exists in this repo --
`.woodpecker/*.yml` today has no branch-restricted step at all (grep confirms every current `when:`
is `event: [push, pull_request]` with no `branch:` key).
"""
from __future__ import annotations

from tools.quality_coordination_sim.when_filter import PipelineEvent, WhenEntry, matches

# Real, live-fetched specimens (2026-08-25) -- not invented. See the research doc for the raw
# `curl`/`gh api` output each of these was read from.
REAL_MAIN_PUSH = PipelineEvent(event="push", branch="main")  # pipeline 163, commit fed3fee
REAL_PR_TARGETING_MAIN = PipelineEvent(event="pull_request", branch="main")  # pipelines 92/94/96, PR #3
REAL_PUSH_OTHER_BRANCH = PipelineEvent(
    event="push", branch="chore/autonomous-quality-coordination-investigation"
)  # pipelines 164/165, this branch's own I7/I8 pushes
REAL_MERGED_PR_PUSH_TO_MAIN = PipelineEvent(event="push", branch="main")  # every real merge, e.g. 27834a6

NAIVE = (WhenEntry(branch=("main",)),)
TRUSTED = (WhenEntry(event=("push",), branch=("main",)),)


def test_naive_branch_only_filter_matches_real_main_push():
    assert matches(NAIVE, REAL_MAIN_PUSH) is True


def test_naive_branch_only_filter_wrongly_matches_real_pr_targeting_main():
    """The documented hazard (docs/superpowers/research/2026-08-25-autonomous-quality-threat-model.md:
    'a Woodpecker branch: main condition alone also matches a PR whose target is main'),
    reproduced against a real recorded pipeline, not a hypothetical one."""
    assert matches(NAIVE, REAL_PR_TARGETING_MAIN) is True


def test_naive_branch_only_filter_excludes_push_to_other_branch():
    assert matches(NAIVE, REAL_PUSH_OTHER_BRANCH) is False


# --- checklist: "prove a candidate privileged filter is NOT selected on pull_request events
#     targeting main" -----------------------------------------------------------------------


def test_trusted_filter_excludes_real_pr_targeting_main():
    assert matches(TRUSTED, REAL_PR_TARGETING_MAIN) is False


# --- checklist: "prove the same filter IS selected on the intended trusted main-push event" -


def test_trusted_filter_matches_real_main_push():
    assert matches(TRUSTED, REAL_MAIN_PUSH) is True
    assert matches(TRUSTED, REAL_MERGED_PR_PUSH_TO_MAIN) is True


def test_trusted_filter_excludes_push_to_other_branch():
    assert matches(TRUSTED, REAL_PUSH_OTHER_BRANCH) is False


# --- OR-across-entries / AND-within-entry semantics, per the fetched docs --------------------


def test_or_across_entries_cron_or_trusted_push():
    multi = (WhenEntry(event=("cron",)), WhenEntry(event=("push",), branch=("main",)))
    assert matches(multi, PipelineEvent(event="cron", branch="main")) is True
    assert matches(multi, REAL_MAIN_PUSH) is True
    assert matches(multi, REAL_PR_TARGETING_MAIN) is False


def test_and_within_entry_both_keys_must_match():
    entry = (WhenEntry(event=("push",), branch=("main",)),)
    # event matches, branch doesn't -> whole entry fails (AND, not OR, within one entry)
    assert matches(entry, PipelineEvent(event="push", branch="not-main")) is False
    # branch matches, event doesn't
    assert matches(entry, PipelineEvent(event="manual", branch="main")) is False
