from services import title_cache as tc


def _tc(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "DB_PATH", tmp_path / "title_cache.db")
    return tc


# --- market_title_fields() - the shared title/sub-title builder --------------
# Previously reimplemented independently in three places (main.py's
# new_market_titles builder, /api/markets/search, and market_catalog.
# upsert_markets), each free to drift - consolidated into one function so
# there's exactly one place this fallback logic can be wrong.

def test_market_title_fields_prefers_real_title():
    m = {"ticker": "T-A", "title": "Real Title", "yes_sub_title": "Yes Sub", "no_sub_title": "No Sub"}
    result = tc.market_title_fields(m)
    assert result == {"title": "Real Title", "yes_sub_title": "Yes Sub", "no_sub_title": "No Sub"}


def test_market_title_fields_falls_back_to_yes_sub_title_when_title_missing():
    # A child of a multi-outcome event can carry a real yes_sub_title with
    # no separate title field at all.
    m = {"ticker": "T-A", "title": None, "yes_sub_title": "Golf / Golfer / Golfing"}
    result = tc.market_title_fields(m)
    assert result["title"] == "Golf / Golfer / Golfing"


def test_market_title_fields_falls_back_to_ticker_as_last_resort():
    m = {"ticker": "T-A", "title": None, "yes_sub_title": None}
    result = tc.market_title_fields(m)
    assert result["title"] == "T-A"


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


def test_event_titles_round_trip_carries_competition(tmp_path, monkeypatch):
    # product_metadata.competition/competition_scope - real Kalshi fields,
    # already fetched on every get_event() call but previously discarded.
    # Direct display value ("Wyndham Championship" on a golf pairing's
    # event card) - see static/index.html's eventGroupCardHTML.
    cache = _tc(tmp_path, monkeypatch)
    cache.save_event_titles({
        "EVT-A": {
            "title": "3rd Round Head-to-Head: Hossler vs James", "sub_title": None, "category": "Sports",
            "mutually_exclusive": True, "competition": "Wyndham Championship", "competition_scope": "3rd Round Matchups",
        },
    })
    result = cache.load_event_titles()["EVT-A"]
    assert result["competition"] == "Wyndham Championship"
    assert result["competition_scope"] == "3rd Round Matchups"


def test_event_titles_competition_is_none_when_legitimately_absent(tmp_path, monkeypatch):
    # Real, honest absence (e.g. a politics/economics event has no
    # "competition" at all) - distinct from mutually_exclusive, where a
    # cached None specifically means "not yet fetched," competition being
    # None is a genuine value, not a backfill signal (see main.py's
    # _fetch_event_titles).
    cache = _tc(tmp_path, monkeypatch)
    cache.save_event_titles({
        "EVT-A": {"title": "Fed Rate Decision", "sub_title": None, "category": "Economics", "mutually_exclusive": True},
    })
    result = cache.load_event_titles()["EVT-A"]
    assert result["competition"] is None
    assert result["competition_scope"] is None


def test_event_titles_round_trip_carries_strike_date_and_nested_fields(tmp_path, monkeypatch):
    # 2026-08-15: the fields main.py._fetch_event_titles has always
    # extracted (part of its own required_event_fields re-fetch check) but
    # this table never had columns for - services/market_events/event_schedule.py now
    # depends on strike_date specifically (confirmed live against KXFED,
    # not covered by the milestone API at all).
    cache = _tc(tmp_path, monkeypatch)
    cache.save_event_titles({
        "KXFED-26SEP": {
            "title": "Fed Rate Decision", "sub_title": "On Sep 16, 2026", "category": "Economics",
            "mutually_exclusive": True, "series_ticker": "KXFED", "available_on_brokers": True,
            "collateral_return_type": "binary", "strike_date": "2026-09-16T18:00:00Z", "strike_period": "",
            "fee_type_override": None, "fee_multiplier_override": 1.0, "last_updated_ts": "2026-08-01T00:00:00Z",
            "product_metadata": {"competition": "FOMC"}, "settlement_sources": [{"name": "Fed", "url": "https://federalreserve.gov"}],
        },
    })
    result = cache.load_event_titles()["KXFED-26SEP"]
    assert result["series_ticker"] == "KXFED"
    assert result["available_on_brokers"] is True
    assert result["strike_date"] == "2026-09-16T18:00:00Z"
    assert result["fee_multiplier_override"] == 1.0
    assert result["product_metadata"] == {"competition": "FOMC"}
    assert result["settlement_sources"] == [{"name": "Fed", "url": "https://federalreserve.gov"}]


def test_event_titles_new_fields_default_sensibly_when_unset(tmp_path, monkeypatch):
    cache = _tc(tmp_path, monkeypatch)
    cache.save_event_titles({"EVT-A": {"title": "Some Event", "sub_title": None, "category": None}})
    result = cache.load_event_titles()["EVT-A"]
    assert result["strike_date"] is None
    assert result["available_on_brokers"] is None
    assert result["product_metadata"] == {}
    assert result["settlement_sources"] == []


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
