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
