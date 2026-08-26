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
