"""CI-owned ratchets for the workflow itself (2026-08-27 audit). Prose budgets that
only live in a rule get exceeded silently - CLAUDE.md went 135 -> 759 lines in 20
days with the sentence 'this directive was already written down here once and
still got missed' inside it. These assertions fail the build instead.

Ratchet direction is down: lower a ceiling when the real number drops; never
raise one to go green (same discipline as tools/quality_audit/baseline.json)."""
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CLAUDE_MD_MAX_LINES = 150
OPEN_DECISIONS_MAX_LINES = 40
PLAN_MAX_LINES = 300
RULES_TOTAL_MAX_LINES = 1000          # was 959 on 2026-08-27; ratchet down, never up
PLAN_BUDGET_APPLIES_FROM = "2026-08-28"  # older plans are history, not held to the budget
ALSO_BUDGETED = {"2026-08-27-workflow-remediation.md"}  # the plan that introduced the budget


def _lines(p: Path) -> int:
    return len(p.read_text().splitlines())


def test_claude_md_is_a_rulebook_not_an_incident_log():
    assert _lines(ROOT / "CLAUDE.md") <= CLAUDE_MD_MAX_LINES


def test_open_decisions_stays_short_enough_to_print_every_session():
    p = ROOT / "docs" / "open-decisions.md"
    assert p.exists() and _lines(p) <= OPEN_DECISIONS_MAX_LINES


def test_new_plans_respect_the_stop_rule_budget():
    over = []
    for p in sorted((ROOT / "docs" / "superpowers" / "plans").glob("*.md")):
        m = re.match(r"(\d{4}-\d{2}-\d{2})-", p.name)
        budgeted = p.name in ALSO_BUDGETED or (m and m.group(1) >= PLAN_BUDGET_APPLIES_FROM)
        if budgeted and _lines(p) > PLAN_MAX_LINES:
            over.append(f"{p.name}: {_lines(p)}")
    assert not over, f"plans over {PLAN_MAX_LINES} lines: {over}"


def test_rule_files_do_not_regrow():
    total = sum(_lines(p) for p in (ROOT / ".claude" / "rules").glob("*.md"))
    assert total <= RULES_TOTAL_MAX_LINES, total


def test_every_hook_harness_timeout_exceeds_the_hooks_own_budget():
    """The exact bug class behind the dead test hook: settings.json gave it 30 s while the
    script's own budget was 240 s, so the harness killed it on every cold run - twice
    diagnosed, never guarded. Any hook declaring BUDGET_SEC must get a larger timeout."""
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text())
    budgets = {}
    for hook in (ROOT / ".claude" / "hooks").glob("*.py"):
        spec = importlib.util.spec_from_file_location(hook.stem, hook)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if hasattr(mod, "BUDGET_SEC"):
            budgets[hook.name] = mod.BUDGET_SEC
    assert "run_tests.py" in budgets
    seen = set()
    for event, groups in settings["hooks"].items():
        for group in groups:
            for h in group.get("hooks", []):
                for name, budget in budgets.items():
                    if name in h.get("command", ""):
                        seen.add(name)
                        assert h.get("timeout", 600) > budget, (name, h.get("timeout"), budget)
    assert seen == set(budgets), f"hooks with a budget but no settings entry: {set(budgets) - seen}"


def test_guard_hooks_are_wired_for_the_events_they_implement():
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text())
    wired = {(event, group.get("matcher", ""), h["command"].split("/")[-1].rstrip('"'))
             for event, groups in settings["hooks"].items()
             for group in groups for h in group.get("hooks", [])}
    assert ("PreToolUse", "Bash", "guard_workflow.py") in wired
    assert ("PreToolUse", "Edit|Write", "guard_workflow.py") in wired
    assert any(e == "PostToolUse" and c == "guard_workflow.py" for e, _, c in wired)


def test_every_hook_script_has_a_test_file():
    untested_legacy = {"check_py_syntax.py", "guard_data_db.py"}  # ratchet: shrink, never grow
    missing = []
    for hook in (ROOT / ".claude" / "hooks").glob("*.py"):
        if hook.name in untested_legacy:
            continue
        candidates = (ROOT / "tests" / f"test_{hook.stem}.py", ROOT / "tests" / f"test_{hook.stem}_hook.py")
        if not any(c.exists() for c in candidates):
            missing.append(hook.name)
    assert not missing, missing
