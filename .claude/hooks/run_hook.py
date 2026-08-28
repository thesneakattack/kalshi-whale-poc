#!/usr/bin/env python3
"""Hook launcher: run the named hook from the checkout the session is actually in.

Claude Code reads .claude/settings.json from the current worktree but expands
$CLAUDE_PROJECT_DIR to the primary checkout. A hook referenced as
"$CLAUDE_PROJECT_DIR/.claude/hooks/x.py" therefore runs the PRIMARY's copy of the
script from inside a worktree session - the wrong branch's code, the wrong git
state for orientation/nudge hooks, and a hard failure (exit 2 = tool blocked)
when the script only exists on the worktree's branch. Found 2026-08-28 when the
new guard_workflow.py blocked every tool call in its own worktree.

This runs the named hook from the session's own checkout with CLAUDE_PROJECT_DIR
set to that root; a hook that does not exist there is skipped silently (exit 0).
stdout/stderr and the exit code pass through untouched, so JSON decisions and
exit-2 blocks behave exactly as before. The root comes from CLAUDE_HOOK_ROOT
when the settings.json prelude has already resolved it (it has: the prelude
must locate this file from the payload's cwd, never from $CLAUDE_PROJECT_DIR,
which is always the primary checkout); otherwise from the payload's `cwd` via
`git rev-parse --show-toplevel`.

Every settings.json entry is the same prelude with only the hook name varying
(tests/test_hooks_wiring.py asserts that); copy an existing entry, never the
old `$CLAUDE_PROJECT_DIR/.claude/hooks/...` form.
"""
import json
import os
import subprocess
import sys
from pathlib import Path


def resolve_root(cwd: str, fallback: str | None) -> Path:
    r = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True)
    top = r.stdout.strip()
    if top:
        return Path(top)
    return Path(fallback or cwd)


def main(argv: list[str] | None = None, stdin_text: str | None = None, run=subprocess.run) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        return 0
    name = argv[0]
    raw = sys.stdin.read() if stdin_text is None else stdin_text
    try:
        cwd = json.loads(raw).get("cwd") or os.getcwd()
    except Exception:
        cwd = os.getcwd()
    pre_resolved = os.environ.get("CLAUDE_HOOK_ROOT")
    root = Path(pre_resolved) if pre_resolved else resolve_root(cwd, os.environ.get("CLAUDE_PROJECT_DIR"))
    hook = root / ".claude" / "hooks" / name
    if not hook.exists():
        return 0
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))
    if name.endswith(".sh"):
        cmd = ["bash", str(hook), *argv[1:]]
    else:
        cmd = [sys.executable, str(hook), *argv[1:]]
    p = run(cmd, input=raw, text=True, env=env, cwd=str(root))
    return p.returncode


if __name__ == "__main__":
    sys.exit(main())
