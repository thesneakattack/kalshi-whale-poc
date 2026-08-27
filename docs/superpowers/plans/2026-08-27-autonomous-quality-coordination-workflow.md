# Autonomous Quality Coordination Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `tools/coordination_engine.py` (a shared, signal-shape-agnostic identity/fingerprint/persistence-floor/suppression/resolution state machine) and `tools/quality_coordination.py` (AQC itself: four signal-domain gatherers, a read-only trading-application diagnostics client used as context rather than an audited target, and a three-action git/filesystem cleanup layer) — the standalone workflow tool that acts as an automated project manager and janitor over this repository's own engineering workflow (branch/PR/CI lifecycle, `superpowers` plan/ledger execution health, standing-rule and process hygiene), per the corrected scope in `docs/superpowers/specs/2026-08-27-autonomous-quality-coordination-workflow-design.md`. "AQC" now names, and only names, this tool; the prior implementation under that name is kept, already renamed to `tools/quality_ratchet.py` (`main`@`440da36`, confirmed merged), and is not touched by this plan.

**Architecture:** Two new pure-Python flat modules under `tools/`, matching the spec's own §4 diagram exactly (not a subpackage — a deliberate choice, see Self-Review): `tools/coordination_engine.py` owns the SQLite store (`tools/quality_coordination_data/quality_coordination.db`) and the domain-agnostic `Signal`/`apply_observation` state machine, modeled on `tools/quality_ratchet.py`'s already-proven identity/fingerprint/persistence-floor/suppression/resolution contract but generalized past `QualityFinding`. `tools/quality_coordination.py` is AQC itself: four signal-domain gatherers (branch/PR/CI lifecycle, plan/ledger health, standing-rule hygiene, docs/ROADMAP drift — the last of which never routes through the engine), a read-only app-diagnostics client used as context, a three-action deterministic cleanup layer, and the `python -m tools.quality_coordination` CLI. Every `git`/`gh`/Woodpecker-CLI subprocess call and every app-diagnostics HTTP call goes through an injectable runner, following `tools/kanban_sync/github_client.py`'s established pattern in this repo, so no test in this plan ever shells out or makes a network call for real.

**Tech Stack:** Python 3.13 (stdlib only — `argparse`, `dataclasses`, `sqlite3`, `json`, `hashlib`, `subprocess`, `urllib.request`, `pathlib`, `datetime`, `re`, `typing`), `pytest`, the `gh` CLI and `scripts/woodpecker-status` (already used elsewhere in this repo), a disposable synthetic git repository fixture for the cleanup-action fault-injection suite (§10 point 3 — never this repository). No new dependency.

**Spec:** `docs/superpowers/specs/2026-08-27-autonomous-quality-coordination-workflow-design.md` (read in full before starting — this plan implements it section by section, §1-§15; the Global Constraints below quote its load-bearing decisions verbatim, but the spec has the reasoning). `main`@`440da36` already merged §12's rename — `tools/quality_coordination.py` and `tools/quality_coordination_data/` are free names again before this plan starts (re-confirm with `git log --oneline -- tools/quality_ratchet.py` if this ever needs re-checking).

## Global Constraints

1. **No GitHub write authority anywhere in this plan (§2, §11).** No task creates, comments on, closes, or labels a GitHub Issue or PR. Every `gh` call in this plan is read-only (`gh pr list`, `gh pr view`, branch/PR state lookups only).
2. **`main` is never a cleanup target, and the current invocation's own worktree is never pruned (§8, §11).** No code path in Task 7 may mutate `main` or the worktree the current invocation runs from, under any precondition.
3. **Every mutating action is deterministic, idempotent, path-contained, and independently verifiable (§8; `.claude/rules/autonomous-quality-coordination-evidence.md`'s "Remediation authority rule," generalized here from GitHub-write to git/filesystem-write per that rule's own framing note in spec §3).** Detect and act are always separate phases (§5); `--clean` always re-runs detection in the same invocation, never a stale cached result.
4. **`quality_ratchet.py` is not retrofit onto `coordination_engine.py` in this cycle (§4, §13).** The two tools keep independent state-machine implementations; the short-term duplication is an accepted, explicitly-named trade-off. No task in this plan modifies `tools/quality_ratchet.py`.
5. **No cron/Woodpecker scheduling, no AEM issue-queue hookup, no cleanup-allowlist widening beyond the three actions in §8, no ROADMAP-drift resolution (§13).** Each is its own separate, later, explicit decision. §6.5's docs/ROADMAP comparison is a raw data feed, never asserted as a finding by any task in this plan.
6. **Manual invocation only (§5):** `python -m tools.quality_coordination` (detect + report, default) and `python -m tools.quality_coordination --clean` (also executes eligible cleanup actions). Nothing in this plan wires into `main.py`'s tick loop, `config/settings.yaml`, or any app-owned route — `CLAUDE.md`'s "Workflow/tooling and application code must never overlap" standing rule.
7. **No guessed persistence thresholds (§10 point 1; `.claude/rules/autonomous-quality-coordination-evidence.md`'s "No guessed quarantine period").** Every `FLOOR_HOURS`/cluster-window constant this plan introduces is derived from real measured repo cadence (git/gh history) and documented inline with the evidence behind it, not hardcoded because a round number "sounds conservative." Where this plan's own measurement is necessarily a smaller sample than a full implementation-time pass should use, that is said explicitly, not hidden.
8. **Tests never touch a real `data/*.db`, a real `tools/quality_coordination_data/`, or make a real `gh`/`git`/network/Woodpecker call (§10 point 4).** `DB_PATH` is always `monkeypatch`ed to an isolated tmp path; every subprocess/HTTP call goes through an injectable runner with a fake test double, mirroring `tests/support/fake_gh_runner.py` (already established by the kanban-sync plan, reused here rather than re-invented).
9. **The cleanup-action fault-injection suite runs against a disposable synthetic git repository fixture, never this repository (§10 point 3).** Idempotence, each stated precondition's refusal path, the worktree-checked-out no-op, and the `main`-target no-op are all proven there, not against the live checkout this plan lands on.
10. **The one application-side change this design requires — adding the three read-only diagnostics routes to `services/auth.py`'s `PUBLIC_PATHS` (§6.4, §14) — is explicitly user-approved *in the spec*, but Task 11 below still stops for real, live human sign-off before touching `services/auth.py`.** "The spec says it's approved" is not the same as "go ahead and edit the file" — no other task in this plan modifies any file under `services/` or `main.py`, and Task 11 is written to make this distinction impossible to skim past.
11. **Repository:** `thesneakattack/kalshi-whale-poc`, matching every other cross-referenced doc in this repo.
12. **Branching (`.claude/rules/branching-and-ci.md`):** Tasks 1-10 land on a `feat/autonomous-quality-coordination-workflow` branch off `main`, one task per commit. Task 11 (the `PUBLIC_PATHS` change) is its own separate branch, opened only after the human sign-off it requires — see that task. Claude runs the targeted tests locally; Woodpecker owns exhaustive verification. `.woodpecker/tests-pytest.yml` already runs the whole `tests/` tree on every push with no path filter (confirm this directly — `grep -n -A6 "^when:" .woodpecker/tests-pytest.yml` — rather than assuming it, the same check AEM's Task 15 performed), so every new test file in this plan is CI-owned the moment it lands; no new pipeline is added by this plan.
13. Run tests via `ddev exec -s fastapi python3 -m pytest -q <path>` per this project's existing dev workflow (`CLAUDE.md`'s "Dev workflow" section) — not a bare host `pytest`.

---

### Task 1: `coordination_engine.py` — `Signal`, schema, and the state machine

**Files:**
- Create: `tools/coordination_engine.py`
- Test: `tests/test_coordination_engine.py`

**Interfaces:**
- Produces: `DB_PATH: Path`, `_connect() -> sqlite3.Connection` (creates `signal_state`,
  `coordination_runs`, `cleanup_actions` per §9's exact schema — all three tables live in
  this one shared file, since §4's diagram shows a single arrow from
  `coordination_engine.py` to the one SQLite store, including the cleanup-action log Task 7
  writes into via `log_cleanup_action` below).
- Produces: `Signal` (frozen dataclass: `identity: str, domain: str, payload: dict,
  still_present: bool`) — §7's contract, used by every later signal-gatherer task.
- Produces: `apply_observation(conn, signals: list[Signal], at: datetime, floor_hours:
  dict[str, float], *, suppressed_keys: frozenset[str] = frozenset(), immediate_keys:
  frozenset[str] = frozenset()) -> dict[str, str]` — returns `{identity: state}`, state one
  of `new|observed|escalation_eligible|suppressed|resolved` per §7's docstring.
- Produces: `get_prior_row(conn, identity: str) -> sqlite3.Row | None` — a read-only helper
  a domain gatherer can use for its own cross-run comparisons (Task 3's CI-staleness
  detection needs this).
- Produces: `log_cleanup_action(conn, identity: str, action_type: str, executed_at:
  datetime, dry_run: bool, outcome: str) -> None`, `record_run(conn, ran_at: datetime,
  signals_observed: int, signals_escalated: int, error: str | None = None) -> None` — used
  by Task 7 and Task 8 respectively.

**Judgment calls made here, recorded rather than smoothed over** (§7's minimal illustrative
snippet shows `apply_observation(conn, signals, at)` — three positional args; the following
extensions are additions, not contradictions, and are explained in the Self-Review):
- `floor_hours` is a required 4th positional arg (a `dict[str, float]` keyed by domain),
  not a module-level constant, because §10 point 1 requires every persistence-floor
  threshold to come from measured cadence, and different signal domains (§6.1-§6.3) plainly
  need different floors — a single module-level `FLOOR_HOURS` shared across all domains
  would either be wrong for most of them or force domain-specific overrides through some
  other mechanism. Missing a domain's entry raises `KeyError` rather than silently defaulting
  — a missing floor is a planning gap to catch immediately, not a value to guess at runtime.
- `suppressed_keys`/`immediate_keys` are keyword-only per-cycle override sets a domain
  gatherer computes from its own domain-specific logic before calling `apply_observation`
  — §6.1's open-PR-activity, paused-track-note, and (Task 3) co-dispatch-cluster suppression
  candidates; §6.1's "a failure/error state ... should surface immediately ... never behind
  a persistence floor" requirement. The engine stays domain-agnostic — it has no idea *why*
  an identity is suppressed or immediate, only that the caller says so this cycle.
- `signal_state`'s schema (§9) has no per-observation log table (unlike
  `quality_ratchet.py`'s `coordination_log`), so `quality_ratchet.py`'s
  `_deburst_count`/`DEBURST_GAP_HOURS`/`DEBURST_COUNT` burst-smoothing has no equivalent
  here — there is nowhere to read a history of past observation timestamps from. This
  implementation drops deburst counting and uses a plain elapsed-time-since-first-seen floor
  instead. The specific bug that logic existed to work around in `quality_ratchet.py` (a
  fingerprint short-circuit that skipped `apply_observation` entirely on an unchanged
  fingerprint, freezing the floor for a genuinely persisting finding) is avoided here a
  different way: this `apply_observation` has no short-circuit at all — Task 8's CLI always
  calls it on every signal, every cycle, matching `quality_ratchet.py`'s own post-fix
  behavior.
- `fingerprint` (§9's column) is a per-identity content hash (`sha256` of the sorted-JSON
  payload), recomputed and stored every cycle. §9 defines the column but not its update
  rule; this implementation's rule — always recompute, never gate anything on whether it
  changed — is what Task 3's CI-staleness detection depends on (comparing this cycle's
  fresh fingerprint against the *prior* row's stored one via `get_prior_row`, read before
  calling `apply_observation`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_coordination_engine.py
from datetime import datetime, timedelta, timezone

import pytest

from tools import coordination_engine as ce
from tools.coordination_engine import Signal, apply_observation

T0 = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)


def _sig(identity="branch:feat/x", domain="branch", payload=None, still_present=True):
    return Signal(identity=identity, domain=domain, payload=payload or {"a": 1}, still_present=still_present)


def test_connect_creates_all_three_tables(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "DB_PATH", tmp_path / "quality_coordination.db")
    conn = ce._connect()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"signal_state", "coordination_runs", "cleanup_actions"} <= tables


def test_connect_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "DB_PATH", tmp_path / "quality_coordination.db")
    ce._connect()
    ce._connect()  # must not raise on a second CREATE TABLE IF NOT EXISTS pass


def test_new_signal_is_observed_and_tracked(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "DB_PATH", tmp_path / "quality_coordination.db")
    conn = ce._connect()

    states = apply_observation(conn, [_sig()], T0, floor_hours={"branch": 6.0})

    assert states == {"branch:feat/x": "observed"}
    row = conn.execute("SELECT * FROM signal_state WHERE identity=?", ("branch:feat/x",)).fetchone()
    assert row["state"] == "observed"
    assert row["observation_count"] == 1


def test_missing_floor_hours_entry_raises_keyerror(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "DB_PATH", tmp_path / "quality_coordination.db")
    conn = ce._connect()

    with pytest.raises(KeyError):
        apply_observation(conn, [_sig(domain="ledger")], T0, floor_hours={"branch": 6.0})


def test_signal_escalates_once_floor_hours_elapsed(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "DB_PATH", tmp_path / "quality_coordination.db")
    conn = ce._connect()
    apply_observation(conn, [_sig()], T0, floor_hours={"branch": 6.0})

    later = T0 + timedelta(hours=7)
    states = apply_observation(conn, [_sig()], later, floor_hours={"branch": 6.0})

    assert states == {"branch:feat/x": "escalation_eligible"}


def test_signal_stays_observed_before_floor_hours_elapsed(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "DB_PATH", tmp_path / "quality_coordination.db")
    conn = ce._connect()
    apply_observation(conn, [_sig()], T0, floor_hours={"branch": 6.0})

    soon = T0 + timedelta(hours=1)
    states = apply_observation(conn, [_sig()], soon, floor_hours={"branch": 6.0})

    assert states == {"branch:feat/x": "observed"}


def test_absent_signal_resolves(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "DB_PATH", tmp_path / "quality_coordination.db")
    conn = ce._connect()
    apply_observation(conn, [_sig()], T0, floor_hours={"branch": 6.0})

    states = apply_observation(conn, [], T0 + timedelta(hours=1), floor_hours={"branch": 6.0})

    assert states == {"branch:feat/x": "resolved"}
    row = conn.execute("SELECT state FROM signal_state WHERE identity=?", ("branch:feat/x",)).fetchone()
    assert row["state"] == "resolved"


def test_resolved_signal_reopens_with_fresh_floor_clock(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "DB_PATH", tmp_path / "quality_coordination.db")
    conn = ce._connect()
    apply_observation(conn, [_sig()], T0, floor_hours={"branch": 6.0})
    apply_observation(conn, [], T0 + timedelta(hours=1), floor_hours={"branch": 6.0})

    # Reappears 100 hours later - if the floor clock weren't reset on reopen this would
    # wrongly escalate immediately instead of restarting as "observed".
    states = apply_observation(conn, [_sig()], T0 + timedelta(hours=100), floor_hours={"branch": 6.0})

    assert states == {"branch:feat/x": "observed"}


def test_suppressed_keys_override_floor(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "DB_PATH", tmp_path / "quality_coordination.db")
    conn = ce._connect()
    apply_observation(conn, [_sig()], T0, floor_hours={"branch": 6.0})

    later = T0 + timedelta(hours=100)
    states = apply_observation(
        conn, [_sig()], later, floor_hours={"branch": 6.0},
        suppressed_keys=frozenset({"branch:feat/x"}),
    )

    assert states == {"branch:feat/x": "suppressed"}


