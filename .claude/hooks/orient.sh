#!/bin/bash
# SessionStart hook: the facts a session would otherwise rediscover. Runs via
# run_hook.py, so CLAUDE_PROJECT_DIR is the session's own checkout (a linked
# worktree or the primary); ddev, data/*.db, and the GitNexus index all live in
# the primary, which git itself names (the common dir's parent) - no path
# convention to keep in sync with the Python hooks.
set -uo pipefail
root="${CLAUDE_PROJECT_DIR:-$PWD}"
cd "$root" 2>/dev/null || exit 0
primary="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
primary="${primary%/.git}"
[ -n "$primary" ] || primary="$root"

echo "=== autotrade orientation ==="
branch=$(git branch --show-current 2>/dev/null)
dirty=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
echo "git: branch '$branch', $dirty uncommitted change(s)$( [ "$root" != "$primary" ] && echo " (linked worktree; primary: $primary)" )"

# The single next action. Printed first and unconditionally: `continue` in a
# fresh session must not depend on the user remembering anything, and the
# banner is the only text guaranteed to be read (2026-08-29).
if [ -f docs/next-action.md ]; then
  echo "NEXT (docs/next-action.md - this is what 'continue' means; do this one thing, nothing else):"
  grep -v '^#' docs/next-action.md | grep -v '^[[:space:]]*$' | sed 's/^/  /'
fi
if [ "$branch" = "main" ]; then
  echo "branch policy: on main - create feat/|fix/|refactor/|chore/|docs/<name> before implementing (.claude/rules/branching-and-ci.md)"
fi

python3 .claude/hooks/guard_workflow.py --sessions 2>/dev/null \
  | awk -F'\t' '$2 == "other" {print "live session pid " $1 " works in " $3 " - never checkout/stash/reset/commit there; ListAgents + SendMessage before touching files it names"}'

n=$(git worktree list --porcelain 2>/dev/null | grep -c '^worktree ')
# One quantity, used for both the message and the threshold: n counts the
# primary, so every comparison below is against `linked`, never raw n (the
# first version tested raw n and so fired one worktree earlier than its own
# message claimed - caught in PR #515's review).
linked=$((n - 1))
if [ "$linked" -gt 0 ]; then
  echo "worktrees: $linked besides the primary - scripts/cleanup-worktrees.sh --dry-run reports the stale ones; /checkpoint removes the provably merged ones after a merge"
  if [ "$linked" -gt 12 ]; then
    echo "  WARNING: $linked is a lot, and each one costs LIVE APP cpu - watchfiles force-polls on any WSL kernel (it checks for 'microsoft-standard' in uname -r, so no inotify regardless of mounts) and --reload-exclude only discards events the walk already paid for. Measured 2026-09-03 (issue #513): 34 worktrees = 47k files = 42.9% of a core; cleaning to 10 took it to ~21%. Remove yours when done."
  fi
fi

if command -v ddev >/dev/null 2>&1 && (cd "$primary" && ddev describe >/dev/null 2>&1); then
  echo "ddev: running - https://kalshi-whale-poc.ddev.site:8443 (GET /api/state is the fastest live read); no venv needed. ddev exec runs only from the primary: cd /app/.claude/worktrees/<name> inside"
else
  echo "ddev: not running - ddev start from $primary"
fi

dbs=$(ls "$primary"/data/*.db 2>/dev/null | wc -l | tr -d ' ')
if [ "$dbs" -gt 0 ]; then
  echo "data/*.db: $dbs live SQLite files - never rm/mv while ddev is up; POST /api/reset instead of deleting paper_broker.db"
fi

if [ -f docs/kalshi/llms.txt ]; then
  pages=$(find docs/kalshi -maxdepth 1 -name '*.md' ! -name README.md | wc -l | tr -d ' ')
  ts=$(git log -1 --format=%ct -- docs/kalshi/llms.txt 2>/dev/null)
  note=""
  if [ -n "$ts" ]; then
    age=$(( ($(date +%s) - ts) / 86400 ))
    [ "$age" -ge 90 ] && note=" - llms.txt last refreshed ${age}d ago, spot-check docs.kalshi.com/llms.txt for drift"
  fi
  echo "docs/kalshi/: $pages mirrored pages - ground truth over memory for anything Kalshi-shaped (HARD RULE)$note"
fi
if [ -f docs/kalshi/CHEATSHEET.md ]; then
  echo "docs/kalshi/CHEATSHEET.md: known answers - check these titles before re-deriving any Kalshi data question; add an entry when a page resolves a new one:"
  grep '^## ' docs/kalshi/CHEATSHEET.md | sed 's/^/  - /'
fi

if [ -f "$primary/.gitnexus/gitnexus.json" ]; then
  indexed=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("lastCommit","")[:7])' "$primary/.gitnexus/gitnexus.json" 2>/dev/null)
  head=$(git -C "$primary" rev-parse --short=7 HEAD 2>/dev/null)
  if [ -n "$indexed" ] && [ "$indexed" = "$head" ]; then
    echo "gitnexus: index current ($indexed) - impact/context/trace before multi-file edits to strategy, risk, advisory, calibration, kalshi client"
  else
    echo "gitnexus: index at '${indexed:-none}', primary HEAD is $head - stale; run: (cd $primary && npx gitnexus@1.6.10 analyze --skip-agents-md) and restart the session if the MCP then errors"
  fi
else
  echo "gitnexus: no index - run: (cd $primary && npx gitnexus@1.6.10 analyze --skip-agents-md)"
fi

if [ -f docs/open-decisions.md ]; then
  echo "open decisions (docs/open-decisions.md - act on or ask; never re-discover or re-plan these):"
  grep '^- ' docs/open-decisions.md | sed 's/^/  /'
fi

# AQC (tools/quality_coordination.py), the user-built workflow janitor: refresh
# it in the background at most every 6 h (16-26 s with network calls, too slow
# for a 15 s hook), say so, and print whatever the store holds right now.
# /checkpoint runs it inline. The throttle marker lives in the runtime dir,
# not the repo.
aqc_mark="${XDG_RUNTIME_DIR:-/tmp}/claude-workflow-guard/aqc-last-start"
if [ -f "$primary/tools/quality_coordination.py" ]; then
  if [ ! -f "$aqc_mark" ] || [ $(( $(date +%s) - $(stat -c %Y "$aqc_mark" 2>/dev/null || echo 0) )) -gt 21600 ]; then
    mkdir -p "$(dirname "$aqc_mark")" && touch "$aqc_mark"
    ( cd "$primary" && setsid nohup python3 -m tools.quality_coordination >/dev/null 2>&1 & ) >/dev/null 2>&1
    echo "AQC: refreshing in the background (at most every 6 h); the line below is the previous run"
  fi
fi
aqc_db="$primary/tools/quality_coordination_data/quality_coordination.db"
if [ -f "$aqc_db" ]; then
  python3 - "$aqc_db" <<'PY' 2>/dev/null || echo "AQC: store unreadable - python -m tools.quality_coordination"
import sqlite3, sys
c = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
run = c.execute("select ran_at, signals_observed, signals_escalated from coordination_runs order by id desc limit 1").fetchone()
if run:
    print(f"AQC: last run {run[0][:16]}Z, {run[1]} signals, {run[2]} escalation-eligible - /checkpoint re-runs it; python -m tools.quality_coordination for detail")
    for (ident,) in c.execute("select identity from signal_state where state='escalation_eligible' order by identity").fetchall()[:8]:
        print(f"  - {ident}")
else:
    print("AQC: never run - python -m tools.quality_coordination (or /checkpoint)")
PY
fi
