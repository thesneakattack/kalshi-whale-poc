# quality-audit: kalshi-infrastructure
"""Contract-documentation provenance interface for the Kalshi integration
boundary (Phase A Task A4, design spec "Documentation provenance in code").

Adapter/normalizer modules under services/kalshi/ declare, per public
operation, exactly which mirrored official docs they implement:

    CONTRACT_DOCS: dict[str, ContractDocs] = {
        "get_markets": (
            "docs/kalshi/get-markets.md",
            "docs/kalshi/pagination.md",
        ),
    }

This module is the importable aggregate over those declarations - the
runtime/test-time counterpart to tools/quality_audit/kalshi_contract_docs.py's
per-module static CI scan. It adds the one check a per-module AST scan
can't do: the same operation name declared by two different modules (a
real hazard once public/account/orders/contracts modules coexist). It also
renders a human-readable inventory for diagnostics.

Deliberately lightweight and static: plain tuples/dataclasses, no runtime
registration, no import-time side effects beyond importing the (small)
package modules when collect_operations() is explicitly called.
"""
from __future__ import annotations

import pkgutil
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path

ContractDocs = tuple[str, ...]

_REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ContractOperation:
    """One documented public operation: which module declares it, its
    CONTRACT_DOCS key, and the repo-relative doc paths backing it."""

    module: str
    operation: str
    docs: ContractDocs


def collect_operations(package_name: str = "services.kalshi") -> list[ContractOperation]:
    """Every CONTRACT_DOCS declaration across the package, sorted
    (module, operation) for deterministic output."""
    package = import_module(package_name)
    modules = [package]
    for info in pkgutil.walk_packages(package.__path__, prefix=package.__name__ + "."):
        modules.append(import_module(info.name))

    operations: list[ContractOperation] = []
    for module in modules:
        mapping = getattr(module, "CONTRACT_DOCS", None)
        if not isinstance(mapping, dict):
            continue
        for operation, docs in mapping.items():
            operations.append(
                ContractOperation(module=module.__name__, operation=str(operation), docs=tuple(docs))
            )
    return sorted(operations, key=lambda op: (op.module, op.operation))


def validate_operations(
    operations: list[ContractOperation], repo_root: Path | None = None
) -> list[str]:
    """Problems with the aggregate declaration set, as human-readable
    strings; empty list means valid. Checks: no operation declared by two
    modules, no empty docs tuple, every doc path exists under repo_root."""
    root = Path(repo_root) if repo_root is not None else _REPO_ROOT
    problems: list[str] = []
    declared_in: dict[str, str] = {}
    for op in operations:
        if op.operation in declared_in:
            problems.append(
                f'duplicate operation "{op.operation}" declared in both '
                f"{declared_in[op.operation]} and {op.module}"
            )
        else:
            declared_in[op.operation] = op.module
        if not op.docs:
            problems.append(f"{op.module}.{op.operation} has an empty CONTRACT_DOCS entry")
        for doc in op.docs:
            if not (root / doc).exists():
                problems.append(f"{op.module}.{op.operation} references nonexistent {doc}")
    return problems


def render_inventory(operations: list[ContractOperation]) -> str:
    """Human-readable operation->docs inventory, grouped by module."""
    if not operations:
        return "no documented Kalshi contract operations declared yet\n"
    lines: list[str] = []
    current_module: str | None = None
    for op in operations:
        if op.module != current_module:
            lines.append(f"{op.module}:")
            current_module = op.module
        lines.append(f"  {op.operation}: {', '.join(op.docs)}")
    return "\n".join(lines) + "\n"
