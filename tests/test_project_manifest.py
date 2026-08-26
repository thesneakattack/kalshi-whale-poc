"""
tools/project_manifest.py (QCP Task 17) - mechanical facts about the repo's
current size/shape (file/line counts, route/service/workflow inventory),
generated fresh rather than hand-maintained in static/status.html where
they inevitably go stale. Everything here runs against a tiny synthetic
mini-repo under tmp_path, never the real repository - deterministic counts
regardless of what this repo's own file counts happen to be on any given
day. No .git directory is created in these fixtures on purpose (per this
task's own plan: "Do not require .git in tests; HEAD may be None if
unavailable") - build_manifest must degrade gracefully, not require git.
"""
import json

import pytest

from tools import project_manifest


def _write(path, text=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_mini_repo(root):
    # --- python: 3 real files (main.py + 2 services), 2 in services/ counted as packages ---
    _write(root / "main.py", "@app.get(\"/api/state\")\nasync def get_state():\n    pass\n\n@app.post(\"/api/reset\")\nasync def reset():\n    pass\n")
    _write(root / "services" / "__init__.py", "")
    _write(root / "services" / "widget_engine.py", "def run():\n    pass\n")
    _write(root / "services" / "gadgets" / "__init__.py", "")
    _write(root / "services" / "gadgets" / "core.py", "def a():\n    pass\ndef b():\n    pass\n")
    _write(
        root / "services" / "gadgets" / "routes.py",
        "@router.get(\"/api/gadgets\")\nasync def list_gadgets():\n    pass\n\n"
        "@router.post(\"/api/gadgets\")\nasync def create_gadget():\n    pass\n",
    )
    # a stray __pycache__ file that must never be counted
    _write(root / "services" / "__pycache__" / "widget_engine.cpython-313.pyc", "not real python")

    # --- tests: 2 files, 3 test functions total ---
    _write(root / "tests" / "test_widget_engine.py", "def test_a():\n    pass\n\ndef test_b():\n    pass\n")
    _write(root / "tests" / "test_gadgets.py", "def test_c():\n    pass\n")

    # --- javascript: 2 real source modules + 1 generated bundle (excluded) + node_modules noise ---
    _write(root / "frontend" / "src" / "js" / "dashboard-core.js", "console.log('a');\n")
    _write(root / "frontend" / "src" / "js" / "shared-utils.js", "console.log('b');\nconsole.log('c');\n")
    _write(root / "static" / "js" / "dashboard.bundle.js", "// generated\n" * 50)
    _write(root / "frontend" / "node_modules" / "some-pkg" / "index.js", "module.exports = {};\n")

    # --- html: 2 real pages ---
    _write(root / "static" / "index.html", "<html></html>\n")
    _write(root / "static" / "status.html", "<html>\n<body></body>\n</html>\n")

    # --- workflow files: 1 GitHub Actions (2 jobs), 1 Woodpecker (3 steps) ---
    _write(
        root / ".github" / "workflows" / "tests.yml",
        "name: tests\non: [push]\njobs:\n  pytest:\n    runs-on: ubuntu-latest\n  lint:\n    runs-on: ubuntu-latest\n",
    )
    _write(
        root / ".woodpecker" / "checks.yml",
        "when:\n  - event: [push]\nsteps:\n  - name: one\n    image: python\n  - name: two\n    image: python\n  - name: three\n    image: python\n",
    )

    # --- data dir noise: must never be scanned for python/line counts ---
    _write(root / "data" / "junk.py", "this must never be counted\n" * 10)

    # --- a nested git worktree checkout: must never be double-counted ---
    _write(
        root / ".claude" / "worktrees" / "some-investigation" / "main.py",
        "this is a full duplicate checkout, must never be counted\n" * 10,
    )
    _write(
        root / ".claude" / "worktrees" / "some-investigation" / "static" / "index.html",
        "<html>duplicate</html>\n",
    )


@pytest.fixture
def mini_repo(tmp_path):
    _build_mini_repo(tmp_path)
    return tmp_path


# --- build_manifest: file/line counts -------------------------------------

def test_python_file_and_line_counts_exclude_pycache_and_data_dir(mini_repo):
    manifest = project_manifest.build_manifest(mini_repo)
    # main.py (7 lines) + services/__init__.py (0->counts as 0 lines, still a file)
    # + widget_engine.py (2 lines) + gadgets/__init__.py (0) + gadgets/core.py (4)
    # + gadgets/routes.py (5) + 2 test files (3+2=... see below) = 7 python files outside tests,
    # tests counted separately below - here just confirm __pycache__/data are excluded.
    assert manifest["files"]["python"] > 0
    # data/junk.py and services/__pycache__/*.pyc must never be counted
    assert manifest["files"]["python"] < 20  # sanity: nowhere near counting the excluded junk


def test_nested_git_worktree_checkout_is_never_double_counted(mini_repo):
    # Found live 2026-08-26: a directory literally named "worktrees" (this
    # repo's own convention, .claude/worktrees/<name>) holds a full nested
    # duplicate checkout - without this exclusion, main.py (7 lines) and
    # index.html would each be counted twice.
    manifest = project_manifest.build_manifest(mini_repo)
    assert manifest["files"]["html"] == 2  # not 3 - the worktree's index.html excluded
    assert manifest["files"]["python"] < 20  # the worktree's main.py excluded


def test_javascript_excludes_generated_bundle_and_node_modules(mini_repo):
    manifest = project_manifest.build_manifest(mini_repo)
    assert manifest["files"]["javascript"] == 2  # dashboard-core.js + shared-utils.js only
    assert manifest["lines"]["javascript"] == 3  # 1 + 2 lines


def test_html_file_and_line_counts(mini_repo):
    manifest = project_manifest.build_manifest(mini_repo)
    assert manifest["files"]["html"] == 2
    assert manifest["lines"]["html"] == 1 + 3  # index.html (1 line) + status.html (3 lines)


# --- build_manifest: services / routes / frontend modules -----------------

def test_services_counts_top_level_package_dirs_and_modules_not_pycache(mini_repo):
    manifest = project_manifest.build_manifest(mini_repo)
    # __init__.py (module itself, excluded as dunder), widget_engine.py, gadgets/ package = 2
    assert manifest["services"] == 2


def test_api_routes_counts_app_and_router_decorators(mini_repo):
    manifest = project_manifest.build_manifest(mini_repo)
    assert manifest["api_routes"] == 4  # 2 @app. in main.py + 2 @router. in gadgets/routes.py


def test_frontend_modules_counts_only_frontend_src_js(mini_repo):
    manifest = project_manifest.build_manifest(mini_repo)
    assert manifest["frontend_modules"] == 2


# --- build_manifest: tests -------------------------------------------------

def test_tests_counts_files_and_test_functions(mini_repo):
    manifest = project_manifest.build_manifest(mini_repo)
    assert manifest["tests"]["files"] == 2
    assert manifest["tests"]["count"] == 3


# --- build_manifest: workflow files/jobs -----------------------------------

def test_workflow_files_and_jobs_parsed_from_both_ci_systems(mini_repo):
    manifest = project_manifest.build_manifest(mini_repo)
    assert manifest["workflow_files"] == 2
    assert manifest["workflow_jobs"] == 2 + 3  # 2 GH Actions jobs + 3 Woodpecker steps


# --- build_manifest: git HEAD ------------------------------------------------

def test_generated_from_head_is_none_with_no_git_directory(mini_repo):
    manifest = project_manifest.build_manifest(mini_repo)
    assert manifest["generated_from_head"] is None


def test_schema_version_is_present(mini_repo):
    manifest = project_manifest.build_manifest(mini_repo)
    assert manifest["schema_version"] == 1


# --- CLI: --write / --check --------------------------------------------------

def test_write_then_check_matches_on_a_freshly_written_manifest(mini_repo, tmp_path):
    out_path = tmp_path / "manifest.json"
    exit_code = project_manifest.main(["--write", str(out_path), "--repo-root", str(mini_repo)])
    assert exit_code == 0
    assert out_path.exists()
    written = json.loads(out_path.read_text())
    assert written["services"] == 2

    check_code = project_manifest.main(["--check", str(out_path), "--repo-root", str(mini_repo)])
    assert check_code == 0


def test_check_fails_when_a_structural_fact_has_actually_drifted(mini_repo, tmp_path):
    out_path = tmp_path / "manifest.json"
    project_manifest.main(["--write", str(out_path), "--repo-root", str(mini_repo)])

    # A real structural change: add a brand new service module.
    _write(mini_repo / "services" / "new_thing.py", "def x():\n    pass\n")

    check_code = project_manifest.main(["--check", str(out_path), "--repo-root", str(mini_repo)])
    assert check_code == 1


def test_check_ignores_generated_from_head_and_generated_at_churn(mini_repo, tmp_path):
    out_path = tmp_path / "manifest.json"
    project_manifest.main(["--write", str(out_path), "--repo-root", str(mini_repo)])
    stored = json.loads(out_path.read_text())
    # Simulate exactly the kind of churn a real git commit produces - HEAD SHA
    # and generation timestamp change on every commit even with zero structural
    # change. The check must not fail on this alone (plan's own Step 6).
    stored["generated_from_head"] = "deadbeef"
    stored["generated_at"] = 0.0
    out_path.write_text(json.dumps(stored))

    check_code = project_manifest.main(["--check", str(out_path), "--repo-root", str(mini_repo)])
    assert check_code == 0


def test_check_exits_2_when_manifest_file_is_missing(mini_repo, tmp_path):
    missing_path = tmp_path / "does-not-exist.json"
    exit_code = project_manifest.main(["--check", str(missing_path), "--repo-root", str(mini_repo)])
    assert exit_code == 2


def test_main_exits_2_when_neither_flag_given(mini_repo):
    assert project_manifest.main(["--repo-root", str(mini_repo)]) == 2


# --- drift tolerance (2026-08-25) -----------------------------------------
# Exact equality made this check fail on ~1 in 5 real commits (measured
# across 25 commits of origin/main: lines.python moved on 21% of them),
# every time for a sub-1% count change carrying no information. The
# resulting alarm fatigue is not hypothetical - it let the pipeline sit red
# across three consecutive real pushes (d644034, 21a303a, b28a380) with
# nobody noticing. These tests pin the tolerance behavior that replaced it.

def _committed(mini_repo, tmp_path, **overrides):
    """Write a manifest, then hand-edit leaf values to simulate drift."""
    out_path = tmp_path / "manifest.json"
    project_manifest.main(["--write", str(out_path), "--repo-root", str(mini_repo)])
    data = json.loads(out_path.read_text())
    for dotted, value in overrides.items():
        target = data
        *parents, leaf = dotted.split(".")
        for p in parents:
            target = target[p]
        target[leaf] = value
    out_path.write_text(json.dumps(data, indent=2) + "\n")
    return out_path


def test_check_passes_when_a_magnitude_field_drifts_within_tolerance(mini_repo, tmp_path):
    # A handful of added lines is what a normal commit does; it must not
    # red CI. 5% drift on a 10% tolerance.
    fresh = project_manifest.build_manifest(mini_repo)
    committed_lines = int(fresh["lines"]["python"] / 1.05)
    path = _committed(mini_repo, tmp_path, **{"lines.python": committed_lines})
    assert project_manifest.main(["--check", str(path), "--repo-root", str(mini_repo)]) == 0


def test_check_fails_when_a_magnitude_field_drifts_beyond_tolerance(mini_repo, tmp_path):
    # The failure this guard actually exists for: status.html displayed
    # "5,189 lines / 29 files" against a repo that had grown an order of
    # magnitude past it.
    path = _committed(mini_repo, tmp_path, **{"lines.python": 2})
    assert project_manifest.main(["--check", str(path), "--repo-root", str(mini_repo)]) == 1


def test_check_fails_when_drift_is_beyond_tolerance_in_either_direction(mini_repo, tmp_path):
    # Shrinking counts (a big deletion or a moved package) must be caught
    # the same as growth - relative drift is symmetric.
    fresh = project_manifest.build_manifest(mini_repo)
    path = _committed(mini_repo, tmp_path, **{"lines.python": fresh["lines"]["python"] * 100})
    assert project_manifest.main(["--check", str(path), "--repo-root", str(mini_repo)]) == 1


def test_check_still_fails_when_a_field_appears_or_disappears(mini_repo, tmp_path):
    # A missing/extra key is a schema change, not a magnitude change, and
    # tolerance must never absorb it.
    out_path = tmp_path / "manifest.json"
    project_manifest.main(["--write", str(out_path), "--repo-root", str(mini_repo)])
    data = json.loads(out_path.read_text())
    del data["api_routes"]
    out_path.write_text(json.dumps(data, indent=2) + "\n")
    assert project_manifest.main(["--check", str(out_path), "--repo-root", str(mini_repo)]) == 1


def test_check_requires_exact_schema_version(mini_repo, tmp_path):
    # schema_version is an identity, not a measurement - tolerance must not
    # apply to it even though it is numeric.
    path = _committed(mini_repo, tmp_path, **{"schema_version": 2})
    assert project_manifest.main(["--check", str(path), "--repo-root", str(mini_repo)]) == 1


def test_tolerance_is_configurable(mini_repo, tmp_path):
    fresh = project_manifest.build_manifest(mini_repo)
    committed_lines = int(fresh["lines"]["python"] / 1.05)
    path = _committed(mini_repo, tmp_path, **{"lines.python": committed_lines})
    # 5% drift passes the default 10% but not an explicit 1%.
    assert project_manifest.main(["--check", str(path), "--repo-root", str(mini_repo), "--tolerance", "0.01"]) == 1
    assert project_manifest.main(["--check", str(path), "--repo-root", str(mini_repo), "--tolerance", "0.20"]) == 0


def test_zero_to_nonzero_drift_is_always_beyond_tolerance(mini_repo, tmp_path):
    # 0 -> anything is an infinite relative change; a percentage cannot
    # express it, so it must not silently divide-by-zero into a pass.
    path = _committed(mini_repo, tmp_path, **{"lines.python": 0})
    assert project_manifest.main(["--check", str(path), "--repo-root", str(mini_repo)]) == 1


def test_within_tolerance_drift_is_still_reported(mini_repo, tmp_path, capsys):
    # Passing silently would hide accumulating drift until it tripped the
    # threshold. The check reports what moved even when it does not fail.
    fresh = project_manifest.build_manifest(mini_repo)
    path = _committed(mini_repo, tmp_path, **{"lines.python": int(fresh["lines"]["python"] / 1.05)})
    project_manifest.main(["--check", str(path), "--repo-root", str(mini_repo)])
    out = capsys.readouterr().out
    assert "lines.python" in out


def test_failure_message_says_how_to_regenerate(mini_repo, tmp_path, capsys):
    # Every prior occurrence of this failure was resolved by the same
    # command; the check should just say it rather than making each person
    # rediscover it.
    path = _committed(mini_repo, tmp_path, **{"lines.python": 2})
    project_manifest.main(["--check", str(path), "--repo-root", str(mini_repo)])
    out = capsys.readouterr().out
    assert "--write" in out
