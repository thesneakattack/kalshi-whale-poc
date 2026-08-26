"""Read-only autonomous quality coordination — persisted observation series only.

No GitHub write credential, no issue/PR authority, no write path outside this module's own
data/quality_coordination.db. See docs/superpowers/specs/2026-08-26-autonomous-quality-
coordination-design.md for the full design; this module implements that spec exactly.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from services.quality.models import QualityFinding

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "quality_coordination.db"

_HOST_SNIPPET_MAX = 80


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # WAL mode: lets a reader (e.g. GET /api/quality/coordination) proceed concurrently
    # with the background coordination cycle's writer instead of blocking on the default
    # rollback-journal lock - same hardening every other DB-owning module in this repo
    # applies (see services/paper_broker.py's _connect for the original incident). Idempotent
    # - safe to run on every connect.
    conn.execute("PRAGMA journal_mode=WAL")
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
        audit_fingerprint TEXT NOT NULL,
        commit_sha TEXT,
        ran_at TEXT NOT NULL,
        items_observed INTEGER NOT NULL,
        items_resolved INTEGER NOT NULL,
        error TEXT
    )""")
    return conn


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
            last = conn.execute(
                "SELECT audit_fingerprint FROM coordination_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if last is None or last["audit_fingerprint"] != fp:
                conn.execute(
                    """INSERT INTO coordination_runs
                       (audit_fingerprint, commit_sha, ran_at, items_observed, items_resolved, error)
                       VALUES (?, ?, ?, 0, 0, ?)""",
                    (fp, _current_commit_sha(repo_root), at.isoformat(), str(exc)),
                )
                conn.commit()
            return RunResult(fp, None, 0, 0, {}, str(exc))

        fp = _fingerprint(report)
        last = conn.execute(
            "SELECT * FROM coordination_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if last is not None and last["audit_fingerprint"] == fp:
            return RunResult(fp, last["commit_sha"], 0, 0, {}, last["error"])

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
    Degrades to [] on any failure — never raises, per I11 §10's outage-behavior design. Every
    branch's own fetch AND response parsing lives inside that branch's own try/except, so a
    single malformed response (a missing key, a commits entry with a null committer, a
    /compare payload that isn't the expected shape) only skips that one branch instead of
    raising out of the function and aborting the whole coordination cycle (including the
    unrelated audit-observation half). The /branches payload itself is also guarded — a
    non-list response degrades to [] the same way a network failure does."""
    base = _GITHUB_API.format(repo=repo)
    try:
        branches = _http_get_json(f"{base}/branches", timeout)
    except Exception:
        return []

    if not isinstance(branches, list):
        return []

    signals: list[BranchSignal] = []
    for b in branches:
        try:
            if not isinstance(b, dict):
                continue
            name = b.get("name")
            if not name or name == "main":
                continue
            compare = _http_get_json(f"{base}/compare/main...{name}", timeout)
            if not isinstance(compare, dict):
                continue
            paths = tuple(
                f["filename"] for f in compare.get("files", []) or []
                if isinstance(f, dict) and "filename" in f
            )
            commits = compare.get("commits", []) or []
            last_commit_iso = None
            if isinstance(commits, list) and commits:
                last_commit = commits[-1]
                if isinstance(last_commit, dict):
                    commit = last_commit.get("commit") or {}
                    committer = commit.get("committer") if isinstance(commit, dict) else None
                    if isinstance(committer, dict):
                        last_commit_iso = committer.get("date")
            if not paths or not last_commit_iso:
                continue
            signals.append(BranchSignal(name=name, changed_paths=paths, last_commit_at_iso=last_commit_iso))
        except Exception:
            continue
    return signals


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


def run_coordination_cycle(repo_root: Path) -> RunResult:
    """The full cycle: fetch branch signals, derive claims, observe main. Synchronous and
    blocking by design (fetch_branch_signals does a real urlopen; observe_main runs a full
    tools.quality_audit pass) — the async wrapper in main.py is what keeps this off the
    event loop, not this function itself, exactly matching backup.py's
    run_backup_cycle/_run_backup_background split. That also makes this directly callable
    from a plain script with no asyncio involved at all, same as backup.py's own
    `if __name__ == "__main__":` block.

    Returns RunResult (Task 4's dataclass, from observe_main) — NOT a plain dict, unlike
    backup.py's run_backup_cycle. A dataclass isn't JSON-serializable on its own; the
    __main__ block below converts via dataclasses.asdict() before json.dumps()."""
    branches = fetch_branch_signals()
    claims = derive_claims()
    return observe_main(repo_root, branches=branches, claims=claims)


if __name__ == "__main__":
    import dataclasses
    import json

    _repo_root = Path(__file__).resolve().parent.parent
    _result = run_coordination_cycle(_repo_root)
    print(json.dumps(dataclasses.asdict(_result), indent=2))
