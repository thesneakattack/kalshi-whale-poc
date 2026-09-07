from pathlib import Path

import pytest

from tools.kanban_sync import labels, review_tier

_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_lane3_dependencies_finds_a_from_import(tmp_path):
    (tmp_path / "services").mkdir(parents=True)
    (tmp_path / "services" / "app_state.py").write_text("STATE = {}\n")
    (tmp_path / "services" / "strategy_engine.py").write_text(
        "from services.app_state import STATE\nimport os\n"
    )
    assert review_tier.lane3_dependencies(tmp_path, depth=1) == {"services/app_state.py"}


def test_lane3_dependencies_finds_a_dotted_module_and_a_package(tmp_path):
    (tmp_path / "services" / "position").mkdir(parents=True)
    (tmp_path / "services" / "position" / "account_positions.py").write_text("")
    (tmp_path / "services" / "exits").mkdir(parents=True)
    (tmp_path / "services" / "exits" / "__init__.py").write_text("")
    (tmp_path / "services" / "strategy_engine.py").write_text(
        "import services.position.account_positions\nfrom services.exits import run\n"
    )
    assert review_tier.lane3_dependencies(tmp_path, depth=1) == {
        "services/position/account_positions.py", "services/exits/",
    }


def test_lane3_dependencies_finds_the_bare_from_services_form(tmp_path):
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
    assert review_tier.lane3_dependencies(tmp_path, depth=1) == {
        "services/fault_log.py", "services/market_lookup.py",
    }


def test_lane3_dependencies_ignores_a_commented_out_import(tmp_path):
    """Parsing rather than pattern-matching also removes a whole class of false
    positive the regex had: a docstring line beginning `from services.x`."""
    (tmp_path / "services").mkdir(parents=True)
    (tmp_path / "services" / "app_state.py").write_text("")
    (tmp_path / "services" / "strategy_engine.py").write_text(
        '# from services.app_state import STATE\n'
        '"""from services.app_state import STATE"""\n'
    )
    assert review_tier.lane3_dependencies(tmp_path, depth=1) == set()


def test_lane3_dependencies_follows_a_second_hop(tmp_path):
    """The depth-2 change (David's decision, 2026-09-07). strategy_engine imports
    app_state, app_state imports stats_power - so stats_power is Tier A even
    though no Lane 3 file names it. Measured before adopting: depth 2 adds 8
    paths and moves 4 PRs in the recorded 200-PR window; depth 3 adds 20 and
    moves 10, and is identical to the full transitive closure on every outcome,
    leaving only 6 of 111 code PRs at Tier B."""
    (tmp_path / "services").mkdir(parents=True)
    (tmp_path / "services" / "stats_power.py").write_text("")
    (tmp_path / "services" / "app_state.py").write_text(
        "from services import stats_power\n"
    )
    (tmp_path / "services" / "strategy_engine.py").write_text(
        "from services import app_state\n"
    )
    assert review_tier.lane3_dependencies(tmp_path, depth=1) == {
        "services/app_state.py",
    }
    assert review_tier.lane3_dependencies(tmp_path, depth=2) == {
        "services/app_state.py", "services/stats_power.py",
    }


def test_lane3_dependencies_stops_at_the_configured_depth(tmp_path):
    """Depth is a bound, not a suggestion: a third hop is not followed. Without
    this the traversal would silently become the closure the measurement
    rejected."""
    (tmp_path / "services").mkdir(parents=True)
    (tmp_path / "services" / "third.py").write_text("")
    (tmp_path / "services" / "second.py").write_text("from services import third\n")
    (tmp_path / "services" / "first.py").write_text("from services import second\n")
    (tmp_path / "services" / "strategy_engine.py").write_text(
        "from services import first\n"
    )
    found = review_tier.lane3_dependencies(tmp_path, depth=2)
    assert found == {"services/first.py", "services/second.py"}
    assert "services/third.py" not in found


def test_lane3_scan_depth_is_two_by_default():
    """The default is the decision. Changing it is a decision to re-measure -
    see docs/open-decisions.md."""
    assert review_tier.LANE3_SCAN_DEPTH == 2


