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
