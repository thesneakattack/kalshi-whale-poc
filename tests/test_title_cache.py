from services import title_cache as tc


def _tc(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "DB_PATH", tmp_path / "title_cache.db")
    return tc


def test_connect_closes_its_connection(tmp_path, monkeypatch):
    """Same fd-leak class as market_history.py's Task 2 - five call sites
    in this module share one non-closing _connect(), and the live fd
    census (2026-09-02) measured this file's handle count growing fastest
    of any store (5 -> 148+ in under an hour).

    Deviation from the plan's literal test body (docs/superpowers/plans/
    2026-09-03-tier0-live-incident-remediation.md, Task 3 Step 1): the
    plan's snippet monkeypatches the real connection's `.close` as an
    instance attribute (`conn.close = _close`), which raises
    `AttributeError: 'sqlite3.Connection' object attribute 'close' is
    read-only` - verified directly against this repo's actual sqlite3
    module (Python 3.13.15 in the fastapi container, same failure on the
    host's 3.12.3, so not a version quirk of one environment), not merely
    assumed from the plan text. This module's own tests/
    test_pipeline_health_cost.py already established the working
    alternative for tracking a real sqlite3 connection's close() calls: a
    thin wrapper delegating everything via __getattr__ instead of
    reassigning an attribute the C extension type won't allow."""
    import sqlite3
    tc_mod = _tc(tmp_path, monkeypatch)
    closed = []
    real_connect = sqlite3.connect

    class _CloseTrackingConnection:
        def __init__(self, inner):
            self._inner = inner

        def close(self):
            closed.append(True)
            self._inner.close()

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, *exc):
            return self._inner.__exit__(*exc)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def _tracking_connect(*args, **kwargs):
        return _CloseTrackingConnection(real_connect(*args, **kwargs))

    monkeypatch.setattr(tc_mod.sqlite3, "connect", _tracking_connect)

    with tc_mod._connect() as conn:
        conn.execute("SELECT 1")

    assert closed == [True]


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


# --- fee_override_for_ticker() - the ticker -> event fee-override join ----
# (issues #264/#258: services/kalshi_fees.py never read fee_type_override/
# fee_multiplier_override back out of this table before this - this is the
# single indexed lookup it now calls instead of the two full-table scans
# above.)


def test_fee_override_for_ticker_joins_market_to_its_event_override(tmp_path, monkeypatch):
    cache = _tc(tmp_path, monkeypatch)
    cache.save_market_titles({
        "KXNFLGAME-26AUG15MINNYG-MIN": {"title": "Vikings win", "yes_sub_title": None, "no_sub_title": None, "event_ticker": "KXNFLGAME-26AUG15MINNYG"},
    })
    cache.save_event_titles({
        "KXNFLGAME-26AUG15MINNYG": {"title": "Vikings vs Giants", "sub_title": None, "category": "Sports",
                                     "fee_type_override": "quadratic_with_combo_maker_fees", "fee_multiplier_override": 0.5},
    })
    assert cache.fee_override_for_ticker("KXNFLGAME-26AUG15MINNYG-MIN") == ("quadratic_with_combo_maker_fees", 0.5)


def test_fee_override_for_ticker_is_none_none_when_market_not_cached(tmp_path, monkeypatch):
    cache = _tc(tmp_path, monkeypatch)
    assert cache.fee_override_for_ticker("NOT-CACHED-TICKER") == (None, None)


def test_fee_override_for_ticker_is_none_none_when_event_carries_no_override(tmp_path, monkeypatch):
    # docs/kalshi/get-event-fee-changes.md: null in both columns means "the
    # override is cleared" - the overwhelming common case, every event that
    # has never had a scheduled fee change.
    cache = _tc(tmp_path, monkeypatch)
    cache.save_market_titles({"TICK-A": {"title": "T", "yes_sub_title": None, "no_sub_title": None, "event_ticker": "EVT-A"}})
    cache.save_event_titles({"EVT-A": {"title": "Event A", "sub_title": None, "category": None}})
    assert cache.fee_override_for_ticker("TICK-A") == (None, None)


def test_fee_override_for_ticker_is_none_none_when_market_cached_but_event_is_not(tmp_path, monkeypatch):
    # A market can be cached (title_cache saw its market row) before its
    # event's row has ever been fetched/saved - the join must not raise.
    cache = _tc(tmp_path, monkeypatch)
    cache.save_market_titles({"TICK-B": {"title": "T", "yes_sub_title": None, "no_sub_title": None, "event_ticker": "EVT-NEVER-SAVED"}})
    assert cache.fee_override_for_ticker("TICK-B") == (None, None)


# --- series_ticker_for() - the ticker -> event -> real series_ticker join --
# (kalshi-category-data-completeness Task 3: services/signal_log.py's
# series_of() used to derive a market's series by splitting the ticker
# string on the first hyphen - docs/kalshi/terms.md:29 says not to parse
# ticker strings at all and to use the documented series_ticker/event_ticker
# fields instead. This is the same single indexed join as
# fee_override_for_ticker() above, one hop further: market_titles.ticker ->
# event_ticker -> event_titles.series_ticker.)


