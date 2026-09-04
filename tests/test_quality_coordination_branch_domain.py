import subprocess
from datetime import datetime, timedelta, timezone

from tools import coordination_engine as ce
from tools.quality_coordination import (
    CLUSTER_WINDOW_MINUTES, FLOOR_HOURS_BRANCH, _branch_first_commit_at, _cluster_siblings,
    collect_branch_signals,
)

AT = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)


class _StubRunner:
    """Records calls, returns queued CompletedProcess results in order - same shape as
    tests/support/fake_gh_runner.py's FakeRunner, redefined locally since this module has
    no dependency on tests/support (that module is kanban_sync's own test infrastructure,
    not shared repo-wide)."""

    def __init__(self):
        self.calls = []
        self._responses = []

    def queue(self, stdout="", returncode=0, stderr=""):
        self._responses.append(subprocess.CompletedProcess([], returncode, stdout, stderr))

    def __call__(self, args):
        self.calls.append(list(args))
        return self._responses.pop(0)


def test_floor_and_cluster_window_are_positive_documented_constants():
    assert FLOOR_HOURS_BRANCH > 0
    assert CLUSTER_WINDOW_MINUTES > 0


def test_branch_first_commit_at_parses_oldest_unique_commit_date(monkeypatch):
    runner = _StubRunner()
    # git log main..feat/x --format=%cI lists newest-first; oldest is the last line.
    runner.queue("2026-08-27T12:05:00-05:00\n2026-08-27T12:00:00-05:00\n")

    result = _branch_first_commit_at("feat/x", "main", git_runner=runner)

    assert result == datetime.fromisoformat("2026-08-27T12:00:00-05:00")


def test_branch_first_commit_at_returns_none_when_branch_has_no_unique_commits(monkeypatch):
    runner = _StubRunner()
    runner.queue("")

    assert _branch_first_commit_at("feat/x", "main", git_runner=runner) is None


def test_cluster_siblings_groups_branches_within_window():
    times = {
        "docs/a": AT,
        "docs/b": AT + timedelta(minutes=3),
        "docs/c": AT + timedelta(minutes=18),
        "feat/lonely": AT - timedelta(days=5),
    }

    clusters = _cluster_siblings(times, window_minutes=20.0)

    assert clusters["docs/a"] == frozenset({"docs/b", "docs/c"})
    assert clusters["docs/b"] == frozenset({"docs/a", "docs/c"})
    assert clusters["docs/c"] == frozenset({"docs/a", "docs/b"})
    assert clusters["feat/lonely"] == frozenset()


def test_cluster_siblings_empty_when_all_branches_far_apart():
    times = {"a": AT, "b": AT + timedelta(hours=5)}

    clusters = _cluster_siblings(times, window_minutes=20.0)

    assert clusters["a"] == frozenset()
    assert clusters["b"] == frozenset()


def test_collect_branch_signals_suppresses_a_coordinated_dispatch_cluster(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "DB_PATH", tmp_path / "q.db")
    with ce._connect() as conn:
        git_runner = _StubRunner()
        # Three branches, each with one commit ~2 minutes apart from the others - a cluster.
        git_runner.queue("2026-08-27T12:00:00-05:00\n")
        git_runner.queue("2026-08-27T12:02:00-05:00\n")
        git_runner.queue("2026-08-27T12:04:00-05:00\n")
        gh_runner = _StubRunner()
        gh_runner.queue("[]")  # no PR for docs/a
        gh_runner.queue("[]")  # no PR for docs/b
        gh_runner.queue("[]")  # no PR for docs/c
        woodpecker_runner = _StubRunner()  # no CI runs yet for any of the three - queue nothing

        signals, suppressed, immediate = collect_branch_signals(
            ["docs/a", "docs/b", "docs/c"],
            git_runner=git_runner, gh_runner=gh_runner, woodpecker_runner=woodpecker_runner,
            conn=conn, worktrees_root=tmp_path / "worktrees",
            at=datetime(2026, 8, 27, 17, 5, 0, tzinfo=timezone.utc),
        )

        assert {"branch:docs/a", "branch:docs/b", "branch:docs/c"} == suppressed
        assert immediate == frozenset()
        assert len(signals) == 3


