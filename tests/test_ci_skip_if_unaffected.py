"""scripts/ci-skip-if-unaffected.sh's include-based path relevance check, run
against a disposable synthetic repository (tests/support/synthetic_git_repo.py)
- not this repo. Unlike ci-skip-heavy-suite.sh (exclude-based, one universal
docs/.claude pattern every workflow shares), this script is per-caller and
narrower - a caller supplies its own RELEVANT_PATTERN, and this test exercises
that contract generically rather than any one workflow's specific pattern."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.support.synthetic_git_repo import git, make_synthetic_repo

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "ci-skip-if-unaffected.sh"

pytestmark = pytest.mark.skipif(shutil.which("git") is None or shutil.which("sh") is None,
                                 reason="needs git and sh")


def _make_repo_with_origin(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    origin.mkdir()
    git(["init", "--bare", "-b", "main"], origin)
    repo = make_synthetic_repo(tmp_path)
    git(["remote", "add", "origin", str(origin)], repo)
    git(["push", "-u", "origin", "main"], repo)
    return repo


def _run_script(repo: Path, relevant_pattern: str | None, branch_env: dict) -> subprocess.CompletedProcess:
    env = {"PATH": os.environ["PATH"], **branch_env}
    if relevant_pattern is not None:
        env["RELEVANT_PATTERN"] = relevant_pattern
    return subprocess.run(["sh", str(SCRIPT)], cwd=repo, capture_output=True, text=True, env=env)


def test_missing_relevant_pattern_fails_safe_to_run(tmp_path):
    repo = _make_repo_with_origin(tmp_path)
    result = _run_script(repo, None, {"CI_PIPELINE_EVENT": "push", "CI_COMMIT_BRANCH": "feature/x"})
    assert result.returncode == 0
    assert result.stdout.strip() == "RUN"
    assert "RELEVANT_PATTERN" in result.stderr


def test_skips_when_no_changed_file_matches_the_relevant_pattern(tmp_path):
    repo = _make_repo_with_origin(tmp_path)
    git(["checkout", "-b", "feature/x"], repo)
    (repo / "docs").mkdir()
    (repo / "docs" / "notes.md").write_text("unrelated\n")
    git(["add", "docs/notes.md"], repo)
    git(["commit", "-m", "unrelated doc change"], repo)
    result = _run_script(repo, r"^requirements.*\.txt$",
                          {"CI_PIPELINE_EVENT": "push", "CI_COMMIT_BRANCH": "feature/x"})
    assert result.returncode == 0 and result.stdout.strip() == "SKIP"


def test_runs_when_a_changed_file_matches_the_relevant_pattern(tmp_path):
    repo = _make_repo_with_origin(tmp_path)
    git(["checkout", "-b", "feature/y"], repo)
    (repo / "requirements-dev.txt").write_text("pytest==9.0.3\n")
    git(["add", "requirements-dev.txt"], repo)
    git(["commit", "-m", "bump a pin"], repo)
    result = _run_script(repo, r"^requirements.*\.txt$",
                          {"CI_PIPELINE_EVENT": "push", "CI_COMMIT_BRANCH": "feature/y"})
    assert result.returncode == 0 and result.stdout.strip() == "RUN"


def test_no_changes_at_all_skips(tmp_path):
    repo = _make_repo_with_origin(tmp_path)
    git(["checkout", "-b", "feature/z"], repo)
    result = _run_script(repo, r"^requirements.*\.txt$",
                          {"CI_PIPELINE_EVENT": "push", "CI_COMMIT_BRANCH": "feature/z"})
    assert result.returncode == 0 and result.stdout.strip() == "SKIP"
