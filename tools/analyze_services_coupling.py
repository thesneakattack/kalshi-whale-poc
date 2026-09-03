"""One-off analysis (not a permanent tool, no test required): builds a real
import graph among every services/**/*.py module plus main.py, using the
same AST-based method as tools/classify_pytest_app_vs_tooling.py, then
computes connected components (undirected - either import direction is a
real coupling for regression-risk purposes: if A imports B, a change to B
can break A's behavior). Answers, empirically rather than by assumption,
whether "app code" is genuinely one irreducible blob (as this repo's
existing tests-pytest.yml rejection of file-level test selection has
always asserted) or whether it actually decomposes into real, separately
testable clusters.

Run as: python -m tools.analyze_services_coupling
"""
from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _module_name(path: Path) -> str:
    rel = path.relative_to(REPO_ROOT).with_suffix("")
    if rel.name == "__init__":
        rel = rel.parent
    return ".".join(rel.parts)


def _direct_app_imports(path: Path) -> set[str]:
    """Bug found and fixed 2026-09-03, before this script's own conclusion
    was trusted: for `from services import candidate_log` (module="services",
    name="candidate_log" - the common style main.py uses for ~30 of its own
    imports, confirmed by grep), the original version only recorded
    node.module ("services" - a bare package, not a real scanned node) and
    silently dropped the actual submodule target. `from X import Y` adds
    BOTH X and X.Y as raw candidates; build_graph's own longest-known-prefix
    resolution (below) picks whichever one is a real module in this scan's
    node set - X.Y for `from services import candidate_log` (resolves to
    services.candidate_log, a real file), X for `from services.risk_manager
    import RiskManager` (X.Y="services.risk_manager.RiskManager" isn't a
    real module and correctly shrinks back down to X="services.risk_manager",
    which is)."""
    tree = ast.parse(path.read_text(), filename=str(path))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "main" or alias.name.split(".")[0] == "services":
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and (node.module == "main" or node.module.split(".")[0] == "services"):
                found.add(node.module)
                for alias in node.names:
                    found.add(f"{node.module}.{alias.name}")
    return found


def build_graph() -> dict[str, set[str]]:
    modules = sorted(REPO_ROOT.glob("services/**/*.py")) + [REPO_ROOT / "main.py"]
    modules = [m for m in modules if "__pycache__" not in str(m)]
    names = {_module_name(m) for m in modules}
    graph: dict[str, set[str]] = defaultdict(set)
    for m in modules:
        src = _module_name(m)
        for target in _direct_app_imports(m):
            # Resolve a dotted import like services.kalshi.public to the
            # longest known module prefix actually in our node set (handles
            # `import services.kalshi.public` pointing at a file whose own
            # module name might be services.kalshi.public or a package
            # services.kalshi with public re-exported - either way, walk up
            # to the first prefix we actually have as a node).
            candidate = target
            while candidate and candidate not in names:
                candidate = ".".join(candidate.split(".")[:-1])
            if candidate and candidate != src:
                graph[src].add(candidate)
                graph[candidate].add(src)  # undirected for coupling purposes
            elif not candidate:
                graph.setdefault(src, set())
    for n in names:
        graph.setdefault(n, set())
    return graph


def connected_components(graph: dict[str, set[str]]) -> list[set[str]]:
    seen: set[str] = set()
    components: list[set[str]] = []
    for node in graph:
        if node in seen:
            continue
        stack = [node]
        comp: set[str] = set()
        while stack:
            n = stack.pop()
            if n in comp:
                continue
            comp.add(n)
            stack.extend(graph.get(n, ()) - comp)
        seen |= comp
        components.append(comp)
    return components


def main() -> None:
    graph = build_graph()
    components = sorted(connected_components(graph), key=len, reverse=True)
    print(f"{len(graph)} app modules (services/**/*.py + main.py), {len(components)} connected component(s)")
    for i, comp in enumerate(components):
        print(f"\n--- component {i} ({len(comp)} modules) ---")
        for n in sorted(comp)[:15]:
            print(" ", n)
        if len(comp) > 15:
            print(f"  ... and {len(comp) - 15} more")


if __name__ == "__main__":
    main()
