"""API-usage inventory scanner for the Quality Control Plane's static audit
CLI (docs/archive/lane-6-observability-quality-safety/plans/2026-08-24-quality-control-plane.md, Task 5, moved there 2026-09-06, planning-lanes migration).
Purely informational (severity=info) - never fails any gate, --strict
included. Catalogs every `client.<method>(...)`/`account.<method>(...)`
call site in the non-test codebase (the two variable names this codebase
consistently uses for KalshiClient/KalshiAccountClient instances - see
tools/quality_audit/resources.py's construction-site survey), grouped by
method name, as raw material for a future REST/WS usage review - not a
blanket ban or a claim that any of these calls are wrong.
"""
from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

from services.quality.models import QualityFinding
from tools.quality_audit import source

_TRACKED_RECEIVER_NAMES = {"client", "account"}


def _iter_tracked_calls(repo_root: Path):
    for path in source.iter_python_files(repo_root, include_tests=False):
        tree = source.parse_python(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            receiver = node.func.value
            if not isinstance(receiver, ast.Name) or receiver.id not in _TRACKED_RECEIVER_NAMES:
                continue
            yield receiver.id, node.func.attr, path, node.lineno


def scan_api_usage(repo_root: Path) -> list[QualityFinding]:
    repo_root = Path(repo_root)
    sites: dict[tuple[str, str], list[str]] = defaultdict(list)
    for receiver, method, path, lineno in _iter_tracked_calls(repo_root):
        sites[(receiver, method)].append(f"{source.relative_path(repo_root, path)}:{lineno}")

    findings: list[QualityFinding] = []
    for (receiver, method), locations in sorted(sites.items()):
        findings.append(
            QualityFinding(
                finding_id=f"api-usage:{receiver}.{method}",
                check="api-usage-inventory",
                severity="info",
                confidence="high",
                source="ci",
                scope=f"{receiver}.{method}",
                summary=f"{receiver}.{method}(...) called at {len(locations)} site(s)",
                evidence={"call_sites": locations},
            )
        )
    return findings
