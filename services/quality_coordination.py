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
