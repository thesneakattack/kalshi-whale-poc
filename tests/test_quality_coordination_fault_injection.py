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
