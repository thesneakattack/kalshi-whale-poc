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
#   2. `git merge-base --is-ancestor <branch> refs/remotes/origin/main` -
#      the branch's tip is already fully contained in origin/main, freshly
#      fetched first. Not local main: local main is only fast-forwarded
#      when the primary checkout happens to be on main itself, so it can
#      be arbitrarily stale (2026-08-28: an idle worktree held local main
#      65 commits behind and every merged branch read as "not merged").
#   3. The worktree's working tree is clean (`git status --porcelain`
#      empty) - nothing uncommitted sitting there.
#
# No time-based quarantine on top of these - a timer would be superstition
# once the guards above establish certainty directly.
#
# A worktree whose root-owned cache files (left behind by `ddev exec`, which
# used to run as root in the container before .ddev/docker-compose.fastapi.
# yaml's `user: "${DDEV_UID}:${DDEV_GID}"` fix, 2026-09-01) block plain
# `git worktree remove` fall through to the `ddev exec` call below. That
# call is now a no-op for anything genuinely still root-owned - `ddev exec
# -s fastapi` runs as the same host uid as the caller now, so it can no
# longer force-delete what the host user couldn't already delete directly.
# Degrades safely either way (see the `[ -e "$path" ]` check right after
# it): a leftover it can't clear is reported as "kept", never silently
# misreported as removed. A genuinely root-owned leftover predating that
# fix needs a one-time `sudo rm -rf` on the host instead.
#
# Usage:
#   scripts/cleanup-worktrees.sh            # act: remove every stale worktree found
#   scripts/cleanup-worktrees.sh --dry-run  # report only, never mutates anything
#
# Run from anywhere in the repo; worktree/branch operations always target
# the primary checkout (the one whose .git is a real directory), not the
# caller's cwd. tests/test_cleanup_worktrees.py drives this script against
# a synthetic repository with `gh`/`ddev` shims on PATH and
# CLEANUP_WORKTREES_GUARD pointing at a stand-in session lister.
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

# Parse `git worktree list --porcelain` into parallel arrays of path/branch,
# and find the primary checkout (real .git directory, not a linked worktree's
# .git file).
#
# A porcelain record is `worktree <path>`, then `HEAD <sha>`, then either
# `branch refs/heads/<name>` or `detached`, then a blank line. Records are
# closed on the next `worktree ` line (and at EOF) rather than on the branch
# line, so a DETACHED worktree is seen at all: keying off `branch` alone made
# every detached entry invisible - never listed, never reported, never
# cleaned. Real leak, 2026-08-28: `git worktree add --detach` (what the
# code-review skill's agents create) left an entry that accumulated silently
# and had to be removed by hand. Detached worktrees are reported and always
# kept - with no branch there is no PR to prove staleness against.
PRIMARY=""
WORKTREE_PATHS=()
WORKTREE_BRANCHES=()
DETACHED_PATHS=()
current_path=""
current_branch=""

flush_worktree() {
  [ -n "$current_path" ] || return 0
  if [ -d "$current_path/.git" ]; then
    PRIMARY="$current_path"
  elif [ -n "$current_branch" ]; then
    WORKTREE_PATHS+=("$current_path")
    WORKTREE_BRANCHES+=("$current_branch")
  else
    DETACHED_PATHS+=("$current_path")
  fi
  current_path=""
  current_branch=""
}

while IFS= read -r line; do
  case "$line" in
    "worktree "*)
      flush_worktree
      current_path="${line#worktree }"
      ;;
    "branch refs/heads/"*)
      current_branch="${line#branch refs/heads/}"
      ;;
  esac
done < <(git worktree list --porcelain)
flush_worktree

if [ -z "$PRIMARY" ]; then
  echo "error: could not identify the primary checkout among registered worktrees" >&2
  exit 1
fi

detached_kept=0
for path in ${DETACHED_PATHS+"${DETACHED_PATHS[@]}"}; do
  echo "keeping: $path (detached HEAD - no branch, so no PR state to check; remove it by hand once you know it is finished)"
  detached_kept=$((detached_kept + 1))
done

