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

from tests.support.synthetic_git_repo import git as _git, make_synthetic_repo

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

FAKE_GUARD = "import os\nprint('#sessions v1')\nprint(os.environ.get('FAKE_SESSIONS', ''), end='')\n"
OLD_GUARD = "import sys\nsys.exit(0)\n"  # the pre-2026-08-28 guard: ignores --sessions, prints nothing


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


def test_detached_worktrees_are_reported_and_never_removed(tmp_path):
    """2026-08-28 leak: the porcelain parser keyed off `branch refs/heads/`, so a
    detached worktree (what `git worktree add --detach` and the review agents create)
    was never listed, never reported, and accumulated until removed by hand. It must
    be visible, counted as kept, and left alone - no branch means no PR to prove
    staleness - while a merged branch worktree beside it is still removed."""
    primary, wt, env = _setup(tmp_path)
    loose = tmp_path / "loose"
    _git(["worktree", "add", "--detach", str(loose), "HEAD"], primary)

    r = _run(primary, env, "--dry-run")
    assert r.returncode == 0, r.stderr
    assert f"keeping: {loose} (detached HEAD" in r.stdout
    assert "stale: feat/x" in r.stdout and "1 removed, 1 kept" in r.stdout

    r = _run(primary, env)
    assert r.returncode == 0, r.stderr
    assert "removed: feat/x" in r.stdout and not wt.exists()
    assert loose.exists() and str(loose) in _worktrees(primary)
    assert "1 removed, 1 kept" in r.stdout


def test_only_a_detached_worktree_still_reports_it(tmp_path):
    primary, wt, env = _setup(tmp_path)
    _git(["worktree", "remove", str(wt)], primary)
    loose = tmp_path / "loose"
    _git(["worktree", "add", "--detach", str(loose), "HEAD"], primary)
    r = _run(primary, env)
    assert r.returncode == 0, r.stderr
    assert f"keeping: {loose} (detached HEAD" in r.stdout and "0 removed, 1 kept" in r.stdout
    assert loose.exists()


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


def test_a_guard_that_cannot_answer_means_occupied_not_empty(tmp_path):
    """Review finding 2026-08-28: the session check used the PRIMARY's guard, and an
    older guard there answers --sessions with nothing and exit 0 - which read as 'no
    live session' and would have deleted an occupied worktree. Missing guard or no
    header must both fail closed."""
    primary, wt, env = _setup(tmp_path)
    old = tmp_path / "old_guard.py"
    old.write_text(OLD_GUARD)
    env["CLEANUP_WORKTREES_GUARD"] = str(old)
    r = _run(primary, env)
    assert r.returncode == 0, r.stderr
    assert "older guard" in r.stderr and "0 removed, 1 kept" in r.stdout and wt.exists()

    env["CLEANUP_WORKTREES_GUARD"] = str(tmp_path / "nowhere.py")
    r = _run(primary, env)
    assert r.returncode == 0, r.stderr
    assert "missing" in r.stderr and "0 removed, 1 kept" in r.stdout and wt.exists()


def test_the_default_guard_is_the_one_shipped_beside_the_script(tmp_path):
    """Version-locks the check to the script: the primary may sit on any branch."""
    primary, wt, env = _setup(tmp_path)
    del env["CLEANUP_WORKTREES_GUARD"]
    r = _run(primary, env)
    assert r.returncode == 0, r.stderr
    assert "warning" not in r.stderr and "removed: feat/x" in r.stdout


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
    """4f35ea5 originally guarded the courtesy ff-only merge's `|| true` against
    aborting the script under set -e. #535 removed that merge entirely (it was
    silently deploying the live app - see the notice tests below), so this now
    retargets to what replaced it: a diverged local main gets a diverged notice
    and is never pulled, and the sweep still completes."""
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
    before = _git(["rev-parse", "main"], primary)
    r = _run(primary, env)
    assert r.returncode == 0, r.stderr
    assert "removed: feat/x" in r.stdout and not wt.exists()
    assert _git(["rev-parse", "main"], primary) == before, "local main must not be pulled"
    assert "diverged from origin/main (1 ahead, 1 behind)" in r.stderr


