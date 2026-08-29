"""orient.sh (the SessionStart hook) must print the orientation for whatever checkout
the launcher hands it, read live sessions from guard_workflow.py --sessions, and never
fail the hook. Driven against a throwaway checkout with a stub guard and a stub ddev so
nothing depends on the host. Needs git and bash (host, CI)."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "orient.sh"

pytestmark = pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None,
                                reason="needs git and bash")


def _checkout(tmp_path: Path) -> tuple[Path, dict]:
    repo = tmp_path / "repo"
    (repo / ".claude" / "hooks").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "feat/x"], cwd=repo, check=True)
    (repo / ".claude" / "hooks" / "guard_workflow.py").write_text(
        "import sys\nsys.stdout.write('111\\tother\\t/elsewhere/wt\\n4242\\tself\\t/me\\n')\n")
    (repo / "docs").mkdir()
    (repo / "docs" / "open-decisions.md").write_text("# Open\n\n- decide X · do Y · me · 2026-08-28\n")
    (repo / "docs" / "next-action.md").write_text(
        "# Next action\n\nCorrelate the drop episode.\n\n## Fallback\n\nAdd stage timing.\n")
    shims = tmp_path / "bin"
    shims.mkdir()
    (shims / "ddev").write_text("#!/bin/bash\nexit 1\n")
    (shims / "ddev").chmod(0o755)
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(repo), "PATH": f"{shims}:{os.environ['PATH']}"}
    return repo, env


def test_orient_reports_branch_other_sessions_and_open_decisions(tmp_path):
    repo, env = _checkout(tmp_path)
    r = subprocess.run(["bash", str(HOOK)], cwd=repo, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "=== autotrade orientation ===" in out
    assert "branch 'feat/x'" in out
    assert "live session pid 111 works in /elsewhere/wt" in out
    assert "pid 4242" not in out  # the session's own process is not a warning
    assert "decide X" in out
    assert "ddev: not running" in out
    assert "gitnexus: no index" in out


def test_orient_never_fails_the_hook_outside_a_checkout(tmp_path):
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path / "nowhere")}
    r = subprocess.run(["bash", str(HOOK)], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0


def test_orient_prints_the_next_action_so_continue_needs_no_context(tmp_path):
    """Typing `continue` in a fresh session must surface the one next action by
    itself - the banner is the only thing guaranteed to be read."""
    repo, env = _checkout(tmp_path)
    r = subprocess.run(["bash", str(HOOK)], cwd=repo, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "NEXT (docs/next-action.md" in r.stdout
    assert "Correlate the drop episode." in r.stdout


def test_orient_survives_a_missing_next_action_file(tmp_path):
    repo, env = _checkout(tmp_path)
    (repo / "docs" / "next-action.md").unlink()
    r = subprocess.run(["bash", str(HOOK)], cwd=repo, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "NEXT" not in r.stdout