def test_collect_branch_signals_does_not_suppress_a_lone_stale_branch(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "DB_PATH", tmp_path / "q.db")
    with ce._connect() as conn:
        git_runner = _StubRunner()
        git_runner.queue("2026-08-01T12:00:00-05:00\n")  # weeks old, no siblings nearby
        gh_runner = _StubRunner()
        gh_runner.queue("[]")
        woodpecker_runner = _StubRunner()

        signals, suppressed, immediate = collect_branch_signals(
            ["feat/abandoned"],
            git_runner=git_runner, gh_runner=gh_runner, woodpecker_runner=woodpecker_runner,
            conn=conn, worktrees_root=tmp_path / "worktrees",
            at=datetime(2026, 8, 27, 17, 5, 0, tzinfo=timezone.utc),
        )

        assert suppressed == frozenset()
        assert signals[0].identity == "branch:feat/abandoned"


def test_collect_branch_signals_marks_a_real_ci_failure_immediate(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "DB_PATH", tmp_path / "q.db")
    with ce._connect() as conn:
        git_runner = _StubRunner()
        git_runner.queue("2026-08-27T12:00:00-05:00\n")
        gh_runner = _StubRunner()
        gh_runner.queue('[{"state": "OPEN"}]')  # find_pr_state-shaped: a JSON array, json.loads'd
        gh_runner.queue("failure\n")  # commit-status-shaped: `gh api ... --jq ".state"` raw-
        # unquotes a scalar jq result, so this is the literal stdout _commit_status reads
        # directly - not a JSON blob to parse (unlike the pr-list response above, which uses
        # `gh ... --json` and is real JSON).
        woodpecker_runner = _StubRunner()

        signals, suppressed, immediate = collect_branch_signals(
            ["feat/broken"],
            git_runner=git_runner, gh_runner=gh_runner, woodpecker_runner=woodpecker_runner,
            conn=conn, worktrees_root=tmp_path / "worktrees",
            at=datetime(2026, 8, 27, 17, 5, 0, tzinfo=timezone.utc),
        )

        assert immediate == frozenset({"branch:feat/broken"})


def test_collect_branch_signals_flags_possibly_stuck_when_step_unchanged_two_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "DB_PATH", tmp_path / "q.db")
    with ce._connect() as conn:
        # Prime signal_state with a prior run's payload for this identity, same shape a real
        # first apply_observation call would have written.
        from tools.coordination_engine import Signal, apply_observation
        prior_payload = {"pr_state": "OPEN", "ci_status": "pending", "furthest_step": "browser-e2e",
                          "furthest_step_started_at": "2026-08-27T16:00:00+00:00"}
        apply_observation(
            conn, [Signal("branch:feat/slow", "branch", prior_payload, True)],
            datetime(2026, 8, 27, 16, 5, 0, tzinfo=timezone.utc), floor_hours={"branch": 6.0},
        )

        git_runner = _StubRunner()
        git_runner.queue("2026-08-27T15:00:00-05:00\n")
        gh_runner = _StubRunner()
        gh_runner.queue('[{"state": "OPEN"}]')  # find_pr_state-shaped: real JSON, json.loads'd
        gh_runner.queue("pending\n")  # commit-status-shaped: raw --jq output, see the sibling
        # test above for why this isn't JSON-wrapped.
        woodpecker_runner = _StubRunner()
        # 1787846400 == 2026-08-27T16:00:00+00:00 (verified against datetime.fromtimestamp) -
        # matching prior_payload's furthest_step_started_at above. An earlier literal here
        # (1756310400) resolved to 2025-08-27T16:00:00+00:00 instead - a one-year-off fixture
        # bug found while running this test, not an implementation defect.
        woodpecker_runner.queue(
            '{"steps": [{"name": "browser-e2e", "started": 1787846400, "stopped": 0}]}'
        )

        signals, suppressed, immediate = collect_branch_signals(
            ["feat/slow"],
            git_runner=git_runner, gh_runner=gh_runner, woodpecker_runner=woodpecker_runner,
            conn=conn, worktrees_root=tmp_path / "worktrees",
            at=datetime(2026, 8, 27, 17, 5, 0, tzinfo=timezone.utc),  # same run 1h later, same step
        )

        assert signals[0].payload["possibly_stuck"] is True
