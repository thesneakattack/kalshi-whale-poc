# Autonomous Engineering Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deterministic, machine-checkable safety gates and the launcher skill that let the user switch on a background agent which claims GitHub Issues, routes each to the workflow this repository already owns, and works them unattended — while every gate that makes unattended operation safe (protected-domain refusal at claim time, the brainstorming human-approval stop, the docs/tests-only merge allowlist, unconditional CI-green, outer-loop halt) is a tested function rather than a thing the agent has to remember.

**Architecture:** One new pure Python package, `tools/autonomous_mode/`, following `tools/quality_coordination_sim/`'s precedent: no network, no `subprocess`, no `gh` invocation, no filesystem write except the one JSON marker file. Every module takes state the agent already read (labels, issue body, changed paths, CI state) and returns a verdict dataclass. A `python -m tools.autonomous_mode <subcommand>` CLI surface is how the agent consumes those verdicts — machine verdicts with real exit codes, not judgment calls. A new `.claude/skills/autonomous-mode/SKILL.md` is the launcher plus the self-contained agent prompt, with a contract test asserting the prompt actually states each binding clause. The loop's ordering guarantees live in a pure state machine (`iteration.py`) so "refused before any work started" is provable, not asserted.

**Tech Stack:** Python 3.13 (stdlib only — `json`, `dataclasses`, `enum`, `datetime`, `fnmatch`, `re`, `argparse`, `pathlib`), `pytest`, the installed `github-issues-kanban` Claude Code skill (label/lock/event-bus protocol, `gh` CLI auth), Claude Code's `Agent`/`TaskStop` tools. No new dependency.

**Spec:** `docs/superpowers/specs/2026-08-26-autonomous-engineering-mode-design.md` — this plan implements every numbered section of that spec; see the Self-Review at the end for the section-by-section coverage check.

## Global Constraints

1. **Empowers, never relaxes (§6).** No task may add a code path, config flag, or prompt clause that skips, weakens, or makes optional any gate this plan builds. Autonomous mode changes *when* work happens, never *what's allowed*.
2. **The brainstorming human-approval gate applies inside autonomous mode with no size-based exception (§6 item 1, §12).** A claimed issue needing a genuinely new architectural decision produces a posted design proposal and then stops. No task may add an "obviously small design" bypass.
3. **Protected-domain issues are refused at claim time, before any investigation or code change starts (§6 item 2).** The merge-time protected-domain check (Task 7) is defense in depth, never the primary gate. Any test that proves a refusal must also prove the work stage was never reached.
4. **Standard git safety is unchanged (§6 item 3):** no `--force`, no `--no-verify`, no commits directly to `main`, and CI must be green before any merge attempt regardless of allowlist status.
5. **The merge allowlist is `docs/**` and `tests/**` only (§7).** No task in this plan widens it, and no task adds unconditional auto-merge (§11) — each is its own separate, explicit, later decision.
6. **Every module in `tools/autonomous_mode/` is pure.** No network I/O, no `subprocess`, no `gh` invocation, no `os.system`, and no filesystem write other than `.claude/autonomous-mode.json`. Verdicts in, verdicts out — the agent performs the actual `gh`/git operations itself. This is `tools/quality_coordination_sim/`'s established shape in this repo, and it is what makes every gate testable with zero credentials.
7. **The marker file is plain JSON at `.claude/autonomous-mode.json` (§8) — deliberately NOT this repo's SQLite `DB_PATH`/`_connect()` persistence idiom.** CLAUDE.md's "Persistence idiom" governs *application* state under `data/*.db` that the running FastAPI process reads and writes; this is per-worktree developer-tooling session state written once by a launcher and cleared by `TaskStop`. Do not "upgrade" it to sqlite3. This constraint exists because a future implementer reading CLAUDE.md will be tempted to.
8. **The kanban skill's claim lock is optimistic, not atomic (§3, §8).** The re-verify-after-acquire mitigation catches the common race window; it is a partial mitigation and an accepted residual risk. No task may document, name, test, or describe it as a guarantee.
9. **Issue-source wiring is out of scope (§11).** No task in this plan creates an adapter exporting `services/quality_coordination.py`'s `escalation_eligible` items, `ROADMAP.md` items, or `docs/superpowers/plans/2026-08-26-active-tracks-board.md`'s tracks into the issue queue. This plan assumes issues already exist.
10. **Nothing here touches real-money safety (§2).** No task modifies `kalshi_account.trading_enabled`, the daily-loss kill switch, CORS, `services/quality_coordination.py`, or resumes AQC's paused Tasks 4-9.
11. **Branching policy (§3, `.claude/rules/branching-and-ci.md`):** this plan's tasks land on a `feat/autonomous-engineering-mode` branch off `main`, one task per commit; Claude runs the targeted tests locally, Woodpecker owns exhaustive verification. Tests never touch a real `data/*.db` file and never call `gh`.

---

## Task 1: Package scaffold and the on/off marker file

**Files:**
- Create: `tools/autonomous_mode/__init__.py`
- Create: `tools/autonomous_mode/marker.py`
- Modify: `.gitignore`
- Test: `tests/test_autonomous_mode.py`

**Interfaces:**
- Produces: `MARKER_RELPATH: Path`, `STATUS_RUNNING`/`STATUS_STOPPED`, `MODE_DRY_RUN`/`MODE_LIVE`/`VALID_MODES`, `MarkerCorruptError`, `MarkerState` (frozen dataclass: `status: str`, `started_at: str`, `agent_id: str`, `worktree: str`, `mode: str = MODE_DRY_RUN`, `stopped_at: str | None = None`), `marker_path(worktree: Path) -> Path`, `read_marker(worktree: Path) -> MarkerState | None`, `write_marker(worktree: Path, state: MarkerState) -> Path`, `start_marker(worktree: Path, *, agent_id: str, mode: str, at: datetime) -> MarkerState`, `mark_stopped(worktree: Path, *, at: datetime) -> MarkerState`, `is_running(worktree: Path) -> bool`.

**Design note — why plain JSON, said out loud so nobody re-litigates it:** spec §8 specifies `.claude/autonomous-mode.json` as a plain JSON marker file. CLAUDE.md's "Persistence idiom" section (one SQLite file per stateful module under `data/`) governs *application* state the live FastAPI process reads and writes; this is per-worktree developer-tooling state a launcher skill writes once and a `TaskStop` clears. See Global Constraint 7. The file is gitignored — it is runtime state of one worktree, not repository state.

**Extension beyond the spec's literal JSON, flagged deliberately:** spec §8 shows the marker as `{"status", "started_at", "agent_id", "worktree"}` prefixed with "e.g.". This task adds two fields: `mode` (`"dry-run"`/`"live"`, required because §10.1's dry-run mode has to be durable — a session that reconnects to a running agent must be able to tell whether it is allowed to push) and `stopped_at` (so a stopped marker retains when it stopped rather than losing the fact). Both default safely; `mode` defaults to `dry-run`, the restrictive value.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_autonomous_mode.py
"""Gate-level tests for tools/autonomous_mode — the deterministic safety gates behind
autonomous engineering mode (docs/superpowers/specs/2026-08-26-autonomous-engineering-
mode-design.md). Every module under test is pure: no network, no subprocess, no gh call,
no filesystem write except the marker file, which every test here redirects to tmp_path.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools.autonomous_mode import marker as m

T0 = datetime(2026, 8, 26, 12, 0, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 8, 26, 13, 30, 0, tzinfo=timezone.utc)


def test_read_marker_returns_none_when_absent(tmp_path):
    assert m.read_marker(tmp_path) is None
    assert m.is_running(tmp_path) is False


def test_start_marker_round_trips_through_disk(tmp_path):
    state = m.start_marker(tmp_path, agent_id="agent-1", mode=m.MODE_DRY_RUN, at=T0)
    assert state.status == m.STATUS_RUNNING
    assert m.marker_path(tmp_path) == tmp_path / ".claude" / "autonomous-mode.json"
    reloaded = m.read_marker(tmp_path)
    assert reloaded == state
    assert m.is_running(tmp_path) is True


def test_start_marker_rejects_an_unknown_mode(tmp_path):
    with pytest.raises(ValueError):
        m.start_marker(tmp_path, agent_id="agent-1", mode="yolo", at=T0)


def test_mark_stopped_preserves_identity_and_records_when(tmp_path):
    m.start_marker(tmp_path, agent_id="agent-1", mode=m.MODE_LIVE, at=T0)
    stopped = m.mark_stopped(tmp_path, at=T1)
    assert stopped.status == m.STATUS_STOPPED
    assert stopped.agent_id == "agent-1"
    assert stopped.started_at == T0.isoformat()
    assert stopped.stopped_at == T1.isoformat()
    assert stopped.mode == m.MODE_LIVE
    assert m.is_running(tmp_path) is False


def test_corrupt_marker_raises_rather_than_reading_as_off(tmp_path):
    """Fail closed: an unreadable marker must not look like 'not running', because we
    cannot prove no agent is already running against this worktree."""
    path = m.marker_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    with pytest.raises(m.MarkerCorruptError):
        m.read_marker(tmp_path)


def test_marker_missing_required_key_raises(tmp_path):
    path = m.marker_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"status": "running", "started_at": T0.isoformat()}))
    with pytest.raises(m.MarkerCorruptError):
        m.read_marker(tmp_path)


def test_marker_defaults_mode_to_dry_run_when_absent(tmp_path):
    """The restrictive default: an older/hand-written marker with no mode is dry-run."""
    path = m.marker_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "status": "running", "started_at": T0.isoformat(),
        "agent_id": "a", "worktree": str(tmp_path),
    }))
    assert m.read_marker(tmp_path).mode == m.MODE_DRY_RUN


def test_mark_stopped_with_no_marker_raises(tmp_path):
    with pytest.raises(m.MarkerCorruptError):
        m.mark_stopped(tmp_path, at=T1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_autonomous_mode.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.autonomous_mode'`

- [ ] **Step 3: Write minimal implementation**

```python
# tools/autonomous_mode/__init__.py
"""Deterministic safety gates for autonomous engineering mode.

Every module in this package is pure: no network I/O, no subprocess, no `gh` invocation,
and no filesystem write other than `.claude/autonomous-mode.json`. The agent running
autonomous mode performs the real `gh`/git operations; this package only tells it whether
an operation is allowed, so each safety claim in
docs/superpowers/specs/2026-08-26-autonomous-engineering-mode-design.md §6-§8 is a tested
function rather than something the agent has to remember mid-loop.
"""
```

```python
# tools/autonomous_mode/marker.py
"""On/off state for autonomous engineering mode — a plain JSON marker file (spec §8).

Deliberately NOT this repo's SQLite persistence idiom. CLAUDE.md's "Persistence idiom"
section governs *application* state under `data/*.db` that the live FastAPI process reads
and writes. This is per-worktree developer-tooling session state: a launcher skill writes
it once, `TaskStop` clears it, and it is gitignored. Do not "upgrade" this to
sqlite3/_connect() — a future implementer reading CLAUDE.md will be tempted to, which is
exactly why this paragraph is here.

"On" and "off" are the conjunction of this file and whether the agent is actually running;
spec §8 is explicit that no separate abstract state machine exists anywhere else.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

MARKER_RELPATH = Path(".claude") / "autonomous-mode.json"

STATUS_RUNNING = "running"
STATUS_STOPPED = "stopped"

MODE_DRY_RUN = "dry-run"
MODE_LIVE = "live"
VALID_MODES = (MODE_DRY_RUN, MODE_LIVE)

_REQUIRED_KEYS = frozenset({"status", "started_at", "agent_id", "worktree"})


class MarkerCorruptError(RuntimeError):
    """The marker exists but cannot be parsed, or is missing a required key.

    Deliberately raises rather than returning None: an unreadable marker must fail the
    launcher closed, never silently read as "off" — we cannot prove no agent is already
    running against this worktree.
    """


@dataclass(frozen=True)
class MarkerState:
    status: str
    started_at: str
    agent_id: str
    worktree: str
    mode: str = MODE_DRY_RUN
    stopped_at: str | None = None


def marker_path(worktree: Path) -> Path:
    return Path(worktree) / MARKER_RELPATH


def read_marker(worktree: Path) -> MarkerState | None:
    path = marker_path(worktree)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        raise MarkerCorruptError(f"{path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise MarkerCorruptError(f"{path}: expected a JSON object, got {type(raw).__name__}")
    missing = _REQUIRED_KEYS - set(raw)
    if missing:
        raise MarkerCorruptError(f"{path}: missing keys {sorted(missing)}")
    return MarkerState(
        status=raw["status"],
        started_at=raw["started_at"],
        agent_id=raw["agent_id"],
        worktree=raw["worktree"],
        mode=raw.get("mode", MODE_DRY_RUN),
        stopped_at=raw.get("stopped_at"),
    )


def write_marker(worktree: Path, state: MarkerState) -> Path:
    path = marker_path(worktree)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(state), indent=2, sort_keys=True) + "\n")
    return path


def start_marker(worktree: Path, *, agent_id: str, mode: str, at: datetime) -> MarkerState:
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {VALID_MODES}, got {mode!r}")
    state = MarkerState(
        status=STATUS_RUNNING,
        started_at=at.isoformat(),
        agent_id=agent_id,
        worktree=str(worktree),
        mode=mode,
    )
    write_marker(worktree, state)
    return state


def mark_stopped(worktree: Path, *, at: datetime) -> MarkerState:
    current = read_marker(worktree)
    if current is None:
        raise MarkerCorruptError(f"no marker at {marker_path(worktree)} to stop")
    stopped = MarkerState(
        status=STATUS_STOPPED,
        started_at=current.started_at,
        agent_id=current.agent_id,
        worktree=current.worktree,
        mode=current.mode,
        stopped_at=at.isoformat(),
    )
    write_marker(worktree, stopped)
    return stopped


def is_running(worktree: Path) -> bool:
    state = read_marker(worktree)
    return state is not None and state.status == STATUS_RUNNING
```

Append to `.gitignore`, after the existing `.claude/settings-json-gitnexus-hooks-removed.json` block:

```gitignore
# Autonomous engineering mode's on/off marker (spec §8) — per-worktree runtime
# session state written by .claude/skills/autonomous-mode, not repository state.
.claude/autonomous-mode.json
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_autonomous_mode.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/autonomous_mode/__init__.py tools/autonomous_mode/marker.py tests/test_autonomous_mode.py .gitignore
git commit -m "feat: add autonomous-mode package scaffold and JSON on/off marker

Spec §8's marker file, deliberately plain JSON rather than this repo's SQLite
persistence idiom — that idiom governs live application state under data/, not
per-worktree tooling session state. Fails closed on a corrupt marker: unreadable
must never read as 'not running'."
```

---

## Task 2: Launch preflight — refuses `main`, refuses double-start

**Files:**
- Create: `tools/autonomous_mode/preflight.py`
- Test: `tests/test_autonomous_mode.py` (append)

**Interfaces:**
- Consumes: `tools.autonomous_mode.marker`'s `MarkerState`, `STATUS_RUNNING`, `VALID_MODES` (Task 1).
- Produces: `PROTECTED_BRANCH: str`, `INITIATIVE_PREFIXES: tuple[str, ...]`, `PreflightVerdict` (frozen dataclass: `ok: bool`, `reasons: tuple[str, ...]`), `preflight(*, branch: str, worktree: Path, marker: MarkerState | None, mode: str) -> PreflightVerdict`.

Spec §8: the launcher "refuses to run against `main` directly (same check the branching policy already requires elsewhere); confirms an initiative branch is checked out." `INITIATIVE_PREFIXES` is copied verbatim from `.claude/rules/branching-and-ci.md`'s "Prefixes" line. Every failing condition is reported, not just the first — a launcher that says "you're on main" and then, after you fix that, says "an agent is already running" wastes a round-trip for no reason.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_autonomous_mode.py (append)
from tools.autonomous_mode import preflight as pf


def _running_marker(tmp_path):
    return m.MarkerState(
        status=m.STATUS_RUNNING, started_at=T0.isoformat(),
        agent_id="agent-1", worktree=str(tmp_path), mode=m.MODE_DRY_RUN,
    )


def test_preflight_accepts_an_initiative_branch(tmp_path):
    v = pf.preflight(branch="feat/autonomous-engineering-mode", worktree=tmp_path,
                     marker=None, mode=m.MODE_DRY_RUN)
    assert v.ok is True
    assert v.reasons == ()


def test_preflight_refuses_main(tmp_path):
    v = pf.preflight(branch="main", worktree=tmp_path, marker=None, mode=m.MODE_DRY_RUN)
    assert v.ok is False
    assert "refuses-to-run-on-main" in v.reasons


def test_preflight_refuses_a_detached_or_unknown_head(tmp_path):
    v = pf.preflight(branch="", worktree=tmp_path, marker=None, mode=m.MODE_DRY_RUN)
    assert v.ok is False
    assert "detached-head-or-unknown-branch" in v.reasons


def test_preflight_refuses_a_branch_outside_the_repo_prefixes(tmp_path):
    v = pf.preflight(branch="wip/scratch", worktree=tmp_path, marker=None, mode=m.MODE_DRY_RUN)
    assert v.ok is False
    assert "not-an-initiative-branch:wip/scratch" in v.reasons


def test_preflight_refuses_when_an_agent_is_already_running(tmp_path):
    v = pf.preflight(branch="fix/x", worktree=tmp_path,
                     marker=_running_marker(tmp_path), mode=m.MODE_DRY_RUN)
    assert v.ok is False
    assert "already-running:agent-1" in v.reasons


def test_preflight_allows_relaunch_after_a_stopped_marker(tmp_path):
    stopped = m.MarkerState(status=m.STATUS_STOPPED, started_at=T0.isoformat(),
                            agent_id="agent-1", worktree=str(tmp_path),
                            mode=m.MODE_DRY_RUN, stopped_at=T1.isoformat())
    assert pf.preflight(branch="fix/x", worktree=tmp_path, marker=stopped,
                        mode=m.MODE_DRY_RUN).ok is True


def test_preflight_refuses_an_unknown_mode(tmp_path):
    v = pf.preflight(branch="fix/x", worktree=tmp_path, marker=None, mode="turbo")
    assert v.ok is False
    assert "invalid-mode:turbo" in v.reasons


def test_preflight_refuses_a_worktree_that_is_not_a_directory(tmp_path):
    missing = tmp_path / "nope"
    v = pf.preflight(branch="fix/x", worktree=missing, marker=None, mode=m.MODE_DRY_RUN)
    assert v.ok is False
    assert any(r.startswith("worktree-not-a-directory:") for r in v.reasons)


def test_preflight_reports_every_failure_not_just_the_first(tmp_path):
    v = pf.preflight(branch="main", worktree=tmp_path,
                     marker=_running_marker(tmp_path), mode="turbo")
    assert v.ok is False
    assert len(v.reasons) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_autonomous_mode.py -v -k preflight`
Expected: FAIL with `ImportError: cannot import name 'preflight' from 'tools.autonomous_mode'`

- [ ] **Step 3: Write minimal implementation**