def test_every_lane3_direct_import_is_covered_by_review_tier_a_paths():
    """Spec §3.1: a module Lane 3 imports directly is Tier A, whatever lane it
    nominally belongs to. Run against the real repo, so a future import of a
    Tier B module from strategy/risk/execution code fails CI here rather than
    shipping as a Tier B PR that changes trading behaviour."""
    uncovered = sorted(
        target for target in review_tier.lane3_dependencies(_REPO_ROOT)
        if not any(target == p or target.startswith(p) for p in labels.REVIEW_TIER_A_PATHS)
    )
    assert not uncovered, f"Lane 3 imports these, but they are not Tier A: {uncovered}"


def test_lane3_dependencies_on_the_real_repo_is_not_vacuous():
    """A liveness check on the scan itself: if the parse or the LANES[3]
    resolution breaks, the coverage test above passes on an empty set and
    reports nothing. The depth-2 scan finds 62 targets (27 at depth 1); the
    floor is set below that so a real removal does not fail CI, but well above
    the depth-1 population, so a silent regression to one hop fails here."""
    targets = review_tier.lane3_dependencies(_REPO_ROOT)
    assert "services/app_state.py" in targets
    assert "services/fault_log.py" in targets      # only visible via `from services import`
    assert "services/stats_power.py" in targets    # second hop, via app_state
    assert "services/diagnostics/" in targets      # second hop; opens paper_broker.DB_PATH
    assert len(targets) >= 55


