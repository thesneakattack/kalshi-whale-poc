from services import title_cache as tc


def _tc(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "DB_PATH", tmp_path / "title_cache.db")
    return tc


def test_market_titles_round_trip(tmp_path, monkeypatch):
    cache = _tc(tmp_path, monkeypatch)
    cache.save_market_titles({
        "TICK-A": {"title": "Title A", "yes_sub_title": "Yes A", "no_sub_title": "No A", "event_ticker": "EVT-A"},
    })
    result = cache.load_market_titles()
    assert result["TICK-A"] == {
        "title": "Title A", "yes_sub_title": "Yes A", "no_sub_title": "No A", "event_ticker": "EVT-A",
    }


def test_market_titles_upsert_overwrites_on_conflict(tmp_path, monkeypatch):
    cache = _tc(tmp_path, monkeypatch)
    cache.save_market_titles({"TICK-A": {"title": "Old", "yes_sub_title": None, "no_sub_title": None, "event_ticker": None}})
    cache.save_market_titles({"TICK-A": {"title": "New", "yes_sub_title": None, "no_sub_title": None, "event_ticker": None}})
    assert cache.load_market_titles()["TICK-A"]["title"] == "New"


def test_event_titles_round_trip_carries_mutually_exclusive(tmp_path, monkeypatch):
    # Real Kalshi field, already fetched on every get_event() call but
    # previously discarded - lets a 2-outcome inversion pair ("Toronto vs
    # Philadelphia Winner") be told apart from a genuine multi-outcome
    # market or independent sibling props - see
    # static/index.html's dedupeInversionPairs/eventGroupCardHTML.
    cache = _tc(tmp_path, monkeypatch)
    cache.save_event_titles({
        "EVT-A": {"title": "Toronto vs Philadelphia", "sub_title": None, "category": "Sports", "mutually_exclusive": True},
        "EVT-B": {"title": "Wyndham Championship Winner", "sub_title": None, "category": "Sports", "mutually_exclusive": False},
    })
    result = cache.load_event_titles()
    assert result["EVT-A"]["mutually_exclusive"] is True
    assert result["EVT-B"]["mutually_exclusive"] is False


def test_event_titles_mutually_exclusive_defaults_to_none_when_unset(tmp_path, monkeypatch):
    cache = _tc(tmp_path, monkeypatch)
    cache.save_event_titles({"EVT-A": {"title": "Some Event", "sub_title": None, "category": None}})
    assert cache.load_event_titles()["EVT-A"]["mutually_exclusive"] is None


def test_event_titles_upsert_overwrites_mutually_exclusive_on_conflict(tmp_path, monkeypatch):
    cache = _tc(tmp_path, monkeypatch)
    cache.save_event_titles({"EVT-A": {"title": "T", "sub_title": None, "category": None, "mutually_exclusive": False}})
    cache.save_event_titles({"EVT-A": {"title": "T", "sub_title": None, "category": None, "mutually_exclusive": True}})
    assert cache.load_event_titles()["EVT-A"]["mutually_exclusive"] is True


def test_add_column_if_missing_is_idempotent_on_a_pre_existing_table(tmp_path, monkeypatch):
    # data/title_cache.db is a live file (CLAUDE.md) - simulates an
    # existing table from before this column existed, confirming the
    # guarded ALTER TABLE runs cleanly against real pre-existing rows
    # rather than erroring on a duplicate-column or missing-table issue.
    import sqlite3
    cache = _tc(tmp_path, monkeypatch)
    conn = sqlite3.connect(cache.DB_PATH)
    conn.execute("CREATE TABLE event_titles (event_ticker TEXT PRIMARY KEY, title TEXT, sub_title TEXT, category TEXT)")
    conn.execute("INSERT INTO event_titles VALUES ('EVT-OLD', 'Old Event', NULL, NULL)")
    conn.commit()
    conn.close()

    cache.save_event_titles({"EVT-NEW": {"title": "New Event", "sub_title": None, "category": None, "mutually_exclusive": True}})
    result = cache.load_event_titles()
    assert result["EVT-OLD"]["title"] == "Old Event"
    assert result["EVT-OLD"]["mutually_exclusive"] is None  # pre-existing row, column was NULL by default
    assert result["EVT-NEW"]["mutually_exclusive"] is True
