"""
Bootstraps pytest-time isolation from live repository data before any test
module in this directory is collected (and therefore before any test can
`import main` or a services module that constructs an app_state singleton).
The actual mechanism - what gets redirected, the sqlite3.connect hard guard,
and the collection-order incident that made per-file redirects insufficient
on their own - lives in tests/support/runtime_isolation.py.
"""
from tests.support.runtime_isolation import install_runtime_isolation, pinned_config_get

install_runtime_isolation()


import pytest


@pytest.fixture(autouse=True)
def _pinned_runtime_config(monkeypatch):
    """config/settings.yaml is live runtime state the user commits as-is, so
    a flag flipped in production reaches every test that reads it through
    config_store.get() without patching (issue #229: PR #222 committed
    realtime_data_plane.two_consumer_mode: true and 16 gateway tests that
    had only ever passed because the file said false failed at once). The
    sections in tests/support/runtime_isolation.py's PINNED_CONFIG_SECTIONS
    are served as fixed test dicts here, for every test, whatever the file
    says. Patched on the config_store *instance* - the one object every
    services module and main.py import - so a test's own
    monkeypatch.setattr(<module>.config_store, "get", ...) (the established
    idiom) replaces this wrapper outright and wins, and a ConfigStore a test
    builds itself (tests/test_config_store.py, which is about the class
    reading a file) is a different instance and is deliberately untouched.
    tests/test_runtime_isolation.py proves the pin fires and cross-checks
    each pinned section's keys against the committed file."""
    from services.config.config_store import config_store
    monkeypatch.setattr(config_store, "get", pinned_config_get(config_store.get))


@pytest.fixture(autouse=True)
def _fresh_whale_pipeline_perf(monkeypatch):
    """services/whale_pipeline_perf.py's module-level singleton is process-
    global mutable state the hot path records into; without this every
    test that exercises the whale provider or stream handler would leak
    lifetime counters into the next test (observability's "no evidence ->
    no rows" contract is the first thing that breaks). Same principle as
    the DB isolation above, applied to an in-memory aggregator."""
    from services import whale_pipeline_perf as wpp
    monkeypatch.setattr(wpp, "perf", wpp.WhalePipelinePerf())


@pytest.fixture(autouse=True)
def _fresh_rest_latency_stats(monkeypatch):
    """services/http_client.py's per-caller-class latency stats (I5) are
    module-global for the same reason and with the same leak: any test that
    drives call_with_backoff leaves lifetime counts behind for the next one.
    Fresh dict per test; the module's own reset_rest_latency_window() only
    rolls windows and is deliberately not a full reset.

    _endpoint_window and the two token buckets' waiters_high_water are the
    rest of what rest_latency_snapshot() reads (audited for #234): both
    module-lifetime, both only ever surfaced once by_class is non-empty -
    which is exactly why they leaked unnoticed behind the empty-by_class
    gate until a test that drives a real call asserts on them."""
    from services import http_client
    monkeypatch.setattr(http_client, "_rest_class_stats", {})
    monkeypatch.setattr(http_client, "_endpoint_window", {})
    for limiter in (http_client._kalshi_read_limiter, http_client._kalshi_write_limiter):
        monkeypatch.setattr(limiter, "waiters_high_water", 0)


@pytest.fixture(autouse=True)
def _fresh_candidate_retry_state(monkeypatch):
    """services/candidate_retry.py's pending queue + window counters (P2
    Task 12) are the same class of module-global mutable state as
    whale_pipeline_perf/http_client above, and just as easy to leak: any
    test anywhere in the suite that calls enqueue()/run_pending() (this
    module's own tests, plus the wiring test in
    test_whale_candidate_lifecycle.py) otherwise leaves _pending entries
    and non-zero window counters behind for the next test, which broke
    test_observability.py's "a fully quiet tick returns metrics == {}"
    contract the first time this module shipped (candidate_retry.retried
    leaked from test_candidate_retry.py into an unrelated observability
    test, alphabetically later in test discovery order - the exact same
    failure shape as the whale_pipeline_perf leak that motivated the
    fixture above it)."""
    from services import candidate_retry
    monkeypatch.setattr(candidate_retry, "_pending", {})
    monkeypatch.setattr(candidate_retry, "_window_retried", 0)
    monkeypatch.setattr(candidate_retry, "_window_recovered", 0)
    monkeypatch.setattr(candidate_retry, "_window_abandoned", 0)


@pytest.fixture(autouse=True)
def _fresh_loop_watchdog_window(monkeypatch):
    """services/loop_watchdog.py's stall window (P0 Task 1) is the same
    class again - three module globals capture_from_runtime folds into
    every capture - and the one that was still missing here (issue #234):
    any test that runs main's lifespan (`with TestClient(main.app)`)
    starts the real watchdog task, which records a late first wakeup
    under load as a stall and leaves it behind when its loop closes, so
    test_observability.py's quiet-tick `metrics == {}` failed on CI with
    `loop_watchdog.stall_max_ms: 185.593` and passed on re-run. Reproduced
    deterministically before this fixture existed by collecting a test
    that plants a sample ahead of it."""
    from services import loop_watchdog
    monkeypatch.setattr(loop_watchdog, "_stall_max_ms", 0.0)
    monkeypatch.setattr(loop_watchdog, "_stall_count", 0)
    monkeypatch.setattr(loop_watchdog, "_samples", 0)