# Live-session detection is guard_workflow.py's `--sessions` ("#sessions v1"
# header, then one line per session: pid, self|other, cwd) - the same
# implementation R6 and orient.sh use, so this script can never see a
# different set of sessions than the guard does. The guard is the copy that
# ships next to THIS script (same commit), never the primary's: the primary
# can sit on an older branch whose guard ignores the flag and prints nothing,
# which would read as "no sessions" and delete a worktree someone is in
# (fail open, caught in review 2026-08-28). A missing guard, a non-zero
# exit, or output without the header therefore all mean "assume occupied".
# Used below to refuse unlocking or removing a worktree any live session
# (this one included) is sitting in or under, whatever the three staleness
# checks say: a lock is the one signal this script once honored
# unconditionally, and stripping it without this check silently defeated
# whatever protection it was providing (2026-08-28 code review).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
worktree_has_live_session() {
  local target guard out cwd
  target="$(cd "$1" 2>/dev/null && pwd -P)" || return 1
  guard="${CLEANUP_WORKTREES_GUARD:-$SCRIPT_DIR/../.claude/hooks/guard_workflow.py}"
  if [ ! -f "$guard" ]; then
    echo "warning: $guard is missing - treating $1 as occupied by a live session" >&2
    return 0
  fi
  if ! out="$(python3 "$guard" --sessions 2>/dev/null </dev/null)" || [ "${out%%$'\n'*}" != "#sessions v1" ]; then
    echo "warning: $guard --sessions gave no usable answer (older guard?) - treating $1 as occupied" >&2
    return 0
  fi
  while IFS=$'\t' read -r _pid _tag cwd; do
    [ -n "$cwd" ] || continue
    cwd="$(cd "$cwd" 2>/dev/null && pwd -P)" || continue
    case "$cwd" in
      "$target"|"$target"/*) return 0 ;;
    esac
  done <<< "$out"
  return 1
}

if [ "${#WORKTREE_PATHS[@]}" -eq 0 ]; then
  echo "cleanup-worktrees: 0 removed, $detached_kept kept"
  exit 0
fi

# Fetch first - this unconditionally refreshes origin/main, which is what
# the ancestor check below compares against. Also fast-forward the LOCAL
# main branch when it is what's checked out in the primary (never force a
# checkout there) - a courtesy for anything else that reads local main;
# nothing downstream depends on it, so a failure (local main diverged)
# must not abort the whole script under set -e.
git -C "$PRIMARY" fetch origin main --quiet
if [ "$(git -C "$PRIMARY" symbolic-ref --short HEAD 2>/dev/null || echo "")" = "main" ]; then
  git -C "$PRIMARY" merge --ff-only refs/remotes/origin/main --quiet || true
fi

remove_err="$(mktemp)"
trap 'rm -f "$remove_err"' EXIT

removed=0
kept=$detached_kept

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

  # refs/remotes/origin/main, fully qualified: git resolves refs/heads/<name>
  # before refs/remotes/<name> (gitrevisions(7)), so a bare `origin/main`
  # would silently resolve to a local branch literally named that if one
  # ever existed - turning a false "not merged" into a worse false "merged".
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
    # lock left over from a session already confirmed above not to be live
    # has no bearing on the three staleness checks. was_locked lets a
    # failed removal restore the original lock instead of leaving a
    # previously-locked worktree unprotected.
    was_locked=0
    if git -C "$PRIMARY" worktree unlock "$path" 2>/dev/null; then
      was_locked=1
    fi

    if ! git -C "$PRIMARY" worktree remove "$path" 2>"$remove_err"; then
      rel="${path#"$PRIMARY"/}"
      if command -v ddev >/dev/null 2>&1 && (cd "$PRIMARY" && ddev describe >/dev/null 2>&1); then
        (cd "$PRIMARY" && ddev exec -s fastapi rm -rf "/app/$rel") || true
      fi
      if [ -e "$path" ]; then
        if [ "$was_locked" = "1" ]; then
          git -C "$PRIMARY" worktree lock "$path" \
            --reason "cleanup-worktrees: removal attempt failed, restoring prior lock" 2>/dev/null || true
        fi
        echo "kept: $branch - worktree removal blocked and ddev fallback did not clear it: $(cat "$remove_err")" >&2
        kept=$((kept + 1))
        continue
      fi
      git -C "$PRIMARY" worktree prune
    fi

    git -C "$PRIMARY" branch -d "$branch"
    if [ -n "$(git -C "$PRIMARY" ls-remote --heads origin "$branch" 2>/dev/null)" ]; then
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
