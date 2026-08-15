"""
Tests services/main.py's real-trading confirmation gate: POST /api/config
must refuse to touch kalshi_account.trading_enabled at all, and POST
/api/trading/enable must require both a connected account and an exact-match
typed confirmation phrase.

main.py constructs PaperBroker/RiskManager/the config_store singleton at
*import time*, which would otherwise touch the real, live data/*.db files and
the real config/settings.yaml (the same file the running ddev app reads on
every poll tick) - see CLAUDE.md's "data/*.db files are live" section. Every
path is redirected to a throwaway temp directory *before* main is imported,
so nothing here can reach the real files no matter what a test does.
"""
import asyncio
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services import config_performance as cp_module
from services import config_store as config_store_module
from services import market_catalog as mc_module
from services import market_history as mh_module
from services import paper_broker as pb_module
from services import risk_manager as rm_module
from services import series_evaluator as se_module

_tmp_dir = Path(tempfile.mkdtemp(prefix="trading_gate_test_"))
pb_module.DB_PATH = _tmp_dir / "paper_broker.db"
rm_module.DB_PATH = _tmp_dir / "risk_state.db"
cp_module.DB_PATH = _tmp_dir / "config_performance.db"
mh_module.DB_PATH = _tmp_dir / "market_history.db"
mc_module.DB_PATH = _tmp_dir / "market_catalog.db"
se_module.DB_PATH = _tmp_dir / "series_evaluator.db"
# main.py derives market_broker/market_risk's db_path from broker.db_path/
# risk.db_path (both already redirected above) rather than a fresh path of
# their own - see main.py's own comment on this - so no separate redirect
# is needed for those two.

_tmp_config_path = _tmp_dir / "settings.yaml"
shutil.copy(config_store_module.CONFIG_PATH, _tmp_config_path)
config_store_module.config_store._path = _tmp_config_path
config_store_module.config_store.reload()

import main  # noqa: E402  (must import after the redirects above)
from fastapi.testclient import TestClient  # noqa: E402
from services import market_analyst_agent  # noqa: E402  (same module object main.py's own import binds - no pre-import DB redirect needed here, done per-test below instead)

# Bare (non-context-manager) TestClient does not trigger ASGI lifespan, so
# main.trading_loop() never starts - these tests only exercise the HTTP
# layer of the four endpoints below, not the background poll loop.
client = TestClient(main.app)


def _reset_trading_state():
    main.config_store.update({"kalshi_account": {"trading_enabled": False}})
    main.account.trading_enabled = False
    main.account._client = None


def test_files_are_actually_redirected_away_from_the_real_repo():
    """Guards the guard: if this ever fails, every other test in this file
    could be touching real project files instead of the temp copies."""
    import services.config_store as csm
    assert "sandbox/autotrade/config/settings.yaml" not in str(csm.config_store._path)
    assert "sandbox/autotrade/data" not in str(pb_module.DB_PATH)
    assert "sandbox/autotrade/data" not in str(rm_module.DB_PATH)


def test_config_endpoint_refuses_trading_enabled_patch():
    _reset_trading_state()
    resp = client.post("/api/config", json={"patch": {"kalshi_account": {"trading_enabled": True}}})
    assert resp.status_code == 400
    assert "trading_enabled" in resp.json()["detail"]
    assert main.config_store.get()["kalshi_account"]["trading_enabled"] is False


def test_config_endpoint_still_allows_other_patches():
    _reset_trading_state()
    resp = client.post("/api/config", json={"patch": {"strategy": {"entry_threshold": 0.7}}})
    assert resp.status_code == 200
    assert main.config_store.get()["strategy"]["entry_threshold"] == 0.7


# --- Change-history logging for manual edits (Item 3D, 2026-08-10) -----------
# config_performance.log_applied_change() used to only ever fire from the
# Advisory apply route - a plain Config-tab save went completely unlogged.

def test_config_endpoint_logs_a_manual_change_to_history():
    _reset_trading_state()
    resp = client.post("/api/config", json={"patch": {"strategy": {"entry_threshold": 0.42}}})
    assert resp.status_code == 200
    changes = client.get("/api/advisory/applied-changes").json()["changes"]
    match = next(c for c in changes if c["config_path"] == "strategy.entry_threshold" and c["new_value"] == 0.42)
    assert match["source"] == "manual"
    assert match["auto_applied"] is False


def test_config_endpoint_does_not_log_a_no_op_patch():
    _reset_trading_state()
    main.config_store.update({"strategy": {"entry_threshold": 0.33}})
    before_count = main.config_performance.applied_changes_count()
    resp = client.post("/api/config", json={"patch": {"strategy": {"entry_threshold": 0.33}}})
    assert resp.status_code == 200
    assert main.config_performance.applied_changes_count() == before_count


def test_config_endpoint_logs_every_changed_field_across_multiple_sections():
    _reset_trading_state()
    resp = client.post("/api/config", json={"patch": {
        "strategy": {"cooldown_sec": 999},
        "risk": {"max_daily_loss_pct": 0.11},
    }})
    assert resp.status_code == 200
    changes = client.get("/api/advisory/applied-changes").json()["changes"]
    paths = {c["config_path"] for c in changes}
    assert "strategy.cooldown_sec" in paths
    assert "risk.max_daily_loss_pct" in paths


def test_enable_trading_rejected_without_connected_account():
    _reset_trading_state()
    assert main.account.enabled is False  # no SDK client constructed
    resp = client.post("/api/trading/enable", json={"confirmation_phrase": "ENABLE REAL TRADING"})
    assert resp.status_code == 400
    assert "connected" in resp.json()["detail"].lower()
    assert main.config_store.get()["kalshi_account"]["trading_enabled"] is False


def test_enable_trading_rejected_with_wrong_phrase(monkeypatch):
    _reset_trading_state()
    monkeypatch.setattr(main.account, "_client", object())  # simulate a connected account
    assert main.account.enabled is True
    resp = client.post("/api/trading/enable", json={"confirmation_phrase": "yes please"})
    assert resp.status_code == 400
    assert "confirmation phrase" in resp.json()["detail"].lower()
    assert main.config_store.get()["kalshi_account"]["trading_enabled"] is False


def test_enable_trading_succeeds_with_correct_phrase_and_connected_account(monkeypatch):
    _reset_trading_state()
    monkeypatch.setattr(main.account, "_client", object())
    resp = client.post("/api/trading/enable", json={"confirmation_phrase": "ENABLE REAL TRADING"})
    assert resp.status_code == 200
    assert resp.json() == {"trading_enabled": True}
    assert main.config_store.get()["kalshi_account"]["trading_enabled"] is True
    assert main.account.trading_enabled is True


def test_disable_trading_always_allowed_no_confirmation_needed(monkeypatch):
    _reset_trading_state()
    monkeypatch.setattr(main.account, "_client", object())
    client.post("/api/trading/enable", json={"confirmation_phrase": "ENABLE REAL TRADING"})
    assert main.config_store.get()["kalshi_account"]["trading_enabled"] is True

    resp = client.post("/api/trading/disable")
    assert resp.status_code == 200
    assert resp.json() == {"trading_enabled": False}
    assert main.config_store.get()["kalshi_account"]["trading_enabled"] is False
    assert main.account.trading_enabled is False


def test_enable_and_disable_trading_both_log_to_change_history(monkeypatch):
    # kalshi_account.trading_enabled bypasses POST /api/config entirely (its
    # own confirmation-gated routes), but it's arguably the single most
    # safety-critical field in the app - it belongs in the same
    # change-history audit trail as everything else, not a blind spot.
    _reset_trading_state()
    monkeypatch.setattr(main.account, "_client", object())
    client.post("/api/trading/enable", json={"confirmation_phrase": "ENABLE REAL TRADING"})
    client.post("/api/trading/disable")
    changes = client.get("/api/advisory/applied-changes").json()["changes"]
    enabled = next(c for c in changes if c["config_path"] == "kalshi_account.trading_enabled" and c["new_value"] is True)
    disabled = next(c for c in changes if c["config_path"] == "kalshi_account.trading_enabled" and c["new_value"] is False)
    assert enabled["source"] == "manual"
    assert disabled["source"] == "manual"


def test_state_endpoint_reports_trading_enabled_flag():
    _reset_trading_state()
    resp = client.get("/api/state")
    assert resp.status_code == 200
    assert resp.json()["account"]["trading_enabled"] is False


def test_state_market_titles_includes_a_recently_closed_trades_ticker(monkeypatch):
    # Real, confirmed-live bug (2026-08-09, direct report: "i see only
    # ticker ids and such in the portfolio trade log, but when i click on
    # the history tab then back to the portfolio it shows titles").
    # _relevant_tickers() scoped /api/state's market_titles to the current
    # watchlist + open positions + signal/decision feeds, but never the
    # Trade Log's own tickers - a closed position's title dropped out the
    # instant it aged out of those other sets, even though the Trade Log
    # (broker.recent_trades) kept showing that trade. Visiting History
    # only ever "fixed" it as a side effect of that tab's own endpoint
    # separately backfilling the shared client-side title cache - the
    # actual gap was here, not there.
    main.broker.reset(starting_bankroll=10000.0)
    monkeypatch.setitem(main.state, "markets", [])  # ticker not in the current watchlist
    monkeypatch.setitem(main.state, "signal_feed", [])
    monkeypatch.setitem(main.state, "decision_feed", [])
    monkeypatch.setitem(main.state["market_titles"], "TICK-CLOSED", {"title": "A Real Title"})
    main.broker.open_position("TICK-CLOSED", "yes", size=10, price=0.5, reason="entry")
    main.broker.close_position("TICK-CLOSED", exit_price=0.6, reason="test close")
    main._bump_generation()

    resp = client.get("/api/state")
    assert resp.status_code == 200
    body = resp.json()
    assert "TICK-CLOSED" in body["market_titles"]
    assert body["market_titles"]["TICK-CLOSED"]["title"] == "A Real Title"


def test_state_market_titles_includes_real_account_position_and_fill_tickers(monkeypatch):
    # Same bug class as the trade_log fix above (2026-08-09 follow-up, direct
    # request: "make sure the REAL Kalshi stuff works just as well as the
    # paper default") - _relevant_tickers() scoped /api/state's market_titles
    # off the paper broker's own tickers, but the *real* connected Kalshi
    # account's positions/fills had no title-resolution path at all, not even
    # a lagging one: renderRealPositions/renderRealFills already call the
    # same marketLabel() the paper panels use, they just never had an entry
    # in market_titles to find.
    main.broker.reset(starting_bankroll=10000.0)
    monkeypatch.setitem(main.state, "markets", [])
    monkeypatch.setitem(main.state, "signal_feed", [])
    monkeypatch.setitem(main.state, "decision_feed", [])
    monkeypatch.setitem(main.state["market_titles"], "REAL-POS", {"title": "A Real Position Title"})
    monkeypatch.setitem(main.state["market_titles"], "REAL-FILL", {"title": "A Real Fill Title"})
    monkeypatch.setitem(main.state, "account", {
        "connected": True, "trading_enabled": False, "error": None,
        "positions": {"market_positions": [{"ticker": "REAL-POS", "position_fp": "10"}]},
        "fills": {"fills": [{"ticker": "REAL-FILL", "side": "yes", "count_fp": "5"}]},
    })
    main._bump_generation()

    resp = client.get("/api/state")
    assert resp.status_code == 200
    body = resp.json()
    assert body["market_titles"]["REAL-POS"]["title"] == "A Real Position Title"
    assert body["market_titles"]["REAL-FILL"]["title"] == "A Real Fill Title"


def test_relevant_tickers_falls_back_to_market_ticker_field_for_fills(monkeypatch):
    # _FILL_FIELDS carries both "ticker" and "market_ticker" (confirmed real
    # fields against a live account) - a fill missing "ticker" but carrying
    # "market_ticker" must still resolve, not silently drop out.
    monkeypatch.setitem(main.state, "markets", [])
    monkeypatch.setitem(main.state, "signal_feed", [])
    monkeypatch.setitem(main.state, "decision_feed", [])
    monkeypatch.setitem(main.state, "account", {
        "connected": True, "trading_enabled": False, "error": None,
        "positions": {"market_positions": []},
        "fills": {"fills": [{"ticker": None, "market_ticker": "REAL-FALLBACK", "side": "yes"}]},
    })
    assert "REAL-FALLBACK" in main._relevant_tickers()


