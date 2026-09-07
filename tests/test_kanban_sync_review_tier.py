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


# Real changed-file lists, taken from the merged PRs themselves (spec §3.2 and
# §7). These are the fixture corpus: if the boundary ever reclassifies one of
# them, that is a decision someone has to make on purpose.
def test_tier_b_the_six_unreviewed_low_blast_radius_prs():
    """The six PRs from the research's unreviewed-30 list that the boundary
    genuinely excuses: a test tightening, a backup-overlap guard, a container
    image line, a gitignore, a comment fix, and a one-off backfill tool.

    Six, not the seven spec §3.2 lists: #498 (tests/test_index_feed_backfill.py)
    moved to Tier A once services/index_feed/ was added as a Lane 3 dependency
    (2026-09-07 adversarial review). #273 is Tier B on its *file list* and Tier
    A on its diff - it adds recovered `raw_trades` rows, which rule 3 catches -
    so this fixture exercises the file rules only."""
    corpus = {
        301: ["tests/test_e2e_terminal_static_and_api.py"],
        308: ["services/backup/backup.py", "services/backup/routes.py",
              "tests/test_backup.py", "tests/test_backup_routes.py"],
        415: ["Dockerfile", "docs/open-decisions.md"],
        445: [".gitignore"],
        623: ["services/diagnostics/routes.py"],
        273: ["static/project-manifest.json", "tests/test_historical_data_backfill.py",
              "tools/historical_data_backfill.py"],
    }
    for number, files in corpus.items():
        tier, reasons = review_tier.review_tier(files)
        assert tier == "B", f"PR #{number} should be Tier B, got A because {reasons}"


def test_pr_498_is_tier_a_through_the_lane_3_dependency_index_feed():
    """The one PR the Lane 3 import correction moved. Its only changed file is a
    test, and it is Tier A through the stem rule because services/index_feed/ is
    a Lane 3 dependency - settlement_edge_entry.py imports it."""
    tier, reasons = review_tier.review_tier(["tests/test_index_feed_backfill.py"])
    assert tier == "A"
    assert any("index_feed" in r for r in reasons)


def test_tier_a_via_main_py_and_its_test():
    tier, reasons = review_tier.review_tier(["main.py", "tests/test_main_scheduler_loops.py"])
    assert tier == "A"
    assert any("main.py" in r for r in reasons)


def test_tier_a_via_lane_1_signal_source():
    tier, _ = review_tier.review_tier([
        "services/series_evaluator.py", "services/trade_category.py",
        "tests/test_series_evaluator.py", "tests/test_trade_category.py",
    ])
    assert tier == "A"


def test_tier_a_via_the_money_ui():
    tier, _ = review_tier.review_tier([
        "frontend/src/js/screener-and-header.js", "frontend/src/js/shared-utils.js",
        "static/css/dashboard.css",
    ])
    assert tier == "A"


def test_a_cheatsheet_inside_a_tier_a_package_alone_is_tier_b():
    """Spec §3.1 rule 2's stated consequence: prose under a Tier A directory
    does not fire rule 1. In the 200-PR window this moves no PR - it is here so
    the behaviour is a decision rather than an accident."""
    tier, _ = review_tier.review_tier(["services/kalshi/CHEATSHEET.md"])
    assert tier == "B"


def test_the_rule_files_are_tier_a_whatever_their_suffix():
    for path in (
        "CLAUDE.md", ".claude/rules/branching-and-ci.md",
        ".claude/skills/checkpoint/SKILL.md",
        "docs/superpowers/plans/2026-09-01-x.md",
        "docs/archive/lane-4-analytics-advisory-research/specs/2026-09-01-x-design.md",
    ):
        tier, reasons = review_tier.review_tier([path])
        assert tier == "A", f"{path} should be Tier A"
        assert any("prose" in r for r in reasons)


