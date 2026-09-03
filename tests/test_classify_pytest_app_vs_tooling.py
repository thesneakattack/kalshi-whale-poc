"""tools/classify_pytest_app_vs_tooling.py's AST-based app/tooling split -
the boundary .woodpecker/tests-pytest-app.yml and tests-pytest-tooling.yml
both depend on to decide what's safe to skip. Tested against synthetic
fixture files under tmp_path, never this repo's real tests/ directory, so
the classification logic is proven independent of what test files happen
to exist today."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.classify_pytest_app_vs_tooling import _imports_app_code  # noqa: E402


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content)
    return p


def test_top_level_services_import_is_app(tmp_path):
    p = _write(tmp_path, "test_x.py", "from services.risk_manager import RiskManager\n")
    assert _imports_app_code(p) is True


def test_top_level_services_submodule_dotted_import_is_app(tmp_path):
    p = _write(tmp_path, "test_x.py", "import services.kalshi.public\n")
    assert _imports_app_code(p) is True


def test_bare_import_main_is_app(tmp_path):
    p = _write(tmp_path, "test_x.py", "import main\n")
    assert _imports_app_code(p) is True


def test_from_main_import_is_app(tmp_path):
    p = _write(tmp_path, "test_x.py", "from main import app\n")
    assert _imports_app_code(p) is True


def test_function_local_lazy_import_is_still_app(tmp_path):
    # tests/test_historical_data_backfill.py's real shape (2026-09-03): a
    # top-level grep for "from services" finds nothing, but two functions
    # lazily `from services import capture_writer` - a naive top-of-file-only
    # check would misclassify this as safely skippable. Confirms the AST
    # walk catches nested-scope imports, not just module-level ones.
    p = _write(tmp_path, "test_x.py", (
        "def test_something():\n"
        "    from services import capture_writer\n"
        "    assert capture_writer is not None\n"
    ))
    assert _imports_app_code(p) is True


def test_tools_only_import_is_tooling(tmp_path):
    p = _write(tmp_path, "test_x.py", "from tools.quality_audit import unit_cost\n")
    assert _imports_app_code(p) is False


def test_stdlib_and_test_support_only_is_tooling(tmp_path):
    p = _write(tmp_path, "test_x.py", (
        "import json\n"
        "import subprocess\n"
        "from tests.support.synthetic_git_repo import git\n"
    ))
    assert _imports_app_code(p) is False


def test_a_module_merely_named_services_something_else_is_not_app(tmp_path):
    # services.* means the top-level package literally named "services" -
    # a hypothetical unrelated top-level package that happens to start with
    # the same 8 characters must not false-positive.
    p = _write(tmp_path, "test_x.py", "import services_unrelated_package\n")
    assert _imports_app_code(p) is False
