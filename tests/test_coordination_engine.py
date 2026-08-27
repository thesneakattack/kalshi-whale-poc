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