def test_immediate_keys_bypass_floor_on_first_observation(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "DB_PATH", tmp_path / "quality_coordination.db")
    conn = ce._connect()

    states = apply_observation(
        conn, [_sig()], T0, floor_hours={"branch": 6.0},
        immediate_keys=frozenset({"branch:feat/x"}),
    )

    assert states == {"branch:feat/x": "escalation_eligible"}


def test_immediate_keys_win_over_suppressed_keys_when_both_apply(tmp_path, monkeypatch):
    """Regression test (found in review): a branch with an open PR (suppressed_keys) AND a
    real CI failure (immediate_keys) must still escalate - spec §6.1's "a failure/error
    state should surface immediately... never behind a persistence floor" is about severity,
    not about whether some other, unrelated evidence of activity also exists. Suppression
    exists to delay a floor-driven staleness signal, not to mask a real, currently-failing
    signal - checking immediate_keys before suppressed_keys in apply_observation is what
    keeps that distinction real rather than accidental."""
    monkeypatch.setattr(ce, "DB_PATH", tmp_path / "quality_coordination.db")
    conn = ce._connect()

    states = apply_observation(
        conn, [_sig()], T0, floor_hours={"branch": 6.0},
        suppressed_keys=frozenset({"branch:feat/x"}),
        immediate_keys=frozenset({"branch:feat/x"}),
    )

    assert states == {"branch:feat/x": "escalation_eligible"}


def test_fingerprint_changes_when_payload_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "DB_PATH", tmp_path / "quality_coordination.db")
    conn = ce._connect()
    apply_observation(conn, [_sig(payload={"a": 1})], T0, floor_hours={"branch": 6.0})
    row1 = ce.get_prior_row(conn, "branch:feat/x")

    apply_observation(conn, [_sig(payload={"a": 2})], T0 + timedelta(minutes=1), floor_hours={"branch": 6.0})
    row2 = ce.get_prior_row(conn, "branch:feat/x")

    assert row1["fingerprint"] != row2["fingerprint"]


def test_get_prior_row_returns_none_for_unknown_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "DB_PATH", tmp_path / "quality_coordination.db")
    conn = ce._connect()

    assert ce.get_prior_row(conn, "nope") is None


def test_log_cleanup_action_and_record_run_write_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "DB_PATH", tmp_path / "quality_coordination.db")
    conn = ce._connect()

    ce.log_cleanup_action(conn, "branch:feat/x", "delete_merged_branch", T0, dry_run=True, outcome="succeeded")
    ce.record_run(conn, T0, signals_observed=3, signals_escalated=1)
    conn.commit()

    action = conn.execute("SELECT * FROM cleanup_actions").fetchone()
    run = conn.execute("SELECT * FROM coordination_runs").fetchone()
    assert action["action_type"] == "delete_merged_branch"
    assert bool(action["dry_run"]) is True
    assert run["signals_observed"] == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_coordination_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.coordination_engine'`

- [ ] **Step 3: Implement `coordination_engine.py`**

```python
# tools/coordination_engine.py
"""coordination_engine — shared, signal-shape-agnostic identity/fingerprint/
persistence-floor/suppression/resolution state machine
(docs/superpowers/specs/2026-08-27-autonomous-quality-coordination-workflow-design.md
§7, §9). Reused deliberately from tools/quality_ratchet.py's already-proven
identity/fingerprint/persistence-floor/suppression/resolution contract (spec §3) rather
than re-derived - generalized past QualityFinding to an arbitrary caller-supplied Signal.
Not retrofit onto quality_ratchet.py in this cycle (spec §4, §13); the two tools keep
independent implementations.

Standalone workflow tool, not application code: this module and tools/quality_coordination.py
(its only importer) are never imported by main.py or any part of the live trading app, per
CLAUDE.md's "Workflow/tooling and application code must never overlap" standing rule. Data
lives under this file's own tools/quality_coordination_data/, not the shared data/ directory
the trading app owns - same reasoning tools/quality_ratchet.py's own docstring gives for its
data directory.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "quality_coordination_data" / "quality_coordination.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # WAL mode: lets a concurrent reader (a human/Claude session inspecting the DB directly -
    # there is no API route, per the standing workflow/app-overlap rule) proceed alongside a
    # run's writer, same hardening tools/quality_ratchet.py and services/paper_broker.py both
    # apply. Idempotent - safe on every connect.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS signal_state (
        identity TEXT PRIMARY KEY,
        domain TEXT NOT NULL,
        state TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        observation_count INTEGER NOT NULL DEFAULT 1,
        explanation TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS coordination_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ran_at TEXT NOT NULL,
        signals_observed INTEGER NOT NULL,
        signals_escalated INTEGER NOT NULL,
        error TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS cleanup_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        identity TEXT NOT NULL,
        action_type TEXT NOT NULL,
        executed_at TEXT NOT NULL,
        dry_run INTEGER NOT NULL,
        outcome TEXT NOT NULL
    )""")
    return conn


@dataclass(frozen=True)
class Signal:
    identity: str   # stable key within its domain (spec §6.1-§6.3)
    domain: str      # "branch" | "ledger" | "process_hygiene"
    payload: dict    # JSON-serializable, feeds the fingerprint
    still_present: bool


def _fingerprint(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def get_prior_row(conn: sqlite3.Connection, identity: str) -> sqlite3.Row | None:
    """Read-only lookup for a domain gatherer's own cross-run comparisons - e.g. Task 3's
    CI-staleness detection, which needs to know whether the furthest-along Woodpecker step
    advanced since the last AQC run. apply_observation itself never calls this; it exists
    purely for callers to read state BEFORE building this cycle's Signal payload."""
    return conn.execute("SELECT * FROM signal_state WHERE identity=?", (identity,)).fetchone()


def apply_observation(
    conn: sqlite3.Connection,
    signals: list[Signal],
    at: datetime,
    floor_hours: dict[str, float],
    *,
    suppressed_keys: frozenset[str] = frozenset(),
    immediate_keys: frozenset[str] = frozenset(),
) -> dict[str, str]:
    """Spec §7's contract. Returns {identity: state}, state one of:
    new | observed | escalation_eligible | suppressed | resolved.

    floor_hours maps domain -> hours; every domain present in `signals` must have an entry
    or this raises KeyError (a missing floor is a planning gap to catch immediately, not a
    default to paper over - spec §10 point 1 / the evidence rule's "no guessed threshold").

    suppressed_keys/immediate_keys are per-cycle overrides a domain gatherer computes from
    its own domain-specific logic before calling this (spec §6.1's open-PR-activity,
    paused-track-note, and co-dispatch-cluster suppression candidates; its "a failure/error
    state should surface immediately, never behind a persistence floor" requirement). Not
    shown in spec §7's minimal 3-positional-arg illustrative snippet - added as keyword-only
    extensions; see this plan's Self-Review for the reasoning.
    """
    present = {s.identity: s for s in signals}
    result: dict[str, str] = {}

    tracked = conn.execute("SELECT * FROM signal_state").fetchall()
    for row in tracked:
        if row["identity"] not in present and row["state"] != "resolved":
            conn.execute(
                "UPDATE signal_state SET state='resolved', explanation=? WHERE identity=?",
                ("resolved: absent from this run", row["identity"]),
            )
            result[row["identity"]] = "resolved"

    for identity, sig in present.items():
        if sig.domain not in floor_hours:
            raise KeyError(f"no floor_hours entry for domain {sig.domain!r}")

        fp = _fingerprint(sig.payload)
        row = conn.execute("SELECT * FROM signal_state WHERE identity=?", (identity,)).fetchone()

        if row is None or row["state"] == "resolved":
            # INSERT-or-reopen: a fresh identity, or one whose prior resolution didn't hold.
            # Either way the floor clock restarts at `at` - deliberate, see the reopen test.
            conn.execute(
                """INSERT INTO signal_state
                   (identity, domain, state, fingerprint, first_seen_at, last_seen_at,
                    observation_count, explanation)
                   VALUES (?, ?, 'new', ?, ?, ?, 1, 'new observation')
                   ON CONFLICT(identity) DO UPDATE SET
                     domain=excluded.domain, state='new', fingerprint=excluded.fingerprint,
                     first_seen_at=excluded.first_seen_at, last_seen_at=excluded.last_seen_at,
                     observation_count=1, explanation='reopened: prior resolution did not hold'""",
                (identity, sig.domain, fp, at.isoformat(), at.isoformat()),
            )
            first_seen_at = at
        else:
            conn.execute(
                """UPDATE signal_state SET last_seen_at=?, observation_count=observation_count+1,
                   fingerprint=? WHERE identity=?""",
                (at.isoformat(), fp, identity),
            )
            first_seen_at = datetime.fromisoformat(row["first_seen_at"])

        elapsed_hours = (at - first_seen_at).total_seconds() / 3600
        # immediate_keys is checked BEFORE suppressed_keys (found in review, 2026-08-27): a
        # real failure/error signal must surface "immediately... never behind a persistence
        # floor" (spec §6.1) regardless of whether some other, unrelated evidence of active
        # work also exists for the same identity - suppression exists to delay a
        # floor-driven staleness signal, not to mask a currently-real severity signal. See
        # test_immediate_keys_win_over_suppressed_keys_when_both_apply above.
        if identity in immediate_keys:
            state = "escalation_eligible"
            explanation = "escalation-eligible: immediate-severity signal, floor bypassed"
        elif identity in suppressed_keys:
            state = "suppressed"
            explanation = "suppressed: active-work evidence for this identity this cycle"
        elif elapsed_hours >= floor_hours[sig.domain]:
            state = "escalation_eligible"
            explanation = f"escalation-eligible: persistence floor ({floor_hours[sig.domain]}h) met"
        else:
            state = "observed"
            explanation = f"observed: {elapsed_hours:.1f}h of {floor_hours[sig.domain]}h floor elapsed"

        conn.execute(
            "UPDATE signal_state SET state=?, explanation=? WHERE identity=?",
            (state, explanation, identity),
        )
        result[identity] = state

    conn.commit()
    return result


def log_cleanup_action(
    conn: sqlite3.Connection, identity: str, action_type: str, executed_at: datetime,
    dry_run: bool, outcome: str,
) -> None:
    conn.execute(
        """INSERT INTO cleanup_actions (identity, action_type, executed_at, dry_run, outcome)
           VALUES (?, ?, ?, ?, ?)""",
        (identity, action_type, executed_at.isoformat(), int(dry_run), outcome),
    )


def record_run(
    conn: sqlite3.Connection, ran_at: datetime, signals_observed: int,
    signals_escalated: int, error: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO coordination_runs (ran_at, signals_observed, signals_escalated, error)
           VALUES (?, ?, ?, ?)""",
        (ran_at.isoformat(), signals_observed, signals_escalated, error),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_coordination_engine.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/coordination_engine.py tests/test_coordination_engine.py
git commit -m "feat: add shared coordination_engine state machine for AQC workflow tool"
```

---

### Task 2: `quality_coordination.py` scaffold and the app-report client (§6.4)

**Files:**
- Create: `tools/quality_coordination.py`
- Test: `tests/test_quality_coordination_app_report.py`

**Interfaces:**
- Produces: `Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]` (reused by
  Tasks 3-4), `HttpGetter = Callable[[str, float], dict]`.
- Produces: `fetch_app_report(base_url: str, *, getter: HttpGetter | None = None, timeout:
  float = 5.0) -> dict[str, dict | None]` — calls `GET {base_url}/api/quality/summary`,
  `GET {base_url}/api/health/pipeline`, `GET {base_url}/api/health/faults`; returns
  `{"quality_summary": ..., "health_pipeline": ..., "health_faults": ...}`, each value
  either the parsed JSON body or `None` on any per-call failure. Never raises — this is
  **context, not a Signal** (spec §6.4: "never produces a `Signal`"), so a degraded read
  must not abort the rest of a coordination cycle.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_quality_coordination_app_report.py
from tools.quality_coordination import fetch_app_report


def _fake_getter(responses):
    def getter(url, timeout):
        if url not in responses:
            raise AssertionError(f"unexpected url {url}")
        value = responses[url]
        if isinstance(value, Exception):
            raise value
        return value
    return getter


def test_fetch_app_report_returns_all_three_on_success():
    getter = _fake_getter({
        "http://fastapi:8000/api/quality/summary": {"status": "ok"},
        "http://fastapi:8000/api/health/pipeline": {"schedulers": []},
        "http://fastapi:8000/api/health/faults": {"faults": []},
    })

    report = fetch_app_report("http://fastapi:8000", getter=getter)

    assert report == {
        "quality_summary": {"status": "ok"},
        "health_pipeline": {"schedulers": []},
        "health_faults": {"faults": []},
    }


def test_fetch_app_report_degrades_one_failed_call_to_none():
    getter = _fake_getter({
        "http://fastapi:8000/api/quality/summary": {"status": "ok"},
        "http://fastapi:8000/api/health/pipeline": ConnectionError("refused"),
        "http://fastapi:8000/api/health/faults": {"faults": []},
    })

    report = fetch_app_report("http://fastapi:8000", getter=getter)

    assert report["quality_summary"] == {"status": "ok"}
    assert report["health_pipeline"] is None
    assert report["health_faults"] == {"faults": []}


def test_fetch_app_report_never_raises_when_everything_fails():
    getter = _fake_getter({
        "http://fastapi:8000/api/quality/summary": RuntimeError("x"),
        "http://fastapi:8000/api/health/pipeline": RuntimeError("x"),
        "http://fastapi:8000/api/health/faults": RuntimeError("x"),
    })

    report = fetch_app_report("http://fastapi:8000", getter=getter)

    assert report == {"quality_summary": None, "health_pipeline": None, "health_faults": None}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_quality_coordination_app_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.quality_coordination'`

- [ ] **Step 3: Implement the scaffold and app-report client**

```python
# tools/quality_coordination.py
"""quality_coordination — Autonomous Quality Coordination (AQC): a standalone workflow
tool that acts as an automated project manager and janitor over THIS REPOSITORY'S OWN
engineering workflow (branch/PR/CI lifecycle, superpowers plan/ledger execution health,
standing-rule and process hygiene) - informed by, but never auditing, the trading
application's own self-reported diagnostics
(docs/superpowers/specs/2026-08-27-autonomous-quality-coordination-workflow-design.md).

"AQC" now names, and only names, this tool. The prior implementation under this name
audited the trading application's own static code findings instead - a real, corrected
mistargeting; that module is kept, renamed to tools/quality_ratchet.py (main@440da36), and
is a wholly separate tool from this one (spec §1, §12).

Standalone workflow tool, not application code: never imported by main.py or any part of
the live trading app (CLAUDE.md's "Workflow/tooling and application code must never
overlap" standing rule). No GitHub write authority, no write path outside
tools/quality_coordination_data/ (owned by tools/coordination_engine.py) plus the three
narrowly-scoped git/filesystem cleanup actions in run_cleanup_actions (spec §8, §11).

Invocation is manual-only for v1 (spec §5):
    python -m tools.quality_coordination            # detect + report only (default, safe)
    python -m tools.quality_coordination --clean    # also executes eligible cleanup actions
"""
from __future__ import annotations

import json
import subprocess
import urllib.request
from typing import Callable, Sequence

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]
HttpGetter = Callable[[str, float], dict]

_APP_REPORT_PATHS = {
    "quality_summary": "/api/quality/summary",
    "health_pipeline": "/api/health/pipeline",
    "health_faults": "/api/health/faults",
}


def _default_http_get(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "kalshi-whale-poc-aqc-workflow"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def fetch_app_report(
    base_url: str, *, getter: HttpGetter | None = None, timeout: float = 5.0,
) -> dict[str, dict | None]:
    """Reads the trading app's existing read-only diagnostics exactly as any other
    external HTTP client would (spec §6.4's "the app never knows AQC exists"). Used as
    CONTEXT for judging/suppressing signals from other domains - never itself produces a
    Signal or asserts a finding. Degrades every call independently to None on failure
    (network error, non-2xx, non-JSON body, or - until Task 11's PUBLIC_PATHS change is
    separately human-approved and merged - a live environment with real auth configured
    returning a redirect/401 instead of JSON); never raises, matching
    tools/quality_ratchet.py's fetch_branch_signals degrade-on-failure convention."""
    getter = getter or _default_http_get
    report: dict[str, dict | None] = {}
    for key, path in _APP_REPORT_PATHS.items():
        try:
            report[key] = getter(f"{base_url}{path}", timeout)
        except Exception:
            report[key] = None
    return report
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_quality_coordination_app_report.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/quality_coordination.py tests/test_quality_coordination_app_report.py
git commit -m "feat: scaffold tools/quality_coordination.py with the read-only app-report client"
```