def test_real_account_position_tickers_excludes_fills():
    # Real, confirmed-live regression (2026-08-09, direct report: "the
    # market watchlist doesn't appear to be updating/repopulating/removing
    # closed markets and those with insufficient volume") - an earlier fix
    # folded real-account *fills* into trading_loop's extra_tickers, which
    # feeds directly into state["markets"] (the live watchlist, wholesale-
    # replaced every tick). get_fills(limit=50) returns historical trade
    # records that can span days/weeks, so a long-since-finalized,
    # zero-volume market stayed pinned in the live watchlist for as long as
    # its fill sat in that window - unlike a position, which drops out the
    # tick it closes. _real_account_position_tickers must only ever surface
    # open positions, never fills, regardless of how stale/finalized the
    # fill's market now is.
    account = {
        "positions": {"market_positions": [{"ticker": "REAL-OPEN-POS"}]},
        "fills": {"fills": [{"ticker": "REAL-OLD-FILL", "side": "yes"}]},
    }
    result = main._real_account_position_tickers(account)
    assert result == {"REAL-OPEN-POS"}
    assert "REAL-OLD-FILL" not in result


# --- Catalog scan honesty (data-robustness audit, 2026-08-10) ---------------
# Real finding: mark_scanned() used to be called for the whole batch
# unconditionally, regardless of whether each series' get_markets() call
# actually succeeded (return_exceptions=True swallows the failure with no
# logging) - a persistently-failing series would mark itself "freshly
# scanned" every rotation forever, looking identical to a healthy-but-quiet
# one, while never actually writing a row.

class _FakeCatalogScanClient:
    def __init__(self, failing_tickers):
        self.failing_tickers = failing_tickers
        self.calls = []

    async def get_markets(self, limit, status, series_ticker):
        self.calls.append(series_ticker)
        if series_ticker in self.failing_tickers:
            raise RuntimeError("simulated transient API failure")
        return [{"ticker": f"{series_ticker}-M1", "occurrence_datetime": None}]


def test_scan_catalog_batch_only_marks_genuinely_succeeded_series_scanned():
    main.state["series_cache"] = {
        "fetched_at": time.time(),
        "series": [{"ticker": "SER-GOOD", "category": "Sports"}, {"ticker": "SER-BAD", "category": "Sports"}],
    }
    fake = _FakeCatalogScanClient(failing_tickers={"SER-BAD"})
    cfg = config_store_module.config_store.get()
    cfg["kalshi"]["live_markets_only"] = True

    asyncio.run(main._scan_catalog_batch(fake, cfg))

    scanned = {"SER-GOOD", "SER-BAD"}
    with mc_module._connect(mc_module.DB_PATH) as conn:
        rows = dict(conn.execute(
            "SELECT series_ticker, last_scanned_at FROM series_scan_state WHERE series_ticker IN (?, ?)",
            tuple(scanned),
        ).fetchall())
    assert "SER-GOOD" in rows  # succeeded - correctly marked scanned
    assert "SER-BAD" not in rows  # failed - must NOT be marked scanned, so it's retried next tick


# --- _get_top_series: per-category discovery (2026-08-15 direct fix) -------
# Real live report: "i see absolutely no signal or trade activity related to
# any markets other than sports or crypto... mentions... politics" - a flat
# global top-N by lifetime volume let Sports/Crypto's much larger lifetime
# volume crowd out every other category entirely.

def _prime_series_cache(entries):
    # entries: list of (ticker, category, volume_fp), already the shape
    # test_scan_catalog_batch_only_marks_genuinely_succeeded_series_scanned
    # above uses to prime the cache without a real network fetch.
    series = [{"ticker": t, "category": c, "volume_fp": v} for t, c, v in entries]
    series.sort(key=lambda s: s["volume_fp"], reverse=True)
    main.state["series_cache"] = {"fetched_at": time.time(), "series": series}


def test_get_top_series_returns_top_n_from_each_configured_category():
    _prime_series_cache([
        ("SPORT-1", "Sports", 100), ("SPORT-2", "Sports", 90), ("SPORT-3", "Sports", 80),
        ("MENTION-1", "Mentions", 10), ("MENTION-2", "Mentions", 5),
    ])
    result = asyncio.run(main._get_top_series(
        _FakePinnedMarketClient({}), categories=["Sports", "Mentions"], top_n_per_category=2,
    ))
    assert result == ["SPORT-1", "SPORT-2", "MENTION-1", "MENTION-2"]


def test_get_top_series_does_not_let_a_huge_category_starve_a_small_one():
    # The exact real-world shape: Sports has far more real entries than
    # Mentions, but each still gets its own top_n_per_category slots -
    # Sports having 50 real series must not reduce Mentions' allotment.
    sports = [(f"SPORT-{i}", "Sports", 1000 - i) for i in range(50)]
    _prime_series_cache(sports + [("MENTION-1", "Mentions", 1)])
    result = asyncio.run(main._get_top_series(
        _FakePinnedMarketClient({}), categories=["Sports", "Mentions"], top_n_per_category=12,
    ))
    assert result.count("MENTION-1") == 1
    assert sum(1 for t in result if t.startswith("SPORT-")) == 12


def test_get_top_series_category_with_fewer_series_than_n_returns_all_of_them():
    _prime_series_cache([("MENTION-1", "Mentions", 10), ("MENTION-2", "Mentions", 5)])
    result = asyncio.run(main._get_top_series(
        _FakePinnedMarketClient({}), categories=["Mentions"], top_n_per_category=12,
    ))
    assert result == ["MENTION-1", "MENTION-2"]  # no padding, no error


def test_get_top_series_category_absent_from_data_contributes_nothing():
    _prime_series_cache([("SPORT-1", "Sports", 100)])
    result = asyncio.run(main._get_top_series(
        _FakePinnedMarketClient({}), categories=["Sports", "Climate and Weather"], top_n_per_category=5,
    ))
    assert result == ["SPORT-1"]  # no crash on a configured category with zero real series


def test_get_top_series_no_categories_falls_back_to_flat_top_n():
    _prime_series_cache([(f"SER-{i}", "Sports", 100 - i) for i in range(10)])
    result = asyncio.run(main._get_top_series(_FakePinnedMarketClient({}), categories=None, top_n_per_category=2))
    assert result == [f"SER-{i}" for i in range(10)]  # 2*6=12 default fallback slots, only 10 exist


def test_get_top_series_output_order_follows_categories_list_order():
    _prime_series_cache([("MENTION-1", "Mentions", 999), ("SPORT-1", "Sports", 1)])
    # Mentions has the higher volume but Sports is listed first in categories -
    # output should group by category in the given order, not by raw volume.
    result = asyncio.run(main._get_top_series(
        _FakePinnedMarketClient({}), categories=["Sports", "Mentions"], top_n_per_category=5,
    ))
    assert result == ["SPORT-1", "MENTION-1"]


