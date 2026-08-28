"""CI-owned wiring checks for .claude/settings.json and .claude/hooks/ - each one a
failure class that was diagnosed at least twice before a test existed: a hook
killed by a harness timeout smaller than its own budget; a hook located through
$CLAUDE_PROJECT_DIR (always the primary checkout, so every worktree session lost
every hook when the primary sat on a branch without the launcher); a guard
implemented for an event it is not wired to; a hook or script with no test.
Ratchet direction is down: the untested allowlist shrinks, never grows."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = ROOT / ".claude" / "settings.json"
HOOKS = ROOT / ".claude" / "hooks"


def _commands():
    settings = json.loads(SETTINGS.read_text())
    return [(event, group.get("matcher", ""), h["command"], h.get("timeout", 600))
            for event, groups in settings["hooks"].items()
            for group in groups for h in group.get("hooks", [])]


def test_every_hook_harness_timeout_exceeds_the_hooks_own_budget():
    budgets = {}
    for hook in HOOKS.glob("*.py"):
        spec = importlib.util.spec_from_file_location(hook.stem, hook)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if hasattr(mod, "BUDGET_SEC"):
            budgets[hook.name] = mod.BUDGET_SEC
    assert "run_tests.py" in budgets
    seen = set()
    for _, _, command, timeout in _commands():
        for name, budget in budgets.items():
            if name in command:
                seen.add(name)
                assert timeout > budget, (name, timeout, budget)
    assert seen == set(budgets), f"hooks with a budget but no settings entry: {set(budgets) - seen}"


def test_every_hook_command_goes_through_the_launcher():
    direct = [c for _, _, c, _ in _commands() if "run_hook.py" not in c]
    assert not direct, direct


def test_every_hook_command_locates_the_launcher_from_the_payload_cwd():
    """Never `$CLAUDE_PROJECT_DIR/.claude/hooks/...`: that is the primary checkout even in
    a worktree session (2026-08-28 F1). The prelude must resolve the checkout from the
    payload's cwd; CLAUDE_PROJECT_DIR is only the last-resort fallback."""
    for _, _, command, _ in _commands():
        assert "rev-parse --show-toplevel" in command, command
        assert '.get("cwd"' in command, command
        assert "$CLAUDE_PROJECT_DIR/.claude/hooks" not in command, command


def test_guard_hooks_are_wired_for_the_events_they_implement():
    wired = {(e, m) for e, m, c, _ in _commands() if "guard_workflow.py" in c}
    assert ("PreToolUse", "Bash") in wired
    assert ("PreToolUse", "Edit|Write") in wired
    assert any(e == "PostToolUse" for e, _ in wired)
    data_guard = {(e, m) for e, m, c, _ in _commands() if "guard_data_db.py" in c}
    assert ("PreToolUse", "Bash") in data_guard
    assert any(e == "SessionStart" for e, _, c, _ in _commands() if "orient.sh" in c)


def test_every_hook_script_and_repo_script_has_a_test_file():
    untested_legacy = {  # ratchet: shrink, never grow
        "check_py_syntax.py", "guard_data_db.py", "ci-skip-heavy-suite.sh", "ci-testmon-run.sh",
    }
    missing = []
    for script in sorted([*HOOKS.glob("*.py"), *HOOKS.glob("*.sh"), *(ROOT / "scripts").glob("*.sh")]):
        if script.name in untested_legacy:
            continue
        stem = script.stem.replace("-", "_")
        candidates = (ROOT / "tests" / f"test_{stem}.py", ROOT / "tests" / f"test_{stem}_hook.py")
        if not any(c.exists() for c in candidates):
            missing.append(script.name)
    assert not missing, missing


def test_settings_keeps_project_scoped_plugin_enablement():
    """settings.json is also where project-scoped plugins are enabled (enabledPlugins) and
    their marketplaces registered (extraKnownMarketplaces). A hooks-only rewrite of the
    file silently disables every one of them - caught in review of the 2026-08-28 rebuild
    before it merged. Each enabled plugin's marketplace must be known."""
    settings = json.loads(SETTINGS.read_text())
    enabled = settings.get("enabledPlugins") or {}
    markets = settings.get("extraKnownMarketplaces") or {}
    assert enabled, "enabledPlugins missing - project-scoped plugins would all be disabled"
    for plugin, on in enabled.items():
        assert on is True, plugin
        _, _, market = plugin.partition("@")
        assert market == "claude-plugins-official" or market in markets, (plugin, sorted(markets))
    for name, spec in markets.items():
        assert spec.get("source", {}).get("repo"), name


def test_no_effort_budget_survives_in_the_hook_layer():
    """Line caps and one-session stop rules were the 2026-08-27 audit's answer to
    'no budget'; the user withdrew them 2026-08-28 as kneecapping. Keep them out."""
    text = (HOOKS / "guard_workflow.py").read_text()
    assert "PLAN_LINE_BUDGET" not in text and "R1" not in text and "R5" not in text
    assert not (ROOT / "tests" / "test_workflow_budgets.py").exists()