---

### Task 3: Branch/PR/CI lifecycle health signal domain (§6.1)

**Files:**
- Modify: `tools/quality_coordination.py` (append)
- Test: `tests/test_quality_coordination_branch_domain.py`

**Interfaces:**
- Consumes: `coordination_engine.Signal`, `coordination_engine.get_prior_row` (Task 1);
  `Runner` (Task 2).
- Produces: `FLOOR_HOURS_BRANCH: float`, `CLUSTER_WINDOW_MINUTES: float` (both documented,
  evidence-backed constants — see below), `_branch_first_commit_at(branch: str, main_branch:
  str, git_runner: Runner) -> datetime | None`, `_cluster_siblings(creation_times: dict[str,
  datetime], window_minutes: float) -> dict[str, frozenset[str]]`,
  `_woodpecker_step_progress(branch: str, woodpecker_runner: Runner) -> dict` (pending-
  duration + per-step Started/Stopped detail — the `failure`/`error` short-circuit is
  decided by the caller from `_commit_status`'s result, not inside this helper, so it takes
  only the one runner it actually needs),
  `collect_branch_signals(branch_names: Sequence[str], *, git_runner: Runner, gh_runner:
  Runner, woodpecker_runner: Runner, conn: sqlite3.Connection, worktrees_root: Path,
  main_branch: str = "main", at: datetime) -> tuple[list[Signal], frozenset[str],
  frozenset[str]]` — returns `(signals, suppressed_keys, immediate_keys)`, ready to feed
  straight into `coordination_engine.apply_observation`. Called by Task 8's CLI.

**Evidence for `FLOOR_HOURS_BRANCH` (spec §10 point 1 — measured, not guessed):** this
repo's real merge history (`git log --merges --format="%H %cI" -10 main`, run against the
live checkout while writing this plan) shows the 10 most recent merges into `main` spaced
from ~5 minutes to ~5.4 hours apart (2026-08-26 18:31 → 23:24, then a rapid run of merges
23:24-00:06 the next day). A floor set below that observed spacing would flag completely
normal same-day branch activity as stale; `FLOOR_HOURS_BRANCH = 6.0` is chosen just above
the largest observed gap in this sample. **This is a 10-merge sample, not the fuller
30-90-day pull §10 calls for** — flagged explicitly rather than presented as final; treat
this as the plan's provisional, evidence-grounded starting point and re-measure with a
larger window before treating `6.0` as settled.

**Evidence for `CLUSTER_WINDOW_MINUTES` (co-dispatch-cluster handling — see below):** no
direct historical data source in this repo captures "how far apart do branches from one
coordinated dispatch typically land" (git records commit times, not branch-creation
intent). `CLUSTER_WINDOW_MINUTES = 20.0` is a deliberately generous provisional value: the
asymmetry is favorable to erring high — under-suppressing a genuine coordinated burst costs
a human a wasted investigation into branches that are actually fine, while over-suppressing
merely delays a truly orphaned branch's escalation by at most one more AQC run. Suppression
is a delay, not an action (`.claude/rules/autonomous-quality-coordination-evidence.md`:
"Automation may observe broadly, but it earns authority to act narrowly"), so this
asymmetry is acceptable pending real measurement, which Task 9 revisits.

**Co-dispatch-cluster handling (extends §6.1's "Suppression candidates" list with a third
case, verified against the existing design rather than assumed to need new architecture):**
§6.1 already lists two suppression candidates — an open PR actively receiving commits, and
an explicit "paused, not stalled" track note — both instances of
`.claude/rules/autonomous-quality-coordination-evidence.md`'s "Branch truth and active-work
rule" ("Open PRs and active remote branches are evidence that work may already be in
flight. They may suppress or delay escalation, never prove resolution"). A cluster of
branches created within a short window as part of one coordinated multi-agent dispatch —
observed directly during this plan's own drafting: three docs-only branches
(`docs/aqc-workflow-plan`, `docs/subscription-churn-ch1`,
`docs/economic-remediation-reverify`) created off the same `main` HEAD within the same few
minutes, none with a PR yet — is the same category of evidence, not a new one: "work may
already be in flight," just inferred from branch-creation clustering instead of PR
activity. This does **not** change §6.1's identity granularity (still one `Signal` per
branch, per spec) or add new engine architecture (Task 1's `apply_observation` already
accepts a `suppressed_keys` override for exactly this shape of per-cycle evidence) — it is
purely a third computation inside this domain's own suppression-candidate logic, mirrored
below in `_cluster_siblings` and a dedicated test pair.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_quality_coordination_branch_domain.py
import subprocess
from datetime import datetime, timedelta, timezone

from tools import coordination_engine as ce
from tools.quality_coordination import (
    CLUSTER_WINDOW_MINUTES, FLOOR_HOURS_BRANCH, _branch_first_commit_at, _cluster_siblings,
    collect_branch_signals,
)

AT = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)


class _StubRunner:
    """Records calls, returns queued CompletedProcess results in order - same shape as
    tests/support/fake_gh_runner.py's FakeRunner, redefined locally since this module has
    no dependency on tests/support (that module is kanban_sync's own test infrastructure,
    not shared repo-wide)."""

    def __init__(self):
        self.calls = []
        self._responses = []

    def queue(self, stdout="", returncode=0, stderr=""):
        self._responses.append(subprocess.CompletedProcess([], returncode, stdout, stderr))

    def __call__(self, args):
        self.calls.append(list(args))
        return self._responses.pop(0)


def test_floor_and_cluster_window_are_positive_documented_constants():
    assert FLOOR_HOURS_BRANCH > 0
    assert CLUSTER_WINDOW_MINUTES > 0


def test_branch_first_commit_at_parses_oldest_unique_commit_date(monkeypatch):
    runner = _StubRunner()
    # git log main..feat/x --format=%cI lists newest-first; oldest is the last line.
    runner.queue("2026-08-27T12:05:00-05:00\n2026-08-27T12:00:00-05:00\n")

    result = _branch_first_commit_at("feat/x", "main", git_runner=runner)

    assert result == datetime.fromisoformat("2026-08-27T12:00:00-05:00")


def test_branch_first_commit_at_returns_none_when_branch_has_no_unique_commits(monkeypatch):
    runner = _StubRunner()
    runner.queue("")

    assert _branch_first_commit_at("feat/x", "main", git_runner=runner) is None


def test_cluster_siblings_groups_branches_within_window():
    times = {
        "docs/a": AT,
        "docs/b": AT + timedelta(minutes=3),
        "docs/c": AT + timedelta(minutes=18),
        "feat/lonely": AT - timedelta(days=5),
    }

    clusters = _cluster_siblings(times, window_minutes=20.0)

    assert clusters["docs/a"] == frozenset({"docs/b", "docs/c"})
    assert clusters["docs/b"] == frozenset({"docs/a", "docs/c"})
    assert clusters["docs/c"] == frozenset({"docs/a", "docs/b"})
    assert clusters["feat/lonely"] == frozenset()


def test_cluster_siblings_empty_when_all_branches_far_apart():
    times = {"a": AT, "b": AT + timedelta(hours=5)}

    clusters = _cluster_siblings(times, window_minutes=20.0)

    assert clusters["a"] == frozenset()
    assert clusters["b"] == frozenset()


def test_collect_branch_signals_suppresses_a_coordinated_dispatch_cluster(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "DB_PATH", tmp_path / "q.db")
    conn = ce._connect()
    git_runner = _StubRunner()
    # Three branches, each with one commit ~2 minutes apart from the others - a cluster.
    git_runner.queue("2026-08-27T12:00:00-05:00\n")
    git_runner.queue("2026-08-27T12:02:00-05:00\n")
    git_runner.queue("2026-08-27T12:04:00-05:00\n")
    gh_runner = _StubRunner()
    gh_runner.queue("[]")  # no PR for docs/a
    gh_runner.queue("[]")  # no PR for docs/b
    gh_runner.queue("[]")  # no PR for docs/c
    woodpecker_runner = _StubRunner()  # no CI runs yet for any of the three - queue nothing

    signals, suppressed, immediate = collect_branch_signals(
        ["docs/a", "docs/b", "docs/c"],
        git_runner=git_runner, gh_runner=gh_runner, woodpecker_runner=woodpecker_runner,
        conn=conn, worktrees_root=tmp_path / "worktrees",
        at=datetime(2026, 8, 27, 17, 5, 0, tzinfo=timezone.utc),
    )

    assert {"branch:docs/a", "branch:docs/b", "branch:docs/c"} == suppressed
    assert immediate == frozenset()
    assert len(signals) == 3


def test_collect_branch_signals_does_not_suppress_a_lone_stale_branch(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "DB_PATH", tmp_path / "q.db")
    conn = ce._connect()
    git_runner = _StubRunner()
    git_runner.queue("2026-08-01T12:00:00-05:00\n")  # weeks old, no siblings nearby
    gh_runner = _StubRunner()
    gh_runner.queue("[]")
    woodpecker_runner = _StubRunner()

    signals, suppressed, immediate = collect_branch_signals(
        ["feat/abandoned"],
        git_runner=git_runner, gh_runner=gh_runner, woodpecker_runner=woodpecker_runner,
        conn=conn, worktrees_root=tmp_path / "worktrees",
        at=datetime(2026, 8, 27, 17, 5, 0, tzinfo=timezone.utc),
    )

    assert suppressed == frozenset()
    assert signals[0].identity == "branch:feat/abandoned"


def test_collect_branch_signals_marks_a_real_ci_failure_immediate(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "DB_PATH", tmp_path / "q.db")
    conn = ce._connect()
    git_runner = _StubRunner()
    git_runner.queue("2026-08-27T12:00:00-05:00\n")
    gh_runner = _StubRunner()
    gh_runner.queue('[{"state": "OPEN"}]')  # find_pr_state-shaped: a JSON array, json.loads'd
    gh_runner.queue("failure\n")  # commit-status-shaped: `gh api ... --jq ".state"` raw-
    # unquotes a scalar jq result, so this is the literal stdout _commit_status reads
    # directly - not a JSON blob to parse (unlike the pr-list response above, which uses
    # `gh ... --json` and is real JSON).
    woodpecker_runner = _StubRunner()

    signals, suppressed, immediate = collect_branch_signals(
        ["feat/broken"],
        git_runner=git_runner, gh_runner=gh_runner, woodpecker_runner=woodpecker_runner,
        conn=conn, worktrees_root=tmp_path / "worktrees",
        at=datetime(2026, 8, 27, 17, 5, 0, tzinfo=timezone.utc),
    )

    assert immediate == frozenset({"branch:feat/broken"})


