#!/usr/bin/env python3
"""PostToolUse hook (Edit|Write matcher): after touching a .py file, run
python3 -m py_compile on it and feed any syntax error straight back - faster
than waiting for a manual check, or for ddev's --reload'd fastapi container to
crash on import later. Silent on success (exit 0, no output) to avoid
transcript noise on every routine edit; no-op for anything that isn't a .py
file.
"""
import json
import os
import subprocess
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
        # PYTHONDONTWRITEBYTECODE avoids writing a .pyc cache file at all -
        # without it, a stale root-owned __pycache__ (e.g. from an earlier
        # `ddev exec` pytest run) causes a PermissionError that this hook
        # used to misreport as a syntax error, which it wasn't.
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", file_path],
            capture_output=True, text=True, timeout=15,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    except Exception:
        return

    if result.returncode != 0:
        sys.stderr.write(f"Syntax error in {file_path}:\n{result.stderr}")
        sys.exit(2)


if __name__ == "__main__":
    main()
