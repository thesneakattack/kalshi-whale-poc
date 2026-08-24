"""Resource-lifecycle scanner for the Quality Control Plane's static audit
CLI (docs/superpowers/plans/2026-08-24-quality-control-plane.md, Task 5).
Flags a Kalshi client (KalshiClient/KalshiAccountClient/
KalshiTradeWebSocketClient - every class in this codebase with an async
close()) constructed inside a function body and never closed within that
same function - the "Unclosed connector" leak class documented live at
services/diagnostics/routes.py's 2026-08-23 fix (its own comment: "Real
leak found 2026-08-23 auditing every KalshiClient() call site for the same
missing-close() shape that caused index_stream_handlers._spec_for's live
'Unclosed connector' incident").

Scoped to constructions inside a function body only - a module-level
singleton construction (services/app_state.py's `account =
KalshiAccountClient(...)`, `trade_stream = KalshiTradeWebSocketClient(...)`)
is a deliberately different, process-lifetime pattern, not a per-call
resource that should ever be closed.

A construction is NOT flagged when ownership visibly leaves the function
instead of being closed there - returned bare (`return client`, not
`return await client.get_market(...)`, which still leaks the client even
though it returns something derived from it), or stored into a container/
attribute for reuse beyond this call (`cache[key] = client`) - matching the
deliberately long-lived, explicitly documented cached-client pattern in
services/whale_stream/whale_stream_handlers.py's _stream_market_client.

v1 only proves presence/absence of a close() call reachable from the
constructing function; it does not attempt real control-flow/exception-path
correctness.
"""
from __future__ import annotations

import ast
from pathlib import Path

from services.quality.models import QualityFinding
from tools.quality_audit import source

_CLOSEABLE_CLASS_NAMES = {"KalshiClient", "KalshiAccountClient", "KalshiTradeWebSocketClient"}

_FunctionDefNode = ast.FunctionDef | ast.AsyncFunctionDef


def _construction_class_name(call: ast.Call) -> str | None:
    # Only a bare `KalshiClient(...)` call counts - not `kpa.KalshiClient(...)`,
    # the third-party SDK class services/kalshi_client.py itself wraps, which
    # this codebase always assigns to `self._client` (an Attribute target,
    # already excluded below) rather than a bare local name.
    if isinstance(call.func, ast.Name):
        return call.func.id
    return None


def _iter_constructions(node: ast.AST, current_func: _FunctionDefNode | None):
    """Yields (enclosing_function, assign_node) for every `name = <Closeable>(...)`
    found strictly inside a function body, attributed to its innermost
    enclosing function (a construction inside a nested function belongs to
    that nested function, not any outer one)."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield from _iter_constructions(child, child)
            continue
        if current_func is not None and isinstance(child, ast.Assign) and isinstance(child.value, ast.Call):
            if _construction_class_name(child.value) in _CLOSEABLE_CLASS_NAMES:
                yield current_func, child
        yield from _iter_constructions(child, current_func)


def _is_closed(func_node: _FunctionDefNode, var_name: str) -> bool:
    for node in ast.walk(func_node):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "close"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == var_name
        ):
            return True
    return False


def _ownership_escapes(func_node: _FunctionDefNode, var_name: str) -> bool:
    for node in ast.walk(func_node):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Name) and node.value.id == var_name:
            return True
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name) and node.value.id == var_name:
            if any(isinstance(target, (ast.Subscript, ast.Attribute)) for target in node.targets):
                return True
    return False


def scan_resource_lifecycle(repo_root: Path) -> list[QualityFinding]:
    repo_root = Path(repo_root)
    findings: list[QualityFinding] = []
    for path in source.iter_python_files(repo_root, include_tests=False):
        tree = source.parse_python(path)
        for func_node, assign_node in _iter_constructions(tree, None):
            class_name = _construction_class_name(assign_node.value)
            for target in assign_node.targets:
                if not isinstance(target, ast.Name):
                    continue
                var_name = target.id
                if _is_closed(func_node, var_name) or _ownership_escapes(func_node, var_name):
                    continue
                module = source.module_dotted_path(repo_root, path)
                findings.append(
                    QualityFinding(
                        finding_id=f"resource-unclosed:{module}:{func_node.name}:{var_name}",
                        check="resource-lifecycle",
                        severity="error",
                        confidence="high",
                        source="ci",
                        scope=f"{module}.{func_node.name}",
                        summary=f"{var_name} ({class_name}) is constructed in {func_node.name} but never closed",
                        evidence={"path": source.relative_path(repo_root, path), "line": assign_node.lineno},
                        remediation=(
                            f"wrap usage in try/finally and call await {var_name}.close(), "
                            "or return/store it if it's intentionally retained beyond this call"
                        ),
                    )
                )
    return findings