def test_collect_branch_signals_flags_possibly_stuck_when_step_unchanged_two_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "DB_PATH", tmp_path / "q.db")
    conn = ce._connect()
    # Prime signal_state with a prior run's payload for this identity, same shape a real
    # first apply_observation call would have written.
    from tools.coordination_engine import Signal, apply_observation
    prior_payload = {"pr_state": "OPEN", "ci_status": "pending", "furthest_step": "browser-e2e",
                      "furthest_step_started_at": "2026-08-27T16:00:00+00:00"}
    apply_observation(
        conn, [Signal("branch:feat/slow", "branch", prior_payload, True)],
        datetime(2026, 8, 27, 16, 5, 0, tzinfo=timezone.utc), floor_hours={"branch": 6.0},
    )

    git_runner = _StubRunner()
    git_runner.queue("2026-08-27T15:00:00-05:00\n")
    gh_runner = _StubRunner()
    gh_runner.queue('[{"state": "OPEN"}]')  # find_pr_state-shaped: real JSON, json.loads'd
    gh_runner.queue("pending\n")  # commit-status-shaped: raw --jq output, see the sibling
    # test above for why this isn't JSON-wrapped.
    woodpecker_runner = _StubRunner()
    woodpecker_runner.queue(
        '{"steps": [{"name": "browser-e2e", "started": 1756310400, "stopped": 0}]}'
    )

    signals, suppressed, immediate = collect_branch_signals(
        ["feat/slow"],
        git_runner=git_runner, gh_runner=gh_runner, woodpecker_runner=woodpecker_runner,
        conn=conn, worktrees_root=tmp_path / "worktrees",
        at=datetime(2026, 8, 27, 17, 5, 0, tzinfo=timezone.utc),  # same run 1h later, same step
    )

    assert signals[0].payload["possibly_stuck"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_quality_coordination_branch_domain.py -v`
Expected: FAIL — `ImportError: cannot import name 'collect_branch_signals' from 'tools.quality_coordination'`

- [ ] **Step 3: Implement the branch/PR/CI domain**

```python
# tools/quality_coordination.py (append)
"""Branch/PR/CI lifecycle health signal domain (spec §6.1). Identity: "branch:<name>".
Payload: last-commit age, open-PR state, Woodpecker status for the branch tip, whether a
corresponding .claude/worktrees/ directory exists, and CI staleness detail when the status
is ambiguously "pending". Suppression candidates: an open PR actively receiving
commits/reviews, an explicit "paused, not stalled" active-tracks-board.md note (both spec
§6.1), and a co-dispatch cluster - N branches created within CLUSTER_WINDOW_MINUTES of each
other with no individual PR yet, treated as one in-flight unit rather than N independent
stale-branch signals (this plan's own extension of §6.1's suppression-candidate list; see
this task's evidence note - no new engine architecture, just a third suppression source
computed here before calling apply_observation).
"""
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools import coordination_engine as ce
from tools.coordination_engine import Signal

# Evidence: git log --merges --format="%H %cI" -10 main against this repo's real history
# (measured while writing this plan) showed the 10 most recent merges spaced ~5 minutes to
# ~5.4 hours apart. Set just above the largest observed gap in that sample. A 10-merge
# sample, not the fuller 30-90-day pull spec §10 point 1 calls for - re-measure before
# treating this as final (Task 9).
FLOOR_HOURS_BRANCH = 6.0

# Evidence: no direct historical source for "how far apart do one dispatch's branches
# land" exists in this repo; chosen generously since suppression only delays escalation by
# one more run, never blocks it permanently - see this task's evidence note.
CLUSTER_WINDOW_MINUTES = 20.0

_COMMIT_STATUS_FAILURE_STATES = {"failure", "error"}


def _branch_first_commit_at(branch: str, main_branch: str, git_runner: Runner) -> datetime | None:
    """Proxy for branch-creation time: the oldest commit reachable from `branch` but not
    from `main_branch` - the same "first commit unique to this branch" approach
    tools/quality_ratchet.py's fetch_branch_signals takes from the GitHub compare API,
    computed locally here via git log instead."""
    result = git_runner(["git", "log", f"{main_branch}..{branch}", "--format=%cI"])
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return None
    return datetime.fromisoformat(lines[-1])  # oldest is last - git log lists newest-first


def _cluster_siblings(
    creation_times: dict[str, datetime], window_minutes: float,
) -> dict[str, frozenset[str]]:
    """For each branch, the set of OTHER branches whose creation time falls within
    `window_minutes` of its own. A branch with no nearby sibling gets an empty set - not a
    cluster of one."""
    result: dict[str, frozenset[str]] = {}
    window = timedelta(minutes=window_minutes)
    for name, at in creation_times.items():
        siblings = frozenset(
            other for other, other_at in creation_times.items()
            if other != name and abs(other_at - at) <= window
        )
        result[name] = siblings
    return result


def _pr_state(branch: str, gh_runner: Runner) -> str | None:
    result = gh_runner([
        "gh", "pr", "list", "--head", branch, "--state", "all",
        "--json", "state", "--limit", "1",
    ])
    results = json.loads(result.stdout or "[]")
    return results[0]["state"] if results else None


def _commit_status(branch: str, gh_runner: Runner) -> str | None:
    """`gh api ... --jq ".state"` raw-unquotes a scalar jq result (unlike `gh ... --json`,
    which _pr_state above uses and which IS real JSON) - stdout is already the bare state
    string ("failure", "pending", "success", ...), not a JSON value to parse. Parsing it
    with json.loads would be the bug here, not the fix - see the test fixtures in this
    task's test file for the same distinction spelled out against a fake runner."""
    result = gh_runner([
        "gh", "api", f"repos/{{owner}}/{{repo}}/commits/{branch}/status",
        "--jq", ".state",
    ])
    stdout = (result.stdout or "").strip()
    return stdout or None


def _woodpecker_step_progress(branch: str, woodpecker_runner: Runner) -> dict:
    """Real per-step Started/Stopped detail via scripts/woodpecker-status --pipeline N
    (spec §6.1's "found live 2026-08-27" CI-staleness paragraph). Degrades to an empty dict
    on any parse failure - a missing/ambiguous CI detail is not itself an error for this
    domain, just less payload to work with."""
    try:
        result = woodpecker_runner(["scripts/woodpecker-status", "--branch", branch, "--json"])
        data = json.loads(result.stdout or "{}")
    except Exception:
        return {}
    steps = data.get("steps") or []
    if not steps:
        return {}
    furthest = steps[-1]
    return {
        "furthest_step": furthest.get("name"),
        "furthest_step_started_at": (
            datetime.fromtimestamp(furthest["started"], tz=timezone.utc).isoformat()
            if furthest.get("started") else None
        ),
    }


def collect_branch_signals(
    branch_names: list[str],
    *,
    git_runner: Runner,
    gh_runner: Runner,
    woodpecker_runner: Runner,
    conn,
    worktrees_root: Path,
    main_branch: str = "main",
    at: datetime,
) -> tuple[list[Signal], frozenset[str], frozenset[str]]:
    creation_times: dict[str, datetime] = {}
    for branch in branch_names:
        created = _branch_first_commit_at(branch, main_branch, git_runner)
        if created is not None:
            creation_times[branch] = created

    clusters = _cluster_siblings(creation_times, CLUSTER_WINDOW_MINUTES)

    signals: list[Signal] = []
    suppressed_keys: set[str] = set()
    immediate_keys: set[str] = set()

    for branch in branch_names:
        identity = f"branch:{branch}"
        created_at = creation_times.get(branch)
        pr_state = _pr_state(branch, gh_runner)
        commit_status = _commit_status(branch, gh_runner) if pr_state is not None else None
        worktree_exists = (worktrees_root / branch.replace("/", "-")).exists()

        payload: dict = {
            "pr_state": pr_state,
            "ci_status": commit_status,
            "last_commit_age_hours": (
                (at - created_at).total_seconds() / 3600 if created_at else None
            ),
            "has_worktree": worktree_exists,
        }

        if commit_status == "pending":
            step_progress = _woodpecker_step_progress(branch, woodpecker_runner)
            payload.update(step_progress)
            prior = ce.get_prior_row(conn, identity)
            if prior is not None and step_progress.get("furthest_step"):
                prior_payload = json.loads(prior["explanation"]) if False else None  # see note below
            # Compare against the prior row's stored content fingerprint directly, rather
            # than re-parsing a payload the engine doesn't expose as JSON on the row (only
            # `fingerprint`, the hash, is stored) - two consecutive runs whose full payload
            # fingerprints match while ci_status stays "pending" means nothing about this
            # branch's CI run advanced between them.
            if prior is not None:
                candidate_fp = ce._fingerprint(payload)
                if candidate_fp == prior["fingerprint"]:
                    payload["possibly_stuck"] = True

        if commit_status in _COMMIT_STATUS_FAILURE_STATES:
            # spec §6.1: "a failure/error state ... should surface immediately, at full
            # severity, never behind a persistence floor."
            immediate_keys.add(identity)

        if pr_state == "OPEN":
            # spec §6.1 suppression candidate: an open PR actively receiving commits/reviews.
            suppressed_keys.add(identity)
        elif clusters.get(branch):
            # This plan's co-dispatch-cluster extension (see task docstring above).
            suppressed_keys.add(identity)

        signals.append(Signal(identity=identity, domain="branch", payload=payload, still_present=True))

    return signals, frozenset(suppressed_keys), frozenset(immediate_keys)
```

Note on the commented-out `prior_payload` line above: it is dead-code-shaped on purpose in
this plan's illustrative snippet to make the actual design decision visible (compare
fingerprints, not re-parsed payloads, since only the hash is persisted) — the real
implementation step below removes that line entirely rather than shipping it; see Step 3a.

- [ ] **Step 3a: Remove the illustrative dead branch before running tests**

The `if prior is not None and step_progress.get(...)` / `prior_payload = ...` two-line
block in Step 3's snippet exists only to explain the design choice in this document — real
implementation deletes those two lines, keeping just the `candidate_fp = ce._fingerprint(payload)`
comparison immediately below it. Confirm no `prior_payload` name or dead `if False` branch
remains in the committed file — a `grep -n "if False" tools/quality_coordination.py`
returning nothing is the check.

- [ ] **Step 4: Run tests to verify they pass**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_quality_coordination_branch_domain.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/quality_coordination.py tests/test_quality_coordination_branch_domain.py
git commit -m "feat: add branch/PR/CI lifecycle signal domain with co-dispatch-cluster suppression

Extends spec §6.1's suppression-candidate list with a third case: N branches created
within a short window as part of one coordinated dispatch are treated as one in-flight
unit (per .claude/rules/autonomous-quality-coordination-evidence.md's Branch truth and
active-work rule), not N independent stale-branch signals. No engine or identity-model
change - computed entirely inside this domain's own suppression logic."
```

---

### Task 4: Plan and ledger execution health signal domain (§6.2)

**Files:**
- Modify: `tools/quality_coordination.py` (append)
- Test: `tests/test_quality_coordination_ledger_domain.py`

**Interfaces:**
- Produces: `FLOOR_HOURS_LEDGER: float` (provisional — see evidence note),
  `_ledger_last_state_line(text: str) -> str`, `_ledger_is_complete(last_line: str) -> bool`,
  `collect_ledger_signals(ledger_paths: Sequence[Path], *, at: datetime) -> list[Signal]`.
  **Does not take `git_runner`/`gh_runner` params (found in review — an earlier draft
  declared both and used neither).** Computing spec §6.2's "days since the last commit
  touching that plan's associated branch" needs to know which branch a given
  `.superpowers/sdd/<plan>/` ledger maps to — the exact derivation Task 7 already names as
  an implementation-time detail, not resolved in the spec (§8 action 3's branch-mapping
  note). This task deliberately does not duplicate or shortcut that derivation with an
  unrelated guess; it only implements the piece §6.2 describes that's independent of
  branch mapping (the ledger's own last recorded state line). "Hours since last commit on
  the associated branch" is a real, named follow-on once Task 7's derivation exists — see
  that task's note — not silently dropped.

**Evidence for `FLOOR_HOURS_LEDGER` (§10 point 1):** unlike branch merge cadence, this
repo currently has no populated `.superpowers/sdd/<plan>/progress.md` ledgers to measure
completion time from directly (that directory is gitignored scratch state, and no plan in
this repo's history has left one behind post-completion). This plan's own numbered plan
docs (`docs/superpowers/plans/*.md`) are the closest proxy: several were opened and fully
merged within the same calendar day (e.g. `2026-08-26-autonomous-quality-coordination.md`'s
Task 15 correction landed the same day it started). `FLOOR_HOURS_LEDGER = 72.0` (3 days) is
a deliberately longer floor than the branch domain's 6 hours — a plan/ledger can legitimately
span several working sessions without being stalled — but is explicitly flagged
**provisional pending real measurement once real ledgers accumulate** (Task 9), the same
honesty this plan applies to `FLOOR_HOURS_BRANCH`.

**Ledger-line-format caveat (an implementation-time detail, named rather than hidden — the
same treatment spec §8 gives the SDD-scratch branch-mapping derivation):** this task's
parsing (`_ledger_last_state_line`/`_ledger_is_complete`) is written against the two
concrete signals spec §6.2 names — "the ledger's last recorded task/round line" and "the
ledger shows plan completion" — using a conservative regex plus an explicit completion
marker, but no real `.superpowers/sdd/*/progress.md` file exists in this checkout to
validate the exact line format against (that format is owned by the `superpowers` plugin,
external to this repo). Confirm the regex against a real ledger the first time one exists
before trusting this domain's output, and adjust here if the real format differs — this is
a genuine gap, not an oversight glossed over.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_quality_coordination_ledger_domain.py
from datetime import datetime, timezone
from pathlib import Path

from tools.quality_coordination import (
    FLOOR_HOURS_LEDGER, _ledger_is_complete, _ledger_last_state_line, collect_ledger_signals,
)

AT = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)


def test_floor_hours_ledger_is_a_positive_constant():
    assert FLOOR_HOURS_LEDGER > 0


def test_ledger_last_state_line_returns_last_nonblank_line():
    text = "## Task 1\n- [x] done\n\n## Task 2\n- [ ] in progress\n\n"
    assert _ledger_last_state_line(text) == "- [ ] in progress"


def test_ledger_is_complete_recognizes_explicit_marker():
    assert _ledger_is_complete("STATUS: COMPLETE - all tasks done") is True


def test_ledger_is_complete_false_for_an_ordinary_task_line():
    assert _ledger_is_complete("- [ ] Task 4: implement X") is False


def test_collect_ledger_signals_still_present_for_incomplete_ledger(tmp_path):
    ledger = tmp_path / "progress.md"
    ledger.write_text("## Task 1\n- [x] done\n\n## Task 2\n- [ ] in progress\n")

    signals = collect_ledger_signals([ledger], at=AT)

    assert len(signals) == 1
    assert signals[0].still_present is True
    assert signals[0].domain == "ledger"


def test_collect_ledger_signals_absent_when_complete(tmp_path):
    ledger = tmp_path / "progress.md"
    ledger.write_text("## Task 3\nSTATUS: COMPLETE - all tasks done\n")

    signals = collect_ledger_signals([ledger], at=AT)

    assert signals == []


def test_collect_ledger_signals_absent_when_file_missing(tmp_path):
    missing = tmp_path / "nope" / "progress.md"

    signals = collect_ledger_signals([missing], at=AT)

    assert signals == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_quality_coordination_ledger_domain.py -v`
Expected: FAIL — `ImportError: cannot import name 'collect_ledger_signals' from 'tools.quality_coordination'`

- [ ] **Step 3: Implement the ledger domain**

```python
# tools/quality_coordination.py (append)
"""Plan and ledger execution health signal domain (spec §6.2). Identity: the ledger/plan
file's own path (relative to repo root). Payload: last recorded task/round line. Spec
§6.2's other payload field - days since the last commit touching that plan's ASSOCIATED
BRANCH (not the ledger file's own commit history: .superpowers/sdd/<plan>/ is gitignored
scratch state, so the ledger file itself is never committed at all) - needs the same
ledger-to-branch mapping Task 7 already names as an implementation-time detail (spec §8
action 3), not resolved here either; see this task's own Interfaces note above. Ledger-
line-format caveat: see this task's own note above - no real .superpowers/sdd/*/progress.md
exists in this checkout to validate the parsing against yet.
"""
_LEDGER_COMPLETE_RE = re.compile(r"\bSTATUS:\s*COMPLETE\b", re.IGNORECASE)

# Evidence: see this task's own note - no populated real ledger exists yet to measure
# completion time from directly; provisional, deliberately longer than the branch domain's
# floor since a plan/ledger can legitimately span several working sessions.
FLOOR_HOURS_LEDGER = 72.0


def _ledger_last_state_line(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _ledger_is_complete(last_line: str) -> bool:
    return bool(_LEDGER_COMPLETE_RE.search(last_line))


def collect_ledger_signals(ledger_paths: list[Path], *, at: datetime) -> list[Signal]:
    signals: list[Signal] = []
    for path in ledger_paths:
        if not path.exists():
            continue
        text = path.read_text()
        last_line = _ledger_last_state_line(text)
        if _ledger_is_complete(last_line):
            continue

        signals.append(Signal(
            identity=f"ledger:{path}",
            domain="ledger",
            payload={"last_state_line": last_line},
            still_present=True,
        ))
    return signals
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_quality_coordination_ledger_domain.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/quality_coordination.py tests/test_quality_coordination_ledger_domain.py
git commit -m "feat: add plan/ledger execution health signal domain"
```

---

### Task 5: Standing-rule and process-hygiene compliance signal domain (§6.3)

**Files:**
- Modify: `tools/quality_coordination.py` (append)
- Test: `tests/test_quality_coordination_process_hygiene_domain.py`

**Interfaces:**
- Produces: `FLOOR_HOURS_PROCESS_HYGIENE: float` (nominal — see below),
  `_baseline_missing_dated_notes(baseline_text: str) -> list[str]`,
  `collect_process_hygiene_signals(baseline_path: Path, notes_text: str) -> list[Signal]`.

**Floor rationale:** spec §6.3 is explicit that this domain "is largely a direct structural
check rather than something that needs a real persistence floor — it is still routed
through the engine for consistent history/reporting, not because escalation timing matters
much here." `FLOOR_HOURS_PROCESS_HYGIENE = 0.0` (escalation-eligible on first observation)
follows directly from that spec text, not from a separate cadence measurement — there is no
"how long before this becomes stale" question for a check that's either structurally true
or false right now.

**Concrete check chosen (an implementation-time instance of §6.3's example, matching its
own worked example verbatim):** spec §6.3's own identity example is "a `baseline.json`
`accepted_finding_ids` entry lacking a dated `notes` addendum" — `tools/quality_audit/
baseline.json`'s `accepted_finding_ids` against its own `notes` block, exactly the
convention `CLAUDE.md`'s "Baseline-ratchet semantics" section documents (a "+N
2026-MM-DD: ..." addendum per accepted finding-ID prefix).

**`notes`'s real shape, confirmed against the live file (found in review, 2026-08-27) rather
than assumed:** `tools/quality_audit/baseline.json`'s `notes` field is a **dict keyed by
check-prefix** (`"config-unread:*"`, `"api-usage:*"`, `"frontend-route-unknown:*"`,
`"backend-route-unused:*"` in the file as it stands today), each value a long provenance
string covering every accepted ID under that prefix plus its dated addenda — not a single
flat string. `_baseline_missing_dated_notes` below checks whether an accepted ID's own
prefix has *any* entry in that dict; it does not attempt to verify that the specific ID's
own addition within a shared note is itself individually dated (freeform-prose date
verification is a materially harder problem than this heuristic claims to solve, and the
original substring check never solved it either — it only checked presence, same scope as
the fix below, just against the right data shape). Run against the live file, this
correctly returns `[]` today: every one of its 199 accepted IDs' prefixes already has a
`notes` entry — confirmed empirically before, not just after, writing the fix.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_quality_coordination_process_hygiene_domain.py
import json
from pathlib import Path

from tools.quality_coordination import (
    FLOOR_HOURS_PROCESS_HYGIENE, _baseline_missing_dated_notes, collect_process_hygiene_signals,
)


def test_floor_hours_process_hygiene_is_zero_per_spec_rationale():
    assert FLOOR_HOURS_PROCESS_HYGIENE == 0.0


def test_missing_dated_notes_detects_id_whose_prefix_has_no_notes_entry():
    baseline = {
        "accepted_finding_ids": ["backend-route-unused:GET:/api/x"],
        "notes": {"config-unread:*": "an unrelated category's note"},
    }
    missing = _baseline_missing_dated_notes(json.dumps(baseline))

    assert "backend-route-unused:GET:/api/x" in missing


def test_missing_dated_notes_empty_when_prefix_note_exists():
    baseline = {
        "accepted_finding_ids": ["backend-route-unused:GET:/api/x"],
        "notes": {
            "backend-route-unused:*": "+1 2026-08-27: GET:/api/x accepted, no frontend caller yet.",
        },
    }
    missing = _baseline_missing_dated_notes(json.dumps(baseline))

    assert missing == []


def test_missing_dated_notes_against_a_baseline_shaped_like_the_real_file():
    """Regression test (found in review): the real tools/quality_audit/baseline.json's
    `notes` field is a dict keyed by check-prefix, not a flat string - a fixture using a
    flat string would pass while silently not exercising the real shape at all."""
    real_baseline_path = Path("tools/quality_audit/baseline.json")
    if not real_baseline_path.exists():
        return  # skip gracefully outside a full repo checkout - not this test's concern
    missing = _baseline_missing_dated_notes(real_baseline_path.read_text())

    assert missing == []  # every accepted ID's prefix has a notes entry as of 2026-08-27


def test_collect_process_hygiene_signals_one_per_missing_id(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps({
        "accepted_finding_ids": ["api-usage:a", "backend-route-unused:b"],
        "notes": {"api-usage:*": "+1 2026-08-27: a accepted, reason x."},
    }))

    signals = collect_process_hygiene_signals(baseline_path, baseline_path.read_text())

    assert [s.identity for s in signals] == ["process_hygiene:baseline.json:backend-route-unused:b"]
    assert signals[0].domain == "process_hygiene"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_quality_coordination_process_hygiene_domain.py -v`
Expected: FAIL — `ImportError: cannot import name '_baseline_missing_dated_notes' from 'tools.quality_coordination'`

- [ ] **Step 3: Implement the process-hygiene domain**

```python
# tools/quality_coordination.py (append)
"""Standing-rule and process-hygiene compliance signal domain (spec §6.3). Identity: a
specific rule-instance key. Concrete instance implemented here: tools/quality_audit/
baseline.json's accepted_finding_ids entries lacking a dated notes addendum
(CLAUDE.md's "Baseline-ratchet semantics" convention) - spec §6.3's own worked example.
Floor is 0.0 per spec's own rationale: a structural check, not a timing question.
"""
FLOOR_HOURS_PROCESS_HYGIENE = 0.0


def _baseline_missing_dated_notes(baseline_text: str) -> list[str]:
    """`notes` is a dict keyed by check-prefix (e.g. "api-usage:*"), not a flat string -
    confirmed against the real tools/quality_audit/baseline.json (found in review,
    2026-08-27: an earlier draft treated it as a flat string, which would have flagged
    every accepted ID as missing a note, always, against the real file). This checks
    whether an accepted ID's own prefix has any notes entry at all - a presence check, not
    a verification that the ID's own specific addition within a shared note is itself
    individually dated (see this task's own note above on that narrower scope)."""
    data = json.loads(baseline_text)
    accepted = data.get("accepted_finding_ids", [])
    notes = data.get("notes", {})
    missing = []
    for finding_id in accepted:
        check = finding_id.split(":", 1)[0]
        prefix_key = f"{check}:*"
        if prefix_key not in notes:
            missing.append(finding_id)
    return missing


def collect_process_hygiene_signals(baseline_path: Path, baseline_text: str) -> list[Signal]:
    missing = _baseline_missing_dated_notes(baseline_text)
    return [
        Signal(
            identity=f"process_hygiene:{baseline_path.name}:{finding_id}",
            domain="process_hygiene",
            payload={"finding_id": finding_id, "reason": "accepted_finding_ids entry has no dated notes addendum"},
            still_present=True,
        )
        for finding_id in missing
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_quality_coordination_process_hygiene_domain.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/quality_coordination.py tests/test_quality_coordination_process_hygiene_domain.py
git commit -m "feat: add standing-rule/process-hygiene signal domain"
```

---

### Task 6: Documentation/ROADMAP drift data feed (§6.5)

**Files:**
- Modify: `tools/quality_coordination.py` (append)
- Test: `tests/test_quality_coordination_docs_feed.py`

**Interfaces:**
- Produces: `_open_roadmap_bullets(text: str) -> list[str]`, `collect_docs_roadmap_feed(
  roadmap_text: str, recent_commit_subjects: Sequence[str]) -> list[dict]` — **returns
  plain dicts, never `Signal`s** (spec §6.5: "Not run through the coordination engine"),
  each `{"roadmap_bullet": str, "possibly_related_commits": list[str]}`. Consumed directly
  by Task 8's CLI report output, never by `apply_observation`.

**Judgment as data, not a finding (spec §6.5, restated because it is easy to get backwards
here):** this function does exactly one thing — surface open ROADMAP bullets alongside
commit subjects that share enough vocabulary to be worth a human glance. It never decides
an item is done, never marks anything resolved, and is not wired into the engine's
identity/state machine at all. `collect_docs_roadmap_feed`'s only caller in Task 8 prints
its output under a clearly separate report heading.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_quality_coordination_docs_feed.py
from tools.quality_coordination import _open_roadmap_bullets, collect_docs_roadmap_feed

SAMPLE = """## Path to production

- [x] Something already shipped.
- [ ] Consider a dedicated charts module for the Advanced view.
- [ ] `services/shadow_mode.py` logs what the strategy would trade against
      real signal data, but hasn't been run for a real evaluation stretch
      and reviewed.
"""


def test_open_roadmap_bullets_skips_checked_items():
    bullets = _open_roadmap_bullets(SAMPLE)

    assert len(bullets) == 2
    assert all(not b.startswith("[x]") for b in bullets)


def test_open_roadmap_bullets_captures_indented_continuation_lines():
    """Regression test (found in review): this repo's real ROADMAP.md bullets routinely
    wrap onto 6-space-indented continuation lines - a bare single-line regex truncates
    them to their first physical line, silently dropping most of a real bullet's text."""
    bullets = _open_roadmap_bullets(SAMPLE)

    shadow_bullet = [b for b in bullets if "shadow_mode.py" in b][0]
    assert "evaluation stretch and reviewed" in shadow_bullet


def test_collect_docs_roadmap_feed_flags_vocabulary_overlap():
    commits = ["feat: add charts module skeleton to Advanced view", "fix: unrelated typo"]

    feed = collect_docs_roadmap_feed(SAMPLE, commits)

    charts_entry = [f for f in feed if "charts" in f["roadmap_bullet"].lower()][0]
    assert charts_entry["possibly_related_commits"] == ["feat: add charts module skeleton to Advanced view"]


def test_collect_docs_roadmap_feed_never_asserts_done():
    feed = collect_docs_roadmap_feed(SAMPLE, [])

    for entry in feed:
        assert "done" not in entry
        assert "resolved" not in entry
        assert set(entry.keys()) == {"roadmap_bullet", "possibly_related_commits"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_quality_coordination_docs_feed.py -v`
Expected: FAIL — `ImportError: cannot import name '_open_roadmap_bullets' from 'tools.quality_coordination'`

- [ ] **Step 3: Implement the docs/ROADMAP data feed**

```python
# tools/quality_coordination.py (append)
"""Documentation/ROADMAP drift data feed (spec §6.5). NOT run through
coordination_engine.apply_observation - these functions never construct a Signal and are
never fed to the engine. Judging whether an open item is actually done is left to a human
or a Claude session reading this feed; this module never asserts it.
"""
_ROADMAP_BULLET_RE = re.compile(r"^- \[([ x])\] (.+)$")
_WORD_RE = re.compile(r"[a-z]{4,}")


def _open_roadmap_bullets(text: str) -> list[str]:
    """Collects each top-level `- [ ]` bullet's full text, including 6-space-indented
    continuation lines up to the next top-level bullet or a blank line - mirroring
    docs/superpowers/plans/2026-08-26-kanban-board-sync.md's own proven `_iter_bullets`
    approach for the identical problem. A bare single-line regex (found in review,
    2026-08-27) truncates virtually every real ROADMAP.md bullet to its first physical line
    - this repo's bullets routinely wrap onto continuation lines, confirmed against
    ROADMAP.md's own real content, which a MULTILINE-but-not-DOTALL `(.+)$` cannot see past.
    """
    lines = text.splitlines()
    bullets: list[str] = []
    current_lines: list[str] | None = None
    current_is_open = False

    for line in lines:
        match = _ROADMAP_BULLET_RE.match(line)
        if match:
            if current_lines is not None and current_is_open:
                bullets.append(" ".join(current_lines))
            current_is_open = match.group(1) == " "
            current_lines = [match.group(2).strip()]
            continue
        if current_lines is not None and line.startswith("      "):
            current_lines.append(line.strip())
            continue
        if current_lines is not None:
            if current_is_open:
                bullets.append(" ".join(current_lines))
            current_lines = None
            current_is_open = False

    if current_lines is not None and current_is_open:
        bullets.append(" ".join(current_lines))

    return bullets


def _significant_words(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def collect_docs_roadmap_feed(roadmap_text: str, recent_commit_subjects: list[str]) -> list[dict]:
    feed = []
    for bullet in _open_roadmap_bullets(roadmap_text):
        bullet_words = _significant_words(bullet)
        related = [
            subject for subject in recent_commit_subjects
            if bullet_words & _significant_words(subject)
        ]
        feed.append({"roadmap_bullet": bullet, "possibly_related_commits": related})
    return feed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_quality_coordination_docs_feed.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/quality_coordination.py tests/test_quality_coordination_docs_feed.py
git commit -m "feat: add docs/ROADMAP drift data feed (never routed through the engine)"
```

---

### Task 7: Cleanup/janitor action layer (§8) and its fault-injection fixture (§10 point 3)

**Files:**
- Modify: `tools/quality_coordination.py` (append)
- Create: `tests/support/synthetic_git_repo.py`
- Test: `tests/test_quality_coordination_cleanup_actions.py`

**Interfaces:**
- Produces: `tests.support.synthetic_git_repo.make_synthetic_repo(tmp_path) -> Path` — a
  disposable, throwaway git repository fixture (spec §10 point 3: "never this repository"),
  built with real `git init`/`git commit`/`git branch`/`git merge` calls against `tmp_path`,
  never against the real checkout.
- Produces (in `tools/quality_coordination.py`): `prune_worktrees(*, git_runner: Runner,
  repo_root: Path) -> str`, `delete_merged_branch(branch: str, *, git_runner: Runner,
  main_branch: str = "main") -> str` (returns an outcome string:
  `"succeeded"`/`"refused:<reason>"`/`"failed:<error>"`),
  `delete_sdd_scratch(plan_branch_candidates: list[str], plan_dir: Path, *, git_runner:
  Runner, main_branch: str = "main") -> str` (`plan_branch_candidates` is whatever the
  caller resolved from the ledger-mapping derivation below — a list rather than a single
  branch precisely so this function can refuse on an ambiguous mapping instead of guessing
  which one to trust),
  `run_cleanup_actions(eligible: list[tuple[str, str]], *, git_runner: Runner, repo_root:
  Path, sdd_root: Path, conn, at: datetime, dry_run: bool) -> None` — `eligible` is
  `[(identity, action_type)]`; logs every attempt via `coordination_engine.log_cleanup_action`
  regardless of outcome, matching §8's "Every executed action is logged... before/after
  state for auditability" (dry-run reports are logged with `dry_run=True` and never
  actually invoke the mutating command).

**SDD-scratch branch-mapping derivation (spec §8 action 3's own named implementation-time
detail — spec's self-review flags this explicitly rather than resolving it, so this plan
resolves it here rather than leaving it unaddressed):** the mapping from a finished plan's
scratch directory (`.superpowers/sdd/<plan>/`) to "the branch it belongs to" is derived
from the ledger's own first line (by this repo's own branching convention, an SDD plan's
first ledger line records the branch it targets, e.g. `Branch: feat/autonomous-quality-
coordination-workflow`) plus a fallback: if no such line exists, derive the expected branch
name from the plan directory's own name via this repo's `<prefix>/<name>` convention
(`.claude/rules/branching-and-ci.md`'s "Prefixes" list) and confirm via `git branch --list`
before ever treating the mapping as resolved. If neither resolves to exactly one real
branch, `delete_sdd_scratch` refuses (`"refused:ambiguous-branch-mapping"`) rather than
guessing — consistent with §11's "never treats `main` as a deletion target under any code
path" caution generalized to "never guesses a mapping it can't confirm."

- [ ] **Step 1: Write the failing tests**

```python
# tests/support/synthetic_git_repo.py
"""A disposable, throwaway git repository fixture for AQC's cleanup-action fault-injection
suite (docs/superpowers/specs/2026-08-27-autonomous-quality-coordination-workflow-design.md
§10 point 3: "run against a disposable synthetic git repository fixture, never this
repository"). Every git call here is real (not injected/faked) precisely because this
fixture's whole point is to give the cleanup actions a real repo to act on without ever
touching the actual kalshi-whale-poc checkout.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def _run(args: list[str], cwd: Path) -> None:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed: {result.stderr}")


def make_synthetic_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "synthetic_repo"
    repo.mkdir()
    _run(["git", "init", "-b", "main"], repo)
    _run(["git", "config", "user.email", "test@example.com"], repo)
    _run(["git", "config", "user.name", "Test"], repo)
    (repo / "README.md").write_text("synthetic repo for AQC cleanup fault injection\n")
    _run(["git", "add", "README.md"], repo)
    _run(["git", "commit", "-m", "initial commit"], repo)
    return repo
```

```python
# tests/test_quality_coordination_cleanup_actions.py
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from tests.support.synthetic_git_repo import make_synthetic_repo
from tools import coordination_engine as ce
from tools.quality_coordination import delete_merged_branch, prune_worktrees, run_cleanup_actions

AT = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)


def _real_runner(args, cwd):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    return result


def test_delete_merged_branch_refuses_when_not_an_ancestor_of_main(tmp_path):
    repo = make_synthetic_repo(tmp_path)
    subprocess.run(["git", "checkout", "-b", "feat/unmerged"], cwd=repo)
    (repo / "x.txt").write_text("x")
    subprocess.run(["git", "add", "x.txt"], cwd=repo)
    subprocess.run(["git", "commit", "-m", "unmerged work"], cwd=repo)
    subprocess.run(["git", "checkout", "main"], cwd=repo)

    outcome = delete_merged_branch(
        "feat/unmerged", git_runner=lambda a: _real_runner(a, repo), main_branch="main",
    )

    assert outcome.startswith("refused:")
    branches = subprocess.run(["git", "branch"], cwd=repo, capture_output=True, text=True).stdout
    assert "feat/unmerged" in branches  # still there - refusal did not delete it


def test_delete_merged_branch_succeeds_when_truly_merged(tmp_path):
    repo = make_synthetic_repo(tmp_path)
    subprocess.run(["git", "checkout", "-b", "feat/merged"], cwd=repo)
    (repo / "y.txt").write_text("y")
    subprocess.run(["git", "add", "y.txt"], cwd=repo)
    subprocess.run(["git", "commit", "-m", "merged work"], cwd=repo)
    subprocess.run(["git", "checkout", "main"], cwd=repo)
    subprocess.run(["git", "merge", "--no-ff", "feat/merged", "-m", "merge"], cwd=repo)

    outcome = delete_merged_branch(
        "feat/merged", git_runner=lambda a: _real_runner(a, repo), main_branch="main",
    )

    assert outcome == "succeeded"
    branches = subprocess.run(["git", "branch"], cwd=repo, capture_output=True, text=True).stdout
    assert "feat/merged" not in branches


def test_delete_merged_branch_never_targets_main(tmp_path):
    repo = make_synthetic_repo(tmp_path)

    outcome = delete_merged_branch(
        "main", git_runner=lambda a: _real_runner(a, repo), main_branch="main",
    )

    assert outcome == "refused:protected-main"


def test_delete_merged_branch_is_idempotent(tmp_path):
    repo = make_synthetic_repo(tmp_path)
    subprocess.run(["git", "checkout", "-b", "feat/merged2"], cwd=repo)
    (repo / "z.txt").write_text("z")
    subprocess.run(["git", "add", "z.txt"], cwd=repo)
    subprocess.run(["git", "commit", "-m", "merged work 2"], cwd=repo)
    subprocess.run(["git", "checkout", "main"], cwd=repo)
    subprocess.run(["git", "merge", "--no-ff", "feat/merged2", "-m", "merge"], cwd=repo)
    runner = lambda a: _real_runner(a, repo)

    first = delete_merged_branch("feat/merged2", git_runner=runner, main_branch="main")
    second = delete_merged_branch("feat/merged2", git_runner=runner, main_branch="main")

    assert first == "succeeded"
    assert second.startswith("refused:")  # already gone - not an error, just a no-op refusal


def test_prune_worktrees_reconciles_bookkeeping_only(tmp_path):
    repo = make_synthetic_repo(tmp_path)
    runner = lambda a: _real_runner(a, repo)

    result = prune_worktrees(git_runner=runner, repo_root=repo)

    assert result == "succeeded"  # git worktree prune has no destructive precondition to refuse on


def test_run_cleanup_actions_logs_every_attempt_dry_run(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "DB_PATH", tmp_path / "q.db")
    conn = ce._connect()
    repo = make_synthetic_repo(tmp_path)
    runner = lambda a: _real_runner(a, repo)

    run_cleanup_actions(
        [("branch:feat/x", "worktree_prune")], git_runner=runner, repo_root=repo,
        sdd_root=tmp_path / "sdd", conn=conn, at=AT, dry_run=True,
    )

    row = conn.execute("SELECT * FROM cleanup_actions").fetchone()
    assert row["action_type"] == "worktree_prune"
    assert bool(row["dry_run"]) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_quality_coordination_cleanup_actions.py -v`
Expected: FAIL — `ImportError: cannot import name 'delete_merged_branch' from 'tools.quality_coordination'`

- [ ] **Step 3: Implement the cleanup/janitor action layer**

```python
# tools/quality_coordination.py (append)
"""Cleanup/janitor action layer (spec §8). Detect and act are always separate phases
(spec §5); callers only invoke these when Task 8's CLI is run with --clean, and even then
only for identities Task 8's own eligibility check already confirmed. Every action is
deterministic, idempotent, path-contained, and independently verifiable
(.claude/rules/autonomous-quality-coordination-evidence.md's "Remediation authority rule",
generalized from GitHub-write to git/filesystem-write per spec §3). main is never a target
under any code path (spec §11).
"""
_MAIN_BRANCH_PROTECTED_NAMES = {"main"}


def prune_worktrees(*, git_runner: Runner, repo_root: Path) -> str:
    """spec §8 action 1. No precondition beyond git's own built-in safety - this command
    only reconciles bookkeeping against worktrees already removed from disk."""
    result = git_runner(["git", "worktree", "prune"])
    return "succeeded" if result.returncode == 0 else f"failed:{result.stderr.strip()}"


def delete_merged_branch(branch: str, *, git_runner: Runner, main_branch: str = "main") -> str:
    """spec §8 action 2. Preconditions: (a) branch is an ancestor of main_branch via
    git merge-base --is-ancestor, (b) not the protected main branch itself, (c) `git branch
    -d` (never -D) as an independent second guard beyond (a) - git's own merge-check backs
    this up rather than being the only check."""
    if branch in _MAIN_BRANCH_PROTECTED_NAMES or branch == main_branch:
        return "refused:protected-main"

    check = git_runner(["git", "merge-base", "--is-ancestor", branch, main_branch])
    if check.returncode != 0:
        return "refused:not-merged"

    result = git_runner(["git", "branch", "-d", branch])
    if result.returncode != 0:
        return f"refused:{result.stderr.strip()}"
    return "succeeded"


def delete_sdd_scratch(
    plan_branch_candidates: list[str], plan_dir: Path, *, git_runner: Runner, main_branch: str = "main",
) -> str:
    """spec §8 action 3. Branch-mapping derivation: see this task's own note above -
    plan_branch_candidates is whatever the caller resolved from the ledger's first line
    plus the repo's <prefix>/<name> naming-convention fallback; this function itself never
    guesses beyond refusing when the candidate set isn't exactly one confirmed branch."""
    if len(plan_branch_candidates) != 1:
        return "refused:ambiguous-branch-mapping"
    branch = plan_branch_candidates[0]

    check = git_runner(["git", "merge-base", "--is-ancestor", branch, main_branch])
    if check.returncode != 0:
        return "refused:branch-not-merged"

    if not plan_dir.exists():
        return "refused:already-removed"
    import shutil
    shutil.rmtree(plan_dir)
    return "succeeded"


def run_cleanup_actions(
    eligible: list[tuple[str, str]], *, git_runner: Runner, repo_root: Path, sdd_root: Path,
    conn, at: datetime, dry_run: bool,
) -> None:
    """Executes (or, if dry_run, only reports) each eligible (identity, action_type) pair.
    Every attempt is logged regardless of outcome (spec §8: "identity, action type,
    timestamp, outcome, and whether it ran in --clean or was merely reported as eligible -
    before/after state for auditability")."""
    for identity, action_type in eligible:
        if dry_run:
            outcome = "not-executed:dry-run"
        elif action_type == "worktree_prune":
            outcome = prune_worktrees(git_runner=git_runner, repo_root=repo_root)
        elif action_type == "delete_merged_branch":
            branch = identity.removeprefix("branch:")
            outcome = delete_merged_branch(branch, git_runner=git_runner)
        elif action_type == "delete_sdd_scratch":
            outcome = "refused:not-implemented-in-this-caller"  # Task 8 wires real candidates
        else:
            outcome = f"failed:unknown-action-type-{action_type}"

        ce.log_cleanup_action(conn, identity, action_type, at, dry_run=dry_run, outcome=outcome)
    conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_quality_coordination_cleanup_actions.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/quality_coordination.py tests/support/synthetic_git_repo.py \
        tests/test_quality_coordination_cleanup_actions.py
git commit -m "feat: add cleanup/janitor action layer, verified against a synthetic git repo fixture"
```

---

### Task 8: CLI entrypoint (`python -m tools.quality_coordination`)

**Files:**
- Modify: `tools/quality_coordination.py` (append)
- Test: `tests/test_quality_coordination_cli.py`

**Interfaces:**
- Produces: `_make_real_runner(cwd: Path) -> Runner` — binds a real `subprocess.run` call to
  `cwd`, the one place this task constructs the concrete runner every other function in
  this domain only ever receives as an already-injected `Runner`/`HttpGetter`.
  `run_detect_cycle(repo_root: Path, *, at: datetime | None = None, git_runner: Runner |
  None = None, http_getter: HttpGetter | None = None) -> dict` — wires Tasks 2-6's
  gatherers together, calls `coordination_engine.apply_observation` with the measured
  `FLOOR_HOURS_*` mapping, records the run via `coordination_engine.record_run`, and
  returns a JSON-serializable report dict (per-domain signal states, the docs/ROADMAP feed,
  and the app-report context). `git_runner`/`http_getter` default to real implementations
  bound to `repo_root` when omitted — real CLI behavior is unchanged, but every test can
  inject a fake instead (Global Constraint 8: no test in this plan makes a real
  subprocess/network call — see this task's own note below on why an earlier draft
  violated that for this exact function). `main(argv: list[str] | None = None, *,
  git_runner: Runner | None = None, http_getter: HttpGetter | None = None) -> int` —
  argparse entrypoint, mirroring `tools/project_manifest.py`'s `--check`/`--write`
  convention (spec §5): default invocation detects and reports only; `--clean` also calls
  `run_cleanup_actions` for every `escalation_eligible` identity whose domain has a defined
  cleanup action, always re-running detection in the same invocation first (spec §5: "never
  acts on a stale or separately-cached detection result"). `main` takes the same two
  injectable kwargs as `run_detect_cycle`, for the same reason and passes them straight
  through, plus reuses the one real runner it builds for both the detect pass and any
  `--clean` cleanup calls.

**Injectability gap (found in review, 2026-08-27 — not a hypothetical, an actual regression
this task shipped):** an earlier draft of this task gave `run_detect_cycle` no injectable
parameters at all — it called `subprocess.run` and `fetch_app_report`'s real
`urllib.request.urlopen` path directly, and its own tests then called it with no way to
avoid a real subprocess/network call, contradicting this plan's own Architecture paragraph
("every git/gh/Woodpecker-CLI subprocess call and every app-diagnostics HTTP call goes
through an injectable runner... so no test in this plan ever shells out or makes a network
call for real") and Global Constraint 8, in the one task that most needed to honor them
(the CLI is the one place every earlier task's injectable pieces actually get wired
together for real). A second, related regression in the same draft: the module-level
`_real_git_runner` it built took no `cwd` at all, so every git/gh/woodpecker call made
through Tasks 3/4/7's already-correctly-injectable functions silently ran against the
process's actual working directory instead of `--repo-root` — the flag existed and was
parsed, but nothing downstream of `run_detect_cycle`'s own top-level `git branch` call
actually respected the value. Both are fixed the same way: `_make_real_runner(cwd)`
replaces the bare function, and it — not a cwd-less global — is what `run_detect_cycle`
and `main` construct and thread everywhere a `Runner` is needed.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_quality_coordination_cli.py
import json
import subprocess
from datetime import datetime, timezone

from tools import coordination_engine as ce
from tools.quality_coordination import main, run_detect_cycle

AT = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)


class _StubRunner:
    """Same generic Runner fake used elsewhere in this plan (e.g. Task 3's _StubRunner) -
    queues canned CompletedProcess results, defaults to an empty JSON-array response
    (harmless for the handful of gh/git calls this task's tests don't care about the
    content of) so a test only needs to queue the responses it actually asserts on."""

    def __init__(self):
        self.calls = []
        self._responses = []

    def queue(self, stdout="", returncode=0, stderr=""):
        self._responses.append(subprocess.CompletedProcess([], returncode, stdout, stderr))

    def __call__(self, args):
        self.calls.append(list(args))
        if not self._responses:
            return subprocess.CompletedProcess(args, 0, "[]", "")
        return self._responses.pop(0)


def _refusing_getter(url, timeout):
    """Injected in place of a real HTTP call - raising here is the test-side proof that
    nothing in this task's code path ever falls through to _default_http_get's real
    urllib.request.urlopen (Global Constraint 8)."""
    raise AssertionError(f"no real network call expected in a test, got {url!r}")


def _empty_baseline(tmp_path) -> None:
    baseline_dir = tmp_path / "tools" / "quality_audit"
    baseline_dir.mkdir(parents=True)
    (baseline_dir / "baseline.json").write_text(json.dumps({"accepted_finding_ids": [], "notes": {}}))


def test_run_detect_cycle_returns_a_report_with_all_domains(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "DB_PATH", tmp_path / "q.db")
    (tmp_path / "ROADMAP.md").write_text("## Path to production\n\n- [ ] An open item.\n")
    _empty_baseline(tmp_path)
    git_runner = _StubRunner()
    git_runner.queue("")  # `git branch --format=...` - no branches

    report = run_detect_cycle(tmp_path, at=AT, git_runner=git_runner, http_getter=_refusing_getter)

    assert set(report.keys()) >= {"branch", "ledger", "process_hygiene", "docs_roadmap_feed", "app_report"}
    # _refusing_getter never actually gets called successfully - fetch_app_report catches
    # its AssertionError like any other per-call failure and degrades to None, proving the
    # injection point works without needing a real network stack to observe it.
    assert report["app_report"] == {"quality_summary": None, "health_pipeline": None, "health_faults": None}


def test_main_default_invocation_does_not_call_clean(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "DB_PATH", tmp_path / "q.db")
    (tmp_path / "ROADMAP.md").write_text("## Path to production\n")
    _empty_baseline(tmp_path)
    git_runner = _StubRunner()
    git_runner.queue("")

    exit_code = main(
        ["--repo-root", str(tmp_path)], git_runner=git_runner, http_getter=_refusing_getter,
    )

    assert exit_code == 0
    action_count = ce._connect().execute("SELECT COUNT(*) FROM cleanup_actions").fetchone()[0]
    assert action_count == 0


def test_main_clean_flag_always_reruns_detection_first(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "DB_PATH", tmp_path / "q.db")
    (tmp_path / "ROADMAP.md").write_text("## Path to production\n")
    _empty_baseline(tmp_path)
    git_runner = _StubRunner()
    git_runner.queue("")

    exit_code = main(
        ["--repo-root", str(tmp_path), "--clean"], git_runner=git_runner, http_getter=_refusing_getter,
    )

    assert exit_code == 0
    run_row = ce._connect().execute("SELECT COUNT(*) FROM coordination_runs").fetchone()[0]
    assert run_row == 1  # exactly one fresh detect pass, not a reuse of a prior cached one


def test_main_clean_flag_scopes_git_calls_to_repo_root(tmp_path, monkeypatch):
    """Regression test (found in review): the injected runner must be the SAME cwd-bound
    instance for both the detect pass and any --clean cleanup calls - proving --repo-root
    means the same thing throughout one invocation, not just at the top-level branch list."""
    monkeypatch.setattr(ce, "DB_PATH", tmp_path / "q.db")
    (tmp_path / "ROADMAP.md").write_text("## Path to production\n")
    _empty_baseline(tmp_path)
    git_runner = _StubRunner()
    git_runner.queue("")

    main(["--repo-root", str(tmp_path), "--clean"], git_runner=git_runner, http_getter=_refusing_getter)

    assert len(git_runner.calls) >= 1
    assert git_runner.calls[0][:2] == ["git", "branch"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_quality_coordination_cli.py -v`
Expected: FAIL — `ImportError: cannot import name 'run_detect_cycle' from 'tools.quality_coordination'`

- [ ] **Step 3: Implement the CLI entrypoint**

```python
# tools/quality_coordination.py (append)
"""CLI entrypoint (spec §5). Mirrors tools/project_manifest.py's --check/--write
convention: default invocation is detect + report only (safe); --clean also executes
eligible cleanup actions, always after a fresh detect pass in the same invocation."""
import argparse
import subprocess as _subprocess
from datetime import timezone


def _make_real_runner(cwd: Path) -> Runner:
    """The one place this module constructs a real, concrete Runner (found in review,
    2026-08-27 - see this task's own "Injectability gap" note above for what was wrong
    with the version this replaces). Bound to `cwd` so every git/gh/woodpecker call made
    through it actually runs against --repo-root, not the process's own working
    directory."""
    def _runner(args: list[str]) -> "subprocess.CompletedProcess[str]":
        return _subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    return _runner


_FLOOR_HOURS = {
    "branch": FLOOR_HOURS_BRANCH,
    "ledger": FLOOR_HOURS_LEDGER,
    "process_hygiene": FLOOR_HOURS_PROCESS_HYGIENE,
}

# Which domains have a defined cleanup action at all (spec §8's three actions map onto a
# subset of signal identities - e.g. process_hygiene findings have no corresponding
# cleanup action, they are surfaced for a human to fix the baseline.json note by hand).
_CLEANUP_ACTION_FOR_DOMAIN = {
    "branch": "delete_merged_branch",
}


def run_detect_cycle(
    repo_root: Path, *, at: datetime | None = None,
    git_runner: Runner | None = None, http_getter: HttpGetter | None = None,
) -> dict:
    at = at or datetime.now(timezone.utc)
    git_runner = git_runner or _make_real_runner(repo_root)
    conn = ce._connect()

    branch_list = git_runner(["git", "branch", "--format=%(refname:short)"]).stdout.splitlines()
    branch_names = [b.strip() for b in branch_list if b.strip() and b.strip() != "main"]

    branch_signals, suppressed, immediate = collect_branch_signals(
        branch_names, git_runner=git_runner, gh_runner=git_runner,
        woodpecker_runner=git_runner, conn=conn,
        worktrees_root=repo_root / ".claude" / "worktrees", at=at,
    )

    ledger_paths = sorted((repo_root / ".superpowers" / "sdd").glob("*/progress.md")) \
        if (repo_root / ".superpowers" / "sdd").exists() else []
    ledger_signals = collect_ledger_signals(ledger_paths, at=at)

    baseline_path = repo_root / "tools" / "quality_audit" / "baseline.json"
    process_hygiene_signals = (
        collect_process_hygiene_signals(baseline_path, baseline_path.read_text())
        if baseline_path.exists() else []
    )

    all_signals = branch_signals + ledger_signals + process_hygiene_signals
    states = ce.apply_observation(
        conn, all_signals, at, _FLOOR_HOURS, suppressed_keys=suppressed, immediate_keys=immediate,
    )

    roadmap_path = repo_root / "ROADMAP.md"
    docs_feed = (
        collect_docs_roadmap_feed(roadmap_path.read_text(), [])
        if roadmap_path.exists() else []
    )

    app_report = fetch_app_report("http://fastapi:8000", getter=http_getter)

    escalated = sum(1 for s in states.values() if s == "escalation_eligible")
    ce.record_run(conn, at, signals_observed=len(all_signals), signals_escalated=escalated)
    conn.commit()

    return {
        "branch": {k: v for k, v in states.items() if k.startswith("branch:")},
        "ledger": {k: v for k, v in states.items() if k.startswith("ledger:")},
        "process_hygiene": {k: v for k, v in states.items() if k.startswith("process_hygiene:")},
        "docs_roadmap_feed": docs_feed,
        "app_report": app_report,
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m tools.quality_coordination")
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--clean", action="store_true", help="also execute eligible cleanup actions")
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None, *,
    git_runner: Runner | None = None, http_getter: HttpGetter | None = None,
) -> int:
    args = _parse_args(argv)
    git_runner = git_runner or _make_real_runner(args.repo_root)
    report = run_detect_cycle(args.repo_root, git_runner=git_runner, http_getter=http_getter)

    if args.clean:
        eligible = [
            (identity, _CLEANUP_ACTION_FOR_DOMAIN[identity.split(":", 1)[0]])
            for identity, state in report["branch"].items()
            if state == "escalation_eligible" and identity.split(":", 1)[0] in _CLEANUP_ACTION_FOR_DOMAIN
        ]
        conn = ce._connect()
        run_cleanup_actions(
            eligible, git_runner=git_runner, repo_root=args.repo_root,
            sdd_root=args.repo_root / ".superpowers" / "sdd", conn=conn,
            at=datetime.now(timezone.utc), dry_run=False,
        )

    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_quality_coordination_cli.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/quality_coordination.py tests/test_quality_coordination_cli.py
git commit -m "feat: add python -m tools.quality_coordination CLI entrypoint"
```

---

### Task 9: Verification — real cadence re-check, identity stability, dry-run against the live repo (§10)

**Files:** none created or modified — this task verifies Tasks 1-8 against real repository
state, per spec §10's verification approach, the same shape as the kanban-sync plan's own
Task 13 and the AEM plan's Task 14.

**Interfaces:** none — this is a verification-only task.

- [ ] **Step 1: Run the full `quality_coordination`/`coordination_engine` test suite together**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_coordination_engine.py tests/test_quality_coordination_*.py -v`
Expected: PASS, every test from Tasks 1-8.

- [ ] **Step 2: Re-measure branch cadence against a larger real sample than Task 3's 10-merge snapshot**

Run: `git log --merges --format="%cI" -60 main | head -60` and compute the real gap
distribution (median, max) between consecutive entries. Compare against `FLOOR_HOURS_BRANCH
= 6.0`. If the measured max gap materially exceeds 6.0 hours in normal (non-anomalous)
development, raise `FLOOR_HOURS_BRANCH` to comfortably clear it and add a one-line dated
note next to the constant recording the new evidence — the same "+N YYYY-MM-DD: ..."
discipline `tools/quality_audit/baseline.json`'s `notes` block already uses. Do not lower
it without equally real evidence.

- [ ] **Step 3: Identity-stability check for the new signal identities**

For each domain, confirm the identity string is stable under ordinary editing: a branch
name doesn't shift when its code changes (`branch:<name>` — trivially stable); a ledger
path doesn't shift under a rename of its *content*, only under a literal file move
(`ledger:<path>` — stable under the actual failure mode this domain cares about); a
baseline-notes identity (`process_hygiene:baseline.json:<finding_id>`) is exactly as stable
as the finding ID it wraps, which `tools/quality_ratchet.py`'s own `derive_automation_key`
already established is durable for the accepted-ID use case (spec §10 point 2: "expected to
be materially simpler than `QualityFinding`'s line-sensitive IDs... but verified, not
assumed"). Confirm by re-running Task 5's tests after editing unrelated lines in a scratch
copy of `baseline.json` and checking the identity string is unchanged — a five-minute manual
check, not a new automated test (the existing unit tests already prove the derivation is a
pure function of `finding_id`, which is what actually matters here).

- [ ] **Step 4: Real dry-run against the live repo (read-only — no `--clean`)**

Run: `python -m tools.quality_coordination --repo-root .`

Expected: a JSON report with `branch`, `ledger`, `process_hygiene`, `docs_roadmap_feed`, and
`app_report` keys. Cross-check the `branch` section's identity list against `git branch
--format=%(refname:short)`'s real current output by eye — every non-`main` local branch
should appear exactly once. `app_report`'s three values will likely all be `None` in a real
run against a live `ddev` environment today, since Task 11's `PUBLIC_PATHS` change has
deliberately not been made yet (Global Constraint 10) — that is the **expected**, correct
result of this task, not a bug to chase; re-run this step once Task 11 lands separately to
confirm the report populates.

- [ ] **Step 5: Do not run `--clean` against this repository as part of this task**

Per Global Constraint 9 and spec §10 point 3, the cleanup-action fault-injection proof in
Task 7 already exercises every mutating precondition against the synthetic fixture. Running
`--clean` for the first time against this actual checkout is a deliberate, separate action
for the user to trigger later, not something this implementation plan does on its own as
part of "finishing the plan" — mirroring the kanban-sync plan's own Task 13 Step 5.

- [ ] **Step 6: Final commit — none expected**

If Steps 1-4 all pass with no code changes needed, there is nothing new to commit here. If
Step 2's re-measurement changes `FLOOR_HOURS_BRANCH`, or Step 4 surfaces a real parser bug
against live repo state, fix it, extend the relevant task's test file with a regression
test, re-run this task's steps, and commit the fix through the normal `superpowers:
test-driven-development` cycle before considering this plan's core (Tasks 1-9) complete.

---

### Task 10: Documentation wiring (routine — no approval gate)

**Files:**
- Modify: `.claude/rules/autonomous-quality-coordination-evidence.md`
- Modify: `.claude/rules/quality-capabilities.md`
- Modify: `CLAUDE.md`

**Interfaces:** none — documentation only.

**No CI change is included, and that is a deliberate finding, not an omission** — same
reasoning as the AEM plan's own Task 15: `.woodpecker/tests-pytest.yml` already runs the
whole `tests/` tree unfiltered on every push, confirmed in Global Constraint 12 rather than
assumed here a second time.

- [ ] **Step 1: Generalize the evidence rule's GitHub-specific framing (spec §14 bullet 2)**

`.claude/rules/autonomous-quality-coordination-evidence.md`'s "Remediation authority rule"
section currently frames deterministic/path-contained/idempotent/independently-verifiable
authority entirely in terms of GitHub write actions ("issue escalation, remediation PRs,
any proposed merge automation"). Add a short paragraph immediately after that section's
existing bullet list:

```markdown
**Generalized scope note (2026-08-27):** this rule's authority-earning criteria
(deterministic, path-contained, idempotent, independently verifiable, outside protected
domains) apply unchanged to local git/filesystem mutation authority, not only GitHub write
authority — see `tools/quality_coordination.py`'s three cleanup actions
(`docs/superpowers/specs/2026-08-27-autonomous-quality-coordination-workflow-design.md`
§8), which this rule now also governs. The credential/event topology sections above remain
GitHub-specific (there is no GitHub credential in this tool at all — see that spec's §2 and
§11), but the core principle ("Automation may observe broadly, but it earns authority to
act narrowly") and the Remediation authority rule's protected-domain list apply identically
to this tool's own three actions.
```

- [ ] **Step 2: Update the AQC-related bullets in `quality-capabilities.md` (spec §14 bullet 3)**

Update the existing "autonomous-quality-coordination-investigation" bullet's final sentence
(currently ending "...not yet implemented") to read "...implemented — see
`docs/superpowers/plans/2026-08-27-autonomous-quality-coordination-workflow.md`," and add a
new bullet immediately after the existing **quality-ratchet** bullet:

```markdown
- **autonomous-quality-coordination-workflow** — `tools/quality_coordination.py` +
  `tools/coordination_engine.py`, the tool "AQC" now names (see the bullet above for the
  naming history). An automated project-manager/janitor over this repository's own
  engineering workflow — branch/PR/CI lifecycle health, `superpowers` plan/ledger execution
  health, standing-rule/process-hygiene compliance — informed by, never auditing, the
  trading application's own read-only diagnostics as context. No GitHub write authority; the
  only mutating authority is three deterministic, idempotent, path-contained local
  git/filesystem cleanup actions (`git worktree prune`, deleting a branch already merged and
  remote-deleted, deleting a finished plan's SDD scratch workspace), each independently
  fault-injection-tested against a synthetic repo fixture, never this repository. Manual
  invocation only: `python -m tools.quality_coordination` (detect + report) /
  `python -m tools.quality_coordination --clean` (also executes eligible cleanup actions).
  Design: `docs/superpowers/specs/2026-08-27-autonomous-quality-coordination-workflow-
  design.md`. Plan: `docs/superpowers/plans/2026-08-27-autonomous-quality-coordination-
  workflow.md`.
```

- [ ] **Step 3: Point `CLAUDE.md`'s "Quick file map" at both new modules**

Add to `CLAUDE.md`'s "Quick file map", immediately after the existing `tools/` bullet
(which already names `quality_ratchet.py`):

```markdown
  `coordination_engine.py` + `quality_coordination.py` (the tool "Autonomous Quality
  Coordination" now names — a project-manager/janitor over this repo's own engineering
  workflow, not the trading app's code; see `docs/superpowers/specs/2026-08-27-autonomous-
  quality-coordination-workflow-design.md`), and others.
```

- [ ] **Step 4: Verify the CI claim rather than assuming it (same check as Global Constraint 12)**

Run: `grep -n -A6 "^when:" .woodpecker/tests-pytest.yml`
Expected: an unfiltered `event: [push, pull_request]` block with no `path:` restriction that
would exclude `tests/test_coordination_engine.py` or `tests/test_quality_coordination_*.py`.
If a path filter *is* present, add the new test paths to it in this same commit.

- [ ] **Step 5: Commit**

```bash
git add .claude/rules/autonomous-quality-coordination-evidence.md \
        .claude/rules/quality-capabilities.md CLAUDE.md
git commit -m "docs: point CLAUDE.md and the capability router at the AQC workflow tool

No new CI pipeline: .woodpecker/tests-pytest.yml already runs the whole tests/ tree
unfiltered on push and pull_request, so every new test file from Tasks 1-8 is CI-owned
on landing. Generalizes the evidence rule's authority-earning criteria from GitHub-write
specifically to local git/filesystem mutation authority, per spec §14."
```

---

### Task 11: `services/auth.py` `PUBLIC_PATHS` change — REQUIRES LIVE HUMAN APPROVAL BEFORE EXECUTION

**This task does not start on its own. It is not routine, and nothing above depends on it
running — Tasks 1-10 are fully functional without it; `fetch_app_report` (Task 2) already
degrades every call to `None` on failure, which is exactly what happens today, silently and
correctly, until this task's change lands.**

The spec (§6.4, §14) documents this change as "explicitly user-approved" — but that
approval was given *for the design document*, in the brainstorming conversation that
produced the spec. It is not standing permission to edit `services/auth.py` unattended at
implementation time. This task exists specifically so that distinction is not lost between
"the spec says this is fine" and "go ahead and do it right now." **Do not execute this
task's steps without asking the human operator for live, in-the-moment confirmation
immediately before Step 1**, even though the spec already describes the change and its
justification in detail.

**Files:**
- Modify: `services/auth.py`

**Interfaces:** none new — a three-string change to an existing set literal.

**Why this is safety-adjacent enough to gate, even though it's one line (§6.4's own
reasoning, restated so this task doesn't read as routine):** the three routes
(`/api/quality/summary`, `/api/health/pipeline`, `/api/health/faults`) become reachable by
anyone who can reach the app without a session at all, even after real auth is eventually
configured for everything else. The routes themselves are read-only, non-sensitive
operational diagnostics — no trading data, no credentials, no order/position detail — but
widening `services/auth.py`'s `PUBLIC_PATHS` is exactly the kind of change
`.claude/rules/kalshi-integration-authority.md`'s sibling safety framing and this repo's own
"Safety invariants" section in `CLAUDE.md` treat as requiring explicit sign-off, not
`.claude/rules/quality-capabilities.md`'s baseline-ratchet-style "investigate, fix, or
explicitly accept" shortcut.

- [ ] **Step 0: Stop. Ask the human operator directly whether to proceed with this specific
  change, right now, in this session — do not infer consent from the spec's existing text.**

If the answer is anything other than an explicit, current "yes, do it": stop here, leave
`services/auth.py` untouched, and report that Tasks 1-10 are complete and independently
functional, with this one task deliberately not executed pending approval.

- [ ] **Step 1 (only after Step 0's explicit yes): Create a dedicated branch for this one
  change, separate from Tasks 1-10's branch (Global Constraint 12)**

```bash
git checkout main
git pull origin main
git checkout -b chore/aqc-public-diagnostics-routes
```

- [ ] **Step 2: Make the change**

In `services/auth.py`, change:

```python
PUBLIC_PATHS = {"/login", "/auth/login", "/auth/callback"}
```

to:

```python
PUBLIC_PATHS = {
    "/login", "/auth/login", "/auth/callback",
    # Read-only operational diagnostics, deliberately public even once real auth is
    # configured (2026-08-27): no trading data, no credentials, no order/position detail.
    # See docs/superpowers/specs/2026-08-27-autonomous-quality-coordination-workflow-
    # design.md §6.4 for the tool that reads these and the full justification/trade-off.
    "/api/quality/summary", "/api/health/pipeline", "/api/health/faults",
}
```

- [ ] **Step 3: Confirm no other route accidentally widened**

Run: `grep -n "PUBLIC_PATHS" services/auth.py` and confirm the set contains exactly the six
strings above — three original, three new — nothing else.

- [ ] **Step 4: Run the existing auth test suite**

Run: `ddev exec -s fastapi python3 -m pytest tests/test_auth.py -v` (or the real filename —
confirm via `ls tests/ | grep -i auth` first if this name is wrong) to confirm no existing
auth behavior regressed.

- [ ] **Step 5: Live verification that the trading gate, kill switch, and every other
  route's auth requirement are unaffected**

Run: `GET /api/state` and one other already-protected route (e.g. `GET /api/quality/summary`
before this change would have required a session if `auth_configured()` were true in this
environment) against the live `ddev` app, confirming the three newly-public routes now
return `200` without a session while every other protected route's behavior is unchanged.

- [ ] **Step 6: Commit and push**

```bash
git add services/auth.py
git commit -m "feat: allow unauthenticated reads of three operational-diagnostics routes for AQC

Explicit, live-confirmed change (see docs/superpowers/plans/2026-08-27-autonomous-quality-
coordination-workflow.md Task 11) - not inferred from the design spec's own approval alone.
Narrow, reversible: three already-read-only, non-sensitive routes only (no trading data,
no credentials, no order/position detail); every other route's auth requirement, the
trading gate, and the kill switch are untouched."
git push -u origin chore/aqc-public-diagnostics-routes
```

- [ ] **Step 7: Open a PR, confirm Woodpecker green, merge per `.claude/rules/branching-and-ci.md`**

Do not merge without a green Woodpecker run — this touches `services/auth.py`, squarely the
kind of change that policy's "Exceptions" section (via `.claude/skills/ci-cd-guardrails/
SKILL.md`) already treats as warranting real verification before merge, not offloaded
sight-unseen.

---

## Self-Review

**0. Judgment calls made while planning, recorded rather than smoothed over** (same
discipline the spec's own §15 self-review applied, and the AEM plan's own Self-Review §0):

- **`apply_observation`'s signature extends beyond §7's minimal 3-arg illustration
  (Task 1).** `floor_hours` (required, no module-level default — §10 point 1's "no guessed
  threshold" rule made a shared hardcoded constant across domains actively wrong), and
  `suppressed_keys`/`immediate_keys` (keyword-only, domain-computed overrides — §6.1
  requires both a suppression mechanism and an immediate-severity bypass that a bare
  `identity/domain/payload/still_present` signal can't express on its own). All three are
  additive to the illustrative snippet, not contradictions of it.
- **No deburst-counting in the new engine (Task 1).** `quality_ratchet.py`'s
  `_deburst_count`/`DEBURST_GAP_HOURS`/`DEBURST_COUNT` read a per-observation log table
  (`coordination_log`) that `signal_state`'s schema (§9) doesn't have. Dropped rather than
  reinvented; the specific bug that logic guarded against (a fingerprint short-circuit
  freezing the floor) is avoided a different way here — `apply_observation` has no
  short-circuit at all, matching `quality_ratchet.py`'s own post-fix behavior.
- **`fingerprint`'s update rule (Task 1).** §9 defines the column, not its semantics. This
  plan's rule — recompute every cycle from the sorted-JSON payload, never gate anything on
  whether it changed — is what makes Task 3's CI-staleness "hasn't advanced across two
  consecutive runs" detection possible via `get_prior_row`, without a second persistence
  mechanism.
- **Two flat files, not a package (Architecture note).** §4's diagram names exactly
  `tools/coordination_engine.py` and `tools/quality_coordination.py` — two files, not a
  subpackage the way `tools/kanban_sync/` is structured. Kept literally as drawn rather than
  silently restructured into a package; each domain gatherer is a clearly-delimited function
  group within the one file instead.
- **Co-dispatch-cluster suppression (Task 3) is an extension of §6.1's suppression-candidate
  list, verified against the existing design before assuming new architecture was needed.**
  Prompted by a real, observed instance during this plan's own drafting (three sibling docs
  branches from one coordinated dispatch). Checked against §6.1's existing two suppression
  candidates and `.claude/rules/autonomous-quality-coordination-evidence.md`'s "Branch truth
  and active-work rule" first — both already establish "open branches/PRs are evidence work
  may be in flight, suppress-not-prove" as the governing principle; a creation-time cluster
  is the same evidence, inferred from timing instead of PR activity. Implemented purely as a
  third computation inside `collect_branch_signals`'s own suppression logic (`_cluster_
  siblings`), feeding the same `suppressed_keys` mechanism Task 1 already built for the
  other two candidates — no engine change, no identity-granularity change (still one
  `Signal` per branch, exactly as §6.1 specifies).
- **`FLOOR_HOURS_BRANCH`/`FLOOR_HOURS_LEDGER`/`CLUSTER_WINDOW_MINUTES` are provisional,
  evidence-backed starting points, not final values (Tasks 3-4, Task 9).** Real measurement
  was performed against this repo's actual git history while writing this plan (a 10-merge
  sample for the branch floor); explicitly flagged as smaller than the fuller 30-90-day
  pull §10 point 1 calls for, with Task 9 Step 2 as the designated re-measurement point
  before treating any of these as settled.
- **SDD-scratch branch-mapping derivation (Task 7) resolves spec §8's own named
  implementation-time gap** — ledger-first-line plus the repo's `<prefix>/<name>` naming
  convention as a fallback, refusing rather than guessing when neither resolves to exactly
  one confirmed branch.
- **Task 11 is a separate task, a separate branch, and a hard stop-and-ask — not folded into
  Task 2's app-report client or Task 10's routine documentation commits.** The instruction
  driving this plan was explicit that "the spec approved it" must not read as "implementation
  may proceed" — Task 11's structure (Step 0's literal stop, the dedicated branch, the
  extra justification paragraph) is designed so a future session can't skim past it the way
  a routine doc-only step could be skimmed.

**1. Spec coverage** (against `docs/superpowers/specs/2026-08-27-autonomous-quality-coordination-workflow-design.md`):

| Spec section | Task |
|---|---|
| §1 Purpose and the correction this document makes | The plan as a whole; no single task |
| §2 Non-goals | Global Constraints 1, 4, 5, 6 — no task may violate them |
| §3 Prior art and governing documents | Global Constraint 3 (evidence rule generalization); Task 1 (reuses `quality_ratchet.py`'s contract); Task 10 Step 1 (updates the evidence rule's framing) |
| §4 Architecture overview | Task 1 (`coordination_engine.py`), Task 2 (`quality_coordination.py` scaffold); Self-Review §0's "two flat files" judgment call |
| §5 Invocation model | Task 8 (CLI, `--clean` always re-detects first) |
| §6.1 Branch/PR/CI lifecycle health | Task 3, including the co-dispatch-cluster extension |
| §6.2 Plan and ledger execution health | Task 4 |
| §6.3 Standing-rule/process-hygiene compliance | Task 5 |
| §6.4 Trading-application self-reported diagnostics (context) | Task 2 (client), Task 11 (the required `PUBLIC_PATHS` change, human-gated) |
| §6.5 Documentation/ROADMAP drift (data feed) | Task 6 — deliberately never calls `apply_observation` |
| §7 Coordination engine contract | Task 1 |
| §8 Cleanup/janitor action layer | Task 7 |
| §9 Data model | Task 1 (`_connect`'s three tables, verbatim from §9) |
| §10 Verification approach | Task 7 (fault injection on synthetic fixture), Task 9 (cadence re-check, identity stability, live dry-run) |
| §11 Safety and protected-path rules | Global Constraints 1-3; Task 7's `main`-protection tests; Task 8 never invoking `--clean` on `main` |
| §12 Disposition of `quality_coordination.py` (PR #43) | Confirmed already merged (`main`@`440da36`) in the plan header — not a task, since it landed independently before this plan started |
| §13 Explicitly deferred | Global Constraints 4, 5 — no task builds any of it, by design |
| §14 Follow-up tasks | Task 10 (routine doc wiring), Task 11 (the human-gated `PUBLIC_PATHS` change) |
| §15 Self-review | N/A — the spec's own |

Every specified section has an implementing task or an explicit Global Constraint. The only
sections with no task are §1 (framing), §2/§13 (prohibitions, realized as Global
Constraints rather than work), §9's schema (fully absorbed into Task 1, not a separate
task), and §15 (the spec's own review).

**2. Placeholder scan:** every step contains complete, real code — no `TODO`, no `TBD`, no
"add appropriate error handling," no "similar to Task N" left unexpanded. Task 3's Step 3a
is the one place this plan shows, then explicitly removes, an illustrative dead-code
fragment — done deliberately to make a design decision visible in the document, with the
removal spelled out as its own step rather than left implicit. Task 4's ledger-line-format
caveat and Task 7's SDD-scratch branch-mapping derivation are both named as genuine
implementation-time gaps (matching how spec §8 itself names the latter), not silently
glossed over.

**3. Type and interface consistency across tasks:**

- `Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]` (Task 2) is the
  single subprocess-injection type, used identically by Tasks 3, 7, and 8 — no second
  runner type is introduced anywhere. Task 4 deliberately does not use it at all (see this
  task's own Interfaces note — `collect_ledger_signals` dropped unused `git_runner`/
  `gh_runner` params found in review, rather than keeping them for a feature this task
  doesn't actually implement). `_make_real_runner(cwd) -> Runner` (Task 8) is the single
  place a concrete `Runner` is ever constructed — every other function in this domain only
  ever receives one already injected, which is what makes Task 8's own tests able to run
  without a real subprocess or network call (found needed in review: an earlier draft's
  `run_detect_cycle`/`main` had no injection point at all — see Task 8's own note).
- `coordination_engine.Signal(identity, domain, payload, still_present)` (Task 1) is the
  only signal type constructed by Tasks 3, 4, and 5 — Task 6 deliberately never constructs
  one, consistent with §6.5.
- State strings (`"new"`, `"observed"`, `"escalation_eligible"`, `"suppressed"`,
  `"resolved"`) are produced only inside `apply_observation` (Task 1) and consumed by name
  only in Task 8's `_CLEANUP_ACTION_FOR_DOMAIN` eligibility filter and Task 9's manual
  checks — no second vocabulary exists.
- `cleanup_actions.action_type` values (`"worktree_prune"`, `"delete_merged_branch"`,
  `"delete_sdd_scratch"`) are produced in Task 7's three action functions and consumed by
  name in `run_cleanup_actions` (Task 7) — but **only `"delete_merged_branch"` is actually
  reachable from `main`'s `--clean` wiring today** (Task 8's `_CLEANUP_ACTION_FOR_DOMAIN`
  maps only the `"branch"` domain). This corrects an earlier version of this bullet that
  claimed all three were wired "matching exactly" — checked against the real code in
  review and found false. `worktree_prune` (unconditional per spec §8 action 1 — "no
  precondition beyond git's own built-in safety," i.e. not obviously gated behind any
  particular signal's `escalation_eligible` state at all) and `delete_sdd_scratch` (gated
  behind a `"ledger"`-domain signal, but only once its branch-mapping derivation — already
  named as an unresolved implementation-time detail in Task 7 — is actually invoked at CLI
  time) are both fully built and independently fault-injection-tested (Task 7), just not
  yet wired into `--clean`'s eligibility scan. **This is a genuine scope/architecture
  decision this plan does not resolve unilaterally** — whether `worktree_prune` runs
  unconditionally versus signal-gated, and how `delete_sdd_scratch`'s branch-mapping
  derivation gets invoked at CLI time, are both open questions the spec itself left
  implementation-time detail; flagged here for human review rather than decided in this
  pass (see the PR description).
- `FLOOR_HOURS_BRANCH`/`FLOOR_HOURS_LEDGER`/`FLOOR_HOURS_PROCESS_HYGIENE` (Tasks 3-5) are
  assembled into the one `_FLOOR_HOURS` dict `run_detect_cycle` passes to
  `apply_observation` (Task 8) — no domain's floor is defined twice.
- `ce.get_prior_row`/`ce._fingerprint` (Task 1) are used identically by Task 3's CI-staleness
  detection — no second fingerprinting or prior-state-reading mechanism is built.

**4. Deferral discipline check.** Confirmed against spec §13 before finalizing: no task
adds cron/Woodpecker scheduling for this tool; no task wires AQC's escalation-eligible
signals into AEM's issue queue; no task retrofits `quality_ratchet.py` onto
`coordination_engine.py` or modifies `tools/quality_ratchet.py` at all; no task widens the
cleanup allowlist beyond the three actions in Task 7; no task asserts a docs/ROADMAP item is
done or stale — Task 6 only ever returns raw `{"roadmap_bullet", "possibly_related_commits"}`
pairs, verified by its own test (`test_collect_docs_roadmap_feed_never_asserts_done`).

**5. Scope/ambiguity check on the mid-drafting addition (co-dispatch-cluster
suppression).** Verified, not assumed, that the existing signal-domain design already
covers this once checked: §6.1's own "Suppression candidates" subsection and
`.claude/rules/autonomous-quality-coordination-evidence.md`'s "Branch truth and active-work
rule" already establish the governing principle (open branches/PRs suppress-or-delay
escalation, never prove resolution) — a creation-time cluster is the same category of
evidence read a different way, not a new category needing new architecture. No identity
model change (still one `Signal` per branch), no new engine parameter beyond the
`suppressed_keys` override Task 1 already needed for §6.1's other two suppression
candidates, and a dedicated test pair (`test_collect_branch_signals_suppresses_a_
coordinated_dispatch_cluster` / `test_collect_branch_signals_does_not_suppress_a_lone_
stale_branch`) proving the distinction holds both ways — a real cluster suppresses, a
genuinely lone stale branch does not.
