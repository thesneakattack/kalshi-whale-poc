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
#   2. `git merge-base --is-ancestor <branch> main` - the branch's tip is
#      already fully contained in main, which was freshly fetched/
#      fast-forwarded first. This alone proves nothing would be lost by
#      deleting it, merged-remote-branch-already-gone or not.
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

# Fetch + fast-forward local main first (only if main is what's actually
# checked out in the primary - never force a checkout there).
git -C "$PRIMARY" fetch origin main --quiet
if [ "$(git -C "$PRIMARY" symbolic-ref --short HEAD 2>/dev/null || echo "")" = "main" ]; then
  git -C "$PRIMARY" merge --ff-only origin/main --quiet
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

  is_ancestor=0
  if git -C "$PRIMARY" merge-base --is-ancestor "$branch" main 2>/dev/null; then
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

    if ! git -C "$PRIMARY" worktree remove "$path" 2>/tmp/cleanup-worktrees-remove-err; then
      rel="${path#"$PRIMARY"/}"
      if command -v ddev >/dev/null 2>&1 && (cd "$PRIMARY" && ddev describe >/dev/null 2>&1); then
        (cd "$PRIMARY" && ddev exec -s fastapi rm -rf "/app/$rel") || true
        git -C "$PRIMARY" worktree prune
      fi
      if [ -e "$path" ]; then
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
