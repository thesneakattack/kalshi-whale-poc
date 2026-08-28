"""CI-owned wiring checks for .claude/settings.json and .claude/hooks/ - each one a
failure class that was diagnosed at least twice before a test existed: a hook
killed by a harness timeout smaller than its own budget; a hook located through
$CLAUDE_PROJECT_DIR (always the primary checkout, so every worktree session lost
every hook when the primary sat on a branch without the launcher); a guard
implemented for an event it is not wired to; a hook or script with no test; a
settings.json rewrite that drops the project's plugin enablement. Ratchet
direction is down: the untested allowlist shrinks, never grows."""
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = ROOT / ".claude" / "settings.json"
HOOKS = ROOT / ".claude" / "hooks"


def _load_hook(name: str):
    path = HOOKS / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _commands():
    settings = json.loads(SETTINGS.read_text())
    return [(event, group.get("matcher", ""), h["command"], h.get("timeout", 600))
            for event, groups in settings["hooks"].items()
            for group in groups for h in group.get("hooks", [])]


def test_every_hook_harness_timeout_exceeds_the_hooks_own_budget():
    budgets = {}
    for hook in HOOKS.glob("*.py"):
        mod = _load_hook(hook.name)
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


def test_all_preludes_are_one_prelude():
    """The harness forces one command string per entry, so the F1 invariant lives in N
    copies. Masking the hook name, they must be byte-identical - a hand edit to one
    copy that keeps the substrings above but breaks the tail would otherwise pass."""
    masked = {re.sub(r'python3 "\$h" \S+; fi$', 'python3 "$h" HOOK; fi', c) for _, _, c, _ in _commands()}
    assert len(masked) == 1, masked
    (prelude,) = masked
    assert prelude.endswith('python3 "$h" HOOK; fi')
    assert 'CLAUDE_HOOK_ROOT="$r"' in prelude  # the launcher reuses the root the prelude already found


def test_guard_hooks_are_wired_for_the_events_they_implement():
    wired = {(e, m) for e, m, c, _ in _commands() if "guard_workflow.py" in c}
    assert ("PreToolUse", "Bash") in wired
    assert ("PreToolUse", "Edit|Write") in wired
    assert ("PostToolUse", "Edit|Write") in wired and ("PostToolUse", "Bash|Read|mcp__gitnexus__.*") in wired
    data_guard = {(e, m) for e, m, c, _ in _commands() if "guard_data_db.py" in c}
    assert ("PreToolUse", "Bash") in data_guard
    assert any(e == "SessionStart" for e, _, c, _ in _commands() if "orient.sh" in c)


def test_every_hook_and_script_has_a_test_the_edit_hook_would_run():
    """One definition of 'covered': run_tests.py's own tests_for(), so the per-edit hook
    and this CI check can never disagree about a file. Extension-less scripts count."""
    run_tests = _load_hook("run_tests.py")
    untested_legacy = {  # ratchet: shrink, never grow
        "check_py_syntax.py", "guard_data_db.py", "ci-skip-heavy-suite.sh", "ci-testmon-run.sh",
        "woodpecker-status", "woodpecker-trigger",
    }
    scripts = sorted(p for p in [*HOOKS.iterdir(), *(ROOT / "scripts").iterdir()]
                     if p.is_file() and not p.name.startswith(".") and "__pycache__" not in p.parts)
    missing = []
    for script in scripts:
        if script.name in untested_legacy:
            continue
        rel = script.relative_to(ROOT).as_posix()
        assert run_tests._in_scope(rel), f"{rel} is not in the edit hook's scope"
        if not run_tests.tests_for(rel, ROOT / "tests"):
            missing.append(rel)
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


def test_gitnexus_version_is_pinned_identically_everywhere():
    """The pin is a literal in several files; skew between them reproduces the
    'index rewritten by one version, queried by another' failure the pin exists to
    prevent. One version, everywhere it is spelled out."""
    files = [ROOT / "CLAUDE.md", HOOKS / "orient.sh", HOOKS / "guard_workflow.py",
             ROOT / ".claude" / "skills" / "checkpoint" / "SKILL.md"]
    found = {}
    for f in files:
        versions = set(re.findall(r"gitnexus@(\d+\.\d+\.\d+)", f.read_text()))
        assert versions, f"{f.name} no longer pins a gitnexus version"
        found[f.name] = versions
    assert len(set().union(*found.values())) == 1, found
    assert "gitnexus@latest" not in (ROOT / "CLAUDE.md").read_text()


def test_no_effort_budget_survives_in_the_hook_layer():
    """Line caps and one-session stop rules were the 2026-08-27 audit's answer to
    'no budget'; the user withdrew them 2026-08-28 as kneecapping. Keep the real
    symbols out (not the rule labels - R10 would contain "R1")."""
    text = (HOOKS / "guard_workflow.py").read_text()
    for symbol in ("PLAN_LINE_BUDGET", "_PYTEST_SCOPED", "_UNCHECKED", "PLAN_DIR"):
        assert symbol not in text, symbol
    assert not (ROOT / "tests" / "test_workflow_budgets.py").exists()
