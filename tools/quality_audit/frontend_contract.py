"""Frontend-to-backend API contract scanner for the Quality Control Plane's
static audit CLI (docs/superpowers/plans/2026-08-24-quality-control-plane.md,
Task 7). Cross-checks every `fetchJSON(...)`/`fetch(...)` call in
frontend/src/js/*.js (the real, hand-authored ES module source per CLAUDE.md
- not static/js/dashboard.bundle.js, which is generated) against every
`@router.<method>(...)`/`@app.<method>(...)` FastAPI route decorator in the
backend. A frontend call whose literal path has no matching backend route
is a high-confidence error (a stale/typo'd endpoint would 404 for real
users); a backend route with no frontend caller is informational only (a
route can legitimately exist for another consumer - curl, a script, a
future feature - without every route needing a UI caller).

No JS parser is available in this toolchain, so frontend extraction is
regex-based on the raw source text, deliberately narrow per this task's own
spec: it only resolves a call whose first argument is a plain string or a
template literal starting with `/` (any `${...}` interpolation inside is
normalized to a wildcard segment, and everything from `?` onward is
dropped as a query string). A call built through concatenation, a bare
variable, or any other opaque expression is reported as a low-confidence,
informational "frontend-route-unknown" finding - explicitly not guessed at
- rather than silently skipped or misclassified as a real mismatch.
"""
from __future__ import annotations

import ast
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from services.quality.models import QualityFinding
from tools.quality_audit import source

_HTTP_METHODS = {"get", "post", "put", "delete", "patch"}
_ROUTE_OBJECT_NAMES = {"router", "app"}

_BACKEND_PARAM_RE = re.compile(r"\{[^}]+\}")
_TEMPLATE_EXPR_RE = re.compile(r"\$\{[^}]*\}")
_CALL_OPEN_RE = re.compile(r"\bfetch(?:JSON)?\(")
_METHOD_RE = re.compile(r"method\s*:\s*['\"](\w+)['\"]")


@dataclass(frozen=True)
class RouteSpec:
    method: str
    path_template: str


@dataclass(frozen=True)
class FrontendCall:
    method: str
    path_template: str | None
    file: str
    line: int
    raw: str


def _normalize_path(raw_path: str) -> str:
    """Strips a query string and collapses any dynamic segment (FastAPI's
    `{name}` or JS's `${expr}`) to a single wildcard, so a backend route and
    a frontend call resolve to the same template string when they match.

    `${expr}` must be substituted before `{name}` - the latter's pattern
    would otherwise match just the `{expr}` part of a `${expr}` segment,
    leaving a stray `$` behind."""
    raw_path = raw_path.split("?", 1)[0]
    raw_path = _TEMPLATE_EXPR_RE.sub("*", raw_path)
    raw_path = _BACKEND_PARAM_RE.sub("*", raw_path)
    return raw_path


def extract_backend_routes(repo_root: Path) -> set[RouteSpec]:
    repo_root = Path(repo_root)
    routes: set[RouteSpec] = set()
    for path in source.iter_python_files(repo_root, include_tests=False):
        tree = source.parse_python(path)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                func = decorator.func
                if func.attr not in _HTTP_METHODS:
                    continue
                if not isinstance(func.value, ast.Name) or func.value.id not in _ROUTE_OBJECT_NAMES:
                    continue
                if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                    continue
                path_value = decorator.args[0].value
                if not isinstance(path_value, str):
                    continue
                routes.add(RouteSpec(method=func.attr.upper(), path_template=_normalize_path(path_value)))
    return routes


def _detect_method(args_text: str) -> str:
    match = _METHOD_RE.search(args_text)
    return match.group(1).upper() if match else "GET"


def _call_argument_span(text: str, open_paren_index: int) -> str:
    """Given the index of the '(' opening a call's argument list, returns
    everything up to (but not including) that call's own matching ')' -
    naive char-by-char paren/quote balancing, not real JS parsing, but
    enough to stop at the end of THIS call's arguments instead of bleeding
    into a later, unrelated call (the actual bug a fixed-size lookahead
    window had: it grabbed a `method: 'POST'` belonging to a subsequent
    call on a bare `fetchJSON('/some/url')` with no options object at all,
    real-repo instance: frontend/src/js/history-core.js's
    /api/suggestions/declined call, immediately followed a few lines later
    by a real POST call at the same nesting depth)."""
    depth = 1
    i = open_paren_index + 1
    in_string: str | None = None
    while i < len(text) and depth > 0:
        ch = text[i]
        if in_string:
            if ch == "\\":
                i += 2
                continue
            if ch == in_string:
                in_string = None
        elif ch in "'\"`":
            in_string = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        i += 1
    return text[open_paren_index + 1 : i - 1]


