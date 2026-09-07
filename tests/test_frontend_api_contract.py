"""Tests for the frontend-to-backend API contract scanner
(docs/archive/lane-6-observability-quality-safety/plans/2026-08-24-quality-control-plane.md, Task 7, moved there 2026-09-06, planning-lanes migration). Uses
small synthetic fixture repos (a couple of Python route files, a couple of
JS files under frontend/src/js/) rather than depending on real repo content
triggering (or not triggering) a mismatch.
"""
from __future__ import annotations

from pathlib import Path

from tools.quality_audit.frontend_contract import (
    RouteSpec,
    extract_backend_routes,
    extract_frontend_calls,
    scan_frontend_contract,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


# --- extract_backend_routes --------------------------------------------------


def test_extracts_router_and_app_decorators(tmp_path):
    _write(
        tmp_path / "services" / "foo" / "routes.py",
        'from fastapi import APIRouter\n\nrouter = APIRouter()\n\n\n@router.get("/api/foo/{ticker}")\nasync def get_foo(ticker: str):\n    return {}\n',
    )
    _write(tmp_path / "main.py", '@app.post("/api/bar")\nasync def post_bar():\n    return {}\n')

    routes = extract_backend_routes(tmp_path)

    assert RouteSpec(method="GET", path_template="/api/foo/*") in routes
    assert RouteSpec(method="POST", path_template="/api/bar") in routes


def test_non_route_decorator_is_ignored(tmp_path):
    _write(tmp_path / "services" / "foo.py", '@some_other_decorator("/api/foo")\ndef f():\n    return {}\n')

    assert extract_backend_routes(tmp_path) == set()


# --- extract_frontend_calls --------------------------------------------------


def test_extracts_plain_string_fetchjson_call(tmp_path):
    _write(tmp_path / "frontend" / "src" / "js" / "app.js", "async function f() {\n  await fetchJSON('/api/config');\n}\n")

    calls = extract_frontend_calls(tmp_path)

    assert len(calls) == 1
    assert calls[0].method == "GET"
    assert calls[0].path_template == "/api/config"


def test_extracts_bare_fetch_call(tmp_path):
    _write(tmp_path / "frontend" / "src" / "js" / "app.js", "async function f() {\n  const res = await fetch('/api/state');\n}\n")

    calls = extract_frontend_calls(tmp_path)

    assert len(calls) == 1
    assert calls[0].path_template == "/api/state"


def test_extracts_template_literal_with_dynamic_segment(tmp_path):
    _write(
        tmp_path / "frontend" / "src" / "js" / "app.js",
        "async function f(ticker) {\n  await fetchJSON(`/api/markets/${encodeURIComponent(ticker)}/detail`);\n}\n",
    )

    calls = extract_frontend_calls(tmp_path)

    assert len(calls) == 1
    assert calls[0].path_template == "/api/markets/*/detail"


def test_query_string_is_stripped(tmp_path):
    _write(
        tmp_path / "frontend" / "src" / "js" / "app.js",
        "async function f() {\n  await fetchJSON(`/api/account/orders?${params}`);\n}\n",
    )

    calls = extract_frontend_calls(tmp_path)

    assert calls[0].path_template == "/api/account/orders"


def test_method_detected_from_options_object(tmp_path):
    _write(
        tmp_path / "frontend" / "src" / "js" / "app.js",
        "async function f() {\n  await fetchJSON('/api/config', {\n    method: 'POST',\n    body: '{}',\n  });\n}\n",
    )

    calls = extract_frontend_calls(tmp_path)

    assert calls[0].method == "POST"


def test_method_of_a_later_unrelated_call_does_not_leak_into_this_one(tmp_path):
    """Real bug found auditing this scanner against the live repo: a bare
    `fetchJSON('/some/url')` with no options object, immediately followed a
    few lines later by an unrelated `fetchJSON(..., {method: 'POST'})`, was
    misdetected as POST too - a fixed-size lookahead window for method
    detection bled past this call's own closing paren. Real-repo instance:
    frontend/src/js/history-core.js's /api/suggestions/declined (bare GET)
    followed by /api/suggestions/undecline (real POST)."""
    _write(
        tmp_path / "frontend" / "src" / "js" / "app.js",
        "async function a() {\n"
        "  const resp = await fetchJSON('/api/first');\n"
        "  const rows = resp.items || [];\n"
        "}\n\n"
        "async function b(id) {\n"
        "  await fetchJSON('/api/second', {\n"
        "    method: 'POST', body: JSON.stringify({id}),\n"
        "  });\n"
        "}\n",
    )

    calls = extract_frontend_calls(tmp_path)
    by_path = {c.path_template: c.method for c in calls}

    assert by_path["/api/first"] == "GET"
    assert by_path["/api/second"] == "POST"


def test_opaque_url_expression_produces_none_path_template(tmp_path):
    _write(
        tmp_path / "frontend" / "src" / "js" / "app.js",
        "async function f(url, opts) {\n  const res = await fetch(url, opts);\n}\n",
    )

    calls = extract_frontend_calls(tmp_path)

    assert len(calls) == 1
    assert calls[0].path_template is None


# --- scan_frontend_contract ---------------------------------------------------


def test_matching_frontend_call_produces_no_finding(tmp_path):
    _write(
        tmp_path / "services" / "foo" / "routes.py",
        'from fastapi import APIRouter\n\nrouter = APIRouter()\n\n\n@router.get("/api/foo")\nasync def get_foo():\n    return {}\n',
    )
    _write(tmp_path / "frontend" / "src" / "js" / "app.js", "await fetchJSON('/api/foo');\n")

    findings = scan_frontend_contract(tmp_path)

    assert [f for f in findings if f.check == "frontend-api-contract" and f.severity == "error"] == []


def test_frontend_call_with_no_matching_backend_route_fails_high_confidence(tmp_path):
    _write(tmp_path / "frontend" / "src" / "js" / "app.js", "await fetchJSON('/api/does-not-exist');\n")

    findings = scan_frontend_contract(tmp_path)

    errors = [f for f in findings if f.severity == "error"]
    assert len(errors) == 1
    assert errors[0].finding_id == "frontend-route-missing:GET:/api/does-not-exist"
    assert errors[0].confidence == "high"


def test_backend_only_route_is_informational_not_a_failure(tmp_path):
    _write(
        tmp_path / "services" / "foo" / "routes.py",
        'from fastapi import APIRouter\n\nrouter = APIRouter()\n\n\n@router.get("/api/only-backend")\nasync def get_foo():\n    return {}\n',
    )

    findings = scan_frontend_contract(tmp_path)

    assert len(findings) == 1
    assert findings[0].finding_id == "backend-route-unused:GET:/api/only-backend"
    assert findings[0].severity == "info"


def test_opaque_frontend_call_produces_low_confidence_info_finding(tmp_path):
    _write(tmp_path / "frontend" / "src" / "js" / "app.js", "await fetch(dynamicUrl);\n")

    findings = scan_frontend_contract(tmp_path)

    assert len(findings) == 1
    assert findings[0].check == "frontend-api-contract"
    assert findings[0].severity == "info"
    assert findings[0].confidence == "low"
    assert findings[0].finding_id.startswith("frontend-route-unknown:")


def test_multiple_call_sites_for_same_missing_route_produce_one_grouped_finding(tmp_path):
    _write(
        tmp_path / "frontend" / "src" / "js" / "a.js",
        "await fetchJSON('/api/missing');\n",
    )
    _write(
        tmp_path / "frontend" / "src" / "js" / "b.js",
        "await fetchJSON('/api/missing');\n",
    )

    findings = scan_frontend_contract(tmp_path)

    errors = [f for f in findings if f.severity == "error"]
    assert len(errors) == 1
    assert len(errors[0].evidence["call_sites"]) == 2