@pytest.fixture(autouse=True)
def _fresh_title_cache_series_ticker_cache(monkeypatch):
    """services/title_cache.py's series_ticker_for() in-memory index
    (_MARKET_EVENT_INDEX/_EVENT_SERIES_INDEX - final whole-branch review
    fix round, kalshi-category-data-completeness Task 3: replaced fix-round
    1's DB-backed memoization, which still put a 548us round trip on the
    exchange-wide trade-tape hot path once per distinct off-watchlist
    ticker per 5-minute negative-TTL window - see that module's own
    module-level comment) is process-global mutable state keyed by ticker/
    event_ticker string. Same leak shape as every other fixture in this
    file: two different tests that happen to reuse the same ticker literal
    (common in this suite - "TICK-A", "MKT-A", etc.) against two different
    per-test tmp_path DB_PATH values would otherwise have one test's
    indexed resolution leak into another's unrelated database, silently
    returning a stale/wrong answer instead of reflecting the fresh
    per-test DB's own load_market_titles()/load_event_titles() calls."""
    from services import title_cache
    monkeypatch.setattr(title_cache, "_MARKET_EVENT_INDEX", {})
    monkeypatch.setattr(title_cache, "_EVENT_SERIES_INDEX", {})


@pytest.fixture(autouse=True)
def _fresh_milestone_live_data_default_path_types(monkeypatch):
    """services/market_watch/milestone_live_data.py's _default_path_types_seen
    (kalshi-category-data-completeness Task 5) is the same class of module-
    global mutable state as every fixture above: it's a fire-once-per-
    process gate keyed by milestone `type` string, so any test that calls
    extract() with an unnamed type (this module's own tests deliberately
    do, to prove the fault-log-once behavior) leaves that type behind for
    the next test - which broke default_path_types_snapshot()'s "empty
    when nothing seen" and "exactly N distinct types" assertions the first
    time this module's own test file ran end to end (collection-order
    dependent: an earlier test's `tennis_tournament_singles`/
    `basketball_game`/`some_type_never_seen_before` calls all leaked into
    the snapshot tests below them in this same file)."""
    from services.market_watch import milestone_live_data
    monkeypatch.setattr(milestone_live_data, "_default_path_types_seen", set())
    # Same class of module-global fire-once-per-process gate (final whole-
    # branch review fix-round, kalshi-category-data-completeness Task 8's
    # unmapped-race_call_status-value fault log) - identical leak risk
    # across tests in the same collection.
    monkeypatch.setattr(milestone_live_data, "_political_race_unmapped_status_seen", set())


@pytest.fixture(autouse=True)
def _capture_writer_not_left_running():
    """services/capture_writer.py's daemon thread is the last input
    capture_from_runtime reads (writer.* rows appear while is_alive()).
    Its own tests start()/stop() in try/finally and stop() already resets
    the module's thread handle, so nothing leaks today - this is the
    containment for the day a test fails between start() and its finally,
    or a new test forgets the stop(): a thread left alive would make every
    later test in the process report a live writer."""
    yield
    from services import capture_writer
    if capture_writer._thread is not None:
        capture_writer.stop()


@pytest.fixture(autouse=True)
def _reset_aio_db_cache():
    """services/diagnostics/_aio_db.py caches one aiosqlite.Connection (and
    its own non-daemon worker thread) per (event loop, db_path). Every test
    here gets DB_PATH monkeypatched to a fresh tmp_path (above), so each
    test that reaches run_offline()/series_watcher's converted read
    functions opens a new, never-reused cache entry - previously only reset
    per-file, by an identical fixture of this same name in
    tests/test_diagnostics.py, tests/test_diagnostics_routes.py,
    tests/test_series_watcher.py, and tests/test_main_tick_executor_wiring.py
    (tests/test_aio_db.py resets a different way, calling
    asyncio.run(_aio_db.reset()) inline at the end of each test body rather
    than via this fixture). Promoted here so a new test file that reaches
    the same call graph can't silently accumulate connections/threads for
    the rest of the run - measured on tests/test_quality_routes.py before
    this fixture existed: 30 cached connections / 31 live threads left
    behind after a 6-test file, never reclaimed until process exit (PR
    adversarial review finding F3, 2026-09-01). Harmless, not a hang either
    way - the threading._register_atexit hook in _aio_db.py closes
    everything correctly at interpreter exit regardless - this just stops
    the mid-run buildup. Safe to coexist with the per-file copies above:
    verified with pytest --setup-show that only one same-named autouse
    fixture instance runs per test - the closer (per-module) definition
    shadows this one - so a file that already defines it locally keeps
    doing exactly what it did before and simply never invokes this one.

    Guarded for a loop already running in this thread (CI regression caught
    on the PR, pipeline 282, 2026-09-01): tests/test_browser_playwright_e2e.py's
    Playwright-backed fixtures tear down while their own asyncio event loop
    is still running in this thread, and asyncio.run()'s own source
    (installed 3.13 asyncio/runners.py) checks
    events._get_running_loop() is not None and, if so, raises
    RuntimeError("asyncio.run() cannot be called from a running event
    loop") before ever touching its argument - exactly the 8 failures CI
    hit. Skipping the reset in that case is correct, not a compromise: a
    test whose teardown already has a running loop never reached
    connection_for() through this fixture's normal path either (the
    Playwright suite doesn't touch _aio_db at all), so there is nothing
    here for it to clean up."""
    yield
    import asyncio

    from services.diagnostics import _aio_db

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_aio_db.reset())
    # else: a loop is already running in this thread - nothing this fixture
    # resets is reachable from a test in that shape, and asyncio.run() would
    # raise unconditionally here regardless of what _aio_db actually holds.
