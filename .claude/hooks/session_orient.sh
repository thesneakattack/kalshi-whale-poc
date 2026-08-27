#!/bin/bash
# SessionStart hook: reconstruct the basics every session instead of leaving
# it to be manually rediscovered - git status, ddev status, open P0 safety
# items, and whether data/*.db files exist (they're live while ddev runs -
# see CLAUDE.md). Most of this predates the repo's git history (see CLAUDE.md
# for the cutover point), so ROADMAP.md/status.html still matter alongside
# git log for anything from before that point.
set -uo pipefail
cd "$CLAUDE_PROJECT_DIR" 2>/dev/null || exit 0

echo "=== autotrade orientation ==="

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  branch=$(git branch --show-current 2>/dev/null)
  dirty=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
  echo "git: on branch '$branch', $dirty uncommitted change(s) - see CLAUDE.md, git history only covers work after the initial commit"
  if [ "$branch" = "main" ]; then
    echo "branch policy: on main - create a short-lived initiative branch (feat/fix/refactor/chore/docs) before implementation work, don't commit directly to main. See .claude/rules/branching-and-ci.md."
  fi
fi

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  # Cheap, network-free count (no gh calls here - this hook has a 15s
  # budget and scripts/cleanup-worktrees.sh's PR-state check is a network
  # round-trip per worktree). Real staleness evaluation is that script's
  # job, run via `/checkpoint` (acts) or `--dry-run` (reports) on demand.
  other_worktrees=$(git worktree list --porcelain 2>/dev/null | grep -c '^worktree ')
  other_worktrees=$((other_worktrees - 1))
  if [ "$other_worktrees" -gt 0 ]; then
    echo "worktrees: $other_worktrees besides the primary checkout - some may be stale (merged/closed); scripts/cleanup-worktrees.sh --dry-run reports which, /checkpoint's PR-check step cleans up the provably-merged ones automatically"
  fi
fi

if command -v ddev >/dev/null 2>&1 && ddev describe >/dev/null 2>&1; then
  echo "ddev: running at https://kalshi-whale-poc.ddev.site - the app is probably already up, don't assume a venv is needed"
else
  echo "ddev: not detected as running here - check 'ddev describe' / 'ddev start' before assuming the app needs a fresh venv"
fi

if [ -f ROADMAP.md ]; then
  # ROADMAP.md was condensed 2026-08-09 (see docs/roadmap-archive-2026-08-09.md
  # for the pre-condensing full detail) - the standalone "## P0" section is
  # gone now that it's fully shipped; "## Path to production" is where the
  # remaining safety/correctness-adjacent open questions (shadow-mode review,
  # deployment target, auth model, real position sizing, ...) live instead.
  ptp_open=$(awk '/^## Path to production/,/^## P4/' ROADMAP.md | grep -c '^- \[ \]')
  echo "ROADMAP.md: $ptp_open open path-to-production item(s) - check before touching trading, risk, or auth code"
fi

if [ -d data ]; then
  dbs=$(cd data && ls -- *.db 2>/dev/null | tr '\n' ' ')
  if [ -n "$dbs" ]; then
    echo "data/*.db present ($dbs) - LIVE SQLite files the running dev server reads/writes; don't rm/mv them without checking ddev status first"
  fi
fi

echo "Docs: ROADMAP.md = forward-looking to-do (check items off in place). static/status.html (/status) = historical build record. Update both when a roadmap item ships - see the /sync-status-docs skill."

if [ -d docs/kalshi ]; then
  kalshi_docs=$(find docs/kalshi -maxdepth 1 -name '*.md' ! -name 'README.md' 2>/dev/null | wc -l | tr -d ' ')
  staleness=""
  if [ -f docs/kalshi/llms.txt ]; then
    # git commit time, not file mtime - mtime resets to "now" on a fresh
    # clone/checkout and would always under-report actual age.
    last_commit_ts=$(git log -1 --format=%ct -- docs/kalshi/llms.txt 2>/dev/null)
    if [ -n "$last_commit_ts" ]; then
      age_days=$(( ($(date +%s) - last_commit_ts) / 86400 ))
      if [ "$age_days" -ge 90 ]; then
        staleness=" - last refreshed ${age_days}d ago (per git history), worth a spot-check against docs.kalshi.com/llms.txt for drift"
      fi
    fi
  fi
  echo "docs/kalshi/: $kalshi_docs locally-mirrored Kalshi API doc page(s) (index: llms.txt, provenance: README.md) - authoritative over training-data assumptions about Kalshi's API, read before touching any call site.$staleness"
fi

if [ -f docs/kalshi/CHEATSHEET.md ]; then
  # Printed unconditionally, every session, not gated on staleness or
  # relevance-matching (2026-08-16 direct correction: a rule that only
  # lives in prose - CLAUDE.md or memory - gets skimmed past; this forces
  # the actual known-answers index into context at session start instead).
  # Titles only (grep, not cat) - cheap even as this file grows; open
  # docs/kalshi/CHEATSHEET.md itself for the full entry once a title looks
  # relevant.
  entries=$(grep -c '^## ' docs/kalshi/CHEATSHEET.md 2>/dev/null || echo 0)
  echo "docs/kalshi/CHEATSHEET.md: $entries known-answer entries already resolved from docs/kalshi/ - check titles below before re-deriving/re-guessing any Kalshi data question, and add a new entry whenever a docs/kalshi/ page resolves one that isn't here yet:"
  grep '^## ' docs/kalshi/CHEATSHEET.md 2>/dev/null | sed 's/^/  - /'
fi