def extract_frontend_calls(repo_root: Path) -> list[FrontendCall]:
    repo_root = Path(repo_root)
    js_dir = repo_root / "frontend" / "src" / "js"
    if not js_dir.exists():
        return []

    calls: list[FrontendCall] = []
    for path in sorted(js_dir.glob("*.js")):
        text = source.read_text(path)
        rel_path = source.relative_path(repo_root, path)
        for match in _CALL_OPEN_RE.finditer(text):
            open_paren_index = match.end() - 1
            line = text.count("\n", 0, match.start()) + 1
            args_text = _call_argument_span(text, open_paren_index)
            method = _detect_method(args_text)

            stripped = args_text.lstrip()
            quote = stripped[0] if stripped else ""
            if quote not in "'\"`":
                calls.append(FrontendCall(method=method, path_template=None, file=rel_path, line=line, raw=stripped[:60]))
                continue

            end = stripped.find(quote, 1)
            if end == -1:
                calls.append(FrontendCall(method=method, path_template=None, file=rel_path, line=line, raw=stripped[:60]))
                continue

            body = stripped[1:end]
            if not body.startswith("/"):
                calls.append(FrontendCall(method=method, path_template=None, file=rel_path, line=line, raw=body))
                continue

            calls.append(FrontendCall(method=method, path_template=_normalize_path(body), file=rel_path, line=line, raw=body))
    return calls


def scan_frontend_contract(repo_root: Path) -> list[QualityFinding]:
    repo_root = Path(repo_root)
    backend_routes = extract_backend_routes(repo_root)
    frontend_calls = extract_frontend_calls(repo_root)
    backend_lookup = {(r.method, r.path_template) for r in backend_routes}

    findings: list[QualityFinding] = []

    missing: dict[tuple[str, str], list[str]] = defaultdict(list)
    for call in frontend_calls:
        if call.path_template is None:
            findings.append(
                QualityFinding(
                    finding_id=f"frontend-route-unknown:{call.file}:{call.line}",
                    check="frontend-api-contract",
                    severity="info",
                    confidence="low",
                    source="ci",
                    scope=f"{call.file}:{call.line}",
                    summary=(
                        f"fetch/fetchJSON call at {call.file}:{call.line} has a non-literal URL "
                        "and was not checked against backend routes"
                    ),
                    evidence={"raw": call.raw},
                )
            )
            continue
        key = (call.method, call.path_template)
        if key not in backend_lookup:
            missing[key].append(f"{call.file}:{call.line}")

    for (method, path_template), sites in sorted(missing.items()):
        findings.append(
            QualityFinding(
                finding_id=f"frontend-route-missing:{method}:{path_template}",
                check="frontend-api-contract",
                severity="error",
                confidence="high",
                source="ci",
                scope=f"{method} {path_template}",
                summary=f"frontend calls {method} {path_template} but no matching backend route was found",
                evidence={"call_sites": sites},
                remediation="add the matching backend route, or fix the frontend URL if it's a typo/stale endpoint",
            )
        )

    frontend_lookup = {(c.method, c.path_template) for c in frontend_calls if c.path_template is not None}
    for route in sorted(backend_routes, key=lambda r: (r.method, r.path_template)):
        if (route.method, route.path_template) in frontend_lookup:
            continue
        findings.append(
            QualityFinding(
                finding_id=f"backend-route-unused:{route.method}:{route.path_template}",
                check="frontend-api-contract",
                severity="info",
                confidence="medium",
                source="ci",
                scope=f"{route.method} {route.path_template}",
                summary=(
                    f"backend route {route.method} {route.path_template} has no direct "
                    "fetch/fetchJSON call found in frontend/src/js"
                ),
                evidence={},
            )
        )
    return findings
