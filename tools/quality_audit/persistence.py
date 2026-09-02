"""Persistence-isolation scanner for the Quality Control Plane's static
audit CLI (docs/superpowers/plans/2026-08-24-quality-control-plane.md, Task
5). Cross-checks every module-level `DB_PATH = ...` assignment under
services/ (and main.py) against tests/support/runtime_isolation.py's
PERSISTENCE_MODULE_PATHS registry (Task 2) - a module with real persisted
state that isn't registered there is invisible to every isolation
guarantee that registry backs (see that module's own docstring for the
2026-08-23 test-contamination incident this exists to prevent from
recurring in a different shape: a new DB_PATH owner nobody remembered to
register).

Deliberately excludes tools/: PERSISTENCE_MODULE_PATHS is specifically
the application's own test-isolation registry, and a tools/-owned module
manages its own persistence isolation directly in its own tests (e.g.
tools/quality_ratchet.py's tests explicitly monkeypatch its DB_PATH in
every test function) rather than relying on the app's shared registry.
Requiring registration would make an app-side test-support registry the
owner of standalone tooling's isolation, which has already gone wrong
once: tools/quality_ratchet.py (tools/quality_coordination.py at the
time, renamed 2026-08-27) originally shipped registered there. This
paragraph used to cite a CLAUDE.md rule that no longer exists (removed
2026-09-02); the exclusion stands on the reasoning above, not on that
rule.

Reads PERSISTENCE_MODULE_PATHS as a plain tuple of strings only - never
calls tests.support.runtime_isolation.loaded_registered_modules(), which
imports every registered module (and, transitively, constructs
services.app_state's eager singletons) as an application side effect this
static scanner must never trigger.
"""
from __future__ import annotations

import ast
from pathlib import Path

from services.quality.models import QualityFinding
from tests.support.runtime_isolation import PERSISTENCE_MODULE_PATHS
from tools.quality_audit import source


def _find_db_path_owners(repo_root: Path) -> dict[str, Path]:
    owners: dict[str, Path] = {}
    for path in source.iter_python_files(repo_root, include_tests=False):
        if path.relative_to(repo_root).parts[0] == "tools":
            continue  # standalone workflow tooling manages its own test isolation - see module docstring
        tree = source.parse_python(path)
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "DB_PATH" for target in node.targets
            ):
                owners[source.module_dotted_path(repo_root, path)] = path
                break
    return owners


def scan_persistence_isolation(repo_root: Path) -> list[QualityFinding]:
    repo_root = Path(repo_root)
    owners = _find_db_path_owners(repo_root)
    registered = set(PERSISTENCE_MODULE_PATHS)

    findings: list[QualityFinding] = []
    for module, path in sorted(owners.items()):
        if module in registered:
            continue
        findings.append(
            QualityFinding(
                finding_id=f"persistence-unisolated:{module}",
                check="persistence-isolation",
                severity="error",
                confidence="high",
                source="ci",
                scope=module,
                summary=(
                    f"{module} defines DB_PATH but is not registered in "
                    "tests/support/runtime_isolation.py's PERSISTENCE_MODULE_PATHS"
                ),
                evidence={"path": source.relative_path(repo_root, path)},
                remediation=(
                    "add this module's dotted path to PERSISTENCE_MODULE_PATHS "
                    "in tests/support/runtime_isolation.py"
                ),
            )
        )
    return findings
