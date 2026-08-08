import time

from services import signal_log as sl


def _log(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "DB_PATH", tmp_path / "signal_log.db")
    return sl


def test_log_signal_and_overall_stats(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    log.log_signal("TICK-A-123", "yes", 1000, 0.8, "simulated")
    stats = log.stats(days=30)
    assert stats["total_signals"] == 1
    assert stats["resolved"] == 0
    assert stats["win_rate"] is None


def test_series_extraction_uses_prefix_before_first_hyphen(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    log.log_signal("KXOSCARBESTPICTURE-26-XYZ", "yes", 1000, 0.8, "simulated")
    stats = log.series_stats("KXOSCARBESTPICTURE-26-OTHER", days=30)
    assert stats["series"] == "KXOSCARBESTPICTURE"
    assert stats["total_signals"] == 1  # same series prefix, counted together


def test_unresolved_batch_respects_older_than_sec(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    now = time.time()
    log.log_signal("TICK-A", "yes", 1000, 0.8, "simulated", seen_at=now)          # too recent
    log.log_signal("TICK-B", "yes", 1000, 0.8, "simulated", seen_at=now - 1000)   # old enough
    batch = log.unresolved_batch(limit=10, older_than_sec=600)
    tickers = {row["ticker"] for row in batch}
    assert tickers == {"TICK-B"}


def test_mark_resolved_updates_win_rate(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    now = time.time()
    log.log_signal("TICK-A", "yes", 1000, 0.8, "simulated", seen_at=now - 1000)
    batch = log.unresolved_batch(limit=10, older_than_sec=600)
    assert len(batch) == 1
    log.mark_resolved(batch[0]["id"], correct=True)
    stats = log.stats(days=30)
    assert stats["resolved"] == 1
    assert stats["correct"] == 1
    assert stats["win_rate"] == 100.0


def test_series_stats_scoped_to_series_not_global(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    log.log_signal("AAA-1", "yes", 1000, 0.8, "simulated")
    log.log_signal("BBB-1", "yes", 1000, 0.8, "simulated")
    stats = log.series_stats("AAA-2", days=30)  # different market, same AAA series
    assert stats["series"] == "AAA"
    assert stats["total_signals"] == 1


def test_window_days_excludes_old_signals(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    now = time.time()
    log.log_signal("TICK-A", "yes", 1000, 0.8, "simulated", seen_at=now - 40 * 86400)  # 40 days ago
    stats = log.stats(days=30)
    assert stats["total_signals"] == 0


def test_clear_all_wipes_every_signal(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    log.log_signal("TICK-A", "yes", 1000, 0.8, "simulated")
    log.log_signal("TICK-B", "no", 2000, 0.7, "simulated")
    log.clear_all()
    stats = log.stats(days=30)
    assert stats["total_signals"] == 0
    assert log.series_stats("TICK-A", days=30)["total_signals"] == 0


def test_recent_returns_newest_first(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    now = time.time()
    log.log_signal("TICK-A", "yes", 1000, 0.8, "simulated", seen_at=now - 100)
    log.log_signal("TICK-B", "no", 2000, 0.7, "simulated", seen_at=now)
    rows = log.recent(limit=10)
    assert [r["ticker"] for r in rows] == ["TICK-B", "TICK-A"]


def test_recent_respects_limit_and_offset(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    now = time.time()
    for i in range(5):
        log.log_signal(f"TICK-{i}", "yes", 1000, 0.8, "simulated", seen_at=now - i)
    first_page = log.recent(limit=2, offset=0)
    second_page = log.recent(limit=2, offset=2)
    assert [r["ticker"] for r in first_page] == ["TICK-0", "TICK-1"]
    assert [r["ticker"] for r in second_page] == ["TICK-2", "TICK-3"]


def test_recent_resolved_only_excludes_unresolved(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    now = time.time()
    log.log_signal("TICK-A", "yes", 1000, 0.8, "simulated", seen_at=now - 700)
    log.log_signal("TICK-B", "no", 2000, 0.7, "simulated", seen_at=now)
    batch = log.unresolved_batch(limit=10, older_than_sec=600)
    assert len(batch) == 1
    log.mark_resolved(batch[0]["id"], correct=True)

    all_rows = log.recent(limit=10)
    resolved_rows = log.recent(limit=10, resolved_only=True)
    assert len(all_rows) == 2
    assert len(resolved_rows) == 1
    assert resolved_rows[0]["ticker"] == "TICK-A"
    assert resolved_rows[0]["correct"] == 1

    unresolved_row = next(r for r in all_rows if r["ticker"] == "TICK-B")
    assert unresolved_row["resolved"] == 0
    assert unresolved_row["correct"] is None


def test_total_count(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    log.log_signal("TICK-A", "yes", 1000, 0.8, "simulated")
    log.log_signal("TICK-B", "no", 2000, 0.7, "simulated")
    assert log.total_count() == 2
    assert log.total_count(resolved_only=True) == 0
