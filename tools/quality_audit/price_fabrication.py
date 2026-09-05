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

Issue #590 (this class in two more syntactic shapes, found by PR #588's own
adversarial review, since fixed here):

1. **Ternary/IfExp**: `price = bid if bid is not None else 0.5` (or the
   flipped ordering, `0.5 if bid is None else bid`) produces an `ast.IfExp`,
   not an `ast.BoolOp` - a distinct node type the original scanner never
   looked at. `_fabricated_ifexp` below handles both orderings and both
   `is None`/`is not None`/truthiness test shapes.
2. **Intermediate-variable indirection**: `bid = m.get(...); price = bid or
   0.5` (or the ternary equivalent) breaks `_dict_get_key`, whose only
   recognized shapes are a direct `.get(...)` call or subscript - a bare
   `ast.Name` operand was invisible to it. `_resolve_name_origin` below
   closes this for a NAME operand in either the `or`-chain or the ternary,
   by finding where that name is assigned.

That resolution is deliberately bounded, not general dataflow analysis:
`_single_assignment_value` looks only at the DIRECT statement list of the
enclosing function or module body (an assignment inside an `if`/`for`/
`while`/nested-`def` is invisible to it, same as `_dict_get_key` only
seeing a call/subscript written directly, never one three names away), and
only resolves a name assigned EXACTLY ONCE in that scope - a reassigned or
multiply-assigned name is treated as ambiguous, not as its first or last
assignment. Cross-function name reuse never resolves: a `bid` parameter in
one function is never confused with a `bid` local in another, since
resolution never looks outside the referencing name's own immediately
enclosing function/module body.

