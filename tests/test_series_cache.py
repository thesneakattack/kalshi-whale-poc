from services import series_cache as sc


def _sc(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "DB_PATH", tmp_path / "series_cache.db")
    return sc


def test_load_returns_empty_shape_when_nothing_persisted_yet(tmp_path, monkeypatch):
    cache = _sc(tmp_path, monkeypatch)
    assert cache.load() == {"fetched_at": 0.0, "series": []}


def test_save_then_load_round_trips(tmp_path, monkeypatch):
    cache = _sc(tmp_path, monkeypatch)
    series = [
        {"ticker": "KXNFLGAME", "title": "Pro Football Game", "category": "Sports", "volume_fp": "5000000"},
        {"ticker": "KXBTCD", "title": "Bitcoin Price", "category": "Crypto", "volume_fp": "3000000"},
    ]
    cache.save(1755000000.0, series)
    result = cache.load()
    assert result["fetched_at"] == 1755000000.0
    assert result["series"] == series


def test_save_overwrites_previous_snapshot(tmp_path, monkeypatch):
    cache = _sc(tmp_path, monkeypatch)
    cache.save(100.0, [{"ticker": "OLD"}])
    cache.save(200.0, [{"ticker": "NEW"}])
    result = cache.load()
    assert result["fetched_at"] == 200.0
    assert result["series"] == [{"ticker": "NEW"}]
