import json

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


def test_save_populates_series_metadata_and_series_tags(tmp_path, monkeypatch):
    cache = _sc(tmp_path, monkeypatch)
    series = [{
        "ticker": "KXNFLGAME", "category": "Sports", "frequency": "weekly",
        "tags": ["NFL", "Football"],
        "settlement_sources": [{"name": "NFL.com", "url": "https://nfl.com"}],
        "contract_url": "https://kalshi.com/c/1", "contract_terms_url": "https://kalshi.com/t/1",
        "fee_type": "quadratic", "fee_multiplier": 1.0,
        "additional_prohibitions": ["some_state_official"], "exchange_index": 3,
        "volume_fp": "5000000", "last_updated_ts": "2026-08-30T00:00:00Z",
    }]
    cache.save(1755000000.0, series)
    with cache._connect() as conn:
        row = conn.execute(
            "SELECT category, frequency, tags_json, settlement_sources_json, contract_url, "
            "contract_terms_url, fee_type, fee_multiplier, additional_prohibitions_json, "
            "exchange_index, volume_fp, last_updated_ts, fetched_at "
            "FROM series_metadata WHERE ticker = ?", ("KXNFLGAME",),
        ).fetchone()
        tags = {r[0] for r in conn.execute(
            "SELECT tag FROM series_tags WHERE ticker = ?", ("KXNFLGAME",))}
    assert row[0:2] == ("Sports", "weekly")
    assert json.loads(row[2]) == ["NFL", "Football"]
    assert json.loads(row[3]) == [{"name": "NFL.com", "url": "https://nfl.com"}]
    assert row[4:8] == ("https://kalshi.com/c/1", "https://kalshi.com/t/1", "quadratic", 1.0)
    assert json.loads(row[8]) == ["some_state_official"]
    assert row[9:] == (3, "5000000", "2026-08-30T00:00:00Z", 1755000000.0)
    assert tags == {"NFL", "Football"}


def test_save_replaces_series_tags_on_resave_not_accumulates(tmp_path, monkeypatch):
    # get-series.md's tags array can shrink upstream - a tag removed from a
    # series must not leave a stale series_tags row forever (spec §1.3:
    # "delete-then-reinsert per ticker, not a diff").
    cache = _sc(tmp_path, monkeypatch)
    cache.save(100.0, [{"ticker": "K1", "tags": ["A", "B"]}])
    cache.save(200.0, [{"ticker": "K1", "tags": ["A"]}])
    with cache._connect() as conn:
        tags = {r[0] for r in conn.execute("SELECT tag FROM series_tags WHERE ticker = ?", ("K1",))}
    assert tags == {"A"}


def test_save_handles_series_with_no_optional_fields(tmp_path, monkeypatch):
    # get-series.md marks tags/settlement_sources/additional_prohibitions
    # all nullable - a bare series dict must not raise.
    cache = _sc(tmp_path, monkeypatch)
    cache.save(100.0, [{"ticker": "BARE"}])
    with cache._connect() as conn:
        row = conn.execute(
            "SELECT category, tags_json, fee_multiplier, exchange_index "
            "FROM series_metadata WHERE ticker = ?", ("BARE",),
        ).fetchone()
        tag_count = conn.execute(
            "SELECT COUNT(*) FROM series_tags WHERE ticker = ?", ("BARE",)).fetchone()[0]
    assert row == (None, "[]", None, None)
    assert tag_count == 0


def test_existing_blob_write_is_unchanged(tmp_path, monkeypatch):
    # D1 is additive alongside series_cache, not instead of it (spec §1.1) -
    # this pins that the pre-existing round trip still works untouched.
    cache = _sc(tmp_path, monkeypatch)
    series = [{"ticker": "KXBTCD", "category": "Crypto", "volume_fp": "3000000"}]
    cache.save(1755000000.0, series)
    result = cache.load()
    assert result == {"fetched_at": 1755000000.0, "series": series}
