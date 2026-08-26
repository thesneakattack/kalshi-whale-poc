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
