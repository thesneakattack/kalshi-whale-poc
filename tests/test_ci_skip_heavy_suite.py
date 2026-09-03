"""scripts/ci-skip-heavy-suite.sh's SAFE_PATTERN classification, run against a
disposable synthetic repository (tests/support/synthetic_git_repo.py) - not this
repo. Focused on the 2026-09-03 fix (a real PR touching only .gitignore paid the
full suite because SAFE_PATTERN never covered it) rather than re-verifying the
whole script's pre-existing docs/.claude-only logic, which predates this file and
was manually verified in docs/superpowers/research/2026-08-25-ci-skip-heavy-suite-verification.md.
Needs real git and sh (host, CI - the fastapi container has git but this still
skips gracefully if either binary is ever absent, matching test_cleanup_worktrees.py's
own convention)."""
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.support.synthetic_git_repo import git, make_synthetic_repo

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "ci-skip-heavy-suite.sh"

pytestmark = pytest.mark.skipif(shutil.which("git") is None or shutil.which("sh") is None,
                                 reason="needs git and sh")


def _make_repo_with_origin(tmp_path: Path) -> Path:
    """A synthetic repo plus a second bare repo standing in for `origin`, so
    ci-skip-heavy-suite.sh's `git fetch origin main` / `git merge-base
    origin/main HEAD` path is exercised for real, not just its no-origin
    fail-safe branch."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    git(["init", "--bare", "-b", "main"], origin)
    repo = make_synthetic_repo(tmp_path)
    git(["remote", "add", "origin", str(origin)], repo)
    git(["push", "-u", "origin", "main"], repo)
    return repo


def _run_script(repo: Path, branch_env: dict) -> str:
    env = {"PATH": __import__("os").environ["PATH"], **branch_env}
    result = subprocess.run(["sh", str(SCRIPT)], cwd=repo, capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_gitignore_only_change_on_a_feature_branch_skips(tmp_path):
    repo = _make_repo_with_origin(tmp_path)
    git(["checkout", "-b", "feature/x"], repo)
    (repo / ".gitignore").write_text("data/quarantine/\n")
    git(["add", ".gitignore"], repo)
    git(["commit", "-m", "gitignore data/quarantine/"], repo)
    assert _run_script(repo, {"CI_PIPELINE_EVENT": "push", "CI_COMMIT_BRANCH": "feature/x"}) == "SKIP"


def test_gitattributes_and_editorconfig_only_changes_skip(tmp_path):
    repo = _make_repo_with_origin(tmp_path)
    git(["checkout", "-b", "feature/y"], repo)
    (repo / ".gitattributes").write_text("*.py text\n")
    (repo / ".editorconfig").write_text("root = true\n")
    git(["add", ".gitattributes", ".editorconfig"], repo)
    git(["commit", "-m", "add editor/git metadata files"], repo)
    assert _run_script(repo, {"CI_PIPELINE_EVENT": "push", "CI_COMMIT_BRANCH": "feature/y"}) == "SKIP"


def test_a_real_code_change_alongside_gitignore_still_runs(tmp_path):
    repo = _make_repo_with_origin(tmp_path)
    git(["checkout", "-b", "feature/z"], repo)
    (repo / "services").mkdir()
    (repo / "services" / "example.py").write_text("x = 1\n")
    (repo / ".gitignore").write_text("data/quarantine/\n")
    git(["add", "services/example.py", ".gitignore"], repo)
    git(["commit", "-m", "real code change plus gitignore"], repo)
    assert _run_script(repo, {"CI_PIPELINE_EVENT": "push", "CI_COMMIT_BRANCH": "feature/z"}) == "RUN"
