import pytest

from services.whale_calibration import calibration_history as ch


@pytest.fixture(autouse=True)
def _redirect_db(tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "DB_PATH", tmp_path / "calibration_history.db")


def _report(resolved_count=100, overall_win_rate=52.0, weights=None):
    return {
        "resolved_count": resolved_count,
        "overall_win_rate": overall_win_rate,
        "per_factor": [
            {"factor": "depth_factor", "gap_pts": 12.5, "discriminates": True},
            {"factor": "unusualness_factor", "gap_pts": -8.0, "discriminates": False},
        ],
        "current_weights": weights or {"depth_factor": 0.25, "unusualness_factor": 0.03},
    }


def test_due_true_on_first_call_ever():
    assert ch.due(1000.0, interval_sec=3600) is True


def test_due_false_immediately_after_a_snapshot():
    ch.record_snapshot(_report(), now=1000.0)
    assert ch.due(1500.0, interval_sec=3600) is False


def test_due_true_once_interval_has_elapsed():
    ch.record_snapshot(_report(), now=1000.0)
    assert ch.due(1000.0 + 3600, interval_sec=3600) is True


def test_record_snapshot_persists_headline_numbers():
    ch.record_snapshot(_report(resolved_count=150, overall_win_rate=48.3), now=1000.0)
    rows = ch.history()
    assert len(rows) == 1
    assert rows[0]["resolved_count"] == 150
    assert rows[0]["overall_win_rate_pct"] == 48.3
    assert rows[0]["recorded_at"] == 1000.0


def test_record_snapshot_persists_per_factor_gaps_and_weights():
    ch.record_snapshot(_report(), now=1000.0)
    row = ch.history()[0]
    assert row["per_factor"]["depth_factor"] == {"gap_pts": 12.5, "discriminates": True}
    assert row["per_factor"]["unusualness_factor"] == {"gap_pts": -8.0, "discriminates": False}
    assert row["weights"] == {"depth_factor": 0.25, "unusualness_factor": 0.03}


def test_history_returns_newest_first():
    ch.record_snapshot(_report(resolved_count=100), now=1000.0)
    ch.record_snapshot(_report(resolved_count=200), now=2000.0)
    ch.record_snapshot(_report(resolved_count=300), now=3000.0)
    rows = ch.history()
    assert [r["resolved_count"] for r in rows] == [300, 200, 100]


def test_history_respects_limit():
    for i in range(5):
        ch.record_snapshot(_report(resolved_count=i), now=1000.0 + i)
    rows = ch.history(limit=2)
    assert len(rows) == 2


def test_clear_all_wipes_every_snapshot():
    ch.record_snapshot(_report(), now=1000.0)
    ch.clear_all()
    assert ch.history() == []
