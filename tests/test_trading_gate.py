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

_tmp_dir = Path(tempfile.mkdtemp(prefix="trading_gate_test_"))
pb_module.DB_PATH = _tmp_dir / "paper_broker.db"
rm_module.DB_PATH = _tmp_dir / "risk_state.db"
cp_module.DB_PATH = _tmp_dir / "config_performance.db"
mh_module.DB_PATH = _tmp_dir / "market_history.db"
mc_module.DB_PATH = _tmp_dir / "market_catalog.db"
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
    main.config_store.update({"advisory": {"enabled": False, "min_resolved_trades_per_variant": 30}})


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


def test_advisory_recommendations_reports_gated_reason_under_threshold():
    _reset_advisory_state()
    main.config_store.update({"advisory": {"enabled": True, "min_resolved_trades_per_variant": 30}})
    resp = client.get("/api/advisory/recommendations")
    assert resp.status_code == 200
    body = resp.json()
    assert body["recommendations"] == []
    assert "resolved trades" in body["gated_reason"]


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
