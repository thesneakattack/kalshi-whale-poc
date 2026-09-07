from pathlib import Path

from tools.kanban_sync import labels, review_tier

_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_lane3_direct_imports_finds_a_from_import(tmp_path):
    (tmp_path / "services").mkdir(parents=True)
    (tmp_path / "services" / "app_state.py").write_text("STATE = {}\n")
    (tmp_path / "services" / "strategy_engine.py").write_text(
        "from services.app_state import STATE\nimport os\n"
    )
    assert review_tier.lane3_direct_imports(tmp_path) == {"services/app_state.py"}


def test_lane3_direct_imports_finds_a_dotted_module_and_a_package(tmp_path):
    (tmp_path / "services" / "position").mkdir(parents=True)
    (tmp_path / "services" / "position" / "account_positions.py").write_text("")
    (tmp_path / "services" / "exits").mkdir(parents=True)
    (tmp_path / "services" / "exits" / "__init__.py").write_text("")
    (tmp_path / "services" / "strategy_engine.py").write_text(
        "import services.position.account_positions\nfrom services.exits import run\n"
    )
    assert review_tier.lane3_direct_imports(tmp_path) == {
        "services/position/account_positions.py", "services/exits/",
    }


def test_lane3_direct_imports_finds_the_bare_from_services_form(tmp_path):
    """The form Lane 3 actually uses, and the one a `from services\\.` regex
    cannot see: `from services import a, b, c` at services/strategy_engine.py:9,
    services/exits/exit_engine.py:17, services/settlement_resolver.py:38-39.
    Six real Lane 3 dependencies stayed invisible to this check until the
    2026-09-07 adversarial review found it."""
    (tmp_path / "services").mkdir(parents=True)
    (tmp_path / "services" / "fault_log.py").write_text("")
    (tmp_path / "services" / "market_lookup.py").write_text("")
    (tmp_path / "services" / "strategy_engine.py").write_text(
        "from services import fault_log, market_lookup\n"
    )
    assert review_tier.lane3_direct_imports(tmp_path) == {
        "services/fault_log.py", "services/market_lookup.py",
    }


def test_lane3_direct_imports_ignores_a_commented_out_import(tmp_path):
    """Parsing rather than pattern-matching also removes a whole class of false
    positive the regex had: a docstring line beginning `from services.x`."""
    (tmp_path / "services").mkdir(parents=True)
    (tmp_path / "services" / "app_state.py").write_text("")
    (tmp_path / "services" / "strategy_engine.py").write_text(
        '# from services.app_state import STATE\n'
        '"""from services.app_state import STATE"""\n'
    )
    assert review_tier.lane3_direct_imports(tmp_path) == set()


def test_every_lane3_direct_import_is_covered_by_review_tier_a_paths():
    """Spec §3.1: a module Lane 3 imports directly is Tier A, whatever lane it
    nominally belongs to. Run against the real repo, so a future import of a
    Tier B module from strategy/risk/execution code fails CI here rather than
    shipping as a Tier B PR that changes trading behaviour."""
    uncovered = sorted(
        target for target in review_tier.lane3_direct_imports(_REPO_ROOT)
        if not any(target == p or target.startswith(p) for p in labels.REVIEW_TIER_A_PATHS)
    )
    assert not uncovered, f"Lane 3 imports these, but they are not Tier A: {uncovered}"


def test_lane3_direct_imports_on_the_real_repo_is_not_vacuous():
    """A liveness check on the scan itself: if the parse or the LANES[3]
    resolution breaks, the coverage test above passes on an empty set and
    reports nothing. The ast scan finds 27 targets at a39d5f9; the floor is set
    below that so a real removal does not fail CI, but well above the 13 the
    superseded regex found."""
    targets = review_tier.lane3_direct_imports(_REPO_ROOT)
    assert "services/app_state.py" in targets
    assert "services/fault_log.py" in targets      # only visible via `from services import`
    assert len(targets) >= 25