def test_script_never_advances_local_main_and_reports_how_far_behind(tmp_path):
    """#535: the removed fast-forward was an unannounced deploy - a .py-touching
    pull in the primary (ddev's bind mount) fires uvicorn --reload. The script
    must never move local main, in either mode, and must say how far behind it
    is instead of pulling silently."""
    primary, wt, env = _setup(tmp_path)
    before = _git(["rev-parse", "main"], primary)
    clone = tmp_path / "clone2"
    _git(["clone", "-q", "-b", "main", str(tmp_path / "origin.git"), str(clone)], tmp_path)
    _git(["config", "user.email", "t@example.com"], clone)
    _git(["config", "user.name", "T"], clone)
    (clone / "remote2.txt").write_text("r2\n")
    _git(["add", "remote2.txt"], clone)
    _git(["commit", "-q", "-m", "remote-only work"], clone)
    _git(["push", "-q", "origin", "main"], clone)

    r_dry = _run(primary, env, "--dry-run")
    assert r_dry.returncode == 0, r_dry.stderr
    assert _git(["rev-parse", "main"], primary) == before
    assert "local main is 1 commit(s) behind origin/main - not pulling" in r_dry.stderr

    r = _run(primary, env)
    assert r.returncode == 0, r.stderr
    assert _git(["rev-parse", "main"], primary) == before
    assert "local main is 1 commit(s) behind origin/main - not pulling" in r.stderr
    assert "uvicorn --reload" in r.stderr and "merge --ff-only origin/main" in r.stderr


def test_no_behind_notice_when_local_main_is_already_current(tmp_path):
    primary, wt, env = _setup(tmp_path)
    r = _run(primary, env)
    assert r.returncode == 0, r.stderr
    assert "behind origin/main" not in r.stderr and "diverged" not in r.stderr


def test_no_upstream_branch_with_stale_local_head_is_still_removed(tmp_path):
    """AR-3/AR-4 gate (2026-09-03 adversarial review of #535's fix): feat/x is
    pushed without -u in _setup(), so it has no configured upstream. With the
    fast-forward removed, git branch -d's fallback-to-HEAD check would refuse
    once local main no longer contains feat/x's tip (open-decisions #35's live
    incident shape), aborting the sweep after the worktree is already gone.
    -D trusts the script's own merge-base --is-ancestor proof against
    refs/remotes/origin/main (already fresh from the fetch) instead of
    re-deriving a weaker one from HEAD."""
    primary, wt, env = _setup(tmp_path)
    initial = _git(["rev-list", "--max-parents=0", "HEAD"], primary)
    _git(["reset", "--hard", initial], primary)
    r = _run(primary, env)
    assert r.returncode == 0, r.stderr
    assert "removed: feat/x" in r.stdout and not wt.exists()
    assert "feat/x" not in _branches(primary)


def test_a_branch_whose_tip_has_commits_not_yet_in_origin_main_is_kept_even_with_a_merged_pr(tmp_path):
    """AR-3: under -D, this refusal ('branch has commits not yet in main', :252-253)
    is the ONLY guard left between a gh-reported MERGED PR and deleting commits
    that aren't actually in origin/main yet - unlike -d, -D has no independent
    merge check of its own to fall back on."""
    primary, wt, env = _setup(tmp_path)
    (wt / "y.txt").write_text("y\n")
    _git(["add", "y.txt"], wt)
    _git(["commit", "-q", "-m", "unmerged followup"], wt)

    r = _run(primary, env, "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "keeping: feat/x (branch has commits not yet in main)" in r.stdout

    r2 = _run(primary, env)
    assert r2.returncode == 0, r2.stderr
    assert wt.exists() and "feat/x" in _branches(primary) and "0 removed, 1 kept" in r2.stdout


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