This scanner's existing bias for a genuinely ambiguous case (issue #577's
own investigation, and the non-string/dynamic-key exclusion below) is to
NOT flag rather than guess - a false negative on a name this scanner can't
confidently trace, over a false positive that erodes trust in a
confidence-high check. The reassigned-name and branch-scoped-assignment
cases above are accepted, documented gaps for exactly that reason, not
oversights - as is `_single_assignment_value` only ever matching a plain
`ast.Assign`: an augmented assignment (`bid += 1`), an annotated one
(`bid: float = m.get(...)`), a tuple/multiple target (`bid, ask = ...`), or
a chained one (`a = b = m.get(...)`) all correctly fail to resolve rather
than crash or, worse, resolve to the wrong target - a real-repo instance
of any of these would silently stay unflagged (2026-09-05 independent
review, confirmed by direct execution against all four shapes; no false
positive or crash in any case, only the same accepted false-negative bias
already documented above). Closing them requires real control-flow-sensitive dataflow
analysis, which issue #590 itself weighed against `services/config/
config_usage.py`'s own precedent (that scanner documents an analogous
intermediate-variable limitation as an accepted gap rather than building
reaching-definition tracking for it - `config_usage.py`'s docstring cites
the same "single unbroken chain" reasoning) and found disproportionate to
the residual exposure once this bounded, single-assignment version closes
the two shapes that actually round-trip through this codebase's own real
call sites (issue #590's own worked examples, and the true-negative
`services/whale_simulator.py:82` shape - a genuine intermediate-variable
ternary whose fallback is a function call, not a literal, so it was never
a violation and stays correctly unflagged either way).
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


# --- issue #590: bounded single-assignment name resolution -----------------
# See this module's own docstring for exactly what this does and does not
# cover. `_link_parents`/`_enclosing_scope_body` give each Name node a way to
# find its immediately enclosing function/module body without a general
# symbol table; `_single_assignment_value` is the actual bound.

_SCOPE_NODE_TYPES = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef)


def _link_parents(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child.parent = node  # type: ignore[attr-defined]


def _enclosing_scope_body(node: ast.AST) -> list[ast.stmt] | None:
    current = getattr(node, "parent", None)
    while current is not None:
        if isinstance(current, _SCOPE_NODE_TYPES):
            return current.body
        current = getattr(current, "parent", None)
    return None


def _single_assignment_value(name: str, scope_body: list[ast.stmt]) -> ast.expr | None:
    """The RHS of `name`'s assignment, if `scope_body` (a flat statement
    list - deliberately NOT recursed into any nested if/for/while/def, so an
    assignment inside one of those is invisible here) contains EXACTLY ONE
    `name = <expr>` at its top level. Two or more assignments to the same
    name, or none, both return None - ambiguous is treated the same as
    absent, never resolved to "the first" or "the last" one."""
    found: ast.expr | None = None
    count = 0
    for stmt in scope_body:
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and stmt.targets[0].id == name
        ):
            count += 1
            found = stmt.value
    return found if count == 1 else None


def _shadowed_by_lambda_param(node: ast.expr, name: str) -> bool:
    """True if `node` sits inside an `ast.Lambda` whose parameter list
    includes `name`, strictly between `node` and its enclosing function/
    module - i.e. `name` here refers to the lambda's own parameter, not
    whatever `_enclosing_scope_body` would otherwise resolve it against.
    Without this check, `lambda bid: bid or 0.5` could be wrongly resolved
    against an unrelated outer `bid = m.get(...)` that merely happens to
    share the parameter's name - a false positive this confidence-high
    check must not produce, even though this exact shape isn't in this
    codebase today (found in review, not from an observed instance -
    correctness of new detection logic doesn't get to wait for a real
    false positive to prove it)."""
    current = getattr(node, "parent", None)
    while current is not None and not isinstance(current, _SCOPE_NODE_TYPES):
        if isinstance(current, ast.Lambda):
            params = current.args
            all_names = [a.arg for a in (*params.posonlyargs, *params.args, *params.kwonlyargs)]
            if params.vararg is not None:
                all_names.append(params.vararg.arg)
            if params.kwarg is not None:
                all_names.append(params.kwarg.arg)
            if name in all_names:
                return True
        current = getattr(current, "parent", None)
    return False


def _resolve_name_origin(node: ast.expr) -> str | None:
    """If `node` is a bare Name whose value, traced through EXACTLY ONE
    assignment in its own immediately enclosing function/module body,
    resolves to a `.get(...)`/subscript call on a price-shaped key, return
    that key. None for anything else (not a Name, no scope found, no single
    assignment, the resolved value isn't itself a `.get()`/subscript, or
    `name` is shadowed by an enclosing lambda's own parameter of the same
    name - deliberately not recursive, so a chain of `a = b; b =
    m.get(...)` is one hop too far and stays unresolved, same conservative
    bias)."""
    if not isinstance(node, ast.Name):
        return None
    if _shadowed_by_lambda_param(node, node.id):
        return None
    scope_body = _enclosing_scope_body(node)
    if scope_body is None:
        return None
    value = _single_assignment_value(node.id, scope_body)
    if value is None:
        return None
    return _dict_get_key(value)


def _price_shaped_origin(node: ast.expr) -> str | None:
    """A price-shaped `.get(...)`/subscript key for `node`, whether it's
    written directly (`_dict_get_key`) or reached through exactly one
    intermediate-variable hop (`_resolve_name_origin`, issue #590)."""
    key = _dict_get_key(node)
    if key is None:
        key = _resolve_name_origin(node)
    return key


def _fabricated_fallback(node: ast.AST) -> ast.expr | None:
    """`node` is `X.get(<price-key>) or ... or <nonzero literal>` (any
    number of `or`-chained values before the literal, Python flattens
    `a or b or c` into one BoolOp - the exact shape
    `ticker_msg.get("yes_bid_dollars") or ticker_msg.get("price_dollars")
    or 0.5` had). An earlier value may also be a bare Name resolving to a
    price-shaped origin (issue #590, `_price_shaped_origin`). Returns the
    offending literal, or None."""
    if not isinstance(node, ast.BoolOp) or not isinstance(node.op, ast.Or):
        return None
    *earlier, last = node.values
    if not _is_nonzero_numeric_literal(last):
        return None
    for value in earlier:
        key = _price_shaped_origin(value)
        if key is not None and _is_price_shaped_key(key):
            return last
    return None


def _ifexp_none_or_truthiness_target(test: ast.expr) -> ast.expr | None:
    """If `test` is `X is None`, `X is not None`, or a bare truthiness check
    on `X` (just `X`, or `not X`), return `X`. None for anything else - a
    ternary keyed on an unrelated condition isn't this bug's shape even if
    one branch happens to be price-shaped (issue #590)."""
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and len(test.comparators) == 1:
        if isinstance(test.ops[0], (ast.Is, ast.IsNot)) and isinstance(test.comparators[0], ast.Constant) \
                and test.comparators[0].value is None:
            return test.left
        return None
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return test.operand
    return test


def _fabricated_ifexp(node: ast.AST) -> ast.expr | None:
    """`node` is `X if <None/truthiness check on X> else <nonzero literal>`
    or the flipped ordering, `<nonzero literal> if <None/truthiness check on
    X> else X`, where X is price-shaped directly or through one
    intermediate-variable hop (issue #590, C1). Returns the offending
    literal, or None."""
    if not isinstance(node, ast.IfExp):
        return None
    target = _ifexp_none_or_truthiness_target(node.test)
    if target is None:
        return None
    for live, literal in ((node.body, node.orelse), (node.orelse, node.body)):
        if not _is_nonzero_numeric_literal(literal):
            continue
        # The live branch must be the SAME expression the test is about -
        # comparing source text (ast.unparse) rather than node identity,
        # since `test` and `body`/`orelse` are necessarily different AST
        # nodes even when they read the exact same source expression.
        if ast.unparse(live) != ast.unparse(target):
            continue
        key = _price_shaped_origin(live)
        if key is not None and _is_price_shaped_key(key):
            return literal
    return None


def scan_price_fabrication(repo_root: Path) -> list[QualityFinding]:
    repo_root = Path(repo_root)
    findings: list[QualityFinding] = []
    for path in source.iter_python_files(repo_root):
        rel = source.relative_path(repo_root, path)
        tree = source.parse_python(path)
        _link_parents(tree)
        for node in ast.walk(tree):
            literal = _fabricated_fallback(node) or _fabricated_ifexp(node)
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
