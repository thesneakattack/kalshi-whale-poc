#!/usr/bin/env bash
# Sweeps every registered git worktree (except the primary checkout) and
# removes the ones that are provably stale, plus their local (and, if
# still present, remote) branch. "Provably stale" means every one of
# these holds, checked independently rather than trusting a single
# signal:
#   1. gh reports exactly one PR for the branch, state MERGED (a PR that
#      is only CLOSED is deliberately left alone - services/kalshi's own
#      sources_worktree.py treats CLOSED-without-merge as "still live
#      work", and this script stays consistent with that judgment rather
#      than guessing).
#   2. `git merge-base --is-ancestor <branch> origin/main` - the branch's
#      tip is already fully contained in origin/main, freshly fetched
#      first (origin/main, not local main - local main only gets fast-
#      forwarded when the primary checkout happens to be on main itself,
#      so it can't be trusted as the comparison target). This alone
#      proves nothing would be lost by deleting it, merged-remote-branch-
#      already-gone or not.
#   3. The worktree's working tree is clean (`git status --porcelain`
#      empty) - nothing uncommitted sitting there.
#
# No time-based quarantine on top of these - per
# .claude/rules/autonomous-quality-coordination-evidence.md's "no guessed
# quarantine period" rule, a timer would just be superstition once the
# guards above already establish certainty directly.
#
# A worktree whose root-owned cache files (left behind by `ddev exec`,
# which runs as root in the container) block plain `git worktree remove`
# is cleaned up via `ddev exec` itself - already-root inside the
# container that created them - never via `sudo` on the host.
#
# Usage:
#   scripts/cleanup-worktrees.sh            # act: remove every stale worktree found
#   scripts/cleanup-worktrees.sh --dry-run  # report only, never mutates anything
#
# Run from anywhere in the repo; worktree/branch operations always target
# the primary checkout (the one whose .git is a real directory), not the
# caller's cwd.
set -euo pipefail

REPO="thesneakattack/kalshi-whale-poc"
DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then
  DRY_RUN=1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "error: gh CLI is not authenticated - cannot check PR state, skipping cleanup" >&2
  exit 1
fi

