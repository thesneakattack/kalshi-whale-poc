"""Kalshi contract-documentation scanner for the Quality Control Plane's
static audit CLI (Kalshi Integration Phase A Task A3,
docs/superpowers/plans/2026-08-24-kalshi-integration-phase-a.md).

Enforces the design spec's "Documentation provenance in code" rule
(docs/superpowers/specs/2026-08-24-kalshi-integration-boundary-design.md):
every public adapter/normalizer operation under `services/kalshi/` must be
covered by a code-adjacent, module-level `CONTRACT_DOCS` dict mapping the
operation name to a tuple of `docs/kalshi/*` paths that actually exist on
disk - so "read the docs before touching this" is a deterministic CI
guard, not just a rule Claude is trusted to remember.

`services/kalshi/` does not exist yet as of A3 (it lands in A4) - scanning
it before then simply finds zero files and reports nothing, which is the
correct behavior for a forward-looking ratchet: it starts guarding the
boundary the moment the boundary starts being created, not before.

Three finding shapes:
- missing mapping: a public function/method has no CONTRACT_DOCS entry
  (error/high - the operation exists but nothing says which doc backs it);
- nonexistent local doc: a CONTRACT_DOCS entry points at a path that isn't
  on disk (error/high - the mapping is actively wrong, not just absent);
- stale mapping: a CONTRACT_DOCS key doesn't match any public operation
  AST-visible in the module (warning/medium, not error - a static scan
  can't rule out every legitimate reason a key doesn't literally match a
  def name, e.g. a re-exported alias, so this is reported rather than
  gated).

A module marked `# quality-audit: kalshi-infrastructure` (e.g.
services/kalshi/provenance.py, the contract-metadata plumbing itself) is
exempt from the missing-mapping requirement only - see
_INFRASTRUCTURE_MARKER below.
"""
from __future__ import annotations

import ast
from pathlib import Path

from services.quality.models import QualityFinding
from tools.quality_audit import source

_KALSHI_PACKAGE_RELATIVE = Path("services") / "kalshi"

# A module carrying this marker anywhere in its source is integration
# *infrastructure* (provenance/validation helpers), not a vendor adapter -
# its public functions have no Kalshi doc to map, so the missing-mapping
# requirement is waived. Same explicit, greppable, diff-reviewable opt-out
# pattern as routers.py's `# quality-audit: standalone-router`. The
# file-existence and stale-key checks still apply to any CONTRACT_DOCS an
# infrastructure module does declare.
_INFRASTRUCTURE_MARKER = "# quality-audit: kalshi-infrastructure"

# Lifecycle methods aren't wire operations - close() releases the SDK
# client's aiohttp session and has no Kalshi doc page to map. Kept
# deliberately tiny: anything that talks to the API still needs a mapping.
_EXEMPT_OPERATION_NAMES = frozenset({"close"})


def _iter_kalshi_modules(repo_root: Path) -> list[Path]:
    package_root = Path(repo_root) / _KALSHI_PACKAGE_RELATIVE
    if not package_root.is_dir():
        return []
    return [
        path
        for path in source.iter_python_files(repo_root, include_tests=False)
        if package_root in path.parents or path.parent == package_root
    ]


def _is_public(name: str) -> bool:
    return not name.startswith("_")


def _public_operations(tree: ast.Module) -> set[str]:
    operations: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_public(node.name):
            operations.add(node.name)
        elif isinstance(node, ast.ClassDef):
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_public(member.name):
                    operations.add(member.name)
    return operations


def _string_elements(node: ast.expr) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.Tuple, ast.List)):
        return [elt.value for elt in node.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)]
    return []


def _contract_docs(tree: ast.Module) -> dict[str, list[str]] | None:
    for node in tree.body:
        # Both `CONTRACT_DOCS = {...}` and the annotated form
        # `CONTRACT_DOCS: dict[str, ContractDocs] = {...}` (an AnnAssign,
        # which real boundary modules use) declare the mapping - missing the
        # latter made the scanner flag transport.py's documented operations
        # as undocumented, caught by its first real consumer at A5.
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        else:
            continue
        if not any(isinstance(target, ast.Name) and target.id == "CONTRACT_DOCS" for target in targets):
            continue
        if not isinstance(value, ast.Dict):
            continue
        mapping: dict[str, list[str]] = {}
        for key_node, value_node in zip(value.keys, value.values):
            if not (isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)):
                continue
            mapping[key_node.value] = _string_elements(value_node)
        return mapping
    return None


def scan_kalshi_contract_docs(repo_root: Path) -> list[QualityFinding]:
    repo_root = Path(repo_root)
    findings: list[QualityFinding] = []

    for path in _iter_kalshi_modules(repo_root):
        module_text = source.read_text(path)
        tree = source.parse_python(path)
        is_infrastructure = _INFRASTRUCTURE_MARKER in module_text
        operations = _public_operations(tree)
        contract_docs = _contract_docs(tree) or {}
        module = source.module_dotted_path(repo_root, path)
        rel_path = source.relative_path(repo_root, path)

        undocumented = (
            ()
            if is_infrastructure
            else sorted(operations - contract_docs.keys() - _EXEMPT_OPERATION_NAMES)
        )
        for operation in undocumented:
            findings.append(
                QualityFinding(
                    finding_id=f"kalshi-contract-docs-missing:{module}:{operation}",
                    check="kalshi-contract-docs",
                    severity="error",
                    confidence="high",
                    source="ci",
                    scope=f"{module}.{operation}",
                    summary=f"{operation} has no CONTRACT_DOCS entry in {rel_path}",
                    evidence={"path": rel_path},
                    remediation=(
                        f"add a \"{operation}\": (...) entry to {module}'s CONTRACT_DOCS "
                        "pointing at the exact docs/kalshi/*.md source(s) this operation implements"
                    ),
                )
            )

        for key in sorted(contract_docs.keys() - operations):
            findings.append(
                QualityFinding(
                    finding_id=f"kalshi-contract-docs-stale:{module}:{key}",
                    check="kalshi-contract-docs",
                    severity="warning",
                    confidence="medium",
                    source="ci",
                    scope=f"{module}.{key}",
                    summary=f"CONTRACT_DOCS key \"{key}\" in {rel_path} has no matching public operation",
                    evidence={"path": rel_path},
                    remediation=f"remove the stale \"{key}\" entry, or rename it to match the current operation",
                )
            )

        for key in sorted(contract_docs.keys()):
            for doc_path in contract_docs[key]:
                if (Path(repo_root) / doc_path).exists():
                    continue
                findings.append(
                    QualityFinding(
                        finding_id=f"kalshi-contract-docs-missing-file:{module}:{key}:{doc_path}",
                        check="kalshi-contract-docs",
                        severity="error",
                        confidence="high",
                        source="ci",
                        scope=f"{module}.{key}",
                        summary=f"CONTRACT_DOCS[\"{key}\"] in {rel_path} points at nonexistent {doc_path}",
                        evidence={"path": rel_path, "doc_path": doc_path},
                        remediation=f"fix the path or mirror the missing doc into docs/kalshi/ first",
                    )
                )

    return findings
