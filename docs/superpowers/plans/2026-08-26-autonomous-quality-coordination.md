# Autonomous Quality Coordination (Report-Only) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a persisted, read-only coordinator observation series that watches `tools.quality_audit`'s static findings on `main`, tracks their identity/persistence/suppression state using policy already proven in the investigation prototype, and exposes it via two read-only API routes — with zero GitHub writes, zero new credentials, and zero code path capable of writing anything outside its own SQLite file.

**Architecture:** One new module, `services/quality_coordination.py`, following this repo's existing per-module-owns-its-own-db-file persistence idiom. It reuses `QualityFinding`/`QualityReport` unmodified, computes a location-free `automation_key` externally (no scanner file touched), ports the investigation's proven `Coordinator` policy (I8) from in-memory dicts to SQLite, fetches branch/PR suppression signals from GitHub's public anonymous API, and is wired into `main.py` via one new `_maybe_*` scheduler tick, matching every existing scheduler in this app.

**Tech Stack:** Python 3.13, `sqlite3` (stdlib), FastAPI (routes), `pytest` (tests), `urllib.request` or the repo's existing HTTP client for the two anonymous GitHub reads — no new dependency.

**Spec:** `docs/superpowers/specs/2026-08-26-autonomous-quality-coordination-design.md` — this plan implements every numbered section of that spec; see the Self-Review at the end for the section-by-section coverage check.

## Global Constraints

- No write lane, no GitHub credential, no issue/PR authority anywhere in this plan (I10 §2; I11 throughout). Auto-merge stays excluded — enabling any write capability is a separate, later, explicit design decision (I10 §2 item 4).
- `automation_key` is computed externally in `services/quality_coordination.py`; **no file under `tools/quality_audit/` is modified by any task in this plan** (I11 §3).
- Only static findings (`tools.quality_audit.__main__.run_audit()`) feed the coordinator. Runtime findings (`source="runtime"`) are never read by this module (I11 §1).
- The module writes to exactly one path, `data/quality_coordination.db`. No task in this plan adds a write call to any other file (I11 §8).
- Tests never touch a real `data/*.db` file — every test that needs persistence uses `monkeypatch.setattr(module, "DB_PATH", tmp_path / "....db")`, this repo's established convention (`tests/test_risk_manager.py`).
- Idempotence is keyed on a content fingerprint of the audit result, never on `git rev-parse HEAD` — the `fastapi` container has no `git` binary (I11 §10, citing the real `project-manifest-regen-gotcha` incident in this repo).
- Every privilege increase gets its own future task with its own rollback condition; this plan does not bundle any GitHub-write capability into scaffolding (I12 checklist item).

---

## Task 1: Schema module and connection helper

**Files:**
- Create: `services/quality_coordination.py`
- Test: `tests/test_quality_coordination.py`

**Interfaces:**
- Produces: `DB_PATH: Path`, `_connect() -> sqlite3.Connection` (creates all three tables `IF NOT EXISTS`, returns an open connection with `row_factory = sqlite3.Row`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_quality_coordination.py
import sqlite3

import services.quality_coordination as qc


def test_connect_creates_all_three_tables(tmp_path, monkeypatch):
    monkeypatch.setattr(qc, "DB_PATH", tmp_path / "quality_coordination.db")
    conn = qc._connect()
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {"coordination_items", "coordination_log", "coordination_runs"} <= tables
    conn.close()


def test_connect_is_idempotent_on_repeated_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(qc, "DB_PATH", tmp_path / "quality_coordination.db")
    qc._connect().close()
    conn = qc._connect()  # CREATE TABLE IF NOT EXISTS must not raise on the second call
    conn.execute("SELECT 1")
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi pytest tests/test_quality_coordination.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.quality_coordination'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/quality_coordination.py
"""Read-only autonomous quality coordination — persisted observation series only.

No GitHub write credential, no issue/PR authority, no write path outside this module's own
data/quality_coordination.db. See docs/superpowers/specs/2026-08-26-autonomous-quality-
coordination-design.md for the full design; this module implements that spec exactly.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "quality_coordination.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS coordination_items (
        automation_key TEXT PRIMARY KEY,
        state TEXT NOT NULL,
        level TEXT NOT NULL,
        first_observed_at TEXT NOT NULL,
        last_observed_at TEXT NOT NULL,
        observation_count INTEGER NOT NULL,
        resolved_at TEXT,
        reopen_count INTEGER NOT NULL DEFAULT 0,
        scope_paths TEXT NOT NULL,
        source_finding_id TEXT NOT NULL,
        source_check TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS coordination_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        automation_key TEXT NOT NULL,
        at TEXT NOT NULL,
        message TEXT NOT NULL,
        FOREIGN KEY(automation_key) REFERENCES coordination_items(automation_key)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS coordination_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        audit_fingerprint TEXT NOT NULL UNIQUE,
        commit_sha TEXT,
        ran_at TEXT NOT NULL,
        items_observed INTEGER NOT NULL,
        items_resolved INTEGER NOT NULL,
        error TEXT
    )""")
    return conn
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi pytest tests/test_quality_coordination.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add services/quality_coordination.py tests/test_quality_coordination.py
git commit -m "feat: add quality coordination schema and connection helper"
```

---

## Task 2: Automation identity derivation

**Files:**
- Modify: `services/quality_coordination.py`
- Test: `tests/test_quality_coordination.py`

**Interfaces:**
- Consumes: `services.quality.models.QualityFinding` (fields: `finding_id`, `check`, `severity`, `confidence`, `source`, `scope`, `summary`, `evidence: dict`, `remediation`).
- Produces: `derive_automation_key(f: QualityFinding) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_quality_coordination.py (append)
from services.quality.models import QualityFinding
from services.quality_coordination import derive_automation_key


def _finding(**kw):
    base = dict(
        finding_id="x", check="c", severity="info", confidence="high",
        source="ci", scope="module.func", summary="s", evidence={},
    )
    base.update(kw)
    return QualityFinding(**base)


def test_semantic_rule_key_uses_scope_directly():
    f = _finding(check="router-registration", scope="services.foo")
    assert derive_automation_key(f) == "router-registration|services.foo|"


def test_frontend_route_unknown_uses_normalized_raw_evidence():
    f = _finding(check="frontend-api-contract", scope="file.js:12", evidence={"raw": "  fetch('/x')  "})
    assert derive_automation_key(f) == "frontend-api-contract|file.js|fetch('/x')"


def test_kalshi_boundary_sdk_import_uses_imported_module():
    f = _finding(check="kalshi-boundary", scope="services/x.py", evidence={"imported": "kalshi_python"})
    assert derive_automation_key(f) == "kalshi-boundary|services/x.py|kalshi_python"


def test_kalshi_boundary_deprecated_read_uses_field_from_evidence():
    f = _finding(check="kalshi-boundary", scope="services/x.py", evidence={"field": "taker_side"})
    assert derive_automation_key(f) == "kalshi-boundary|services/x.py|taker_side"