def test_reset_route_wires_market_catalog_and_market_history_flags():
    # Danger Zone gap (data-robustness audit, 2026-08-10): market_catalog
    # already had a clear_all() written for exactly this, just never called
    # from the reset route; market_history had no clear_all() at all - the
    # two largest data/*.db files on disk had no self-serve reset path.
    mc_module.upsert_markets("SER-A", "Sports", [{
        "ticker": "SER-A-M1", "event_ticker": "SER-A-EVT", "volume_24h_fp": "1000",
        "occurrence_datetime": _iso(datetime.now(timezone.utc) - timedelta(minutes=5)), "status": "open",
    }])
    mh_module.record_snapshots([{"ticker": "TICK-A", "yes_price": 0.5}])

    resp = client.post("/api/reset", json={
        "paper": False, "market_catalog": True, "market_history": True,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert "market_catalog" in body["cleared"]
    assert "market_history" in body["cleared"]
    assert mc_module.scan_progress()["total_markets"] == 0
    assert mh_module.snapshot_count() == 0


# --- Series evaluator (Item 1) -----------------------------------------------

def test_series_evaluator_status_route_returns_the_full_overview():
    se_module.clear_all()
    se_module.record_trade_observed("SERA", now=1000.0)
    se_module.record_trade_observed("SERB", now=1000.0)
    resp = client.get("/api/series-evaluator/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "enabled" in body
    assert {r["series"] for r in body["series"]} == {"SERA", "SERB"}


def test_series_evaluator_reset_route_resets_an_existing_series():
    se_module.clear_all()
    se_module.record_trade_observed("SERA", now=1000.0)
    with se_module._connect() as conn:
        conn.execute("UPDATE series_status SET status = 'rejected', strike_count = 3 WHERE series = 'SERA'")
    resp = client.post("/api/series-evaluator/reset", json={"series": "SERA"})
    assert resp.status_code == 200
    row = [r for r in se_module.overview() if r["series"] == "SERA"][0]
    assert row["status"] == "observing"
    assert row["strike_count"] == 0


def test_series_evaluator_reset_route_404s_for_an_unknown_series():
    se_module.clear_all()
    resp = client.post("/api/series-evaluator/reset", json={"series": "NEVER-SEEN"})
    assert resp.status_code == 404


def test_reset_route_wires_series_evaluator_flag():
    se_module.clear_all()
    se_module.record_trade_observed("SERA", now=1000.0)
    resp = client.post("/api/reset", json={"paper": False, "series_evaluator": True})
    assert resp.status_code == 200
    assert "series_evaluator" in resp.json()["cleared"]
    assert se_module.overview() == []


class _FakePinnedMarketClient:
    def __init__(self, markets_by_ticker):
        self.markets_by_ticker = markets_by_ticker

    async def get_market(self, ticker):
        return self.markets_by_ticker[ticker]

    async def get_series_list(self, category=None):
        # A pinned watchlist no longer replaces discovery outright (2026-08-15
        # merge fix) - discovery always runs too, so this fake needs to answer
        # for it. Empty on purpose: these tests are specifically about pinned/
        # extra_tickers behavior, not discovery, so discovery should
        # contribute zero additional markets.
        return []

    async def get_candidate_markets(self, min_volume, series_tickers):
        return []


class _FakeTradeTapeClient:
    """pages_by_ticker: ticker -> list of pages, each page a
    (trades, cursor) tuple in the order they'd be returned across
    successive calls. An empty-string cursor signals no more pages,
    matching Kalshi's own documented contract."""
    def __init__(self, pages_by_ticker):
        self.pages_by_ticker = pages_by_ticker
        self.calls = []

    async def get_trades(self, ticker=None, limit=25, min_ts=None, cursor=None):
        self.calls.append({"ticker": ticker, "limit": limit, "min_ts": min_ts, "cursor": cursor})
        pages = self.pages_by_ticker.get(ticker, [])
        page_index = 0 if cursor is None else next(
            i for i, (_, c) in enumerate(pages[:-1]) if c == cursor
        ) + 1
        trades, next_cursor = pages[page_index]
        return {"trades": trades, "cursor": next_cursor}


def _trade(trade_id, ticker, created_time):
    return {"trade_id": trade_id, "ticker": ticker, "created_time": created_time}


def test_fetch_trades_for_ticker_pages_until_cursor_is_empty():
    # Direct instruction (2026-08-11): "i want trade tape to be unlimited,
    # never capped, for whale-watch-worthy markets." A ticker with more
    # trades than one page holds must still return every one of them, not
    # just the first page. Pagination only applies once min_ts is set
    # (i.e. every tick after the first) - see the next test for why
    # min_ts=None deliberately does NOT paginate (a real incident this
    # exact function caused when it did).
    fake = _FakeTradeTapeClient({
        "T-A": [
            ([_trade("1", "T-A", "t1"), _trade("2", "T-A", "t2")], "cursor-1"),
            ([_trade("3", "T-A", "t3")], ""),
        ],
    })
    trades = asyncio.run(main._fetch_trades_for_ticker(fake, "T-A", min_ts=1000))
    assert {t["trade_id"] for t in trades} == {"1", "2", "3"}
    assert len(fake.calls) == 2


def test_fetch_trades_for_ticker_does_not_paginate_on_cold_start():
    # Real, confirmed-live incident (2026-08-11): with min_ts=None (no
    # watermark yet), Kalshi has no natural stopping point short of a
    # ticker's entire history - pagination would fire up to
    # _TRADE_TAPE_MAX_PAGES_PER_TICKER pages per ticker, on every watched
    # ticker, simultaneously, on every cold start. This must return the
    # first page only and stop, not walk the cursor.
    fake = _FakeTradeTapeClient({
        "T-A": [
            ([_trade("1", "T-A", "t1")], "cursor-1"),
            ([_trade("2", "T-A", "t2")], ""),
        ],
    })
    trades = asyncio.run(main._fetch_trades_for_ticker(fake, "T-A", min_ts=None))
    assert {t["trade_id"] for t in trades} == {"1"}
    assert len(fake.calls) == 1


def test_fetch_trades_for_ticker_stops_on_first_empty_page():
    fake = _FakeTradeTapeClient({"T-A": [([], "")]})
    trades = asyncio.run(main._fetch_trades_for_ticker(fake, "T-A", min_ts=None))
    assert trades == []
    assert len(fake.calls) == 1


def test_fetch_trade_tape_returns_more_than_the_old_flat_cap():
    # Direct instruction: genuinely unbounded, not just a bigger number.
    # Simulates a single ticker alone producing more trades than the old
    # 100-item platform-wide cap ever allowed through to whale detection -
    # since_ts set so this exercises the paginated (post-cold-start) path.
    many_trades = [_trade(str(i), "T-A", f"t{i:04d}") for i in range(150)]
    fake = _FakeTradeTapeClient({
        "T-A": [
            (many_trades[:100], "cursor-1"),
            (many_trades[100:], ""),
        ],
    })
    markets = [{"ticker": "T-A"}]
    trades = asyncio.run(main._fetch_trade_tape(fake, markets, since_ts=1000.0))
    assert len(trades) == 150


def test_fetch_trade_tape_passes_min_ts_with_overlap_margin_when_since_ts_given():
    fake = _FakeTradeTapeClient({"T-A": [([], "")]})
    markets = [{"ticker": "T-A"}]
    asyncio.run(main._fetch_trade_tape(fake, markets, since_ts=1000.0))
    assert fake.calls[0]["min_ts"] == 990  # 1000 - 10s overlap margin


def test_fetch_trade_tape_uses_no_min_ts_on_first_call():
    fake = _FakeTradeTapeClient({"T-A": [([], "")]})
    markets = [{"ticker": "T-A"}]
    asyncio.run(main._fetch_trade_tape(fake, markets, since_ts=None))
    assert fake.calls[0]["min_ts"] is None


def test_fetch_markets_regroups_extra_ticker_into_its_series_existing_run():
    # Direct report (2026-08-11): "watchlist groupings is broken... likely a
    # result of the active removal of watchlist items. reorganization should
    # occur at the same time the watchlist updates." Confirmed root cause:
    # extra_tickers (open positions kept alive after rotating off the main
    # selection) used to be appended at the very end regardless of series,
    # which the frontend's renderMarketCards assumes never happens (it
    # groups by treating same-series markets as always consecutive). Here,
    # SERA appears once in the pinned watchlist and again only via
    # extra_tickers (simulating a position whose series otherwise dropped
    # off) - without the fix, the second SERA ticker would land after SERB,
    # splitting one series into two non-adjacent runs.
    # watchlist_size/max_children_per_parent needed now that discovery always
    # runs alongside a pinned watchlist (2026-08-15 merge fix), even though
    # the fake client's discovery methods return nothing for this test.
    cfg = {"kalshi": {"markets_watchlist": ["SERA-M1", "SERB-M1"], "watchlist_size": 50, "max_children_per_parent": None}}
    fake = _FakePinnedMarketClient({
        "SERA-M1": {"ticker": "SERA-M1", "event_ticker": "SERA-EVT1"},
        "SERB-M1": {"ticker": "SERB-M1", "event_ticker": "SERB-EVT1"},
        "SERA-M2": {"ticker": "SERA-M2", "event_ticker": "SERA-EVT2"},
    })
    markets = asyncio.run(main._fetch_markets(fake, cfg, extra_tickers=["SERA-M2"]))
    tickers = [m["ticker"] for m in markets]
    assert tickers == ["SERA-M1", "SERA-M2", "SERB-M1"]


def test_fetch_markets_live_only_excludes_ineligible_series_when_enabled():
    # The BEFORE-check (direct request): a series currently serving backoff
    # must not be re-admitted to the watchlist, regardless of whether it
    # would otherwise qualify on volume/live-status.
    main.state["live_status_cache"].clear()
    mc_module.clear_all()
    se_module.clear_all()
    now_ts = datetime.now(timezone.utc)
    mc_module.upsert_markets("SERGOOD", "Sports", [{
        "ticker": "SERGOOD-M1", "event_ticker": "SERGOOD-EVT1", "volume_24h_fp": "1000",
        "occurrence_datetime": _iso(now_ts + timedelta(minutes=-5)), "status": "open",
    }])
    mc_module.upsert_markets("SERBAD", "Sports", [{
        "ticker": "SERBAD-M1", "event_ticker": "SERBAD-EVT1", "volume_24h_fp": "1000",
        "occurrence_datetime": _iso(now_ts + timedelta(minutes=-5)), "status": "open",
    }])
    se_module.record_trade_observed("SERBAD", now=1000.0)
    with se_module._connect() as conn:
        conn.execute(
            "UPDATE series_status SET status = 'rejected', next_eligible_at = ? WHERE series = 'SERBAD'",
            (time.time() + 99999,),
        )
    cfg = _cfg_live_only()
    cfg["series_evaluator"] = {"enabled": True}
    fake = _FakeHydrationClient(
        hydrated_markets={
            "SERGOOD-M1": {"ticker": "SERGOOD-M1", "event_ticker": "SERGOOD-EVT1", "yes_bid_dollars": "0.5"},
        },
        widget_status="live",
    )
    markets = asyncio.run(main._fetch_markets(fake, cfg))
    tickers = {m["ticker"] for m in markets}
    assert "SERGOOD-M1" in tickers
    assert "SERBAD-M1" not in tickers


def test_fetch_markets_live_only_includes_series_when_evaluator_disabled():
    # series_evaluator.enabled defaults False - a rejected series must not
    # be filtered when the feature hasn't been opted into.
    main.state["live_status_cache"].clear()
    mc_module.clear_all()
    se_module.clear_all()
    now_ts = datetime.now(timezone.utc)
    mc_module.upsert_markets("SERBAD", "Sports", [{
        "ticker": "SERBAD-M1", "event_ticker": "SERBAD-EVT1", "volume_24h_fp": "1000",
        "occurrence_datetime": _iso(now_ts + timedelta(minutes=-5)), "status": "open",
    }])
    se_module.record_trade_observed("SERBAD", now=1000.0)
    with se_module._connect() as conn:
        conn.execute(
            "UPDATE series_status SET status = 'rejected', next_eligible_at = ? WHERE series = 'SERBAD'",
            (time.time() + 99999,),
        )
    cfg = _cfg_live_only()  # no series_evaluator key at all, matching a not-yet-upgraded config
    fake = _FakeHydrationClient(
        hydrated_markets={
            "SERBAD-M1": {"ticker": "SERBAD-M1", "event_ticker": "SERBAD-EVT1", "yes_bid_dollars": "0.5"},
        },
        widget_status="live",
    )
    markets = asyncio.run(main._fetch_markets(fake, cfg))
    assert "SERBAD-M1" in {m["ticker"] for m in markets}


# --- Advisory engine (docs/advisory-engine-plan.md) --------------------------
# Same reasoning as the real-trading gate above: advisory.auto_apply_enabled
# is the one advisory-config field that can make config changes happen with
# no human click in the loop, so it gets the same "/api/config can't touch
# it" treatment as kalshi_account.trading_enabled - see main.py's
# update_config(). The actual POST /api/advisory/auto-apply/enable endpoint
# isn't built yet (docs/advisory-engine-plan.md §3/§8: ships as a distinct
# follow-up after the manual-apply path has run for a while), so there's
# nothing else to test for that path yet beyond the guard itself.

def _reset_advisory_state():
    main.broker.reset(starting_bankroll=10000.0)
    # auto_apply_enabled reset explicitly, not just enabled/min_resolved -
    # this test file's config starting point is a *copy of the real, live*
    # config/settings.yaml (see the module-level shutil.copy above), and
    # this field is meant to reflect live operator intent (toggled via the
    # History tab's confirmation-gated button, not this file's own tests) -
    # a real live session enabling it left it True in the copied baseline,
    # breaking every test here that assumed a False default. advisory.
    # auto_apply_enabled is one of the two fields POST /api/config always
    # refuses to touch (see _PROTECTED_CONFIG_PATHS), so it must be reset
    # here directly rather than through the route these tests exercise.
    main.config_store.update({
        "advisory": {"enabled": False, "min_resolved_trades_per_variant": 30, "auto_apply_enabled": False},
    })


def test_config_endpoint_refuses_auto_apply_enabled_patch():
    _reset_advisory_state()
    resp = client.post("/api/config", json={"patch": {"advisory": {"auto_apply_enabled": True}}})
    assert resp.status_code == 400
    assert "auto_apply_enabled" in resp.json()["detail"]
    assert main.config_store.get()["advisory"]["auto_apply_enabled"] is False


def test_config_endpoint_still_allows_other_advisory_patches():
    _reset_advisory_state()
    resp = client.post("/api/config", json={"patch": {"advisory": {"enabled": True}}})
    assert resp.status_code == 200
    assert main.config_store.get()["advisory"]["enabled"] is True


def test_advisory_status_reports_disabled_by_default():
    _reset_advisory_state()
    resp = client.get("/api/advisory/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["auto_apply_enabled"] is False
    assert "current_fingerprint" in body


def test_advisory_recommendations_empty_and_gated_when_disabled():
    _reset_advisory_state()
    resp = client.get("/api/advisory/recommendations")
    assert resp.status_code == 200
    body = resp.json()
    assert body["recommendations"] == []
    assert "disabled" in body["gated_reason"]


def test_advisory_apply_rejected_when_disabled():
    _reset_advisory_state()
    resp = client.post("/api/advisory/recommendations/apply", json={"id": "whatever"})
    assert resp.status_code == 400
    assert "disabled" in resp.json()["detail"]


def test_advisory_apply_404_for_unknown_id_when_enabled():
    _reset_advisory_state()
    main.config_store.update({"advisory": {"enabled": True}})
    resp = client.post("/api/advisory/recommendations/apply", json={"id": "does-not-exist"})
    assert resp.status_code == 404


def test_advisory_recommendations_reports_resolved_count_with_no_trade_history():
    # No blanket gate anymore (2026-08-10 unified-engine merge) - an empty
    # recommendations list here just means there's no trade history to
    # suggest anything from, not that a per-variant floor is blocking it.
    _reset_advisory_state()
    main.config_store.update({"advisory": {"enabled": True, "min_resolved_trades_per_variant": 30}})
    resp = client.get("/api/advisory/recommendations")
    assert resp.status_code == 200
    body = resp.json()
    assert body["recommendations"] == []
    assert body["resolved_count"] == 0
    assert body["min_resolved_trades_per_variant"] == 30


def test_advisory_apply_end_to_end_updates_config_and_logs_change():
    # Seeds the real broker.trade_log directly (bypassing strategy_engine's
    # gating, which isn't what this test is about) with enough resolved
    # trades under the *current* config fingerprint to trigger a real
    # entry_threshold recommendation, then exercises the full HTTP round
    # trip: fetch it, apply it, confirm config_store and the audit trail
    # both reflect the change.
    _reset_advisory_state()
    main.config_store.update({
        "advisory": {"enabled": True, "min_resolved_trades_per_variant": 5},
        "strategy": {"entry_threshold": 0.5},
    })
    cfg = main.config_store.get()
    fp = main.config_performance.fingerprint(cfg)
    main.config_performance.record_variant(fp, cfg)

    for _ in range(4):
        main.broker.open_position(
            "TICK-LOW", "yes", size=10, price=0.5,
            reason="whale print 5000 @ 0.5 (conf 0.30)", config_fingerprint=fp,
        )
        main.broker.close_position(
            "TICK-LOW", exit_price=0.4,
            reason="stop-loss hit: unrealized loss 20% of cost basis (limit 20%)",
        )
    for _ in range(4):
        main.broker.open_position(
            "TICK-HIGH", "yes", size=10, price=0.5,
            reason="whale print 5000 @ 0.5 (conf 0.90)", config_fingerprint=fp,
        )
        main.broker.close_position("TICK-HIGH", exit_price=1.0, reason="market settled YES - position won")

    resp = client.get("/api/advisory/recommendations")
    assert resp.status_code == 200
    recs = resp.json()["recommendations"]
    entry_rec = next(r for r in recs if r["config_path"] == "strategy.entry_threshold")
    assert entry_rec["current_value"] == 0.5

    apply_resp = client.post("/api/advisory/recommendations/apply", json={"id": entry_rec["id"]})
    assert apply_resp.status_code == 200
    assert main.config_store.get()["strategy"]["entry_threshold"] == entry_rec["suggested_value"]

    changes = client.get("/api/advisory/applied-changes").json()["changes"]
    assert changes[0]["config_path"] == "strategy.entry_threshold"
    assert changes[0]["new_value"] == entry_rec["suggested_value"]
    assert changes[0]["auto_applied"] is False
    # No trades have been placed under the brand-new post-apply fingerprint
    # yet - effect tracking correctly reports "nothing to compare" rather
    # than fabricating a delta from zero trades.
    assert changes[0]["effect"] is None


def test_applied_changes_effect_reports_a_real_before_after_win_rate_delta():
    # Item 3D (2026-08-10) - once real trades exist under BOTH the old and
    # new fingerprint, the change-history view should show a genuine
    # measured win-rate/P&L delta, via the same variant_summaries()
    # cross-variant comparisons already use - not a fabricated number.
    _reset_advisory_state()
    main.config_store.update({"strategy": {"entry_threshold": 0.5}})
    fp_before = main.config_performance.fingerprint(main.config_store.get())
    for _ in range(3):
        main.broker.open_position(
            "OLD-TICK", "yes", size=10, price=0.5, reason="whale print",
            config_fingerprint=fp_before,
        )
        main.broker.close_position("OLD-TICK", exit_price=0.4, reason="stop-loss hit: unrealized loss 20% of cost basis (limit 20%)")

    resp = client.post("/api/config", json={"patch": {"strategy": {"entry_threshold": 0.7}}})
    assert resp.status_code == 200
    fp_after = main.config_performance.fingerprint(main.config_store.get())

    for _ in range(3):
        main.broker.open_position(
            "NEW-TICK", "yes", size=10, price=0.5, reason="whale print",
            config_fingerprint=fp_after,
        )
        main.broker.close_position("NEW-TICK", exit_price=1.0, reason="market settled YES - position won")

    changes = client.get("/api/advisory/applied-changes").json()["changes"]
    match = next(c for c in changes if c["config_path"] == "strategy.entry_threshold" and c["new_value"] == 0.7)
    assert match["effect"] is not None
    assert match["effect"]["before_win_rate_pct"] == 0.0
    assert match["effect"]["before_n"] == 3
    assert match["effect"]["after_win_rate_pct"] == 100.0
    assert match["effect"]["after_n"] == 3


def test_applied_changes_effect_is_none_for_non_strategy_changes():
    # market_strategy.*/risk.*/etc. changes never alter the fingerprinted
    # strategy.* subset, so fingerprint_before always equals
    # fingerprint_after for them - correctly no effect to report, not a
    # bug in the logging.
    _reset_advisory_state()
    resp = client.post("/api/config", json={"patch": {"risk": {"max_daily_loss_pct": 0.33}}})
    assert resp.status_code == 200
    changes = client.get("/api/advisory/applied-changes").json()["changes"]
    match = next(c for c in changes if c["config_path"] == "risk.max_daily_loss_pct" and c["new_value"] == 0.33)
    assert match["effect"] is None


def test_advisory_apply_end_to_end_handles_a_market_strategy_recommendation():
    # 2026-08-10 unified-engine merge: recommendations can now carry a
    # market_strategy.* config_path (min_momentum_delta), not just
    # strategy.* - this exercises the apply route's generalized
    # `section, _, field = config_path.partition(".")` against a real,
    # non-"strategy" section end to end, seeded via market_broker (the
    # separate MarketNativeStrategy broker momentum_reversal closes
    # actually come from), not the whale-follow broker.
    _reset_advisory_state()
    main.config_store.update({
        "advisory": {"enabled": True, "min_resolved_trades_per_variant": 5},
        "market_strategy": {"min_momentum_delta": 0.03},
    })
    main.market_broker.reset(starting_bankroll=10000.0)
    for i in range(3):
        main.market_broker.open_position(
            f"MTICK-{i}", "yes", size=10, price=0.5, reason="momentum entry",
        )
        main.market_broker.close_position(
            f"MTICK-{i}", exit_price=0.4,
            reason="momentum reversed: price moved 4% against this yes position over 30m",
        )

    resp = client.get("/api/advisory/recommendations")
    assert resp.status_code == 200
    recs = resp.json()["recommendations"]
    mom_rec = next(r for r in recs if r["config_path"] == "market_strategy.min_momentum_delta")
    assert mom_rec["current_value"] == 0.03
    assert mom_rec["suggested_value"] == 0.04

    apply_resp = client.post("/api/advisory/recommendations/apply", json={"id": mom_rec["id"]})
    assert apply_resp.status_code == 200
    assert main.config_store.get()["market_strategy"]["min_momentum_delta"] == 0.04

    changes = client.get("/api/advisory/applied-changes").json()["changes"]
    assert changes[0]["config_path"] == "market_strategy.min_momentum_delta"
    assert changes[0]["new_value"] == 0.04


# --- MarketNativeStrategy / market_history debug endpoints -------------------
# Backend-only for now (docs/advisory-engine-plan.md §9-adjacent, direct
# request 2026-08-08) - no UI panel yet, but real endpoints, so smoke-test
# them the same as everything else rather than leaving them unverified.

def test_market_strategy_state_endpoint_reports_disabled_by_default():
    # Explicitly set, not assumed from whatever config/settings.yaml
    # contained at the moment this test module happened to import (the
    # one-time copy above) - the real file is live-tunable and a prior
    # real session enabling market_strategy for actual use shouldn't make
    # this test flaky. main.config_store is already redirected to the temp
    # copy at this point, so this can't touch the real file.
    main.config_store.update({"market_strategy": {"enabled": False}})
    main.market_broker.reset(starting_bankroll=10000.0)
    resp = client.get("/api/market-strategy/state")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["broker"]["bankroll"] == 10000.0
    assert body["broker"]["positions"] == []
    assert body["summary"]["total_closed"] == 0


def test_market_strategy_state_endpoint_uses_its_own_broker_not_the_whale_ones():
    main.broker.reset(starting_bankroll=7777.0)
    main.market_broker.reset(starting_bankroll=8888.0)
    main._bump_generation()  # direct .reset() calls above bypass the route that normally does this - see get_state()'s ETag cache
    resp = client.get("/api/market-strategy/state")
    assert resp.json()["broker"]["bankroll"] == 8888.0
    # And the whale-follow broker's own state endpoint is unaffected.
    state_resp = client.get("/api/state")
    assert state_resp.json()["broker"]["bankroll"] == 7777.0


def test_market_history_summary_endpoint_reports_counts():
    resp = client.get("/api/market-history/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert "tracked_tickers" in body
    assert "total_snapshots" in body
    assert "resolved_outcomes" in body


def test_market_history_hypothetical_trades_endpoint_returns_list():
    resp = client.get("/api/market-history/hypothetical-trades")
    assert resp.status_code == 200
    assert isinstance(resp.json()["trades"], list)


# --- _fetch_live_status: schedule-aware light polling ----------------------
# Direct request: once markets/whale data have populated the system, no need
# to re-derive live status from 2 fresh API calls every single tick - use
# the market's own schedule (occurrence_datetime/close_time) plus a cached
# previous status, re-polling only lightly. Also the window this discovered
# was a real bug in disguise as a comment: bounds were inverted from what
# was documented (excluded anything that had been running >1h, included
# anything starting hours in the future) - fixed alongside the caching work.

def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _market_at(offset_sec: float, event_ticker="EVT-A", close_offset_sec: float | None = None) -> dict:
    now = datetime.now(timezone.utc)
    m = {
        "ticker": f"{event_ticker}-T", "event_ticker": event_ticker,
        "occurrence_datetime": _iso(now + timedelta(seconds=offset_sec)),
    }
    if close_offset_sec is not None:
        m["close_time"] = _iso(now + timedelta(seconds=close_offset_sec))
    return m


class _FakeLiveClient:
    """Records every call it receives - tests assert against call counts to
    prove a poll was (or, more often, was NOT) actually made, not just that
    the returned status looks right."""

    def __init__(self, widget_status="live", has_milestone=True, live_data_fails=False):
        self.widget_status = widget_status
        self.has_milestone = has_milestone
        self.live_data_fails = live_data_fails
        self.milestone_calls = []
        self.live_data_calls = []

    async def get_milestones_for_event(self, event_ticker):
        self.milestone_calls.append(event_ticker)
        return [{"id": "ms1", "type": "game"}] if self.has_milestone else []

    async def get_live_data(self, ms_type, ms_id):
        self.live_data_calls.append((ms_type, ms_id))
        if self.live_data_fails:
            return {"live_data": {"details": {}}}  # no widget_status - a real, seen shape
        return {"live_data": {"details": {"widget_status": self.widget_status}}}


def test_fetch_live_status_polls_a_new_event_with_no_cache():
    main.state["live_status_cache"].clear()
    fake = _FakeLiveClient(widget_status="live")
    markets = [_market_at(offset_sec=-300)]  # started 5 min ago
    result = asyncio.run(main._fetch_live_status(fake, markets))
    assert result == {"EVT-A": "live"}
    assert fake.milestone_calls == ["EVT-A"]
    assert main.state["live_status_cache"]["EVT-A"]["status"] == "live"


def test_fetch_live_status_excludes_events_starting_far_in_the_future():
    main.state["live_status_cache"].clear()
    fake = _FakeLiveClient()
    markets = [_market_at(offset_sec=3 * 3600)]  # starts in 3h - past the 1h lookahead
    result = asyncio.run(main._fetch_live_status(fake, markets))
    assert result == {}
    assert fake.milestone_calls == []  # never even attempted a poll


def test_fetch_live_status_regression_includes_event_that_started_three_hours_ago():
    # The bug: old bounds excluded anything that had been running >1h. An
    # event 3h into its run must still be tracked (within the 6h lookback).
    main.state["live_status_cache"].clear()
    fake = _FakeLiveClient(widget_status="live")
    markets = [_market_at(offset_sec=-3 * 3600)]
    result = asyncio.run(main._fetch_live_status(fake, markets))
    assert result == {"EVT-A": "live"}
    assert fake.milestone_calls == ["EVT-A"]


def test_fetch_live_status_excludes_events_that_started_more_than_six_hours_ago():
    main.state["live_status_cache"].clear()
    fake = _FakeLiveClient()
    markets = [_market_at(offset_sec=-7 * 3600)]
    result = asyncio.run(main._fetch_live_status(fake, markets))
    assert result == {}
    assert fake.milestone_calls == []


def test_fetch_live_status_reuses_a_recent_cached_value_without_polling():
    main.state["live_status_cache"].clear()
    main.state["live_status_cache"]["EVT-A"] = {"status": "live", "checked_at": time.time()}
    fake = _FakeLiveClient(widget_status="live")
    markets = [_market_at(offset_sec=-300)]
    result = asyncio.run(main._fetch_live_status(fake, markets))
    assert result == {"EVT-A": "live"}
    assert fake.milestone_calls == []  # trusted the cache, never polled


def test_fetch_live_status_repolls_once_the_cache_entry_is_stale():
    main.state["live_status_cache"].clear()
    stale_check = time.time() - main._LIVE_STATUS_REPOLL_SEC - 1
    main.state["live_status_cache"]["EVT-A"] = {"status": "live", "checked_at": stale_check}
    fake = _FakeLiveClient(widget_status="live")
    markets = [_market_at(offset_sec=-300)]
    result = asyncio.run(main._fetch_live_status(fake, markets))
    assert result == {"EVT-A": "live"}
    assert fake.milestone_calls == ["EVT-A"]  # due for a light re-poll


def test_fetch_live_status_treats_past_close_time_as_finished_without_polling():
    main.state["live_status_cache"].clear()
    fake = _FakeLiveClient()
    markets = [_market_at(offset_sec=-3600, close_offset_sec=-60)]  # closed a minute ago
    result = asyncio.run(main._fetch_live_status(fake, markets))
    assert result == {"EVT-A": "finished"}
    assert fake.milestone_calls == []  # schedule alone answered this
    assert main.state["live_status_cache"]["EVT-A"]["status"] == "finished"


def test_fetch_live_status_never_repolls_a_terminal_status():
    main.state["live_status_cache"].clear()
    # Old checked_at, well past the repoll window - would normally trigger a
    # re-poll, except "finished" is terminal.
    old_check = time.time() - main._LIVE_STATUS_REPOLL_SEC * 10
    main.state["live_status_cache"]["EVT-A"] = {"status": "finished", "checked_at": old_check}
    fake = _FakeLiveClient(widget_status="live")  # would flip back to "live" if actually polled
    markets = [_market_at(offset_sec=-300)]
    result = asyncio.run(main._fetch_live_status(fake, markets))
    assert result == {"EVT-A": "finished"}
    assert fake.milestone_calls == []


def test_fetch_live_status_schedule_fallback_only_for_milestone_tracked_events():
    # Direct correction: "just because a market is open doesn't mean it's
    # live like sports or mentions or award shows - be careful about how
    # you infer when you can't find live status." No milestone at all
    # (Kalshi never confirmed this is a discrete, clocked live event) means
    # no basis to guess - must not appear in the result at all, not "none",
    # not "live".
    main.state["live_status_cache"].clear()
    fake = _FakeLiveClient(has_milestone=False)
    markets = [_market_at(offset_sec=-300)]  # started 5 min ago
    result = asyncio.run(main._fetch_live_status(fake, markets))
    assert result == {}
    assert "EVT-A" not in main.state["live_status_cache"]


def test_fetch_live_status_schedule_fallback_applies_when_milestone_exists_but_live_data_empty():
    # The narrower, safer case the fallback IS meant for: Kalshi confirmed
    # this is milestone-tracked (a real game), the live-data call just
    # didn't return a usable widget_status this tick - infer live from the
    # schedule since we already know this is the right kind of market.
    main.state["live_status_cache"].clear()
    fake = _FakeLiveClient(has_milestone=True, live_data_fails=True)
    markets = [_market_at(offset_sec=-300)]  # started 5 min ago -> past occurrence
    result = asyncio.run(main._fetch_live_status(fake, markets))
    assert result == {"EVT-A": "live"}
    assert main.state["live_status_cache"]["EVT-A"]["source"] == "schedule"


def test_fetch_live_status_schedule_fallback_says_none_before_scheduled_start():
    main.state["live_status_cache"].clear()
    fake = _FakeLiveClient(has_milestone=True, live_data_fails=True)
    markets = [_market_at(offset_sec=1800)]  # starts in 30 min - within the 1h lookahead, not started yet
    result = asyncio.run(main._fetch_live_status(fake, markets))
    assert result == {"EVT-A": "none"}


def test_fetch_live_status_confirmed_milestone_status_wins_over_fallback():
    main.state["live_status_cache"].clear()
    fake = _FakeLiveClient(widget_status="live", has_milestone=True, live_data_fails=False)
    markets = [_market_at(offset_sec=-300)]
    result = asyncio.run(main._fetch_live_status(fake, markets))
    assert result == {"EVT-A": "live"}
    assert main.state["live_status_cache"]["EVT-A"]["source"] == "milestone"


# --- _fetch_markets (live_markets_only): catalog rows must be hydrated with
# real prices, not left at whatever fallback state["latest_prices"] uses ----
# Real, confirmed-live bug: market_catalog rows only ever carry schedule/
# title/volume metadata (see services/market_catalog.py - no yes_bid_dollars
# column exists), so every live-only-selected market silently fell through
# to state["latest_prices"]'s `float(m.get("yes_bid_dollars") or 0.5)`
# fallback - every card showed 50c/50c YES/NO and never moved. Direct
# report: "showing 50c in green and red for all sets of yes/no values all
# across the app. its not updating either." Fixed by re-fetching the real,
# full market object (by series, batched) for whatever round_robin_select
# actually selected, before returning it.

class _FakeHydrationClient(_FakeLiveClient):
    """Extends the live-status fake with the two calls the hydration pass
    itself makes - get_markets (the batch, per-series path) and get_market
    (the per-ticker fallback for whatever the batch didn't return)."""

    def __init__(self, hydrated_markets, **kwargs):
        super().__init__(**kwargs)
        self.hydrated_markets = hydrated_markets  # ticker -> full market dict
        self.get_markets_calls = []
        self.get_market_calls = []

    async def get_markets(self, limit, status, series_ticker=None):
        self.get_markets_calls.append(series_ticker)
        return [m for t, m in self.hydrated_markets.items() if t.startswith(series_ticker)]

    async def get_market(self, ticker):
        self.get_market_calls.append(ticker)
        return self.hydrated_markets[ticker]


def _cfg_live_only(**overrides):
    cfg = {"kalshi": {
        "markets_watchlist": [], "min_volume_24h": 0, "live_markets_only": True,
        "watchlist_size": 10, "max_children_per_parent": None,
    }}
    cfg["kalshi"].update(overrides)
    return cfg


def test_fetch_markets_live_only_hydrates_catalog_rows_with_real_prices():
    main.state["live_status_cache"].clear()
    mc_module.clear_all()
    # A catalog row has no price fields at all - matches what
    # market_catalog.upsert_markets/candidates_in_window actually store.
    mc_module.upsert_markets("SERA", "Sports", [{
        "ticker": "SERA-EVT1-YES", "event_ticker": "SERA-EVT1", "volume_24h_fp": "1000",
        "occurrence_datetime": _iso(datetime.now(timezone.utc) + timedelta(minutes=-5)), "status": "open",
    }])
    fake = _FakeHydrationClient(
        hydrated_markets={
            "SERA-EVT1-YES": {"ticker": "SERA-EVT1-YES", "event_ticker": "SERA-EVT1", "yes_bid_dollars": "0.73"},
        },
        widget_status="live",
    )
    markets = asyncio.run(main._fetch_markets(fake, _cfg_live_only()))
    assert len(markets) == 1
    assert markets[0]["yes_bid_dollars"] == "0.73"  # real price, not a catalog row missing the field
    assert fake.get_markets_calls == ["SERA"]  # hydrated via the batched per-series path


def test_fetch_markets_live_only_falls_back_to_per_ticker_fetch_when_batch_misses_it():
    # The batch fetch filters status="open" - a market that settled between
    # the catalog scan and now won't come back from it. Confirmed live:
    # this was silently falling back to the unpriced catalog row (the same
    # 0.5-fallback bug, just for a smaller residual set) before the
    # per-ticker fallback existed.
    main.state["live_status_cache"].clear()
    mc_module.clear_all()
    mc_module.upsert_markets("SERA", "Sports", [{
        "ticker": "SERA-EVT1-YES", "event_ticker": "SERA-EVT1", "volume_24h_fp": "1000",
        "occurrence_datetime": _iso(datetime.now(timezone.utc) + timedelta(minutes=-5)), "status": "open",
    }])
    fake = _FakeHydrationClient(
        hydrated_markets={
            # Deliberately NOT returned by get_markets (simulates status="open"
            # excluding an already-finalized market) - only reachable via the
            # per-ticker get_market fallback.
        },
        widget_status="live",
    )
    fake.get_market_calls = []

    async def fake_get_market(ticker):
        fake.get_market_calls.append(ticker)
        return {"ticker": ticker, "event_ticker": "SERA-EVT1", "yes_bid_dollars": "0.00", "status": "finalized"}
    fake.get_market = fake_get_market

    markets = asyncio.run(main._fetch_markets(fake, _cfg_live_only()))
    assert len(markets) == 1
    assert markets[0]["yes_bid_dollars"] == "0.00"
    assert markets[0]["status"] == "finalized"
    assert fake.get_market_calls == ["SERA-EVT1-YES"]


# --- _fetch_event_titles: mutually_exclusive extraction --------------------
# Real Kalshi field, already present on every get_event() response this
# function was already fetching - previously discarded. Direct report:
# "'technically' they may be different markets but they are just
# inversions of each other" - confirmed live, a 2-outcome mutually_exclusive
# event's sibling markets' yes_bid prices sum to ~1.0. This field is what
# lets the dashboard tell that apart from independent sibling props sharing
# an event (not mutually exclusive) or a genuine multi-outcome market.

class _FakeEventClient:
    def __init__(self, events):
        self.events = events  # event_ticker -> full get_event()-shaped dict

    async def get_event(self, event_ticker):
        return self.events[event_ticker]


def test_fetch_event_titles_extracts_mutually_exclusive_true():
    main.state["event_titles"].clear()
    fake = _FakeEventClient({
        "EVT-A": {"event": {"title": "Toronto vs Philadelphia", "sub_title": None, "category": "Sports", "mutually_exclusive": True}},
    })
    markets = [{"ticker": "T-A", "event_ticker": "EVT-A"}]
    result = asyncio.run(main._fetch_event_titles(fake, markets))
    assert result["EVT-A"]["mutually_exclusive"] is True


def test_fetch_event_titles_extracts_mutually_exclusive_false():
    main.state["event_titles"].clear()
    fake = _FakeEventClient({
        "EVT-B": {"event": {"title": "Toronto vs Philadelphia: Outs Recorded", "sub_title": None, "category": "Sports", "mutually_exclusive": False}},
    })
    markets = [{"ticker": "T-B", "event_ticker": "EVT-B"}]
    result = asyncio.run(main._fetch_event_titles(fake, markets))
    assert result["EVT-B"]["mutually_exclusive"] is False


def test_fetch_event_titles_backfills_a_cached_entry_missing_mutually_exclusive():
    # Real, confirmed-live gap: every event_titles row cached before this
    # field existed has mutually_exclusive=None forever, since this
    # function only ever fetches what's "not yet cached" - a cached None
    # must be treated as "never fetched this field", not skipped.
    main.state["event_titles"].clear()
    main.state["event_titles"]["EVT-A"] = {"title": "Old cached title", "sub_title": None, "category": None, "mutually_exclusive": None}
    fake = _FakeEventClient({
        "EVT-A": {"event": {"title": "Toronto vs Philadelphia", "sub_title": None, "category": "Sports", "mutually_exclusive": True}},
    })
    markets = [{"ticker": "T-A", "event_ticker": "EVT-A"}]
    result = asyncio.run(main._fetch_event_titles(fake, markets))
    assert result["EVT-A"]["mutually_exclusive"] is True


def test_fetch_event_titles_does_not_refetch_an_already_complete_cache_entry():
    main.state["event_titles"].clear()
    main.state["event_titles"]["EVT-A"] = {"title": "Cached", "sub_title": None, "category": None, "mutually_exclusive": False}
    fake = _FakeEventClient({})  # would KeyError if _fetch_event_titles tried to re-fetch it
    markets = [{"ticker": "T-A", "event_ticker": "EVT-A"}]
    result = asyncio.run(main._fetch_event_titles(fake, markets))
    assert result == {}


def test_fetch_event_titles_extracts_competition_from_product_metadata():
    # Real, confirmed-live: get_event()'s product_metadata.competition -
    # "Wyndham Championship" for golf - was already fetched here and
    # discarded outright.
    main.state["event_titles"].clear()
    fake = _FakeEventClient({
        "EVT-A": {"event": {
            "title": "3rd Round Head-to-Head: Hossler vs James", "sub_title": None, "category": "Sports",
            "mutually_exclusive": True,
            "product_metadata": {"competition": "Wyndham Championship", "competition_scope": "3rd Round Matchups"},
        }},
    })
    markets = [{"ticker": "T-A", "event_ticker": "EVT-A"}]
    result = asyncio.run(main._fetch_event_titles(fake, markets))
    assert result["EVT-A"]["competition"] == "Wyndham Championship"
    assert result["EVT-A"]["competition_scope"] == "3rd Round Matchups"


def test_fetch_event_titles_competition_is_none_when_product_metadata_missing():
    # Real, honest absence (e.g. politics/economics events have no
    # product_metadata at all) - must not raise on the missing key.
    main.state["event_titles"].clear()
    fake = _FakeEventClient({
        "EVT-A": {"event": {"title": "Fed Rate Decision", "sub_title": None, "category": "Economics", "mutually_exclusive": True}},
    })
    markets = [{"ticker": "T-A", "event_ticker": "EVT-A"}]
    result = asyncio.run(main._fetch_event_titles(fake, markets))
    assert result["EVT-A"]["competition"] is None
    assert result["EVT-A"]["competition_scope"] is None


# --- Real account: widened position/fill/order field allowlists ------------
# Direct data-usage review finding: fees_paid_dollars/total_traded_dollars/
# last_updated_ts (positions) and created_time/fee_cost/is_taker/fill_id/
# order_id (fills) are real fields, confirmed against a live connected
# account, that _POSITION_FIELDS/_FILL_FIELDS were dropping before they ever
# reached the frontend - most notably fills had no timestamp at all.

def test_slim_position_keeps_fees_and_last_updated():
    position = {
        "ticker": "T-A", "position_fp": "9.13", "market_exposure_dollars": "4.8389",
        "realized_pnl_dollars": "0.0", "fees_paid_dollars": "0.1592",
        "total_traded_dollars": "4.8389", "last_updated_ts": "2026-08-08T15:05:18.514462Z",
        "some_other_real_field_not_used_anywhere": "should not leak through",
    }
    result = main._slim_position(position)
    assert result["fees_paid_dollars"] == "0.1592"
    assert result["total_traded_dollars"] == "4.8389"
    assert result["last_updated_ts"] == "2026-08-08T15:05:18.514462Z"
    assert "some_other_real_field_not_used_anywhere" not in result


def test_slim_event_position_keeps_the_real_event_fields():
    # event_positions - Kalshi's real parent-event grouping/exposure rollup
    # - was previously fetched every tick and dropped entirely before
    # /api/state (direct report: real positions on sibling child markets of
    # the same event rendered as unrelated flat rows with no grouping).
    event_position = {
        "event_ticker": "EVT-A", "total_cost_dollars": "4.84", "total_cost_shares_fp": "10.58",
        "event_exposure_dollars": "0.0", "realized_pnl_dollars": "0.48", "fees_paid_dollars": "0.04",
        "cursor": "should not leak through",
    }
    result = main._slim_event_position(event_position)
    assert result["event_ticker"] == "EVT-A"
    assert result["total_cost_dollars"] == "4.84"
    assert "cursor" not in result


def test_join_real_position_prices_attaches_current_yes_price():
    account_snapshot = {"positions": {"market_positions": [{"ticker": "REAL-A"}, {"ticker": "REAL-B"}]}}
    main._join_real_position_prices(account_snapshot, {"REAL-A": 0.62})
    positions = account_snapshot["positions"]["market_positions"]
    assert positions[0]["current_yes_price_dollars"] == 0.62
    # Genuinely absent (not yet fetched) is None, never a fabricated default
    # - same "don't show a number you can't honestly back" practice used
    # elsewhere in this app.
    assert positions[1]["current_yes_price_dollars"] is None


def test_join_real_position_prices_no_op_when_not_connected():
    account_snapshot = {"positions": None}
    main._join_real_position_prices(account_snapshot, {"REAL-A": 0.62})  # must not raise
    assert account_snapshot["positions"] is None


def test_slim_fill_keeps_created_time_fee_and_taker_flag():
    fill = {
        "ticker": "T-A", "market_ticker": "T-A", "side": "yes", "action": "buy", "count_fp": "9.13",
        "yes_price_dollars": "0.53", "no_price_dollars": "0.47",
        "created_time": "2026-08-08T15:05:18.514409Z", "fee_cost": "0.1592", "is_taker": True,
        "fill_id": "d670144c-75eb-5c82-168c-ec1d412b8184", "order_id": "8cf67fcb-6c78-440b-a9b7-9e29230979ed",
    }
    result = main._slim_fill(fill)
    assert result["created_time"] == "2026-08-08T15:05:18.514409Z"
    assert result["fee_cost"] == "0.1592"
    assert result["is_taker"] is True
    assert result["order_id"] == "8cf67fcb-6c78-440b-a9b7-9e29230979ed"


def test_slim_order_keeps_the_fields_the_order_history_panel_reads():
    order = {
        "order_id": "8cf67fcb-6c78-440b-a9b7-9e29230979ed", "user_id": "some-uuid", "client_order_id": "",
        "ticker": "T-A", "side": "yes", "action": "buy", "type": "market", "status": "executed",
        "yes_price_dollars": "0.99", "no_price_dollars": "0.01", "fill_count_fp": "9.13",
        "remaining_count_fp": "0.00", "initial_count_fp": "9.13",
        "taker_fees_dollars": "0.1592", "maker_fees_dollars": "0.0",
        "created_time": "2026-08-08T15:05:18.514409Z", "last_update_time": "2026-08-08T15:05:18.514409Z",
        "subaccount_number": 0,  # a real field, not currently rendered anywhere - confirms it's excluded
    }
    result = main._slim_order(order)
    assert result["order_id"] == "8cf67fcb-6c78-440b-a9b7-9e29230979ed"
    assert result["status"] == "executed"
    assert result["taker_fees_dollars"] == "0.1592"
    assert result["created_time"] == "2026-08-08T15:05:18.514409Z"
    assert "subaccount_number" not in result


def test_account_orders_endpoint_reports_not_connected_when_account_disabled(monkeypatch):
    # Other tests in this file monkeypatch main.account._client to simulate
    # a connected account - set it explicitly here rather than assuming
    # whatever the ambient state happens to be after them.
    monkeypatch.setattr(main.account, "_client", None)
    resp = client.get("/api/account/orders")
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is False
    assert body["orders"] == []


def test_account_orders_endpoint_slims_and_paginates(monkeypatch):
    monkeypatch.setattr(main.account, "_client", object())  # simulate a connected account

    async def fake_get_orders(limit, cursor=None, status=None):
        assert limit == 25
        return {
            "orders": [{
                "order_id": "ord-1", "ticker": "T-A", "side": "yes", "action": "buy", "type": "market",
                "status": "executed", "yes_price_dollars": "0.99", "no_price_dollars": "0.01",
                "fill_count_fp": "9.13", "remaining_count_fp": "0.00", "initial_count_fp": "9.13",
                "taker_fees_dollars": "0.1592", "maker_fees_dollars": "0.0",
                "created_time": "2026-08-08T15:05:18Z", "last_update_time": "2026-08-08T15:05:18Z",
            }],
            "cursor": "next-page-token",
        }
    monkeypatch.setattr(main.account, "get_orders", fake_get_orders)

    resp = client.get("/api/account/orders")
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is True
    assert body["cursor"] == "next-page-token"
    assert len(body["orders"]) == 1
    assert body["orders"][0]["order_id"] == "ord-1"


def test_market_catalog_status_endpoint_reports_progress():
    mc_module.clear_all()
    mc_module.upsert_markets("SER-A", "Sports", [
        {"ticker": "TICK-A", "event_ticker": "EVT-A", "volume_24h_fp": "1000",
         "occurrence_datetime": _iso(datetime.now(timezone.utc) + timedelta(minutes=-5)), "status": "open"},
    ])
    mc_module.mark_scanned(["SER-A"])
    resp = client.get("/api/market-catalog/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["scanned_series"] == 1
    assert body["total_markets"] == 1
    assert "enabled" in body


# ---- Market Analyst: on-demand analyze (direct request, 2026-08-09 - ----
# ---- replaces the earlier automatic per-tick background scan) ----------

class _FakeAnalystKalshiClient:
    """No event_ticker on the fake market, so the get_event bonus-fetch
    branch is exercised separately by real market_analyst_agent tests -
    this fake only needs to prove _run_market_analyst_for_ticker's own
    gating/wiring, not re-test get_event's already-covered call shape."""

    def __init__(self, market_detail):
        self._market_detail = market_detail
        self.get_market_calls = []

    async def get_market(self, ticker):
        self.get_market_calls.append(ticker)
        return self._market_detail


def _isolate_market_analyst_dbs(tmp_path, monkeypatch):
    # Both DBs _run_market_analyst_for_ticker's real code path touches
    # (market_analyst_agent's own analyses table, plus signal_log.stats()
    # for the prompt's whale-track-record context) - redirected per-test so
    # nothing here can reach the real, live data/*.db files (see this
    # module's own docstring on why that matters).
    import services.signal_log as signal_log_module
    monkeypatch.setattr(market_analyst_agent, "DB_PATH", tmp_path / "market_analyst.db")
    monkeypatch.setattr(signal_log_module, "DB_PATH", tmp_path / "signal_log.db")


def test_run_market_analyst_for_ticker_gated_when_disabled(tmp_path, monkeypatch):
    _isolate_market_analyst_dbs(tmp_path, monkeypatch)
    cfg = {**main.config_store.get(), "market_analyst": {"enabled": False}}
    fake_client = _FakeAnalystKalshiClient({"title": "T"})
    result = asyncio.run(main._run_market_analyst_for_ticker(fake_client, cfg, "TICK-A"))
    assert result["ok"] is False
    assert "disabled" in result["reason"].lower()
    assert fake_client.get_market_calls == []  # gated before ever touching the network


def test_run_market_analyst_for_ticker_gated_without_api_key(tmp_path, monkeypatch):
    _isolate_market_analyst_dbs(tmp_path, monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = {**main.config_store.get(), "market_analyst": {"enabled": True}}
    fake_client = _FakeAnalystKalshiClient({"title": "T"})
    result = asyncio.run(main._run_market_analyst_for_ticker(fake_client, cfg, "TICK-A"))
    assert result["ok"] is False
    assert "api" in result["reason"].lower()


def test_run_market_analyst_for_ticker_gated_during_cooldown(tmp_path, monkeypatch):
    _isolate_market_analyst_dbs(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    cfg = {**main.config_store.get(), "market_analyst": {"enabled": True, "reanalyze_cooldown_sec": 1800}}
    market_analyst_agent.record_analysis(
        "TICK-A", "TICK", 0.5, 0.6, 0.7, "r", "m", analyzed_at=time.time(),
    )
    fake_client = _FakeAnalystKalshiClient({"title": "T"})
    result = asyncio.run(main._run_market_analyst_for_ticker(fake_client, cfg, "TICK-A"))
    assert result["ok"] is False
    assert "recently" in result["reason"].lower()
    assert fake_client.get_market_calls == []


def test_run_market_analyst_for_ticker_returns_reason_when_market_fetch_raises(tmp_path, monkeypatch):
    # A ticker Kalshi's API doesn't recognize (typo, delisted, etc.) -
    # get_market() raising must degrade to a clean gated reason, not bubble
    # an exception up through the route.
    _isolate_market_analyst_dbs(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    cfg = {**main.config_store.get(), "market_analyst": {"enabled": True}}

    class _FailingClient:
        async def get_market(self, ticker):
            raise RuntimeError("404: no such market")

    result = asyncio.run(main._run_market_analyst_for_ticker(_FailingClient(), cfg, "NOT-A-REAL-TICKER"))
    assert result["ok"] is False
    assert "could not fetch" in result["reason"].lower()


def test_run_market_analyst_for_ticker_rejects_a_concurrent_request_for_the_same_ticker(tmp_path, monkeypatch):
    # Real bug found by audit (2026-08-09): last_analyzed_at only gets
    # recorded at the very end, after two real awaits - a second request
    # for the same ticker that starts before the first one finishes would
    # read the same pre-commit cooldown state and both would spend a real
    # API call. Reproduces the exact race: request A is parked mid-fetch
    # (simulating a slow network call) when request B fires for the same
    # ticker; B must be rejected without ever reaching the network itself.
    _isolate_market_analyst_dbs(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    main._analyzing_tickers.clear()
    cfg = {**main.config_store.get(), "market_analyst": {"enabled": True}}

    async def fake_analyze_market(market_detail, snapshot, model, api_key):
        return {"estimated_probability": 0.6, "confidence": 0.5, "reasoning": "r"}
    monkeypatch.setattr(market_analyst_agent, "analyze_market", fake_analyze_market)

    started = asyncio.Event()
    proceed = asyncio.Event()

    class _SlowClient:
        def __init__(self):
            self.get_market_calls = 0

        async def get_market(self, ticker):
            self.get_market_calls += 1
            started.set()
            await proceed.wait()
            return {"title": "T", "yes_bid_dollars": "0.5"}

    fake_client = _SlowClient()

    async def scenario():
        task_a = asyncio.create_task(main._run_market_analyst_for_ticker(fake_client, cfg, "TICK-A"))
        await started.wait()  # request A is now parked inside its "network" call
        result_b = await main._run_market_analyst_for_ticker(fake_client, cfg, "TICK-A")
        proceed.set()
        result_a = await task_a
        return result_a, result_b

    result_a, result_b = asyncio.run(scenario())
    assert result_a["ok"] is True
    assert result_b["ok"] is False
    assert "already analyzing" in result_b["reason"].lower()
    assert fake_client.get_market_calls == 1  # B was rejected before ever touching the network
    assert market_analyst_agent.total_count() == 1  # only A's analysis was ever recorded


def test_run_market_analyst_for_ticker_succeeds_and_records_analysis(tmp_path, monkeypatch):
    _isolate_market_analyst_dbs(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    cfg = {**main.config_store.get(), "market_analyst": {"enabled": True, "model": "claude-sonnet-5"}}

    async def fake_analyze_market(market_detail, snapshot, model, api_key):
        assert model == "claude-sonnet-5"
        assert api_key == "fake-key"
        return {"estimated_probability": 0.72, "confidence": 0.65, "reasoning": "Because of X."}
    monkeypatch.setattr(market_analyst_agent, "analyze_market", fake_analyze_market)

    fake_client = _FakeAnalystKalshiClient({"title": "T", "yes_bid_dollars": "0.55"})
    result = asyncio.run(main._run_market_analyst_for_ticker(fake_client, cfg, "TICK-A"))

    assert result["ok"] is True
    assert result["estimated_probability"] == 0.72
    assert result["market_price"] == 0.55
    assert fake_client.get_market_calls == ["TICK-A"]
    assert market_analyst_agent.total_count() == 1  # actually persisted, not just returned


def test_post_market_analyst_analyze_route_returns_gated_reason_when_disabled(tmp_path, monkeypatch):
    _isolate_market_analyst_dbs(tmp_path, monkeypatch)
    main.config_store.update({"market_analyst": {"enabled": False}})
    resp = client.post("/api/market-analyst/analyze", json={"ticker": "TICK-A"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "disabled" in body["reason"].lower()


# ---- Market Analyst: per-series analysis mode (Item 3B, 2026-08-10) --------

def test_build_series_context_scopes_stats_and_trades_to_the_series(tmp_path, monkeypatch):
    _isolate_market_analyst_dbs(tmp_path, monkeypatch)
    main.broker.reset(starting_bankroll=10000.0)
    main.broker.open_position("KXTICK-A", "yes", size=10, price=0.5, reason="whale print 5000 @ 0.5 (conf 0.6)")
    main.broker.close_position("KXTICK-A", exit_price=1.0, reason="market settled YES - position won")
    main.broker.open_position("OTHER-B", "yes", size=10, price=0.5, reason="whale print 5000 @ 0.5 (conf 0.6)")
    main.broker.close_position("OTHER-B", exit_price=0.0, reason="market settled NO - position lost")

    cfg = {**main.config_store.get(), "strategy": {**main.config_store.get()["strategy"], "excluded_series": ["OTHER"]}}
    ctx = main._build_series_context(cfg, "KXTICK")
    assert ctx["series"] == "KXTICK"
    assert ctx["trade_summary"]["total_closed"] == 1  # only the KXTICK-A trade, not OTHER-B
    assert ctx["currently_excluded"] is False


def test_build_series_context_reports_excluded_and_notional_override():
    cfg = {
        **main.config_store.get(),
        "strategy": {**main.config_store.get()["strategy"], "excluded_series": ["KXTICK"]},
        "whale_watcher_kalshi": {"min_notional_usd_by_series": {"KXTICK": 750}},
    }
    ctx = main._build_series_context(cfg, "KXTICK")
    assert ctx["currently_excluded"] is True
    assert ctx["min_notional_override"] == 750


def test_series_suggestions_from_raw_converts_exclude_action():
    cfg = {"strategy": {"excluded_series": []}}
    raw = [{"action": "exclude", "rationale": "weak whale accuracy"}]
    out = main._series_suggestions_from_raw(cfg, "KXTICK", raw)
    assert len(out) == 1
    s = out[0]
    assert s["config_path"] == "strategy.excluded_series"
    assert s["current_value"] == []
    assert s["suggested_value"] == ["KXTICK"]
    assert s["source"] == "series-analyst"
    assert s["series"] == "KXTICK"
    assert "id" in s


def test_series_suggestions_from_raw_converts_include_action():
    cfg = {"strategy": {"excluded_series": ["KXTICK", "OTHER"]}}
    raw = [{"action": "include", "rationale": "back to normal"}]
    out = main._series_suggestions_from_raw(cfg, "KXTICK", raw)
    assert out[0]["suggested_value"] == ["OTHER"]


def test_series_suggestions_from_raw_drops_no_op_actions():
    # Already excluded and the model still says "exclude" - nothing to apply.
    cfg = {"strategy": {"excluded_series": ["KXTICK"]}}
    raw = [{"action": "exclude", "rationale": "r"}]
    assert main._series_suggestions_from_raw(cfg, "KXTICK", raw) == []


def test_run_series_analysis_gated_when_disabled(tmp_path, monkeypatch):
    _isolate_market_analyst_dbs(tmp_path, monkeypatch)
    cfg = {**main.config_store.get(), "market_analyst": {"enabled": False}}
    result = asyncio.run(main._run_series_analysis(cfg, "KXTICK"))
    assert result["ok"] is False
    assert "disabled" in result["reason"].lower()


def test_run_series_analysis_gated_without_api_key(tmp_path, monkeypatch):
    _isolate_market_analyst_dbs(tmp_path, monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = {**main.config_store.get(), "market_analyst": {"enabled": True}}
    result = asyncio.run(main._run_series_analysis(cfg, "KXTICK"))
    assert result["ok"] is False
    assert "api" in result["reason"].lower()


def test_run_series_analysis_gated_during_cooldown(tmp_path, monkeypatch):
    _isolate_market_analyst_dbs(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    cfg = {**main.config_store.get(), "market_analyst": {"enabled": True, "reanalyze_cooldown_sec": 1800}}
    market_analyst_agent.record_series_analysis("KXTICK", "s", [], "m", analyzed_at=time.time())
    result = asyncio.run(main._run_series_analysis(cfg, "KXTICK"))
    assert result["ok"] is False
    assert "recently" in result["reason"].lower()


def test_run_series_analysis_rejects_a_concurrent_request_for_the_same_series(tmp_path, monkeypatch):
    _isolate_market_analyst_dbs(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    main._analyzing_series.clear()
    cfg = {**main.config_store.get(), "market_analyst": {"enabled": True}}

    started = asyncio.Event()
    proceed = asyncio.Event()

    async def slow_analyze_series(series_ctx, model, api_key):
        started.set()
        await proceed.wait()
        return {"summary": "s", "suggestions": []}
    monkeypatch.setattr(market_analyst_agent, "analyze_series", slow_analyze_series)

    async def scenario():
        task_a = asyncio.create_task(main._run_series_analysis(cfg, "KXTICK"))
        await started.wait()
        result_b = await main._run_series_analysis(cfg, "KXTICK")
        proceed.set()
        result_a = await task_a
        return result_a, result_b

    result_a, result_b = asyncio.run(scenario())
    assert result_a["ok"] is True
    assert result_b["ok"] is False
    assert "already analyzing" in result_b["reason"].lower()


def test_run_series_analysis_succeeds_and_records_analysis(tmp_path, monkeypatch):
    _isolate_market_analyst_dbs(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    cfg = {**main.config_store.get(), "market_analyst": {"enabled": True, "model": "claude-sonnet-5"},
           "strategy": {**main.config_store.get()["strategy"], "excluded_series": []}}

    async def fake_analyze_series(series_ctx, model, api_key):
        assert series_ctx["series"] == "KXTICK"
        assert model == "claude-sonnet-5"
        return {"summary": "Weak whale accuracy.", "suggestions": [{"action": "exclude", "rationale": "r"}]}
    monkeypatch.setattr(market_analyst_agent, "analyze_series", fake_analyze_series)

    result = asyncio.run(main._run_series_analysis(cfg, "KXTICK"))
    assert result["ok"] is True
    assert result["series"] == "KXTICK"
    assert result["summary"] == "Weak whale accuracy."
    assert len(result["suggestions"]) == 1
    assert result["suggestions"][0]["suggested_value"] == ["KXTICK"]
    assert market_analyst_agent.get_series_analysis(result["analysis_id"]) is not None


def test_post_market_analyst_series_analyze_route_returns_gated_reason_when_disabled(tmp_path, monkeypatch):
    _isolate_market_analyst_dbs(tmp_path, monkeypatch)
    main.config_store.update({"market_analyst": {"enabled": False}})
    resp = client.post("/api/market-analyst/series/analyze", json={"series": "KXTICK"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "disabled" in body["reason"].lower()


def test_post_market_analyst_series_apply_end_to_end(tmp_path, monkeypatch):
    _isolate_market_analyst_dbs(tmp_path, monkeypatch)
    main.config_store.update({"strategy": {"excluded_series": []}})
    suggestions = main._series_suggestions_from_raw(
        main.config_store.get(), "KXTICK", [{"action": "exclude", "rationale": "weak whale accuracy"}],
    )
    analysis_id = market_analyst_agent.record_series_analysis("KXTICK", "s", suggestions, "m")

    resp = client.post("/api/market-analyst/series/apply", json={
        "analysis_id": analysis_id, "suggestion_id": suggestions[0]["id"],
    })
    assert resp.status_code == 200
    assert main.config_store.get()["strategy"]["excluded_series"] == ["KXTICK"]

    changes = client.get("/api/advisory/applied-changes").json()["changes"]
    match = next(c for c in changes if c["config_path"] == "strategy.excluded_series")
    assert match["source"] == "series-analyst"


def test_post_market_analyst_series_apply_404s_for_unknown_analysis(tmp_path, monkeypatch):
    _isolate_market_analyst_dbs(tmp_path, monkeypatch)
    resp = client.post("/api/market-analyst/series/apply", json={
        "analysis_id": "does-not-exist", "suggestion_id": "whatever",
    })
    assert resp.status_code == 404


def test_post_market_analyst_series_apply_404s_for_unknown_suggestion(tmp_path, monkeypatch):
    _isolate_market_analyst_dbs(tmp_path, monkeypatch)
    analysis_id = market_analyst_agent.record_series_analysis("KXTICK", "s", [], "m")
    resp = client.post("/api/market-analyst/series/apply", json={
        "analysis_id": analysis_id, "suggestion_id": "does-not-exist",
    })
    assert resp.status_code == 404


def test_post_market_analyst_series_apply_rejects_stale_suggestion(tmp_path, monkeypatch):
    # Direct report (2026-08-11): "make sure the suggested values arent
    # stale." The suggestion's current_value ("KXTICK" not yet excluded) was
    # captured when the analysis ran - if strategy.excluded_series has since
    # changed (a manual edit here, simulating any intervening change), the
    # live value no longer matches what the suggestion assumed, and applying
    # it anyway would silently overwrite based on a stale premise.
    _isolate_market_analyst_dbs(tmp_path, monkeypatch)
    main.config_store.update({"strategy": {"excluded_series": []}})
    suggestions = main._series_suggestions_from_raw(
        main.config_store.get(), "KXTICK", [{"action": "exclude", "rationale": "weak whale accuracy"}],
    )
    analysis_id = market_analyst_agent.record_series_analysis("KXTICK", "s", suggestions, "m")

    # Something else changes strategy.excluded_series after analysis time.
    main.config_store.update({"strategy": {"excluded_series": ["KXOTHER"]}})

    resp = client.post("/api/market-analyst/series/apply", json={
        "analysis_id": analysis_id, "suggestion_id": suggestions[0]["id"],
    })
    assert resp.status_code == 409
    assert "stale" in resp.json()["detail"].lower()
    # The stale apply must not have gone through - KXTICK was never added.
    assert main.config_store.get()["strategy"]["excluded_series"] == ["KXOTHER"]


def test_config_value_at_path_reads_nested_field():
    cfg = {"strategy": {"entry_threshold": 0.6}}
    assert main._config_value_at_path(cfg, "strategy.entry_threshold") == 0.6


def test_config_value_at_path_missing_section_or_field_returns_none():
    cfg = {"strategy": {"entry_threshold": 0.6}}
    assert main._config_value_at_path(cfg, "nonexistent.field") is None
    assert main._config_value_at_path(cfg, "strategy.nonexistent") is None


# ---- Market Analyst: "Feed the Analyst" full-spectrum scan (Item 3C, 2026-08-10) ----

def test_types_compatible_treats_int_and_float_as_interchangeable():
    assert main._types_compatible(0.5, 1) is True
    assert main._types_compatible(5, 0.1) is True


def test_types_compatible_rejects_bool_against_numeric():
    # bool is technically an int subclass in Python - a stray True/False
    # landing in a numeric field would be real config corruption, not a
    # reasonable suggestion, so this must be rejected even though
    # isinstance(True, int) is True.
    assert main._types_compatible(0.5, True) is False
    assert main._types_compatible(True, False) is True


def test_types_compatible_requires_exact_match_for_other_types():
    assert main._types_compatible("a", "b") is True
    assert main._types_compatible("a", 1) is False
    assert main._types_compatible([1, 2], [3]) is True
    assert main._types_compatible([1, 2], "x") is False


def test_full_spectrum_suggestions_from_raw_accepts_a_real_existing_field():
    cfg = {"strategy": {"entry_threshold": 0.5}}
    raw = [{"config_path": "strategy.entry_threshold", "suggested_value": 0.6, "rationale": "r"}]
    out = main._full_spectrum_suggestions_from_raw(cfg, raw)
    assert len(out) == 1
    assert out[0]["current_value"] == 0.5
    assert out[0]["suggested_value"] == 0.6
    assert out[0]["source"] == "full-spectrum-analyst"


def test_full_spectrum_suggestions_from_raw_rejects_nonexistent_field():
    cfg = {"strategy": {"entry_threshold": 0.5}}
    raw = [{"config_path": "strategy.made_up_field", "suggested_value": 1, "rationale": "r"}]
    assert main._full_spectrum_suggestions_from_raw(cfg, raw) == []


def test_full_spectrum_suggestions_from_raw_rejects_nonexistent_section():
    cfg = {"strategy": {"entry_threshold": 0.5}}
    raw = [{"config_path": "made_up_section.field", "suggested_value": 1, "rationale": "r"}]
    assert main._full_spectrum_suggestions_from_raw(cfg, raw) == []


def test_full_spectrum_suggestions_from_raw_rejects_protected_fields():
    cfg = {"kalshi_account": {"trading_enabled": False}, "advisory": {"auto_apply_enabled": False}}
    raw = [
        {"config_path": "kalshi_account.trading_enabled", "suggested_value": True, "rationale": "r"},
        {"config_path": "advisory.auto_apply_enabled", "suggested_value": True, "rationale": "r"},
    ]
    assert main._full_spectrum_suggestions_from_raw(cfg, raw) == []


def test_full_spectrum_suggestions_from_raw_drops_no_op_and_type_mismatched():
    cfg = {"strategy": {"entry_threshold": 0.5, "live_markets_only": False}}
    raw = [
        {"config_path": "strategy.entry_threshold", "suggested_value": 0.5, "rationale": "r"},  # no-op
        {"config_path": "strategy.live_markets_only", "suggested_value": "yes", "rationale": "r"},  # type mismatch
    ]
    assert main._full_spectrum_suggestions_from_raw(cfg, raw) == []


def test_run_full_spectrum_analysis_gated_when_disabled(tmp_path, monkeypatch):
    _isolate_market_analyst_dbs(tmp_path, monkeypatch)
    cfg = {**main.config_store.get(), "market_analyst": {"enabled": False}}
    result = asyncio.run(main._run_full_spectrum_analysis(cfg))
    assert result["ok"] is False
    assert "disabled" in result["reason"].lower()


def test_run_full_spectrum_analysis_gated_without_api_key(tmp_path, monkeypatch):
    _isolate_market_analyst_dbs(tmp_path, monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = {**main.config_store.get(), "market_analyst": {"enabled": True}}
    result = asyncio.run(main._run_full_spectrum_analysis(cfg))
    assert result["ok"] is False
    assert "api" in result["reason"].lower()


def test_run_full_spectrum_analysis_gated_during_cooldown(tmp_path, monkeypatch):
    _isolate_market_analyst_dbs(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    cfg = {**main.config_store.get(), "market_analyst": {"enabled": True, "reanalyze_cooldown_sec": 1800}}
    market_analyst_agent.record_full_spectrum_analysis("s", [], "m", analyzed_at=time.time())
    result = asyncio.run(main._run_full_spectrum_analysis(cfg))
    assert result["ok"] is False
    assert "recently" in result["reason"].lower()


def test_run_full_spectrum_analysis_rejects_a_concurrent_request(tmp_path, monkeypatch):
    _isolate_market_analyst_dbs(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    main._full_spectrum_analyzing = False
    cfg = {**main.config_store.get(), "market_analyst": {"enabled": True}}

    started = asyncio.Event()
    proceed = asyncio.Event()

    async def slow_analyze_full_spectrum(context, model, api_key):
        started.set()
        await proceed.wait()
        return {"summary": "s", "suggestions": []}
    monkeypatch.setattr(market_analyst_agent, "analyze_full_spectrum", slow_analyze_full_spectrum)

    async def scenario():
        task_a = asyncio.create_task(main._run_full_spectrum_analysis(cfg))
        await started.wait()
        result_b = await main._run_full_spectrum_analysis(cfg)
        proceed.set()
        result_a = await task_a
        return result_a, result_b

    result_a, result_b = asyncio.run(scenario())
    assert result_a["ok"] is True
    assert result_b["ok"] is False
    assert "already running" in result_b["reason"].lower()
    main._full_spectrum_analyzing = False


def test_run_full_spectrum_analysis_succeeds_and_records_analysis(tmp_path, monkeypatch):
    _isolate_market_analyst_dbs(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    cfg = {**main.config_store.get(), "market_analyst": {"enabled": True, "model": "claude-sonnet-5"}}

    async def fake_analyze_full_spectrum(context, model, api_key):
        assert "config" in context
        assert model == "claude-sonnet-5"
        return {"summary": "Looks healthy overall.",
                "suggestions": [{"config_path": "risk.max_daily_loss_pct", "suggested_value": 0.3, "rationale": "r"}]}
    monkeypatch.setattr(market_analyst_agent, "analyze_full_spectrum", fake_analyze_full_spectrum)

    result = asyncio.run(main._run_full_spectrum_analysis(cfg))
    assert result["ok"] is True
    assert result["summary"] == "Looks healthy overall."
    assert len(result["suggestions"]) == 1
    assert result["suggestions"][0]["config_path"] == "risk.max_daily_loss_pct"
    assert market_analyst_agent.get_full_spectrum_analysis(result["analysis_id"]) is not None


def test_post_market_analyst_full_spectrum_analyze_route_returns_gated_reason_when_disabled(tmp_path, monkeypatch):
    _isolate_market_analyst_dbs(tmp_path, monkeypatch)
    main.config_store.update({"market_analyst": {"enabled": False}})
    resp = client.post("/api/market-analyst/full-spectrum/analyze")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "disabled" in body["reason"].lower()


def test_post_market_analyst_full_spectrum_apply_end_to_end(tmp_path, monkeypatch):
    _isolate_market_analyst_dbs(tmp_path, monkeypatch)
    main.config_store.update({"risk": {"max_daily_loss_pct": 0.25}})
    suggestions = main._full_spectrum_suggestions_from_raw(
        main.config_store.get(),
        [{"config_path": "risk.max_daily_loss_pct", "suggested_value": 0.3, "rationale": "tighten the kill switch"}],
    )
    analysis_id = market_analyst_agent.record_full_spectrum_analysis("s", suggestions, "m")

    resp = client.post("/api/market-analyst/full-spectrum/apply", json={
        "analysis_id": analysis_id, "suggestion_id": suggestions[0]["id"],
    })
    assert resp.status_code == 200
    assert main.config_store.get()["risk"]["max_daily_loss_pct"] == 0.3

    changes = client.get("/api/advisory/applied-changes").json()["changes"]
    match = next(c for c in changes if c["config_path"] == "risk.max_daily_loss_pct")
    assert match["source"] == "full-spectrum-analyst"


def test_post_market_analyst_full_spectrum_apply_404s_for_unknown_analysis(tmp_path, monkeypatch):
    _isolate_market_analyst_dbs(tmp_path, monkeypatch)
    resp = client.post("/api/market-analyst/full-spectrum/apply", json={
        "analysis_id": "does-not-exist", "suggestion_id": "whatever",
    })
    assert resp.status_code == 404


def test_post_market_analyst_full_spectrum_apply_404s_for_unknown_suggestion(tmp_path, monkeypatch):
    _isolate_market_analyst_dbs(tmp_path, monkeypatch)
    analysis_id = market_analyst_agent.record_full_spectrum_analysis("s", [], "m")
    resp = client.post("/api/market-analyst/full-spectrum/apply", json={
        "analysis_id": analysis_id, "suggestion_id": "does-not-exist",
    })
    assert resp.status_code == 404


def test_post_market_analyst_full_spectrum_apply_rejects_stale_suggestion(tmp_path, monkeypatch):
    # Same staleness guard as the series-apply route above - the suggestion
    # assumed risk.max_daily_loss_pct was 0.25 when the analysis ran; if it's
    # since moved (a manual edit here, standing in for any intervening
    # change - another analyst suggestion, a plain Config-tab save, etc.),
    # applying the old suggestion would silently clobber the newer value.
    _isolate_market_analyst_dbs(tmp_path, monkeypatch)
    main.config_store.update({"risk": {"max_daily_loss_pct": 0.25}})
    suggestions = main._full_spectrum_suggestions_from_raw(
        main.config_store.get(),
        [{"config_path": "risk.max_daily_loss_pct", "suggested_value": 0.3, "rationale": "tighten the kill switch"}],
    )
    analysis_id = market_analyst_agent.record_full_spectrum_analysis("s", suggestions, "m")

    main.config_store.update({"risk": {"max_daily_loss_pct": 0.4}})

    resp = client.post("/api/market-analyst/full-spectrum/apply", json={
        "analysis_id": analysis_id, "suggestion_id": suggestions[0]["id"],
    })
    assert resp.status_code == 409
    assert "stale" in resp.json()["detail"].lower()
    assert main.config_store.get()["risk"]["max_daily_loss_pct"] == 0.4


# --- _enrich_recent_trades (2026-08-10 - Portfolio Trade Log real-outcome fix) ---
# Direct report: "not seeing the results of the positions in the trade log" -
# a close row rendered identically to a still-open entry, since PaperBroker.
# state()'s raw recent_trades carry no close_type/realized_pnl/won at all.
# Uses a fresh, isolated PaperBroker (not main.broker, the shared module-level
# singleton other tests in this file touch) so this can't pollute anything else.

def test_enrich_recent_trades_leaves_a_still_open_entry_unenriched(tmp_path):
    fresh_broker = pb_module.PaperBroker(starting_bankroll=1000.0, db_path=tmp_path / "fresh_broker_open.db")
    fresh_broker.open_position(ticker="TICK-A", side="yes", size=100, price=0.5, reason="test entry")
    enriched = main._enrich_recent_trades(fresh_broker)
    assert len(enriched) == 1
    assert enriched[0].get("close_type") is None
    assert enriched[0].get("realized_pnl") is None


def test_enrich_recent_trades_attaches_real_close_outcome(tmp_path):
    fresh_broker = pb_module.PaperBroker(starting_bankroll=1000.0, db_path=tmp_path / "fresh_broker_close.db")
    fresh_broker.open_position(ticker="TICK-A", side="yes", size=100, price=0.5, reason="test entry")
    # A reason trade_analytics.classify_close_type actually recognizes
    # (see its _CLOSE_TYPE_PATTERNS) - close_position() prepends "closed: "
    # and appends "(realized ...)" itself, so this must match starting
    # right after "closed: ".
    close_trade = fresh_broker.close_position("TICK-A", 0.8, "take-profit hit: unrealized gain 60% of cost basis")
    enriched = main._enrich_recent_trades(fresh_broker)
    close_row = next(r for r in enriched if r["id"] == close_trade.id)
    assert close_row["close_type"] == "take_profit"
    assert close_row["won"] is True
    assert isinstance(close_row["realized_pnl"], float)
    assert close_row["realized_pnl"] > 0


def test_enrich_recent_trades_pairs_correctly_even_when_entry_is_outside_the_tail_25(tmp_path):
    # The real reason this re-derives over the FULL trade_log rather than
    # just the displayed tail-25 slice: an entry more than 25 trades back
    # must still pair correctly with a close inside the recent window.
    fresh_broker = pb_module.PaperBroker(starting_bankroll=100000.0, db_path=tmp_path / "fresh_broker_many.db")
    fresh_broker.open_position(ticker="OLD-A", side="yes", size=10, price=0.5, reason="old entry")
    for i in range(30):
        fresh_broker.open_position(ticker=f"FILLER-{i}", side="yes", size=1, price=0.5, reason="filler")
        fresh_broker.close_position(f"FILLER-{i}", 0.5, "take-profit hit: filler")
    close_trade = fresh_broker.close_position("OLD-A", 0.9, "take-profit hit: unrealized gain 80% of cost basis")
    enriched = main._enrich_recent_trades(fresh_broker)
    close_row = next(r for r in enriched if r["id"] == close_trade.id)
    assert close_row["close_type"] == "take_profit"
    assert close_row["won"] is True


# --- market-native / shadow un-halt routes (2026-08-10) --------------------
# Real bug found live investigating a direct report ("market-native strategy
# seems to have stalled"): market_strategy.py's own risk manager had tripped
# its kill switch with no route to ever clear it - and services/shadow_mode.py
# had the identical gap (confirmed live, dormant only because mode was
# "paper" at the time).

def test_market_risk_halt_and_resume_routes():
    resp = client.post("/api/market-risk/halt")
    assert resp.status_code == 200
    assert resp.json()["halted"] is True
    assert main.market_risk.halted is True

    resp = client.post("/api/market-risk/resume")
    assert resp.status_code == 200
    assert resp.json()["halted"] is False
    assert main.market_risk.halted is False


def test_shadow_risk_resume_route():
    main.shadow.halted = True
    main.shadow.halt_reason = "test halt"
    resp = client.post("/api/shadow-risk/resume")
    assert resp.status_code == 200
    assert resp.json()["halted"] is False
    assert main.shadow.halted is False


def test_reset_endpoint_market_native_flag_resets_broker_and_risk():
    main.market_broker.open_position(ticker="TICK-A", side="yes", size=10, price=0.5, reason="test")
    main.market_risk.manual_halt("test halt")
    resp = client.post("/api/reset", json={"paper": False, "market_native": True})
    assert resp.status_code == 200
    assert "market_native" in resp.json()["cleared"]
    assert main.market_broker.positions == {}
    assert main.market_risk.halted is False