def test_an_archived_lane_doc_outside_the_pipeline_dirs_is_tier_b():
    """Spec D9: a claim-asserting document outside research/specs/plans is Tier
    B unless escalated - it is misplaced first and under-reviewed second."""
    tier, _ = review_tier.review_tier([
        "docs/archive/lane-9-tooling-ci-process-governance/README.md"
    ])
    assert tier == "B"


def test_a_data_model_line_in_a_lane_4_file_is_tier_a():
    """Spec D12: the data model is Tier A wherever it changes. services/
    analytics/ is Lane 4 and on no path list."""
    diff = (
        "diff --git a/services/analytics/store.py b/services/analytics/store.py\n"
        "--- a/services/analytics/store.py\n"
        "+++ b/services/analytics/store.py\n"
        "@@ -1,3 +1,5 @@\n"
        "+CREATE TABLE IF NOT EXISTS whale_rollup (id INTEGER PRIMARY KEY)\n"
    )
    tier, reasons = review_tier.review_tier(["services/analytics/store.py"], diff_text=diff)
    assert tier == "A"
    assert any("data model" in r for r in reasons)


def test_a_data_model_string_in_an_unchanged_context_line_does_not_fire():
    """Only added or removed lines count: a matching line that merely sits in
    the diff's context is not a change to the data model."""
    diff = (
        "@@ -1,3 +1,4 @@\n"
        " CREATE TABLE IF NOT EXISTS whale_rollup (id INTEGER PRIMARY KEY)\n"
        "+LOG = logging.getLogger(__name__)\n"
    )
    tier, _ = review_tier.review_tier(["services/analytics/store.py"], diff_text=diff)
    assert tier == "B"


def test_the_hotpath_label_alone_is_tier_a():
    tier, reasons = review_tier.review_tier(
        ["services/quality/report.py"], pr_labels=[labels.CONCERN_HOTPATH]
    )
    assert tier == "A"
    assert any(labels.CONCERN_HOTPATH in r for r in reasons)


def test_escalation_makes_any_pr_tier_a_and_says_so():
    tier, reasons = review_tier.review_tier([".gitignore"], escalate=True)
    assert tier == "A"
    assert any("escalat" in r for r in reasons)


def test_no_rule_moves_a_pr_from_a_to_b():
    """Spec §3.1 rule 5: escalation is upward-only. There is no de-escalation
    parameter, and adding a Tier B file to a Tier A PR cannot lower it."""
    tier, _ = review_tier.review_tier(["services/risk_manager.py", ".gitignore"])
    assert tier == "A"


def test_a_test_of_a_tier_a_module_is_tier_a_but_the_tests_dir_is_not():
    """Spec D8. `tests/test_<stem>*.py` for a Tier A stem, matched as a prefix
    at an underscore boundary - not as a substring, which would make
    `test_maintenance.py` Tier A through the stem `main` (from main.py)."""
    assert review_tier.review_tier(["tests/test_strategy_engine_gate.py"])[0] == "A"
    assert review_tier.review_tier(["tests/test_risk_manager.py"])[0] == "A"
    assert review_tier.review_tier(["tests/test_maintenance.py"])[0] == "B"
    assert review_tier.review_tier(["tests/test_e2e_terminal_static_and_api.py"])[0] == "B"


def test_an_extensionless_file_under_a_tier_a_path_is_code():
    """scripts/woodpecker-status carries no suffix; the suffix list exists to
    exclude prose, and an extensionless executable is not prose. Measured
    before adopting: the only extensionless file anywhere in the 200-PR window
    is Dockerfile, which is under no Tier A path, so §3.2's counts stand."""
    assert review_tier.review_tier(["scripts/woodpecker-status"])[0] == "A"
    assert review_tier.review_tier(["Dockerfile"])[0] == "B"


def test_reasons_name_the_rule_and_the_path_that_fired_it():
    """The output is pasted into a PR comment as the record of the decision, so
    a reason has to be readable on its own."""
    _, reasons = review_tier.review_tier(["services/risk_manager.py"])
    assert reasons == ["path: services/risk_manager.py (under services/risk_manager.py)"]