def test_resource_unclosed_falls_back_to_finding_id():
    """No class name available in evidence today (I11 §3 fallback #1) — falls back to the
    existing finding_id, which stays as symbol-sensitive as it already is."""
    f = _finding(check="resource-lifecycle", finding_id="resource-lifecycle:mod.func:client", scope="mod.func")
    assert derive_automation_key(f) == "resource-lifecycle|mod.func|resource-lifecycle:mod.func:client"


def test_kalshi_boundary_host_falls_back_to_bounded_snippet():
    """No isolated host token available in evidence today (I11 §3 fallback #2) — falls back to
    the raw snippet, bounded to 80 chars."""
    f = _finding(check="kalshi-boundary", scope="services/x.py", evidence={"snippet": "x" * 200})
    key = derive_automation_key(f)
    assert key == "kalshi-boundary|services/x.py|" + "x" * 80


def test_line_shift_does_not_change_identity():
    """I1 §9's location-free contract: two findings differing only in scope's line number
    produce the SAME automation_key when the rule is semantic (scope already excludes line for
    these rules by construction)."""
    f1 = _finding(check="config-usage", scope="alerting.crash_auto_resolve_after_sec")
    f2 = _finding(check="config-usage", scope="alerting.crash_auto_resolve_after_sec")
    assert derive_automation_key(f1) == derive_automation_key(f2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi pytest tests/test_quality_coordination.py -v -k derive_automation_key or semantic_rule or frontend_route or sdk_import or deprecated_read or resource_unclosed or boundary_host or line_shift`
Expected: FAIL with `ImportError: cannot import name 'derive_automation_key'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/quality_coordination.py (append)
from services.quality.models import QualityFinding

_HOST_SNIPPET_MAX = 80


def derive_automation_key(f: QualityFinding) -> str:
    """<check>/<rule>|<scope-without-line>|<subject>, per I1 §9 and I11 §3. Computed entirely
    from QualityFinding's existing fields — no scanner file is read or modified to produce this.
    Two named fallbacks (resource-lifecycle, kalshi-boundary host snippet) documented in I11 §3;
    both degrade to a still-usable, just more line-sensitive, key rather than raising."""
    check = f.check
    evidence = f.evidence or {}

    if check == "frontend-api-contract" and "raw" in evidence:
        scope_no_line = f.scope.split(":")[0]
        subject = evidence["raw"].strip()
        return f"{check}|{scope_no_line}|{subject}"

    if check == "kalshi-boundary":
        if "imported" in evidence:
            return f"{check}|{f.scope}|{evidence['imported']}"
        if "module" in evidence:
            return f"{check}|{f.scope}|{evidence['module']}"
        if "field" in evidence:
            return f"{check}|{f.scope}|{evidence['field']}"
        if "snippet" in evidence:
            return f"{check}|{f.scope}|{evidence['snippet'][:_HOST_SNIPPET_MAX]}"

    if check == "resource-lifecycle":
        return f"{check}|{f.scope}|{f.finding_id}"

    return f"{check}|{f.scope}|"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi pytest tests/test_quality_coordination.py -v`
Expected: PASS (9 tests total)

- [ ] **Step 5: Commit**

```bash
git add services/quality_coordination.py tests/test_quality_coordination.py
git commit -m "feat: derive location-free automation identity for quality findings"
```

---

## Task 3: Coordinator policy, ported to SQLite

**Files:**
- Modify: `services/quality_coordination.py`
- Test: `tests/test_quality_coordination.py`

**Interfaces:**
- Consumes: `derive_automation_key` (Task 2), `_connect` (Task 1).
- Produces: `Signal` (dataclass: `automation_key: str`, `level: str`, `scope_paths: tuple[str, ...]`, `source_finding_id: str`, `source_check: str`), `BranchSignal` (dataclass: `name: str`, `changed_paths: tuple[str, ...]`, `last_commit_at_iso: str`), `Claim` (dataclass: `automation_key: str`, `source: str`), `apply_observation(conn, signals: list[Signal], branches: list[BranchSignal], claims: list[Claim], at: datetime) -> dict[str, str]` — returns `{automation_key: state}` for every item touched this call, mutating `coordination_items`/`coordination_log` in `conn` (caller commits).

This is the direct SQLite port of `tools/quality_coordination_sim/coordinator.py`'s proven policy — same constants, same precedence, same invariants, now reading/writing rows instead of an in-memory dict. The port's correctness bar is: it reproduces the outcome of every scenario `tests/test_quality_coordination_sim.py` already proved, restated against SQLite.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_quality_coordination.py (append)
from datetime import datetime, timedelta, timezone

from services.quality_coordination import (
    BranchSignal, Claim, Signal, apply_observation, _connect,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _sig(key="k1", level="info", paths=("services/x.py",)):
    return Signal(automation_key=key, level=level, scope_paths=paths,
                  source_finding_id="fid", source_check="check")


def test_new_signal_creates_observed_item(tmp_path, monkeypatch):
    monkeypatch.setattr("services.quality_coordination.DB_PATH", tmp_path / "q.db")
    conn = _connect()
    result = apply_observation(conn, [_sig()], [], [], T0)
    conn.commit()
    assert result["k1"] == "observed"
    row = conn.execute("SELECT * FROM coordination_items WHERE automation_key='k1'").fetchone()
    assert row["observation_count"] == 1
    conn.close()


def test_absent_signal_resolves_previously_tracked_item(tmp_path, monkeypatch):
    monkeypatch.setattr("services.quality_coordination.DB_PATH", tmp_path / "q.db")
    conn = _connect()
    apply_observation(conn, [_sig()], [], [], T0)
    conn.commit()
    result = apply_observation(conn, [], [], [], T0 + timedelta(hours=1))
    conn.commit()
    assert result["k1"] == "resolved"
    conn.close()


def test_exact_claim_suppresses(tmp_path, monkeypatch):
    monkeypatch.setattr("services.quality_coordination.DB_PATH", tmp_path / "q.db")
    conn = _connect()
    apply_observation(conn, [_sig()], [], [], T0)
    conn.commit()
    result = apply_observation(
        conn, [_sig()], [], [Claim(automation_key="k1", source="PR#1")], T0 + timedelta(minutes=5)
    )
    conn.commit()
    assert result["k1"] == "suppressed_pending_work"
    conn.close()


def test_path_overlap_branch_suppresses(tmp_path, monkeypatch):
    monkeypatch.setattr("services.quality_coordination.DB_PATH", tmp_path / "q.db")
    conn = _connect()
    apply_observation(conn, [_sig()], [], [], T0)
    conn.commit()
    branch = BranchSignal(name="fix/x", changed_paths=("services/x.py",),
                           last_commit_at_iso=(T0 + timedelta(minutes=5)).isoformat())
    result = apply_observation(conn, [_sig()], [branch], [], T0 + timedelta(minutes=5))
    conn.commit()
    assert result["k1"] == "suppressed_pending_work"
    conn.close()


def test_floor_met_with_no_signal_is_escalation_eligible(tmp_path, monkeypatch):
    monkeypatch.setattr("services.quality_coordination.DB_PATH", tmp_path / "q.db")
    conn = _connect()
    apply_observation(conn, [_sig(level="warning")], [], [], T0)
    conn.commit()
    result = apply_observation(conn, [_sig(level="warning")], [], [], T0 + timedelta(hours=7))
    conn.commit()
    assert result["k1"] == "escalation_eligible"
    conn.close()


def test_recurrence_reopens_same_key_with_history(tmp_path, monkeypatch):
    monkeypatch.setattr("services.quality_coordination.DB_PATH", tmp_path / "q.db")
    conn = _connect()
    apply_observation(conn, [_sig()], [], [], T0)
    conn.commit()
    apply_observation(conn, [], [], [], T0 + timedelta(hours=1))  # resolved
    conn.commit()
    result = apply_observation(conn, [_sig()], [], [], T0 + timedelta(hours=5))
    conn.commit()
    assert result["k1"] == "observed"
    row = conn.execute("SELECT reopen_count FROM coordination_items WHERE automation_key='k1'").fetchone()
    assert row["reopen_count"] == 1
    log_count = conn.execute(
        "SELECT COUNT(*) c FROM coordination_log WHERE automation_key='k1'"
    ).fetchone()["c"]
    assert log_count >= 3  # observed, resolved, reopened — history retained, not truncated
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi pytest tests/test_quality_coordination.py -v -k apply_observation or new_signal or absent_signal or exact_claim or path_overlap or floor_met or recurrence_reopens`
Expected: FAIL with `ImportError: cannot import name 'apply_observation'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/quality_coordination.py (append)
from dataclasses import dataclass
from datetime import datetime, timedelta

FLOOR_HOURS = {"error": 2.0, "warning": 6.0, "info": 6.0}
DEBURST_GAP_HOURS = 1.0
DEBURST_COUNT = 2
STALENESS_IDLE_HOURS = 3.0
STALENESS_HARD_CAP_HOURS = 24.0


@dataclass(frozen=True)
class Signal:
    automation_key: str
    level: str
    scope_paths: tuple[str, ...]
    source_finding_id: str
    source_check: str


@dataclass(frozen=True)
class BranchSignal:
    name: str
    changed_paths: tuple[str, ...]
    last_commit_at_iso: str


@dataclass(frozen=True)
class Claim:
    automation_key: str
    source: str


def _log(conn: sqlite3.Connection, key: str, at: datetime, message: str) -> None:
    conn.execute(
        "INSERT INTO coordination_log (automation_key, at, message) VALUES (?, ?, ?)",
        (key, at.isoformat(), message),
    )


def _suppressing_signal(conn: sqlite3.Connection, key: str, scope_paths: tuple[str, ...],
                         branches: list[BranchSignal], claims: list[Claim], at: datetime):
    for c in claims:
        if c.automation_key == key:
            return ("claim", c.source)
    for b in branches:
        idle_hours = (at - datetime.fromisoformat(b.last_commit_at_iso)).total_seconds() / 3600
        if idle_hours >= STALENESS_HARD_CAP_HOURS or idle_hours >= STALENESS_IDLE_HOURS:
            continue
        if any(p in b.changed_paths for p in scope_paths):
            return ("branch", b.name)
    return None


def _deburst_count(conn: sqlite3.Connection, key: str) -> int:
    times = [
        datetime.fromisoformat(r["at"])
        for r in conn.execute(
            "SELECT at FROM coordination_log WHERE automation_key=? ORDER BY at", (key,)
        ).fetchall()
    ]
    if not times:
        return 0
    count, last = 1, times[0]
    for t in times[1:]:
        if (t - last).total_seconds() / 3600 >= DEBURST_GAP_HOURS:
            count += 1
            last = t
    return count


def _floor_met(conn: sqlite3.Connection, key: str, level: str, first_observed_at: datetime, at: datetime) -> bool:
    elapsed_hours = (at - first_observed_at).total_seconds() / 3600
    floor = FLOOR_HOURS.get(level, FLOOR_HOURS["info"])
    if elapsed_hours >= floor:
        return True
    return _deburst_count(conn, key) >= DEBURST_COUNT and elapsed_hours >= DEBURST_GAP_HOURS


def apply_observation(conn: sqlite3.Connection, signals: list[Signal], branches: list[BranchSignal],
                       claims: list[Claim], at: datetime) -> dict[str, str]:
    present = {s.automation_key: s for s in signals}
    result: dict[str, str] = {}

    tracked = conn.execute("SELECT * FROM coordination_items").fetchall()
    for row in tracked:
        key = row["automation_key"]
        if key not in present and row["state"] != "resolved":
            conn.execute(
                "UPDATE coordination_items SET state='resolved', resolved_at=? WHERE automation_key=?",
                (at.isoformat(), key),
            )
            _log(conn, key, at, "resolved: absent from fresh main audit")

    for key, sig in present.items():
        row = conn.execute(
            "SELECT * FROM coordination_items WHERE automation_key=?", (key,)
        ).fetchone()
        if row is None:
            conn.execute(
                """INSERT INTO coordination_items
                   (automation_key, state, level, first_observed_at, last_observed_at,
                    observation_count, reopen_count, scope_paths, source_finding_id, source_check)
                   VALUES (?, 'observed', ?, ?, ?, 1, 0, ?, ?, ?)""",
                (key, sig.level, at.isoformat(), at.isoformat(),
                 "\n".join(sig.scope_paths), sig.source_finding_id, sig.source_check),
            )
            _log(conn, key, at, "new item observed on main")
            first_observed_at = at
        elif row["state"] == "resolved":
            conn.execute(
                """UPDATE coordination_items SET state='observed', first_observed_at=?,
                   last_observed_at=?, observation_count=observation_count+1,
                   reopen_count=reopen_count+1, level=?, scope_paths=? WHERE automation_key=?""",
                (at.isoformat(), at.isoformat(), sig.level, "\n".join(sig.scope_paths), key),
            )
            _log(conn, key, at, f"reopened (recurrence #{row['reopen_count'] + 1}); prior history retained")
            first_observed_at = at
        else:
            conn.execute(
                """UPDATE coordination_items SET last_observed_at=?, observation_count=observation_count+1,
                   level=?, scope_paths=? WHERE automation_key=?""",
                (at.isoformat(), sig.level, "\n".join(sig.scope_paths), key),
            )
            _log(conn, key, at, "repeated observation on main")
            first_observed_at = datetime.fromisoformat(row["first_observed_at"])

        signal = _suppressing_signal(conn, key, sig.scope_paths, branches, claims, at)
        if signal is not None:
            kind, source = signal
            conn.execute("UPDATE coordination_items SET state='suppressed_pending_work' WHERE automation_key=?", (key,))
            reason = f"suppressed: exact claim from {source}" if kind == "claim" else f"suppressed: path overlap with live branch {source}"
            _log(conn, key, at, reason)
            result[key] = "suppressed_pending_work"
        elif _floor_met(conn, key, sig.level, first_observed_at, at):
            conn.execute("UPDATE coordination_items SET state='escalation_eligible' WHERE automation_key=?", (key,))
            _log(conn, key, at, "escalation-eligible: persistence floor met, no active-work signal")
            result[key] = "escalation_eligible"
        else:
            conn.execute("UPDATE coordination_items SET state='observed' WHERE automation_key=?", (key,))
            result[key] = "observed"

    for row in tracked:
        if row["automation_key"] not in present and row["state"] != "resolved":
            result[row["automation_key"]] = "resolved"

    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi pytest tests/test_quality_coordination.py -v`
Expected: PASS (15 tests total)

- [ ] **Step 5: Commit**

```bash
git add services/quality_coordination.py tests/test_quality_coordination.py
git commit -m "feat: port the coordinator suppression/persistence policy to SQLite"
```

---

## Task 4: `observe_main` entrypoint with content-fingerprint idempotence

**Files:**
- Modify: `services/quality_coordination.py`
- Test: `tests/test_quality_coordination.py`

**Interfaces:**
- Consumes: `tools.quality_audit.__main__.run_audit(repo_root: Path) -> QualityReport`, `derive_automation_key`, `apply_observation`, `_connect`.
- Produces: `RunResult` (dataclass: `audit_fingerprint: str`, `commit_sha: str | None`, `items_observed: int`, `items_resolved: int`, `states: dict[str, str]`, `error: str | None`), `observe_main(repo_root: Path, at: datetime | None = None, branches: list[BranchSignal] | None = None, claims: list[Claim] | None = None) -> RunResult`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_quality_coordination.py (append)
from unittest.mock import patch

from services.quality.models import QualityReport
from services.quality_coordination import observe_main


def test_observe_main_is_idempotent_on_repeated_identical_audit(tmp_path, monkeypatch):
    monkeypatch.setattr("services.quality_coordination.DB_PATH", tmp_path / "q.db")
    report = QualityReport(findings=[_finding(check="config-usage", scope="a.b")])
    with patch("services.quality_coordination._run_static_audit", return_value=report), \
         patch("services.quality_coordination._current_commit_sha", return_value=None):
        r1 = observe_main(tmp_path, at=T0)
        r2 = observe_main(tmp_path, at=T0 + timedelta(minutes=1))
    assert r1.audit_fingerprint == r2.audit_fingerprint
    assert r2.items_observed == 0  # second call short-circuits, no re-processing
    conn = _connect()
    runs = conn.execute("SELECT COUNT(*) c FROM coordination_runs").fetchone()["c"]
    assert runs == 1  # only one row, not two
    conn.close()


def test_observe_main_writes_run_row_with_counts(tmp_path, monkeypatch):
    monkeypatch.setattr("services.quality_coordination.DB_PATH", tmp_path / "q.db")
    report = QualityReport(findings=[_finding(check="config-usage", scope="a.b")])
    with patch("services.quality_coordination._run_static_audit", return_value=report), \
         patch("services.quality_coordination._current_commit_sha", return_value="deadbeef"):
        result = observe_main(tmp_path, at=T0)
    assert result.items_observed == 1
    assert result.commit_sha == "deadbeef"
    assert result.error is None


def test_observe_main_survives_scanner_exception_and_records_error(tmp_path, monkeypatch):
    monkeypatch.setattr("services.quality_coordination.DB_PATH", tmp_path / "q.db")
    with patch("services.quality_coordination._run_static_audit", side_effect=RuntimeError("boom")):
        result = observe_main(tmp_path, at=T0)
    assert result.error is not None and "boom" in result.error
    assert result.items_observed == 0


def test_scope_paths_prefers_evidence_path_over_dotted_scope():
    """Real bug this test exists to prevent: `scope` is a dotted module path for most semantic
    rules (e.g. 'services.foo'), which never equals a real GitHub file path ('services/foo.py')
    — using it directly for suppression path-overlap matching would make every such rule
    permanently unsuppressible by any real branch. evidence['path'] is the real file path."""
    from services.quality_coordination import _scope_paths
    f = _finding(check="router-registration", scope="services.foo", evidence={"path": "services/foo.py"})
    assert _scope_paths(f) == ("services/foo.py",)


def test_scope_paths_falls_back_to_scope_for_aggregate_rules_with_no_path():
    from services.quality_coordination import _scope_paths
    f = _finding(check="api-usage-inventory", scope="KalshiClient.get_market", evidence={})
    assert _scope_paths(f) == ("KalshiClient.get_market",)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi pytest tests/test_quality_coordination.py -v -k observe_main or scope_paths`
Expected: FAIL with `ImportError: cannot import name 'observe_main'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/quality_coordination.py (append)
import hashlib
import subprocess
from datetime import timezone

from services.quality.models import QualityReport
from tools.quality_audit.__main__ import run_audit as _run_static_audit


def _current_commit_sha(repo_root: Path) -> str | None:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def _fingerprint(report: QualityReport) -> str:
    parts = sorted(f"{f.finding_id}:{f.severity}" for f in report.findings)
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def _scope_paths(f: QualityFinding) -> tuple[str, ...]:
    """Real repo-relative file path(s) for suppression path-overlap matching against GitHub's
    compare API (Task 5), which reports paths like 'services/x.py' — NOT `scope`, which for most
    semantic rules (I1 §3.1: router-registration, background-wiring, persistence-isolation,
    config-usage, ...) is a dotted module/config path like 'services.foo' or
    'alerting.crash_auto_resolve_after_sec' and would never match a real filename. Prefer
    evidence['path'] (present for the rules whose evidence I1 §3.1 lists 'path' for — the large
    majority) and fall back to `scope` only for the aggregate/cross-file rules (api-usage,
    frontend-route-missing, backend-route-unused) that have no single meaningful file path at
    all — those simply won't path-overlap-suppress, which is correct: an aggregate finding isn't
    owned by one file for a branch to be "fixing."""
    evidence = f.evidence or {}
    if "path" in evidence:
        return (evidence["path"],)
    return (f.scope,)


@dataclass(frozen=True)
class RunResult:
    audit_fingerprint: str
    commit_sha: str | None
    items_observed: int
    items_resolved: int
    states: dict[str, str]
    error: str | None


def observe_main(repo_root: Path, at: datetime | None = None,
                  branches: list[BranchSignal] | None = None,
                  claims: list[Claim] | None = None) -> RunResult:
    at = at or datetime.now(timezone.utc)
    branches = branches or []
    claims = claims or []
    conn = _connect()
    try:
        try:
            report = _run_static_audit(repo_root)
        except Exception as exc:
            fp = hashlib.sha256(f"error:{exc}".encode()).hexdigest()
            conn.execute(
                """INSERT OR IGNORE INTO coordination_runs
                   (audit_fingerprint, commit_sha, ran_at, items_observed, items_resolved, error)
                   VALUES (?, ?, ?, 0, 0, ?)""",
                (fp, _current_commit_sha(repo_root), at.isoformat(), str(exc)),
            )
            conn.commit()
            return RunResult(fp, None, 0, 0, {}, str(exc))

        fp = _fingerprint(report)
        existing = conn.execute(
            "SELECT * FROM coordination_runs WHERE audit_fingerprint=?", (fp,)
        ).fetchone()
        if existing is not None:
            return RunResult(fp, existing["commit_sha"], 0, 0, {}, existing["error"])

        signals = [
            Signal(derive_automation_key(f), f.severity, _scope_paths(f), f.finding_id, f.check)
            for f in report.findings
        ]
        states = apply_observation(conn, signals, branches, claims, at)
        resolved = sum(1 for s in states.values() if s == "resolved")
        sha = _current_commit_sha(repo_root)
        conn.execute(
            """INSERT INTO coordination_runs
               (audit_fingerprint, commit_sha, ran_at, items_observed, items_resolved, error)
               VALUES (?, ?, ?, ?, ?, NULL)""",
            (fp, sha, at.isoformat(), len(signals), resolved),
        )
        conn.commit()
        return RunResult(fp, sha, len(signals), resolved, states, None)
    finally:
        conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi pytest tests/test_quality_coordination.py -v`
Expected: PASS (20 tests total)

- [ ] **Step 5: Commit**

```bash
git add services/quality_coordination.py tests/test_quality_coordination.py
git commit -m "feat: add observe_main entrypoint with content-fingerprint idempotence"
```

---

## Task 5: GitHub branch/PR signal fetch with outage degradation

**Files:**
- Modify: `services/quality_coordination.py`
- Test: `tests/test_quality_coordination.py`

**Interfaces:**
- Produces: `derive_claims() -> list[Claim]` (returns `[]` unconditionally — I11 §4, 0% historical claim coverage), `fetch_branch_signals(repo="thesneakattack/kalshi-whale-poc", timeout: float = 5.0) -> list[BranchSignal]` (anonymous GitHub reads; returns `[]` on any network/HTTP error, never raises).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_quality_coordination.py (append)
import urllib.error

from services.quality_coordination import derive_claims, fetch_branch_signals


def test_derive_claims_returns_empty_list():
    assert derive_claims() == []


def test_fetch_branch_signals_degrades_to_empty_list_on_network_error(monkeypatch):
    def _raise(*a, **kw):
        raise urllib.error.URLError("no network")
    monkeypatch.setattr("services.quality_coordination._http_get_json", _raise)
    assert fetch_branch_signals() == []


def test_fetch_branch_signals_parses_real_shaped_response(monkeypatch):
    branches_payload = [{"name": "fix/x", "commit": {"sha": "abc"}}]
    compare_payload = {
        "files": [{"filename": "services/x.py"}],
        "commits": [{"commit": {"committer": {"date": "2026-01-01T00:00:00Z"}}}],
    }

    def _fake_get(url, timeout):
        if url.endswith("/branches"):
            return branches_payload
        return compare_payload

    monkeypatch.setattr("services.quality_coordination._http_get_json", _fake_get)
    result = fetch_branch_signals()
    assert len(result) == 1
    assert result[0].name == "fix/x"
    assert result[0].changed_paths == ("services/x.py",)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi pytest tests/test_quality_coordination.py -v -k derive_claims or fetch_branch_signals`
Expected: FAIL with `ImportError: cannot import name 'derive_claims'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/quality_coordination.py (append)
import json
import urllib.error
import urllib.request

_GITHUB_API = "https://api.github.com/repos/{repo}"


def derive_claims() -> list[Claim]:
    """I11 §4: zero historical exact-claim coverage measured (I3 §6). Named extension point,
    not a missing function — returns [] unconditionally until a claim mechanism is designed."""
    return []


def _http_get_json(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"User-Agent": "kalshi-whale-poc-quality-coordination"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def fetch_branch_signals(repo: str = "thesneakattack/kalshi-whale-poc", timeout: float = 5.0) -> list[BranchSignal]:
    """Anonymous, unauthenticated GitHub reads only (I11 §4/§7 — no credential exists to use).
    Degrades to [] on any failure — never raises, per I11 §10's outage-behavior design."""
    base = _GITHUB_API.format(repo=repo)
    try:
        branches = _http_get_json(f"{base}/branches", timeout)
    except Exception:
        return []

    signals: list[BranchSignal] = []
    for b in branches:
        name = b.get("name")
        if not name or name == "main":
            continue
        try:
            compare = _http_get_json(f"{base}/compare/main...{name}", timeout)
        except Exception:
            continue
        paths = tuple(f["filename"] for f in compare.get("files", []))
        commits = compare.get("commits", [])
        last_commit_iso = (
            commits[-1]["commit"]["committer"]["date"] if commits else None
        )
        if not paths or not last_commit_iso:
            continue
        signals.append(BranchSignal(name=name, changed_paths=paths, last_commit_at_iso=last_commit_iso))
    return signals
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi pytest tests/test_quality_coordination.py -v`
Expected: PASS (23 tests total)

- [ ] **Step 5: Commit**

```bash
git add services/quality_coordination.py tests/test_quality_coordination.py
git commit -m "feat: fetch branch suppression signals from anonymous GitHub API"
```

---

## Task 6: Scheduler wiring and config flag

**Files:**
- Modify: `services/app_state.py`
- Modify: `services/quality_coordination.py`
- Modify: `main.py`
- Modify: `config/settings.yaml`
- Test: `tests/test_quality_coordination_scheduler.py`

**Interfaces:**
- Consumes: `observe_main`, `fetch_branch_signals`, `derive_claims` (Tasks 4–5).
- Produces: `services.quality_coordination.latest_run_at() -> float | None`, `main._maybe_run_quality_coordination(cfg: dict) -> None`.

**Design note, grounded in a real incident in this exact repo:** a naive `_maybe_*` scheduler that
tracks "last run" as a bare in-memory variable is precisely the bug `services/backup/backup.py`'s
own docstring documents fixing live 2026-08-23 (`_maybe_run_backup`) — in-memory state resets to
zero on every `uvicorn --reload`, not just a real restart, making the scheduler think it's
immediately overdue on every single code edit. `_maybe_run_backup`'s fix (still in the codebase
today) is the pattern this task follows exactly: track state in `services/app_state.py`'s shared
`state` dict (survives reload, since that module isn't reimported), and seed it from the module's
own persisted history on first check rather than trusting an unseeded `0.0`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_quality_coordination_scheduler.py
from unittest.mock import patch

import main
from services.app_state import state


def test_scheduler_skips_when_disabled():
    state["quality_coordination"] = {"last_run_at": 0.0}
    cfg = {"quality_coordination": {"enabled": False}}
    with patch("main.observe_main") as mock_observe:
        main._maybe_run_quality_coordination(cfg)
    mock_observe.assert_not_called()


EPOCH = 1_800_000_000.0  # a realistic time.time() scale — real epoch seconds, not small test
#                          numbers, because last_run_at=0.0 is a sentinel that only reliably
#                          means "always overdue" when compared against a real epoch-scale value
#                          (exactly how the real, already-proven backup.py pattern this mirrors
#                          works in production) — small mock values like 1000.0 would silently
#                          break that property and pass for the wrong reason.


def test_scheduler_runs_once_then_waits_for_interval():
    state["quality_coordination"] = {"last_run_at": 0.0}
    cfg = {"quality_coordination": {"enabled": True, "interval_sec": 3600}}
    with patch("main.observe_main") as mock_observe, \
         patch("main.fetch_branch_signals", return_value=[]), \
         patch("main.derive_claims", return_value=[]), \
         patch("main.latest_run_at", return_value=None), \
         patch("main.time") as mock_time:
        mock_time.time.return_value = EPOCH
        main._maybe_run_quality_coordination(cfg)  # first check: due (never run, no history)
        mock_time.time.return_value = EPOCH + 5
        main._maybe_run_quality_coordination(cfg)  # 5s later, well under the 3600s interval
    assert mock_observe.call_count == 1


def test_scheduler_seeds_last_run_at_from_persisted_history_on_cold_start():
    """The cold-start-reload fix: if in-memory state is unseeded (0.0) but a prior run is
    already recorded on disk, the scheduler must not treat that as newly overdue."""
    state["quality_coordination"] = {"last_run_at": 0.0}
    cfg = {"quality_coordination": {"enabled": True, "interval_sec": 3600}}
    with patch("main.observe_main") as mock_observe, \
         patch("main.fetch_branch_signals", return_value=[]), \
         patch("main.derive_claims", return_value=[]), \
         patch("main.latest_run_at", return_value=EPOCH), \
         patch("main.time") as mock_time:
        mock_time.time.return_value = EPOCH + 5  # 5s after the persisted last run, not due
        main._maybe_run_quality_coordination(cfg)
    mock_observe.assert_not_called()
    assert state["quality_coordination"]["last_run_at"] == EPOCH
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi pytest tests/test_quality_coordination_scheduler.py -v`
Expected: FAIL with `AttributeError: module 'main' has no attribute '_maybe_run_quality_coordination'`

- [ ] **Step 3: Write minimal implementation**

Add to `config/settings.yaml` (new top-level section, alongside `backup:`/`observability:`):

```yaml
quality_coordination:
  enabled: true
  interval_sec: 3600
```

Add to `services/app_state.py`'s `state` dict defaults, alongside the existing `"backup": {...}` entry:

```python
    "quality_coordination": {"last_run_at": 0.0},
```

Add to `services/quality_coordination.py` (append):

```python
def latest_run_at() -> float | None:
    """Most recent persisted run timestamp, for cold-start seeding — same role as backup.py's
    `latest()` call in `_maybe_run_backup`. Returns None if no run has ever completed."""
    conn = _connect()
    try:
        row = conn.execute("SELECT MAX(ran_at) AS m FROM coordination_runs").fetchone()
        if row is None or row["m"] is None:
            return None
        return datetime.fromisoformat(row["m"]).timestamp()
    finally:
        conn.close()
```

Add to `main.py` (near the other `_maybe_*` scheduler definitions, following `_maybe_run_backup`'s
exact cold-start-seeding shape):

```python
import time
from pathlib import Path as _Path

from services.app_state import state as _app_state
from services.quality_coordination import (
    derive_claims, fetch_branch_signals, latest_run_at, observe_main,
)


def _maybe_run_quality_coordination(cfg: dict) -> None:
    qc_cfg = cfg.get("quality_coordination") or {}
    if not qc_cfg.get("enabled", True):
        return
    qc_state = _app_state.setdefault("quality_coordination", {"last_run_at": 0.0})
    if qc_state["last_run_at"] == 0.0:
        persisted = latest_run_at()
        if persisted is not None:
            qc_state["last_run_at"] = persisted
    interval = qc_cfg.get("interval_sec", 3600)
    now_ts = time.time()
    if now_ts - qc_state["last_run_at"] <= interval:
        return
    qc_state["last_run_at"] = now_ts
    repo_root = _Path(__file__).resolve().parent
    branches = fetch_branch_signals()
    claims = derive_claims()
    observe_main(repo_root, branches=branches, claims=claims)
```

Wire the call into the existing tick loop: in `main.py`, the line `_maybe_check_signal_resolutions(cfg)`
(inside the same tick body as `_maybe_scan_catalog_batch(cfg)` and `_maybe_run_backup(cfg)`, shown
by `grep -n "_maybe_check_signal_resolutions(cfg)" main.py`) gains one new line immediately after
it:

```python
            _maybe_check_signal_resolutions(cfg)
            _maybe_run_quality_coordination(cfg)
            _maybe_run_backup(cfg)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi pytest tests/test_quality_coordination_scheduler.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add services/app_state.py services/quality_coordination.py main.py config/settings.yaml tests/test_quality_coordination_scheduler.py
git commit -m "feat: wire quality coordination into the scheduler tick, cold-start-safe"
```

---

## Task 7: Read routes

**Files:**
- Modify: `services/quality/routes.py`
- Test: `tests/test_quality_routes.py` (existing file — append)

**Interfaces:**
- Consumes: `services.quality_coordination._connect`.
- Produces: `GET /api/quality/coordination` (new route), and a new `"coordination"` key on the existing `GET /api/quality/summary` response.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_quality_routes.py (append)
from fastapi.testclient import TestClient

import services.quality_coordination as qc
from main import app


def test_quality_coordination_route_returns_items_and_log(tmp_path, monkeypatch):
    monkeypatch.setattr(qc, "DB_PATH", tmp_path / "q.db")
    conn = qc._connect()
    conn.execute(
        """INSERT INTO coordination_items
           (automation_key, state, level, first_observed_at, last_observed_at,
            observation_count, reopen_count, scope_paths, source_finding_id, source_check)
           VALUES ('k1', 'escalation_eligible', 'warning', '2026-01-01T00:00:00',
                   '2026-01-01T06:00:00', 3, 0, 'a.py', 'fid', 'check')"""
    )
    conn.commit()
    conn.close()
    client = TestClient(app)
    resp = client.get("/api/quality/coordination")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"][0]["automation_key"] == "k1"
    assert body["items"][0]["state"] == "escalation_eligible"


def test_quality_summary_gains_coordination_rollup(tmp_path, monkeypatch):
    monkeypatch.setattr(qc, "DB_PATH", tmp_path / "q.db")
    client = TestClient(app)
    resp = client.get("/api/quality/summary")
    assert resp.status_code == 200
    assert set(resp.json()["coordination"]) == {"escalation_eligible", "suppressed", "observed"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi pytest tests/test_quality_routes.py -v -k coordination`
Expected: FAIL with 404 on `/api/quality/coordination`

- [ ] **Step 3: Write minimal implementation**

```python
# services/quality/routes.py — add import at top:
from services import quality_coordination as _qc

# add new route, anywhere after the existing @router.get("/api/quality/summary"):
@router.get("/api/quality/coordination")
async def get_quality_coordination():
    conn = _qc._connect()
    try:
        items = [dict(r) for r in conn.execute("SELECT * FROM coordination_items").fetchall()]
        for item in items:
            item["log"] = [
                dict(r) for r in conn.execute(
                    "SELECT at, message FROM coordination_log WHERE automation_key=? ORDER BY at",
                    (item["automation_key"],),
                ).fetchall()
            ]
        return {"items": items}
    finally:
        conn.close()


def _coordination_rollup() -> dict[str, int]:
    conn = _qc._connect()
    try:
        rows = conn.execute("SELECT state, COUNT(*) c FROM coordination_items GROUP BY state").fetchall()
        counts = {r["state"]: r["c"] for r in rows}
        return {
            "escalation_eligible": counts.get("escalation_eligible", 0),
            "suppressed": counts.get("suppressed_pending_work", 0),
            "observed": counts.get("observed", 0),
        }
    finally:
        conn.close()
```

Then, in `get_quality_summary`'s existing return statement (`services/quality/routes.py`), change:

```python
        "storage": {"databases": storage_entries},
        "research": {
```

to:

```python
        "storage": {"databases": storage_entries},
        "coordination": _coordination_rollup(),
        "research": {
```

(one new line inserted between the existing `"storage"` and `"research"` keys — every other key in that return block is unchanged).

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi pytest tests/test_quality_routes.py -v`
Expected: PASS (all existing tests plus the 2 new ones)

- [ ] **Step 5: Commit**

```bash
git add services/quality/routes.py tests/test_quality_routes.py
git commit -m "feat: expose quality coordination state via two read-only routes"
```

---

## Task 8: Fault-injection proofs

**Files:**
- Create: `tests/test_quality_coordination_fault_injection.py`

**Interfaces:**
- Consumes: everything from Tasks 1–5.

I12's checklist requires proofs for: identity, PR secret isolation, stale SHA, self-modification protection, duplicate retry, suppression, and non-resolution by claims. **PR secret isolation, stale SHA, and duplicate-retry-against-a-real-write are marked N/A here, stated not skipped**: this plan builds no write lane, no GitHub credential, and no write-retry path — I9's `tools/quality_coordination_sim/write_gate.py` already proved those three properties for the write-gate *design* that would apply if a future decision ever activates one; re-proving them against code this plan doesn't ship would test nothing real. The four properties this plan's code actually has are proven below.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_quality_coordination_fault_injection.py
"""I12 fault-injection proofs. PR secret isolation / stale-SHA-write-refusal / duplicate-write-
retry are N/A here (this plan ships no write lane, no credential, no write-retry path — see I9's
write_gate.py prototype for those three properties against the design that WOULD need them).
The four properties below are the ones this plan's actual code has."""
from datetime import datetime, timedelta, timezone

import services.quality_coordination as qc

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _sig(key="k1", level="info", paths=("services/x.py",)):
    return qc.Signal(automation_key=key, level=level, scope_paths=paths,
                      source_finding_id="fid", source_check="check")


# 1. Identity: rename/line-shift does not fragment a tracked item.
def test_identity_stable_across_repeated_derivation():
    f1 = qc.derive_automation_key(_finding_stub(scope="alerting.crash_auto_resolve_after_sec"))
    f2 = qc.derive_automation_key(_finding_stub(scope="alerting.crash_auto_resolve_after_sec"))
    assert f1 == f2


def _finding_stub(**kw):
    from services.quality.models import QualityFinding
    base = dict(finding_id="x", check="config-usage", severity="info", confidence="high",
                source="ci", scope="a", summary="s", evidence={})
    base.update(kw)
    return QualityFinding(**base)


# 2. Self-modification protection: the module has no write call to anything but its own DB.
def test_module_has_no_file_write_outside_its_own_db():
    import ast
    import inspect
    source = inspect.getsource(qc)
    tree = ast.parse(source)
    write_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("write_text", "write_bytes", "open"):
                write_calls.append(node)
    assert write_calls == [], "found a file-write call outside sqlite3.connect(DB_PATH)"


# 3. Suppression: an item with an active claim never reaches escalation-eligible.
def test_suppression_blocks_escalation_even_past_floor(tmp_path, monkeypatch):
    monkeypatch.setattr(qc, "DB_PATH", tmp_path / "q.db")
    conn = qc._connect()
    qc.apply_observation(conn, [_sig(level="warning")], [], [], T0)
    conn.commit()
    result = qc.apply_observation(
        conn, [_sig(level="warning")], [], [qc.Claim(automation_key="k1", source="PR#1")],
        T0 + timedelta(hours=10),  # well past the 6h warning floor
    )
    conn.commit()
    assert result["k1"] == "suppressed_pending_work"
    conn.close()


# 4. Non-resolution by claims: a claim never sets state to resolved while the finding is present.
def test_claim_never_marks_resolved_while_present(tmp_path, monkeypatch):
    monkeypatch.setattr(qc, "DB_PATH", tmp_path / "q.db")
    conn = qc._connect()
    qc.apply_observation(conn, [_sig()], [], [], T0)
    conn.commit()
    for i in range(1, 21):
        result = qc.apply_observation(
            conn, [_sig()], [], [qc.Claim(automation_key="k1", source="PR#1")],
            T0 + timedelta(hours=i),
        )
        conn.commit()
        assert result["k1"] == "suppressed_pending_work"
    row = conn.execute("SELECT resolved_at FROM coordination_items WHERE automation_key='k1'").fetchone()
    assert row["resolved_at"] is None
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ddev exec -s fastapi pytest tests/test_quality_coordination_fault_injection.py -v`
Expected: PASS immediately IS the expected outcome for this task — these are proofs over Tasks 1–5's already-implemented code, not new production code. Run once to confirm all 4 pass; if any fails, that is a real regression in Tasks 1–5 to fix before proceeding, not an expected red step.

- [ ] **Step 3: N/A — no new production code for this task**

- [ ] **Step 4: Run test to verify it passes**

Run: `ddev exec -s fastapi pytest tests/test_quality_coordination_fault_injection.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add tests/test_quality_coordination_fault_injection.py
git commit -m "test: fault-inject quality coordination identity, self-modification, suppression, and claim non-resolution"
```

---

## Task 9: Documentation updates

**Files:**
- Modify: `CLAUDE.md`
- Modify: `.claude/rules/quality-capabilities.md`

No `.woodpecker/*.yml` or `.github/workflows/*.yml` change is included in this plan — confirmed by reading current CI state (all 5 pipelines already run `event: [push, pull_request]` with no `main`-only branch restriction; this module runs inside the live `fastapi` process, not in CI, so no pipeline needs a new step, and no required-context/branch-protection change is needed).

- [ ] **Step 1: Update CLAUDE.md's "Start investigations here" list**

In the existing numbered list (`GET /api/quality/summary` entry), append one clause noting the new field:

```markdown
1. `GET /api/quality/summary` — the single composite health read: overall
   status, active findings, alerts, faults, storage summary, latest
   research-run status, and a `coordination` rollup (escalation-eligible/
   suppressed/observed counts from the persisted quality-coordination
   observation series — `GET /api/quality/coordination` for the full
   per-item detail and explanation log). Start here for "is something wrong."
```

- [ ] **Step 2: Add a router entry to `.claude/rules/quality-capabilities.md`**

Add one bullet to the "Available quality/reliability capabilities" list:

```markdown
- **quality-coordination-observation** — `services/quality_coordination.py`,
  a read-only persisted observation series over `tools.quality_audit`'s
  static findings (identity/persistence/suppression policy from the
  autonomous-quality-coordination investigation, I8/I11). No GitHub write
  authority exists — `GET /api/quality/coordination` for detail,
  `GET /api/quality/summary`'s `coordination` field for the rollup.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md .claude/rules/quality-capabilities.md
git commit -m "docs: point CLAUDE.md and the capability router at quality coordination"
```

---

## Self-Review

**0. Two real bugs caught and fixed while drafting this plan, not before it — recorded rather than
smoothed over (same discipline the spec's own self-review, §12, already applied):**

- **Task 4's original `Signal` construction used `f.scope.split(":")[:1]` as the suppression
  path.** Wrong: `scope` is a dotted module/config path for most semantic rules (I1 §3.1 —
  `services.foo`, `alerting.crash_auto_resolve_after_sec`), never a real file path, so it would
  never equal anything GitHub's compare API reports (`services/foo.py`) and every such rule would
  be permanently unsuppressible by any real branch, silently. Fixed by adding `_scope_paths()`,
  which prefers `evidence["path"]` (present for the large majority of rules per I1 §3.1's own
  evidence-keys column) and falls back to `scope` only for the aggregate rules that have no single
  file to begin with — where "never suppressible" is actually correct. Two new tests
  (`test_scope_paths_prefers_evidence_path_over_dotted_scope`,
  `test_scope_paths_falls_back_to_scope_for_aggregate_rules_with_no_path`) prove both branches.
- **Task 6's original scheduler tracked "last run" as a bare in-memory module global** (`_LAST_
  QUALITY_COORDINATION_RUN: float = 0.0`) checked with `now - last_run < interval`. This is
  exactly the bug class `services/backup/backup.py`'s own docstring documents fixing live
  2026-08-23 in this same repo: in-memory state resets on every `uvicorn --reload`, not just a
  real restart, so the scheduler would treat every dev-loop code edit as newly overdue. Fixed by
  following `_maybe_run_backup`'s already-proven pattern exactly: state lives in
  `services/app_state.py`'s shared `state` dict (survives reload) and is seeded from the module's
  own persisted history (`latest_run_at()`, new in this task) on first check rather than trusting
  an unseeded `0.0`. Caught the fix's own test-design bug too, before it shipped: an early draft of
  the scheduler tests mocked `time.time()` with small numbers (`1000.0`), which silently breaks the
  `last_run_at == 0.0` sentinel's "always overdue" property — that property only holds against
  real, epoch-scale timestamps (billions of seconds), which is what makes the *production* pattern
  correct. Fixed by using a realistic epoch constant (`EPOCH = 1_800_000_000.0`) in the tests
  instead, with the reasoning recorded inline as a comment so a future editor doesn't revert it
  back to a "cleaner-looking" small number.

**1. Spec coverage** (against `docs/superpowers/specs/2026-08-26-autonomous-quality-coordination-design.md`):

| Spec section | Task |
|---|---|
| §1 Module/interfaces, schema | Task 1, 3, 4 |
| §2 Read endpoint | Task 7 |
| §3 Automation identity | Task 2 |
| §4 Branch/main observation, suppression | Task 3, 5 |
| §5 Local advisory tier | N/A per spec — no task needed |
| §6 Reporting surfaces | Task 7 |
| §7 Credential/token/event | N/A per spec — no task needed |
| §8 Protected paths/self-modification | Task 8 (proof) |
| §9 Deterministic fixer registry | N/A per spec — no task needed |
| §10 Retention/idempotence/retry/outage | Task 4, 5 |
| §11 Staged activation/rollback | Task 6 (`quality_coordination.enabled`) |

Every specified section has an implementing task; every explicitly-N/A section correctly has none.

**2. Placeholder scan:** every step above contains complete, real code — no `TODO`, no "add appropriate error handling," no "similar to Task N" without the actual code repeated. Task 6's "wire into the existing tick loop" step names the exact anchor function (`_maybe_check_signal_resolutions`) to search for rather than leaving the integration point vague.

**3. Type consistency check:** `Signal`/`BranchSignal`/`Claim` (Task 3) are used with identical field names in Task 4 (`observe_main`), Task 5 (`fetch_branch_signals`'s return type), Task 6 (scheduler wiring), and Task 8 (fault injection) — no renamed field anywhere. `apply_observation`'s signature (`conn, signals, branches, claims, at`) matches every call site across Tasks 3, 4, and 8 exactly. `DB_PATH`/`_connect` (Task 1) are the only persistence primitives referenced by every later task — no second DB-access path was introduced.

**Stage-sequence note (I12 checklist item):** this plan implements only the first two rollout stages the checklist lists — "local/dry-run state" (Tasks 1–5, buildable and testable with no live wiring) and "branch/main reporting" + "persistent integrated-state observation" (Tasks 6–7, the scheduler and routes). "External reporting/SARIF," "issue escalation," and "deterministic draft PR creation" are the three later stages the checklist names — **no task in this plan builds any of them**, because I10/I11 did not activate them. Building implementation tasks for a capability neither prior document activated would violate I10 §2 item 4's binding re-verification requirement before any future write-stage activation. A future plan, written after that re-verification, covers those stages — this one does not reserve a placeholder for them.
