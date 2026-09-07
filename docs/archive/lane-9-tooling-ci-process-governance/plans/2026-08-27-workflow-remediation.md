# Workflow Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the workflow audit's four root causes into mechanisms — one test owner, plugin routing printed at session start, a single open-decisions list, a stop rule, no duplicate skills — and put the three user-owned decisions to the user instead of deciding them.

**Architecture:** Every change moves a rule from prose into something the harness runs or prints, or deletes a redundant layer. No task adds a paragraph to CLAUDE.md. Tasks 1–6 are mechanical and Claude-owned; Tasks 7–9 are one-pass investigations ending in a decision request; Task 10 finishes the branch.

**Tech Stack:** bash hooks, Python 3.13 hook script + pytest, Markdown skills/rules.

**Spec:** `docs/superpowers/specs/2026-08-27-workflow-audit.md`

**Branch / worktree:** `chore/workflow-remediation` in `.claude/worktrees/chore-workflow-remediation`, off `origin/main` @ `a9bab31`. Another session (autotrade-73, notified 2026-08-28) owns `feat/realtime-data-plane-remediation` in the primary checkout: `main.py`, `services/{exits,observability,strategy_engine,whale_stream,market_watch,diagnostics}`, `tests/`, `services/*/README.md`. **Do not touch those paths.** It will message before editing `ROADMAP.md`.

## Status (2026-08-28)

- Tasks 1–5: shipped on this branch (`2d42793` hook, `e3008e2` session_orient, `e818218` open-decisions, `385623c` CLAUDE.md, `ecde431` stop rule). Deviation from Task 5: `realtime-data-plane-evidence.md` keeps its Completion rule verbatim for the in-flight realtime plan; the stop rule is appended for investigations started after 2026-08-28.
- Task 6: shipped for `final-verification`, `quality-plan-task`, `frontend-modularization-task`, and the two completed investigation skills. Deferred by agreement with the session executing the realtime plan (multi-day): `realtime-data-plane-investigation`, `root-cause-debugging`, and `kalshi-integration-refactor` (named by the frozen `kalshi-integration-authority.md`) stay until that plan closes — tracked in `docs/open-decisions.md`.
- Tasks 7–9: measurements taken; decisions put to the user. Task 10 pending.

## Global Constraints

- Touch only `.claude/`, `CLAUDE.md`, `ROADMAP.md`, `docs/`, `tools/`, `scripts/`, `tests/test_run_tests_hook.py`. Never `services/`, `main.py`, `frontend/`, `data/`.
- One task = one commit; stage specific paths; push after each commit (docs-only pushes take the CI fast path).
- Never run the full pytest suite locally in this plan. CI owns it. Targeted files only.
- No new prose rule longer than one line. A rule that needs a paragraph becomes a hook, a printed line, or a test.
- This plan stays under 300 lines. A task that outgrows its budget stops and records itself in `docs/open-decisions.md`.
- Safety invariants (CLAUDE.md "Safety invariants", `data/*.db` rules, branch protection) are untouched.

### Task 1: One local test owner — scope the hook, fix its timeout, stop swallowing failures

**Files:** Modify `.claude/hooks/run_tests.py`, `.claude/settings.json` (the `run_tests.py` entry), `.claude/skills/checkpoint/SKILL.md:29-41`; Create `tests/test_run_tests_hook.py`.

**Interfaces:** Produces `tests_for(file_path: str, tests_dir: Path) -> list[Path]` and `main(argv=None, run=subprocess.run, tests_dir=TESTS_DIR) -> int` in `run_tests.py`, loadable by path.

- [ ] **Step 1: Write the failing test** — `tests/test_run_tests_hook.py`:

```python
import importlib.util, io, json, subprocess, sys
from pathlib import Path
HOOK = Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "run_tests.py"

def _load():
    spec = importlib.util.spec_from_file_location("run_tests_hook", HOOK)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

def _stdin(monkeypatch, path):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"tool_input": {"file_path": path}})))

def test_tests_for_maps_module_to_its_test_files(tmp_path):
    for n in ("test_risk_manager.py", "test_risk_manager_kill_switch.py", "test_other.py"): (tmp_path / n).write_text("")
    got = sorted(p.name for p in _load().tests_for("services/risk_manager.py", tmp_path))
    assert got == ["test_risk_manager.py", "test_risk_manager_kill_switch.py"]

def test_tests_for_package_module_includes_package_tests(tmp_path):
    for n in ("test_exit_engine.py", "test_exits.py"): (tmp_path / n).write_text("")
    got = sorted(p.name for p in _load().tests_for("services/exits/exit_engine.py", tmp_path))
    assert got == ["test_exit_engine.py", "test_exits.py"]

def test_no_matching_tests_runs_nothing(tmp_path, capsys, monkeypatch):
    calls = []; _stdin(monkeypatch, "services/nothing_here.py")
    rc = _load().main(run=lambda *a, **k: calls.append(a) or subprocess.CompletedProcess(a, 0, "", ""), tests_dir=tmp_path)
    assert rc == 0 and calls == [] and "CI owns it" in capsys.readouterr().out

def test_timeout_is_loud_not_silent(tmp_path, capsys, monkeypatch):
    (tmp_path / "test_risk_manager.py").write_text(""); _stdin(monkeypatch, "services/risk_manager.py")
    def boom(*a, **k): raise subprocess.TimeoutExpired(a[0], k.get("timeout"))
    rc = _load().main(run=boom, tests_dir=tmp_path)
    assert rc == 2 and "exceeded" in capsys.readouterr().err
```

- [ ] **Step 2: Run it to verify it fails** — `ddev exec -s fastapi python3 -m pytest tests/test_run_tests_hook.py -q -p no:testmon` → FAIL, `AttributeError: ... 'tests_for'`.

- [ ] **Step 3: Replace the hook** with:

```python
#!/usr/bin/env python3
"""PostToolUse hook (Edit|Write): run only the test files named after the edited
module. CI (.woodpecker/tests-pytest.yml) is the only full-suite owner.
`.claude/settings.json` must give this hook a timeout > BUDGET_SEC."""
import json, subprocess, sys
from pathlib import Path

BUDGET_SEC = 55
TESTS_DIR = Path(__file__).resolve().parents[2] / "tests"

def _in_scope(file_path: str) -> bool:
    return file_path.endswith(".py") and ("/services/" in file_path or file_path.startswith("services/")
                                         or file_path.endswith("main.py"))

def tests_for(file_path: str, tests_dir: Path) -> list[Path]:
    p = Path(file_path)
    stems = {p.stem} if p.stem != "main" else {"main", "routes"}
    if "services" in p.parts:
        i = p.parts.index("services")
        if len(p.parts) > i + 2:
            stems.add(p.parts[i + 1])  # package name, e.g. "exits"
    return sorted({t for s in stems for t in tests_dir.glob(f"test_{s}*.py")})

def main(argv=None, run=subprocess.run, tests_dir=TESTS_DIR) -> int:
    try:
        file_path = (json.load(sys.stdin).get("tool_input") or {}).get("file_path") or ""
    except Exception:
        return 0
    if not _in_scope(file_path):
        return 0
    targets = tests_for(file_path, tests_dir)
    if not targets:
        print(f"run_tests hook: no tests/test_<module>*.py for {file_path}; CI owns it")
        return 0
    cmd = ["ddev", "exec", "-s", "fastapi", "python3", "-m", "pytest", "-q", "-p", "no:testmon",
           *(f"tests/{t.name}" for t in targets)]
    try:
        r = run(cmd, capture_output=True, text=True, timeout=BUDGET_SEC)
    except subprocess.TimeoutExpired:
        sys.stderr.write(f"run_tests hook: {len(targets)} file(s) exceeded {BUDGET_SEC}s budget for {file_path} - narrow the scope or let CI own it\n")
        return 2
    except FileNotFoundError:
        print("run_tests hook: ddev not found; skipped")
        return 0
    if r.returncode != 0:
        sys.stderr.write(f"pytest failed after editing {file_path}:\n{r.stdout}\n{r.stderr}")
        return 2
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the test to verify it passes** — same command → 4 passed.

- [ ] **Step 5: Fix the harness timeout and the checkpoint skill** — in `.claude/settings.json` change the `run_tests.py` hook's `"timeout": 30` to `60`. In `checkpoint/SKILL.md` replace step 2 (lines 29–41) with:

```markdown
2. **Do not run the suite locally.** CI is the only full-suite owner; the
   per-edit hook already ran the edited module's own tests. Run one file
   locally only while actively debugging a specific failure.