# Same live-session detection .claude/hooks/guard_workflow.py's R6 rule
# already uses (a Claude session's control socket -> its /proc/<pid>/cwd),
# reimplemented in bash since this script has no Python dependency
# otherwise. Used below to refuse unlocking a worktree a live session is
# actually sitting in, regardless of what the three staleness checks say -
# a lock is the one signal this script previously honored unconditionally,
# and code-review on the initial unlock fix (2026-08-28) correctly flagged
# that stripping it without this check would silently defeat whatever
# protection it was providing.
worktree_has_live_session() {
  local target sockdir sock pid cwd
  target="$(cd "$1" 2>/dev/null && pwd -P)" || return 1
  sockdir="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/cc-socks"
  [ -d "$sockdir" ] || return 1
  for sock in "$sockdir"/*.sock; do
    [ -e "$sock" ] || continue
    pid="$(basename "$sock" .sock)"
    cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null)" || continue
    if [ "$cwd" = "$target" ]; then
      return 0
    fi
  done
  return 1
}

# Parse `git worktree list --porcelain` into parallel arrays of
# path/branch, and find the primary checkout (real .git directory, not a
# linked worktree's .git file).
PRIMARY=""
WORKTREE_PATHS=()
WORKTREE_BRANCHES=()
current_path=""
while IFS= read -r line; do
  case "$line" in
    "worktree "*)
      current_path="${line#worktree }"
      ;;
    "branch refs/heads/"*)
      branch="${line#branch refs/heads/}"
      if [ -d "$current_path/.git" ]; then
        PRIMARY="$current_path"
      else
        WORKTREE_PATHS+=("$current_path")
        WORKTREE_BRANCHES+=("$branch")
      fi
      ;;
  esac
done < <(git worktree list --porcelain)

if [ -z "$PRIMARY" ]; then
  echo "error: could not identify the primary checkout among registered worktrees" >&2
  exit 1
fi

if [ "${#WORKTREE_PATHS[@]}" -eq 0 ]; then
  echo "no non-primary worktrees registered - nothing to check"
  exit 0
fi

# Fetch first - this unconditionally refreshes origin/main, which is what
# the ancestor check below now compares against directly. Also
# fast-forward the LOCAL main branch when it's what's actually checked
# out in the primary (never force a checkout there) - purely a courtesy
# for anything else that reads local main; the ancestor check itself no
# longer depends on this happening, so `|| true` here: a failure (e.g.
# local main has diverged from origin/main) must not abort the whole
# script under set -e before the cleanup loop even starts, for a step
# nothing downstream actually needs.
git -C "$PRIMARY" fetch origin main --quiet
if [ "$(git -C "$PRIMARY" symbolic-ref --short HEAD 2>/dev/null || echo "")" = "main" ]; then
  git -C "$PRIMARY" merge --ff-only refs/remotes/origin/main --quiet || true
fi

removed=0
kept=0

for i in "${!WORKTREE_PATHS[@]}"; do
  path="${WORKTREE_PATHS[$i]}"
  branch="${WORKTREE_BRANCHES[$i]}"

  pr_json="$(gh pr list --repo "$REPO" --head "$branch" --state all --json number,state 2>/dev/null || echo "[]")"
  pr_count="$(echo "$pr_json" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')"
  pr_state="$(echo "$pr_json" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d[0]["state"] if d else "")')"
  pr_number="$(echo "$pr_json" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d[0]["number"] if d else "")')"

  is_merged=0
  if [ "$pr_count" = "1" ] && [ "$pr_state" = "MERGED" ]; then
    is_merged=1
  fi

  # refs/remotes/origin/main, not bare origin/main and not local main:
  # local main is only fast-forwarded above when $PRIMARY's HEAD is
  # literally main, so whenever $PRIMARY sits on any other branch - the
  # common case in this multi-worktree workflow - local main can be
  # arbitrarily stale. Real bug found live 2026-08-28: this under-reported
  # a just-merged branch as "not yet in main" and blocked its own cleanup,
  # because $PRIMARY was on feat/realtime-data-plane-remediation at the
  # time. origin/main is unconditionally fresh (fetched above regardless
  # of what's checked out in $PRIMARY). The fully-qualified
  # refs/remotes/origin/main form matters too, not just cosmetically:
  # git's ref-resolution order checks refs/heads/<name> before
  # refs/remotes/<name> (gitrevisions(7)), so a bare `origin/main` would
  # silently resolve to a local branch literally named that instead, if
  # one ever existed - turning a false "not merged" into a worse false
  # "merged", i.e. an actual unsafe-deletion path rather than just an
  # overly-conservative keep. Verified live: with such a shadowing local
  # branch present, the bare form misresolves; the refs/remotes/ form
  # doesn't.
  is_ancestor=0
  if git -C "$PRIMARY" merge-base --is-ancestor "$branch" refs/remotes/origin/main 2>/dev/null; then
    is_ancestor=1
  fi

  is_clean=0
  if [ -z "$(git -C "$path" status --porcelain 2>/dev/null)" ]; then
    is_clean=1
  fi

  if [ "$is_merged" = "1" ] && [ "$is_ancestor" = "1" ] && [ "$is_clean" = "1" ]; then
    if [ "$DRY_RUN" = "1" ]; then
      echo "stale: $branch (worktree $path, PR #$pr_number merged) - would remove"
      removed=$((removed + 1))
      continue
    fi

    if worktree_has_live_session "$path"; then
      echo "kept: $branch - a live Claude session's cwd is inside $path; not touching its lock or removing it" >&2
      kept=$((kept + 1))
      continue
    fi

    # Unlock before attempting removal - both `worktree remove` below and
    # `worktree prune` in the ddev fallback refuse a locked worktree. A
    # lock left over from whatever agent session used this worktree (and
    # already confirmed above not to be a currently-live session) has no
    # further bearing on whether it's provably stale by the three checks
    # above. was_locked tracks whether this call actually changed
    # anything, so a failed removal below can restore the original lock
    # state instead of silently leaving a previously-locked worktree
    # unprotected. Real bug found 2026-08-28: without the unlock, the ddev
    # fallback deleted a locked worktree's directory but `worktree prune`
    # silently skipped deregistering it, leaving git's worktree metadata
    # pointing at a now-nonexistent path - `branch -d` then refused with
    # "used by worktree", aborting the whole script (set -e) with the
    # branch never deleted, locally or remotely.
    was_locked=0
    if git -C "$PRIMARY" worktree unlock "$path" 2>/dev/null; then
      was_locked=1
    fi

    if ! git -C "$PRIMARY" worktree remove "$path" 2>/tmp/cleanup-worktrees-remove-err; then
      rel="${path#"$PRIMARY"/}"
      if command -v ddev >/dev/null 2>&1 && (cd "$PRIMARY" && ddev describe >/dev/null 2>&1); then
        (cd "$PRIMARY" && ddev exec -s fastapi rm -rf "/app/$rel") || true
        git -C "$PRIMARY" worktree prune
      fi
      if [ -e "$path" ]; then
        if [ "$was_locked" = "1" ]; then
          git -C "$PRIMARY" worktree lock "$path" \
            --reason "cleanup-worktrees: removal attempt failed, restoring prior lock" 2>/dev/null || true
        fi
        echo "kept: $branch - worktree removal blocked and ddev fallback did not clear it: $(cat /tmp/cleanup-worktrees-remove-err)" >&2
        kept=$((kept + 1))
        continue
      fi
    fi

    git -C "$PRIMARY" branch -d "$branch"
    if [ -n "$(git ls-remote --heads origin "$branch" 2>/dev/null)" ]; then
      git -C "$PRIMARY" push origin --delete "$branch"
    fi
    echo "removed: $branch (worktree $path, PR #$pr_number merged)"
    removed=$((removed + 1))
  else
    reason="PR not merged (state: ${pr_state:-none found})"
    if [ "$is_merged" = "1" ] && [ "$is_ancestor" = "0" ]; then
      reason="branch has commits not yet in main"
    elif [ "$is_merged" = "1" ] && [ "$is_clean" = "0" ]; then
      reason="worktree has uncommitted changes"
    fi
    if [ "$DRY_RUN" = "1" ]; then
      echo "keeping: $branch ($reason)"
    fi
    kept=$((kept + 1))
  fi
done

echo "cleanup-worktrees: $removed removed, $kept kept"