# Real changed-file lists, taken from the merged PRs themselves (spec §3.2 and
# §7). These are the fixture corpus: if the boundary ever reclassifies one of
# them, that is a decision someone has to make on purpose.
def test_tier_b_the_six_unreviewed_low_blast_radius_prs():
    """The six PRs from the research's unreviewed-30 list that the boundary
    genuinely excuses: a test tightening, a backup-overlap guard, a container
    image line, a gitignore, a comment fix, and a one-off backfill tool.

    Four, down from six: the 2026-09-07 depth-2 decision moved #308
    (services/backup/, now Tier A explicitly - it runs shutil.rmtree over
    data/backups/) and #623 (services/diagnostics/, a second-hop Lane 3
    dependency that opens paper_broker.DB_PATH). Both are named as Tier A
    fixtures below. Earlier the same day #498 left this set when
    services/index_feed/ was added. #273 is Tier B on its *file list* and Tier
    A on its diff - it adds recovered `raw_trades` rows, which rule 3 catches -
    so this fixture exercises the file rules only."""
    corpus = {
        301: ["tests/test_e2e_terminal_static_and_api.py"],
        415: ["Dockerfile", "docs/open-decisions.md"],
        445: [".gitignore"],
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


def test_pr_623_is_tier_a_through_the_second_hop_dependency_diagnostics():
    """The depth-2 decision's first reclassification. services/diagnostics/ is
    not imported by any Lane 3 file directly; it is reached in two hops, and its
    _aio_db.py opens paper_broker.DB_PATH and signal_log.DB_PATH."""
    tier, reasons = review_tier.review_tier(["services/diagnostics/routes.py"])
    assert tier == "A"
    assert any("diagnostics" in r for r in reasons)


def test_pr_308_is_tier_a_because_backup_deletes_recorded_history():
    """Named explicitly rather than reached by the scan. services/backup/ runs
    shutil.rmtree over data/backups/; it is dangerous because of what it does,
    not because of who imports it, which is the same reason services/reset/ and
    services/db.py are hand-listed. Only the full transitive closure reaches it,
    and the measurement rejected the closure."""
    tier, reasons = review_tier.review_tier([
        "services/backup/backup.py", "services/backup/routes.py",
        "tests/test_backup.py", "tests/test_backup_routes.py",
    ])
    assert tier == "A"
    assert any("backup" in r for r in reasons)


def test_the_destructive_modules_are_tier_a_whoever_imports_them():
    """The import graph answers "does a defect here reach a decision"; it cannot
    answer "does this module destroy data". These three are listed for the
    second reason."""
    for path in (
        "services/backup/backup.py",
        "services/data_quarantine.py",
        "services/candidate_ledger.py",
    ):
        assert review_tier.review_tier([path])[0] == "A", path


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
    """*Alone* is the point: the file list must be Tier B on its own, or this
    test passes without the label doing anything. services/quality/report.py
    was the example until the 2026-09-07 depth-2 change made it Tier A by
    path - it kept passing and stopped proving anything, which is why the
    assertion below checks the unlabelled tier first."""
    files = ["tools/historical_data_backfill.py"]
    assert review_tier.review_tier(files)[0] == "B"
    tier, reasons = review_tier.review_tier(files, pr_labels=[labels.CONCERN_HOTPATH])
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


_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "review_artifact_first_lines.tsv"


def _fixture_first_lines() -> list[str]:
    lines = []
    for raw in _FIXTURE.read_text().splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        parts = raw.split("\t", 1)
        lines.append(parts[1] if len(parts) > 1 else parts[0])
    return lines


def test_review_artifact_pattern_matches_this_repos_real_artifact_headings():
    """The forms this repo actually posts, taken verbatim from merged PRs."""
    for line in (
        "## Self-review",
        "## Consolidation",
        "## Independent adversarial review",
        "**Consolidation — GO**",
        "**Self-review (lean, per PR #587)**",
        "**Adversarial review** (independent Agent-tool call, no session memory)",
        "## PR-stage adversarial review (fresh Agent, no session memory)",
        "## Dispatching-session self-review (lean)",
        "Tier B self-review",
        # the prefix forms .claude/rules/branching-and-ci.md documents verbatim;
        # `stage 2 of 3` was rejected until the 2026-09-07 PR-stage review (B2)
        "stage 2 of 3: Adversarial review",
        "## stage 2 of 3 — Adversarial review",
        # real headings that continue with a noun or preposition, not a verb
        "## Adversarial review record (reconstructed after the fact, 2026-09-03)",
        "## Adversarial review + consolidation — coordinator-direct",
        "## Self-review of the PR as submitted (before the PR-level adversarial pass)",
        "## Consolidation addendum — retroactive adversarial review complete",
        "## Independent adversarial sweep (fresh, memory-less pass — read-only)",
    ):
        assert review_tier.is_review_artifact_first_line(line), line


def test_review_artifact_pattern_rejects_comments_that_merely_mention_a_review():
    """The false-positive direction, and the dangerous one: a comment that
    *talks about* a review would otherwise count as one, moving the narration
    from the PR body into a comment's first line and defeating the whole gate.
    Every line here is verbatim from a merged PR (the 2026-09-07 adversarial
    review found eight; PR #632, a Tier A PR, reached its required three only
    through the second of them)."""
    for line in (
        "## Fix-list recheck (adversarial review returned NO-GO)",
        "**Response to the independent adversarial review's finding** (commit `f21cf3c`)",
        "## Clarifying the 1740-vs-1800 discrepancy the adversarial review flagged",
        "## Correction to the self-review's own claim",
        "## Addendum to self-review gap #2 (rate-limit disclosure)",
        "**Status (df, pre-/compact checkpoint):** standing by, waiting on 71's "
        "independent adversarial pass",
        "Merging as the durable #601 benchmark record per tonight's action plan. "
        "Consolidation GO is above",
        "**Coordinator check against the three sign-off conditions.** This is not "
        "the adversarial review",
        "Re-triggering CI: required pr/* contexts never posted",
        "## Checkpoint — fleet-wide pause (David, 3.5h), stopping here",
        "## Production-scale equivalence check (read-only, no writes)",
        "## INCOMPLETE — independent adversarial review terminated mid-pass",
        "**PR review cycle complete** (self-review + adversarial review + "
        "consolidation, per CLAUDE.md)",
        # Narration that *starts* with the word, which anchoring alone did not
        # stop. The first two are verbatim from merged PRs #511 and #515 -
        # author responses reporting a review's verdict, not review artifacts.
        # Found by the 2026-09-07 PR-stage adversarial review (B1); under Tier B
        # any one of these was a full PASS with no review behind it.
        "**Adversarial review returned NO-GO on one Critical finding. It was "
        "right, the finding was mine, and it is now fixed in `4ba223b`.**",
        "**Adversarial review returned NO-GO. It was right, and it found exactly "
        "the doubt I flagged when opening this — fixed in `7aaacc2`.**",
        "Consolidation is still pending - do not merge yet.",
        "Adversarial review is running now, will post when it lands.",
        "Self-review pending; holding the merge.",
        "Consolidation GO is above; merging now.",
        "Self-review, adversarial review and consolidation are all in the PR "
        "body above.",
        "Consolidation was posted on the other PR.",
        "Self-review says this is fine.",
    ):
        assert not review_tier.is_review_artifact_first_line(line), line


def test_review_artifact_pattern_against_the_recorded_snapshot():
    """Frozen data (tests/fixtures/review_artifact_first_lines.tsv): 265 real
    comment first lines from the 200 most recently merged PRs as of 2026-09-07.

    214 match. The 51 that do not are CI re-triggers, corrections, checkpoints,
    rechecks, responses, and two `PR review cycle complete` summaries - one
    comment claiming all three stages is not three artifacts. Exactly one real
    artifact is missed (`## Review outcome (independent adversarial review,
    fresh Agent call)`, PR #501), and that failure is *closed*: the PR reads
    FAIL and the author gives the comment a conventional heading.

    216 until the 2026-09-07 PR-stage adversarial review: the label-boundary
    rule dropped exactly two, PRs #511 and #515, both author responses that
    report a review's verdict rather than being one. Nothing else moved.

    The plan measured 213 of 261 against a snapshot taken earlier the same day
    with a per-PR fetch loop. This snapshot, taken in one `gh pr list
    --json number,comments` call at execution time, is a strict superset: four
    records added, none removed (#387 one consolidation, #329 a self-review and
    a consolidation, #517 a commit note). Three of the four are real artifacts
    and match; the fourth is not one and does not. The pattern itself is
    unchanged - it still returns exactly 213 of 261 on the earlier file."""
    lines = _fixture_first_lines()
    matched = [ln for ln in lines if review_tier.is_review_artifact_first_line(ln)]
    assert len(lines) == 265
    assert len(matched) == 214


def test_count_review_artifacts_uses_only_the_first_line_of_a_comment():
    """A comment that *narrates* a review counts for nothing (spec D2): 12 of
    the 24 unreviewed code PRs in the research narrated one in the body."""
    comments = [
        "## Self-review\n\nfindings: none",
        "Merging now — the adversarial review found nothing worth blocking on.",
    ]
    count, matched = review_tier.count_review_artifacts(comments)
    assert count == 1
    assert matched == ["## Self-review"]


def test_committed_review_documents_do_not_satisfy_the_pr_gate():
    """The PR-stage cycle reviews the PR *as submitted*, so its artifacts are PR
    comments. Counting review-named files in the diff would let a planning-
    pipeline PR's earlier-stage documents satisfy its PR-stage requirement -
    this very branch carries seven such files and would have printed PASS with
    zero PR-stage comments, contradicting CLAUDE.md's "nothing is shared,
    reused, or 'already covered' across stages"."""
    files = [
        "docs/archive/lane-9-tooling-ci-process-governance/research/x-self-review.md",
        "docs/archive/lane-9-tooling-ci-process-governance/research/x-adversarial-review.md",
        "docs/archive/lane-9-tooling-ci-process-governance/research/x-consolidation.md",
    ]
    # the signature takes comments only; files cannot contribute a count at all
    assert review_tier.count_review_artifacts([])[0] == 0
    with pytest.raises(TypeError):
        review_tier.count_review_artifacts([], files)


def test_count_review_artifacts_does_not_double_count_one_comment():
    count, _ = review_tier.count_review_artifacts(
        ["## Self-review and adversarial review and consolidation"]
    )
    assert count == 1


def test_count_review_artifacts_ignores_an_empty_or_whitespace_comment():
    count, _ = review_tier.count_review_artifacts(["", "   \n\n"])
    assert count == 0
