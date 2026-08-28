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

echo "Docs: ROADMAP.md = forward-looking to-do (check items off in place, /close-roadmap-item). docs/status-archive-2026-08-26.html = frozen pre-git history; git log is the only maintained record since."

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

# Composite health read - printed so the first investigative move is reading
# it, not opening sqlite3 (measured 198:19 the other way, 2026-08-27 audit).
# Port 8443 is ddev-router's real HTTPS binding for this project (not 443).
# The composite summary measured 4.4s (2026-08-28); 8s leaves margin inside
# this hook's 15s budget.
qs=$(curl -s --max-time 8 https://kalshi-whale-poc.ddev.site:8443/api/quality/summary 2>/dev/null)
if [ -n "$qs" ]; then
  python3 - "$qs" <<'PY' 2>/dev/null || echo "quality: /api/quality/summary returned unparseable JSON"
import json, sys
d = json.loads(sys.argv[1])
def n(k):
    v = d.get(k)
    return len(v) if isinstance(v, (list, dict)) else v
print(f"quality: overall={d.get('overall_status') or d.get('status')} findings={n('findings')} alerts={n('alerts')} faults={n('faults')} - GET /api/quality/summary for detail, before any ad hoc sqlite3/python -c")
PY
else
  echo "quality: /api/quality/summary unreachable (ddev down?) - check before assuming health"
fi

# Installed plugin capabilities - printed every session because routing that
# lived only in .claude/rules/tooling-plugins.md was used 0 times in 10 days.
cat <<'EOF'
plugins (use them; never write a project skill or tool that duplicates one):
  superpowers: brainstorming | writing-plans | executing-plans | test-driven-development | systematic-debugging (any bug) | verification-before-completion (before "done") | requesting-code-review | using-git-worktrees
  gitnexus MCP: impact/context/trace before multi-file edits to strategy, risk, advisory, calibration, kalshi client
  dimensional-analysis: after implementing any cents/dollars/probability/contracts/P&L math
  chrome-devtools MCP: any browser-facing evidence (console, network, WebSocket)
  context7 MCP: library docs for FastAPI/Pydantic/asyncio; never for Kalshi (docs/kalshi/ is canonical)
  github MCP / gh: PRs, statuses, issues; github-issues-kanban skill owns claim/dispatch on the board
EOF

# AQC (tools/quality_coordination.py) - the user-built workflow janitor. Too
# slow for this hook's budget (15.8s measured, network calls), so /checkpoint
# runs it; this prints the last stored result so no session forgets it exists.
if [ -f tools/quality_coordination_data/quality_coordination.db ]; then
  python3 - <<'PY' 2>/dev/null || echo "AQC: store unreadable - run: python -m tools.quality_coordination"
import sqlite3
c = sqlite3.connect("file:tools/quality_coordination_data/quality_coordination.db?mode=ro", uri=True)
run = c.execute("select ran_at, signals_observed, signals_escalated from coordination_runs order by id desc limit 1").fetchone()
if not run:
    print("AQC: never run - run: python -m tools.quality_coordination (or /checkpoint)")
else:
    esc = c.execute("select identity from signal_state where state='escalation_eligible' order by identity").fetchall()
    plans = c.execute("select count(*) from signal_state where identity like 'ledger:plan:%' and state!='resolved'").fetchone()[0]
    print(f"AQC: last run {run[0][:16]}Z, {run[1]} signals, {run[2]} escalation-eligible, {plans} plan(s) with unfinished tasks - /checkpoint re-runs it; python -m tools.quality_coordination for detail")
    for (ident,) in esc[:8]:
        print(f"  - {ident}")
PY
else
  echo "AQC: never run in this checkout - run: python -m tools.quality_coordination (or /checkpoint)"
fi

if [ -f docs/open-decisions.md ]; then
  echo "open decisions (docs/open-decisions.md - act on or ask about these; don't re-discover them, don't write a new plan for them):"
  grep '^- ' docs/open-decisions.md | sed 's/^/  /'
fi
