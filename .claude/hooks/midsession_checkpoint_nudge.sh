#!/bin/bash
# UserPromptSubmit hook: mechanical backstop for CLAUDE.md's "Long-session
# workflow" directive, covering the one gap the SessionStart/compact hook
# (post_compact_reorient.sh) doesn't - session time BEFORE any compaction
# happens, which could be arbitrarily long. UserPromptSubmit is one of the
# few hook events whose stdout Claude Code actually adds to context on
# exit 0 (verified against the primary hooks docs, same check that ruled
# out PreCompact/Stop for this purpose).
#
# Time-throttled (real git check at most once per CHECK_INTERVAL_SEC)
# rather than running git status on every single prompt, so the added
# cost is bounded regardless of message frequency - most invocations exit
# after a single file read.
set -uo pipefail
cd "$CLAUDE_PROJECT_DIR" 2>/dev/null || exit 0
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

STATE_FILE=".claude/hooks/.checkpoint_nudge_state"
CHECK_INTERVAL_SEC=600   # at most one real git check per 10 minutes
FILES_THRESHOLD=5
LINES_THRESHOLD=150

now=$(date +%s)
last_check=0
[ -f "$STATE_FILE" ] && last_check=$(cat "$STATE_FILE" 2>/dev/null)
last_check=${last_check:-0}
case "$last_check" in ''|*[!0-9]*) last_check=0 ;; esac

if [ $((now - last_check)) -lt "$CHECK_INTERVAL_SEC" ]; then
  exit 0  # checked recently - stay near-zero-cost on every other prompt
fi

mkdir -p .claude/hooks
echo "$now" > "$STATE_FILE"

files_changed=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
[ "${files_changed:-0}" -eq 0 ] && exit 0

lines_changed=$(git diff HEAD --shortstat 2>/dev/null | grep -oE '[0-9]+ (insertion|deletion)' | grep -oE '[0-9]+' | awk '{s+=$1} END {print s+0}')

if [ "$files_changed" -ge "$FILES_THRESHOLD" ] || [ "${lines_changed:-0}" -ge "$LINES_THRESHOLD" ]; then
  echo "checkpoint nudge (10-min interval check): $files_changed file(s) / ~${lines_changed:-0} line(s) uncommitted right now. If this work is verified (tests passing), consider /checkpoint before it grows further or a compaction blurs the details. See CLAUDE.md's 'Long-session workflow' section."
fi
