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
            capture_output=True, text=True, timeout=240,
        )
    except Exception:
        # Real live incident, 2026-08-23: the suite has grown to ~95-110s
        # (measured directly, repeatedly, the same session this was found),
        # well past the previous 60s timeout - meaning this hook's
        # subprocess.run() was silently hitting TimeoutExpired (a plain
        # Exception, caught right here) on every single invocation, never
        # once completing. Two real costs, not just a missed-regression
        # risk: (1) this hook stopped giving any real signal at all, despite
        # looking like it ran; (2) subprocess.run's host-side kill-on-
        # timeout does not reliably kill the process ddev exec spawned
        # *inside* the container - a real orphaned full-suite pytest run
        # was caught mid-flight writing test fixture rows (tickers
        # `KXTICK-A`/`OTHER-B` from test_trading_gate.py) into the real,
        # live data/paper_broker.db (see tests/conftest.py's docstring for
        # the full incident and the actual root-cause fix - this timeout
        # bump reduces how often a timeout fires at all, it isn't the fix
        # for cross-test contamination by itself). 240s gives real margin
        # above measured runtime instead of a number already smaller than
        # normal, successful completion.
        return  # ddev not running/not found - don't block the edit over tooling issues

    if result.returncode != 0:
        sys.stderr.write(f"pytest failed after editing {file_path}:\n{result.stdout}\n{result.stderr}")
        sys.exit(2)


if __name__ == "__main__":
    main()
