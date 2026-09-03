"""scripts/ci-testmon-run.sh's PYTEST_SPLIT validation and target-file
resolution - the two things this script does before it ever invokes
pytest for real. Does not exercise the actual pytest invocation (that's
this repo's own full suite, run for real by .woodpecker/tests-pytest-app.yml
and tests-pytest-tooling.yml) - just the fail-loud contract and that it
asks tools.classify_pytest_app_vs_tooling for the right split."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "ci-testmon-run.sh"

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="needs sh")


def _run(env_extra: dict) -> subprocess.CompletedProcess:
    env = {"PATH": os.environ["PATH"], "CI_PIPELINE_EVENT": "manual", **env_extra}
    return subprocess.run(["sh", str(SCRIPT)], cwd=ROOT, capture_output=True, text=True, env=env)


def test_missing_pytest_split_fails_loud():
    result = _run({})
    assert result.returncode == 1
    assert "PYTEST_SPLIT must be" in result.stderr


def test_invalid_pytest_split_fails_loud():
    result = _run({"PYTEST_SPLIT": "everything"})
    assert result.returncode == 1
    assert "PYTEST_SPLIT must be" in result.stderr


def test_valid_split_reaches_pytest_invocation(monkeypatch):
    # Don't actually run the real (slow, full) suite here - swap PATH to a
    # directory with fake `python`/`python3` binaries that just echo their
    # argv and exit 0. The script calls `python3` (for the classify helper)
    # and bare `python` (for the actual pytest invocation) - both need
    # faking, or the real interpreter answers instead and the test proves
    # nothing about which args ci-testmon-run.sh actually built.
    fake_bin = ROOT / "build" / "test_ci_testmon_run_fakebin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    real_python3 = shutil.which("python3")
    assert real_python3, "need a real python3 on PATH to run the classify helper for real"
    fake_python3 = fake_bin / "python3"
    fake_python3.write_text(f"#!/bin/sh\nexec {real_python3} \"$@\"\n")
    fake_python3.chmod(0o755)
    fake_python = fake_bin / "python"
    fake_python.write_text("#!/bin/sh\necho FAKE_PYTHON_ARGS: \"$@\"\n")
    fake_python.chmod(0o755)
    try:
        result = subprocess.run(
            ["sh", str(SCRIPT)], cwd=ROOT, capture_output=True, text=True,
            env={"PATH": f"{fake_bin}:{os.environ['PATH']}", "CI_PIPELINE_EVENT": "manual",
                 "PYTEST_SPLIT": "tooling"},
        )
        assert result.returncode == 0, result.stderr
        assert "FAKE_PYTHON_ARGS:" in result.stdout
        assert "-m pytest" in result.stdout
        assert "tests/test_ci_testmon_run.py" in result.stdout
    finally:
        shutil.rmtree(fake_bin, ignore_errors=True)
