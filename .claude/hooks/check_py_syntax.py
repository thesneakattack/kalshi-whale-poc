#!/usr/bin/env python3
"""PostToolUse hook (Edit|Write matcher): after touching a .py file, parse it
with the builtin compile() and feed any SyntaxError straight back - faster
than waiting for a manual check, or for ddev's --reload'd fastapi container to
crash on import later. Silent on success (exit 0, no output) to avoid
transcript noise on every routine edit; no-op for anything that isn't a .py
file.

Deliberately uses compile(source, ..., 'exec') rather than `python3 -m
py_compile` or py_compile.compile(): both of those always write a real .pyc
to disk (py_compile.compile() even refuses cfile='/dev/null' outright -
"non-regular file"), and this project's containers run some commands as root
against the same bind-mounted repo, so a stale root-owned __pycache__ has
twice now turned a plain PermissionError into a misreported "syntax error."
compile() only builds a code object in memory - no file, no cache, no
ownership to trip over.
"""
import json
import sys


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return

    file_path = (payload.get("tool_input") or {}).get("file_path") or ""
    if not file_path.endswith(".py"):
        return

    try:
        with open(file_path, "rb") as f:
            source = f.read()
    except Exception:
        return

    try:
        compile(source, file_path, "exec")
    except SyntaxError as e:
        sys.stderr.write(f"Syntax error in {file_path}:\n{e}")
        sys.exit(2)
    except Exception:
        return


if __name__ == "__main__":
    main()