```python
# tools/autonomous_mode/preflight.py
"""Launch preconditions for autonomous engineering mode (spec §8, "Launch").

Refuses to start against `main`, against a detached HEAD, against a branch outside this
repo's initiative-branch prefixes, and against a worktree that already has a running
agent. Pure: takes the branch/worktree/marker the launcher already read; performs no git
or filesystem mutation of its own.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tools.autonomous_mode.marker import STATUS_RUNNING, VALID_MODES, MarkerState

PROTECTED_BRANCH = "main"

# Verbatim from .claude/rules/branching-and-ci.md's "Prefixes" line. If that rule ever
# changes its prefix list, this tuple is the one place to update.
INITIATIVE_PREFIXES = ("feat/", "fix/", "refactor/", "chore/", "docs/")


@dataclass(frozen=True)
class PreflightVerdict:
    ok: bool
    reasons: tuple[str, ...]


def preflight(*, branch: str, worktree: Path, marker: MarkerState | None,
              mode: str) -> PreflightVerdict:
    reasons: list[str] = []

    if not branch or not branch.strip():
        reasons.append("detached-head-or-unknown-branch")
    elif branch == PROTECTED_BRANCH:
        reasons.append("refuses-to-run-on-main")
    elif not branch.startswith(INITIATIVE_PREFIXES):
        reasons.append(f"not-an-initiative-branch:{branch}")

    if mode not in VALID_MODES:
        reasons.append(f"invalid-mode:{mode}")

    if marker is not None and marker.status == STATUS_RUNNING:
        reasons.append(f"already-running:{marker.agent_id}")

    if not Path(worktree).is_dir():
        reasons.append(f"worktree-not-a-directory:{worktree}")

    return PreflightVerdict(ok=not reasons, reasons=tuple(reasons))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_autonomous_mode.py -v`
Expected: PASS (17 tests total)

- [ ] **Step 5: Commit**

```bash
git add tools/autonomous_mode/preflight.py tests/test_autonomous_mode.py
git commit -m "feat: add autonomous-mode launch preflight

Spec §8's launch checks as a function rather than a prompt instruction: refuses
main, detached HEAD, a non-initiative branch, an already-running agent, and an
unknown mode — reporting every failure at once so a launcher doesn't round-trip."
```

---

## Task 3: Path matcher and the protected-domain classifier

**Files:**
- Create: `tools/autonomous_mode/pathmatch.py`
- Create: `tools/autonomous_mode/protected_domains.py`
- Test: `tests/test_autonomous_mode.py` (append)

**Interfaces:**
- Produces (`pathmatch`): `normalize_path(path: str) -> str`, `matches(path: str, pattern: str) -> bool`, `matches_any(path: str, patterns: Iterable[str]) -> str | None` (returns the matching pattern, or None).
- Produces (`protected_domains`): `ProtectedDomain` (frozen dataclass: `name: str`, `patterns: tuple[str, ...]`), `PROTECTED_DOMAINS: tuple[ProtectedDomain, ...]`, `ProtectedVerdict` (frozen dataclass: `protected: bool`, `domain: str | None`, `matched_path: str | None`, `pattern: str | None`), `classify_paths(paths: Iterable[str]) -> ProtectedVerdict`.

This is spec §6 item 2's list — `.claude/rules/autonomous-quality-coordination-evidence.md`'s "Remediation authority rule" domains, generalized beyond AQC and mapped onto this repository's real paths. **The spec names domains, not paths; the mapping is where judgment enters**, and it is deliberately over-broad rather than surgical: an unattended agent must not even attempt changes here, so a false refusal costs one human round-trip while a false permission costs a bad unattended change to trading code.

**`main.py` is protected** because it holds both the trading loop and `POST /api/trading/enable`. **`config/settings.yaml` is protected** because it holds `kalshi_account.trading_enabled` and every `risk.*` limit. **The `autonomous-mode-self` domain protects this mechanism's own implementation** (spec §6 item 2's last clause) — note that this does not block *building* it, since implementation happens in a normal human-supervised session, not under autonomous mode.

**Two matching subtleties, both real failure modes rather than pedantry.** First: plain `fnmatch` lets `*` cross a `/`, so `services/*.py` would also match `services/kalshi/account_client.py` — patterns are therefore matched segment-by-segment, with a separate explicit `<dir>/**` prefix form. Second: normalization must **not** use `path.lstrip("./")`, which strips *any* leading `.` or `/` character and silently rewrites `.claude/autonomous-mode.json` to `claude/autonomous-mode.json` — every `.claude/**` pattern would stop matching and the mechanism's own self-protection would quietly stop working. There is a test for exactly that.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_autonomous_mode.py (append)
from tools.autonomous_mode import pathmatch as pm
from tools.autonomous_mode import protected_domains as pd


def test_star_does_not_cross_a_path_separator():
    assert pm.matches("services/risk_manager.py", "services/*.py") is True
    assert pm.matches("services/kalshi/account_client.py", "services/*.py") is False


def test_double_star_suffix_matches_everything_below_a_directory():
    assert pm.matches("services/advisory/advisory_engine.py", "services/advisory/**") is True
    assert pm.matches("services/advisory", "services/advisory/**") is True
    assert pm.matches("services/advisory_engine.py", "services/advisory/**") is False


def test_normalize_strips_a_leading_dot_slash_without_eating_a_dotfile():
    """Regression guard for lstrip('./'), which would rewrite '.claude/x' to 'claude/x'
    and silently disable every .claude/** protected pattern."""
    assert pm.normalize_path("./services/x.py") == "services/x.py"
    assert pm.normalize_path(".claude/autonomous-mode.json") == ".claude/autonomous-mode.json"
    assert pm.matches(".claude/autonomous-mode.json", ".claude/autonomous-mode.json") is True


def test_matcher_never_matches_a_traversal_segment():
    assert pm.matches("../services/risk_manager.py", "services/**") is False
    assert pm.matches("docs/../services/risk_manager.py", "docs/**") is False


def test_matches_any_returns_the_matching_pattern():
    assert pm.matches_any("docs/a/b.md", ("tests/**", "docs/**")) == "docs/**"
    assert pm.matches_any("services/x.py", ("tests/**", "docs/**")) is None


@pytest.mark.parametrize("path,domain", [
    ("main.py", "real-trading-and-order-execution"),
    ("services/kalshi/account_client.py", "real-trading-and-order-execution"),
    ("services/kalshi/orders.py", "real-trading-and-order-execution"),
    ("services/shadow_mode.py", "real-trading-and-order-execution"),
    ("services/risk_manager.py", "risk-limits-and-kill-switches"),
    ("config/settings.yaml", "risk-limits-and-kill-switches"),
    ("services/paper_broker.py", "sizing-bankroll-exposure"),
    ("services/exits/exit_engine.py", "sizing-bankroll-exposure"),
    ("services/advisory/advisory_engine.py", "whale-advisory-confidence-calibration"),
    ("services/whale_calibration/confidence_calibration.py", "whale-advisory-confidence-calibration"),
    ("services/confidence_scoring.py", "whale-advisory-confidence-calibration"),
    ("services/strategy_engine.py", "strategy-ev-fee-pnl-settlement"),
    ("services/kalshi_fees.py", "strategy-ev-fee-pnl-settlement"),
    ("services/auth.py", "security-auth-policy"),
    (".woodpecker/tests-pytest.yml", "ci-branch-protection-credential-policy"),
    (".claude/rules/branching-and-ci.md", "ci-branch-protection-credential-policy"),
    ("tools/autonomous_mode/merge_allowlist.py", "autonomous-mode-self"),
    (".claude/skills/autonomous-mode/SKILL.md", "autonomous-mode-self"),
    ("tests/test_autonomous_mode_fault_injection.py", "autonomous-mode-self"),
])
def test_every_protected_domain_is_reachable(path, domain):
    v = pd.classify_paths([path])
    assert v.protected is True
    assert v.domain == domain
    assert v.matched_path == path


def test_ordinary_paths_are_not_protected():
    v = pd.classify_paths(["docs/a.md", "tests/test_x.py", "frontend/src/js/panels/x.js"])
    assert v.protected is False
    assert v.domain is None


def test_a_traversal_or_absolute_path_is_protected_as_unresolvable():
    """Fail closed in this direction: a path the matcher cannot reason about must never
    be classified as safe-to-touch."""
    assert pd.classify_paths(["../../etc/passwd"]).domain == "unresolvable-path"
    assert pd.classify_paths(["/etc/passwd"]).domain == "unresolvable-path"


def test_domain_reported_is_deterministic_regardless_of_input_order():
    earlier = "services/risk_manager.py"            # domain #2
    later = "services/strategy_engine.py"           # domain #5
    assert pd.classify_paths([earlier, later]).domain == "risk-limits-and-kill-switches"
    assert pd.classify_paths([later, earlier]).domain == "risk-limits-and-kill-switches"


def test_empty_and_blank_paths_are_ignored_not_matched():
    assert pd.classify_paths(["", "   ", "docs/a.md"]).protected is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_autonomous_mode.py -v -k pathmatch or protected or domain or normalize or traversal`
Expected: FAIL with `ImportError: cannot import name 'pathmatch' from 'tools.autonomous_mode'`

- [ ] **Step 3: Write minimal implementation**

```python
# tools/autonomous_mode/pathmatch.py
"""Explicit, dependency-free path matching for the safety gates.

Two pattern forms only, both matched against repo-relative POSIX paths:

  `<dir>/**`      everything at or below <dir> (prefix match on a path boundary)
  anything else   fnmatch applied SEGMENT BY SEGMENT, so `*` never crosses a `/`

The segment rule matters: plain `fnmatch.fnmatch` lets `*` match `/`, so a `services/*.py`
protected pattern would also match `services/kalshi/account_client.py` and a `docs/*`
allowlist entry would match paths well outside `docs/`. Both callers here are safety
gates where an over-broad match is a real failure, so the matching is written out rather
than delegated to a library whose defaults point the wrong way.

A path containing a `..` segment, or an absolute path, never matches anything. Each caller
then fails closed in its own direction: `protected_domains.classify_paths` reports such a
path as protected (`unresolvable-path`), and `merge_allowlist` treats it as outside the
allowlist. Never "unmatched, therefore fine."
"""
from __future__ import annotations

import fnmatch
from typing import Iterable


def normalize_path(path: str) -> str:
    """Repo-relative POSIX form. Never use `str.lstrip("./")` here: lstrip takes a SET of
    characters, so it would rewrite `.claude/autonomous-mode.json` to
    `claude/autonomous-mode.json` and silently disable every `.claude/**` pattern."""
    normalized = path.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _is_unresolvable(path: str) -> bool:
    return path.startswith("/") or ".." in path.split("/")


def matches(path: str, pattern: str) -> bool:
    normalized = normalize_path(path)
    if not normalized or _is_unresolvable(normalized):
        return False
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        return normalized == prefix or normalized.startswith(prefix + "/")
    path_parts = normalized.split("/")
    pattern_parts = pattern.split("/")
    if len(path_parts) != len(pattern_parts):
        return False
    return all(fnmatch.fnmatch(p, g) for p, g in zip(path_parts, pattern_parts))


def matches_any(path: str, patterns: Iterable[str]) -> str | None:
    for pattern in patterns:
        if matches(path, pattern):
            return pattern
    return None
```

```python
# tools/autonomous_mode/protected_domains.py
"""Spec §6 item 2: the domains an unattended agent refuses to touch AT CLAIM TIME.

The domain list is `.claude/rules/autonomous-quality-coordination-evidence.md`'s
"Remediation authority rule" list, generalized beyond AQC. The spec names domains; the
mapping onto this repository's real paths below is where judgment enters, and it is
deliberately over-broad rather than surgical: a false refusal costs one human round-trip,
a false permission costs an unattended change to trading code.

`main.py` is included because it holds both the trading loop and POST /api/trading/enable.
`config/settings.yaml` is included because it holds `kalshi_account.trading_enabled` and
every `risk.*` limit. `autonomous-mode-self` is the spec's own last clause — this
mechanism's loop, allowlist, and protected-domain list are themselves protected. That does
not block building it: implementation happens in a normal human-supervised session.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from tools.autonomous_mode.pathmatch import matches_any, normalize_path


@dataclass(frozen=True)
class ProtectedDomain:
    name: str
    patterns: tuple[str, ...]


PROTECTED_DOMAINS: tuple[ProtectedDomain, ...] = (
    ProtectedDomain("real-trading-and-order-execution", (
        "main.py",
        "services/kalshi/account.py",
        "services/kalshi/account_client.py",
        "services/kalshi/orders.py",
        "services/execution.py",
        "services/shadow_mode.py",
    )),
    ProtectedDomain("risk-limits-and-kill-switches", (
        "services/risk_manager.py",
        "config/settings.yaml",
        "services/config/**",
        "services/config_bounds.py",
        "services/config_overrides.py",
    )),
    ProtectedDomain("sizing-bankroll-exposure", (
        "services/paper_broker.py",
        "services/position/**",
        "services/exits/**",
        "services/account_positions.py",
        "services/mutual_exclusivity.py",
    )),
    ProtectedDomain("whale-advisory-confidence-calibration", (
        "services/advisory/**",
        "services/whale_calibration/**",
        "services/whale_stream/**",
        "services/whalewatchers/**",
        "services/confidence_scoring.py",
        "services/whale_gate.py",
        "services/whale_simulator.py",
    )),
    ProtectedDomain("strategy-ev-fee-pnl-settlement", (
        "services/strategy_engine.py",
        "services/kalshi_fees.py",
        "services/trade_analytics.py",
        "services/settlement_edge.py",
        "services/settlement_edge_entry.py",
        "services/backtest/**",
    )),
    ProtectedDomain("security-auth-policy", (
        "services/auth.py",
        "services/accounts_store.py",
        ".ddev/**",
        ".env",
        ".env.example",
    )),
    ProtectedDomain("ci-branch-protection-credential-policy", (
        ".woodpecker/**",
        ".github/**",
        "scripts/**",
        ".claude/rules/branching-and-ci.md",
        "tools/quality_audit/baseline.json",
    )),
    ProtectedDomain("autonomous-mode-self", (
        "tools/autonomous_mode/**",
        ".claude/skills/autonomous-mode/**",
        ".claude/autonomous-mode.json",
        "tests/test_autonomous_mode*.py",
        "docs/superpowers/specs/2026-08-26-autonomous-engineering-mode-design.md",
        "docs/superpowers/plans/2026-08-26-autonomous-engineering-mode.md",
    )),
)

_UNRESOLVABLE = "unresolvable-path"


@dataclass(frozen=True)
class ProtectedVerdict:
    protected: bool
    domain: str | None
    matched_path: str | None
    pattern: str | None


def classify_paths(paths: Iterable[str]) -> ProtectedVerdict:
    """First protected hit wins, iterating DOMAINS-major so the reported domain is a
    deterministic function of the domain list's declaration order, never of the caller's
    path ordering."""
    normalized = [normalize_path(p) for p in paths if p and p.strip()]

    for path in normalized:
        if path.startswith("/") or ".." in path.split("/"):
            return ProtectedVerdict(True, _UNRESOLVABLE, path, None)

    for domain in PROTECTED_DOMAINS:
        for path in normalized:
            hit = matches_any(path, domain.patterns)
            if hit is not None:
                return ProtectedVerdict(True, domain.name, path, hit)

    return ProtectedVerdict(False, None, None, None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_autonomous_mode.py -v`
Expected: PASS (45 tests total — the parametrized domain test contributes 19)

- [ ] **Step 5: Commit**

```bash
git add tools/autonomous_mode/pathmatch.py tools/autonomous_mode/protected_domains.py tests/test_autonomous_mode.py
git commit -m "feat: add protected-domain classifier and a segment-aware path matcher

Spec §6 item 2's domain list, mapped onto this repo's real paths and deliberately
over-broad: a false refusal costs a round-trip, a false permission costs an
unattended change to trading code. Matching is written out rather than delegated
to fnmatch, whose * crosses / and would silently over-match; normalization avoids
lstrip('./'), which would rewrite .claude/x to claude/x and disable the
mechanism's own self-protection."
```

---

## Task 4: Issue scope contract and the claim-time scope gate

**Files:**
- Create: `tools/autonomous_mode/issue_scope.py`
- Test: `tests/test_autonomous_mode.py` (append)

**Interfaces:**
- Consumes: `classify_paths`, `ProtectedVerdict` (Task 3).
- Produces: `SCOPE_HEADING: str`, `ScopeVerdict` (frozen dataclass: `claimable: bool`, `paths: tuple[str, ...]`, `refusal: str | None`, `protected: ProtectedVerdict`), `parse_scope(issue_body: str) -> tuple[str, ...]`, `scope_verdict(issue_body: str) -> ScopeVerdict`.

**Judgment call, flagged because the spec leaves it open.** Spec §6 item 2 says "an issue whose target scope/paths touch any of these is refused before any investigation or code change starts" — but the spec never says how an issue *declares* its scope, and there is no way to know what a fix touches before doing it. This task resolves that by requiring an explicit `## Scope` section in the issue body listing repo-relative paths/globs, and by **failing closed**: an issue with no declared scope is not claimable, and the agent posts a `blocked` event asking for one. That keeps the gate honest (it gates on a declaration, and Task 7 independently re-checks the *actual* diff before merge) rather than pretending to infer scope. The kanban skill's `references/issue-as-task-contract.md` already requires a structured issue body, so this is an addition to an existing contract, not a new one.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_autonomous_mode.py (append)
from tools.autonomous_mode import issue_scope as isc

_BODY_TEMPLATE = """\
## Problem
Something is wrong.

## Scope
- `services/analytics/trends.py`
- tests/test_trends.py

## Acceptance criteria
- tests pass
"""


def test_parse_scope_reads_list_items_and_strips_backticks():
    assert isc.parse_scope(_BODY_TEMPLATE) == (
        "services/analytics/trends.py", "tests/test_trends.py",
    )


def test_parse_scope_stops_at_the_next_heading():
    assert "tests pass" not in isc.parse_scope(_BODY_TEMPLATE)


def test_parse_scope_accepts_bare_lines_without_list_markers():
    body = "## Scope\ndocs/a.md\ndocs/b.md\n"
    assert isc.parse_scope(body) == ("docs/a.md", "docs/b.md")


def test_parse_scope_returns_empty_when_the_section_is_absent():
    assert isc.parse_scope("## Problem\nno scope here\n") == ()


def test_scope_verdict_refuses_an_issue_with_no_declared_scope():
    v = isc.scope_verdict("## Problem\nno scope here\n")
    assert v.claimable is False
    assert v.refusal == "no-declared-scope"
    assert v.paths == ()


def test_scope_verdict_refuses_a_protected_domain_and_names_it():
    body = "## Scope\n- services/risk_manager.py\n"
    v = isc.scope_verdict(body)
    assert v.claimable is False
    assert v.refusal == "protected-domain:risk-limits-and-kill-switches"
    assert v.protected.matched_path == "services/risk_manager.py"


def test_scope_verdict_refuses_when_only_one_of_several_paths_is_protected():
    body = "## Scope\n- docs/a.md\n- services/strategy_engine.py\n- tests/test_x.py\n"
    v = isc.scope_verdict(body)
    assert v.claimable is False
    assert v.refusal == "protected-domain:strategy-ev-fee-pnl-settlement"


def test_scope_verdict_clears_an_ordinary_issue():
    v = isc.scope_verdict(_BODY_TEMPLATE)
    assert v.claimable is True
    assert v.refusal is None
    assert v.protected.protected is False


def test_scope_heading_match_is_case_insensitive_and_tolerates_a_suffix():
    body = "## scope (files this touches)\n- docs/a.md\n"
    assert isc.parse_scope(body) == ("docs/a.md",)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_autonomous_mode.py -v -k scope`
Expected: FAIL with `ImportError: cannot import name 'issue_scope' from 'tools.autonomous_mode'`

- [ ] **Step 3: Write minimal implementation**

```python
# tools/autonomous_mode/issue_scope.py
"""The issue-body scope contract, and spec §6 item 2's claim-time refusal built on it.

The spec requires refusing a protected-domain issue "before any investigation or code
change starts" but does not say how an issue declares what it will touch — and nothing can
infer that before the work is done. Resolution: the issue body must carry a `## Scope`
section listing repo-relative paths/globs, and an issue without one is NOT claimable.
Failing closed here is the point: an undeclared scope is an unbounded scope.

This is a declaration-time gate. `merge_allowlist` independently re-checks the ACTUAL diff
before any merge (spec §7), so a fix that wanders outside its declared scope is still
caught — by the gate that can see real paths.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from tools.autonomous_mode.protected_domains import ProtectedVerdict, classify_paths

