"""Tests for services/kalshi/provenance.py — the runtime half of the
contract-documentation provenance interface (Kalshi Integration Phase A
Task A4, docs/superpowers/plans/2026-08-24-kalshi-integration-phase-a.md).

The static half (tools/quality_audit/kalshi_contract_docs.py, Task A3)
AST-scans services/kalshi/ modules in CI; this module is the importable
counterpart: collect every CONTRACT_DOCS declaration across the live
package, validate the aggregate (paths exist, no operation declared twice
across modules — the cross-module hazard a per-module dict literal can't
express), and render a human-readable inventory for diagnostics.

Validation/rendering are pure functions over ContractOperation values so
they're testable without importing synthetic modules; only
collect_operations touches the real package, and its test runs against the
live services.kalshi tree (empty-but-valid at A4, growing from A6 on).
"""
from __future__ import annotations

from pathlib import Path

from services.kalshi import provenance
from services.kalshi.provenance import ContractOperation

REPO_ROOT = Path(__file__).resolve().parent.parent


def _op(module: str, operation: str, docs: tuple[str, ...]) -> ContractOperation:
    return ContractOperation(module=module, operation=operation, docs=docs)


def test_validate_operations_with_valid_local_paths_reports_no_problems(tmp_path):
    (tmp_path / "docs" / "kalshi").mkdir(parents=True)
    (tmp_path / "docs" / "kalshi" / "get-markets.md").write_text("# Get Markets\n")

    ops = [_op("services.kalshi.public", "get_markets", ("docs/kalshi/get-markets.md",))]

    assert provenance.validate_operations(ops, repo_root=tmp_path) == []


def test_validate_operations_flags_nonexistent_doc_path(tmp_path):
    ops = [_op("services.kalshi.public", "get_markets", ("docs/kalshi/missing.md",))]

    problems = provenance.validate_operations(ops, repo_root=tmp_path)

    assert len(problems) == 1
    assert "get_markets" in problems[0]
    assert "docs/kalshi/missing.md" in problems[0]


def test_validate_operations_flags_duplicate_operation_across_modules(tmp_path):
    (tmp_path / "docs" / "kalshi").mkdir(parents=True)
    (tmp_path / "docs" / "kalshi" / "get-markets.md").write_text("# Get Markets\n")

    docs = ("docs/kalshi/get-markets.md",)
    ops = [
        _op("services.kalshi.public", "get_markets", docs),
        _op("services.kalshi.account", "get_markets", docs),
    ]

    problems = provenance.validate_operations(ops, repo_root=tmp_path)

    assert len(problems) == 1
    assert "duplicate" in problems[0]
    assert "services.kalshi.public" in problems[0]
    assert "services.kalshi.account" in problems[0]


def test_validate_operations_flags_empty_docs_tuple(tmp_path):
    ops = [_op("services.kalshi.public", "get_markets", ())]

    problems = provenance.validate_operations(ops, repo_root=tmp_path)

    assert len(problems) == 1
    assert "empty" in problems[0]


def test_render_inventory_groups_operations_by_module():
    ops = [
        _op("services.kalshi.account", "get_balance", ("docs/kalshi/get-balance.md",)),
        _op("services.kalshi.public", "get_market", ("docs/kalshi/get-market.md",)),
        _op(
            "services.kalshi.public",
            "get_markets",
            ("docs/kalshi/get-markets.md", "docs/kalshi/pagination.md"),
        ),
    ]

    text = provenance.render_inventory(ops)

    assert "services.kalshi.account:" in text
    assert "services.kalshi.public:" in text
    assert "get_balance: docs/kalshi/get-balance.md" in text
    assert "get_markets: docs/kalshi/get-markets.md, docs/kalshi/pagination.md" in text


def test_render_inventory_with_no_operations_says_so():
    text = provenance.render_inventory([])

    assert "no documented Kalshi contract operations" in text


def test_collect_operations_walks_the_real_package_and_validates_cleanly():
    """The live services.kalshi tree must always aggregate to a valid
    provenance state — every declared CONTRACT_DOCS path exists in this
    repo, no operation is declared by two modules. Empty at A4 (the
    skeleton declares no operations yet); grows from A6 on without this
    test needing to change."""
    ops = provenance.collect_operations()

    assert isinstance(ops, list)
    assert all(isinstance(op, ContractOperation) for op in ops)
    assert provenance.validate_operations(ops, repo_root=REPO_ROOT) == []