```

- [ ] **Step 6: Verify end to end and commit** — `time (echo '{"tool_input":{"file_path":"services/risk_manager.py"}}' | python3 .claude/hooks/run_tests.py)` → exit 0, under 60 s.

```bash
git add .claude/hooks/run_tests.py .claude/settings.json .claude/skills/checkpoint/SKILL.md tests/test_run_tests_hook.py
git commit -m "fix: per-edit hook runs only the edited module's tests, with a harness timeout it can meet

The hook's command took 188s against a 30s harness timeout; two prior
'silent timeout' incidents raised the inner timeout and never checked the
outer one. One local owner now: scoped tests here, full suite in CI."
```

### Task 2: `session_orient.sh` prints what prose failed to make routine

**Files:** Modify `.claude/hooks/session_orient.sh` — delete line 57 (references retired `static/status.html` and `/sync-status-docs`), append after line 87:

- [ ] **Step 1: Append three blocks**

```bash
qs=$(curl -s --max-time 3 https://kalshi-whale-poc.ddev.site/api/quality/summary 2>/dev/null)
if [ -n "$qs" ]; then
  python3 - "$qs" <<'PY' 2>/dev/null || echo "quality: /api/quality/summary returned unparseable JSON"
import json, sys
d = json.loads(sys.argv[1])
def n(k): v = d.get(k); return len(v) if isinstance(v, (list, dict)) else v
print(f"quality: overall={d.get('overall_status') or d.get('status')} findings={n('findings')} alerts={n('alerts')} faults={n('faults')} - GET /api/quality/summary for detail, before any ad hoc sqlite3/python -c")
PY
else
  echo "quality: /api/quality/summary unreachable (ddev down?) - check before assuming health"
fi

cat <<'EOF'
plugins (use them; never write a project skill or tool that duplicates one):
  superpowers: brainstorming | writing-plans | executing-plans | test-driven-development | systematic-debugging (any bug) | verification-before-completion (before "done") | requesting-code-review | using-git-worktrees
  gitnexus MCP: impact/context/trace before multi-file edits to strategy, risk, advisory, calibration, kalshi client
  dimensional-analysis: after implementing any cents/dollars/probability/contracts/P&L math
  chrome-devtools MCP: any browser-facing evidence (console, network, WebSocket)
  context7 MCP: library docs for FastAPI/Pydantic/asyncio; never for Kalshi (docs/kalshi/ is canonical)
  github MCP / gh: PRs, statuses, issues; github-issues-kanban skill owns claim/dispatch on the board
EOF

if [ -f docs/open-decisions.md ]; then
  echo "open decisions (docs/open-decisions.md - act on or ask about these; don't re-discover them):"
  grep '^- ' docs/open-decisions.md | sed 's/^/  /'
fi
```

- [ ] **Step 2: Verify both states and commit** — `time bash .claude/hooks/session_orient.sh | tail -25` with ddev up → three blocks, under 5 s. With the curl target unreachable (temp copy pointing at a bogus port) → the "unreachable" line, exit 0.

```bash
git add .claude/hooks/session_orient.sh
git commit -m "feat: session_orient prints quality summary, plugin routing, and open decisions unconditionally"
```

### Task 3: One open-decisions list, printed every session

**Files:** Create `docs/open-decisions.md` (≤40 lines). Memory files are left as they are; this list supersedes their "act on next trigger" notes.

- [ ] **Step 1: Create the file**

```markdown
# Open decisions

One line each: item · next action · who decides · since. Printed by
`session_orient.sh` every session. Remove a line when it is done; never
archive here. A `feedback` memory or "standing guidance" gets its line here
in the same session it is written.

- Consolidate six orchestrator skills into `plan-task` + domain checklists · this plan Task 6 · me · 2026-08-26
- Retire AQC tools or keep one on cron · this plan Task 7 → decision · you · 2026-08-27
- `tools/kanban_sync` vs github-issues-kanban plugin: one writer · this plan Task 8 → decision · you · 2026-08-27
- Keep the per-edit test hook at all · this plan Task 9 → decision · you · 2026-08-27
- Six failing tests on `feat/realtime-data-plane-remediation` (test_quality_coordination_cleanup_actions + 5) · read CI, fix or reassign · me · 2026-08-27
- `advisory_engine` suggests on win rate alone, never cost/P&L · implement cost-aware suggestion or close · you (design approval) · 2026-08-22
- Banded cost-aware gate EV diagnostic (0.60–0.95 band negative-EV) · approve `docs/superpowers/specs/2026-08-26-economic-strategy-remediation-design.md` or close · you · 2026-08-26
- Shadow-mode evaluation stretch has never run · schedule a dated stretch · you · 2026-08-26
- Path-based CI test selection for code changes · find the original rejection incident, then decide · me → you · 2026-08-26
- Trade-stream consumer liveness watchdog (queue at capacity, no drain) · implement in realtime branch · other session · 2026-08-27
```

- [ ] **Step 2: Verify and commit** — `bash .claude/hooks/session_orient.sh | grep -c '^  - '` → 10.

```bash
git add docs/open-decisions.md
git commit -m "docs: single open-decisions list, printed at session start"
```

### Task 4: CLAUDE.md becomes a ≤150-line rulebook; delete the stale tooling section

**Files:** Modify `CLAUDE.md` (759 → ≤150); Modify `.claude/rules/tooling-plugins.md` (delete lines 260–302 "Known unresolved"; rewrite the forward references at lines 91 and 252).

- [ ] **Step 1: Rewrite CLAUDE.md to this skeleton** — keep every current `##` heading; under each keep only imperative rules as single lines; every incident narrative is reachable via `git log -S'<phrase>' -- CLAUDE.md`, not text. Line budgets: intro 6 · HARD RULE data plane: six properties one line each + "never trade one for another silently" + "measured against the docs/kalshi ceiling" 12 · Standing goal + paper-focus 6 · Current objective: three axes 5 · Start investigations here: six checks, numbered, one line each 8 · Git history + doc pointers 5 · Dev workflow (ddev facts) 10 · `data/*.db` rules 6 · Persistence idiom code block 10 · Safety invariants 7 · Bug pattern: one sentence + "trace to the backend definition" 3 · Tooling/app separation: five bullets 6 · Quick file map 14 · Kalshi docs HARD RULE + cheatsheet rule 5 · Branching/CI pointer 3 · Long-session workflow: checkpoint cadence, CI owns the full suite, stage specific paths, subagent model tiering 8 · **Toolchain** (new): "Use installed plugins before writing anything; `session_orient.sh` prints the list. Explore/Plan subagents skip CLAUDE.md — pure lookup only." 3 · Budget: "one investigation pass, ≤1 session, before code lands; plans ≤300 lines" 2.

- [ ] **Step 2: Delete the stale tooling-plugins section** — confirm `python3 -c "import json;print(json.load(open('$HOME/.claude/settings.json'))['hooks'])"` shows only `sql_guard.py`; delete lines 260–302; rewrite lines 91 and 252 to "Auto-augment hooks are disabled (verified 2026-08-27)."

- [ ] **Step 3: Verify and commit** — `wc -l CLAUDE.md` ≤150; `for f in $(grep -oE '\.claude/[a-z/-]+\.md' CLAUDE.md | sort -u); do test -f "$f" || echo MISSING $f; done` → nothing; `grep -c 'HARD RULE' CLAUDE.md` → 2.

```bash
git add CLAUDE.md .claude/rules/tooling-plugins.md
git commit -m "docs: CLAUDE.md is a rulebook, not an incident log (759 -> <150 lines); drop resolved GitNexus-hooks section"
```

### Task 5: Stop rule replaces both completion rules

**Files:** Modify `.claude/rules/realtime-data-plane-evidence.md:66-76` and `.claude/rules/autonomous-quality-coordination-evidence.md:124-137`.

- [ ] **Step 1: Replace each section with**

```markdown
## Stop rule

One investigation pass, at most one session, before a code change (fix or
guard) lands. The write-up comes after the fix and fits on one page.
Rejected alternatives get one line each, not a matrix. A plan over 300
lines, or an investigation crossing a session boundary with no landed
change, is the signal to stop and decide — not to add tasks.
```

- [ ] **Step 2: Verify and commit** — `grep -c 'Stop rule' .claude/rules/*.md` → 2; `grep -ci 'completion rule' .claude/rules/*.md` → 0.

```bash
git add .claude/rules/realtime-data-plane-evidence.md .claude/rules/autonomous-quality-coordination-evidence.md
git commit -m "docs: replace investigation completion rules with a stop rule (budget, not criteria)"
```

### Task 6: Delete duplicate skills; one `plan-task` orchestrator with domain checklists

**Files:** Delete `.claude/skills/{root-cause-debugging,final-verification,autonomous-quality-coordination-investigation,economic-strategy-effectiveness-investigation,quality-plan-task,frontend-modularization-task,kalshi-integration-refactor,realtime-data-plane-investigation}/`. Create `.claude/skills/plan-task/SKILL.md` (≤60 lines) and `.claude/skills/plan-task/domains/{quality,frontend,kalshi,realtime,economic}.md` (≤15 lines each). Modify `.claude/rules/quality-capabilities.md`, `CLAUDE.md` (file-map line), `ROADMAP.md` (repoint names).

- [ ] **Step 1: Write `plan-task/SKILL.md`** — frontmatter `description: Use when implementing, resuming, or reviewing any numbered task from a plan under docs/superpowers/plans/ — pass the plan path and domain.` Body: (1) re-ground — `git branch --show-current`, `git log main..HEAD --oneline`, read the plan, pick the first task whose deliverable is absent from HEAD; (2) load `domains/<domain>.md` and follow it; (3) `superpowers:test-driven-development`; (4) targeted verification only; (5) commit, push, `gh api repos/thesneakattack/kalshi-whale-poc/commits/$(git rev-parse HEAD)/status`; (6) stop — one task per invocation. No evidence-class taxonomy; that belongs in a spec.

- [ ] **Step 2: Write the five domain files** — only what is unique to that domain, lifted from the skill being deleted. `kalshi.md`: read `docs/kalshi/CHEATSHEET.md`, then the exact mirrored page; `kalshi-contract-review` for fixtures. `quality.md`: `tools/quality_audit` baseline-ratchet semantics; new deterministic guard → `ci-cd-guardrails`. `frontend.md`: `frontend-verification`; bundle rebuilt; no source/bundle drift. `realtime.md`: measure before tuning (`realtime-data-plane-evidence.md`); hot-path cost measured. `economic.md`: reuse existing reviewed analytics functions first; `dimensional-analysis` after any math.

- [ ] **Step 3: Delete the eight directories and repoint references** — `grep -rln 'root-cause-debugging\|final-verification\|quality-plan-task\|frontend-modularization-task\|kalshi-integration-refactor\|realtime-data-plane-investigation\|economic-strategy-effectiveness-investigation\|autonomous-quality-coordination-investigation' CLAUDE.md .claude ROADMAP.md`; per hit: debugging → `superpowers:systematic-debugging`; verification → `superpowers:verification-before-completion`; orchestrators → `plan-task` + domain.

- [ ] **Step 4: Verify and commit** — `ls .claude/skills | wc -l` → 13; the Step 3 grep → no hits.

```bash
git add -A .claude/skills CLAUDE.md .claude/rules/quality-capabilities.md ROADMAP.md
git commit -m "refactor: one plan-task orchestrator + domain checklists; drop skills that duplicate superpowers"
```

### Task 7: Investigate → decide: retire the AQC tools? (≤1 h; output is a decision request, nothing deleted)

- [ ] **Step 1: Measure** — `python -m tools.quality_coordination` vs `scripts/cleanup-worktrees.sh --dry-run`; list every signal AQC reports that the script does not (expected: `ledger` and `process_hygiene` empty, `branch` overlaps the script, `docs_roadmap_feed` re-prints ROADMAP).
- [ ] **Step 2: `AskUserQuestion`** — (a) retire: delete `tools/quality_coordination.py`, `tools/coordination_engine.py`, `tools/quality_coordination_sim/`, `tools/quality_ratchet.py`, `tools/quality_coordination_data/`, `tests/test_quality_coordination_*.py`, `tests/test_coordination_engine.py`, `tests/test_quality_ratchet*.py`, `.claude/rules/autonomous-quality-coordination-evidence.md` (spec/plan docs stay); (b) keep `quality_ratchet.py` only, on a weekly cron; (c) keep all. Record the answer as the open-decisions line's next action.

### Task 8: Investigate → decide: `tools/kanban_sync` or the plugin (≤1 h)

- [ ] **Step 1: Measure** — from `.claude/skills/kanban-board-sync/SKILL.md:15-30` and `tools/kanban_sync/sync.py`, list what each writer touches (tool: Project Status, milestones, sub-issues, labels; plugin: labels, claims); `git log --oneline --grep=kanban 7aad88a..` for ongoing cost since PR #124.
- [ ] **Step 2: `AskUserQuestion`** — (a) plugin only (lose Project Status/milestone sync); (b) tool only (lose claim/dispatch); (c) keep both with the documented division and freeze the tool. Record the answer.

### Task 9: Investigate → decide: keep the per-edit hook at all (≤30 min)

- [ ] **Step 1: Measure** — for the last five branch pushes, `gh api repos/thesneakattack/kalshi-whale-poc/commits/<sha>/status` → `created_at`/`updated_at` of `ci/woodpecker/push/tests-pytest`; median round-trip vs Task 1's scoped hook time.
- [ ] **Step 2: `AskUserQuestion`** — (a) keep the scoped hook; (b) delete the hook and `tests/test_run_tests_hook.py`; CI is the only owner. Record the answer.

### Task 10: Finish the branch

- [ ] **Step 1:** `superpowers:verification-before-completion` — re-run every Task 1–6 verify command once; paste output into the PR body.
- [ ] **Step 2:** `superpowers:finishing-a-development-branch` → `gh pr create`, body listing the three human gates (Tasks 7–9) as unchecked boxes. If `feat/realtime-data-plane-remediation` has since edited `CLAUDE.md`/`ROADMAP.md`, rebase and resolve first.
- [ ] **Step 3:** After merge, update memory `workflow-audit-2026-08-27.md` to "executed; decisions in docs/open-decisions.md"; remove the worktree (`ExitWorktree`).