SCOPE_HEADING = "## scope"

_LIST_MARKERS = ("- ", "* ", "+ ")


@dataclass(frozen=True)
class ScopeVerdict:
    claimable: bool
    paths: tuple[str, ...]
    refusal: str | None
    protected: ProtectedVerdict


def parse_scope(issue_body: str) -> tuple[str, ...]:
    collected: list[str] = []
    in_block = False
    for line in (issue_body or "").splitlines():
        stripped = line.strip()
        if not in_block:
            if stripped.lower().startswith(SCOPE_HEADING):
                in_block = True
            continue
        if stripped.startswith("#"):
            break
        if not stripped:
            continue
        for marker in _LIST_MARKERS:
            if stripped.startswith(marker):
                stripped = stripped[len(marker):].strip()
                break
        cleaned = stripped.strip("`").strip()
        if cleaned:
            collected.append(cleaned)
    return tuple(collected)


def scope_verdict(issue_body: str) -> ScopeVerdict:
    paths = parse_scope(issue_body)
    if not paths:
        return ScopeVerdict(
            claimable=False, paths=(), refusal="no-declared-scope",
            protected=ProtectedVerdict(False, None, None, None),
        )
    protected = classify_paths(paths)
    if protected.protected:
        return ScopeVerdict(
            claimable=False, paths=paths,
            refusal=f"protected-domain:{protected.domain}", protected=protected,
        )
    return ScopeVerdict(claimable=True, paths=paths, refusal=None, protected=protected)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_autonomous_mode.py -v`
Expected: PASS (54 tests total)

- [ ] **Step 5: Commit**

```bash
git add tools/autonomous_mode/issue_scope.py tests/test_autonomous_mode.py
git commit -m "feat: require a declared issue scope and refuse protected domains on it

Spec §6 item 2 demands a claim-time refusal but never says how an issue declares
what it touches, and nothing can infer that before the work is done. Resolved with
an explicit '## Scope' section, failing closed: an undeclared scope is an unbounded
scope. The actual diff is independently re-checked before merge (spec §7)."
```

---

## Task 5: Label routing table

**Files:**
- Create: `tools/autonomous_mode/routing.py`
- Test: `tests/test_autonomous_mode.py` (append)

**Interfaces:**
- Produces: `TYPE_PREFIX: str`, `SKILL_LINE_PREFIX: str`, `Route` (frozen dataclass: `label: str`, `skill: str`, `requires_human_approval: bool`, `accepts_named_skill: bool`, `note: str`), `ROUTING_TABLE: dict[str, Route]`, `RoutingVerdict` (frozen dataclass: `claimable: bool`, `route: Route | None`, `resolved_skill: str | None`, `refusal: str | None`), `named_skill(issue_body: str) -> str | None`, `route_for_issue(labels: Iterable[str], issue_body: str = "") -> RoutingVerdict`.

Spec §5's table verbatim. Two rows ("whichever numbered-task orchestrator the issue names", "the domain's existing investigation skill if the issue names one") require reading the issue body, which is what `accepts_named_skill` plus an optional `Skill:` line covers. `type:feature`/`type:design` carry `requires_human_approval=True` — the machine-readable half of spec §6 item 1; the behavioral half is Task 9's terminal state and Task 13's prompt contract.

**Note for the implementer:** `type:*` is **not** in the kanban skill's canonical `assets/label-scheme.json` (which defines `status:*`, `claimed-by:*`, `claim-expires:*`, `depends-on:#*`, `agent-output:*`, `size:*`, `complexity:*`, `priority:*`, and the control labels). It is a repo-local extension this design introduces, and Task 15 documents it as such. Do not "fix" this by trying to reuse an existing label family.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_autonomous_mode.py (append)
from tools.autonomous_mode import routing as rt


def test_bug_routes_to_systematic_debugging_without_human_approval():
    v = rt.route_for_issue(["type:bug", "priority:p1"])
    assert v.claimable is True
    assert v.resolved_skill == "superpowers:systematic-debugging"
    assert v.route.requires_human_approval is False


@pytest.mark.parametrize("label", ["type:feature", "type:design"])
def test_feature_and_design_route_to_brainstorming_and_require_human_approval(label):
    v = rt.route_for_issue([label])
    assert v.claimable is True
    assert v.resolved_skill == "superpowers:brainstorming"
    assert v.route.requires_human_approval is True


def test_investigation_defaults_to_root_cause_debugging():
    v = rt.route_for_issue(["type:investigation"])
    assert v.claimable is True
    assert v.resolved_skill == "root-cause-debugging"


def test_investigation_uses_a_skill_named_in_the_body():
    body = "## Problem\nx\n\nSkill: `realtime-data-plane-investigation`\n"
    v = rt.route_for_issue(["type:investigation"], body)
    assert v.resolved_skill == "realtime-data-plane-investigation"


def test_plan_task_refuses_when_the_issue_names_no_plan_or_orchestrator():
    v = rt.route_for_issue(["type:plan-task"])
    assert v.claimable is False
    assert v.refusal == "plan-task-names-no-skill-or-plan"


def test_plan_task_accepts_a_named_orchestrator():
    v = rt.route_for_issue(["type:plan-task"], "Skill: quality-plan-task\n")
    assert v.claimable is True
    assert v.resolved_skill == "quality-plan-task"


def test_an_unlabeled_issue_is_refused():
    v = rt.route_for_issue(["priority:p2", "size:s"])
    assert v.claimable is False
    assert v.refusal == "missing-type-label"


def test_two_type_labels_are_refused_rather_than_guessed():
    v = rt.route_for_issue(["type:bug", "type:feature"])
    assert v.claimable is False
    assert v.refusal == "ambiguous-type-labels:type:bug,type:feature"


def test_an_unrecognized_type_label_is_refused():
    v = rt.route_for_issue(["type:chore"])
    assert v.claimable is False
    assert v.refusal == "unrecognized-type-label:type:chore"


def test_named_skill_ignores_a_line_that_is_not_a_skill_line():
    assert rt.named_skill("## Problem\nSkills are great\n") is None


def test_every_routing_table_entry_names_a_real_skill_string():
    """A route whose `skill` is empty would silently dispatch to nothing."""
    for label, route in rt.ROUTING_TABLE.items():
        assert route.label == label
        assert route.skill.strip()
        assert route.note.strip()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_autonomous_mode.py -v -k routing or route or skill`
Expected: FAIL with `ImportError: cannot import name 'routing' from 'tools.autonomous_mode'`

- [ ] **Step 3: Write minimal implementation**

```python
# tools/autonomous_mode/routing.py
"""Spec §5's routing table: which workflow this repository ALREADY has owns an issue.

The mechanism invents no new workflow for any category of work; it decides when to run an
existing one, keyed on the issue's `type:*` label. `type:*` is a repo-local extension to
the kanban skill's canonical label scheme (assets/label-scheme.json defines status/claim/
dependency/agent-output/sizing/priority/control, not type) — see Task 15's documentation.

Exactly one `type:*` label is required. Zero, two, or an unrecognized one all refuse
rather than guess: a mis-routed unattended dispatch is worse than a stalled one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

TYPE_PREFIX = "type:"
SKILL_LINE_PREFIX = "skill:"


@dataclass(frozen=True)
class Route:
    label: str
    skill: str
    requires_human_approval: bool
    accepts_named_skill: bool
    note: str


ROUTING_TABLE: dict[str, Route] = {
    "type:bug": Route(
        label="type:bug",
        skill="superpowers:systematic-debugging",
        requires_human_approval=False,
        accepts_named_skill=False,
        note=("Prove root cause before changing behavior; this repo's root-cause-debugging "
              "skill carries the same discipline and its investigation-to-guard rule."),
    ),
    "type:investigation": Route(
        label="type:investigation",
        skill="root-cause-debugging",
        requires_human_approval=False,
        accepts_named_skill=True,
        note=("If the issue names a domain investigation skill on a `Skill:` line "
              "(realtime-data-plane-investigation, economic-strategy-effectiveness-"
              "investigation, ...), that skill wins; otherwise a general pass using "
              "root-cause-debugging's discipline."),
    ),
    "type:plan-task": Route(
        label="type:plan-task",
        skill="superpowers:executing-plans",
        requires_human_approval=False,
        accepts_named_skill=True,
        note=("The issue MUST name the orchestrator or plan file on a `Skill:` line "
              "(quality-plan-task, frontend-modularization-task, "
              "kalshi-integration-refactor, or a plan path for "
              "superpowers:executing-plans / subagent-driven-development)."),
    ),
    "type:feature": Route(
        label="type:feature",
        skill="superpowers:brainstorming",
        requires_human_approval=True,
        accepts_named_skill=False,
        note=("Spec §6 item 1: the human-approval hard gate applies inside autonomous mode "
              "with no size-based exception. A design proposal is posted as an event and "
              "the issue stops there."),
    ),
    "type:design": Route(
        label="type:design",
        skill="superpowers:brainstorming",
        requires_human_approval=True,
        accepts_named_skill=False,
        note=("Spec §6 item 1: the human-approval hard gate applies inside autonomous mode "
              "with no size-based exception. A design proposal is posted as an event and "
              "the issue stops there."),
    ),
}


@dataclass(frozen=True)
class RoutingVerdict:
    claimable: bool
    route: Route | None
    resolved_skill: str | None
    refusal: str | None


def named_skill(issue_body: str) -> str | None:
    for line in (issue_body or "").splitlines():
        stripped = line.strip()
        for marker in ("- ", "* ", "+ "):
            if stripped.startswith(marker):
                stripped = stripped[len(marker):].strip()
                break
        if stripped.lower().startswith(SKILL_LINE_PREFIX):
            value = stripped[len(SKILL_LINE_PREFIX):].strip().strip("`").strip()
            return value or None
    return None


def route_for_issue(labels: Iterable[str], issue_body: str = "") -> RoutingVerdict:
    type_labels = sorted(l for l in labels if l.startswith(TYPE_PREFIX))
    if not type_labels:
        return RoutingVerdict(False, None, None, "missing-type-label")
    if len(type_labels) > 1:
        return RoutingVerdict(
            False, None, None, f"ambiguous-type-labels:{','.join(type_labels)}",
        )

    label = type_labels[0]
    route = ROUTING_TABLE.get(label)
    if route is None:
        return RoutingVerdict(False, None, None, f"unrecognized-type-label:{label}")

    resolved = route.skill
    if route.accepts_named_skill:
        named = named_skill(issue_body)
        if named:
            resolved = named
        elif label == "type:plan-task":
            return RoutingVerdict(False, route, None, "plan-task-names-no-skill-or-plan")

    return RoutingVerdict(True, route, resolved, None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_autonomous_mode.py -v`
Expected: PASS (66 tests total)

- [ ] **Step 5: Commit**

```bash
git add tools/autonomous_mode/routing.py tests/test_autonomous_mode.py
git commit -m "feat: add the label -> existing-skill routing table

Spec §5 as a dispatch function. The mechanism invents no workflow; it decides when
to run one this repo already owns. Zero, two, or an unrecognized type:* label all
refuse rather than guess — a mis-routed unattended dispatch is worse than a stalled
one. type:* is a repo-local extension to the kanban skill's label scheme."
```

---

## Task 6: Claim lock — claimability, TTL staleness, re-verify-after-acquire

**Files:**
- Create: `tools/autonomous_mode/claim.py`
- Test: `tests/test_autonomous_mode.py` (append)

**Interfaces:**
- Produces: `STATUS_CLAIMABLE`, `STATUS_CLAIMED`, `CLAIMED_BY_PREFIX`, `CLAIM_EXPIRES_PREFIX`, `CLAIM_TTL_PREFIX`, `DEPENDS_ON_PREFIX`, `AGENT_SKIP_LABEL`, `DEFAULT_TTL: timedelta`, `MAX_TTL: timedelta`, `ClaimCheck` (frozen dataclass: `action: str`, `reason: str`), `claimants(labels) -> tuple[str, ...]`, `expires_at(labels) -> datetime | None`, `ttl(labels) -> timedelta`, `blocking_dependencies(labels, done: Iterable[int]) -> tuple[int, ...]`, `is_stale(labels, now: datetime) -> bool`, `is_claimable(labels, now: datetime, done: Iterable[int] = ()) -> ClaimCheck`, `verify_after_acquire(labels, agent_id: str) -> ClaimCheck`, `claim_labels(agent_id: str, now: datetime, labels: Iterable[str] = ()) -> tuple[str, ...]`.
- `ClaimCheck.action` is one of `"proceed"`, `"stale-release"`, `"release-and-retry"`, `"not-claimable"` — these exact strings are consumed by Task 8.

Implements the kanban skill's `references/lock-protocol.md` steps 1-7 as pure label arithmetic, plus spec §8's mitigation: **re-read the claim labels immediately after acquiring, before starting any real work.** Global Constraint 8 applies — this catches the common race window, it is not atomicity, and nothing in this module or its tests may describe it as a guarantee.

Conflict tie-break follows the lock protocol's own suggestion ("the one whose claim sorts later alphabetically" releases), made concrete: `min(claimants)` wins, everyone else releases. Deterministic and requires no coordination.

A `status:claimed` issue with no parseable `claim-expires:` label is treated as **stale**: the lock protocol defines the lock as the conjunction of all three labels, so a malformed lock is not held. The alternative — treating it as permanently held — would strand an issue forever with no recovery path.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_autonomous_mode.py (append)
from datetime import timedelta

from tools.autonomous_mode import claim as cl


def _claimed(agent="agent-a", expires=None, extra=()):
    expires = expires if expires is not None else (T0 + timedelta(minutes=30))
    return ("status:claimed", f"claimed-by:{agent}",
            f"claim-expires:{expires.isoformat()}", *extra)


def test_a_claimable_issue_proceeds():
    check = cl.is_claimable(("status:claimable", "type:bug"), T0)
    assert check.action == "proceed"


def test_an_issue_without_status_claimable_is_not_claimable():
    check = cl.is_claimable(("type:bug",), T0)
    assert check.action == "not-claimable"
    assert check.reason == "not-status-claimable"


def test_agent_skip_is_refused_before_anything_else():
    check = cl.is_claimable(("status:claimable", "agent-skip"), T0)
    assert check.action == "not-claimable"
    assert check.reason == "agent-skip"


def test_unresolved_dependencies_block_the_claim():
    check = cl.is_claimable(("status:claimable", "depends-on:#7", "depends-on:#9"),
                            T0, done={9})
    assert check.action == "not-claimable"
    assert check.reason == "unresolved-dependencies:#7"


def test_resolved_dependencies_do_not_block_the_claim():
    check = cl.is_claimable(("status:claimable", "depends-on:#7"), T0, done={7})
    assert check.action == "proceed"


def test_a_live_claim_by_another_agent_is_not_claimable():
    check = cl.is_claimable(_claimed(), T0 + timedelta(minutes=5))
    assert check.action == "not-claimable"
    assert check.reason == "held-by:agent-a"


def test_an_expired_claim_asks_for_a_stale_release():
    check = cl.is_claimable(_claimed(), T0 + timedelta(minutes=31))
    assert check.action == "stale-release"
    assert check.reason == "stale-claim:agent-a"


def test_a_claimed_issue_with_no_expiry_label_is_stale_not_permanently_held():
    labels = ("status:claimed", "claimed-by:agent-a")
    assert cl.is_stale(labels, T0) is True


def test_ttl_defaults_to_thirty_minutes_and_honours_an_override():
    assert cl.ttl(()) == timedelta(minutes=30)
    assert cl.ttl(("claim-ttl:2h",)) == timedelta(hours=2)
    assert cl.ttl(("claim-ttl:90s",)) == timedelta(seconds=90)


def test_ttl_is_clamped_to_the_protocol_maximum():
    assert cl.ttl(("claim-ttl:72h",)) == timedelta(hours=24)


def test_an_unparseable_ttl_falls_back_to_the_default():
    assert cl.ttl(("claim-ttl:soon",)) == timedelta(minutes=30)


def test_claim_labels_are_the_three_lock_protocol_labels():
    labels = cl.claim_labels("agent-a", T0)
    assert labels == (
        "status:claimed",
        "claimed-by:agent-a",
        f"claim-expires:{(T0 + timedelta(minutes=30)).isoformat()}",
    )


def test_verify_after_acquire_proceeds_for_a_sole_claimant():
    check = cl.verify_after_acquire(_claimed("agent-a"), "agent-a")
    assert check.action == "proceed"
    assert check.reason == "sole-claimant"


def test_verify_after_acquire_releases_the_losing_agent_on_a_conflict():
    labels = ("status:claimed", "claimed-by:agent-a", "claimed-by:agent-b")
    assert cl.verify_after_acquire(labels, "agent-a").action == "proceed"
    loser = cl.verify_after_acquire(labels, "agent-b")
    assert loser.action == "release-and-retry"
    assert loser.reason == "lost-conflict-tiebreak-to:agent-a"


def test_verify_after_acquire_reports_a_lost_claim():
    check = cl.verify_after_acquire(_claimed("agent-a"), "agent-b")
    assert check.action == "not-claimable"
    assert check.reason == "claim-lost"


def test_verify_after_acquire_reports_no_claim_at_all():
    check = cl.verify_after_acquire(("status:claimable",), "agent-a")
    assert check.action == "not-claimable"
    assert check.reason == "claim-lost"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_autonomous_mode.py -v -k claim or ttl or stale`
Expected: FAIL with `ImportError: cannot import name 'claim' from 'tools.autonomous_mode'`

- [ ] **Step 3: Write minimal implementation**

```python
# tools/autonomous_mode/claim.py
"""The kanban skill's lock protocol as pure label arithmetic.

Implements references/lock-protocol.md steps 1-7 plus spec §8's mitigation: re-read the
claim labels immediately after acquiring, before starting any real work.

That mitigation catches the COMMON race window. It is NOT atomicity — the skill's own
v0.1.0 scope says so explicitly ("true atomic lock via external service" is deferred to
v0.2+), and spec §8 accepts the residual risk rather than over-promising. Nothing in this
module may be described as a guarantee.

Conflict tie-break: `min(claimants)` wins, everyone else releases. The lock protocol
suggests exactly this shape ("deterministically chosen — e.g., the one whose claim sorts
later alphabetically") and it needs no coordination between the racing agents.

A `status:claimed` issue with no parseable `claim-expires:` is treated as stale: the lock
is defined as the conjunction of all three labels, so a malformed lock is not held. The
alternative strands the issue forever with no recovery path.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

STATUS_CLAIMABLE = "status:claimable"
STATUS_CLAIMED = "status:claimed"
CLAIMED_BY_PREFIX = "claimed-by:"
CLAIM_EXPIRES_PREFIX = "claim-expires:"
CLAIM_TTL_PREFIX = "claim-ttl:"
DEPENDS_ON_PREFIX = "depends-on:#"
AGENT_SKIP_LABEL = "agent-skip"

DEFAULT_TTL = timedelta(minutes=30)
MAX_TTL = timedelta(hours=24)

_TTL_PATTERN = re.compile(r"^(\d+)([smh])$")
_TTL_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600}


@dataclass(frozen=True)
class ClaimCheck:
    action: str  # "proceed" | "stale-release" | "release-and-retry" | "not-claimable"
    reason: str


def claimants(labels: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        label[len(CLAIMED_BY_PREFIX):].strip()
        for label in labels
        if label.startswith(CLAIMED_BY_PREFIX) and label[len(CLAIMED_BY_PREFIX):].strip()
    )


def expires_at(labels: Iterable[str]) -> datetime | None:
    for label in labels:
        if not label.startswith(CLAIM_EXPIRES_PREFIX):
            continue
        raw = label[len(CLAIM_EXPIRES_PREFIX):].strip()
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None
    return None


