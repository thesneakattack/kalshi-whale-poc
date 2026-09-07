"""Background-scheduler wiring scanner for the Quality Control Plane's
static audit CLI (docs/archive/lane-6-observability-quality-safety/plans/2026-08-24-quality-control-plane.md (moved there 2026-09-06, planning-lanes migration),
Task 4). Flags a `_maybe_*`-named function that has the shape of a
background-task scheduler (calls task_supervisor.supervise(...) or
asyncio.create_task(...), or assigns into a `[...]["task"]`-style slot) but
is never referenced anywhere else in the non-test codebase - the "defined a
scheduler, forgot to wire the tick loop to call it" failure class.

Reference counting excludes only the function's own body (so a same-file
caller - e.g. a `_maybe_*` helper defined and called directly inside
main.py's own tick loop, a real, legitimate pattern in this repo - still
counts as wired) and tests/. v1 only proves presence/absence of a
reference, not real control-flow reachability.
"""
from __future__ import annotations

import ast
from pathlib import Path

from services.quality.models import QualityFinding
from tools.quality_audit import source

_SCHEDULER_PREFIX = "_maybe_"
_SCHEDULER_CALL_NAMES = {"task_supervisor.supervise", "asyncio.create_task"}
_SCHEDULER_SUBSCRIPT_KEY = "task"

_FunctionDefNode = ast.FunctionDef | ast.AsyncFunctionDef


def _dotted_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _subscript_key(node: ast.Subscript) -> str | None:
    key = node.slice
    if isinstance(key, ast.Constant) and isinstance(key.value, str):
        return key.value
    return None


def _is_scheduler_style(func_node: _FunctionDefNode) -> bool:
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call) and _dotted_name(node.func) in _SCHEDULER_CALL_NAMES:
            return True
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and _subscript_key(target) == _SCHEDULER_SUBSCRIPT_KEY:
                    return True
    return False


def _find_scheduler_functions(repo_root: Path) -> list[tuple[str, Path, _FunctionDefNode]]:
    found: list[tuple[str, Path, _FunctionDefNode]] = []
    for path in source.iter_python_files(repo_root, include_tests=False):
        tree = source.parse_python(path)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith(_SCHEDULER_PREFIX):
                continue
            if _is_scheduler_style(node):
                found.append((node.name, path, node))
    return found


def _count_external_references(
    repo_root: Path, function_name: str, defining_path: Path, def_node: _FunctionDefNode
) -> int:
    body_start = def_node.lineno
    body_end = getattr(def_node, "end_lineno", None) or def_node.lineno
    count = 0
    for path in source.iter_python_files(repo_root, include_tests=False):
        tree = source.parse_python(path)
        same_file = path == defining_path
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == function_name:
                lineno = node.lineno
            elif isinstance(node, ast.Attribute) and node.attr == function_name:
                lineno = node.lineno
            else:
                continue
            if same_file and body_start <= lineno <= body_end:
                continue
            count += 1
    return count


def scan_background_wiring(repo_root: Path) -> list[QualityFinding]:
    repo_root = Path(repo_root)
    findings: list[QualityFinding] = []
    for function_name, path, def_node in _find_scheduler_functions(repo_root):
        if _count_external_references(repo_root, function_name, path, def_node) > 0:
            continue
        module = source.module_dotted_path(repo_root, path)
        findings.append(
            QualityFinding(
                finding_id=f"background-unwired:{module}:{function_name}",
                check="background-wiring",
                severity="error",
                confidence="high",
                source="ci",
                scope=f"{module}.{function_name}",
                summary=(
                    f"{function_name} has the shape of a background scheduler "
                    "but is never called outside its own definition"
                ),
                evidence={"path": source.relative_path(repo_root, path), "line": def_node.lineno},
                remediation=f"call {function_name}(...) from the trading loop (main.py) or another live call path",
            )
        )
    return findings
