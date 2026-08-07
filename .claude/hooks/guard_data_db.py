#!/usr/bin/env python3
"""PreToolUse hook (Bash matcher): ask for confirmation before a command
deletes, moves, or truncates a data/*.db file. Those are live SQLite
databases the running ddev dev server actively reads and writes
(paper_broker.db, risk_state.db, signal_log.db, accounts.db) - deleting or
moving one out from under it desyncs in-memory app state from disk until the
next restart (this happened once already - see CLAUDE.md). Prefer
POST /api/reset over deleting paper_broker.db by hand.

Best-effort text matching on the raw command string, not a shell parser -
same limitation as the global sql_guard.py hook this mirrors.
"""
import json
import re
import sys

DB_TARGET = re.compile(r"data/\S*\.db\b")
DESTRUCTIVE = re.compile(r"\b(rm|mv|shred|truncate)\b")
TRUNCATE_REDIRECT = re.compile(r">\s*data/\S*\.db\b")


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return

    command = (payload.get("tool_input") or {}).get("command") or ""
    if not command or not DB_TARGET.search(command):
        return

    if not (DESTRUCTIVE.search(command) or TRUNCATE_REDIRECT.search(command)):
        return

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": (
                "Command targets a data/*.db file with rm/mv/truncate/shred. These are "
                "live SQLite databases the running ddev dev server may be reading/writing "
                "right now - deleting or moving one mid-run desyncs in-memory state from "
                "disk until the next restart. Check `ddev describe` first, or use "
                "POST /api/reset instead of deleting paper_broker.db by hand."
            ),
        }
    }))


if __name__ == "__main__":
    main()
