"""Kalshi integration-boundary CI ratchet — Phase A Task A15.

Turns the boundary the A4-A14 migration built into an enforced invariant:
new vendor leakage cannot silently re-enter on future pushes/PRs. Four
hard, high-confidence checks (deliberately narrow - no broad
`ticker`/`trade_id` string bans, per the plan):

1. kalshi_python_async imported anywhere outside services/kalshi/ —
   the boundary's transport module is the one place the SDK may enter
   (the legacy facades stopped importing it at A8/A11).
2. A raw Kalshi host string used outside the approved integration seam
   (services/kalshi/ plus the reviewed census allowlist: shared
   transport, docs-sync/canary tooling, the remaining facades).
   Docstrings are prose, not usage — a module/class/function docstring
   mentioning docs.kalshi.com is filtered out by provable AST context.
3. Legacy compatibility-facade imports beyond the reviewed ratchet
   baseline — the strangler migration may only shrink this surface.
   Falling BELOW baseline emits an info finding prompting the baseline
   down, so the ratchet keeps tightening instead of going stale.
4. A deprecated/high-risk direction-alias read (taker_side/
   taker_outcome_side/taker_book_side via .get("...") or ["..."]) outside
   the boundary and outside the one reviewed archival site
   (services/series_watcher.py's raw column copies - CLAUDE.md's
   accumulated-history rule and the permanent semantic rule both allow
   archival raw preservation).

Check "undocumented integration operation" is deliberately NOT here — 
tools/quality_audit/kalshi_contract_docs.py (A3) already owns it.

Detection logic is shared with tools/kalshi_census.py (the informational
inventory this ratchet enforces a subset of) rather than re-implemented —
investigation-to-guard rule option 3, shared logic for runtime/CI.
"""
from __future__ import annotations

import ast
from pathlib import Path

from services.quality.models import QualityFinding
from tools.kalshi_census import (
    _KALSHI_HOST_TOKEN,
    _scan_direct_host_usage,
    _scan_known_field_reads,
    _scan_legacy_wrapper,
    _scan_sdk_imports,
)
from tools.quality_audit import source

_BOUNDARY_PREFIX = "services/kalshi/"

# Raw host strings allowed outside services/kalshi/: the reviewed census
# seam (see tools/kalshi_census.py's _APPROVED_INTEGRATION_FILES rationale).
_HOST_ALLOWED_FILES = frozenset({
    "services/http_client.py",
    "tools/kalshi_public_canary.py",
    "tools/kalshi_docs_sync.py",
    "tools/kalshi_census.py",         # the shared detection token itself
    "tools/quality_audit/kalshi_boundary.py",
})

# C9: the migration ratchet became a final invariant. The legacy module
# paths were deleted at zero callers (C8); ANY import of them - with or
# without a reintroduced file - is a hard error, no baseline, no
# file-existence precondition.
BANNED_LEGACY_MODULES = frozenset({
    "services.kalshi_client",
    "services.kalshi_account_client",
    "services.kalshi_trade_ws",
})

_DEPRECATED_DIRECTION_FIELDS = frozenset({"taker_side", "taker_outcome_side", "taker_book_side"})

# The one reviewed archival site allowed to copy raw direction aliases
# into storage columns (pure preservation, no interpretation - A13/A14).
_ARCHIVAL_ALLOWED_FILES = frozenset({"services/series_watcher.py"})


def _docstring_linenos(tree: ast.Module) -> set[int]:
    """Line numbers spanned by module/class/function docstrings - prose,
    not usage, for the host check."""
    linenos: set[int] = set()
    nodes = [tree] + [n for n in ast.walk(tree) if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))]
    for node in nodes:
        body = getattr(node, "body", [])
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            doc = body[0]
            linenos.update(range(doc.lineno, (doc.end_lineno or doc.lineno) + 1))
    return linenos


def scan_kalshi_boundary(repo_root: Path) -> list[QualityFinding]:
    repo_root = Path(repo_root)
    findings: list[QualityFinding] = []

    # 1. SDK import outside services/kalshi/
    for site in _scan_sdk_imports(repo_root):
        if site["file"].startswith(_BOUNDARY_PREFIX):
            continue
        findings.append(QualityFinding(
            finding_id=f"kalshi-boundary-sdk-import:{site['file']}:{site['line']}",
            check="kalshi-boundary", severity="error", confidence="high", source="ci",
            scope=site["file"],
            summary=f"kalshi_python_async imported outside services/kalshi/ at {site['file']}:{site['line']}",
            evidence={"path": site["file"], "line": site["line"], "imported": site.get("imported")},
            remediation="route vendor SDK access through services/kalshi/ (transport.py builds clients; gateways own operations)",
        ))

    # 2. Raw Kalshi host string outside the approved seam (docstrings are prose)
    docstring_cache: dict[str, set[int]] = {}
    for site in _scan_direct_host_usage(repo_root):
        rel = site["file"]
        if rel.startswith(_BOUNDARY_PREFIX) or rel in _HOST_ALLOWED_FILES:
            continue
        if rel not in docstring_cache:
            docstring_cache[rel] = _docstring_linenos(source.parse_python(repo_root / rel))
        if site["line"] in docstring_cache[rel]:
            continue
        findings.append(QualityFinding(
            finding_id=f"kalshi-boundary-host:{rel}:{site['line']}",
            check="kalshi-boundary", severity="error", confidence="high", source="ci",
            scope=rel,
            summary=f"raw Kalshi host string used outside the integration boundary at {rel}:{site['line']}",
            evidence={"path": rel, "line": site["line"], "snippet": site.get("snippet")},
            remediation="use the services/kalshi/ gateways (or config-provided base URLs) instead of embedding vendor hosts",
        ))

    # 3. Legacy-facade imports: a final invariant since C9 - any import
    # of a deleted legacy module path is an error, file or no file.
    for site in _scan_legacy_wrapper(repo_root)["imports"]:
        if site["module"] not in BANNED_LEGACY_MODULES:
            continue
        findings.append(QualityFinding(
            finding_id=f"kalshi-boundary-legacy-import:{site['file']}:{site['line']}",
            check="kalshi-boundary", severity="error", confidence="high", source="ci",
            scope=site["file"],
            summary=(
                f"{site['module']} imported at {site['file']}:{site['line']} - the legacy "
                "compatibility facades were deleted at zero callers (C8) and must not return"
            ),
            evidence={"path": site["file"], "line": site["line"], "module": site["module"]},
            remediation="import the services/kalshi/ gateways (public/websocket/account_client) instead",
        ))

    # 4. Deprecated direction-alias read outside boundary + archival allowlist
    for field, sites in _scan_known_field_reads(repo_root).items():
        if field not in _DEPRECATED_DIRECTION_FIELDS:
            continue
        for entry in sites:
            rel, _, line = entry.rpartition(":")
            if rel.startswith(_BOUNDARY_PREFIX) or rel in _ARCHIVAL_ALLOWED_FILES:
                continue
            findings.append(QualityFinding(
                finding_id=f"kalshi-boundary-deprecated-read:{rel}:{line}",
                check="kalshi-boundary", severity="error", confidence="high", source="ci",
                scope=rel,
                summary=f"deprecated Kalshi direction alias {field!r} read outside the boundary at {rel}:{line}",
                evidence={"path": rel, "line": line, "field": field},
                remediation=(
                    "resolve direction through services/kalshi/contracts/trade.py "
                    "(resolve_taker_outcome_side / public_trade_from_ws) instead of reading vendor aliases"
                ),
            ))

    return findings