def ttl(labels: Iterable[str]) -> timedelta:
    for label in labels:
        if not label.startswith(CLAIM_TTL_PREFIX):
            continue
        match = _TTL_PATTERN.match(label[len(CLAIM_TTL_PREFIX):].strip())
        if match is None:
            return DEFAULT_TTL
        seconds = int(match.group(1)) * _TTL_UNIT_SECONDS[match.group(2)]
        return min(timedelta(seconds=seconds), MAX_TTL)
    return DEFAULT_TTL


def blocking_dependencies(labels: Iterable[str], done: Iterable[int] = ()) -> tuple[int, ...]:
    done_set = set(done)
    blocking: list[int] = []
    for label in labels:
        if not label.startswith(DEPENDS_ON_PREFIX):
            continue
        raw = label[len(DEPENDS_ON_PREFIX):].strip()
        if not raw.isdigit():
            continue
        number = int(raw)
        if number not in done_set:
            blocking.append(number)
    return tuple(sorted(blocking))


def is_stale(labels: Iterable[str], now: datetime) -> bool:
    labels = tuple(labels)
    if STATUS_CLAIMED not in labels and not claimants(labels):
        return False
    expiry = expires_at(labels)
    if expiry is None:
        return True
    return expiry <= now


def is_claimable(labels: Iterable[str], now: datetime, done: Iterable[int] = ()) -> ClaimCheck:
    labels = tuple(labels)

    if AGENT_SKIP_LABEL in labels:
        return ClaimCheck("not-claimable", AGENT_SKIP_LABEL)

    blocking = blocking_dependencies(labels, done)
    if blocking:
        rendered = ",".join(f"#{n}" for n in blocking)
        return ClaimCheck("not-claimable", f"unresolved-dependencies:{rendered}")

    held_by = claimants(labels)
    if STATUS_CLAIMED in labels or held_by:
        holder = held_by[0] if held_by else "unknown"
        if is_stale(labels, now):
            return ClaimCheck("stale-release", f"stale-claim:{holder}")
        return ClaimCheck("not-claimable", f"held-by:{holder}")

    if STATUS_CLAIMABLE not in labels:
        return ClaimCheck("not-claimable", "not-status-claimable")

    return ClaimCheck("proceed", "claimable")


def verify_after_acquire(labels: Iterable[str], agent_id: str) -> ClaimCheck:
    """Spec §8's re-verify-after-acquire mitigation. Partial, not atomic — see the module
    docstring."""
    held_by = claimants(labels)
    if agent_id not in held_by:
        return ClaimCheck("not-claimable", "claim-lost")
    if len(held_by) == 1:
        return ClaimCheck("proceed", "sole-claimant")
    winner = min(held_by)
    if winner == agent_id:
        return ClaimCheck("proceed", "won-conflict-tiebreak")
    return ClaimCheck("release-and-retry", f"lost-conflict-tiebreak-to:{winner}")


