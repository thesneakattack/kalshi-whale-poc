#!/usr/bin/env python3
"""
Emit a copy of the user-global ~/.claude/settings.json with GitNexus's
always-on hooks removed and everything else preserved byte-for-byte in
meaning (same keys, same order, same values).

Why this exists as a script instead of an edit: GitNexus's install added a
`PreToolUse` hook on `Grep|Glob|Bash` and a `PostToolUse` hook on `Bash`,
each spawning a node process on *every* such call. That is the idle
overhead `.claude/rules/tooling-plugins.md` explicitly forbids. Three
Claude sessions tried to remove them directly and all three were refused by
Claude Code's permission classifier, which guards the user-global settings
file against agent edits. So Claude generates the corrected file here and
the user applies it.

The `sql_guard.py` PreToolUse hook in the same file is deliberately
PRESERVED - it is a project safety guard, not toolchain overhead.

Usage:
    python3 .claude/tools/strip_gitnexus_hooks.py [--check]

    (no args)  write the corrected file next to this repo's .claude/
    --check    exit 1 if the live settings still contain GitNexus hooks,
               0 if they are already clean; writes nothing

Apply manually:
    cp ~/.claude/settings.json ~/.claude/settings.json.bak-$(date +%F)
    cp .claude/settings-json-gitnexus-hooks-removed.json ~/.claude/settings.json
"""
import collections
import json
import pathlib
import sys

LIVE = pathlib.Path.home() / ".claude" / "settings.json"
OUT = pathlib.Path(__file__).resolve().parent.parent / "settings-json-gitnexus-hooks-removed.json"


def strip(data):
    """Remove every hook whose command mentions gitnexus. Drops a hook group
    that ends up empty, and drops an event whose groups all vanish, so the
    result has no hollow scaffolding left behind."""
    removed = []
    hooks = data.get("hooks", {})
    for event in list(hooks):
        kept_groups = []
        for group in hooks[event]:
            kept = [h for h in group.get("hooks", []) if "gitnexus" not in h.get("command", "").lower()]
            dropped = [h for h in group.get("hooks", []) if h not in kept]
            for h in dropped:
                removed.append(f"{event} matcher={group.get('matcher')!r}: {h.get('command')}")
            if kept:
                group["hooks"] = kept
                kept_groups.append(group)
        if kept_groups:
            hooks[event] = kept_groups
        else:
            del hooks[event]
    if not hooks:
        data.pop("hooks", None)
    return data, removed


def main():
    if not LIVE.exists():
        print(f"error: {LIVE} does not exist", file=sys.stderr)
        return 2

    data = json.loads(LIVE.read_text(), object_pairs_hook=collections.OrderedDict)
    data, removed = strip(data)

    if "--check" in sys.argv:
        if removed:
            print(f"GitNexus hooks STILL PRESENT in {LIVE}:")
            for r in removed:
                print(f"  - {r}")
            return 1
        print(f"clean: no GitNexus hooks in {LIVE}")
        return 0

    OUT.write_text(json.dumps(data, indent=2) + "\n")
    if removed:
        print(f"wrote {OUT}\nremoved {len(removed)} GitNexus hook(s):")
        for r in removed:
            print(f"  - {r}")
        print(
            "\nApply with:\n"
            "  cp ~/.claude/settings.json ~/.claude/settings.json.bak-$(date +%F)\n"
            f"  cp {OUT} {LIVE}"
        )
    else:
        print(f"wrote {OUT}\nno GitNexus hooks found - live settings are already clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
