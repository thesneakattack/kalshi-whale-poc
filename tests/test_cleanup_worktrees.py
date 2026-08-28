"""scripts/cleanup-worktrees.sh against a disposable synthetic repository - one case
per failure the script already shipped a fix for on 2026-08-28 (bf532e1 unlock-before-
remove, 06a6c6c never-unlock-a-live-session's-worktree, 82834dc origin/main-not-stale-
local-main, 4f35ea5 ff-only-failure-must-not-abort) plus the root-owned-cache shape
that leaves a dangling directory. `gh` and `ddev` are shims on PATH; the session lister
is a stand-in via CLEANUP_WORKTREES_GUARD. Needs real git and bash (host, CI - the
fastapi container has neither)."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.support.synthetic_git_repo import make_synthetic_repo

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "cleanup-worktrees.sh"

pytestmark = pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None,
                                reason="needs git and bash")

GH_SHIM = r'''#!/bin/bash
# gh stand-in: auth is always ok; `pr list --head <branch>` answers from $GH_FAKE_PRS.
if [ "$1" = "auth" ]; then exit 0; fi
if [ "$1" = "pr" ] && [ "$2" = "list" ]; then
  head=""
  while [ $# -gt 0 ]; do if [ "$1" = "--head" ]; then head="$2"; fi; shift; done
  python3 -c 'import json,os,sys; d=json.loads(os.environ.get("GH_FAKE_PRS","{}")); print(json.dumps(d.get(sys.argv[1], [])))' "$head"
  exit 0
fi
echo "gh shim: unexpected $*" >&2
exit 1
'''

DDEV_SHIM = r'''#!/bin/bash
# ddev stand-in: `describe` ok; `exec -s fastapi rm -rf /app/<rel>` acts as root would.
if [ "$1" = "describe" ]; then exit 0; fi
if [ "$1" = "exec" ]; then
  target="${@: -1}"
  rel="${target#/app/}"
  chmod -R u+rwx "$DDEV_FAKE_PRIMARY/$rel" 2>/dev/null
  rm -rf "$DDEV_FAKE_PRIMARY/$rel"
  exit 0
fi
exit 1
'''

FAKE_GUARD = "import os\nprint(os.environ.get('FAKE_SESSIONS', ''), end='')\n"


def _git(args, cwd) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, f"git {' '.join(args)}: {r.stderr}"
    return r.stdout.strip()


def _setup(tmp_path: Path, pr_state: str = "MERGED"):
    primary = make_synthetic_repo(tmp_path)
    (primary / ".gitignore").write_text(".pytest_cache/\n__pycache__/\n")
    _git(["add", ".gitignore"], primary)
    _git(["commit", "-q", "-m", "ignore caches"], primary)
    origin = tmp_path / "origin.git"
    _git(["init", "--bare", "-q", str(origin)], tmp_path)
    _git(["remote", "add", "origin", str(origin)], primary)
    _git(["push", "-q", "-u", "origin", "main"], primary)

    wt = primary / ".claude" / "worktrees" / "feat-x"
    _git(["worktree", "add", "-q", str(wt), "-b", "feat/x"], primary)
    (wt / "x.txt").write_text("x\n")
    _git(["add", "x.txt"], wt)
    _git(["commit", "-q", "-m", "feat x"], wt)
    _git(["push", "-q", "origin", "feat/x"], wt)
    _git(["merge", "-q", "--no-edit", "feat/x"], primary)
    _git(["push", "-q", "origin", "main"], primary)

    shims = tmp_path / "bin"
    shims.mkdir()
    for name, body in (("gh", GH_SHIM), ("ddev", DDEV_SHIM)):
        (shims / name).write_text(body)
        (shims / name).chmod(0o755)
    guard = tmp_path / "fake_guard.py"
    guard.write_text(FAKE_GUARD)
    env = {
        **os.environ,
        "PATH": f"{shims}:{os.environ['PATH']}",
        "GH_FAKE_PRS": json.dumps({"feat/x": [{"number": 7, "state": pr_state}]}),
        "DDEV_FAKE_PRIMARY": str(primary),
        "CLEANUP_WORKTREES_GUARD": str(guard),
        "FAKE_SESSIONS": "",
    }
    return primary, wt, env


def _run(primary: Path, env: dict, *args):
    return subprocess.run(["bash", str(SCRIPT), *args], cwd=primary, env=env, capture_output=True, text=True)


def _worktrees(primary: Path) -> str:
    return _git(["worktree", "list", "--porcelain"], primary)


def _branches(primary: Path) -> list[str]:
    return _git(["branch", "--format=%(refname:short)"], primary).split()


def test_dry_run_reports_and_mutates_nothing(tmp_path):
    primary, wt, env = _setup(tmp_path)
    r = _run(primary, env, "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "stale: feat/x" in r.stdout and "would remove" in r.stdout
    assert wt.exists() and "feat/x" in _branches(primary)


def test_merged_clean_worktree_is_removed_with_both_branches(tmp_path):
    primary, wt, env = _setup(tmp_path)
    r = _run(primary, env)
    assert r.returncode == 0, r.stderr
    assert "removed: feat/x" in r.stdout and not wt.exists()
    assert "feat/x" not in _branches(primary) and str(wt) not in _worktrees(primary)
    assert _git(["ls-remote", "--heads", "origin", "feat/x"], primary) == ""


def test_unmerged_pr_is_kept(tmp_path):
    primary, wt, env = _setup(tmp_path, pr_state="OPEN")
    r = _run(primary, env)
    assert r.returncode == 0, r.stderr
    assert wt.exists() and "feat/x" in _branches(primary) and "0 removed, 1 kept" in r.stdout


def test_locked_worktree_with_no_live_session_is_unlocked_and_removed(tmp_path):
    """bf532e1: a leftover lock used to make `worktree remove` and `worktree prune` both
    refuse, leaving git metadata pointing at a deleted directory and the branch undeleted."""
    primary, wt, env = _setup(tmp_path)
    _git(["worktree", "lock", str(wt)], primary)
    r = _run(primary, env)
    assert r.returncode == 0, r.stderr
    assert "removed: feat/x" in r.stdout and not wt.exists()
    assert "feat/x" not in _branches(primary) and str(wt) not in _worktrees(primary)


def test_worktree_a_live_session_sits_in_is_left_alone_and_stays_locked(tmp_path):
    """06a6c6c: the lock must never be stripped from a worktree a live session is in -
    including the session running this script."""
    primary, wt, env = _setup(tmp_path)
    _git(["worktree", "lock", str(wt)], primary)
    (wt / "sub").mkdir()
    env["FAKE_SESSIONS"] = f"4242\tself\t{wt}/sub\n"
    r = _run(primary, env)
    assert r.returncode == 0, r.stderr
    assert "live Claude session" in r.stderr and "0 removed, 1 kept" in r.stdout
    assert wt.exists() and "feat/x" in _branches(primary)
    assert "locked" in _worktrees(primary)


def test_stale_local_main_does_not_hide_a_branch_merged_into_origin_main(tmp_path):
    """82834dc: local main is only fast-forwarded when the primary is on main; an idle
    checkout of main left it 65 commits behind and every merged branch read as unmerged."""
    primary, wt, env = _setup(tmp_path)
    initial = _git(["rev-list", "--max-parents=0", "HEAD"], primary)
    _git(["checkout", "-q", "-b", "other"], primary)
    _git(["branch", "-f", "main", initial], primary)
    r = _run(primary, env)
    assert r.returncode == 0, r.stderr
    assert "removed: feat/x" in r.stdout and not wt.exists()


def test_diverged_local_main_does_not_abort_the_sweep(tmp_path):
    """4f35ea5: the courtesy ff-only merge of local main failing must not kill the whole
    script under set -e before the loop starts."""
    primary, wt, env = _setup(tmp_path)
    clone = tmp_path / "clone"
    _git(["clone", "-q", "-b", "main", str(tmp_path / "origin.git"), str(clone)], tmp_path)
    _git(["config", "user.email", "t@example.com"], clone)
    _git(["config", "user.name", "T"], clone)
    (clone / "remote.txt").write_text("r\n")
    _git(["add", "remote.txt"], clone)
    _git(["commit", "-q", "-m", "remote work"], clone)
    _git(["push", "-q", "origin", "main"], clone)
    (primary / "local.txt").write_text("l\n")
    _git(["add", "local.txt"], primary)
    _git(["commit", "-q", "-m", "local work"], primary)
    r = _run(primary, env)
    assert r.returncode == 0, r.stderr
    assert "removed: feat/x" in r.stdout and not wt.exists()


def test_undeletable_cache_falls_back_to_ddev_and_leaves_no_dangling_worktree(tmp_path):
    """The reset-close-positions-first shape (2026-08-28): root-owned .pytest_cache from
    `ddev exec` pytest blocks `worktree remove`; the directory must still end up gone,
    deregistered, and the branch deleted - not a directory git no longer knows about."""
    primary, wt, env = _setup(tmp_path)
    cache = wt / ".pytest_cache"
    cache.mkdir()
    (cache / "f").write_text("")
    cache.chmod(0o555)
    try:
        r = _run(primary, env)
    finally:
        if cache.exists():
            cache.chmod(0o755)
    assert r.returncode == 0, r.stderr
    assert "removed: feat/x" in r.stdout
    assert not wt.exists() and str(wt) not in _worktrees(primary) and "feat/x" not in _branches(primary)
