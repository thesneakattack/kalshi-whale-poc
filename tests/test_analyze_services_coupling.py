"""tools/analyze_services_coupling.py's import-graph + connected-components
logic, tested against synthetic module trees under tmp_path - not this
repo's real services/ tree, so the graph-building logic is proven
independent of what the real coupling happens to look like today. This is
also a regression test for a real bug caught before its own conclusion was
trusted (2026-09-03): `from services import x` (module="services",
name="x") was only recording the bare "services" package, silently
dropping the real target and undercounting coupling."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.analyze_services_coupling import (  # noqa: E402
    _direct_app_imports,
    connected_components,
)


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content)
    return p


def test_from_package_import_submodule_resolves_to_the_submodule(tmp_path):
    # The exact bug: `from services import candidate_log` must be
    # discoverable as depending on services.candidate_log, not silently
    # dropped as an edge to the bare "services" package.
    p = _write(tmp_path, "main.py", "from services import candidate_log\n")
    found = _direct_app_imports(p)
    assert "services.candidate_log" in found


def test_from_submodule_import_name_resolves_to_the_submodule_not_the_name(tmp_path):
    p = _write(tmp_path, "x.py", "from services.risk_manager import RiskManager\n")
    found = _direct_app_imports(p)
    # Both the bare submodule and the (bogus, non-module) name.attr form are
    # emitted as raw candidates - build_graph's resolution step is what
    # picks the real one; this test only checks _direct_app_imports itself
    # doesn't silently drop the real candidate.
    assert "services.risk_manager" in found


def test_two_modules_joined_only_via_a_third_are_one_component():
    graph = {"a": {"b"}, "b": {"a", "c"}, "c": {"b"}, "d": set()}
    components = connected_components(graph)
    sizes = sorted(len(c) for c in components)
    assert sizes == [1, 3]


def test_fully_disjoint_modules_are_separate_components():
    graph = {"a": set(), "b": set(), "c": set()}
    components = connected_components(graph)
    assert sorted(len(c) for c in components) == [1, 1, 1]
