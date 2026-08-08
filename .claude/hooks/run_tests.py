#!/usr/bin/env python3
"""PostToolUse hook (Edit|Write matcher): after touching main.py or any
services/*.py file, run the test suite inside the ddev container right away -
catches a regression at the edit that caused it instead of at the next manual
pytest run. No-op for any other file (static/ HTML/JS, config/settings.yaml,
docs, ...) since pytest doesn't cover those. Runs via `ddev exec` per
CLAUDE.md's dev workflow - this project's Python deps live in the fastapi
container, not the host. Silent on success; on failure, exits 2 with the
pytest output so it's fed straight back instead of being discovered later.
"""
import json
import subprocess
import sys


def _in_scope(file_path: str) -> bool:
    if not file_path.endswith(".py"):
        return False
    return file_path in ("main.py",) or file_path.endswith("/main.py") \
        or file_path.startswith("services/") or "/services/" in file_path


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return

    file_path = (payload.get("tool_input") or {}).get("file_path") or ""
    if not _in_scope(file_path):
        return

    try:
        result = subprocess.run(
            ["ddev", "exec", "-s", "fastapi", "python3", "-m", "pytest", "-q"],
            capture_output=True, text=True, timeout=60,
        )
    except Exception:
        return  # ddev not running/not found - don't block the edit over tooling issues

    if result.returncode != 0:
        sys.stderr.write(f"pytest failed after editing {file_path}:\n{result.stdout}\n{result.stderr}")
        sys.exit(2)


if __name__ == "__main__":
    main()
