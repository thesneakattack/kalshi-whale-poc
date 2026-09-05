"""Fabricated-price-fallback scanner (issue #577) - the CI half of the
"shared logic + CI guard" disposition behind
services.kalshi.contracts.trade.parse_fixed_point_dollars.

Six sites across the app built a price/bid/ask by substituting a
non-zero literal (`0.5` in every real case found) whenever the real value
was falsy - `X.get("yes_bid_dollars") or 0.5`. That is a FALSY check, not
a MISSING check: it fabricated the substitute both when the field was
genuinely absent AND when the real value was a genuine `0.0` (a real
zero bid, indistinguishable afterwards from the invented 0.5). One of the
six shipped into a persisted `market_history.snapshots` table (~1.4M
contaminated rows, issue #578) and another fed directly into an LLM
analyst's own reasoning prompt - unit tests would cover today's call
sites; this scanner closes the class the same way unit_cost.py (issue
#212) closed the no-side `1 - price` inversion class.

The rule is deliberately narrow and structural (AST, never text, so
prose in docstrings/comments is invisible to it): a finding is a
`BoolOp` with the `or` operator whose LAST value is a non-zero numeric
literal, where at least one EARLIER value is a `.get(...)` call or a
subscript (`X[...]`) keyed on a string literal that looks like a price
field (case-insensitive substring match on "bid", "ask", "price", or
"dollars"). Deliberately excludes an `or 0.0` / `or 0` fallback on the
same kind of field: a real Kalshi wire zero is legitimate and must be
preserved, not flagged as if fabricating it were the bug - only a
NON-ZERO substitute is ever the violation (Finding B of issue #577's own
investigation: `or 0.0` there is required, not a mistake). A
count/size/volume field's `or 0.0` (e.g. `volume_24h_fp`) is already
outside this scanner's field-name filter regardless, since none of
"bid"/"ask"/"price"/"dollars" appear in that key.

The one sanctioned spelling for "I have no real value" is
parse_fixed_point_dollars (None-preserving); nothing needs allow-listing
in that helper itself, since its own body never uses this `or` shape.

Severity error / confidence high: the match is provable from the syntax
tree and the fix is mechanical (call parse_fixed_point_dollars and either
omit the missing value or handle None explicitly, per issue #577). tests/
is never scanned (a test may spell the expression out to pin a fixture,
e.g. asserting the OLD buggy behavior stays fixed).
"""
from __future__ import annotations

import ast
from pathlib import Path

from services.quality.models import QualityFinding
from tools.quality_audit import source

CHECK = "fabricated-price-fallback"

# Case-insensitive substrings of a dict key that mark it as a price-shaped
# field this scanner cares about - the exact vocabulary issue #577's own
# six sites used (yes_bid_dollars, yes_ask_dollars, price_dollars,
# no_price_dollars, ...).
_PRICE_KEY_SUBSTRINGS = ("bid", "ask", "price", "dollars")


def _is_price_shaped_key(key: str) -> bool:
    lowered = key.lower()
    return any(s in lowered for s in _PRICE_KEY_SUBSTRINGS)


def _dict_get_key(node: ast.expr) -> str | None:
    """The string key of `X.get("key")` / `X.get("key", default)` or
    `X["key"]`, else None (not this shape, or a non-constant/non-string
    key such as a variable)."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            return node.args[0].value
        return None
    if isinstance(node, ast.Subscript):
        idx = node.slice
        if isinstance(idx, ast.Constant) and isinstance(idx.value, str):
            return idx.value
    return None


def _is_nonzero_numeric_literal(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Constant)
        and type(node.value) in (int, float)
        and node.value != 0
    )


def _fabricated_fallback(node: ast.AST) -> ast.expr | None:
    """`node` is `X.get(<price-key>) or ... or <nonzero literal>` (any
    number of `or`-chained values before the literal, Python flattens
    `a or b or c` into one BoolOp - the exact shape
    `ticker_msg.get("yes_bid_dollars") or ticker_msg.get("price_dollars")
    or 0.5` had). Returns the offending literal, or None."""
    if not isinstance(node, ast.BoolOp) or not isinstance(node.op, ast.Or):
        return None
    *earlier, last = node.values
    if not _is_nonzero_numeric_literal(last):
        return None
    for value in earlier:
        key = _dict_get_key(value)
        if key is not None and _is_price_shaped_key(key):
            return last
    return None


def scan_price_fabrication(repo_root: Path) -> list[QualityFinding]:
    repo_root = Path(repo_root)
    findings: list[QualityFinding] = []
    for path in source.iter_python_files(repo_root):
        rel = source.relative_path(repo_root, path)
        tree = source.parse_python(path)
        for node in ast.walk(tree):
            literal = _fabricated_fallback(node)
            if literal is None:
                continue
            findings.append(QualityFinding(
                finding_id=f"{CHECK}:{rel}:{node.lineno}",
                check=CHECK, severity="error", confidence="high", source="ci",
                scope=rel,
                summary=(
                    f"fabricated non-zero price fallback at {rel}:{node.lineno} "
                    f"(`or {ast.unparse(literal)}` on a price/bid/ask field) - "
                    "the same falsy-not-missing class issue #577 fixed"
                ),
                evidence={"path": rel, "line": node.lineno, "col": node.col_offset},
                remediation=(
                    "use the shared parse_fixed_point_dollars helper (services/kalshi/contracts/"
                    "trade.py) and handle the None case explicitly (omit the value, or an honest "
                    "'unknown') - never substitute a literal for a genuinely missing or falsy "
                    "price/bid/ask"
                ),
            ))
    return findings
