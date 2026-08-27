import json

from tests.support.fake_gh_runner import FakeRunner
from tools.kanban_sync import labels
from tools.kanban_sync.github_client import GithubClient
from tools.kanban_sync.sources_worktree import (
    WorktreeInfo, build_worktree_items, collect_worktree_items,
    live_worktree_branches, parse_worktree_list,
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


def test_build_worktree_items_closed_pr_is_in_progress():
    worktrees = [WorktreeInfo(path="/x", branch="feat/x")]

    items = build_worktree_items(worktrees, pr_state_by_branch={"feat/x": "CLOSED"})

    assert items[0].status_label == labels.STATUS_IN_PROGRESS
    assert items[0].done is False


def test_build_worktree_items_no_pr_yet_is_phase_implementing():
    worktrees = [WorktreeInfo(path="/x", branch="feat/x")]

    items = build_worktree_items(worktrees, pr_state_by_branch={"feat/x": None})

    assert items[0].phase_label == labels.PHASE_IMPLEMENTING


def test_build_worktree_items_open_pr_is_phase_verification():
    worktrees = [WorktreeInfo(path="/x", branch="feat/x")]

    items = build_worktree_items(worktrees, pr_state_by_branch={"feat/x": "OPEN"})

    assert items[0].phase_label == labels.PHASE_VERIFICATION


def test_build_worktree_items_merged_pr_is_phase_done():
    worktrees = [WorktreeInfo(path="/x", branch="feat/x")]

    items = build_worktree_items(worktrees, pr_state_by_branch={"feat/x": "MERGED"})

    assert items[0].phase_label == labels.PHASE_DONE


def test_build_worktree_items_closed_pr_is_phase_implementing():
    worktrees = [WorktreeInfo(path="/x", branch="feat/x")]

    items = build_worktree_items(worktrees, pr_state_by_branch={"feat/x": "CLOSED"})

    assert items[0].phase_label == labels.PHASE_IMPLEMENTING


def test_live_worktree_branches_excludes_main():
    worktrees = parse_worktree_list(PORCELAIN)

    result = live_worktree_branches(worktrees)

    assert result == {"feat/autonomous-quality-coordination"}


def test_live_worktree_branches_empty_when_only_main_worktree():
    worktrees = [WorktreeInfo(path="/x", branch="main")]

    result = live_worktree_branches(worktrees)

    assert result == set()


def test_collect_worktree_items_queries_pr_state_per_branch():
    runner = FakeRunner()
    runner.queue(json.dumps([{"state": "OPEN"}]))  # find_pr_state for feat/autonomous-quality-coordination
    client = GithubClient("thesneakattack/kalshi-whale-poc", runner=runner)

    items = collect_worktree_items(PORCELAIN, client)

    assert len(items) == 1
    assert items[0].status_label == labels.STATUS_READY_FOR_REVIEW