def test_series_ticker_for_resolves_via_market_and_event_titles(tmp_path, monkeypatch):
    cache = _tc(tmp_path, monkeypatch)
    cache.save_market_titles({"MKT-A": {"title": "T", "yes_sub_title": "", "no_sub_title": "",
                                          "event_ticker": "EVT-A"}})
    cache.save_event_titles({"EVT-A": {"series_ticker": "REAL-SERIES"}})
    assert cache.series_ticker_for("MKT-A") == "REAL-SERIES"


def test_series_ticker_for_returns_none_when_market_uncached(tmp_path, monkeypatch):
    cache = _tc(tmp_path, monkeypatch)
    assert cache.series_ticker_for("UNSEEN-TICKER") is None


def test_series_ticker_for_returns_none_when_event_series_ticker_is_null(tmp_path, monkeypatch):
    cache = _tc(tmp_path, monkeypatch)
    cache.save_market_titles({"MKT-B": {"title": "T", "yes_sub_title": "", "no_sub_title": "",
                                          "event_ticker": "EVT-B"}})
    cache.save_event_titles({"EVT-B": {}})  # event cached, series_ticker never populated
    assert cache.series_ticker_for("MKT-B") is None


# --- series_ticker_for() in-memory index, zero DB access -------------------
# (kalshi-category-data-completeness Task 3, fix-round 1 2026-08-31 CRITICAL
# finding, then a SECOND fix round on the same finding via the final whole-
# branch review's independent adversarial pass: fix-round 1's DB-backed
# memoization still put a measured 548us round trip on the exchange-wide
# trade-tape hot path once per distinct off-watchlist ticker per 5-minute
# negative-TTL window (config/settings.yaml's trade_stream_exchange_wide:
# true means most tickers seen are genuinely off this app's watchlist-scoped
# title_cache and never resolve) - the exact per-trade-DB-round-trip shape
# behind the 2026-08-11 incident, just paced at 5-minute intervals instead
# of every trade. Replaced with a plain in-memory dict index
# (_MARKET_EVENT_INDEX/_EVENT_SERIES_INDEX), populated as a free side effect
# of load_market_titles()/load_event_titles()/save_market_titles()/
# save_event_titles() - functions this module already has to call anyway -
# so series_ticker_for() itself never touches the DB at all, not even once.)


def test_series_ticker_for_never_touches_the_db_not_even_once(tmp_path, monkeypatch):
    # The core claim of the second fix round: unlike fix-round 1's memoize-
    # after-first-hit design, series_ticker_for() must make ZERO _connect()
    # calls, for a resolved ticker, an unresolved one, or a repeat lookup -
    # there is nothing left to memoize because there was never a query.
    cache = _tc(tmp_path, monkeypatch)
    cache.save_market_titles({"MKT-NODB": {"title": "T", "yes_sub_title": "", "no_sub_title": "",
                                             "event_ticker": "EVT-NODB"}})
    cache.save_event_titles({"EVT-NODB": {"series_ticker": "REAL-SERIES"}})
    calls = []

    def _boom():
        calls.append(1)
        raise AssertionError("series_ticker_for() must never call _connect()")

    monkeypatch.setattr(cache, "_connect", _boom)
    assert cache.series_ticker_for("MKT-NODB") == "REAL-SERIES"
    assert cache.series_ticker_for("MKT-NODB") == "REAL-SERIES"
    assert cache.series_ticker_for("UNSEEN-NODB") is None
    assert cache.series_ticker_for("UNSEEN-NODB") is None
    assert calls == []


def test_series_ticker_for_index_survives_a_cold_start_via_load_functions(tmp_path, monkeypatch):
    # The cold-start property the old DB-backed cache also had: a ticker
    # resolved in a PRIOR process run (persisted to the DB, then this
    # "process" restarts and calls load_market_titles()/load_event_titles()
    # the way services/app_state.py does at startup, not save_*) must still
    # resolve - the index has to be seeded by load, not only by save.
    cache = _tc(tmp_path, monkeypatch)
    cache.save_market_titles({"MKT-COLD": {"title": "T", "yes_sub_title": "", "no_sub_title": "",
                                             "event_ticker": "EVT-COLD"}})
    cache.save_event_titles({"EVT-COLD": {"series_ticker": "COLD-SERIES"}})
    # Simulate a fresh process: wipe the in-memory index (a real restart
    # would start with empty dicts) but keep the same on-disk DB_PATH.
    monkeypatch.setattr(cache, "_MARKET_EVENT_INDEX", {})
    monkeypatch.setattr(cache, "_EVENT_SERIES_INDEX", {})
    assert cache.series_ticker_for("MKT-COLD") is None  # not yet reloaded

    cache.load_market_titles()
    cache.load_event_titles()
    assert cache.series_ticker_for("MKT-COLD") == "COLD-SERIES"


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
