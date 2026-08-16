#!/bin/bash
# SessionStart hook (matcher: "compact"): fires immediately after any
# compaction - manual /compact or automatic - Claude Code's documented
# mechanism for re-injecting context that summarization can blur (stdout
# from a SessionStart hook is added to context on exit 0; PreCompact/Stop
# hooks' stdout is not, which is why this uses SessionStart/compact rather
# than either of those - see CLAUDE.md's "Long-session workflow" section).
# Deliberately narrower than session_orient.sh (matcher "*", full
# startup/clear/resume orientation): just the checkpoint-relevant state
# that's easy to lose track of right after a summarization pass.
set -uo pipefail
cd "$CLAUDE_PROJECT_DIR" 2>/dev/null || exit 0

echo "=== post-compaction checkpoint check ==="

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  dirty=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
  if [ "${dirty:-0}" -gt 0 ]; then
    stat=$(git diff --shortstat HEAD 2>/dev/null | sed 's/^ *//')
    echo "git: $dirty uncommitted change(s) ($stat) - if this work is verified (tests passing), consider /checkpoint now rather than carrying it forward through more compactions. See CLAUDE.md's 'Long-session workflow' section."
  else
    echo "git: working tree clean."
  fi
fi

echo "Re-check the todo list against what's actually done - summarization can blur exact task state."
