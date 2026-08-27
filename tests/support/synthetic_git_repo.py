"""A disposable, throwaway git repository fixture for AQC's cleanup-action fault-injection
suite (docs/superpowers/specs/2026-08-27-autonomous-quality-coordination-workflow-design.md
§10 point 3: "run against a disposable synthetic git repository fixture, never this
repository"). Every git call here is real (not injected/faked) precisely because this
fixture's whole point is to give the cleanup actions a real repo to act on without ever
touching the actual kalshi-whale-poc checkout.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def _run(args: list[str], cwd: Path) -> None:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed: {result.stderr}")


def make_synthetic_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "synthetic_repo"
    repo.mkdir()
    _run(["git", "init", "-b", "main"], repo)
    _run(["git", "config", "user.email", "test@example.com"], repo)
    _run(["git", "config", "user.name", "Test"], repo)
    (repo / "README.md").write_text("synthetic repo for AQC cleanup fault injection\n")
    _run(["git", "add", "README.md"], repo)
    _run(["git", "commit", "-m", "initial commit"], repo)
    return repo