def claim_labels(agent_id: str, now: datetime, labels: Iterable[str] = ()) -> tuple[str, ...]:
    expiry = now + ttl(labels)
    return (
        STATUS_CLAIMED,
        f"{CLAIMED_BY_PREFIX}{agent_id}",
        f"{CLAIM_EXPIRES_PREFIX}{expiry.isoformat()}",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_autonomous_mode.py -v`
Expected: PASS (82 tests total)

- [ ] **Step 5: Commit**

```bash
git add tools/autonomous_mode/claim.py tests/test_autonomous_mode.py
git commit -m "feat: implement the claim lock protocol and re-verify-after-acquire

The kanban skill's lock protocol as pure label arithmetic, plus spec §8's
mitigation for its optimistic (explicitly non-atomic) concurrency: re-read the
claim labels right after acquiring, before any real work. Deliberately documented
as a partial mitigation, not a guarantee. A claimed issue with no parseable
expiry is stale, not permanently held — otherwise it strands with no recovery."
```

---

## Task 7: Merge-allowlist diff gate

**Files:**
- Create: `tools/autonomous_mode/merge_allowlist.py`
- Test: `tests/test_autonomous_mode.py` (append)

**Interfaces:**
- Consumes: `classify_paths` (Task 3), `matches_any` (Task 3), `MODE_LIVE`/`MODE_DRY_RUN` (Task 1).
- Produces: `MERGE_ALLOWLIST_PATTERNS: tuple[str, ...]`, `CI_SUCCESS: str`, `MergeVerdict` (frozen dataclass: `action: str`, `reason: str`, `outside_allowlist: tuple[str, ...]`), `merge_verdict(*, changed_paths: Iterable[str], ci_state: str, mode: str) -> MergeVerdict`.
- `MergeVerdict.action` is `"merge"` or `"needs-human-merge"` — these exact strings are consumed by Task 9.

Spec §7: checked against the **actual diff**, immediately before any `gh pr merge` call — never against the issue's label, which can be stale or wrong about what a fix actually touched. The allowlist is `docs/**` and `tests/**` and nothing else (Global Constraint 5).

**Rule order is load-bearing and tested as such:** dry-run → CI-green → empty-diff → protected-domain → allowlist. CI-green sits above the allowlist so a red run refuses *unconditionally* (spec §6 item 3, §10.2 scenario 3) and reports the honest reason rather than an allowlist complaint. Protected-domain sits above the allowlist so the refusal names the domain — the more actionable message — even though a protected path is necessarily outside the allowlist anyway.

**The "no production code in the same diff" half of spec §7's `tests/**` rule needs no separate implementation:** production code is by definition outside the allowlist, so a mixed `tests/` + `services/` diff already refuses on `outside-allowlist`. There is a test asserting exactly that, so nobody later adds a redundant second rule.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_autonomous_mode.py (append)
from tools.autonomous_mode import merge_allowlist as ma

LIVE = m.MODE_LIVE


def test_a_docs_only_green_diff_merges():
    v = ma.merge_verdict(changed_paths=["docs/a.md", "docs/sub/b.md"],
                         ci_state="success", mode=LIVE)
    assert v.action == "merge"
    assert v.reason == "allowlisted-and-ci-green"


def test_a_tests_only_green_diff_merges():
    v = ma.merge_verdict(changed_paths=["tests/test_x.py"], ci_state="success", mode=LIVE)
    assert v.action == "merge"


def test_production_code_in_the_diff_needs_a_human_merge():
    v = ma.merge_verdict(changed_paths=["docs/a.md", "services/analytics/trends.py"],
                         ci_state="success", mode=LIVE)
    assert v.action == "needs-human-merge"
    assert v.reason == "outside-allowlist"
    assert v.outside_allowlist == ("services/analytics/trends.py",)


def test_tests_plus_production_code_is_refused_by_the_allowlist_itself():
    """Spec §7's 'no production code in the same diff' needs no separate rule: production
    code is outside the allowlist by construction. Asserted so nobody adds a second one."""
    v = ma.merge_verdict(changed_paths=["tests/test_x.py", "services/analytics/trends.py"],
                         ci_state="success", mode=LIVE)
    assert v.action == "needs-human-merge"
    assert v.reason == "outside-allowlist"


@pytest.mark.parametrize("ci_state", ["failure", "pending", "error", "", "unknown"])
def test_a_non_green_ci_blocks_merge_unconditionally_even_for_a_docs_only_diff(ci_state):
    v = ma.merge_verdict(changed_paths=["docs/a.md"], ci_state=ci_state, mode=LIVE)
    assert v.action == "needs-human-merge"
    assert v.reason.startswith("ci-not-green:")


def test_ci_is_checked_before_the_allowlist_so_the_reason_is_honest():
    v = ma.merge_verdict(changed_paths=["services/analytics/trends.py"],
                         ci_state="failure", mode=LIVE)
    assert v.reason == "ci-not-green:failure"


def test_an_empty_diff_never_merges():
    v = ma.merge_verdict(changed_paths=[], ci_state="success", mode=LIVE)
    assert v.action == "needs-human-merge"
    assert v.reason == "empty-diff"


def test_a_protected_path_in_the_diff_names_the_domain_not_just_the_allowlist():
    v = ma.merge_verdict(changed_paths=["docs/a.md", "services/risk_manager.py"],
                         ci_state="success", mode=LIVE)
    assert v.action == "needs-human-merge"
    assert v.reason == "protected-domain:risk-limits-and-kill-switches"


def test_a_traversal_path_never_merges():
    v = ma.merge_verdict(changed_paths=["docs/../services/risk_manager.py"],
                         ci_state="success", mode=LIVE)
    assert v.action == "needs-human-merge"
    assert v.reason == "protected-domain:unresolvable-path"


def test_dry_run_never_merges_even_for_a_perfect_diff():
    v = ma.merge_verdict(changed_paths=["docs/a.md"], ci_state="success",
                         mode=m.MODE_DRY_RUN)
    assert v.action == "needs-human-merge"
    assert v.reason.startswith("dry-run-mode:")


def test_the_allowlist_is_exactly_docs_and_tests():
    """Global Constraint 5 as a test: widening this is a separate explicit decision, so a
    silent extra entry should break the build."""
    assert ma.MERGE_ALLOWLIST_PATTERNS == ("docs/**", "tests/**")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_autonomous_mode.py -v -k merge or allowlist`
Expected: FAIL with `ImportError: cannot import name 'merge_allowlist' from 'tools.autonomous_mode'`

- [ ] **Step 3: Write minimal implementation**

```python
# tools/autonomous_mode/merge_allowlist.py
"""Spec §7: the merge allowlist, checked against the ACTUAL diff.

Never against the issue's label — a label can be stale or simply wrong about what a fix
turned out to touch. This function is called immediately before any `gh pr merge`, with
the real changed-path list the agent just read from the PR.

The allowlist is `docs/**` and `tests/**`, and nothing else. Widening it is a separate,
explicit, per-category human decision (spec §7, §11); no code path here grants a category
this module does not literally list.

Rule order is load-bearing:

  dry-run -> ci-green -> empty-diff -> protected-domain -> allowlist

CI sits above the allowlist so a red run refuses unconditionally and reports the honest
reason (spec §6 item 3). Protected-domain sits above the allowlist so the refusal names
the domain, which is the more actionable message, even though a protected path is
necessarily outside the allowlist anyway — defence in depth over the claim-time gate,
which only ever saw a declared scope.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from tools.autonomous_mode.marker import MODE_LIVE
from tools.autonomous_mode.pathmatch import matches_any, normalize_path
from tools.autonomous_mode.protected_domains import classify_paths

MERGE_ALLOWLIST_PATTERNS: tuple[str, ...] = ("docs/**", "tests/**")

CI_SUCCESS = "success"


@dataclass(frozen=True)
class MergeVerdict:
    action: str  # "merge" | "needs-human-merge"
    reason: str
    outside_allowlist: tuple[str, ...]


def merge_verdict(*, changed_paths: Iterable[str], ci_state: str,
                  mode: str) -> MergeVerdict:
    paths = tuple(normalize_path(p) for p in changed_paths if p and p.strip())

    if mode != MODE_LIVE:
        return MergeVerdict("needs-human-merge", f"dry-run-mode:{mode}", ())

    if ci_state != CI_SUCCESS:
        return MergeVerdict("needs-human-merge", f"ci-not-green:{ci_state or 'unknown'}", ())

    if not paths:
        return MergeVerdict("needs-human-merge", "empty-diff", ())

    protected = classify_paths(paths)
    if protected.protected:
        return MergeVerdict(
            "needs-human-merge",
            f"protected-domain:{protected.domain}",
            (protected.matched_path,) if protected.matched_path else (),
        )

    outside = tuple(p for p in paths if matches_any(p, MERGE_ALLOWLIST_PATTERNS) is None)
    if outside:
        return MergeVerdict("needs-human-merge", "outside-allowlist", outside)

    return MergeVerdict("merge", "allowlisted-and-ci-green", ())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_autonomous_mode.py -v`
Expected: PASS (97 tests total — the parametrized CI test contributes 5)

- [ ] **Step 5: Commit**

```bash
git add tools/autonomous_mode/merge_allowlist.py tests/test_autonomous_mode.py
git commit -m "feat: gate merges on the actual diff against a docs/tests-only allowlist

Spec §7. Checked against real changed paths immediately before gh pr merge, never
against the issue label, which can be stale about what a fix turned out to touch.
Rule order is tested, not incidental: CI-green sits above the allowlist so a red
run refuses unconditionally with an honest reason, and protected-domain sits above
it so the refusal names the domain."
```

---

## Task 8: Pre-work iteration gate — ordering, proved

**Files:**
- Create: `tools/autonomous_mode/iteration.py`
- Test: `tests/test_autonomous_mode.py` (append)

**Interfaces:**
- Consumes: `is_claimable`, `verify_after_acquire` (Task 6); `scope_verdict` (Task 4); `route_for_issue`, `Route` (Task 5).
- Produces: `Stage` (str Enum: `CLAIM_CHECK`, `CLAIM_VERIFY`, `SCOPE_GATE`, `ROUTE`, `WORK`, `PUSH`, `CI`, `MERGE_GATE`, `RELEASE`), `Outcome` (str Enum: `NOT_CLAIMABLE`, `STALE_RELEASE_REQUIRED`, `RELEASED_ON_CONFLICT`, `REFUSED_NO_SCOPE`, `REFUSED_PROTECTED_DOMAIN`, `REFUSED_UNROUTABLE`, `CLEARED_TO_WORK`), `IterationInput` (frozen dataclass: `issue_number: int`, `labels: tuple[str, ...]`, `body: str`, `agent_id: str`, `now: datetime`, `done_issues: frozenset[int] = frozenset()`, `post_acquire_labels: tuple[str, ...] | None = None`), `IterationResult` (frozen dataclass: `outcome: Outcome`, `stages_reached: tuple[Stage, ...]`, `reason: str`, `route: Route | None = None`, `resolved_skill: str | None = None`, `scope_paths: tuple[str, ...] = ()`, `events: tuple[str, ...] = ()`), `plan_iteration(inp: IterationInput) -> IterationResult`.

This composes Tasks 4-6 into spec §4's loop steps 1-4, in exactly the spec's order: claim → re-verify → protected-domain/scope → route → work. `stages_reached` is what makes Global Constraint 3 provable rather than asserted: a refusal outcome must have `Stage.WORK not in result.stages_reached`. That single property is spec §6 item 2's whole point ("refused before any investigation or code change starts — stricter than 'won't auto-merge'"), and it is tested for every refusal path.

`events` names the kanban event-bus event types the agent must post for that outcome, in order (`claimed`, `released`, `blocked`, `stale-release` — all from `references/event-bus.md`'s table). Task 11's loop controller consumes exactly this tuple, which is how a repeated refusal reaches the outer-loop kill switch.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_autonomous_mode.py (append)
from tools.autonomous_mode import iteration as it

_OK_BODY = "## Scope\n- docs/a.md\n"


def _inp(**kw):
    base = dict(
        issue_number=1,
        labels=("status:claimable", "type:bug"),
        body=_OK_BODY,
        agent_id="agent-a",
        now=T0,
        done_issues=frozenset(),
        post_acquire_labels=("status:claimed", "claimed-by:agent-a",
                             f"claim-expires:{(T0 + timedelta(minutes=30)).isoformat()}"),
    )
    base.update(kw)
    return it.IterationInput(**base)


def test_a_clean_issue_is_cleared_to_work():
    r = it.plan_iteration(_inp())
    assert r.outcome is it.Outcome.CLEARED_TO_WORK
    assert r.stages_reached[-1] is it.Stage.WORK
    assert r.resolved_skill == "superpowers:systematic-debugging"
    assert r.scope_paths == ("docs/a.md",)
    assert r.events == ("claimed",)


def test_a_protected_domain_issue_never_reaches_the_work_stage():
    """Spec §6 item 2's whole point, as an assertion on stages, not on a message."""
    r = it.plan_iteration(_inp(body="## Scope\n- services/risk_manager.py\n"))
    assert r.outcome is it.Outcome.REFUSED_PROTECTED_DOMAIN
    assert it.Stage.WORK not in r.stages_reached
    assert it.Stage.ROUTE not in r.stages_reached
    assert r.reason == "protected-domain:risk-limits-and-kill-switches"
    assert r.events == ("blocked", "released")


def test_an_issue_with_no_declared_scope_never_reaches_the_work_stage():
    r = it.plan_iteration(_inp(body="## Problem\nno scope\n"))
    assert r.outcome is it.Outcome.REFUSED_NO_SCOPE
    assert it.Stage.WORK not in r.stages_reached


def test_an_unroutable_issue_never_reaches_the_work_stage():
    r = it.plan_iteration(_inp(labels=("status:claimable",)))
    assert r.outcome is it.Outcome.REFUSED_UNROUTABLE
    assert r.reason == "missing-type-label"
    assert it.Stage.WORK not in r.stages_reached
    assert it.Stage.ROUTE in r.stages_reached


def test_an_unclaimable_issue_stops_at_the_claim_check():
    r = it.plan_iteration(_inp(labels=("status:claimed", "claimed-by:agent-z",
                                       f"claim-expires:{(T0 + timedelta(minutes=30)).isoformat()}",
                                       "type:bug")))
    assert r.outcome is it.Outcome.NOT_CLAIMABLE
    assert r.stages_reached == (it.Stage.CLAIM_CHECK,)


def test_an_expired_claim_asks_for_a_stale_release_and_emits_that_event():
    r = it.plan_iteration(_inp(
        labels=("status:claimed", "claimed-by:agent-z",
                f"claim-expires:{(T0 - timedelta(minutes=1)).isoformat()}", "type:bug"),
    ))
    assert r.outcome is it.Outcome.STALE_RELEASE_REQUIRED
    assert r.events == ("stale-release",)
    assert it.Stage.WORK not in r.stages_reached


def test_a_lost_claim_conflict_releases_before_any_work():
    r = it.plan_iteration(_inp(
        post_acquire_labels=("status:claimed", "claimed-by:agent-a", "claimed-by:agent-A0"),
    ))
    assert r.outcome is it.Outcome.RELEASED_ON_CONFLICT
    assert r.events == ("released",)
    assert it.Stage.WORK not in r.stages_reached
    assert r.stages_reached == (it.Stage.CLAIM_CHECK, it.Stage.CLAIM_VERIFY)


def test_skipping_the_post_acquire_read_is_itself_a_refusal():
    """The re-verify step cannot be silently omitted: no post-acquire read, no work."""
    r = it.plan_iteration(_inp(post_acquire_labels=None))
    assert r.outcome is it.Outcome.NOT_CLAIMABLE
    assert r.reason == "no-post-acquire-read"
    assert it.Stage.WORK not in r.stages_reached


def test_stages_are_recorded_in_the_specs_own_order():
    r = it.plan_iteration(_inp())
    assert r.stages_reached == (
        it.Stage.CLAIM_CHECK, it.Stage.CLAIM_VERIFY,
        it.Stage.SCOPE_GATE, it.Stage.ROUTE, it.Stage.WORK,
    )


def test_no_refusal_outcome_ever_reaches_the_work_stage():
    """Blanket property over every refusal the gate can produce."""
    cases = [
        _inp(body="## Scope\n- services/risk_manager.py\n"),
        _inp(body="## Problem\nno scope\n"),
        _inp(labels=("status:claimable",)),
        _inp(labels=("status:claimable", "agent-skip", "type:bug")),
        _inp(labels=("status:claimable", "type:bug", "depends-on:#4")),
        _inp(post_acquire_labels=None),
        _inp(post_acquire_labels=("status:claimed", "claimed-by:someone-else")),
    ]
    for case in cases:
        r = it.plan_iteration(case)
        assert r.outcome is not it.Outcome.CLEARED_TO_WORK
        assert it.Stage.WORK not in r.stages_reached
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_autonomous_mode.py -v -k iteration or stage or work_stage`
Expected: FAIL with `ImportError: cannot import name 'iteration' from 'tools.autonomous_mode'`

- [ ] **Step 3: Write minimal implementation**

```python
# tools/autonomous_mode/iteration.py
"""Spec §4's loop, steps 1-4, as a pure state machine.

The point of modelling this rather than leaving it to the agent's prompt is
`stages_reached`: spec §6 item 2 requires a protected-domain issue to be refused "before
any investigation or code change starts — stricter than 'won't auto-merge'". That claim is
only checkable if the gate records how far it got, so every refusal can be asserted to
have never reached `Stage.WORK`. A message saying "refused" proves nothing; a stage list
that stops before WORK does.

`events` names the kanban event-bus types the agent must post for the outcome, in order,
drawn from references/event-bus.md's table. Task 11's LoopController consumes this exact
tuple, which is how repeated refusals reach the outer-loop kill switch (spec §8).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from tools.autonomous_mode.claim import is_claimable, verify_after_acquire
from tools.autonomous_mode.issue_scope import scope_verdict
from tools.autonomous_mode.routing import Route, route_for_issue


class Stage(str, Enum):
    CLAIM_CHECK = "claim-check"
    CLAIM_VERIFY = "claim-verify"
    SCOPE_GATE = "scope-gate"
    ROUTE = "route"
    WORK = "work"
    PUSH = "push"
    CI = "ci"
    MERGE_GATE = "merge-gate"
    RELEASE = "release"


class Outcome(str, Enum):
    NOT_CLAIMABLE = "not-claimable"
    STALE_RELEASE_REQUIRED = "stale-release-required"
    RELEASED_ON_CONFLICT = "released-on-conflict"
    REFUSED_NO_SCOPE = "refused-no-scope"
    REFUSED_PROTECTED_DOMAIN = "refused-protected-domain"
    REFUSED_UNROUTABLE = "refused-unroutable"
    CLEARED_TO_WORK = "cleared-to-work"


@dataclass(frozen=True)
class IterationInput:
    issue_number: int
    labels: tuple[str, ...]
    body: str
    agent_id: str
    now: datetime
    done_issues: frozenset[int] = frozenset()
    post_acquire_labels: tuple[str, ...] | None = None


@dataclass(frozen=True)
class IterationResult:
    outcome: Outcome
    stages_reached: tuple[Stage, ...]
    reason: str
    route: Route | None = None
    resolved_skill: str | None = None
    scope_paths: tuple[str, ...] = ()
    events: tuple[str, ...] = ()


def plan_iteration(inp: IterationInput) -> IterationResult:
    stages: list[Stage] = [Stage.CLAIM_CHECK]

    check = is_claimable(inp.labels, inp.now, inp.done_issues)
    if check.action == "not-claimable":
        return IterationResult(Outcome.NOT_CLAIMABLE, tuple(stages), check.reason)
    if check.action == "stale-release":
        return IterationResult(
            Outcome.STALE_RELEASE_REQUIRED, tuple(stages), check.reason,
            events=("stale-release",),
        )

    stages.append(Stage.CLAIM_VERIFY)
    if inp.post_acquire_labels is None:
        return IterationResult(Outcome.NOT_CLAIMABLE, tuple(stages), "no-post-acquire-read")
    verify = verify_after_acquire(inp.post_acquire_labels, inp.agent_id)
    if verify.action == "release-and-retry":
        return IterationResult(
            Outcome.RELEASED_ON_CONFLICT, tuple(stages), verify.reason, events=("released",),
        )
    if verify.action != "proceed":
        return IterationResult(
            Outcome.NOT_CLAIMABLE, tuple(stages), verify.reason, events=("released",),
        )

    stages.append(Stage.SCOPE_GATE)
    scope = scope_verdict(inp.body)
    if not scope.claimable:
        outcome = (
            Outcome.REFUSED_PROTECTED_DOMAIN
            if (scope.refusal or "").startswith("protected-domain:")
            else Outcome.REFUSED_NO_SCOPE
        )
        return IterationResult(
            outcome, tuple(stages), scope.refusal or "unclaimable-scope",
            scope_paths=scope.paths, events=("blocked", "released"),
        )

    stages.append(Stage.ROUTE)
    routing = route_for_issue(inp.labels, inp.body)
    if not routing.claimable:
        return IterationResult(
            Outcome.REFUSED_UNROUTABLE, tuple(stages), routing.refusal or "unroutable",
            scope_paths=scope.paths, events=("blocked", "released"),
        )

    stages.append(Stage.WORK)
    return IterationResult(
        Outcome.CLEARED_TO_WORK, tuple(stages), "cleared",
        route=routing.route, resolved_skill=routing.resolved_skill,
        scope_paths=scope.paths, events=("claimed",),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_autonomous_mode.py -v`
Expected: PASS (107 tests total)

- [ ] **Step 5: Commit**

```bash
git add tools/autonomous_mode/iteration.py tests/test_autonomous_mode.py
git commit -m "feat: compose the pre-work gates into a stage-recording state machine

Spec §4 steps 1-4 in the spec's own order, with stages_reached so §6 item 2's
'refused before any investigation or code change starts' is provable rather than
asserted: every refusal path is tested to never reach Stage.WORK. Skipping the
post-acquire re-read is itself a refusal — the mitigation cannot be omitted."
```

---

## Task 9: Post-work completion gate — the design stop and the merge decision

**Files:**
- Modify: `tools/autonomous_mode/iteration.py`
- Test: `tests/test_autonomous_mode.py` (append)

**Interfaces:**
- Consumes: `Stage` (Task 8), `merge_verdict` (Task 7).
- Produces: `CompletionOutcome` (str Enum: `AWAITING_HUMAN_DESIGN_APPROVAL`, `BLOCKED`, `MERGED`, `NEEDS_HUMAN_MERGE`), `CompletionInput` (frozen dataclass: `issue_number: int`, `changed_paths: tuple[str, ...]`, `ci_state: str`, `mode: str`, `design_proposal_only: bool = False`, `blocked_reason: str | None = None`), `CompletionResult` (frozen dataclass: `outcome: CompletionOutcome`, `stages_reached: tuple[Stage, ...]`, `reason: str`, `outside_allowlist: tuple[str, ...] = ()`, `events: tuple[str, ...] = ()`), `plan_completion(inp: CompletionInput) -> CompletionResult`.

This is spec §4's loop steps 6-9 plus **spec §6 item 1's hard gate as a terminal state**. `AWAITING_HUMAN_DESIGN_APPROVAL` is deliberately terminal: nothing in this module transitions out of it, so "the agent proposes a design and genuinely stops" is a structural property of the state machine, not a hope about the prompt.

**On what is and isn't testable here — say this out loud rather than pretending otherwise.** Spec §6 item 1 is a *behavioral* requirement: it asks the agent to notice that a claimed issue needs a genuinely new architectural decision. No function can decide that for it. What this task can and does make mechanical is the consequence: once `design_proposal_only=True` is set, the loop provably cannot push, cannot merge, and cannot self-approve. The judgment that sets the flag is enforced two other ways — Task 5's `Route.requires_human_approval` makes `type:feature`/`type:design` structurally approval-requiring, and Task 13's prompt-contract test asserts the agent prompt states the gate with no size-based exception. Task 14's fault-injection scenario 6 exercises the whole chain. Three partial enforcements, honestly labelled, beat one imaginary complete one.

The design stop emits a `blocked` event, which by Task 11's rules counts toward the outer-loop kill switch's "two consecutive blocked events" trigger. That is intended: two design proposals in a row means the queue is full of work autonomous mode cannot finish unattended, and halting to let a human look is the correct response, not a bug.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_autonomous_mode.py (append)
def _comp(**kw):
    base = dict(
        issue_number=1,
        changed_paths=("docs/a.md",),
        ci_state="success",
        mode=m.MODE_LIVE,
        design_proposal_only=False,
        blocked_reason=None,
    )
    base.update(kw)
    return it.CompletionInput(**base)


def test_a_design_proposal_stops_before_push_and_never_merges():
    """Spec §6 item 1: the agent posts the design and stops. Terminal by construction."""
    r = it.plan_completion(_comp(design_proposal_only=True))
    assert r.outcome is it.CompletionOutcome.AWAITING_HUMAN_DESIGN_APPROVAL
    assert it.Stage.PUSH not in r.stages_reached
    assert it.Stage.MERGE_GATE not in r.stages_reached
    assert r.events == ("progress", "blocked")


def test_the_design_stop_holds_even_for_a_perfectly_allowlisted_green_diff():
    """No size-based or 'it was only docs' exception (spec §6 item 1, §12)."""
    r = it.plan_completion(_comp(design_proposal_only=True,
                                 changed_paths=("docs/a.md",), ci_state="success"))
    assert r.outcome is it.CompletionOutcome.AWAITING_HUMAN_DESIGN_APPROVAL


def test_a_blocked_iteration_stops_before_push():
    r = it.plan_completion(_comp(blocked_reason="upstream-api-down"))
    assert r.outcome is it.CompletionOutcome.BLOCKED
    assert r.reason == "blocked:upstream-api-down"
    assert it.Stage.PUSH not in r.stages_reached
    assert r.events == ("blocked",)


def test_an_allowlisted_green_diff_merges_and_releases():
    r = it.plan_completion(_comp())
    assert r.outcome is it.CompletionOutcome.MERGED
    assert r.stages_reached == (it.Stage.WORK, it.Stage.PUSH, it.Stage.CI,
                                it.Stage.MERGE_GATE, it.Stage.RELEASE)
    assert r.events == ("result",)


def test_a_non_allowlisted_diff_opens_a_pr_and_stops_at_the_merge_gate():
    r = it.plan_completion(_comp(changed_paths=("services/analytics/trends.py",)))
    assert r.outcome is it.CompletionOutcome.NEEDS_HUMAN_MERGE
    assert r.reason == "outside-allowlist"
    assert r.outside_allowlist == ("services/analytics/trends.py",)
    assert it.Stage.RELEASE not in r.stages_reached


def test_a_red_ci_run_blocks_merge_even_for_a_docs_only_diff():
    r = it.plan_completion(_comp(ci_state="failure"))
    assert r.outcome is it.CompletionOutcome.NEEDS_HUMAN_MERGE
    assert r.reason == "ci-not-green:failure"


def test_the_design_gate_is_checked_before_the_blocked_reason():
    """Both set: the design stop is the more specific, more binding outcome."""
    r = it.plan_completion(_comp(design_proposal_only=True, blocked_reason="whatever"))
    assert r.outcome is it.CompletionOutcome.AWAITING_HUMAN_DESIGN_APPROVAL
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_autonomous_mode.py -v -k completion or design or merge_gate`
Expected: FAIL with `AttributeError: module 'tools.autonomous_mode.iteration' has no attribute 'plan_completion'`

- [ ] **Step 3: Write minimal implementation**

```python
# tools/autonomous_mode/iteration.py (append)
from tools.autonomous_mode.merge_allowlist import merge_verdict


class CompletionOutcome(str, Enum):
    AWAITING_HUMAN_DESIGN_APPROVAL = "awaiting-human-design-approval"
    BLOCKED = "blocked"
    MERGED = "merged"
    NEEDS_HUMAN_MERGE = "needs-human-merge"


@dataclass(frozen=True)
class CompletionInput:
    issue_number: int
    changed_paths: tuple[str, ...]
    ci_state: str
    mode: str
    design_proposal_only: bool = False
    blocked_reason: str | None = None


@dataclass(frozen=True)
class CompletionResult:
    outcome: CompletionOutcome
    stages_reached: tuple[Stage, ...]
    reason: str
    outside_allowlist: tuple[str, ...] = ()
    events: tuple[str, ...] = ()


def plan_completion(inp: CompletionInput) -> CompletionResult:
    """Spec §4 loop steps 6-9, with §6 item 1's hard gate as a TERMINAL state.

    Nothing here transitions out of AWAITING_HUMAN_DESIGN_APPROVAL: "proposes a design and
    stops" is structural, not a hope about the prompt. The judgment that sets
    `design_proposal_only` is the agent's — no function can decide whether an issue needs a
    genuinely new architectural decision — but its consequence is mechanical, and
    `Route.requires_human_approval` plus the prompt-contract test cover the other two
    thirds of that enforcement.
    """
    if inp.design_proposal_only:
        return CompletionResult(
            CompletionOutcome.AWAITING_HUMAN_DESIGN_APPROVAL,
            (Stage.WORK,),
            "brainstorming-gate:design-proposal-posted-awaiting-human-approval",
            events=("progress", "blocked"),
        )

    if inp.blocked_reason:
        return CompletionResult(
            CompletionOutcome.BLOCKED, (Stage.WORK,),
            f"blocked:{inp.blocked_reason}", events=("blocked",),
        )

    stages = [Stage.WORK, Stage.PUSH, Stage.CI, Stage.MERGE_GATE]
    verdict = merge_verdict(
        changed_paths=inp.changed_paths, ci_state=inp.ci_state, mode=inp.mode,
    )
    if verdict.action == "merge":
        stages.append(Stage.RELEASE)
        return CompletionResult(
            CompletionOutcome.MERGED, tuple(stages), verdict.reason, events=("result",),
        )
    return CompletionResult(
        CompletionOutcome.NEEDS_HUMAN_MERGE, tuple(stages), verdict.reason,
        outside_allowlist=verdict.outside_allowlist, events=("result",),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_autonomous_mode.py -v`
Expected: PASS (114 tests total)

- [ ] **Step 5: Commit**

```bash
git add tools/autonomous_mode/iteration.py tests/test_autonomous_mode.py
git commit -m "feat: add the completion gate with a terminal design-approval state

Spec §4 steps 6-9 plus §6 item 1's hard gate. AWAITING_HUMAN_DESIGN_APPROVAL has
no outgoing transition, so 'proposes a design and genuinely stops' is structural
rather than a hope about the prompt. The judgment that sets the flag stays the
agent's — no function can decide whether an issue needs a new architectural
decision — but its consequence is mechanical."
```

---

## Task 10: Dry-run mode

**Files:**
- Modify: `tools/autonomous_mode/iteration.py`
- Test: `tests/test_autonomous_mode.py` (append)

**Interfaces:**
- Modifies: `CompletionOutcome` gains one member, `DRY_RUN_STOPPED = "dry-run-stopped"`. `plan_completion`'s signature is unchanged (`mode` is already on `CompletionInput`).
- Consumes: `MODE_LIVE` (Task 1), `merge_verdict` (Task 7).

Spec §10.1: "The agent claims and investigates/plans normally but stops before any push/merge, reporting only what it *would* do. Lets the user sanity-check routing and judgment against real issues with zero write risk, before autonomous mode is ever allowed to act for real."

Two halves, both tested. **Zero write risk:** in any mode other than `live`, `plan_completion` returns before `Stage.PUSH` unconditionally — including for a docs-only, CI-green, perfectly allowlisted diff, which is exactly the case a weaker implementation would let slip. **Reporting what it would do:** the dry-run branch re-runs `merge_verdict` with `mode=MODE_LIVE` and reports the hypothetical decision, so the user sees `would-merge` / `would-need-human-merge` and the reason behind it rather than a content-free "dry run finished."

`merge_allowlist.merge_verdict`'s own dry-run branch (Task 7) becomes unreachable *from `plan_completion`* once this lands, because the earlier return catches it. Keep it: `merge_verdict` is also called directly from the CLI (Task 12), and a second independent refusal on the same condition is defence in depth, not dead code. There is a test pinning both.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_autonomous_mode.py (append)
@pytest.mark.parametrize("changed,ci,expected_preview", [
    (("docs/a.md",), "success", "would-merge"),
    (("tests/test_x.py",), "success", "would-merge"),
    (("services/analytics/trends.py",), "success", "would-need-human-merge"),
    (("docs/a.md",), "failure", "would-need-human-merge"),
    (("services/risk_manager.py",), "success", "would-need-human-merge"),
    ((), "success", "would-need-human-merge"),
])
def test_dry_run_never_pushes_and_previews_the_live_decision(changed, ci, expected_preview):
    r = it.plan_completion(_comp(mode=m.MODE_DRY_RUN, changed_paths=changed, ci_state=ci))
    assert r.outcome is it.CompletionOutcome.DRY_RUN_STOPPED
    assert it.Stage.PUSH not in r.stages_reached
    assert it.Stage.MERGE_GATE not in r.stages_reached
    assert it.Stage.RELEASE not in r.stages_reached
    assert expected_preview in r.reason


def test_dry_run_preview_carries_the_live_reason_not_just_a_verdict():
    r = it.plan_completion(_comp(mode=m.MODE_DRY_RUN,
                                 changed_paths=("services/analytics/trends.py",)))
    assert "outside-allowlist" in r.reason
    assert r.outside_allowlist == ("services/analytics/trends.py",)


def test_dry_run_emits_a_progress_event_not_a_result_event():
    """A dry run reports; it does not deliver. `result` would imply work landed."""
    r = it.plan_completion(_comp(mode=m.MODE_DRY_RUN))
    assert r.events == ("progress",)


def test_the_design_gate_still_wins_over_dry_run():
    r = it.plan_completion(_comp(mode=m.MODE_DRY_RUN, design_proposal_only=True))
    assert r.outcome is it.CompletionOutcome.AWAITING_HUMAN_DESIGN_APPROVAL


def test_merge_verdict_keeps_its_own_independent_dry_run_refusal():
    """Defence in depth: plan_completion returns first, but the CLI calls merge_verdict
    directly, so its own mode check must stay."""
    v = ma.merge_verdict(changed_paths=["docs/a.md"], ci_state="success",
                         mode=m.MODE_DRY_RUN)
    assert v.action == "needs-human-merge"


def test_an_unknown_mode_is_treated_as_dry_run_not_as_live():
    """Fail closed on a typo: anything that is not exactly 'live' cannot push."""
    r = it.plan_completion(_comp(mode="Live"))
    assert r.outcome is it.CompletionOutcome.DRY_RUN_STOPPED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_autonomous_mode.py -v -k dry_run`
Expected: FAIL with `AttributeError: DRY_RUN_STOPPED`

- [ ] **Step 3: Write minimal implementation**

Add one member to `CompletionOutcome` in `tools/autonomous_mode/iteration.py`:

```python
class CompletionOutcome(str, Enum):
    AWAITING_HUMAN_DESIGN_APPROVAL = "awaiting-human-design-approval"
    BLOCKED = "blocked"
    DRY_RUN_STOPPED = "dry-run-stopped"
    MERGED = "merged"
    NEEDS_HUMAN_MERGE = "needs-human-merge"
```

Add the import and the dry-run branch to `plan_completion`, immediately after the
`blocked_reason` branch and before the `stages = [...]` line:

```python
# tools/autonomous_mode/iteration.py — add to the imports
from tools.autonomous_mode.marker import MODE_LIVE
```

```python
    # Spec §10.1: zero write risk. Anything that is not exactly MODE_LIVE stops here,
    # before Stage.PUSH, including a docs-only CI-green diff that live mode would happily
    # merge — that is precisely the case a weaker check would let slip. The preview re-runs
    # the gate as if live so the user sees the real decision and its reason, not a
    # content-free "dry run finished".
    if inp.mode != MODE_LIVE:
        hypothetical = merge_verdict(
            changed_paths=inp.changed_paths, ci_state=inp.ci_state, mode=MODE_LIVE,
        )
        would = "would-merge" if hypothetical.action == "merge" else "would-need-human-merge"
        return CompletionResult(
            CompletionOutcome.DRY_RUN_STOPPED,
            (Stage.WORK,),
            f"dry-run-mode:{inp.mode}:{would}:{hypothetical.reason}",
            outside_allowlist=hypothetical.outside_allowlist,
            events=("progress",),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_autonomous_mode.py -v`
Expected: PASS (125 tests total — the parametrized dry-run test contributes 6)

- [ ] **Step 5: Commit**

```bash
git add tools/autonomous_mode/iteration.py tests/test_autonomous_mode.py
git commit -m "feat: add dry-run mode with a live-decision preview

Spec §10.1. Anything that is not exactly 'live' returns before Stage.PUSH — tested
against a docs-only CI-green diff specifically, since that is the case a weaker
check would let through. The preview re-runs the merge gate as if live so a dry run
reports the real decision and its reason instead of finishing silently."
```

---

## Task 11: Outer-loop controller — the second kill switch

**Files:**
- Create: `tools/autonomous_mode/loop_control.py`
- Test: `tests/test_autonomous_mode.py` (append)

**Interfaces:**
- Consumes: nothing from earlier tasks at import time; `record_iteration` accepts the `events` tuple produced by `plan_iteration`/`plan_completion` (Tasks 8-10).
- Produces: `CONSECUTIVE_BLOCKED_LIMIT: int`, `LoopDecision` (frozen dataclass: `action: str` — `"continue"` or `"halt"` — and `reason: str`), `LoopController` (class: `__init__(self, *, consecutive_blocked_limit: int = CONSECUTIVE_BLOCKED_LIMIT)`, properties `halted: bool` / `halt_reason: str` / `consecutive_blocked: int`, methods `record_event(event_type: str) -> LoopDecision`, `record_iteration(events: Iterable[str]) -> LoopDecision`, `user_override() -> LoopDecision`, `session_end() -> LoopDecision`).

Spec §8's "second kill switch, inherited from the skill itself": the kanban skill's YOLO auto-disable triggers (two consecutive blocked events, any error during a YOLO dispatch, explicit user override, session end) stop the **outer loop**, not just the current issue's dispatch — "a degraded run halts itself rather than continuing to claim more issues in a bad state."

The important, easily-missed property, and the one this task exists to make true: **halting is sticky and applies to the loop, not the iteration.** Once halted, every subsequent `record_event`/`record_iteration` still returns `halt` with the original reason. A controller that reset on the next iteration would satisfy a naive reading of "auto-disable" while defeating the entire point.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_autonomous_mode.py (append)
from tools.autonomous_mode import loop_control as lc


def test_a_healthy_run_continues():
    c = lc.LoopController()
    assert c.record_event("claimed").action == "continue"
    assert c.record_event("result").action == "continue"
    assert c.halted is False


def test_two_consecutive_blocked_events_halt_the_outer_loop():
    c = lc.LoopController()
    assert c.record_event("blocked").action == "continue"
    decision = c.record_event("blocked")
    assert decision.action == "halt"
    assert decision.reason == "consecutive-blocked-events:2"
    assert c.halted is True


def test_a_successful_iteration_between_two_blocks_resets_the_counter():
    c = lc.LoopController()
    c.record_event("blocked")
    c.record_event("result")
    assert c.record_event("blocked").action == "continue"
    assert c.halted is False


def test_a_single_error_event_halts_immediately():
    c = lc.LoopController()
    decision = c.record_event("error")
    assert decision.action == "halt"
    assert decision.reason == "error-event-during-dispatch"


def test_a_halt_is_sticky_and_keeps_its_original_reason():
    """The whole point of an OUTER-loop kill switch: it does not reset next iteration."""
    c = lc.LoopController()
    c.record_event("error")
    for _ in range(5):
        decision = c.record_event("result")
        assert decision.action == "halt"
        assert decision.reason == "error-event-during-dispatch"


def test_an_explicit_user_override_halts():
    c = lc.LoopController()
    decision = c.user_override()
    assert decision.action == "halt"
    assert decision.reason == "user-override"


def test_session_end_halts():
    c = lc.LoopController()
    assert c.session_end().reason == "session-end"


def test_record_iteration_consumes_an_iteration_results_event_tuple():
    c = lc.LoopController()
    assert c.record_iteration(("blocked", "released")).action == "continue"
    assert c.record_iteration(("blocked", "released")).action == "halt"


def test_two_consecutive_design_stops_halt_the_outer_loop():
    """Intended, not a bug: a queue full of work autonomous mode cannot finish unattended
    should stop and let a human look."""
    c = lc.LoopController()
    stop = it.plan_completion(_comp(design_proposal_only=True))
    assert c.record_iteration(stop.events).action == "continue"
    assert c.record_iteration(stop.events).action == "halt"


def test_an_unknown_event_type_is_ignored_rather_than_treated_as_a_failure():
    """references/event-bus.md: consumers MUST ignore unknown types rather than erroring."""
    c = lc.LoopController()
    assert c.record_event("some-future-v0.2-event").action == "continue"
    assert c.halted is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_autonomous_mode.py -v -k loop_control or halt or blocked_events`
Expected: FAIL with `ImportError: cannot import name 'loop_control' from 'tools.autonomous_mode'`

- [ ] **Step 3: Write minimal implementation**

```python
# tools/autonomous_mode/loop_control.py
"""Spec §8's "second kill switch": the kanban skill's YOLO auto-disable triggers stop the
OUTER loop, not just the current issue's dispatch.

Triggers, from the skill's references/yolo-mode.md: two consecutive blocked events, any
error event during a dispatch, an explicit user override, session end. Spec §8's addition
is the scope — "a degraded run halts itself rather than continuing to claim more issues in
a bad state."

The property that makes this a kill switch rather than a speed bump: **halting is sticky**.
Once halted, every later call still returns halt with the original reason. A controller
that reset on the next iteration would satisfy a naive reading of "auto-disable" while
defeating the point entirely.

Unknown event types are ignored, per references/event-bus.md ("consumers MUST ignore
unknown types rather than erroring") — a v0.2 event type must not halt a healthy loop.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

CONSECUTIVE_BLOCKED_LIMIT = 2

_BLOCKED = "blocked"
_ERROR = "error"
_RESET_EVENTS = frozenset({"claimed", "result", "progress", "released", "stale-release"})


@dataclass(frozen=True)
class LoopDecision:
    action: str  # "continue" | "halt"
    reason: str


class LoopController:
    def __init__(self, *, consecutive_blocked_limit: int = CONSECUTIVE_BLOCKED_LIMIT) -> None:
        self._limit = consecutive_blocked_limit
        self._consecutive_blocked = 0
        self._halted = False
        self._halt_reason = ""

    @property
    def halted(self) -> bool:
        return self._halted

    @property
    def halt_reason(self) -> str:
        return self._halt_reason

    @property
    def consecutive_blocked(self) -> int:
        return self._consecutive_blocked

    def _halt(self, reason: str) -> LoopDecision:
        if not self._halted:
            self._halted = True
            self._halt_reason = reason
        return LoopDecision("halt", self._halt_reason)

    def _decision(self) -> LoopDecision:
        if self._halted:
            return LoopDecision("halt", self._halt_reason)
        return LoopDecision("continue", "ok")

    def record_event(self, event_type: str) -> LoopDecision:
        if self._halted:
            return self._decision()
        if event_type == _ERROR:
            return self._halt("error-event-during-dispatch")
        if event_type == _BLOCKED:
            self._consecutive_blocked += 1
            if self._consecutive_blocked >= self._limit:
                return self._halt(f"consecutive-blocked-events:{self._consecutive_blocked}")
            return self._decision()
        if event_type in _RESET_EVENTS:
            self._consecutive_blocked = 0
        return self._decision()

    def record_iteration(self, events: Iterable[str]) -> LoopDecision:
        """Feed one iteration's event tuple (IterationResult.events / CompletionResult.events).
        A `blocked` anywhere in the tuple counts once for that iteration; a trailing
        `released` in the same tuple must not reset the counter it just incremented."""
        events = tuple(events)
        if _ERROR in events:
            return self._halt("error-event-during-dispatch")
        if _BLOCKED in events:
            return self.record_event(_BLOCKED)
        for event in events:
            self.record_event(event)
        return self._decision()

    def user_override(self) -> LoopDecision:
        return self._halt("user-override")

    def session_end(self) -> LoopDecision:
        return self._halt("session-end")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_autonomous_mode.py -v`
Expected: PASS (135 tests total)

- [ ] **Step 5: Commit**

```bash
git add tools/autonomous_mode/loop_control.py tests/test_autonomous_mode.py
git commit -m "feat: halt the outer loop on the kanban skill's auto-disable triggers

Spec §8's second kill switch, with the property that makes it one: halting is
sticky and scoped to the loop, not the iteration. A controller that reset next
iteration would satisfy a naive reading of 'auto-disable' while defeating the
point. record_iteration counts one blocked per iteration so a trailing 'released'
in the same event tuple cannot reset the counter it just incremented."
```

---

## Task 12: CLI verdict surface

**Files:**
- Create: `tools/autonomous_mode/__main__.py`
- Test: `tests/test_autonomous_mode_cli.py`

**Interfaces:**
- Consumes: every module from Tasks 1-11.
- Produces: `EXIT_OK = 0`, `EXIT_REFUSED = 2`, `EXIT_USAGE = 1`, `build_parser() -> argparse.ArgumentParser`, `main(argv: list[str] | None = None) -> int`. Seven subcommands: `preflight`, `marker-start`, `marker-stop`, `marker-status`, `iteration`, `completion`, `merge-check`.

**This is the load-bearing task for the whole design, and it is worth saying why.** Every gate built so far is a Python function, but the thing that actually runs autonomous mode is an agent following a prompt. If the agent's only interface to these gates were "remember to think about protected domains," they would be documentation, not gates. The CLI is what makes them mechanical: the agent runs `python -m tools.autonomous_mode iteration ...` and gets a JSON verdict plus a **non-zero exit code** on refusal, which is visible even if it ignores the JSON entirely.

Exit codes are therefore part of the contract, not decoration: `0` means "proceed with what you were about to do", `2` means "refused — do not proceed", `1` means the invocation itself was malformed. For `completion`, `0` is reserved for `MERGED` specifically: the agent merges only on a zero exit.

Per Global Constraint 6, this module invokes no `gh` and no git — every input is state the agent has already read and passes in. `--body-file` and `--changed-file` take paths rather than inline strings so markdown and path lists survive shell quoting intact.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_autonomous_mode_cli.py
"""The CLI is what turns the gate functions into gates: the agent gets a JSON verdict AND
a non-zero exit code on refusal, so a refusal is visible even if it ignores the payload."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools.autonomous_mode.__main__ import EXIT_OK, EXIT_REFUSED, EXIT_USAGE, main

T0 = datetime(2026, 8, 26, 12, 0, 0, tzinfo=timezone.utc)
CLAIM_EXPIRES = f"claim-expires:{(T0 + timedelta(minutes=30)).isoformat()}"


def _run(capsys, argv):
    code = main(argv)
    out = capsys.readouterr().out
    return code, json.loads(out) if out.strip() else {}


def _body(tmp_path, text="## Scope\n- docs/a.md\n"):
    path = tmp_path / "body.md"
    path.write_text(text)
    return str(path)


def _changed(tmp_path, paths):
    path = tmp_path / "changed.txt"
    path.write_text("\n".join(paths) + "\n")
    return str(path)


def test_preflight_on_an_initiative_branch_exits_zero(capsys, tmp_path):
    code, payload = _run(capsys, [
        "preflight", "--branch", "feat/x", "--worktree", str(tmp_path), "--mode", "dry-run",
    ])
    assert code == EXIT_OK
    assert payload["ok"] is True


def test_preflight_on_main_exits_refused(capsys, tmp_path):
    code, payload = _run(capsys, [
        "preflight", "--branch", "main", "--worktree", str(tmp_path), "--mode", "dry-run",
    ])
    assert code == EXIT_REFUSED
    assert "refuses-to-run-on-main" in payload["reasons"]


def test_marker_start_status_stop_round_trip(capsys, tmp_path):
    code, payload = _run(capsys, [
        "marker-start", "--worktree", str(tmp_path), "--agent-id", "agent-a",
        "--mode", "dry-run", "--now", T0.isoformat(),
    ])
    assert code == EXIT_OK
    assert payload["status"] == "running"

    code, payload = _run(capsys, ["marker-status", "--worktree", str(tmp_path)])
    assert code == EXIT_OK
    assert payload["running"] is True
    assert payload["agent_id"] == "agent-a"

    code, payload = _run(capsys, [
        "marker-stop", "--worktree", str(tmp_path), "--now", T0.isoformat(),
    ])
    assert code == EXIT_OK
    assert payload["status"] == "stopped"

    code, payload = _run(capsys, ["marker-status", "--worktree", str(tmp_path)])
    assert payload["running"] is False


def test_marker_status_on_a_worktree_with_no_marker(capsys, tmp_path):
    code, payload = _run(capsys, ["marker-status", "--worktree", str(tmp_path)])
    assert code == EXIT_OK
    assert payload["running"] is False
    assert payload["marker"] is None


def test_iteration_clears_a_clean_issue(capsys, tmp_path):
    code, payload = _run(capsys, [
        "iteration", "--issue", "12",
        "--labels", "status:claimable,type:bug",
        "--body-file", _body(tmp_path),
        "--agent-id", "agent-a", "--now", T0.isoformat(),
        "--post-acquire-labels", f"status:claimed,claimed-by:agent-a,{CLAIM_EXPIRES}",
    ])
    assert code == EXIT_OK
    assert payload["outcome"] == "cleared-to-work"
    assert payload["resolved_skill"] == "superpowers:systematic-debugging"


def test_iteration_refuses_a_protected_domain_with_a_nonzero_exit(capsys, tmp_path):
    code, payload = _run(capsys, [
        "iteration", "--issue", "12",
        "--labels", "status:claimable,type:bug",
        "--body-file", _body(tmp_path, "## Scope\n- services/risk_manager.py\n"),
        "--agent-id", "agent-a", "--now", T0.isoformat(),
        "--post-acquire-labels", f"status:claimed,claimed-by:agent-a,{CLAIM_EXPIRES}",
    ])
    assert code == EXIT_REFUSED
    assert payload["outcome"] == "refused-protected-domain"
    assert "work" not in payload["stages_reached"]


def test_completion_exits_zero_only_when_the_merge_is_allowed(capsys, tmp_path):
    code, payload = _run(capsys, [
        "completion", "--issue", "12", "--changed-file", _changed(tmp_path, ["docs/a.md"]),
        "--ci-state", "success", "--mode", "live",
    ])
    assert code == EXIT_OK
    assert payload["outcome"] == "merged"


def test_completion_exits_refused_for_a_non_allowlisted_diff(capsys, tmp_path):
    code, payload = _run(capsys, [
        "completion", "--issue", "12",
        "--changed-file", _changed(tmp_path, ["services/analytics/trends.py"]),
        "--ci-state", "success", "--mode", "live",
    ])
    assert code == EXIT_REFUSED
    assert payload["outcome"] == "needs-human-merge"


def test_completion_in_dry_run_exits_refused_and_previews(capsys, tmp_path):
    code, payload = _run(capsys, [
        "completion", "--issue", "12", "--changed-file", _changed(tmp_path, ["docs/a.md"]),
        "--ci-state", "success", "--mode", "dry-run",
    ])
    assert code == EXIT_REFUSED
    assert payload["outcome"] == "dry-run-stopped"
    assert "would-merge" in payload["reason"]


def test_completion_design_proposal_exits_refused(capsys, tmp_path):
    code, payload = _run(capsys, [
        "completion", "--issue", "12", "--changed-file", _changed(tmp_path, ["docs/a.md"]),
        "--ci-state", "success", "--mode", "live", "--design-proposal-only",
    ])
    assert code == EXIT_REFUSED
    assert payload["outcome"] == "awaiting-human-design-approval"


def test_merge_check_is_callable_standalone(capsys, tmp_path):
    code, payload = _run(capsys, [
        "merge-check", "--changed-file", _changed(tmp_path, ["docs/a.md"]),
        "--ci-state", "success", "--mode", "live",
    ])
    assert code == EXIT_OK
    assert payload["action"] == "merge"


def test_a_missing_body_file_is_a_usage_error_not_a_silent_pass(capsys, tmp_path):
    code, payload = _run(capsys, [
        "iteration", "--issue", "12", "--labels", "status:claimable,type:bug",
        "--body-file", str(tmp_path / "nope.md"),
        "--agent-id", "agent-a", "--now", T0.isoformat(),
        "--post-acquire-labels", f"status:claimed,claimed-by:agent-a,{CLAIM_EXPIRES}",
    ])
    assert code == EXIT_USAGE
    assert "error" in payload


def test_no_subcommand_is_a_usage_error(capsys):
    assert main([]) == EXIT_USAGE


def test_the_package_makes_no_network_or_subprocess_call():
    """Global Constraint 6 as a test: no module in this package may import subprocess,
    socket, urllib, requests, or http.client."""
    import ast

    forbidden = {"subprocess", "socket", "urllib", "requests", "http", "os"}
    package = Path(__file__).resolve().parent.parent / "tools" / "autonomous_mode"
    offenders = []
    for source_file in sorted(package.glob("*.py")):
        tree = ast.parse(source_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            for name in names:
                if name in forbidden:
                    offenders.append(f"{source_file.name}:{name}")
    assert offenders == [], f"forbidden imports in a pure gate package: {offenders}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_autonomous_mode_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.autonomous_mode.__main__'`

- [ ] **Step 3: Write minimal implementation**

```python
# tools/autonomous_mode/__main__.py
"""`python -m tools.autonomous_mode <subcommand>` — the agent's interface to the gates.

Every gate in this package is a pure function, but the thing that actually runs autonomous
mode is an agent following a prompt. Without this surface the gates would be documentation
("remember to think about protected domains"); with it they are mechanical: the agent runs
a subcommand and gets a JSON verdict plus an exit code that is visible even if it ignores
the payload entirely.

Exit codes are part of the contract:

    0  proceed with what you were about to do
    2  refused — do not proceed
    1  the invocation itself was malformed

For `completion`, 0 is reserved for MERGED specifically: merge only on a zero exit.

No `gh`, no git, no network (Global Constraint 6) — every input is state the agent already
read and passes in. `--body-file`/`--changed-file` take paths rather than inline strings so
markdown and path lists survive shell quoting intact.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path

from tools.autonomous_mode import marker as marker_mod
from tools.autonomous_mode.iteration import (
    CompletionInput, CompletionOutcome, IterationInput, Outcome,
    plan_completion, plan_iteration,
)
from tools.autonomous_mode.merge_allowlist import merge_verdict
from tools.autonomous_mode.preflight import preflight

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_REFUSED = 2


def _emit(payload: dict) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _as_dict(obj) -> dict:
    if is_dataclass(obj):
        return {k: (list(v) if isinstance(v, tuple) else v)
                for k, v in asdict(obj).items()}
    return dict(obj)


def _split(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _read_lines(path: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in Path(path).read_text().splitlines() if line.strip())


def _now(value: str | None) -> datetime:
    return datetime.fromisoformat(value) if value else datetime.now(timezone.utc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.autonomous_mode")
    subs = parser.add_subparsers(dest="command")

    p = subs.add_parser("preflight")
    p.add_argument("--branch", required=True)
    p.add_argument("--worktree", required=True)
    p.add_argument("--mode", required=True)

    p = subs.add_parser("marker-start")
    p.add_argument("--worktree", required=True)
    p.add_argument("--agent-id", required=True)
    p.add_argument("--mode", required=True)
    p.add_argument("--now")

    p = subs.add_parser("marker-stop")
    p.add_argument("--worktree", required=True)
    p.add_argument("--now")

    p = subs.add_parser("marker-status")
    p.add_argument("--worktree", required=True)

    p = subs.add_parser("iteration")
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--labels", required=True)
    p.add_argument("--body-file", required=True)
    p.add_argument("--agent-id", required=True)
    p.add_argument("--now")
    p.add_argument("--done", default="")
    p.add_argument("--post-acquire-labels")

    p = subs.add_parser("completion")
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--changed-file", required=True)
    p.add_argument("--ci-state", required=True)
    p.add_argument("--mode", required=True)
    p.add_argument("--design-proposal-only", action="store_true")
    p.add_argument("--blocked-reason")

    p = subs.add_parser("merge-check")
    p.add_argument("--changed-file", required=True)
    p.add_argument("--ci-state", required=True)
    p.add_argument("--mode", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if not args.command:
        _emit({"error": "no subcommand given", "commands": sorted(
            ["preflight", "marker-start", "marker-stop", "marker-status",
             "iteration", "completion", "merge-check"])})
        return EXIT_USAGE

    try:
        return _dispatch(args)
    except (OSError, ValueError, marker_mod.MarkerCorruptError) as exc:
        _emit({"error": f"{type(exc).__name__}: {exc}"})
        return EXIT_USAGE


def _dispatch(args) -> int:
    if args.command == "preflight":
        worktree = Path(args.worktree)
        verdict = preflight(
            branch=args.branch, worktree=worktree,
            marker=marker_mod.read_marker(worktree), mode=args.mode,
        )
        _emit(_as_dict(verdict))
        return EXIT_OK if verdict.ok else EXIT_REFUSED

    if args.command == "marker-start":
        state = marker_mod.start_marker(
            Path(args.worktree), agent_id=args.agent_id, mode=args.mode,
            at=_now(args.now),
        )
        _emit(_as_dict(state))
        return EXIT_OK

    if args.command == "marker-stop":
        state = marker_mod.mark_stopped(Path(args.worktree), at=_now(args.now))
        _emit(_as_dict(state))
        return EXIT_OK

    if args.command == "marker-status":
        state = marker_mod.read_marker(Path(args.worktree))
        _emit({
            "running": state is not None and state.status == marker_mod.STATUS_RUNNING,
            "agent_id": state.agent_id if state else None,
            "mode": state.mode if state else None,
            "marker": _as_dict(state) if state else None,
        })
        return EXIT_OK

    if args.command == "iteration":
        done = frozenset(int(n) for n in _split(args.done) if n.isdigit())
        post = _split(args.post_acquire_labels) if args.post_acquire_labels else None
        result = plan_iteration(IterationInput(
            issue_number=args.issue, labels=_split(args.labels),
            body=Path(args.body_file).read_text(), agent_id=args.agent_id,
            now=_now(args.now), done_issues=done, post_acquire_labels=post,
        ))
        payload = _as_dict(result)
        payload["route"] = _as_dict(result.route) if result.route else None
        _emit(payload)
        return EXIT_OK if result.outcome is Outcome.CLEARED_TO_WORK else EXIT_REFUSED

    if args.command == "completion":
        result = plan_completion(CompletionInput(
            issue_number=args.issue, changed_paths=_read_lines(args.changed_file),
            ci_state=args.ci_state, mode=args.mode,
            design_proposal_only=args.design_proposal_only,
            blocked_reason=args.blocked_reason,
        ))
        _emit(_as_dict(result))
        return EXIT_OK if result.outcome is CompletionOutcome.MERGED else EXIT_REFUSED

    if args.command == "merge-check":
        verdict = merge_verdict(
            changed_paths=_read_lines(args.changed_file),
            ci_state=args.ci_state, mode=args.mode,
        )
        _emit(_as_dict(verdict))
        return EXIT_OK if verdict.action == "merge" else EXIT_REFUSED

    _emit({"error": f"unknown command {args.command!r}"})
    return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
```

Note for the implementer: `_as_dict` converts tuples to lists so `stages_reached` and
`reasons` serialize as JSON arrays; `Stage`/`Outcome` are `str` enums, so `asdict` already
yields their string values — this is why the tests compare against `"cleared-to-work"` and
`"work"` rather than enum members.

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_autonomous_mode_cli.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/autonomous_mode/__main__.py tests/test_autonomous_mode_cli.py
git commit -m "feat: expose the autonomous-mode gates as a CLI with real exit codes

Without this the gates are documentation an agent has to remember; with it they
are mechanical — a JSON verdict plus a non-zero exit visible even if the payload
is ignored. Exit 0 means proceed, 2 means refused, 1 means malformed invocation,
and for `completion` 0 is reserved for MERGED specifically. A static import check
pins Global Constraint 6: no subprocess, socket, urllib, or http anywhere in the
package."
```

---

## Task 13: The `/autonomous-mode` launcher skill and its prompt contract

**Files:**
- Create: `.claude/skills/autonomous-mode/SKILL.md`
- Test: `tests/test_autonomous_mode_skill_contract.py`

**Interfaces:**
- Consumes: every CLI subcommand from Task 12 (by name, in the prompt text).
- Produces: no Python interface. The deliverable is the skill file; the test's contract is that specific binding clauses and CLI invocations are present in it.

**Naming decision (spec §8 leaves it open — "name TBD"):** the skill is named `autonomous-mode`, matching the placeholder the spec used throughout and the marker filename `.claude/autonomous-mode.json`. Keeping the two aligned means `/autonomous-mode on` and the file it writes are obviously the same thing.

**What this test does and does not prove — stated plainly, because the distinction matters.** It proves the agent prompt *states* each binding clause and *names* each gate command. It cannot prove the agent obeys them. That is the honest limit of a prompt-level contract, and it is exactly why every gate whose violation would be expensive is also a CLI call with a non-zero exit code (Task 12) rather than only a sentence here. Treat the two as layers: the prompt tells the agent what to run, the exit code makes ignoring it visible.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_autonomous_mode_skill_contract.py
"""The launcher skill's prompt must STATE each binding clause and NAME each gate command.

This proves what the prompt says, not that the agent obeys it — the honest limit of a
prompt-level contract, and exactly why every expensive-to-violate gate is also a CLI call
with a non-zero exit code (tools/autonomous_mode/__main__.py) rather than only a sentence.
"""
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "autonomous-mode" / "SKILL.md"


@pytest.fixture(scope="module")
def text():
    return SKILL.read_text()


def test_the_skill_file_exists_with_frontmatter(text):
    assert text.startswith("---\n")
    assert "name: autonomous-mode" in text
    assert "description:" in text


@pytest.mark.parametrize("command", [
    "python -m tools.autonomous_mode preflight",
    "python -m tools.autonomous_mode marker-start",
    "python -m tools.autonomous_mode marker-stop",
    "python -m tools.autonomous_mode marker-status",
    "python -m tools.autonomous_mode iteration",
    "python -m tools.autonomous_mode completion",
])
def test_every_gate_command_is_named_in_the_prompt(text, command):
    assert command in text


@pytest.mark.parametrize("clause", [
    "refuses to run against `main`",
    "protected-domain",
    "before any investigation or code change",
    "no size-based exception",
    "superpowers:brainstorming",
    "docs/**",
    "tests/**",
    "needs human merge",
    "exit code",
    "dry-run",
    "outer loop",
    "TaskStop",
])
def test_every_binding_clause_is_stated(text, clause):
    assert clause in text, f"the agent prompt never states: {clause!r}"


def test_the_prompt_forbids_the_standard_git_escape_hatches(text):
    for forbidden in ("--force", "--no-verify", "--admin"):
        assert forbidden in text, f"the prompt must explicitly forbid {forbidden}"
    assert "never" in text.lower()


def test_the_prompt_states_the_lock_is_not_atomic(text):
    """Global Constraint 8: the mitigation must never be described as a guarantee."""
    lowered = text.lower()
    assert "not atomic" in lowered or "optimistic" in lowered
    assert "re-read" in lowered or "re-verify" in lowered


def test_the_prompt_declares_the_repo_local_type_labels(text):
    for label in ("type:bug", "type:investigation", "type:plan-task",
                  "type:feature", "type:design"):
        assert label in text


def test_the_prompt_points_at_the_repository_rules_it_must_load(text):
    for pointer in ("CLAUDE.md", ".claude/rules/", "branching-and-ci.md"):
        assert pointer in text


def test_the_prompt_names_the_deferred_scope_so_the_agent_does_not_invent_it(text):
    assert "does not widen" in text or "not widen" in text
    assert "auto-merge" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_autonomous_mode_skill_contract.py -v`
Expected: FAIL with `FileNotFoundError: .claude/skills/autonomous-mode/SKILL.md`

- [ ] **Step 3: Write minimal implementation**

````markdown
<!-- .claude/skills/autonomous-mode/SKILL.md -->
---
name: autonomous-mode
description: This skill should be used when the user asks to "turn on autonomous mode", "switch on autonomous engineering", "start working issues on your own", "/autonomous-mode on|off|status", or to stop an autonomous run. Launches (or stops) a background agent pinned to the current worktree that claims GitHub Issues via the github-issues-kanban protocol, routes each to the workflow this repository already owns, and works them unattended behind the safety gates in tools/autonomous_mode.
---

# Autonomous engineering mode

Implements `docs/superpowers/specs/2026-08-26-autonomous-engineering-mode-design.md`.
Autonomous mode changes **when** work happens. It never changes **what's allowed**.

Every safety decision below is a command that returns JSON and an **exit code**
(`0` proceed, `2` refused, `1` malformed invocation). Run the command and obey the
exit code. Do not re-derive any of these judgements by reasoning about them.

## `on` — launch

1. `git branch --show-current` and `git rev-parse --show-toplevel`.
2. `python -m tools.autonomous_mode preflight --branch <branch> --worktree <root> --mode <dry-run|live>`
   — this **refuses to run against `main`**, against a detached HEAD, against a branch
   outside this repo's initiative prefixes, and against a worktree that already has a
   running agent. Non-zero exit: report the reasons and stop. Do not proceed.
3. Default to `--mode dry-run` unless the user explicitly asked for live operation.
   Dry-run claims and investigates normally but stops before any push or merge and
   reports what it *would* have done.
4. `python -m tools.autonomous_mode marker-start --worktree <root> --agent-id <id> --mode <mode>`.
5. Spawn the background agent (`Agent`, `run_in_background: true`) with the loop prompt
   below, pasted in full. The agent inherits no context from this session, so the prompt
   must be self-contained.
6. Tell the user the agent id, the mode, and that stopping is `/autonomous-mode off`.

## `off` — stop

`TaskStop` on the agent id from `marker-status`, then
`python -m tools.autonomous_mode marker-stop --worktree <root>`.

## `status`

`python -m tools.autonomous_mode marker-status --worktree <root>`. "On" and "off" are the
conjunction of this marker and whether the agent is actually running — there is no other
state anywhere.

---

## The loop prompt (paste verbatim into the background agent)

> You are running autonomous engineering mode for this worktree. You have no inherited
> context. Before anything else, read `CLAUDE.md` and every file in `.claude/rules/` —
> in particular `.claude/rules/branching-and-ci.md`. Every rule there binds you exactly as
> it binds an interactive session. Autonomous mode changes when work happens, never what
> is allowed.
>
> **Repeat until stopped:**
>
> 1. **Find claimable work.** Use the `github-issues-kanban` skill's Dispatch primitive.
>    It respects `depends-on:#N` dependency DAGs and skips claimed or locked issues.
>
> 2. **Claim it**, following the skill's lock protocol: write the three claim labels in one
>    `gh issue edit`, then **re-read the labels immediately**. The lock is optimistic
>    concurrency, **not atomic** — the skill's own v0.1.0 scope says so. The re-read catches
>    the common race window and nothing more. If more than one `claimed-by:` label is
>    present and yours does not sort first, release yours and pick another issue.
>
> 3. **Run the gate**, passing the labels you read *after* claiming:
>
>    ```bash
>    python -m tools.autonomous_mode iteration \
>      --issue <N> --labels <pre-claim labels, comma-separated> \
>      --body-file <issue body written to a file> --agent-id <your id> \
>      --post-acquire-labels <labels re-read after claiming, comma-separated>
>    ```
>
>    A non-zero **exit code** means refused. Post the refusal as an event-bus comment,
>    release the claim, and move on. **Never** start work on a refused issue.
>
>    The gate refuses **protected-domain** issues — real trading and order execution, risk
>    limits and kill switches, sizing/bankroll/exposure, whale/advisory/confidence/
>    calibration logic, strategy/EV/fee/P&L/settlement semantics, security and auth policy,
>    CI/branch-protection/credential policy, and this mechanism's own implementation. That
>    refusal happens **before any investigation or code change** starts, which is stricter
>    than refusing to auto-merge. It also refuses an issue with no declared `## Scope`
>    section and an issue with no usable `type:*` label.
>
> 4. **Route by label** to the workflow this repository already owns. The gate's
>    `resolved_skill` field tells you which:
>
>    | Label | Routed to |
>    |---|---|
>    | `type:bug` | `superpowers:systematic-debugging` (this repo's `root-cause-debugging` discipline) |
>    | `type:investigation` | the domain skill the issue names, else `root-cause-debugging` |
>    | `type:plan-task` | the orchestrator or plan file the issue names |
>    | `type:feature` / `type:design` | `superpowers:brainstorming` |
>
> 5. **Do the work** using that skill's normal workflow, unchanged: TDD, one task per
>    commit, an initiative branch, targeted local verification, Woodpecker for the rest.
>
>    **If the issue needs a genuinely new architectural decision**, `superpowers:brainstorming`'s
>    human-approval hard gate applies here exactly as it applies outside autonomous mode:
>    produce the design, post it as an event-bus comment on the issue, and **stop**. Do not
>    self-approve. There is **no size-based exception** — "obviously small" is precisely the
>    judgement that erodes under unattended operation. Implementation waits for a separate,
>    later, human "proceed".
>
> 6. **Report progress** as event-bus comments on the issue throughout.
>
> 7. **Push and wait for Woodpecker.** Never `--force`, never `--no-verify`, never
>    `--admin`, never commit directly to `main`.
>
> 8. **Run the completion gate** with the *actual* diff, never the issue's label:
>
>    ```bash
>    git diff --name-only origin/main...HEAD > /tmp/changed.txt
>    python -m tools.autonomous_mode completion \
>      --issue <N> --changed-file /tmp/changed.txt \
>      --ci-state <success|failure|pending> --mode <your mode> \
>      [--design-proposal-only] [--blocked-reason <why>]
>    ```
>
>    Exit `0` — and only exit `0` — means you may `gh pr merge`. The merge allowlist is
>    `docs/**` and `tests/**` only; this skill **does not widen** it and there is no
>    unconditional auto-merge. Anything else: open the PR, post a "needs human merge" event,
>    and stop on that issue. CI must be green regardless of the allowlist.
>
> 9. **Release the claim** and post the closing event.
>
> 10. **Check the outer loop.** The kanban skill's auto-disable triggers — two consecutive
>     `blocked` events, any `error` event during a dispatch, an explicit user override,
>     session end — halt the **outer loop**, not just the current issue's dispatch. When one
>     fires, post the reason, stop claiming, and end the run. A degraded run halts itself
>     rather than continuing to claim more issues in a bad state.
>
> 11. Otherwise pace yourself and return to step 1.
>
> **In `--mode dry-run`:** do steps 1-6 normally, then run the completion gate, report its
> `would-merge` / `would-need-human-merge` preview as an event, and stop. Never push, never
> merge, never open a PR.

## Not in scope for this skill

Wiring an issue *source* (AQC findings, `ROADMAP.md` items, the active-tracks board),
widening the merge allowlist, unconditional auto-merge, and multi-worktree fleet
management are each a separate explicit decision — spec §11. Do not implement any of them
because a loop iteration would be more convenient with them.
````

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_autonomous_mode_skill_contract.py -v`
Expected: PASS (24 tests — the two parametrized tests contribute 6 and 12)

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/autonomous-mode/SKILL.md tests/test_autonomous_mode_skill_contract.py
git commit -m "feat: add the /autonomous-mode launcher skill and its prompt contract

Spec §8's launcher, named to match the marker file it writes (§8 left the name
open). The contract test proves the prompt STATES each binding clause and NAMES
each gate command — it cannot prove the agent obeys, which is exactly why every
expensive-to-violate gate is also a CLI call with a non-zero exit code rather than
only a sentence in a prompt."
```

---

## Task 14: Fault-injection suite — one scenario per safety claim

**Files:**
- Create: `tests/test_autonomous_mode_fault_injection.py`

**Interfaces:**
- Consumes: every module from Tasks 1-11. Adds no production code.

Spec §10.2 lists six scenarios, "one per safety claim in §6-§8". This task turns each into an executable proof over the code Tasks 1-11 already shipped. **These tests are expected to pass on the first run** — they are proofs over existing behavior, not a red-green cycle. If any fails, that is a real regression in Tasks 1-11 to fix before proceeding, not an expected red step. Same shape as the AQC plan's own Task 8.

**One reading of the spec resolved here, flagged rather than papered over.** §10.2 scenario 1 says "a protected-domain-**labeled** issue is refused at claim time." This suite tests refusal on the issue's declared `## Scope` paths, not on a label, because §7 of the same spec is explicit that a label "can be stale or wrong about what a fix actually touched" — trusting one for the *stricter* of the two gates would be inconsistent. The scenario's actual safety claim ("refused at claim time, not merely at merge time") is tested exactly as written; only the mechanism that identifies the domain differs.

Scenario 6's second half — that the agent, having posted a design, genuinely does not implement — is provable here only at the state-machine level (the terminal outcome has no outgoing transition). The behavioral half is covered by Task 13's prompt contract, and a manual rehearsal is recorded as Step 3 below. Three partial enforcements, honestly labelled.

- [ ] **Step 1: Write the scenario suite**

```python
# tests/test_autonomous_mode_fault_injection.py
"""Spec §10.2: one executable scenario per safety claim in §6-§8.

These are proofs over the code Tasks 1-11 already shipped, so they PASS on first run. A
failure here is a real regression in those tasks, not an expected red step.
"""
from datetime import datetime, timedelta, timezone

from tools.autonomous_mode import loop_control as lc
from tools.autonomous_mode import marker as m
from tools.autonomous_mode import iteration as it

T0 = datetime(2026, 8, 26, 12, 0, 0, tzinfo=timezone.utc)


def _labels(agent="agent-a", expires_minutes=30):
    return ("status:claimed", f"claimed-by:{agent}",
            f"claim-expires:{(T0 + timedelta(minutes=expires_minutes)).isoformat()}")


# Scenario 1 — a protected-domain issue is refused AT CLAIM TIME, not merely at merge time.
def test_scenario_1_protected_domain_refused_before_any_work():
    """§10.2 scenario 1. The spec says 'protected-domain-labeled'; this refuses on the
    issue's DECLARED SCOPE instead, because §7 of the same spec says a label can be stale
    or wrong about what a fix actually touched — trusting one for the stricter gate would
    be inconsistent. The safety claim itself is tested exactly as written."""
    result = it.plan_iteration(it.IterationInput(
        issue_number=1,
        labels=("status:claimable", "type:bug"),
        body="## Scope\n- services/risk_manager.py\n",
        agent_id="agent-a", now=T0,
        post_acquire_labels=_labels(),
    ))
    assert result.outcome is it.Outcome.REFUSED_PROTECTED_DOMAIN
    assert it.Stage.WORK not in result.stages_reached
    assert it.Stage.ROUTE not in result.stages_reached
    assert it.Stage.PUSH not in result.stages_reached


# Scenario 2 — a stale claim (TTL expired) recovers on the next dispatch cycle.
def test_scenario_2_stale_claim_recovers_on_the_next_cycle():
    stale = it.plan_iteration(it.IterationInput(
        issue_number=1, labels=("type:bug", *_labels(expires_minutes=-1)),
        body="## Scope\n- docs/a.md\n", agent_id="agent-b", now=T0,
        post_acquire_labels=_labels("agent-b"),
    ))
    assert stale.outcome is it.Outcome.STALE_RELEASE_REQUIRED
    assert stale.events == ("stale-release",)

    # After the conductor performs the stale-release, the issue is claimable again.
    recovered = it.plan_iteration(it.IterationInput(
        issue_number=1, labels=("status:claimable", "type:bug"),
        body="## Scope\n- docs/a.md\n", agent_id="agent-b", now=T0,
        post_acquire_labels=_labels("agent-b"),
    ))
    assert recovered.outcome is it.Outcome.CLEARED_TO_WORK


# Scenario 3 — a red Woodpecker run blocks merge unconditionally, allowlist or not.
def test_scenario_3_red_ci_blocks_merge_for_allowlisted_and_non_allowlisted_diffs():
    for changed in (("docs/a.md",), ("tests/test_x.py",), ("services/analytics/trends.py",)):
        for ci_state in ("failure", "pending", "error", ""):
            result = it.plan_completion(it.CompletionInput(
                issue_number=1, changed_paths=changed, ci_state=ci_state,
                mode=m.MODE_LIVE,
            ))
            assert result.outcome is it.CompletionOutcome.NEEDS_HUMAN_MERGE
            assert result.reason.startswith("ci-not-green:")
            assert it.Stage.RELEASE not in result.stages_reached


# Scenario 4 — two concurrent claim attempts resolve to exactly one worker proceeding.
def test_scenario_4_two_concurrent_claims_resolve_to_exactly_one_worker():
    """Proves the re-verify-after-acquire mitigation catches the common race. It is a
    partial mitigation of an explicitly non-atomic lock, not a guarantee (spec §8)."""
    contested = ("status:claimed", "claimed-by:agent-a", "claimed-by:agent-b")
    results = [
        it.plan_iteration(it.IterationInput(
            issue_number=1, labels=("status:claimable", "type:bug"),
            body="## Scope\n- docs/a.md\n", agent_id=agent, now=T0,
            post_acquire_labels=contested,
        ))
        for agent in ("agent-a", "agent-b")
    ]
    proceeding = [r for r in results if r.outcome is it.Outcome.CLEARED_TO_WORK]
    releasing = [r for r in results if r.outcome is it.Outcome.RELEASED_ON_CONFLICT]
    assert len(proceeding) == 1
    assert len(releasing) == 1
    assert it.Stage.WORK not in releasing[0].stages_reached


# Scenario 5 — a YOLO error event halts the OUTER loop, not just one dispatch.
def test_scenario_5_an_error_event_halts_the_outer_loop_not_one_dispatch():
    controller = lc.LoopController()
    assert controller.record_event("claimed").action == "continue"
    assert controller.record_event("error").action == "halt"
    # The halt must survive subsequent healthy iterations — otherwise it is a speed bump.
    for _ in range(3):
        assert controller.record_event("result").action == "halt"
    assert controller.halt_reason == "error-event-during-dispatch"


def test_scenario_5b_two_consecutive_blocked_events_also_halt_the_outer_loop():
    controller = lc.LoopController()
    assert controller.record_event("blocked").action == "continue"
    assert controller.record_event("blocked").action == "halt"


# Scenario 6 — a new design decision produces a posted proposal and genuinely stops.
def test_scenario_6_a_design_proposal_stops_and_no_implementation_follows():
    result = it.plan_completion(it.CompletionInput(
        issue_number=1, changed_paths=("docs/a.md",), ci_state="success",
        mode=m.MODE_LIVE, design_proposal_only=True,
    ))
    assert result.outcome is it.CompletionOutcome.AWAITING_HUMAN_DESIGN_APPROVAL
    assert it.Stage.PUSH not in result.stages_reached
    assert it.Stage.MERGE_GATE not in result.stages_reached
    assert it.Stage.RELEASE not in result.stages_reached
    assert "blocked" in result.events  # the proposal is posted as an event-bus comment


def test_scenario_6b_the_design_stop_is_terminal_under_every_input_combination():
    """No combination of mode, diff, or CI state transitions out of the design stop."""
    for mode in (m.MODE_LIVE, m.MODE_DRY_RUN):
        for changed in ((), ("docs/a.md",), ("services/analytics/trends.py",)):
            for ci_state in ("success", "failure", "pending"):
                result = it.plan_completion(it.CompletionInput(
                    issue_number=1, changed_paths=changed, ci_state=ci_state,
                    mode=mode, design_proposal_only=True,
                ))
                assert result.outcome is it.CompletionOutcome.AWAITING_HUMAN_DESIGN_APPROVAL


# Cross-cutting — the mechanism cannot authorise a change to itself.
def test_the_mechanism_refuses_to_modify_its_own_policy_implementation():
    """Spec §6 item 2's last clause: the loop, the allowlist, and the protected-domain list
    itself are protected. An unattended agent must not be able to widen its own leash."""
    for path in ("tools/autonomous_mode/merge_allowlist.py",
                 "tools/autonomous_mode/protected_domains.py",
                 ".claude/skills/autonomous-mode/SKILL.md"):
        claim_time = it.plan_iteration(it.IterationInput(
            issue_number=1, labels=("status:claimable", "type:bug"),
            body=f"## Scope\n- {path}\n", agent_id="agent-a", now=T0,
            post_acquire_labels=_labels(),
        ))
        assert claim_time.outcome is it.Outcome.REFUSED_PROTECTED_DOMAIN

        merge_time = it.plan_completion(it.CompletionInput(
            issue_number=1, changed_paths=(path,), ci_state="success", mode=m.MODE_LIVE,
        ))
        assert merge_time.outcome is it.CompletionOutcome.NEEDS_HUMAN_MERGE
```

- [ ] **Step 2: Run the suite**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_autonomous_mode_fault_injection.py -v`
Expected: PASS (9 tests). PASS on the first run **is** the expected outcome — these prove Tasks 1-11's shipped behavior, they are not a red-green cycle. A failure is a real regression to fix before proceeding.

- [ ] **Step 3: Record the manual rehearsal that the automated suite cannot cover**

The behavioral half of scenario 6 — that an agent *recognises* a new architectural decision
and sets the design-proposal path in the first place — is a judgement no test can make. Run
it once by hand before autonomous mode is ever used in `--mode live`, and record the result
in the PR description:

1. File one issue labelled `type:design` with a real, genuinely open design question and a
   `## Scope` naming only non-protected paths.
2. `/autonomous-mode on` in `--mode dry-run`.
3. Confirm the agent posts a design proposal as an event-bus comment and stops — no branch,
   no commit, no PR, no further claim on that issue.
4. `/autonomous-mode off`.

If the agent implements instead of stopping, that is a prompt defect in Task 13's SKILL.md,
not a gate defect: fix the prompt and re-run before going live.

- [ ] **Step 4: Run the full autonomous-mode test set together**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_autonomous_mode.py tests/test_autonomous_mode_cli.py tests/test_autonomous_mode_skill_contract.py tests/test_autonomous_mode_fault_injection.py -v`
Expected: PASS (182 tests total)

- [ ] **Step 5: Commit**

```bash
git add tests/test_autonomous_mode_fault_injection.py
git commit -m "test: fault-inject every safety claim in spec §6-§8

One executable scenario per §10.2 item: claim-time protected-domain refusal, stale
TTL recovery, unconditional CI blocking, concurrent-claim resolution, outer-loop
halt, and the terminal design stop — plus a cross-cutting proof that the mechanism
cannot authorise a change to its own allowlist or protected-domain list. §10.2
scenario 1 says 'protected-domain-labeled'; the gate refuses on declared scope
instead, since §7 of the same spec says a label can be stale about what a fix
touched. The safety claim is tested exactly as written; only the identifying
mechanism differs."
```

---

## Task 15: Documentation wiring

**Files:**
- Modify: `CLAUDE.md`
- Modify: `.claude/rules/quality-capabilities.md`

**Interfaces:** none — documentation only.

**No CI change is included, and that is a deliberate finding rather than an omission.**
`.woodpecker/tests-pytest.yml` already runs the whole `tests/` tree on every push and pull
request with no path filter, so all four new test files are CI-owned the moment they land —
no new pipeline, no new required status context, no branch-protection update. This satisfies
`.claude/rules/quality-capabilities.md`'s CI ownership rule without adding a pipeline whose
only job would be to re-run tests that already run. Confirm this before committing by
reading `.woodpecker/tests-pytest.yml`'s `when:` block rather than trusting this paragraph.

- [ ] **Step 1: Add a capability-router entry**

Add one bullet to `.claude/rules/quality-capabilities.md`'s "Available quality/reliability
capabilities" list:

```markdown
- **autonomous-mode** — switching autonomous engineering work on or off for the
  current worktree (`/autonomous-mode on|off|status`). Spawns a background agent
  that claims GitHub Issues via the `github-issues-kanban` protocol and routes
  each to the workflow this repo already owns, behind the gates in
  `tools/autonomous_mode/` (protected-domain refusal at claim time, the
  brainstorming human-approval stop, a `docs/**`/`tests/**`-only merge allowlist,
  unconditional CI-green, outer-loop halt). Design:
  `docs/superpowers/specs/2026-08-26-autonomous-engineering-mode-design.md`.
  It empowers, it never relaxes — every gate binds inside autonomous mode exactly
  as it binds an interactive session.
```

- [ ] **Step 2: Point `CLAUDE.md` at the mechanism and its repo-local label extension**

Add to `CLAUDE.md`'s "Quick file map", immediately after the existing `.claude/` bullet:

```markdown
- `tools/autonomous_mode/` — the deterministic safety gates behind
  `/autonomous-mode` (protected domains, issue-scope contract, label routing,
  claim lock, merge allowlist, loop state machine, outer-loop kill switch).
  Pure: no network, no subprocess, no `gh` — verdicts in, verdicts out, consumed
  by the agent as `python -m tools.autonomous_mode <subcommand>` with exit code
  `0` proceed / `2` refused. The `type:bug|investigation|plan-task|feature|design`
  issue labels it routes on are a **repo-local extension** to the
  `github-issues-kanban` skill's canonical label scheme, which defines
  `status:*`/`claimed-by:*`/`depends-on:#*`/`priority:*` but no `type:*`.
```

- [ ] **Step 3: Verify the CI claim above rather than assuming it**

Run: `grep -n -A6 "^when:" .woodpecker/tests-pytest.yml`
Expected: an unfiltered `event: [push, pull_request]` block with no `path:` restriction that
would exclude `tests/test_autonomous_mode*.py`. If a path filter *is* present, add the new
test paths to it in this same commit — the CI ownership rule is not satisfied by tests that
CI never runs.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md .claude/rules/quality-capabilities.md
git commit -m "docs: point CLAUDE.md and the capability router at autonomous mode

No new CI pipeline: .woodpecker/tests-pytest.yml already runs the whole tests/
tree unfiltered on push and pull_request, so all four new test files are CI-owned
on landing. Records the type:* labels as a repo-local extension to the kanban
skill's canonical scheme so a future session doesn't hunt for them there."
```

---


---



## Self-Review

**0. Judgment calls made while planning, recorded rather than smoothed over** (same
discipline the spec's own §12 applied). Each is a place the spec was genuinely open, not a
place this plan chose to depart from it:

- **How an issue declares what it will touch (Task 4).** Spec §6 item 2 requires refusing a
  protected-domain issue "before any investigation or code change starts," but never says
  how the gate knows an issue's scope — and nothing can infer it before the work is done.
  Resolved with a required `## Scope` section in the issue body, **failing closed**: no
  declared scope means not claimable. The alternative readings were both worse — inferring
  scope from the issue title is guesswork, and refusing only on a `protected-domain` label
  contradicts §7 of the same spec, which is explicit that a label "can be stale or wrong
  about what a fix actually touched." Task 7 independently re-checks the *actual* diff, so
  a fix that wanders outside its declared scope is still caught by the gate that can see
  real paths.
- **Domains → paths (Task 3).** The spec names eight protected *domains*; mapping them onto
  this repository's real files is judgment. The mapping is deliberately over-broad — `main.py`
  is protected because it holds both the trading loop and `POST /api/trading/enable`;
  `config/settings.yaml` because it holds `kalshi_account.trading_enabled` and every `risk.*`
  limit. A false refusal costs one human round-trip; a false permission costs an unattended
  change to trading code.
- **Two marker fields beyond the spec's literal JSON (Task 1).** §8 shows the marker with
  `e.g.` and four keys. `mode` was added because §10.1's dry-run has to be durable — a
  session reconnecting to a running agent must be able to tell whether it may push — and
  `stopped_at` so a stopped marker retains when it stopped. `mode` defaults to `dry-run`,
  the restrictive value.
- **The skill's name (Task 13).** §8 marked it "name TBD." Chosen: `autonomous-mode`,
  matching the placeholder the spec used throughout and the marker filename, so
  `/autonomous-mode on` and `.claude/autonomous-mode.json` are obviously the same thing.
- **`type:*` labels are a repo-local extension.** The `github-issues-kanban` skill's
  canonical `assets/label-scheme.json` defines `status:*`, `claimed-by:*`, `claim-expires:*`,
  `depends-on:#*`, `agent-output:*`, `size:*`, `complexity:*`, `priority:*`, and three control
  labels — **no `type:*` family at all**. Spec §5's routing table therefore introduces new
  labels rather than reusing existing ones. Task 15 records this in `CLAUDE.md` so a future
  session doesn't hunt for them in the skill's schema and conclude something is broken.
- **What the brainstorming gate can and cannot be (Tasks 9, 13, 14).** Spec §6 item 1 is a
  behavioral requirement: it asks the agent to *notice* that an issue needs a genuinely new
  architectural decision. No function can decide that. This plan makes the *consequence*
  mechanical (a terminal `AWAITING_HUMAN_DESIGN_APPROVAL` state with no outgoing transition),
  makes the *category* structural (`Route.requires_human_approval` on `type:feature`/
  `type:design`), and makes the *instruction* contract-tested (Task 13). The judgment itself
  gets a manual rehearsal (Task 14 Step 3). Three partial enforcements, labelled as such —
  not one imaginary complete one.
- **§10.2 scenario 1's wording (Task 14).** It says "a protected-domain-**labeled** issue."
  The suite tests refusal on declared scope instead, for the reason above. The scenario's
  actual safety claim — "refused at claim time, not merely at merge time" — is tested exactly
  as written; only the mechanism identifying the domain differs.
- **Where the code lives.** `tools/autonomous_mode/`, not `services/`. This is developer
  tooling that no FastAPI route imports, following `tools/quality_coordination_sim/`'s
  established shape in this repo (pure, no-network, no-credential decision modules with
  tests under `tests/`). Putting it in `services/` would put it on the app's import graph
  for no benefit and would trip the architecture audit's expectations for that package.

**1. Spec coverage** (against `docs/superpowers/specs/2026-08-26-autonomous-engineering-mode-design.md`):

| Spec section | Task |
|---|---|
| §1 Purpose | The plan as a whole; no single task |
| §2 Non-goals | Global Constraints 9, 10 — no task may violate them |
| §3 Prior art and constraints | Global Constraints 1-11; Task 2 (branching policy), Task 6 (kanban lock protocol), Task 13 (prompt loads `CLAUDE.md` + `.claude/rules/`) |
| §4 Architecture overview (the loop) | Task 8 (steps 1-4), Task 9 (steps 6-9), Task 13 (the loop prompt itself) |
| §5 Skill routing | Task 5 (the table as a dispatch function), surfaced in Task 13 |
| §6 item 1 — brainstorming gate | Task 5 (`requires_human_approval`), Task 9 (terminal state), Task 13 (prompt contract), Task 14 (scenario 6) |
| §6 item 2 — protected-domain refusal at claim time | Task 3 (the classifier), Task 4 (the scope gate), Task 8 (`stages_reached` proof), Task 14 (scenario 1) |
| §6 item 3 — standard git safety | Task 7 (unconditional CI-green), Task 13 (explicit `--force`/`--no-verify`/`--admin` prohibitions), Global Constraint 4 |
| §7 Merge-allowlist | Task 7 (the diff gate), Task 9 (composition), Task 14 (scenario 3) |
| §8 State | Task 1 (marker file) |
| §8 Launch | Task 2 (preflight), Task 13 (launcher steps) |
| §8 Concurrency / re-verify-after-acquire | Task 6, Task 8, Task 14 (scenario 4) |
| §8 Second kill switch (outer loop) | Task 11, Task 14 (scenarios 5, 5b) |
| §9 Data flow summary | Descriptive; realized by Tasks 8-9's `events` tuples, Task 12's CLI, Task 13's prompt |
| §10.1 Dry-run mode | Task 10 |
| §10.2 Fault injection | Task 14 (six scenarios + one cross-cutting self-modification proof) |
| §11 Explicitly deferred | Global Constraints 5, 9; Task 13's "Not in scope" section. **No task builds any of it**, by design |
| §12 Self-review | N/A — the spec's own |

Every specified section has an implementing task. The only sections with no task are §1
(framing), §2/§11 (prohibitions, which appear as Global Constraints and as a "Not in scope"
section in the prompt rather than as work), §9 (a summary of what other sections build), and
§12 (the spec's own review).

**2. Placeholder scan:** every step contains complete, real code — no `TODO`, no `TBD`, no
"add appropriate error handling," no "similar to Task N" without the code repeated. The one
occurrence of the string "TBD" in this plan is Task 13 quoting the spec's own "name TBD"
while resolving it. Task 15's documentation steps quote the exact markdown to insert and
name the exact anchor to insert it after. Task 14 Step 3 is prose because it describes a
manual rehearsal, and says so explicitly rather than implying an automatable step.

**3. Type and interface consistency across tasks:**

- `MODE_DRY_RUN` / `MODE_LIVE` / `VALID_MODES` are defined once (Task 1) and imported by
  Tasks 2, 7, 9, 10, 12, 14 — no second mode vocabulary exists anywhere.
- `ClaimCheck.action`'s four values (`"proceed"`, `"stale-release"`, `"release-and-retry"`,
  `"not-claimable"`) are produced in Task 6 and consumed by name in Task 8's `plan_iteration`
  — the strings match exactly, and Task 8 handles all four.
- `MergeVerdict.action`'s two values (`"merge"`, `"needs-human-merge"`) are produced in
  Task 7 and consumed in Task 9 and Task 12's exit-code mapping — matching exactly.
- `Stage` and `Outcome` (Task 8) and `CompletionOutcome` (Task 9, extended once in Task 10)
  are the only outcome vocabularies; Tasks 12 and 14 compare against those members, and
  Task 12's JSON compares against their `str` values (`"cleared-to-work"`, `"work"`), which
  works because both are `str` enums.
- `IterationResult.events` / `CompletionResult.events` are `tuple[str, ...]` of kanban
  event-bus type names, produced in Tasks 8-10 and consumed by `LoopController.record_iteration`
  in Task 11 and by Task 14's scenarios — one shared vocabulary, drawn from the skill's own
  `references/event-bus.md` table (`claimed`, `released`, `progress`, `blocked`, `result`,
  `error`, `stale-release`).
- `classify_paths` (Task 3) is the single protected-domain entry point, called from Task 4
  (claim time) and Task 7 (merge time). There is no second copy of the domain list.
- `matches` / `matches_any` (Task 3) are the single path-matching implementation, used by
  Tasks 3 and 7. No task re-implements globbing.
- `plan_iteration(IterationInput) -> IterationResult` and
  `plan_completion(CompletionInput) -> CompletionResult` keep identical signatures across
  Tasks 8, 9, 10, 12, and 14; Task 10 changes only the enum and one branch, never a
  signature.

**4. Deferral discipline check.** Confirmed against spec §11 before finalizing: no task
wires AQC findings, `ROADMAP.md`, or the active-tracks board into the issue queue; no task
widens the merge allowlist beyond `docs/**` and `tests/**` (Task 7 has a test pinning it to
exactly those two); no task adds unconditional auto-merge; and no task designs multi-worktree
fleet management. Task 13's SKILL.md names all four as out of scope inside the prompt itself,
so the running agent inherits the prohibition rather than depending on this document.
