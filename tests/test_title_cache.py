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


# --- series_ticker_for() in-process memoization ------------------------------
# (kalshi-category-data-completeness Task 3, PR review fix, 2026-08-31 CRITICAL
# finding: series_of() -> series_ticker_for() reintroduced the exact per-trade
# fresh-_connect()-plus-schema-check cost shape that froze the app for several
# minutes on 2026-08-11 (services/whalewatchers/kalshi_trade_tape.py's own
# incident note) - series_of() is now unconditionally on that exchange-wide hot
# path (min_contracts_for, trades_observed_by_series). Memoized in-process:
# a market's event_ticker and an event's series_ticker are immutable once the
# exchange assigns them, so a resolved (non-None) mapping is cached for the
# life of the process - free after the first real lookup. An unresolved (None)
# result is cached too, but only within _NEGATIVE_TTL_SEC (same idiom/window
# as kalshi_trade_tape.py's own _market_cache) - a market genuinely uncached
# today can become cached later (this app fetches title data continuously),
# so a permanent negative cache would silently freeze a ticker onto the wrong
# prefix-fallback answer forever, trading accuracy for speed exactly the way
# CLAUDE.md's data-plane HARD RULE forbids doing silently.)


def test_series_ticker_for_memoizes_a_resolved_ticker_after_the_first_db_hit(tmp_path, monkeypatch):
    cache = _tc(tmp_path, monkeypatch)
    cache.save_market_titles({"MKT-MEMO": {"title": "T", "yes_sub_title": "", "no_sub_title": "",
                                             "event_ticker": "EVT-MEMO"}})
    cache.save_event_titles({"EVT-MEMO": {"series_ticker": "REAL-SERIES"}})
    real_connect = cache._connect
    calls = []

    def _counting_connect():
        calls.append(1)
        return real_connect()

    monkeypatch.setattr(cache, "_connect", _counting_connect)
    assert cache.series_ticker_for("MKT-MEMO") == "REAL-SERIES"
    assert cache.series_ticker_for("MKT-MEMO") == "REAL-SERIES"
    assert cache.series_ticker_for("MKT-MEMO") == "REAL-SERIES"
    assert len(calls) == 1  # only the first call actually touched the DB


def test_series_ticker_for_memoizes_an_unresolved_ticker_within_the_negative_ttl(tmp_path, monkeypatch):
    cache = _tc(tmp_path, monkeypatch)
    real_connect = cache._connect
    calls = []

    def _counting_connect():
        calls.append(1)
        return real_connect()

    monkeypatch.setattr(cache, "_connect", _counting_connect)
    assert cache.series_ticker_for("UNSEEN-MEMO") is None
    assert cache.series_ticker_for("UNSEEN-MEMO") is None
    assert len(calls) == 1  # the negative result is cached too (bounded TTL)


def test_series_ticker_for_re_checks_a_negative_result_once_the_ttl_expires(tmp_path, monkeypatch):
    # A market genuinely uncached at first can become cached later (this app
    # fetches title data continuously) - the negative cache must not freeze a
    # ticker onto a wrong fallback answer forever once the real data arrives.
    cache = _tc(tmp_path, monkeypatch)
    fake_now = [1000.0]
    monkeypatch.setattr(cache.time, "monotonic", lambda: fake_now[0])
    assert cache.series_ticker_for("LATE-RESOLVED") is None
    cache.save_market_titles({"LATE-RESOLVED": {"title": "T", "yes_sub_title": "", "no_sub_title": "",
                                                   "event_ticker": "EVT-LATE"}})
    cache.save_event_titles({"EVT-LATE": {"series_ticker": "REAL-LATE-SERIES"}})
    fake_now[0] += cache._SERIES_TICKER_NEGATIVE_TTL_SEC + 1
    assert cache.series_ticker_for("LATE-RESOLVED") == "REAL-LATE-SERIES"


def test_series_ticker_for_degrades_to_none_on_a_db_error_rather_than_raising(tmp_path, monkeypatch):
    # 2026-08-31 review, Important finding: before this fix series_of() could
    # never raise (pure .split()); callers like strategy_engine's core entry
    # gate and the trade-tape prescan gate were never written expecting a DB
    # failure from this function, so a lock/disk/corruption condition must
    # degrade to the same fallback series_of() already uses for an uncached
    # ticker, never propagate.
    import sqlite3
    cache = _tc(tmp_path, monkeypatch)

    def _boom():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(cache, "_connect", _boom)
    assert cache.series_ticker_for("ANY-TICKER") is None


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
