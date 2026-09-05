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

import pytest

from services import app_state as app_state_module
from services import candidate_log as cl_module
from services import capture_writer as cw_module
from services.config import config_performance as cp_module
from services.config import config_store as config_store_module
from services.market_catalog import market_catalog as mc_module
from services import market_analyst_agent
from services.market_analyst_agent import _db as maa_db_module
from services import market_history as mh_module
from services import mutual_exclusivity
from services import paper_broker as pb_module
from services import risk_manager as rm_module
from services import series_evaluator as se_module
from services import series_watcher as sw_module
from services import settlement_edge as sedge_module
from services.whale_stream import whale_stream_handlers as wsh_module
from services import settlement_resolver

_tmp_dir = Path(tempfile.mkdtemp(prefix="trading_gate_test_"))
# pb_module.DB_PATH is deliberately NOT re-overridden here (2026-08-27 real
# gap found writing the close_positions_first reset tests below): conftest's
# install_runtime_isolation() already redirects it - services.paper_broker is
# the first entry in _EAGER_SINGLETON_MODULES - and does so BEFORE this file
# is even imported. That redirect is also what services.app_state's own
# `broker = PaperBroker(...)` singleton (services/app_state.py:91, what
# `main.broker` below actually is) picks up, transitively, the moment
# anything imports services.app_state - which install_runtime_isolation()
# itself does, via services.market_events.event_schedule (another
# _EAGER_SINGLETON_MODULES entry) importing it. So by the time this line used
# to run, main.broker was ALREADY constructed against that path; reassigning
# pb_module.DB_PATH here again did nothing for main.broker (dead code) while
# staying live for anything that reads the module attribute fresh instead of
# going through the broker instance - trade_archive.archive_epoch() reads
# pb_module.DB_PATH directly, so it silently pointed at a second, different,
# never-written-to tmp file. No existing test here had ever called
# POST /api/reset with paper: True (the default) to expose the divergence.
rm_module.DB_PATH = _tmp_dir / "risk_state.db"
cp_module.DB_PATH = _tmp_dir / "config_performance.db"
mh_module.DB_PATH = _tmp_dir / "market_history.db"
mc_module.DB_PATH = _tmp_dir / "market_catalog.db"
se_module.DB_PATH = _tmp_dir / "series_evaluator.db"
# main.py captures raw prints/book snapshots through series_watcher on the
# stream + tick paths - redirect it like every other store so a test run
# can never write into the real data/series_watcher.db (CLAUDE.md).
sw_module.DB_PATH = _tmp_dir / "series_watcher.db"
# _process_stream_lifecycle's "determined" handling (2026-08-23) now calls
# into all three of these on every yes/no-result event - previously
# market_analyst_agent was only ever redirected per-test (see
# _isolate_market_analyst_dbs below) because nothing at module scope
# touched it; that stopped being true once test_lifecycle_* below started
# exercising the real "determined" path instead of just observing stats.
cl_module.DB_PATH = _tmp_dir / "candidate_log.db"
# cl_module.record_rejection() routes both its writes through
# capture_writer now (P3 Task 16/17), not cl_module.DB_PATH directly -
# redirect its stores too. Module-level, matching every other redirect in
# this block (not monkeypatch) - later test files still correctly
# override this via their own per-test monkeypatch fixtures, since
# monkeypatch.setattr always sets a fresh value regardless of what was
# there before.
cw_module._STORE_PATHS = {
    "rejected_candidates": cl_module.DB_PATH, "rejection_events": cl_module.DB_PATH,
}
cw_module._buffers = {"rejected_candidates": {}, "rejection_events": []}
cw_module._last_flush_at = {"rejected_candidates": 0.0, "rejection_events": 0.0}
cw_module._dropped_counts = {"rejected_candidates": 0, "rejection_events": 0}
sedge_module.DB_PATH = _tmp_dir / "settlement_edge.db"
maa_db_module.DB_PATH = _tmp_dir / "market_analyst.db"

_tmp_config_path = _tmp_dir / "settings.yaml"
shutil.copy(config_store_module.CONFIG_PATH, _tmp_config_path)
config_store_module.config_store._path = _tmp_config_path
config_store_module.config_store.reload()

import main  # noqa: E402  (must import after the redirects above)


# A14: production dispatch normalizes every WS message at the gateway
# before any handler runs (services/kalshi/websocket.py) - these wrappers
# mirror that, so handler tests exercise the same shapes production
# delivers instead of raw vendor payloads the handlers no longer parse.
def _run_lifecycle(msg):
    from services.kalshi.contracts import lifecycle as lifecycle_contract
    if isinstance(msg, tuple):  # a wrapped call site's trailing comma parses as a 1-tuple
        (msg,) = msg
    asyncio.run(main._process_stream_lifecycle(lifecycle_contract.normalize_lifecycle(msg)))


def _run_stream_ticker(msg):
    from services.kalshi.contracts import ticker as ticker_contract
    if isinstance(msg, tuple):
        (msg,) = msg
    # 2026-09-03 live-incident fix: check_exits/check_pending_fills/
    # position_netting.review is now throttled by a module-level
    # last-run timestamp (ticker_exit_check_min_interval_sec). Reset it
    # before every call so existing tests, written against the pre-
    # throttle behavior of "the block runs every call", keep seeing
    # that - a test of the throttle itself sets this timestamp deliberately
    # instead of going through this helper.
    wsh_module._last_ticker_exit_check_at = 0.0
    asyncio.run(main._process_stream_ticker(ticker_contract.normalize_ticker(msg)))
from fastapi.testclient import TestClient  # noqa: E402
from services.position import account_positions  # noqa: E402
from services.market_watch import discovery_cache  # noqa: E402
from services.confidence_scoring import DEFAULT_WEIGHTS  # noqa: E402

# Bare (non-context-manager) TestClient does not trigger ASGI lifespan, so
# main.trading_loop() never starts - these tests only exercise the HTTP
# layer of the four endpoints below, not the background poll loop.
client = TestClient(main.app)


def _reset_trading_state():
    main.config_store.update({"kalshi_account": {"trading_enabled": False}})
    main.account.trading_enabled = False
    main.account._client = None


@pytest.fixture(autouse=True)
def _reset_trading_gate_state():
    """main.account.trading_enabled/_client and config_store's
    kalshi_account.trading_enabled are real, mutable singleton state that
    only ever gets cleared by an explicit _reset_trading_state() call -
    most tests in this file call it at SETUP (protecting themselves against
    a leak from whichever test ran before), but calling it at setup alone
    does nothing to protect the NEXT test from THIS one. monkeypatch.setattr
    (main.account, "_client", ...) gets auto-restored after a test (back to
    None, since _reset_trading_state() had just set it going in) - but
    main.account.trading_enabled, flipped True by the real POST
    /api/trading/enable code path rather than monkeypatch, is not.

    Real, confirmed leak (root-cause-debugging investigation, 2026-08-27,
    reproducing a ci/woodpecker/push/tests-pytest failure seen under real
    `pytest -n 4` that never reproduced sequentially or single-file):
    test_enable_trading_succeeds_with_correct_phrase_and_connected_account
    ends at `assert main.account.trading_enabled is True` with no reset
    afterward - confirmed by deterministically reproducing the exact
    adversarial order with zero xdist involved: `pytest -p no:xdist tests/
    test_trading_gate.py::test_enable_trading_succeeds_with_correct_phrase_
    and_connected_account tests/test_trading_gate.py::
    test_flatten_all_closes_paper_positions_with_correct_phrase` fails the
    second test with exactly the reported AttributeError: 'NoneType' object
    has no attribute 'get_positions' (main.account._client is None again,
    but main.account.trading_enabled is still True from the leaking test,
    so /api/trading/flatten-all now believes it should also flatten the
    real account and reaches into a client that was never reconnected).

    NOTE: this had first been suspected of
    test_flatten_all_also_flattens_the_real_account_when_trading_enabled
    instead, since it also flips trading_enabled True via the real route -
    but that test already calls _reset_trading_state() as its own last line
    (2026-08-22, commit a8de0347) and does not leak; pairing it directly
    against test_flatten_all_closes_paper_positions_with_correct_phrase
    under -p no:xdist passes cleanly. Verify the specific adversarial pair
    before trusting a stack trace's call site to point at the right test -
    the leak and the crash happen in different tests entirely.

    Same class of fix as _reset_shared_singletons below: an every-test
    autouse fixture, not another manual per-test call that's easy to add to
    a new test's setup and just as easy to forget at its teardown - reset
    unconditionally both before AND after every test in this file so no
    future test that flips trading_enabled can leak into whichever test the
    scheduler happens to run next, regardless of xdist worker ordering."""
    _reset_trading_state()
    yield
    _reset_trading_state()


@pytest.fixture(autouse=True)
def _reset_shared_singletons():
    """main.state["discovery_cache"] and main.config_store are both real,
    mutable singletons redirected/constructed exactly ONCE at this file's
    IMPORT time (see the module-level setup above config_store_module.
    config_store.reload() / `import main`) - unlike mc_module/se_module/etc,
    which most tests that care about clean catalog state already defend
    with their own explicit clear_all() calls at the top of the test body,
    nothing in this file resets these two between tests.

    Two real, confirmed cross-test-order leaks found here (root-cause-
    debugging investigation, 2026-08-27, xdist-parallel test-isolation fix
    for ci/woodpecker/push/tests-pytest failing under real `pytest -n 4`
    while staying green sequentially/single-worker):

    1. discovery_cache's `markets` list genuinely retaining a market
       inserted by an EARLIER-run test's synchronous
       main._refresh_discovery_cache call - confirmed by deterministically
       reproducing the exact adversarial order with no xdist involved:
       `pytest -p no:xdist tests/test_trading_gate.py::
       test_refresh_discovery_cache_includes_rejected_series_when_
       evaluator_disabled tests/test_trading_gate.py::
       test_fetch_markets_regroups_extra_ticker_into_its_series_existing_run`
       fails the second test with a stray "SERBAD-M1" ticker leaking into
       its result the exact same way.
    2. config_store's on-disk YAML genuinely retaining a value POSTed
       through the real /api/market-analyst/full-spectrum/apply route by
       an EARLIER-run test (test_post_market_analyst_full_spectrum_apply_
       end_to_end sets risk.max_daily_loss_pct to 0.3, exactly matching a
       LATER test's own suggested_value, silently tripping
       _full_spectrum_suggestions_from_raw's no-op "already the current
       value" filter) - confirmed the same way: `pytest -p no:xdist tests/
       test_trading_gate.py::test_post_market_analyst_full_spectrum_apply_
       end_to_end tests/test_trading_gate.py::
       test_run_full_spectrum_analysis_succeeds_and_records_analysis` fails
       the second test with 0 suggestions instead of 1.

    Both leaks only ever manifested under pytest-xdist's -n 4 parallel
    scheduling, which does not guarantee tests within one worker process
    run in this file's own definition order the way plain sequential
    pytest happens to (dynamic work-stealing redistributes tests across
    workers as they free up) - by luck, both polluting tests above sit
    LATER in this file than the victim test they were caught poisoning, so
    sequential/single-worker runs never hit the adversarial order. Reset
    here, once, so correctness stops depending on collection/scheduling
    order. Not a production bug - both singletons accumulating state across
    the real app's runtime lifetime is exactly the intended behavior."""
    main.state["discovery_cache"] = {"fetched_at": 0.0, "markets": [], "refreshing": False, "task": None}
    shutil.copy(config_store_module.CONFIG_PATH, _tmp_config_path)
    config_store_module.config_store.reload()


@pytest.fixture(autouse=True)
def _reset_candidate_log_store():
    """cl_module.DB_PATH (candidate_log.db) is a real, persisted, module-
    level store redirected exactly ONCE at this file's import time (see the
    cl_module.DB_PATH / cw_module._STORE_PATHS block above) and shared by
    every test in this file - unlike mc_module, which the test_lifecycle_*
    tests' own _reset_lifecycle_stats() helper already clear_all()s at
    setup, nothing in this file ever cleared candidate_log between tests.

    Real, confirmed leak (root-cause-debugging investigation, 2026-08-27,
    third instance of the same xdist-parallel test-isolation class this
    file's two fixtures above already closed - PR #119 for discovery_cache/
    config_store, PR #125 for main.account.trading_enabled):
    test_lifecycle_settled_resolves_outcome_via_a_fresh_rest_read records a
    rejection for ("TICK-A", "whale_follow", "entry_threshold") and then
    runs the real settled path, whose candidate_log.resolve_from_market_
    results() sets that row's resolved = 1. When
    test_lifecycle_determined_updates_catalog_status_but_does_not_resolve_
    outcome runs AFTER it, its own record_rejection() for the exact same
    (ticker, strategy, gate_name) key hits capture_writer's
    rejected_candidates UPSERT - whose `ON CONFLICT ... DO UPDATE SET ...
    WHERE rejected_candidates.resolved = 0` guard deliberately refuses to
    reopen an already-resolved row (correct production semantics: a later
    rejection of a settled ticker must not un-resolve its outcome). The one
    row stays resolved = 1, and the test's `gate_summary()[...]
    ["resolved_count"] == 0` assertion sees 1 instead. Confirmed
    deterministically with zero xdist involved: `pytest -p no:xdist tests/
    test_trading_gate.py::test_lifecycle_settled_resolves_outcome_via_a_
    fresh_rest_read tests/test_trading_gate.py::test_lifecycle_determined_
    updates_catalog_status_but_does_not_resolve_outcome` fails the second
    test with exactly `1 == 0` on the untouched merge base; the reverse
    order passes, and so does this whole file sequentially - only because
    `determined` happens to be DEFINED before `settled` here, an ordering
    pytest-xdist's work-stealing scheduler does not preserve.

    clear_all() is the right reset (not a raw DELETE): it flushes both
    capture_writer buffers first, since record_rejection() no longer writes
    either table synchronously (P3 Task 16/17) and a row still sitting in a
    buffer would land moments after a bare DELETE and silently un-wipe it.
    Same idiom as the two fixtures above - reset unconditionally both
    before AND after every test, so no current or future test that records
    or resolves a candidate can leak into whichever test the scheduler runs
    next. The per-test _reset_lifecycle_stats() calls are left as-is.

    Scope note: the same settled path also writes market_history
    (record_outcome), settlement_edge (resolve_window) and
    market_analyst_agent (resolve_from_market_results) through their own
    module-level stores; no test in this file currently reads any of those
    across a test boundary (the whole file passes in full reverse
    definition order with only this fixture added - see the commit), so
    they are deliberately not reset here. Add them the same way, with a
    reproduced adversarial pair, if a future test starts asserting on one
    of them."""
    cl_module.clear_all()
    yield
    cl_module.clear_all()


@pytest.fixture(autouse=True)
def _reset_milestone_by_event_cache():
    """main.state["milestone_by_event"] (== services.app_state.state's same
    key - main.py imports `state` directly from services.app_state, not a
    copy) is Task 5's broad, watchlist-independent milestone cache, read by
    this file's _fetch_live_status tests since Task 6 wired it in. The ONLY
    other file that touches this key is tests/test_milestone_scan.py, whose
    own autouse _isolated_state fixture resets it to {} only BEFORE each of
    ITS tests (`state["milestone_by_event"] = {}` then `yield`, no teardown
    reset) - it protects its own tests regardless of what ran before them,
    but does nothing to stop what it leaves behind (several of its tests
    end with non-empty state, e.g. test_scan_builds_the_broad_event_ticker_
    to_milestone_id_map leaves {"EVT-A": "ms-1", "EVT-B": "ms-1"}) from
    reaching whatever runs next in the same process.

    Real, confirmed leak (root-cause-debugging investigation, 2026-08-30,
    fixing a review finding on the Task 6 commit before it ever reached real
    CI): this repo's actual CI entrypoint (scripts/ci-testmon-run.sh:37,53)
    runs `pytest -n 4` on every path (full-suite PR/main runs and
    testmon-scoped branch pushes alike) with no --dist=loadscope/loadfile,
    so pytest-xdist's default --dist=load dynamically assigns individual
    test items to worker processes as they free up - two tests from
    different files can and do land adjacent in the same worker, in which
    "adjacent" means "same Python process, same main.state object,
    sequential." Reproduced deterministically with zero xdist involved:
    `pytest -p no:xdist
    tests/test_milestone_scan.py::test_scan_builds_the_broad_event_ticker_to_milestone_id_map
    tests/test_trading_gate.py::test_fetch_live_status_polls_a_new_event_with_no_cache`
    fails the second test on this untouched merge base with
    `assert [] == ['EVT-A']` - the broad cache's leftover "EVT-A" entry
    (from the first test) makes _fetch_live_status skip the per-event REST
    call the second test asserts DID happen. Every _fetch_live_status test
    in this file that defaults to _market_at's "EVT-A" ticker and asserts
    `fake.milestone_calls == ["EVT-A"]` is equally exposed, not just the one
    used for the repro.

    Same class of fix as _reset_shared_singletons above (reset-before only,
    no yield/teardown needed): resetting to {} before every test in this
    file makes this file's own results independent of whatever any other
    file or worker left behind, regardless of collection/scheduling order.
    Deliberately not resetting AFTER too (unlike _reset_trading_gate_state/
    _reset_candidate_log_store above): test_milestone_scan.py already
    resets-before its own tests unconditionally, so it needs no help from
    this file, and no third file currently reads this key - add a teardown
    reset here too, the same way, if one ever does."""
    main.state["milestone_by_event"] = {}


