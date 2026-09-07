"""coordination_engine — shared, signal-shape-agnostic identity/fingerprint/
persistence-floor/suppression/resolution state machine
(docs/archive/lane-9-tooling-ci-process-governance/specs/2026-08-27-autonomous-quality-coordination-workflow-design.md
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

import contextlib
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from services import db

DB_PATH = Path(__file__).resolve().parent / "quality_coordination_data" / "quality_coordination.db"


def _init_signal_state(conn: sqlite3.Connection) -> None:
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


def _init_coordination_runs(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS coordination_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ran_at TEXT NOT NULL,
        signals_observed INTEGER NOT NULL,
        signals_escalated INTEGER NOT NULL,
        error TEXT
    )""")


def _init_cleanup_actions(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS cleanup_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        identity TEXT NOT NULL,
        action_type TEXT NOT NULL,
        executed_at TEXT NOT NULL,
        dry_run INTEGER NOT NULL,
        outcome TEXT NOT NULL
    )""")


db.register_schema("signal_state", _init_signal_state)
db.register_schema("coordination_runs", _init_coordination_runs)
db.register_schema("cleanup_actions", _init_cleanup_actions)


@contextlib.contextmanager
def _connect():
    """Every existing `with ce._connect() as conn:` call site keeps working
    unchanged - now backed by services/db.py's closing connect(). WAL mode
    and the busy_timeout pragma are set by db.connect() itself, same as
    every other migrated module.

    conn.row_factory = sqlite3.Row is caller-side state on the connection
    object, not schema, so it's set here on the yielded connection before
    yield - same reasoning as services/settlement_edge.py's Task 11, but
    there it stayed at the one call site that needed it; here every one of
    this module's 24 callers relies on Row access (dict-style ["col"]
    lookups), so it moves into the wrapper itself, unchanged from where the
    pre-migration _connect() used to set it."""
    with db.connect(
        DB_PATH, tables=("signal_state", "coordination_runs", "cleanup_actions")
    ) as conn:
        conn.row_factory = sqlite3.Row
        yield conn


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
    paused-track-note, and (Task 3) co-dispatch-cluster suppression candidates; its "a
    failure/error state should surface immediately, never behind a persistence floor"
    requirement). Not shown in spec §7's minimal 3-positional-arg illustrative snippet -
    added as keyword-only extensions; see this plan's Self-Review for the reasoning.
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
