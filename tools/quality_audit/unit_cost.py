"""Inline side-adjusted unit-cost scanner (issue #212) - the CI half of the
"shared logic + CI guard" disposition behind services/kalshi_fees.unit_cost.

The side-aware per-contract cost of a Kalshi contract - the yes price for
a "yes" position, (1 - yes_price) for a "no" one - was re-derived inline
26 times across 14 backend modules, and three modules had each written the
same private helper. CLAUDE.md names the class twice: the no-side
`1 - price` inversion is one of the two shipped bugs under "A displayed
value must match its label". Unit tests would cover today's call sites;
this scanner closes the class, so the 27th copy fails the push that adds
it instead of waiting for the next mislabelled dollar figure.

The rule is deliberately narrow and structural (AST, never text, so prose
in docstrings and comments is invisible to it): a finding is a
`1 - <expr>` / `1.0 - <expr>` subtraction that sits inside a branch of a
conditional keyed on a side string - an `x if side == "yes" else y`
expression or an `if side == "yes": ... else: ...` statement, in either
constant order, with `==`/`!=`/`is`/`is not`, through any wrapper such as
`str(side).lower()`. That is exactly the shape every one of the 26 sites
had, and nothing else in the tree has it: a complement keyed on a number
(`prob if prob > 0.5 else 1 - prob`), a plain `1 - math.exp(...)`, the
migrated `1 - kalshi_fees.unit_cost(side, price)`, and a side-keyed
constant pick (`1.0 if result == "yes" else 0.0`) are all outside it. The
one place allowed to spell the complement is the helper itself, matched
by file AND function name so a same-named function elsewhere is still a
copy. A genuine non-price complement that lands inside a side branch is
hoisted out of the branch, not allow-listed here.

Severity error / confidence high: the match is provable from the syntax
tree and the fix is mechanical (call kalshi_fees.unit_cost). tests/ is
never scanned (a test may spell the expression out to pin the helper).
"""
from __future__ import annotations

import ast
from pathlib import Path

from services.quality.models import QualityFinding
from tools.quality_audit import source

CHECK = "unit-cost-inline"

# The single sanctioned spelling: this function, in this file. Both must
# match - a `def unit_cost` in any other module is a fourth private copy.
HELPER_FILE = "services/kalshi_fees.py"
HELPER_NAME = "unit_cost"

_SIDE_STRINGS = frozenset({"yes", "no"})
_SIDE_OPS = (ast.Eq, ast.NotEq, ast.Is, ast.IsNot)


def _is_side_keyed(test: ast.expr) -> bool:
    """True when `test` compares something against the literal "yes" or
    "no" with an equality-style operator - the shape of every side pick.
    Walks the test so `not side == "yes"`, `a and side == "no"` and
    `str(side).lower() == "yes"` all count; `side in ("yes", "no")` does
    not (a membership check picks nothing)."""
    for node in ast.walk(test):
        if not isinstance(node, ast.Compare):
            continue
        if not all(isinstance(op, _SIDE_OPS) for op in node.ops):
            continue
        for operand in (node.left, *node.comparators):
            if isinstance(operand, ast.Constant) and operand.value in _SIDE_STRINGS:
                return True
    return False


def _is_one_minus(node: ast.AST) -> bool:
    """`1 - <expr>` or `1.0 - <expr>` (never `True - x`)."""
    return (
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Sub)
        and isinstance(node.left, ast.Constant)
        and type(node.left.value) in (int, float)
        and node.left.value == 1
    )


def _branches(node: ast.AST) -> list[ast.AST] | None:
    """The branch bodies of a side-keyed conditional, or None when `node`
    is not one."""
    if isinstance(node, ast.IfExp) and _is_side_keyed(node.test):
        return [node.body, node.orelse]
    if isinstance(node, ast.If) and _is_side_keyed(node.test):
        return [*node.body, *node.orelse]
    return None


def _helper_span(tree: ast.Module) -> tuple[int, int] | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == HELPER_NAME:
            return node.lineno, node.end_lineno or node.lineno
    return None


def _inline_complements(tree: ast.Module) -> list[ast.BinOp]:
    seen: set[tuple[int, int]] = set()
    hits: list[ast.BinOp] = []
    for node in ast.walk(tree):
        branches = _branches(node)
        if branches is None:
            continue
        for branch in branches:
            for inner in ast.walk(branch):
                if _is_one_minus(inner) and (inner.lineno, inner.col_offset) not in seen:
                    seen.add((inner.lineno, inner.col_offset))
                    hits.append(inner)
    return sorted(hits, key=lambda n: (n.lineno, n.col_offset))


def scan_unit_cost_derivations(repo_root: Path) -> list[QualityFinding]:
    repo_root = Path(repo_root)
    findings: list[QualityFinding] = []
    for path in source.iter_python_files(repo_root):
        rel = source.relative_path(repo_root, path)
        tree = source.parse_python(path)
        exempt = _helper_span(tree) if rel == HELPER_FILE else None
        for node in _inline_complements(tree):
            if exempt is not None and exempt[0] <= node.lineno <= exempt[1]:
                continue
            findings.append(QualityFinding(
                finding_id=f"{CHECK}:{rel}:{node.lineno}",
                check=CHECK, severity="error", confidence="high", source="ci",
                scope=rel,
                summary=(
                    f"inline side-adjusted `1 - ...` unit-cost derivation at {rel}:{node.lineno} "
                    "- the no-side inversion class CLAUDE.md names twice"
                ),
                evidence={"path": rel, "line": node.lineno, "col": node.col_offset},
                remediation=(
                    "call services.kalshi_fees.unit_cost(side, yes_price) - the one shared, tested "
                    "definition (issue #212); a non-price complement that genuinely belongs here is "
                    "hoisted out of the side-keyed branch, never allow-listed"
                ),
            ))
    return findings