def test_files_are_actually_redirected_away_from_the_real_repo():
    """Guards the guard: if this ever fails, every other test in this file
    could be touching real project files instead of the temp copies."""
    import services.config.config_store as csm
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


# --- POST /api/trading/flatten-all (2026-08-23 gap-check finding: no ------
# "get flat immediately" path existed at all before this) -------------------

def test_flatten_all_rejects_wrong_confirmation_phrase():
    resp = client.post("/api/trading/flatten-all", json={"confirmation_phrase": "not it"})
    assert resp.status_code == 400
    assert "confirmation phrase" in resp.json()["detail"].lower()


def test_flatten_all_closes_paper_positions_with_correct_phrase():
    main.broker.reset(starting_bankroll=10000.0)
    main.broker.open_position("TICK-A", "yes", size=10, price=0.5, reason="entry")
    main.broker.open_position("TICK-B", "no", size=10, price=0.4, reason="entry")
    assert len(main.broker.positions) == 2

    resp = client.post("/api/trading/flatten-all", json={"confirmation_phrase": "FLATTEN ALL POSITIONS"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["paper_closed"]) == 2
    assert main.broker.positions == {}
    assert body["real_result"] is None  # trading_enabled is false by default


def test_flatten_all_is_a_no_op_with_no_open_positions():
    main.broker.reset(starting_bankroll=10000.0)
    resp = client.post("/api/trading/flatten-all", json={"confirmation_phrase": "FLATTEN ALL POSITIONS"})
    assert resp.status_code == 200
    assert resp.json()["paper_closed"] == []


def test_flatten_all_also_flattens_the_real_account_when_trading_enabled(monkeypatch):
    _reset_trading_state()
    monkeypatch.setattr(main.account, "_client", object())
    client.post("/api/trading/enable", json={"confirmation_phrase": "ENABLE REAL TRADING"})
    assert main.account.trading_enabled is True

    async def fake_flatten_all(account):
        assert account is main.account  # the route passes its own facade in
        return [{"ticker": "REAL-A", "position_fp": 10.0, "order": {"ok": True}, "error": None}]
    # A9: flatten orchestration lives in services/execution.py now, above
    # the vendor adapter - the route composes it with the account facade.
    monkeypatch.setattr(main.execution, "flatten_all_real_positions", fake_flatten_all)

    main.broker.reset(starting_bankroll=10000.0)
    resp = client.post("/api/trading/flatten-all", json={"confirmation_phrase": "FLATTEN ALL POSITIONS"})
    assert resp.status_code == 200
    assert resp.json()["real_result"] == [{"ticker": "REAL-A", "position_fp": 10.0, "order": {"ok": True}, "error": None}]
    _reset_trading_state()


# --- POST /api/trading/close-positions (2026-09-03, off-watchlist entry ---
# bleed remediation: flatten-all is all-or-nothing, and there was no path
# to close only a SUBSET of open positions - e.g. the ones a bug opened
# outside the user's configured watchlist while leaving legitimate ones
# open - without a full flatten. Selective sibling of flatten-all, same
# typed-confirmation-gate shape. ------------------------------------------

def test_close_positions_rejects_wrong_confirmation_phrase():
    resp = client.post("/api/trading/close-positions", json={"tickers": ["TICK-A"], "confirmation_phrase": "not it"})
    assert resp.status_code == 400
    assert "confirmation phrase" in resp.json()["detail"].lower()


def test_close_positions_rejects_empty_ticker_list():
    resp = client.post("/api/trading/close-positions", json={"tickers": [], "confirmation_phrase": "CLOSE SELECTED POSITIONS"})
    assert resp.status_code == 400


def test_close_positions_closes_only_the_specified_tickers():
    main.broker.reset(starting_bankroll=10000.0)
    main.broker.open_position("TICK-A", "yes", size=10, price=0.5, reason="entry")
    main.broker.open_position("TICK-B", "no", size=10, price=0.4, reason="entry")
    main.broker.open_position("TICK-C", "yes", size=10, price=0.3, reason="entry")

    resp = client.post(
        "/api/trading/close-positions",
        json={"tickers": ["TICK-A", "TICK-B"], "confirmation_phrase": "CLOSE SELECTED POSITIONS"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert {t["ticker"] for t in body["closed"]} == {"TICK-A", "TICK-B"}
    assert body["missing"] == []
    assert set(main.broker.positions.keys()) == {"TICK-C"}
    main.broker.reset(starting_bankroll=10000.0)


def test_close_positions_reports_tickers_with_no_open_position_as_missing():
    main.broker.reset(starting_bankroll=10000.0)
    main.broker.open_position("TICK-A", "yes", size=10, price=0.5, reason="entry")

    resp = client.post(
        "/api/trading/close-positions",
        json={"tickers": ["TICK-A", "TICK-NEVER-OPENED"], "confirmation_phrase": "CLOSE SELECTED POSITIONS"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert {t["ticker"] for t in body["closed"]} == {"TICK-A"}
    assert body["missing"] == ["TICK-NEVER-OPENED"]
    main.broker.reset(starting_bankroll=10000.0)


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
    main.bump_generation()

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
    main.bump_generation()

    resp = client.get("/api/state")
    assert resp.status_code == 200
    body = resp.json()
    assert body["market_titles"]["REAL-POS"]["title"] == "A Real Position Title"
    assert body["market_titles"]["REAL-FILL"]["title"] == "A Real Fill Title"


def test_relevant_tickers_reads_the_canonical_ticker_for_fills(monkeypatch):
    # A14: presentation reads the canonical "ticker" key only. Both real
    # sources guarantee it: the REST Fill schema REQUIRES ticker AND
    # market_ticker, documenting market_ticker as "legacy field name, same
    # as ticker" (docs/kalshi/get-fills.md), and a WS fill gets the
    # canonical alias from services/kalshi/contracts/fill.py at the
    # gateway before any handler stores it. The old market_ticker
    # fallback here guarded a ticker-less shape neither surface can
    # produce - that alias knowledge now lives only at the boundary.
    monkeypatch.setitem(main.state, "markets", [])
    monkeypatch.setitem(main.state, "signal_feed", [])
    monkeypatch.setitem(main.state, "decision_feed", [])
    from services.kalshi.contracts import fill as fill_contract
    ws_fill = fill_contract.normalize_fill({"market_ticker": "REAL-WS-FILL", "side": "yes"})
    monkeypatch.setitem(main.state, "account", {
        "connected": True, "trading_enabled": False, "error": None,
        "positions": {"market_positions": []},
        "fills": {"fills": [ws_fill, {"ticker": "REAL-REST-FILL", "market_ticker": "REAL-REST-FILL", "side": "yes"}]},
    })
    assert "REAL-WS-FILL" in main._relevant_tickers()
    assert "REAL-REST-FILL" in main._relevant_tickers()


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
    # Isolated from whatever config/settings.yaml's own kalshi.categories
    # currently says (2026-09-03 fix: this test broke when that list was
    # narrowed to Crypto/Commodities for the off-watchlist entry bleed
    # remediation) - _scan_catalog_batch filters all_series to cfg["kalshi"]
    # ["categories"] membership (catalog_scan.py), and this test's own
    # fixture series are "Sports", so the category this test cares about
    # must be pinned here, not inherited from the live config file.
    cfg = {**config_store_module.config_store.get(), "kalshi": {
        **config_store_module.config_store.get()["kalshi"], "live_markets_only": True, "categories": ["Sports"],
    }}

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
        "occurrence_datetime": _iso(datetime.now(timezone.utc) - timedelta(minutes=5)), "status": "active",
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


# --- close_positions_first (2026-08-27 direct request: don't abandon open --
# positions' unrealized P&L into the archive uncredited) --------------------

def test_reset_close_positions_first_closes_positions_and_archive_records_the_real_close():
    from services.reset import trade_archive as ta_module

    main.broker.reset(starting_bankroll=10000.0)
    main.broker.open_position("TICK-A", "yes", size=10, price=0.5, reason="entry")
    main.broker.open_position("TICK-B", "no", size=10, price=0.4, reason="entry")
    main.state["latest_prices"] = {"TICK-A": 0.6, "TICK-B": 0.3}
    assert len(main.broker.positions) == 2

    resp = client.post("/api/reset", json={"paper": True, "close_positions_first": True})
    assert resp.status_code == 200
    body = resp.json()
    assert main.broker.positions == {}

    close_entry = next(c for c in body["cleared"] if isinstance(c, dict)
                        and c.get("domain") == "close_positions_first")
    assert close_entry["closed"] == 2

    archive_entry = next(c for c in body["cleared"] if isinstance(c, dict) and c.get("domain") == "archive")
    epoch_id = archive_entry["epoch"]["epoch_id"]
    archived = ta_module.epoch_trades(epoch_id)
    # Both entries plus both closes were archived - the closes are real
    # `close:`-reason trades, not orphaned archived_positions rows.
    assert len(archived) == 4
    close_reasons = [r["reason"] for r in archived if r["ticker"] in ("TICK-A", "TICK-B")
                      and r["reason"].startswith("closed:")]
    assert len(close_reasons) == 2


def test_reset_close_positions_first_is_a_no_op_without_open_positions():
    main.broker.reset(starting_bankroll=10000.0)
    resp = client.post("/api/reset", json={"paper": True, "close_positions_first": True})
    assert resp.status_code == 200
    close_entry = next(c for c in resp.json()["cleared"] if isinstance(c, dict)
                        and c.get("domain") == "close_positions_first")
    assert close_entry["closed"] == 0


def test_reset_close_positions_first_is_ignored_for_a_ranged_reset():
    # A ranged reset only ever purges the trades table (closed history) -
    # positions/bankroll are current live state, never touched by range
    # scoping (see ResetBody.range_start's own docstring). Combining the
    # flag with a range must not surprise-close live positions that the
    # range-scoped reset itself would never have touched.
    main.broker.reset(starting_bankroll=10000.0)
    main.broker.open_position("TICK-C", "yes", size=5, price=0.5, reason="entry")

    resp = client.post("/api/reset", json={
        "paper": True, "close_positions_first": True, "range_start": time.time() - 3600,
    })
    assert resp.status_code == 200
    assert "TICK-C" in main.broker.positions
    cleared = resp.json()["cleared"]
    assert not any(isinstance(c, dict) and c.get("domain") == "close_positions_first" for c in cleared)


def test_reset_preview_reports_close_positions_first_count():
    main.broker.reset(starting_bankroll=10000.0)
    main.broker.open_position("TICK-D", "yes", size=5, price=0.5, reason="entry")

    resp = client.get("/api/reset/preview", params={"paper": True, "close_positions_first": True})
    assert resp.status_code == 200
    assert resp.json()["counts"]["close_positions_first"] == 1


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

    async def get_markets_by_tickers(self, tickers):
        # Batched (2026-08-16) - _cached_market_fetch's real interface now.
        return {t: self.markets_by_ticker[t] for t in tickers if t in self.markets_by_ticker}

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


# --- markets_watchlist_mode (2026-08-17 direct request: "give the option
# to merge with discovery or make it exclusive to the manual list") -------

def test_fetch_markets_merge_mode_is_the_unchanged_default():
    """No markets_watchlist_mode key at all, and the field set explicitly
    to "merge", must both behave exactly like before this feature existed -
    pinned plus whatever discovery contributes."""
    main.state["discovery_cache"]["markets"] = [
        {"ticker": "DISCOVERED-M1", "event_ticker": "DISCOVERED-EVT1"},
    ]
    fake = _FakePinnedMarketClient({
        "PINNED-M1": {"ticker": "PINNED-M1", "event_ticker": "PINNED-EVT1"},
    })
    for mode_cfg in (
        {"kalshi": {"markets_watchlist": ["PINNED-M1"], "watchlist_size": 50, "max_children_per_parent": None}},
        {"kalshi": {"markets_watchlist": ["PINNED-M1"], "watchlist_size": 50,
                    "max_children_per_parent": None, "markets_watchlist_mode": "merge"}},
    ):
        markets = asyncio.run(main._fetch_markets(fake, mode_cfg))
        tickers = {m["ticker"] for m in markets}
        assert tickers == {"PINNED-M1", "DISCOVERED-M1"}


def test_fetch_markets_exclusive_mode_skips_discovery_entirely():
    """The actual feature: exclusive mode must produce ONLY the pinned
    list, even though discovery_cache genuinely has other markets sitting
    in it ready to contribute - proving discovery is skipped, not just
    coincidentally empty."""
    main.state["discovery_cache"]["markets"] = [
        {"ticker": "DISCOVERED-M1", "event_ticker": "DISCOVERED-EVT1"},
    ]
    fake = _FakePinnedMarketClient({
        "PINNED-M1": {"ticker": "PINNED-M1", "event_ticker": "PINNED-EVT1"},
    })
    cfg = {"kalshi": {"markets_watchlist": ["PINNED-M1"], "watchlist_size": 50,
                      "max_children_per_parent": None, "markets_watchlist_mode": "exclusive"}}
    markets = asyncio.run(main._fetch_markets(fake, cfg))
    tickers = {m["ticker"] for m in markets}
    assert tickers == {"PINNED-M1"}
    assert "DISCOVERED-M1" not in tickers


def test_fetch_markets_exclusive_mode_with_empty_pin_list_is_an_empty_watchlist():
    """No fallback to discovery when the pin list is empty in exclusive
    mode - an empty watchlist is the honest answer, not a silent revert to
    automatic discovery."""
    main.state["discovery_cache"]["markets"] = [
        {"ticker": "DISCOVERED-M1", "event_ticker": "DISCOVERED-EVT1"},
    ]
    fake = _FakePinnedMarketClient({})
    cfg = {"kalshi": {"markets_watchlist": [], "watchlist_size": 50,
                      "max_children_per_parent": None, "markets_watchlist_mode": "exclusive"}}
    markets = asyncio.run(main._fetch_markets(fake, cfg))
    assert markets == []


def test_fetch_markets_exclusive_mode_also_skips_the_live_only_rest_calls():
    """Exclusive must short-circuit BEFORE the live_markets_only branch,
    not just filter its output - that branch makes real REST hydration
    calls (_FakeLiveClient raises if any discovery method is actually
    called), so reaching it at all in exclusive mode would be wasted work
    at best and a live incident at worst."""
    class _ExplodingIfCalledClient(_FakePinnedMarketClient):
        async def get_candidate_markets(self, min_volume, series_tickers):
            raise AssertionError("discovery must not run in exclusive mode")

    fake = _ExplodingIfCalledClient({
        "PINNED-M1": {"ticker": "PINNED-M1", "event_ticker": "PINNED-EVT1"},
    })
    cfg = {"kalshi": {"markets_watchlist": ["PINNED-M1"], "watchlist_size": 50,
                      "max_children_per_parent": None, "markets_watchlist_mode": "exclusive",
                      "live_markets_only": True}}
    markets = asyncio.run(main._fetch_markets(fake, cfg))
    assert {m["ticker"] for m in markets} == {"PINNED-M1"}


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
        "occurrence_datetime": _iso(now_ts + timedelta(minutes=-5)), "status": "active",
    }])
    mc_module.upsert_markets("SERBAD", "Sports", [{
        "ticker": "SERBAD-M1", "event_ticker": "SERBAD-EVT1", "volume_24h_fp": "1000",
        "occurrence_datetime": _iso(now_ts + timedelta(minutes=-5)), "status": "active",
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
        "occurrence_datetime": _iso(now_ts + timedelta(minutes=-5)), "status": "active",
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


def test_fetch_markets_series_pin_overrides_live_markets_only():
    # Direct request (2026-08-16): "the market watch list should act as
    # that override, that's what the pinned list is for" - KXBTC15M can
    # never pass live_markets_only's milestone-based live-status check by
    # design (no real-world broadcast data for a pure price-crossing
    # market). A series-level entry in markets_watchlist must bring its
    # currently-open market in regardless, the same way a literal ticker
    # pin already bypasses every other automatic-discovery-only filter.
    # occurrence_datetime is set outside candidates_in_window's own 6h
    # lookback window (but within upsert_markets' own 1-day write-time
    # horizon), so the ordinary live-only discovery path would never
    # surface this row on its own - only the pin should.
    main.state["live_status_cache"].clear()
    mc_module.clear_all()
    now_ts = datetime.now(timezone.utc)
    mc_module.upsert_markets("SERBTC", "Crypto", [{
        "ticker": "SERBTC-M1", "event_ticker": "SERBTC-EVT1", "volume_24h_fp": "0",
        "occurrence_datetime": _iso(now_ts + timedelta(hours=-12)),
        "close_time": _iso(now_ts + timedelta(minutes=10)), "status": "active",
    }])
    cfg = _cfg_live_only(markets_watchlist=["SERBTC"])
    fake = _FakeHydrationClient(hydrated_markets={}, widget_status="live")
    markets = asyncio.run(main._fetch_markets(fake, cfg))
    assert "SERBTC-M1" in {m["ticker"] for m in markets}


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


def test_advisory_status_includes_evidence_provenance_block(monkeypatch):
    _reset_advisory_state()
    monkeypatch.setattr(
        main.advisory_routes.evidence_provenance, "current_completeness_state",
        lambda: {"degraded": False, "defects": [], "checked_at": 0.0},
    )

    resp = client.get("/api/advisory/status")

    assert resp.status_code == 200
    assert resp.json()["evidence_provenance"] == {"degraded": False, "defects": [], "checked_at": 0.0}


def test_advisory_recommendations_includes_evidence_provenance_when_disabled(monkeypatch):
    _reset_advisory_state()
    monkeypatch.setattr(
        main.advisory_routes.evidence_provenance, "current_completeness_state",
        lambda: {"degraded": True, "defects": [{"component": "index_feed"}], "checked_at": 1.0},
    )

    resp = client.get("/api/advisory/recommendations")

    assert resp.json()["evidence_provenance"]["degraded"] is True


def test_advisory_recommendations_includes_evidence_provenance_when_enabled(monkeypatch):
    _reset_advisory_state()
    main.config_store.update({"advisory": {"enabled": True}})
    monkeypatch.setattr(
        main.advisory_routes.evidence_provenance, "current_completeness_state",
        lambda: {"degraded": True, "defects": [], "checked_at": 2.0},
    )

    resp = client.get("/api/advisory/recommendations")

    assert resp.status_code == 200
    assert resp.json()["evidence_provenance"]["checked_at"] == 2.0


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
    # risk.*/advisory.*/etc. changes never alter the fingerprinted
    # strategy.* subset, so fingerprint_before always equals
    # fingerprint_after for them - correctly no effect to report, not a
    # bug in the logging.
    _reset_advisory_state()
    resp = client.post("/api/config", json={"patch": {"risk": {"max_daily_loss_pct": 0.33}}})
    assert resp.status_code == 200
    changes = client.get("/api/advisory/applied-changes").json()["changes"]
    match = next(c for c in changes if c["config_path"] == "risk.max_daily_loss_pct" and c["new_value"] == 0.33)
    assert match["effect"] is None


# --- Whale-signal calibration manual apply -----------------------------------
# POST /api/confidence-calibration/apply (2026-08-17, direct report: "it
# doesn't auto apply. doesn't update its values on the frontend, doesn't
# actually refine itself over time. just does nothing"). Before this route,
# suggested_weights could only ever reach the live config via the auto-apply
# toggle - gated behind a typed confirmation phrase and only even checked
# once per confidence_calibration.snapshot_interval_sec. Same manual-apply
# shape as the advisory apply route above: always available regardless of
# auto_apply_enabled, recomputes the report fresh.

def _reset_calibration_state():
    main.config_store.update({
        "confidence_calibration": {"enabled": False, "min_resolved_signals": 30, "auto_apply_enabled": False},
        "whale_confidence_weights": dict(DEFAULT_WEIGHTS),
    })


def _seed_calibration_signals(monkeypatch, tmp_path, n_per_bucket=10, discriminate=True):
    """Writes real resolved signals through signal_log (not synthetic dicts -
    the route reads signal_log.resolved_signals_with_factors() directly, so
    a dict-level fixture like tests/test_confidence_calibration.py's own
    _discriminating_dataset can't exercise the HTTP layer end to end).
    depth_factor cleanly predicts correctness (low third wrong, high third
    right) when discriminate=True, mirroring that same module's fixture
    shape; every other factor held constant so it can never discriminate -
    when discriminate=False, depth_factor is held constant too, so nothing
    in the report ever suggests a change."""
    import services.signal_log as signal_log_module
    monkeypatch.setattr(signal_log_module, "DB_PATH", tmp_path / "signal_log.db")
    now = time.time() - 1000
    for i in range(n_per_bucket):
        depth = 0.5 if not discriminate else 0.1 + i * 0.01
        signal_log_module.log_signal(
            f"TICK-LOW-{i}", "yes", 1000, 0.5, "real-provider", seen_at=now,
            factors={"depth_factor": depth, "unusualness_factor": 0.5, "proximity_factor": 0.5,
                     "context_factor": 0.5, "agreement_factor": 0.5, "cluster_factor": 0.5,
                     "trend_factor": 0.5, "analyst_factor": 0.5, "block_trade_factor": 0.5},
        )
    for i in range(n_per_bucket):
        depth = 0.5 if not discriminate else 0.9 + i * 0.01
        signal_log_module.log_signal(
            f"TICK-HIGH-{i}", "yes", 1000, 0.5, "real-provider", seen_at=now,
            factors={"depth_factor": depth, "unusualness_factor": 0.5, "proximity_factor": 0.5,
                     "context_factor": 0.5, "agreement_factor": 0.5, "cluster_factor": 0.5,
                     "trend_factor": 0.5, "analyst_factor": 0.5, "block_trade_factor": 0.5},
        )
    batch = signal_log_module.unresolved_batch(limit=n_per_bucket * 2, older_than_sec=0)
    for row in batch:
        correct = discriminate and row["ticker"].startswith("TICK-HIGH")
        signal_log_module.mark_resolved(row["id"], correct=correct)


def test_calibration_apply_rejected_when_disabled():
    _reset_calibration_state()
    resp = client.post("/api/confidence-calibration/apply")
    assert resp.status_code == 400
    assert "disabled" in resp.json()["detail"]


def test_calibration_apply_rejected_when_not_enough_resolved_signals(tmp_path, monkeypatch):
    _reset_calibration_state()
    main.config_store.update({"confidence_calibration": {"enabled": True, "min_resolved_signals": 30}})
    _seed_calibration_signals(monkeypatch, tmp_path, n_per_bucket=5)  # 10 rows, below the 30 floor
    resp = client.post("/api/confidence-calibration/apply")
    assert resp.status_code == 400
    assert "10/30" in resp.json()["detail"]


def test_calibration_apply_rejected_when_nothing_discriminates(tmp_path, monkeypatch):
    _reset_calibration_state()
    main.config_store.update({"confidence_calibration": {"enabled": True, "min_resolved_signals": 20}})
    _seed_calibration_signals(monkeypatch, tmp_path, n_per_bucket=10, discriminate=False)
    resp = client.post("/api/confidence-calibration/apply")
    assert resp.status_code == 400
    assert "nothing to apply" in resp.json()["detail"]
    assert main.config_store.get()["whale_confidence_weights"] == dict(DEFAULT_WEIGHTS)


def test_calibration_apply_end_to_end_updates_config_and_logs_change(tmp_path, monkeypatch):
    _reset_calibration_state()
    main.config_store.update({"confidence_calibration": {"enabled": True, "min_resolved_signals": 20}})
    _seed_calibration_signals(monkeypatch, tmp_path, n_per_bucket=10, discriminate=True)

    report_resp = client.get("/api/confidence-calibration/report")
    assert report_resp.status_code == 200
    suggested = report_resp.json()["report"]["suggested_weights"]
    assert suggested is not None

    apply_resp = client.post("/api/confidence-calibration/apply")
    assert apply_resp.status_code == 200
    body = apply_resp.json()
    assert body["applied"] is True
    new_weights = main.config_store.get()["whale_confidence_weights"]
    assert new_weights == body["new_weights"]
    # depth_factor is the only real signal in this fixture - it should have
    # moved off DEFAULT_WEIGHTS' own value, same as the report's own suggestion.
    assert new_weights["depth_factor"] != DEFAULT_WEIGHTS["depth_factor"]

    changes = client.get("/api/advisory/applied-changes").json()["changes"]
    match = next(c for c in changes if c["config_path"] == "whale_confidence_weights")
    assert match["source"] == "calibration-manual"
    assert match["auto_applied"] is False


def test_calibration_apply_twice_against_unchanged_data_does_not_crash(tmp_path, monkeypatch):
    # blended_weights_for_auto_apply is not a fixed point: suggested_weights
    # is recomputed fresh from the raw signal data each call (unaware of
    # what config it's blending into), while current_weights on the second
    # call is already the first call's renormalized output - re-blending a
    # renormalized value against a raw one shifts the total again rather
    # than converging, so a second apply against literally unchanged data
    # legitimately finds a new (if small) delta rather than "nothing to
    # apply." Documenting the real behavior here rather than assuming
    # idempotency the underlying blend was never designed to have -
    # services/whale_calibration/confidence_calibration.py's own test_blended_weights_* suite
    # covers that function's math in isolation; this just confirms the
    # route survives being called repeatedly, same as a human clicking
    # twice would.
    _reset_calibration_state()
    main.config_store.update({"confidence_calibration": {"enabled": True, "min_resolved_signals": 20}})
    _seed_calibration_signals(monkeypatch, tmp_path, n_per_bucket=10, discriminate=True)

    first = client.post("/api/confidence-calibration/apply")
    assert first.status_code == 200

    second = client.post("/api/confidence-calibration/apply")
    assert second.status_code in (200, 400)
    if second.status_code == 400:
        assert "nothing to apply" in second.json()["detail"]


# --- market_history debug endpoint --------------------------------------
# Backend-only for now (docs/advisory-engine-plan.md §9-adjacent, direct
# request 2026-08-08) - no UI panel yet, but a real endpoint, so smoke-test
# it the same as everything else rather than leaving it unverified.

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
    the returned status looks right. get_live_datas (batched, 2026-08-16)
    replaces the old per-milestone get_live_data - returns milestone_id ->
    {"details": {...}} flat, matching KalshiClient.get_live_datas' own real
    shape (not get_live_data()'s single-call {"live_data": {...}} wrapper).

    get_events(with_milestones=True) (kalshi-category-data-completeness
    Task 10) replaces the old per-event get_milestones_for_event - stands
    in for the real KalshiPublicGateway.get_events, which joins Kalshi's
    top-level `milestones` array onto each returned event.
    milestone_calls keeps recording the tickers actually fetched this tick
    (now via one batched call instead of N individual ones) so every
    pre-existing "was this event (re)polled" assertion below stays
    unchanged."""

    def __init__(self, widget_status="live", has_milestone=True, live_data_fails=False):
        self.widget_status = widget_status
        self.has_milestone = has_milestone
        self.live_data_fails = live_data_fails
        self.milestone_calls = []
        self.live_datas_calls = []  # list of milestone_id batches requested

    async def get_events(self, event_tickers, with_milestones=False):
        self.milestone_calls.extend(event_tickers)
        ms_list = [{"id": "ms1", "type": "game"}] if self.has_milestone else []
        return [{"event_ticker": et, "milestones": ms_list} for et in event_tickers]

    async def get_live_datas(self, milestone_ids):
        self.live_datas_calls.append(list(milestone_ids))
        if self.live_data_fails:
            return {mid: {"type": "game", "details": {}} for mid in milestone_ids}  # no widget_status - a real, seen shape
        return {mid: {"type": "game", "details": {"widget_status": self.widget_status}} for mid in milestone_ids}


# --- _fetch_live_status routes live-data through milestone_live_data.extract()
# (Task 6, kalshi-category-data-completeness) - proves the wiring actually
# dispatches on milestone `type` now, not still a raw details.get(
# "widget_status") read (which would return "live" for the fixture below,
# since the raw key IS present in it). ------------------------------------

def test_fetch_live_status_returns_none_for_a_settlement_input_type():
    # company_report is one of D2's named no-op types (Task 5,
    # milestone_live_data.py's _EXTRACTORS) - an index/report series, not a
    # real-world resolution event with a genuine live/finished state.
    main.state["live_status_cache"].clear()

    class _FakeReportClient:
        async def get_events(self, event_tickers, with_milestones=False):
            return [{"event_ticker": et, "milestones": [{"id": "ms1", "type": "company_report"}]} for et in event_tickers]

        async def get_live_datas(self, milestone_ids):
            return {mid: {"type": "company_report", "details": {"widget_status": "live"}} for mid in milestone_ids}

    markets = [_market_at(offset_sec=-300)]
    result = asyncio.run(main._fetch_live_status(_FakeReportClient(), markets))
    # status=None (not "live") - explicit negative confirmation, not the
    # bare-absent-key "no milestone at all" case (see
    # services/app_state.py:192's own "live_status" dict contract, which
    # already lists None alongside "live"/"finished"/"none" as a value every
    # known reader treats as "not live", e.g. whale_simulator.py's `== "live"`).
    assert result == {"EVT-A": None}
    assert main.state["live_status_cache"]["EVT-A"]["source"] == "no_live_status_type"


def test_fetch_live_status_polls_a_new_event_with_no_cache():
    main.state["live_status_cache"].clear()
    fake = _FakeLiveClient(widget_status="live")
    markets = [_market_at(offset_sec=-300)]  # started 5 min ago
    result = asyncio.run(main._fetch_live_status(fake, markets))
    assert result == {"EVT-A": "live"}
    assert fake.milestone_calls == ["EVT-A"]
    assert main.state["live_status_cache"]["EVT-A"]["status"] == "live"


def test_fetch_live_status_excludes_events_starting_far_in_the_future():
    # Lookahead widened 1h -> 12h on 2026-08-17: 30 Sports events sat on the
    # watchlist while live_status held ONE entry, because a game scheduled
    # for 13:35 is ~7.5h out at 06:00 and fell outside the old bound, so no
    # score/period/clock was ever captured for any of them. 3h is now
    # deliberately INSIDE the window; the exclusion is tested past 12h.
    main.state["live_status_cache"].clear()
    fake = _FakeLiveClient()
    markets = [_market_at(offset_sec=14 * 3600)]  # starts in 14h - past the 12h lookahead
    result = asyncio.run(main._fetch_live_status(fake, markets))
    assert result == {}
    assert fake.milestone_calls == []  # never even attempted a poll


def test_fetch_live_status_now_tracks_a_game_scheduled_later_today():
    """The case the widening exists for - a game hours away must be tracked
    so its live state is captured once it starts."""
    main.state["live_status_cache"].clear()
    fake = _FakeLiveClient(widget_status="none")
    markets = [_market_at(offset_sec=7 * 3600)]
    asyncio.run(main._fetch_live_status(fake, markets))
    assert fake.milestone_calls == ["EVT-A"]


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
    # Lookback widened 6h -> 8h alongside the lookahead change, so a long
    # game (extra innings, rain delay) stays tracked to its real end.
    main.state["live_status_cache"].clear()
    fake = _FakeLiveClient()
    markets = [_market_at(offset_sec=-9 * 3600)]
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


def test_fetch_live_status_caps_poll_batch_size_per_tick():
    # Real live incident (2026-08-15 tick_duration investigation): to_poll
    # had no cap at all, so a large fraction of a big in-window candidate
    # pool becoming simultaneously due fired dozens-to-hundreds of
    # concurrent calls in a single tick, saturating the shared Kalshi rate
    # limiter and starving every OTHER read call sharing it - confirmed
    # live: _fetch_account_snapshot stalled to 32-33s on the same ticks
    # despite its own independent 20s interval cache working correctly.
    main.state["live_status_cache"].clear()
    fake = _FakeLiveClient(widget_status="live")
    n = main._LIVE_STATUS_MAX_POLL_PER_TICK + 5
    markets = [_market_at(offset_sec=-300, event_ticker=f"EVT-{i}") for i in range(n)]
    result = asyncio.run(main._fetch_live_status(fake, markets))
    assert len(set(fake.milestone_calls)) == main._LIVE_STATUS_MAX_POLL_PER_TICK
    assert len(result) == main._LIVE_STATUS_MAX_POLL_PER_TICK


def test_fetch_live_status_batch_prioritizes_oldest_checked_first():
    main.state["live_status_cache"].clear()
    fake = _FakeLiveClient(widget_status="live")
    n = main._LIVE_STATUS_MAX_POLL_PER_TICK + 3
    now = time.time()
    markets = []
    for i in range(n):
        et = f"EVT-{i}"
        markets.append(_market_at(offset_sec=-300, event_ticker=et))
        # Staggered ages, all past the repoll threshold (all due) - EVT-0 is
        # the most-overdue, EVT-(n-1) the least-overdue of the bunch.
        main.state["live_status_cache"][et] = {
            "status": "live", "checked_at": now - main._LIVE_STATUS_REPOLL_SEC - (n - i),
        }
    asyncio.run(main._fetch_live_status(fake, markets))
    polled = set(fake.milestone_calls)
    assert len(polled) == main._LIVE_STATUS_MAX_POLL_PER_TICK
    expected_polled = {f"EVT-{i}" for i in range(main._LIVE_STATUS_MAX_POLL_PER_TICK)}
    assert polled == expected_polled


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


def test_fetch_live_status_political_race_runoff_does_not_fall_through_to_schedule_live():
    # Regression for a real bug (kalshi-category-data-completeness Task 8,
    # fix-round 1 -> re-review): a "Runoff" political_race extractor result
    # of Python None is FALSY, so it was silently skipped by _fetch_live_
    # status's own `if status: confirmed[et] = status` check (a few lines
    # above the schedule fallback) and fell through to the schedule
    # fallback instead - which, for any event past its scheduled
    # occurrence_datetime (true here, started 5 min ago), re-derives
    # "live" independent of what the extractor actually said, silently
    # reproducing the exact is_live entry-gate-bypass bug the fix was
    # meant to close. milestone_live_data.py's real _political_race
    # extractor maps "Runoff" -> the "none" STRING specifically so this
    # can't happen (a truthy value routes straight into confirmed[et] and
    # never reaches the fallback) - this test proves that against the
    # real _fetch_live_status/milestone_live_data.extract() wiring, not
    # just the extractor in isolation.
    main.state["live_status_cache"].clear()

    class _FakePoliticalRaceClient:
        async def get_events(self, event_tickers, with_milestones=False):
            return [{"event_ticker": et, "milestones": [{"id": "ms1", "type": "political_race"}]} for et in event_tickers]

        async def get_live_datas(self, milestone_ids):
            return {
                mid: {
                    "type": "political_race",
                    "details": {
                        "race_call_status": "Runoff",
                        "tabulation_status": "Vote Certified",
                        "winner": "",
                    },
                }
                for mid in milestone_ids
            }

    markets = [_market_at(offset_sec=-300)]  # started 5 min ago -> past occurrence, schedule fallback would say "live"
    result = asyncio.run(main._fetch_live_status(_FakePoliticalRaceClient(), markets))
    assert result == {"EVT-A": "none"}
    assert main.state["live_status_cache"]["EVT-A"]["source"] == "milestone"


# --- _fetch_live_status: broad milestone cache (services/market_watch/
# milestone_scan.py) consulted before the per-event REST call --------------

def test_fetch_live_status_uses_broad_milestone_cache_and_skips_the_per_event_call():
    main.state["live_status_cache"].clear()
    main.state["milestone_by_event"] = {"EVT-A": "ms-from-bulk-scan"}
    fake = _FakeLiveClient(widget_status="live", has_milestone=True)
    markets = [_market_at(offset_sec=-300)]
    result = asyncio.run(main._fetch_live_status(fake, markets))
    assert result == {"EVT-A": "live"}
    assert fake.milestone_calls == []  # broad cache already had it - no per-event REST call needed
    assert fake.live_datas_calls == [["ms-from-bulk-scan"]]


def test_fetch_live_status_falls_back_to_per_event_call_when_broad_cache_misses():
    main.state["live_status_cache"].clear()
    main.state["milestone_by_event"] = {}  # cold cache - milestone_scan hasn't reached this event yet
    fake = _FakeLiveClient(widget_status="live", has_milestone=True)
    markets = [_market_at(offset_sec=-300)]
    result = asyncio.run(main._fetch_live_status(fake, markets))
    assert result == {"EVT-A": "live"}
    assert fake.milestone_calls == ["EVT-A"]  # unchanged, pre-existing behavior


def test_fetch_live_status_broad_cache_mixed_hit_and_miss_in_one_tick():
    # Per-event granularity within a single tick's to_poll batch: one event
    # already covered by the broad scan, the other not yet - only the miss
    # should take the per-event REST fallback, and both ids still need to
    # reach the single downstream batched get_live_datas call together.
    main.state["live_status_cache"].clear()
    main.state["milestone_by_event"] = {"EVT-A": "ms-from-bulk-scan"}  # A hits, B doesn't
    fake = _FakeLiveClient(widget_status="live", has_milestone=True)
    markets = [
        _market_at(offset_sec=-300, event_ticker="EVT-A"),
        _market_at(offset_sec=-300, event_ticker="EVT-B"),
    ]
    result = asyncio.run(main._fetch_live_status(fake, markets))
    assert result == {"EVT-A": "live", "EVT-B": "live"}
    assert fake.milestone_calls == ["EVT-B"]  # only the miss went through the per-event REST call
    assert fake.live_datas_calls == [["ms-from-bulk-scan", "ms1"]]  # cached + freshly-fetched ids, batched together


def test_fetch_live_status_broad_cache_hit_also_routes_through_the_extractor():
    # test_fetch_live_status_returns_none_for_a_settlement_input_type above
    # only exercises the needs_fetch path (a fresh get_events(with_milestones=
    # True) call always returns a full milestone dict per event,
    # `ms["type"]` included). An
    # event resolved via the broad milestone_by_event cache instead
    # (milestone_scan.py) never has that dict at all - the cache only maps
    # event_ticker -> milestone id - so this proves the wiring's `ld["type"]`
    # sourcing (not a separately-tracked `ms["type"]`) closes the same gap
    # for THIS path too, not just the one the brief's own test happens to
    # cover.
    main.state["live_status_cache"].clear()
    main.state["milestone_by_event"] = {"EVT-A": "ms-from-bulk-scan"}

    class _FakeBroadCacheReportClient:
        async def get_events(self, event_tickers, with_milestones=False):
            raise AssertionError("broad cache hit - the batched get_events fallback call must not happen")

        async def get_live_datas(self, milestone_ids):
            return {mid: {"type": "company_report", "details": {"widget_status": "live"}} for mid in milestone_ids}

    markets = [_market_at(offset_sec=-300)]
    result = asyncio.run(main._fetch_live_status(_FakeBroadCacheReportClient(), markets))
    # Not "live" via extract(), and not "live" via the schedule fallback
    # either - status=None, same explicit-negative-confirmation contract as
    # the needs_fetch path's equivalent test above.
    assert result == {"EVT-A": None}
    assert main.state["live_status_cache"]["EVT-A"]["source"] == "no_live_status_type"


# --- Task 10 (kalshi-category-data-completeness): get_events(with_milestones
# =True) replaces get_milestones_for_event entirely - _fetch_live_status must
# never call it, even indirectly. -------------------------------------------

def test_fetch_live_status_never_calls_get_milestones_for_event():
    # Deliberately has no get_milestones_for_event at all - if
    # _fetch_live_status still called it (directly, or via any fallback
    # path) this would raise AttributeError, proving the old per-event
    # endpoint is genuinely gone from this call site, not just unused by
    # coincidence in the other fixtures above.
    main.state["live_status_cache"].clear()
    main.state["milestone_by_event"] = {}  # force the needs_fetch path, not the broad cache

    class _FakeClientNoMilestoneEndpoint:
        def __init__(self):
            self.get_events_calls = []

        async def get_events(self, event_tickers, with_milestones=False):
            self.get_events_calls.append((list(event_tickers), with_milestones))
            return [{"event_ticker": et, "milestones": [{"id": "ms1", "type": "game"}]} for et in event_tickers]

        async def get_live_datas(self, milestone_ids):
            return {mid: {"type": "game", "details": {"widget_status": "live"}} for mid in milestone_ids}

    fake = _FakeClientNoMilestoneEndpoint()
    markets = [_market_at(offset_sec=-300)]
    result = asyncio.run(main._fetch_live_status(fake, markets))
    assert result == {"EVT-A": "live"}
    assert fake.get_events_calls == [(["EVT-A"], True)]


def test_fetch_live_status_degrades_gracefully_when_get_events_raises():
    # Regression (fix-round 1, task review): the old per-event
    # asyncio.gather(..., return_exceptions=True) meant a milestone-fetch
    # failure could never raise out of _fetch_live_status at all - one
    # event's REST failure just meant that event got no milestone this
    # tick. The new single batched get_events(needs_fetch,
    # with_milestones=True) call has no such isolation by default, and
    # (unlike propagate_milestone_winners, which already wraps its own
    # equivalent call in a broad try/except) this function had none -
    # an unwrapped failure would propagate through main.py's own
    # asyncio.gather (no return_exceptions there either) and be caught
    # only by trading_loop's per-TICK try/except, aborting event_titles/
    # event_live_data/trade_tape processing for the rest of that tick
    # too, not just milestone discovery. Proves the wrapped call degrades
    # instead: the event just gets no milestone this tick (schedule
    # fallback still applies, same as any other cache-miss-with-no-
    # milestone-yet tick), and _fetch_live_status itself never raises.
    main.state["live_status_cache"].clear()
    main.state["milestone_by_event"] = {}  # force the needs_fetch path

    class _FakeClientGetEventsRaises:
        async def get_events(self, event_tickers, with_milestones=False):
            raise RuntimeError("simulated backoff-exhausted REST failure")

        async def get_live_datas(self, milestone_ids):
            raise AssertionError("should not be called - no milestone was ever resolved")

    markets = [_market_at(offset_sec=-300)]  # started 5 min ago -> past occurrence
    result = asyncio.run(main._fetch_live_status(_FakeClientGetEventsRaises(), markets))
    # No milestone resolved this tick (get_events raised) -> falls to the
    # "no milestone at all" bare-else branch (live_status.py's own
    # comment: "genuinely unknown... rather than guessed at either way"),
    # not the schedule fallback (which requires has_milestone) and not a
    # raised exception.
    assert result == {}
    assert "EVT-A" not in main.state["live_status_cache"]


def test_fetch_live_status_no_live_status_type_does_not_permanently_starve_poll_budget():
    # Bug found in review, before this fix: the no_live_status_type branch
    # `continue`d without ever writing cache[et], so its checked_at stayed
    # at to_poll.sort()'s 0.0 default forever. A milestone's `type` never
    # changes, so a company_report/truflation/... event would win the front
    # of _LIVE_STATUS_MAX_POLL_PER_TICK's bounded batch on EVERY tick,
    # permanently starving genuinely due-for-repoll live events out of the
    # per-tick budget - the same tick_duration-plateau shape the 2026-08-15
    # incident this file's own _LIVE_STATUS_MAX_POLL_PER_TICK comment
    # describes. Proves checked_at actually advances across ticks instead,
    # participating in the normal _LIVE_STATUS_REPOLL_SEC cadence.
    main.state["live_status_cache"].clear()

    class _FakeReportClient:
        async def get_events(self, event_tickers, with_milestones=False):
            return [{"event_ticker": et, "milestones": [{"id": "ms1", "type": "company_report"}]} for et in event_tickers]

        async def get_live_datas(self, milestone_ids):
            return {mid: {"type": "company_report", "details": {"widget_status": "live"}} for mid in milestone_ids}

    fake = _FakeReportClient()
    markets = [_market_at(offset_sec=-300)]

    asyncio.run(main._fetch_live_status(fake, markets))
    entry = main.state["live_status_cache"]["EVT-A"]
    assert entry["status"] is None
    assert entry["source"] == "no_live_status_type"
    first_checked_at = entry["checked_at"]

    # Not yet due for repoll - the bare bug fix alone (writing SOME cache
    # entry) isn't enough on its own to prove starvation is fixed; this
    # confirms it's the SAME repoll-gated cadence as every other status,
    # not a re-poll-every-tick regression in the other direction.
    result = asyncio.run(main._fetch_live_status(fake, markets))
    assert result == {"EVT-A": None}
    assert main.state["live_status_cache"]["EVT-A"]["checked_at"] == first_checked_at

    # Once stale (past _LIVE_STATUS_REPOLL_SEC), it must actually get
    # re-polled and checked_at bumped forward - the real regression check:
    # before the fix, checked_at would still read 0.0-derived/never-updated
    # here, and to_poll.sort() would have kept placing this event first on
    # every tick regardless of how long ago it was last (not) cached.
    main.state["live_status_cache"]["EVT-A"]["checked_at"] = time.time() - main._LIVE_STATUS_REPOLL_SEC - 1
    asyncio.run(main._fetch_live_status(fake, markets))
    assert main.state["live_status_cache"]["EVT-A"]["checked_at"] > first_checked_at


# --- live_game_state surfacing (2026-08-16 API-doc audit finding B2) - the
# same get_live_datas call above already fetches the full real payload
# (score/quarter/clock/down-distance/last_play), previously only ever read
# for widget_status. Pure value-add at zero extra API cost. -----------------

class _FakeGameStateClient:
    def __init__(self, details):
        self._details = details

    async def get_events(self, event_tickers, with_milestones=False):
        return [{"event_ticker": et, "milestones": [{"id": "ms1", "type": "football_game"}]} for et in event_tickers]

    async def get_live_datas(self, milestone_ids):
        return {mid: {"type": "football_game", "details": self._details} for mid in milestone_ids}


def test_fetch_live_status_surfaces_real_game_state_details():
    main.state["live_status_cache"].clear()
    main.state["live_game_state"].clear()
    details = {
        "away_points": 24, "home_points": 20, "clock": "00:00", "quarter": 4,
        "widget_status": "live", "last_play": {"description": "End Game"},
    }
    fake = _FakeGameStateClient(details)
    markets = [_market_at(offset_sec=-300)]
    asyncio.run(main._fetch_live_status(fake, markets))
    assert main.state["live_game_state"]["EVT-A"]["details"] == details
    assert "updated_at" in main.state["live_game_state"]["EVT-A"]


def test_fetch_live_status_does_not_surface_game_state_for_empty_details():
    main.state["live_status_cache"].clear()
    main.state["live_game_state"].clear()
    fake = _FakeGameStateClient({})
    markets = [_market_at(offset_sec=-300)]
    asyncio.run(main._fetch_live_status(fake, markets))
    assert "EVT-A" not in main.state["live_game_state"]


# --- _fetch_markets (live_markets_only): catalog rows must be hydrated with
# real prices, not left at whatever fallback state["latest_prices"] uses ----
# Real, confirmed-live bug: market_catalog rows only ever carry schedule/
# title/volume metadata (see services/market_catalog/market_catalog.py - no yes_bid_dollars
# column exists), so every live-only-selected market silently fell through
# to state["latest_prices"]'s `float(m.get("yes_bid_dollars") or 0.5)`
# fallback - every card showed 50c/50c YES/NO and never moved. Direct
# report: "showing 50c in green and red for all sets of yes/no values all
# across the app. its not updating either." Fixed by re-fetching the real,
# full market object (by series, batched) for whatever round_robin_select
# actually selected, before returning it.

class _FakeHydrationClient(_FakeLiveClient):
    """Extends the live-status fake with the one call the hydration pass
    itself makes - a single batched get_markets_by_tickers (2026-08-23:
    replaced the old per-series get_markets gather + per-ticker fallback
    with one call, since get_markets_by_tickers already batches and
    carries no status filter to fall back around)."""

    def __init__(self, hydrated_markets, **kwargs):
        super().__init__(**kwargs)
        self.hydrated_markets = hydrated_markets  # ticker -> full market dict
        self.get_markets_by_tickers_calls = []

    async def get_markets_by_tickers(self, tickers):
        self.get_markets_by_tickers_calls.extend(tickers)
        return {t: self.hydrated_markets[t] for t in tickers if t in self.hydrated_markets}


def _cfg_live_only(**overrides):
    cfg = {"kalshi": {
        "markets_watchlist": [], "min_volume_24h": 0, "live_markets_only": True,
        "watchlist_size": 10, "max_children_per_parent": None,
    }}
    cfg["kalshi"].update(overrides)
    return cfg


def test_fetch_markets_live_only_hydrates_catalog_rows_with_real_prices():
    main.state["live_status_cache"].clear()
    # SERA-EVT1-YES is reused (with different hydrated values) across this
    # test group - _cached_market_fetch's own cache must not leak a value
    # from one test into the next now that live_markets_only hydration
    # goes through it too (2026-08-23).
    main.state["market_object_cache"].clear()
    mc_module.clear_all()
    # A catalog row has no price fields at all - matches what
    # market_catalog.upsert_markets/candidates_in_window actually store.
    mc_module.upsert_markets("SERA", "Sports", [{
        "ticker": "SERA-EVT1-YES", "event_ticker": "SERA-EVT1", "volume_24h_fp": "1000",
        "occurrence_datetime": _iso(datetime.now(timezone.utc) + timedelta(minutes=-5)), "status": "active",
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
    assert fake.get_markets_by_tickers_calls == ["SERA-EVT1-YES"]  # hydrated via one batched call


def test_fetch_markets_live_only_hydrates_an_already_settled_market_too():
    # get_markets_by_tickers carries no status filter (unlike the old
    # status="open" per-series batch this replaced), so an already-settled
    # market hydrates on the same single call - no separate fallback needed.
    main.state["live_status_cache"].clear()
    # SERA-EVT1-YES is reused (with different hydrated values) across this
    # test group - _cached_market_fetch's own cache must not leak a value
    # from one test into the next now that live_markets_only hydration
    # goes through it too (2026-08-23).
    main.state["market_object_cache"].clear()
    mc_module.clear_all()
    mc_module.upsert_markets("SERA", "Sports", [{
        "ticker": "SERA-EVT1-YES", "event_ticker": "SERA-EVT1", "volume_24h_fp": "1000",
        "occurrence_datetime": _iso(datetime.now(timezone.utc) + timedelta(minutes=-5)), "status": "active",
    }])
    fake = _FakeHydrationClient(
        hydrated_markets={
            "SERA-EVT1-YES": {"ticker": "SERA-EVT1-YES", "event_ticker": "SERA-EVT1",
                               "yes_bid_dollars": "0.00", "status": "finalized"},
        },
        widget_status="live",
    )
    markets = asyncio.run(main._fetch_markets(fake, _cfg_live_only()))
    assert len(markets) == 1
    assert markets[0]["yes_bid_dollars"] == "0.00"
    assert markets[0]["status"] == "finalized"
    assert fake.get_markets_by_tickers_calls == ["SERA-EVT1-YES"]


def test_fetch_markets_live_only_falls_back_to_catalog_row_when_fetch_fails():
    # A genuine fetch failure (network error, real API error) must not lose
    # the whole tick's watchlist - falls back to the unpriced catalog row,
    # same "degrade honestly, never silently drop" idiom the discovery
    # path's own confirmation fallback uses (see
    # test_refresh_discovery_cache_keeps_a_market_the_confirm_call_could_not_return).
    main.state["live_status_cache"].clear()
    # SERA-EVT1-YES is reused (with different hydrated values) across this
    # test group - _cached_market_fetch's own cache must not leak a value
    # from one test into the next now that live_markets_only hydration
    # goes through it too (2026-08-23).
    main.state["market_object_cache"].clear()
    mc_module.clear_all()
    mc_module.upsert_markets("SERA", "Sports", [{
        "ticker": "SERA-EVT1-YES", "event_ticker": "SERA-EVT1", "volume_24h_fp": "1000",
        "occurrence_datetime": _iso(datetime.now(timezone.utc) + timedelta(minutes=-5)), "status": "active",
    }])
    fake = _FakeHydrationClient(hydrated_markets={}, widget_status="live")

    async def failing_get_markets_by_tickers(tickers):
        raise RuntimeError("simulated network failure")
    fake.get_markets_by_tickers = failing_get_markets_by_tickers

    markets = asyncio.run(main._fetch_markets(fake, _cfg_live_only()))
    assert len(markets) == 1
    assert markets[0]["ticker"] == "SERA-EVT1-YES"
    assert "yes_bid_dollars" not in markets[0]  # the original, price-less catalog row


def test_fetch_markets_live_only_hydration_is_cached_not_refetched_every_tick():
    # The actual fix (2026-08-23, direct report of real REST rate limiting
    # "especially position sections"): live_markets_only hydration now
    # goes through _cached_market_fetch, the same TTL-cached path pinned/
    # extra_tickers already used - a second call within the cache window
    # must not re-hit get_markets_by_tickers at all.
    main.state["live_status_cache"].clear()
    main.state["market_object_cache"].clear()
    mc_module.clear_all()
    mc_module.upsert_markets("SERA", "Sports", [{
        "ticker": "SERA-EVT1-YES", "event_ticker": "SERA-EVT1", "volume_24h_fp": "1000",
        "occurrence_datetime": _iso(datetime.now(timezone.utc) + timedelta(minutes=-5)), "status": "active",
    }])
    fake = _FakeHydrationClient(
        hydrated_markets={
            "SERA-EVT1-YES": {"ticker": "SERA-EVT1-YES", "event_ticker": "SERA-EVT1", "yes_bid_dollars": "0.73"},
        },
        widget_status="live",
    )
    first = asyncio.run(main._fetch_markets(fake, _cfg_live_only()))
    assert first[0]["yes_bid_dollars"] == "0.73"
    assert fake.get_markets_by_tickers_calls == ["SERA-EVT1-YES"]

    second = asyncio.run(main._fetch_markets(fake, _cfg_live_only()))
    assert second[0]["yes_bid_dollars"] == "0.73"  # still hydrated, from cache
    assert fake.get_markets_by_tickers_calls == ["SERA-EVT1-YES"]  # no second REST call


def test_cached_market_fetch_evicts_oldest_once_past_the_size_cap(monkeypatch):
    # Unbounded growth guard added 2026-08-23 alongside routing
    # live_markets_only's hydration through this cache too - a much
    # larger, faster-rotating ticker population than the small pinned/
    # open-position set this cache originally served.
    main.state["market_object_cache"].clear()
    monkeypatch.setattr(discovery_cache, "_MAX_MARKET_OBJECT_CACHE", 4)

    class _FakeClient:
        async def get_markets_by_tickers(self, tickers):
            return {t: {"ticker": t} for t in tickers}

    fake = _FakeClient()
    asyncio.run(discovery_cache._cached_market_fetch(fake, ["A", "B", "C"]))
    asyncio.run(discovery_cache._cached_market_fetch(fake, ["D", "E"]))  # 5 total, past the cap of 4
    cache = main.state["market_object_cache"]
    assert len(cache) <= 4
    # Oldest-written entries (A, B) are the eviction candidates; the two
    # just-written this call (D, E) must never be evicted.
    assert "D" in cache and "E" in cache


# --- _refresh_discovery_cache: real-time terminal-status confirmation
# (2026-08-16, close_time-mutability fix - ROADMAP.md's "Active
# investigation" entry). Kalshi's own market_lifecycle.md confirms
# close_time can be revised earlier via a close_date_updated event this app
# doesn't listen for, so a catalog row scanned before that revision keeps
# showing a stale close_time/status indefinitely - confirmed live against a
# real finalized MLB market whose catalog row still read status=active,
# close_time 2.5 days out, 24 minutes after Kalshi's own API had already
# moved it to finalized. The non-live-only discovery path (this project's
# actual default, live_markets_only: false) had zero real-time check at
# all before this - a stale/closed catalog row could reach the real
# watchlist and sit there with no live price, since nothing overlays a
# fresher price for a market with no new trades. -----------------------

class _FakeConfirmClient:
    def __init__(self, confirmed_by_ticker):
        self.confirmed_by_ticker = confirmed_by_ticker
        self.get_markets_by_tickers_calls = []

    async def get_markets_by_tickers(self, tickers):
        self.get_markets_by_tickers_calls.append(list(tickers))
        return {t: self.confirmed_by_ticker[t] for t in tickers if t in self.confirmed_by_ticker}


def _discovery_cfg(**overrides):
    cfg = {"kalshi": {"min_volume_24h": 0, "watchlist_size": 10, "max_children_per_parent": None}}
    cfg["kalshi"].update(overrides)
    return cfg


def test_refresh_discovery_cache_drops_a_market_confirmed_no_longer_active():
    mc_module.clear_all()
    main.state["event_titles"].clear()
    now_ts = datetime.now(timezone.utc)
    mc_module.upsert_markets("SERA", "Sports", [{
        "ticker": "SERA-M1", "event_ticker": "SERA-EVT1", "series_ticker": "SERA", "volume_24h_fp": "1000",
        "occurrence_datetime": _iso(now_ts + timedelta(minutes=-5)), "status": "active",
    }])
    # Stale catalog row still says "active" - the real-time confirm call is
    # what actually catches that Kalshi has since moved this market on.
    fake = _FakeConfirmClient({"SERA-M1": {"ticker": "SERA-M1", "status": "finalized"}})
    asyncio.run(main._refresh_discovery_cache(_discovery_cfg(), fake))
    assert main.state["discovery_cache"]["markets"] == []
    assert fake.get_markets_by_tickers_calls == [["SERA-M1"]]


def test_refresh_discovery_cache_keeps_a_confirmed_still_active_market_with_real_data():
    mc_module.clear_all()
    main.state["event_titles"].clear()
    now_ts = datetime.now(timezone.utc)
    mc_module.upsert_markets("SERA", "Sports", [{
        "ticker": "SERA-M1", "event_ticker": "SERA-EVT1", "series_ticker": "SERA", "volume_24h_fp": "1000",
        "occurrence_datetime": _iso(now_ts + timedelta(minutes=-5)), "status": "active",
    }])
    real = {"ticker": "SERA-M1", "status": "active", "yes_bid_dollars": "0.73"}
    fake = _FakeConfirmClient({"SERA-M1": real})
    asyncio.run(main._refresh_discovery_cache(_discovery_cfg(), fake))
    markets = main.state["discovery_cache"]["markets"]
    assert len(markets) == 1
    # The real confirmed object replaces the catalog row - real current
    # price, not the catalog's own price-less copy.
    assert markets[0]["yes_bid_dollars"] == "0.73"


def test_refresh_discovery_cache_keeps_a_market_the_confirm_call_could_not_return():
    # A genuine fetch miss (404, transient error) must not drop a ticker
    # outright - same "degrade honestly, never guess" idiom _fetch_markets'
    # own live_markets_only hydration fallback already uses.
    mc_module.clear_all()
    main.state["event_titles"].clear()
    now_ts = datetime.now(timezone.utc)
    mc_module.upsert_markets("SERA", "Sports", [{
        "ticker": "SERA-M1", "event_ticker": "SERA-EVT1", "series_ticker": "SERA", "volume_24h_fp": "1000",
        "occurrence_datetime": _iso(now_ts + timedelta(minutes=-5)), "status": "active",
    }])
    fake = _FakeConfirmClient({})  # SERA-M1 not returned
    asyncio.run(main._refresh_discovery_cache(_discovery_cfg(), fake))
    markets = main.state["discovery_cache"]["markets"]
    assert [m["ticker"] for m in markets] == ["SERA-M1"]


def test_refresh_discovery_cache_no_candidates_skips_the_confirmation_call():
    mc_module.clear_all()  # empty catalog - nothing selected, nothing to confirm
    main.state["event_titles"].clear()
    fake = _FakeConfirmClient({})
    asyncio.run(main._refresh_discovery_cache(_discovery_cfg(), fake))
    assert main.state["discovery_cache"]["markets"] == []
    assert fake.get_markets_by_tickers_calls == []  # cheap no-op, not a wasted call


def test_refresh_discovery_cache_excludes_ineligible_series_when_evaluator_enabled():
    # Mirrors test_fetch_markets_live_only_excludes_ineligible_series_when_enabled
    # for the discovery path that's actually live by default
    # (kalshi.live_markets_only: false) - the live_markets_only branch above
    # had this exact pair of cases covered, this path didn't.
    mc_module.clear_all()
    se_module.clear_all()
    main.state["event_titles"].clear()
    now_ts = datetime.now(timezone.utc)
    mc_module.upsert_markets("SERGOOD", "Sports", [{
        "ticker": "SERGOOD-M1", "event_ticker": "SERGOOD-EVT1", "series_ticker": "SERGOOD", "volume_24h_fp": "1000",
        "occurrence_datetime": _iso(now_ts + timedelta(minutes=-5)), "status": "active",
    }])
    mc_module.upsert_markets("SERBAD", "Sports", [{
        "ticker": "SERBAD-M1", "event_ticker": "SERBAD-EVT1", "series_ticker": "SERBAD", "volume_24h_fp": "1000",
        "occurrence_datetime": _iso(now_ts + timedelta(minutes=-5)), "status": "active",
    }])
    se_module.record_trade_observed("SERBAD", now=1000.0)
    with se_module._connect() as conn:
        conn.execute(
            "UPDATE series_status SET status = 'rejected', next_eligible_at = ? WHERE series = 'SERBAD'",
            (time.time() + 99999,),
        )
    cfg = _discovery_cfg()
    cfg["series_evaluator"] = {"enabled": True}
    fake = _FakeConfirmClient({"SERGOOD-M1": {"ticker": "SERGOOD-M1", "status": "active"}})
    asyncio.run(main._refresh_discovery_cache(cfg, fake))
    tickers = {m["ticker"] for m in main.state["discovery_cache"]["markets"]}
    assert "SERGOOD-M1" in tickers
    assert "SERBAD-M1" not in tickers


def test_refresh_discovery_cache_includes_rejected_series_when_evaluator_disabled():
    # Direct request (2026-08-16): a series series_evaluator previously
    # rejected must be fully back in consideration, not just "no worse off,"
    # the moment series_evaluator.enabled is false - confirmed live against
    # a real incident where a 15+ hour whale-stream outage (fixed in 6973974)
    # left every observed series sitting at trades_observed=0 through its own
    # max_observation_sec window, so evaluate_pending auto-rejected all 38 of
    # them in one batch for a reason that had nothing to do with series
    # quality. ineligible_series() is the only thing that reads 'rejected'
    # status to gate discovery, and main._refresh_discovery_cache only
    # consults it at all when series_evaluator.enabled is true - this locks
    # that gate in for the path that's actually live by default.
    mc_module.clear_all()
    se_module.clear_all()
    main.state["event_titles"].clear()
    now_ts = datetime.now(timezone.utc)
    mc_module.upsert_markets("SERBAD", "Sports", [{
        "ticker": "SERBAD-M1", "event_ticker": "SERBAD-EVT1", "series_ticker": "SERBAD", "volume_24h_fp": "1000",
        "occurrence_datetime": _iso(now_ts + timedelta(minutes=-5)), "status": "active",
    }])
    se_module.record_trade_observed("SERBAD", now=1000.0)
    with se_module._connect() as conn:
        conn.execute(
            "UPDATE series_status SET status = 'rejected', next_eligible_at = ? WHERE series = 'SERBAD'",
            (time.time() + 99999,),
        )
    cfg = _discovery_cfg()
    cfg["series_evaluator"] = {"enabled": False}
    fake = _FakeConfirmClient({"SERBAD-M1": {"ticker": "SERBAD-M1", "status": "active"}})
    asyncio.run(main._refresh_discovery_cache(cfg, fake))
    tickers = {m["ticker"] for m in main.state["discovery_cache"]["markets"]}
    assert "SERBAD-M1" in tickers


# --- _refresh_discovery_cache_background: owns its own client (2026-08-16
# client-lifecycle fix, real live incident: "the whale watching stream has
# halted completely" investigation also turned up repeated "[discovery]
# background refresh failed" log lines - root cause was this background
# task reusing the calling tick's own KalshiClient, which that tick's own
# `finally: await client.close()` closes at the end of the same tick
# regardless of whether this independent task has finished with it). -------

class _FakeBackgroundClient:
    instances: list["_FakeBackgroundClient"] = []

    def __init__(self, base_url, timeout):
        self.base_url = base_url
        self.timeout = timeout
        self.closed = False
        self.get_markets_by_tickers_calls: list[list[str]] = []
        _FakeBackgroundClient.instances.append(self)

    async def get_markets_by_tickers(self, tickers):
        self.get_markets_by_tickers_calls.append(list(tickers))
        return {}

    async def close(self):
        self.closed = True


def test_refresh_discovery_cache_background_creates_and_closes_its_own_client(monkeypatch):
    mc_module.clear_all()
    main.state["event_titles"].clear()
    main.state["discovery_cache"] = {"fetched_at": 0.0, "markets": [], "refreshing": True, "task": None}
    _FakeBackgroundClient.instances = []
    monkeypatch.setattr(discovery_cache, "KalshiPublicGateway", _FakeBackgroundClient)

    asyncio.run(main._refresh_discovery_cache_background(
        _discovery_cfg(base_url="https://example.invalid", request_timeout_sec=10)
    ))

    # Its own client - never the caller's - and closed afterward regardless
    # of how long the caller's own client has already been gone.
    assert len(_FakeBackgroundClient.instances) == 1
    assert _FakeBackgroundClient.instances[0].closed is True
    assert main.state["discovery_cache"]["refreshing"] is False


def test_refresh_discovery_cache_background_still_closes_client_on_failure(monkeypatch):
    # task_supervisor.supervise (the real caller, see lifespan/
    # _maybe_refresh_discovery_cache) is what catches this now - this
    # function itself just guarantees cleanup via `finally` and lets the
    # exception propagate, so calling it directly (as this test does) must
    # now expect the raise too.
    mc_module.clear_all()
    main.state["event_titles"].clear()
    main.state["discovery_cache"] = {"fetched_at": 0.0, "markets": [], "refreshing": True, "task": None}
    _FakeBackgroundClient.instances = []

    def _boom(*args, **kwargs):
        raise RuntimeError("Session is closed")

    monkeypatch.setattr(discovery_cache, "KalshiPublicGateway", _FakeBackgroundClient)
    monkeypatch.setattr(main.market_catalog, "open_candidates", _boom)

    with pytest.raises(RuntimeError, match="Session is closed"):
        asyncio.run(main._refresh_discovery_cache_background(
            _discovery_cfg(base_url="https://example.invalid", request_timeout_sec=10)
        ))

    assert len(_FakeBackgroundClient.instances) == 1
    assert _FakeBackgroundClient.instances[0].closed is True
    assert main.state["discovery_cache"]["refreshing"] is False


# --- _process_stream_ticker: opened_since guard (2026-08-16 self-review
# finding) - the one of check_exits' three call sites (main tick loop,
# _process_stream_trade, here) missing the 2026-08-11 same-tick stale-price
# guard. The ticker and trade WS channels are independent streams with no
# ordering guarantee between them, so a ticker update reflecting a moment
# before a whale's fill can still be processed right after
# _process_stream_trade opens a position on that fresher fill price - same
# stale-price-vs-fresh-entry shape as the original incident, just via the
# ticker path. strategy.check_exits itself is monkeypatched here (rather
# than setting up a real open position) since the only thing under test is
# whether _process_stream_ticker passes opened_since at all, not
# check_exits' own exit logic (already covered elsewhere).

def test_process_stream_ticker_passes_opened_since_to_check_exits(monkeypatch):
    main.state["running"] = True
    main.state["signal_feed"] = [{"ticker": "TICK-A"}]
    main.state["markets"] = []
    main.state["latest_prices"] = {}

    captured = {}

    def fake_check_exits(latest_prices, signal_feed, cfg, market_results, opened_since=None, category_by_ticker=None, close_times=None, **_additive_kwargs):
        # **_additive_kwargs: check_exits' signature grows additively (tick_cache,
        # latest_prices_updated_at, ...) - this double only cares about opened_since.
        captured["opened_since"] = opened_since
        return []
    monkeypatch.setattr(main.strategy, "check_exits", fake_check_exits)

    before = time.time()
    _run_stream_ticker(({"market_ticker": "TICK-A", "yes_bid_dollars": "0.5"}))
    after = time.time()

    assert captured["opened_since"] is not None
    assert before <= captured["opened_since"] <= after


# --- _process_stream_ticker: check_pending_fills / position_netting.review
# wiring (P8 Task 38, Family-C-lite). Both used to run only from trading_
# loop's own tick; concurrency safety under a second, WS-triggered caller is
# proven directly against the pure functions in
# tests/test_position_management_concurrency.py - these confirm the wiring
# itself: the calls actually happen from this handler, on the right gate.

def test_process_stream_ticker_fills_a_pending_limit_order(monkeypatch):
    main.state["running"] = True
    main.state["signal_feed"] = []
    main.state["markets"] = []
    main.state["latest_prices"] = {}
    main.state["latest_asks"] = {}
    main.state["market_titles"] = {}
    main.state["event_titles"] = {}
    main.broker.reset(starting_bankroll=10000.0)
    main.broker.place_limit_order("TICK-A", "yes", size=10, limit_price=0.55, reason="r", expires_at=time.time() + 60, confidence=0.95)

    # A yes_bid print alone doesn't move latest_asks; seed it directly the
    # way a prior ticker message already would have, then a bid print that
    # doesn't touch the ask keeps the same fillable ask in place.
    main.state["latest_asks"]["TICK-A"] = 0.50
    _run_stream_ticker({"market_ticker": "TICK-A", "yes_bid_dollars": "0.48"})

    assert main.broker.pending_orders == {}  # resolved, not left dangling
    assert "TICK-A" in main.broker.positions
    assert main.broker.positions["TICK-A"].size == 10
    assert any(d.get("action") == "trade" and d.get("source") == "limit_order" for d in main.state["decision_feed"])


def test_process_stream_ticker_runs_check_pending_fills_even_without_signal_feed(monkeypatch):
    """check_exits' own gate (state.get("signal_feed")) must not also gate
    check_pending_fills/position_netting.review - neither reads signal_feed
    at all, and trading_loop's tick never gated them on it either."""
    main.state["running"] = True
    main.state["signal_feed"] = []  # falsy - would skip check_exits, must not skip the other two
    main.state["markets"] = []
    main.state["latest_prices"] = {}
    main.state["latest_asks"] = {"TICK-A": 0.50}
    main.state["market_titles"] = {}
    main.state["event_titles"] = {}
    main.broker.reset(starting_bankroll=10000.0)
    main.broker.place_limit_order("TICK-A", "yes", size=10, limit_price=0.55, reason="r", expires_at=time.time() + 60, confidence=0.95)

    _run_stream_ticker({"market_ticker": "TICK-A", "yes_bid_dollars": "0.48"})

    assert "TICK-A" in main.broker.positions  # filled despite the empty signal_feed


def test_process_stream_ticker_skips_check_pending_fills_when_not_running(monkeypatch):
    main.state["running"] = False
    main.state["signal_feed"] = []
    main.state["markets"] = []
    main.state["latest_prices"] = {}
    main.state["latest_asks"] = {"TICK-A": 0.50}
    main.state["market_titles"] = {}
    main.state["event_titles"] = {}
    main.broker.reset(starting_bankroll=10000.0)
    main.broker.place_limit_order("TICK-A", "yes", size=10, limit_price=0.55, reason="r", expires_at=time.time() + 60, confidence=0.95)

    _run_stream_ticker({"market_ticker": "TICK-A", "yes_bid_dollars": "0.48"})

    assert main.broker.pending_orders  # untouched - the app is paused
    main.state["running"] = True  # restore for later tests in this module


def test_process_stream_ticker_calls_position_netting_review_when_enabled(monkeypatch):
    monkeypatch.setattr(main.config_store, "get", lambda: {"position_netting": {"enabled": True, "min_dwell_sec": 0}})
    main.state["running"] = True
    main.state["signal_feed"] = []
    main.state["markets"] = []
    main.state["latest_prices"] = {"TICK-A": 0.6, "TICK-B": 0.6}
    main.state["latest_asks"] = {}
    main.state["market_titles"] = {"TICK-A": {"event_ticker": "EVT-1"}, "TICK-B": {"event_ticker": "EVT-1"}}
    main.state["event_titles"] = {"EVT-1": {"mutually_exclusive": True}}
    main.broker.reset(starting_bankroll=10000.0)
    main.broker.open_position("TICK-A", "yes", 100, 0.6, "r")
    main.broker.open_position("TICK-B", "yes", 100, 0.6, "r")

    _run_stream_ticker({"market_ticker": "TICK-A", "yes_bid_dollars": "0.6"})

    assert main.broker.positions == {}  # locked-loss pair closed via the WS path


def test_process_stream_ticker_position_netting_is_a_noop_when_disabled(monkeypatch):
    main.state["running"] = True
    main.state["signal_feed"] = []
    main.state["markets"] = []
    main.state["latest_prices"] = {"TICK-A": 0.6, "TICK-B": 0.6}
    main.state["latest_asks"] = {}
    main.state["market_titles"] = {"TICK-A": {"event_ticker": "EVT-1"}, "TICK-B": {"event_ticker": "EVT-1"}}
    main.state["event_titles"] = {"EVT-1": {"mutually_exclusive": True}}
    main.broker.reset(starting_bankroll=10000.0)
    main.broker.open_position("TICK-A", "yes", 100, 0.6, "r")
    main.broker.open_position("TICK-B", "yes", 100, 0.6, "r")

    _run_stream_ticker({"market_ticker": "TICK-A", "yes_bid_dollars": "0.6"})

    assert set(main.broker.positions) == {"TICK-A", "TICK-B"}  # position_netting.enabled defaults False


# --- _process_stream_ticker: ticker_exit_check_min_interval_sec throttle
# (2026-09-03 live-incident fix). check_exits/check_pending_fills/
# position_netting.review used to run on every ticker message; these tests
# go around _run_stream_ticker's own per-call reset (that reset exists so
# every OTHER test in this file keeps seeing pre-throttle behavior) to
# exercise the throttle directly.

def test_process_stream_ticker_throttles_repeat_calls_within_the_interval(monkeypatch):
    # No config_store.get() monkeypatch: relies on the real, currently
    # committed config/settings.yaml default (kalshi.
    # ticker_exit_check_min_interval_sec: 2.0) - check_pending_fills'
    # validate_fn needs a real, full "strategy" section to resolve
    # against, which a hand-rolled partial dict wouldn't have.
    wsh_module._last_ticker_exit_check_at = 0.0
    main.state["running"] = True
    main.state["signal_feed"] = []
    main.state["markets"] = []
    main.state["latest_prices"] = {}
    main.state["latest_asks"] = {"TICK-A": 0.50}
    main.state["market_titles"] = {}
    main.state["event_titles"] = {}
    main.broker.reset(starting_bankroll=10000.0)
    main.broker.place_limit_order("TICK-A", "yes", size=10, limit_price=0.55, reason="r", expires_at=time.time() + 60, confidence=0.95)

    # First call: throttle gate is at 0.0, so `now - 0.0 >= 2.0` is true -
    # the block runs and fills the order.
    asyncio.run(main._process_stream_ticker({"ticker": "TICK-A", "yes_bid_dollars": "0.48"}))
    assert "TICK-A" in main.broker.positions

    # Second call, immediately after: within the 2.0s window, so the block
    # must be skipped entirely - place a new pending order and confirm it
    # is NOT filled even though the same fillable ask is present.
    main.broker.place_limit_order("TICK-B", "yes", size=10, limit_price=0.55, reason="r", expires_at=time.time() + 60, confidence=0.95)
    main.state["latest_asks"]["TICK-B"] = 0.50
    asyncio.run(main._process_stream_ticker({"ticker": "TICK-B", "yes_bid_dollars": "0.48"}))
    assert "TICK-B" not in main.broker.positions
    assert "TICK-B" in main.broker.pending_orders


def test_process_stream_ticker_runs_again_once_the_interval_elapses(monkeypatch):
    # No config_store.get() monkeypatch - same reasoning as the test above.
    # Simulate "2+ seconds have already passed" the same way sibling
    # _maybe_* throttle tests do (test_main_tick_executor_wiring.py) -
    # backdate the module's last-run timestamp rather than sleeping.
    wsh_module._last_ticker_exit_check_at = time.time() - 5.0
    main.state["running"] = True
    main.state["signal_feed"] = []
    main.state["markets"] = []
    main.state["latest_prices"] = {}
    main.state["latest_asks"] = {"TICK-A": 0.50}
    main.state["market_titles"] = {}
    main.state["event_titles"] = {}
    main.broker.reset(starting_bankroll=10000.0)
    main.broker.place_limit_order("TICK-A", "yes", size=10, limit_price=0.55, reason="r", expires_at=time.time() + 60, confidence=0.95)

    asyncio.run(main._process_stream_ticker({"ticker": "TICK-A", "yes_bid_dollars": "0.48"}))

    assert "TICK-A" in main.broker.positions  # gate was open - block ran


def test_process_stream_ticker_throttle_reads_the_config_value_live(monkeypatch):
    """A 0.0 interval (or any interval already elapsed) must never block -
    proves the gate reads config_store.get() fresh each call rather than a
    module-level constant, matching this file's other live-reloadable
    kalshi.* settings."""
    # Merge onto the real config (not a hand-rolled partial dict) -
    # check_pending_fills' validate_fn needs a real "strategy" section.
    _cfg = dict(main.config_store.get())
    _cfg["kalshi"] = {**_cfg["kalshi"], "ticker_exit_check_min_interval_sec": 0.0}
    monkeypatch.setattr(main.config_store, "get", lambda: _cfg)
    wsh_module._last_ticker_exit_check_at = time.time()  # "just ran" - would block a >0 interval
    main.state["running"] = True
    main.state["signal_feed"] = []
    main.state["markets"] = []
    main.state["latest_prices"] = {}
    main.state["latest_asks"] = {"TICK-A": 0.50}
    main.state["market_titles"] = {}
    main.state["event_titles"] = {}
    main.broker.reset(starting_bankroll=10000.0)
    main.broker.place_limit_order("TICK-A", "yes", size=10, limit_price=0.55, reason="r", expires_at=time.time() + 60, confidence=0.95)

    asyncio.run(main._process_stream_ticker({"ticker": "TICK-A", "yes_bid_dollars": "0.48"}))

    assert "TICK-A" in main.broker.positions


def test_process_stream_ticker_missing_kalshi_config_falls_back_to_default(monkeypatch):
    """Several tests in this file monkeypatch config_store.get() to a
    partial dict with no "kalshi" key at all - the throttle must fail open
    to the documented 2.0s default (config/settings.yaml's own value)
    rather than raising, exactly as _run_stream_ticker_calls_position_
    netting_review_when_enabled already does above."""
    # Merge onto the real config, just drop "kalshi" - check_pending_fills'
    # validate_fn still needs a real "strategy" section to resolve against.
    _cfg = {k: v for k, v in main.config_store.get().items() if k != "kalshi"}
    monkeypatch.setattr(main.config_store, "get", lambda: _cfg)
    wsh_module._last_ticker_exit_check_at = 0.0
    main.state["running"] = True
    main.state["signal_feed"] = []
    main.state["markets"] = []
    main.state["latest_prices"] = {}
    main.state["latest_asks"] = {"TICK-A": 0.50}
    main.state["market_titles"] = {}
    main.state["event_titles"] = {}
    main.broker.reset(starting_bankroll=10000.0)
    main.broker.place_limit_order("TICK-A", "yes", size=10, limit_price=0.55, reason="r", expires_at=time.time() + 60, confidence=0.95)

    asyncio.run(main._process_stream_ticker({"ticker": "TICK-A", "yes_bid_dollars": "0.48"}))

    assert "TICK-A" in main.broker.positions  # no KeyError, gate defaulted to open


# --- _process_stream_trade: trade_exit_check_min_interval_sec throttle
# (2026-09-03 live-incident fix, parallel to _process_stream_ticker's own
# throttle above). This call used to be deliberately unthrottled - see the
# removed test_process_stream_trade_check_exits_is_not_throttled this
# block replaces - reasoning "fires only on an emitted signal (~0.1-0.5% of
# trades), already self-throttled by construction." Real measured cost has
# a long tail (401ms avg, 15.6s max observed) that frequency alone doesn't
# bound: 38 of 43 recent WS reconnects correlate with a 10-76s event-loop
# stall, consistent with 2+ long-tail check_exits draws stacking within a
# burst of correlated whale activity. See trade_exit_check_min_interval_
# sec's own comment in config/settings.yaml for the full mechanism.

def _stub_trade_signal_provider(monkeypatch):
    """Shared setup for the throttle tests below: makes _process_stream_
    trade reach its check_exits call site for ticker TICK-A without needing
    a fully valid whale-signal dict shape (open_position/evaluate/etc.) -
    only "was check_exits reached, and how many times" is in scope for
    these tests, same reasoning the removed not-throttled test used."""
    monkeypatch.setattr(wsh_module, "_streaming_trade_tape_enabled", lambda: True)

    async def _noop_handle_signal(*args, **kwargs):
        return None

    monkeypatch.setattr(wsh_module, "_handle_signal", _noop_handle_signal)

    class _StubProvider:
        async def fetch_signals(self, since_ts=None, market_context=None):
            return [{
                "ticker": "TICK-A", "side": "yes", "confidence": 0.9, "source": "whale_trade",
                "price": 0.5, "size": 10, "reason": "r",
            }]

    # Patched on app_state, the single source of truth every path resolves
    # through since #565 - whale_stream_handlers no longer owns a copy.
    monkeypatch.setattr(app_state_module, "_whale_provider", _StubProvider())


def _spy_on_check_exits(monkeypatch):
    calls = []
    real_check_exits = main.strategy.check_exits

    def _spy_check_exits(*args, **kwargs):
        calls.append(1)
        return real_check_exits(*args, **kwargs)

    monkeypatch.setattr(main.strategy, "check_exits", _spy_check_exits)
    return calls


def _reset_trade_stream_state():
    main.state["running"] = True
    main.state["signal_feed"] = []
    main.state["markets"] = []
    main.state["latest_prices"] = {"TICK-A": 0.5}
    main.state["latest_asks"] = {}
    main.state["market_titles"] = {}
    main.state["event_titles"] = {}
    main.state["trade_tape"] = []
    main.broker.reset(starting_bankroll=10000.0)


_TRADE_MSG = {
    "trade_id": "T-1", "market_ticker": "TICK-A", "yes_price_dollars": "0.5",
    "count": 10, "taker_side": "yes",
}


def test_process_stream_trade_check_exits_throttles_repeat_calls_within_the_interval(monkeypatch):
    # No config_store.get() monkeypatch: relies on the real, currently
    # committed config/settings.yaml default (kalshi.
    # trade_exit_check_min_interval_sec: 2.0), same reasoning as the ticker
    # throttle tests above.
    wsh_module._last_trade_exit_check_at = 0.0
    calls = _spy_on_check_exits(monkeypatch)
    _stub_trade_signal_provider(monkeypatch)
    _reset_trade_stream_state()

    # First call: throttle gate is at 0.0, so `now - 0.0 >= 2.0` is true -
    # check_exits runs.
    asyncio.run(main._process_stream_trade(dict(_TRADE_MSG)))
    # Second call, immediately after: within the 2.0s window, so check_exits
    # must be skipped entirely.
    asyncio.run(main._process_stream_trade(dict(_TRADE_MSG)))

    assert len(calls) == 1  # the second call was throttled


def test_process_stream_trade_check_exits_runs_again_once_the_interval_elapses(monkeypatch):
    # Simulate "2+ seconds have already passed" the same way the ticker
    # throttle test does - backdate the module's last-run timestamp rather
    # than sleeping.
    wsh_module._last_trade_exit_check_at = time.time() - 5.0
    calls = _spy_on_check_exits(monkeypatch)
    _stub_trade_signal_provider(monkeypatch)
    _reset_trade_stream_state()

    asyncio.run(main._process_stream_trade(dict(_TRADE_MSG)))

    assert len(calls) == 1  # gate was open - check_exits ran


def test_process_stream_trade_check_exits_throttle_reads_the_config_value_live(monkeypatch):
    """A 0.0 interval (or any interval already elapsed) must never block -
    proves the gate reads config_store.get() fresh each call rather than a
    module-level constant, matching ticker_exit_check_min_interval_sec's
    own test above and this file's other live-reloadable kalshi.*
    settings."""
    _cfg = dict(main.config_store.get())
    _cfg["kalshi"] = {**_cfg["kalshi"], "trade_exit_check_min_interval_sec": 0.0}
    monkeypatch.setattr(main.config_store, "get", lambda: _cfg)
    wsh_module._last_trade_exit_check_at = time.time()  # "just ran" - would block a >0 interval
    calls = _spy_on_check_exits(monkeypatch)
    _stub_trade_signal_provider(monkeypatch)
    _reset_trade_stream_state()

    asyncio.run(main._process_stream_trade(dict(_TRADE_MSG)))

    assert len(calls) == 1


def test_process_stream_trade_check_exits_missing_kalshi_config_falls_back_to_default(monkeypatch):
    """Several tests in this file monkeypatch config_store.get() to a
    partial dict with no "kalshi" key at all - the throttle must fail open
    to the documented 2.0s default (config/settings.yaml's own value)
    rather than raising, exactly as the ticker throttle's own equivalent
    test above does. _stream_market_client (called unconditionally while
    building fetch_signals' market_context, unrelated to this throttle) is
    stubbed out here since IT does direct cfg["kalshi"]["base_url"]
    indexing and would otherwise raise first, on a pre-existing code path
    this test isn't exercising."""
    monkeypatch.setattr(wsh_module, "_stream_market_client", lambda cfg: None)
    _cfg = {k: v for k, v in main.config_store.get().items() if k != "kalshi"}
    monkeypatch.setattr(main.config_store, "get", lambda: _cfg)
    wsh_module._last_trade_exit_check_at = 0.0
    calls = _spy_on_check_exits(monkeypatch)
    _stub_trade_signal_provider(monkeypatch)
    _reset_trade_stream_state()

    asyncio.run(main._process_stream_trade(dict(_TRADE_MSG)))

    assert len(calls) == 1  # no KeyError, gate defaulted to open


def test_process_stream_trade_throttled_skip_is_covered_by_ticker_path_safety_net(monkeypatch):
    """The nuance this fix has to get right (not just copy the ticker
    throttle blindly): unlike the ticker path - a call that fires 10+/sec
    and would very likely fire again within milliseconds regardless of
    throttling - the trade path's check_exits call only fires when a
    signal was actually emitted (~0.1-0.5% of trades). A skipped call here
    is not obviously "will just run again soon" the same way a skipped
    ticker-path call is.

    This proves the actual safety net directly instead of assuming it:
    exit_engine.check_exits scans ALL open positions off shared broker/
    state on every call, regardless of which path (trade or ticker)
    triggered it - so a position that needed closing and got skipped by
    the (now-throttled) trade-path call is still closed by the very next
    _process_stream_ticker call, which independently re-evaluates the
    identical broker/state and is not gated by trade_exit_check_min_
    interval_sec at all. Uses market_results as the trigger (exit_engine.
    check_exits' own docstring: "checked first and unconditionally, not
    behind any opt-in flag") so this doesn't also depend on take_profit/
    stop_loss/auto_exit strategy config - only on check_exits actually
    running for TICK-A's position, by whichever path gets there first."""
    wsh_module._last_trade_exit_check_at = time.time()  # "just ran" - trade path's own gate is closed
    wsh_module._last_ticker_exit_check_at = 0.0  # ticker path's own gate is open
    _stub_trade_signal_provider(monkeypatch)
    _reset_trade_stream_state()
    main.state["signal_feed"] = ["placeholder"]  # non-empty - opens the ticker path's own signal_feed gate
    main.state["market_results"] = {"TICK-A": "yes"}  # settled - any check_exits call closes this unconditionally
    main.broker.open_position("TICK-A", "yes", 100, 0.5, "r")

    asyncio.run(main._process_stream_trade(dict(_TRADE_MSG)))

    # Throttled: the trade path's own check_exits call did not run this
    # time, so the settled position is still open.
    assert "TICK-A" in main.broker.positions

    # The ticker path's own, independently-throttled check_exits call picks
    # up the exact same open position (shared broker/state) and closes it -
    # the safety net that bounds the trade-path skip above to a few
    # seconds of staleness, not a missed exit.
    asyncio.run(main._process_stream_ticker({"ticker": "TICK-A", "yes_bid_dollars": "0.5"}))
    assert "TICK-A" not in main.broker.positions


# --- _process_stream_lifecycle: market_lifecycle_v2 (2026-08-17,
# docs/next-session-pickup-2026-08-17.md item #2 of the REST-vs-websocket
# architecture finding). close_date_updated (2026-08-17) and
# determined/settled (2026-08-23, once real captured message shapes from
# ddev logs confirmed determined carries result/settled does not - see the
# function's own docstring) are all wired to real effects now.

def _reset_lifecycle_stats():
    settlement_resolver._pending.clear()
    main.state["lifecycle_stream_stats"] = {
        "events_by_type": {}, "close_time_updates_applied": 0, "last_event_at": None,
        "catalog_updates_applied": 0, "outcomes_resolved_via_lifecycle": 0,
    }
    mc_module.clear_all()


def _seed_catalog_row(ticker: str, series_ticker: str = "SER-LC"):
    mc_module.upsert_markets(series_ticker, "Crypto", [{
        "ticker": ticker, "event_ticker": f"{series_ticker}-EVT", "volume_24h_fp": "1000",
        "occurrence_datetime": _iso(datetime.now(timezone.utc) - timedelta(minutes=5)), "status": "active",
    }])


def test_lifecycle_close_date_updated_refreshes_matching_market():
    _reset_lifecycle_stats()
    _seed_catalog_row("TICK-A")
    main.state["markets"] = [{"ticker": "TICK-A", "close_time": "2026-01-01T00:00:00Z"}]
    gen_before = main.state["generation"]

    _run_lifecycle((
        {"event_type": "close_date_updated", "market_ticker": "TICK-A", "close_ts": 1735689600},
    ))

    assert main.state["markets"][0]["close_time"] == "2025-01-01T00:00:00Z"
    assert main.state["lifecycle_stream_stats"]["close_time_updates_applied"] == 1
    assert main.state["lifecycle_stream_stats"]["events_by_type"]["close_date_updated"] == 1
    assert main.state["generation"] > gen_before
    # The persisted catalog row gets the same close_ts, not just the
    # in-memory overlay - the gap this module's own CHEATSHEET.md named.
    # (Checked via a raw column read, not candidates_in_window - that query
    # also filters on close_ts > now, and 1735689600 above is deliberately
    # a past timestamp, so it'd be excluded there for an unrelated reason.)
    assert main.state["lifecycle_stream_stats"]["catalog_updates_applied"] == 1
    with mc_module._connect(mc_module.DB_PATH) as conn:
        close_ts = conn.execute("SELECT close_ts FROM markets WHERE ticker = ?", ("TICK-A",)).fetchone()[0]
    assert close_ts == 1735689600


def test_lifecycle_close_date_updated_is_a_noop_for_an_unknown_ticker():
    _reset_lifecycle_stats()
    main.state["markets"] = [{"ticker": "TICK-B", "close_time": "2026-01-01T00:00:00Z"}]
    gen_before = main.state["generation"]

    _run_lifecycle((
        {"event_type": "close_date_updated", "market_ticker": "TICK-A", "close_ts": 1735689600},
    ))

    # The event still counts toward observability...
    assert main.state["lifecycle_stream_stats"]["events_by_type"]["close_date_updated"] == 1
    # ...but nothing was actually updated, since TICK-A isn't in state["markets"]
    # or the catalog (never seeded here).
    assert main.state["lifecycle_stream_stats"]["close_time_updates_applied"] == 0
    assert main.state["lifecycle_stream_stats"]["catalog_updates_applied"] == 0
    assert main.state["markets"][0]["close_time"] == "2026-01-01T00:00:00Z"
    assert main.state["generation"] == gen_before


def test_lifecycle_determined_updates_catalog_status_but_does_not_resolve_outcome():
    # 2026-08-23 correction (services/exits/README.md's audit finding):
    # determined is not terminal - result can still flip via disputed ->
    # amended before finalized, so no outcome resolution happens here
    # anymore, only the persisted catalog status.
    _reset_lifecycle_stats()
    _seed_catalog_row("TICK-A")
    cl_module.record_rejection("TICK-A", "whale_follow", "entry_threshold", 0.1, 0.5, "yes", now=time.time())

    _run_lifecycle((
        {"event_type": "determined", "market_ticker": "TICK-A", "result": "yes",
         "determination_ts": 1735689600, "settlement_value": "1.0000"},
    ))

    stats = main.state["lifecycle_stream_stats"]
    assert stats["events_by_type"]["determined"] == 1
    assert stats["catalog_updates_applied"] == 1  # status -> "determined"
    assert stats["outcomes_resolved_via_lifecycle"] == 0
    with mc_module._connect(mc_module.DB_PATH) as conn:
        status = conn.execute("SELECT status FROM markets WHERE ticker = ?", ("TICK-A",)).fetchone()[0]
    assert status == "determined"
    summary = cl_module.gate_summary()
    matching = [g for g in summary if g["strategy"] == "whale_follow" and g["gate_name"] == "entry_threshold"]
    assert matching and matching[0]["resolved_count"] == 0  # not resolved yet - still just "determined"


def test_lifecycle_determined_still_updates_catalog_for_a_scalar_result():
    _reset_lifecycle_stats()
    _seed_catalog_row("TICK-A")

    _run_lifecycle((
        {"event_type": "determined", "market_ticker": "TICK-A", "result": "scalar"},
    ))

    stats = main.state["lifecycle_stream_stats"]
    assert stats["catalog_updates_applied"] == 1  # status still recorded
    assert stats["outcomes_resolved_via_lifecycle"] == 0


class _FakeBatchSettleClient:
    """get_markets_by_tickers() fake for the deferred resolver path (P4
    Tasks 19+24) - the settled handler no longer does any REST of its own;
    resolution happens later, batched, in settlement_resolver.run_pending."""

    def __init__(self, markets_by_ticker):
        self._markets = markets_by_ticker
        self.calls = []

    async def get_markets_by_tickers(self, tickers):
        self.calls.append(sorted(tickers))
        return {t: self._markets[t] for t in tickers if t in self._markets}


def _forbid_inline_stream_client(monkeypatch):
    def _must_not_construct(cfg):
        raise AssertionError("the settled handler must not construct a REST client inline anymore (P4 Task 19)")
    monkeypatch.setattr(wsh_module, "_stream_market_client", _must_not_construct)


def test_lifecycle_settled_enqueues_deferred_resolution_without_inline_rest(monkeypatch):
    # P4 Task 19: the settled branch's inline get_market() was 23% of all
    # REST demand (I8) and, run serially on the WS consumer during
    # settlement cascades, the driver of the queue-saturation drop episodes
    # (71/81 started at :00-:09). The handler now only enqueues.
    _reset_lifecycle_stats()
    _seed_catalog_row("TICK-A")
    _forbid_inline_stream_client(monkeypatch)

    _run_lifecycle((
        {"event_type": "settled", "market_ticker": "TICK-A", "settled_ts": 1735689600},
    ))

    stats = main.state["lifecycle_stream_stats"]
    assert stats["events_by_type"]["settled"] == 1
    assert stats["catalog_updates_applied"] == 1  # status -> finalized, from the event itself
    assert stats["outcomes_resolved_via_lifecycle"] == 0  # resolution is deferred now
    assert settlement_resolver.pending() == [("TICK-A", 1735689600.0)]
    with mc_module._connect(mc_module.DB_PATH) as conn:
        status = conn.execute("SELECT status FROM markets WHERE ticker = ?", ("TICK-A",)).fetchone()[0]
    assert status == "finalized"


def test_lifecycle_settled_end_to_end_resolves_through_the_batched_resolver(monkeypatch):
    # The same end-to-end guarantee the old inline test gave (a settled
    # event ends with candidate_log rows resolved), now through the
    # enqueue -> run_pending path that replaced it.
    _reset_lifecycle_stats()
    _seed_catalog_row("TICK-A")
    _forbid_inline_stream_client(monkeypatch)
    cl_module.record_rejection("TICK-A", "whale_follow", "entry_threshold", 0.1, 0.5, "yes", now=time.time())

    _run_lifecycle((
        {"event_type": "settled", "market_ticker": "TICK-A", "settled_ts": 1735689600},
    ))
    client = _FakeBatchSettleClient({"TICK-A": {"ticker": "TICK-A", "status": "finalized", "result": "yes"}})
    result = asyncio.run(settlement_resolver.run_pending(client, now=time.time() + 61.0))

    assert result["resolved"] == 1
    assert client.calls == [["TICK-A"]]
    summary = cl_module.gate_summary()
    matching = [g for g in summary if g["strategy"] == "whale_follow" and g["gate_name"] == "entry_threshold"]
    assert matching and matching[0]["resolved_count"] >= 1


def test_lifecycle_settled_tolerates_a_missing_settled_ts(monkeypatch):
    # A malformed/partial settled payload must still enqueue (falling back
    # to the arrival clock) - dropping it would orphan the ticker until the
    # slower REST-tick fallback happens to see it.
    _reset_lifecycle_stats()
    _seed_catalog_row("TICK-A")
    _forbid_inline_stream_client(monkeypatch)
    before = time.time()

    _run_lifecycle((
        {"event_type": "settled", "market_ticker": "TICK-A"},
    ))

    pending = settlement_resolver.pending()
    assert len(pending) == 1 and pending[0][0] == "TICK-A"
    assert pending[0][1] >= before  # fell back to now, not 0/None
    assert main.state["lifecycle_stream_stats"]["catalog_updates_applied"] == 1


def test_lifecycle_ignores_message_missing_event_type_or_ticker():
    _reset_lifecycle_stats()
    _run_lifecycle(({"market_ticker": "TICK-A"}))
    _run_lifecycle(({"event_type": "created"}))
    assert main.state["lifecycle_stream_stats"]["events_by_type"] == {}


def test_lifecycle_close_date_updated_tolerates_unparseable_close_ts():
    _reset_lifecycle_stats()
    main.state["markets"] = [{"ticker": "TICK-A", "close_time": "2026-01-01T00:00:00Z"}]

    _run_lifecycle((
        {"event_type": "close_date_updated", "market_ticker": "TICK-A", "close_ts": "not-a-number"},
    ))

    assert main.state["lifecycle_stream_stats"]["close_time_updates_applied"] == 0
    assert main.state["markets"][0]["close_time"] == "2026-01-01T00:00:00Z"


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
        self.events = events  # event_ticker -> full get_event()-shaped dict (nested under "event")
        self.get_events_calls = []

    async def get_events(self, event_tickers):
        # Batched (2026-08-16) - flat Event objects, not get_event()'s
        # single-item {"event": {...}} wrapper; unwrap the fixture's nested
        # shape here so existing fixtures below don't need reshaping. The
        # real API always includes event_ticker on every returned event
        # (main._fetch_event_titles keys its result off it) - injected here
        # since the hand-written fixtures below predate that requirement
        # and don't bother setting it themselves.
        self.get_events_calls.append(list(event_tickers))
        results = []
        for et in event_tickers:
            if et in self.events:
                event = dict(self.events[et]["event"])
                event.setdefault("event_ticker", et)
                results.append(event)
        return results


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
    result = account_positions._slim_event_position(event_position)
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
         "occurrence_datetime": _iso(datetime.now(timezone.utc) + timedelta(minutes=-5)), "status": "active"},
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
    monkeypatch.setattr(maa_db_module, "DB_PATH", tmp_path / "market_analyst.db")
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


def test_build_series_context_reports_excluded_and_contracts_override():
    cfg = {
        **main.config_store.get(),
        "strategy": {**main.config_store.get()["strategy"], "excluded_series": ["KXTICK"]},
        "whale_watcher_kalshi": {"min_contracts_by_series": {"KXTICK": 750}},
    }
    ctx = main._build_series_context(cfg, "KXTICK")
    assert ctx["currently_excluded"] is True
    assert ctx["min_contracts_override"] == 750


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


def test_enrich_recent_trades_attaches_market_settlement_result_for_early_exits(tmp_path):
    # close_type/won (above) only describe the position's OWN exit price -
    # for an early exit (stop_loss here, not settled_win/settled_loss) the
    # market keeps trading after the close and can resolve either way.
    # market_result (services/market_history.py's real outcomes table) is
    # the market's actual settled result, independent of that early close -
    # what makes an early exit checkable in hindsight.
    fresh_broker = pb_module.PaperBroker(starting_bankroll=1000.0, db_path=tmp_path / "fresh_broker_mr.db")
    fresh_broker.open_position(ticker="TICK-MR-A", side="yes", size=100, price=0.5, reason="test entry")
    early_exit = fresh_broker.close_position("TICK-MR-A", 0.4, "stop-loss hit: unrealized loss 20% of cost basis")
    fresh_broker.open_position(ticker="TICK-MR-B", side="no", size=50, price=0.3, reason="test entry, still open")
    mh_module.record_outcome("TICK-MR-A", "yes", resolved_at=time.time())

    enriched = main._enrich_recent_trades(fresh_broker)
    settled_row = next(r for r in enriched if r["id"] == early_exit.id)
    assert settled_row["close_type"] == "stop_loss"
    assert settled_row["market_result"] == "yes"

    open_row = next(r for r in enriched if r["ticker"] == "TICK-MR-B")
    assert open_row["market_result"] is None


def test_trading_history_endpoint_attaches_market_settlement_result():
    # Same market_result enrichment as _enrich_recent_trades above, but on
    # the History tab's own separate endpoint/code path
    # (services/history/routes.py's get_trading_history, which builds rows
    # via trade_analytics.build_trade_history directly rather than through
    # _enrich_recent_trades) - a real, separate gap: /api/trading-history's
    # trades never carried this field at all before this fix.
    main.broker.reset(starting_bankroll=10000.0)
    main.broker.open_position("TICK-HIST-A", "yes", size=10, price=0.5, reason="test entry")
    main.broker.close_position("TICK-HIST-A", 0.4, "stop-loss hit: unrealized loss 20% of cost basis")
    mh_module.record_outcome("TICK-HIST-A", "no", resolved_at=time.time())

    resp = client.get("/api/trading-history")
    assert resp.status_code == 200
    trades = resp.json()["trades"]
    row = next(t for t in trades if t["ticker"] == "TICK-HIST-A")
    assert row["close_type"] == "stop_loss"
    assert row["market_result"] == "no"


# --- shadow un-halt route (2026-08-10) --------------------------------------
# Real bug found live: services/shadow_mode.py's own risk manager had no
# route to ever clear a tripped kill switch (confirmed live, dormant only
# because mode was "paper" at the time).

def test_shadow_risk_resume_route():
    main.shadow.halted = True
    main.shadow.halt_reason = "test halt"
    resp = client.post("/api/shadow-risk/resume")
    assert resp.status_code == 200
    assert resp.json()["halted"] is False
    assert main.shadow.halted is False


def test_process_stream_ticker_records_cadence_only_for_open_position_tickers(monkeypatch):
    """P8 Task 34: the per-position ticker-cadence write is gated on the tick
    loop's own open_position_tickers set - an open position's ticker is
    stamped, any other ticker on the exchange-wide stream is not (bounded by
    position count by construction, never exchange-wide)."""
    main.state["running"] = False  # skip check_exits; this test is about the cadence stamp only
    main.state["markets"] = []
    main.state["latest_prices"] = {}
    main.state["open_position_tickers"] = {"TICK-A"}
    main.state["open_position_ticker_seen_at"] = {}

    before = time.time()
    _run_stream_ticker(({"market_ticker": "TICK-A", "yes_bid_dollars": "0.5"}))
    _run_stream_ticker(({"market_ticker": "TICK-Z", "yes_bid_dollars": "0.5"}))
    after = time.time()

    seen = main.state["open_position_ticker_seen_at"]
    assert set(seen) == {"TICK-A"}
    assert before <= seen["TICK-A"] <= after


def test_process_stream_ticker_writes_asks_and_stamps_both_timestamps(monkeypatch):
    """P7 Task 29 (redesigned): the WS ticker handler is the primary writer
    for BOTH price dicts - before this, latest_asks had no WS writer at all
    (the handler read yes_ask_dollars onto the market row but never into
    latest_asks), so asks were frozen at their REST seed forever."""
    main.state["running"] = False
    main.state["markets"] = []
    main.state["latest_prices"] = {}
    main.state["latest_asks"] = {}
    main.state["latest_prices_updated_at"] = {}
    main.state["latest_asks_updated_at"] = {}
    main.state["open_position_tickers"] = set()

    before = time.time()
    _run_stream_ticker(({"market_ticker": "TICK-A", "yes_bid_dollars": "0.40", "yes_ask_dollars": "0.45"}))
    after = time.time()

    assert main.state["latest_prices"]["TICK-A"] == 0.40
    assert main.state["latest_asks"]["TICK-A"] == 0.45
    assert before <= main.state["latest_prices_updated_at"]["TICK-A"] <= after
    assert before <= main.state["latest_asks_updated_at"]["TICK-A"] <= after


def test_process_stream_ticker_never_fabricates_an_ask_when_the_message_has_none(monkeypatch):
    main.state["running"] = False
    main.state["markets"] = []
    main.state["latest_prices"] = {}
    main.state["latest_asks"] = {}
    main.state["latest_prices_updated_at"] = {}
    main.state["latest_asks_updated_at"] = {}
    main.state["open_position_tickers"] = set()

    _run_stream_ticker(({"market_ticker": "TICK-A", "yes_bid_dollars": "0.40"}))

    assert "TICK-A" not in main.state["latest_asks"]  # check_pending_fills must see absent, not 0.5


# --- issue #577 root fix (2026-09-05): no more fabricated 0.5 bid -------------

def test_process_stream_ticker_never_fabricates_a_bid_when_the_message_has_neither_bid_nor_price(monkeypatch):
    main.state["running"] = False
    main.state["markets"] = []
    main.state["latest_prices"] = {}
    main.state["latest_asks"] = {}
    main.state["latest_prices_updated_at"] = {}
    main.state["latest_asks_updated_at"] = {}
    main.state["open_position_tickers"] = set()

    _run_stream_ticker(({"market_ticker": "TICK-A", "yes_ask_dollars": "0.45"}))

    assert "TICK-A" not in main.state["latest_prices"]  # never a fabricated 0.5
    assert "TICK-A" not in main.state["latest_prices_updated_at"]


def test_process_stream_ticker_preserves_a_real_zero_bid_not_fabricated_to_0_5(monkeypatch):
    # The exact regression this issue is about: 0.0 is falsy, so the old
    # `float(... or ... or 0.5)` silently turned a real 0.0 bid into a
    # fabricated 0.5.
    main.state["running"] = False
    main.state["markets"] = []
    main.state["latest_prices"] = {}
    main.state["latest_asks"] = {}
    main.state["latest_prices_updated_at"] = {}
    main.state["latest_asks_updated_at"] = {}
    main.state["open_position_tickers"] = set()

    _run_stream_ticker(({"market_ticker": "TICK-A", "yes_bid_dollars": "0.0000"}))

    assert main.state["latest_prices"]["TICK-A"] == 0.0


def test_process_stream_ticker_falls_back_to_price_dollars_when_bid_is_absent(monkeypatch):
    main.state["running"] = False
    main.state["markets"] = []
    main.state["latest_prices"] = {}
    main.state["latest_asks"] = {}
    main.state["latest_prices_updated_at"] = {}
    main.state["latest_asks_updated_at"] = {}
    main.state["open_position_tickers"] = set()

    _run_stream_ticker(({"market_ticker": "TICK-A", "price_dollars": "0.33"}))

    assert main.state["latest_prices"]["TICK-A"] == 0.33


def test_process_stream_ticker_no_real_bid_still_writes_a_real_ask(monkeypatch):
    # A message with no usable price no longer aborts the rest of the
    # handler the way a caught parse exception used to (previously this
    # was an exceptional path that returned early; a missing/falsy bid is
    # now an ordinary, expected condition).
    main.state["running"] = False
    main.state["markets"] = []
    main.state["latest_prices"] = {}
    main.state["latest_asks"] = {}
    main.state["latest_prices_updated_at"] = {}
    main.state["latest_asks_updated_at"] = {}
    main.state["open_position_tickers"] = set()

    _run_stream_ticker(({"market_ticker": "TICK-A", "yes_ask_dollars": "0.45"}))

    assert main.state["latest_asks"]["TICK-A"] == 0.45


def test_pipeline_health_reports_open_position_price_staleness():
    """P7 Task 29 (redesigned) / R4: /api/health/pipeline derives per-open-
    position price staleness from the write stamps - visibility only."""
    now = time.time()
    main.state["open_position_tickers"] = {"FRESH", "STALE", "UNSTAMPED"}
    main.state["latest_prices_updated_at"] = {"FRESH": now - 5, "STALE": now - 400}

    body = client.get("/api/health/pipeline").json()["price_staleness"]

    assert body["open_position_count"] == 3
    assert body["stamped_count"] == 2
    assert body["unstamped_count"] == 1
    assert 395 <= body["open_position_oldest_age_sec"] <= 405
    assert body["stale_over_300s_count"] == 1


def test_lifecycle_settled_also_resolves_signal_log_rows_for_the_ticker(tmp_path, monkeypatch):
    """P8 Task 30: signal_log resolves alongside the other four stores, per
    row (each signal keeps its own side) - now through the deferred batched
    resolver (P4 Tasks 19+24) instead of the settled handler's former
    inline REST read."""
    import services.signal_log as signal_log_module
    monkeypatch.setattr(signal_log_module, "DB_PATH", tmp_path / "signal_log.db")
    _reset_lifecycle_stats()
    _seed_catalog_row("TICK-A")
    _forbid_inline_stream_client(monkeypatch)
    signal_log_module.log_signal("TICK-A", "yes", 1000, 0.8, "kalshi_trade_tape", seen_at=time.time() - 3600)
    signal_log_module.log_signal("TICK-A", "no", 1000, 0.8, "kalshi_trade_tape", seen_at=time.time() - 3600)

    _run_lifecycle((
        {"event_type": "settled", "market_ticker": "TICK-A", "settled_ts": 1735689600},
    ))
    client = _FakeBatchSettleClient({"TICK-A": {"ticker": "TICK-A", "status": "finalized", "result": "yes"}})
    result = asyncio.run(settlement_resolver.run_pending(client, now=time.time() + 61.0))

    rows = {r["side"]: r for r in signal_log_module.recent(limit=10) if r["ticker"] == "TICK-A"}
    assert rows["yes"]["resolved"] == 1 and rows["yes"]["correct"] == 1
    assert rows["no"]["resolved"] == 1 and rows["no"]["correct"] == 0
    assert result["resolved_rows"] >= 2  # the counter the stats loop feeds from


def test_pipeline_health_reports_every_background_scheduler(monkeypatch):
    """P8 Task 36: with the trigger checks relocated out of trading_loop, the
    pipeline route is the one place a human can confirm each scheduler is
    still firing - last-started age + busy flag per scheduler, unknown
    reported as None rather than fabricated."""
    now = time.time()
    monkeypatch.setitem(main.state, "signal_resolution_check", {"last_checked_at": now - 12, "checking": False, "task": None})
    monkeypatch.setitem(main.state, "catalog_scan", {"scanning": True, "last_started_at": now - 3, "task": None})

    body = client.get("/api/health/pipeline").json()["schedulers"]

    assert {"signal_resolution", "backup", "research", "event_schedule", "catalog_scan", "candidate_retry", "settlement_resolver", "auto_apply"} <= set(body)
    assert 10 <= body["signal_resolution"]["last_started_sec_ago"] <= 15
    assert body["signal_resolution"]["busy"] is False
    assert body["catalog_scan"]["busy"] is True
    # Review finding (PR #198): the resolver's own counters must be
    # reachable, or a dropped settlement is invisible data loss. Issue #208
    # added the split: `dropped_total` is the conservation sum, and the two
    # counters that mean opposite things are reachable separately, along
    # with what the skipped markets actually carried.
    for key in ("pending", "enqueued_total", "resolved_total", "dropped_total",
                "dropped_after_max_attempts", "skipped_non_binary_result",
                "non_binary_by_result", "non_binary_recent"):
        assert key in body["settlement_resolver"]
    sr = body["settlement_resolver"]
    assert sr["dropped_total"] == sr["dropped_after_max_attempts"] + sr["skipped_non_binary_result"]


def test_pipeline_health_exposes_the_me_pairing_gate_counter(monkeypatch):
    """Final-review finding: mutual_exclusivity.me_pairing_stats() shipped
    with zero callers outside its own tests, so the entry-gate fallback's
    own "the catalog had no entry for this candidate" gap was unmeasurable
    in production - against CLAUDE.md's "these properties fail silently:
    measure them". Surfaced alongside strategy_engine's me_gate_stats as
    its own key, since the two count different gates."""
    monkeypatch.setattr(mutual_exclusivity, "_me_pairing_stats", {"me_pairing_unknown_total": 7})

    body = client.get("/api/health/pipeline").json()

    assert body["me_pairing_gate"] == {"me_pairing_unknown_total": 7}
    # Still its own key, never folded into the other gate's counter.
    assert "me_pairing_unknown_total" not in body["strategy_gates"]


def test_close_positions_sells_a_no_position_at_the_no_bid(monkeypatch):
    # 2026-09-04 adversarial review D1: this endpoint priced BOTH sides off
    # state["latest_prices"] (yes_bid), so a NO close was paid (1 - yes_bid)
    # - the NO ask - and on an empty yes book that fabricated $1.00/contract.
    main.broker.reset(starting_bankroll=10000.0)
    main.broker.open_position("TICK-A", "no", size=100, price=0.40, reason="entry")
    bankroll_before = main.broker.bankroll
    monkeypatch.setitem(main.state, "latest_prices", {"TICK-A": 0.20})
    monkeypatch.setitem(main.state, "latest_asks", {"TICK-A": 0.30})

    resp = client.post(
        "/api/trading/close-positions",
        json={"tickers": ["TICK-A"], "confirmation_phrase": "CLOSE SELECTED POSITIONS"},
    )

    assert resp.status_code == 200
    expected = bankroll_before + 100 * 0.70 - main.kalshi_fees.taker_fee(100, 0.30, ticker="TICK-A")
    assert main.broker.bankroll == pytest.approx(expected, abs=0.01)
    main.broker.reset(starting_bankroll=10000.0)


def test_close_positions_pays_zero_not_one_on_an_unsellable_book(monkeypatch):
    main.broker.reset(starting_bankroll=10000.0)
    main.broker.open_position("TICK-A", "no", size=100, price=0.40, reason="entry")
    bankroll_before = main.broker.bankroll
    monkeypatch.setitem(main.state, "latest_prices", {"TICK-A": 0.0})
    monkeypatch.setitem(main.state, "latest_asks", {"TICK-A": 1.0})

    resp = client.post(
        "/api/trading/close-positions",
        json={"tickers": ["TICK-A"], "confirmation_phrase": "CLOSE SELECTED POSITIONS"},
    )

    assert resp.status_code == 200
    assert main.broker.bankroll == pytest.approx(bankroll_before, abs=0.01)
    assert main.broker.positions == {}
    main.broker.reset(starting_bankroll=10000.0)
