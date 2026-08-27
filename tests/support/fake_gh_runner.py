"""Generic fake for a `Callable[[Sequence[str]], subprocess.CompletedProcess[str]]`
runner - used to test both git and gh CLI wrappers in tools/kanban_sync
without ever shelling out for real (spec §13: "no live GitHub calls
required for the test suite itself").
"""
from __future__ import annotations

import subprocess
from typing import Sequence


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self._responses: list[subprocess.CompletedProcess] = []

    def queue(self, stdout: str, returncode: int = 0, stderr: str = "") -> None:
        self._responses.append(
            subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)
        )

    def __call__(self, args: Sequence[str]) -> subprocess.CompletedProcess:
        self.calls.append(list(args))
        if not self._responses:
            raise AssertionError(f"FakeRunner called with {list(args)} but no response was queued")
        return self._responses.pop(0)
