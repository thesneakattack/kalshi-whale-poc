"""Router-registration scanner for the Quality Control Plane's static audit
CLI (docs/superpowers/plans/2026-08-24-quality-control-plane.md, Task 4).
Flags an APIRouter defined under a service package but never mounted via
app.include_router(...) in main.py - the one FastAPI entrypoint per
CLAUDE.md's "Quick file map" (`main.py` - FastAPI app, API/auth routes
only, no HTML). A module can opt out deliberately with a
`# quality-audit: standalone-router` comment anywhere in its source.

AST-based, not a raw grep: main.py's own router-mounting style is
`from services.foo import routes as foo_routes` followed by
`app.include_router(foo_routes.router)`, so this resolves the import-alias
chain rather than string-matching "include_router" against router names.
"""
from __future__ import annotations

import ast
from pathlib import Path

from services.quality.models import QualityFinding
from tools.quality_audit import source

_STANDALONE_MARKER = "# quality-audit: standalone-router"
_ENTRYPOINT_FILENAME = "main.py"


def _find_router_assignments(repo_root: Path) -> dict[str, Path]:
    """Every module defining `<name> = APIRouter(...)` at module scope, keyed
    by that module's dotted import path."""
    found: dict[str, Path] = {}
    for path in source.iter_python_files(repo_root, include_tests=False):
        tree = source.parse_python(path)
        for node in tree.body:
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            callee = node.value.func
            callee_name = callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, "id", None)
            if callee_name != "APIRouter":
                continue
            found[source.module_dotted_path(repo_root, path)] = path
            break
    return found


def _import_alias_map(tree: ast.Module) -> dict[str, str]:
    """Maps a local name to the dotted path it was imported from, for every
    `from X import Y [as Z]` in a module."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        for alias in node.names:
            local_name = alias.asname or alias.name
            aliases[local_name] = f"{node.module}.{alias.name}"
    return aliases


def _find_mounted_router_modules(repo_root: Path) -> set[str]:
    entrypoint = repo_root / _ENTRYPOINT_FILENAME
    if not entrypoint.exists():
        return set()
    tree = source.parse_python(entrypoint)
    aliases = _import_alias_map(tree)
    mounted: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "include_router":
            continue
        if not node.args:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name):
            dotted = aliases.get(arg.value.id)
            if dotted:
                mounted.add(dotted)
        elif isinstance(arg, ast.Name):
            dotted = aliases.get(arg.id)
            if dotted:
                # symbol import - `from services.foo.routes import router` -
                # the module path is the symbol's dotted path minus its own
                # trailing attribute name.
                mounted.add(dotted.rsplit(".", 1)[0])
    return mounted


def _has_standalone_marker(path: Path) -> bool:
    return _STANDALONE_MARKER in source.read_text(path)


def scan_router_registration(repo_root: Path) -> list[QualityFinding]:
    repo_root = Path(repo_root)
    router_modules = _find_router_assignments(repo_root)
    mounted = _find_mounted_router_modules(repo_root)

    findings: list[QualityFinding] = []
    for dotted_module, path in sorted(router_modules.items()):
        if dotted_module in mounted or _has_standalone_marker(path):
            continue
        findings.append(
            QualityFinding(
                finding_id=f"router-unmounted:{dotted_module}",
                check="router-registration",
                severity="error",
                confidence="high",
                source="ci",
                scope=dotted_module,
                summary=(
                    f"{dotted_module} defines an APIRouter but is never mounted "
                    f"via app.include_router in {_ENTRYPOINT_FILENAME}"
                ),
                evidence={"path": source.relative_path(repo_root, path)},
                remediation=(
                    f"mount it in {_ENTRYPOINT_FILENAME} (app.include_router(...)), "
                    f"or add `{_STANDALONE_MARKER}` if it's intentionally unmounted"
                ),
            )
        )
    return findings
